# evefit MCP server（stdio + HTTP）

把 `evefit/api.py` 的函数暴露成 **MCP 工具**，让 agent 直接问「这套配装什么样、哪里有问题」
「这条战报是谁打的、损失多少」，而不是自己写 Python —— 更不是自己打开 zkillboard 网页。

**一句话定位：MCP 自己解释自己。** 服务的 `initialize.instructions` 给推荐顺序与三条判读
纪律，`tools/list` 里每个工具的 `description` 写清「何时用 / 返回什么 / 已知限制」，
`inputSchema` 写清参数与 enum。README 只留**运维契约**（怎么起、怎么连、错误语义、限制），
**不复制工具文案** —— 文案的权威来源永远是 `mcp_server.py` 的 `_TOOL_DESCRIPTIONS`
与 `INSTRUCTIONS`（Skill 只做发现性，见第 8 节）。

- 服务（协议 + stdio 传输 + 命令行）：`evefit/mcp_server.py`
- **HTTP 传输**：`evefit/mcp_http.py`
- 真实客户端冒烟：`evefit/mcp/client_smoke.py`（stdio）、`evefit/mcp/client_smoke_http.py`（http）
- 契约测试（含自解释守卫）：`evefit/tests/test_mcp_server.py`、`evefit/tests/test_mcp_http.py`

两种传输，**同一套协议实现**（同一个 `dispatch()`、同一套错误码、同一个信封）；
都是**零第三方依赖**：只需要 `eveshipfit-dogma-engine[sde]`，不需要 `mcp` 包（见第 9 节）。

| 传输 | 启动 | 用途 |
|---|---|---|
| **stdio**（默认） | `python -m evefit.mcp_server` | 一个客户端一个子进程；本地客户端最省事 |
| **HTTP**（Streamable HTTP） | `python -m evefit.mcp_server --transport http --host 0.0.0.0 --port 8080` | **能跨机器 / 局域网访问**；容器里的唯一可行形态 |

---

## 1. 一行启动

### 1.1 stdio（默认，客户端当子进程用）

```powershell
cd F:\WorkSpace\GIT\opencode_general_workspace
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp_server
```

它是个 **stdio 服务**：从 stdin 读 JSON-RPC、往 stdout 写 JSON-RPC，读到 EOF 就退出。
所以直接在终端里裸跑「看起来卡住了」是正常的 —— 它是给客户端当子进程用的。

自检（不用客户端，手撸两句协议）：

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual","version":"1"}}}' |
  uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp_server
```

回包里就有 `instructions`（推荐顺序 + 三条判读纪律）与 `capabilities.tools`。

### 1.2 HTTP（局域网 / 容器）

```powershell
# 绑所有网卡（局域网可访问）；容器里的推荐写法
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp_server --transport http --host 0.0.0.0 --port 8080

# 只给本机用（默认就是 127.0.0.1）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp_server --transport http
```

它前台阻塞，日志全部走 **stderr**，stdout 一个字节都没有（http 模式下 stdout 也不是
协议通道，但同样不许被污染）。看到这两行就说明起来了：

```
[evefit-mcp] evefit 1.0.0 启动（http）：POST http://0.0.0.0:8080/mcp · GET http://0.0.0.0:8080/health
[evefit-mcp] 监听 0.0.0.0（所有网卡）：本机用 http://127.0.0.1:8080/mcp 访问，局域网用本机 IP 访问
```

自检：

```powershell
curl http://127.0.0.1:8080/health
python -m evefit.mcp.client_smoke_http                    # 完整 HTTP 客户端冒烟
python -m evefit.mcp.client_smoke_http --base-url http://127.0.0.1:18080
```

### 1.3 命令行开关

| 开关 | 默认 | 说明 |
|---|---|---|
| `--transport stdio\|http` | `stdio` | 传输形态。**默认 stdio 是硬约束**（T-46 行为不变） |
| `--host HOST` | `127.0.0.1` | http 监听地址。**容器里传 `0.0.0.0`**（默认只绑回环 = 不误暴露） |
| `--port PORT` | `8080` | http 监听端口；`0` = 让系统分配（测试用） |
| `--log-level LEVEL` | `info` | `debug / info / warning / error`，写 **stderr** |
| `--version` / `-h, --help` | — | 也是写 **stderr**（stdout 只留给协议） |

`--key value` 与 `--key=value` 两种写法都认。不合法的值 → 退出码 `2`；不认识的参数只
警告，不影响启动。

## 2. HTTP 传输（单端点 `/mcp`）

按 MCP `2025-03-26` / `2025-06-18` 的 **Streamable HTTP** 实现，标准库
`http.server.ThreadingHTTPServer`，零第三方依赖。

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/mcp` | 请求体是**一条** JSON-RPC 消息。`Accept` 含 `text/event-stream` → 回 SSE（`event: message` + 一条 `data: <json>`）；否则 → `200` + `Content-Type: application/json` 直接回 JSON（整个响应是一个 JSON-RPC 对象，不是数组） |
| `GET` | `/mcp` | **`405` + `Allow: POST, DELETE, OPTIONS`**（理由见下） |
| `DELETE` | `/mcp` | 终止会话 → `200` + `{"ok":true,"ended":true,"session_id":"…","sessions":N}`（幂等：再删一次 `ended:false`） |
| `OPTIONS` | `/mcp` | `204` 预检：`Access-Control-Allow-Origin: *`、`Allow-Methods`、`Allow-Headers: Content-Type, Accept, Mcp-Session-Id, Last-Event-ID, MCP-Protocol-Version`、`Max-Age: 86400` |
| `GET` | `/health` | `200` + 健康信息（见 2.2） |

**为什么 `GET /mcp` 是 405 而不是一条 SSE 长连接？** 规范两种都允许。本服务**从不主动
向客户端发消息**（没有 sampling / roots / logging / 通知推送），一条永远空转的 SSE 流只会
白占一个线程和一条套接字，还把「连接断了」变成新的故障面。405 是规范明确许可的答复，
官方 SDK 也必须容忍。

### 2.1 会话（`Mcp-Session-Id`）

- `initialize` **成功**时在**响应头**返回 `Mcp-Session-Id`（32 位 hex）；
- 后续请求带上这个头 → 复用同一个会话（协商到的 `protocolVersion`、`initialized` 标记都在里面）；
- 缺失 / 不认识的 id → **按新会话处理**（打一行 warning，不回 404）。容器重启后旧客户端的
  会话 id 自然失效，硬拒会让它永远连不上，而重新协商一遍是幂等的；
- 会话表上限 1024，超出淘汰最老的；`DELETE /mcp` 终止单个会话。

### 2.2 `GET /health`（容器 healthcheck 的契约）

```json
{"ok": true, "server": "evefit", "version": "1.0.0", "transport": "http", "tools": 7, "sessions": 3}
```

| 字段 | 值 |
|---|---|
| `ok` | 恒 `true`（能回这个就说明进程活着） |
| `server` | `evefit` |
| `version` | `1.0.0`（= `mcp_server.SERVER_VERSION`） |
| `transport` | `http` |
| `tools` | 工具条数（`= len(mcp_server.TOOL_NAMES)`，与 `tools/list` 同源；**别抄数字**，上面的 `7` 只是当前快照） |
| `sessions` | 当前会话数（没握手过就是 `0`） |

**字段名别改** —— 容器 healthcheck 按名取值。

### 2.3 错误语义：HTTP 状态码 vs JSON-RPC 错误码

| 情形 | HTTP | body |
|---|---|---|
| JSON-RPC 层的错误（坏 JSON `-32700`、坏请求 `-32600`、未知方法 `-32601`、参数不合法 `-32602`、内部异常 `-32603`） | **`200`** | JSON-RPC 错误对象（错误语义在信封里，客户端按 JSON-RPC 解析） |
| 通知（无 `id` 的消息） | `202` | **空 body**（回了就是错位的响应） |
| 请求体 > **1 MiB** | `413` | `{"ok":false,"error":{"kind":"payload_too_large",…}}` |
| 缺 `Content-Length`（含 chunked） | `411` | `{"ok":false,"error":{"kind":"length_required",…}}` |
| 方法不对（`GET /mcp`、`POST /health`） | `405` | `…"kind":"method_not_allowed"` + `Allow` 头 |
| 路径不存在 | `404` | `…"kind":"not_found"`（消息里带两个合法端点） |
| 处理请求时抛未预期异常 | `500` | `{"ok":false,"error":{"kind":"internal_error","detail":"…"}}`，**进程继续服务** |
| 连接超时（30 s 没读完请求） | `408` | 然后断开 |

**参数 / 业务错误的区分与 stdio 完全一致**：`fit` 缺失 / 空串 / 超 256 KiB / 未知工具名 →
`-32602`；解析失败、模块名拼错、路径不存在 → **成功响应** + `isError: true` + 信封 `ok:false`。

### 2.4 硬约束（容器化时要靠它们）

- **threaded**：一个连接一个线程，慢客户端只挂住自己那条线程（30 s socket 超时兜底）；
- **body ≤ 1 MiB**（`mcp_http.MAX_BODY_BYTES`）：在 `json.loads` 之前拦住（MCP 那层另有
  256 KiB 的 `fit` 上限）；
- **异常不杀进程**：handler 抛异常 → `500 + JSON`，`serve_forever` 继续；
- **日志只走 stderr**：访问日志、启动日志、异常栈全部 `[evefit-mcp]` 前缀 → stderr；
- **`SIGTERM`** → 优雅停止（`docker stop` 发的就是它）；`Ctrl+C` 同理。

### 2.5 客户端怎么连

http 传输的客户端配置就是一个 URL（不需要 `command` / `cwd` / `PYTHONPATH`）：

```json
{
  "mcpServers": {
    "evefit": {
      "type": "http",
      "url": "http://192.168.1.20:8080/mcp"
    }
  }
}
```

> 具体字段名取决于客户端（`url` / `serverUrl` / `transport: "http"`），但**路径永远是
> `/mcp`**、**协议是 Streamable HTTP**。容器里跑时把 IP 换成宿主机 / 容器网络里的地址。

手撸一句（PowerShell）：

```powershell
$body = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual","version":"1"}}}'
Invoke-WebRequest -Method Post -Uri http://127.0.0.1:8080/mcp -Body $body -ContentType 'application/json' -Headers @{ Accept = 'application/json' }
```

## 3. 客户端配置（stdio，可直接粘贴）

### 3.1 通用 stdio MCP 客户端（`mcpServers` JSON）

零安装：用 `uv` 现场拉依赖（首次会下载 ~100 MB，之后走 `UV_CACHE_DIR` 缓存）。

```json
{
  "mcpServers": {
    "evefit": {
      "command": "uv",
      "args": [
        "run",
        "--python", "3.13",
        "--no-project",
        "--with", "eveshipfit-dogma-engine[sde]==13.6.0",
        "python",
        "-m", "evefit.mcp_server"
      ],
      "cwd": "F:\\WorkSpace\\GIT\\opencode_general_workspace",
      "env": {
        "UV_CACHE_DIR": "F:\\WorkSpace\\GIT\\opencode_general_workspace\\evefit\\spike\\uvcache",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

三个字段都不是装饰：

| 字段 | 为什么要写 |
|---|---|
| `cwd` | `python -m evefit.mcp_server` 靠 **cwd** 找到 `evefit` 包。客户端不支持 `cwd` 时，把仓库根加进下面的 `PYTHONPATH`（见 3.2）。 |
| `env.UV_CACHE_DIR` | uv 的缓存目录。不指定会落到用户级缓存（`%LOCALAPPDATA%\uv\cache`），与项目 `evefit/spike/uvcache` 里的既有缓存不共享，首次启动会重新下载 SDE / 引擎。 |
| `env.PYTHONIOENCODING` | `utf-8`。Windows 默认代码页（cp936）会让子进程的 stderr 写崩、也会让管道里的中文变乱码。**stdout 我们按 UTF-8 字节读写**，这个变量管的是 stderr 与内部字符串。 |

> JSON 里 Windows 路径要写双反斜杠（`F:\\WorkSpace\\...`）。也可以写成
> `"F:/WorkSpace/opencode_general_workspace"` 这种正斜杠形式，两个都行。

### 3.2 已经装好依赖的 Python（省掉 uv 包装）

如果某个 venv 里已经有 `eveshipfit-dogma-engine[sde]`：

```json
{
  "mcpServers": {
    "evefit": {
      "command": "python",
      "args": ["-m", "evefit.mcp_server"],
      "cwd": "F:\\WorkSpace\\GIT\\opencode_general_workspace",
      "env": {
        "PYTHONPATH": "F:\\WorkSpace\\GIT\\opencode_general_workspace",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

### 3.3 客户端连上之后

先 `initialize`（拿 `instructions` 与 `capabilities.tools`），再 `tools/list`（拿全部工具 +
schema），然后按 `instructions` 给的顺序调。**服务无业务状态**：每次调用自带 `fit`，
不依赖会话上下文；`Engine` / `Index` / LRU 是进程级单例。

当前握手信息：`serverInfo = {"name": "evefit", "version": "1.0.0"}`，
`capabilities.tools = {"listChanged": false}`，
支持协议版本 `2026-07-28 / 2025-11-25 / 2025-06-18 / 2025-03-26 / 2024-11-05`。
协议版本 ≥ `2025-06-18` 时工具结果里**同时**给 `structuredContent`。

## 4. 工具：权威文案在 MCP 里，这里只有索引

**唯一真源是 `mcp_server.py`**：`TOOL_NAMES`（名字与顺序）、`_TOOL_DESCRIPTIONS`（title +
description）、`_schema_for()`（参数）。`tools/list`、`/health` 的 `tools`、客户端冒烟、验收
测试全部从它派生 —— README 这张表只是**给人看的索引**，不是第二份文案。

想读线上到底怎么写的（不需要引擎，纯文本）：

```powershell
cd F:\WorkSpace\GIT\opencode_general_workspace
python -c "from evefit import mcp_server as m; print(m.INSTRUCTIONS); [print(n, '::', m._TOOL_DESCRIPTIONS[n][1]) for n in m.TOOL_NAMES]"
```

### 4.1 索引（顺序 = 推荐调用顺序）

| 工具 | 一句话 |
|---|---|
| `fit_parse` | 第一步：把输入归一化成结构，回显「我读懂了什么」 |
| `fit_killmail` | 战报上下文：损失价值 / 参战方（挂名 / 有伤害 / 零伤害 / 记录船是蛋四个计数）/ 伤害构成 / 星系与安全等级 / 受伤 vs EHP / **受害者与伤害 Top 的驾驶员·军团·联盟名字** |
| `fit_index` | 默认入口：六维度摘要（L0）+ flags + budget |
| `fit_view` | 单视角全字段（L1）：overview / provenance / unknown_reasons / confidence / flags |
| `fit_get` | 按路径精确取一个值 + 它的 provenance / reason |
| `fit_detail` | 列表型明细（升级候选、逐模块电容、完整技能表 512 条） |
| `zkb_search` | 战报列表检索（先筛 killmail_id，再对那条调 `fit_killmail`）；**每行带受害者驾驶员 / 军团 / 联盟名字** |

每个工具的「何时用 / 返回什么 / 限制」、每个参数的含义与默认值，都写在它的
`description` / `inputSchema` 里 —— **别在这份 README 里补第二份**（复制就会漂移；
`tests/test_mcp_server.py` 的三段守卫盯着这件事）。

配装类工具另接受三个可选参数（`zkb_search` 没有 `fit`，也不接受它们）：

| 参数 | 默认 | 可用值 |
|---|---|---|
| `skill_tier` | `all4+min_required` | `all4+min_required`、`all5` |
| `damage_profile` | `uniform` | `uniform`（25/25/25/25） |
| `state_policy` | `engine_default` | `engine_default` |

`fit` 是**字符串**：EFT 文本、DNA 字符串（`fitting:` 前缀可有可无）、killmail 的 JSON
文本，或者 zkillboard / ESI 的 killmail 链接（后两种会联网）。字典 / 数组也接受
（会被直接递进 `api`），但 schema 里只广告 string。

### 4.2 `zkb_search` 的调用注意（三条真会踩到的）

- **时间只能是 `past_seconds`**：zkb 已停用 `startTime` / `endTime`（abuse 防护），传了会
  得到 `invalid_argument`，而且**一次请求都不会发**（省配额、错误也更直接）。
- **单页最多 200 条**：更早的数据靠 `page` 往回翻。`limit` 只截断返回的 `kills` 数组，
  `count` 与 `aggregates` 仍按**整页**算 —— 截断这件事写在返回的 `note` 里，别把它读成
  「一共就这么多」。
- **有 1.2 s 硬限流**：所有出网请求串行 + 最小间隔 `ZKB_MIN_INTERVAL_S`（`both` 至少
  2.4 s），429 / 403 → `rate_limited`。别在循环里逐条查；名字 → id 的解析结果会进程内缓存。
- **名字只为「真正返回的那几行」解析**：`limit` 截断之后才查，且所有 id 合成**一批**
  `POST /universe/names/`（单批 ≤ 1000、批间隔 ≥ 0.05 s、进程内永久缓存）。所以
  `limit=3` 的 200 条整页也只会发一次名字请求，不会 200 次。

### 4.3 身份（驾驶员 / 军团 / 联盟）

两条战报工具都会把 `character_id` / `corporation_id` / `alliance_id` 解析成名字：

| 位置 | 形状 |
|---|---|
| `fit_killmail` → `victim` | `character` / `corporation` / `alliance` 各 `{"id", "name"}`（外加 `ship`） |
| `fit_killmail` → `attackers.final_blow` | 同上三块 + `ship` + `weapon` + `security_status` |
| `fit_killmail` → `attackers.by_pilot` | 按伤害降序前 10：三块身份 + `ship` + `damage` / `damage_pct` / `final_blow` / `security_status` |
| `zkb_search` → `kills[i].victim` | 三块身份 + `ship{type_id,name,group}` |

**`null` 的语义**：id 缺失 → 整个对象 `null`（没加联盟就是 `null`）；id 有但名字查不到
（删号 / NPC / ESI 缺项）→ `name: null`，并在 `result.warnings` 里给一句「N 个实体名未解析」。
工具**不编名字**，也不要你拿网页去补。

### 4.4 战报口径注意（蛋 / 零伤害）

- **攻击者人数 ≠ 有伤害人数**：`attackers.count` 是**挂名**人数（= ESI `attackers` 数组长度），
  `damaging_count`（`damage_done > 0`）+ `zero_damage_count`（`damage_done == 0`）**恒等于**它。
  实测 139239127：79 人挂名 → **57 人有伤害 / 22 人零伤害**。
- **蛋的伤害是真的，船归属不是**：`capsule_count` = 记录船为 `Capsule`（`type_id == 670`）的人数
  （139239127 实测 **3**）。蛋装不了任何装备，**蛋不可能打出武器伤害** —— 那个数字属于该玩家在
  同一次交火中**已被摧毁的船**；蛋出现在攻击者里，说明**这些玩家在这场交火里已经失去了自己的船**
  （ESI 只是把他那一刻的「记录船」写成了蛋）。所以：**不要**扣掉这部分伤害（数字是真的），也
  **不要**把蛋当成实际输出船（归属是错的）。
- **标记在哪**：`by_ship_group[].ship_attribution`（**全表都有**，取值 `"capsule"` / `"normal"`）、
  `by_pilot[].recorded_ship_is_capsule`（布尔），以及 `capsule_count > 0` 时 `warnings` 里的一条。
- **`by_ship_group` 的 Capsule 行 `count` 可能大于 `capsule_count`**：SDE 的 `Capsule` 组里有两种
  type（`670` `Capsule` 与 `33328` `Capsule - Genolution 'Auroral' 197-variant`）。139239127 实测：
  组内 **4** 个条目 / 伤害 1162921，而严格 `type_id == 670` 是 **3** 人（多出来那个是 33328、
  0 伤害）。两个数字都对 —— 一个数**条目**、一个数**严格同型**；`ship_attribution` 按**组**判定，
  所以 33328 那条也被标成 `capsule`（它同样是蛋，同样装不了武器）。

## 5. 返回契约

每个工具的结果是 `content: [{"type": "text", "text": "<JSON>"}]`，`text` 就是
`evefit/api.py` 的**信封原样**：

```json
{"ok": true,  "result": { ... }, "meta": {"spec_version": "v1", "skill_tier": {"name": "all4+min_required", "count": 512}, ...}}
{"ok": false, "error": {"kind": "invalid_path", "message": "…", "hint": ["ship", "meta", …]}}
```

协议版本 ≥ `2025-06-18` 时**同时**给 `structuredContent`（内容就是同一个信封对象），
方便客户端直接取结构而不必二次解析文本。老版本只给 `content` 里的文本。

### 错误语义（很重要）

| 情形 | JSON-RPC 层 | 内容 |
|---|---|---|
| 参数本身不合法（缺 `fit`、`fit` 是空串、超长、类型不对、未知工具名） | **错误** `-32602` | `error.data.field` / `error.data.hint` |
| 业务错误（解析失败、船/模块认不出、路径不存在、`view` 不认识、联网失败） | **成功响应** | `isError: true` + 信封 `ok:false`，原因在 `error.kind` / `candidates` / `hint` / `line` / `raw` |
| 服务内部异常 | **成功响应** | `isError: true` + `{"ok":false,"error":{"kind":"internal_error", "detail": "…"}}`，进程不崩 |

也就是说：**agent 看到 `isError: true` 不是「工具坏了」**，而是「这次输入有问题，去读
`error` 里的结构化原因」—— 比如 `unknown_module` 会带上 `candidates`（只报候选、绝不
代填），`invalid_path` 会带上可用前缀列表。`error.kind` 的取值集合：
`parse_failed` / `ambiguous_ship` / `unknown_module` / `invalid_path` / `invalid_argument` /
`not_a_killmail` / `unknown_entity` / `rate_limited` / `index_unavailable` /
`network_failed` / `internal_error`。

## 6. 已知限制

1. **`damage_profile` 只支持 `uniform`**（25/25/25/25）。其它剖面会返回
   `invalid_argument`，**不会**静默按均匀剖面算 —— 加权 EHP 属于投影层的活，接口层
   换算就是二次换算（红线 N5）。
2. **`state_policy` 只支持 `engine_default`**（L2 只记录这一个来源）。
3. **`fit_detail` 不指定 `view` 时返回六个视角全部**，实测 10 KB 起（`meta.assumptions`
   里还带 512 条完整技能表）。先 `fit_view` 定位，再 `fit_detail(view="...")`。工具
   **不做截断**：截断 = 丢事实，宁可大也不悄悄少东西。
4. **`fit` 上限 256 KiB**（`evefit.mcp_server.MAX_FIT_CHARS`），空串直接 `-32602`。
   正常 EFT / DNA / killmail JSON 都在几 KB 量级，这个上限比真实输入大一个量级。
5. **联网形态**：`fit` 传 zkillboard / ESI 链接、或 `{"killmail_id":N,"hash":"…"}`（对象）
   需要网络，超时 20 s，失败统一 `network_failed`。
6. **首调用慢**：第一次调用要打开 SDE 索引（实测 100–300 ms），之后是进程级单例 +
   LRU 缓存（同一份配装第二次调用 ~20 ms）。
7. **stdio 是单进程单客户端**：按「一个客户端一个子进程」用；服务顺序处理请求，不做并发
   调度（MCP 规范允许）。要给多个 agent 用，就各起一个子进程 —— 或者改用 **http 传输**：
   它是 threaded 的，多个客户端并发进来各占一个线程（`Engine` / `Index` / LRU 都是进程级
   单例 + 锁，状态共享）。
8. **http 是明文 HTTP（没有 TLS）**，也没有鉴权：`--host 0.0.0.0` 就是「谁连都能用」。
   只在可信网段（本机 / 局域网 / 容器内网）这么跑；要暴露到公网，得在前面放一层反向代理
   （TLS + token）。默认 `127.0.0.1` 就是为了不误暴露。
9. **`zkb_search` 有硬限流**：所有出网请求串行 + 最小间隔 1.2 s（`both` 至少 2.4 s），
   而且 zkb 单页最多 200 条、时间窗口只能用 `past_seconds`（起止时间已被停用）。
   它是**列表层**：拿不到完整配装，别指望用它替代 `fit_killmail`。
10. **名字要多一跳 ESI**：`fit_killmail` / `zkb_search` 的身份名字走
    `POST /universe/names/`（单批 ≤ 1000、批间隔 0.05 s、同一进程内永久缓存）。
    一条战报的所有 id 合成一批 → **一次调用最多一次名字请求**；命中缓存则一次都不发。
    名字拿不到就是 `name: null` + `warnings`，**不编、不猜**。
11. **`unknown` 不是 0**：三态 provenance 里 `unknown` 表示「算不出来」或「不适用」，
    不是零值；原因在 `unknown_reasons` 里（`not_applicable` / `input_incomplete` /
    `ambiguous` / `engine_omits`）。工具不换算、不评分、不补数。

## 7. 自检 / 排查

```powershell
# 真实客户端冒烟（stdio）：起子进程 → initialize → tools/list → 用例表（每个工具 + 两个负例）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp.client_smoke

# 真实 HTTP 客户端冒烟：握手 → 202 通知 → 清单 → SSE → 用例表 → /health
#   （先按 1.2 起服务；绑 0.0.0.0 时也用 127.0.0.1 连，这本身就是局域网可达的证据）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp.client_smoke_http
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp.client_smoke_http --base-url http://127.0.0.1:18080

# 契约测试（含子进程集成、stdout 纯净性、非永真断言、自解释守卫、http 传输）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" --with pytest python -m pytest -q evefit/tests -p no:cacheprovider
```

`client_smoke.py` 三种模式：

| 命令 | 用什么客户端 |
|---|---|
| `python -m evefit.mcp.client_smoke` | `auto`：装了官方 `mcp` 包就用它，否则（或它失败时）用手写客户端 |
| `... --stdio` | 强制手写客户端（只用标准库，零依赖） |
| `... --sdk` | 强制官方 SDK（需要 `--with mcp`；装不上会直接失败） |

`client_smoke_http.py` 只有一条路径（标准库 `http.client`），两个开关：
`--base-url`（默认 `http://127.0.0.1:8080`；写 `0.0.0.0` 会被自动换成 `127.0.0.1` 并打一行
提示）、`--timeout`（默认 `300` 秒）。

**常见事故 1：stdout 被污染。** 症状是「客户端说服务没响应 / 解析失败」。本服务做了两层
防护：所有日志走 stderr；启动时把进程 fd 1 改指 stderr，协议通道走一份私有副本 ——
即使某个第三方库里有裸 `print()`，也污染不了 JSON-RPC 流。`client_smoke` 的读循环对
每一行都要求「能解成 JSON-RPC 2.0」，所以这类事故会当场变红，而不是偶发。**http 模式**
同样把 fd 1 接到 stderr，所以容器日志里应有尽有，而 stdout 一个字节都没有（有测试钉住）。

**常见事故 2：`initialize` 有响应但后面的请求「没会话」。** 检查有没有把响应头里的
`Mcp-Session-Id` 带回去。本服务对缺失 / 不认识的 id **不报错**（按新会话处理），所以症状
是「能调通但会话数一直涨 / 上下文没复用」，而不是一个刺眼的错误。`client_smoke_http` 会
在 `initialize` 那一步直接把会话 id 打出来。

**打开请求级日志**：`--log-level debug`（或 `EVEFIT_MCP_DEBUG=1`），写到 stderr。

## 8. 没有 Skill 也能用吗

**能，而且这是本项目的设计前提。** Skill 与 MCP 不是「二选一」，关系是「说明书 vs 产品」：

| 通道 | 谁注入 | 覆盖什么 | 没了它还能用吗 |
|---|---|---|---|
| `initialize.instructions` | 客户端（握手结果） | 推荐顺序 + 三条判读纪律，约 600 字符 | **能**——见下面第 2 条 |
| `tools/list[].description` | 客户端（工具清单） | 每个工具「何时用 / 返回什么 / 限制」 | —— 这就是主通道 |
| `inputSchema` | 客户端（工具清单） | 参数、enum、默认值、必填 | —— 同上 |
| `SKILL.md` | **用户自己装**的 Skill | **只**：何时用它 / 不该用它做什么 / 怎么连 / 三条纪律 | **能** |

1. **客户端注入 `instructions` 时**：agent 一握手就拿到推荐顺序与三条判读纪律，接着
   `tools/list` 拿到每个工具的三段说明 —— 不需要任何 Skill 就能正确导航。DSH 已实证
   `instructions` 会被注入模型上下文；Claude Desktop / Cursor 等也走同一套握手。
2. **客户端不注入 `instructions` 时**（有些客户端只注入工具清单）：靠 `description` 也能用。
   每个工具描述都写明**何时用 / 返回什么 / 已知限制**，三段齐全由
   `tests/test_mcp_server.py::test_every_tool_description_is_self_explaining` 逐条钉住
   （删掉任一段就变红，配对断言见 `test_description_guard_is_not_vacuous`）；再加上
   `TOOL_NAMES` 的顺序就是推荐调用顺序，顺序信息从清单里也读得出来。
3. **Skill 的价值只是「发现性」**：在**还没连上 MCP** 的时候，让 agent / 用户知道「世界上有
   evefit 这个东西、什么时候该想起它」。一旦连上，指引全部来自 MCP 本身。
4. **所以 Skill 里不许复制工具细节**（工具清单、字段表、调用顺序细节）—— 那会变成第二份
   真源，迟早与 MCP 漂移。`tests/test_mcp_server.py::test_skill_md_is_a_thin_shell` 钉住：
   `SKILL.md` ≤ 60 行、每个工具名出现 ≤ 1 次、必须指向本 README。

一句话：**MCP 是唯一真源，Skill 是门铃。** 门铃坏了不影响屋里有人。

## 9. 依赖路线（为什么手写而不装 `mcp`）

- 官方 `mcp` 包**能装**：实测 2025-12 的 `uv run --with mcp` 拉到 **2.3.0**。
- 但**验收命令的临时环境里没有它**：`uv run --with "eveshipfit-dogma-engine[sde]==13.6.0"`
  下 `import mcp` → `ModuleNotFoundError`。让服务依赖 `mcp`，这条命令会死在导入阶段。
- 而且 `mcp` 2.x 把 `FastMCP` 改名成 `MCPServer`、传输 API 也变了（`mcp.server.fastmcp`
  会直接抛 `ModuleNotFoundError` 并让你看迁移指南），对「只想暴露一组纯函数」来说，
  换来的抽象远大于收益。HTTP 传输同理：`StreamableHTTPSessionManager` 也在 `mcp` 包里，
  用不了。

MCP 的两种传输本身都很薄：

- **stdio**：一行一条 JSON-RPC 2.0 消息；
- **Streamable HTTP**：一个 `POST /mcp` 收一条 JSON-RPC 消息，按 `Accept` 回 JSON 或一条
  SSE 事件；会话靠 `Mcp-Session-Id` 头；通知回 202。

本服务实现 `initialize`（版本协商 + `capabilities.tools` + `instructions`）、
`notifications/initialized`、`ping`、`tools/list`、`tools/call`，以及
`-32700 / -32600 / -32601 / -32602 / -32603` 五个错误码和通知不回包的规矩 —— 这就是全部，
两种传输共用这一份。所以：

- **服务**：手写，零第三方依赖（这是验收命令能跑起来的前提，也是唯一可行解）；
- **客户端**：`client_smoke.py` 优先用官方 SDK（装了 `mcp` 时），否则用标准库
  `subprocess` + `json` 手写 —— 官方 SDK 那条路还能顺带用第二套协议实现验一遍本服务
  的线格式。本机（Windows + DSH 沙箱）上 `asyncio` 创建子进程会 `PermissionError:
  [WinError 5]`，所以 SDK 路径会**自动回退**到手写客户端并打印一行原因。
  `client_smoke_http.py` 则只用 `http.client`（验收环境里也没有 `mcp`，这条路是唯一可行解）。
