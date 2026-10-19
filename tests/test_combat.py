"""P6-B2 测试：交战数学（`project/combat.py`）。

三条守卫来自 P6-B1 取证时**真实踩到**的坑，每条都做成「改坏就必须红」的断言：

1. `optimalSigRadius` 是 **40000**，不是 40（属性表 ×1000 口径）；
2. 伤害属性 ID 是 **114/116/117/118**（热伤 118，115 不存在）；
3. 逐件齐射不由引擎给（`-11` 全 null），`volley = dps × cycle`。

另有 **pyfa 对拍**（3 组参考值，容差 `combat.TOLERANCE_REL`）与**组合变异**
（把两处口径同时改坏，确认输出真的变）—— 这两条保证上面的守卫不是永真。
"""

from __future__ import annotations

import math

import pytest

from noobbaozi.config.paths import db_path
from noobbaozi.engine import adapter
from noobbaozi.engine.assumptions import derive_skill_tier
from noobbaozi.project import combat
from noobbaozi.project.views import shared
from noobbaozi.project.views.base import ViewContext
from noobbaozi.sde.index import Index

# =============================================================================
# pyfa 参考值（逐位对拍）
# =============================================================================
# 来源：pyfa-org/Pyfa @ d2722a4c8b2ec9cdfded5024cb97df362ac513da
#   graphs/data/fitApplicationProfile/calc/turret.py::calcTrackingFactor / calcTurretDamageMult
#   graphs/data/fitApplicationProfile/calc/launcher.py::calcMissileFactor
#   eos/calc.py::calculateRangeFactor
# 复算方式（P6-B1 台架同一套）：
#   cth = calculateRangeFactor(optimal, falloff, range, restrictedRange=False)
#         * calcTrackingFactor(tracking, 40000, angular, sig)
# 数值由 pyfa 源码原样跑出后粘贴，**不是**用本模块算的。

PYFA_REFERENCE: tuple[dict, ...] = (
    {
        "label": "A · cth 中间区（range 10km / optimal 8km / falloff 5km）",
        "kind": "turret",
        "inputs": dict(tracking=50.0, optimal_sig_radius=40000.0, angular_vel=0.08,
                       target_signature=125.0, range_m=10000.0, optimal_m=8000.0, falloff_m=5000.0),
        "chance_to_hit": 0.7463146958771829,
        "damage_multiplier": 0.6692370136209457,
    },
    {
        "label": "B · cth 深 falloff（range 20km）",
        "kind": "turret",
        "inputs": dict(tracking=50.0, optimal_sig_radius=40000.0, angular_vel=0.02,
                       target_signature=125.0, range_m=20000.0, optimal_m=8000.0, falloff_m=5000.0),
        "chance_to_hit": 0.018244633747382024,
        "damage_multiplier": 0.03415630386650525,
    },
    {
        "label": "C · cth 高位（tracking 300 / 角速度 0.15）",
        "kind": "turret",
        "inputs": dict(tracking=300.0, optimal_sig_radius=40000.0, angular_vel=0.15,
                       target_signature=125.0, range_m=3000.0, optimal_m=2700.0, falloff_m=18624.375),
        "chance_to_hit": 0.9822352714278872,
        "damage_multiplier": 0.9887383472181724,
    },
    {
        "label": "D · 导弹应用（HML: ER140/EV85/drf0.682, Vt200/S125）",
        "kind": "missile",
        "inputs": dict(explosion_radius_m=140.0, explosion_velocity_mps=85.0,
                       damage_reduction_factor=0.682, target_velocity=200.0,
                       target_signature=125.0),
        "factor": 0.516409899261299,
    },
    {
        "label": "E · 导弹应用（火箭: ER40/EV170/drf0.604, Vt500/S35）",
        "kind": "missile",
        "inputs": dict(explosion_radius_m=40.0, explosion_velocity_mps=170.0,
                       damage_reduction_factor=0.604, target_velocity=500.0,
                       target_signature=35.0),
        "factor": 0.480823992108601,
    },
    {
        "label": "F · 导弹应用（Vt=0：应只受 S/E 限制）",
        "kind": "missile",
        "inputs": dict(explosion_radius_m=241.0, explosion_velocity_mps=71.0,
                       damage_reduction_factor=0.882, target_velocity=0.0,
                       target_signature=125.0),
        "factor": 0.5186721991701245,
    },
)

# =============================================================================
# 样本配装
# =============================================================================

TURRET_EFT = (
    "[Thorax, 炮台样本]\n"
    "Magnetic Field Stabilizer II\n\n"
    "Neutron Blaster Cannon II, Void M"
)
"""1 门中子炮 + 磁稳：命中率落在中间区（可分辨 40000 vs 40）。"""

MISSILE_EFT = (
    "[Caracal, 导弹样本]\n"
    "Ballistic Control System II\n\n"
    "Light Missile Launcher II, Scourge Light Missile"
)

DRONE_EFT = "[Algos, 无人机样本]\nHammerhead I x4"

DISINTEGRATOR_EFT = "[Vedmak, 分解器样本]\nDamage Control II\n\nHeavy Entropic Disintegrator II, Mystic M"

EMPTY_EFT = "[Hecate, 无武器样本]\nDamage Control II"

DAMAGE_IDS = tuple(attribute_id for _name, attribute_id in shared.DAMAGE_TYPES)


# =============================================================================
# fixtures
# =============================================================================


@pytest.fixture(scope="module")
def engine() -> adapter.Engine:
    return adapter.Engine()


@pytest.fixture(scope="module")
def index():
    try:
        path = db_path()
    except FileNotFoundError:  # pragma: no cover - 索引库是构建产物
        pytest.skip("索引库未构建：先跑 noobbaozi.sde.ingest.build_index()")
    opened = Index.open(path)
    yield opened
    opened.close()


def _ctx(engine, index, eft: str) -> ViewContext:
    fit = adapter.load_eft(eft)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return ViewContext(engine=engine, index=index, fit=fit, assumptions={}, meta={})


@pytest.fixture(scope="module")
def turret_ctx(engine, index) -> ViewContext:
    return _ctx(engine, index, TURRET_EFT)


@pytest.fixture(scope="module")
def missile_ctx(engine, index) -> ViewContext:
    return _ctx(engine, index, MISSILE_EFT)


@pytest.fixture(scope="module")
def drone_ctx(engine, index) -> ViewContext:
    return _ctx(engine, index, DRONE_EFT)


# =============================================================================
# 1. pyfa 对拍（第二把尺子）
# =============================================================================


@pytest.mark.parametrize("case", [c for c in PYFA_REFERENCE if c["kind"] == "turret"],
                         ids=[c["label"] for c in PYFA_REFERENCE if c["kind"] == "turret"])
def test_pyfa_reference_turret(case: dict) -> None:
    cth = combat.turret_chance_to_hit(**case["inputs"])
    assert cth == pytest.approx(case["chance_to_hit"], rel=combat.TOLERANCE_REL)
    mult = combat.turret_damage_multiplier(cth)
    assert mult == pytest.approx(case["damage_multiplier"], rel=combat.TOLERANCE_REL)


@pytest.mark.parametrize("case", [c for c in PYFA_REFERENCE if c["kind"] == "missile"],
                         ids=[c["label"] for c in PYFA_REFERENCE if c["kind"] == "missile"])
def test_pyfa_reference_missile(case: dict) -> None:
    factor = combat.missile_application_factor(**case["inputs"])
    assert factor == pytest.approx(case["factor"], rel=combat.TOLERANCE_REL)


# =============================================================================
# 2. 坑①：optimalSigRadius 必须是 40000
# =============================================================================


def test_optimal_sig_constant_is_40000() -> None:
    assert combat.TURRET_SIGNATURE_RESOLUTION == 40000.0


def test_optimal_sig_must_be_40000_not_40(missile_ctx, turret_ctx) -> None:
    """守卫：炮台输入里的 optimal_sig 必须是 40000。

    变异证明（这条断言不是永真）：把同一个 sig 换成 40（= ÷1000 的展示口径），
    命中率会从「中间区」跳到接近 1 —— 两者必须显著不同。
    """
    result = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0, angular_vel=0.01)
    weapon = next(w for w in result["weapons"] if w["chance_to_hit"] is not None)
    assert weapon["inputs"]["optimal_sig_radius"] == 40000.0
    assert 0.3 < weapon["chance_to_hit"] < 0.95

    tracking = weapon["inputs"]["tracking_rad_s"]
    right = combat.turret_chance_to_hit(
        tracking=tracking, optimal_sig_radius=40000.0, angular_vel=0.01,
        target_signature=125.0, range_m=3000.0, optimal_m=5400.0, falloff_m=5000.0,
    )
    wrong = combat.turret_chance_to_hit(
        tracking=tracking, optimal_sig_radius=40.0, angular_vel=0.01,
        target_signature=125.0, range_m=3000.0, optimal_m=5400.0, falloff_m=5000.0,
    )
    assert wrong > 0.999, "把 sig 换成 40 必须给出接近 100% 的命中率（否则这条守卫无效）"
    assert abs(right - wrong) > 0.1


def test_combat_reads_raw_attributes_not_physical(turret_ctx) -> None:
    """raw 口径：引擎给 160 的原始值是 ×1000 后的量级（0.39 级物理值 ≠ 390 级 raw）。

    炮台属性在 `ctx.variant()` 里是 raw；`ctx.attrs()` 会 ÷1000。两边**同缩**时公式
    等价，混用才出事——所以这里只断言「本模块读的是 raw」。
    """
    from noobbaozi.project.views.base import item_attributes  # noqa: PLC0415

    raw_items = turret_ctx.variant().get("items") or []
    positions = [
        position for position, item in enumerate(raw_items)
        if combat._raw_attr(item.get("attributes") or {}, 160) is not None  # noqa: SLF001
    ]
    assert positions, "样本里必须有炮台（trackingSpeed=160）"
    position = positions[0]
    raw_tracking = combat._raw_attr(raw_items[position]["attributes"], 160)  # noqa: SLF001
    physical_tracking = item_attributes(raw_items[position]).get(160)
    assert raw_tracking > 1.0
    assert physical_tracking == pytest.approx(raw_tracking / 1000.0, rel=1e-9)

    from noobbaozi.project.views.base import ship_attributes  # noqa: PLC0415

    assert combat._raw_ship_attrs(turret_ctx)[37] == pytest.approx(  # noqa: SLF001
        ship_attributes(turret_ctx.variant())[37], rel=1e-12
    )  # 37 不缩放


# =============================================================================
# 3. 坑②：伤害属性 ID = 114/116/117/118
# =============================================================================


def test_damage_ids_are_114_116_117_118() -> None:
    assert DAMAGE_IDS == (114, 116, 117, 118)
    assert 115 not in DAMAGE_IDS


def test_damage_split_includes_thermal(turret_ctx, missile_ctx) -> None:
    """Void M 是动能 17.7 + 热 17.7；漏了 118 会让热伤变 0、总和减半。"""
    result = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0)
    weapon = result["weapons"][0]
    split = weapon["damage_split"]
    assert split is not None
    assert split["thermal"] > 0.0, "热伤（118）必须被读到"
    assert split["kinetic"] > 0.0
    assert split["thermal"] == pytest.approx(split["kinetic"], rel=1e-6)

    total = sum(split.values())
    assert total > 0.0
    # 四系之和必须等于「齐射」——漏一系就对不上。
    assert total == pytest.approx(weapon["nominal_volley"], rel=1e-6)


def test_damage_split_helper_drops_thermal_with_wrong_ids() -> None:
    """变异证明：按 114–117 求和时热伤被漏掉（115 不存在），总和只剩一半。"""
    charge = {114: 0.0, 115: 0.0, 116: 0.0, 117: 17.7, 118: 17.7}
    correct = combat._damage_split(charge, 1.0)  # noqa: SLF001
    wrong = {
        name: (charge.get(attribute_id) or 0.0)
        for name, attribute_id in (("em", 114), ("explosive", 115), ("kinetic", 116), ("thermal", 117))
    }
    assert correct is not None
    assert sum(correct.values()) == pytest.approx(35.4)
    assert sum(wrong.values()) == pytest.approx(17.7)  # 漏热伤 + 错位


# =============================================================================
# 4. 坑③：volley = dps × cycle（引擎不给 -11）
# =============================================================================


def test_engine_volley_is_damage_volley_and_matches_dps_times_cycle(
    turret_ctx, missile_ctx, drone_ctx
) -> None:
    """守卫：``-21`` 是装备级齐射，且 ``-21 ≡ dps × cycle``。

    P6-B1 报告曾把 ``-11``（damageAlpha）当成齐射，误判成「引擎不给齐射」。
    正确 ID 是 ``-21``；本模块优先用它，并用恒等式自检。若有人把 ``-21``
    换成 ``-11``，这里会拿到 None → 红。
    """
    from noobbaozi.config import attributes as A  # noqa: PLC0415

    for ctx in (turret_ctx, missile_ctx, drone_ctx):
        items = ctx.variant().get("items") or []
        for item in items:
            raw = item.get("attributes") or {}
            dps = combat._raw_attr(raw, A.ATTR_DAMAGE_DPS)  # noqa: SLF001
            volley = combat._raw_attr(raw, A.ATTR_DAMAGE_VOLLEY)  # noqa: SLF001
            cycle = combat._raw_attr(raw, A.ATTR_CYCLE_TIME)  # noqa: SLF001
            if dps is None or volley is None or cycle is None:
                continue
            assert volley == pytest.approx(dps * cycle / 1000.0, rel=1e-9)

    for ctx, defender in ((turret_ctx, missile_ctx), (missile_ctx, turret_ctx)):
        result = combat.engagement(ctx, defender, range_m=3000.0)
        for weapon in result["weapons"]:
            if weapon["nominal_volley"] is None or weapon["cycle_s"] is None:
                continue
            assert weapon["volley_source"] == "engine(-21)"
            expected = weapon["nominal_dps"] * weapon["cycle_s"]
            # 严格恒等式在上一层用 raw 值验过（1e-9）；这里比较的是**取整后的输出**
            # （dps / cycle 都做了 3 位小数），所以容差放到 1e-3。
            assert weapon["nominal_volley"] == pytest.approx(expected, rel=1e-3)


# =============================================================================
# 5. wrecking 口径（默认含、可关）
# =============================================================================


def test_perfect_application_constant() -> None:
    assert combat.PERFECT_APPLICATION == pytest.approx(1.01505, rel=1e-12)
    assert combat.turret_damage_multiplier(1.0) == pytest.approx(1.01505, rel=1e-12)
    assert combat.turret_damage_multiplier(1.0, include_wrecking=False) == pytest.approx(1.0, rel=1e-12)


def test_wrecking_flag_scales_engagement(turret_ctx, missile_ctx) -> None:
    with_w = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0, angular_vel=0.01)
    without = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0, angular_vel=0.01,
                                include_wrecking=False)
    assert with_w["inputs"]["include_wrecking"] is True
    assert without["inputs"]["include_wrecking"] is False
    assert without["totals"]["effective_dps"] < with_w["totals"]["effective_dps"]
    ratio = with_w["totals"]["effective_dps"] / without["totals"]["effective_dps"]
    # 输出做了 3 位小数取整，所以给 1e-4 的相对容差（取整误差 ~1e-6）。
    assert ratio == pytest.approx(combat.PERFECT_APPLICATION, rel=1e-4)


# =============================================================================
# 6. 端到端语义
# =============================================================================


def test_hp_pool_and_ehp_are_both_reported_with_purpose(turret_ctx, missile_ctx) -> None:
    result = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0)
    survival = result["defender_survival"]
    layers = survival["hp_pool"]["layers"]
    assert survival["hp_pool"]["value"] == pytest.approx(
        layers["shield"] + layers["armor"] + layers["hull"], rel=1e-9
    )
    assert survival["ehp"]["value"] > survival["hp_pool"]["value"] > 0.0
    assert "damage_taken" in survival["hp_pool"]["compare_with"]
    assert survival["time_to_kill_s"]["value"] is not None
    assert result["totals"]["damage_split"] is not None
    assert sum(result["totals"]["damage_split"].values()) == pytest.approx(1.0, rel=1e-6)


def test_missile_out_of_range_scores_zero(missile_ctx, turret_ctx) -> None:
    far = combat.engagement(missile_ctx, turret_ctx, range_m=200000.0)
    weapon = far["weapons"][0]
    assert weapon["status"] == "degraded"
    assert weapon["reason"] == "out_of_missile_range"
    assert weapon["effective_dps"] == 0.0


def test_missile_application_uses_target_speed(missile_ctx, turret_ctx) -> None:
    slow = combat.engagement(missile_ctx, turret_ctx, range_m=3000.0, target_velocity=0.0)
    fast = combat.engagement(missile_ctx, turret_ctx, range_m=3000.0, target_velocity=2000.0)
    slow_factor = slow["weapons"][0]["application"]
    fast_factor = fast["weapons"][0]["application"]
    assert slow_factor > fast_factor
    assert fast_factor >= 0.0


def test_missile_drf_is_read_from_sde_by_name(missile_ctx, turret_ctx) -> None:
    """DRF 不在常量表里，靠 SDE 属性名反查（`aoeDamageReductionFactor`）。

    Scourge Light Missile 的 DRF = 0.604（EVE Uni DRF 表）。若按名字反查失败，
    这里会拿到 None（应用系数退化成 1.0 的「完美」口径），必须红。
    """
    result = combat.engagement(missile_ctx, turret_ctx, range_m=3000.0, target_velocity=2000.0)
    weapon = result["weapons"][0]
    assert weapon["inputs"]["damage_reduction_factor"] == pytest.approx(0.604, rel=1e-6)
    # 弹药属性已经被技能改过（Guided Missile Precision IV → ER 40×0.8=32）
    assert weapon["inputs"]["explosion_radius_m"] == pytest.approx(32.0, rel=1e-6)


def test_turret_hit_chance_drops_with_angular_velocity(turret_ctx, missile_ctx) -> None:
    still = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0, angular_vel=0.0)
    moving = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0, angular_vel=0.05)
    assert still["weapons"][0]["chance_to_hit"] > moving["weapons"][0]["chance_to_hit"]


def test_unsupported_disintegrator_gives_lower_bound_only(engine, index, missile_ctx) -> None:
    ctx = _ctx(engine, index, DISINTEGRATOR_EFT)
    result = combat.engagement(ctx, missile_ctx, range_m=3000.0)
    weapon = result["weapons"][0]
    assert weapon["status"] == "unsupported"
    assert weapon["effective_dps"] is None
    assert weapon["lower_bound_dps"] == pytest.approx(weapon["nominal_dps"], rel=1e-12)
    assert result["totals"]["coverage"] < 1.0
    assert result["defender_survival"]["time_to_kill_s"]["partial"] is True


def test_unsupported_drones_give_lower_bound_only(drone_ctx, missile_ctx) -> None:
    result = combat.engagement(drone_ctx, missile_ctx, range_m=3000.0)
    weapon = result["weapons"][0]
    assert weapon["family"] == "drone"
    assert weapon["status"] == "unsupported"
    assert weapon["lower_bound_dps"] == pytest.approx(weapon["nominal_dps"], rel=1e-12)
    assert result["totals"]["unsupported_dps"] == pytest.approx(weapon["nominal_dps"], rel=1e-12)


def test_empty_fit_reports_no_weapons(engine, index, missile_ctx) -> None:
    ctx = _ctx(engine, index, EMPTY_EFT)
    result = combat.engagement(ctx, missile_ctx, range_m=1000.0)
    assert result["weapons"] == []
    assert result["totals"]["effective_dps"] == 0.0
    assert result["defender_survival"]["time_to_kill_s"]["value"] is None
    assert any(flag.startswith("no_weapons") for flag in result["flags"])


def test_ammo_override_is_rejected(turret_ctx, missile_ctx) -> None:
    with pytest.raises(ValueError, match="ammo"):
        combat.engagement(turret_ctx, missile_ctx, range_m=1000.0, ammo=12789)


def test_exchange_is_symmetric_in_structure(turret_ctx, missile_ctx) -> None:
    result = combat.exchange(turret_ctx, missile_ctx, range_m=3000.0, angular_vel=0.01)
    a_ttk = result["a_on_b"]["defender_survival"]["time_to_kill_s"]["value"]
    b_ttk = result["b_on_a"]["defender_survival"]["time_to_kill_s"]["value"]
    assert a_ttk is not None and b_ttk is not None
    assert result["race"]["ratio_a_over_b"] == pytest.approx(a_ttk / b_ttk, rel=1e-6)
    assert result["race"]["faster"] in {"a", "b", "even"}
    # 两个方向各算各的：攻守互换后 nominal DPS 必须来自各自的 ctx
    assert result["a_on_b"]["attacker"]["ship"]["name"] == "Thorax"
    assert result["b_on_a"]["attacker"]["ship"]["name"] == "Caracal"


# =============================================================================
# 7. 组合变异（两处口径同时改坏 → 输出必须真的变）
# =============================================================================


def test_mutation_scale_mix_and_wrecking(missile_ctx, turret_ctx) -> None:
    """同时改坏「sig ÷1000」与「去掉 wrecking」，结果必须与正确值明显不同。

    这条不是重复守卫：它证明**两个口径是独立可观测的**（一个只动命中率，
    一个只动倍率），因此单条守卫不会被另一种改动掩盖。
    """
    correct = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0, angular_vel=0.01)
    weapon = correct["weapons"][0]
    tracking = weapon["inputs"]["tracking_rad_s"]
    cth_wrong = combat.turret_chance_to_hit(
        tracking=tracking, optimal_sig_radius=40.0, angular_vel=0.01,
        target_signature=125.0, range_m=3000.0, optimal_m=5400.0, falloff_m=5000.0,
    )
    wrong_dps = weapon["nominal_dps"] * combat.turret_damage_multiplier(
        cth_wrong, include_wrecking=False
    )
    assert wrong_dps != pytest.approx(correct["totals"]["effective_dps"], rel=1e-3)


def test_mutation_thermal_and_cycle(missile_ctx, turret_ctx) -> None:
    """同时漏热伤 + 用错周期单位（毫秒当秒）：齐射恒等式必须立刻垮掉。"""
    result = combat.engagement(turret_ctx, missile_ctx, range_m=3000.0)
    weapon = result["weapons"][0]
    split = dict(weapon["damage_split"])
    split.pop("thermal", None)
    broken_volley = sum(split.values())
    assert broken_volley != pytest.approx(weapon["nominal_volley"], rel=1e-3)

    wrong_cycle = weapon["cycle_s"] * 1000.0
    assert weapon["nominal_dps"] * wrong_cycle != pytest.approx(
        weapon["nominal_volley"], rel=1e-3
    )


# =============================================================================
# 8. 纯公式边界
# =============================================================================


def test_zero_inputs_are_not_silently_one() -> None:
    base = dict(tracking=1.0, optimal_sig_radius=40000.0, angular_vel=0.0,
                target_signature=100.0, range_m=1000.0, optimal_m=1000.0, falloff_m=1000.0)
    assert combat.turret_chance_to_hit(**base) == pytest.approx(1.0)
    assert combat.turret_chance_to_hit(**{**base, "tracking": 0.0}) == 0.0
    assert combat.turret_chance_to_hit(**{**base, "target_signature": 0.0}) == 0.0
    assert combat.missile_application_factor(
        explosion_radius_m=0.0, explosion_velocity_mps=10.0,
        damage_reduction_factor=0.682, target_velocity=100.0, target_signature=100.0
    ) == 1.0
    assert combat.missile_application_factor(
        explosion_radius_m=40.0, explosion_velocity_mps=170.0,
        damage_reduction_factor=0.604, target_velocity=100.0, target_signature=0.0
    ) == 0.0


def test_units_fmt_rejects_nan() -> None:
    """NaN 不许流进响应（fmt 抛异常比静默输出 NaN 好）。"""
    with pytest.raises(ValueError):
        from noobbaozi.project import units  # noqa: PLC0415

        units.fmt(math.nan)
