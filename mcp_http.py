"""L5 传输形态之三：**MCP over Streamable HTTP**（单端点 `/mcp`）。

为什么需要这一层
----------------

stdio 传输按「一个客户端一个子进程」用，**跨不了机器边界**：它没有监听套接字，别的
主机/容器根本连不上。要「跑在容器里被局域网访问」，先得有一个监听 TCP 的传输层 ——
本模块就是那一层，只搬字节，**不碰业务**：每条消息最后还是交给
`noobbaozi.mcp_server.dispatch()`（同一个函数、同一套错误码、同一个信封），所以 stdio 与
http 两种传输在协议层是**逐字节同构**的，差别只在「消息怎么过线」。

零第三方依赖
------------

官方 `mcp` SDK 里有 `StreamableHTTPSessionManager`，但验收命令的临时环境里装不了它
（见 `mcp_server` 模块 docstring），所以按 MCP `2025-03-26` / `2025-06-18` 的
**Streamable HTTP** 规范手写：标准库 `http.server.ThreadingHTTPServer` + `json`。

端点表
------

| 方法 | 路径 | 行为 |
|---|---|---|
| `POST` | `/mcp` | 单条 JSON-RPC 消息。`Accept` 含 `text/event-stream` → 回 SSE（一条 `data:` 事件）；否则 `200 + application/json` |
| `GET` | `/mcp` | **405 Method Not Allowed**（见下「GET 的选择」） |
| `DELETE` | `/mcp` | 终止会话 → `200 + JSON`（`ended` 表示是否真的删掉了一个会话） |
| `OPTIONS` | `/mcp` | `204` + CORS 预检头 |
| `GET` | `/health` | `200 + JSON`：`ok / server / version / transport / tools / sessions`（容器 healthcheck 用，**字段名别改**） |

GET 的选择（405 而不是长连接心跳）
----------------------------------

规范允许两种：① 建一条服务端 → 客户端的 SSE 流；② 直接 `405`。本服务**从不主动向
客户端发消息**（没有 sampling / roots / logging / resources 变更通知），一条永远空转的
SSE 长连接只会白占一个线程 + 一条套接字，还让「连接断了」变成新的故障面。所以选
**405 + `Allow: POST, DELETE, OPTIONS`** —— 这是规范明确允许的答复，客户端（含官方 SDK）
收到 405 必须容忍，不会因此判服务不可用。

三条硬约束
----------

1. **threaded**：`ThreadingHTTPServer`，一个连接一个线程。慢客户端（读得慢 / 不发完
   body）只拖住它自己那条线程，不阻塞别人。
2. **不崩**：任一请求处理路径上的异常 → `500 + JSON`，`serve_forever` 继续跑。线程化
   的 HTTP 服务本来就不会因为一个 handler 抛异常而退出，但**返回 500 是必须的**：
   否则客户端只看到连接被掐断。
3. **有界**：单请求 body ≤ `MAX_BODY_BYTES`（1 MiB，超过 → `413`）；socket 读写超时
   `SOCKET_TIMEOUT_S`（默认 30 s，超时 → `408` 或直接断开）。MCP 那层已有 256 KiB 的
   `fit` 上限，这里的 1 MiB 是**传输层**的防滥用闸门（在 `json.loads` 之前就拦住）。

会话
----

`initialize` 成功时在响应头返回 `Mcp-Session-Id`（uuid4 hex），后续请求带这个头就复用
同一个 `_Session`（协议版本 / `initialized` 标记都在里面）。缺失 / 不认识的 id → **按新
会话处理**（不认识的 id 会打一行日志而不是回 404：容器重启后旧客户端的 id 自然失效，
硬拒会让它永远连不上，而重新协商一遍是幂等的）。
"""

from __future__ import annotations

import json
import socket
import threading
import time
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from noobbaozi import mcp_server as core

# =============================================================================
# 一、常量（端点 / 限额 / 头名集中在这里）
# =============================================================================

HTTP_PATH: str = "/mcp"
"""MCP 单端点。请求体是**一条** JSON-RPC 消息（批量数组会由 `dispatch` 判 `-32600`）。"""

HEALTH_PATH: str = "/health"
"""容器 healthcheck 端点（`GET`；不需要会话）。"""

MCP_SESSION_HEADER: str = "Mcp-Session-Id"
"""会话头：`initialize` 成功时在**响应头**返回，客户端后续请求带回来复用会话。"""

TRANSPORT_NAME: str = "http"
"""`/health` 里的 `transport` 字段（与 `noobbaozi/cli.py` 的 `local` 不是一回事）。"""

MAX_BODY_BYTES: int = 1024 * 1024
"""单请求 body 上限（**1 MiB**）。超过 → `413`，且在 `json.loads` 之前就拦住。"""

DRAIN_LIMIT_BYTES: int = 8 * 1024 * 1024
"""超限时最多再**丢弃**多少字节（不缓冲）。

为什么要丢：只回 413 就关连接的话，客户端往往还在往里写 body，Windows 上这会变成
RST，客户端可能连 413 都收不到（拿到的是 `ConnectionAbortedError`）。丢一段有界的
数据再关，客户端能干净地读到 413。丢的部分**不进内存**（64 KiB 一读、读完就扔）。
"""

SOCKET_TIMEOUT_S: float = 30.0
"""单个连接的读写超时（秒）：慢连接 / 半截 body 不会把线程挂死一整天。"""

MAX_SESSIONS: int = 1024
"""会话表上限（超过就淘汰最老的）。每个会话只有几十字节状态，但**不该无界**。"""

JSON_CONTENT_TYPE: str = "application/json; charset=utf-8"
SSE_CONTENT_TYPE: str = "text/event-stream"

ALLOWED_METHODS: str = "POST, DELETE, OPTIONS"
"""`/mcp` 上真正实现了的方法（`GET` 故意不给，见模块 docstring）。"""

CORS_ALLOW_HEADERS: str = (
    "Content-Type, Accept, Mcp-Session-Id, Last-Event-ID, MCP-Protocol-Version"
)
CORS_EXPOSE_HEADERS: str = MCP_SESSION_HEADER
CORS_MAX_AGE: str = "86400"

_UPLOAD_CHUNK: int = 64 * 1024
"""读 body / 丢弃 body 的块大小（不做「一次读满」的分配）。"""

_STATUS_KINDS: dict[int, str] = {
    400: "bad_request",
    404: "not_found",
    405: "method_not_allowed",
    408: "timeout",
    411: "length_required",
    413: "payload_too_large",
    415: "unsupported_media_type",
    500: "internal_error",
}
"""HTTP 状态码 → `error.kind`（传输层错误的 kind 与业务错误共用一套词汇表）。"""


# =============================================================================
# 二、JSON / SSE 编码（stdout 不参与，这里只是格式化）
# =============================================================================


def _json_text(payload: Any) -> str:
    """紧凑 JSON（不转义中文：SSE 与 HTTP body 都是 UTF-8，转义纯属浪费字节）。"""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _json_bytes(payload: Any) -> bytes:
    """紧凑 JSON → UTF-8 字节（HTTP body 用）。"""
    return _json_text(payload).encode("utf-8")


def sse_payload(message: Any) -> bytes:
    """一条 JSON-RPC 消息 → 一段 SSE 事件。

    形状（换行必须精确，SSE 的分帧全靠它）：

    ```
    event: message
    data: {"jsonrpc":"2.0","id":1,"result":{...}}

    ```

    `data:` 后面是**紧凑** JSON（没有裸换行，不需要多行 `data:` 拼接）。
    """
    return f"event: message\ndata: {_json_text(message)}\n\n".encode("utf-8")


# =============================================================================
# 三、会话表（唯一的状态；线程安全）
# =============================================================================


class SessionStore:
    """`Mcp-Session-Id` → `mcp_server._Session`（进程级，带锁）。"""

    __slots__ = ("_lock", "_sessions", "_max", "created", "closed")

    def __init__(self, *, max_sessions: int = MAX_SESSIONS) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, core._Session] = {}
        self._max: int = max(1, int(max_sessions))
        self.created: int = 0
        """累计建立过的会话数（观测用；不等于当前数量）。"""
        self.closed: int = 0
        """累计被 `DELETE /mcp` 终止的会话数（观测用）。"""

    # -- 读 ------------------------------------------------------------------

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)

    def get(self, session_id: str | None) -> core._Session | None:
        """按 id 取会话；没有 / 不认识 → `None`（调用方按新会话处理）。"""
        if not session_id:
            return None
        with self._lock:
            return self._sessions.get(session_id)

    def ids(self) -> list[str]:
        """当前所有会话 id（测试 / 排查用；顺序 = 建立顺序）。"""
        with self._lock:
            return list(self._sessions)

    # -- 写 ------------------------------------------------------------------

    def register(self, session: core._Session) -> str:
        """把一个**已经握手成功**的会话登记进去，返回新建的 `Mcp-Session-Id`。"""
        session_id = uuid.uuid4().hex
        with self._lock:
            while len(self._sessions) >= self._max:
                # dict 保持插入顺序：popitem(last=False) 淘汰最老的那个。
                self._sessions.popitem(last=False)
            self._sessions[session_id] = session
            self.created += 1
            total = len(self._sessions)
        core._log(f"http 会话建立 {session_id[:8]}…（当前 {total} 个）")
        return session_id

    def create(self) -> tuple[str, core._Session]:
        """新建一个空会话并登记（测试 / 无 `initialize` 的直连场景用）。"""
        session = core._Session()
        return self.register(session), session

    def end(self, session_id: str | None) -> bool:
        """删除一个会话；返回「是否真的删掉了一个」。"""
        if not session_id:
            return False
        with self._lock:
            removed = self._sessions.pop(session_id, None)
            if removed is None:
                return False
            self.closed += 1
            total = len(self._sessions)
        core._log(f"http 会话终止 {session_id[:8]}…（剩余 {total} 个）")
        return True


# =============================================================================
# 四、`/health`（容器 healthcheck 的契约，**字段名不许改**）
# =============================================================================


def tool_count() -> int:
    """`/health` 里的 `tools`：**就是 `tools/list` 会给出的条数**（同一个来源，不抄数字）。"""
    return len(core._TOOL_ORDER)


def health_payload(store: SessionStore) -> dict:
    """`/health` 的响应体。

    六个字段是**契约**（容器 healthcheck 按名字取值）：

    | 字段 | 值 |
    |---|---|
    | `ok` | 恒 `True`（能回这个就说明进程活着） |
    | `server` | `noobbaozi` |
    | `version` | `mcp_server.SERVER_VERSION` |
    | `transport` | `http` |
    | `tools` | 工具条数（5） |
    | `sessions` | 当前会话数 |
    """
    return {
        "ok": True,
        "server": core.SERVER_NAME,
        "version": core.SERVER_VERSION,
        "transport": TRANSPORT_NAME,
        "tools": tool_count(),
        "sessions": len(store),
    }


# =============================================================================
# 五、传输层失败（不是 JSON-RPC 错误）
# =============================================================================


class _HTTPFailure(Exception):
    """直接回 `status + JSON` 的传输层失败（**不是** JSON-RPC 错误）。

    区别很重要：JSON-RPC 层的错误（`-32700` / `-32600` / `-32601` / `-32602` /
    `-32603`）一律 `200 + JSON-RPC 错误对象`（协议信封自带错误语义，客户端解析它);
    这里抛的是「消息都没法当成 JSON-RPC 看」或「端点 / 大小 / 方法」这一层的问题。
    """

    def __init__(
        self,
        status: int,
        message: str,
        *,
        kind: str | None = None,
        headers: dict[str, str] | None = None,
        close: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.message = message
        self.kind = kind or _STATUS_KINDS.get(status, "transport_error")
        self.headers = headers or {}
        self.close = close


# =============================================================================
# 六、请求处理（一个连接一个线程）
# =============================================================================


class MCPHTTPRequestHandler(BaseHTTPRequestHandler):
    """`/mcp` + `/health` 的处理函数。

    继承 `BaseHTTPRequestHandler` 后只有四件事要自己管：路由、读 body（有界）、
    把消息交给 `core.dispatch()`、按 `Accept` 决定 JSON 还是 SSE 回。其余（HTTP/1.1
    分帧、`100-continue`、超时、错误日志）标准库都做了。
    """

    protocol_version = "HTTP/1.1"
    """HTTP/1.1：连接默认 keep-alive（每个响应都带准确的 `Content-Length`）。"""

    timeout = SOCKET_TIMEOUT_S
    """socket 读写超时（`StreamRequestHandler.setup()` 会把它设到套接字上）。"""

    server_version = f"{core.SERVER_NAME}-mcp-http"
    sys_version = ""

    _body_consumed: bool = False
    """本条请求的 body 是否已经被读掉（keep-alive 下每条请求重置）。

    为什么要记这个：**没读完 body 就回错误**会让连接上的字节流错位（下一条请求会把
    残留的 body 当成请求行来解析），而且客户端可能根本收不到我们的响应（对端关闭 +
    接收缓冲里还有未读数据 = RST）。所以每条请求进来先清标记，报错前把残留 body
    有界丢掉。
    """

    # -- 基础 ----------------------------------------------------------------

    def handle_one_request(self) -> None:
        """每条请求进来先把「body 读过没有」清掉（同一连接会复用同一个 handler 实例）。"""
        self._body_consumed = False
        super().handle_one_request()

    def version_string(self) -> str:
        """`Server` 响应头的值（别把 Python 版本号也广告出去）。"""
        return f"{core.SERVER_NAME}-mcp-http/{core.SERVER_VERSION}"

    def log_message(self, format: str, *args: object) -> None:
        """把标准库的访问日志改道 `core._log`（→ **stderr**，带统一前缀）。"""
        core._log(f"http {self.address_string()} {format % args}")

    def _path(self) -> str:
        """请求路径（去掉 query / fragment 与末尾斜杠；`/mcp/` 等同 `/mcp`）。"""
        path = (self.path or "/").split("?", 1)[0].split("#", 1)[0]
        if len(path) > 1:
            path = path.rstrip("/") or "/"
        return path

    def _store(self) -> SessionStore:
        """本服务的会话表（挂在 server 上，所有线程共享同一个）。"""
        return self.server.sessions  # type: ignore[attr-defined]

    # -- 写响应 --------------------------------------------------------------

    def _send_bytes(
        self,
        status: int,
        body: bytes,
        content_type: str,
        *,
        extra: dict[str, str] | None = None,
        close: bool = False,
    ) -> None:
        """发一个完整响应（准确的 `Content-Length` + CORS 头）。

        `Access-Control-Allow-Origin: *` 是给浏览器客户端 / 浏览器里跑的调试面板用的：
        MCP Streamable HTTP 本来就要跨源（页面的 origin 与 `127.0.0.1:8080` 不同）。
        """
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            if status not in (204, 304):
                # 204/304 不允许带 Content-Length（RFC 9110 §8.6）。
                self.send_header("Content-Length", str(len(body)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", CORS_EXPOSE_HEADERS)
            for key, value in (extra or {}).items():
                self.send_header(key, value)
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # 客户端在响应写完前就断开了：这是正常事件，不是服务故障。
            self.close_connection = True
            core._debug("http 客户端在响应写完前断开")

    def _send_error(self, failure: _HTTPFailure) -> None:
        """传输层失败 → `status + {"ok": false, "error": {...}}`。"""
        self._drain_pending_body()
        payload = {"ok": False, "error": {"kind": failure.kind, "message": failure.message}}
        self._send_bytes(
            failure.status,
            _json_bytes(payload),
            JSON_CONTENT_TYPE,
            extra=failure.headers,
            close=failure.close,
        )

    def _drain_pending_body(self) -> None:
        """报错前把**还没读的** body 有界丢掉（防 RST / 防 keep-alive 错位）。"""
        if self._body_consumed:
            return
        self._body_consumed = True
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return
        try:
            length = int(str(raw_length).strip())
        except (TypeError, ValueError):
            return
        if length <= 0:
            return
        if length > DRAIN_LIMIT_BYTES:
            # body 实在太大：不浪费带宽去读完，直接关连接（调用方的错误响应已带 close）。
            self.close_connection = True
            return
        self._drain(length)

    def _send_500(self, exc: BaseException) -> None:
        """未预期异常 → `500 + JSON`（进程 / 服务继续跑）。"""
        payload = {
            "ok": False,
            "error": {
                "kind": "internal_error",
                "message": "MCP 服务内部异常，本次请求没有结果",
                "detail": f"{type(exc).__name__}: {exc}",
            },
        }
        self._send_bytes(500, _json_bytes(payload), JSON_CONTENT_TYPE, close=True)

    # -- 读 body -------------------------------------------------------------

    def _read_body(self) -> bytes:
        """读满 `Content-Length` 字节（有界；超限 → 413）。

        Raises:
            _HTTPFailure: 缺长度 / 长度非法 / 超限 / 读不完整（都带 `close=True`，
                因为这时连接上的字节流已经无法保证与消息边界对齐）。
        """
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            if (self.headers.get("Transfer-Encoding") or "").strip().lower() == "chunked":
                raise _HTTPFailure(
                    411,
                    "不支持 chunked 请求体：请用 Content-Length 声明长度",
                    close=True,
                )
            raise _HTTPFailure(411, "缺少 Content-Length", close=True)
        try:
            length = int(str(raw_length).strip())
        except (TypeError, ValueError):
            raise _HTTPFailure(
                400, f"Content-Length 不是整数：{raw_length!r}", close=True
            ) from None
        if length < 0:
            raise _HTTPFailure(400, f"Content-Length 不能是负数：{length}", close=True)
        if length > MAX_BODY_BYTES:
            # 有界丢掉：不缓冲（读完就扔），但要让客户端能把 413 收干净。
            self._body_consumed = True
            self._drain(min(length, DRAIN_LIMIT_BYTES))
            raise _HTTPFailure(
                413,
                f"请求体太大：{length} 字节，上限 {MAX_BODY_BYTES} 字节（1 MiB）",
                close=True,
            )
        if length == 0:
            self._body_consumed = True
            return b""
        data = self.rfile.read(length)
        self._body_consumed = True
        if not data or len(data) != length:
            raise _HTTPFailure(
                400,
                f"请求体读取不完整：声明 {length} 字节，实际 {len(data or b'')} 字节",
                close=True,
            )
        return data

    def _drain(self, limit: int) -> None:
        """有界丢弃请求体（读完就扔，不进内存），让客户端能把 413 收干净。"""
        remaining = max(0, int(limit))
        try:
            while remaining > 0:
                block = self.rfile.read(min(_UPLOAD_CHUNK, remaining))
                if not block:
                    break
                remaining -= len(block)
        except (OSError, ValueError):  # 对端断了 / 超时：不用补刀，直接走 413
            pass

    # -- 会话 ----------------------------------------------------------------

    def _resolve_session(self) -> tuple[core._Session, bool]:
        """取会话；返回 `(session, 是否新会话)`。

        新会话**不登记**：只有 `initialize` 成功才登记并发 `Mcp-Session-Id`（规范要求
        「服务端分配会话 id 发生在初始化阶段」）。没握手的请求用一次性会话跑完就丢 ——
        既满足「缺失时按新会话处理」，也不会让未握手的请求把会话表撑爆。
        """
        session_id = (self.headers.get(MCP_SESSION_HEADER) or "").strip()
        if session_id:
            existing = self._store().get(session_id)
            if existing is not None:
                return existing, False
            core._log(f"未知会话 {session_id[:8]}…（按新会话处理）", level="warning")
        return core._Session(), True

    # -- 分发（异常边界） ----------------------------------------------------

    def _guarded(self, handler: Callable[[], None]) -> None:
        """执行一个处理函数：传输层失败回状态码，未预期异常回 500，**都不往外抛**。"""
        try:
            handler()
        except _HTTPFailure as failure:
            core._debug(f"http {failure.status} {failure.message}")
            self._send_error(failure)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True
            core._debug("http 连接被客户端提前关闭")
        except (TimeoutError, socket.timeout):
            core._log(f"http 请求超时（>{SOCKET_TIMEOUT_S}s），断开连接", level="warning")
            self._send_error(
                _HTTPFailure(408, f"请求超时：{SOCKET_TIMEOUT_S}s 内没有读完请求", close=True)
            )
        except Exception as exc:  # noqa: BLE001 - 边界层：任何异常都不得让服务倒下
            core._log(f"http 请求处理异常：\n{traceback.format_exc()}", level="error")
            self._send_500(exc)

    # -- HTTP 方法 -----------------------------------------------------------

    def do_POST(self) -> None:  # noqa: N802 - 标准库约定的方法名
        self._guarded(self._handle_post)

    def do_GET(self) -> None:  # noqa: N802
        self._guarded(self._handle_get)

    def do_DELETE(self) -> None:  # noqa: N802
        self._guarded(self._handle_delete)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._guarded(self._handle_options)

    # -- POST /mcp -----------------------------------------------------------

    def _handle_post(self) -> None:
        """`POST /mcp`：一条 JSON-RPC 消息 → JSON 或 SSE 响应；通知 → 202 无 body。"""
        path = self._path()
        if path == HEALTH_PATH:
            raise _HTTPFailure(
                405, f"{HEALTH_PATH} 只支持 GET", headers={"Allow": "GET"}
            )
        if path != HTTP_PATH:
            raise _HTTPFailure(
                404,
                f"未知路径 {path!r}；MCP 端点是 {HTTP_PATH}，健康检查是 {HEALTH_PATH}",
            )

        started = time.perf_counter()
        body = self._read_body()
        session, fresh = self._resolve_session()
        wants_sse = "text/event-stream" in (self.headers.get("Accept") or "").lower()

        text = core._decode_line(body)
        message: object = None
        if text is None:
            core._log("POST /mcp 收到非 UTF-8 的请求体，已丢弃", level="warning")
            response: dict | None = core._error_response(
                None, core.PARSE_ERROR, "请求体不是 UTF-8 文本"
            )
        else:
            try:
                message = json.loads(text)
            except json.JSONDecodeError as exc:
                core._log(f"POST /mcp JSON 解析失败：{exc}", level="warning")
                response = core._error_response(
                    None, core.PARSE_ERROR, f"JSON 解析失败：{exc}"
                )
            else:
                response = core.dispatch(message, session)

        extra: dict[str, str] = {}
        if (
            response is not None
            and fresh
            and isinstance(message, dict)
            and message.get("method") == "initialize"
            and "result" in response
        ):
            # 只有握手**成功**才分配会话 id（失败时不发头，客户端会当成没握手）。
            extra[MCP_SESSION_HEADER] = self._store().register(session)

        if response is None:
            # 通知（无 id）/ 客户端发来的响应：202 + **空 body**（规范要求）。
            self._send_bytes(202, b"", JSON_CONTENT_TYPE)
            core._debug(
                f"POST /mcp 通知 → 202（{(time.perf_counter() - started) * 1000.0:.1f}ms）"
            )
            return

        if wants_sse:
            self._send_bytes(
                200,
                sse_payload(response),
                SSE_CONTENT_TYPE,
                extra={**extra, "Cache-Control": "no-cache"},
            )
        else:
            self._send_bytes(200, _json_bytes(response), JSON_CONTENT_TYPE, extra=extra)
        core._debug(
            f"POST /mcp {'sse' if wants_sse else 'json'} → 200"
            f"（{(time.perf_counter() - started) * 1000.0:.1f}ms）"
        )

    # -- GET /health + GET /mcp(405) ----------------------------------------

    def _handle_get(self) -> None:
        """`GET /health` → 健康信息；`GET /mcp` → 405（不提供下行 SSE 流）。"""
        self._drain_pending_body()  # GET 没有 body；有也不读，先丢掉免得 keep-alive 错位
        path = self._path()
        if path == HTTP_PATH:
            raise _HTTPFailure(
                405,
                "GET /mcp 不提供 SSE 下行流：本服务不主动向客户端推送消息，"
                "请用 POST /mcp 发请求",
                headers={"Allow": ALLOWED_METHODS},
            )
        if path != HEALTH_PATH:
            raise _HTTPFailure(
                404,
                f"未知路径 {path!r}；MCP 端点是 {HTTP_PATH}，健康检查是 {HEALTH_PATH}",
            )
        self._send_bytes(
            200, _json_bytes(health_payload(self._store())), JSON_CONTENT_TYPE
        )

    # -- DELETE /mcp ---------------------------------------------------------

    def _handle_delete(self) -> None:
        """`DELETE /mcp`：终止会话（幂等；没带 / 不认识 id 也回 200，`ended: false`）。"""
        self._drain_pending_body()  # DELETE 不带 body；有也不读
        path = self._path()
        if path == HEALTH_PATH:
            raise _HTTPFailure(
                405, f"{HEALTH_PATH} 只支持 GET", headers={"Allow": "GET"}
            )
        if path != HTTP_PATH:
            raise _HTTPFailure(
                404,
                f"未知路径 {path!r}；MCP 端点是 {HTTP_PATH}，健康检查是 {HEALTH_PATH}",
            )
        session_id = (self.headers.get(MCP_SESSION_HEADER) or "").strip() or None
        ended = self._store().end(session_id)
        payload = {
            "ok": True,
            "ended": ended,
            "session_id": session_id,
            "sessions": len(self._store()),
        }
        self._send_bytes(200, _json_bytes(payload), JSON_CONTENT_TYPE)

    # -- OPTIONS /mcp（CORS 预检） ------------------------------------------

    def _handle_options(self) -> None:
        """`OPTIONS /mcp`：CORS 预检（204，无 body）。"""
        self._drain_pending_body()  # 预检请求不该带 body；有也不读
        path = self._path()
        if path == HEALTH_PATH:
            raise _HTTPFailure(
                405, f"{HEALTH_PATH} 只支持 GET", headers={"Allow": "GET"}
            )
        if path != HTTP_PATH:
            raise _HTTPFailure(
                404,
                f"未知路径 {path!r}；MCP 端点是 {HTTP_PATH}，健康检查是 {HEALTH_PATH}",
            )
        self._send_bytes(
            204,
            b"",
            "text/plain; charset=utf-8",
            extra={
                "Allow": ALLOWED_METHODS,
                "Access-Control-Allow-Methods": ALLOWED_METHODS,
                "Access-Control-Allow-Headers": CORS_ALLOW_HEADERS,
                "Access-Control-Max-Age": CORS_MAX_AGE,
            },
        )


# =============================================================================
# 七、服务对象与生命周期
# =============================================================================


class MCPHTTPServer(ThreadingHTTPServer):
    """threaded MCP HTTP 服务（一个连接一个线程 + 一张共享会话表）。"""

    daemon_threads = True
    """工作线程是 daemon：`server_close()` 不会等长连接（stdin 那条 30 s 超时）结束。"""

    allow_reuse_address = True
    request_queue_size = 64
    """listen backlog：局域网里多个客户端同时连进来时不至于排队被拒。"""

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler] = MCPHTTPRequestHandler,
        *,
        sessions: SessionStore | None = None,
    ) -> None:
        self.sessions: SessionStore = sessions or SessionStore()
        self.started_at: float = time.time()
        super().__init__(address, handler)


def create_server(
    host: str = core.DEFAULT_HTTP_HOST,
    port: int = core.DEFAULT_HTTP_PORT,
    *,
    sessions: SessionStore | None = None,
) -> MCPHTTPServer:
    """建一个**已绑定但还没开始 accept** 的服务（`port=0` 时由系统分配端口）。

    Returns:
        `MCPHTTPServer`；真实监听地址在 `server.server_address`（`port=0` 时用这个读
        系统分配到的端口）。

    Raises:
        OSError: 端口被占用 / 地址不可绑。
    """
    server = MCPHTTPServer((host, int(port)), MCPHTTPRequestHandler, sessions=sessions)
    bound_host, bound_port = server.server_address[:2]
    core._log(
        f"{core.SERVER_NAME} {core.SERVER_VERSION} 启动（http）："
        f"POST http://{bound_host}:{bound_port}{HTTP_PATH} · "
        f"GET http://{bound_host}:{bound_port}{HEALTH_PATH}"
    )
    if bound_host in ("0.0.0.0", "::", ""):
        core._log(
            f"监听 0.0.0.0（所有网卡）：本机用 http://127.0.0.1:{bound_port}{HTTP_PATH} "
            "访问，局域网用本机 IP 访问"
        )
    return server


def _warmup() -> None:
    """预热 `api` 与工具清单（第一次请求不用付导入 / 建 schema 的钱）。

    失败**不退出**：和 stdio 入口一致 —— 让客户端在真正调用时拿到结构化的
    `internal_error`，而不是连不上（「进程活着但工具坏了」比「连不上」可诊断得多）。
    """
    started = time.perf_counter()
    try:
        core._api()
        core._build_tool_specs()
        core._log(
            f"工具就绪：{list(core._TOOL_ORDER)}"
            f"（{(time.perf_counter() - started) * 1000:.0f}ms）"
        )
    except BaseException as exc:  # noqa: BLE001 - 预热失败不阻断服务
        core._log(
            f"预热失败（服务继续，调用时返回 internal_error）：{type(exc).__name__}: {exc}",
            level="warning",
        )


def _install_stop_handler() -> Callable[[], None]:
    """`SIGTERM` → `KeyboardInterrupt`（容器 `docker stop` 发的就是 SIGTERM）。

    Returns:
        还原函数（正常退出时调一下）。装不上（非主线程 / 平台不支持）时返回空操作
        —— 退化成「只能 Ctrl+C」，不影响服务本身。
    """
    try:
        import signal

        def _stop(_signum: int, _frame: Any) -> None:
            raise KeyboardInterrupt

        previous = signal.signal(signal.SIGTERM, _stop)
    except (ImportError, ValueError, OSError, AttributeError, RuntimeError):
        return lambda: None

    def _restore() -> None:
        try:
            signal.signal(signal.SIGTERM, previous)  # type: ignore[arg-type]
        except (ValueError, OSError, TypeError):  # pragma: no cover - 极端环境
            pass

    return _restore


def run_http(
    host: str = core.DEFAULT_HTTP_HOST,
    port: int = core.DEFAULT_HTTP_PORT,
    *,
    log_level: str = "info",
    warmup: bool = True,
    poll_interval: float = 0.25,
) -> int:
    """起 HTTP 服务并阻塞（`python -m noobbaozi.mcp_server --transport http`）。

    Returns:
        进程退出码：`0` = 正常停止；`1` = 端口 / 地址绑不上；`2` = 参数不合法。
    """
    try:
        core.set_log_level(log_level)
    except ValueError as exc:
        core._log(str(exc), level="error")
        return 2

    try:
        server = create_server(host, port)
    except OSError as exc:
        core._log(f"HTTP 服务无法监听 {host}:{port}：{exc}", level="error")
        return 1

    if warmup:
        _warmup()

    restore = _install_stop_handler()
    try:
        server.serve_forever(poll_interval=poll_interval)
    except KeyboardInterrupt:
        core._log("收到中断，停止 HTTP 服务")
    finally:
        restore()
        server.server_close()
    return 0


__all__ = [
    "ALLOWED_METHODS",
    "CORS_ALLOW_HEADERS",
    "HEALTH_PATH",
    "HTTP_PATH",
    "MAX_BODY_BYTES",
    "MCPHTTPServer",
    "MCPHTTPRequestHandler",
    "MCP_SESSION_HEADER",
    "SOCKET_TIMEOUT_S",
    "SessionStore",
    "TRANSPORT_NAME",
    "create_server",
    "health_payload",
    "run_http",
    "sse_payload",
    "tool_count",
]
