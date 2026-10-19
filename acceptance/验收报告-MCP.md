# 验收报告 · MCP 服务器（P4 最后一块）

- 执行：`noobbaozi-tester`（独立验收）
- 客户端：**我自己写的**（`acceptance/check_mcp.py`，仅标准库 + `subprocess`），
  **没有复用**开发方的 `client_smoke.py`
- 启动方式：`python -m noobbaozi.mcp_server`（stdio，零第三方依赖）
- 样本：`fixtures/f1-hecate-killmail.eft`（Hecate，6 高槽）
- 实现侧基线（我自己跑）：`pytest` **707+ passed**

## 0. 五组结论

| 组 | 结论 | 关键数字 |
|---|---|---|
| ① 协议层 | ✅ **PASS** | 5 个协议版本原样回；越界版本回首选；通知 0 回包；**10 个工具**（从 `TOOL_NAMES` 派生）；`fit_view` 六值 enum；`required` 含 `path`；`-32700/-32601/-32602` 全对 |
| ② stdout 纯净性 | ✅ **PASS** | **17 行，0 非 JSON-RPC 行**，首行无 BOM |
| ③ 五个工具 | ✅ **PASS** | 六视角 23 字段全对；`speed` 三处**逐位相同**（1482.735）；`skill_tier = all4+min_required / 512` |
| ④ 负例语义 | ✅ **PASS** | `invalid_path` / `unknown_module`（candidates 非空）/ 四种参数非法 → `-32602` |
| ⑤ 健壮性 | ✅ **PASS** | 垃圾行后存活；1MB payload → `-32602`；20 次同输入 **606.6×** 加速 |

**缺陷：0。**

## 1. ① 协议层

| 用例 | 结果 |
|---|---|
| `initialize("2026-07-28")` | → `'2026-07-28'`（原样回） |
| `initialize("2025-11-25")` | → `'2025-11-25'` |
| `initialize("2025-06-18")` | → `'2025-06-18'` |
| `initialize("2025-03-26")` | → `'2025-03-26'` |
| `initialize("2024-11-05")` | → `'2024-11-05'` |
| `initialize("1999-01-01")`（越界） | → `'2026-07-28'`（首选） |
| `notifications/initialized` | **回包 0 条**（0.6s 窗口） |
| `tools/list` | **10 个**（从 `evefit.mcp_server.TOOL_NAMES` 派生，不再手写）|
| `fit_view.inputSchema.view.enum` | 六视角齐全 |
| `fit_get.inputSchema.required` | `['fit', 'path']` |
| `ping` | `result = {}` |
| 坏 JSON | `-32700` |
| 未知方法 | `-32601` |
| 缺 `fit` | `-32602` |

> 会话能力：`capabilities.tools = {listChanged: false}`，`instructions` 非空（带推荐调用顺序）。

## 2. ② stdout 纯净性

- 整个主会话共 **17 行** stdout 输出，逐行解析后 `jsonrpc == "2.0"` **0 违规**；
- 首行**无 UTF-8 BOM**（输入也是无 BOM 字节，符合你提醒的 PowerShell 管道坑）；
- 日志确实存在，但**全在 stderr**（`[noobbaozi-mcp] 工具就绪 / initialize: ...`）—— 这是正确做法。

## 3. ③ 五个工具

| 工具 | 结果 |
|---|---|
| `fit_parse` | `ok=true`，`ship = {type_id: 35683, name: "Hecate", group: "Tactical Destroyer"}`，**0.05s** |
| `fit_index` | `ok=true`，顶层键 = `ship/meta/六视角/flags/provenance_counts/budget`；**0.13s** |
| `fit_view("mobility")` | `ok=true`，含 `overview/provenance/unknown_reasons/confidence/flags` |
| `fit_get("mobility.speed_mps")` | `{path, value: 1482.735, provenance: "assumed", source_view: "mobility"}` |
| `fit_detail("stability")` | `ok=true`，返回 `{ship, views}` |

**`fit_index` 与我的 L0 契约对拍**（`L0_CHECKLIST` 是我按文档 §3 手写的，未 import 产品 `INDEX_SPEC`）：

- 六视角 23 个字段：**0 缺失、0 多余**（观察列表为空）
- 形状：`slots` 为 `"3/6"` 字符串 ✓；`damage_split` 四键 dict ✓；`range_m` 只含 `optimal`（无导弹）✓
- `provenance_counts`：**带标签对象** `{measured, assumed, unknown}` ✓
- `flags`：`[{id, hint}]`，**无 evidence** ✓；hint 均 ≤10 字（如 `"护盾最弱:电磁"`、`"信号×5.5"`、`"CPU超11"`）
- `budget`：`{chars: 1996, degraded: false}`（`chars` 已是自身正文长度 ✓）

**三处一致（逐位相同）**：`fit_index.mobility.speed_mps = fit_view.overview.speed_mps = fit_get.value = 1482.735`

**技能档回显**：`meta.skill_tier = {name: "all4+min_required", count: 512, hash: "f1ea35f655c3"}` ✓（不是零技能）

## 4. ④ 负例语义

| 用例 | 结果 |
|---|---|
| `fit_get({"path": "mobility.nope"})` | **正常 JSON-RPC 响应**；`isError: true`、`ok: false`、`kind: invalid_path`、`hint` 列出 mobility 的 L0 字段 ✅ |
| `fit_index`（`Damage Controll II` 拼错） | `isError: true`、`kind: unknown_module`、`candidates` 非空（含 `Damage Control II` / `Damage Control I`）✅ |
| `fit = ""` | `-32602` |
| `fit` > 256 KiB | `-32602` |
| `fit = 123`（类型错） | `-32602` |
| `arguments = "not-a-dict"` | `-32602` |

判据符合要求：**业务失败走 `isError`，协议错误只留给“参数本身不合法”**。

## 5. ⑤ 健壮性

| 用例 | 结果 |
|---|---|
| 灌一行垃圾 | 回 `-32700`；**随后 `ping` 正常**（服务存活） |
| 1MB payload | `-32602`；随后 `ping` 正常 |
| `tools/call` 缺 `arguments` | `-32602` |
| 同输入 20 次 | 第 1 次 **171ms**，后续中位 **0ms**，加速 **606.6×**（LRU 命中） |

## 6. 我自己踩的两个坑（不是产品缺陷）

1. **客户端读取线程**：最初用 `proc.stdout.read(65536)`，管道上会阻塞到读满 65536 字节，
   一条响应才几十字节 → 永远不返回，整场挂死。改成逐行 `readline()` 后正常。
2. **解析错误响应的 `id` 是 `null`**：我按固定 id 等，导致把「`-32700` 正确返回」误判成
   「没返回」。改为在若干条响应里按 `error.code` 匹配后通过。

> 两次都是「验收器自己写错」，不是服务问题 —— 与之前 `budget.chars` 同类。
> 已固化成注释：**断言要按响应的实际语义匹配，不要假设 id 一定回显。**

## 7. 文档 ↔ 实现一致性

**未发现冲突。** 两处形状备注（不算冲突，建议文档补一句）：

| 项 | 观察 |
|---|---|
| `fit_get` 返回 | 除 `value/provenance/source_view` 外还回 `path`（回显请求路径，便于日志串联） |
| `fit_detail` 返回 | 信封是 `{ship, views}`（明细挂在 `views` 下，与 `fit_view` 的扁平结构不同层） |

## 8. 环境坑验证

| 坑 | 我的做法 |
|---|---|
| `asyncio.create_subprocess_exec` 报 `WinError 5` | 全程只用 `subprocess.Popen`，未触发 |
| PowerShell 管道加 UTF-8 BOM | 客户端直接写**无 BOM 字节**，首条消息未被判 `-32700` |
| stdout 被日志污染 | 服务日志走 stderr，stdout 17 行全为 JSON-RPC |

## 9. 复现

```powershell
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
$env:PYTHONUNBUFFERED='1'
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
  python -u noobbaozi/acceptance/check_mcp.py
```

产物：`out/mcp-check.json`（版本协商逐条结果 + 缺陷列表）。
