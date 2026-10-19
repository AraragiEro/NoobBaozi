"""P3 `sensitivity`（灵敏度引擎）测试：三种 op + **独立重算自证** + 与 `cap_sensitivity` 交叉验证。

口径要点（与 `project/sensitivity.py` 的 docstring 一致）：
- `disable_item` = `items[N].state = "offline"`（不删件）；
- `set_state` = 指定状态（`off` 接受为 `offline` 别名）；
- `set_mode` = `ship["mode"] = mode_id`（T3C/T3D 模式）。
每个 `changes[i].value` 都是**把变体真的交给引擎重算**得到的；本文件在测试侧再独立算一遍。

本文件无网络（内联样本 + 本地 SDE 索引）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from noobbaozi import api
from noobbaozi.config import attributes as A
from noobbaozi.config.paths import db_path
from noobbaozi.engine import adapter
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier
from noobbaozi.project import explain, sensitivity
from noobbaozi.project.views import build_view
from noobbaozi.project.views.base import ViewContext
from noobbaozi.sde.index import Index

FIXTURES = Path(__file__).resolve().parents[1] / "acceptance" / "fixtures"
REL = 1e-9


# =============================================================================
# fixtures
# =============================================================================


@pytest.fixture(scope="module")
def engine() -> adapter.Engine:
    return adapter.Engine()


@pytest.fixture(scope="module")
def index(engine):
    try:
        opened = Index.open(db_path())
    except FileNotFoundError:  # pragma: no cover - 环境缺库时跳过
        pytest.skip("索引库未构建：先跑 noobbaozi.sde.ingest.build_index()")
    yield opened
    opened.close()


def _ctx(engine, index, name: str) -> ViewContext:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    if name.endswith(".eft"):
        fit = engine.load_eft(text)
    else:
        raw = json.loads(text)
        if isinstance(raw, list):
            raw = raw[0]
        raw.pop("zkb", None)
        fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


@pytest.fixture(scope="module")
def hecate(engine, index) -> ViewContext:
    """f1：Hecate（T3D，3 个模式），MWD 在 `item_index=7`。"""
    return _ctx(engine, index, "f1-hecate-killmail.eft")


@pytest.fixture(scope="module")
def damnation(engine, index) -> ViewContext:
    """f14：Command Damnation —— `cap_sensitivity[0]` 摘掉后真的变稳（100%）。"""
    return _ctx(engine, index, "f14-command-damnation.json")


@pytest.fixture(scope="module")
def confessor(engine, index) -> ViewContext:
    """f10：Confessor（T3D）+ 5 条摘掉后变稳的电容候选。"""
    return _ctx(engine, index, "f10-t3d-confessor.json")


@pytest.fixture(scope="module")
def raven(engine, index) -> ViewContext:
    """f15：Raven —— `cap_sensitivity[0]` 摘掉后从**不稳定**变稳（62.243%），delta 非零。"""
    return _ctx(engine, index, "f15-battleship-raven.json")


@pytest.fixture(scope="module")
def dominix(engine, index) -> ViewContext:
    """f20：Dominix —— 遥感电容传输 54.08 GJ/s，另一种电容配置。"""
    return _ctx(engine, index, "f20-drone-dominix.json")


@pytest.fixture(scope="module")
def cap_booster(engine, index) -> ViewContext:
    """f23：带电容注电器的船（`stable_pct` 已被注电器托到 100%）。"""
    return _ctx(engine, index, "f23-cap-booster.json")


@pytest.fixture(scope="module")
def oneiros(engine, index) -> ViewContext:
    """f12：Logistics Oneiros —— 电容基线本来就不稳定，摘掉最耗电那件也不翻转。"""
    return _ctx(engine, index, "f12-logistics-oneiros.json")


@pytest.fixture(scope="module")
def legion(engine, index) -> ViewContext:
    """f7：T3C（Legion）—— 本地 SDE 索引的 `modes` 表**只有 T3D**，这艘船枚举不到模式。"""
    return _ctx(engine, index, "f7-t3c-legion.json")


@pytest.fixture(scope="module")
def empty_fit(engine, index) -> ViewContext:
    """f58：只有船、**0 模块**（EFT 只有船头一行）。"""
    return _ctx(engine, index, "f58-empty-fit.eft")


def _mwd_index(ctx: ViewContext) -> int:
    items = ctx.items("base")
    return next(
        position
        for position, item in enumerate(items)
        if "Microwarpdrive" in str(item.get("name") or "")
    )


def _recompute(ctx: ViewContext, op: dict, metric: str) -> float | None:
    """**独立重算**：在测试侧按 op 语义自己搭变体，直接问引擎要值。

    刻意不走 `ctx.calculate_variant`（那是被测实现用的缓存入口），也不用
    `sensitivity._metric_value` 以外的任何被测中间量。
    """
    fit = ctx.fit
    kind = op["kind"]
    if kind in ("disable_item", "set_state"):
        state = "offline" if kind == "disable_item" else str(op["state"])
        items = [dict(item) for item in fit["items"]]
        items[op["item_index"]] = {**items[op["item_index"]], "state": state}
        variant = {**fit, "items": items}
    elif kind == "set_mode":
        variant = {**fit, "ship": {**(fit.get("ship") or {}), "mode": int(op["mode_id"])}}
    else:  # pragma: no cover - 测试不该造出未知 op
        raise AssertionError(f"未知 op: {op}")

    calculation = ctx.engine.calculate(variant)
    return sensitivity._metric_value(calculation, metric)


def _close(left: float, right: float) -> bool:
    return math.isclose(left, right, rel_tol=REL, abs_tol=1e-12)


def _change(ctx: ViewContext, metric: str, op: dict) -> dict:
    result = sensitivity.sensitivity(ctx, metric, ops=[op])
    assert result is not None
    assert len(result["changes"]) == 1, result.get("warnings")
    return result["changes"][0]


# =============================================================================
# 一、三种 op 各一个真实样本
# =============================================================================


def test_disable_item_reports_value_and_delta(hecate: ViewContext) -> None:
    """`disable_item(MWD)`：速度从 1482.735 掉到 216.0（推进模块全部失效）。"""
    result = sensitivity.sensitivity(
        hecate,
        "mobility.speed_mps",
        ops=[{"kind": "disable_item", "item_index": _mwd_index(hecate)}],
    )
    assert result is not None
    assert result["metric"] == "mobility.speed_mps"
    assert result["combine"] == "multiplicative"
    assert result["baseline"] == pytest.approx(1482.7354837878534, rel=1e-12)

    change = result["changes"][0]
    assert change["op"]["kind"] == "disable_item"
    assert change["op"]["item_index"] == _mwd_index(hecate)
    assert change["op"]["state"] == "offline"
    assert "Microwarpdrive" in change["op"]["label"]
    assert change["value"] == pytest.approx(216.0, rel=1e-12)
    assert change["delta"] == pytest.approx(216.0 - result["baseline"], rel=1e-12)
    assert change["delta"] < 0.0
    assert change["delta_pct"] == pytest.approx(change["delta"] / result["baseline"], rel=1e-12)


def test_set_state_overload_increases_speed(hecate: ViewContext) -> None:
    """`set_state(MWD, overload)`：速度上升（过载推进的加成来自引擎，不是我们加的）。"""
    change = _change(
        hecate,
        "mobility.speed_mps",
        {"kind": "set_state", "item_index": _mwd_index(hecate), "state": "overload"},
    )
    assert change["op"]["state"] == "overload"
    assert change["value"] > 1482.7354837878534
    assert change["delta"] > 0.0


def test_set_state_accepts_off_alias(hecate: ViewContext) -> None:
    """`state: "off"` 是 `offline` 的别名（引擎只认 `offline`）—— 输出里写引擎真值。"""
    change = _change(
        hecate,
        "mobility.speed_mps",
        {"kind": "set_state", "item_index": _mwd_index(hecate), "state": "off"},
    )
    assert change["op"]["state"] == "offline"
    assert change["value"] == pytest.approx(216.0, rel=1e-12)


def test_set_mode_changes_the_metric(hecate: ViewContext) -> None:
    """`set_mode`：Hecate 三个模式里 Propulsion 模式明显更快（+844 m/s）。"""
    result = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=64)
    assert result is not None
    modes = [
        change for change in result["changes"] if change["op"]["kind"] == "set_mode"
    ]
    assert len(modes) == 3, f"Hecate 有 3 个模式，实际 {len(modes)}"

    labels = {change["op"]["mode_name"] for change in modes}
    assert labels == {"Defense", "Propulsion", "Sharpshooter"}
    propulsion = next(change for change in modes if change["op"]["mode_name"] == "Propulsion")
    assert propulsion["value"] > result["baseline"]
    assert propulsion["note"] and "Propulsion" in propulsion["note"]
    # 模式是**跨视角**的横切能力：同一模式下速度与 EHP 都会变（这里只验速度）
    assert all(change["op"]["kind"] == "set_mode" for change in modes)


# =============================================================================
# 二、每个 delta 必须可自证（独立重算）
# =============================================================================


@pytest.mark.parametrize(
    ("fixture_name", "metric"),
    (
        ("hecate", "mobility.speed_mps"),
        ("damnation", "stability.capacitor.stable_pct"),
    ),
)
def test_every_change_is_independently_recomputable(
    fixture_name: str, metric: str, request
) -> None:
    """全自动计划（全部 op）逐条独立重算：`changes[i].value` 必须与引擎重算一致（1e-9）。"""
    ctx = request.getfixturevalue(fixture_name)
    result = sensitivity.sensitivity(ctx, metric, max_ops=64)
    assert result is not None
    assert result["changes"], "样本人群必有可执行的 op"

    for position, change in enumerate(result["changes"]):
        independent = _recompute(ctx, change["op"], metric)
        assert independent is not None
        assert _close(change["value"], independent), (
            f"第 {position} 条（{change['op']}）：报告 {change['value']!r} "
            f"vs 独立重算 {independent!r}"
        )
        assert change["delta"] == pytest.approx(independent - result["baseline"], rel=1e-12)


def test_self_check_is_not_vacuous(hecate: ViewContext) -> None:
    """**非永真证明**：把 `value` 改 10%，独立重算必须**对不上**（证明自证不是恒真）。"""
    result = sensitivity.sensitivity(
        hecate, "mobility.speed_mps", ops=[{"kind": "disable_item", "item_index": _mwd_index(hecate)}]
    )
    change = result["changes"][0]
    tampered = change["value"] * 1.10
    independent = _recompute(hecate, change["op"], "mobility.speed_mps")
    assert not _close(tampered, independent), "篡改 10% 之后仍然「自证通过」—— 检查是空的"


# =============================================================================
# 三、与 `cap_sensitivity` 的交叉验证（最有价值的一条）
# =============================================================================


@pytest.mark.parametrize(
    "fixture_name",
    ("damnation", "confessor", "raven", "hecate", "dominix", "cap_booster", "oneiros"),
)
def test_cross_validation_with_cap_sensitivity(fixture_name: str, request) -> None:
    """同一套配装：`sensitivity(disable_item=最耗电那件)` 必须与 `cap_sensitivity[0]` 一致。

    `cap_sensitivity` 的实现是**摘件**（`_fit_without`），本模块是**置 offline**；
    实测两者对电容的数值等价（模块不生效时既不耗电也不生产），这条测试把等价性钉住。
    f15 是唯一让「基线 < 摘后」的样本之一（0 → 62.243），所以它验的是**真 delta**，
    另外几个样本基线已是 100%（值相等但 delta = 0）。

    样本覆盖 **7 艘不同船 / 7 种电容配置**（巡洋舰 / 战列舰 / 战列巡洋舰 / T3D /
    无人机船 / 带注电器 / 后勤舰）：这是 T-54 之后第二次做「摘件 vs 置 offline」的对拍。
    """
    ctx = request.getfixturevalue(fixture_name)
    overview = build_view("stability", ctx)["overview"]
    cap_rows = overview.get("cap_sensitivity") or []
    assert cap_rows, "样本必须带电容灵敏度候选"

    top = cap_rows[0]
    merged = ctx.items("base")
    candidates = [
        (position, item)
        for position, item in enumerate(merged)
        if item.get("name") == top["module"] and item.get("slot_type") != "cargo"
    ]
    assert candidates, f"{top['module']} 不在 ctx.items() 里"
    # 同型模块重复时按「装备级耗电」挑那件 —— 与 `cap_sensitivity` 的选择口径同向
    position, item = max(
        candidates,
        key=lambda row: (row[1].get("attributes") or {}).get(A.ATTR_CAP_PEAK_LOAD) or 0.0,
    )
    assert (item.get("attributes") or {}).get(A.ATTR_CAP_PEAK_LOAD) == pytest.approx(
        top["cap_use_gj_s"], abs=1e-3
    ), "挑中的模块耗电与 `cap_sensitivity[0].cap_use_gj_s` 对不上（说明选错了那一件）"

    result = sensitivity.sensitivity(
        ctx,
        "stability.capacitor.stable_pct",
        ops=[{"kind": "disable_item", "item_index": position}],
    )
    assert result is not None
    assert result["combine"] == "single"

    change = result["changes"][0]
    # 容差 1e-3：`cap_sensitivity.stable_pct` 是 **L1 的 3 位小数**（`units.fmt`），
    # 本模块给全精度 → 只该差在舍入位（f15 实测 62.242543794884554 vs 62.243）。
    assert change["value"] == pytest.approx(top["stable_pct"], abs=1e-3), (
        f"{top['module']}：sensitivity={change['value']!r} vs cap_sensitivity={top['stable_pct']!r}"
    )
    # `becomes_stable` 的口径（stable_pct > 0）也必须一致
    assert (change["value"] > 0.0) is bool(top["becomes_stable"])
    if fixture_name == "raven":
        assert result["baseline"] == 0.0, "f15 基线不稳定（stable_pct = 0）"
        assert change["value"] > 0.0, "摘掉最耗电那件之后真的变稳（62.243%）"
        assert change["delta"] == pytest.approx(change["value"] - result["baseline"], rel=1e-12)
        assert change["delta"] > 0.0


# =============================================================================
# 四、边界：max_ops / 非法 op / 不支持的指标
# =============================================================================


def test_max_ops_truncates_with_a_warning(hecate: ViewContext) -> None:
    """`max_ops` 是性能护栏：超了只执行前 N 个，`truncated: true` + warning，不静默。"""
    result = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=3)
    assert result["truncated"] is True
    assert result["max_ops"] == 3
    assert result["ops_planned"] > 3
    assert len(result["changes"]) == 3
    assert result["ops_executed"] == 3
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "truncated" in kinds


@pytest.mark.parametrize("max_ops", (0, -1, None, 1.5, True))
def test_max_ops_must_be_positive_int(max_ops: object, hecate: ViewContext) -> None:
    with pytest.raises(ValueError):
        sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=max_ops)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "metric",
    ("survivability.no_such_field", "not_a_view.metric", "resources.hardpoints.turret.free", "", None),
)
def test_unsupported_metric_returns_none(metric: object, hecate: ViewContext) -> None:
    assert sensitivity.sensitivity(hecate, metric) is None  # type: ignore[arg-type]


def test_invalid_ops_are_reported_not_fatal(hecate: ViewContext) -> None:
    """坏 op（未知 kind / 越界下标 / 非法 state / 货舱件）→ warnings，不抛异常。"""
    result = sensitivity.sensitivity(
        hecate,
        "mobility.speed_mps",
        ops=[
            {"kind": "explode", "item_index": 0},
            {"kind": "disable_item", "item_index": 999},
            {"kind": "set_state", "item_index": _mwd_index(hecate), "state": "onfire"},
            {"kind": "set_mode", "mode_id": "not-an-int"},
            {"kind": "disable_item", "item_index": _mwd_index(hecate)},
        ],
    )
    assert result is not None
    assert len(result["changes"]) == 1, "只有最后那条合法的 op 该执行"
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert kinds.count("invalid_op") == 4


def test_auto_plan_skips_already_offline_items(engine, index) -> None:
    """已经 offline 的模块**不执行** `disable_item`（那是空操作），但仍计入 `ops_planned`。

    计数口径（`project/sensitivity.py` 模块 docstring「计数口径」）：
    `ops_planned == ops_executed + skipped`。所以「计划数」不会因为一件模块已 offline 而变小，
    变的是 `skipped`（+1）与 `ops_executed`（−1）。
    """
    fresh = _ctx(engine, index, "f1-hecate-killmail.eft")
    base = sensitivity.sensitivity(fresh, "mobility.speed_mps", max_ops=64)
    planned_base = base["ops_planned"]
    executed_base = base["ops_executed"]

    items = [dict(item) for item in fresh.fit["items"]]
    mwd = _mwd_index(fresh)
    items[mwd] = {**items[mwd], "state": "offline"}
    fresh.fit = {**fresh.fit, "items": items}
    after = sensitivity.sensitivity(fresh, "mobility.speed_mps", max_ops=64)

    assert after["ops_planned"] == planned_base, "空操作仍计入计划总数（三个数要自洽）"
    assert after["ops_executed"] == executed_base - 1, "被跳过的那件不再产出 change"
    assert after["skipped"] == base["skipped"] + 1
    assert after["ops_planned"] == after["ops_executed"] + after["skipped"]
    assert "noop" in [warning["kind"] for warning in after["warnings"]]


# =============================================================================
# 五、口径与形状
# =============================================================================


def test_delta_pct_is_none_when_baseline_is_zero(hecate: ViewContext) -> None:
    """基线为 0 时 `delta_pct` 给 `None`（除零不编数字）。

    f1 的电容本来就不稳定（`stable_pct == 0`），关掉一件模块后仍可能是 0 → 正好走这条分支。
    """
    result = sensitivity.sensitivity(
        hecate,
        "stability.capacitor.stable_pct",
        ops=[{"kind": "disable_item", "item_index": 0}],
    )
    assert result["baseline"] == 0.0
    assert result["changes"][0]["delta_pct"] is None


def test_combine_matches_explain_classification(hecate: ViewContext) -> None:
    """`combine` 与 explain 的分类同一套：可加 / 乘性 / 单值。"""
    additive = sensitivity.sensitivity(hecate, "resources.cpu.load", max_ops=2)
    multiplicative = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=2)
    single = sensitivity.sensitivity(hecate, "stability.capacitor.stable_pct", max_ops=2)
    assert additive["combine"] == "additive"
    assert multiplicative["combine"] == "multiplicative"
    assert single["combine"] == "single"


def test_envelope_has_every_contract_field(hecate: ViewContext) -> None:
    required = {
        "metric",
        "combine",
        "baseline",
        "changes",
        "warnings",
        "ops_planned",
        "ops_executed",
        "skipped",
        "truncated",
        "max_ops",
        "provenance",
        "tolerance",
    }
    result = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=2)
    assert required <= set(result)
    for change in result["changes"]:
        assert {"op", "value", "delta", "delta_pct", "note"} <= set(change)
        assert {"kind", "item_index", "label", "state"} <= set(change["op"])


# =============================================================================
# 六、计数自洽：`ops_planned == ops_executed + skipped`
# =============================================================================


def _assert_counts(result: dict) -> None:
    """三个计数必须**恒等**：计划 = 执行 + 跳过（否则调用方无法回答「少的去哪了」）。"""
    assert result["ops_planned"] == result["ops_executed"] + result["skipped"], (
        f"计数不自洽：planned={result['ops_planned']} executed={result['ops_executed']} "
        f"skipped={result['skipped']}；warnings={[w['kind'] for w in result['warnings']]}"
    )
    assert result["ops_executed"] == len(result["changes"])


def test_counts_are_self_consistent_without_truncation(hecate: ViewContext) -> None:
    result = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=64)
    assert result["truncated"] is False
    _assert_counts(result)
    assert result["skipped"] == 0
    assert "noop" not in [warning["kind"] for warning in result["warnings"]]


def test_counts_are_self_consistent_when_truncated(hecate: ViewContext) -> None:
    """截断时 `skipped` **必须**把被砍掉的 op 算进去（旧实现的 `skipped` 只数空操作 → 三数打架）。"""
    cut = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=3)
    assert cut["truncated"] is True
    _assert_counts(cut)
    assert cut["ops_executed"] == 3
    assert cut["ops_planned"] == 20, "f1：17 件已装模块 + 3 个模式"
    assert cut["skipped"] == 17

    warning = next(w for w in cut["warnings"] if w["kind"] == "truncated")
    assert warning["executed"] == 3
    assert warning["noops"] == 0
    # 归因：跳过数 = 被截断数 + 空操作数
    assert cut["skipped"] == (warning["planned"] - warning["executed"]) + warning["noops"]


def test_truncation_keeps_executed_values_identical(hecate: ViewContext) -> None:
    """截断只影响**覆盖面**，不改变已执行项的 `value`（前 N 条与完整跑逐位相同）。"""
    full = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=64)
    cut = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=3)

    assert cut["baseline"] == full["baseline"]
    assert [c["value"] for c in cut["changes"]] == [c["value"] for c in full["changes"][:3]]
    assert [c["delta"] for c in cut["changes"]] == [c["delta"] for c in full["changes"][:3]]
    assert [c["op"] for c in cut["changes"]] == [c["op"] for c in full["changes"][:3]]


def test_truncation_sets_a_warning_not_a_silent_gap(hecate: ViewContext) -> None:
    cut = sensitivity.sensitivity(hecate, "mobility.speed_mps", max_ops=3)
    kinds = [warning["kind"] for warning in cut["warnings"]]
    assert kinds.count("truncated") == 1


# =============================================================================
# 七、边界样本（0 模块 / 全 offline / 重复 op / 模式不可用）
# =============================================================================


def test_zero_module_fit_still_has_mode_ops_only(empty_fit: ViewContext) -> None:
    """0 模块（只有船）：没有逐件 op，但 T3D 仍可切模式；计数照样自洽。"""
    assert empty_fit.fit["items"] == []

    result = sensitivity.sensitivity(empty_fit, "mobility.speed_mps", max_ops=64)
    assert result is not None
    assert result["baseline"] == pytest.approx(216.0, rel=1e-12)
    assert result["changes"], "Hecate 有 3 个模式 → 0 模块也仍有 op 可跑"
    assert {change["op"]["kind"] for change in result["changes"]} == {"set_mode"}
    assert result["ops_executed"] == 3
    assert result["ops_planned"] == result["ops_executed"] + result["skipped"]
    assert result["skipped"] == 0


def test_zero_module_fit_does_not_divide_by_zero_in_delta_pct(empty_fit: ViewContext) -> None:
    """0 模块时 `cpu.load` 基线是 0 → `delta_pct` 必须给 `None`（不是 inf / 不是 0）。"""
    result = sensitivity.sensitivity(empty_fit, "resources.cpu.load", max_ops=64)
    assert result is not None
    assert result["baseline"] == 0.0
    assert result["changes"]
    assert all(change["delta_pct"] is None for change in result["changes"])


def test_zero_module_fit_peak_load_is_unavailable_not_zero(empty_fit: ViewContext) -> None:
    """0 模块时引擎**不发** `peak_load` 属性 → 本模块给 `None`（不补 0），接口层报 `invalid_path`。

    同一份 0 模块配装上 `cpu.load` / `powergrid.load` 是 **0.0**（引擎发了属性），
    `capacitor.peak_load` 却取不到 —— 这个差别来自引擎，不是本模块的口径差。
    """
    assert sensitivity.sensitivity(empty_fit, "stability.capacitor.peak_load") is None
    text = (FIXTURES / "f58-empty-fit.eft").read_text(encoding="utf-8")
    envelope = api.fit_sensitivity(text, "stability.capacitor.peak_load")
    assert envelope.get("ok") is False
    assert envelope["error"]["kind"] == "invalid_path"


def test_all_items_offline_reports_every_item_op_as_skipped(hecate: ViewContext) -> None:
    """全部模块 offline：`baseline` 等于船体裸值，逐件 op 全进 `skipped`，只剩模式 op。"""
    items = [{**item, "state": "offline"} for item in hecate.fit["items"]]
    offline = ViewContext(
        engine=hecate.engine,
        index=hecate.index,
        fit={**hecate.fit, "items": items},
        assumptions=hecate.assumptions,
        meta=hecate.meta,
    )

    result = sensitivity.sensitivity(offline, "mobility.speed_mps", max_ops=64)
    assert result is not None
    assert result["baseline"] == pytest.approx(216.0, rel=1e-12), "全部模块不生效 → 速度回到船体裸值"
    assert {change["op"]["kind"] for change in result["changes"]} == {"set_mode"}
    _assert_counts(result)
    assert result["skipped"] >= 1
    assert "noop" in [warning["kind"] for warning in result["warnings"]]
    # 已 offline 的模块**不会**出现在 changes 里（不产出 delta 0 的噪音行）
    disabled_indexes = {
        change["op"]["item_index"]
        for change in result["changes"]
        if change["op"]["kind"] != "set_mode"
    }
    assert disabled_indexes == set()


def test_disable_item_on_offline_item_is_a_noop(hecate: ViewContext) -> None:
    """`disable_item` 与 `set_state offline` 同一判据：已经是 offline → 跳过（不是再记一条 delta 0）。"""
    mwd = _mwd_index(hecate)
    items = [dict(item) for item in hecate.fit["items"]]
    items[mwd] = {**items[mwd], "state": "offline"}
    offline = ViewContext(
        engine=hecate.engine,
        index=hecate.index,
        fit={**hecate.fit, "items": items},
        assumptions=hecate.assumptions,
        meta=hecate.meta,
    )

    result = sensitivity.sensitivity(
        offline, "mobility.speed_mps", ops=[{"kind": "disable_item", "item_index": mwd}]
    )
    assert result is not None
    assert result["changes"] == []
    assert result["ops_planned"] == 1
    assert result["ops_executed"] == 0
    assert result["skipped"] == 1
    assert "noop" in [warning["kind"] for warning in result["warnings"]]


def test_same_op_twice_runs_once(hecate: ViewContext) -> None:
    """同一件 op 提交两次 → 只执行一次（重复 op 是空操作，进 `skipped`，不重复计数）。"""
    mwd = _mwd_index(hecate)
    result = sensitivity.sensitivity(
        hecate,
        "mobility.speed_mps",
        ops=[
            {"kind": "disable_item", "item_index": mwd},
            {"kind": "disable_item", "item_index": mwd},
        ],
    )
    assert result is not None
    assert len(result["changes"]) == 1
    assert result["ops_planned"] == 2
    assert result["ops_executed"] == 1
    assert result["skipped"] == 1
    _assert_counts(result)
    assert "noop" in [warning["kind"] for warning in result["warnings"]]


def test_same_effect_via_both_kinds_is_deduped(hecate: ViewContext) -> None:
    """`disable_item(i)` 与 `set_state(i, offline)` **效果相同** → 第二条也是空操作。"""
    mwd = _mwd_index(hecate)
    result = sensitivity.sensitivity(
        hecate,
        "mobility.speed_mps",
        ops=[
            {"kind": "disable_item", "item_index": mwd},
            {"kind": "set_state", "item_index": mwd, "state": "offline"},
        ],
    )
    assert result is not None
    assert len(result["changes"]) == 1
    assert result["skipped"] == 1


def test_set_mode_of_current_mode_is_a_noop(hecate: ViewContext) -> None:
    """切到**当前已经是**的模式 = 空操作（与已 offline 的 disable_item 同一判据）。

    样本的 `ship.mode` 是 `None`，所以这里显式把一个真实模式 id 写进 fit 再造上下文
    （f1 的 Hecate 有三个模式；35686 = `Hecate Defense Mode`）。
    """
    defense = sensitivity._modes(hecate)[0]["type_id"]
    with_mode = ViewContext(
        engine=hecate.engine,
        index=hecate.index,
        fit={**hecate.fit, "ship": {**hecate.fit["ship"], "mode": defense}},
        assumptions=hecate.assumptions,
        meta=hecate.meta,
    )
    result = sensitivity.sensitivity(
        with_mode, "mobility.speed_mps", ops=[{"kind": "set_mode", "mode_id": defense}]
    )
    assert result is not None
    assert result["changes"] == []
    assert result["skipped"] == 1
    assert "noop" in [warning["kind"] for warning in result["warnings"]]

    # 自动计划同样不再给「当前模式」那一条
    auto = sensitivity.sensitivity(with_mode, "mobility.speed_mps", max_ops=64)
    assert auto is not None
    modes = [c for c in auto["changes"] if c["op"]["kind"] == "set_mode"]
    assert len(modes) == 2, "3 个模式里，当前那个不该再计划一次"
    assert defense not in {c["op"]["mode_id"] for c in modes}
    _assert_counts(auto)


def test_set_mode_not_in_ship_modes_is_skipped(hecate: ViewContext) -> None:
    """可枚举但不在清单里的 mode → **跳过**（不抛异常，也不给 delta 0 的假变化行）。"""
    result = sensitivity.sensitivity(
        hecate, "mobility.speed_mps", ops=[{"kind": "set_mode", "mode_id": 999999}]
    )
    assert result is not None
    assert result["changes"] == []
    assert result["ops_planned"] == 1
    assert result["skipped"] == 1
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "mode_not_available" in kinds


def test_set_mode_on_t3c_is_skipped_gracefully(legion: ViewContext) -> None:
    """T3C（索引里枚举不到模式）给 set_mode → **优雅跳过**，不是报错、不是假变化。

    本地 SDE 索引的 `modes` 表只有 T3D（Confessor / Svipul / Jackdaw / Hecate）+ 两艘新船，
    T3C 的模式根本没收录 —— 所以「无法判断是否适用」，跳过并如实说。
    """
    assert sensitivity._modes(legion) == [], "前提：这艘 T3C 在本索引里枚举不到模式"
    result = sensitivity.sensitivity(
        legion, "mobility.speed_mps", ops=[{"kind": "set_mode", "mode_id": 35686}]
    )
    assert result is not None
    assert result["changes"] == [], "无法判断适用性时不许编一条 delta 0 的变化"
    assert result["skipped"] == 1
    _assert_counts(result)
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "modes_unknown" in kinds


def test_t3c_auto_plan_warns_that_modes_are_not_enumerated(legion: ViewContext) -> None:
    """T3C 自动计划静默少了「模式」这一维 → 必须留 warning（否则能力缺失变成隐身）。"""
    result = sensitivity.sensitivity(legion, "mobility.speed_mps", max_ops=64)
    assert result is not None
    assert result["changes"], "逐件 op 仍在"
    assert all(change["op"]["kind"] != "set_mode" for change in result["changes"])
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "modes_unknown" in kinds


def test_non_t3_ship_does_not_get_the_mode_warning(raven: ViewContext) -> None:
    """非 T3 船本来就没有模式 → 不该被 `modes_unknown` 警告刷屏（那是 T3C 数据缺口的事实）。"""
    result = sensitivity.sensitivity(raven, "mobility.speed_mps", max_ops=64)
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "modes_unknown" not in kinds


# =============================================================================
# 八、`supported_metrics()` 的每个串都必须是 `fit_get` 能解析的路径
# =============================================================================


def _f1_text() -> str:
    return (FIXTURES / "f1-hecate-killmail.eft").read_text(encoding="utf-8")


def test_every_sensitivity_metric_is_a_fit_get_path() -> None:
    """14 个指标串逐个过 `api.fit_get`：**登记了就必须取得到**（坏契约会让 agent 直接踩空）。"""
    text = _f1_text()
    for metric in sensitivity.supported_metrics():
        envelope = api.fit_get(text, metric)
        assert envelope.get("ok") is True, (
            f"{metric}: fit_get 拒绝了这个已登记的指标 → {envelope.get('error')}"
        )
        result = envelope["result"]
        assert result["path"] == metric
        assert "value" in result and "provenance" in result


def test_sensitivity_metric_contract_check_is_not_vacuous() -> None:
    """**非永真证明**：不存在的路径必须被 `fit_get` 拒绝（证明上面那条不是恒真）。"""
    envelope = api.fit_get(_f1_text(), "resources.cpu.load_typo")
    assert envelope.get("ok") is False
    assert envelope["error"]["kind"] == "invalid_path"


def test_sensitivity_metrics_include_the_five_extras() -> None:
    """`sensitivity` 比 `explain` 多的 5 个单值指标（`combine == "single"`）必须在表里。"""
    extras = [m for m in sensitivity.supported_metrics() if m not in explain.supported_metrics()]
    assert sorted(extras) == sorted(
        [
            "stability.capacitor.stable_pct",
            "stability.capacitor.peak_load",
            "stability.capacitor.capacity",
            "resources.cpu.free",
            "resources.powergrid.free",
        ]
    )
    for metric in extras:
        assert sensitivity._combine(metric) == "single"


def test_api_fit_sensitivity_keeps_the_counts_identity() -> None:
    """接口层只做组装：`ops_planned == ops_executed + skipped` 必须原样出现在 `fit_sensitivity` 里。

    这条同时钉住「截断在接口层不会被吞掉」：`max_ops=3` 的 `truncated` 与 `skipped` 都要穿出来。
    """
    text = _f1_text()
    envelope = api.fit_sensitivity(text, "mobility.speed_mps", max_ops=3)
    assert envelope.get("ok") is True, envelope.get("error")
    result = envelope["result"]
    assert result["truncated"] is True
    assert result["ops_executed"] == len(result["changes"]) == 3
    assert result["ops_planned"] == result["ops_executed"] + result["skipped"]
    assert result["skipped"] == 17
