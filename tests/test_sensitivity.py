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

from noobbaozi.config.paths import db_path
from noobbaozi.engine import adapter
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier
from noobbaozi.project import sensitivity
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


@pytest.mark.parametrize("fixture_name", ("damnation", "confessor", "raven"))
def test_cross_validation_with_cap_sensitivity(fixture_name: str, request) -> None:
    """同一套配装：`sensitivity(disable_item=最耗电那件)` 必须与 `cap_sensitivity[0]` 一致。

    `cap_sensitivity` 的实现是**摘件**（`_fit_without`），本模块是**置 offline**；
    实测两者对电容的数值等价（模块不生效时既不耗电也不生产），这条测试把等价性钉住。
    f15 是唯一让「基线 < 摘后」的样本之一（0 → 62.243），所以它验的是**真 delta**，
    另外两个样本基线已是 100%（值相等但 delta = 0）。
    """
    ctx = request.getfixturevalue(fixture_name)
    overview = build_view("stability", ctx)["overview"]
    cap_rows = overview.get("cap_sensitivity") or []
    assert cap_rows, "样本必须带电容灵敏度候选"

    top = cap_rows[0]
    merged = ctx.items("base")
    position = next(
        position for position, item in enumerate(merged) if item.get("name") == top["module"]
    )

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
    """已经 offline 的模块不再生成 `disable_item`（那是空操作，执行它只会制造噪音）。"""
    fresh = _ctx(engine, index, "f1-hecate-killmail.eft")
    base = sensitivity.sensitivity(fresh, "mobility.speed_mps", max_ops=64)
    planned_base = base["ops_planned"]

    items = [dict(item) for item in fresh.fit["items"]]
    mwd = _mwd_index(fresh)
    items[mwd] = {**items[mwd], "state": "offline"}
    fresh.fit = {**fresh.fit, "items": items}
    after = sensitivity.sensitivity(fresh, "mobility.speed_mps", max_ops=64)

    assert after["ops_planned"] == planned_base - 1
    assert after["skipped"] >= 1


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
