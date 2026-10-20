# noobbaozi MCP server（stdio + HTTP）

把 `noobbaozi/api.py` 的函数暴露成 **MCP 工具**，让 agent 直接问「这套配装什么样、哪里有问题」
「这条战报是谁打的、损失多少」，而不是自己写 Python —— 更不是自己打开 zkillboard 网页。

**一句话定位：MCP 自己解释自己。** 服务的 `initialize.instructions` 给推荐顺序与三条判读
纪律，`tools/list` 里每个工具的 `description` 写清「何时用 / 返回什么 / 已知限制」，
`inputSchema` 写清参数与 enum。README 只留**运维契约**（怎么起、怎么连、错误语义、限制），
**不复制工具文案** —— 文案的权威来源永远是 `mcp_server.py` 的 `_TOOL_DESCRIPTIONS`
与 `INSTRUCTIONS`（Skill 只做发现性，见第 8 节）。

- 服务（协议 + stdio 传输 + 命令行）：`noobbaozi/mcp_server.py`
- **HTTP 传输**：`noobbaozi/mcp_http.py`
- 真实客户端冒烟：`noobbaozi/mcp/client_smoke.py`（stdio）、`noobbaozi/mcp/client_smoke_http.py`（http）
- 契约测试（含自解释守卫）：`noobbaozi/tests/test_mcp_server.py`、`noobbaozi/tests/test_mcp_http.py`

两种传输，**同一套协议实现**（同一个 `dispatch()`、同一套错误码、同一个信封）；
都是**零第三方依赖**：只需要 `eveshipfit-dogma-engine[sde]`，不需要 `mcp` 包（见第 9 节）。

| 传输 | 启动 | 用途 |
|---|---|---|
| **stdio**（默认） | `python -m noobbaozi.mcp_server` | 一个客户端一个子进程；本地客户端最省事 |
| **HTTP**（Streamable HTTP） | `python -m noobbaozi.mcp_server --transport http --host 0.0.0.0 --port 8080` | **能跨机器 / 局域网访问**；容器里的唯一可行形态 |

---

## 1. 一行启动

### 1.1 stdio（默认，客户端当子进程用）

```powershell
cd F:\WorkSpace\GIT\opencode_general_workspace
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.mcp_server
```

它是个 **stdio 服务**：从 stdin 读 JSON-RPC、往 stdout 写 JSON-RPC，读到 EOF 就退出。
所以直接在终端里裸跑「看起来卡住了」是正常的 —— 它是给客户端当子进程用的。

自检（不用客户端，手撸两句协议）：

```powershell
'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"manual","version":"1"}}}' |
  uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.mcp_server
```

回包里就有 `instructions`（推荐顺序 + 三条判读纪律）与 `capabilities.tools`。

### 1.2 HTTP（局域网 / 容器）

```powershell
# 绑所有网卡（局域网可访问）；容器里的推荐写法
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.mcp_server --transport http --host 0.0.0.0 --port 8080

# 只给本机用（默认就是 127.0.0.1）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.mcp_server --transport http
```

它前台阻塞，日志全部走 **stderr**，stdout 一个字节都没有（http 模式下 stdout 也不是
协议通道，但同样不许被污染）。看到这两行就说明起来了：

```
[noobbaozi-mcp] noobbaozi 1.0.0 启动（http）：POST http://0.0.0.0:8080/mcp · GET http://0.0.0.0:8080/health
[noobbaozi-mcp] 监听 0.0.0.0（所有网卡）：本机用 http://127.0.0.1:8080/mcp 访问，局域网用本机 IP 访问
```

自检：

```powershell
curl http://127.0.0.1:8080/health
python -m noobbaozi.mcp.client_smoke_http                    # 完整 HTTP 客户端冒烟
python -m noobbaozi.mcp.client_smoke_http --base-url http://127.0.0.1:18080
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
{"ok": true, "server": "noobbaozi", "version": "1.0.0", "transport": "http", "tools": 7, "sessions": 3}
```

| 字段 | 值 |
|---|---|
| `ok` | 恒 `true`（能回这个就说明进程活着） |
| `server` | `noobbaozi` |
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
- **日志只走 stderr**：访问日志、启动日志、异常栈全部 `[noobbaozi-mcp]` 前缀 → stderr；
- **`SIGTERM`** → 优雅停止（`docker stop` 发的就是它）；`Ctrl+C` 同理。

### 2.5 客户端怎么连

http 传输的客户端配置就是一个 URL（不需要 `command` / `cwd` / `PYTHONPATH`）：

```json
{
  "mcpServers": {
    "noobbaozi": {
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
    "noobbaozi": {
      "command": "uv",
      "args": [
        "run",
        "--python", "3.13",
        "--no-project",
        "--with", "eveshipfit-dogma-engine[sde]==13.6.0",
        "python",
        "-m", "noobbaozi.mcp_server"
      ],
      "cwd": "F:\\WorkSpace\\GIT\\opencode_general_workspace",
      "env": {
        "UV_CACHE_DIR": "F:\\WorkSpace\\GIT\\opencode_general_workspace\\noobbaozi\\spike\\uvcache",
        "PYTHONIOENCODING": "utf-8"
      }
    }
  }
}
```

三个字段都不是装饰：

| 字段 | 为什么要写 |
|---|---|
| `cwd` | `python -m noobbaozi.mcp_server` 靠 **cwd** 找到 `noobbaozi` 包。客户端不支持 `cwd` 时，把仓库根加进下面的 `PYTHONPATH`（见 3.2）。 |
| `env.UV_CACHE_DIR` | uv 的缓存目录。不指定会落到用户级缓存（`%LOCALAPPDATA%\uv\cache`），与项目 `noobbaozi/spike/uvcache` 里的既有缓存不共享，首次启动会重新下载 SDE / 引擎。 |
| `env.PYTHONIOENCODING` | `utf-8`。Windows 默认代码页（cp936）会让子进程的 stderr 写崩、也会让管道里的中文变乱码。**stdout 我们按 UTF-8 字节读写**，这个变量管的是 stderr 与内部字符串。 |

> JSON 里 Windows 路径要写双反斜杠（`F:\\WorkSpace\\...`）。也可以写成
> `"F:/WorkSpace/opencode_general_workspace"` 这种正斜杠形式，两个都行。

### 3.2 已经装好依赖的 Python（省掉 uv 包装）

如果某个 venv 里已经有 `eveshipfit-dogma-engine[sde]`：

```json
{
  "mcpServers": {
    "noobbaozi": {
      "command": "python",
      "args": ["-m", "noobbaozi.mcp_server"],
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

当前握手信息：`serverInfo = {"name": "noobbaozi", "version": "1.0.0"}`，
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
python -c "from noobbaozi import mcp_server as m; print(m.INSTRUCTIONS); [print(n, '::', m._TOOL_DESCRIPTIONS[n][1]) for n in m.TOOL_NAMES]"
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
| `fit_explain` | 归因链：这个数**为什么**是这个数（A 可加 / B 乘性 / C 投影，见 §4.10） |
| `fit_sensitivity` | 灵敏度链：改一个输入（关模块 / 切状态 / 切模式）这个数会变多少（见 §4.11） |
| `fit_project` | 投射：一艘船**给**另一艘船什么（加成 / 遥修 / 负面）；**一给一**，不链式传递（见 §4.12） |
| `fit_engagement` | 交战数学：A 打得动 B 吗（命中 / 有效 DPS / 击穿时间 / **编队量级**，见 §4.13） |
| `zkb_search` | 战报列表检索（先筛 killmail_id，再对那条调 `fit_killmail`）；**每行带受害者驾驶员 / 军团 / 联盟名字** |
| `zkb_stats` | 实体画像（角色 / 公司 / 联盟）：战斗总量 + zkb 风格指标 + 7 天活动 + 6 类榜单；**公式未公开的指标只转述，不给威胁判词** |
| `zkb_related` | 星系交火总览：一个星系 + 一个时间点 → 双方阵营 / 舰船构成 / ISK / **killmail 清单**；**唯一能查任意历史窗口的入口**（见 §4.14） |
| `sde_search` | **名字反查**：用户口语里的名字（中英文都认）→ `type_id` / 中英文名 / 组 / 类别；`ambiguous` 与 `did_you_mean` 让 agent 知道自己**不确定**（见 §4.15） |
| `sde_type` | 类型详情：dogma 属性 / effect / 装配限制 / 技能要求 / 船体加成（**SDE 原值**，见 §4.16） |
| `can_fit` | 能不能装：探针配装 + 引擎违规 → `fits` / `blockers` / `remaining` / **`assumptions`**（见 §4.16） |

每个工具的「何时用 / 返回什么 / 限制」、每个参数的含义与默认值，都写在它的
`description` / `inputSchema` 里 —— **别在这份 README 里补第二份**（复制就会漂移；
`tests/test_mcp_server.py` 的三段守卫盯着这件事）。

配装类工具另接受三个可选参数（`zkb_search` 没有 `fit`，也不接受它们）：

| 参数 | 默认 | 可用值 |
|---|---|---|
| `skill_tier` | `all4+min_required` | `all4+min_required`、`all5` |
| `damage_profile` | `uniform` | `uniform`（25/25/25/25） |
| `state_policy` | `engine_default` | `engine_default` |

`fit_explain` 还多一个自己的参数：`depth`（`1` / `2`，默认 `2`，见 §4.10）；
`fit_sensitivity` 多两个：`ops`（数组，不给 = 自动计划）与 `max_ops`（默认 `32`，见 §4.11）；
`fit_project` 用 **`target`** 而不是 `fit`，并多四个：`sources` / `effects` / `buffs` /
`strict_state`（见 §4.12）；
`fit_engagement` 用 **`attacker` + `defender`**（两个配装输入）、必填 `distance_m`，另有
`mode` / `fleet` / `observed_hp` / `window_s` / `duration_s` / `points`（见 §4.13）。

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

### 4.5 `fit_parse` 的 `eft` 字段（T-54：规范文本由服务给，不要自己拼）

`result.eft` 是**从真实装配渲染出来的规范 EFT 文本**（字符串，可直接复制到 EFT / pyfa /
游戏剪贴板）。此前项目只有解析、没有渲染，agent 只能自己拼，实测拼出过「5 门 RLML +
50MN Micromassdrive」这种**不存在的配装**（真实是 3 门 RLML + 1 件隐身）。

格式（`project/eft.py` 是唯一实现）：

```
[Osprey Navy Issue, Killmail 139274776]      ← 首行 [船名, 配装名]；名字为空时写 [船名]

Damage Control II                            ← 低槽
Nanofiber Internal Structure II
...
                                             ← 组间空一行：低 → 中 → 高 → 改装 →（子系统）
Rapid Light Missile Launcher II              ← 同型模块**每件单独一行**（不写 ×N）
Rapid Light Missile Launcher II
Rapid Light Missile Launcher II
Improved Cloaking Device II

                                             ← 无人机 / 舰载机段（没有就不出现）
Warrior II x5
                                             ← 货舱 / 弹药段（loaded charge 也在这里，不进模块行）
Caldari Navy Scourge Light Missile x1232
```

- 换行一律 `\n`，末尾**没有**多余空行；
- `loaded charge`（弹药）**不写进模块行**，统一落在货舱 / 弹药段；
- **可往返**：`parse(emit(fit))` 的已装模块集合（`type_id` + 数量）与原始一致
  （`tests/test_eft.py` 钉住）；
- 渲染失败（名字查不到 / 槽位类型没有 EFT 表示）→ `eft: null` + 一条 `warnings`，
  **不编造任何一行**。看到 `null` 就如实说渲染不出来，别手写一份补上。

### 4.6 `resources.hardpoints`：高槽 ≠ 炮位，且「不占硬点」与「分不清」是两件事

`turret` 与 `launcher` 是**两种硬点**，都长在**高槽**模块上，但「高槽 5 个」**不是**
「5 个发射器挂点」—— 这是实测过的口径错（旧字段 `hardpoints_free` 直接输出船体的**基准
炮位数**，装 0 / 3 / 5 门发射器都是同一个数，已删除）。

```json
"hardpoints": {
  "turret":   {"total": 2, "used": 0, "free": 2},
  "launcher": {"total": 3, "used": 0, "free": 3},
  "non_hardpoint": 1,
  "non_hardpoint_modules": ["Improved Cloaking Device II"],
  "unattributed": 0
}
```

**高槽里的每一件只会落到四个去处**（`turret` / `launcher` / `non_hardpoint` / `unattributed`）：

| 字段 | 含义 | 三态 |
|---|---|---|
| `total` | 船体属性 102 `turretSlotsLeft` / 101 `launcherSlotsLeft`（**基准值**，引擎已叠加 hardpoint 修正） | `measured` |
| `used` | **数**高槽里已装的炮 / 发射器（按名字 / 组名关键词分类，规则见 `project/views/resources.py` 的 `HARDPOINT_*`） | `assumed` |
| `free` | `total - used`，**可以为负** —— 负数 = 非法装配，同时出 `hardpoint_violation` flag | `assumed` |
| `non_hardpoint` | 高槽里**确定不占硬点**的件数（隐身 / 智能炸弹 / 吸电 / 遥修…）。**一件都不进 `used`** | `assumed` |
| `non_hardpoint_modules` | 上面那件事的**样例名字**（最多 3 个，人读时不用猜是哪件） | `assumed` |
| `unattributed` | **分不清**归到哪一类的件数（`Festival Launcher` 一族：看不出是武器还是别的） | `assumed` |

> **为什么把 `non_hardpoint` 和 `unattributed` 拆成两个数**：拆之前它们挤在一个
> `unclassified` 里，于是「有一件我们认不出的武器」和「有一件确定不占硬点的隐身」长得
> 一模一样。实测踩过：一艘船报 `unclassified: 1`，agent 会以为可能藏了一件武器，实际是
> 那件**隐身**（确定不占硬点）。现在确定的进 `non_hardpoint`（还带样例名字），`unattributed`
> 只剩真正分不清的 —— 后者是「我们能力不足」，前者是「事实如此」，不能混。
>
> **注意字段名**：第四态叫 **`unattributed`**（不是 `unclassified`）。1/5 做过一次改名
> （`unclassified` → `unattributed`）：它表示的是「**没归因**」，而不是「归类为未分类」——
> 后者听起来像我们放弃了这个字段。

- 它**和 `slots` 并列出现在 L0**（`resources.slots` 与 `resources.hardpoints`）：
  agent 只看摘要时也能分清「5 个高槽」与「3 个发射器挂点」；
- `total` 缺失 → `free` 也是 `null`（不补 0），`used` 仍是数出来的真值；
- 超装：`hardpoints.launcher = {total: 3, used: 5, free: -2}` → `hardpoint_violation`
  flag。`evidence` 的形状是
  `{kind: "launcher", total: 3, used: 5, over: 2, over_kinds: "发射器超2"}`：L1 的 `message`
  与 L0 的 `hint`（`发射器超2`）**都从 `evidence.over_kinds` 生成**。两类同时超装时
  `kind` 取最严重的一类、另一类进 `others`（机器可读），`over_kinds` 是
  `"炮位超1+发射器超1"`（实测：Osprey 装 3 炮 + 4 发射器）。**`free` 为负绝不会静默通过**。
- 模块名一律按 **SDE 实名**匹配（`Small Tractor Beam II` 不是 `Tractor Beam II`，
  `Festival Launcher` 不是 `Festival Launcher I`）—— 写用例前先核 SDE。

### 4.6.1 无人机：`bandwidth.used` 是「全部放飞所需」，且两条上限互不替代

**`resources.drone.bandwidth.used` 的口径务必读准**：它是**机舱里全部无人机「全部放飞」
所需**的带宽（Σ `droneBandwidthUsed × 数量`），**不是当前已放飞的消耗**。

fit 文本与引擎 `Layout` 都**没有「已放飞」这个维度**（只有 `drone_bay`）——所以「已放飞」
在本产品里**根本不存在**。这不是缺陷，是输入形态决定的上限。

> **层在哪**：`fit_index`（L0）的 `resources` 摘要**不带 `drone`**（键集恒为 `cpu` /
> `powergrid` / `feasible` / `slots` / `hardpoints`）。读无人机要用 `fit_view("resources")`，
> 或 `fit_get("resources.drone.bandwidth.used")`（它会兜底到 L1）。

> 正确读法（f7 / Legion 实测）：「**25 架全放需要 175 Mbit/s，而总带宽只有 50**」，
> **不是**「现在已经放了 175」。

超限由**两条 flag 分别报，互不替代、可同时命中**：

| flag | 量的是 | 单位 |
|---|---|---|
| `drone_bandwidth_exceeded` | 带宽够不够放全部无人机 | **Mbit/s** |
| `launched_drones_exceeded` | 同时放飞的**架数**是否超上限 | **架** |

**一条命中不代表另一条也命中**：带宽够但架数超（放 5 架大船无人机）、或架数够但带宽超
（放 1 架超宽无人机）都会发生 —— 所以别用一条的结论代替另一条。

**`feasible` 不含无人机**：它只是 `cpu.free >= 0 and powergrid.free >= 0 and
calibration.free >= 0` 的三项合判（任一为负 → `false`，任一缺失 → `null`）。无人机带宽 /
架数**不在那三个数里**（主人裁决：不改 `feasible` 的定义）。

> 因此会遇到「**`feasible: true` 但无人机放不下**」。这时看两处，**都不要去 `blocked_by`
> 里找**：
>
> - **`result.flags`** 里那两条（L1 的 `evidence` 直接给超多少：带宽是
>   `{bandwidth_total, bandwidth_required, over, source}`，架数是
>   `{drone_count, max_launched, over}`）；
> - **`resources.upgrade_space[].inherits`** —— 配装**本来就有的**资源违规会作为继承项贴在
>   **每一个**候选上（实测 f7 的 16 个候选全是 `["drone_bandwidth", "launched_drones"]`）。
>
> 为什么不在 `blocked_by` 里：它只装**候选自己新引入**的维度（实测是 `powergrid` /
> `calibration` 之类），继承来的走 `inherits`（见 §4.9 ④）。f7 那 4 个候选在旧口径下曾报成
> `fits=false` + `blocked_by=["drone_bandwidth", …]`，T-54 的「`fits` 只看增量」裁决后改成
> `fits=true` + `inherits=[…]` —— 所以**这两条无人机上限不会出现在 `blocked_by` 里**。

### 4.7 `zkb_stats` 的调用注意（2026-10-19 实测）

`zkb_stats(entity, entity_type, include=None)`。**`entity_type` 必填**（`character` /
`corporation` / `alliance`）—— 同一串数字在三个命名空间里都可能存在，**不靠 id 猜**。

#### ① 错误分类：五种情况，五个互不相同的 `kind`（`zkb_search` 与 `zkb_stats` 同源）

zkb 的 `/api/stats/` **不用状态码表达失败**，所以判成败只能看正文。但**正文怎么归类
两个工具必须一致** —— 曾经 `{"error": "Invalid type or id"}` 在 `zkb_search` 里判
`network_failed`、在 `zkb_stats` 里判 `unknown_entity`，agent 无从判断该「换参数」还是
「等一会儿重试」。现在共用 `api._zkb_error_kind()`：

| 上游 / 自身情况 | `error.kind` | agent 该怎么反应 |
|---|---|---|
| `{"error": "Invalid type or id"}`（HTTP **200**） | `unknown_entity` | 换 id / 换 `entity_type` |
| 正文含限流词（`too many` / `rate limit` …） | `rate_limited` | 等 `hint.min_interval_s` 再试 |
| **HTTP 429 / 403** | `rate_limited` + `hint.min_interval_s` | 同上 |
| **空响应体（0 字节）** | `internal_error` + `warnings: ["empty_body: …"]` | 报障（见下） |
| **空壳文档**：只有 `activepvp` / `info`（**无 `name`**）/ `topIskKillIDs` / `topLists`，战斗字段全缺 | `unknown_entity` + `warnings: ["empty_entity: …"]` | 这个实体没有画像，**别当成 0 战绩** |
| 非 JSON / 被截断 / 5xx | `network_failed` | 重试或换时间 |
| **我们自己的异常** | `internal_error`（**唯一**该用它的地方） | 报障 |

> **`internal_error` 不是「网络不好」的意思**，它的语义是「**我们自己出 bug 了**」。
> 把限流报成 `internal_error`，agent 会去报告故障而不是等一会儿重试 —— 方向完全反了。
>
> 空体那条最阴：本层的 URL 构造**恒以 `/` 结尾**，所以线上看到 `empty_body` 意味着上游
> 异常（实测「少写结尾斜杠」就是这个现象），不是参数问题。

#### ② 四个 ratio 的公式未公开 —— 只转述，判词一律不给

`solo_ratio` / `avg_gang_size` / `danger_ratio` / `gang_ratio` 全部是 **zkb 自有指标**，
返回里 `style.formula` 恒为 `null`。实测反推不出公式（N=10 实体）：

| 候选式 | 与 `dangerRatio` 的最大偏差 |
|---|---|
| `shipsDestroyed/(shipsDestroyed+shipsLost)` | **22.18** 个百分点 |
| `pointsDestroyed/(pointsDestroyed+pointsLost)` | **7.47** 个百分点（最接近，但仍不吻合） |
| `iskDestroyed/(iskDestroyed+iskLost)` | **38.34** 个百分点 |

`avgGangSize` 的三个候选式（`attackersDestroyed/shipsDestroyed`、`attackersLost/shipsLost`、
两者合并）也都不吻合（30.70 / 64.95 / 29.08）。**别自己补一个「看起来像」的公式。**

两个量纲必须记牢：

- `solo_ratio` 是 **0–100 的百分数**（1 位小数），**不是 0–1 比例** —— 写 `1.8` 不是 `0.018`；
- `avg_gang_size` 是 **「人/次」的实数均值**（实测 2–38.7），**不是比例**；
- `gang_ratio + solo_ratio ≠ 100`（实测 100.0–103.7）—— 两者**不互补**，别互相反推。

**这几个数全部原样透传，一位都不加工。** zkb 没有承诺它们是整数：`dangerRatio: 12.5`
会被如实给成 `12.5`（早期实现用 `int()` 收口，把它截成 `12` —— 静默丢精度，已修）。
同类一起透传的还有：`gang_ratio` / `solo_ratio` / `avg_gang_size` / `solo_kills` /
`solo_losses` / `combat` 六项 / `activity_7d` 六项 / `top.*.{count,isk}` /
`entity.security_status`（zkb 给 `int 4` 就还是 `int 4`，不会被 `float()` 改写成 `4.0`）/
`blocks.months.*`。

#### ③ `efficiency` 是我们派生的，不等于 `danger_ratio`

```json
"combat": {"efficiency": {"value": 0.829, "kind": "derived",
                          "formula": "isk_destroyed / (isk_destroyed + isk_lost)",
                          "decimals": 4,
                          "note": "我们派生，与 style.danger_ratio 不是同一个东西"}}
```

同一角色实测：isk 效率 **82.9%**，而 zkb 的 `dangerRatio` 是 **69**。**把 82.9 说成「威胁度」就是错的。**

它是返回里**唯一**允许有「加工」的数（因为我们自己算的），所以加工必须写在明面上：
`kind: derived` + `formula` + `decimals`。zkb 的数不享受这个待遇 —— 它们没有 `formula` 可写，
所以**一个字节都不许改**。

#### ④ `top.locations` 不是星系

`top` 有且仅有 6 类：`characters` / `corporations` / `alliances` / `ships` / `systems` /
`locations`（**没有 `regions`** —— zkb 没有区域榜）。其中 `locations` 是**「天体 / 站点」级**：

```
Y5J-EU IV - Asteroid Belt 1        （小行星带）
OL3-78 (Star)                      （恒星）
Stargate (MQ-O27)                  （星门）
OSY-UD X - Moon 14                 （月球）
Thera VII / 1DH-SX VI              （行星）
```

**要看星系用 `top.systems`。** 空榜一律返回 `[]`，**键不会省略**（形状是契约）。

#### ⑤ 体积：默认约 3 KB，大块必须显式 `include`

默认（`include=None`）实测紧凑 JSON：角色 **1,910 B** / 公司 **2,706 B** / 联盟 **3,215 B**，
**都 ≤ 4 KB**（守卫断言 `<= 4096`）。四个大块只能点名索取：

| 块 | 角色 | 公司 | 联盟 |
|---|---:|---:|---:|
| `months` | 9,213 / +11.2K | 1,720 / +4.5K | **33,601 / +36.9K** |
| `rankings` | 1,215 / +3.2K | 1,964 / +4.7K | 2,314 / +5.6K |
| `top_all_time` | 7,776 / +9.7K | 8,055 / +10.8K | **26,085 / +29.4K** |
| `groups` | 4,355 / +6.3K | 3,994 / +6.8K | **16,630 / +19.9K** |
| 四块全要 | 24.7 KB | 18.6 KB | **~82 KB ← 触发 `large_response` 软警告** |

- **没有「全量」开关**：原始全量联盟响应 168 KB，逐个点名是唯一的门槛；
- 单块上限 64 KB（实测最大 33.5 KB，正常不触发）；超了才截断 + `truncated: true`；
- 整个响应 > 64 KB **不截断**，只追加 `warnings: ["large_response: …"]` —— **隐式截断比大响应更坏**；
- `months` 只补 `[首次有数据月, 末次有数据月]` **之间**的空洞（`filled_internal_gaps`），
  **不向两端外扩**：`range` 是**观测区间**，不是「实体存在区间」。缺失 ≠ 零。

### 4.8 `fit_killmail` 的原文来源：zkb 优先，ESI 后备

冷路径（联网、进程刚起）原来有**四段串行**外部请求：

```
zkb /api/killID/<id>/            0.86–1.06 s   ← 只有它给 hash / 价值块
ESI /killmails/<id>/<hash>/      0.72–2.63 s   ← 依赖 hash
ESI POST /universe/names/        0.67–0.98 s   ┐ 互不依赖
ESI /universe/systems/<id>/      0.66–0.71 s   ┘
```

现在有两处变化，**都不改变数据语义**：

**① zkb 的 `/api/killID/` 本身就带整条 killmail。** 实测 16 条真实战报，
zkb 原文与 ESI 原文**逐字段一致**（`attackers` 条数与每个字段值、`victim` 全字段、
`victim.items` 逐项）。所以按 `victim.items` **分三种**（判的是「键在不在」，不是「真不真」）：

| `victim.items` | 我们的行为 | `killmail_origin` |
|---|---|---|
| **键不存在** | ❌ 可疑（可能是 zkb 版本 / 裁剪差异）→ **回落 ESI** + `warnings` 说明 | `"esi"` |
| **键在但为 `[]`** | ✅ 短路，不请求 ESI；**并写一条 `warnings`** | `"zkillboard"` |
| **键在且非空** | ✅ 短路，不请求 ESI | `"zkillboard"` |
| 调用方直接贴 killmail JSON | 不联网 | `"local"` |

覆盖率实测：16 条样本里 **13 条（81%）带非空 items**，3 条是 `[]` —— 那 3 条在
**zkb 与 ESI 里都是空**（蛋 / 空船）。所以按「键存在」放宽后，短路覆盖**接近 100%**。

> **空数组为什么可以短路而键不存在不行**：`[]` 是**事实**（这条战报本来就没模块），
> 键整个不见则是**疑点**（这份响应可能不是完整的那一版）。拿不准就回落 —— 宁可多花
> 一次 1.7–2.6 s，也不猜。
>
> **空数组必须说出来**：`items: []` 与「我们漏了模块数据」在返回里长得一模一样，
> agent 看不到我们的取证，所以短路时会补一条
> `"该战报没有已装模块（zkb 与 ESI 均为空）"`。

**② `names` 与 `systems` 并行。** 两者互不依赖（一个要 id 集合、一个要 system_id），
现在 `universe_system()` 在工作线程里跑、`names` 留在主线程，join 之后再装配
（星系名要用 names 的结果，所以**只能并行取数，不能并行装配**）。

> `names` 为什么不进线程：它有批间闸门（`_UNIVERSE_NAMES_MIN_INTERVAL_S`，靠模块级
> 时间戳记账）与进程内缓存字典。放进线程虽不坏（缓存有锁），但闸门的「与上一次请求
> 至少隔 0.05 s」在并发下就不再是全局串行 —— 让最娇的那条留在主线程，省这份复杂度。

**`killmail_source` 与 `killmail_origin` 是两个维度**，别混：

| 字段 | 取值 | 说的是 |
|---|---|---|
| `killmail_source` | `network` / `local` | **输入形态**：链接 / id / id+hash 要联网，还是调用方直接贴的 JSON |
| `killmail_origin` | `zkillboard` / `esi` / `local` | **原文从哪来**（只有它区分 zkb 与 ESI） |

> **反直觉但正确**：**ESI 链接**（`https://esi.evetech.net/latest/killmails/<id>/<hash>/`）与
> `{killmail_id, hash}` 对象都**先问 zkb**，所以它们的 `killmail_origin` 通常仍是
> `"zkillboard"` —— **不是** `"esi"`。`"esi"` 只在 zkb 原文**缺 `victim.items` 键**、真的
> 回落时才出现。实测 139239127：zkb 链接 / ESI 链接 / id+hash 对象三种输入全是
> `killmail_source=network` + `killmail_origin=zkillboard`，本地 JSON 是 `local` + `local`。

实测收益（本地网络，带 items 的战报）：端到端冷调用 **2.48 s → 1.59 s**；
回落路径（**`victim.items` 键不存在**时才走）只省掉并行那一段（约 4.26 s → 3.94 s）。
热命中仍是 **0.1 ms** 量级（结果缓存）。

### 4.9 L2（`fit_detail`）的形状与两个新字段

#### ① `result` 的形状：`{ship, flags, views}` —— `meta` **不在**里面

```jsonc
{"ship":  {"name": "Hecate", "group": "Tactical Destroyer", "type_id": 35683},
 "flags": [ /* 不属于任何视角的诊断，见下 */ ],
 "views": {"resources": {...}, "stability": {...}, ...}}
```

**`meta` 在信封上**（`envelope["meta"]`），不在 `result` 里；detail 的信封 `meta` 额外带
`assumptions`（512 条完整技能表）。三层分工记牢：

| 层 | 技能档在哪 | 完整技能表 |
|---|---|---|
| L0 `fit_index` | `result.meta.skill_tier` = `{name, count, hash}` | ❌ |
| L1 `fit_view` | **没有 `meta`** → 看信封 `meta.skill_tier` | ❌ |
| L2 `fit_detail` | 看信封 `meta.skill_tier`（`result` 里没有 `meta`） | ✅ 信封 `meta.assumptions.skill_tier` |

**`meta.skill_tier.hash`** 是技能档的稳定摘要（sha1 前 12 位，按 `type_id` 排序后算）。
`count` 相同**不代表内容相同**（同为 512 条、某个技能 4 级 vs 5 级时 count 一模一样）——
判断「两次分析是不是同一个技能档」**只能靠 hash**，且 L0 与所有入口信封的 hash **同源可对**。

#### ② 顶层 `flags`：装「不属于任何视角」的诊断（一条都不许丢）

`flag["view"]` 若是**不存在的视角名**（或压根没有 `view`），它归不进任何视角。这类 flag：

- L0 出现在 `result.flags`（`{id, hint}` 压缩形）；
- **L2 出现在 `result.flags`（完整形：`id` / `view` / `message` / `evidence`）**；
- 没有时是 `[]`（形状恒定）。

> 为什么必须单独兜底：`render_detail()` 只构建**被请求的**视角，它内部的 residual 会被
> 丢掉。若不补这个桶，同一条诊断会在 L0 看得见、到 L2 深挖时**彻底消失** —— 那就是
> 「静默丢诊断」。顶层桶的归位目标是**全部六个视角**，所以它与本次查哪个视角**无关**
> （`view="mobility"` 也一样看得到 firepower 的孤儿 flag 不会混进来）。

#### ③ `stability.cap_sensitivity[]` 的 `becomes_stable`

每条 = `{module, type_id, cap_use_gj_s, stable_pct, becomes_stable, depletes_in_s,
stable_delta_pct}`。`becomes_stable` 说的是「**只摘掉这一个模块**，电容能不能稳住」。

**`becomes_stable: false` ≠ 「摘掉它也没用」**：它只回答「摘掉**这一个**够不够」，
摘掉之后**仍可能不稳**（还有别的耗电大头）。实测跨样本 **225 条里 80 条不翻转**。
要判断「摘掉哪些才够」得看 `stable_delta_pct` 与其它条目的组合，别把 `false` 读成
「这模块不耗电」。

#### ④ `resources.upgrade_space[]` 的 `blocked_by`

每条 = `{slot, module, module_type_id, candidate, candidate_type_id, meta_level_now,
meta_level, cpu_free, power_free, calibration_free, fits, inherits?, blocked_by?}`。

- `fits: true` → 候选装得上，**没有 `blocked_by` 键**；
- `inherits` **仅当非空时出现**（候选没继承任何违规 → 这个键整个不存在）；
- `fits: false` → 多一个 **`blocked_by`**，列出**卡在哪一项**：`cpu` / `powergrid` /
  `calibration` / `charge_capacity` 等。例：

```json
{"slot": "high", "module": "Expanded Probe Launcher I",
 "candidate": "Core Probe Launcher II", "fits": false,
 "blocked_by": ["charge_capacity"], "inherits": ["cpu", "powergrid"]}
```

  `blocked_by` 是「**缺什么**」的答案，`inherits` 是「这个候选**继承**了哪几项资源约束」——
  两者不是一个意思，别互相替代。`fits: true` 时**不要**去读 `blocked_by`（它不存在）；
  `fits` 只看**增量**（候选自己新引入的违规），所以 `fits: true` 的行**可以**带 `inherits`
  —— 那是配装的老毛病，不是这个候选的锅（实测 f7 的无人机超限就落在这里，见 §4.6.1）。

### 4.10 `fit_explain`：归因链（这个数**为什么**是这个数）

`fit_explain(fit, metric, depth=2)`。`result` **就是** `project/explain.py` 的返回，
接口层**不改形状、不裁剪、不取整**（唯一动作是深拷贝一份，见本节末）。

**`metric` 是登记过的叶子指标，不是任意路径**（登记表 = `explain.supported_metrics()`，
目前 9 个）：

| 类 | `combine` | 指标 | 判据 |
|---|---|---|---|
| **A 可加** | `additive` | `survivability.ehp_mixed` / `firepower.dps.base` / `resources.cpu.load` / `resources.powergrid.load` | 引擎该属性的源**全是** `mod_add` / `mod_sub` |
| **B 乘性** | `multiplicative` | `mobility.speed_mps` / `stability.capacitor.peak_recharge` | 源是 `pre_mul` / `post_mul` / `post_div` / `post_percent` |
| **C 投影** | `multiplicative` | `survivability.thinnest.{shield,armor,hull}.ehp` | 投影层自己算，引擎**没有**这个属性 |

**三类口径不许混**（混了验收立刻变永真或永假）：

- **A 可加**：`Σ sources[].contribution == value`（另带 `sum_of_contributions`；`unexplained`
  是真 0）。所以 `contribution` **必须**保持全精度 —— 舍入会立刻破坏这条加和判据。
- **B 乘性**：给 `base` + **有序因子链**（`factor` / `operator` / `penalty`）+ `replayed` /
  `replay_ok`。**因子不能相加** —— 这些行的 `contribution` 一律 `null`。注意 `factor` 是
  **算子原始值**：`post_percent 586.45` 要按 `1 + 586.45/100` = ×6.8645 理解，`post_div`
  是除数，`pre/post_mul` 还要按堆叠惩罚折算（CLI 人话出口印的是**折算后**的乘数）。
- **C 投影**：`value = 层 HP ÷ 该层最大共振率`；`sources` 恒为**两项**（被除数 + 除数，
  除数在 `factor` 里），并带 `formula` / `damage_type` / `tied`（四系并列时 `damage_type`
  是 `null`、`tied: true`）。

**`depth`**：`1` = 指标的直接贡献者；`2`（默认）= 每个贡献者**自己的**源再展开一层。
超出 `1..2` → `invalid_argument` + `field="depth"`（**不是** `-32602`：换参数是业务侧的
动作）。超过深度的节点 `children: []` + `truncated: true`，并计入 `truncated_nodes`。

**`value` 是全精度**（`9102.577816172383` 这种），**不是 L1 的 3 位小数** —— 展示时自己
舍入；判断「加起来对不对」必须用全精度。

**`provenance` 与 `combine` 正交**：`ehp_mixed` / `dps.base` / `peak_recharge` 的三态都是
`assumed`（它们映射负 ID 的 pass-4 派生值），但它们**可以是**可加的。别拿三态判断可加性。

**体量**（`result` 紧凑 JSON，实测 f1）：`firepower.dps.base` **6.7 KB** / `resources.cpu.load`
**6.3 KB**（源链最长），其余 1.7–3.1 KB。默认 `depth=2`。

**错误口径**：

| 情形 | `error.kind` | 带什么 |
|---|---|---|
| `metric` 不在登记表（含空串） | `invalid_path` | `path` + **`hint` = 当前登记表全集** |
| `metric` 在表里、但本配装取不到值 | `invalid_path` | 同上（这一类指标对本配装不适用） |
| `depth` ∉ 1..2（含 `true` / `"2"`） | `invalid_argument` | `field="depth"` + `hint=[1, 2]` |

> **返回值已深拷贝**：`explain` 内部那个对象是**共享 LRU**（`sources=True` 2.95 ms/次，
> 不该重复算）；若把引用直接递出去，调用方就地改一个字段就会污染下一次调用。接口层复制
> 一份（6 KB 量级）把「只读」从口头约定变成结构保证；`api.clear_caches()` 也会**连
> `explain` 的 LRU 一起清**（否则「改完实现测试仍绿」那个老坑会在归因入口重演）。

### 4.11 `fit_sensitivity`：灵敏度链（改一个输入，这个数会变多少）

`fit_sensitivity(fit, metric, ops=None, max_ops=32)`。`explain` 回答「这个数由什么构成」，
它回答「**关掉那件 / 换个状态 / 换个模式**，这个数会变多少」—— 每个 op 都**重新进引擎
算一遍**（实测 0.1–0.33 ms/次），不是算术推的。

**`metric` 比 §4.10 宽 5 个**：`explain` 的 9 个 + 5 个**单值**指标
（`stability.capacitor.{stable_pct,peak_load,capacity}` / `resources.cpu.free` /
`resources.powergrid.free`）。这 5 个在返回里的 **`combine == "single"`** —— 第四种取值：
既不是相加、也不是相乘，就是一个单值（别拿它去套 A/B/C 的判据）。

**三种 op**（`ops=None` = 自动计划：每件已装模块 `disable_item` 一次 + 每个非当前模式
`set_mode` 一次）：

| `op.kind` | 语义 | 必填字段 |
|---|---|---|
| `disable_item` | 关掉第 N 件已装模块（`state = offline`，**不删件**） | `item_index` |
| `set_state` | 把第 N 件设为 `offline` / `online` / `active` / `overload`（`off` 是别名） | `item_index` + `state` |
| `set_mode` | T3C / T3D 模式切换 | `mode_id` |

**返回形状**：`{metric, combine, baseline, changes[], warnings[], ops_planned,
ops_executed, skipped, truncated, max_ops, provenance, tolerance}`；每条
`changes[i] = {op: {kind, item_index|mode_id, label, state?/mode_name?}, value, delta,
delta_pct, note}`。

- **`delta = value - baseline`**（每位都成立，可自证）；`delta_pct = delta / baseline`
  是**比例**（不是 0–100 的百分数），而且 **`baseline == 0` 时是 `null`** —— 不适用，
  不是 0、也不是无限大；基线为**负**时百分比符号会反过来（看 `delta` 更直观）；
- **计数恒等式：`ops_planned == ops_executed + skipped`**（恒成立）。三个数的口径：
  `ops_planned` 是**考虑过**的 op 总数（**含空操作**）/ `ops_executed` 是**真产出一条
  `changes[]`** 的 op 数 / `skipped` 是**没产出**的总数。
  `skipped` = 空操作（模块已 offline、状态或模式已是目标值、**重复 op**、
  `mode_not_available`）+ 被 `max_ops` **砍掉**的 + `variant_error` +
  `metric_missing_after_change`。**`skipped` 只是总数，逐条原因看 `warnings[].kind`**
  （见下表；`noop` 是空操作的汇总条、带 `count`，一个被跳过的 op 可能同时计入
  `noop.count` 和某个具体原因条目 —— 所以别把各 kind 的条数直接相加当 `skipped`）；
- **`truncated`** 是布尔：是否被 `max_ops` 截断（截断时另给一条 `truncated` warning，
  写明可执行计划数、空操作数与执行数）；
- **坏 op 不是错误**：未知 `kind` / 越界 `item_index` / 货舱件 / 非法 `state` /
  非法 `mode_id` → 进 `warnings`（`invalid_op`）并**不计入** `ops_planned`（它压根没成为
  计划的一部分），其余 op 照跑（这是**数据问题**，不是参数问题）。

**`warnings[].kind` 全集**（`skipped` 的原因都在这里，别只看计数）：

| kind | 什么时候出现 | 计入 `skipped`？ |
|---|---|---|
| `noop` | **空操作汇总**（带 `count`）：模块已 offline、状态/模式已是目标值、重复 op | ✅（`count` 就是条数） |
| `truncated` | 可执行 op 数 > `max_ops` | ✅（被砍掉的那些） |
| `variant_error` | 变体交给引擎后抛异常（本场景的正常输出，不猜 0） | ✅ |
| `metric_missing_after_change` | 变体算出来了，但这个指标取不到（不补 0） | ✅ |
| `mode_not_available` | 给定的 `mode_id` **不在本船的可切换模式**里 | ✅（同时计入 `noop.count`） |
| `modes_unknown` | 本船的模式在本地 SDE 索引里**枚举不到**（T3C 常见）→ 自动计划不含 `set_mode`，或显式 mode 无法判断是否适用 | ❌（**维度未覆盖**，不是某条 op 被跳过） |
| `no_index` | 压根没有 SDE 索引 → 无法枚举模式，只给逐件 op | ❌ |
| `invalid_op` | 单条 op 不合法（未知 kind / 越界 / 货舱件 / 非法 state / 非法 mode_id） | ❌（没进计划） |
| `invalid_ops` | `ops` 整个不是数组 | ❌ |

> **`modes_unknown` 是「没测」而不是「测了没影响」**：T3C 的模式在本地索引里常常枚举
> 不到，那时自动计划**不含** `set_mode` 维度 —— 如实说「这一维没覆盖」，别读成
> 「切模式对这个数没影响」。

**两条容易误读的口径**（实测）：

- **全 offline ≡ 同型空配装**：把每件模块设成 `offline` 之后，9 个 `explain` 指标里有
  **8 个与同型空配装逐位相同**（`ehp_mixed` / `cpu.load` / `powergrid.load` /
  `speed_mps` / `peak_recharge` / `thinnest.*.ehp`）；**唯一例外是 `dps.base`**
  —— 全 offline 是 **`0.0`**（有武器但没生效），空配装是 **`null`**（压根没有武器属性）。
  所以别拿「全关掉」去反推「空船基线」，差的那一项是「有没有武器」这个事实。
- **`sensitivity` 没有自己的进程级缓存**（每个变体走 `engine.calculate`，命中引擎自己的
  LRU）；跨调用的结果是 **api 的结果缓存**（key 含 `metric`、规范化后的 `ops`、`max_ops`）。
  `api.clear_caches()` 清的是 **api 结果缓存 + `explain` 的 LRU**，没有「sensitivity 的
  LRU」这个东西 —— 别在文档或测试里把它当成第二层要清的对象。

**错误口径**：

| 情形 | `error.kind` | 带什么 |
|---|---|---|
| `metric` 不在登记表（含空串） | `invalid_path` | `path` + **`hint` = 14 个可用指标** |
| `metric` 在表里、但基线取不到 | `invalid_path` | 同上 |
| `max_ops` 不是正整数（`0` / `-1` / `true` / `"8"`） | `invalid_argument` | `field="max_ops"` + `hint={min, default}` |

**体量 / 耗时**（实测 f1，`max_ops=32` → 自动计划 20 个 op）：`result` 紧凑 JSON
**3.7–4.3 KB**，单次 **46–164 ms**（首次数值含引擎预热）。所以按需给 `ops`、别在循环里
对同一配装重复枚举（幂等靠 api 结果缓存，见上）。

> **只给数值变化，不给判词**：返回里没有「该不该拆」的结论（红线：多视角、无判据）。
> 拆不拆由用户按自己的目标决定 —— 同一个 `delta` 对「跑得动」和「打得多」意义不同。

### 4.12 `fit_project`：投射影响（一艘船**给**另一艘船什么）

`fit_project(target, sources=None, effects=None, buffs=None, strict_state=True)`。目标是 `target`
（**不是** `fit`），`sources` 是要把能力投过来的来源船。六个配装工具的最后一环：`projection`
把来源船的 `outgoing` 塞进目标的 `incoming`，再算一次目标 —— **不重算任何 dogma**。

**这是「一给一」，不是「编队」**（最重要的口径）：

- 一次调用 = **一个目标** + N 个来源的**并集**；
- 投射**不链式传递**：目标收到的 `incoming` 不会变成它的 `outgoing`（实测 B 收 A 的 web 后
  `outgoing` 逐项不变）；
- 编队只能由调用方**并联**：对每个目标各调一次，边自己枚举。

**三条引擎语义**（决定了 `strict_state` 的存在；实测见 `projection.py`）：

| 语义 | 事实 | 我们的处理 |
|---|---|---|
| `effects`（遥修 / web / 点） | **严格按 `state == "active"` 产生**，offline 的不出 | 原样采信（引擎已过滤） |
| `buffs`（指挥脉冲） | **只看「模块在 + 弹头在」，与 `state` 无关** —— offline 的指挥爆发器照样给 | `strict_state=True`（默认）→ 自己按 active 重算一遍再采信；`False` → 引擎原样 |
| 命令舰自己 | 会被**自己的**脉冲加成（引擎行为，与直觉不同） | 保留，并给 `self_buff_included` warning（注明「未与游戏内行为对拍」） |

两种 `strict_state` 都**一定会给 warning**（`buff_state_filtered` / `buff_state_not_filtered`），
差异不静默。实测 f14（burst 置 offline）→ f15：`strict=True` 是 +0 / `strict=False` 是
**+4,962.776 EHP**（68,200.973 → 73,163.749）。

**返回形状**：`{strict_state, target, applied{effects[], buffs[]}, sources[], delta, repaired,
warnings[]}`。

- `delta.metrics[]`：**固定 10 条** `{path, before, after, delta, delta_pct, unit}` ——
  EHP 混合 + 三层 / 速度 / 电容六项；`delta_pct` 是**比例**，`before == 0` 时是 `null`；
- `delta.attributes[]`：**每个真的变了的**船体属性 + `attributed_to[]`（把 `projected.index`
  映射回「谁造成的」）；
- `repaired`：遥修落点 —— **它改的是「回血速度」，不是 EHP**，两者在返回里是分开的：

  ```
  repaired.armor.effective: 0 → 675.23 hp/s（+675.23）     ← 有效（按抗性折算）
  repaired.armor.rate     : null → 387.41 hp/s（delta null）← raw，源船没这条属性时一侧为 null
  ```

  **看 `effective` 那一侧**；`delta: null` 表示「一侧缺失，不可比」，不是 0 变化；
- `applied.effects[]` / `applied.buffs[]`：合并后的投射内容，每条带 `from`（谁给的）；
  `buffs` 按 `id` 取 **max**（引擎自己也取 max，顺序无关）。

**错误归属**（四条，各有测试）：

| 情形 | `error.kind` | 带什么 |
|---|---|---|
| `sources[i]` 解析失败 / 形态不对 | `invalid_argument` | `index=i` + `field="sources[i]"` + `cause`（原错误 kind） |
| `sources` 整个不是数组 | `invalid_argument` | `field="sources"` |
| `strict_state` 不是布尔 | `invalid_argument` | `field="strict_state"` |
| `effects` 里 `effect_id` 未知 | **不是错误** | `warnings[]` 里 `unknown_effect_id`（含可用示例编号），**结果照给** |
| 目标超载 / 非法配装 | **不是错误** | 照常给数值 + 该配装自己的 warnings |

> `effect_id` 是**信任边界**：引擎对未知 `effect_id` **静默忽略**（无异常、无 warning）。
> 所以显式 `effects` 会先查 `projection.effect_catalog(type_id)`，不在目录里就报出来 ——
> 不跟着引擎静默。

**成本 / 体量**（实测）：`projection.project` 本身 **5–7 ms**；本层端到端 **17–27 ms**
（1–2 个 killmail 来源，含两份配装的归一化 + 建上下文；无来源 7 ms）；缓存命中 **≈0.1 ms**；
`result` 紧凑 JSON **≈5.6 KB**。

> **不做**：命中率 / 有效 DPS（交战数学另模块）、距离 / 视线 / 持续时间 / 叠加上限（引擎
> 本来就没有这些维度）、「该不该这么配」的判词。

### 4.13 `fit_engagement`：交战数学（A 打得动 B 吗）

`fit_engagement(attacker, defender, *, distance_m, angular_velocity=0, target_velocity=0,
include_wrecking=True, mode="exchange", fleet=None, observed_hp=None, window_s=None,
duration_s=None, points=6)`。**`distance_m` 必填** —— 距离与角速度是**输入**，本层不做
几何假设（不做「中心到中心」那种假设）。

**三个 `mode`**：

| `mode` | 调什么 | 回答什么 |
|---|---|---|
| `exchange`（默认） | `combat.exchange` | A ↔ B 双向换血：各自有效 DPS + 击穿时间 + **谁先死** |
| `timeline` | `combat.timeline` | 双方同时开火、按有效 DPS **线性扣血**的采样（`points` 至少 2） |
| `anchor` | `combat.explain_anchor` | **战报锚点**：能否在 `window_s` 内打出 `observed_hp`（需同时给这两个） |

**三条必须记住的边界**：

1. **`hp_pool` 用来和战报比，`ehp` 用来算击穿时间 —— 两个数用途不同**。每个单方向结果里
   两者都有，并各带 `compare_with` 说明：`hp_pool`（盾+甲+结构 HP，与战报 `damage_taken`
   同量纲）vs `ehp`（引擎 `-43` 的原始伤害口径，`ttk = ehp ÷ 有效 DPS`）。
   拿 `ehp` 去比 `damage_taken` 会得出「战报只打掉 16%」这种错结论。
2. **未建模的武器只给下界**。已建模：炮台（hybrid/laser/projectile，命中率 + 伤害倍率）、
   导弹（应用系数）。未建模：无人机 / 舰载机 / 智能炸弹 / 分解器等 → 该件 `status:
   "unsupported"` + `lower_bound_dps`（名义 DPS 的完美应用值，**不猜**）；
   `totals.coverage < 1` 时 `time_to_kill_s` 是**上界**（少算武器 → 击穿时间偏长）。
3. **`fleet=N` 是「同型 × 数量」的量级模型，不是个体配装**。响应带
   `fleet_assumption: true` + `fleet_note` + flag `order_of_magnitude_only`；
   真实编队不可能全同型，所以它**不可用于精确对拍**，只做量级校验（`magnitude_check`，
   判据是预测/观察落在 [1/10, 10]）。数量来源：`fleet=N` 或 `{"count": N}` /
   `{"killmail_attackers": fit_killmail(...)["attackers"]}` —— **不手填、不猜**。

**战报锚点（`mode="anchor"`）怎么读**：

- `explainable`：是否存在**某个几何格**能在窗口内打出这么多血（`region` 是格子列表）；
  `false` 时看 `reasons`（含「窗口内最多只能打掉 X 点、最短需要 Y 秒」）；
- `best`：需要时间最短的那一格（`range_m` / `angular_vel` / `window_needed_s`）；
- **参数是扫描变量，不是拟合变量** —— 没有任何数被调过让锚点对上；
- `caveats` 永远存在：血量口径是**扣掉的血**（不是 EHP）、护盾被动回充与遥修未建模、
  技能按 `all4+min_required` 假设、攻方有 unsupported 武器时覆盖率 < 1；
- **窗口 ≠ 交战时长**：战报里的「9 秒」可能是两次死亡的**间隔**，实际开打时刻不可知。

**错误归属**：

| 情形 | `error.kind` | 带什么 |
|---|---|---|
| `distance_m` 缺失 / 非数 / 负数 / nan / inf | `invalid_argument` | `field="distance_m"` |
| `attacker` / `defender` 解析失败 | `invalid_argument` | `field="attacker"` / `"defender"` + `cause` |
| `mode` 不认识 / `fleet` 形态不对 / `anchor` 缺 `observed_hp` 或 `window_s` | `invalid_argument` | `field` + `hint` |
| `target_velocity` 在 `exchange`/`timeline` 下给非 0 值 | `invalid_argument` | `field="target_velocity"`（那两条按双方 maxVelocity 自动填，**明确拒绝 > 静默忽略**） |
| **一方没有对敌武器** | **不是错误** | `ttk=null` + `flags: ["no_weapons: …"]`，人话出口印「无对敌武器，无法对算」 |

**成本 / 体量**（实测 Hecate ↔ Raven @1 km）：`exchange` 端到端 **15–57 ms**（含两次
归一化；首次含引擎预热），缓存命中 **≈0.2 ms**；`result` 紧凑 JSON **≈11.4 KB**
（`exchange` 最大，因为两侧各有一份完整单方向结果）；`timeline` ≈1 KB、`anchor` ≈0.7 KB、
`fleet` ≈1.5 KB。

> **不做**：电容 / 距离控制 / 后勤 / 跑位几何 / 有效 DPS 之外的命中率场景；不判「该不该
> 打」（那是人的决定，本层只给数）。

### 4.14 `zkb_related`：星系交火总览（**唯一能查任意历史窗口的入口**）

`zkb_related(url=None, system=None, time=None, *, limit=20)`。

**为什么需要它**：zkb 的列表接口（`zkb_search`）已经**停用** `startTime` / `endTime`
（`because of abuse`），只能 `pastSeconds` + `page` 往回翻 —— 等于**查不了任意历史窗口**。
而 `/api/related/<systemID>/<YYYYMMDDHHMM>/` 把时间放在**路径里**，所以任意历史窗口都能查。
它还额外给两样别处拿不到的东西：**阵营划分**（`teamA` / `teamB`）与每条 kill 的完整明细块。

**两种给法（二选一，同时给会 `invalid_argument`）**：

| 给法 | 例子 |
|---|---|
| `url`（首选） | `https://zkillboard.com/related/30004614/202609091400/`（**域名不限**，见下节；查询串会被忽略） |
| `system` + `time` | `system="AV-VB6"`（名字或 id）+ `time="202609091400"` |

> `time` **只认 12 位** `YYYYMMDDHHMM`。`2026-09-09 14:00` / 10 位 / 13 位一律
> `invalid_argument`（`field: "time"`）—— **不做宽松解析**：把「我猜你是 14 点」当成
> 用户意图，比直接报错危险得多。

#### 接受的链接形态（**域名不限**）

工具只从**路径**里取两个参数：`/related/<systemID>/<12 位时间戳>`。**host 是什么不影响结果**，
所以第三方战报站（evetools 系）的 related 链接可以**直接**贴进来。

| 输入 | 结果 |
|---|---|
| `https://zkillboard.com/related/30004614/202609091400/` | ✅ |
| `https://br.evetools.org/related/30004614/202609091400` | ✅（无尾斜杠也认） |
| `https://kb.evetools.org/related/31001152/202010250400/` | ✅ |
| `…/related/30004614/202609091400/?page=1`（带查询串） | ✅ 查询串被**忽略** |
| 非 `/related/<id>/<12 位>` 形态（如 `/kill/139275237/`） | ❌ `invalid_argument`（`field: "url"`） |

> **数据源始终是 zkillboard；第三方站点的域名只用于提供 `systemID` + 时间戳。**
> 换句话说：`br.evetools.org/related/…` 只是「换了个地方贴同一组参数」，返回的战况
> **不是**从那个域名取的。（实测：任意 host 都会被接受，包括 `localhost` —— 因为我们
> 根本不读 host；请求永远发到 `zkillboard.com/api/related/<systemID>/<时间戳>/`。）

**返回是摘要，不是原响应的转储**。原始响应实测 **212 KB**（AV-VB6 / Fountain，23v23），
摘要约 **4–5 KB**：

```jsonc
{"system": {"id": 30004614, "name": "AV-VB6"}, "region": "Fountain",
 "window": {"startTime": "2026-09-09 13:00", "endTime": "2026-09-09 16:00",
            "exHours": 1, "complete": true},
 "sides": {"teamA": {"pilots": 21, "corporations": 10, "alliances": 1,
                     "ships": [{"ship": "Dominix", "group": "Battleship", "count": 5}],
                     "isk": 1585949032.3,
                     "kills": [{"killmail_id": 138324530, "damage_taken": 450,
                                "attacker_count": 4, "time": "2026-09-09T14:11:22Z"}],
                     "kills_total": 10, "kills_truncated": false},
           "teamB": {"…": "同上"}},
 "killmail_ids": {"teamA": [138324530, "…"], "teamB": ["…"]},
 "notes": ["exHours 是窗口半径…"],
 "truncated": {"kills": false, "limit": 20, "omitted": {"teamA": 0, "teamB": 0}}}
```

**四个必须知道的口径**（也在返回的 `notes` 里）：

1. **`exHours` 是窗口半径，且窗口不对称**：实测 `startTime = time − exHours`、
   `endTime = time + 2×exHours`（`time=14:00` / `exHours=1` → `13:00..16:00`，**3 小时**）。
   它说的是「这个时间点附近有多热闹」，**不等于整场战斗的时长** —— 别拿窗口宽度当战斗长度。
2. **`complete` 只是 zkb 自己的判断**：`true` 不代表没有遗漏（它不解释判据）。
3. **相邻星系没有被统计**：`adjacentSystems` 只给名字，`summary` 只含 `systemID` 这**一个**
   星系；要邻星系得各自再查一次（本工具不会替你递归）。
4. **`limit` 只截逐 kill 明细**：`sides.*.kills` 是前 `limit` 条（新的在前），被截时
   `kills_truncated: true` + `truncated.omitted` 给出每边省略几条；
   **`killmail_ids` 永远给全**（按时间倒序）—— 下一步就是拿它们喂 `fit_killmail`。

**命名约定（故意混排，但有规则）**：回显 zkb 原生字段的地方保持它的拼写
（`window.startTime` / `exHours` / `complete`），**我们派生的字段一律 snake_case**
（`killmail_id` / `damage_taken` / `attacker_count`）。

**两种时间格式并存**：`window.startTime` / `endTime` 是 **zkb 原文的字符串**
（`YYYY-MM-DD HH:MM`）；`kills[].time` 是本层转成的 **ISO 8601 UTC**
（与 `fit_killmail` 的 `killmail_time` 同格式）。

**错误归属**：`url` 形态不对 / 时间戳不合法 / 两套来源同时给 → `invalid_argument`（带 `field`）；
星系名认不出 → `unknown_entity`；`{"error": …}` 的归类走**共用**的 `_zkb_error_kind()`
（`Invalid type or id` → `unknown_entity`；限流词 → `rate_limited`；其余 → `network_failed`）。
**判成败只能看正文** —— zkb 的错误体也是 HTTP 200。

### 4.15 `sde_search` + `locale`：把「人类说法」落成 `type_id`（2026-10-20）

**为什么加**：agent 常只拿到用户口语里的名字（「损伤控制 II」/「vulcan 220」），不知道确切
英文名就调不了 `fit_parse`（那是 EFT 口径），于是只能**猜**。本工具把这一步变成一次本地查询。

#### `sde_search(query, *, kind=None, limit=10, locale="en")`

**检索是四级**（同一份 SQL，按 `type_id` 排序稳定）：

| 级 | 判据 | 例 |
|---|---|---|
| 0 | 精确（英文名或中文名） | `Hawk` → `Hawk` |
| 1 | 前缀 | `Hecat` → `Hecate…` |
| 2 | 包含 | `ecate` → `Hecate` |
| **3** | **每个词都出现（AND，顺序不限）** | `autocannon vulcan 220` → `220mm Vulcan AutoCannon II` |

> 第四级是这次新加的：前三级对 `"autocannon vulcan 220"` 是 **0 命中**（词序与名字里的
> 顺序不一致，短语包含匹配不到）。第四级按空格切词，**每个词都要在名字里出现**，顺序不限。

**`kind` 按 SDE category 过滤，不靠名字猜**：

| `kind` | category id | 说明 |
|---|---|---|
| `all`（默认） | — | 不过滤 |
| `ship` | 6 | `Ship` |
| `module` | 7 | `Module` |
| `charge` | 8 | `Charge` |

> 实测必要性：名字里带 `Hawk` 的还有**涂装**（`SKINs`）和**蓝图**（`Blueprint`）。
> 靠关键字筛会把它们当成船；按 category 过滤后 `kind=ship` 只剩 `Hawk` / `Nighthawk`。
> 无人机 / 植入体等本参数不覆盖，用 `kind=all`。

**`ambiguous`**：有**多于一个**候选是精确/前缀命中 → `true`。含义是「**该追问**，别挑第一个」。
**`did_you_mean`**：0 命中时把每个词截前 4 字符重试（`autocannonn vulcann 220mm` →
`auto vulc 220m`），命中则放进 `did_you_mean` 并显式标注「**近似结果，非精确**」。
连近似都没有时**不给这个键**（不编）。

**纯数字 query** 按 `type_id` 精确查（`query="2048"`）—— 拿到 id 想知道中文名 / 类别时用。

#### `locale`：只覆盖名字最多的三个入口

`fit_parse` / `fit_killmail` / `sde_search` 接受 `locale: "en" | "zh"`（默认 `en`，**向后兼容**）。

- `locale="en"`：返回体**逐字节不变**（不会凭空多出 `name_en`）；
- `locale="zh"`：**船名 / 模块名 / 弹药名**给官方中文（SDE `types.name_zh`），并多一个
  `name_en` 便于核对；
- **玩家 / 军团 / 联盟名永远不译** —— 那是人名，不是 SDE 类型。
- `zh-CN` / `zh_CN` 一律 `invalid_argument`（`field: "locale"`）—— **不做宽松解析**。

**体量实测**（同一份 Hecate 配装 / 同一条战报）：

| 入口 | `locale=en` | `locale=zh` | 变化 |
|---|---:|---:|---:|
| `fit_parse` | 5,002 B | 5,029 B | **+27 B（+0.5%）**（中文名更短，`name_en` 又加回来） |
| `fit_killmail` | 7,788 B | 8,166 B | **+378 B（+4.9%）**（战报里类型名更多） |

> 中文**没有**把体量撑大 —— 每个中文名省下的字节刚好抵消 `name_en` 的开销。

`fit_index` / `fit_view` / `fit_get` / `fit_explain` / `fit_detail` 本单**不加** `locale`
（它们主要返回数字）。

### 4.16 SDE dogma 数据 + `sde_type` + `can_fit`（2026-10-20，schema v2）

#### 索引库现在灌了 dogma（`schema_version = 2`）

| 表 | 行数 | 来源 | 说明 |
|---|---:|---|---|
| `type_attributes` | **648,726** | `typeDogma.jsonl`（26.4 MB） | 类型 → 属性值，**SDE 原值** |
| `effects` | **3,427** | `dogmaEffects.jsonl` | effect 定义（`duration_attribute_id` 等） |
| `type_effects` | **54,307** | `typeDogma.jsonl` | 类型 → effect（含 `isDefault`） |
| `type_bonuses` | **3,571** | `typeBonus.jsonl` | 船体加成；**1,682 条挂技能** |
| `buffs` | **277** | `dbuffCollections.jsonl` | 增益编号 → 中英文名 |
| `attributes.display_name_zh` | 1,251 / 2,871 | `dogmaAttributes.jsonl` | 新增列（**允许 NULL**） |

**重建成本**（本机，`build_index(force=True)`）：**3.9 s**、库 **45.3 MB**（v1 是 15.0 MB）。
NAS 上按同一个脚本重跑即可；`PRAGMA user_version` 从 1 → **2**，旧库必须重建（不是加列迁移）。

> **两个 SDE 结构要点**（都实测过，容易读反）：
> 1. `typeBonus` 的 `_key` 是**船**，`types[]._key` 才是**技能** —— 所以「每级 +N%」的
>    `skill_type_id` 拿得到，不要写成 NULL。
> 2. `dogmaEffects` **没有** `displayName` —— effect 的中文名 SDE 不给，`name_zh` 恒为
>    `null`（列留着，等 SDE 补）。

#### `sde_type(query, *, locale="zh", include=None, limit_attributes=None)`

**锚点**（type 4403 Reactive Armor Hardener，已写成测试）：

```
armorEmDamageResonance(267) = 0.85 · duration(73) = 10000 · resistanceShiftAmount(1849) = 6.0
maxGroupFitted(1544) = 1 · maxGroupActive(763) = 1 · effect 4928 = adaptiveArmorHardener
required_skills: Hull Upgrades(3394) 3 级
```

- **默认只给「值非 0 或 有中文名」的属性**（过滤噪声）；`include="all"` 给全量；
- `limit_attributes` 默认 **60**、上限 **300**，被截时 `truncated.attributes=true`；
- `bonuses[].kind`：`type` = **每级**加成（值 = 每级 +N，**没乘技能等级**）、`role` = 角色加成（不挂技能）；
- 数值全是 **SDE 原值**（毫秒不换算）。

#### `can_fit(ship, module, *, locale="en")`

做法：拼一条「这艘船 + 这一个模块」的探针 EFT → 走引擎归一化 + `validate` → 违规翻中文。

**实测**（Hawk + Capital Armor Repairer I）：

```json
{"fits": false,
 "blockers": [{"kind": "resource", "resource": "powergrid",
               "used": 125000.0, "available": 56.25,
               "detail": "能量栅格不足：需要 125000.0，可用 56.25"},
              {"kind": "capital_item", "detail": "这是旗舰级装备，普通舰船装不上"}],
 "remaining": {"cpu": 153.0, "powergrid": -124943.75, "calibration": null},
 "slot_used": "low",
 "assumptions": ["技能档 all4+min_required（…）", "探针配装：…", "**不含已装模块的挤占** …"]}
```

> **`fits: true` 只对探针成立** —— 真实配装里 CPU/PG 已被别的东西吃掉，可能更紧。
> `fits: false` 的 `blockers` 才是确定的。**不认识的违规原样透传 `rule`、不编中文。**

## 5. 返回契约

每个工具的结果是 `content: [{"type": "text", "text": "<JSON>"}]`，`text` 就是
`noobbaozi/api.py` 的**信封原样**：

```json
{"ok": true,  "result": { ... }, "meta": {"spec_version": "v1", "skill_tier": {"name": "all4+min_required", "count": 512}, ...}}
{"ok": false, "error": {"kind": "invalid_path", "message": "…", "hint": ["ship", "meta", …]}}
```

协议版本 ≥ `2025-06-18` 时**同时**给 `structuredContent`（内容就是同一个信封对象），
方便客户端直接取结构而不必二次解析文本。老版本只给 `content` 里的文本。

### 术语表 `meta.glossary`（术语在**返回体里**自解释）

**为什么放在返回体里、而不是放在这份 README 里**：agent 不读 README、也不读 `SKILL.md`；
它只看 `tools/list` 的 description（每条 ≤600 字符，塞不下）和返回的 JSON。术语只写进文档
就等于不存在。实测有一个**只接了 MCP** 的 agent，把 `provenance` 的 `assumed`（依赖假设）
当成了「引擎实测」，又把 `repair_total_hp_s: 269.5` 当成了长期回复速率 —— 那个数是
`unlimited_charges` 口径的**峰值**。

所以每个 `fit_*` 信封的 `meta.glossary` 里带的是**本次返回里真的出现了的术语**（按需裁剪；
没出现的术语不写，免得 agent 去猜一个不存在的字段）：

```json
"meta": {
  "spec_version": "v1", "engine_version": "13.6.0", "sde_build": 1234567,
  "skill_tier": {"name": "all4+min_required", "count": 512, "hash": "…"},
  "damage_profile": "uniform", "state_policy": "engine_default", "source_format": "eft",
  "glossary": {
    "provenance": "measured=引擎直接给、换假设不变；assumed=依赖技能档/模块状态/伤害剖面，换假设会变；unknown=取不到（≠0，别用常识补数，看 unknown_reasons）",
    "budget": "chars=正文长度（不含 budget 块）；degraded=true 表示超出字符预算，只上报不裁剪",
    "assumes": "某数值所依赖的前提：unlimited_charges=回复速率（repair_total_hp_s）按「充能无限」算；charge_limited=true 时那只是峰值，实际受充能/装填限制",
    "damage_profile": "uniform=四系伤害均分 25/25/25/25；真实战况取决于对手",
    "state_policy": "engine_default=模块状态按引擎默认推定（不是实测状态）"
  }
}
```

唯一真源是 `api.GLOSSARY_TERMS`（九个术语）：`provenance` / `confidence` /
`unknown_reasons` / `combine` / `minimality` / `budget` / `assumes` /
`damage_profile` / `state_policy`。**每个术语的文案都写全了它的合法取值**，比如
`unknown_reasons` 的五种原因、`combine` 的四种取值 —— 新增一个枚举值而没更新文案，
`tests/test_api.py` 会直接红。

| 入口 | 本次返回会带哪几条（示例样本） |
|---|---|
| `fit_index`（L0） | `provenance`（来自 `provenance_counts`）+ `budget` + `assumes`（`repair_total_hp_s` 的前提）+ 两个回显参数 |
| `fit_view` / `fit_detail` | 上面那些（取决于视角）+ `confidence` + `unknown_reasons`；survivability 另有 `assumes` |
| `fit_get` | `provenance`（该路径是 `unknown` 时另有 `unknown_reasons`）+ 两个回显参数 |
| `fit_explain` | `provenance` + `combine` + `minimality`（含 `folded_noops`）+ 两个回显参数 |
| `fit_sensitivity` | `provenance` + `combine` + 两个回显参数 |
| `fit_parse` / `fit_project` / `fit_engagement` / `fit_killmail` | 只有两个回显参数（结果里没有这些口径） |
| `zkb_search` / `zkb_stats` | **不带**：`labels` / `npc` / `solo` / `efficiency` 已在工具 description 里写清，且不涉及本项目的三态 / `combine` 口径 |

**体量代价**（实测 f1 Hecate 样本）：信封 `meta` 从 242 → 368（`parse` / `project`）／
656（`index`）／826（`view`）字符，L0 的术语表另由测试卡在 460 字符以内。
**`budget.chars` 的口径没变**（它只量正文，不含 `budget` 块本身，也不含信封 `meta`）：
f1 是 2,201、65 个样本里最肥的 2,236 —— 仍在 `INDEX_BUDGET_CHARS = 2,500` 以内。

守卫在 `tests/test_api.py` 的「信封术语表」一节，三个方向都查：① 每个入口的术语集合是
**枚举断言**（不是「有 glossary 就行」）；② glossary 里每条都必须有本次返回里的载体
（否则是**死条目**）；③ 本次返回里出现的每个术语都必须被解释（否则就是**漏项**，也就是
本单要修的那个 bug）。`fit_engagement` 的单方向结果里也有个 `reason`（`no_damage` 这类
自由文本），它**不算** `unknown_reasons` 的载体 —— 按名字命中会给出一个牛头不对马嘴的解释。

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
4. **`fit` 上限 256 KiB**（`noobbaozi.mcp_server.MAX_FIT_CHARS`），空串直接 `-32602`。
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
12. **`zkb_stats` 的四个 ratio 公式未公开**：`formula` 恒为 `null`，我们只转述数值，
    **不据此给威胁等级判词**；`solo_ratio` 是 0–100 百分数、`avg_gang_size` 是人/次均值，
    都不是比例；`gang_ratio + solo_ratio ≠ 100`。详见 §4.7。
13. **`render_overview` 的体量预算（`BUDGET_CHARS`）是历史包袱，不是活路径**：
    实测 MCP 的**每一个**工具与 CLI **没有一处**调用它 —— L0 走 `render_index`、L1 走
    `render_view`、L2 走 `render_detail`。它现在只被测试与验收脚本（`acceptance/`）
    用作**回归基线**（2,860 字段全齐那条），并在 `docs/输出分层与工具集-v1.md` 里
    标为 deprecated。
    **但它仍是验收门**：`acceptance-*.json` 的期望写着「overview 装进预算（`degraded=False`），
    覆盖完整列表」，所以它一降级，验收就红 —— 这不是「没人用就可以不管」。
    实测三样本余量：`f12` **38** / `f47` 1,374 / `f67` 1,502（上限 19,000）。
    **38 字符不是「留了预算」，是「没预算」**：下一个字段就会触发降级，而降级会整块丢掉
    `flags.evidence`。**已提请把 `BUDGET_CHARS` 显式提到 20,500**（那是 dev-engine 的文件，
    待其确认后落地）。

## 7. 自检 / 排查

```powershell
# 真实客户端冒烟（stdio）：起子进程 → initialize → tools/list → 用例表（每个工具 + 两个负例）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.mcp.client_smoke

# 真实 HTTP 客户端冒烟：握手 → 202 通知 → 清单 → SSE → 用例表 → /health
#   （先按 1.2 起服务；绑 0.0.0.0 时也用 127.0.0.1 连，这本身就是局域网可达的证据）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.mcp.client_smoke_http
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m noobbaozi.mcp.client_smoke_http --base-url http://127.0.0.1:18080

# 契约测试（含子进程集成、stdout 纯净性、非永真断言、自解释守卫、http 传输）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" --with pytest python -m pytest -q noobbaozi/tests -p no:cacheprovider
```

`client_smoke.py` 三种模式：

| 命令 | 用什么客户端 |
|---|---|
| `python -m noobbaozi.mcp.client_smoke` | `auto`：装了官方 `mcp` 包就用它，否则（或它失败时）用手写客户端 |
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

**打开请求级日志**：`--log-level debug`（或 `NOOBBAOZI_MCP_DEBUG=1`），写到 stderr。

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
   noobbaozi 这个东西、什么时候该想起它」。一旦连上，指引全部来自 MCP 本身。
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
