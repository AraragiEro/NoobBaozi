"""`evefit/mcp_server.py` 的契约测试（文档 §10.5「MCP (stdio)」）。

钉住五件事：

1. **工具清单与 schema 形状**：五个名字、`fit` 必填且是 string、`fit_view.view` 是
   六值 `enum`、`fit_get.path` 必填、共同参数带 `default` 且与 `api` 的常量同源；
2. **协议行为**：`initialize` 版本协商（支持列表内原样回、列表外回首选）、
   `capabilities.tools`、通知不回包、未知方法 `-32601`、坏 JSON `-32700`、
   坏请求 `-32600`、参数不合法 `-32602`；
3. **业务错误不是协议错误**：`ok:false` 走 `result` + `isError:true`，
   `structuredContent` 里能读到 `kind` / `candidates` / `hint`；
4. **`structuredContent` 按协商版本开关**：`2025-06-18` 起才有，`2024-11-05` 不该有；
5. **真起子进程**：真客户端连上、五个工具都 `ok:true`（冒烟用例表覆盖 5 个；
   `fit_killmail` 由本文件的进程内用例单独钉住），且 **stdout 里没有一行不是
   JSON-RPC**（stderr 与 stdout 分开读）。

**断言非永真**：`test_smoke_goes_red_when_tool_name_is_wrong` 故意把用例里的工具名
改错，配对断言「改之前 PASS / 改之后 FAIL」—— 改一次就能证明这些断言真的在断言
（PROGRESS 第 180 / 196 条的教训：空转的断言比没有断言更危险）。

绝大多数断言用**进程内 `dispatch()`**（快、能精确看响应体）；只有「`python -m evefit.mcp_server`
这个入口本身」与「stdout 是否被污染」必须起子进程。
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evefit import api, mcp_server
from evefit.mcp import client_smoke

EVEFIT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = EVEFIT_ROOT.parent
EFT_PATH = EVEFIT_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"
KILLMAIL_ZKB_PATH = EVEFIT_ROOT / "tests" / "fixtures" / "killmail" / "139239127-zkb.json"
"""`fit_killmail` 的真实样本（zkillboard 原始响应：79 attackers + zkb 价值块）。"""

SIX_VIEWS = ("firepower", "survivability", "mobility", "resources", "constraints", "stability")

TOOL_NAMES = mcp_server.TOOL_NAMES
"""工具清单**直接取 `mcp_server.TOOL_NAMES`**（单一真源）：本文件不写工具名 / 条数字面量。

加一个工具时这里一行都不用改 —— 断言的是「`tools/list` == 清单」「清单里每个工具都有
title / description / schema」，而不是「一共有几个」。
"""
FIT_TOOLS = tuple(name for name in TOOL_NAMES if name.startswith("fit_"))
"""配装类工具（有 `fit` 入参的那几个）；`zkb_search` 不在其中（它查的是列表）。"""


# =============================================================================
# fixtures / 小工具
# =============================================================================


@pytest.fixture(scope="module", autouse=True)
def _singletons() -> None:
    """进程内调用会用到同一套 `Engine` / `Index` 单例；建不起索引就跳过。"""
    try:
        api.get_engine()
        api.get_index()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"引擎 / 索引不可用：{exc}")


@pytest.fixture(scope="module")
def eft_text() -> str:
    return EFT_PATH.read_text(encoding="utf-8")


def _session(version: str | None = mcp_server.STRUCTURED_CONTENT_SINCE) -> mcp_server._Session:
    """造一个已协商到 `version` 的会话（`None` = 没协商过）。"""
    session = mcp_server._Session()
    session.protocol_version = version
    return session


def _rpc(
    method: str,
    params: object = None,
    *,
    request_id: object = 1,
    version: str | None = mcp_server.STRUCTURED_CONTENT_SINCE,
) -> dict:
    """进程内跑一条 JSON-RPC 请求，返回响应（断言 `id` 已回显）。"""
    message: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        message["id"] = request_id
    if params is not None:
        message["params"] = params
    response = mcp_server.dispatch(message, _session(version))
    assert response is not None, f"{method} 应返回响应"
    assert response["id"] == request_id
    return response


def _call(
    tool: str,
    arguments: dict,
    *,
    version: str | None = mcp_server.STRUCTURED_CONTENT_SINCE,
) -> dict:
    """进程内跑一次 `tools/call`，返回 `result`（`CallToolResult`）。"""
    response = _rpc("tools/call", {"name": tool, "arguments": arguments}, version=version)
    assert "error" not in response, f"{tool} 不该是协议错误：{response.get('error')}"
    return response["result"]


def _envelope(result: dict) -> dict:
    """从 `CallToolResult` 取信封（校验文本就是同一份信封的 JSON）。"""
    content = result["content"]
    assert isinstance(content, list) and content
    assert content[0]["type"] == "text"
    envelope = json.loads(content[0]["text"])
    assert isinstance(envelope, dict)
    return envelope


def _tool_spec(name: str) -> dict:
    for spec in mcp_server._build_tool_specs():
        if spec["name"] == name:
            return spec
    raise AssertionError(f"tools/list 里没有 {name}")


def _run_script(argv: list[str], *, timeout: float = 300.0) -> subprocess.CompletedProcess:
    """子进程跑一个 `evefit.*` 模块（cwd = 仓库根，utf-8，环境与客户端一致）。"""
    return subprocess.run(
        [sys.executable, *argv],
        cwd=str(WORKSPACE_ROOT),
        env=client_smoke._server_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


# =============================================================================
# 一、工具清单与 schema 形状（不起子进程）
# =============================================================================


def test_tools_list_is_the_single_source_of_truth() -> None:
    """`tools/list` **就是 `TOOL_NAMES` 本身**（名字、顺序、条数三者同源）。

    这条是 T-50 的收敛点：以前这里断言「恰好五个名字」，加一个工具就得改测试；
    现在断言「广告出去的就是清单本身」——加工具只改 `mcp_server.TOOL_NAMES` 一处，
    本文件不动。顺带钉住 `fit_killmail` 紧跟在 `fit_parse` 后面（战报先 parse 再拿上下文）。
    """
    response = _rpc("tools/list")
    tools = response["result"]["tools"]
    advertised = [tool["name"] for tool in tools]
    assert advertised == list(TOOL_NAMES), "tools/list 必须就是 TOOL_NAMES 本身（单一真源）"
    assert set(advertised) == set(mcp_server._HANDLERS)
    assert set(advertised) == set(mcp_server._TOOL_DESCRIPTIONS)
    assert mcp_server.TOOL_COUNT == len(TOOL_NAMES) == len(advertised)
    assert mcp_server._TOOL_ORDER is mcp_server.TOOL_NAMES, "旧别名必须是同一个对象，不是第二份清单"
    assert len(advertised) >= 7, f"本轮至少 7 个工具，实际 {advertised}"
    assert advertised.index("fit_killmail") == advertised.index("fit_parse") + 1
    for tool in tools:
        assert tool["title"], f"{tool['name']} 缺 title"
        assert len(tool["description"]) > 40, f"{tool['name']} 的 description 太短"
        assert tool["inputSchema"]["type"] == "object"
        assert tool["annotations"]["readOnlyHint"] is True


def test_every_fit_tool_requires_a_string_fit() -> None:
    """`fit` 是**配装类工具**的共同必填项，且声明成 string + 长度上下限。

    `zkb_search` 不在这条里：它没有 `fit`（查的是列表，不是配装）—— 见
    `test_zkb_search_schema`。
    """
    for name in FIT_TOOLS:
        schema = _tool_spec(name)["inputSchema"]
        assert "fit" in schema["required"], f"{name} 没把 fit 标成必填"
        fit = schema["properties"]["fit"]
        assert fit["type"] == "string"
        assert fit["minLength"] == 1
        assert fit["maxLength"] == mcp_server.MAX_FIT_CHARS
        assert "EFT" in fit["description"] and "DNA" in fit["description"]
        assert "killmail" in fit["description"]


def test_common_parameters_declare_defaults_from_api() -> None:
    """三个共同参数的 `default` / `enum` **与 api 的常量同源**（不抄第二份）。"""
    properties = _tool_spec("fit_index")["inputSchema"]["properties"]
    assert properties["skill_tier"]["default"] == api.SKILL_TIER_ALL4
    assert properties["skill_tier"]["enum"] == list(api.SKILL_TIERS)
    assert properties["damage_profile"]["default"] == api.DAMAGE_PROFILE_UNIFORM
    assert properties["damage_profile"]["enum"] == list(api.DAMAGE_PROFILES)
    assert properties["state_policy"]["default"] == api.STATE_POLICY_ENGINE_DEFAULT
    assert properties["state_policy"]["enum"] == list(api.STATE_POLICIES)
    # 共同参数都不是必填
    assert "skill_tier" not in _tool_spec("fit_index")["inputSchema"]["required"]


def test_fit_view_requires_view_enum_of_six() -> None:
    """`fit_view` 的 `view` 必填，`enum` 就是六个视角名（且**与注册表一致**）。"""
    from evefit.project.views import available_views

    schema = _tool_spec("fit_view")["inputSchema"]
    assert schema["required"] == ["fit", "view"]
    view = schema["properties"]["view"]
    assert view["type"] == "string"
    assert view["enum"] == list(SIX_VIEWS)
    assert view["enum"] == list(available_views()), "enum 必须来自视角注册表，不能抄一份"


def test_fit_get_requires_path() -> None:
    """`fit_get` 的 `path` 必填。"""
    schema = _tool_spec("fit_get")["inputSchema"]
    assert schema["required"] == ["fit", "path"]
    assert schema["properties"]["path"]["type"] == "string"


def test_fit_detail_has_optional_view_and_path() -> None:
    """`fit_detail` 的 `view` / `path` 都可选（不传 = 六个视角全给，口径照 api）。"""
    schema = _tool_spec("fit_detail")["inputSchema"]
    assert schema["required"] == ["fit"]
    assert schema["properties"]["view"]["enum"] == list(SIX_VIEWS)
    assert "path" in schema["properties"]


def test_tools_without_view_do_not_advertise_it() -> None:
    """`fit_parse` / `fit_killmail` / `fit_index` 不该带 `view` 参数（别让 agent 以为能传）。"""
    for name in ("fit_parse", "fit_killmail", "fit_index"):
        properties = _tool_spec(name)["inputSchema"]["properties"]
        assert "view" not in properties
    assert "path" not in _tool_spec("fit_parse")["inputSchema"]["properties"]


# =============================================================================
# 二、协议行为（进程内 dispatch）
# =============================================================================


def test_initialize_echoes_each_supported_version() -> None:
    """`protocolVersion` 在支持列表里 → **原样回它**（协商成功）。"""
    for version in mcp_server.PROTOCOL_VERSIONS:
        session = mcp_server._Session()
        response = mcp_server.dispatch(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": version, "capabilities": {}},
            },
            session,
        )
        assert response["result"]["protocolVersion"] == version
        assert session.protocol_version == version


def test_initialize_unknown_version_falls_back_to_preferred() -> None:
    """不支持的版本 → 回本服务最想用的那个（由客户端决定是否断开）。"""
    response = _rpc(
        "initialize", {"protocolVersion": "1999-01-01", "capabilities": {}}
    )
    assert response["result"]["protocolVersion"] == mcp_server.PREFERRED_PROTOCOL_VERSION


def test_initialize_declares_tools_capability_and_instructions() -> None:
    """`capabilities.tools` 必须在；`serverInfo` 与 `instructions` 也要有。"""
    response = _rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    result = response["result"]
    assert isinstance(result["capabilities"]["tools"], dict)
    assert result["serverInfo"]["name"] == mcp_server.SERVER_NAME
    assert result["serverInfo"]["version"] == mcp_server.SERVER_VERSION
    assert "fit_parse" in result["instructions"]


def test_unknown_method_is_method_not_found() -> None:
    """未知方法 → `-32601`（不是 `-32602`，也不是静默成功）。"""
    response = _rpc("tools/not_a_method")
    assert response["error"]["code"] == mcp_server.METHOD_NOT_FOUND


def test_bad_jsonrpc_version_is_invalid_request() -> None:
    """`jsonrpc` 不是 `"2.0"` → `-32600`。"""
    response = mcp_server.dispatch(
        {"jsonrpc": "1.0", "id": 7, "method": "tools/list"}, _session()
    )
    assert response["error"]["code"] == mcp_server.INVALID_REQUEST


def test_batch_array_is_invalid_request() -> None:
    """顶层数组（JSON-RPC 批量）本服务不支持 → `-32600`，且 id 为 null。"""
    response = mcp_server.dispatch([{"jsonrpc": "2.0", "id": 1, "method": "ping"}], _session())
    assert response["error"]["code"] == mcp_server.INVALID_REQUEST
    assert response["id"] is None


def test_notifications_never_get_a_response() -> None:
    """`notifications/initialized` 是通知：**必须不回包**（回了就是错位的响应）。"""
    session = _session()
    message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    assert mcp_server.dispatch(message, session) is None
    assert session.initialized is True
    # 未知通知同样不回包
    assert (
        mcp_server.dispatch(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {}}, session
        )
        is None
    )


def test_served_loop_emits_only_jsonrpc_lines() -> None:
    """`serve()` 把坏 JSON 收成 `-32700`，**输出仍然逐行都是 JSON-RPC**。"""
    lines = [
        b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
        b"not json at all\n",
        b'{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
        b"\n",
    ]
    out = io.StringIO()
    code = mcp_server.serve(reader=lines, writer=out)
    assert code == 0
    emitted = [line for line in out.getvalue().splitlines() if line.strip()]
    assert len(emitted) == 3, f"应有 3 条响应（ping / -32700 / tools-list），实际 {emitted}"
    parsed = [json.loads(line) for line in emitted]
    assert all(message["jsonrpc"] == "2.0" for message in parsed)
    assert parsed[0]["id"] == 1
    assert parsed[1]["error"]["code"] == mcp_server.PARSE_ERROR
    assert parsed[1]["id"] is None
    assert parsed[2]["id"] == 2


def test_serve_tolerates_utf8_bom() -> None:
    """行首带 UTF-8 BOM 也能用（Windows 工具链常见的坑：BOM 会让第一句话 -32700）。"""
    payload = '{"jsonrpc":"2.0","id":1,"method":"ping"}'.encode("utf-8")
    out = io.StringIO()
    assert mcp_server.serve(reader=[b"\xef\xbb\xbf" + payload], writer=out) == 0
    emitted = [json.loads(line) for line in out.getvalue().splitlines() if line.strip()]
    assert len(emitted) == 1
    assert emitted[0]["id"] == 1 and emitted[0]["result"] == {}


# =============================================================================
# 三、tools/call 的成功路径（进程内）
# =============================================================================


def test_fit_parse_reports_what_it_read(eft_text: str) -> None:
    """`fit_parse`：回显格式 / 船 / 模块件数 / 技能档 / warnings。"""
    envelope = _envelope(_call("fit_parse", {"fit": eft_text}))
    assert envelope["ok"] is True
    result = envelope["result"]
    assert result["source_format"] == "eft"
    assert result["ship"]["name"] == "Hecate"
    assert result["modules"]["fitted"] > 0
    assert result["skills"]["name"] == api.SKILL_TIER_ALL4
    assert isinstance(result["warnings"], list)
    assert envelope["meta"]["spec_version"]


def test_fit_index_is_the_l0_summary(eft_text: str) -> None:
    """`fit_index`：六维度 + flags + budget，`meta` 回显实际技能档。"""
    envelope = _envelope(_call("fit_index", {"fit": eft_text}))
    assert envelope["ok"] is True
    result = envelope["result"]
    for view in SIX_VIEWS:
        assert view in result
    assert isinstance(result["flags"], list)
    assert result["budget"]["chars"] > 0
    assert envelope["meta"]["skill_tier"]["name"] == api.SKILL_TIER_ALL4
    assert envelope["meta"]["damage_profile"] == api.DAMAGE_PROFILE_UNIFORM
    assert envelope["meta"]["state_policy"] == api.STATE_POLICY_ENGINE_DEFAULT


def test_fit_view_returns_l1(eft_text: str) -> None:
    """`fit_view`：L1 五件套齐全，mobility 里有 speed_mps。"""
    envelope = _envelope(_call("fit_view", {"fit": eft_text, "view": "mobility"}))
    assert envelope["ok"] is True
    result = envelope["result"]
    assert "speed_mps" in result["overview"]
    for key in ("provenance", "unknown_reasons", "confidence", "flags"):
        assert key in result


def test_fit_get_returns_value_and_provenance(eft_text: str) -> None:
    """`fit_get`：值 + 三态 + 来源视角（与进程内 `api.fit_get` 逐位相同）。"""
    envelope = _envelope(_call("fit_get", {"fit": eft_text, "path": "mobility.speed_mps"}))
    assert envelope["ok"] is True
    result = envelope["result"]
    expected = api.fit_get(eft_text, "mobility.speed_mps")["result"]
    assert result == expected


def test_fit_detail_gives_full_skill_table(eft_text: str) -> None:
    """`fit_detail`：只看指定视角，完整技能表在 `meta.assumptions.skill_tier`。"""
    envelope = _envelope(_call("fit_detail", {"fit": eft_text, "view": "stability"}))
    assert envelope["ok"] is True
    assert list(envelope["result"]["views"]) == ["stability"]
    tier = envelope["meta"]["assumptions"]["skill_tier"]
    assert len(tier) == 512, f"完整技能表应是 512 条，实际 {len(tier)}"


def test_skill_tier_argument_is_honoured(eft_text: str) -> None:
    """显式传 `skill_tier=all5`：`meta` 要回显 **all5**（不许静默按 all4 算）。"""
    envelope = _envelope(
        _call("fit_index", {"fit": eft_text, "skill_tier": api.SKILL_TIER_ALL5})
    )
    assert envelope["ok"] is True
    assert envelope["meta"]["skill_tier"]["name"] == api.SKILL_TIER_ALL5


def test_fit_as_json_object_reaches_api() -> None:
    """`fit` 传对象（schema 只广告 string，但真实客户端常直接递对象）：不进 `-32602`。"""
    result = _call("fit_index", {"fit": {"ship": {"type_id": 35683}, "items": []}})
    envelope = _envelope(result)
    # 交给 api 判：要么算出来，要么给结构化业务错误 —— 但**不能**是协议错误
    assert envelope["ok"] in (True, False)
    if envelope["ok"] is False:
        assert envelope["error"]["kind"]


# =============================================================================
# 四、错误语义：参数错误 vs 业务错误
# =============================================================================


def test_missing_fit_is_invalid_params() -> None:
    """缺 `fit` → `-32602`，`error.data.field == "fit"`。"""
    response = _rpc("tools/call", {"name": "fit_index", "arguments": {}})
    assert response["error"]["code"] == mcp_server.INVALID_PARAMS
    assert response["error"]["data"]["field"] == "fit"
    assert "fit" in response["error"]["message"]


def test_empty_fit_is_invalid_params() -> None:
    """空串 / 全空白 → `-32602`（不是把空文本塞进引擎）。"""
    for value in ("", "   \n\t "):
        response = _rpc("tools/call", {"name": "fit_parse", "arguments": {"fit": value}})
        assert response["error"]["code"] == mcp_server.INVALID_PARAMS


def test_oversized_fit_is_rejected_before_parsing() -> None:
    """超过 `MAX_FIT_CHARS` → `-32602`，并且**明确说出上限**。"""
    oversized = "x" * (mcp_server.MAX_FIT_CHARS + 1)
    response = _rpc("tools/call", {"name": "fit_index", "arguments": {"fit": oversized}})
    assert response["error"]["code"] == mcp_server.INVALID_PARAMS
    assert str(mcp_server.MAX_FIT_CHARS) in response["error"]["message"]
    assert response["error"]["data"]["hint"]["max_chars"] == mcp_server.MAX_FIT_CHARS


def test_non_string_fit_is_invalid_params() -> None:
    """`fit` 是数字 / 布尔 → `-32602`。"""
    for value in (123, 1.5, True):
        response = _rpc("tools/call", {"name": "fit_parse", "arguments": {"fit": value}})
        assert response["error"]["code"] == mcp_server.INVALID_PARAMS


def test_unknown_tool_is_invalid_params() -> None:
    """未知工具名 → `-32602`（MCP 规范：未知工具属于 invalid params）。"""
    response = _rpc("tools/call", {"name": "fit_idx", "arguments": {"fit": "x"}})
    assert response["error"]["code"] == mcp_server.INVALID_PARAMS
    assert "fit_index" in response["error"]["message"]


def test_invalid_path_is_a_business_error_not_a_protocol_error(eft_text: str) -> None:
    """负例①：路径不存在 → **成功响应** + `isError:true` + `invalid_path` + hint。"""
    result = _call("fit_get", {"fit": eft_text, "path": "mobility.nope"})
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_path"
    assert envelope["error"]["hint"]
    assert result["structuredContent"] == envelope


def test_unknown_module_is_a_business_error_with_candidates() -> None:
    """负例②：模块名拼错 → `unknown_module` + `candidates`（只报候选，不代填）。"""
    result = _call("fit_index", {"fit": client_smoke.TYPO_EFT})
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "unknown_module"
    candidates = envelope["error"]["candidates"]
    assert candidates and all(item["name"] for item in candidates)
    assert "Damage Control II" in [item["name"] for item in candidates]


def test_internal_exception_becomes_internal_error(
    monkeypatch: pytest.MonkeyPatch, eft_text: str
) -> None:
    """服务内部异常 → `isError:true` + `kind=internal_error`，**进程不崩**。"""

    def _boom(_arguments: dict) -> dict:
        raise RuntimeError("故意的内部异常")

    monkeypatch.setitem(mcp_server._HANDLERS, "fit_index", _boom)
    result = _call("fit_index", {"fit": eft_text})
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "internal_error"
    assert "故意的内部异常" in envelope["error"]["detail"]
    # 服务还能继续服务
    monkeypatch.undo()
    assert _envelope(_call("fit_index", {"fit": eft_text}))["ok"] is True


def test_structured_content_is_gated_by_protocol_version(eft_text: str) -> None:
    """`structuredContent` 只在协商版本 ≥ 2025-06-18 时出现。"""
    old = _call("fit_index", {"fit": eft_text}, version="2024-11-05")
    assert "structuredContent" not in old
    assert _envelope(old)["ok"] is True  # 老客户端的文本通道照样完整

    new = _call("fit_index", {"fit": eft_text}, version="2025-06-18")
    assert new["structuredContent"]["ok"] is True

    unknown = _call("fit_index", {"fit": eft_text}, version=None)
    assert "structuredContent" not in unknown, "没协商过版本时按最老客户端处理"


def test_success_does_not_set_is_error(eft_text: str) -> None:
    """成功时**不写** `isError`（保持最小；MCP 里缺省即 false）。"""
    result = _call("fit_get", {"fit": eft_text, "path": "mobility.speed_mps"})
    assert "isError" not in result


# =============================================================================
# 五、真起子进程（集成）
# =============================================================================


def test_stdio_client_round_trip_and_stdout_purity(eft_text: str) -> None:
    """真客户端连上：五个工具都 `ok:true`，且 stdout 每一行都是 JSON-RPC。

    这一条同时钉住三件事：握手 / 清单 / 五个工具真能算出东西；**stdout 没被污染**
    （每一行都能解成带 `jsonrpc` 的对象）；**stderr 是分开的通道**（日志在那里，
    不在这里）。
    """
    records: dict[str, dict] = {}
    with client_smoke.StdioClient() as client:
        handshake = client.request(
            "initialize",
            {
                "protocolVersion": client_smoke.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1"},
            },
        )
        assert handshake["protocolVersion"] == client_smoke.PROTOCOL_VERSION
        client.notify("notifications/initialized")
        listing = client.request("tools/list")
        advertised = [tool["name"] for tool in listing["tools"]]
        assert advertised == list(mcp_server._TOOL_ORDER), "tools/list 必须就是注册表本身"
        assert [name for name in advertised if name in TOOL_NAMES] == list(TOOL_NAMES)

        for tool, arguments in (
            ("fit_parse", {"fit": eft_text}),
            ("fit_index", {"fit": eft_text}),
            ("fit_view", {"fit": eft_text, "view": "mobility"}),
            ("fit_get", {"fit": eft_text, "path": "mobility.speed_mps"}),
            ("fit_detail", {"fit": eft_text, "view": "stability"}),
        ):
            result = client.request("tools/call", {"name": tool, "arguments": arguments})
            envelope = _envelope(result)
            assert envelope["ok"] is True, f"{tool} 应 ok:true，实际 {envelope.get('error')}"
            assert "isError" not in result, f"{tool} 成功时不该带 isError"
            records[tool] = envelope

        raw_lines = list(client._raw_lines)
        stderr_text = client.stderr_text

    # stdout：每一行都是 JSON-RPC 2.0（**没有非 JSON-RPC 的行**）
    assert len(raw_lines) >= 7
    for line in raw_lines:
        message = json.loads(line)
        assert message["jsonrpc"] == "2.0", f"stdout 出现了非 JSON-RPC 行：{line[:200]!r}"

    # stderr：是**另一个**通道，服务的日志在这里（不在 stdout 里）
    assert stderr_text, "服务应把启动日志写到 stderr"
    assert any("[evefit-mcp]" in line for line in stderr_text.splitlines())
    assert all("[evefit-mcp]" not in line for line in raw_lines)

    # 五个工具确实各自算出了东西（不是同一个信封被复用）
    assert records["fit_parse"]["result"]["ship"]["name"] == "Hecate"
    assert records["fit_index"]["result"]["mobility"]["speed_mps"] > 0
    assert "speed_mps" in records["fit_view"]["result"]["overview"]
    assert records["fit_get"]["result"]["path"] == "mobility.speed_mps"
    assert list(records["fit_detail"]["result"]["views"]) == ["stability"]


def test_smoke_script_exits_zero() -> None:
    """`python -m evefit.mcp.client_smoke --stdio` 真的 PASS 并退出 `0`。"""
    completed = _run_script(["-m", "evefit.mcp.client_smoke", "--stdio"])
    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    assert "PASS  7/7" in completed.stdout
    for tool in TOOL_NAMES:
        assert tool in completed.stdout
    assert "FAIL" not in completed.stdout


def test_smoke_goes_red_when_tool_name_is_wrong() -> None:
    """**断言非永真**：故意把用例里的工具名改错 → 冒烟必须变红。

    配对断言（改之前 PASS / 改之后 FAIL）是这条测试的全部价值：它证明
    `test_smoke_script_exits_zero` 的绿灯不是空转得来的（PROGRESS 第 180 条）。
    """
    cases = client_smoke.build_cases()[:2]  # parse + index，够快
    assert client_smoke.run(cases=cases, transport="stdio", echo=False) is True

    broken = [dict(case) for case in cases]
    broken[0]["tool"] = "fit_parse_typo"
    assert client_smoke.run(cases=broken, transport="stdio", echo=False) is False


def test_server_version_flag_writes_only_to_stderr() -> None:
    """`--version` 只写 stderr：stdout 一个字节都没有（协议通道不许被误用）。"""
    completed = _run_script(["-m", "evefit.mcp_server", "--version"])
    assert completed.returncode == 0
    assert completed.stdout == "", f"stdout 必须是空的，实际 {completed.stdout!r}"
    assert mcp_server.SERVER_NAME in completed.stderr


def test_server_survives_garbage_input() -> None:
    """往服务嘴里灌垃圾：不崩、退出码 0、回的都是 JSON-RPC 错误。"""
    completed = subprocess.run(
        [sys.executable, "-m", "evefit.mcp_server"],
        cwd=str(WORKSPACE_ROOT),
        env=client_smoke._server_env(),
        input='not json\n{"jsonrpc":"2.0","id":1,"method":"nope"}\n[]\n{"id":2}\n',
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
    )
    assert completed.returncode == 0, completed.stderr
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 4, f"四行输入应得到四条响应，实际 {lines}"
    codes = [json.loads(line)["error"]["code"] for line in lines]
    assert codes == [
        mcp_server.PARSE_ERROR,
        mcp_server.METHOD_NOT_FOUND,
        mcp_server.INVALID_REQUEST,
        mcp_server.INVALID_REQUEST,
    ]


# =============================================================================
# 六、`fit_killmail`（T-49：战报上下文）
# =============================================================================


def _offline_system(monkeypatch: pytest.MonkeyPatch) -> None:
    """星系查询打桩（真实 ESI 快照：30002537 = Amamake，lowsec）+ 清进程级缓存。"""
    monkeypatch.setattr(api, "universe_names", lambda ids: {30002537: "Amamake"})
    monkeypatch.setattr(
        api,
        "universe_system",
        lambda system_id: {
            "name": "Amamake",
            "security_status": 0.43876123428344727,
            "security_class": "E",
            "has_class": True,
        },
    )
    api.clear_caches()


def test_fit_killmail_schema_and_wording() -> None:
    """`fit_killmail` 只有 `fit` + 三个共同参数；title / description 写明「别开网页」。"""
    spec = _tool_spec("fit_killmail")
    assert "战报上下文" in spec["title"]
    assert "谁打的" in spec["title"]
    assert "zkillboard 网页" in spec["description"], "description 必须禁止 agent 自己开网页"
    assert "fit_parse" in spec["description"] and "fit_killmail" in spec["description"]
    schema = spec["inputSchema"]
    assert schema["required"] == ["fit"]
    assert set(schema["properties"]) == {
        "fit",
        "skill_tier",
        "damage_profile",
        "state_policy",
    }


def test_fit_killmail_returns_battle_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tools/call fit_killmail` → 战报上下文（人数 / 价值 / 伤害校验 / 比率）齐活。"""
    zkb_text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    _offline_system(monkeypatch)

    call_result = _call("fit_killmail", {"fit": zkb_text})
    envelope = _envelope(call_result)
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert result["killmail"]["killmail_id"] == 139239127
    assert result["killmail"]["solar_system"] == {"id": 30002537, "name": "Amamake"}
    assert result["killmail"]["value"]["total"] == 76942042803.52
    assert result["attackers"]["count"] == 79
    assert result["attackers"]["damage_check"] == "matches_victim"
    assert result["context"]["damage_taken_vs_ehp"]["ratio"] is not None
    # structuredContent 就是同一个信封；成功时不该有 isError
    assert call_result.get("structuredContent") == envelope
    assert "isError" not in call_result


def test_fit_killmail_on_a_fit_is_a_business_error() -> None:
    """给 EFT → **业务错误**（`isError:true` + `not_a_killmail`），不是协议错误。"""
    eft = EFT_PATH.read_text(encoding="utf-8")
    call_result = _call("fit_killmail", {"fit": eft})
    envelope = _envelope(call_result)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "not_a_killmail"
    assert envelope["error"]["hint"]
    assert call_result["isError"] is True


# =============================================================================
# 七、`zkb_search`（T-50：战报列表检索）
# =============================================================================

ZKB_FIXTURES = EVEFIT_ROOT / "tests" / "fixtures" / "zkb"
"""离线样本（**真实响应**：`losses/shipTypeID/22444/pastSeconds/604800` 与
ESI `/universe/ids/`，裁剪说明见 `tests/fixtures/zkb/README.md`）。"""


def _zkb_fixture(name: str) -> object:
    return json.loads((ZKB_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def zkb_offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把**生产请求器**换成 fixture（不联网），并清掉限流时间戳（否则要真等 1.2 s）。

    换的是 `api._zkb_default_requester` 而不是 `api.zkb_search`：这样 MCP → api →
    请求器整条链路都真的跑了一遍，只有「最后那一跳」是假的。
    """
    losses = _zkb_fixture("losses-shipTypeID-22444-pastSeconds-604800.json")
    ids = _zkb_fixture("esi-ids-sleipnir-hecate.json")
    seen: list[str] = []

    def fake(url: str, body: object | None = None) -> object:
        seen.append(url)
        return ids if body is not None else losses

    api._reset_zkb_state()
    monkeypatch.setattr(api, "_zkb_default_requester", fake)
    yield seen
    api._reset_zkb_state()


def test_zkb_search_schema_has_no_fit() -> None:
    """`zkb_search` 的 schema：**没有 `fit`**、没有必填项、`side` enum 与 api 同源。"""
    spec = _tool_spec("zkb_search")
    assert "战报列表" in spec["title"]
    for token in ("past_seconds", "startTime", "fit_killmail", "AND"):
        assert token in spec["description"], f"description 必须写清 {token}"
    schema = spec["inputSchema"]
    assert schema["required"] == []
    assert "fit" not in schema["properties"], "zkb_search 不是配装工具，不该要求 fit"
    assert set(schema["properties"]) == {
        "character",
        "corporation",
        "alliance",
        "ship",
        "solar_system",
        "region",
        "past_seconds",
        "page",
        "side",
        "limit",
    }
    assert schema["properties"]["side"]["enum"] == list(api.ZKB_SIDES)
    assert schema["properties"]["side"]["default"] == api.ZKB_SIDE_BOTH
    assert schema["properties"]["limit"]["maximum"] == api.ZKB_PAGE_SIZE
    assert schema["properties"]["limit"]["default"] == api.ZKB_LIMIT_DEFAULT
    assert "pastSeconds" not in schema["properties"], "参数名是 past_seconds，URL 段才是 pastSeconds"


def test_zkb_search_call_round_trip(zkb_offline: list[str]) -> None:
    """`tools/call zkb_search` → 列表 + 聚合；**URL 用 pastSeconds**，不是起止时间。"""
    call_result = _call(
        "zkb_search",
        {"ship": "Sleipnir", "past_seconds": 604800, "side": "losses"},
    )
    envelope = _envelope(call_result)
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert result["count"] == 12
    assert result["query"]["resolved"]["ship"] == {"id": 22444, "name": "Sleipnir"}
    assert zkb_offline == [
        api.ESI_UNIVERSE_IDS_API,
        "https://zkillboard.com/api/losses/shipTypeID/22444/pastSeconds/604800/",
    ], f"URL 拼错：{zkb_offline}"
    assert not any("startTime" in url or "endTime" in url for url in zkb_offline)
    first = result["kills"][0]
    assert first["killmail_id"] == 139265742
    assert first["ship"]["name"] == "Sleipnir"
    assert first["value"] == 397729064.05
    assert "要明细请对具体 killmail_id 调 fit_killmail" in result["note"]
    assert call_result.get("structuredContent") == envelope
    assert "isError" not in call_result


def test_zkb_search_time_range_is_rejected_as_business_error(
    zkb_offline: list[str],
) -> None:
    """传 `startTime` → **业务错误**（`invalid_argument`），且**一次请求都没发**。"""
    call_result = _call(
        "zkb_search", {"ship": 22444, "startTime": 20261010, "endTime": 20261012}
    )
    envelope = _envelope(call_result)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_argument"
    assert "past_seconds" in envelope["error"]["hint"]["use"]
    assert call_result["isError"] is True
    assert zkb_offline == [], f"被拒绝的查询不该出网，实际发了 {zkb_offline}"


def test_zkb_search_without_filters_is_invalid_argument(zkb_offline: list[str]) -> None:
    """一个过滤条件都不给 → `invalid_argument`（裸的 /api/kills/ 不是一次检索）。"""
    envelope = _envelope(_call("zkb_search", {}))
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["hint"]["accepted"]
    assert zkb_offline == []
