"""L5 传输形态之二：**MCP over stdio**（文档 §10.5）。

```
python -m noobbaozi.mcp_server
```

本文件**只做传输层**：每个工具都是一次 `noobbaozi.api.<入口>()` 调用，返回的
`content[0].text` 就是 api 的信封原样（`json.dumps` 的紧凑序列化），**不重写任何逻辑**
（§10.5「三者共用同一个 Python 函数层」）。

为什么是**手写**而不是官方 `mcp` SDK
------------------------------------

选路判据只有一条：**验收命令能不能跑起来**。

```
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" \
    python -m noobbaozi.mcp_server
```

这条命令的临时环境里**只有** eveshipfit，`import mcp` 直接 `ModuleNotFoundError`
（实测评测：`mcp` 在 PyPI 上能装、最新是 **2.3.0**，但 2.x 把 `FastMCP` 改成了
`MCPServer`，且它不在本命令的 `--with` 里）。让服务依赖 `mcp` 会让验收命令直接死在
导入阶段，所以服务侧**零第三方依赖**：stdio 传输就是「换行分隔的 JSON-RPC 2.0」，
本文件按 JSON-RPC 2.0 与 MCP 的传输要求实现四个方法 + 协商 + 错误码。

协议范围（够用即止，不多做）
---------------------------

- `initialize`（`protocolVersion` 协商 + `capabilities.tools` + `instructions`）；
- `notifications/initialized`（通知，不回包）；`ping`；
- `tools/list`、`tools/call`；
- 未知方法 → `-32601`；坏 JSON → `-32700`；坏请求 → `-32600`；参数不合法 → `-32602`；
- 本服务**从不主动发请求**（无采样 / 无 roots / 无 logging），只应答。

三条纪律
--------

1. **stdout 只走 JSON-RPC**。任何日志一律 stderr（`_log()`）。更强的一层保险在
   `_claim_stdout()`：启动时把 fd 1 **改指 stderr**，协议通道留在 `dup` 出来的私有
   fd 上 —— 这样即使引擎 / SDE 库里有裸 `print()`，也污染不了协议流（这是 MCP 事故
   的第一名成因：stdout 混进一行日志，客户端整段 JSON 解析崩）。
2. **业务错误不是协议错误**。`api` 返回 `ok:false`（解析失败 / 路径不存在 / 模块名
   拼错）时，JSON-RPC 层照样是**成功响应**，`content[0].text` 里放那份失败信封，并
   置 `isError: true` —— agent 才能看到 `kind` / `candidates` / `hint` / `line`。
   只有「参数本身不合法」（缺 `fit`、`fit` 是空串、超过 `MAX_FIT_CHARS`）才走
   `-32602`。判据：**api 能给出更好的解释时，就不要抢在它前面拒绝**。
3. **不崩**。请求处理路径上任何未预期异常都变成 `isError: true` +
   `{"ok": false, "error": {"kind": "internal_error", ...}}`（§10.4），进程继续服务。

`structuredContent`
-------------------

`2025-06-18` 起的协议版本在 `CallToolResult` 里加了 `structuredContent`。本服务在
**协商到的版本 ≥ 该版本**时同时给出它（内容就是同一个信封对象），老版本只给
`content` 里的文本。**不**声明 `outputSchema`：信封有两种形状（成功 / 失败），声明
一个宽松 schema 只会多一层校验面，换个客户端的严格校验就可能把一次正常调用判成违约。

单例与缓存
----------

`api` 的 `Engine` / `Index` 是**进程级单例**，这里直接复用（`_api()` 拿模块本身，
不缓存任何结果）：同一份配装连续调多个工具，只有第一次付引擎 / 开库的成本。
`noobbaozi.api` 的 LRU 命中时一次引擎调用都不发生（见 `api.py` 模块 docstring）。
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import time
import traceback
from typing import Any, Callable

# =============================================================================
# 一、常量（协议 / 限额 / 顺序都集中在这里）
# =============================================================================

SERVER_NAME: str = "noobbaozi"
"""`serverInfo.name`：客户端列表里显示的名字。"""

SERVER_VERSION: str = "1.0.0"
"""`serverInfo.version`：本传输层的版本（与 `api.SPEC_VERSION` 无关，别混）。"""

PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2026-07-28",
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
"""支持的 MCP 协议版本（新 → 旧）。

本服务用到的四个方法（`initialize` / `tools/list` / `tools/call` / `ping`）在这几个
版本之间**没有不兼容改动**，唯一的版本差异是 `structuredContent` 从 `2025-06-18`
才有 —— 所以「声称支持哪些版本」这件事，这里说得起真话。客户端要的版本在列表里
就原样回它，不在列表里就回 `PREFERRED_PROTOCOL_VERSION`（按规范，此时由客户端决定
要不要断开）。
"""

PREFERRED_PROTOCOL_VERSION: str = PROTOCOL_VERSIONS[0]
"""本服务最想用的版本（协商不上客户端那一个时回这个）。"""

STRUCTURED_CONTENT_SINCE: str = "2025-06-18"
"""`structuredContent` 从哪个版本开始有（ISO 日期串可以直接按字典序比较）。"""

PARSE_ERROR: int = -32700
INVALID_REQUEST: int = -32600
METHOD_NOT_FOUND: int = -32601
INVALID_PARAMS: int = -32602
INTERNAL_ERROR: int = -32603

MAX_FIT_CHARS: int = 256 * 1024
"""`fit` 的长度上限（字符）。

MCP 输入是**整条消息一次性**进来的，没法流式限制；正常 EFT / DNA / killmail 的
JSON 文本都在几 KB 量级（实测 f1 的 killmail JSON ≈ 12 KB），256 KiB 已经比最大的
真实输入大一个量级。超限直接 `-32602`：既不把巨型字符串塞进 `normalize_fit()`，
也不让 `repr()` / 错误回显再复制一份（那是「吃满内存」的真正原因）。
"""

MAX_PATH_CHARS: int = 4096
"""`path` 的长度上限（路径表达式是几百字符量级，4096 已极宽松）。"""

TOOL_NAMES: tuple[str, ...] = (
    "fit_parse",
    "fit_killmail",
    "fit_index",
    "fit_view",
    "fit_get",
    "fit_detail",
    "fit_explain",
    "fit_sensitivity",
    "fit_project",
    "fit_engagement",
    "zkb_search",
    "zkb_stats",
    "zkb_related",
)
"""**工具清单的唯一真源**（单数真理）：`tools/list`、`/health` 的 `tools`、客户端冒烟、
验收测试全部从这里派生 —— 本文件之外**不许再出现工具名清单或工具条数的字面量**。

顺序就是推荐调用顺序：

1. `fit_parse` —— 先确认这套配装被正确读懂了；
2. `fit_killmail` —— 分析一条战报时拿上下文（损失价值 / 参战方 / 伤害构成）；
3. `fit_index` / `fit_view` / `fit_get` / `fit_detail` —— 六维度从摘要到深挖；
4. `fit_explain` —— 某个数「为什么是这个数」（归因链，A 可加 / B 乘性 / C 投影）；
5. `fit_sensitivity` —— 改一个输入（关模块 / 切状态 / 切模式），这个数会变多少；
6. `fit_project` —— 一艘船**给**另一艘船什么（加成 / 遥修 / 负面），一给一；
7. `fit_engagement` —— A 打 B 打得动吗（命中 / 有效 DPS / 击穿时间 / 编队量级）；
8. `zkb_search` —— 先按条件筛出 `killmail_id`，再回头对具体那条用 `fit_killmail`；
9. `zkb_stats` —— 看一个实体（角色 / 公司 / 联盟）的画像：值不值得往下翻战报；
10. `zkb_related` —— 一个星系 + 一个时间点 → 那场交火的双方阵营与 killmail 清单
    （**唯一能查任意历史窗口的入口**：列表接口的 `startTime` 已被 zkb 停用）。

加工具的正确姿势：往这个元组里加一个名字、在 `_HANDLERS` 里加实现、在
`_TOOL_DESCRIPTIONS` 里加文案、(必要时) 在 `_schema_for()` 里加 schema ——
**测试一条都不用改**（它们只断言「`tools/list` 的名字集合 == `TOOL_NAMES`」）。
"""

TOOL_COUNT: int = len(TOOL_NAMES)
"""工具条数（**派生值**，不许手写数字；`/health` 与冒烟都读它）。"""

_TOOL_ORDER: tuple[str, ...] = TOOL_NAMES
"""`TOOL_NAMES` 的历史别名（`mcp_http.tool_count()` 等既有调用点仍在用它）。

保留它是为了「单一真源」而不是「两份清单」：它**就是**同一个元组对象。
"""

_COMMON_PARAMETERS: tuple[str, ...] = ("skill_tier", "damage_profile", "state_policy")
"""配装类工具共用的三个可选参数（§10.2）；`zkb_search` 不接受它们（传了会报错）。"""

_DEBUG: bool = os.environ.get("NOOBBAOZI_MCP_DEBUG", "").strip().lower() not in (
    "",
    "0",
    "false",
    "no",
)
"""`NOOBBAOZI_MCP_DEBUG=1` 时把每个进来的请求也打到 stderr（排查客户端行为用）。"""

_LOG_PREFIX: str = "[noobbaozi-mcp] "

TRANSPORTS: tuple[str, ...] = ("stdio", "http")
"""支持的传输形态（`--transport`）：`stdio` 是默认，`http` 是 Streamable HTTP。"""

DEFAULT_TRANSPORT: str = "stdio"
"""默认传输 = stdio：T-46 的行为**一个字节都没变**（786 条测试的基线）。"""

DEFAULT_HTTP_HOST: str = "127.0.0.1"
"""http 的默认监听地址。**容器里会显式传 `0.0.0.0`**（默认只绑回环 = 不误暴露到局域网）。"""

DEFAULT_HTTP_PORT: int = 8080
"""http 的默认端口（容器约定，别改）。"""

LOG_LEVELS: dict[str, int] = {"debug": 10, "info": 20, "warning": 30, "error": 40}
"""`--log-level` 的可用值（数值越小越啰嗦）。"""

DEFAULT_LOG_LEVEL: str = "info"

_LOG_LEVEL: int = LOG_LEVELS[DEFAULT_LOG_LEVEL]
_LOG_LEVEL_NAME: str = DEFAULT_LOG_LEVEL


# =============================================================================
# 二、日志（**只走 stderr**）
# =============================================================================


def _write_log(message: str) -> None:
    """真正写出去（不做级别过滤）：stdout 只有一个用途（stdio 的 JSON-RPC），日志全在 stderr。"""
    try:
        sys.stderr.write(f"{_LOG_PREFIX}{message}\n")
        sys.stderr.flush()
    except (OSError, ValueError):  # pragma: no cover - stderr 被关掉时无事可做
        pass


def set_log_level(name: str) -> str:
    """设置日志级别（`--log-level`），返回规范化后的名字。

    Raises:
        ValueError: 级别不认识（调用方把它转成「参数错误」的退出码）。
    """
    global _LOG_LEVEL, _LOG_LEVEL_NAME
    key = str(name or "").strip().lower()
    if key not in LOG_LEVELS:
        raise ValueError(f"未知日志级别 {name!r}；可用：{list(LOG_LEVELS)}")
    _LOG_LEVEL = LOG_LEVELS[key]
    _LOG_LEVEL_NAME = key
    return key


def get_log_level() -> str:
    """当前日志级别名（`info` / `debug` / …）。"""
    return _LOG_LEVEL_NAME


def _log(message: str, *, level: str = "info") -> None:
    """按级别往 **stderr** 打一行日志（低于当前级别的直接丢弃）。"""
    if LOG_LEVELS.get(level, LOG_LEVELS["info"]) < _LOG_LEVEL:
        return
    _write_log(message)


def _debug(message: str) -> None:
    """请求级调试日志：`NOOBBAOZI_MCP_DEBUG=1` **或** `--log-level debug` 时打开。"""
    if _DEBUG:
        _write_log(message)  # 环境变量是硬开关：即使 --log-level error 也要看得到
        return
    _log(message, level="debug")


# =============================================================================
# 三、api 的**懒加载**（延迟导入，只为两件事：启动更快、导入失败也能给出结构化错误）
# =============================================================================

_API_MODULE: Any = None
_API_IMPORT_ERROR: BaseException | None = None
_VIEWS_CACHE: tuple[str, ...] | None = None


def _api() -> Any:
    """拿 `noobbaozi.api` 模块（单例；导入失败时**抛原异常**，由调用方转内部错误）。

    为什么不写在文件顶部的 `from noobbaozi import api`：那样 `import noobbaozi.mcp_server`
    就会连带加载引擎 / SDE 适配层，`tools/list` 之前的时间都花在导入上；更糟的是
    **导入期的裸 print 会落在 `_claim_stdout()` 之前**（stdout 还没被保护起来）。
    延迟到第一次真正需要时再导入，`_claim_stdout()` 就总是先手。
    """
    global _API_MODULE, _API_IMPORT_ERROR
    if _API_MODULE is not None:
        return _API_MODULE
    if _API_IMPORT_ERROR is not None:
        raise _API_IMPORT_ERROR
    try:
        from noobbaozi import api as module
    except BaseException as exc:  # noqa: BLE001 - 导入失败要能被转成 internal_error
        _API_IMPORT_ERROR = exc
        raise
    _API_MODULE = module
    return module


def _view_names() -> tuple[str, ...]:
    """六个视角名（**从注册表读**，不在本文件里再抄一份）。

    抄一份的代价是「api 加了视角、MCP 的 enum 还是旧的」—— 那种漂移只有在客户端
    被 enum 挡住时才暴露，排查成本极高。注册表为空时退回 `VIEW_ORDER`（规范顺序），
    保证 enum 非空。
    """
    global _VIEWS_CACHE
    if _VIEWS_CACHE is None:
        _api()  # 导入 api 会连带导入 render → 六个视角完成注册
        from noobbaozi.project.views import VIEW_ORDER, available_views

        registered = tuple(available_views())
        _VIEWS_CACHE = registered or tuple(VIEW_ORDER)
    return _VIEWS_CACHE


# =============================================================================
# 四、参数校验（**只校验「参数本身」**，业务判断一律留给 api）
# =============================================================================


class _InvalidParams(Exception):
    """请求参数不合法 → JSON-RPC `-32602`（**不是** api 的业务错误）。"""

    def __init__(
        self, message: str, *, field: str | None = None, hint: object = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.field = field
        self.hint = hint

    def data(self) -> dict:
        """`error.data`：把「哪个参数、可用值是什么」结构化地带出去。"""
        payload: dict[str, object] = {}
        if self.field is not None:
            payload["field"] = self.field
        if self.hint is not None:
            payload["hint"] = self.hint
        return payload


class _MethodNotFound(Exception):
    """未知方法 → `-32601`。"""


class _UnknownTool(Exception):
    """未知工具名 → `-32602`（MCP 规范：未知工具属于 invalid params）。"""


def _fit_argument(arguments: dict, field: str = "fit") -> object:
    """取出配装输入（默认键名 `fit`；`fit_project` 用 `target`）。

    四形态：字符串三形态 + 对象 / 数组兜底。

    Args:
        arguments: `tools/call` 的 `arguments`。
        field: 参数名（`fit` / `target`）——错误信封的 `field` 与文案都跟着它走。

    Returns:
        原样交给 `api`。字符串原样传（EFT / DNA / JSON 文本），字典 / 列表
        **直接传对象**（`api` 会判成 `INPUT_JSON`，比先序列化成文本更保真：缓存 key
        与 `meta.source_format` 都走对象那条路）。

    Raises:
        _InvalidParams: 缺参 / 类型不对 / 空串 / 超长。
    """
    if field not in arguments:
        raise _InvalidParams(f"缺少必填参数 {field}", field=field)

    fit = arguments.get(field)
    if isinstance(fit, (dict, list)):
        # 对象形式不在 schema 里（schema 声明 string），但真实客户端常把「读进来的
        # killmail 对象」直接递过来 —— 多接一种不亏，大小仍按序列化后的长度卡。
        try:
            size = len(json.dumps(fit, ensure_ascii=False))
        except (TypeError, ValueError) as exc:
            raise _InvalidParams(
                f"{field} 对象无法序列化成 JSON：{exc}", field=field
            ) from exc
        if size > MAX_FIT_CHARS:
            raise _InvalidParams(
                f"{field} 太大：序列化后 {size} 字符，上限 {MAX_FIT_CHARS}",
                field=field,
                hint={"max_chars": MAX_FIT_CHARS},
            )
        return fit

    if not isinstance(fit, str):
        raise _InvalidParams(
            f"{field} 必须是字符串（EFT 文本 / DNA / killmail JSON 文本）"
            f"，收到 {type(fit).__name__}",
            field=field,
        )
    text = fit.strip()
    if not text:
        raise _InvalidParams(
            f"{field} 不能为空：请传 EFT 文本、DNA 字符串或 killmail 的 JSON 文本",
            field=field,
        )
    if len(fit) > MAX_FIT_CHARS:
        raise _InvalidParams(
            f"{field} 太长：{len(fit)} 字符，上限 {MAX_FIT_CHARS}"
            "（正常 EFT / DNA / killmail JSON 都在 256 KiB 以内）",
            field=field,
            hint={"max_chars": MAX_FIT_CHARS},
        )
    return fit


def _path_argument(arguments: dict) -> str:
    """取出 `fit_get` 的 `path`（必填、非空、有长度上限）。"""
    if "path" not in arguments:
        raise _InvalidParams("缺少必填参数 path", field="path")
    path = arguments.get("path")
    if not isinstance(path, str):
        raise _InvalidParams(
            f"path 必须是字符串，收到 {type(path).__name__}", field="path"
        )
    text = path.strip()
    if not text:
        raise _InvalidParams(
            "path 不能为空：要传点路径，如 mobility.speed_mps", field="path"
        )
    if len(text) > MAX_PATH_CHARS:
        raise _InvalidParams(
            f"path 太长：{len(text)} 字符，上限 {MAX_PATH_CHARS}", field="path"
        )
    return text


def _metric_argument(arguments: dict) -> str:
    """取出 `fit_explain` 的 `metric`（必填、必须是字符串）。

    **不在这里校验「指标在不在登记表里」**：那件事的正确答案是 `invalid_path` + 一份
    `supported_metrics()` 列表，只有 `api.fit_explain` 给得出来（本层拒绝会变成 `-32602`
    「参数错了」，把「换一个指标」误导成「调用方式错了」）。本层只挡类型。
    """
    if "metric" not in arguments:
        raise _InvalidParams(
            "缺少必填参数 metric", field="metric", hint=list(_api().EXPLAIN_METRICS)
        )
    metric = arguments.get("metric")
    if not isinstance(metric, str):
        raise _InvalidParams(
            f"metric 必须是字符串，收到 {type(metric).__name__}", field="metric"
        )
    return metric


def _depth_argument(arguments: dict) -> dict:
    """`fit_explain` 的可选 `depth`：**只搬值，不判合法性**。

    `depth=3` / `depth="2"` 的正确答案是 `invalid_argument` + `field=depth`（业务失败，
    agent 该去改参数），而不是 `-32602`（协议层参数错）—— 与 `metric` 同一条纪律。
    没给或给 `None` 时**不传**，交给 `api` 自己的默认值（2）。
    """
    depth = arguments.get("depth")
    return {} if depth is None else {"depth": depth}


def _view_argument(arguments: dict, *, required: bool) -> str | None:
    """取出 `view`（可选 / 必填按工具而定）。

    **不认识的名字不在这里拒绝**：`api.fit_view` 的 `invalid_argument` 会把可用视角
    一起带回来，比本层回一个 `-32602` 信息量更大（纪律 2）。
    """
    if "view" not in arguments or arguments.get("view") is None:
        if not required:
            return None
        raise _InvalidParams(
            "缺少必填参数 view（六个视角之一）",
            field="view",
            hint=list(_view_names()),
        )
    view = arguments.get("view")
    if not isinstance(view, str):
        raise _InvalidParams(
            f"view 必须是字符串，收到 {type(view).__name__}", field="view"
        )
    if not view.strip():
        raise _InvalidParams(
            "view 不能为空（六个视角之一）", field="view", hint=list(_view_names())
        )
    return view


def _common_arguments(arguments: dict) -> dict:
    """三个共同参数（§10.2）：**只透传显式给了的**，缺省交给 `api` 自己的默认值。"""
    params: dict[str, str] = {}
    for name in _COMMON_PARAMETERS:
        value = arguments.get(name)
        if value is None:
            continue
        if not isinstance(value, str):
            raise _InvalidParams(
                f"{name} 必须是字符串，收到 {type(value).__name__}", field=name
            )
        params[name] = value
    return params


# =============================================================================
# 五、工具实现（每个都是一次 `api` 调用，**不做任何加工**）
# =============================================================================


def _tool_fit_parse(arguments: dict) -> dict:
    """`fit_parse` → `api.parse_fit()`。"""
    return _api().parse_fit(_fit_argument(arguments), **_common_arguments(arguments))


def _tool_fit_killmail(arguments: dict) -> dict:
    """`fit_killmail` → `api.fit_killmail()`（**只接受战报**；EFT 会得到 not_a_killmail）。"""
    return _api().fit_killmail(_fit_argument(arguments), **_common_arguments(arguments))


def _tool_fit_index(arguments: dict) -> dict:
    """`fit_index` → `api.fit_index()`。"""
    return _api().fit_index(_fit_argument(arguments), **_common_arguments(arguments))


def _tool_fit_view(arguments: dict) -> dict:
    """`fit_view` → `api.fit_view()`。"""
    return _api().fit_view(
        _fit_argument(arguments),
        _view_argument(arguments, required=True),
        **_common_arguments(arguments),
    )


def _tool_fit_get(arguments: dict) -> dict:
    """`fit_get` → `api.fit_get()`。"""
    return _api().fit_get(
        _fit_argument(arguments),
        _path_argument(arguments),
        **_common_arguments(arguments),
    )


def _tool_fit_detail(arguments: dict) -> dict:
    """`fit_detail` → `api.fit_detail()`（`view` / `path` 都可选，口径照 api）。"""
    return _api().fit_detail(
        _fit_argument(arguments),
        _view_argument(arguments, required=False),
        _path_argument(arguments) if "path" in arguments and arguments.get("path") else None,
        **_common_arguments(arguments),
    )


def _tool_fit_explain(arguments: dict) -> dict:
    """`fit_explain` → `api.fit_explain()`（`metric` 必填、`depth` 可选，**不做任何加工**）。

    `metric` 的登记表校验与 `depth` 的 1..2 校验都在 `api` 层：那里给 `invalid_path` +
    `supported_metrics()` / `invalid_argument` + `field=depth`，比本层抛 `-32602` 更能
    告诉 agent「下一步改什么」。
    """
    return _api().fit_explain(
        _fit_argument(arguments),
        _metric_argument(arguments),
        **_depth_argument(arguments),
        **_common_arguments(arguments),
    )


def _tool_fit_sensitivity(arguments: dict) -> dict:
    """`fit_sensitivity` → `api.fit_sensitivity()`（`metric` 必填，`ops` / `max_ops` 可选）。

    与 `fit_explain` 同一条纪律：指标登记表 / `max_ops` 的正整数校验都在 `api` 层
    （`invalid_path` + 指标全集 / `invalid_argument` + `field=max_ops`），本层只挡类型。
    """
    return _api().fit_sensitivity(
        _fit_argument(arguments),
        _metric_argument(arguments),
        **_sensitivity_options(arguments),
        **_common_arguments(arguments),
    )


def _sensitivity_options(arguments: dict) -> dict:
    """`fit_sensitivity` 的两个可选参数：`ops`（数组，原样递）与 `max_ops`（整数，原样递）。

    **都只搬值**：坏 op（未知 kind / 越界下标 / 货舱件）是**数据问题**，由 `sensitivity`
    写进 `warnings` 并跳过；`max_ops` 不是正整数才是参数错误，由 `api` 给它
    `invalid_argument` + `field`。
    """
    params: dict[str, object] = {}
    ops = arguments.get("ops")
    if ops is not None:
        params["ops"] = ops
    max_ops = arguments.get("max_ops")
    if max_ops is not None:
        params["max_ops"] = max_ops
    return params


def _tool_fit_project(arguments: dict) -> dict:
    """`fit_project` → `api.fit_project()`（`target` 必填，`sources` / `effects` / `buffs` / `strict_state` 可选）。

    与另外两个 P3 工具同一条纪律：**本层只挡类型**，错误归属交给 `api`
    （`sources[i]` 坏 → `invalid_argument` + `index`；`strict_state` 非布尔 →
    `invalid_argument` + `field`；未知 `effect_id` 不是错误，是 warning）。
    """
    return _api().fit_project(
        _fit_argument(arguments, "target"),
        **_project_options(arguments),
        **_common_arguments(arguments),
    )


def _project_options(arguments: dict) -> dict:
    """`fit_project` 的四个可选参数：**只搬值，不判合法性**（口径全在 api / projection）。

    - `sources` 不是数组 → api 给 `invalid_argument` + `field="sources"`；
    - `strict_state` 不是布尔 → api 给 `invalid_argument` + `field="strict_state"`；
    - `effects` / `buffs` 里的坏条目 → projection 写进 `warnings`（数据问题，不是参数问题）。
    """
    params: dict[str, object] = {}
    for name in ("sources", "effects", "buffs", "strict_state"):
        if name in arguments and arguments[name] is not None:
            params[name] = arguments[name]
    return params


def _tool_fit_engagement(arguments: dict) -> dict:
    """`fit_engagement` → `api.fit_engagement()`（`attacker` / `defender` / `distance_m` 必填）。

    本层只挡类型（两个配装参数走 `_fit_argument`），其余全部交给 `api`：
    `distance_m` 缺失/非数 → `invalid_argument`；`mode` 不认识 → `invalid_argument`；
    **一方无武器不是错误**（`ttk=null` + `no_weapons`）。
    """
    return _api().fit_engagement(
        _fit_argument(arguments, "attacker"),
        _fit_argument(arguments, "defender"),
        **_engagement_options(arguments),
        **_common_arguments(arguments),
    )


_ENGAGEMENT_OPTION_KEYS: tuple[str, ...] = (
    "distance_m",
    "angular_velocity",
    "target_velocity",
    "include_wrecking",
    "mode",
    "fleet",
    "observed_hp",
    "window_s",
    "duration_s",
    "points",
)
"""`fit_engagement` 的可选参数（**只搬值**，合法性与归属全在 api 层）。

保留 `False` / `0`：`include_wrecking=False` 与 `distance_m=0` 都是有效输入，
不能按真假过滤（只滤 `None`）。
"""


def _engagement_options(arguments: dict) -> dict:
    return {
        key: arguments[key]
        for key in _ENGAGEMENT_OPTION_KEYS
        if key in arguments and arguments[key] is not None
    }


_ZKB_INJECTION_KEYS: tuple[str, ...] = ("requester", "clock", "sleeper")
"""`api.zkb_search` 的三个**测试注入点**：MCP 层不转发（客户端不该能塞一个请求替身）。"""


def _tool_zkb_search(arguments: dict) -> dict:
    """`zkb_search` → `api.zkb_search(**arguments)`（**不做任何加工**）。

    为什么不在这里挑参数：`api.zkb_search` 的 `**common` 已经会对「时间区间类参数」
    和「不认识的参数」给出最好的解释（`invalid_argument` + `field` + `hint`）。
    本层再筛一遍只会把同一个错误说得更模糊 —— 纪律：api 能解释得更好，就不要抢在
    它前面拒绝。唯一例外是三个注入点（那是测试用的，不是协议表面）。
    """
    params = {
        key: value
        for key, value in arguments.items()
        if key not in _ZKB_INJECTION_KEYS
    }
    return _api().zkb_search(**params)


def _tool_zkb_stats(arguments: dict) -> dict:
    """`zkb_stats` → `api.zkb_stats(**arguments)`（**不做任何加工**）。

    同样的纪律：`entity_type` 必填、`include` 白名单、时间区间参数拒绝 —— 全在 api 层判，
    那里给得出 `field` + `hint`。本层只剔掉三个测试注入点。
    """
    params = {
        key: value
        for key, value in arguments.items()
        if key not in _ZKB_INJECTION_KEYS
    }
    return _api().zkb_stats(**params)


def _tool_zkb_related(arguments: dict) -> dict:
    """`zkb_related` → `api.zkb_related(**arguments)`（**不做任何加工**）。

    同样的纪律：`url` / `system` + `time` 的互斥与时间戳格式全在 api 层判 —— 那里给得出
    `field` + `hint`。本层只剔掉三个测试注入点。
    """
    params = {
        key: value
        for key, value in arguments.items()
        if key not in _ZKB_INJECTION_KEYS
    }
    return _api().zkb_related(**params)


_HANDLERS: dict[str, Callable[[dict], dict]] = {
    "fit_parse": _tool_fit_parse,
    "fit_killmail": _tool_fit_killmail,
    "fit_index": _tool_fit_index,
    "fit_view": _tool_fit_view,
    "fit_get": _tool_fit_get,
    "fit_detail": _tool_fit_detail,
    "fit_explain": _tool_fit_explain,
    "fit_sensitivity": _tool_fit_sensitivity,
    "fit_project": _tool_fit_project,
    "fit_engagement": _tool_fit_engagement,
    "zkb_search": _tool_zkb_search,
    "zkb_stats": _tool_zkb_stats,
    "zkb_related": _tool_zkb_related,
}
"""工具名 → 实现（键集合必须与 `TOOL_NAMES` 一致，`_build_tool_specs()` 会核对）。"""


# =============================================================================
# 六、工具清单（JSON Schema）：名字 / 参数 / enum 全部照 §10
# =============================================================================

_FIT_DESCRIPTION: str = (
    "配装输入。可传：EFT 文本（如 '[Hecate, x]\\nDamage Control II\\n...'）、"
    "DNA 字符串（`fitting:` 前缀可有可无）、killmail 的 JSON 文本，"
    "或 zkillboard / ESI 的 killmail 链接（后两种会联网取数，失败给 network_failed）。"
    f"空串与超过 {MAX_FIT_CHARS} 字符的输入会被参数校验拒绝。"
)

_TOOL_DESCRIPTIONS: dict[str, tuple[str, str]] = {
    # 名字 → (title, description)
    #
    # **每条 description 都必须自解释三段**（T-51 裁决：MCP 是唯一真源，Skill 只做发现性）：
    #   ① 何时用 / 它回答什么问题  ② 返回里最该看什么  ③ 已知限制
    # 为什么三段都写在**工具描述**里而不是 SKILL.md 里：客户端一定会把 `tools/list` 的
    # description 注入上下文，而 SKILL.md 只在「客户端装了 Skill 且没连 MCP」时有价值。
    # `tests/test_mcp_server.py::test_every_tool_description_is_self_explaining` 钉住三段，
    # 删掉任一段就变红（配对断言见同文件的 `test_description_guard_is_not_vacuous`）。
    "fit_parse": (
        "解析配装（确认读懂了没有）+ 规范 EFT 文本",
        "配装刚拿到手时用它：回答「这套到底读懂了没有」。把 EFT / DNA / killmail 归一化成"
        "结构，返回 source_format、船（type_id/name/group）、模块件数（已装 / 货舱 / 逐槽位）、"
        "**`eft`（从真实装配渲染出的规范 EFT 文本，可直接复制）**、技能档与 warnings —— "
        "先看 ship 与 modules.fitted，再看 warnings。"
        "**推荐的第一步**，也是**唯一该用来拿 EFT 文本的地方**：需要把配装写成 EFT 时读 "
        "`result.eft`，不要自己拼（自拼会漂成不存在的模块与件数）。"
        "限制：只归一化、不算任何维度；模块名认不出时给 candidates 而不"
        "代填；result.fit 是引擎结构原样（不加工）；`eft` 渲染不出来时是 null（原因在 "
        "warnings 里，不编造任何一行）。",
    ),
    "fit_killmail": (
        "战报上下文（谁打的、损失多少、挨了多少伤害）",
        "想知道这套为什么死、被谁打、值多少钱时用它。返回损失价值"
        "（fitted/dropped/destroyed/total）、参战方（count 挂名 + "
        "damaging_count 有伤害 + zero_damage_count 零伤害 + capsule_count 记录船是蛋；"
        "damage_total/damage_check、最终一击、按舰级与武器族分桶、安全等级）、星系与"
        "「受伤 vs 自身 EHP」。"
        "**包含受害者与攻击者（Top 伤害）的驾驶员 / 军团 / 联盟名字**（查不到就是 null，不编）。"
        "**先 fit_parse 看配装，再 fit_killmail 拿上下文；不要自己去打开 "
        "zkillboard 网页。** 原文来源见 killmail_origin（zkb 或 esi）。"
        "限制：只接受 killmail（JSON / {killmail_id, hash} / killmail "
        "链接），EFT / DNA 会得到 not_a_killmail；拿不到的值一律 null，"
        "damage_check 不等时给 mismatch + damage_diff；蛋（Capsule）在攻击者里说明该玩家"
        "已失去自己的船，其伤害来自被摧毁的船，船归属不可信（ship_attribution / "
        "recorded_ship_is_capsule）；只给事实，不给生死结论。",
    ),
    "fit_index": (
        "六维度摘要（L0，默认入口）",
        "配装读懂了、要一个整体画像时用它：回答「这套整体什么样、哪个维度可疑」。返回 "
        "firepower / survivability / mobility / resources / constraints / stability 六维度"
        "各 3–5 个数，加 flags（id + hint）与 provenance_counts —— 先看 flags，那里有「哪儿"
        "不对」的一句话。**默认入口**，批量看战报、快速判断时用它。限制：约 2k 字符，只有"
        "摘要不展开明细；要看某个视角全字段用 fit_view，深挖用 fit_detail，不要一上来就 detail。",
    ),
    "fit_view": (
        "单视角全字段（L1）",
        "fit_index 里某个维度看起来可疑时用它：回答「这个维度到底怎么算的」。返回单视角全"
        "字段：overview + provenance（measured/assumed/unknown 三态表）+ unknown_reasons + "
        "confidence + flags + evidence，不降级、不裁剪。限制：一次只看一个视角，2–3k 字符；"
        "只要一个数就用 fit_get 更省。",
    ),
    "fit_get": (
        "按路径取一个值 + provenance",
        "已经知道要哪个数、或在 fit_view 里看到可疑字段时用它：回答「这一个值是多少、可不可"
        "信」。返回一个值 + provenance（三态）+ source_view，几百字符。"
        "**value 为 null 是合法答案**（该值未知或不适用），这时看 provenance 与 reason，"
        "别当取数失败。限制：只能取路径上已有的值，路径真的不存在才给 invalid_path + hint；"
        "不做换算、不做评分。",
    ),
    "fit_detail": (
        "列表型明细（深挖）",
        "fit_index / fit_view 定位到问题、要逐条明细时才用它：回答「具体是哪些模块、哪些升级"
        "候选、完整技能表长什么样」。返回列表型明细：升级候选、逐模块电容、完整技能表"
        "（meta.assumptions.skill_tier，512 条）。限制：不指定 view 时返回**六个视角全部**，"
        "体量很大（实测 10 KB 起）—— 先 fit_view 定位，再 fit_detail(view=\"...\")；path 走 "
        "'<视角>.<字段>'；工具不做截断：宁可大也不悄悄少东西。",
    ),
    "fit_explain": (
        "为什么某个指标是这个数（归因链）",
        "想知道某个数为什么这么大、由什么构成时用它：回答「这个数和它上面的数从哪来」。"
        "返回 value / combine / base + sources 源链（depth 2 再展开每项的子源）。"
        "**三类口径别混**：可加类（combine=additive：ehp_mixed / dps.base / cpu.load / "
        "powergrid.load）各项 contribution 之和 == value；乘性类（speed_mps / "
        "capacitor.peak_recharge）给 base + 有序因子链，**factor 不能相加**；投影类"
        "（thinnest.*.ehp）给 formula + 两项。"
        "限制：explain.value 是全精度（与 L1 的 3 位小数不同，要展示请自己舍入）；"
        "dps.base / cpu.load 源链约 6 KB；depth 只支持 1–2；metric 不在登记表给 "
        "invalid_path + 可用列表。",
    ),
    "fit_sensitivity": (
        "改一个输入看指标怎么变（逐件关闭 / 状态 / 模式）",
        "想知道「拆掉那件 / 关掉它 / 换个模式，这个数会变多少」时用它：回答「哪件模块在撑着"
        "这个数」。返回 baseline + changes[]（每条的 op 与 value / delta / delta_pct / note）"
        "+ warnings / ops_planned / ops_executed / skipped / truncated / max_ops。"
        "**计数恒等式：ops_planned == ops_executed + skipped**（skipped 是总数：空操作 / "
        "截断 / 变体失败 / 指标缺失；逐条原因看 warnings[].kind，如 noop、modes_unknown）。"
        "ops 不给 = 自动计划（每件已装模块关一次 + 每个非当前模式切一次）。"
        "**delta_pct 是比例**（delta/baseline），基线为 0 时是 null —— 不是 0，也不是无限大。"
        "限制：每个 op 一次引擎重算，默认 max_ops=32，超了截断（truncated=true + warning）；"
        "只给数值变化，**不给「该不该拆」的判词**；combine 可能是 additive / multiplicative / "
        "single（单值指标）；metric 不在表里给 invalid_path + 可用列表。",
    ),
    "fit_project": (
        "一艘船给另一艘船什么（加成 / 遥修 / 负面，一给一）",
        "想知道「A 给 B 什么、B 的数变了多少」时用它：返回 10 个指标的 baseline→after delta、"
        "每个变了的属性（attributed_to 指向来源）、applied 的 effects/buffs、遥修落点 repaired"
        "（改的是回血速度，不是 EHP）与 warnings。"
        "**是「一给一」不是「编队」**：一次 = 一个目标 + N 个来源的**并集**，投射**不链式传递**"
        "（目标收到的不再往下传）；编队要自己对每个目标各调一次、边自己枚举。"
        "strict_state 默认 true：照 EVE 只采信 active 模块的加成（引擎对 buffs 不看 state），"
        "false = 引擎原样；两种都会给 warning 说明差异。"
        "限制：不判「该不该这么配」；未知 effect_id 给 warning 不报错；目标非法也照给数值；"
        "sources[i] 解析失败是 invalid_argument（带 index）。",
    ),
    "fit_engagement": (
        "交战数学：A 打得动 B 吗（命中 / 有效 DPS / 击穿时间）",
        "想知道「这套打得动他吗、几秒能打穿」时用它：返回命中率 / 伤害应用系数 / 有效 DPS、"
        "守方两个血量口径、击穿时间与双向换血比。"
        "**hp_pool 用来和战报 damage_taken 比，ehp 用来算击穿时间** —— 两个数用途不同，别混。"
        "mode：exchange（双向换血，默认）/ timeline（线性时间线）/ anchor（战报锚点：能否在"
        "窗口内打出观察血量）。fleet=N 走「同型 × 数量」的**量级模型**（带 fleet_assumption + "
        "order_of_magnitude_only），不是个体配装。"
        "限制：只建模炮台与导弹，其它武器给**下界**（unsupported + lower_bound；coverage<1 时"
        "击穿时间是上界）；distance_m 必填、角速度由调用方给；一方无对敌武器不是错误"
        "（ttk=null + no_weapons，无法对算）。",
    ),
    "zkb_search": (
        "战报列表检索（谁在打谁 / 最近损失了什么）",
        "还没决定看哪条战报时用它：回答「这段时间都有哪些战报」。查某人/某公司/某联盟/某船型"
        "/某星系/某区域的列表（AND 复合条件：多个条件同时给就是同时满足）。时间只能用 "
        "past_seconds（zkb 已停用起止时间：startTime/endTime 会返回 invalid_argument）；"
        "单页最多 200 条，更早的数据靠 page 往回翻。返回每条 killmail_id / killmail_time / "
        "ship{type_id,name,group} / victim{character,corporation,alliance,ship} / value / "
        "labels / npc / solo，加整页聚合（total_value / median_value / by_ship / time_span）；"
        "side = kills / losses / both。"
        "**每行带受害者驾驶员 / 军团 / 联盟名字**（只解析真正返回的那几行，一次批量查完）。"
        "限制：列表不含明细"
        "（明细用 fit_killmail）；limit 只截断 kills 数组，count 与 aggregates 仍按整页算；"
        "1.2 s 限流，别在循环里逐条查。",
    ),
    "zkb_stats": (
        "实体画像（战绩 / 风格 / 主要活动地）",
        "想知道某个角色 / 公司 / 联盟「打得怎么样、主要在哪儿、值不值得深挖」时用它。"
        "返回 combat（ships / isk / points 的 destroyed 与 lost，加 efficiency）、"
        "style（danger_ratio / gang_ratio / solo_ratio / avg_gang_size，公式未公开故 "
        "formula=null，只转述不给威胁等级判词）、activity_7d（固定 6 键，本类型天生没有的给 "
        "null 且不告警）、top（characters / corporations / alliances / ships / systems / "
        "locations 各前 5，空榜给 []）。"
        "注意：efficiency 是我们按 isk 派生的，≠ danger_ratio；locations 是行星 / 月球 / "
        "小行星带 / 星门级、**不是星系**（要看星系用 systems）；默认约 3KB，大块 "
        "（months / rankings / top_all_time / groups）必须显式 include，联盟全要约 120KB；"
        "entity_type 必填、不靠 id 猜。",
    ),
    "zkb_related": (
        "星系交火总览（双方阵营 / 舰船构成 / killmail 清单）",
        "想知道某个星系在某个时间点「那场仗打成什么样」时用它。"
        "返回 system / region / window（startTime / endTime / exHours / complete）、"
        "sides（teamA / teamB 各自的 pilots / corporations / alliances / ships 构成 / isk / "
        "kills 明细）与 **killmail_ids**（每边给全，可直接丢给 fit_killmail 深挖）。"
        "**这是唯一能查任意历史窗口的入口**：列表接口的 startTime/endTime 已被 zkb 停用，"
        "而 related 把时间放在路径里。"
        "限制：time 固定 12 位 YYYYMMDDHHMM（202609091400），不做宽松解析；"
        "exHours 是**窗口半径**、窗口不对称、不等于整场战斗；complete 只是 zkb 的判断；"
        "相邻星系不包含；逐 kill 明细按 limit 截断（killmail_ids 不受它限制）。",
    ),
}

_TOOL_ANNOTATIONS: dict[str, Any] = {
    "readOnlyHint": True,
    "idempotentHint": True,
    "openWorldHint": True,
}
"""`openWorldHint: true` 是真的：`fit` 传 killmail 链接 / id+hash 时会联网取数。"""


def _common_properties() -> dict:
    """配装类工具共用的属性（§10.2）：`fit` 必填，其余可选并写明默认值。

    `zkb_search` **不在这里** —— 它没有 `fit`，走 `_zkb_input_schema()`。
    """
    api = _api()
    return {
        "fit": {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_FIT_CHARS,
            "description": _FIT_DESCRIPTION,
        },
        "skill_tier": {
            "type": "string",
            "enum": list(api.SKILL_TIERS),
            "default": api.SKILL_TIER_ALL4,
            "description": (
                "技能档：默认 all4+min_required（全部已发布技能 4 级 + 逐模块补到 "
                "max(4, required)）；all5 是它的单调提升。实际生效的档名与技能条数"
                "会在返回的 meta.skill_tier 里回显。"
            ),
        },
        "damage_profile": {
            "type": "string",
            "enum": list(api.DAMAGE_PROFILES),
            "default": api.DAMAGE_PROFILE_UNIFORM,
            "description": (
                "伤害剖面。当前只支持 uniform（25/25/25/25）；其它剖面会返回 "
                "invalid_argument，而不是静默按均匀剖面算。"
            ),
        },
        "state_policy": {
            "type": "string",
            "enum": list(api.STATE_POLICIES),
            "default": api.STATE_POLICY_ENGINE_DEFAULT,
            "description": "模块状态推定策略（当前只支持 engine_default）。",
        },
    }


def _input_schema(*, view: str = "none", path: str = "none") -> dict:
    """拼一个 `inputSchema`：公共属性 + 该工具自己的 `view` / `path`。

    Args:
        view: `"none"`（无此参数）/ `"required"` / `"optional"`。
        path: 同 `view`（`fit_get` 必填，`fit_detail` 可选）。
    """
    properties = _common_properties()
    required = ["fit"]
    if view != "none":
        properties["view"] = {
            "type": "string",
            "enum": list(_view_names()),
            "description": (
                "视角名（六个视角之一）：firepower 火力 / survivability 生存 / "
                "mobility 机动 / resources 资源 / constraints 约束 / stability 不稳定。"
            ),
        }
        if view == "required":
            required.append("view")
    if path != "none":
        properties["path"] = {
            "type": "string",
            "minLength": 1,
            "maxLength": MAX_PATH_CHARS,
            "description": (
                "点路径 / 下标路径表达式，与 L0/L1 同一套：mobility.speed_mps、"
                "firepower.weapons[].name、flags[0].id、budget.chars。"
            ),
        }
        if path == "required":
            required.append("path")
    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }


def _zkb_input_schema() -> dict:
    """`zkb_search` 的 `inputSchema`（**没有 `fit`**：它不是配装工具）。

    六个实体条件都是 `string|integer`（名字或整数 id）且可同时给（AND 复合）；
    再给 `past_seconds` / `page` / `side` / `limit` 四个开关。**没有 `required`**：
    「至少要给一个实体条件」由 api 判（它给得出比 schema 更有用的解释）。
    """
    api = _api()

    def _entity(label: str, segment: str) -> dict:
        return {
            "type": ["string", "integer"],
            "description": (
                f"{label}（名字或整数 id），对应 zkb 的 {segment} 段；"
                "可与其它条件同时给（同时满足 = AND）。名字走 ESI /universe/ids/ 解析。"
            ),
        }

    properties: dict[str, Any] = {
        "character": _entity("角色", "characterID"),
        "corporation": _entity("公司", "corporationID"),
        "alliance": _entity("联盟", "allianceID"),
        "ship": _entity("船型", "shipTypeID"),
        "solar_system": _entity("星系", "solarSystemID"),
        "region": _entity("区域", "regionID"),
        "past_seconds": {
            "type": "integer",
            "minimum": 1,
            "description": (
                "时间窗口（秒）：只查最近这么多秒的战报（604800 = 7 天，86400 = 1 天）。"
                "**时间只能这样给**：zkb 已停用 startTime / endTime（abuse 防护），"
                "传起止时间会得到 invalid_argument。"
            ),
        },
        "page": {
            "type": "integer",
            "minimum": 1,
            "default": 1,
            "description": (
                "页码（1 = 最新一页）。单页最多 200 条（pastSeconds 窗口内的更早数据"
                "靠 page 往回翻）。"
            ),
        },
        "side": {
            "type": "string",
            "enum": list(api.ZKB_SIDES),
            "default": api.ZKB_SIDE_BOTH,
            "description": (
                "kills = 击杀 / losses = 损失 / both = 两者都要"
                "（两条路径各请求一次，合并去重后按 killmail_time 倒序）。"
            ),
        },
        "limit": {
            "type": "integer",
            "minimum": 1,
            "maximum": api.ZKB_PAGE_SIZE,
            "default": api.ZKB_LIMIT_DEFAULT,
            "description": (
                "只截断返回的 kills 数组；count 与 aggregates 仍按整页算"
                "（截断这件事会写在 note 里）。"
            ),
        },
    }
    return {"type": "object", "properties": properties, "required": []}


def _stats_input_schema() -> dict:
    """`zkb_stats` 的 `inputSchema`（**没有 `fit`**：它不是配装工具）。

    `entity` 必填、`entity_type` **必填且是三选一 enum**（同一串数字在角色 / 公司 / 联盟三个
    命名空间里都可能存在，靠 id 猜类型必然出错）；`include` 是白名单数组。
    """
    api = _api()
    return {
        "type": "object",
        "properties": {
            "entity": {
                "type": ["string", "integer"],
                "description": (
                    "实体：角色 / 公司 / 联盟的名字或整数 id。"
                    "名字走 ESI /universe/ids/ 解析（与 zkb_search 同一套，带进程内缓存）。"
                ),
            },
            "entity_type": {
                "type": "string",
                "enum": list(api.ZKB_STATS_ENTITY_KINDS),
                "description": (
                    "实体类型（**必填**）：character / corporation / alliance。"
                    "不靠 id 猜 —— 同一串数字在三个命名空间里都可能存在；"
                    "给错类型会得到 unknown_entity（zkb 自己会校验 id 与类型）。"
                ),
            },
            "include": {
                "type": "array",
                "items": {"type": "string", "enum": list(api.ZKB_STATS_BLOCKS)},
                "description": (
                    "可选大块，默认一块都不给：months（月度时间序列，只补内部空洞）/ "
                    "rankings / top_all_time / groups。每块带 bytes 与 truncated；"
                    "联盟四块全要约 120KB。不认识的块名会 invalid_argument。"
                ),
            },
        },
        "required": ["entity", "entity_type"],
    }


def _related_input_schema() -> dict:
    """`zkb_related` 的 `inputSchema`（**没有 `fit`**：它不是配装工具）。

    **两套来源二选一**（`url` 或 `system` + `time`）；`url` 与 `system`/`time` 同时给
    → `invalid_argument`（这个判断留给 api：它能说清「两套来源打架」）。
    所以 `required` 是空 —— 「至少要给一套」讲不清 JSON Schema，交给 api 报错更好。
    """
    api = _api()
    return {
        "type": "object",
        "properties": {
            "url": {
                "type": "string",
                "description": (
                    "首选：https://zkillboard.com/related/<systemID>/<YYYYMMDDHHMM>/ "
                    "（也认 /api/related/...；查询串会被忽略）。**给了它就别再给 system / time**。"
                ),
            },
            "system": {
                "type": ["string", "integer"],
                "description": (
                    "星系（名字或整数 id）；不给 url 时必填。"
                    "名字走 ESI /universe/ids/ 解析（与 zkb_search 同一套，带进程内缓存）。"
                ),
            },
            "time": {
                "type": "string",
                "description": (
                    "时间戳，**固定 12 位** YYYYMMDDHHMM（如 202609091400 = 2026-09-09 14:00 UTC）。"
                    "不认 2026-09-09 14:00 / 10 位 / 13 位 —— 不做宽松解析，直接 invalid_argument。"
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": api.ZKB_PAGE_SIZE,
                "default": api.ZKB_RELATED_KILL_LIMIT,
                "description": (
                    "每边最多几条逐 kill 明细（默认 20）。"
                    "**不影响 killmail_ids** —— 那个永远给全（按时间倒序）。"
                ),
            },
        },
        "required": [],
    }


def _explain_input_schema() -> dict:
    """`fit_explain` 的 `inputSchema`：公共属性 + 必填 `metric` + 可选 `depth`。

    `metric` **刻意不给 enum**：给了 enum，客户端 / 网关可能先按 schema 拒掉，agent 就
    只能拿到一个协议层报错；不给 enum，`api.fit_explain` 才能回 `invalid_path` + 一份
    当前登记表（`supported_metrics()`）—— 那才是「下一步该换哪个指标」的答案。
    `depth` 给 enum [1, 2] + `default: 2`（这是**取值域**，不是业务判断）。
    """
    properties = _common_properties()
    properties["metric"] = {
        "type": "string",
        "minLength": 1,
        "description": (
            "要归因的指标路径（叶子指标，不是任意路径）：可加 survivability.ehp_mixed / "
            "firepower.dps.base / resources.cpu.load / resources.powergrid.load；"
            "乘性 mobility.speed_mps / stability.capacitor.peak_recharge；"
            "投影 survivability.thinnest.{shield,armor,hull}.ehp。"
            "不在登记表 → invalid_path，hint 给可用全集。"
        ),
    }
    properties["depth"] = {
        "type": "integer",
        "enum": list(range(1, _api().EXPLAIN_MAX_DEPTH + 1)),
        "default": _api().EXPLAIN_MAX_DEPTH,
        "description": (
            "归因深度：1 = 只看直接贡献者；2（默认）= 再展开每个贡献者自己的源。"
            "其它值 → invalid_argument。"
        ),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": ["fit", "metric"],
    }


def _sensitivity_input_schema() -> dict:
    """`fit_sensitivity` 的 `inputSchema`：公共属性 + 必填 `metric` + 可选 `ops` / `max_ops`。

    `metric` 同 `fit_explain`：**不给 enum**（否则客户端先按 schema 拒掉，agent 只能拿到
    协议层报错；不给枚举，`api` 才能回 `invalid_path` + 当前登记表）。
    `ops` 是显式操作列表（不给 = 自动计划），三项形状写在 items 里；坏 op 由模块写进
    `warnings` 并跳过 —— 所以这里不收紧 `required`。
    """
    properties = _common_properties()
    properties["metric"] = {
        "type": "string",
        "minLength": 1,
        "description": (
            "要算灵敏度的指标：explain 的 9 个（ehp_mixed / dps.base / cpu.load / "
            "powergrid.load / speed_mps / capacitor.peak_recharge / thinnest.*.ehp）+ 5 个单值"
            "（capacitor.stable_pct / capacitor.peak_load / capacitor.capacity / "
            "resources.cpu.free / resources.powergrid.free）。不在表里 → invalid_path + 清单。"
        ),
    }
    properties["ops"] = {
        "type": "array",
        "description": (
            "显式操作列表；不给 = 自动计划（每件已装模块 disable_item 一次 + 每个非当前模式 "
            "set_mode 一次）。坏 op 会进 warnings 并跳过，不猜。"
        ),
        "items": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["disable_item", "set_state", "set_mode"],
                },
                "item_index": {
                    "type": "integer",
                    "minimum": 0,
                    "description": "第几件已装模块（disable_item / set_state 必填）",
                },
                "state": {
                    "type": "string",
                    "enum": ["off", "offline", "online", "active", "overload"],
                    "description": "set_state 的目标状态（off 是 offline 的别名）",
                },
                "mode_id": {
                    "type": "integer",
                    "description": "set_mode 的目标模式 type_id（T3C / T3D）",
                },
            },
            "required": ["kind"],
        },
    }
    properties["max_ops"] = {
        "type": "integer",
        "minimum": 1,
        "default": _api().SENSITIVITY_MAX_OPS,
        "description": (
            "最多执行几个 op（默认 = "
            f"{_api().SENSITIVITY_MAX_OPS}）：每个 op 一次引擎重算，超了截断并给 warning。"
            "非正整数 → invalid_argument。"
        ),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": ["fit", "metric"],
    }


def _project_input_schema() -> dict:
    """`fit_project` 的 `inputSchema`：必填 `target` + 可选 `sources` / `effects` / `buffs` /
    `strict_state` + 三个共同参数。

    `target` 与其它工具的 `fit` 是**同一套四形态**（EFT / DNA / killmail JSON / 链接），
    只是键名不同 —— 这个工具没有 `fit` 这个参数。
    """
    properties = _common_properties()
    properties.pop("fit", None)  # 目标叫 `target`，不是 `fit`
    properties["target"] = {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_FIT_CHARS,
        "description": (
            "目标配装（四形态：EFT 文本 / DNA / killmail JSON / zkillboard·ESI 链接）。"
            "返回的 delta 都是「这个目标在收到投射前后」的对比。"
        ),
    }
    properties["sources"] = {
        "type": "array",
        "items": {"type": "string"},
        "description": (
            "来源船数组（每项是一个配装输入，四形态同 target）。"
            "**不是编队**：N 个来源的投射取并集投给这一个目标，且不链式传递。"
            "不给 = 只看显式 effects / buffs（都没有时是恒等 delta）。"
        ),
    }
    properties["effects"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "type_id": {"type": "integer", "description": "仅标识/展示用"},
                "effect_id": {"type": "integer", "description": "引擎 effect 编号"},
                "attributes": {
                    "type": "object",
                    "description": "该 effect 带的属性（键是属性 id 的字符串）",
                },
            },
            "required": ["type_id", "effect_id"],
        },
        "description": (
            "显式投射 effect（手工构造时用）。effect_id 不在该 type 的编号目录里 → "
            "unknown_effect_id warning 并跳过（引擎会静默忽略，我们不静默）。"
        ),
    }
    properties["buffs"] = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "value": {"type": "number"},
            },
            "required": ["id", "value"],
        },
        "description": "显式投射 buff（`{id, value}` 数组）；与 sources 的 buffs 按 id 取 max。",
    }
    properties["strict_state"] = {
        "type": "boolean",
        "default": _api().PROJECT_STRICT_STATE_DEFAULT,
        "description": (
            "true（默认，照 EVE）= 非 active 模块不提供加成；false = 引擎原样"
            "（引擎对 buffs 不看 state，offline 的指挥爆发器也照样给）。"
        ),
    }
    return {
        "type": "object",
        "properties": properties,
        "required": ["target"],
    }


def _engagement_input_schema() -> dict:
    """`fit_engagement` 的 `inputSchema`：两个配装输入 + 必填 `distance_m` + 交战参数。

    `attacker` / `defender` 与其它工具的 `fit` 是**同一套四形态**（只是键名不同）。
    `fleet` 用 `anyOf`（正整数 = 数量；对象 = `{count}` / `{killmail_attackers}`）。
    """
    properties = _common_properties()
    properties.pop("fit", None)  # 这个工具有两个配装输入：attacker / defender
    fit_description = (
        "（四形态：EFT 文本 / DNA / killmail JSON / zkillboard·ESI 链接）"
    )
    properties["attacker"] = {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_FIT_CHARS,
        "description": "攻方配装" + fit_description,
    }
    properties["defender"] = {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_FIT_CHARS,
        "description": "守方配装" + fit_description,
    }
    properties["distance_m"] = {
        "type": "number",
        "minimum": 0,
        "description": (
            "**必填**：面到面距离（m）。距离与角速度是**输入** —— 本层不复刻几何假设；"
            "近距离（≤ 炮台最佳射程）才谈得上命中"
        ),
    }
    properties["angular_velocity"] = {
        "type": "number",
        "minimum": 0,
        "default": 0,
        "description": "角速度（rad/s）。0 是乐观上界（迎头/静止）",
    }
    properties["target_velocity"] = {
        "type": "number",
        "minimum": 0,
        "default": 0,
        "description": (
            "守方绝对速度（m/s）。**只对 anchor / fleet 生效**：exchange/timeline 按双方 "
            "maxVelocity 自动填（在那两个 mode 给非 0 值会 invalid_argument）"
        ),
    }
    properties["include_wrecking"] = {
        "type": "boolean",
        "default": True,
        "description": (
            "炮台伤害是否含 wrecking（默认 true，与 pyfa 一致）；false 归一到纸面口径"
            "（完美应用 = 1.0）"
        ),
    }
    properties["mode"] = {
        "type": "string",
        "enum": list(_api().ENGAGEMENT_MODES),
        "default": _api().ENGAGEMENT_MODE_EXCHANGE,
        "description": (
            "exchange = 双向换血比（默认）；timeline = 线性时间线；anchor = 战报锚点"
            "（能否在 window_s 内打出 observed_hp，需同时给这两个）"
        ),
    }
    properties["fleet"] = {
        "anyOf": [
            {"type": "integer", "minimum": 1},
            {"type": "object"},
        ],
        "description": (
            "编队量级：正整数，或 {count:N} / {killmail_attackers: fit_killmail().attackers}。"
            "**只在 mode=exchange 下允许**；结果是「同型 × 数量」的量级模型，不是个体配装"
        ),
    }
    properties["observed_hp"] = {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "战报里**扣掉的血**（damage_taken）。mode=anchor 必填；fleet 下可选（做量级校验）",
    }
    properties["window_s"] = {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "时间窗（秒）。mode=anchor 必填；fleet 下可选",
    }
    properties["duration_s"] = {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "timeline 的时间窗（秒）；不给 = 最长击穿时间",
    }
    properties["points"] = {
        "type": "integer",
        "minimum": 2,
        "default": 6,
        "description": "timeline 的采样点数（至少 2）",
    }
    return {
        "type": "object",
        "properties": properties,
        "required": ["attacker", "defender", "distance_m"],
    }


def _schema_for(name: str) -> dict:
    """工具 → `inputSchema`（`zkb_*` 不是配装工具，各走自己那一套）。"""
    if name == "zkb_search":
        return _zkb_input_schema()
    if name == "zkb_stats":
        return _stats_input_schema()
    if name == "zkb_related":
        return _related_input_schema()
    if name == "fit_explain":
        return _explain_input_schema()
    if name == "fit_sensitivity":
        return _sensitivity_input_schema()
    if name == "fit_project":
        return _project_input_schema()
    if name == "fit_engagement":
        return _engagement_input_schema()
    return _input_schema(
        view={"fit_view": "required", "fit_detail": "optional"}.get(name, "none"),
        path={"fit_get": "required", "fit_detail": "optional"}.get(name, "none"),
    )


_SPECS_CACHE: tuple[dict, ...] | None = None


def _build_tool_specs() -> tuple[dict, ...]:
    """构建 `tools/list` 的 `tools`（**懒构建 + 缓存**：enum 要等 api 导入后才准）。

    清单的唯一来源是 `TOOL_NAMES`；这里只做两件事：核对实现齐了没有、按名字取
    `(title, description, inputSchema)`。**没有第二个工具名清单。**
    """
    global _SPECS_CACHE
    if _SPECS_CACHE is not None:
        return _SPECS_CACHE
    if set(_HANDLERS) != set(TOOL_NAMES):  # pragma: no cover - 结构自检
        raise RuntimeError(
            f"_HANDLERS 与 TOOL_NAMES 不一致：{sorted(_HANDLERS)} vs {sorted(TOOL_NAMES)}"
        )
    missing = [name for name in TOOL_NAMES if name not in _TOOL_DESCRIPTIONS]
    if missing:  # pragma: no cover - 结构自检
        raise RuntimeError(f"这些工具没有 title / description：{missing}")
    specs: list[dict] = []
    for name in TOOL_NAMES:
        title, description = _TOOL_DESCRIPTIONS[name]
        specs.append(
            {
                "name": name,
                "title": title,
                "description": description,
                "inputSchema": _schema_for(name),
                "annotations": dict(_TOOL_ANNOTATIONS),
            }
        )
    _SPECS_CACHE = tuple(specs)
    return _SPECS_CACHE


INSTRUCTIONS: str = (
    "noobbaozi 配装分析（EVE Online）。按这个顺序用：\n"
    "① fit_parse 先确认读懂了；\n"
    "② fit_killmail 战报上下文；\n"
    "③ fit_index 默认入口：六维度摘要；\n"
    "④ fit_view 展开可疑维度；⑤ fit_get 只取一个数（带 provenance）；\n"
    "⑥ fit_detail 深挖，很大，按需；\n"
    "⑦ fit_explain 归因：为什么这么大；\n"
    "⑧ fit_sensitivity 灵敏度：改输入看指标变多少；\n"
    "⑨ fit_project 投射：船给船什么（一给一）；\n"
    "⑩ fit_engagement 交战：命中 / 有效 DPS / 击穿 / 编队；\n"
    "⑪ zkb_search 先找战报：筛 id 再回 ②。\n"
    "⑫ zkb_stats 实体画像：决定用⑪翻哪些战报。\n"
    "⑬ zkb_related 一场交火：星系+时间窗 → 双方阵营 / killmail id（唯一能查任意历史窗口）。\n"
    "三条判读纪律：\n"
    "1) unknown 不是 0：provenance=unknown 别用常识补数，看 unknown_reasons"
    "（not_applicable=不适用、input_incomplete=输入不足、ambiguous=无唯一答案）。\n"
    "2) 顶层数值可能是不可达的叠加值：多推进同时启用时 speed_mps/signature_radius_m "
    "叠加，用 mobility.propulsion_states.single 看单个推进的真实值；"
    "state_exclusive flag 会提示。\n"
    "3) 别自己开 zkillboard/ESI 网页：战报上下文找 fit_killmail，列表检索找 zkb_search。\n"
    "业务失败是 isError=true 的正常响应，读 error.kind；术语见 meta.glossary。"
)


# =============================================================================
# 七、JSON-RPC 消息构造
# =============================================================================


def _error_response(
    request_id: object,
    code: int,
    message: str,
    *,
    data: dict | None = None,
) -> dict:
    """JSON-RPC 2.0 错误响应（`data` 可选，用来带 `field` / `hint`）。"""
    error: dict[str, Any] = {"code": code, "message": message}
    if data:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _success_response(request_id: object, result: object) -> dict:
    """JSON-RPC 2.0 成功响应。"""
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


# =============================================================================
# 八、会话（唯一的状态：协商到的协议版本）
# =============================================================================


class _Session:
    """一次 stdio 连接的会话状态（服务本身**无业务状态**，见 §10.6）。"""

    __slots__ = ("protocol_version", "initialized", "client_info", "calls")

    def __init__(self) -> None:
        self.protocol_version: str | None = None
        self.initialized: bool = False
        self.client_info: dict = {}
        self.calls: int = 0

    def supports_structured_content(self) -> bool:
        """协商到的版本是否支持 `structuredContent`（未协商 → 按老版本处理）。"""
        version = self.protocol_version
        return bool(version) and version >= STRUCTURED_CONTENT_SINCE


# =============================================================================
# 九、工具结果（信封 → content / structuredContent / isError）
# =============================================================================


def _internal_error_envelope(message: str, exc: BaseException) -> dict:
    """内部异常 → §10.4 形状的**失败信封**（`kind=internal_error`）。"""
    return {
        "ok": False,
        "error": {
            "kind": "internal_error",
            "message": message,
            "detail": f"{type(exc).__name__}: {exc}",
        },
    }


def _tool_result(envelope: dict, session: _Session) -> dict:
    """信封 → `CallToolResult`。

    - `content[0].text`：信封的紧凑 JSON（**老客户端也能拿到全部信息**）；
    - `structuredContent`：同一个信封对象（协商版本 ≥ 2025-06-18 时才加）；
    - `isError`：`ok` 不是 `true` 时为 `True`（成功时**不写**这个键，保持最小）。
    """
    try:
        text = json.dumps(envelope, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        # 理论上 api 的结果都是 JSON 可序列化的；真出事也要给出结构化原因。
        envelope = _internal_error_envelope("结果无法序列化成 JSON", exc)
        text = json.dumps(envelope, ensure_ascii=False)

    result: dict[str, Any] = {"content": [{"type": "text", "text": text}]}
    if session.supports_structured_content():
        result["structuredContent"] = envelope
    if envelope.get("ok") is not True:
        result["isError"] = True
    return result


# =============================================================================
# 十、方法实现
# =============================================================================


def _initialize(params: dict, session: _Session) -> dict:
    """`initialize`：协商协议版本 + 声明 `capabilities.tools`。

    协商规则（规范要求）：客户端要的版本本服务支持 → **原样回它**；不支持 → 回
    本服务最想用的那个，由客户端决定继续还是断开。`protocolVersion` 缺失 / 不是字符
    串时按「客户端没提要求」处理，回 `PREFERRED_PROTOCOL_VERSION`。
    """
    requested = params.get("protocolVersion")
    client_info = params.get("clientInfo")
    if isinstance(client_info, dict):
        session.client_info = client_info
    if isinstance(requested, str) and requested in PROTOCOL_VERSIONS:
        agreed = requested
    else:
        agreed = PREFERRED_PROTOCOL_VERSION
    session.protocol_version = agreed
    session.initialized = True
    _log(
        "initialize: client="
        f"{(session.client_info.get('name') or '?')}"
        f"/{(session.client_info.get('version') or '?')}"
        f" requested={requested!r} agreed={agreed}"
    )
    return {
        "protocolVersion": agreed,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": INSTRUCTIONS,
    }


def _list_tools() -> dict:
    """`tools/list`：`TOOL_NAMES` 里的全部工具 + 完整 JSON Schema（顺序 = 推荐调用顺序）。"""
    return {"tools": list(_build_tool_specs())}


def _call_tool(params: dict, session: _Session) -> dict:
    """`tools/call`：校验 → 一次 api 调用 → 信封包成 `CallToolResult`。

    Raises:
        _InvalidParams: 参数本身不合法（→ `-32602`）。
        _UnknownTool: 工具名不存在（→ `-32602`）。
    """
    name = params.get("name")
    if not isinstance(name, str) or not name:
        raise _InvalidParams(
            "缺少 tools/call 的 name", field="name", hint=list(TOOL_NAMES)
        )
    handler = _HANDLERS.get(name)
    if handler is None:
        raise _UnknownTool(
            f"未知工具 {name!r}；可用工具：{list(TOOL_NAMES)}"
        )
    raw_arguments = params.get("arguments")
    if raw_arguments is None:
        arguments: dict = {}
    elif isinstance(raw_arguments, dict):
        arguments = raw_arguments
    else:
        raise _InvalidParams(
            f"arguments 必须是对象，收到 {type(raw_arguments).__name__}", field="arguments"
        )

    session.calls += 1
    started = time.perf_counter()
    try:
        envelope = handler(arguments)
    except _InvalidParams:
        raise
    except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得让进程倒下
        _log(f"tools/call {name} 内部异常：\n{traceback.format_exc()}")
        return _tool_result(
            _internal_error_envelope("MCP 服务内部异常，本次没有可用结果", exc), session
        )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    if not isinstance(envelope, dict):  # pragma: no cover - api 契约保证是 dict
        envelope = _internal_error_envelope(
            "接口层返回了非信封对象", TypeError(f"got {type(envelope).__name__}")
        )
    ok = envelope.get("ok") is True
    if _DEBUG:  # 关着的时候连 json.dumps 都不做：detail 的信封动辄 10 KB
        _debug(
            f"tools/call {name} ok={ok} {elapsed_ms:.1f}ms "
            f"chars={len(json.dumps(envelope, ensure_ascii=False))}"
        )
        if not ok:
            # 业务失败（解析失败 / 路径不存在）是**正常操作**，不是服务异常：
            # 只有打开调试日志时才落 stderr，免得 agent 试错时把 stderr 刷满。
            _debug(
                f"tools/call {name} 业务失败："
                f"{json.dumps(envelope.get('error'), ensure_ascii=False)[:300]}"
            )
    return _tool_result(envelope, session)


_KNOWN_METHODS: tuple[str, ...] = (
    "initialize",
    "ping",
    "tools/list",
    "tools/call",
)


def _handle_request(method: str, params: object, session: _Session) -> object:
    """请求方法分发（只认四个；其余 `-32601`）。

    Raises:
        _MethodNotFound: 未知方法。
        _InvalidParams: 参数不合法。
    """
    if method == "initialize":
        if params is not None and not isinstance(params, dict):
            raise _InvalidParams("initialize 的 params 必须是对象", field="params")
        return _initialize(params or {}, session)
    if method == "ping":
        return {}
    if method == "tools/list":
        return _list_tools()
    if method == "tools/call":
        if not isinstance(params, dict):
            raise _InvalidParams("tools/call 的 params 必须是对象", field="params")
        return _call_tool(params, session)
    raise _MethodNotFound(
        f"未实现的方法 {method!r}；本服务只支持 {list(_KNOWN_METHODS)}"
    )


def _handle_notification(method: str, params: object, session: _Session) -> None:
    """通知（无 `id`）：**一定不回包**（回了就是协议违规，客户端会当成错位的响应）。"""
    if method == "notifications/initialized":
        session.initialized = True
        _debug("notifications/initialized")
        return
    _debug(f"忽略通知 {method!r}（params={json.dumps(params, ensure_ascii=False)[:120] if params else None}）")


def dispatch(message: object, session: _Session) -> dict | None:
    """一条 JSON-RPC 消息 → 一条响应（通知返回 `None`）。

    Args:
        message: 已经从 JSON 解出来的对象（可能是任何东西）。
        session: 会话状态。

    Returns:
        响应 dict；**通知返回 `None`**（调用方不许写任何东西出去）。
    """
    if not isinstance(message, dict):
        return _error_response(
            None,
            INVALID_REQUEST,
            f"请求必须是 JSON 对象，收到 {type(message).__name__}",
        )

    request_id = message.get("id")
    has_id = "id" in message
    method = message.get("method")

    if message.get("jsonrpc") != "2.0":
        # MCP 只用 JSON-RPC 2.0；别的版本一律拒绝（不猜）。
        return _error_response(
            request_id if has_id else None,
            INVALID_REQUEST,
            "jsonrpc 必须是 '2.0'",
        )

    if method is None:
        # 没有 method：这可能是客户端对我们**从未发出的请求**的响应。本服务不主动
        # 发请求，所以静默忽略，绝不回一个 id 对不上的错包。
        _debug(f"忽略无 method 的消息：{json.dumps(message, ensure_ascii=False)[:200]}")
        return None
    if not isinstance(method, str) or not method:
        return _error_response(
            request_id if has_id else None,
            INVALID_REQUEST,
            "method 必须是非空字符串",
        )

    params = message.get("params")
    if not has_id:
        _handle_notification(method, params, session)
        return None

    _debug(f"→ {method}")
    try:
        result = _handle_request(method, params, session)
    except _InvalidParams as exc:
        _log(f"{method} 参数不合法：{exc.message}")
        return _error_response(request_id, INVALID_PARAMS, exc.message, data=exc.data())
    except _MethodNotFound as exc:
        return _error_response(request_id, METHOD_NOT_FOUND, str(exc))
    except _UnknownTool as exc:
        return _error_response(
            request_id, INVALID_PARAMS, str(exc), data={"hint": list(TOOL_NAMES)}
        )
    except Exception as exc:  # noqa: BLE001 - 最后一层兜底：不让进程倒下
        _log(f"{method} 内部异常：\n{traceback.format_exc()}")
        return _error_response(
            request_id,
            INTERNAL_ERROR,
            "MCP 服务内部异常",
            data={"detail": f"{type(exc).__name__}: {exc}"},
        )
    return _success_response(request_id, result)


# =============================================================================
# 十一、stdio 传输（读行 / 写行；**stdout 独占**）
# =============================================================================


def _claim_stdout() -> Any:
    """独占 stdout：协议通道拿一份私有副本，**fd 1 改指 stderr**。

    这样做的收益：即使 `eveshipfit` / 引擎里存在裸 `print()`（第三方库的常见毛病），
    它写的也是 stderr，不可能把一行人话塞进 JSON-RPC 流 —— 那种污染会让客户端在
    解析某个响应时拿到一行非 JSON，整条连接报废，而且现象是「客户端说服务没响应」，
    极难定位。

    实现：`os.dup(1)` 留一份私有 fd 专写协议；`os.dup2(2, 1)` 把进程级 stdout 接到
    stderr 上（`sys.stdout` 仍指向 fd 1，于是所有 `print()` 自动改道 stderr）。

    Returns:
        写协议用的流：通常是 `wb`（无缓冲）的二进制流；测试替身（没有 fileno 的
        `StringIO`）时原样返回该替身。
    """
    stream = sys.stdout
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):  # pragma: no cover - 测试替身
        return stream

    writer = os.fdopen(os.dup(fd), "wb", buffering=0)
    try:
        os.dup2(2, fd)
    except OSError as exc:  # pragma: no cover - 极端环境（stderr 也关了）
        _log(f"把 fd 1 接到 stderr 失败（{exc}），stdout 保护降级")
    return writer


def _configure_streams() -> None:
    """stdout / stderr 统一 UTF-8：Windows 控制台默认代码页会把中文日志写崩。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if not callable(reconfigure):
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):  # pragma: no cover - 受限流
            continue


def _is_binary(stream: Any) -> bool:
    """流是不是二进制（决定写 bytes 还是 str）。"""
    mode = getattr(stream, "mode", None)
    if isinstance(mode, str):
        return "b" in mode
    return not hasattr(stream, "encoding")


def emit(writer: Any, payload: dict) -> None:
    """写一条 JSON-RPC 消息：**紧凑 JSON + 单个 `\\n`**（协议要求：消息内不含换行）。

    Raises:
        BrokenPipeError / OSError: 客户端已经关了管道（调用方按正常退出处理）。
    """
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    if _is_binary(writer):
        writer.write(line.encode("utf-8"))
    else:  # pragma: no cover - 测试替身
        writer.write(line)
    flush = getattr(writer, "flush", None)
    if callable(flush):
        flush()


def _iter_stdin_lines() -> Any:
    """逐行产出 stdin 的原始行（bytes 优先：不走 Windows 控制台代码页）。"""
    reader = getattr(sys.stdin, "buffer", None)
    if reader is None:  # pragma: no cover - 测试替身
        reader = sys.stdin
    return iter(reader)


def _decode_line(raw: object) -> str | None:
    """一行原始字节 → 文本；不是 UTF-8 时给 `None`（调用方回 `-32700`）。

    顺带剥掉行首的 **UTF-8 BOM**（`\\ufeff`）。这不是洁癖：Windows 上不少工具（含
    PowerShell 的管道）写文件 / 管道默认带 BOM，而 JSON 规范不允许文档前面有 BOM
    ——`json.loads` 会直接报 `Unexpected UTF-8 BOM`，表现为「服务连上了但第一句话就
    报 -32700」，看起来像协议不兼容。宽容一个字节，省掉一整类误报。
    """
    if isinstance(raw, bytes):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None
    else:  # pragma: no cover - 测试替身给的是 str
        text = str(raw)
    if text.startswith("\ufeff"):
        text = text[1:]
    return text


def serve(reader: Any = None, writer: Any = None) -> int:
    """主循环：读一行 → 分发 → 写一行。`reader` / `writer` 可注入（便于测试）。

    Returns:
        进程退出码（正常 EOF → `0`）。
    """
    session = _Session()
    if reader is None:
        reader = _iter_stdin_lines()
    if writer is None:  # pragma: no cover - 只在直接调用 serve() 时发生
        writer = sys.stdout
    for raw in reader:
        text = _decode_line(raw)
        if text is None:
            _log("收到非 UTF-8 的输入，已丢弃这一行")
            emit(writer, _error_response(None, PARSE_ERROR, "消息不是 UTF-8 文本"))
            continue
        line = text.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError as exc:
            _log(f"JSON 解析失败：{exc}")
            emit(writer, _error_response(None, PARSE_ERROR, f"JSON 解析失败：{exc}"))
            continue
        response = dispatch(message, session)
        if response is not None:
            emit(writer, response)
    _debug(f"EOF：本会话共处理 {session.calls} 次工具调用")
    return 0


# =============================================================================
# 十二、命令行（`--transport` / `--host` / `--port` / `--log-level`）
# =============================================================================

_USAGE: str = (
    "用法：\n"
    "  python -m noobbaozi.mcp_server                      # 默认：stdio（换行分隔 JSON-RPC 2.0）\n"
    "  python -m noobbaozi.mcp_server --transport http --host 0.0.0.0 --port 8080\n"
    "\n"
    "选项：\n"
    f"  --transport stdio|http  传输形态，默认 {DEFAULT_TRANSPORT}\n"
    f"  --host HOST             http 监听地址，默认 {DEFAULT_HTTP_HOST}"
    "（容器里传 0.0.0.0）\n"
    f"  --port PORT             http 监听端口，默认 {DEFAULT_HTTP_PORT}（0 = 系统分配）\n"
    f"  --log-level LEVEL       日志级别，{'|'.join(LOG_LEVELS)}，"
    f"默认 {DEFAULT_LOG_LEVEL}（日志一律 stderr）\n"
    "  --version               版本，写 stderr\n"
    "  -h, --help              这份说明，写 stderr\n"
    "\n"
    "stdio 模式下 stdout 只写协议消息；日志一律 stderr（http 模式下 stdout 一个字节都没有）。\n"
    "环境变量：NOOBBAOZI_MCP_DEBUG=1 打开请求级调试日志。"
)


class _UsageError(Exception):
    """命令行参数不合法 → 退出码 `2`（说明写 stderr，stdout 保持干净）。"""


@dataclasses.dataclass(frozen=True)
class CliOptions:
    """解析后的命令行选项（`host` / `port` 为 `None` 表示用 http 的默认值）。"""

    transport: str = DEFAULT_TRANSPORT
    host: str | None = None
    port: int | None = None
    log_level: str = DEFAULT_LOG_LEVEL
    show_help: bool = False
    show_version: bool = False
    unknown: tuple[str, ...] = ()


def _parse_cli(args: list[str]) -> CliOptions:
    """手写命令行解析（**不用 argparse**：argparse 的帮助 / 错误默认写 stdout）。

    支持 `--key value` 与 `--key=value` 两种写法；不认识的参数**不报错**（沿用 T-46
    的宽容行为：客户端偶尔塞一个自定义 flag，不该让服务起不来），收集到 `unknown` 里
    打一行警告。

    Raises:
        _UsageError: 值不合法（未知传输 / 未知级别 / 端口不是整数或越界）。
    """
    transport = DEFAULT_TRANSPORT
    host: str | None = None
    port: int | None = None
    log_level = DEFAULT_LOG_LEVEL
    show_help = False
    show_version = False
    unknown: list[str] = []

    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        name, has_inline, inline = arg.partition("=")

        if name in ("--help", "-h"):
            show_help = True
            continue
        if name == "--version":
            show_version = True
            continue
        if name not in ("--transport", "--host", "--port", "--log-level"):
            unknown.append(arg)
            continue

        if has_inline:
            value = inline
        else:
            if index >= len(args):
                raise _UsageError(f"{name} 需要一个值")
            value = args[index]
            index += 1
        value = value.strip()

        if name == "--transport":
            key = value.lower()
            if key not in TRANSPORTS:
                raise _UsageError(
                    f"--transport 只能是 {' / '.join(TRANSPORTS)}，收到 {value!r}"
                )
            transport = key
        elif name == "--host":
            if not value:
                raise _UsageError("--host 不能为空")
            host = value
        elif name == "--port":
            try:
                port = int(value, 10)
            except ValueError:
                raise _UsageError(f"--port 必须是整数，收到 {value!r}") from None
            if not 0 <= port <= 65535:
                raise _UsageError(f"--port 必须在 0..65535 之间，收到 {port}")
        else:
            key = value.lower()
            if key not in LOG_LEVELS:
                raise _UsageError(
                    f"--log-level 只能是 {' / '.join(LOG_LEVELS)}，收到 {value!r}"
                )
            log_level = key

    return CliOptions(
        transport=transport,
        host=host,
        port=port,
        log_level=log_level,
        show_help=show_help,
        show_version=show_version,
        unknown=tuple(unknown),
    )


def _redirect_stdout_to_stderr() -> None:
    """把进程的 fd 1 接到 stderr（http 模式没有协议流，但**也不许有东西写到 stdout**）。

    比 `_claim_stdout()` 轻：不需要留协议通道的私有副本，只做 fd 改道 —— 但效果一样：
    引擎 / 第三方库里任何裸 `print()` 都落到 stderr，容器日志里能看到，而 stdout 干净。
    """
    stream = sys.stdout
    try:
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):  # pragma: no cover - 测试替身
        return
    try:
        os.dup2(2, fd)
    except OSError as exc:  # pragma: no cover - 极端环境（fd 2 也关了）
        _log(f"把 fd 1 接到 stderr 失败（{exc}），stdout 保护降级", level="warning")


def _run_http_transport(options: CliOptions) -> int:
    """HTTP 传输入口（**延迟导入** `noobbaozi.mcp_http`）。

    延迟导入有两个理由：① stdio 路径不该为 HTTP 付导入成本；② `mcp_http` 反过来
    import 本模块（拿 `dispatch` / `_Session` / 日志），模块级互相 import 会成环。
    """
    _configure_streams()
    _redirect_stdout_to_stderr()
    try:
        from noobbaozi.mcp_http import run_http
    except BaseException as exc:  # noqa: BLE001 - 导入失败要说清楚，不静默退化
        _log(f"HTTP 传输不可用：{type(exc).__name__}: {exc}", level="error")
        return 1
    return run_http(
        host=options.host or DEFAULT_HTTP_HOST,
        port=DEFAULT_HTTP_PORT if options.port is None else options.port,
        log_level=options.log_level,
    )


# =============================================================================
# 十三、入口
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    """`python -m noobbaozi.mcp_server` 的入口：**默认 stdio**，`--transport http` 走 HTTP。

    注意：`--help` / `--version` 也写 **stderr** —— stdio 模式下这个进程的 stdout 只有
    一个用途，即使被误用也不能破例（否则「`--help` 之后客户端解析崩」又是一次事故）。

    Returns:
        进程退出码（`0` 正常；`1` http 起不来；`2` 命令行参数不合法）。
    """
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        options = _parse_cli(args)
    except _UsageError as exc:
        _configure_streams()
        _log(f"参数错误：{exc}", level="error")
        _log(_USAGE)
        return 2

    if options.show_version:
        _configure_streams()
        _log(
            f"{SERVER_NAME} {SERVER_VERSION} "
            f"(protocols {list(PROTOCOL_VERSIONS)}, transports {list(TRANSPORTS)})"
        )
        return 0
    if options.show_help:
        _configure_streams()
        _log(_USAGE)
        return 0

    set_log_level(options.log_level)
    if options.unknown:
        _log(f"忽略未知参数：{list(options.unknown)}（用 --help 看可用选项）")

    if options.transport == "http":
        return _run_http_transport(options)

    # ---- 默认：stdio（T-46 行为，**一字不改**）------------------------------
    writer = _claim_stdout()
    _configure_streams()
    if options.host or options.port is not None:
        _log("stdio 传输忽略 --host / --port（要监听端口请加 --transport http）", level="warning")

    started = time.perf_counter()
    _log(f"{SERVER_NAME} {SERVER_VERSION} 启动（stdio）")
    try:
        _api()
        _build_tool_specs()
        _log(f"工具就绪：{list(TOOL_NAMES)}（{(time.perf_counter() - started) * 1000:.0f}ms）")
    except BaseException as exc:  # noqa: BLE001 - **不在这里退出**：让客户端看到结构化错误
        _log(
            "预热失败（服务继续，调用时返回 internal_error）："
            f"{type(exc).__name__}: {exc}"
        )

    try:
        return serve(writer=writer)
    except (BrokenPipeError, KeyboardInterrupt):
        _log("客户端断开 / 被中断，正常退出")
        return 0
    except OSError as exc:
        _log(f"stdio 读写失败，退出：{exc}")
        return 0


if __name__ == "__main__":  # pragma: no cover - 进程入口
    raise SystemExit(main())


__all__ = [
    "CliOptions",
    "DEFAULT_HTTP_HOST",
    "DEFAULT_HTTP_PORT",
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_TRANSPORT",
    "INSTRUCTIONS",
    "LOG_LEVELS",
    "MAX_FIT_CHARS",
    "MAX_PATH_CHARS",
    "PROTOCOL_VERSIONS",
    "PREFERRED_PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "STRUCTURED_CONTENT_SINCE",
    "TRANSPORTS",
    "dispatch",
    "emit",
    "get_log_level",
    "main",
    "serve",
    "set_log_level",
]
