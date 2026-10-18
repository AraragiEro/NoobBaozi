"""`noobbaozi/mcp_http.py` 的传输契约测试（T-47：MCP over Streamable HTTP）。

钉住八件事：

1. **端点形状**：`POST /mcp`（JSON / SSE 两种响应）、`GET /mcp` → `405`、
   `DELETE /mcp` → 终止会话、`OPTIONS /mcp` → CORS 预检、`GET /health` → 六个字段；
2. **会话**：`initialize` 成功时在**响应头**给 `Mcp-Session-Id`，后续请求带回来复用；
   不认识 / 缺失的 id 按新会话处理（不硬拒）；
3. **通知**：无 `id` 的消息 → `202` 且 **body 为空**（回了就是错位的响应）；
4. **错误码沿用 `dispatch`**：坏 JSON `-32700`、未知方法 `-32601`、缺 `fit` `-32602`；
5. **有界**：请求体 > 1 MiB → `413`；慢客户端（只发头不发 body）**不阻塞**别人；
6. **不崩**：handler 里抛异常 → `500 + JSON`，随后服务仍然可用；
7. **并发**：两个线程同时 `fit_get`，都拿到结果（threaded 而不是串行）；
8. **命令行开关**：默认仍是 stdio（`_parse_cli([])`）、`--transport http --host 0.0.0.0
   --port N` 真能起服务、`0.0.0.0` 绑定的服务用 `127.0.0.1` 连得上、stdout 一个字节没有。

**断言非永真**：`test_health_tools_check_is_not_vacuous` 把 `/health` 的 `tools` 改成
「`len(TOOL_NAMES) - 1`」，配对断言「改之前通过 / 改之后 `check_health` 必须抛」——
证明这条断言真的在断言（PROGRESS 第 180 / 196 条的教训：空转的断言比没有断言更危险）。

**工具条数不写字面量**：`TOOL_NAMES` 直接取 `mcp_server.TOOL_NAMES`（单一真源），
所有「条数」都从 `len(TOOL_NAMES)` 算 —— 以后加工具，本文件一行都不用改。

绝大多数用例起**真实 HTTP 服务**（后台线程 + 随机端口 `0`）并用真实 `http.client` 连，
只有「命令行入口本身」必须起子进程。
"""

from __future__ import annotations

import http.client
import json
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from noobbaozi import api, mcp_http, mcp_server
from noobbaozi.mcp import client_smoke, client_smoke_http

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = NOOBBAOZI_ROOT.parent
EFT_PATH = NOOBBAOZI_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"

TOOL_NAMES = mcp_server.TOOL_NAMES
"""工具清单**从 `mcp_server.TOOL_NAMES` 派生**（本文件不写任何工具名 / 条数字面量）。

加一个工具时这里一行都不用改 —— 这正是 T-50 把「工具清单」收敛成单一真源的目的。
断言「名字集合 == `TOOL_NAMES`」而不是「条数 == 5」：前者能抓出改名 / 漏注册，
后者只会在加工具时制造一次无意义的红灯。
"""
SPEED_PATH = "mobility.speed_mps"


# =============================================================================
# fixtures / 小工具
# =============================================================================


@pytest.fixture(scope="module", autouse=True)
def _singletons() -> None:
    """工具调用要走同一套 `Engine` / `Index` 单例；建不起来就跳过（与 stdio 测试一致）。"""
    try:
        api.get_engine()
        api.get_index()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"引擎 / 索引不可用：{exc}")


@pytest.fixture(scope="module")
def http_server() -> str:
    """起一个真实 HTTP 服务（后台线程 + `port=0` 让系统分配），返回 `host:port`。"""
    server = mcp_http.create_server("127.0.0.1", 0)
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    host, port = server.server_address[:2]
    try:
        yield f"{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=10)
        assert not thread.is_alive(), "后台服务线程没停下来"


@pytest.fixture(scope="module")
def eft_text() -> str:
    return EFT_PATH.read_text(encoding="utf-8")


def _client(base: str, *, timeout: float = 120.0) -> client_smoke_http.HttpClient:
    """指向某个 `host:port` 的客户端（每个请求一条新连接，确定性优先）。"""
    return client_smoke_http.HttpClient(f"http://{base}", timeout=timeout)


def _request(
    base: str,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> client_smoke_http.HttpResult:
    """raw HTTP 请求（4xx / 5xx 也照常返回，不抛）。"""
    return _client(base).request(method, path, body=body, headers=headers)


def _jsonrpc_body(message: dict) -> bytes:
    return json.dumps(message, ensure_ascii=False).encode("utf-8")


def _rpc(
    base: str,
    method: str,
    params: object = None,
    *,
    request_id: object = 1,
    session: str | None = None,
    accept: str = client_smoke_http.JSON_ACCEPT,
    raw_body: bytes | None = None,
) -> tuple[client_smoke_http.HttpResult, dict]:
    """发一条 JSON-RPC 请求，返回 `(HTTP 结果, 消息对象)`（JSON 或 SSE 都认）。"""
    if raw_body is None:
        message: dict = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        raw_body = _jsonrpc_body(message)
    headers = {"Content-Type": "application/json", "Accept": accept}
    if session:
        headers[mcp_http.MCP_SESSION_HEADER] = session
    result = _request(base, "POST", mcp_http.HTTP_PATH, body=raw_body, headers=headers)
    return result, client_smoke_http._message_from(result, accept=accept)


def _initialize(base: str, *, request_id: object = 1) -> tuple[client_smoke_http.HttpResult, dict, str]:
    """握手一次，返回 `(HTTP 结果, 消息对象, 会话 id)`。"""
    result, payload = _rpc(
        base,
        "initialize",
        {
            "protocolVersion": client_smoke.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "pytest", "version": "1"},
        },
        request_id=request_id,
    )
    return result, payload, result.header(mcp_http.MCP_SESSION_HEADER) or ""


def _call(
    base: str,
    tool: str,
    arguments: dict,
    *,
    session: str | None = None,
    request_id: object = 99,
) -> tuple[dict, dict]:
    """`tools/call` → `(CallToolResult, 信封)`。"""
    result, payload = _rpc(
        base,
        "tools/call",
        {"name": tool, "arguments": arguments},
        request_id=request_id,
        session=session,
    )
    assert "error" not in payload, f"{tool} 不该是协议错误：{payload.get('error')}"
    call_result = payload["result"]
    envelope, _is_error, _structured = client_smoke._envelope_from_result(call_result)
    return call_result, envelope


def _free_port() -> int:
    """拿一个当前空闲的端口（绑一下再放开；用于命令行子进程测试）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# =============================================================================
# 一、POST /mcp：握手、清单、通知
# =============================================================================


def test_post_initialize_returns_session_header(http_server: str) -> None:
    """`initialize` → `200` + **`Mcp-Session-Id` 响应头** + 原样回协议版本。"""
    result, payload, session = _initialize(http_server)
    assert result.status == 200
    assert "application/json" in result.content_type
    assert payload["id"] == 1
    assert payload["jsonrpc"] == "2.0"
    assert session, f"initialize 必须在响应头给会话 id，实际头：{sorted(result.headers)}"
    assert len(session) >= 16, f"会话 id 太短：{session!r}"
    handshake = payload["result"]
    assert handshake["protocolVersion"] == client_smoke.PROTOCOL_VERSION
    assert isinstance(handshake["capabilities"]["tools"], dict)
    assert handshake["serverInfo"] == {
        "name": mcp_server.SERVER_NAME,
        "version": mcp_server.SERVER_VERSION,
    }


def test_tools_list_matches_the_single_source_of_truth(http_server: str) -> None:
    """`tools/list` 的名字与顺序 == `mcp_server.TOOL_NAMES`（不比对数字）。"""
    _result, _payload, session = _initialize(http_server)
    result, payload = _rpc(http_server, "tools/list", request_id=2, session=session)
    assert result.status == 200
    tools = [tool["name"] for tool in payload["result"]["tools"]]
    assert tools == list(TOOL_NAMES)


def test_notification_is_202_without_body(http_server: str) -> None:
    """通知（无 `id`）→ `202` 且 **body 为空**（绝不回 JSON-RPC 响应）。"""
    _result, _payload, session = _initialize(http_server)
    result = _request(
        http_server,
        "POST",
        mcp_http.HTTP_PATH,
        body=_jsonrpc_body({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        headers={
            "Content-Type": "application/json",
            mcp_http.MCP_SESSION_HEADER: session,
        },
    )
    assert result.status == 202
    assert result.body == b"", f"通知不该带 body，实际 {result.body[:200]!r}"
    # 未知通知同样 202 + 空 body
    unknown = _request(
        http_server,
        "POST",
        mcp_http.HTTP_PATH,
        body=_jsonrpc_body({"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}),
        headers={"Content-Type": "application/json"},
    )
    assert unknown.status == 202
    assert unknown.body == b""


def test_session_is_reused_and_unknown_id_starts_a_new_one(http_server: str) -> None:
    """带已知 id → 复用同一会话；带不认识 / 不带的 id → 按新会话处理（不硬拒）。"""
    _result, _payload, session = _initialize(http_server)
    # 复用：同一个 id 发两次 tools/list，都能成功（会话状态在服务端）
    for _ in range(2):
        result, payload = _rpc(http_server, "tools/list", session=session)
        assert result.status == 200
        assert len(payload["result"]["tools"]) == len(TOOL_NAMES)
        assert mcp_http.MCP_SESSION_HEADER not in result.headers, "已有会话时不该再发会话头"

    # 不认识的 id：回 200（新会话跑完就丢），不是 404
    bogus, payload = _rpc(http_server, "tools/list", session="0" * 32)
    assert bogus.status == 200
    assert len(payload["result"]["tools"]) == len(TOOL_NAMES)

    # 完全不带头：同样按新会话处理
    anonymous, payload = _rpc(http_server, "tools/list")
    assert anonymous.status == 200
    assert len(payload["result"]["tools"]) == len(TOOL_NAMES)


# =============================================================================
# 二、错误码沿用 dispatch（不是传输层错误）
# =============================================================================


def test_bad_json_is_parse_error(http_server: str) -> None:
    """坏 JSON（含空 body）→ `-32700`，`id` 为 null。**HTTP 状态仍是 200**（信封携带错误）。"""
    for body in (b"not json at all", b"", b'{"jsonrpc":"2.0",}'):
        result, payload = _rpc(
            http_server, "", raw_body=body
        )
        assert result.status == 200, f"JSON-RPC 层的错误不该用 HTTP 状态码表达：{body!r}"
        assert payload["error"]["code"] == mcp_server.PARSE_ERROR
        assert payload["id"] is None


def test_unknown_method_is_method_not_found(http_server: str) -> None:
    """未知方法 → `-32601`。"""
    result, payload = _rpc(http_server, "tools/not_a_method")
    assert result.status == 200
    assert payload["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_missing_fit_is_invalid_params(http_server: str) -> None:
    """缺 `fit` → `-32602` + `error.data.field == "fit"`（与 stdio 完全一致）。"""
    result, payload = _rpc(
        http_server,
        "tools/call",
        {"name": "fit_index", "arguments": {}},
        request_id=7,
    )
    assert result.status == 200
    assert payload["id"] == 7
    assert payload["error"]["code"] == mcp_server.INVALID_PARAMS
    assert payload["error"]["data"]["field"] == "fit"


def test_batch_array_is_invalid_request(http_server: str) -> None:
    """顶层数组（JSON-RPC 批量）→ `-32600`（批量不支持，别假装支持）。"""
    result, payload = _rpc(
        http_server, "", raw_body=b'[{"jsonrpc":"2.0","id":1,"method":"ping"}]'
    )
    assert result.status == 200
    assert payload["error"]["code"] == mcp_server.INVALID_REQUEST
    assert payload["id"] is None


# =============================================================================
# 三、Accept 协商：SSE 与 JSON
# =============================================================================


def test_accept_event_stream_returns_sse(http_server: str) -> None:
    """`Accept: text/event-stream` → `text/event-stream` + 至少一条 `data:` 行。"""
    _result, _payload, session = _initialize(http_server)
    result, payload = _rpc(
        http_server,
        "tools/list",
        request_id=3,
        session=session,
        accept=client_smoke_http.SSE_ACCEPT,
    )
    assert result.status == 200
    assert "text/event-stream" in result.content_type
    assert b"data:" in result.body
    text = result.text
    assert text.endswith("\n\n"), f"SSE 帧必须以空行结束：{text[-40:]!r}"
    assert payload["id"] == 3
    assert len(payload["result"]["tools"]) == len(TOOL_NAMES)
    # 同一请求，不带 Accept 时回 JSON（两条路走同一个 dispatch，结果同构）
    plain, plain_payload = _rpc(http_server, "tools/list", request_id=3, session=session)
    assert "application/json" in plain.content_type
    assert plain_payload["result"] == payload["result"]


def test_accept_both_prefers_sse(http_server: str) -> None:
    """`Accept: application/json, text/event-stream` → 规范允许任选，本服务选 SSE。"""
    result, payload = _rpc(
        http_server,
        "ping",
        request_id=4,
        accept="application/json, text/event-stream",
    )
    assert "text/event-stream" in result.content_type
    assert payload["result"] == {}


# =============================================================================
# 四、GET /health（容器 healthcheck 的契约）
# =============================================================================


def test_health_has_the_six_contract_fields(http_server: str) -> None:
    """`/health` 六个字段齐全，值照契约（`tools` 与 `tools/list` 同源）。"""
    _result, _payload, _session = _initialize(http_server)
    result = _request(http_server, "GET", mcp_http.HEALTH_PATH)
    assert result.status == 200
    assert "application/json" in result.content_type
    payload = result.json()
    assert set(client_smoke_http.HEALTH_FIELDS) <= set(payload)
    client_smoke_http.check_health(payload, expected_tools=len(TOOL_NAMES))
    assert payload["server"] == mcp_server.SERVER_NAME
    assert payload["version"] == mcp_server.SERVER_VERSION
    assert payload["transport"] == "http"
    assert payload["tools"] == mcp_server.TOOL_COUNT == len(TOOL_NAMES)
    assert len(TOOL_NAMES) >= 7, f"本轮至少 7 个工具，实际 {TOOL_NAMES}"
    assert isinstance(payload["sessions"], int) and payload["sessions"] >= 1


def test_health_tools_check_is_not_vacuous(
    http_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**断言非永真**：`/health` 的 `tools` 改成「少一个」→ `check_health` 必须变红。"""
    expected = len(TOOL_NAMES)
    # 改之前：通过
    healthy = _request(http_server, "GET", mcp_http.HEALTH_PATH).json()
    client_smoke_http.check_health(healthy, expected_tools=expected)

    # 把服务端报的条数改成「少一个」（模拟「契约被改坏」）：字段检查必须发现
    monkeypatch.setattr(mcp_http, "tool_count", lambda: expected - 1)
    broken = _request(http_server, "GET", mcp_http.HEALTH_PATH).json()
    assert broken["tools"] == expected - 1
    with pytest.raises(AssertionError):
        client_smoke_http.check_health(broken, expected_tools=expected)


# =============================================================================
# 五、方法 / 路径 / 会话终止 / CORS
# =============================================================================


def test_get_mcp_is_method_not_allowed(http_server: str) -> None:
    """`GET /mcp` → `405` + `Allow`（本服务不主动推送消息，不建空转的 SSE 流）。"""
    result = _request(http_server, "GET", mcp_http.HTTP_PATH)
    assert result.status == 405
    assert result.header("allow") == mcp_http.ALLOWED_METHODS
    payload = result.json()
    assert payload["ok"] is False
    assert payload["error"]["kind"] == "method_not_allowed"
    assert "POST" in payload["error"]["message"]


def test_delete_terminates_the_session(http_server: str) -> None:
    """`DELETE /mcp` → `200`，`ended: true`，再删一次 `ended: false`（幂等）。"""
    _result, _payload, session = _initialize(http_server)
    headers = {mcp_http.MCP_SESSION_HEADER: session}
    first = _request(http_server, "DELETE", mcp_http.HTTP_PATH, headers=headers)
    assert first.status == 200
    payload = first.json()
    assert payload["ok"] is True
    assert payload["ended"] is True
    assert payload["session_id"] == session

    second = _request(http_server, "DELETE", mcp_http.HTTP_PATH, headers=headers)
    assert second.status == 200
    assert second.json()["ended"] is False
    # 已经不存在的会话 id 也被当成新会话（不硬拒）：句柄没了但服务照常
    result, _payload = _rpc(http_server, "tools/list", session=session)
    assert result.status == 200


def test_options_is_cors_preflight(http_server: str) -> None:
    """`OPTIONS /mcp` → `204` + CORS 预检头（来源任意、允许三个请求头）。"""
    result = _request(
        http_server,
        "OPTIONS",
        mcp_http.HTTP_PATH,
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert result.status == 204
    assert result.body == b""
    assert result.header("access-control-allow-origin") == "*"
    methods = result.header("access-control-allow-methods") or ""
    assert "POST" in methods and "DELETE" in methods
    allowed = result.header("access-control-allow-headers") or ""
    for header in ("Content-Type", "Accept", mcp_http.MCP_SESSION_HEADER):
        assert header in allowed
    assert result.header("allow") == mcp_http.ALLOWED_METHODS


def test_unknown_path_is_404(http_server: str) -> None:
    """没有实现的路径 → `404` + 提示（不是静默 200）。"""
    for method in ("GET", "POST"):
        result = _request(
            http_server, method, "/nope", body=b"{}" if method == "POST" else None
        )
        assert result.status == 404
        payload = result.json()
        assert payload["error"]["kind"] == "not_found"
        assert mcp_http.HTTP_PATH in payload["error"]["message"]


def test_cors_header_present_on_normal_responses(http_server: str) -> None:
    """普通响应也带 `Access-Control-Allow-Origin: *`（浏览器里的调试面板能用）。"""
    result = _request(http_server, "GET", mcp_http.HEALTH_PATH)
    assert result.header("access-control-allow-origin") == "*"
    assert result.header("access-control-expose-headers") == mcp_http.MCP_SESSION_HEADER


# =============================================================================
# 六、硬约束：有界、并发、不崩
# =============================================================================


def test_oversized_body_is_413(http_server: str) -> None:
    """请求体 > 1 MiB → `413`（在 `json.loads` 之前就拦住）。"""
    oversized = b"x" * (mcp_http.MAX_BODY_BYTES + 1)
    result = _request(
        http_server,
        "POST",
        mcp_http.HTTP_PATH,
        body=oversized,
        headers={"Content-Type": "application/json"},
    )
    assert result.status == 413
    payload = result.json()
    assert payload["ok"] is False
    assert payload["error"]["kind"] == "payload_too_large"
    assert str(mcp_http.MAX_BODY_BYTES) in payload["error"]["message"]


def test_empty_body_without_content_length_is_411(http_server: str) -> None:
    """没有 `Content-Length` 的 POST → `411`（不猜长度，也不挂在那里等）。"""
    host, port_text = http_server.split(":")
    connection = http.client.HTTPConnection(host, int(port_text), timeout=30)
    try:
        connection.putrequest("POST", mcp_http.HTTP_PATH)
        connection.putheader("Content-Type", "application/json")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 411
        payload = json.loads(response.read())
        assert payload["error"]["kind"] == "length_required"
    finally:
        connection.close()


def test_two_clients_call_fit_get_concurrently(http_server: str, eft_text: str) -> None:
    """两个线程同时 `fit_get`：**都成功**（threaded，不是串行队列）。"""
    _result, _payload, session = _initialize(http_server)
    outcomes: list[tuple[int, dict]] = []
    lock = threading.Lock()

    def _worker(request_id: int) -> None:
        result, payload = _rpc(
            http_server,
            "tools/call",
            {"name": "fit_get", "arguments": {"fit": eft_text, "path": SPEED_PATH}},
            request_id=request_id,
            session=session,
        )
        with lock:
            outcomes.append((result.status, payload))

    threads = [threading.Thread(target=_worker, args=(index,)) for index in (11, 12)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=180)
    elapsed = time.perf_counter() - started

    assert not any(thread.is_alive() for thread in threads), "并发调用没在超时内结束"
    assert len(outcomes) == 2, f"两个线程都应拿到响应，实际 {len(outcomes)}"
    for status, payload in outcomes:
        assert status == 200
        envelope, _is_error, _structured = client_smoke._envelope_from_result(payload["result"])
        assert envelope["ok"] is True, f"并发的 fit_get 应成功，实际 {envelope.get('error')}"
        assert envelope["result"]["path"] == SPEED_PATH
        assert envelope["result"]["value"] > 0
    assert elapsed < 180


def test_slow_client_does_not_block_others(http_server: str) -> None:
    """**慢客户端不阻塞别人**：一条连接只发头不发 body，另一个请求照样立刻成功。

    这是「必须 threaded」的直接证据：`ThreadingHTTPServer` 给每条连接一个线程，
    半截请求只会挂住它自己那条线程（30 s 超时后自生自灭）。
    """
    host, port_text = http_server.split(":")
    stalled = socket.create_connection((host, int(port_text)), timeout=10)
    try:
        stalled.sendall(
            b"POST /mcp HTTP/1.1\r\n"
            b"Host: " + host.encode() + b"\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: 4096\r\n\r\n"
        )  # 故意不发 body
        started = time.perf_counter()
        result = _request(http_server, "GET", mcp_http.HEALTH_PATH)
        elapsed = time.perf_counter() - started
        assert result.status == 200, "半截请求不该影响别的客户端"
        assert elapsed < 15, f"另一个客户端被卡住了 {elapsed:.1f}s（说明不是 threaded）"
    finally:
        stalled.close()


def test_handler_exception_is_500_and_service_survives(
    http_server: str, monkeypatch: pytest.MonkeyPatch, eft_text: str
) -> None:
    """handler 抛异常 → `500 + JSON`；随后**同一个服务仍可用**（进程 / 服务没倒）。"""

    def _boom(_message: object, _session: object) -> dict:
        raise RuntimeError("故意的传输层内部异常")

    monkeypatch.setattr(mcp_server, "dispatch", _boom)
    # 500 的 body 是**传输层**错误信封（不是 JSON-RPC 消息），所以直接用 raw 读
    failed = _request(
        http_server,
        "POST",
        mcp_http.HTTP_PATH,
        body=_jsonrpc_body({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
        headers={"Content-Type": "application/json"},
    )
    assert failed.status == 500
    assert "application/json" in failed.content_type
    payload = failed.json()
    assert payload["ok"] is False
    assert payload["error"]["kind"] == "internal_error"
    assert "故意的传输层内部异常" in payload["error"]["detail"]

    monkeypatch.undo()
    # 服务仍可用：同一个端口、同一条路径，再调一次成功
    healthy, payload = _rpc(http_server, "tools/list")
    assert healthy.status == 200
    assert len(payload["result"]["tools"]) == len(TOOL_NAMES)
    _result, envelope = _call(http_server, "fit_get", {"fit": eft_text, "path": SPEED_PATH})
    assert envelope["ok"] is True


def test_engine_crash_is_a_business_error_not_a_500(
    http_server: str, monkeypatch: pytest.MonkeyPatch, eft_text: str
) -> None:
    """**工具内部**异常（不是传输层）仍走 `isError: true` 的**成功响应** —— 与 stdio 同构。"""

    def _boom(_arguments: dict) -> dict:
        raise RuntimeError("故意的工具内部异常")

    monkeypatch.setitem(mcp_server._HANDLERS, "fit_get", _boom)
    result, payload = _rpc(
        http_server,
        "tools/call",
        {"name": "fit_get", "arguments": {"fit": eft_text, "path": SPEED_PATH}},
    )
    assert result.status == 200, "业务层异常不该变成 HTTP 500"
    call_result = payload["result"]
    assert call_result["isError"] is True
    envelope = json.loads(call_result["content"][0]["text"])
    assert envelope["error"]["kind"] == "internal_error"


# =============================================================================
# 七、命令行开关（默认 stdio；http 能起；stdout 干净）
# =============================================================================


def test_cli_defaults_to_stdio() -> None:
    """**默认仍是 stdio**（T-46 的向后兼容是硬约束）。"""
    options = mcp_server._parse_cli([])
    assert options.transport == "stdio"
    assert options.host is None
    assert options.port is None
    assert options.log_level == "info"


def test_cli_parses_http_transport_switches() -> None:
    """`--transport http --host 0.0.0.0 --port 8080`（容器约定）。

    支持 `--key value` 与 `--key=value` 两种写法（后者是 curl / 脚本里更常见的）。
    """
    options = mcp_server._parse_cli(
        ["--transport", "http", "--host", "0.0.0.0", "--port", "8080", "--log-level", "debug"]
    )
    assert (options.transport, options.host, options.port, options.log_level) == (
        "http",
        "0.0.0.0",
        8080,
        "debug",
    )

    inline = mcp_server._parse_cli(["--transport=http", "--port=0", "--log-level=warning"])
    assert (inline.transport, inline.port, inline.log_level) == ("http", 0, "warning")


def test_cli_rejects_bad_values_and_keeps_unknown_args() -> None:
    """非法值 → `_UsageError`（退出码 2）；不认识的参数只警告、不阻止启动。"""
    for bad in (
        ["--transport", "socks"],
        ["--port", "abc"],
        ["--port", "70000"],
        ["--log-level", "loud"],
        ["--port"],
    ):
        with pytest.raises(mcp_server._UsageError):
            mcp_server._parse_cli(bad)

    tolerant = mcp_server._parse_cli(["--whatever", "1"])
    assert tolerant.transport == "stdio"
    assert tolerant.unknown == ("--whatever", "1")


def test_log_level_filters_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--log-level error` 真的压掉 info，但 `level="error"` 的照写（日志一律 stderr）。"""
    monkeypatch.setattr(mcp_server, "_LOG_LEVEL", mcp_server.LOG_LEVELS["error"])
    monkeypatch.setattr(mcp_server, "_LOG_LEVEL_NAME", "error")
    try:
        mcp_server._log("这条 info 不该出现")
        mcp_server._log("这条 error 该出现", level="error")
    finally:
        monkeypatch.undo()
    captured = capsys.readouterr()
    assert "这条 info 不该出现" not in captured.err
    assert "这条 error 该出现" in captured.err
    assert captured.out == "", "日志绝不能写到 stdout"


def test_http_transport_cli_serves_health_via_loopback() -> None:
    """真起子进程：`--transport http --host 0.0.0.0 --port N`，用 `127.0.0.1` 连 `/health`。

    这一条同时证明：① 命令行开关真的生效（不是只解析了参数）；② 绑 `0.0.0.0`（所有网卡）
    的服务用回环地址连得上 —— 也就是局域网可达；③ **stdout 一个字节都没有**（http 模式
    里 stdout 仍然不许被污染）。
    """
    port = _free_port()
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "noobbaozi.mcp_server",
            "--transport",
            "http",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
            "--log-level",
            "debug",
        ],
        cwd=str(WORKSPACE_ROOT),
        env=client_smoke._server_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 180
        health: dict | None = None
        attempts = 0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            attempts += 1
            try:
                _result, health = _client(f"127.0.0.1:{port}", timeout=5).health()
                break
            except (OSError, http.client.HTTPException, AssertionError, ValueError):
                time.sleep(0.2)
        assert health is not None, (
            f"服务没在 180s 内就绪（pid={process.pid}, rc={process.poll()}, "
            f"轮询 {attempts} 次）"
        )
        client_smoke_http.check_health(health, expected_tools=len(TOOL_NAMES))
        assert health["transport"] == "http"
        assert health["sessions"] == 0, "还没握手，会话数应为 0"
    finally:
        process.kill()
        out, err = process.communicate(timeout=60)
    assert out == b"", f"http 模式下 stdout 必须是空的，实际 {out[:300]!r}"
    assert b"[noobbaozi-mcp]" in err, f"服务日志应写在 stderr，实际 {err[:300]!r}"


def test_help_and_version_stay_on_stderr() -> None:
    """`--help` / `--version` 仍然只写 stderr，且 `--help` 里能看到新开关。"""
    for flag in ("--version", "--help"):
        completed = subprocess.run(
            [sys.executable, "-m", "noobbaozi.mcp_server", flag],
            cwd=str(WORKSPACE_ROOT),
            env=client_smoke._server_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
        assert completed.returncode == 0, completed.stderr
        assert completed.stdout == "", f"{flag} 的 stdout 必须为空，实际 {completed.stdout!r}"
    completed = subprocess.run(
        [sys.executable, "-m", "noobbaozi.mcp_server", "--help"],
        cwd=str(WORKSPACE_ROOT),
        env=client_smoke._server_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )
    for token in ("--transport", "--host", "--port", "--log-level", "stdio", "http"):
        assert token in completed.stderr, f"--help 里应提到 {token}：\n{completed.stderr}"


# =============================================================================
# 八、零第三方依赖 / stdio 不被破坏（结构断言）
# =============================================================================


def test_transport_uses_only_stdlib() -> None:
    """http 传输只 import 标准库 + 本包（**不许**出现 `mcp` SDK 或其它第三方包）。"""
    import ast

    source = (NOOBBAOZI_ROOT / "mcp_http.py").read_text(encoding="utf-8")
    modules: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    third_party = {
        name
        for name in modules
        if name
        not in sys.stdlib_module_names
        and name
        not in {
            "__future__",
            "noobbaozi",
        }
    }
    assert not third_party, f"mcp_http 引入了第三方依赖：{sorted(third_party)}"
