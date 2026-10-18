# evefit MCP server（stdio）

把 `evefit/api.py` 的五个函数暴露成 **MCP 工具**，让 agent（Claude Desktop / Cursor /
自研 agent…）能直接问「这套 Hecate 的机动怎么样」，而不是让 agent 自己写 Python。

传输层是 **MCP over stdio**（换行分隔的 JSON-RPC 2.0），**零第三方依赖**：只需要
`eveshipfit-dogma-engine[sde]`，不需要 `mcp` 包（原因见文末「依赖路线」）。

- 服务：`evefit/mcp_server.py`
- 真实客户端冒烟：`evefit/mcp/client_smoke.py`
- 契约测试：`evefit/tests/test_mcp_server.py`

---

## 1. 一行启动

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

## 2. 客户端配置（可直接粘贴）

### 2.1 通用 stdio MCP 客户端（`mcpServers` JSON）

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
| `cwd` | `python -m evefit.mcp_server` 靠 **cwd** 找到 `evefit` 包。客户端不支持 `cwd` 时，把仓库根加进下面的 `PYTHONPATH`（见 2.2）。 |
| `env.UV_CACHE_DIR` | uv 的缓存目录。不指定会落到用户级缓存（`%LOCALAPPDATA%\uv\cache`），与项目 `evefit/spike/uvcache` 里的既有缓存不共享，首次启动会重新下载 SDE / 引擎。 |
| `env.PYTHONIOENCODING` | `utf-8`。Windows 默认代码页（cp936）会让子进程的 stderr 写崩、也会让管道里的中文变乱码。**stdout 我们按 UTF-8 字节读写**，这个变量管的是 stderr 与内部字符串。 |

> JSON 里 Windows 路径要写双反斜杠（`F:\\WorkSpace\\...`）。也可以写成
> `"F:/WorkSpace/opencode_general_workspace"` 这种正斜杠形式，两个都行。

### 2.2 已经装好依赖的 Python（省掉 uv 包装）

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

### 2.3 客户端连上之后

先 `tools/list`（应看到 5 个工具），再按第 4 节的顺序调。服务的 `initialize` 结果里
带一段 `instructions`，多数 agent 会自己读它来排调用顺序。

当前握手信息：`serverInfo = {"name": "evefit", "version": "1.0.0"}`，
`capabilities.tools = {"listChanged": false}`，
支持协议版本 `2026-07-28 / 2025-11-25 / 2025-06-18 / 2025-03-26 / 2024-11-05`。

## 3. 五个工具

| 工具 | 一句话 | 体量 | 参数 |
|---|---|---|---|
| `fit_parse` | 把输入归一化成结构，回显「我读懂了什么」（格式 / 船 / 模块件数 / 技能档 / warnings） | 中（~4 KB） | `fit` |
| `fit_index` | **默认入口**：六维度摘要（L0）+ flags + budget | ~2 KB | `fit` |
| `fit_view` | 单视角全字段（L1）：overview / provenance / unknown_reasons / confidence / flags | 2–3 KB | `fit`, `view`* |
| `fit_get` | 按路径精确取一个值 + 它的 provenance / reason | 极小（~0.3 KB） | `fit`, `path`* |
| `fit_detail` | 列表型明细（升级候选、逐模块电容、**完整技能表 512 条**） | 大（10 KB 起） | `fit`, `view`?, `path`? |

`*` = 必填。五个工具都还接受三个可选参数（§10.2）：

| 参数 | 默认 | 可用值 |
|---|---|---|
| `skill_tier` | `all4+min_required` | `all4+min_required`、`all5` |
| `damage_profile` | `uniform` | `uniform`（25/25/25/25） |
| `state_policy` | `engine_default` | `engine_default` |

`view` 的六个值：`firepower`（火力）、`survivability`（生存）、`mobility`（机动）、
`resources`（资源）、`constraints`（约束）、`stability`（不稳定）。

`fit` 是**字符串**：EFT 文本、DNA 字符串（`fitting:` 前缀可有可无）、killmail 的 JSON
文本，或者 zkillboard / ESI 的 killmail 链接（后两种会联网）。字典 / 数组也接受
（会被直接递进 `api`），但 schema 里只广告 string。

### 典型调用顺序

```
fit_parse      ① 先确认这套配装被正确读懂了（船对不对、模块读进来几件、有没有 warning）
   ↓
fit_index      ② 看六维度摘要，定位可疑的那个维度
   ↓
fit_view       ③ 展开可疑维度（要看全字段时）
fit_get        ④ 或者只要一个数：mobility.speed_mps / firepower.dps.total / budget.chars
   ↓
fit_detail     ⑤ 真需要明细时才调（升级候选、逐模块电容、完整技能表）
```

举例：

```json
{"name": "fit_index", "arguments": {"fit": "[Hecate, 139246907]\nDamage Control II\n..."}}
{"name": "fit_get",   "arguments": {"fit": "...", "path": "mobility.speed_mps"}}
{"name": "fit_view",  "arguments": {"fit": "...", "view": "mobility"}}
{"name": "fit_detail","arguments": {"fit": "...", "view": "stability"}}
```

## 4. 返回契约

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
`index_unavailable` / `network_failed` / `internal_error`。

## 5. 已知限制

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
7. **单进程单客户端**：stdio 传输按「一个客户端一个子进程」用；服务顺序处理请求，不做
   并发调度（MCP 规范允许）。要给多个 agent 用，就各起一个子进程。

## 6. 自检 / 排查

```powershell
# 真实客户端冒烟：起子进程 → initialize → tools/list → 五个工具 + 两个负例
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python -m evefit.mcp.client_smoke

# 契约测试（含子进程集成、stdout 纯净性、非永真断言）
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" --with pytest python -m pytest -q evefit/tests -p no:cacheprovider
```

`client_smoke.py` 三种模式：

| 命令 | 用什么客户端 |
|---|---|
| `python -m evefit.mcp.client_smoke` | `auto`：装了官方 `mcp` 包就用它，否则（或它失败时）用手写客户端 |
| `... --stdio` | 强制手写客户端（只用标准库，零依赖） |
| `... --sdk` | 强制官方 SDK（需要 `--with mcp`；装不上会直接失败） |

**常见事故：stdout 被污染。** 症状是「客户端说服务没响应 / 解析失败」。本服务做了两层
防护：所有日志走 stderr；启动时把进程 fd 1 改指 stderr，协议通道走一份私有副本 ——
即使某个第三方库里有裸 `print()`，也污染不了 JSON-RPC 流。`client_smoke` 的读循环对
每一行都要求「能解成 JSON-RPC 2.0」，所以这类事故会当场变红，而不是偶发。

**打开请求级日志**：`EVEFIT_MCP_DEBUG=1`（写到 stderr，不影响协议）。

## 7. 依赖路线（为什么手写而不装 `mcp`）

- 官方 `mcp` 包**能装**：实测 2025-12 的 `uv run --with mcp` 拉到 **2.3.0**。
- 但**验收命令的临时环境里没有它**：`uv run --with "eveshipfit-dogma-engine[sde]==13.6.0"`
  下 `import mcp` → `ModuleNotFoundError`。让服务依赖 `mcp`，这条命令会死在导入阶段。
- 而且 `mcp` 2.x 把 `FastMCP` 改名成 `MCPServer`、传输 API 也变了（`mcp.server.fastmcp`
  会直接抛 `ModuleNotFoundError` 并让你看迁移指南），对「只想暴露 5 个纯函数」来说，
  换来的抽象远大于收益。

MCP 的 stdio 传输本身很薄：**一行一条 JSON-RPC 2.0 消息**。本服务实现
`initialize`（版本协商 + `capabilities.tools`）、`notifications/initialized`、`ping`、
`tools/list`、`tools/call`，以及 `-32700 / -32600 / -32601 / -32602 / -32603` 五个错误码
和通知不回包的规矩 —— 这就是全部。所以：

- **服务**：手写，零第三方依赖（这是验收命令能跑起来的前提，也是唯一可行解）；
- **客户端**：`client_smoke.py` 优先用官方 SDK（装了 `mcp` 时），否则用标准库
  `subprocess` + `json` 手写 —— 官方 SDK 那条路还能顺带用第二套协议实现验一遍本服务
  的线格式。本机（Windows + DSH 沙箱）上 `asyncio` 创建子进程会 `PermissionError:
  [WinError 5]`，所以 SDK 路径会**自动回退**到手写客户端并打印一行原因。
