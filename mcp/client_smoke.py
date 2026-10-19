"""**真实 MCP 客户端**冒烟：起 `python -m noobbaozi.mcp_server` 子进程，走完整 stdio 流程。

这是 T-46 的**验收关键**：不是「有代码」，而是「有客户端真的连上、逐个调通」。

```
python -m noobbaozi.mcp.client_smoke          # auto：装了官方 SDK 就用它，否则用手写客户端
python -m noobbaozi.mcp.client_smoke --sdk    # 强制官方 SDK（mcp 包）
python -m noobbaozi.mcp.client_smoke --stdio  # 强制手写 stdlib 客户端（零第三方依赖）
```

为什么要两种客户端
------------------

验收命令给的环境是 `uv run --with "eveshipfit-dogma-engine[sde]==13.6.0"`，**里面没有
`mcp`**（实测 `import mcp` → `ModuleNotFoundError`）。所以：

- `stdio` 客户端：**只用标准库** `subprocess` + `json` + `threading`，在任何环境都能跑
  —— 它才是验收路径上的那一个；
- `sdk` 客户端：装了官方 `mcp` 包时优先用它（`ClientSession` + `stdio_client`），
  好处是「协议实现本身」被第二套独立代码验证一遍；没装就跳过，不算失败。

两个客户端做的事完全一样：`initialize` → `notifications/initialized` → `tools/list`
→ 依次跑**用例表**（`build_cases()`：每个配装工具一次 + 两个负例），打印每个调用的摘要
与耗时，最后给 `PASS` / `FAIL`。**用例表覆盖多少条与「注册了多少个工具」是两件事**：
`tools/list` 的条数以服务返回为准（`zkb_search` 要联网，故意不进这张离线用例表）。

顺带守住的一条硬约束
--------------------

手写客户端的读循环对**每一行**都要求「能 `json.loads` 成带 `jsonrpc` 的对象」，否则
直接抛 `ProtocolError` 并打印那一行 —— 这就是「stdout 只允许输出 JSON-RPC」的运行时
断言：服务端只要混进一行日志，冒烟立刻变红，而不是等到某个客户端偶发解析失败。
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

CLIENT_NAME: str = "noobbaozi-client-smoke"
CLIENT_VERSION: str = "1.0.0"

PROTOCOL_VERSION: str = "2025-06-18"
"""冒烟会话请求的协议版本。

选它而不是最新的 `2026-07-28`：`2025-06-18` 是**第一个带 `structuredContent`** 的版本，
既验证了结构化返回（新客户端要的东西），又落在「本服务声称支持的全部版本」区间里。
更老的 `2024-11-05`（无 `structuredContent`）在 `tests/test_mcp_server.py` 里用**进程内
`dispatch`** 覆盖 —— 那条路径不需要再起一个子进程。
"""

DEFAULT_TIMEOUT_S: float = 300.0
"""单条响应 / 单次工具调用的超时（首次调用要开 SDE 索引，实测 100–300ms；300s 极宽松）。"""

NOOBBAOZI_ROOT: Path = Path(__file__).resolve().parents[1]
"""`noobbaozi/` 包目录（`noobbaozi/mcp/client_smoke.py` 往上两级）。"""

WORKSPACE_ROOT: Path = NOOBBAOZI_ROOT.parent
"""仓库根：`python -m noobbaozi.mcp_server` 的 cwd（`-m` 靠 cwd 找到 `noobbaozi` 包）。"""

EFT_PATH: Path = NOOBBAOZI_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"
"""验收固定样本：Hecate killmail 的 EFT 文本（api / cli 测试同源）。"""

TYPO_EFT: str = "[Hecate, typo]\nDamage Controll II\n"
"""负例样本：`Damage Controll` 少一个 `o`（应得 `unknown_module` + candidates）。"""

COMMAND_BURST_EFT: str = (
    "[Absolution, CmdBurst]\n\nArmor Command Burst II, Armor Energizing Charge\n"
)
"""投射样本：Absolution 带一件**装了弹头**的装甲指挥爆发（EFT 里弹头写在模块行逗号后）。

为什么必须带弹头：引擎的 `outgoing.buffs` 只看「模块在 + 弹头在」（见
`project/projection.py` 模块 docstring）；不写弹头那条会得到 `no_outgoing`。
"""


class ProtocolError(RuntimeError):
    """对端（服务）违反协议：stdout 出现了非 JSON-RPC 的行 / 响应 id 对不上 / 提前 EOF。"""


# =============================================================================
# 一、记录结构
# =============================================================================


class CallRecord:
    """一次 `tools/call` 的观测结果（两个客户端产出的形状完全一样）。"""

    __slots__ = (
        "name",
        "tool",
        "elapsed_ms",
        "envelope",
        "is_error",
        "structured",
        "error",
    )

    def __init__(self, name: str, tool: str) -> None:
        self.name = name
        self.tool = tool
        self.elapsed_ms: float = 0.0
        self.envelope: dict | None = None
        self.is_error: bool = False
        self.structured: dict | None = None
        self.error: str | None = None


class SessionInfo:
    """一次连接的握手信息（打印 + 断言用）。"""

    __slots__ = ("transport", "protocol_version", "server", "tools")

    def __init__(self, transport: str) -> None:
        self.transport = transport
        self.protocol_version: str | None = None
        self.server: dict = {}
        self.tools: list[str] = []


# =============================================================================
# 二、样本与断言（**用例表就是这个脚本的验收内容**）
# =============================================================================


def _require(condition: object, message: str) -> None:
    """断言（抛 `AssertionError`，消息里带用例名由调用方拼好）。"""
    if not condition:
        raise AssertionError(message)


def _eft_text() -> str:
    """读 f1 样本（UTF-8）。"""
    return EFT_PATH.read_text(encoding="utf-8")


def _check_parse(envelope: dict) -> None:
    """`fit_parse`：读懂了 —— EFT / Hecate / 有已装模块 / 技能档有实际条数。"""
    _require(envelope.get("ok") is True, "fit_parse 应 ok:true")
    result = envelope.get("result") or {}
    _require(
        result.get("source_format") == "eft",
        f"source_format 应为 eft，实际 {result.get('source_format')!r}",
    )
    ship = result.get("ship") or {}
    _require(ship.get("name") == "Hecate", f"船名应为 Hecate，实际 {ship.get('name')!r}")
    _require(ship.get("type_id") == 35683, f"type_id 应为 35683，实际 {ship.get('type_id')!r}")
    modules = result.get("modules") or {}
    _require(modules.get("fitted", 0) > 0, "已装模块数应 > 0")
    _require(isinstance(modules.get("by_slot"), dict), "modules.by_slot 应为对象")
    skills = result.get("skills") or {}
    _require(skills.get("count", 0) > 0, "技能条数应 > 0")
    _require(
        skills.get("name") == "all4+min_required",
        f"技能档应回显 all4+min_required，实际 {skills.get('name')!r}",
    )
    _require(isinstance(result.get("warnings"), list), "warnings 应为列表")
    _require(isinstance(result.get("fit"), dict), "result.fit 应是引擎结构对象")


def _check_index(envelope: dict) -> None:
    """`fit_index`：L0 六维度齐全 + flags / budget + meta 回显实际技能档与输入形态。"""
    _require(envelope.get("ok") is True, "fit_index 应 ok:true")
    result = envelope.get("result") or {}
    for view in ("firepower", "survivability", "mobility", "resources", "constraints", "stability"):
        _require(view in result, f"L0 缺视角 {view}")
    _require(isinstance(result.get("flags"), list), "L0 应带 flags 列表")
    budget = result.get("budget") or {}
    _require(isinstance(budget, dict) and "chars" in budget, "L0 应带 budget.chars")
    meta = envelope.get("meta") or {}
    tier = meta.get("skill_tier") or {}
    _require(
        tier.get("name") == "all4+min_required",
        f"meta.skill_tier.name 应回显实际档名，实际 {tier.get('name')!r}",
    )
    _require(tier.get("count", 0) > 0, "meta.skill_tier.count 应 > 0")
    _require(meta.get("source_format") == "eft", "meta.source_format 应为 eft")
    _require(meta.get("damage_profile") == "uniform", "meta.damage_profile 应为 uniform")


def _check_view(envelope: dict) -> None:
    """`fit_view(mobility)`：L1 五件套齐全，overview 里有速度。"""
    _require(envelope.get("ok") is True, "fit_view 应 ok:true")
    result = envelope.get("result") or {}
    overview = result.get("overview") or {}
    _require(isinstance(overview, dict) and overview, "view.overview 应为非空对象")
    _require("speed_mps" in overview, f"mobility.overview 应有 speed_mps，实际键 {list(overview)}")
    for key in ("provenance", "unknown_reasons", "confidence", "flags"):
        _require(key in result, f"L1 缺 {key}")
    _require(isinstance(result.get("provenance"), dict), "provenance 应是三态表")
    _require(all(v in ("measured", "assumed", "unknown") for v in result["provenance"].values()),
             "provenance 的取值只能是 measured / assumed / unknown")


def _check_get(envelope: dict) -> None:
    """`fit_get(mobility.speed_mps)`：拿到数值 + 三态 + 来源视角。"""
    _require(envelope.get("ok") is True, "fit_get 应 ok:true")
    result = envelope.get("result") or {}
    _require(result.get("path") == "mobility.speed_mps", "result.path 应回显请求路径")
    _require(
        isinstance(result.get("value"), (int, float)) and not isinstance(result.get("value"), bool),
        f"speed_mps 应是数值，实际 {result.get('value')!r}",
    )
    _require(result.get("value") > 0, f"speed_mps 应 > 0，实际 {result.get('value')!r}")
    _require(
        result.get("provenance") in ("measured", "assumed", "unknown"),
        f"provenance 不合法：{result.get('provenance')!r}",
    )
    _require(result.get("source_view") == "mobility", "source_view 应为 mobility")


def _check_detail(envelope: dict) -> None:
    """`fit_detail(stability)`：只有这一个视角 + 完整技能表在 `meta.assumptions`。"""
    _require(envelope.get("ok") is True, "fit_detail 应 ok:true")
    result = envelope.get("result") or {}
    views = result.get("views") or {}
    _require(list(views) == ["stability"], f"views 应只含 stability，实际 {list(views)}")
    entry = views["stability"]
    _require(isinstance(entry.get("overview"), (dict, list)), "detail 条目应有 overview")
    for key in ("provenance", "unknown_reasons", "confidence", "flags", "missing"):
        _require(key in entry, f"detail 条目缺 {key}")
    assumptions = (envelope.get("meta") or {}).get("assumptions") or {}
    tier = assumptions.get("skill_tier") or {}
    _require(
        len(tier) > 400,
        f"完整技能表应很大（实测 512 条），实际 {len(tier)} 条",
    )


def _check_explain(envelope: dict) -> None:
    """`fit_explain(survivability.ehp_mixed)`：A 类可加，**各项之和 == value**（全精度）。"""
    _require(envelope.get("ok") is True, "fit_explain 应 ok:true")
    result = envelope.get("result") or {}
    _require(
        result.get("metric") == "survivability.ehp_mixed",
        f"metric 应回显请求值，实际 {result.get('metric')!r}",
    )
    _require(result.get("combine") == "additive", "ehp_mixed 是可加类（combine=additive）")
    sources = result.get("sources")
    _require(isinstance(sources, list) and sources, "sources 应为非空列表")
    contributions = [
        entry.get("contribution")
        for entry in sources
        if entry.get("contribution") is not None
    ]
    _require(contributions, "可加类必须给出 contribution")
    total = sum(contributions)
    value = result.get("value")
    _require(
        isinstance(value, (int, float))
        and abs(total - value) <= max(1e-9 * abs(value), 1e-9),
        f"可加类 Σ contribution 应等于 value（Σ={total!r} / value={value!r}）",
    )
    _require(
        result.get("sum_of_contributions") is not None,
        "可加类应带 sum_of_contributions",
    )


def _check_sensitivity(envelope: dict) -> None:
    """`fit_sensitivity(resources.cpu.free)`：每条 change 的 `delta` 必须 = `value - baseline`。"""
    _require(envelope.get("ok") is True, "fit_sensitivity 应 ok:true")
    result = envelope.get("result") or {}
    _require(
        result.get("metric") == "resources.cpu.free",
        f"metric 应回显请求值，实际 {result.get('metric')!r}",
    )
    baseline = result.get("baseline")
    _require(isinstance(baseline, (int, float)), "baseline 应是数值")
    changes = result.get("changes")
    _require(isinstance(changes, list) and changes, "changes 应为非空列表")
    for change in changes:
        op = change.get("op") or {}
        _require(op.get("kind"), f"每条 change 必须带 op.kind：{change!r}")
        value = change.get("value")
        delta = change.get("delta")
        _require(isinstance(value, (int, float)) and isinstance(delta, (int, float)), "value/delta 应是数值")
        _require(
            abs((value - baseline) - delta) <= max(1e-9 * abs(value), 1e-9),
            f"delta 必须等于 value - baseline（delta={delta!r} / value={value!r} / baseline={baseline!r}）",
        )
    _require(
        result.get("ops_executed") == len(changes),
        "ops_executed 必须等于真的跑出来的条数",
    )


def _check_project(envelope: dict) -> None:
    """`fit_project(hecate ← 装甲指挥爆发)`：EHP 真的变了、来源行与 buffs 都在。"""
    _require(envelope.get("ok") is True, "fit_project 应 ok:true")
    result = envelope.get("result") or {}
    _require(result.get("strict_state") is True, "默认必须 strict_state=True")
    sources = result.get("sources")
    _require(isinstance(sources, list) and sources, "sources 行不能为空")
    _require(sources[0].get("label"), "来源行必须带 label（谁给的）")
    applied = result.get("applied") or {}
    _require(applied.get("buffs"), "装甲指挥爆发必须产出投射 buff")

    metrics = (result.get("delta") or {}).get("metrics") or []
    _require(metrics, "delta.metrics 不能为空")
    ehp = next(
        (entry for entry in metrics if entry.get("path") == "survivability.ehp_mixed"),
        None,
    )
    _require(ehp is not None, "delta.metrics 必须含 survivability.ehp_mixed")
    _require(
        isinstance(ehp.get("before"), (int, float)) and isinstance(ehp.get("after"), (int, float)),
        f"EHP 前后都应是数值：{ehp!r}",
    )
    _require(ehp["after"] > ehp["before"], "装甲指挥爆发应让目标 EHP 变大")
    _require(
        result.get("repaired") is not None,
        "repaired 块必须存在（遥修落点；这条样本里它是 0 变化，但形状要在）",
    )


def _check_invalid_path(envelope: dict) -> None:
    """负例①：路径不存在 → 业务失败 `invalid_path` + 可用前缀 `hint`。"""
    _require(envelope.get("ok") is False, "不存在的路径必须 ok:false（不能用 value:null 冒充）")
    error = envelope.get("error") or {}
    _require(
        error.get("kind") == "invalid_path",
        f"error.kind 应为 invalid_path，实际 {error.get('kind')!r}",
    )
    _require(error.get("hint"), "invalid_path 必须给 hint（可用前缀列表）")
    _require(error.get("message"), "invalid_path 必须给 message")


def _check_unknown_module(envelope: dict) -> None:
    """负例②：模块名拼错 → `unknown_module` + `candidates`（只报候选，不代填）。"""
    _require(envelope.get("ok") is False, "模块名拼错必须 ok:false")
    error = envelope.get("error") or {}
    _require(
        error.get("kind") == "unknown_module",
        f"error.kind 应为 unknown_module，实际 {error.get('kind')!r}",
    )
    candidates = error.get("candidates")
    _require(isinstance(candidates, list) and candidates, "unknown_module 必须给 candidates")
    for candidate in candidates:
        _require(isinstance(candidate, dict), f"candidate 应是对象，实际 {candidate!r}")
        _require(candidate.get("name"), f"candidate 必须有 name：{candidate!r}")
    _require(error.get("raw"), "unknown_module 应带 raw（用户写错的那一行）")


def build_cases() -> tuple[dict, ...]:
    """冒烟用例表：**七个本地可算的配装工具各一次 + 两个负例**（顺序就是推荐调用顺序）。

    `fit_killmail` / `zkb_*` 要联网，不在表里（各自的契约测试覆盖）。

    Returns:
        每项 `{"name", "tool", "arguments", "check"}`；`check(envelope)` 失败抛
        `AssertionError`。改这张表就能改冒烟的范围（测试里也用它造「必然变红」的用例）。
    """
    eft = _eft_text()
    return (
        {
            "name": "parse",
            "tool": "fit_parse",
            "arguments": {"fit": eft},
            "check": _check_parse,
        },
        {
            "name": "index",
            "tool": "fit_index",
            "arguments": {"fit": eft},
            "check": _check_index,
        },
        {
            "name": "view",
            "tool": "fit_view",
            "arguments": {"fit": eft, "view": "mobility"},
            "check": _check_view,
        },
        {
            "name": "get",
            "tool": "fit_get",
            "arguments": {"fit": eft, "path": "mobility.speed_mps"},
            "check": _check_get,
        },
        {
            "name": "detail",
            "tool": "fit_detail",
            "arguments": {"fit": eft, "view": "stability"},
            "check": _check_detail,
        },
        {
            "name": "explain",
            "tool": "fit_explain",
            "arguments": {"fit": eft, "metric": "survivability.ehp_mixed"},
            "check": _check_explain,
        },
        {
            "name": "sensitivity",
            "tool": "fit_sensitivity",
            # 单值指标 + max_ops=4：冒烟要快，且 4 条足够验「delta == value - baseline」
            "arguments": {"fit": eft, "metric": "resources.cpu.free", "max_ops": 4},
            "check": _check_sensitivity,
        },
        {
            "name": "project",
            "tool": "fit_project",
            # 目标 = Hecate，来源 = 带弹头的装甲指挥爆发（没有弹头会得 no_outgoing）
            "arguments": {"target": eft, "sources": [COMMAND_BURST_EFT]},
            "check": _check_project,
        },
        {
            "name": "neg:invalid_path",
            "tool": "fit_get",
            "arguments": {"fit": eft, "path": "mobility.nope"},
            "check": _check_invalid_path,
        },
        {
            "name": "neg:unknown_module",
            "tool": "fit_index",
            "arguments": {"fit": TYPO_EFT},
            "check": _check_unknown_module,
        },
    )


# =============================================================================
# 三、手写 stdlib 客户端（零第三方依赖）
# =============================================================================


def _server_env() -> dict:
    """子进程环境：继承 + 强制 UTF-8 / 不缓冲 + 把仓库根塞进 `PYTHONPATH`。

    `PYTHONIOENCODING=utf-8` 是必需的：Windows 默认代码页会让子进程的 stderr 写崩，
    而 stdout 我们本来就按 UTF-8 字节读写（协议要求）。
    """
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUNBUFFERED"] = "1"
    root = str(WORKSPACE_ROOT)
    existing = env.get("PYTHONPATH")
    env["PYTHONPATH"] = root if not existing else root + os.pathsep + existing
    return env


class StdioClient:
    """最小的 stdio MCP 客户端：`subprocess` + 换行分隔 JSON-RPC（就是协议本身）。

    三个设计点：

    1. **读线程 + 队列**：Windows 上没法给 `readline()` 设超时，用线程把 stdout 的每一行
       推给 `queue.Queue`，主线程 `get(timeout=…)` —— 超时就能干净地报错而不是挂死；
    2. **逐行校验**：每一行必须能 `json.loads` 成带 `jsonrpc == "2.0"` 的对象，否则
       `ProtocolError`（这就是「stdout 只许有 JSON-RPC」的运行时断言）；
    3. **stderr 单独收**：另起一个线程把 stderr 收进列表，失败时打尾巴用 —— 与 stdout
       **完全分开**，绝不混读。
    """

    def __init__(
        self,
        command: list[str] | None = None,
        *,
        cwd: Path | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.command = command or [sys.executable, "-m", "noobbaozi.mcp_server"]
        self.cwd = Path(cwd or WORKSPACE_ROOT)
        self.timeout = timeout
        self._process: subprocess.Popen | None = None
        self._lines: "queue.Queue[bytes | None]" = queue.Queue()
        self._stderr: list[str] = []
        self._next_id = 0
        self._raw_lines: list[str] = []
        """服务写出的**每一条** stdout 原始行（测试用来验证「没有非 JSON-RPC 的行」）。"""

    # -- 生命周期 -------------------------------------------------------------

    def __enter__(self) -> "StdioClient":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def start(self) -> None:
        """拉起服务子进程（stdout / stderr 都走管道，且分开）。"""
        self._process = subprocess.Popen(
            self.command,
            cwd=str(self.cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_server_env(),
        )
        import threading

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        """把 stdout 的每一行（bytes）推进队列；EOF 放一个 `None` 哨兵。"""
        assert self._process is not None and self._process.stdout is not None
        for raw in self._process.stdout:
            self._lines.put(raw)
        self._lines.put(None)

    def _read_stderr(self) -> None:
        """把 stderr 收进列表（与 stdout 完全分开读）。"""
        assert self._process is not None and self._process.stderr is not None
        for raw in self._process.stderr:
            text = raw.decode("utf-8", "replace").rstrip()
            if text:
                self._stderr.append(text)

    def close(self) -> None:
        """关 stdin → 等服务退出（超时就杀），然后收尸。"""
        process = self._process
        if process is None:
            return
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except OSError:  # pragma: no cover - 对端已经关了管道
            pass
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:  # pragma: no cover - 只在不退出时发生
            process.kill()
            process.wait(timeout=10)
        self._process = None

    # -- 协议 ----------------------------------------------------------------

    @property
    def stderr_text(self) -> str:
        """服务写过的 stderr（诊断用；**不是**协议通道）。"""
        return "\n".join(self._stderr)

    def _send(self, message: dict) -> None:
        """写一条消息：紧凑 JSON + 单个 `\\n`（协议要求消息内不含换行）。"""
        process = self._process
        if process is None or process.stdin is None:
            raise ProtocolError("服务进程不在运行")
        line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
        process.stdin.write(line.encode("utf-8"))
        process.stdin.flush()

    def _readline(self, timeout: float | None = None) -> dict:
        """读一行并**校验它是合法的 JSON-RPC 消息**。"""
        try:
            raw = self._lines.get(timeout=timeout or self.timeout)
        except queue.Empty:  # pragma: no cover - 只在服务挂死时发生
            raise ProtocolError(
                f"等待响应超时（{self.timeout}s）；stderr 尾巴：\n{self.stderr_text[-2000:]}"
            ) from None
        if raw is None:
            raise ProtocolError(
                "服务在给出响应前就退出了（stdout EOF）；stderr 尾巴：\n"
                f"{self.stderr_text[-2000:]}"
            )
        text = raw.decode("utf-8", "replace").strip()
        self._raw_lines.append(text)
        if not text:
            raise ProtocolError("stdout 出现空行（协议消息不允许为空行）")
        try:
            message = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProtocolError(
                f"stdout 出现了非 JSON-RPC 的一行（这是最常见的 MCP 事故）：{exc}\n"
                f"  该行内容：{text[:400]!r}"
            ) from exc
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            raise ProtocolError(f"stdout 的消息不是 JSON-RPC 2.0 对象：{text[:400]!r}")
        return message

    def _allocate_id(self) -> int:
        self._next_id += 1
        return self._next_id

    def request(self, method: str, params: object = None, *, timeout: float | None = None) -> dict:
        """发一个请求，返回它的 `result`（`error` 则抛 `ProtocolError`，带原始错误体）。"""
        request_id = self._allocate_id()
        message: dict[str, Any] = {"jsonrpc": "2.0", "id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        while True:
            response = self._readline(timeout)
            if response.get("id") != request_id:
                # 可能是服务端的通知（本服务不发）或错位的响应；本服务只应答，所以这里是硬错。
                raise ProtocolError(
                    f"响应 id 对不上：期望 {request_id}，收到 {response.get('id')!r}"
                )
            if "error" in response:
                raise ProtocolError(
                    f"{method} 返回 JSON-RPC 错误：{json.dumps(response['error'], ensure_ascii=False)}"
                )
            return response.get("result") or {}

    def notify(self, method: str, params: object = None) -> None:
        """发一个通知（**不带 id**，服务不许回包）。"""
        message: dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)


def _envelope_from_result(result: dict) -> tuple[dict, bool, dict | None]:
    """`CallToolResult` → `(信封, isError, structuredContent)`。

    优先用 `structuredContent`（结构就是信封本身）；没有它时解析 `content[0].text`
    （老客户端的路径，也是「传输层不丢信息」的保证）。
    """
    structured = result.get("structuredContent")
    is_error = bool(result.get("isError"))
    if isinstance(structured, dict) and structured:
        return structured, is_error, structured
    content = result.get("content") or []
    _require(isinstance(content, list) and content, "CallToolResult.content 必须是非空列表")
    first = content[0]
    _require(
        isinstance(first, dict) and first.get("type") == "text",
        f"content[0] 应是 {{type:'text'}}，实际 {type(first).__name__}",
    )
    text = first.get("text")
    _require(isinstance(text, str) and text, "content[0].text 必须是非空字符串")
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"content[0].text 不是 JSON：{exc}；内容：{text[:300]!r}") from exc
    _require(isinstance(envelope, dict), "content[0].text 解出来必须是对象（信封）")
    return envelope, is_error, None


def _collect_stdio(cases: tuple[dict, ...]) -> tuple[SessionInfo, list[CallRecord]]:
    """手写客户端：initialize → notifications/initialized → tools/list → 逐个 tools/call。"""
    info = SessionInfo("手写 stdlib JSON-RPC（subprocess + json）")
    records: list[CallRecord] = []
    with StdioClient() as client:
        handshake = client.request(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": CLIENT_NAME, "version": CLIENT_VERSION},
            },
        )
        info.protocol_version = handshake.get("protocolVersion")
        info.server = handshake.get("serverInfo") or {}
        capabilities = handshake.get("capabilities") or {}
        _require(
            isinstance(capabilities.get("tools"), dict),
            f"initialize 的 capabilities.tools 必须存在，实际 {capabilities!r}",
        )
        _require(
            info.protocol_version == PROTOCOL_VERSION,
            f"服务应原样接受 {PROTOCOL_VERSION}，实际 {info.protocol_version!r}",
        )
        _require(handshake.get("instructions"), "initialize 应带 instructions（给 agent 的用法说明）")

        client.notify("notifications/initialized")

        listing = client.request("tools/list")
        tools = listing.get("tools") or []
        _require(isinstance(tools, list) and tools, "tools/list 必须返回非空 tools 列表")
        info.tools = [tool.get("name") for tool in tools if isinstance(tool, dict)]
        for record_spec in cases:
            if record_spec["tool"] not in info.tools:
                raise ProtocolError(
                    f"tools/list 里没有 {record_spec['tool']}（实际：{info.tools}）"
                )
        for record_spec in cases:
            record = CallRecord(record_spec["name"], record_spec["tool"])
            started = time.perf_counter()
            result = client.request(
                "tools/call",
                {"name": record_spec["tool"], "arguments": record_spec["arguments"]},
            )
            record.elapsed_ms = (time.perf_counter() - started) * 1000.0
            envelope, is_error, structured = _envelope_from_result(result)
            record.envelope = envelope
            record.is_error = is_error
            record.structured = structured
            records.append(record)
    return info, records


# =============================================================================
# 四、官方 SDK 客户端（装了 `mcp` 才走这条）
# =============================================================================


def sdk_available() -> bool:
    """官方 `mcp` 包是否可用（导入失败一律当「没装」，不区分原因）。"""
    try:
        import mcp  # noqa: F401
        from mcp import ClientSession  # noqa: F401
        from mcp.client.stdio import StdioServerParameters, stdio_client  # noqa: F401
    except Exception:  # noqa: BLE001 - 装不上就走手写客户端
        return False
    return True


def _collect_sdk(cases: tuple[dict, ...]) -> tuple[SessionInfo, list[CallRecord]]:
    """官方 SDK 客户端（`ClientSession` + `stdio_client`），流程与手写客户端一致。"""
    import anyio

    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    try:  # SDK v1（mcp 1.x）与 v2（mcp 2.x）的字段集合不同，尽量都给
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "noobbaozi.mcp_server"],
            env=_server_env(),
            cwd=str(WORKSPACE_ROOT),
        )
    except TypeError:  # pragma: no cover - 老版本没有 cwd 字段
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "noobbaozi.mcp_server"],
            env=_server_env(),
        )

    info = SessionInfo("官方 SDK（mcp.ClientSession + stdio_client）")
    records: list[CallRecord] = []

    async def _session() -> None:
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                handshake = await session.initialize()
                info.protocol_version = getattr(handshake, "protocolVersion", None)
                server_info = getattr(handshake, "serverInfo", None)
                if server_info is not None:
                    info.server = {
                        "name": getattr(server_info, "name", None),
                        "version": getattr(server_info, "version", None),
                    }
                _require(
                    info.protocol_version in (PROTOCOL_VERSION, "2026-07-28", "2025-11-25"),
                    f"SDK 协商到的版本异常：{info.protocol_version!r}",
                )
                listing = await session.list_tools()
                info.tools = [tool.name for tool in listing.tools]
                for record_spec in cases:
                    _require(
                        record_spec["tool"] in info.tools,
                        f"tools/list 里没有 {record_spec['tool']}（实际：{info.tools}）",
                    )
                for record_spec in cases:
                    record = CallRecord(record_spec["name"], record_spec["tool"])
                    started = time.perf_counter()
                    result = await session.call_tool(
                        record_spec["tool"], record_spec["arguments"]
                    )
                    record.elapsed_ms = (time.perf_counter() - started) * 1000.0
                    content = [
                        {"type": getattr(block, "type", None), "text": getattr(block, "text", None)}
                        for block in (result.content or [])
                    ]
                    envelope, is_error, structured = _envelope_from_result(
                        {
                            "content": content,
                            "structuredContent": getattr(result, "structuredContent", None),
                            "isError": bool(getattr(result, "isError", False)),
                        }
                    )
                    record.envelope = envelope
                    record.is_error = is_error
                    record.structured = structured
                    records.append(record)

    anyio.run(_session)
    return info, records


# =============================================================================
# 五、评估与打印
# =============================================================================


def _short(value: object, limit: int = 160) -> str:
    """紧凑 JSON（截断），只用于打印摘要。"""
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):  # pragma: no cover
        text = repr(value)
    return text if len(text) <= limit else text[:limit] + "…"


def summarize(tool: str, envelope: dict) -> str:
    """每个工具一行**摘要**（只挑几个能证明「真的算出来了」的字段，不做判断）。"""
    if envelope.get("ok") is not True:
        error = envelope.get("error") or {}
        parts = [f"kind={error.get('kind')!r}"]
        if error.get("message"):
            parts.append(f"message={_short(error.get('message'), 90)}")
        if error.get("hint"):
            parts.append(f"hint={_short(error.get('hint'), 90)}")
        if error.get("candidates"):
            names = [c.get("name") for c in error["candidates"] if isinstance(c, dict)]
            parts.append(f"candidates={names}")
        return " · ".join(parts)

    result = envelope.get("result") or {}
    if tool == "fit_parse":
        ship = result.get("ship") or {}
        modules = result.get("modules") or {}
        skills = result.get("skills") or {}
        return (
            f"format={result.get('source_format')} · "
            f"ship={ship.get('name')}({ship.get('type_id')}) · "
            f"modules fitted={modules.get('fitted')} cargo={modules.get('cargo')} · "
            f"skills={skills.get('name')}/{skills.get('count')} · "
            f"warnings={len(result.get('warnings') or [])}"
        )
    if tool == "fit_index":
        ship = result.get("ship") or {}
        mobility = result.get("mobility") or {}
        budget = result.get("budget") or {}
        flags = result.get("flags") or []
        return (
            f"ship={ship.get('name')}({ship.get('type_id')}) · "
            f"speed_mps={mobility.get('speed_mps')} · "
            f"flags={[f.get('id') for f in flags if isinstance(f, dict)]} · "
            f"budget.chars={budget.get('chars')}"
        )
    if tool == "fit_view":
        overview = result.get("overview") or {}
        provenance = result.get("provenance") or {}
        counts: dict[str, int] = {}
        for state in provenance.values():
            counts[state] = counts.get(state, 0) + 1
        return (
            f"overview 字段={list(overview)} · provenance={counts} · "
            f"speed_mps={overview.get('speed_mps')}"
        )
    if tool == "fit_get":
        return (
            f"{result.get('path')} = {_short(result.get('value'), 80)} · "
            f"provenance={result.get('provenance')} · source_view={result.get('source_view')}"
        )
    if tool == "fit_detail":
        views = result.get("views") or {}
        assumptions = (envelope.get("meta") or {}).get("assumptions") or {}
        tier = assumptions.get("skill_tier") or {}
        return (
            f"views={list(views)} · stability.overview 字段="
            f"{list((views.get('stability') or {}).get('overview') or {})} · "
            f"完整技能表={len(tier)} 条"
        )
    return _short(result)


def _evaluate(
    cases: tuple[dict, ...],
    info: SessionInfo,
    records: list[CallRecord],
    *,
    echo: bool = True,
    supports_structured: bool = True,
) -> bool:
    """跑完后的统一评估：断言 + 打印。返回 `True` = PASS。"""
    failures: list[str] = []
    if echo:
        print(f"[client_smoke] transport = {info.transport}")
        print(
            f"[client_smoke] initialize: protocolVersion={info.protocol_version} "
            f"server={info.server.get('name')}/{info.server.get('version')}"
        )
        print(
            f"[client_smoke] tools/list: {len(info.tools)} 个工具（{', '.join(info.tools)}）"
        )
        print("")

    for index, (case, record) in enumerate(zip(cases, records), start=1):
        label = f"[{index}/{len(cases)}] {record.name}"
        if record.envelope is None:  # pragma: no cover - 两个 transport 都会填
            failures.append(f"{record.name}: 没有拿到信封")
            if echo:
                print(f"{label:<26} FAIL  没有拿到信封")
            continue
        try:
            _require(
                record.is_error == (record.envelope.get("ok") is not True),
                f"isError({record.is_error}) 与信封 ok={record.envelope.get('ok')} 不一致",
            )
            if supports_structured:
                _require(
                    isinstance(record.structured, dict),
                    "协商版本支持 structuredContent，但服务没有给",
                )
            else:
                _require(
                    record.structured is None,
                    "老协议版本不该有 structuredContent",
                )
            case["check"](record.envelope)
        except AssertionError as exc:
            failures.append(f"{record.name}: {exc}")
            if echo:
                print(f"{label:<26} FAIL  {record.elapsed_ms:7.1f}ms  {exc}")
            continue
        if echo:
            print(
                f"{label:<26} ok    {record.elapsed_ms:7.1f}ms  "
                f"{summarize(record.tool, record.envelope)}"
            )

    if echo:
        print("")
        if failures:
            print(f"FAIL  {len(failures)}/{len(cases)} 个用例失败：")
            for failure in failures:
                print(f"  - {failure}")
        else:
            print(
                f"PASS  {len(cases)}/{len(cases)}"
                f"（用例表全部符合契约；服务已注册 {len(info.tools)} 个工具）"
            )
    return not failures


def run(
    cases: tuple[dict, ...] | None = None,
    *,
    transport: str = "auto",
    echo: bool = True,
) -> bool:
    """跑一遍冒烟，返回 `True` = PASS。

    Args:
        cases: 用例表（默认 `build_cases()`）；测试里可以换成「必然失败」的表来证明
            断言不是永真。
        transport: `auto`（装了 SDK 就用）/ `sdk`（强制）/ `stdio`（强制手写）。
        echo: 是否打印过程（测试里关掉）。
    """
    cases = tuple(build_cases() if cases is None else cases)
    if transport not in ("auto", "sdk", "stdio"):
        raise ValueError(f"transport 只能是 auto / sdk / stdio，收到 {transport!r}")

    attempts: list[str] = []
    if transport == "sdk":
        attempts.append("sdk")
    elif transport == "stdio":
        attempts.append("stdio")
    else:
        attempts.append("sdk" if sdk_available() else "stdio")
        if attempts[0] == "sdk":
            attempts.append("stdio")  # SDK 出问题时回退到零依赖路径

    for mode in attempts:
        try:
            if mode == "sdk":
                info, records = _collect_sdk(cases)
                supports_structured = True
            else:
                info, records = _collect_stdio(cases)
                supports_structured = (info.protocol_version or "") >= "2025-06-18"
        except Exception as exc:  # noqa: BLE001 - 冒烟失败要说清是哪一步
            if echo:
                print(f"[client_smoke] {mode} 客户端失败：{type(exc).__name__}: {exc}")
                if mode == "sdk" and len(attempts) > 1:
                    print("[client_smoke] 回退到手写 stdlib 客户端重试…")
            if mode == "sdk" and len(attempts) > 1:
                continue
            if echo:
                print("")
                print("FAIL  连接 / 握手阶段就失败了")
            return False
        return _evaluate(
            cases, info, records, echo=echo, supports_structured=supports_structured
        )
    if echo:  # pragma: no cover - 循环必然 return
        print("FAIL  没有可用的客户端")
    return False


# =============================================================================
# 六、入口
# =============================================================================


def main(argv: list[str] | None = None) -> int:
    """跑冒烟并返回退出码（`0` = PASS，`1` = FAIL）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    transport = "auto"
    if "--sdk" in args:
        transport = "sdk"
    elif "--stdio" in args:
        transport = "stdio"

    try:
        passed = run(transport=transport)
    except KeyboardInterrupt:  # pragma: no cover
        print("\n被中断", file=sys.stderr)
        return 130
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
