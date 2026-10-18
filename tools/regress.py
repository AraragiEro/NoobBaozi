r"""T-33 全部门禁一键化：`python -m noobbaozi.tools.regress`。

判断「项目健康」原本要手工跑四件事，漏一件就等于没跑（而且没人会记得自己漏了哪件）：

1. `pytest -q noobbaozi/tests`
2. `python -m noobbaozi.tools.check_attr_coverage`（常量覆盖硬门）
3. `python noobbaozi/acceptance/check_snapshot.py --enforce`（回归快照）
4. `python noobbaozi/acceptance/run_acceptance.py`（独立验收，36 样本，较慢）

历史上已经出过「看起来全绿其实全错」：`derive_skill_tier` 静默退化到 19 个技能，
所有数值都跑在旧档上，测试、快照、验收却一路绿灯。所以本工具的默认值是
**全开 + 严格**，而不是「能跑多少算多少」：

- 默认跑 `pytest` / `attr-coverage` / `snapshot` 三门（快速门）；`--full` 追加
  `acceptance`（36 样本，较慢）。默认不跑 acceptance 是**速度**取舍，不是放松：
  报告里会显式写明「本次未纳入 acceptance」，所以「绿」的范围永远可见。
- **一门失败不中止**其余门：逐门执行、收集全部结果，一次看全项目哪里坏了。
- 唯一的例外是**显式声明的依赖**：`acceptance` 依赖 `pytest`。依赖门没通过时
  `acceptance` 标 `SKIPPED` 并写明「因为 pytest 失败」——此时跑它毫无意义，而且
  它慢。
- 退出码：全 PASS → **0**；任一 FAIL → **1**；一项都没真跑（全部 SKIPPED / 空选择）
  → **2**。
- **部分 SKIPPED 也不算绿**：只要有门被跳过而没有任何 FAIL，退出码给 **1**。
  「跳过」是「没验证」，不是「通过」——把 SKIPPED 当绿灯就是把门禁做成安慰剂，
  这正是本项目踩过的坑（见 `tests/test_attr_coverage.py` 里「假绿过一轮」的记录）。

用法：

    python -m noobbaozi.tools.regress                                   # 全部快速门禁
    python -m noobbaozi.tools.regress --full                            # 追加 acceptance
    python -m noobbaozi.tools.regress --gate pytest --gate snapshot     # 只跑指定门
    python -m noobbaozi.tools.regress --json                            # 机器可读（CI / agent）
    python -m noobbaozi.tools.regress --timeout 1200                    # 单门超时（默认 600s）

门禁命令用 `sys.executable`，因此**必须与使用者同一套 python / uv 环境**：

    $env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python -m noobbaozi.tools.regress

`--gate` 写错 id、或参数本身不合法时，argparse 自己以退出码 2 结束（stderr 有 usage）。
这与「全部 SKIPPED → 2」同码，但 stderr 会立刻说明是参数问题。

纪律：只用标准库（`subprocess` / `argparse` / `json`），无网络、无随机；本模块**只读**，
不写任何源文件。被调用的脚本自己会写 `acceptance/out/` 产物（那是它们既有行为，
且按 §11 不提交）。`run_gates` 的 `runner` 可注入，测试用它驱动编排逻辑，
不必真起子进程（见 `tests/test_regress.py`）。
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence

EVE_ROOT: Path = Path(__file__).resolve().parents[1]
"""`noobbaozi/` 目录（本文件在 `noobbaozi/tools/` 下）。"""

WORKSPACE: Path = EVE_ROOT.parent
"""仓库根：所有门禁命令的工作目录（`pytest -q noobbaozi/tests` 这类相对路径都相对它）。"""

DEFAULT_TIMEOUT_S: int = 600
"""单门超时（秒）。超时 → `FAIL` 并在摘要里注明，绝不当作跳过。"""

FAIL_TAIL_LINES: int = 30
"""失败时打印/记录的输出末尾行数（成功时不打印，避免刷屏）。"""

PASS: str = "PASS"
FAIL: str = "FAIL"
SKIPPED: str = "SKIPPED"

EXIT_OK: int = 0
EXIT_FAIL: int = 1
EXIT_ALL_SKIPPED: int = 2


# =============================================================================
# 数据结构
# =============================================================================


@dataclass(frozen=True)
class Gate:
    """一门门禁：id → 命令 → 说明（+ 依赖声明）。

    `command` 是**完整 argv**（含解释器），执行时不再过 shell——这样带引号的参数、
    Windows 路径里的空格都不会被二次解释。
    """

    id: str
    title: str
    command: tuple[str, ...]
    requires: tuple[str, ...] = ()
    full_only: bool = False
    timeout_s: int = DEFAULT_TIMEOUT_S

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "command": list(self.command),
            "requires": list(self.requires),
            "full_only": self.full_only,
            "timeout_s": self.timeout_s,
        }


@dataclass(frozen=True)
class ExecOutcome:
    """一次执行的结果（`runner` 的返回类型，也是测试注入假执行器的契约）。

    - `exit_code is None` 表示「根本没跑起来」（命令不存在 / 执行器异常），必判 FAIL；
    - `timed_out=True` 表示超时被强制结束，必判 FAIL；
    - `output` 是合并后的 stdout+stderr（只在 FAIL 时取尾巴）。
    """

    exit_code: int | None
    output: str = ""
    timed_out: bool = False
    error: str = ""


@dataclass(frozen=True)
class GateResult:
    """一门门禁的结论（汇总表 / JSON 的基本单元）。"""

    gate: Gate
    status: str
    duration_s: float
    summary: str
    exit_code: int | None = None
    output_tail: str = ""
    timed_out: bool = False
    skipped_reason: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.gate.id,
            "title": self.gate.title,
            "command": list(self.gate.command),
            "requires": list(self.gate.requires),
            "status": self.status,
            "exit_code": self.exit_code,
            "duration_s": round(self.duration_s, 3),
            "summary": self.summary,
            "timed_out": self.timed_out,
            "skipped_reason": self.skipped_reason,
            "output_tail": self.output_tail,
        }


Runner = Callable[[Gate, int], ExecOutcome]
"""执行器签名：`(gate, timeout_s) -> ExecOutcome`。

生产用 `subprocess_runner`；测试注入假执行器即可驱动全部编排逻辑而不起子进程。
"""


# =============================================================================
# 门禁清单（id → 命令 → 说明）
# =============================================================================


def build_gates(python: str | None = None) -> tuple[Gate, ...]:
    """门禁清单。用 `sys.executable`，保证子进程和当前解释器/环境一致。

    `python` 参数只是为了测试时能拿到稳定字符串，生产不传。
    """
    py = python or sys.executable or "python"
    return (
        Gate(
            id="pytest",
            title="单元测试（noobbaozi/tests 全量）",
            command=(py, "-m", "pytest", "-q", "noobbaozi/tests", "-p", "no:cacheprovider"),
        ),
        Gate(
            id="attr-coverage",
            title="常量覆盖硬门（FIELD_SOURCES / 裸属性 ID / 常量表自洽）",
            command=(py, "-m", "noobbaozi.tools.check_attr_coverage"),
        ),
        Gate(
            id="snapshot",
            title="回归快照对拍（§6.2 主样本 + 四模式 + fixtures 漂移）",
            command=(py, "noobbaozi/acceptance/check_snapshot.py", "--enforce"),
            # 技能档护栏退化时该脚本退出码 2 → 这里照样判 FAIL（非零即红）
        ),
        Gate(
            id="acceptance",
            title="独立验收（六视角 + I1–I7，36 样本，较慢）",
            command=(py, "noobbaozi/acceptance/run_acceptance.py", "--label", "regress"),
            requires=("pytest",),
            full_only=True,
        ),
    )


GATES: tuple[Gate, ...] = build_gates()
"""默认门禁清单（顺序 = 执行顺序 = 汇总表顺序）。"""

GATE_IDS: tuple[str, ...] = tuple(gate.id for gate in GATES)


# =============================================================================
# 选择：--full / --gate
# =============================================================================


def select_gates(
    ids: Sequence[str] | None = None,
    *,
    full: bool = False,
    gates: Sequence[Gate] | None = None,
) -> tuple[list[Gate], list[str]]:
    """按 `--gate` / `--full` 选出本次要跑的门的**有序**列表，并返回提示语。

    - 不给 `ids`：默认全部非 `full_only` 门；`full=True` 时全给；
    - 给 `ids`：只跑点名的门，**但显式声明的依赖会被自动补进来**（`--gate acceptance`
      会连带 `pytest`）。否则「验收独立通过」会变成「验收在不满足前置的假设下通过」,
      那是假绿；补依赖时返回一条 note，让报告里看得见。
    """
    ordered = tuple(gates if gates is not None else GATES)
    registry = {gate.id: gate for gate in ordered}
    notes: list[str] = []

    if ids:
        wanted: list[str] = []
        for gate_id in ids:
            if gate_id not in wanted:
                wanted.append(gate_id)
        added: list[str] = []
        pending = list(wanted)
        while pending:
            gate = registry[pending.pop()]
            for dep in gate.requires:
                if dep not in wanted:
                    wanted.append(dep)
                    added.append(dep)
                    pending.append(dep)
        if added:
            notes.append("`--gate` 按依赖声明自动补上：" + ", ".join(added))
        selected = [gate for gate in ordered if gate.id in wanted]
        return selected, notes

    selected = [gate for gate in ordered if full or not gate.full_only]
    if not full:
        slow = [gate.id for gate in ordered if gate.full_only]
        if slow:
            notes.append(
                "本次未加 `--full`，慢门未纳入：" + ", ".join(slow)
                + "（结论只覆盖快速门，范围如实写在报告里）"
            )
    return selected, notes


# =============================================================================
# 执行
# =============================================================================


def _as_text(chunk: object) -> str:
    if chunk is None:
        return ""
    if isinstance(chunk, bytes):
        return chunk.decode("utf-8", "replace")
    return str(chunk)


def subprocess_runner(gate: Gate, timeout_s: int) -> ExecOutcome:
    """默认执行器：真起子进程，捕获输出，不打印全文。"""
    env = dict(os.environ)
    # 强制子进程用 UTF-8 输出：否则 Windows 上按 cp936 写、这里按 UTF-8 读 → 乱码
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(  # noqa: S603 - 参数是我们自己写死的 argv，无 shell
            list(gate.command),
            cwd=str(WORKSPACE),
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
        )
    except FileNotFoundError:
        return ExecOutcome(None, error=f"命令不存在：{gate.command[0]}（解释器/脚本路径不可用）")
    except subprocess.TimeoutExpired as exc:
        partial = _as_text(exc.stdout) + _as_text(exc.stderr)
        return ExecOutcome(None, partial, timed_out=True, error=f"超时（>{timeout_s}s 被强制结束）")
    except OSError as exc:
        return ExecOutcome(None, error=f"无法执行：{type(exc).__name__}: {exc}")

    output = _as_text(completed.stdout)
    stderr = _as_text(completed.stderr)
    if stderr:
        output = f"{output}\n{stderr}" if output else stderr
    return ExecOutcome(completed.returncode, output)


def _tail(text: str, lines: int = FAIL_TAIL_LINES) -> str:
    """输出末尾 `lines` 行（去掉纯空行，避免尾巴全被空行占满）。"""
    rows = [line.rstrip() for line in (text or "").splitlines()]
    rows = [line for line in rows if line.strip()]
    return "\n".join(rows[-lines:])


def _hint(output: str, width: int = 110) -> str:
    """一行摘要：取输出最后一条非空行（pytest 是 `N passed in Xs`，其余是结论行）。"""
    for line in reversed((output or "").splitlines()):
        text = line.strip()
        if text:
            return text if len(text) <= width else text[: width - 1] + "…"
    return ""


def _to_result(gate: Gate, outcome: ExecOutcome, duration_s: float, timeout_s: int) -> GateResult:
    """`ExecOutcome` → `GateResult`（状态判定 + 摘要 + 尾巴）。"""
    output_tail = _tail(outcome.output)
    if outcome.timed_out:
        return GateResult(
            gate, FAIL, duration_s, f"超时（>{timeout_s}s 被强制结束）",
            exit_code=outcome.exit_code, output_tail=output_tail, timed_out=True,
        )
    if outcome.exit_code is None:
        return GateResult(
            gate, FAIL, duration_s, outcome.error or "命令未返回退出码",
            exit_code=None, output_tail=output_tail,
        )
    status = PASS if outcome.exit_code == 0 else FAIL
    summary = f"退出码 {outcome.exit_code}"
    hint = _hint(outcome.output)
    if hint:
        summary = f"{summary} · {hint}"
    return GateResult(
        gate, status, duration_s, summary, exit_code=outcome.exit_code,
        output_tail=output_tail if status == FAIL else "",
    )


def run_gates(
    gates: Sequence[Gate],
    *,
    runner: Runner | None = None,
    timeout_s: int | None = None,
    progress: Callable[[str], None] | None = None,
) -> list[GateResult]:
    """逐门执行并**收集**结果（一门失败不中止后面的门）。

    依赖处理：`gate.requires` 里任一依赖已跑且不是 PASS → 本门 `SKIPPED`；
    依赖**根本没在本次选择里**（调用方直接传了残缺列表）→ 同样 `SKIPPED`，
    宁可标「没验证」（退出码非 0）也不假装满足前置。
    """
    execute = runner if runner is not None else subprocess_runner
    results: list[GateResult] = []
    done: dict[str, GateResult] = {}
    total = len(gates)

    for index, gate in enumerate(gates, start=1):
        if progress is not None:
            progress(f"[{index}/{total}] {gate.id} … 执行")
        failed = [dep for dep in gate.requires if dep in done and done[dep].status != PASS]
        missing = [dep for dep in gate.requires if dep not in done]
        if failed or missing:
            parts: list[str] = []
            if failed:
                parts.append("依赖门未通过：" + "、".join(f"{dep}({done[dep].status})" for dep in failed))
            if missing:
                parts.append("依赖门未纳入本次选择：" + "、".join(missing))
            reason = "；".join(parts) + " → 跳过本门（不是通过）"
            result = GateResult(gate, SKIPPED, 0.0, reason, skipped_reason=reason)
        else:
            limit = timeout_s if timeout_s is not None else gate.timeout_s
            started = time.perf_counter()
            try:
                outcome = execute(gate, limit)
            except Exception as exc:  # noqa: BLE001 - 执行器自身炸了也不能让编排器崩
                outcome = ExecOutcome(None, error=f"执行器异常：{type(exc).__name__}: {exc}")
            duration_s = time.perf_counter() - started
            result = _to_result(gate, outcome, duration_s, limit)
        results.append(result)
        done[gate.id] = result
        if progress is not None:
            progress(f"[{index}/{total}] {gate.id} → {result.status}（{_fmt_duration(result.duration_s)}）")
    return results


def count_statuses(results: Sequence[GateResult]) -> dict[str, int]:
    counts = {PASS: 0, FAIL: 0, SKIPPED: 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    return counts


def exit_code_for(results: Sequence[GateResult]) -> int:
    """退出码映射（严格版）。

    - 任一 `FAIL` → 1（最优先：坏了就是坏了）；
    - 一项都没有 / 全部 `SKIPPED` → 2（什么都没验证）；
    - 有 `SKIPPED` 但无 `FAIL` → **1**（部分 SKIPPED 不算绿：跳过 ≠ 通过）；
    - 其余（全 PASS）→ 0。
    """
    if any(result.status == FAIL for result in results):
        return EXIT_FAIL
    if not results:
        return EXIT_ALL_SKIPPED
    if all(result.status == SKIPPED for result in results):
        return EXIT_ALL_SKIPPED
    if any(result.status == SKIPPED for result in results):
        return EXIT_FAIL
    return EXIT_OK


# =============================================================================
# 输出：汇总表 / JSON
# =============================================================================


def _fmt_duration(seconds: float) -> str:
    if seconds <= 0.0:
        return "0.0s"
    if seconds < 60.0:
        return f"{seconds:.1f}s"
    minutes, rest = divmod(seconds, 60.0)
    return f"{int(minutes)}m{rest:04.1f}s"


VERDICT_BY_EXIT: Mapping[int, str] = {
    EXIT_OK: "全绿：所有门禁通过",
    EXIT_FAIL: "不绿：有门禁失败，或有门禁被跳过（跳过不算通过）",
    EXIT_ALL_SKIPPED: "一个门禁都没真跑（全部 SKIPPED / 空选择）",
}


def render_report(
    results: Sequence[GateResult],
    *,
    notes: Sequence[str] = (),
    exit_code: int = EXIT_OK,
    full: bool = False,
) -> str:
    """人看的汇总表（门 id / 状态 / 耗时 / 一行摘要 + 失败尾巴 + 结论）。"""
    counts = count_statuses(results)
    lines: list[str] = []
    lines.append("=" * 96)
    lines.append("noobbaozi 门禁汇总" + ("（--full）" if full else "（快速门）"))
    lines.append("=" * 96)
    for note in notes:
        lines.append(f"  · {note}")
    if notes:
        lines.append("")
    lines.append(f"  {'门 id':<15}{'状态':<9}{'耗时':>8}  摘要")
    lines.append("  " + "-" * 92)
    for result in results:
        lines.append(
            f"  {result.gate.id:<15}{result.status:<9}{_fmt_duration(result.duration_s):>8}"
            f"  {result.summary}"
        )
    lines.append("")
    lines.append(
        f"  PASS {counts[PASS]} / FAIL {counts[FAIL]} / SKIPPED {counts[SKIPPED]}"
        f"（共 {len(results)} 门）"
    )

    for result in results:
        if result.status == FAIL and result.output_tail:
            lines.append("")
            lines.append("-" * 96)
            lines.append(
                f"失败详情：{result.gate.id}（仅末尾 {FAIL_TAIL_LINES} 行；"
                "完整输出请单跑该门）"
            )
            lines.append("-" * 96)
            lines.append(result.output_tail)

    lines.append("")
    lines.append(f"结论：{VERDICT_BY_EXIT.get(exit_code, '未知')}（退出码 {exit_code}）")
    if exit_code != EXIT_OK:
        lines.append(
            "单门复跑：python -m noobbaozi.tools.regress --gate <id>；"
            "或直接执行该门命令看完整输出。"
        )
    return "\n".join(lines)


def build_payload(
    results: Sequence[GateResult],
    *,
    exit_code: int,
    full: bool,
    notes: Sequence[str] = (),
    timeout_s: int = DEFAULT_TIMEOUT_S,
    gates: Sequence[Gate] | None = None,
) -> dict:
    """机器可读结果（CI / agent 消费）。"""
    counts = count_statuses(results)
    return {
        "tool": "noobbaozi.tools.regress",
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "python": sys.executable,
        "workspace": str(WORKSPACE),
        "full": bool(full),
        "timeout_s": timeout_s,
        "notes": list(notes),
        "selected": [result.gate.id for result in results],
        "registry": [gate.as_dict() for gate in (gates if gates is not None else GATES)],
        "gates": [result.as_dict() for result in results],
        "counts": {
            "total": len(results),
            "pass": counts[PASS],
            "fail": counts[FAIL],
            "skipped": counts[SKIPPED],
        },
        "ok": exit_code == EXIT_OK,
        "exit_code": exit_code,
    }


# =============================================================================
# 入口
# =============================================================================

_EPILOG = """\
门禁清单（顺序即执行顺序）：
  pytest          pytest -q noobbaozi/tests -p no:cacheprovider
  attr-coverage   python -m noobbaozi.tools.check_attr_coverage
  snapshot        python noobbaozi/acceptance/check_snapshot.py --enforce
  acceptance      python noobbaozi/acceptance/run_acceptance.py --label regress
                  （仅 --full；声明依赖 pytest，pytest 失败则 SKIPPED）

退出码：0 全 PASS；1 任一 FAIL（或有门被跳过）；2 一项都没真跑。
"""


def main(argv: Sequence[str] | None = None, *, runner: Runner | None = None) -> int:
    """CLI 入口。`runner` 只给测试用（注入假执行器，不真起子进程）。"""
    parser = argparse.ArgumentParser(
        prog="python -m noobbaozi.tools.regress",
        description="noobbaozi 全部门禁一键化（默认全开、默认严格）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="额外跑 acceptance（36 样本，较慢；声明依赖 pytest）",
    )
    parser.add_argument(
        "--gate",
        action="append",
        metavar="ID",
        choices=list(GATE_IDS),
        help=f"只跑指定门（可多次）；可选：{', '.join(GATE_IDS)}",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="输出机器可读 JSON（stdout 只有 JSON）",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_S,
        metavar="SEC",
        help=f"单门超时秒数（默认 {DEFAULT_TIMEOUT_S}；超时判 FAIL）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.timeout <= 0:
        parser.error("--timeout 必须是正数")
    timeout_s = int(args.timeout)

    selected, notes = select_gates(args.gate, full=args.full)
    progress: Callable[[str], None] | None = None
    if not args.json:
        progress = lambda text: print(text, file=sys.stderr, flush=True)  # noqa: E731

    results = run_gates(selected, runner=runner, timeout_s=timeout_s, progress=progress)
    exit_code = exit_code_for(results)

    if args.json:
        payload = build_payload(
            results, exit_code=exit_code, full=args.full, notes=notes, timeout_s=timeout_s,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False))
    else:
        print(render_report(results, notes=notes, exit_code=exit_code, full=args.full))
    return exit_code


if __name__ == "__main__":  # pragma: no cover —— 入口在测试里走 main()
    raise SystemExit(main())
