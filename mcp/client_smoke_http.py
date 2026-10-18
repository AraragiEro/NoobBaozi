"""**真实 HTTP MCP 客户端**冒烟：走 `POST /mcp` 的完整流程，不 mock 任何东西。

```
python -m evefit.mcp.client_smoke_http                        # 连 http://127.0.0.1:8080
python -m evefit.mcp.client_smoke_http --base-url http://127.0.0.1:18080
```

为什么客户端用标准库
--------------------

验收命令的环境里只有 `eveshipfit-dogma-engine[sde]`（没有 `mcp`），所以客户端必须是
`http.client` + `json`。这同时也是一层**独立的协议验证**：`Streamable HTTP` 的线格式
（`Accept` 协商、`Mcp-Session-Id`、202 空 body、SSE `data:` 行）被第二套手写代码验一遍。

这个脚本验的是什么
------------------

| 步骤 | 断言 |
|---|---|
| `initialize` | `200`；**响应头有 `Mcp-Session-Id`**；协商到 `2025-06-18`；带 `instructions` |
| `notifications/initialized` | **`202` 且 body 为空**（通知绝不回包） |
| `tools/list` | 名字集合 == `mcp_server.TOOL_NAMES`（**不写数字**：加工具不用改这里） |
| `ping`（`Accept: text/event-stream`） | `200` + `Content-Type: text/event-stream` + 至少一条 `data:` 行 |
| 用例表各一次 | 与 stdio 冒烟**同一张用例表**（`client_smoke.build_cases()`），`ok:true` |
| 两个负例 | `invalid_path` / `unknown_module`，都是 `isError:true` 的业务错误 |
| `GET /health` | 六个字段齐全，`transport == "http"`，`tools == tools/list 的条数` |

**局域网可达性**：`--base-url` 的默认值是 `http://127.0.0.1:8080`。服务端按容器的约定绑
`0.0.0.0:8080` 时，用 `127.0.0.1` 连得通就是「监听在所有网卡」的直接证据（本机回环是
所有网卡监听的子集）。如果 `--base-url` 里写的是 `0.0.0.0`（那是**绑定**地址，不是可连
地址），客户端会把它换成 `127.0.0.1` 再连，并打一行提示。
"""

from __future__ import annotations

import http.client
import json
import sys
import time
from urllib.parse import urlsplit

from evefit.mcp import client_smoke

DEFAULT_BASE_URL: str = "http://127.0.0.1:8080"
"""默认连本机回环的 8080（服务端按容器约定绑 `0.0.0.0:8080` 时同样连得上）。"""

DEFAULT_TIMEOUT_S: float = 300.0
"""单请求超时（首次工具调用要开 SDE 索引，实测 100–300 ms；300 s 极宽松）。"""

MCP_PATH: str = "/mcp"
HEALTH_PATH: str = "/health"
SESSION_HEADER: str = "Mcp-Session-Id"

CLIENT_NAME: str = "evefit-client-smoke-http"
CLIENT_VERSION: str = "1.0.0"

JSON_ACCEPT: str = "application/json"
SSE_ACCEPT: str = "text/event-stream"

HEALTH_FIELDS: tuple[str, ...] = (
    "ok",
    "server",
    "version",
    "transport",
    "tools",
    "sessions",
)
"""`/health` 的**契约字段**（容器 healthcheck 按名取值，别改）。"""


# =============================================================================
# 一、HTTP 结果与客户端
# =============================================================================


class HttpResult:
    """一次 HTTP 交互的观测结果（状态 / 头 / body / 耗时）。"""

    __slots__ = ("status", "headers", "body", "elapsed_ms", "method", "path")

    def __init__(
        self,
        method: str,
        path: str,
        status: int,
        headers: dict[str, str],
        body: bytes,
        elapsed_ms: float,
    ) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.headers = headers
        self.body = body
        self.elapsed_ms = elapsed_ms

    # -- 便捷读取 ------------------------------------------------------------

    @property
    def content_type(self) -> str:
        return self.headers.get("content-type", "")

    def header(self, name: str) -> str | None:
        """取响应头（大小写不敏感；值是 `str`）。"""
        return self.headers.get(name.strip().lower())

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", "replace")

    def json(self) -> object:
        """body 当 JSON 解析；不是 JSON 时抛 `ValueError`（消息里带 body 尾巴）。"""
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"响应不是 JSON：{exc}；body={self.text[:300]!r}") from exc

    def summary(self) -> str:
        return (
            f"{self.method} {self.path} → {self.status} "
            f"{self.content_type or '(no content-type)'} {len(self.body)}B"
        )


def connect_host(host: str | None) -> str:
    """`0.0.0.0` / `::` 是**绑定**地址，不是可连地址 —— 换成回环再连。

    服务端按容器约定绑 `0.0.0.0` 时，本脚本用 `127.0.0.1` 连上，正是「所有网卡都在
    监听」的证据；反过来直接连 `0.0.0.0` 在部分平台上是未定义行为。
    """
    if not host or host in ("0.0.0.0", "*"):
        return "127.0.0.1"
    if host in ("::", "::0", "[::]"):
        return "::1"
    return host


class HttpClient:
    """最小的 Streamable HTTP 客户端：`http.client` + `json`（零第三方依赖）。

    会话 id 存在实例上，后续请求自动带 `Mcp-Session-Id` 头（这正是规范要求的复用）。
    每个请求开一条新连接：冒烟只跑十来次，多一次 TCP 握手换来的确定性（不会被服务端
    的 keep-alive 超时坑到）更划算。
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, timeout: float = DEFAULT_TIMEOUT_S) -> None:
        parts = urlsplit(base_url)
        if parts.scheme not in ("http", "https"):
            raise ValueError(f"base_url 必须以 http:// 或 https:// 开头，收到 {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.scheme = parts.scheme
        self.host = connect_host(parts.hostname)
        self.host_in_url = parts.hostname or ""
        self.port = parts.port or (443 if self.scheme == "https" else 80)
        self.prefix = (parts.path or "").rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None
        self._next_id = 0

    # -- 底层 ----------------------------------------------------------------

    def _connection(self) -> http.client.HTTPConnection:
        factory = (
            http.client.HTTPSConnection
            if self.scheme == "https"
            else http.client.HTTPConnection
        )
        return factory(self.host, self.port, timeout=self.timeout)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResult:
        """发一个请求并读完整响应（不抛 HTTP 状态异常：4xx/5xx 也照常返回）。"""
        target = f"{self.prefix}{path}"
        connection = self._connection()
        started = time.perf_counter()
        try:
            connection.request(method, target, body=body, headers=headers or {})
            response = connection.getresponse()
            raw = response.read()
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            merged = {key.lower(): value for key, value in response.getheaders()}
            return HttpResult(method, target, response.status, merged, raw, elapsed_ms)
        finally:
            connection.close()

    # -- JSON-RPC ------------------------------------------------------------

    def _allocate_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def _post_message(self, message: dict, *, accept: str) -> HttpResult:
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", "Accept": accept}
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return self.request("POST", MCP_PATH, body=body, headers=headers)

    def call(
        self, method: str, params: object = None, *, accept: str = JSON_ACCEPT
    ) -> tuple[HttpResult, dict]:
        """发一个 JSON-RPC **请求**，返回 `(HTTP 结果, JSON-RPC 响应对象)`。

        `accept=SSE_ACCEPT` 时从 `text/event-stream` 里取第一条 `data:` 事件。
        """
        message: dict = {"jsonrpc": "2.0", "id": self._allocate_id(), "method": method}
        if params is not None:
            message["params"] = params
        result = self._post_message(message, accept=accept)
        _require(result.status == 200, f"{method} 期望 HTTP 200，实际 {result.status}")
        return result, _message_from(result, accept=accept)

    def notify(self, method: str, params: object = None) -> HttpResult:
        """发一个 **通知**（不带 id）：服务端必须回 `202` 且不回 body。"""
        message: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        return self._post_message(message, accept=JSON_ACCEPT)

    def health(self) -> tuple[HttpResult, dict]:
        """`GET /health`（不需要会话）。"""
        result = self.request("GET", HEALTH_PATH, headers={"Accept": JSON_ACCEPT})
        _require(result.status == 200, f"/health 期望 HTTP 200，实际 {result.status}")
        payload = result.json()
        _require(isinstance(payload, dict), f"/health 应回对象，实际 {type(payload).__name__}")
        return result, payload

    def delete_session(self) -> HttpResult:
        """`DELETE /mcp`：终止当前会话。"""
        headers: dict[str, str] = {}
        if self.session_id:
            headers[SESSION_HEADER] = self.session_id
        return self.request("DELETE", MCP_PATH, headers=headers)


# =============================================================================
# 二、SSE 解析与断言
# =============================================================================


def _require(condition: object, message: str) -> None:
    """断言（抛 `AssertionError`；消息里带足以定位的内容）。"""
    if not condition:
        raise AssertionError(message)


def parse_sse(body: bytes) -> list[object]:
    """SSE body → 事件里的 JSON 消息列表（只取 `data:` 行，按空行分帧）。"""
    messages: list[object] = []
    pending: list[str] = []
    for line in body.decode("utf-8", "replace").splitlines():
        if line.startswith("data:"):
            pending.append(line[5:].lstrip())
        elif not line.strip():
            if pending:
                messages.append(json.loads("\n".join(pending)))
                pending = []
    if pending:
        messages.append(json.loads("\n".join(pending)))
    return messages


def _message_from(result: HttpResult, *, accept: str) -> dict:
    """按 `Content-Type` 把响应变成一条 JSON-RPC 消息（JSON 或 SSE）。"""
    if "text/event-stream" in result.content_type:
        events = parse_sse(result.body)
        _require(events, f"SSE 响应里没有任何 data: 事件；body={result.text[:300]!r}")
        _require(len(events) == 1, f"一次请求应恰好一条消息，实际 {len(events)} 条")
        message = events[0]
    else:
        _require(
            "application/json" in result.content_type,
            f"未请求 SSE 时应回 application/json，实际 {result.content_type!r}",
        )
        message = result.json()
    _require(isinstance(message, dict), f"JSON-RPC 消息应是对象，实际 {type(message).__name__}")
    _require(message.get("jsonrpc") == "2.0", f"不是 JSON-RPC 2.0 消息：{message}")
    return message


def expected_tool_names() -> tuple[str, ...]:
    """本仓库服务该给的工具清单（**从 `mcp_server.TOOL_NAMES` 派生**，绝不抄数字）。

    冒烟脚本可能指向**别的机器上的旧服务**，所以导入失败时不报错、返回空元组 ——
    调用方会退化成「清单非空且包含用例里的工具」这种较弱但仍有意义的断言。
    """
    try:
        from evefit.mcp_server import TOOL_NAMES
    except Exception:  # noqa: BLE001 - 客户端不该因为「本仓库代码导入失败」而崩
        return ()
    return tuple(TOOL_NAMES)


def check_health(payload: dict, *, expected_tools: int | None = None) -> None:
    """`/health` 的字段契约（**容器 healthcheck 依赖它，字段名不许改**）。

    Args:
        expected_tools: 期望的工具条数；`None` 表示「取 `mcp_server.TOOL_NAMES` 的长度」
            —— 这样加工具时这里不用改任何字面量。
    """
    if expected_tools is None:
        expected_tools = len(expected_tool_names())
    missing = [field for field in HEALTH_FIELDS if field not in payload]
    _require(not missing, f"/health 缺字段 {missing}（实际 {sorted(payload)}）")
    _require(payload["ok"] is True, f"/health ok 应为 true，实际 {payload['ok']!r}")
    _require(
        payload["server"] == "evefit", f"/health server 应为 'evefit'，实际 {payload['server']!r}"
    )
    _require(
        isinstance(payload["version"], str) and payload["version"],
        f"/health version 应是非空字符串，实际 {payload['version']!r}",
    )
    _require(
        payload["transport"] == "http",
        f"/health transport 应为 'http'，实际 {payload['transport']!r}",
    )
    _require(
        payload["tools"] == expected_tools,
        f"/health tools 应等于 tools/list 的条数 {expected_tools}，实际 {payload['tools']!r}",
    )
    sessions = payload["sessions"]
    _require(
        isinstance(sessions, int) and not isinstance(sessions, bool) and sessions >= 0,
        f"/health sessions 应是非负整数，实际 {sessions!r}",
    )


# =============================================================================
# 三、跑一遍
# =============================================================================


class _Reporter:
    """打印每步状态 + 耗时，并收集失败（不中途抛，最后一起报）。"""

    def __init__(self, total: int, *, echo: bool) -> None:
        self.total = total
        self.echo = echo
        self.index = 0
        self.failures: list[str] = []

    def _line(self, name: str, state: str, elapsed_ms: float, note: str) -> None:
        self.index += 1
        if self.echo:
            print(f"[{self.index:2d}/{self.total}] {name:<28} {state:<5} {elapsed_ms:8.1f}ms  {note}")

    def ok(self, name: str, elapsed_ms: float, note: str = "") -> None:
        self._line(name, "ok", elapsed_ms, note)

    def fail(self, name: str, elapsed_ms: float, reason: str) -> None:
        self.failures.append(f"{name}: {reason}")
        self._line(name, "FAIL", elapsed_ms, reason)

    def step(self, name: str, fn, note_fn=None):
        """执行一步：成功打印摘要，失败记下来继续（前提步骤出错时由调用方提前返回）。"""
        started = time.perf_counter()
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - 一步失败不该中断整份报告
            self.fail(name, (time.perf_counter() - started) * 1000.0, f"{type(exc).__name__}: {exc}")
            return None
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        note = ""
        if note_fn is not None:
            try:
                note = note_fn(value) or ""
            except Exception:  # pragma: no cover -摘要打印失败不影响判定
                note = ""
        self.ok(name, elapsed_ms, note)
        return value


def run(
    cases: tuple[dict, ...] | None = None,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT_S,
    echo: bool = True,
) -> bool:
    """跑一遍 HTTP 冒烟，返回 `True` = PASS。

    Args:
        cases: 用例表（默认 `client_smoke.build_cases()`，与 stdio 冒烟同源）。
        base_url: 服务地址（默认 `http://127.0.0.1:8080`）。
        timeout: 单请求超时（秒）。
        echo: 是否打印过程（测试里关掉）。
    """
    cases = tuple(client_smoke.build_cases() if cases is None else cases)
    total = 3 + len(cases) + 2  # initialize / initialized / tools-list / 用例 / SSE ping / health
    reporter = _Reporter(total, echo=echo)

    try:
        client = HttpClient(base_url, timeout=timeout)
    except ValueError as exc:
        if echo:
            print(f"[client_smoke_http] base_url 不合法：{exc}")
        return False

    if echo:
        extra = ""
        if client.host_in_url != client.host:
            extra = f"（绑定地址 {client.host_in_url} → 实连 {client.host}）"
        print(f"[client_smoke_http] base_url = {client.base_url}{extra}")
        print(f"[client_smoke_http] 目标 = {client.scheme}://{client.host}:{client.port}{client.prefix}{MCP_PATH}")
        print("")

    # -- 1) initialize（读 Mcp-Session-Id）----------------------------------
    started = time.perf_counter()
    try:
        handshake_result, handshake = client.call(
            "initialize",
            {
                "protocolVersion": client_smoke.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        session_id = handshake_result.header(SESSION_HEADER)
        _require(handshake_result.status == 200, f"initialize 期望 200，实际 {handshake_result.status}")
        _require(
            bool(session_id),
            f"initialize 的响应头里必须有 {SESSION_HEADER}（实际头：{sorted(handshake_result.headers)}）",
        )
        client.session_id = session_id
        _require("error" not in handshake, f"initialize 不该是 JSON-RPC 错误：{handshake.get('error')}")
        result = handshake.get("result") or {}
        _require(
            result.get("protocolVersion") == client_smoke.PROTOCOL_VERSION,
            f"协议版本应为 {client_smoke.PROTOCOL_VERSION}，实际 {result.get('protocolVersion')!r}",
        )
        _require(
            isinstance((result.get("capabilities") or {}).get("tools"), dict),
            f"capabilities.tools 必须在：{result.get('capabilities')!r}",
        )
        _require(result.get("instructions"), "initialize 应带 instructions")
        server = result.get("serverInfo") or {}
        _require(server.get("name") == "evefit", f"serverInfo.name 应为 evefit，实际 {server.get('name')!r}")
    except Exception as exc:  # noqa: BLE001 - 握手失败就没有后续步骤
        if echo:
            print(f"[client_smoke_http] 握手失败：{type(exc).__name__}: {exc}")
            print("")
            print("FAIL  连接 / 握手阶段就失败了")
        return False
    reporter.ok(
        "initialize",
        (time.perf_counter() - started) * 1000.0,
        f"protocolVersion={result.get('protocolVersion')} "
        f"session={str(session_id)[:8]}… server={server.get('name')}/{server.get('version')}",
    )

    # -- 2) notifications/initialized（202 且空 body）-----------------------
    started = time.perf_counter()
    try:
        notified = client.notify("notifications/initialized")
        _require(notified.status == 202, f"通知应回 202，实际 {notified.status}")
        _require(notified.body == b"", f"通知不该带 body，实际 {notified.body[:200]!r}")
        reporter.ok(
            "notifications/initialized",
            (time.perf_counter() - started) * 1000.0,
            "202 · body 为空",
        )
    except Exception as exc:  # noqa: BLE001
        reporter.fail(
            "notifications/initialized",
            (time.perf_counter() - started) * 1000.0,
            f"{type(exc).__name__}: {exc}",
        )

    # -- 3) tools/list -------------------------------------------------------
    started = time.perf_counter()
    tools: list[str] = []
    try:
        listing_result, listing = client.call("tools/list")
        tools = [
            tool.get("name")
            for tool in (listing.get("result") or {}).get("tools") or []
            if isinstance(tool, dict)
        ]
        _require(bool(tools), f"tools/list 必须返回非空工具列表，实际 {tools!r}")
        expected = expected_tool_names()
        if expected:
            _require(
                set(tools) == set(expected),
                f"tools/list 的名字集合必须等于 mcp_server.TOOL_NAMES：\n"
                f"  实际 {tools}\n  期望 {list(expected)}",
            )
        for case in cases:
            _require(case["tool"] in tools, f"tools/list 里没有 {case['tool']}（实际 {tools}）")
        reporter.ok(
            "tools/list",
            (time.perf_counter() - started) * 1000.0,
            f"{len(tools)} 个工具（{', '.join(tools)}）",
        )
    except Exception as exc:  # noqa: BLE001
        reporter.fail("tools/list", (time.perf_counter() - started) * 1000.0, f"{type(exc).__name__}: {exc}")

    # -- 4) SSE：Accept: text/event-stream ----------------------------------
    started = time.perf_counter()
    try:
        sse_result, pong = client.call("ping", accept=SSE_ACCEPT)
        _require(
            "text/event-stream" in sse_result.content_type,
            f"Accept 含 text/event-stream 时应回 SSE，实际 {sse_result.content_type!r}",
        )
        _require(b"data:" in sse_result.body, f"SSE body 里没有 data: 行：{sse_result.text[:200]!r}")
        _require(pong.get("jsonrpc") == "2.0" and "result" in pong, f"SSE 里的消息不合法：{pong}")
        reporter.ok(
            "ping (SSE)",
            (time.perf_counter() - started) * 1000.0,
            f"{sse_result.content_type} · {len(parse_sse(sse_result.body))} 条 data:",
        )
    except Exception as exc:  # noqa: BLE001
        reporter.fail("ping (SSE)", (time.perf_counter() - started) * 1000.0, f"{type(exc).__name__}: {exc}")

    # -- 5) 用例表（每个工具各一次 + 两个负例；与 stdio 冒烟同源）-----------
    for case in cases:
        started_call = time.perf_counter()
        try:
            _http_result, response = client.call(
                "tools/call", {"name": case["tool"], "arguments": case["arguments"]}
            )
            _require("error" not in response, f"不该是 JSON-RPC 错误：{response.get('error')}")
            call_result = response.get("result") or {}
            envelope, is_error, structured = client_smoke._envelope_from_result(call_result)
            _require(
                is_error == (envelope.get("ok") is not True),
                f"isError({is_error}) 与信封 ok={envelope.get('ok')} 不一致",
            )
            _require(
                isinstance(structured, dict),
                "协商版本 ≥ 2025-06-18，服务应给 structuredContent",
            )
            case["check"](envelope)
            reporter.ok(
                case["name"],
                (time.perf_counter() - started_call) * 1000.0,
                client_smoke.summarize(case["tool"], envelope),
            )
        except Exception as exc:  # noqa: BLE001
            reporter.fail(
                case["name"],
                (time.perf_counter() - started_call) * 1000.0,
                f"{type(exc).__name__}: {exc}",
            )

    # -- 6) GET /health ------------------------------------------------------
    started = time.perf_counter()
    try:
        _health_result, health = client.health()
        check_health(health, expected_tools=len(tools) or len(expected_tool_names()))
        # 冒烟里额外一条：**刚刚握过手**，所以会话数至少是 1（顺便证明会话是真被服务端记着的）
        _require(
            health["sessions"] >= 1,
            f"刚完成 initialize，/health 的 sessions 应 >= 1，实际 {health['sessions']!r}",
        )
        reporter.ok(
            "GET /health",
            (time.perf_counter() - started) * 1000.0,
            f"transport={health['transport']} tools={health['tools']} sessions={health['sessions']}",
        )
    except Exception as exc:  # noqa: BLE001
        reporter.fail("GET /health", (time.perf_counter() - started) * 1000.0, f"{type(exc).__name__}: {exc}")

    # -- 收尾 ----------------------------------------------------------------
    if echo:
        print("")
        if reporter.failures:
            print(f"FAIL  {len(reporter.failures)}/{total} 个步骤失败：")
            for failure in reporter.failures:
                print(f"  - {failure}")
        else:
            print(f"PASS  {total}/{total}（握手 + 通知 + 清单 + SSE + 用例表 + /health）")
    return not reporter.failures


# =============================================================================
# 四、入口
# =============================================================================

_USAGE: str = (
    "用法：python -m evefit.mcp.client_smoke_http [--base-url URL] [--timeout 秒]\n"
    f"  --base-url  默认 {DEFAULT_BASE_URL}（服务端绑 0.0.0.0:8080 时用 127.0.0.1 连）\n"
    f"  --timeout   单请求超时，默认 {DEFAULT_TIMEOUT_S:.0f} 秒\n"
    "  退出码：0 = PASS，1 = FAIL，2 = 参数错误"
)


def main(argv: list[str] | None = None) -> int:
    """跑冒烟并返回退出码（`0` = PASS，`1` = FAIL，`2` = 参数错误）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    base_url = DEFAULT_BASE_URL
    timeout = DEFAULT_TIMEOUT_S

    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        name, has_inline, inline = arg.partition("=")
        if name in ("--help", "-h"):
            print(_USAGE)
            return 0
        if name not in ("--base-url", "--timeout"):
            print(f"未知参数：{arg}\n\n{_USAGE}", file=sys.stderr)
            return 2
        if has_inline:
            value = inline
        elif index < len(args):
            value = args[index]
            index += 1
        else:
            print(f"{name} 需要一个值\n\n{_USAGE}", file=sys.stderr)
            return 2
        if name == "--base-url":
            base_url = value.strip()
        else:
            try:
                timeout = float(value)
            except ValueError:
                print(f"--timeout 必须是数字，收到 {value!r}", file=sys.stderr)
                return 2

    try:
        passed = run(base_url=base_url, timeout=timeout)
    except KeyboardInterrupt:  # pragma: no cover
        print("\n被中断", file=sys.stderr)
        return 130
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
