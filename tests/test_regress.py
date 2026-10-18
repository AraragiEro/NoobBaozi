r"""T-33 守护测试：门禁**编排逻辑**（不真跑门禁）。

为什么要单独立一份测试，而不是「让 regress 自己跑一遍看看」：

1. **真跑全部门禁至少几分钟**（`acceptance` 36 样本），把它塞进 `pytest` 会让每次
   `pytest` 都变成「跑两遍验收」，最后一定有人加 `-k "not regress"` 绕过；
2. 编排逻辑（依赖跳过、退出码、JSON 结构、尾巴截断）**与门禁本身是否通过无关**，
   它需要的是可控的输入，而不是真实的红/绿；
3. 本项目的教训是「看起来全绿其实全错」：如果编排器自己坏了（比如把所有结果都
   记成 PASS、把 SKIPPED 当通过），一个真跑一遍的测试**照样会绿**——所以这里的
   驱动方式是**注入假执行器** `FakeRunner`，并显式断言 `subprocess.run` 从未被调用。

每条断言都带反向验证（非永真）：给同一个假执行器灌一个失败，退出码必须从 0 变 1；
把输出灌到 100 行，尾巴必须只有 30 行；依赖门失败时，`acceptance` 的执行器
**一次都不该被调用**（跳过 ≠ 通过 ≠ 跑了但没测）。

写入范围：本文件 + `evefit/tools/regress.py`。不修改被门禁检查的任何模块。
"""

from __future__ import annotations

import json

import pytest

from evefit.tools import regress as REG

# =============================================================================
# 假执行器
# =============================================================================


class FakeRunner:
    """可注入的假执行器：按 gate id 决定返回，并记录调用。

    `outcomes` 的值可以是 `ExecOutcome`（直接返回）或 `callable(gate, timeout_s)`
    （需要按超时参数变形时用）。没登记的 gate 用 `default`。
    """

    def __init__(self, outcomes: dict | None = None, default: REG.ExecOutcome | None = None):
        self.outcomes = dict(outcomes or {})
        self.default = default if default is not None else REG.ExecOutcome(0, "ok")
        self.calls: list[tuple[str, int]] = []

    def __call__(self, gate: REG.Gate, timeout_s: int) -> REG.ExecOutcome:
        self.calls.append((gate.id, timeout_s))
        outcome = self.outcomes.get(gate.id, self.default)
        if callable(outcome):
            outcome = outcome(gate, timeout_s)
        return outcome

    @property
    def called_ids(self) -> list[str]:
        return [gate_id for gate_id, _ in self.calls]


def gate(gate_id: str) -> REG.Gate:
    """按 id 取真实清单里的门（测试要覆盖生产清单，不能另造一份假清单）。"""
    for candidate in REG.GATES:
        if candidate.id == gate_id:
            return candidate
    raise AssertionError(f"清单里没有门 {gate_id}")


def fast_gates() -> list[REG.Gate]:
    """默认（非 --full）选择出的门。"""
    return REG.select_gates(None)[0]


def run(gates, outcomes=None, default=None) -> tuple[list[REG.GateResult], FakeRunner]:
    runner = FakeRunner(outcomes, default)
    return REG.run_gates(gates, runner=runner), runner


def by_id(results) -> dict[str, REG.GateResult]:
    return {result.gate.id: result for result in results}


# =============================================================================
# 1. 门禁清单完整性
# =============================================================================


def test_registry_contains_the_four_required_gates():
    """四件事必须都在清单里：pytest / attr-coverage / snapshot / acceptance。"""
    ids = [item.id for item in REG.GATES]
    assert ids == list(REG.GATE_IDS)
    assert {"pytest", "attr-coverage", "snapshot", "acceptance"} <= set(ids)
    assert len(ids) == len(set(ids)), "门 id 不能重复（--gate 依赖 id 定位）"


def test_every_gate_is_well_formed():
    """每门都要有说明、有可执行 argv、有正数超时（空壳门禁等于没有门禁）。"""
    for item in REG.GATES:
        assert item.id.strip() and item.title.strip()
        assert len(item.command) >= 2, f"{item.id} 的命令不完整：{item.command}"
        assert all(isinstance(part, str) and part for part in item.command)
        assert item.timeout_s > 0
        for dependency in item.requires:
            assert dependency in REG.GATE_IDS, f"{item.id} 声明了不存在的依赖 {dependency}"


def test_gate_commands_are_the_documented_ones():
    """命令必须就是文档里那几条（尤其 snapshot 必须带 --enforce，不能是软检查）。"""
    pytest_gate = gate("pytest")
    assert "evefit/tests" in pytest_gate.command
    assert "pytest" in pytest_gate.command
    assert "no:cacheprovider" in pytest_gate.command

    assert gate("attr-coverage").command[-1] == "evefit.tools.check_attr_coverage"
    assert gate("snapshot").command[-2:] == ("evefit/acceptance/check_snapshot.py", "--enforce")
    assert "evefit/acceptance/run_acceptance.py" in gate("acceptance").command


def test_snapshot_gate_is_enforced_not_soft():
    """`check_snapshot.py` 默认是 skip 语义，只有 --enforce 才是真门禁——必须钉住。"""
    assert "--enforce" in gate("snapshot").command
    assert "--strict" not in gate("snapshot").command  # 没有「放宽」这类参数


def test_default_timeout_is_600_seconds():
    assert REG.DEFAULT_TIMEOUT_S == 600
    assert gate("pytest").timeout_s == 600
    assert gate("snapshot").timeout_s == 600


def test_acceptance_declares_pytest_dependency_and_is_full_only():
    acceptance = gate("acceptance")
    assert acceptance.requires == ("pytest",)
    assert acceptance.full_only is True
    for gate_id in ("pytest", "attr-coverage", "snapshot"):
        assert gate(gate_id).full_only is False


# =============================================================================
# 2. 选择：默认快跑 / --full / --gate（含依赖自动补齐）
# =============================================================================


def test_default_selection_excludes_acceptance():
    selected, notes = REG.select_gates(None)
    assert [item.id for item in selected] == ["pytest", "attr-coverage", "snapshot"]
    assert "acceptance" not in [item.id for item in selected]
    # 默认不跑慢门是速度取舍，必须写进报告：绿的范围要可见
    assert any("acceptance" in note for note in notes)


def test_full_selection_appends_acceptance():
    selected, notes = REG.select_gates(None, full=True)
    assert [item.id for item in selected] == [
        "pytest", "attr-coverage", "snapshot", "acceptance",
    ]
    assert notes == []


def test_gate_selection_expands_declared_dependencies():
    """`--gate acceptance` 必须连带 pytest：否则「验收通过」是在不满足前置的假设下通过的。"""
    selected, notes = REG.select_gates(["acceptance"])
    assert [item.id for item in selected] == ["pytest", "acceptance"]
    assert any("pytest" in note for note in notes)


def test_gate_selection_keeps_registry_order():
    selected, _ = REG.select_gates(["snapshot", "pytest"])
    assert [item.id for item in selected] == ["pytest", "snapshot"]


def test_gate_selection_deduplicates_repeated_ids():
    selected, _ = REG.select_gates(["pytest", "pytest"])
    assert [item.id for item in selected] == ["pytest"]


def test_unknown_gate_id_is_rejected_by_argparse(capsys):
    with pytest.raises(SystemExit) as excinfo:
        REG.main(["--gate", "does-not-exist"], runner=FakeRunner())
    assert excinfo.value.code == 2
    assert "does-not-exist" in capsys.readouterr().err


# =============================================================================
# 3. 依赖声明：前置 FAIL → 本门 SKIPPED（且真的没跑）
# =============================================================================


def test_failed_pytest_skips_acceptance_and_never_runs_it():
    results, runner = run(list(REG.GATES), {"pytest": REG.ExecOutcome(1, "1 failed")})
    results_by_id = by_id(results)

    assert results_by_id["pytest"].status == REG.FAIL
    assert results_by_id["acceptance"].status == REG.SKIPPED
    assert "pytest" in results_by_id["acceptance"].skipped_reason
    assert "FAIL" in results_by_id["acceptance"].skipped_reason

    # 跳过 = 没跑：执行器一次都不该被用来跑 acceptance（否则就是在浪费几分钟）
    assert "pytest" in runner.called_ids
    assert "acceptance" not in runner.called_ids

    assert REG.exit_code_for(results) == REG.EXIT_FAIL


def test_one_failure_does_not_abort_the_remaining_gates():
    """收集式执行：pytest 红了，后面的门照跑（一次看全项目哪里坏）。"""
    results, runner = run(list(REG.GATES), {"pytest": REG.ExecOutcome(1, "boom")})
    results_by_id = by_id(results)

    assert len(results) == len(REG.GATES)
    assert results_by_id["attr-coverage"].status == REG.PASS
    assert results_by_id["snapshot"].status == REG.PASS
    assert runner.called_ids == ["pytest", "attr-coverage", "snapshot"]


def test_acceptance_runs_when_pytest_passes():
    results, runner = run(list(REG.GATES))
    assert by_id(results)["acceptance"].status == REG.PASS
    assert runner.called_ids == [item.id for item in REG.GATES]


def test_acceptance_without_pytest_in_selection_is_skipped_not_green():
    """依赖门不在本次选择里 → 不能假装前置满足：标 SKIPPED，不跑。"""
    results, runner = run([gate("acceptance")])
    assert results[0].status == REG.SKIPPED
    assert "未纳入本次选择" in results[0].skipped_reason
    assert runner.called_ids == []
    assert REG.exit_code_for(results) == REG.EXIT_ALL_SKIPPED


# =============================================================================
# 4. 退出码映射（全 PASS→0 / 有 FAIL→1 / 全 SKIPPED→2）
# =============================================================================


def test_all_pass_maps_to_exit_zero():
    results, _ = run(fast_gates())
    assert {result.status for result in results} == {REG.PASS}
    assert REG.exit_code_for(results) == REG.EXIT_OK


def test_any_fail_maps_to_exit_one():
    results, _ = run(fast_gates(), {"snapshot": REG.ExecOutcome(1, "↔ DIFF")})
    assert by_id(results)["snapshot"].status == REG.FAIL
    assert REG.exit_code_for(results) == REG.EXIT_FAIL


def test_all_skipped_maps_to_exit_two():
    results, _ = run([gate("acceptance")])
    assert {result.status for result in results} == {REG.SKIPPED}
    assert REG.exit_code_for(results) == REG.EXIT_ALL_SKIPPED


def test_empty_selection_maps_to_exit_two():
    """什么都没选 = 什么都没验，不能读成 0。"""
    assert REG.exit_code_for([]) == REG.EXIT_ALL_SKIPPED


def test_partial_skip_is_not_green():
    """部分 SKIPPED 不能当绿：跳过 ≠ 通过。"""
    results, _ = run([gate("acceptance"), gate("pytest")])  # 依赖还没跑 → 跳过；pytest 通过
    assert [result.status for result in results] == [REG.SKIPPED, REG.PASS]
    assert REG.exit_code_for(results) == REG.EXIT_FAIL


def test_failure_changes_exit_code_a_fake_runner_proves_assertion_is_not_vacuous():
    """非永真自检：同一批门，灌一个失败，退出码必须 0 → 1。"""
    green, _ = run(list(REG.GATES))
    red, _ = run(list(REG.GATES), {"attr-coverage": REG.ExecOutcome(1, "硬门缺口")})
    assert REG.exit_code_for(green) == REG.EXIT_OK
    assert REG.exit_code_for(red) == REG.EXIT_FAIL


# =============================================================================
# 5. 执行器异常 / 超时 / 命令不存在：一律 FAIL，不许变成通过
# =============================================================================


def test_timeout_marks_gate_failed_and_says_so():
    results, _ = run(list(REG.GATES), {"pytest": REG.ExecOutcome(None, "partial", timed_out=True)})
    pytest_result = by_id(results)["pytest"]
    assert pytest_result.status == REG.FAIL
    assert pytest_result.timed_out is True
    assert "超时" in pytest_result.summary
    assert REG.exit_code_for(results) == REG.EXIT_FAIL


def test_timeout_override_is_plumbed_into_the_runner():
    runner = FakeRunner()
    REG.run_gates(list(REG.GATES), runner=runner, timeout_s=42)
    assert runner.calls and all(timeout == 42 for _, timeout in runner.calls)


def test_missing_command_marks_fail_with_reason():
    results, _ = run(
        list(REG.GATES),
        {"snapshot": REG.ExecOutcome(None, error="命令不存在：python（解释器路径不可用）")},
    )
    snapshot_result = by_id(results)["snapshot"]
    assert snapshot_result.status == REG.FAIL
    assert snapshot_result.exit_code is None
    assert "命令不存在" in snapshot_result.summary
    assert REG.exit_code_for(results) == REG.EXIT_FAIL


def test_runner_exception_becomes_fail_not_a_crash():
    def boom(_gate, _timeout_s):
        raise RuntimeError("kaboom")

    results = REG.run_gates([gate("pytest")], runner=boom)
    assert results[0].status == REG.FAIL
    assert "执行器异常" in results[0].summary
    assert "kaboom" in results[0].summary


def test_nonzero_exit_codes_are_all_fail():
    for code in (1, 2, 3, 127):
        results, _ = run([gate("snapshot")], {"snapshot": REG.ExecOutcome(code, "nope")})
        assert results[0].status == REG.FAIL, f"退出码 {code} 必须是 FAIL"
        assert results[0].exit_code == code


# =============================================================================
# 6. 输出：尾巴 30 行 / 汇总表 / JSON
# =============================================================================


def test_fail_tail_is_only_the_last_30_lines():
    output = "\n".join(f"L{index:03d}" for index in range(1, 101))
    results, _ = run([gate("snapshot")], {"snapshot": REG.ExecOutcome(1, output)})
    tail = results[0].output_tail.splitlines()
    assert len(tail) == REG.FAIL_TAIL_LINES == 30
    assert tail[0] == "L071"
    assert tail[-1] == "L100"


def test_pass_output_is_not_stored_as_tail():
    """成功时不保留输出（避免刷屏/JSON 膨胀），只留一行摘要。"""
    output = "\n".join(f"L{index:03d}" for index in range(1, 101))
    results, _ = run(fast_gates(), default=REG.ExecOutcome(0, output))
    assert all(result.output_tail == "" for result in results)
    assert all(result.status == REG.PASS for result in results)


def test_report_contains_a_row_per_gate_with_status_and_duration():
    results, _ = run(list(REG.GATES), {"pytest": REG.ExecOutcome(1, "1 failed")})
    report = REG.render_report(results, notes=["note-here"], exit_code=REG.EXIT_FAIL)

    for item in REG.GATES:
        assert item.id in report
    assert "note-here" in report
    assert "PASS 2 / FAIL 1 / SKIPPED 1" in report  # pytest 红 → acceptance 跳过
    assert "失败详情：pytest" in report
    assert "1 failed" in report
    assert "退出码 1" in report
    assert "结论：不绿" in report


def test_report_does_not_print_full_output_of_passing_gates():
    output = "\n".join(f"L{index:03d}" for index in range(1, 101))
    results, _ = run(fast_gates(), default=REG.ExecOutcome(0, output))
    report = REG.render_report(results, exit_code=REG.EXIT_OK)
    assert "L099" not in report          # 只留最后一行摘要（L100），不打印全文
    assert "失败详情" not in report
    assert "结论：全绿" in report


def test_json_payload_has_the_documented_shape(capsys):
    runner = FakeRunner({"snapshot": REG.ExecOutcome(3, "boom")})
    code = REG.main(["--json", "--gate", "snapshot"], runner=runner)
    captured = capsys.readouterr()

    payload = json.loads(captured.out)   # stdout 必须只有 JSON（能整体解析）
    assert payload["tool"] == "evefit.tools.regress"
    assert payload["full"] is False
    assert payload["selected"] == ["snapshot"]
    assert payload["exit_code"] == REG.EXIT_FAIL == code
    assert payload["ok"] is False
    assert payload["counts"] == {"total": 1, "pass": 0, "fail": 1, "skipped": 0}
    assert payload["timeout_s"] == REG.DEFAULT_TIMEOUT_S
    assert len(payload["registry"]) == len(REG.GATES)

    entry = payload["gates"][0]
    for key in (
        "id", "title", "command", "requires", "status", "exit_code",
        "duration_s", "summary", "timed_out", "skipped_reason", "output_tail",
    ):
        assert key in entry, f"JSON 缺字段 {key}"
    assert entry["id"] == "snapshot"
    assert entry["status"] == REG.FAIL
    assert entry["exit_code"] == 3
    assert entry["output_tail"] == "boom"
    assert isinstance(entry["command"], list) and entry["command"]
    assert captured.err == ""            # --json 模式下没有人类输出污染 stderr


def test_json_all_pass_is_ok_true(capsys):
    code = REG.main(["--json"], runner=FakeRunner())
    payload = json.loads(capsys.readouterr().out)
    assert code == REG.EXIT_OK
    assert payload["ok"] is True
    assert payload["counts"]["pass"] == 3
    assert [entry["status"] for entry in payload["gates"]] == [REG.PASS] * 3


def test_json_gate_acceptance_pulls_pytest_in_and_reports_it(capsys):
    """CLI 层也验证依赖补齐：JSON 的 selected 必须含 pytest，且它先跑。"""
    runner = FakeRunner()
    code = REG.main(["--json", "--gate", "acceptance"], runner=runner)
    payload = json.loads(capsys.readouterr().out)

    assert payload["selected"] == ["pytest", "acceptance"]
    assert runner.called_ids == ["pytest", "acceptance"]
    assert any("pytest" in note for note in payload["notes"])
    assert code == REG.EXIT_OK


# =============================================================================
# 7. CLI 接线：--full 真的把 acceptance 加进来；失败时打印汇总表与尾巴
# =============================================================================


def test_full_run_actually_invokes_acceptance(capsys):
    """`--full` 不能只是「报告里写了」——执行器必须真的被调用到 acceptance。"""
    runner = FakeRunner()
    code = REG.main(["--full", "--json"], runner=runner)
    payload = json.loads(capsys.readouterr().out)

    assert payload["full"] is True
    assert payload["selected"] == ["pytest", "attr-coverage", "snapshot", "acceptance"]
    assert runner.called_ids == payload["selected"]
    assert code == REG.EXIT_OK


def test_failure_run_prints_table_and_tail_with_progress_on_stderr(capsys):
    runner = FakeRunner({"pytest": REG.ExecOutcome(1, "E   assert 1 == 2")})
    code = REG.main([], runner=runner)
    captured = capsys.readouterr()

    assert code == REG.EXIT_FAIL
    assert "门禁汇总" in captured.out
    assert "失败详情：pytest" in captured.out
    assert "E   assert 1 == 2" in captured.out
    # 进度条走 stderr，stdout 只有汇总表（便于管道 / 日志分离）
    assert "[1/3] pytest" in captured.err
    assert "[1/3] pytest" not in captured.out


def test_no_real_subprocess_is_spawned(monkeypatch, capsys):
    """注入的 runner 必须完全替代子进程：一真跑就不是「编排逻辑测试」了。"""

    def forbidden(*_args, **_kwargs):
        raise AssertionError("测试不得真起子进程")

    monkeypatch.setattr(REG.subprocess, "run", forbidden)

    code = REG.main(["--json"], runner=FakeRunner())
    assert code == REG.EXIT_OK
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_default_runner_is_the_subprocess_runner():
    """不注入时必须用真执行器（默认严格，不是空跑）。"""
    results = REG.run_gates([], runner=None)
    assert results == []
    assert REG.subprocess_runner is not None and callable(REG.subprocess_runner)
