"""P6-A 投射影响：`project/projection.py` 的契约与**实测语义**。

这里钉住的六件事全部来自 `.tmp/projection_contract.md` 的取证（不是照着实现写的）：

1. web 单张 −60% 精确作用；多张走**引擎自带的叠加惩罚**（0.869 / 0.571 / 0.283）；
2. 遥修改的是目标的 `-45/-46/-47/-48/-49` **修复率**，不是 EHP；`-48 == -45 ÷ -25`；
3. 指挥脉冲的 buffs **按 id 取 max**（5 / 16.24 / 20 / 30 / 50 → max，顺序无关）；
4. `strict_state` 两种取值结果不同，且各自的 warning 存在；
5. 未知 `effect_id` **不静默**（引擎会静默忽略，我们必须报）；
6. 守两条红线：**不能拿 `beacon()` 整包当投影**、**没有链式传递**。

本文件无网络（本地 SDE + 验收样本）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from noobbaozi.config import attributes as A
from noobbaozi.config.paths import db_path
from noobbaozi.engine import adapter
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier
from noobbaozi.project import projection
from noobbaozi.project.views.base import ViewContext, ship_attributes
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
    fit["name"] = name
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
    """f1：1× Warp Disruptor + 2× Fleeting Compact Stasis Webifier（−55%/张）。"""
    return _ctx(engine, index, "f1-hecate-killmail.eft")


@pytest.fixture(scope="module")
def raven(engine, index) -> ViewContext:
    """f15：战列舰目标（无指挥脉冲、无远程模块）。"""
    return _ctx(engine, index, "f15-battleship-raven.json")


@pytest.fixture(scope="module")
def oneiros(engine, index) -> ViewContext:
    """f12：4× Large Remote Armor Repairer II + 维修无人机。"""
    return _ctx(engine, index, "f12-logistics-oneiros.json")


@pytest.fixture(scope="module")
def damnation(engine, index) -> ViewContext:
    """f14：装甲指挥舰（2× Armor Command Burst + 弹头）+ 遥修目标。"""
    return _ctx(engine, index, "f14-command-damnation.json")


@pytest.fixture(scope="module")
def absolution(engine, index) -> ViewContext:
    """f22：指挥战巡（自己带 3 个 buff，会自加成）。"""
    return _ctx(engine, index, "f22-armor-absolution.json")


@pytest.fixture(scope="module")
def raven_with_offline_bursts(engine, index, damnation) -> ViewContext:
    """f14 的指挥爆发器全部设 offline（引擎仍出 buffs —— strict_state 的判定样本）。"""
    names = {
        position: (index.get_type(item["type_id"]) or {}).get("name") or ""
        for position, item in enumerate(damnation.fit["items"])
    }
    items = [
        {**item, "state": "offline"} if "Command Burst" in names[position] else item
        for position, item in enumerate(damnation.fit["items"])
    ]
    return ViewContext(
        engine=engine,
        index=index,
        fit={**damnation.fit, "items": items, "name": "f14-bursts-offline"},
        assumptions=damnation.assumptions,
        meta=damnation.meta,
    )


def _metric(result: dict, path: str) -> dict:
    return next(row for row in result["delta"]["metrics"] if row["path"] == path)


def _attribute(result: dict, attribute_id: int) -> dict | None:
    return next(
        (row for row in result["delta"]["attributes"] if row["id"] == attribute_id), None
    )


def _close(left: float, right: float, rel: float = REL) -> bool:
    return math.isclose(left, right, rel_tol=rel, abs_tol=1e-12)


def _web(value: float = -60.0) -> dict:
    return {"type_id": 527, "effect_id": 6426, "attributes": {20: value}}


# =============================================================================
# 一、web：单张精确 / 多张叠加惩罚（引擎算，不是我们算）
# =============================================================================


def test_single_web_slows_the_target_exactly(raven: ViewContext) -> None:
    """单张 −60% web：135.6 → 54.24（正好 ×0.4）。"""
    result = projection.project(raven, effects=[_web()])
    speed = _metric(result, "mobility.speed_mps")

    assert speed["before"] == pytest.approx(135.6, rel=REL)
    assert speed["after"] == pytest.approx(54.24, rel=REL)
    assert speed["delta"] == pytest.approx(54.24 - 135.6, rel=REL)
    assert speed["delta_pct"] == pytest.approx((54.24 - 135.6) / 135.6, rel=REL)
    assert speed["unit"] == "m/s"

    applied = result["applied"]["effects"]
    assert len(applied) == 1
    assert applied[0]["effect_id"] == 6426
    assert applied[0]["from"] == {"kind": "explicit", "source": None, "effect_index": 0}


def test_stacking_penalty_comes_from_the_engine(raven: ViewContext) -> None:
    """N 张 −60% web：0.869 / 0.571 / 0.283 的叠加惩罚是**引擎给的**（我们不重算）。"""
    penalties = (1.0, 0.8691199808003974, 0.5705831435106105, 0.2829551540233114)
    factor = 1.0
    for count, penalty in enumerate(penalties, start=1):
        factor *= 1.0 - 0.6 * penalty
        result = projection.project(raven, effects=[_web()] * count)
        speed = _metric(result, "mobility.speed_mps")
        assert speed["after"] == pytest.approx(135.6 * factor, rel=REL), (
            f"{count} 张 web：期望 135.6×{factor!r}，实际 {speed['after']!r}"
        )
        # 惩罚因子逐条出现在归因里（第 2 张开始 < 1）
        row = _attribute(result, A.ATTR_MAX_VELOCITY)
        assert row is not None
        assert len(row["attributed_to"]) == count
        assert row["attributed_to"][-1]["penalty"] == pytest.approx(penalty, rel=1e-9)


# =============================================================================
# 二、遥修：改的是修复率，不是 EHP；-48 == -45 ÷ -25
# =============================================================================


def test_remote_repair_changes_repair_rates(damnation: ViewContext, oneiros: ViewContext) -> None:
    """遥修只加目标的修复率（-45 / -46 / -48 / -49），**EHP 一分不动**。"""
    result = projection.project(damnation, sources=[oneiros])

    armor_rate = _attribute(result, A.ATTR_ARMOR_REPAIR_RATE)
    armor_effective = _attribute(result, A.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE)
    hull_rate = _attribute(result, A.ATTR_HULL_REPAIR_RATE)
    assert armor_rate is not None and armor_effective is not None
    assert hull_rate is not None
    assert armor_rate["before"] is None, "引擎在 0 时会省略该属性（不是给了 0）"
    assert armor_rate["after"] == pytest.approx(387.41333333329067, rel=1e-9)
    assert hull_rate["after"] == pytest.approx(5.759999999999365, rel=1e-9)
    assert armor_effective["after"] == pytest.approx(2409.3737612977025, rel=1e-9)

    # 主归因：改动落在 -45/-46 上，且能映射回来源船
    assert armor_rate["attributed_to"], "遥修必须能被归因到来源"
    assert armor_rate["attributed_to"][0]["source"]["kind"] == "source"
    assert armor_rate["attributed_to"][0]["source"]["source"] == "f12-logistics-oneiros.json"

    # EHP 不在改动清单里，指标差值为 0
    assert _attribute(result, A.ATTR_EHP) is None
    assert _metric(result, "survivability.ehp_mixed")["delta"] == 0.0
    assert _metric(result, "survivability.thinnest.armor.ehp")["delta"] == 0.0


def test_effective_repair_rate_is_raw_over_effective_resonance(
    engine, damnation: ViewContext, oneiros: ViewContext
) -> None:
    """`-48 == -45 ÷ -25`（在**独立重算**的变体上验，不用本模块的中间量）。"""
    result = projection.project(damnation, sources=[oneiros])

    # 用返回的 applied.effects 自己搭变体（不信 project() 的任何内部字段）
    incoming_effects = [
        {
            "type_id": effect["type_id"],
            "effect_id": effect["effect_id"],
            "attributes": {int(key): value for key, value in effect["attributes"].items()},
        }
        for effect in result["applied"]["effects"]
    ]
    calculation = engine.calculate({**damnation.fit, "incoming": {"effects": incoming_effects}})
    attrs = ship_attributes(calculation)

    raw = attrs[A.ATTR_ARMOR_REPAIR_RATE]
    effective = attrs[A.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE]
    resonance = attrs[A.ATTR_ARMOR_EFFECTIVE_RESONANCE]
    assert resonance > 0.0
    assert effective == pytest.approx(raw / resonance, rel=1e-9), (
        f"-48={effective!r} 应等于 -45/{resonance!r}={raw / resonance!r}"
    )


# =============================================================================
# 三、指挥脉冲：buffs 按 id 取 max
# =============================================================================


@pytest.mark.parametrize("value", (5.0, 16.24, 20.0, 30.0, 50.0))
def test_buff_value_applies_as_percent_of_armor(
    raven: ViewContext, value: float
) -> None:
    """`{id: 15, value: X}` = 护甲 HP +X%（实测 5/16.24/20/30/50 全部线性）。"""
    result = projection.project(raven, buffs=[{"id": 15, "value": value}])
    armor = _attribute(result, A.ATTR_ARMOR_HP)
    assert armor is not None
    assert armor["after"] == pytest.approx(7656.0 * (1.0 + value / 100.0), rel=1e-9)


def test_same_buff_id_takes_max_not_last(raven: ViewContext) -> None:
    """同一个 id 两条、值不同 → **取 max**（顺序无关；引擎自己也取 max）。"""
    forward = projection.project(
        raven, buffs=[{"id": 15, "value": 10.0}, {"id": 15, "value": 30.0}]
    )
    reverse = projection.project(
        raven, buffs=[{"id": 15, "value": 30.0}, {"id": 15, "value": 10.0}]
    )
    expected = 7656.0 * 1.30
    assert _attribute(forward, A.ATTR_ARMOR_HP)["after"] == pytest.approx(expected, rel=1e-9)
    assert _attribute(reverse, A.ATTR_ARMOR_HP)["after"] == pytest.approx(expected, rel=1e-9)

    applied = forward["applied"]["buffs"]
    assert [(buff["id"], buff["value"]) for buff in applied] == [(15, 30.0)], (
        "合并后只应留一条 id=15，值是 max(10, 30)=30"
    )


def test_command_burst_from_a_real_ship_raises_armor(
    raven: ViewContext, damnation: ViewContext
) -> None:
    """f14 的真实 outgoing.buffs（id 13/15）→ Raven 护甲 HP +15.68%、热能共振降低。"""
    result = projection.project(raven, sources=[damnation])

    buffs = {(buff["id"], buff["value"]) for buff in result["applied"]["buffs"]}
    assert buffs == {(13, -15.68), (15, 15.68)}

    armor = _attribute(result, A.ATTR_ARMOR_HP)
    assert armor["after"] == pytest.approx(7656.0 * 1.1568, rel=1e-9)
    thermal = _attribute(result, A.ATTR_ARMOR_THERMAL_RESONANCE)
    assert thermal["after"] == pytest.approx(0.46750002324581175 * 0.8432, rel=1e-9)

    # 目标自己没有指挥脉冲，不该报自加成
    assert [w["kind"] for w in result["warnings"]].count("self_buff_included") == 0


def test_command_ship_self_buff_is_reported(absolution: ViewContext, raven: ViewContext) -> None:
    """命令舰自己会被自己的脉冲加成（引擎行为）→ 必须留下 `self_buff_included`。"""
    result = projection.project(absolution, sources=[raven])
    assert "self_buff_included" in [warning["kind"] for warning in result["warnings"]]


# =============================================================================
# 四、strict_state：两种取值结果不同，且各自有 warning
# =============================================================================


def test_strict_state_filters_inactive_command_sources(
    raven: ViewContext, raven_with_offline_bursts: ViewContext
) -> None:
    """`strict_state=True`：offline 的指挥爆发器不提供加成（照 EVE），并留 `buff_state_filtered`。"""
    result = projection.project(
        raven, sources=[raven_with_offline_bursts], strict_state=True
    )
    assert result["strict_state"] is True
    assert result["applied"]["buffs"] == []
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "buff_state_filtered" in kinds
    filtered = next(w for w in result["warnings"] if w["kind"] == "buff_state_filtered")
    assert filtered["filtered"]["count"] == 2
    assert filtered["filtered"]["dropped_ids"] == [13, 15]
    # 只有 web（速度）生效，EHP 不动
    assert _metric(result, "mobility.speed_mps")["after"] == pytest.approx(54.24, rel=1e-9)
    assert _metric(result, "survivability.ehp_mixed")["delta"] == 0.0


def test_non_strict_state_keeps_engine_behaviour(
    raven: ViewContext, raven_with_offline_bursts: ViewContext
) -> None:
    """`strict_state=False`：按引擎原样（非 active 也计入），并留 `buff_state_not_filtered`。"""
    result = projection.project(
        raven, sources=[raven_with_offline_bursts], strict_state=False
    )
    assert result["strict_state"] is False
    assert {(b["id"], b["value"]) for b in result["applied"]["buffs"]} == {
        (13, -15.68),
        (15, 15.68),
    }
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "buff_state_not_filtered" in kinds
    assert _metric(result, "survivability.ehp_mixed")["delta"] > 0.0


def test_strict_state_changes_the_result_by_a_measurable_amount(
    raven: ViewContext, raven_with_offline_bursts: ViewContext
) -> None:
    """两个取值的**数字真的不同**（否则 strict_state 就是装饰）。"""
    strict = projection.project(raven, sources=[raven_with_offline_bursts], strict_state=True)
    loose = projection.project(raven, sources=[raven_with_offline_bursts], strict_state=False)
    strict_ehp = _metric(strict, "survivability.ehp_mixed")["after"]
    loose_ehp = _metric(loose, "survivability.ehp_mixed")["after"]
    assert strict_ehp == pytest.approx(68200.97256706195, rel=1e-9)
    assert loose_ehp == pytest.approx(73163.74855325505, rel=1e-9)
    assert loose_ehp - strict_ehp > 4000.0


def test_strict_state_must_be_bool(raven: ViewContext) -> None:
    with pytest.raises(ValueError):
        projection.project(raven, strict_state=1)  # type: ignore[arg-type]


def test_sources_must_be_contexts(raven: ViewContext) -> None:
    with pytest.raises(TypeError):
        projection.project(raven, sources=[{"label": "naked fit"}])
    with pytest.raises(TypeError):
        projection.project({"ship": {"type_id": 1}})  # type: ignore[arg-type]


# =============================================================================
# 五、未知 effect_id 不静默
# =============================================================================


def test_unknown_effect_id_is_reported_and_skipped(raven: ViewContext) -> None:
    """引擎对未知 effect_id **静默忽略**；本模块必须报 `unknown_effect_id` 并跳过。"""
    result = projection.project(
        raven, effects=[{"type_id": 527, "effect_id": 999999, "attributes": {20: -60.0}}]
    )
    assert result["applied"]["effects"] == []
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert "unknown_effect_id" in kinds
    assert _metric(result, "mobility.speed_mps")["delta"] == 0.0


def test_known_effect_id_passes_the_catalog(raven: ViewContext) -> None:
    """对照组：6426 在 `Stasis Webifier II` 的目录里 → 正常应用。"""
    assert 6426 in projection.effect_catalog(527)
    result = projection.project(raven, effects=[_web()])
    assert len(result["applied"]["effects"]) == 1
    assert _metric(result, "mobility.speed_mps")["delta"] < 0.0


# =============================================================================
# 六、两条红线：不准 beacon 整包 / 不准假装链式传递
# =============================================================================


def test_beacon_is_never_used_wholesale(hecate: ViewContext, raven: ViewContext) -> None:
    """f1 的 outgoing 只有 3 条投射 effect；`beacon()` 会带 8 条（含模块自用效果）。

    这条同时守住两个副作用：不能多出 effect，也不能把 CPU/电网/质量的自用效果泄漏给目标。
    """
    result = projection.project(raven, sources=[hecate])
    assert [effect["effect_id"] for effect in result["applied"]["effects"]] == [39, 6426, 6426]

    changed = {row["id"] for row in result["delta"]["attributes"]}
    assert A.ATTR_CPU_LOAD not in changed, "CPU 使用量泄漏 = 拿 beacon 整包了"
    assert 15 not in changed, "powerLoad 泄漏 = 拿 beacon 整包了"


def test_no_chain_propagation(
    engine, hecate: ViewContext, raven: ViewContext
) -> None:
    """B 收了 A 的 web 之后，B 的 `outgoing` **逐项不变** → 没有链式传递。"""
    result = projection.project(raven, sources=[hecate])
    incoming = {
        "effects": [
            {
                "type_id": effect["type_id"],
                "effect_id": effect["effect_id"],
                "attributes": {int(k): v for k, v in effect["attributes"].items()},
            }
            for effect in result["applied"]["effects"]
        ]
    }
    baseline = engine.calculate(raven.fit)
    loaded = engine.calculate({**raven.fit, "incoming": incoming})
    assert loaded.get("outgoing") == baseline.get("outgoing")
    assert loaded.get("buffs") == baseline.get("buffs")


# =============================================================================
# 七、形状：delta 块覆盖任务书要求，且归因可回溯
# =============================================================================


def test_delta_metrics_cover_the_required_paths(hecate: ViewContext, raven: ViewContext) -> None:
    result = projection.project(raven, sources=[hecate])
    assert [row["path"] for row in result["delta"]["metrics"]] == list(projection.METRIC_PATHS)
    required = {
        "survivability.ehp_mixed",
        "survivability.thinnest.shield.ehp",
        "survivability.thinnest.armor.ehp",
        "survivability.thinnest.hull.ehp",
        "mobility.speed_mps",
    }
    assert required <= {row["path"] for row in result["delta"]["metrics"]}
    capacitor = [
        row for row in result["delta"]["metrics"] if row["path"].startswith("stability.capacitor.")
    ]
    assert len(capacitor) == 5


def test_attribution_maps_projected_index_back_to_the_source(
    hecate: ViewContext, raven: ViewContext
) -> None:
    """`from.type == "projected"` 的 index 必须映射回「谁造成的」。"""
    result = projection.project(raven, sources=[hecate])
    row = _attribute(result, A.ATTR_MAX_VELOCITY)
    assert row is not None
    assert len(row["attributed_to"]) == 2
    for entry in row["attributed_to"]:
        assert entry["source"] == {
            "kind": "source",
            "source": "f1-hecate-killmail.eft",
            "source_effect_index": entry["effect_index"],
        }
        assert entry["operator"] == "post_percent"
    assert [entry["effect_index"] for entry in row["attributed_to"]] == [1, 2]


def test_envelope_has_every_contract_field(hecate: ViewContext, raven: ViewContext) -> None:
    result = projection.project(raven, sources=[hecate])
    assert {
        "strict_state",
        "target",
        "applied",
        "sources",
        "delta",
        "repaired",
        "warnings",
    } <= set(result)
    assert {"effects", "buffs"} <= set(result["applied"])
    assert {"metrics", "attributes"} <= set(result["delta"])
    assert {"armor", "hull", "shield"} <= set(result["repaired"])

    assert result["sources"][0]["ship"]["name"] == "Hecate"
    assert result["target"]["ship"]["name"] == "Raven"


def test_repaired_block_reports_repair_deltas(oneiros: ViewContext, damnation: ViewContext) -> None:
    result = projection.project(damnation, sources=[oneiros])
    repaired = result["repaired"]
    assert repaired["armor"]["rate"]["after"] == pytest.approx(387.41333333329067, rel=1e-9)
    assert repaired["armor"]["effective"]["after"] == pytest.approx(2409.3737612977025, rel=1e-9)
    assert repaired["hull"]["rate"]["after"] == pytest.approx(5.759999999999365, rel=1e-9)
    assert repaired["shield"]["rate"]["after"] is None, "没有盾修 → None（不补 0）"


# =============================================================================
# 八、副作用与空输入
# =============================================================================


def test_empty_projection_is_the_identity(raven: ViewContext) -> None:
    """没有任何来源/投射 → 所有指标 delta 为 0，且不改任何属性。"""
    result = projection.project(raven)
    assert result["applied"] == {"effects": [], "buffs": []}
    assert result["delta"]["attributes"] == []
    assert all(row["delta"] == 0.0 for row in result["delta"]["metrics"])


def test_does_not_mutate_inputs(hecate: ViewContext, raven: ViewContext) -> None:
    """`project()` 只读输入：目标与来源的 fit 都必须原封不动（我们内部才拷贝）。"""
    import copy

    target_before = copy.deepcopy(raven.fit)
    source_before = copy.deepcopy(hecate.fit)
    projection.project(raven, sources=[hecate], buffs=[{"id": 15, "value": 10.0}])
    assert raven.fit == target_before, "目标 fit 被就地改了"
    assert hecate.fit == source_before, "来源 fit 被就地改了"


def test_invalid_explicit_entries_are_reported(raven: ViewContext) -> None:
    """结构不合法的显式条目 → `invalid_effect` / `invalid_buff`，不抛异常。"""
    result = projection.project(
        raven,
        effects=[{"type_id": 527}, {"type_id": "x", "effect_id": 1}],
        buffs=[{"id": "x", "value": 1.0}],
    )
    kinds = [warning["kind"] for warning in result["warnings"]]
    assert kinds.count("invalid_effect") == 2
    assert "invalid_buff" in kinds
    assert result["applied"]["effects"] == []
    assert result["applied"]["buffs"] == []
