"""L5 传输形态之一：`evefit/cli.py` 的契约测试（文档 §10.5「人工验证的基线」）。

钉住四件事：

1. **退出码三档**：`ok:true` → `0`、`ok:false` → `1`、参数 / IO 错误 → `2`
   （配装内容有问题**不是**参数错误，走 `1` + 结构化错误）；
2. **`--json` 可以被 `json.loads`**，且内容就是 `evefit.api` 返回值的原样
   （CLI 与 MCP 共用同一个函数层：这里顺手与 `api.fit_get` 对数）；
3. **人话输出不换算**：打印出来的数字与 `json.dumps(api 的值)` 逐字符相同；
4. **`python -m evefit.cli` 真的能跑**（子进程，含 `--fit-file -` 从 stdin 读）。

绝大多数断言用**进程内** `cli.main([...])`（快、能 `capsys` 抓输出）；
另留两条**子进程**测试，专门验 `python -m` 这个入口本身（argv / stdin / 退出码）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from evefit import api, cli

EVEFIT_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = EVEFIT_ROOT.parent
EFT_PATH = EVEFIT_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"
KILL_PATH = EVEFIT_ROOT / "spike" / "kill.json"


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
    """子进程跑 `python -m evefit.cli`（工作目录 = 仓库根，`-m` 靠 cwd 找到 evefit 包）。"""
    return subprocess.run(
        [sys.executable, "-m", "evefit.cli", *argv],
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
# 四、`python -m evefit.cli` 入口本身（子进程）
# =============================================================================


def test_module_entry_point_json() -> None:
    """真起一个子进程跑 `python -m evefit.cli index --json`（退出码 + 可解析）。"""
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
