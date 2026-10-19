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


def test_missile_drf_comes_from_registered_constant(missile_ctx, turret_ctx) -> None:
    """DRF 走常量表（Lead 已登记 1353），取值必须是弹药上的 0.604。

    Scourge Light Missile 的 DRF = 0.604（EVE Uni DRF 表）。若常量没登记/读错，
    这里会拿到 None（应用系数退化成 1.0 的「完美」口径），必须红。
    """
    from noobbaozi.config import attributes as A  # noqa: PLC0415

    assert A.ATTR_AOE_DAMAGE_REDUCTION_FACTOR == 1353
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


# =============================================================================
# 9. P6-B3：场景扫描 / 时间线 / 战报锚点（P1 那对真实互杀）
# =============================================================================

P1_EXEQUROR = (
    "[Exequror Navy Issue, P1]\n"
    "Damage Control II\n1600mm Steel Plates II\n"
    "Magnetic Field Stabilizer II\nMagnetic Field Stabilizer II\nMagnetic Field Stabilizer II\n"
    "Multispectrum Coating II\n\n"
    "50MN Y-T8 Compact Microwarpdrive\nWarp Scrambler II\n\n"
    "Heavy Neutron Blaster II, Void M\nHeavy Neutron Blaster II, Void M\n"
    "Heavy Neutron Blaster II, Void M\nHeavy Neutron Blaster II, Void M\n"
)
"""P1 的攻击方：来自战报 139280812（行为 139280805 的 solo 击杀者）。"""

P1_FLYCATCHER = (
    "[Flycatcher, P1]\n"
    "Damage Control II\n\n"
    "Medium Shield Extender II\nMultispectrum Shield Hardener II\n"
    "5MN Y-T8 Compact Microwarpdrive\nMedium Shield Extender II\nWarp Scrambler II\n\n"
    "Interdiction Sphere Launcher I, Warp Disrupt Probe\nSmall Energy Nosferatu II\n\n"
    "Small Core Defense Field Extender I\nSmall EM Shield Reinforcer II\n"
)
"""P1 的守方：来自战报 139280805（damage_taken = 4158，solo 击杀）。"""

P1_DAMAGE_TAKEN = 4158.0
P1_WINDOW_S = 9.0
"""两条战报相隔 9 秒（13:36:04 → 13:36:13）。注意：这是**死亡间隔**，不是交战时长。"""


@pytest.fixture(scope="module")
def p1_attacker(engine, index) -> ViewContext:
    return _ctx(engine, index, P1_EXEQUROR)


@pytest.fixture(scope="module")
def p1_defender(engine, index) -> ViewContext:
    return _ctx(engine, index, P1_FLYCATCHER)


ANCHOR_RANGES = [0.0, 1500.0, 3000.0, 4500.0, 6000.0, 9000.0, 15000.0]
ANCHOR_ANGLES = [0.0, 0.01, 0.02, 0.05, 0.1, 0.2]


def test_sweep_shape_and_angular_monotonicity(p1_attacker, p1_defender) -> None:
    result = combat.sweep(p1_attacker, p1_defender, ranges_m=ANCHOR_RANGES,
                          angulars=ANCHOR_ANGLES, target_velocity=224.0)
    assert result["shape"] == [len(ANCHOR_RANGES), len(ANCHOR_ANGLES)]
    assert len(result["cells"]) == len(ANCHOR_RANGES) * len(ANCHOR_ANGLES)
    assert result["best"] is not None

    by_key = {(cell["range_m"], cell["angular_vel"]): cell for cell in result["cells"]}
    for range_m in ANCHOR_RANGES:
        still = by_key[(round(range_m, 3), 0.0)]["effective_dps"]
        fast = by_key[(round(range_m, 3), 0.2)]["effective_dps"]
        assert still >= fast, f"角速度变大时有效 DPS 不应上升（range={range_m}）"


def test_sweep_best_cell_matches_engagement(p1_attacker, p1_defender) -> None:
    result = combat.sweep(p1_attacker, p1_defender, ranges_m=ANCHOR_RANGES[:4],
                          angulars=ANCHOR_ANGLES[:4], target_velocity=224.0)
    best = result["best"]
    direct = combat.engagement(p1_attacker, p1_defender, range_m=best["range_m"],
                               angular_vel=best["angular_vel"], target_velocity=224.0)
    assert best["effective_dps"] == pytest.approx(
        direct["totals"]["effective_dps"], rel=1e-9
    )


def test_timeline_is_linear_and_consistent(p1_attacker, p1_defender) -> None:
    result = combat.timeline(p1_attacker, p1_defender, range_m=3000.0,
                             angular_vel=0.05, points=5)
    first = result["samples"][0]
    assert first["t_s"] == 0.0
    assert first["a_hp_fraction"] == pytest.approx(1.0)
    assert first["b_hp_fraction"] == pytest.approx(1.0)

    # Flycatcher 没有对敌武器 → 它一定先死，A 不会死
    assert result["rates"]["b_dps_on_a"] == 0.0
    assert result["endpoints"]["a_dies_at_s"] is None
    assert result["endpoints"]["b_dies_at_s"] > 0
    assert result["endpoints"]["first_to_die"] is None  # 有一方永不死亡

    # 线性扣血：最后一点必须打空守方 EHP
    last = result["samples"][-1]
    assert last["b_remaining_ehp"] == pytest.approx(0.0, abs=1e-3)
    assert last["b_hp_fraction"] == pytest.approx(0.0, abs=1e-6)

    # 单调不增
    values = [sample["b_remaining_ehp"] for sample in result["samples"]]
    assert values == sorted(values, reverse=True)


def test_damage_window_converts_raw_to_hp(p1_attacker, p1_defender) -> None:
    window = combat.damage_window(p1_attacker, p1_defender, window_s=P1_WINDOW_S,
                                  range_m=0.0, angular_vel=0.0, target_velocity=224.0)
    assert window["raw_damage"] > window["hp_removed"] > 0.0, "抗性/共振率必须让扣血 < 原始伤害"
    longer = combat.damage_window(p1_attacker, p1_defender, window_s=30.0,
                                  range_m=0.0, angular_vel=0.0, target_velocity=224.0)
    assert longer["hp_removed"] > window["hp_removed"]
    # 扣血不能超过总血池（层模型会在血池耗尽时停手）
    survival = combat.engagement(p1_attacker, p1_defender, range_m=0.0,
                                 target_velocity=224.0)["defender_survival"]
    assert longer["hp_removed"] <= survival["hp_pool"]["value"] + 1e-6


def test_p1_anchor_strict_9s_is_not_explainable(p1_attacker, p1_defender) -> None:
    """硬要求：9 秒内能不能打出 4158 点血？—— **不能**，并给出最短所需时间。

    这不是「模型坏了」：9 秒是两次死亡的间隔，不是交战时长；模型给出的是
    「若只靠 Exequror 一条船、从零距离静止开打，至少需要 25 秒」。
    """
    result = combat.explain_anchor(
        p1_attacker, p1_defender, observed_hp=P1_DAMAGE_TAKEN, window_s=P1_WINDOW_S,
        ranges_m=ANCHOR_RANGES, angulars=ANCHOR_ANGLES, target_velocity=224.0,
    )
    assert result["explainable"] is False
    assert result["region_count"] == 0
    assert result["max_hp_removed_in_window"] < P1_DAMAGE_TAKEN
    assert result["best"]["window_needed_s"] > P1_WINDOW_S
    assert result["reasons"], "不可解释时必须给出原因"

    # 最短所需时间 ~25s：与「1500 点/9 秒 → 4158 点需要 ~25 秒」自洽
    assert 20.0 < result["best"]["window_needed_s"] < 40.0
    assert result["required_hp_dps"] == pytest.approx(P1_DAMAGE_TAKEN / P1_WINDOW_S, rel=1e-6)


def test_p1_anchor_becomes_explainable_with_enough_time(p1_attacker, p1_defender) -> None:
    """把窗口放宽到 60 秒必须可解释 —— 证明上面的「不可解释」是**时长问题**，
    不是公式坏了（否则这条会一起红）。"""
    result = combat.explain_anchor(
        p1_attacker, p1_defender, observed_hp=P1_DAMAGE_TAKEN, window_s=60.0,
        ranges_m=ANCHOR_RANGES, angulars=ANCHOR_ANGLES, target_velocity=224.0,
    )
    assert result["explainable"] is True
    assert result["region_count"] > 0
    assert result["region_count"] <= len(ANCHOR_RANGES) * len(ANCHOR_ANGLES)
    assert result["max_hp_removed_in_window"] >= P1_DAMAGE_TAKEN


def test_p1_anchor_best_is_a_grid_cell_not_a_fitted_parameter(p1_attacker, p1_defender) -> None:
    """禁止拟合：`best` 必须是网格里的格子，range/angular 只能是采样值。"""
    result = combat.explain_anchor(
        p1_attacker, p1_defender, observed_hp=P1_DAMAGE_TAKEN, window_s=60.0,
        ranges_m=ANCHOR_RANGES, angulars=ANCHOR_ANGLES, target_velocity=224.0,
    )
    best = result["best"]
    assert best["range_m"] in [round(value, 3) for value in ANCHOR_RANGES]
    assert best["angular_vel"] in [round(value, 6) for value in ANCHOR_ANGLES]


def test_p1_reverse_leg_has_no_weapons(p1_defender, p1_attacker) -> None:
    """P1 的反向腿：Flycatcher 只有拦截弹/nos，打出 0 伤害 —— ESI 不给攻击方配装，
    12561 的反向锚点无法用「Flycatcher 打 Exequror」来复现（这是事实，不是缺陷）。"""
    result = combat.engagement(p1_defender, p1_attacker, range_m=3000.0)
    assert result["totals"]["effective_dps"] == 0.0
    assert any(flag.startswith("no_weapons") for flag in result["flags"])

# =============================================================================
# 10. P6-B4：编队聚合（同型 × 数量，只做量级校验）
# =============================================================================

FAKE_ATTACKERS_BY_GROUP: dict = {
    "count": 9,
    "damaging_count": 8,
    "by_ship_group": [
        {"group": "Battleship", "count": 8, "damage": 11360, "damage_pct": 0.904387},
        {"group": "Logistics", "count": 1, "damage": 1201, "damage_pct": 0.095613},
    ],
}
"""`fit_killmail(...)["attackers"]` 的**结构**（数字取自战报 139280812 的真实聚合）。"""


def test_fleet_from_killmail_uses_killmail_counts(missile_ctx) -> None:
    """数量必须来自战报 `by_ship_group`：Cruiser × 8（假数据里没有 Cruiser，这里换成真结构）。"""
    attackers = {
        "by_ship_group": [
            {"group": "Cruiser", "count": 3, "damage": 900, "damage_pct": 0.9},
            {"group": "Frigate", "count": 5, "damage": 100, "damage_pct": 0.1},
        ]
    }
    derived = combat.fleet_from_killmail(attackers, missile_ctx)  # Caracal = Cruiser
    assert derived["group"] == "Cruiser"
    assert derived["count"] == 3
    assert derived["source"] == "killmail.attackers.by_ship_group"
    assert derived["damage_pct"] == pytest.approx(0.9, rel=1e-6)


def test_fleet_from_killmail_rejects_unknown_group(p1_defender) -> None:
    """模板是 Interdictor，战报里只有 Battleship/Logistics → 必须报错，不许编数量。"""
    with pytest.raises(ValueError, match="Interdictor"):
        combat.fleet_from_killmail(FAKE_ATTACKERS_BY_GROUP, p1_defender)


def test_fleet_from_killmail_rejects_missing_aggregate(p1_attacker) -> None:
    with pytest.raises(ValueError, match="by_ship_group"):
        combat.fleet_from_killmail({}, p1_attacker)


def test_fleet_engagement_marks_assumption_and_scales(p1_attacker, p1_defender) -> None:
    single = combat.engagement(p1_attacker, p1_defender, range_m=0.0, target_velocity=224.0)
    fleet = combat.fleet_engagement(
        p1_attacker, p1_defender, count=4, range_m=0.0, target_velocity=224.0,
        window_s=9.0, observed_hp=4158.0,
    )
    assert fleet["fleet_assumption"] is True
    assert "非个体配装" in fleet["fleet_note"]
    assert fleet["count"] == 4
    assert fleet["totals"]["fleet_effective_dps"] == pytest.approx(
        single["totals"]["effective_dps"] * 4, rel=1e-6
    )
    assert fleet["defender_survival"]["time_to_kill_s"]["fleet_assumption"] is True
    assert "fleet_assumption" in fleet["flags"]
    assert "order_of_magnitude_only" in fleet["flags"]
    assert fleet["window"]["magnitude"]["verdict"] in {
        "same_order", "predicted_high_x", "predicted_low_x"
    } or fleet["window"]["magnitude"]["verdict"].startswith("predicted_")


def test_fleet_count_is_not_hardcoded(p1_attacker, p1_defender) -> None:
    """非永真：两个不同数量必须给出不同结果（写死 count 的实现在这里会红）。"""
    three = combat.fleet_engagement(p1_attacker, p1_defender, count=3,
                                    range_m=0.0, target_velocity=224.0)
    eight = combat.fleet_engagement(p1_attacker, p1_defender, count=8,
                                    range_m=0.0, target_velocity=224.0)
    assert eight["totals"]["fleet_effective_dps"] > three["totals"]["fleet_effective_dps"]
    assert eight["defender_survival"]["time_to_kill_s"]["value"] < (
        three["defender_survival"]["time_to_kill_s"]["value"]
    )


def test_fleet_engagement_takes_count_from_killmail(p1_attacker) -> None:
    """直接传 attackers 时，数量取战报聚合（Cruiser × 2）。"""
    attackers = {"by_ship_group": [{"group": "Cruiser", "count": 2, "damage_pct": 1.0}]}
    result = combat.fleet_engagement(
        p1_attacker, p1_attacker, killmail_attackers=attackers, range_m=0.0,
    )
    assert result["count"] == 2
    assert result["count_source"]["source"] == "killmail.attackers.by_ship_group"


def test_fleet_engagement_requires_exactly_one_count_source(p1_attacker, p1_defender) -> None:
    with pytest.raises(ValueError, match="count"):
        combat.fleet_engagement(p1_attacker, p1_defender, range_m=0.0)
    with pytest.raises(ValueError, match="count"):
        combat.fleet_engagement(p1_attacker, p1_defender, count=2,
                                killmail_attackers=FAKE_ATTACKERS_BY_GROUP, range_m=0.0)


def test_magnitude_check_is_order_of_magnitude_only() -> None:
    same = combat.magnitude_check(12561.0 * 0.5, 12561.0)
    assert same["verdict"] == "same_order"
    high = combat.magnitude_check(12561.0 * 50.0, 12561.0)
    assert high["verdict"].startswith("predicted_high_x")
    low = combat.magnitude_check(12561.0 / 50.0, 12561.0)
    assert low["verdict"].startswith("predicted_low_x")
    assert "不做精确对拍" in same["note"]


def test_fleet_size_for_damage_gives_an_interval(p1_attacker, p1_defender) -> None:
    """反向用法：4158 点血 / 9 秒 → 需要几条同型 Exequror？给区间不是单点。"""
    result = combat.fleet_size_for_damage(
        p1_attacker, p1_defender, observed_hp=4158.0, window_s=9.0,
        ranges_m=ANCHOR_RANGES, angulars=ANCHOR_ANGLES, target_velocity=224.0,
    )
    low, high = result["count_range"]
    assert result["fleet_assumption"] is True
    assert low >= 2, "单船 9 秒打不出 4158（P6-B3 已证），所以至少 2 条"
    assert high > low, "几何未知 → 必须是区间，不能是单点"
    assert result["best_case"]["needed_count"] == low
    assert result["worst_case"]["needed_count"] == high


def test_fleet_size_for_damage_rejects_zero_damage(p1_defender, p1_attacker) -> None:
    """守方当攻方（Flycatcher 没对敌武器）→ 无法反推，必须给原因而不是一个假数。"""
    result = combat.fleet_size_for_damage(
        p1_defender, p1_attacker, observed_hp=12561.0, window_s=9.0,
        ranges_m=ANCHOR_RANGES, angulars=ANCHOR_ANGLES,
    )
    assert result["count_range"] is None
    assert result["reasons"]

# ---- 真实反向腿：8×Apocalypse（模板来自战报 139129161，同军团 Cosmic-Wanderers）----

P1_APOCALYPSE = """[Apocalypse, Killmail 139129161]

1600mm Steel Plates II
1600mm Steel Plates II
Reactive Armor Hardener
Multispectrum Coating II
Multispectrum Coating II
Heat Sink II
Damage Control II

500MN Y-T8 Compact Microwarpdrive
Heavy F-RX Compact Capacitor Booster, Navy Cap Booster 800
Tracking Computer II, Optimal Range Script
Large Micro Jump Drive

Mega Pulse Laser II, Imperial Navy Radio L
Mega Pulse Laser II, Imperial Navy Radio L
Mega Pulse Laser II, Imperial Navy Radio L
Mega Pulse Laser II, Imperial Navy Radio L
Mega Pulse Laser II, Imperial Navy Radio L
Mega Pulse Laser II, Imperial Navy Radio L
Mega Pulse Laser II, Imperial Navy Radio L
Mega Pulse Laser II, Imperial Navy Radio L

Large Trimark Armor Pump I
Large Trimark Armor Pump I
Large Trimark Armor Pump I

Warrior I x5
Valkyrie I x5
"""
"""P1 反向腿的攻方模板：战报 139129161（Cosmic-Wanderers 的 Apocalypse 阵亡配装）。"""

P1_ATTACKERS_REAL = {
    "count": 10,
    "damaging_count": 9,
    "by_ship_group": [
        {"group": "Battleship", "count": 8, "damage": 11360, "damage_pct": 0.904387},
        {"group": "Logistics", "count": 1, "damage": 1201, "damage_pct": 0.095613},
        {"group": "Interceptor", "count": 1, "damage": 0, "damage_pct": 0.0},
    ],
}
"""战报 139280812 的 `attackers.by_ship_group` 原样（8×Battleship 是**战报事实**）。"""


@pytest.fixture(scope="module")
def p1_apocalypse(engine, index) -> ViewContext:
    return _ctx(engine, index, P1_APOCALYPSE)


def test_p1_reverse_fleet_count_comes_from_the_killmail(p1_apocalypse) -> None:
    derived = combat.fleet_from_killmail(P1_ATTACKERS_REAL, p1_apocalypse)
    assert derived["group"] == "Battleship"
    assert derived["count"] == 8
    assert derived["source"] == "killmail.attackers.by_ship_group"


def test_p1_reverse_fleet_magnitude_is_same_order(p1_apocalypse, p1_attacker) -> None:
    """8×Apocalypse 打 Exequror：9 秒窗口量级 vs 战报 12561 —— 只验「同数量级」。"""
    result = combat.fleet_engagement(
        p1_apocalypse, p1_attacker, killmail_attackers=P1_ATTACKERS_REAL, range_m=20000.0,
        angular_vel=0.0, window_s=9.0, observed_hp=12561.0,
    )
    assert result["count"] == 8
    assert result["fleet_assumption"] is True
    assert "非个体配装" in result["fleet_note"]
    magnitude = result["window"]["magnitude"]
    assert magnitude["verdict"] == "same_order", magnitude
    assert 0.1 <= magnitude["ratio"] <= 10.0
    # 覆盖率 < 1 是事实：Apocalypse 带 10 架无人机，无人机应用本层不建模
    assert result["totals"]["coverage"] < 1.0


def test_p1_reverse_fleet_count_changes_result(p1_apocalypse, p1_attacker) -> None:
    """非永真：把战报数量改成 3，结果必须跟着变（写死 8 的实现在这里红）。"""
    fewer = dict(P1_ATTACKERS_REAL)
    fewer["by_ship_group"] = [
        {"group": "Battleship", "count": 3, "damage": 3000, "damage_pct": 1.0},
    ]
    three = combat.fleet_engagement(p1_apocalypse, p1_attacker, killmail_attackers=fewer,
                                    range_m=20000.0)
    eight = combat.fleet_engagement(p1_apocalypse, p1_attacker, killmail_attackers=P1_ATTACKERS_REAL,
                                    range_m=20000.0)
    assert three["count"] == 3
    assert eight["count"] == 8
    assert eight["totals"]["fleet_effective_dps"] > three["totals"]["fleet_effective_dps"]


def test_fleet_size_has_practical_range_and_degenerate_flag(p1_apocalypse, p1_attacker) -> None:
    result = combat.fleet_size_for_damage(
        p1_apocalypse, p1_attacker, observed_hp=12561.0, window_s=9.0,
        ranges_m=[0.0, 5000.0, 20000.0, 60000.0, 120000.0],
        angulars=[0.0, 0.1, 0.3],
    )
    low, high = result["count_range"]
    practical_low, practical_high = result["count_range_practical"]
    assert low == practical_low
    assert 1 <= low <= practical_high <= high
    assert result["degenerate_upper"] is True
    assert "count_range_practical" in result["assumptions"]["degenerate_upper_note"]




