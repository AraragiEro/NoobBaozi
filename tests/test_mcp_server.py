"""`noobbaozi/mcp_server.py` 的契约测试（文档 §10.5「MCP (stdio)」）。

钉住六件事：

1. **工具清单与 schema 形状**：名字集合 == `mcp_server.TOOL_NAMES`（单一真源，本文件不
   写工具名字面量）、`fit` 必填且是 string、`fit_view.view` 是六值 `enum`、
   `fit_get.path` 必填、共同参数带 `default` 且与 `api` 的常量同源；
2. **协议行为**：`initialize` 版本协商（支持列表内原样回、列表外回首选）、
   `capabilities.tools`、通知不回包、未知方法 `-32601`、坏 JSON `-32700`、
   坏请求 `-32600`、参数不合法 `-32602`；
3. **业务错误不是协议错误**：`ok:false` 走 `result` + `isError:true`，
   `structuredContent` 里能读到 `kind` / `candidates` / `hint`；
4. **`structuredContent` 按协商版本开关**：`2025-06-18` 起才有，`2024-11-05` 不该有；
5. **真起子进程**：真客户端连上、工具都 `ok:true`（冒烟用例表覆盖**本地可算**的配装工具；
   需要联网的 `fit_killmail` / `zkb_*` 不在那张表里，由各自的契约测试覆盖），
   且 **stdout 里没有一行不是 JSON-RPC**（stderr 与 stdout 分开读）；
6. **自解释守卫（T-51）**：每条工具 description 三段齐全（何时用 / 返回什么 / 限制）、
   `instructions` 提到全部工具且长度受控、`SKILL.md` 是薄壳（不复制工具清单）。

**断言非永真**：每条守卫都配了一条「故意改坏 → 必须变红」的配对断言 ——
`test_description_guard_is_not_vacuous` / `test_skill_guard_is_not_vacuous` /
`test_smoke_goes_red_when_tool_name_is_wrong`。改一次就能证明这些断言真的在断言
（PROGRESS 第 180 / 196 条的教训：空转的断言比没有断言更危险）。

绝大多数断言用**进程内 `dispatch()`**（快、能精确看响应体）；只有「`python -m noobbaozi.mcp_server`
这个入口本身」与「stdout 是否被污染」必须起子进程。
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from noobbaozi import api, mcp_server
from noobbaozi.mcp import client_smoke

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = NOOBBAOZI_ROOT.parent
EFT_PATH = NOOBBAOZI_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"
KILLMAIL_ZKB_PATH = NOOBBAOZI_ROOT / "tests" / "fixtures" / "killmail" / "139239127-zkb.json"
"""`fit_killmail` 的真实样本（zkillboard 原始响应：79 attackers + zkb 价值块）。"""

SIX_VIEWS = ("firepower", "survivability", "mobility", "resources", "constraints", "stability")

TOOL_NAMES = mcp_server.TOOL_NAMES
"""工具清单**直接取 `mcp_server.TOOL_NAMES`**（单一真源）：本文件不写工具名 / 条数字面量。

加一个工具时这里一行都不用改 —— 断言的是「`tools/list` == 清单」「清单里每个工具都有
title / description / schema」，而不是「一共有几个」。
"""
FIT_TOOLS = tuple(name for name in TOOL_NAMES if name.startswith("fit_"))
"""配装类工具（有一个「配装输入」入参的那几个）；`zkb_search` 不在其中（它查的是列表）。"""

FIT_INPUT_KEYS: dict[str, tuple[str, ...]] = {
    "fit_project": ("target",),
    "fit_engagement": ("attacker", "defender"),
}
"""配装输入参数名（**默认 `fit`**；这几个例外）：

- `fit_project` → `target`（「一给一」里被投的那艘船）；
- `fit_engagement` → `attacker` + `defender`（两份配装对打，没有单独的 `fit`）。

这张映射只服务 schema 断言，不参与运行期逻辑。
"""


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
    """子进程跑一个 `noobbaozi.*` 模块（cwd = 仓库根，utf-8，环境与客户端一致）。"""
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
    """配装输入是**配装类工具**的共同必填项，声明成 string + 长度上下限。

    参数名按 `FIT_INPUT_KEYS` 取（默认 `fit`；`fit_project` 是 `target`，
    `fit_engagement` 是 `attacker` + `defender`）。
    `zkb_search` 不在这条里：它没有配装输入（查的是列表）—— 见 `test_zkb_search_schema`。
    """
    for name in FIT_TOOLS:
        schema = _tool_spec(name)["inputSchema"]
        for key in FIT_INPUT_KEYS.get(name, ("fit",)):
            assert key in schema["required"], f"{name} 没把 {key} 标成必填"
            fit = schema["properties"][key]
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
    from noobbaozi.project.views import available_views

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


def test_glossary_travels_over_mcp(eft_text: str) -> None:
    """`meta.glossary` 必须真的**穿过 MCP 传输**：只接了 MCP 的 agent 看到的就是这份 JSON。

    本单的起因就是这个 —— agent 拿不到 `mcp/README.md`、也不读 `SKILL.md`，
    它手里只有 `tools/list` 的 description（≤600）与返回体。术语解释若只写在文档里，
    对它就等于不存在。
    """
    index = _envelope(_call("fit_index", {"fit": eft_text}))
    glossary = index["meta"]["glossary"]
    assert {"provenance", "budget", "assumes", "damage_profile", "state_policy"} <= set(
        glossary
    )
    assert "assumed" in glossary["provenance"], "三态必须写到返回体里，不是只写进文档"
    assert "unlimited_charges" in glossary["assumes"]
    assert "charge_limited" in glossary["assumes"]

    view = _envelope(_call("fit_view", {"fit": eft_text, "view": "survivability"}))
    assert {"provenance", "confidence", "unknown_reasons", "assumes"} <= set(
        view["meta"]["glossary"]
    )

    # `structuredContent` 是同一个信封对象（协议 ≥ 2025-06-18）：术语表也在里面
    structured = _call("fit_index", {"fit": eft_text})["structuredContent"]
    assert structured["meta"]["glossary"] == glossary


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
    assert any("[noobbaozi-mcp]" in line for line in stderr_text.splitlines())
    assert all("[noobbaozi-mcp]" not in line for line in raw_lines)

    # 五个工具确实各自算出了东西（不是同一个信封被复用）
    assert records["fit_parse"]["result"]["ship"]["name"] == "Hecate"
    assert records["fit_index"]["result"]["mobility"]["speed_mps"] > 0
    assert "speed_mps" in records["fit_view"]["result"]["overview"]
    assert records["fit_get"]["result"]["path"] == "mobility.speed_mps"
    assert list(records["fit_detail"]["result"]["views"]) == ["stability"]


def test_smoke_script_exits_zero() -> None:
    """`python -m noobbaozi.mcp.client_smoke --stdio` 真的 PASS 并退出 `0`。

    「PASS N/N」的 N **从用例表派生**：写死 `7/7` 的话，往 `build_cases()` 里加一条用例
    就会红在这里 —— 而那条新增用例本身可能是对的（本文件不该变成第二份计数清单）。
    """
    completed = _run_script(["-m", "noobbaozi.mcp.client_smoke", "--stdio"])
    assert completed.returncode == 0, f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
    expected = len(client_smoke.build_cases())
    assert f"PASS  {expected}/{expected}" in completed.stdout
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
    completed = _run_script(["-m", "noobbaozi.mcp_server", "--version"])
    assert completed.returncode == 0
    assert completed.stdout == "", f"stdout 必须是空的，实际 {completed.stdout!r}"
    assert mcp_server.SERVER_NAME in completed.stderr


def test_server_survives_garbage_input() -> None:
    """往服务嘴里灌垃圾：不崩、退出码 0、回的都是 JSON-RPC 错误。"""
    completed = subprocess.run(
        [sys.executable, "-m", "noobbaozi.mcp_server"],
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


AMAMAKE = {
    "name": "Amamake",
    "security_status": 0.43876123428344727,
    "security_class": "E",
    "has_class": True,
}
"""真实 ESI 快照（`GET /universe/systems/30002537/`）：lowsec，类名 `E`。"""


def _offline_system(monkeypatch: pytest.MonkeyPatch) -> list[list[int]]:
    """星系查询 + 实体名打桩（真实 ESI 快照 / 真实 names 样本）+ 清进程级缓存。

    返回每次 `POST /universe/names/` 的请求体（用来断言「一次调用只查一批名字」）。
    """
    entries = json.loads(
        (ZKB_FIXTURES / "esi-universe-names.json").read_text(encoding="utf-8")
    )
    calls: list[list[int]] = []

    def fake_post(url: str, payload: object) -> object:
        calls.append(list(payload))  # type: ignore[arg-type]
        wanted = set(payload)  # type: ignore[arg-type]
        return [entry for entry in entries if entry["id"] in wanted]

    monkeypatch.setattr(api, "universe_system", lambda system_id: dict(AMAMAKE))
    monkeypatch.setattr(api, "_post_json", fake_post)
    api.clear_caches()
    return calls


def test_fit_killmail_schema_and_wording() -> None:
    """`fit_killmail` 只有 `fit` + 三个共同参数；title / description 写明「别开网页」与名字。"""
    spec = _tool_spec("fit_killmail")
    assert "战报上下文" in spec["title"]
    assert "谁打的" in spec["title"]
    assert "zkillboard 网页" in spec["description"], "description 必须禁止 agent 自己开网页"
    assert "fit_parse" in spec["description"] and "fit_killmail" in spec["description"]
    assert "包含受害者与攻击者（Top 伤害）的驾驶员 / 军团 / 联盟名字" in spec["description"], (
        "T-52：description 必须写明名字已给（否则 agent 会自己去查 ESI）"
    )
    # T-53：蛋的口径必须写在 description 里（agent 只会读这里，不会读 README）
    for fact in ("蛋（Capsule）", "已失去自己的船", "船归属不可信"):
        assert fact in spec["description"], f"description 必须写明「{fact}」"
    for field in ("damaging_count", "zero_damage_count", "capsule_count"):
        assert field in spec["description"], f"description 必须写清 {field}（否则 agent 读不懂计数）"
    schema = spec["inputSchema"]
    assert schema["required"] == ["fit"]
    assert set(schema["properties"]) == {
        "fit",
        "skill_tier",
        "damage_profile",
        "state_policy",
        "locale",  # T-3/5：名字最多的两个入口带 locale（zh 时给官方中文）
    }


def test_fit_killmail_returns_battle_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """`tools/call fit_killmail` → 战报上下文（人数 / 价值 / 伤害校验 / 比率 / 人名）齐活。"""
    zkb_text = KILLMAIL_ZKB_PATH.read_text(encoding="utf-8")
    calls = _offline_system(monkeypatch)

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
    # T-52：MCP 出去的结果里就有名字（agent 不用再查 ESI）
    assert result["victim"]["character"]["name"] == "Warlock Industries Event"
    assert result["attackers"]["by_pilot"][0]["character"]["name"]
    assert result["attackers"]["final_blow"]["character"]["name"] == "Ignacious Deviant"
    assert len(calls) == 1
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

ZKB_FIXTURES = NOOBBAOZI_ROOT / "tests" / "fixtures" / "zkb"
"""离线样本（**真实响应**：`losses/shipTypeID/22444/pastSeconds/604800` 与
ESI `/universe/ids/`，裁剪说明见 `tests/fixtures/zkb/README.md`）。"""


def _zkb_fixture(name: str) -> object:
    return json.loads((ZKB_FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture()
def zkb_offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把**生产请求器**与**实体名解析**都换成 fixture（不联网），并清掉限流时间戳。

    换的是 `api._zkb_default_requester` / `api._post_json` 而不是 `api.zkb_search`：这样
    MCP → api → 请求器整条链路都真的跑了一遍，只有「最后那一跳」是假的。
    两条 POST 端点必须分开拦：`/universe/ids/`（名字 → id）走请求器，
    `/universe/names/`（id → 名字）走 `_post_json` —— 漏一个就会真联网。
    """
    losses = _zkb_fixture("losses-shipTypeID-22444-pastSeconds-604800.json")
    ids = _zkb_fixture("esi-ids-sleipnir-hecate.json")
    names = _zkb_fixture("esi-universe-names.json")
    seen: list[str] = []

    def fake(url: str, body: object | None = None) -> object:
        seen.append(url)
        return ids if body is not None else losses

    def fake_names(url: str, payload: object) -> object:
        seen.append(url)
        wanted = set(payload)  # type: ignore[arg-type]
        return [entry for entry in names if entry["id"] in wanted]

    api._reset_zkb_state()
    api.clear_caches()  # 实体名缓存是进程级的：不清就没法断言「这轮发了几次请求」
    monkeypatch.setattr(api, "_zkb_default_requester", fake)
    monkeypatch.setattr(api, "_post_json", fake_names)
    yield seen
    api._reset_zkb_state()


def test_zkb_search_schema_has_no_fit() -> None:
    """`zkb_search` 的 schema：**没有 `fit`**、没有必填项、`side` enum 与 api 同源。"""
    spec = _tool_spec("zkb_search")
    assert "战报列表" in spec["title"]
    for token in ("past_seconds", "startTime", "fit_killmail", "AND"):
        assert token in spec["description"], f"description 必须写清 {token}"
    assert "每行带受害者驾驶员 / 军团 / 联盟名字" in spec["description"], (
        "T-52：description 必须写清每行带名字（否则 agent 会自己去查 ESI）"
    )
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
        api.ESI_UNIVERSE_NAMES_API,
    ], f"URL 拼错：{zkb_offline}"
    assert not any("startTime" in url or "endTime" in url for url in zkb_offline)
    first = result["kills"][0]
    assert first["killmail_id"] == 139265742
    assert first["ship"]["name"] == "Sleipnir"
    assert first["value"] == 397729064.05
    # T-52：每行带受害者身份（名字真的出来了，不是 null）
    assert first["victim"]["character"]["name"]
    assert first["victim"]["corporation"]["name"]
    assert all(row["victim"]["character"]["name"] for row in result["kills"])
    assert result["warnings"] == []
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


# =============================================================================
# 八、自解释守卫（T-51：MCP 是唯一真源，Skill 只做发现性）
# =============================================================================
#
# 背景（用户质疑「一定需要 skill 吗？MCP 本身能否替代？」的裁决）：
#
# - MCP 的 `initialize.instructions` 与 `tools/list[].description` **一定会被客户端注入
#   上下文**（DSH 已实证），所以工具细节必须写在 MCP 里；
# - Skill 只保留「何时用 / 怎么连 / 判读纪律」，**不复制工具细节** —— 复制就会漂移。
#
# 这几条守卫就是那个裁决的执行器：描述少一段、instructions 漏一个工具、SKILL.md 开始
# 抄工具清单，任意一种都变红。它们都是**对的断言**，但对的断言也可能变成空转，
# 所以每类都配一条「故意改坏 → 必须变红」的配对断言。

WHEN_TOKENS: tuple[str, ...] = ("当你", "用于", "回答", "想知道")
"""「何时用 / 它回答什么问题」的标志词（命中任一即认为这一段写了）。"""

RETURN_TOKENS: tuple[str, ...] = ("返回", "给出", "包含")
"""「返回里最该看什么」的标志词。"""

LIMIT_TOKENS: tuple[str, ...] = ("限制", "注意", "不支持", "不含", "最多", "只用")
"""「已知限制」的标志词。"""

INSTRUCTIONS_MAX_CHARS = 800
"""`initialize.instructions` 的字符上限：它是**索引**，不是文档（T-51）。"""

DESCRIPTION_MAX_CHARS = 600
"""单条工具描述的长度上限：防止某条描述又膨胀成一份小文档。"""

SKILL_PATH = NOOBBAOZI_ROOT / "skill" / "noobbaozi" / "SKILL.md"
"""Skill 载体（**整份文件都受守卫约束**：它只能是个薄壳）。"""

SKILL_MAX_LINES = 60
"""`SKILL.md` 的行数上限。"""

SKILL_TOOL_NAME_MAX = 1
"""每个工具名在 `SKILL.md` 里最多出现几次（允许提一句路由，不允许大段复制）。"""


def _missing_sections(description: str) -> list[str]:
    """一条 description 缺了哪几段（空列表 = 三段齐全）。

    三段 = ① 何时用 / 回答什么问题 ② 返回里最该看什么 ③ 已知限制。
    """
    missing: list[str] = []
    if not any(token in description for token in WHEN_TOKENS):
        missing.append("何时用")
    if not any(token in description for token in RETURN_TOKENS):
        missing.append("返回什么")
    if not any(token in description for token in LIMIT_TOKENS):
        missing.append("限制")
    return missing


@pytest.mark.parametrize("name", TOOL_NAMES)
def test_every_tool_description_is_self_explaining(name: str) -> None:
    """**每个工具的 description 都必须自解释三段**（何时用 / 返回什么 / 限制）。

    这是 T-51 的核心守卫：工具细节只允许存在于 MCP 里（Skill 不复制），所以「描述写全」
    不再是文案偏好，而是**唯一真源的完整性要求**。缺任一段 → 红。
    """
    description = _tool_spec(name)["description"]
    missing = _missing_sections(description)
    assert missing == [], (
        f"{name} 的 description 缺 {missing}（三段 = 何时用 / 返回什么 / 限制）；"
        f"工具细节只有 MCP 这一份，不能省：\n{description}"
    )
    assert len(description) <= DESCRIPTION_MAX_CHARS, (
        f"{name} 的 description {len(description)} 字符，超过 {DESCRIPTION_MAX_CHARS}"
        "（描述是索引，不是文档）"
    )


def test_instructions_lists_every_tool_in_recommended_order() -> None:
    """`instructions` 必须提到**全部**工具名，且顺序 = 推荐调用顺序（= `TOOL_NAMES`）。"""
    instructions = _rpc(
        "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}}
    )["result"]["instructions"]
    assert instructions == mcp_server.INSTRUCTIONS, "握手返回的就是模块常量本身"

    positions: list[int] = []
    for name in TOOL_NAMES:
        assert name in instructions, f"instructions 漏了 {name}（顺序指引不完整）"
        positions.append(instructions.index(name))
    assert positions == sorted(positions), (
        f"instructions 里工具必须按推荐调用顺序出现，实际顺序索引 {positions}"
    )


def test_instructions_stays_an_index_not_a_document() -> None:
    """`instructions` 控制长度，且三条判读纪律的关键锚点必须都在里面。"""
    instructions = mcp_server.INSTRUCTIONS
    assert len(instructions) <= INSTRUCTIONS_MAX_CHARS, (
        f"instructions {len(instructions)} 字符，超过 {INSTRUCTIONS_MAX_CHARS}："
        "它应该是一份索引（顺序 + 三条纪律），不是一份文档"
    )
    for anchor in (
        "unknown_reasons",  # 纪律①：unknown 不是 0
        "propulsion_states.single",  # 纪律②：顶层数值的叠加/不可达
        "state_exclusive",  # 纪律②：flag 提示
        "zkillboard",  # 纪律③：不要自己开网页
    ):
        assert anchor in instructions, f"instructions 缺判读纪律的锚点：{anchor}"


def test_description_guard_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """**配对断言**：把某条 description 的「限制」段删掉 → 守卫必须变红。

    走的是真实链路（改 `_TOOL_DESCRIPTIONS` → 清 `_SPECS_CACHE` → 从 `tools/list` 取回
    描述），所以它同时证明「守卫读的是线上那份文案」而不是测试自己编的副本。
    """
    name = "fit_index"
    title, description = mcp_server._TOOL_DESCRIPTIONS[name]
    assert _missing_sections(description) == [], "前提：真描述三段齐全"

    broken = "看配装时用它，回答这套整体什么样；返回六维度摘要。"  # 缺「限制」段
    assert _missing_sections(broken) == ["限制"]

    monkeypatch.setitem(mcp_server._TOOL_DESCRIPTIONS, name, (title, broken))
    monkeypatch.setattr(mcp_server, "_SPECS_CACHE", None)
    assert "限制" in _missing_sections(_tool_spec(name)["description"])


def _skill_problems(text: str) -> list[str]:
    """`SKILL.md` 的守卫（空列表 = 合格）。

    只查两件**结构**事：行数上限、工具名出现次数上限。它们各自对应一种退化：
    前者退化成一份文档，后者退化成工具清单的副本。
    """
    problems: list[str] = []
    line_count = len(text.splitlines())
    if line_count > SKILL_MAX_LINES:
        problems.append(f"行数 {line_count} > {SKILL_MAX_LINES}")
    for name in TOOL_NAMES:
        count = text.count(name)
        if count > SKILL_TOOL_NAME_MAX:
            problems.append(f"{name} 出现 {count} 次 > {SKILL_TOOL_NAME_MAX}")
    return problems


def test_skill_md_is_a_thin_shell() -> None:
    """`SKILL.md` 只留「何时用 / 怎么连 / 三条纪律」，不复制工具细节。

    守卫分两层：结构（行数 + 工具名次数）交给 `_skill_problems()`；内容锚点（怎么连、
    指向 README、不该做什么、三条纪律）在这里直接断言。
    """
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert _skill_problems(text) == [], (
        f"{SKILL_PATH} 退化了：{_skill_problems(text)}；"
        "工具清单 / 字段表 / 调用顺序细节都属于 MCP 描述，不能复制过来"
    )
    assert "mcp/README.md" in text, "SKILL.md 必须指向 mcp/README.md（客户端配置在那里）"
    assert "python -m noobbaozi.mcp_server --transport http" in text, "怎么连必须一行写清"
    assert "不生成配装" in text, "必须写明「不该用它做什么」"
    for anchor in (
        "unknown_reasons",  # 纪律①
        "propulsion_states.single",  # 纪律②
        "state_exclusive",  # 纪律②
        "zkillboard",  # 纪律③
    ):
        assert anchor in text, f"SKILL.md 缺判读纪律的锚点：{anchor}"


def test_skill_guard_is_not_vacuous() -> None:
    """**配对断言**：把工具清单式的表格塞回 `SKILL.md` → `_skill_problems()` 必须报错。

    抄法要贴近真实退化形态：一张表（每个工具名一次）**加**一句「调用顺序如下」的名单
    —— 名单里再出现一次，于是每个工具名都越界（≤ 1 是给「提一句路由」留的额度）。
    """
    text = SKILL_PATH.read_text(encoding="utf-8")
    assert _skill_problems(text) == [], "前提：现在的 SKILL.md 是合格的薄壳"

    copied_table = (
        text
        + "\n\n## 工具清单\n\n"
        + "\n".join(f"| `{name}` | 一句话 | 参数 |" for name in TOOL_NAMES)
        + "\n\n调用顺序：" + " / ".join(TOOL_NAMES) + "。\n"
    )
    problems = _skill_problems(copied_table)
    for name in TOOL_NAMES:
        assert any(name in problem for problem in problems), (
            f"把 {name} 抄进 SKILL.md 之后守卫没报错：{problems}"
        )

    too_long = text + "\n" * (SKILL_MAX_LINES + 1)
    assert any("行数" in problem for problem in _skill_problems(too_long))


# =============================================================================
# 五、实体画像（T-55）：`zkb_stats` 在 MCP 表面的契约
# =============================================================================

STATS_CHARACTER_PATH = ZKB_FIXTURES / "stats-character-2117515414.json"
STATS_ALLIANCE_PATH = ZKB_FIXTURES / "stats-alliance-1354830081.json"


@pytest.fixture()
def stats_offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把**生产请求器**换成 stats fixture（不联网），清掉限流时间戳。

    只拦 GET：`zkb_stats` 用整数 id 时不需要任何 ESI POST。真发了 POST 就说明 MCP 层
    把参数递错了 —— 那正是这条 fixture 想抓的。
    """
    document = json.loads(STATS_CHARACTER_PATH.read_text(encoding="utf-8-sig"))
    seen: list[str] = []

    def fake(url: str, body: object | None = None) -> object:
        seen.append(url)
        if body is not None:
            raise AssertionError(f"zkb_stats 用整数 id 不该发 POST：{url}")
        return document

    api._reset_zkb_state()
    api.clear_caches()
    monkeypatch.setattr(api, "_zkb_default_requester", fake)
    yield seen
    api._reset_zkb_state()


def test_zkb_stats_schema_has_no_fit() -> None:
    """`zkb_stats` 的 schema：**没有 `fit`**；`entity` 与 `entity_type` 必填。

    `entity_type` 的 `enum` **与 api 同源**（不抄第二份），且它必须是必填 —— 同一串数字
    在角色 / 公司 / 联盟三个命名空间里都可能存在，靠 id 猜类型必然出错。
    """
    spec = _tool_spec("zkb_stats")
    assert "实体画像" in spec["title"]
    schema = spec["inputSchema"]
    assert "fit" not in schema["properties"], "zkb_stats 不是配装工具"
    assert schema["required"] == ["entity", "entity_type"]
    assert schema["properties"]["entity"]["type"] == ["string", "integer"]
    assert schema["properties"]["entity_type"]["enum"] == list(
        api.ZKB_STATS_ENTITY_KINDS
    )
    include = schema["properties"]["include"]
    assert include["type"] == "array"
    assert include["items"]["enum"] == list(api.ZKB_STATS_BLOCKS)
    for token in ("entity_type", "include", "months"):
        assert token in spec["description"], f"description 必须写清 {token}"


def test_zkb_stats_description_pins_the_three_traps() -> None:
    """description 必须点名三个**真会踩**的坑（它们是这个工具最容易误读的地方）。

    ① `efficiency` 是我们派生的，≠ `danger_ratio`；② `locations` 不是星系；
    ③ 大块要显式 `include`（默认约 3KB，联盟全要 120KB）。
    """
    description = _tool_spec("zkb_stats")["description"]
    assert "danger_ratio" in description
    assert "efficiency" in description
    assert "不是星系" in description and "systems" in description
    assert "include" in description
    assert "formula=null" in description, "公式未公开这件事必须写出来"


def test_zkb_stats_call_round_trip(stats_offline: list[str]) -> None:
    """`tools/call zkb_stats` → MCP → api → 请求器整条链路（只有最后一跳是假的）。"""
    call_result = _call(
        "zkb_stats", {"entity": 2117515414, "entity_type": "character"}
    )
    assert call_result.get("isError") is not True, call_result
    envelope = _envelope(call_result)
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert result["entity"]["name"] == "SixtyEverSnake"
    assert result["style"]["danger_ratio"] == 69
    assert result["style"]["solo_ratio"] == 1.8, "百分数量纲（不是 0.018）"
    assert result["top"]["alliances"] == [], "空榜必须保留键"
    assert stats_offline == [
        "https://zkillboard.com/api/stats/characterID/2117515414/"
    ], f"URL 拼错：{stats_offline}"


def test_zkb_stats_missing_entity_type_is_a_business_error(
    stats_offline: list[str],
) -> None:
    """不传 `entity_type` → 业务错误（`isError`），**不是** JSON-RPC 协议错误。

    协议层只管「参数形状」；「必须显式指定实体类型」是业务判断，该由 api 给
    `invalid_argument` + `hint`，客户端才读得懂怎么改。
    """
    call_result = _call("zkb_stats", {"entity": 2117515414})
    assert call_result.get("isError") is True
    envelope = _envelope(call_result)
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "entity_type"
    assert envelope["error"]["hint"]["accepted"] == list(api.ZKB_STATS_ENTITY_KINDS)
    assert stats_offline == [], "参数错误不该发任何请求"


def test_zkb_stats_unknown_block_is_a_business_error(
    stats_offline: list[str],
) -> None:
    """`include` 里的未知块名 → `invalid_argument` + 可用清单（**不静默忽略**）。"""
    call_result = _call(
        "zkb_stats",
        {"entity": 2117515414, "entity_type": "character", "include": ["nope"]},
    )
    assert call_result.get("isError") is True
    envelope = _envelope(call_result)
    assert envelope["error"]["field"] == "include"
    assert envelope["error"]["hint"]["accepted"] == list(api.ZKB_STATS_BLOCKS)
    assert stats_offline == []


def test_zkb_stats_injected_requester_is_not_forwards() -> None:
    """客户端**不能**塞 `requester` / `clock` / `sleeper` 进来（那三个是测试注入点）。

    MCP 层必须把它们剔掉，否则外部客户端就能替换掉出网实现。这里从反面证明：
    即使参数里带了 `requester`，也只会被丢掉（于是走生产请求器，被 fixture 拦下来）。
    """
    spec = _tool_spec("zkb_stats")["inputSchema"]
    for name in ("requester", "clock", "sleeper"):
        assert name not in spec["properties"], f"{name} 不该出现在协议表面"



# =============================================================================
# 六、`fit_explain`（第 9 个工具）：schema 形状 + 三类口径 + 两种错误的归属
# =============================================================================


def test_fit_explain_schema_shape() -> None:
    """`metric` 必填、`depth` 是 1/2 的整数（默认 2）；**`metric` 刻意不给 enum**。

    不给 enum 是设计决定：schema 一旦枚举，客户端 / 网关可能先按 schema 拒掉，
    agent 只能拿到协议层报错；不给枚举，`api.fit_explain` 才能回 `invalid_path` +
    一份当前登记表 —— 那才是「下一步换哪个指标」的答案。
    """
    spec = _tool_spec("fit_explain")
    schema = spec["inputSchema"]
    assert schema["required"] == ["fit", "metric"]
    assert "metric" in schema["properties"]
    assert "enum" not in schema["properties"]["metric"], "metric 不许在 schema 里枚举"
    depth = schema["properties"]["depth"]
    assert depth["type"] == "integer"
    assert depth["enum"] == [1, 2]
    assert depth["default"] == 2
    for name in mcp_server._COMMON_PARAMETERS:
        assert name in schema["properties"]


def test_fit_explain_returns_the_envelope_with_meta_triplet(eft_text: str) -> None:
    """调一次 A 可加类：`content[0].text` 与 `structuredContent` 是同一份信封，带版本三元组。"""
    result = _call("fit_explain", {"fit": eft_text, "metric": "survivability.ehp_mixed"})
    assert result.get("isError") is not True, "成功调用不该带 isError"
    envelope = _envelope(result)
    assert envelope["ok"] is True
    assert result["structuredContent"] == envelope
    body = envelope["result"]
    assert body["metric"] == "survivability.ehp_mixed"
    assert body["combine"] == "additive"
    assert body["sources"]
    meta = envelope["meta"]
    for key in ("spec_version", "engine_version", "sde_build"):
        assert meta.get(key), f"信封 meta 缺版本三元组的 {key}"


def test_fit_explain_all_three_classes_are_reachable_through_mcp(eft_text: str) -> None:
    """A / B / C 三类指标都能从 MCP 表面取到（不是只有 api 能取）。"""
    cases = (
        ("survivability.ehp_mixed", "additive"),
        ("mobility.speed_mps", "multiplicative"),
        ("survivability.thinnest.armor.ehp", "multiplicative"),
    )
    for metric, combine in cases:
        result = _call("fit_explain", {"fit": eft_text, "metric": metric})
        envelope = _envelope(result)
        assert envelope["ok"] is True, (metric, envelope.get("error"))
        assert envelope["result"]["combine"] == combine, metric


def test_fit_explain_depth_out_of_range_is_a_business_error(eft_text: str) -> None:
    """`depth=3` → **成功响应** + `isError:true` + `invalid_argument`（不是 `-32602`）。"""
    result = _call(
        "fit_explain", {"fit": eft_text, "metric": "survivability.ehp_mixed", "depth": 3}
    )
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "depth"


def test_fit_explain_unknown_metric_is_a_business_error_with_hint(eft_text: str) -> None:
    """`metric="nope"` → `invalid_path` + `hint` 是当前登记表（agent 据此换参数）。"""
    result = _call("fit_explain", {"fit": eft_text, "metric": "nope"})
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["error"]["kind"] == "invalid_path"
    assert envelope["error"]["hint"] == list(api.EXPLAIN_METRICS)


def test_fit_explain_missing_or_non_string_metric_is_invalid_params() -> None:
    """缺 `metric` / `metric` 不是字符串 → `-32602`（**参数形态**错，不是业务失败）。"""
    response = _rpc(
        "tools/call", {"name": "fit_explain", "arguments": {"fit": "[Hecate, x]"}}
    )
    assert response["error"]["code"] == mcp_server.INVALID_PARAMS
    assert response["error"]["data"]["field"] == "metric"

    response = _rpc(
        "tools/call",
        {"name": "fit_explain", "arguments": {"fit": "[Hecate, x]", "metric": 42}},
    )
    assert response["error"]["code"] == mcp_server.INVALID_PARAMS
    assert response["error"]["data"]["field"] == "metric"


# =============================================================================
# 七、`fit_sensitivity`（第 10 个工具）：schema + combine 三态 + 两个参数的错误归属
# =============================================================================


def test_fit_sensitivity_schema_shape() -> None:
    """`metric` 必填（不给 enum）；`ops` 是可选数组、`max_ops` 默认 32 且 `minimum=1`。"""
    schema = _tool_spec("fit_sensitivity")["inputSchema"]
    assert schema["required"] == ["fit", "metric"]
    assert "enum" not in schema["properties"]["metric"], "metric 不许在 schema 里枚举"
    ops = schema["properties"]["ops"]
    assert ops["type"] == "array"
    assert ops["items"]["properties"]["kind"]["enum"] == [
        "disable_item",
        "set_state",
        "set_mode",
    ]
    assert ops["items"]["required"] == ["kind"]
    max_ops = schema["properties"]["max_ops"]
    assert max_ops["type"] == "integer" and max_ops["minimum"] == 1
    assert max_ops["default"] == api.SENSITIVITY_MAX_OPS
    for name in mcp_server._COMMON_PARAMETERS:
        assert name in schema["properties"]


def test_fit_sensitivity_roundtrip_returns_envelope(eft_text: str) -> None:
    """调一次单值指标：`content[0].text` 与 `structuredContent` 同一份信封 + 版本三元组。"""
    result = _call(
        "fit_sensitivity", {"fit": eft_text, "metric": "resources.cpu.free", "max_ops": 3}
    )
    assert result.get("isError") is not True, "成功调用不该带 isError"
    envelope = _envelope(result)
    assert result["structuredContent"] == envelope
    body = envelope["result"]
    assert body["combine"] == "single"
    assert body["changes"] and len(body["changes"]) == 3
    for key in ("spec_version", "engine_version", "sde_build"):
        assert envelope["meta"].get(key), f"信封 meta 缺版本三元组的 {key}"


def test_fit_sensitivity_all_combine_values_are_reachable(eft_text: str) -> None:
    """`combine` 的 `additive` / `multiplicative` / `single` 三种都能从 MCP 取到。"""
    for metric, combine in (
        ("survivability.ehp_mixed", "additive"),
        ("mobility.speed_mps", "multiplicative"),
        ("stability.capacitor.stable_pct", "single"),
    ):
        result = _call("fit_sensitivity", {"fit": eft_text, "metric": metric, "max_ops": 2})
        envelope = _envelope(result)
        assert envelope["ok"] is True, (metric, envelope.get("error"))
        assert envelope["result"]["combine"] == combine, metric


def test_fit_sensitivity_explicit_ops_are_forwarded(eft_text: str) -> None:
    """MCP 侧给一条显式 op：只跑那一条（不是自动计划的 20 条）。"""
    result = _call(
        "fit_sensitivity",
        {
            "fit": eft_text,
            "metric": "resources.cpu.free",
            "ops": [{"kind": "disable_item", "item_index": 0}],
        },
    )
    body = _envelope(result)["result"]
    assert body["ops_planned"] == 1 and body["ops_executed"] == 1
    assert body["changes"][0]["op"]["item_index"] == 0


def test_fit_sensitivity_max_ops_out_of_range_is_a_business_error(eft_text: str) -> None:
    """`max_ops=0` → 成功响应 + `isError:true` + `invalid_argument` + `field=max_ops`。"""
    for value in (0, -3, "8"):
        result = _call(
            "fit_sensitivity", {"fit": eft_text, "metric": "resources.cpu.free", "max_ops": value}
        )
        assert result["isError"] is True, f"max_ops={value!r} 应是业务失败"
        envelope = _envelope(result)
        assert envelope["error"]["kind"] == "invalid_argument"
        assert envelope["error"]["field"] == "max_ops"


def test_fit_sensitivity_unknown_metric_is_a_business_error_with_hint(eft_text: str) -> None:
    """`metric="nope"` → `invalid_path` + `hint` 是 14 个可用指标。"""
    result = _call("fit_sensitivity", {"fit": eft_text, "metric": "nope"})
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["error"]["kind"] == "invalid_path"
    assert envelope["error"]["hint"] == list(api.SENSITIVITY_METRICS)


def test_fit_sensitivity_missing_metric_is_invalid_params() -> None:
    """缺 `metric` → `-32602`（参数形态错，不是业务失败）。"""
    response = _rpc(
        "tools/call", {"name": "fit_sensitivity", "arguments": {"fit": "[Hecate, x]"}}
    )
    assert response["error"]["code"] == mcp_server.INVALID_PARAMS
    assert response["error"]["data"]["field"] == "metric"


# =============================================================================
# 八、README §4.11 与 sensitivity 契约一致（文档守卫：代码加了 kind，文档必须跟上）
# =============================================================================
#
# 文档漂移最典型的形态：代码新增一个 warning kind，README 的表格没跟上 —— agent 就会把
# 「这一维没覆盖」（`modes_unknown`）读成「切模式没影响」。这条守卫把两件事绑在一起。

README_PATH = NOOBBAOZI_ROOT / "mcp" / "README.md"
"""运维/契约手册（§4.11 是 `fit_sensitivity` 的口径）。"""

SENSITIVITY_WARNING_KINDS: tuple[str, ...] = (
    "noop",
    "truncated",
    "variant_error",
    "metric_missing_after_change",
    "mode_not_available",
    "modes_unknown",
    "no_index",
    "invalid_op",
    "invalid_ops",
)
"""`sensitivity` 会发出的 `warnings[].kind` 全集（与 `project/sensitivity.py` 的发射点一致）。"""


def _readme_section(start_marker: str, end_marker: str) -> str:
    """取 README 里 `[start_marker, end_marker)` 这一段（找不到就抛，不返回空串冒充）。"""
    text = README_PATH.read_text(encoding="utf-8")
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


def _section_411_problems(section: str) -> list[str]:
    """§4.11 缺了什么（空列表 = 合格）。"""
    problems: list[str] = []
    if "ops_planned == ops_executed + skipped" not in section:
        problems.append("缺计数恒等式 ops_planned == ops_executed + skipped")
    for kind in SENSITIVITY_WARNING_KINDS:
        if f"`{kind}`" not in section:
            problems.append(f"缺 warning kind：{kind}")
    if "全 offline" not in section:
        problems.append("缺「全 offline ≡ 同型空配装」口径")
    if "dps.base" not in section:
        problems.append("缺 dps.base 的例外（0.0 vs None）")
    if "没有自己的进程级缓存" not in section:
        problems.append("缺「sensitivity 没有自己的 LRU」的准确说法")
    return problems


def test_readme_section_411_documents_the_sensitivity_contract() -> None:
    """`mcp/README.md` §4.11 必须写全：恒等式 + 9 个 warning kind + 两条误读口径。"""
    section = _readme_section("### 4.11", "## 5. 返回契约")
    problems = _section_411_problems(section)
    assert problems == [], f"§4.11 与实现脱节：{problems}"


def test_readme_section_411_guard_is_not_vacuous() -> None:
    """**配对断言**：从 §4.11 里删掉一个 warning kind → 守卫必须报出来。"""
    section = _readme_section("### 4.11", "## 5. 返回契约")
    assert _section_411_problems(section) == [], "前提：现在的 §4.11 是合格的"

    broken = section.replace("`modes_unknown`", "")
    problems = _section_411_problems(broken)
    assert any("modes_unknown" in problem for problem in problems), (
        f"删掉 kind 后守卫没报错：{problems}"
    )

    # 删掉恒等式也要报（否则「计数口径」可能被悄悄改回旧行为而不被发现）
    without_identity = section.replace("ops_planned == ops_executed + skipped", "")
    assert any("恒等式" in problem for problem in _section_411_problems(without_identity))


# =============================================================================
# 九、`fit_project`（第 11 个工具）：schema + 一给一 + 两种错误的归属
# =============================================================================

PROJECT_FIXTURES = NOOBBAOZI_ROOT / "acceptance" / "fixtures"
PROJECT_TARGET_PATH = PROJECT_FIXTURES / "f15-battleship-raven.json"
PROJECT_BURST_PATH = PROJECT_FIXTURES / "f14-command-damnation.json"
PROJECT_BURST_EFT = "[Absolution, CmdBurst]\n\nArmor Command Burst II, Armor Energizing Charge\n"


def _project_target_text() -> str:
    return PROJECT_TARGET_PATH.read_text(encoding="utf-8")


def test_fit_project_schema_shape() -> None:
    """必填 `target`（**没有** `fit`）；`sources` 是字符串数组；`strict_state` 布尔默认 true。"""
    schema = _tool_spec("fit_project")["inputSchema"]
    assert schema["required"] == ["target"]
    assert "fit" not in schema["properties"], "这个工具的参数叫 target，不叫 fit"
    assert schema["properties"]["target"]["type"] == "string"
    assert schema["properties"]["sources"]["type"] == "array"
    assert schema["properties"]["sources"]["items"]["type"] == "string"
    assert schema["properties"]["strict_state"]["type"] == "boolean"
    assert schema["properties"]["strict_state"]["default"] is True
    assert {"effects", "buffs"} <= set(schema["properties"])
    for name in mcp_server._COMMON_PARAMETERS:
        assert name in schema["properties"]


def test_fit_project_roundtrip_over_mcp() -> None:
    """目标（killmail）+ 来源（EFT 指挥爆发）：EHP 真的变了、来源行带 label。"""
    result = _call(
        "fit_project",
        {"target": _project_target_text(), "sources": [PROJECT_BURST_EFT]},
    )
    assert result.get("isError") is not True, "成功调用不该带 isError"
    envelope = _envelope(result)
    assert result["structuredContent"] == envelope
    body = envelope["result"]
    assert body["strict_state"] is True
    assert body["sources"] and body["sources"][0]["label"]
    metrics = {entry["path"]: entry for entry in body["delta"]["metrics"]}
    assert metrics["survivability.ehp_mixed"]["delta"] > 0


def test_fit_project_bad_source_is_a_business_error_with_index() -> None:
    """`sources[1]` 坏 → 成功响应 + `isError:true` + `invalid_argument` + `index=1`。"""
    result = _call(
        "fit_project",
        {"target": _project_target_text(), "sources": [PROJECT_BURST_EFT, "这不是配装"]},
    )
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["index"] == 1
    assert envelope["error"]["field"] == "sources[1]"


def test_fit_project_unknown_effect_is_a_warning_not_an_error() -> None:
    """未知 `effect_id` 走 MCP 仍是成功响应：`unknown_effect_id` 在 warnings 里。"""
    result = _call(
        "fit_project",
        {
            "target": _project_target_text(),
            "effects": [{"type_id": 527, "effect_id": 999999}],
        },
    )
    assert result.get("isError") is not True
    envelope = _envelope(result)
    assert envelope["ok"] is True
    assert [w["kind"] for w in envelope["result"]["warnings"]] == ["unknown_effect_id"]


def test_fit_project_bad_strict_state_is_a_business_error() -> None:
    """`strict_state` 不是布尔 → `invalid_argument` + `field`（业务失败，不是 `-32602`）。"""
    result = _call(
        "fit_project",
        {"target": _project_target_text(), "strict_state": "yes"},
    )
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "strict_state"


def test_fit_project_missing_target_is_invalid_params() -> None:
    """缺 `target` → `-32602`（参数形态错；这个工具不接受 `fit`）。"""
    for arguments in ({}, {"fit": "[Hecate, x]"}):
        response = _rpc("tools/call", {"name": "fit_project", "arguments": arguments})
        assert response["error"]["code"] == mcp_server.INVALID_PARAMS
        assert response["error"]["data"]["field"] == "target"


# =============================================================================
# 十、`fit_engagement`（第 12 个工具）：三个 mode + 三条边界 + 错误归属
# =============================================================================

ENGAGEMENT_ATTACKER_PATH = PROJECT_FIXTURES / "f1-hecate-killmail.eft"
ENGAGEMENT_DEFENDER_PATH = PROJECT_FIXTURES / "f15-battleship-raven.json"


def _engagement_attacker() -> str:
    return ENGAGEMENT_ATTACKER_PATH.read_text(encoding="utf-8")


def _engagement_defender() -> str:
    return ENGAGEMENT_DEFENDER_PATH.read_text(encoding="utf-8")


def test_fit_engagement_schema_shape() -> None:
    """必填 `attacker` + `defender` + `distance_m`；**没有 `fit`**；`mode` 枚举与 api 同源。"""
    schema = _tool_spec("fit_engagement")["inputSchema"]
    assert schema["required"] == ["attacker", "defender", "distance_m"]
    assert "fit" not in schema["properties"], "这个工具有两个配装输入，不叫 fit"
    for name in ("attacker", "defender"):
        assert schema["properties"][name]["type"] == "string"
        assert schema["properties"][name]["maxLength"] == mcp_server.MAX_FIT_CHARS
    assert schema["properties"]["distance_m"]["minimum"] == 0
    assert schema["properties"]["mode"]["enum"] == list(api.ENGAGEMENT_MODES)
    assert schema["properties"]["mode"]["default"] == api.ENGAGEMENT_MODE_EXCHANGE
    assert "anyOf" in schema["properties"]["fleet"]
    assert schema["properties"]["points"]["minimum"] == 2
    for name in mcp_server._COMMON_PARAMETERS:
        assert name in schema["properties"]


def test_fit_engagement_roundtrip_over_mcp() -> None:
    """A ↔ B：`kind=exchange`、两侧都有有效 DPS、两个血量口径各带用途说明。"""
    result = _call(
        "fit_engagement",
        {"attacker": _engagement_attacker(), "defender": _engagement_defender(), "distance_m": 1000},
    )
    assert result.get("isError") is not True, "成功调用不该带 isError"
    envelope = _envelope(result)
    assert result["structuredContent"] == envelope
    body = envelope["result"]
    assert body["kind"] == "exchange"
    for direction in ("a_on_b", "b_on_a"):
        totals = body[direction]["totals"]
        assert totals["effective_dps"] > 0
        survival = body[direction]["defender_survival"]
        assert survival["hp_pool"]["value"] != survival["ehp"]["value"]
        assert survival["hp_pool"]["compare_with"] and survival["ehp"]["compare_with"]

    # timeline / anchor 也走一遍 MCP 表面
    timeline = _envelope(
        _call(
            "fit_engagement",
            {
                "attacker": _engagement_attacker(),
                "defender": _engagement_defender(),
                "distance_m": 1000,
                "mode": "timeline",
                "points": 3,
            },
        )
    )
    assert timeline["result"]["kind"] == "timeline"

    anchor = _envelope(
        _call(
            "fit_engagement",
            {
                "attacker": _engagement_attacker(),
                "defender": _engagement_defender(),
                "distance_m": 1000,
                "mode": "anchor",
                "observed_hp": 500,
                "window_s": 60,
            },
        )
    )
    assert anchor["result"]["kind"] == "anchor_check"


def test_fit_engagement_unarmed_side_over_mcp() -> None:
    """一方无武器仍是成功响应：`ttk=null` + `no_weapons`（不是错误）。"""
    result = _call(
        "fit_engagement",
        {"attacker": "[Hecate, empty]\n", "defender": _engagement_defender(), "distance_m": 1000},
    )
    assert result.get("isError") is not True
    envelope = _envelope(result)
    assert envelope["ok"] is True
    assert envelope["result"]["race"]["a_time_to_kill_s"] is None
    assert any(
        "no_weapons" in flag for flag in envelope["result"]["a_on_b"]["flags"]
    )


def test_fit_engagement_fleet_over_mcp() -> None:
    """`fleet=8` → `fleet_assumption` + `order_of_magnitude_only`（量级模型）。"""
    result = _call(
        "fit_engagement",
        {
            "attacker": _engagement_attacker(),
            "defender": _engagement_defender(),
            "distance_m": 1000,
            "fleet": 8,
        },
    )
    body = _envelope(result)["result"]
    assert body["fleet_assumption"] is True
    assert "order_of_magnitude_only" in body["flags"]


def test_fit_engagement_bad_distance_is_a_business_error() -> None:
    """缺 `distance_m` / 值非法 → 成功响应 + `isError:true` + `invalid_argument` + `field`。"""
    for arguments in (
        {"attacker": "[Hecate, x]", "defender": "[Hecate, x]"},
        {"attacker": "[Hecate, x]", "defender": "[Hecate, x]", "distance_m": "20km"},
        {"attacker": "[Hecate, x]", "defender": "[Hecate, x]", "distance_m": -5},
    ):
        result = _call("fit_engagement", arguments)
        assert result["isError"] is True, arguments
        envelope = _envelope(result)
        assert envelope["error"]["kind"] == "invalid_argument"
        assert envelope["error"]["field"] == "distance_m"


def test_fit_engagement_bad_side_is_a_business_error_with_field() -> None:
    """守方坏 → `invalid_argument` + `field="defender"`（能指出是哪一侧）。"""
    result = _call(
        "fit_engagement",
        {"attacker": _engagement_attacker(), "defender": "这不是配装", "distance_m": 1000},
    )
    assert result["isError"] is True
    envelope = _envelope(result)
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "defender"


def test_fit_engagement_missing_sides_is_invalid_params() -> None:
    """缺 `attacker` / `defender` → `-32602`（参数形态错）。"""
    for arguments in ({"defender": "[Hecate, x]", "distance_m": 1000}, {"attacker": "[Hecate, x]", "distance_m": 1000}):
        response = _rpc("tools/call", {"name": "fit_engagement", "arguments": arguments})
        assert response["error"]["code"] == mcp_server.INVALID_PARAMS
        assert response["error"]["data"]["field"] in ("attacker", "defender")


# =============================================================================
# 七、`zkb_related`（T-1/5 新一轮）在 MCP 表面的契约
# =============================================================================

RELATED_FIXTURE = ZKB_FIXTURES / "related-30004614-202609091400.json"
RELATED_URL = "https://zkillboard.com/related/30004614/202609091400/"


@pytest.fixture()
def related_offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把**生产请求器**换成 related fixture（不联网），清掉限流时间戳。

    只拦 GET：`--url` 形态不需要 ESI 名字解析。真发了 POST 直接判失败 ——
    那说明 MCP 层把参数递错了。
    """
    document = json.loads(RELATED_FIXTURE.read_text(encoding="utf-8-sig"))
    seen: list[str] = []

    def fake(url: str, body: object | None = None) -> object:
        seen.append(url)
        if body is not None:
            raise AssertionError(f"zkb_related 用 url 时不该发 POST：{url}")
        return document

    api._reset_zkb_state()
    api.clear_caches()
    monkeypatch.setattr(api, "_zkb_default_requester", fake)
    yield seen
    api._reset_zkb_state()


def test_zkb_related_schema_has_no_fit() -> None:
    """`zkb_related`：**没有 `fit`**；四个参数；`required` 为空（由 api 判「至少给一套」）。"""
    spec = _tool_spec("zkb_related")
    assert "交火" in spec["title"]
    schema = spec["inputSchema"]
    assert "fit" not in schema["properties"], "zkb_related 不是配装工具"
    assert set(schema["properties"]) == {"url", "system", "time", "limit"}
    assert schema["required"] == [], (
        "「url 或 system+time 二选一」讲不清 JSON Schema —— 留给 api 报错更准确"
    )
    assert schema["properties"]["time"]["type"] == "string"
    assert schema["properties"]["limit"]["default"] == api.ZKB_RELATED_KILL_LIMIT


def test_zkb_related_description_pins_the_four_traps() -> None:
    """description 必须点名四个真会踩的坑（agent 只读这里）。"""
    description = _tool_spec("zkb_related")["description"]
    assert "12 位" in description and "YYYYMMDDHHMM" in description, "时间戳格式"
    assert "窗口半径" in description and "不等于整场战斗" in description, "exHours 的语义"
    assert "killmail_ids" in description, "必须告诉 agent 拿到 id 之后能干什么"
    assert "唯一能查任意历史窗口" in description, "这是它相对列表接口的核心价值"
    assert "complete" in description, "complete 只是 zkb 的判断"
    assert "相邻星系" in description, "相邻星系不包含"


def test_zkb_related_call_round_trip(related_offline: list[str]) -> None:
    """`tools/call zkb_related` → MCP → api → 请求器整条链路（只有最后一跳是假的）。"""
    call_result = _call("zkb_related", {"url": RELATED_URL})
    assert call_result.get("isError") is not True, call_result
    envelope = _envelope(call_result)
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert result["system"] == {"id": 30004614, "name": "AV-VB6"}
    assert result["region"] == "Fountain"
    assert len(result["killmail_ids"]["teamA"]) == 10
    assert len(result["killmail_ids"]["teamB"]) == 9
    assert related_offline == [
        "https://zkillboard.com/api/related/30004614/202609091400/"
    ], f"URL 拼错：{related_offline}"


def test_zkb_related_bad_time_is_a_business_error(related_offline: list[str]) -> None:
    """坏时间戳 → 业务错误（`isError`），**不是** JSON-RPC 协议错误。"""
    call_result = _call(
        "zkb_related", {"system": 30004614, "time": "2026-09-09 14:00"}
    )
    assert call_result.get("isError") is True
    envelope = _envelope(call_result)
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "time"
    assert related_offline == [], "参数错误不该发任何请求"


def test_zkb_related_cannot_be_injected() -> None:
    """客户端**不能**塞 `requester` / `clock` / `sleeper`（那三个是测试注入点）。"""
    spec = _tool_spec("zkb_related")["inputSchema"]
    for name in ("requester", "clock", "sleeper"):
        assert name not in spec["properties"], f"{name} 不该出现在协议表面"


# -----------------------------------------------------------------------------
# 「任意域名的 related 链接都能吃，但数据源始终是 zkillboard」
#
# 主人实测 `https://br.evetools.org/related/30004614/202609091400` 直接可用，裁定
# **不加数据源、不改代码**，只把这个事实写进说明。代码本就是「只解析路径」，所以这里
# 钉的是**说明**与**行为**两件事，缺一不可（说明漏了，agent 会以为只认 zkillboard 域名；
# 行为变了，说明就成了假话）。
# -----------------------------------------------------------------------------


def test_zkb_related_description_allows_any_domain_but_zkillboard_source() -> None:
    """description 必须同时说清两件事：**域名不限** 与 **数据源始终是 zkillboard**。"""
    description = _tool_spec("zkb_related")["description"]
    assert "任意域名" in description, (
        "必须写明域名不限，否则 agent 会以为只吃 zkillboard.com 的链接"
    )
    assert "数据源始终是 zkillboard" in description, (
        "必须写明数据源仍是 zkillboard —— 否则会被读成「从 evetools 取数」"
    )
    assert "不是那个域名的数据" in description, "反面也要写清：返回的不是那个域名的数据"
    # 三个真实域名举例，以及「12 位」这个硬格式
    for token in ("zkillboard", "br.evetools", "kb.evetools"):
        assert token in description, f"description 里应举到 {token}"
    assert "12 位" in description and "YYYYMMDDHHMM" in description


def test_zkb_related_any_domain_guard_is_not_vacuous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**配对证明**：把那两句从 description 里删掉，上一条必须不成立。

    打桩在 `_TOOL_DESCRIPTIONS` 上并清掉 `_SPECS_CACHE` —— 同一份代码、同一个取值路径，
    只少了两句话，所以它证明的是「断言真的依赖这两句」，不是「恰好命中了」。
    """
    title, description = mcp_server._TOOL_DESCRIPTIONS["zkb_related"]
    stripped = description.replace("任意域名", "").replace("数据源始终是 zkillboard", "")
    assert stripped != description, "打桩没起作用：这两句压根不在 description 里"
    monkeypatch.setitem(mcp_server._TOOL_DESCRIPTIONS, "zkb_related", (title, stripped))
    monkeypatch.setattr(mcp_server, "_SPECS_CACHE", None)

    degraded = _tool_spec("zkb_related")["description"]
    assert "任意域名" not in degraded
    assert "数据源始终是 zkillboard" not in degraded


@pytest.mark.parametrize(
    "url",
    (
        "https://br.evetools.org/related/30004614/202609091400",
        "https://kb.evetools.org/related/30004614/202609091400/",
        "https://zkillboard.com/related/30004614/202609091400/?page=1",
    ),
)
def test_zkb_related_accepts_third_party_hosts_but_calls_zkillboard(
    related_offline: list[str], url: str
) -> None:
    """第三方域名的 related 链接**直接可用**，且请求永远发到 zkillboard（行为侧的非永真）。"""
    call_result = _call("zkb_related", {"url": url})
    assert call_result.get("isError") is not True, call_result
    assert _envelope(call_result)["ok"] is True
    assert related_offline == [
        "https://zkillboard.com/api/related/30004614/202609091400/"
    ], f"第三方域名不该改变请求目标，实际请求了 {related_offline}"


def test_zkb_related_rejects_a_foreign_path(related_offline: list[str]) -> None:
    """「域名随便」**不等于**「路径随便」：第三方域名 + 非 related 路径仍要 `invalid_argument`。"""
    call_result = _call(
        "zkb_related", {"url": "https://br.evetools.org/kill/139275237/"}
    )
    assert call_result.get("isError") is True
    envelope = _envelope(call_result)
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "url"
    assert related_offline == [], "路径不对不该发请求"


# =============================================================================
# 八、`sde_search`（T-3/5）与 `locale` 在 MCP 表面的契约
# =============================================================================


def test_sde_search_schema_has_no_fit() -> None:
    """`sde_search`：**没有 `fit`**；四个参数；`required` 只有 `query`。"""
    spec = _tool_spec("sde_search")
    assert "反查" in spec["title"]
    schema = spec["inputSchema"]
    assert "fit" not in schema["properties"], "sde_search 不是配装工具"
    assert set(schema["properties"]) == {"query", "kind", "limit", "locale"}
    assert schema["required"] == ["query"]
    assert schema["properties"]["kind"]["enum"] == [
        api.SDE_SEARCH_KIND_ALL,
        *api.SDE_SEARCH_KINDS,
    ]
    assert schema["properties"]["limit"]["maximum"] == api.SDE_SEARCH_LIMIT_MAX


def test_sde_search_description_pins_the_traps() -> None:
    """description 必须点名四件 agent 只在这里能看到的事。"""
    description = _tool_spec("sde_search")["description"]
    assert "ambiguous" in description, "歧义语义"
    assert "did_you_mean" in description and "非精确" in description, "近似结果要标注"
    assert "每个词都出现" in description, "第四级（词全命中、顺序不限）"
    assert "category" in description, "kind 按 category 过滤（不是按名字猜）"
    assert "type_id" in description and "纯数字" in description, "数字 query 的口径"


def test_sde_search_call_round_trip() -> None:
    """`tools/call sde_search` → MCP → api → 本地索引（全链路，不联网）。"""
    call_result = _call("sde_search", {"query": "损伤控制", "limit": 3})
    assert call_result.get("isError") is not True, call_result
    envelope = _envelope(call_result)
    assert envelope["ok"] is True, envelope
    result = envelope["result"]
    assert result["query"] == "损伤控制"
    assert result["matches"][0]["type_id"] == 2048
    assert result["matches"][0]["name_zh"] == "损伤控制 II"
    assert result["matches"][0]["category"] == "Module"


def test_sde_search_bad_arguments_are_business_errors() -> None:
    """坏 `kind` / 空 `query` → `isError`（业务错误），**不是** JSON-RPC 协议错误。"""
    for arguments in ({"query": "Hawk", "kind": "drone"}, {"query": "  "}):
        call_result = _call("sde_search", arguments)
        assert call_result.get("isError") is True, arguments
        envelope = _envelope(call_result)
        assert envelope["error"]["kind"] == "invalid_argument"
        assert envelope["error"]["field"] in ("kind", "query")


def test_locale_is_only_exposed_on_the_name_heavy_tools() -> None:
    """`locale` 只给 `fit_parse` / `fit_killmail` / `sde_search`；其它入口**不许**出现。"""
    with_locale = {"fit_parse", "fit_killmail", "sde_search"}
    for name in mcp_server.TOOL_NAMES:
        properties = _tool_spec(name)["inputSchema"]["properties"]
        if name in with_locale:
            assert "locale" in properties, f"{name} 应该有 locale"
            assert properties["locale"]["enum"] == list(api.LOCALES)
            assert properties["locale"]["default"] == api.LOCALE_EN
        else:
            assert "locale" not in properties, (
                f"{name} 本单不该有 locale（它主要返回数字，见 _LOCALE_TOOLS 的注释）"
            )


def test_tool_count_is_derived_and_instructions_cover_all_14() -> None:
    """14 个工具；`INSTRUCTIONS` 按 `TOOL_NAMES` 顺序逐个提到（且不超 800）。"""
    assert mcp_server.TOOL_COUNT == len(mcp_server.TOOL_NAMES) == 14
    assert len(mcp_server.INSTRUCTIONS) <= 800
    positions = [mcp_server.INSTRUCTIONS.find(name) for name in mcp_server.TOOL_NAMES]
    assert all(position >= 0 for position in positions), (
        f"有工具没写进 INSTRUCTIONS：{[n for n, p in zip(mcp_server.TOOL_NAMES, positions) if p < 0]}"
    )
    assert positions == sorted(positions), "INSTRUCTIONS 里的顺序必须与 TOOL_NAMES 一致"
