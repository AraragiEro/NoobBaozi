"""L5 传输形态之一：`noobbaozi/cli.py` 的契约测试（文档 §10.5「人工验证的基线」）。

钉住四件事：

1. **退出码三档**：`ok:true` → `0`、`ok:false` → `1`、参数 / IO 错误 → `2`
   （配装内容有问题**不是**参数错误，走 `1` + 结构化错误）；
2. **`--json` 可以被 `json.loads`**，且内容就是 `noobbaozi.api` 返回值的原样
   （CLI 与 MCP 共用同一个函数层：这里顺手与 `api.fit_get` 对数）；
3. **人话输出不换算**：打印出来的数字与 `json.dumps(api 的值)` 逐字符相同；
4. **`python -m noobbaozi.cli` 真的能跑**（子进程，含 `--fit-file -` 从 stdin 读）。

绝大多数断言用**进程内** `cli.main([...])`（快、能 `capsys` 抓输出）；
另留两条**子进程**测试，专门验 `python -m` 这个入口本身（argv / stdin / 退出码）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from noobbaozi import api, cli

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = NOOBBAOZI_ROOT.parent
EFT_PATH = NOOBBAOZI_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"
KILL_PATH = NOOBBAOZI_ROOT / "spike" / "kill.json"


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


def _run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    """进程内跑一次 CLI，返回 `(退出码, stdout, stderr)`（用 `capsys` 抓输出）。"""
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _subprocess_cli(argv: list[str], *, stdin: str | None = None) -> subprocess.CompletedProcess:
    """子进程跑 `python -m noobbaozi.cli`（工作目录 = 仓库根，`-m` 靠 cwd 找到 noobbaozi 包）。"""
    return subprocess.run(
        [sys.executable, "-m", "noobbaozi.cli", *argv],
        cwd=str(WORKSPACE_ROOT),
        input=stdin,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


# =============================================================================
# 一、退出码 0：人话 / JSON 两条出口
# =============================================================================


def test_index_human_is_readable(eft_text: str, capsys: pytest.CaptureFixture[str]) -> None:
    """`index` 不带 `--json`：紧凑人话摘要（船名 + 六视角 + flags + budget）。"""
    code, out, err = _run_cli(["index", "--fit-file", str(EFT_PATH)], capsys)
    assert code == cli.EXIT_OK, err
    assert err == ""
    assert out.splitlines()[0].startswith("Hecate (35683)")
    for block in ("firepower:", "survivability:", "mobility:", "resources:",
                  "constraints:", "stability:", "flags(", "budget:"):
        assert block in out, f"人话摘要里缺少 {block}"
    # 不要花哨表格：不出现表格边框字符
    assert "|" not in out and "┌" not in out


def test_index_json_is_machine_readable(eft_text: str, capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` 就是 `json.dumps(api 返回值)`：能 `json.loads`，且与 api 逐键相同。"""
    code, out, err = _run_cli(["index", "--fit-file", str(EFT_PATH), "--json"], capsys)
    assert code == cli.EXIT_OK, err
    assert err == ""
    envelope = json.loads(out)
    from_api = api.fit_index(eft_text)
    assert envelope["ok"] is True
    assert envelope == from_api, "--json 必须是 api 返回值的原样序列化"


def test_get_json_value_matches_api(eft_text: str, capsys: pytest.CaptureFixture[str]) -> None:
    """`get --json` 的 value 与进程内 `api.fit_get` 逐位相同（CLI 不另算一次）。"""
    code, out, err = _run_cli(
        ["get", "--fit-file", str(EFT_PATH), "--path", "mobility.speed_mps", "--json"], capsys
    )
    assert code == cli.EXIT_OK, err
    envelope = json.loads(out)
    expected = api.fit_get(eft_text, "mobility.speed_mps")
    assert envelope["result"]["value"] == expected["result"]["value"]
    assert envelope["result"]["provenance"] == expected["result"]["provenance"]


def test_human_output_does_not_reformat_numbers(
    eft_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """人话输出**不换算、不四舍五入**：打印的数字 == `json.dumps(api 的值)`。"""
    value = api.fit_get(eft_text, "mobility.speed_mps")["result"]["value"]
    code, out, _err = _run_cli(
        ["get", "--fit-file", str(EFT_PATH), "--path", "mobility.speed_mps"], capsys
    )
    assert code == cli.EXIT_OK
    assert out.splitlines()[0] == f"mobility.speed_mps = {json.dumps(value)}"


def test_all_five_commands_run(eft_text: str, capsys: pytest.CaptureFixture[str]) -> None:
    """五个子命令都能跑通（`parse` / `view` / `detail` 的人话输出不空）。"""
    cases = (
        (["parse", "--fit-file", str(EFT_PATH)], "parse ok"),
        (["view", "--fit-file", str(EFT_PATH), "--view", "mobility"], "overview:"),
        (["detail", "--fit-file", str(EFT_PATH), "--view", "stability"], "detail ·"),
    )
    for argv, marker in cases:
        code, out, err = _run_cli(argv, capsys)
        assert code == cli.EXIT_OK, f"{argv}: {err}"
        assert marker in out, f"{argv} 的输出里没有 {marker}：{out[:200]}"


def test_killmail_and_dna_sources_work(capsys: pytest.CaptureFixture[str]) -> None:
    """`--killmail FILE` 与 `--dna TEXT` 也能当输入来源。"""
    code, out, err = _run_cli(
        ["parse", "--killmail", str(KILL_PATH), "--json"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert json.loads(out)["result"]["source_format"] == "killmail"

    dna = "35683:2048;1:10190;1::"
    code, out, err = _run_cli(["index", "--dna", dna, "--json"], capsys)
    assert code == cli.EXIT_OK, err
    assert json.loads(out)["result"]["ship"]["type_id"] == 35683


# =============================================================================
# 二、退出码 1：api 失败（配装 / 路径 / 视角本身有问题）
# =============================================================================


def test_invalid_path_json_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    """路径不存在 → 退出码 `1`，`--json` 输出仍可 `json.loads`（ok:false + kind）。"""
    code, out, err = _run_cli(
        ["get", "--fit-file", str(EFT_PATH), "--path", "mobility.nope", "--json"], capsys
    )
    assert code == cli.EXIT_API_FAILURE
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_path"
    assert envelope["error"]["hint"]
    assert err == ""


def test_api_failure_human_goes_to_stderr(capsys: pytest.CaptureFixture[str]) -> None:
    """人话模式下失败信息进 **stderr**（stdout 干净，可被管道直接消费）。"""
    code, out, err = _run_cli(
        ["get", "--fit-file", str(EFT_PATH), "--path", "mobility.nope"], capsys
    )
    assert code == cli.EXIT_API_FAILURE
    assert out == ""
    assert "失败 invalid_path" in err
    assert "hint:" in err


def test_bad_eft_exit_code_1(capsys: pytest.CaptureFixture[str]) -> None:
    """配装本身写错 → `1`（**不是** 2：参数没错，是内容有问题）。"""
    code, out, err = _run_cli(
        ["index", "--eft", "[Hecate, x]\nDamage Controll II", "--json"], capsys
    )
    assert code == cli.EXIT_API_FAILURE
    envelope = json.loads(out)
    assert envelope["error"]["kind"] == "unknown_module"
    assert envelope["error"]["candidates"]


# =============================================================================
# 三、退出码 2：参数 / IO 错误
# =============================================================================


def test_no_source_is_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    """一个输入来源都没给 → `2`（参数错误）。"""
    code, out, err = _run_cli(["index"], capsys)
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "参数错误" in err and "--fit-file" in err


def test_two_sources_is_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    """输入来源互斥：同时给两个 → `2`。"""
    code, _out, err = _run_cli(
        ["index", "--fit-file", str(EFT_PATH), "--eft", "[Hecate, x]"], capsys
    )
    assert code == cli.EXIT_USAGE
    assert "互斥" in err


def test_bad_choice_is_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    """`--skill-tier` 取值不在白名单 / 缺必填 `--view` → argparse 自己给 `2`。"""
    code, _out, _err = _run_cli(
        ["index", "--fit-file", str(EFT_PATH), "--skill-tier", "all-5"], capsys
    )
    assert code == cli.EXIT_USAGE

    code, _out, _err = _run_cli(["view", "--fit-file", str(EFT_PATH)], capsys)
    assert code == cli.EXIT_USAGE


def test_missing_file_is_exit_code_2(capsys: pytest.CaptureFixture[str]) -> None:
    """文件读不到 → `2` + 人话（不是「配装解析失败」）。"""
    code, out, err = _run_cli(["index", "--fit-file", "no-such-fit.eft"], capsys)
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "读不到" in err


def test_non_utf8_file_message(monkeypatch: pytest.MonkeyPatch,
                               capsys: pytest.CaptureFixture[str]) -> None:
    """非 UTF-8 文件 → `2` + 人话提示（指明是编码问题，不是配装问题）。"""

    def boom(_path: str) -> str:
        raise UnicodeDecodeError("utf-8", b"\xff\xfe", 0, 1, "invalid start byte")

    monkeypatch.setattr(cli, "_read_utf8", boom)
    code, out, err = _run_cli(["index", "--fit-file", "whatever.eft"], capsys)
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "不是 UTF-8 文本" in err
    assert "UTF-8 保存" in err


# =============================================================================
# 四、`python -m noobbaozi.cli` 入口本身（子进程）
# =============================================================================


def test_module_entry_point_json() -> None:
    """真起一个子进程跑 `python -m noobbaozi.cli index --json`（退出码 + 可解析）。"""
    completed = _subprocess_cli(
        ["index", "--fit-file", str(EFT_PATH), "--json"]
    )
    assert completed.returncode == cli.EXIT_OK, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["ok"] is True
    assert envelope["result"]["ship"]["name"] == "Hecate"
    assert envelope["meta"]["skill_tier"]["name"] == "all4+min_required"


def test_module_entry_point_reads_stdin(eft_text: str) -> None:
    """`--fit-file -` 从 stdin 读 EFT（UTF-8，无 BOM）。"""
    completed = _subprocess_cli(["parse", "--fit-file", "-", "--json"], stdin=eft_text)
    assert completed.returncode == cli.EXIT_OK, completed.stderr
    envelope = json.loads(completed.stdout)
    assert envelope["ok"] is True
    assert envelope["result"]["source_format"] == "eft"
    assert envelope["result"]["ship"]["type_id"] == 35683


def test_module_entry_point_argument_error_exit_code() -> None:
    """子进程里的参数错误也是 `2`（`python -m` 与进程内 `main()` 行为一致）。"""
    completed = _subprocess_cli(["index"])
    assert completed.returncode == cli.EXIT_USAGE
    assert "参数错误" in completed.stderr


# =============================================================================
# 六、`killmail` 子命令（T-49：战报上下文）
# =============================================================================
#
# 样本：`noobbaozi/tests/fixtures/killmail/139239127-zkb.json`（zkillboard 的真实响应原文）。
# 星系名 / 安全等级一律打桩，不联网。

KILLMAIL_DIR = NOOBBAOZI_ROOT / "tests" / "fixtures" / "killmail"
KILLMAIL_ZKB_PATH = KILLMAIL_DIR / "139239127-zkb.json"
AMAMAKE = {
    "name": "Amamake",
    "security_status": 0.43876123428344727,
    "security_class": "E",
    "has_class": True,
}


def _offline_system(monkeypatch: pytest.MonkeyPatch) -> list[list[int]]:
    """星系查询 + 实体名都打桩（真实 ESI 快照 / 真实 names 样本）+ 清掉进程级缓存。

    返回每次 `POST /universe/names/` 的请求体 —— 测试用它断言「一条战报只查一批名字」。
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


def test_killmail_json_output_is_the_api_envelope(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`killmail --json`：退出码 0，stdout 就是 `api.fit_killmail` 的信封原样。"""
    calls = _offline_system(monkeypatch)
    code, out, err = _run_cli(
        ["killmail", "--fit-file", str(KILLMAIL_ZKB_PATH), "--json"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert err == ""
    envelope = json.loads(out)
    assert envelope["ok"] is True
    result = envelope["result"]
    assert result["killmail"]["killmail_id"] == 139239127
    assert result["killmail"]["solar_system"]["name"] == "Amamake"
    assert result["attackers"]["count"] == 79
    assert result["attackers"]["damage_check"] == "matches_victim"
    assert result["context"]["damage_taken_vs_ehp"]["ratio"] is not None
    # T-52：驾驶员 / 军团 / 联盟名字都进 `--json`（受害者 + 伤害 Top + 最终一击）
    assert result["victim"]["character"]["name"] == "Warlock Industries Event"
    assert result["victim"]["alliance"]["name"] == "Warlock Industries Alliance"
    assert result["attackers"]["by_pilot"][0]["character"]["name"]
    assert result["attackers"]["final_blow"]["character"]["name"] == "Ignacious Deviant"
    assert len(calls) == 1, "一条战报只该发一次 names 请求"
    # CLI 与 API 共用同一个函数层：两边必须一模一样
    assert envelope == api.fit_killmail(KILLMAIL_ZKB_PATH.read_text(encoding="utf-8"))


def test_killmail_human_output_is_readable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`killmail` 不带 `--json`：人话摘要（战报一行 + 分桶 + 伤害 vs EHP）**带人名**。"""
    _offline_system(monkeypatch)
    code, out, err = _run_cli(["killmail", "--fit-file", str(KILLMAIL_ZKB_PATH)], capsys)
    assert code == cli.EXIT_OK, err
    assert err == ""
    assert out.splitlines()[0].startswith("killmail 139239127")
    for block in (
        "victim: Warlock Industries Event (Warlock Industries Corporation / "
        "Warlock Industries Alliance) — Sarathiel (87381)",
        "final_blow: Ignacious Deviant (Hierarch Apotheosis Consortium / Ruinous Powers)",
        "Hexa 2500mm Repeating Cannon II",
        "伤害 Top3:",
        "value:",
        "attackers: count=79",
        "by_ship_group(",
        "by_weapon_family(",
        "context.damage_taken_vs_ehp:",
        "warnings(",
    ):
        assert block in out, f"人话摘要里缺少 {block}：\n{out}"
    assert "|" not in out, "不要花哨表格"


def test_killmail_human_output_shows_capsule_and_zero_damage(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """T-53：攻击者行给四个计数、Capsule 桶标「记录船」、末尾一行蛋警告。"""
    _offline_system(monkeypatch)
    code, out, err = _run_cli(["killmail", "--fit-file", str(KILLMAIL_ZKB_PATH)], capsys)
    assert code == cli.EXIT_OK, err
    assert "count=79 · 有伤害 57 · 零伤害 22 · 记录船是蛋 3" in out, out
    assert "Capsule × 4 = 1162921" in out, "蛋桶的伤害照旧打印（不扣）"
    assert "（记录船，非实际输出船）" in out, "蛋桶后面必须有人话标注"
    assert out.count("（记录船，非实际输出船）") == 1, "只有蛋桶该带这个标注"
    last = out.splitlines()[-1]
    assert "警告:" in last and "蛋" in last, f"末尾该有一行蛋警告，实际是：{last!r}"
    assert "蛋装不了武器" in last and "已被摧毁的船" in last, last


def test_killmail_rejects_a_fit_without_a_battle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """给 EFT → 退出码 `1`（业务失败）+ `--json` 里是 `not_a_killmail`。"""
    code, out, err = _run_cli(
        ["killmail", "--fit-file", str(EFT_PATH), "--json"], capsys
    )
    assert code == cli.EXIT_API_FAILURE, err
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "not_a_killmail"

    # 人话模式：错误写 stderr，退出码同样是 1
    code, out, err = _run_cli(["killmail", "--fit-file", str(EFT_PATH)], capsys)
    assert code == cli.EXIT_API_FAILURE
    assert out == ""
    assert "not_a_killmail" in err


# =============================================================================
# 五、`search`（T-50：`--character NAME --ship SHIP --past-seconds 604800 --json`）
# =============================================================================
#
# 目标是钉住 **CLI 的接线**（选项名 → api 的关键字参数），不是重测 api 的逻辑：
# 因此只把 api 的请求器换成 fixture（不联网），其余元件都是真的。

ZKB_FIXTURES = NOOBBAOZI_ROOT / "tests" / "fixtures" / "zkb"
LOSSES_URL = "https://zkillboard.com/api/losses/shipTypeID/22444/pastSeconds/604800/"
KILLS_URL = "https://zkillboard.com/api/kills/shipTypeID/22444/pastSeconds/604800/"


@pytest.fixture()
def zkb_offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把战报查询与**实体名解析**都换成 fixture（不联网），并清掉限流时间戳。

    两条 POST 端点分开打桩：`/universe/ids/`（名字 → id）走 `_zkb_default_requester`，
    `/universe/names/`（id → 名字）走 `_post_json` —— 两个都得拦，否则会真联网。
    """
    losses = json.loads(
        (ZKB_FIXTURES / "losses-shipTypeID-22444-pastSeconds-604800.json").read_text(
            encoding="utf-8"
        )
    )
    ids = json.loads(
        (ZKB_FIXTURES / "esi-ids-sleipnir-hecate.json").read_text(encoding="utf-8")
    )
    names = json.loads(
        (ZKB_FIXTURES / "esi-universe-names.json").read_text(encoding="utf-8")
    )
    seen: list[str] = []

    def fake(url: str, body: object | None = None) -> object:
        seen.append(url)
        return ids if body is not None else losses

    def fake_names(url: str, payload: object) -> object:
        seen.append(url)
        wanted = set(payload)  # type: ignore[arg-type]
        return [entry for entry in names if entry["id"] in wanted]

    api._reset_zkb_state()
    api.clear_caches()  # 实体名缓存也是进程级的：不清会让「这轮发了几次请求」不可复现
    monkeypatch.setattr(api, "_zkb_default_requester", fake)
    monkeypatch.setattr(api, "_post_json", fake_names)
    yield seen
    api._reset_zkb_state()


def test_search_json_is_the_api_envelope(
    zkb_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """`search --ship Sleipnir --past-seconds 604800 --json` → 信封原样 + 退出码 0。"""
    code, out, err = _run_cli(
        ["search", "--ship", "Sleipnir", "--past-seconds", "604800", "--json"], capsys
    )
    assert code == cli.EXIT_OK, err
    envelope = json.loads(out)
    assert envelope["ok"] is True
    result = envelope["result"]
    assert result["count"] == 12
    assert result["query"]["resolved"]["ship"] == {"id": 22444, "name": "Sleipnir"}
    assert result["query"]["past_seconds"] == 604800
    assert result["query"]["side"] == api.ZKB_SIDE_BOTH, "没给 --side 时默认 both"
    # T-52：每行带受害者的驾驶员 / 军团 / 联盟名字
    for row in result["kills"]:
        assert row["victim"]["character"]["name"], row["killmail_id"]
    # 名字 → id 先解析，两条战报路径各一次，身份名字最后一批（一批 = 不为每行各查一次）
    assert zkb_offline == [
        api.ESI_UNIVERSE_IDS_API,
        KILLS_URL,
        LOSSES_URL,
        api.ESI_UNIVERSE_NAMES_API,
    ], zkb_offline


def test_search_human_output_is_readable(
    zkb_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """人话模式只排版：条件 / count / 聚合 / 每条一行（**带受害者名字**）/ note 都在。"""
    code, out, err = _run_cli(
        ["search", "--ship", "22444", "--past-seconds", "604800", "--side", "losses"],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    for block in ("search ·", "条件:", "count=12", "by_ship:", "time_span:", "urls:"):
        assert block in out, f"人话摘要里缺少 {block}：\n{out}"
    assert "139265742" in out, "每条战报的 killmail_id 必须出现"
    assert "受害者=" in out, "每行必须带受害者身份"
    assert "id:" not in out, "这批 id 全都能解析出名字，不该出现 id: 占位"
    assert "fit_killmail" in out, "必须提示明细走 fit_killmail"
    assert "|" not in out, "不要花哨表格"


def test_search_bad_page_is_exit_code_1_without_network(
    zkb_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """`--page 0` → 业务失败（退出码 1）+ `invalid_argument`，且**一次请求都没发**。"""
    code, out, err = _run_cli(["search", "--ship", "22444", "--page", "0", "--json"], capsys)
    assert code == cli.EXIT_API_FAILURE
    envelope = json.loads(out)
    assert envelope["ok"] is False
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "page"
    assert zkb_offline == []


def test_search_without_filters_is_exit_code_1(
    zkb_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """一个过滤条件都不给 → 退出码 1（不是参数错误 2：那是 api 的业务判断）。"""
    code, out, err = _run_cli(["search", "--json"], capsys)
    assert code == cli.EXIT_API_FAILURE
    envelope = json.loads(out)
    assert envelope["error"]["kind"] == "invalid_argument"
    assert envelope["error"]["field"] == "filters"
    assert zkb_offline == []


def test_search_human_failure_goes_to_stderr(
    zkb_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """人话模式下失败写 stderr（stdout 保持干净），退出码 1。"""
    code, out, err = _run_cli(["search", "--ship", "22444", "--limit", "9999"], capsys)
    assert code == cli.EXIT_API_FAILURE
    assert out == ""
    assert "invalid_argument" in err


def test_module_entry_point_knows_search() -> None:
    """真起子进程：`python -m noobbaozi.cli search --help` 里能看到这一组选项。"""
    completed = _subprocess_cli(["search", "--help"])
    assert completed.returncode == 0, completed.stderr
    for token in (
        "--character",
        "--corporation",
        "--alliance",
        "--ship",
        "--solar-system",
        "--region",
        "--past-seconds",
        "--page",
        "--side",
        "--limit",
        "--json",
    ):
        assert token in completed.stdout, f"--help 里应提到 {token}：\n{completed.stdout}"


# =============================================================================
# 三、实体画像（T-55）：`stats`
# =============================================================================

STATS_CHARACTER = ZKB_FIXTURES / "stats-character-2117515414.json"
STATS_ALLIANCE = ZKB_FIXTURES / "stats-alliance-1354830081.json"


@pytest.fixture()
def stats_offline(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """把 zkb 的 GET 请求器换成 stats fixture（不联网），并清掉限流时间戳。

    用**整数 id** 调用时不会有 ESI 名字解析，所以只需拦 GET 这一跳；`POST` 一旦发生就
    直接判失败 —— 那说明 CLI 走错了解析路径。
    """
    document = json.loads(STATS_CHARACTER.read_text(encoding="utf-8-sig"))
    seen: list[str] = []

    def fake(url: str, body: object | None = None) -> object:
        seen.append(url)
        if body is not None:
            raise AssertionError(f"stats 用整数 id 不该发 POST：{url}")
        return document

    api._reset_zkb_state()
    api.clear_caches()
    monkeypatch.setattr(api, "_zkb_default_requester", fake)
    yield seen
    api._reset_zkb_state()


def test_stats_json_is_the_api_envelope(
    stats_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """`stats --character <id> --json` → stdout 就是 `api.zkb_stats` 的信封原样，退出码 0。"""
    code, out, err = _run_cli(
        ["stats", "--character", "2117515414", "--json"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert err == ""
    envelope = json.loads(out)
    assert envelope["ok"] is True
    result = envelope["result"]
    assert result["entity"]["name"] == "SixtyEverSnake"
    assert result["style"]["danger_ratio"] == 69
    assert result["style"]["solo_ratio"] == 1.8
    assert stats_offline == [
        "https://zkillboard.com/api/stats/characterID/2117515414/"
    ], f"URL 拼错：{stats_offline}"


def test_stats_human_output_carries_the_units(
    stats_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """人话输出**必须带量纲**，否则 `1.8` 一定被读成比例。

    钉住三个锚点：`solo` 的百分号、`avg_gang_size` 的「人/次」、`efficiency` 与
    `danger_ratio` 不是同一个东西的说明。
    """
    code, out, err = _run_cli(["stats", "--character", "2117515414"], capsys)
    assert code == cli.EXIT_OK, err
    assert "SixtyEverSnake" in out
    assert "solo=1.8%（百分数）" in out, f"百分数量纲丢了：\n{out}"
    assert "人/次" in out, f"avg_gang_size 的量纲丢了：\n{out}"
    assert "≠ zkb 的 danger_ratio" in out, "必须点明 efficiency 不是 danger_ratio"
    assert "formula=None" in out, "公式未公开要说出来"
    assert "top.locations" in out, "6 类榜单都要列出来"
    assert "Asteroid Belt" in out or "(Star)" in out, "locations 是天体级"


def test_stats_requires_exactly_one_entity(capsys: pytest.CaptureFixture[str]) -> None:
    """`stats` 的实体是**恰好一个**：0 个或 2 个都是用法错误（退出码 2，不进 api）。"""
    code, out, err = _run_cli(["stats"], capsys)
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "恰好一个实体" in err

    code, out, err = _run_cli(
        ["stats", "--character", "A", "--alliance", "B"], capsys
    )
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "只能查一个实体" in err
    assert "character, alliance" in err, "要说清到底多给了哪几个"


def test_stats_include_is_passed_through(
    stats_offline: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    """`--include` 可重复，原样递到 api 的 `include`；人话里带上补洞说明。"""
    code, out, err = _run_cli(
        ["stats", "--character", "2117515414", "--include", "months", "--json"], capsys
    )
    assert code == cli.EXIT_OK, err
    result = json.loads(out)["result"]
    assert list(result["blocks"]) == ["months"]
    block = result["blocks"]["months"]
    assert block["filled_internal_gaps"] == 37
    assert block["range"] == {"first": "2021-05", "last": "2026-10"}

    code, out, err = _run_cli(
        ["stats", "--character", "2117515414", "--include", "months"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert "补内部空洞=37" in out, f"months 的补洞语义没说清：\n{out}"


def test_stats_rejects_unknown_include_before_calling_api(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """非法 `--include` 由 argparse 挡住（退出码 2），**不会**进 api。"""
    code, out, err = _run_cli(
        ["stats", "--character", "2117515414", "--include", "nope"], capsys
    )
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "invalid choice" in err


def test_stats_human_failure_goes_to_stderr(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """人话模式下业务失败写 stderr、stdout 保持干净，退出码 1。"""
    api._reset_zkb_state()
    monkeypatch.setattr(
        api, "_zkb_default_requester",
        lambda url, body=None: {"error": "Invalid type or id"},
    )
    try:
        code, out, err = _run_cli(["stats", "--alliance", "1354830081"], capsys)
        assert code == cli.EXIT_API_FAILURE
        assert out == ""
        assert "unknown_entity" in err
    finally:
        api._reset_zkb_state()


def test_module_entry_point_knows_stats() -> None:
    """真起子进程：`python -m noobbaozi.cli stats --help` 里能看到这一组选项。"""
    completed = _subprocess_cli(["stats", "--help"])
    assert completed.returncode == 0, completed.stderr
    for token in ("--character", "--corporation", "--alliance", "--include", "--json"):
        assert token in completed.stdout, f"--help 里应提到 {token}：\n{completed.stdout}"


# =============================================================================
# 六、`explain`：三类口径各有一条人话出口 + 错误口径走 stderr
# =============================================================================


def test_explain_human_additive_lists_terms_and_sum(
    eft_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 可加类的人话：逐项 `数值 · 名字 · 占比` + `Σ == value`。"""
    code, out, err = _run_cli(
        ["explain", "--fit-file", str(EFT_PATH), "--metric", "survivability.ehp_mixed"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert err == ""
    assert out.splitlines()[0].startswith("survivability.ehp_mixed = ")
    assert "可加类" in out and "Σ" in out and "= value" in out
    for label in ("ehpShield", "ehpArmor", "ehpHull"):
        assert label in out, f"人话里缺少那一项：{label}"
    assert "%" in out, "可加类应给每项占比"
    assert "|" not in out and "┌" not in out


def test_explain_human_multiplicative_shows_converted_chain(
    eft_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """B 乘性类的人话：`base` → 因子链 → 结果，且乘数**已按算子语义折算**。

    这条盯的是一类会骗人的写法：把 `factor` 原样当乘数印出来（`post_percent 586.45`
    其实要按 `1 + 586.45/100` = ×6.8645 解释）。人话里出现 `× 586` 就是错的。
    """
    code, out, err = _run_cli(
        ["explain", "--fit-file", str(EFT_PATH), "--metric", "mobility.speed_mps"], capsys
    )
    assert code == cli.EXIT_OK, err
    assert "base = 180 m/s" in out
    assert "→" in out and "replay_ok=True" in out
    assert "× 6.8645" in out, f"post_percent 586.45 必须折算成 ×6.8645：\n{out}"
    assert "× 586" not in out, f"把 post_percent 的原始值当乘数印出来了：\n{out}"
    assert "不能相加" in out, "乘性类必须写上「因子不能相加」"


def test_explain_human_projection_shows_formula(
    eft_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """C 投影类的人话：先给 `formula`，再给「层 HP」与被除数/除数两项。"""
    code, out, err = _run_cli(
        [
            "explain", "--fit-file", str(EFT_PATH),
            "--metric", "survivability.thinnest.armor.ehp",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    assert "公式：" in out, f"投影类必须给公式：\n{out}"
    assert "层 HP = " in out
    assert "÷ " in out, "投影类第二项是除数"
    assert "replay_ok=True" in out


def test_explain_json_matches_api(eft_text: str, capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` 就是 api 信封原样（人话才做展示舍入）。"""
    code, out, err = _run_cli(
        [
            "explain", "--fit-file", str(EFT_PATH),
            "--metric", "survivability.ehp_mixed", "--json",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    envelope = json.loads(out)
    api.clear_caches()
    assert envelope == api.fit_explain(eft_text, "survivability.ehp_mixed")


def test_explain_depth_out_of_range_is_api_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--depth 3` 是**业务失败**（退出码 1、`invalid_argument`），不是 argparse 的 2。"""
    code, out, err = _run_cli(
        [
            "explain", "--fit-file", str(EFT_PATH),
            "--metric", "survivability.ehp_mixed", "--depth", "3",
        ],
        capsys,
    )
    assert code == cli.EXIT_API_FAILURE, f"应走 api 的 invalid_argument，不是参数错：{err}"
    assert out == ""
    assert "invalid_argument" in err and "depth" in err


def test_explain_unknown_metric_is_api_failure_with_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """不在登记表 → 退出码 1 + `invalid_path` + 可用指标列表（stderr）。"""
    code, out, err = _run_cli(
        ["explain", "--fit-file", str(EFT_PATH), "--metric", "nope"], capsys
    )
    assert code == cli.EXIT_API_FAILURE
    assert out == ""
    assert "invalid_path" in err
    assert "survivability.ehp_mixed" in err, "失败信息里必须给可用指标"


def test_explain_requires_metric(capsys: pytest.CaptureFixture[str]) -> None:
    """`--metric` 是必填：缺了就是 argparse 的用法错误（退出码 2）。"""
    code, out, err = _run_cli(["explain", "--fit-file", str(EFT_PATH)], capsys)
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "--metric" in err


def test_module_entry_point_knows_explain() -> None:
    """真起子进程：`python -m noobbaozi.cli explain --help` 里有这一组选项。"""
    completed = _subprocess_cli(["explain", "--help"])
    assert completed.returncode == 0, completed.stderr
    for token in ("--metric", "--depth", "--killmail-id", "--json"):
        assert token in completed.stdout, f"--help 里应提到 {token}：\n{completed.stdout}"


# =============================================================================
# 七、`sensitivity`：人话出口（delta / 四个计数 / warnings）+ 三种错误归属
# =============================================================================


def test_sensitivity_human_is_readable(
    eft_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """人话：基线 + 计划/执行/跳过/截断 + 逐条 delta（含比例），并说明 delta_pct 口径。"""
    code, out, err = _run_cli(
        [
            "sensitivity", "--fit-file", str(EFT_PATH),
            "--metric", "resources.cpu.free", "--max-ops", "3",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    assert err == ""
    first = out.splitlines()[0]
    assert first.startswith("resources.cpu.free = ")
    assert "combine=single" in first
    assert "计划" in out and "执行" in out and "截断 True" in out
    assert "disable_item" in out
    assert "注意：delta_pct = delta ÷ baseline" in out, "必须交代 delta_pct 是比例"
    assert "warnings(" in out, "截断必须说出来"
    assert "|" not in out and "┌" not in out


def test_sensitivity_json_matches_api(
    eft_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--json` 就是 api 信封原样。"""
    code, out, err = _run_cli(
        [
            "sensitivity", "--fit-file", str(EFT_PATH),
            "--metric", "resources.cpu.free", "--max-ops", "2", "--json",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    envelope = json.loads(out)
    api.clear_caches()
    assert envelope == api.fit_sensitivity(eft_text, "resources.cpu.free", max_ops=2)


def test_sensitivity_ops_json_array_is_forwarded(
    eft_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--ops-json` 的合法数组原样透传（一条 disable_item → 一条 change）。"""
    code, out, err = _run_cli(
        [
            "sensitivity", "--fit-file", str(EFT_PATH),
            "--metric", "resources.cpu.free",
            "--ops-json", '[{"kind": "disable_item", "item_index": 0}]',
            "--json",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    result = json.loads(out)["result"]
    assert result["ops_planned"] == 1 and result["ops_executed"] == 1
    assert result["changes"][0]["op"]["state"] == "offline"


def test_sensitivity_bad_ops_json_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """`--ops-json` 解析失败 / 顶层不是数组 → **参数错误（退出码 2）**，不拿去问引擎。"""
    for payload in ("[not json", '{"kind": "disable_item"}'):
        code, out, err = _run_cli(
            [
                "sensitivity", "--fit-file", str(EFT_PATH),
                "--metric", "resources.cpu.free", "--ops-json", payload,
            ],
            capsys,
        )
        assert code == cli.EXIT_USAGE, f"payload={payload!r} 应退出 2：{err}"
        assert out == ""
        assert "--ops-json" in err


def test_sensitivity_bad_max_ops_is_api_failure(capsys: pytest.CaptureFixture[str]) -> None:
    """`--max-ops 0` 是**业务失败**（退出码 1、`invalid_argument`），不是 argparse 的 2。"""
    code, out, err = _run_cli(
        [
            "sensitivity", "--fit-file", str(EFT_PATH),
            "--metric", "resources.cpu.free", "--max-ops", "0",
        ],
        capsys,
    )
    assert code == cli.EXIT_API_FAILURE, f"应走 api 的 invalid_argument：{err}"
    assert out == ""
    assert "invalid_argument" in err and "max_ops" in err


def test_sensitivity_unknown_metric_is_api_failure_with_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """不在登记表 → 退出码 1 + `invalid_path` + 可用指标列表（stderr）。"""
    code, out, err = _run_cli(
        ["sensitivity", "--fit-file", str(EFT_PATH), "--metric", "nope"], capsys
    )
    assert code == cli.EXIT_API_FAILURE
    assert out == ""
    assert "invalid_path" in err
    assert "resources.cpu.free" in err, "失败信息里必须给可用指标"


def test_sensitivity_requires_metric(capsys: pytest.CaptureFixture[str]) -> None:
    """`--metric` 必填（缺了是 argparse 的用法错误，退出码 2）。"""
    code, out, err = _run_cli(["sensitivity", "--fit-file", str(EFT_PATH)], capsys)
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "--metric" in err


def test_module_entry_point_knows_sensitivity() -> None:
    """真起子进程：`python -m noobbaozi.cli sensitivity --help` 里有这一组选项。"""
    completed = _subprocess_cli(["sensitivity", "--help"])
    assert completed.returncode == 0, completed.stderr
    for token in ("--metric", "--max-ops", "--ops-json", "--json"):
        assert token in completed.stdout, f"--help 里应提到 {token}：\n{completed.stdout}"


# =============================================================================
# 八、`project`：人话必须区分「改了 EHP」与「改了回血速度」
# =============================================================================

PROJECT_FIXTURES = NOOBBAOZI_ROOT / "acceptance" / "fixtures"
PROJECT_TARGET_PATH = PROJECT_FIXTURES / "f15-battleship-raven.json"
PROJECT_BURST_PATH = PROJECT_FIXTURES / "f14-command-damnation.json"
PROJECT_REPAIR_PATH = PROJECT_FIXTURES / "f12-logistics-oneiros.json"


def test_project_human_shows_ehp_and_who_gave_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人话：EHP 前后 + 来源 + buff（谁给的）；不带 markdown 记号。"""
    code, out, err = _run_cli(
        [
            "project", "--fit-file", str(PROJECT_TARGET_PATH),
            "--source", str(PROJECT_BURST_PATH),
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    assert err == ""
    assert out.splitlines()[0].startswith("Raven")
    assert "EHP " in out and "→" in out
    assert "← " in out, "必须写出「谁给的」"
    assert "投射 buff" in out
    assert "|" not in out and "**" not in out, "人话输出不该带 markdown 记号"


def test_project_human_separates_repair_rate_from_ehp(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """遥修：EHP 不变（+0），回血速度单独一节 —— 这是最容易误读的地方。"""
    code, out, err = _run_cli(
        [
            "project", "--fit-file", str(PROJECT_TARGET_PATH),
            "--source", str(PROJECT_REPAIR_PATH),
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    assert "EHP " in out and "（+0.0000" in out, "遥修不改 EHP"
    assert "改了回血速度" in out and "不改 EHP" in out
    assert "hp/s" in out, "回血速度要带单位"


def test_project_json_matches_api(capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` 就是 api 信封原样。"""
    code, out, err = _run_cli(
        [
            "project", "--fit-file", str(PROJECT_TARGET_PATH),
            "--source", str(PROJECT_BURST_PATH), "--json",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    envelope = json.loads(out)
    api.clear_caches()
    expected = api.fit_project(
        PROJECT_TARGET_PATH.read_text(encoding="utf-8"),
        [PROJECT_BURST_PATH.read_text(encoding="utf-8")],
    )
    assert envelope == expected


def test_project_missing_source_file_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    """来源文件读不到 → 参数错误（退出码 2），不拿去问引擎。"""
    code, out, err = _run_cli(
        [
            "project", "--fit-file", str(PROJECT_TARGET_PATH),
            "--source", str(PROJECT_FIXTURES / "no-such-file.eft"),
        ],
        capsys,
    )
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "no-such-file" in err


def test_project_bad_source_content_is_api_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """来源**内容**坏 → 退出码 1 + `invalid_argument` + `sources[1]`（能指出第几项）。"""
    code, out, err = _run_cli(
        [
            "project", "--fit-file", str(PROJECT_TARGET_PATH),
            "--source", str(PROJECT_BURST_PATH),
            "--source-eft", "这不是配装",
        ],
        capsys,
    )
    assert code == cli.EXIT_API_FAILURE, err
    assert out == ""
    assert "invalid_argument" in err and "sources[1]" in err


def test_project_no_strict_state_changes_the_numbers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--no-strict-state` 真的换口径：offline 指挥爆发的 buff 会被采信（EHP 变多）。

    不写系统临时目录（沙箱下 `tmp_path` 会 `PermissionError`）；用项目内 gitignore 掉的
    `.tmp/`，跑完删掉。
    """
    fit = api.parse_fit(PROJECT_BURST_PATH.read_text(encoding="utf-8"))["result"]["fit"]
    for item in fit["items"]:
        if item.get("type_id") == 43552:
            item["state"] = "offline"
    scratch = NOOBBAOZI_ROOT / ".tmp"
    scratch.mkdir(exist_ok=True)
    offline_path = scratch / "pytest-offline-burst.json"
    offline_path.write_text(json.dumps(fit, ensure_ascii=False), encoding="utf-8")

    base_args = ["project", "--fit-file", str(PROJECT_TARGET_PATH), "--source", str(offline_path), "--json"]
    code, out, err = _run_cli(base_args, capsys)
    assert code == cli.EXIT_OK, err
    strict = json.loads(out)["result"]
    ehp_strict = next(m for m in strict["delta"]["metrics"] if m["path"] == "survivability.ehp_mixed")

    api.clear_caches()
    code, out, err = _run_cli([*base_args, "--no-strict-state"], capsys)
    assert code == cli.EXIT_OK, err
    lax = json.loads(out)["result"]
    ehp_lax = next(m for m in lax["delta"]["metrics"] if m["path"] == "survivability.ehp_mixed")

    assert strict["strict_state"] is True and lax["strict_state"] is False
    assert ehp_strict["delta"] == 0.0 and ehp_lax["delta"] > 0.0

    offline_path.unlink(missing_ok=True)


def test_project_effects_json_bad_and_unknown(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--effects-json` 顶层坏 → 退出码 2；effect_id 未知 → 退出码 0 + warning。"""
    code, out, err = _run_cli(
        [
            "project", "--fit-file", str(PROJECT_TARGET_PATH),
            "--effects-json", "{not json",
        ],
        capsys,
    )
    assert code == cli.EXIT_USAGE
    assert "--effects-json" in err

    code, out, err = _run_cli(
        [
            "project", "--fit-file", str(PROJECT_TARGET_PATH),
            "--effects-json", '[{"type_id": 527, "effect_id": 999999}]', "--json",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    envelope = json.loads(out)
    assert [w["kind"] for w in envelope["result"]["warnings"]] == ["unknown_effect_id"]


def test_module_entry_point_knows_project() -> None:
    """真起子进程：`python -m noobbaozi.cli project --help` 里有这一组选项。"""
    completed = _subprocess_cli(["project", "--help"])
    assert completed.returncode == 0, completed.stderr
    for token in ("--source", "--source-eft", "--effects-json", "--no-strict-state", "--json"):
        assert token in completed.stdout, f"--help 里应提到 {token}：\n{completed.stdout}"


# =============================================================================
# 九、`engagement`：三块分开印（应用 / 两个血量口径 / 击穿与谁先死）
# =============================================================================

ENGAGEMENT_ATTACKER_PATH = PROJECT_FIXTURES / "f1-hecate-killmail.eft"
ENGAGEMENT_DEFENDER_PATH = PROJECT_FIXTURES / "f15-battleship-raven.json"


def _engagement_args(*extra: str) -> list[str]:
    return [
        "engagement",
        "--fit-file", str(ENGAGEMENT_ATTACKER_PATH),
        "--defender", str(ENGAGEMENT_DEFENDER_PATH),
        "--distance-m", "1000",
        *extra,
    ]


def test_engagement_human_has_three_separate_blocks(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """人话必须分三块：命中/应用/有效 DPS、HP 池 vs EHP、击穿时间与谁先死。"""
    code, out, err = _run_cli(_engagement_args(), capsys)
    assert code == cli.EXIT_OK, err
    assert err == ""
    assert "【命中率 / 应用系数 / 有效 DPS】" in out
    assert "【HP 池 vs EHP（用途不同，别混）】" in out
    assert "【击穿时间 / 谁先死】" in out
    assert "hp_pool" in out and "ehp" in out
    assert "damage_taken" in out, "hp_pool 必须标「适合和战报比」"
    assert "击穿" in out
    assert "|" not in out and "**" not in out, "人话输出不该带 markdown 记号"


def test_engagement_human_unarmed_says_it_cannot_compare(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """一方无武器 → 明确印「无对敌武器，无法对算」，不是把 null 印成 0。"""
    code, out, err = _run_cli(
        [
            "engagement", "--eft", "[Hecate, empty]",
            "--defender", str(ENGAGEMENT_DEFENDER_PATH),
            "--distance-m", "1000",
        ],
        capsys,
    )
    assert code == cli.EXIT_OK, err
    assert "无对敌武器，无法对算" in out
    assert "no_weapons" in out


def test_engagement_json_matches_api(capsys: pytest.CaptureFixture[str]) -> None:
    """`--json` 就是 api 信封原样。"""
    code, out, err = _run_cli([*_engagement_args(), "--json"], capsys)
    assert code == cli.EXIT_OK, err
    envelope = json.loads(out)
    api.clear_caches()
    expected = api.fit_engagement(
        ENGAGEMENT_ATTACKER_PATH.read_text(encoding="utf-8"),
        ENGAGEMENT_DEFENDER_PATH.read_text(encoding="utf-8"),
        distance_m=1000.0,
    )
    assert envelope == expected


def test_engagement_missing_distance_is_usage_error(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--distance-m` 是必填（argparse 用法错误，退出码 2）。"""
    code, out, err = _run_cli(
        [
            "engagement", "--fit-file", str(ENGAGEMENT_ATTACKER_PATH),
            "--defender", str(ENGAGEMENT_DEFENDER_PATH),
        ],
        capsys,
    )
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "--distance-m" in err


def test_engagement_defender_required_and_exclusive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """守方必须给且只能给一个（缺 → 2；两个都给 → 2）。"""
    code, out, err = _run_cli(
        ["engagement", "--fit-file", str(ENGAGEMENT_ATTACKER_PATH), "--distance-m", "1000"],
        capsys,
    )
    assert code == cli.EXIT_USAGE
    assert "--defender" in err

    code, out, err = _run_cli(
        [
            "engagement", "--fit-file", str(ENGAGEMENT_ATTACKER_PATH),
            "--defender", str(ENGAGEMENT_DEFENDER_PATH),
            "--defender-eft", "[Raven, x]",
            "--distance-m", "1000",
        ],
        capsys,
    )
    assert code == cli.EXIT_USAGE
    assert "互斥" in err


def test_engagement_timeline_mode_human(capsys: pytest.CaptureFixture[str]) -> None:
    """`--mode timeline`：输出时间线（速率 + 端点 + 采样）。"""
    code, out, err = _run_cli(_engagement_args("--mode", "timeline", "--points", "3"), capsys)
    assert code == cli.EXIT_OK, err
    assert "时间线" in out
    assert "first_to_die=" in out
    assert out.count("t=") == 3, f"应有 3 个采样点：\n{out}"


def test_engagement_fleet_human_declares_magnitude(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--fleet 8`：人话必须声明「量级估计，不是个体配装」并列出 flags。"""
    code, out, err = _run_cli(
        _engagement_args("--fleet", "8", "--window-s", "9", "--observed-hp", "4158"), capsys
    )
    assert code == cli.EXIT_OK, err
    assert "编队量级" in out and "量级估计，不是个体配装" in out
    assert "order_of_magnitude_only" in out
    assert "量级校验" in out, "给了 observed_hp 就要有量级校验"


def test_engagement_bad_anchor_params_is_api_failure(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--mode anchor` 缺 `--observed-hp`/`--window-s` → 退出码 1 + `invalid_argument`。"""
    code, out, err = _run_cli(_engagement_args("--mode", "anchor"), capsys)
    assert code == cli.EXIT_API_FAILURE, err
    assert out == ""
    assert "invalid_argument" in err


def test_module_entry_point_knows_engagement() -> None:
    """真起子进程：`python -m noobbaozi.cli engagement --help` 里有这一组选项。"""
    completed = _subprocess_cli(["engagement", "--help"])
    assert completed.returncode == 0, completed.stderr
    for token in (
        "--defender", "--defender-eft", "--distance-m", "--angular-velocity",
        "--mode", "--fleet", "--observed-hp", "--window-s", "--json",
    ):
        assert token in completed.stdout, f"--help 里应提到 {token}：\n{completed.stdout}"
