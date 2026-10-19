"""交战数学（P6-B）：命中率 / 伤害应用 / 有效 DPS / 换血比。

本模块属于**第 ③ 层**（软件设计 §7.8，主人批准）：属性计算全交引擎，本层只做
「确定性的、可对拍的、必须显式声明假设的」战斗数学。三条硬约束逐条落地：

1. **公式有公开出处**（见下）；不得凭记忆写、不得「看起来合理」就上。
2. **有第二把尺子**：所有公式都与 pyfa 源码逐位对拍过
   （`graphs/data/fitApplicationProfile/calc/{turret,launcher}.py` @ commit
   `d2722a4c8b2ec9cdfded5024cb97df362ac513da`；对拍样本见 `tests/test_combat.py`
   的 `PYFA_REFERENCE`，容差 `TOLERANCE_REL`）。
3. **不反向影响 ①②**：本模块只**读**引擎给的属性（`ctx.variant()` 的原始值），
   一个数都不改；冲突以引擎为准。

## 公式出处（逐条）

**炮台命中率** —— EVE University Wiki《Turret mechanics》`oldid=228576`：

```
chance = 0.5 ^ [ (angular × optimalSigRadius / (tracking × signature))²
               + (max(0, range − optimal) / falloff)² ]
```

`optimalSigRadius` 是炮台签名分辨率，**固定 40000 m**（见 `TURRET_SIGNATURE_RESOLUTION`）。

**炮台伤害倍率** —— 同页「Damage / Average damage」：

```
multiplier = 0.5 × min(cth² + 0.98·cth + 0.0501, 6·cth)
```

完美应用（cth=1）时该值是 **1.01505**，不是 1.0：命中率 100% 时每发在 50%–149%
均匀分布，另有 1% 概率 wrecking（×3），所以期望伤害比纸面伤害高 1.5%。
`include_wrecking=False` 时除以 `PERFECT_APPLICATION` 归一到纸面口径。

**导弹伤害应用** —— EVE University Wiki《Missile mechanics》`oldid=224314`：

```
factor = min(1, S/E, (S·Ve / (E·Vt))^drf)
```

**角速度**是**输入参数**（`angular_vel`），本模块不复刻「中心到中心」的几何假设
（那条假设在 P6-B1 报告里已单独列出）；调用方给多少就按多少算，并在响应的
`assumptions` 里回显。

## 单位陷阱（P6-B1 实测，必须有守卫）

`config/attributes.py` 把 `trackingSpeed`(160) 与 `optimalSigRadius`(620) 都登记成
`SCALE_KILO`，投影层的 `ctx.attrs()` 会把**两者同时 ÷1000**（tracking 391.67→0.39167，
sig 40000→40）。这一对同时缩放时公式**等价**，但**混用**（raw tracking + 40 sig，
或 0.39 tracking + 40000 sig）会差 1000 倍。
所以本模块统一用**引擎原始值**（`ctx.variant()`），并在炮台输入里保留原始
`optimal_sig_radius`，测试 `test_optimal_sig_must_be_40000` 钉死这条。

## 伤害属性 ID（同批实测）

`114 em / 116 explosive / 117 kinetic / 118 thermal`，**115 不存在**。
按连续编号 114–117 求和会**漏掉热伤**（Void M 会从 35.4 变成 17.7），
`test_damage_ids_are_114_116_117_118` 钉死。

## 齐射（volley）是 `-21`，不是 `-11`

P6-B1 报告里有一处**笔误需要更正**：当时看的是 `-11`，以为引擎不给齐射。
`-11` 是 `damageAlpha`（首轮齐射的另一种口径，逐件常常没有值）；
**装备级齐射是 `-21 damageVolley`**，引擎逐件会给。本模块优先用 `-21`，
并用 `volley ≡ dps × cycle` 的恒等式兜底/自检（`volley_source` 标出来源）。

## 覆盖范围与 `unsupported`
**已建模**：炮台（hybrid / laser / projectile，走命中率 + 伤害倍率）、导弹
（走应用系数）。**未建模**（`status="unsupported"`，只给 `lower_bound_dps` = 名义
DPS 的完美应用值，不猜）：无人机、舰载机、智能炸弹、分解器（Entropic
Disintegrator，伤害有 ramp）、以及其他表外武器。`totals.coverage` 告诉调用方
「有名义 DPS 的百分之多少真的被算了」，`time_to_kill_s` 在 `coverage < 1` 时是
**上界**（少算武器 → 击穿时间偏长）。
"""

from __future__ import annotations

import math
from typing import Mapping

from noobbaozi.config import attributes as A
from noobbaozi.project import units
from noobbaozi.project.views import shared

__all__ = [
    "PERFECT_APPLICATION",
    "TOLERANCE_REL",
    "TURRET_SIGNATURE_RESOLUTION",
    "damage_window",
    "engagement",
    "exchange",
    "explain_anchor",
    "missile_application_factor",
    "sweep",
    "timeline",
    "turret_chance_to_hit",
    "turret_damage_multiplier",
]

TURRET_SIGNATURE_RESOLUTION: float = 40000.0
"""炮台签名分辨率（`optimalSigRadius`，m）。EVE 把 old-style 的分辨率合并进 tracking 后
所有炮台固定 40 km（EVE Uni《Turret mechanics》「A long, long time ago…」注记）。

**它不是 40**：属性表把 620 标成 ×1000（展示口径），投影层 `ctx.attrs()` 会给出 40；
战斗数学必须用引擎原始值 40000（pyfa 也是直接用 `getModifiedItemAttr`）。
"""

PERFECT_APPLICATION: float = 0.5 * (1.0 + 0.98 + 0.0501)
"""完美应用（命中率 100%）的期望伤害倍率 = 1.01505。

来源：EVE Uni《Turret mechanics》「Average damage」：0.99×0.995 + 0.01×3 = 1.01505。
含 1% wrecking（×3）。`include_wrecking=False` 时用它把倍率归一到纸面口径。
"""

TOLERANCE_REL: float = 1e-6
"""与 pyfa 对拍的相对容差（实测公式层差 < 1e-15，留 1e-6 给浮点与版本差异）。"""

ATTR_AOE_DAMAGE_REDUCTION_FACTOR_NAME: str = "aoeDamageReductionFactor"
"""导弹 DRF 的 **SDE 属性名**（只用于文档与交叉核对；取值走常量表
`A.ATTR_AOE_DAMAGE_REDUCTION_FACTOR`）。

P6-B2 时该常量还没登记，本模块曾按名字在 SDE 里反查；Lead 已在
`config/attributes.py` 登记 1353，现在直接 import 常量（见 `_fill_missile`）。
"""

SWEEP = "sweep"
"""内部标记：一次 `_weapon_entries` 扫描。"""

TURRET_FAMILIES: frozenset[str] = frozenset({"hybrid", "laser", "projectile"})
"""走「命中率 × 伤害倍率」的武器家族（`shared.FAMILY_BY_GROUP` 的三类）。"""

DISINTEGRATOR_HINT = "Entropic Disintegrator"
"""分解器的 group/名字特征：**既不是炮台也不是导弹**（伤害有 ramp，本层不建模）。"""

_INT_PREFIX = "type_"


# =============================================================================
# 纯公式（公开 API：测试与上层都可以直接对拍）
# =============================================================================


def turret_chance_to_hit(
    *,
    tracking: float | None,
    optimal_sig_radius: float | None,
    angular_vel: float | None,
    target_signature: float | None,
    range_m: float | None,
    optimal_m: float | None,
    falloff_m: float | None,
) -> float:
    """炮台命中率（EVE Uni 单一指数形式，见模块 docstring）。

    边界（与 pyfa `calcTrackingFactor` / `eos.calc.calculateRangeFactor` 同口径）：
    - `tracking <= 0` 或 `target_signature <= 0` → **0**（打不中，不是 1）；
    - `angular_vel <= 0` → 跟踪项 0；
    - `falloff <= 0` → 距离项退化成硬边界（optimal 内 1、外 0）；
    - `range <= optimal` → 距离项 0（`max(0, …)`）。
    """
    if tracking is None or tracking <= 0.0:
        return 0.0
    if target_signature is None or target_signature <= 0.0:
        return 0.0
    if optimal_sig_radius is None or optimal_sig_radius <= 0.0:
        return 0.0

    if angular_vel is None or angular_vel <= 0.0:
        tracking_term = 0.0
    else:
        tracking_term = (angular_vel * optimal_sig_radius) / (tracking * target_signature)

    if falloff_m is None or falloff_m <= 0.0:
        beyond = 0.0 if (range_m is None or range_m <= (optimal_m or 0.0)) else math.inf
    else:
        beyond = max(0.0, (range_m or 0.0) - (optimal_m or 0.0)) / falloff_m

    return 0.5 ** (tracking_term * tracking_term + beyond * beyond)


def turret_damage_multiplier(chance_to_hit: float, *, include_wrecking: bool = True) -> float:
    """命中率 → 期望伤害倍率（闭式，见模块 docstring）。

    `include_wrecking=True`（默认，与 pyfa 一致）：完美应用 = 1.01505。
    `False`：除以 `PERFECT_APPLICATION`，完美应用 = 1.0（纸面口径）。
    """
    cth = max(0.0, min(1.0, float(chance_to_hit)))
    multiplier = 0.5 * min(cth * cth + 0.98 * cth + 0.0501, 6.0 * cth)
    if include_wrecking:
        return multiplier
    return multiplier / PERFECT_APPLICATION


def missile_application_factor(
    *,
    explosion_radius_m: float | None,
    explosion_velocity_mps: float | None,
    damage_reduction_factor: float | None,
    target_velocity: float | None,
    target_signature: float | None,
) -> float:
    """导弹应用系数 `min(1, S/E, (S·Ve/(E·Vt))^drf)`（EVE Uni，见模块 docstring）。

    边界：`E <= 0` → 1（pyfa 跳过两项，等价）；`Vt <= 0` → 只比 `S/E`；
    `S <= 0` → 0。导弹没有 wrecking，所以与 `include_wrecking` 无关。
    """
    if explosion_radius_m is None or explosion_radius_m <= 0.0:
        return 1.0
    sig = target_signature or 0.0
    if sig <= 0.0:
        return 0.0

    factors = [1.0, sig / explosion_radius_m]
    if target_velocity is not None and target_velocity > 0.0:
        drf = 1.0 if damage_reduction_factor is None else float(damage_reduction_factor)
        base = (sig * (explosion_velocity_mps or 0.0)) / (explosion_radius_m * target_velocity)
        if base > 0.0:
            factors.append(base ** drf)
        else:
            factors.append(0.0)
    return min(factors)


# =============================================================================
# 属性读取（只读引擎原始值）
# =============================================================================


def _raw_attr(attrs: Mapping | dict, attribute_id: int) -> float | None:
    """原始属性值（**不做单位换算**：战斗公式要的是 dogma 原生量纲）。"""
    entry = attrs.get(attribute_id, attrs.get(str(attribute_id))) if isinstance(attrs, Mapping) else None
    if isinstance(entry, Mapping):
        entry = entry.get("value")
    if entry is None:
        return None
    return float(entry)


def _raw_ship_attrs(ctx) -> dict[int, float]:
    """船体属性的**原始值** `{id: float}`（空值跳过，不补 0）。"""
    raw = (ctx.variant().get("ship") or {}).get("attributes") or {}
    return {
        int(key): float(entry.get("value") if isinstance(entry, Mapping) else entry)
        for key, entry in raw.items()
        if (entry.get("value") if isinstance(entry, Mapping) else entry) is not None
    }


def _ship_speed(ctx) -> float:
    """船体 `maxVelocity`（m/s，原始值；缺省 0）。"""
    return _raw_ship_attrs(ctx).get(A.ATTR_MAX_VELOCITY) or 0.0


def _ship_label(ctx) -> str | None:
    return (ctx.fit or {}).get("name")


def _ship_block(ctx) -> dict:
    ship = (ctx.fit or {}).get("ship") or {}
    type_id = ship.get("type_id") if isinstance(ship, Mapping) else None
    row = ctx.index.get_type(type_id) if (ctx.index is not None and isinstance(type_id, int)) else None
    return {
        "type_id": int(type_id) if isinstance(type_id, int) else None,
        "name": (row or {}).get("name"),
    }


def _flat_attributes(calculation_item: Mapping) -> dict[int, float]:
    """单个 calculation item 的原始属性（`charge` 里那一层同样适用）。"""
    raw = (calculation_item or {}).get("attributes") or {}
    return {
        int(key): float(entry.get("value") if isinstance(entry, Mapping) else entry)
        for key, entry in raw.items()
        if (entry.get("value") if isinstance(entry, Mapping) else entry) is not None
    }


def _damage_split(charge_attrs: Mapping[int, float], multiplier: float) -> dict[str, float] | None:
    """弹药四系伤害（× `damageMultiplier`）→ `{em, explosive, kinetic, thermal}`。

    **属性 ID 来自 `shared.DAMAGE_TYPES`**（114/116/117/118）。115 不存在；
    按连续编号求和会漏热伤（见模块 docstring）。
    """
    values = {name: (charge_attrs.get(attribute_id) or 0.0) * multiplier
              for name, attribute_id in shared.DAMAGE_TYPES}
    if not any(values.values()):
        return None
    return values


def _split_fractions(split: Mapping[str, float] | None) -> dict[str, float] | None:
    if not split:
        return None
    total = sum(split.values())
    if total <= 0.0:
        return None
    return {name: units.fmt(value / total, 6) for name, value in split.items()}


# =============================================================================
# 武器扫描
# =============================================================================


def _weapon_entries(ctx) -> list[dict]:
    """逐件扫出武器，并给出「能不能按已溯源公式算」的判定。

    判定复用 `views/shared.classify_weapon()`（**判定只此一份**：什么算武器、family 是
    什么，与 `firepower.weapons[]` 同源），但属性一律取**原始值**。
    """
    merged = ctx.items()
    raw_items = ctx.variant().get("items") or []
    entries: list[dict] = []

    for position, item in enumerate(merged):
        if item.get("slot_type") == shared.SLOT_CARGO:
            continue
        raw = raw_items[position] if position < len(raw_items) else {}
        raw_attrs = _flat_attributes(raw)
        charge_attrs = _flat_attributes(raw.get("charge") or {})
        is_weapon, family = shared.classify_weapon(item, charge_attrs)
        if not is_weapon:
            continue

        count = max(int(item.get("quantity") or 1), 1)
        nominal = (_raw_attr(raw_attrs, A.ATTR_DAMAGE_DPS) or 0.0) * count
        cycle_raw = _raw_attr(raw_attrs, A.ATTR_CYCLE_TIME)
        cycle_s = units.ms_to_s(cycle_raw) if cycle_raw else None
        multiplier = _raw_attr(raw_attrs, A.ATTR_DAMAGE_MULTIPLIER)
        if multiplier is None:
            multiplier = 1.0
        split = _damage_split(charge_attrs, multiplier)

        # 齐射优先用引擎的装备级 `-21`（属性全交引擎）；缺失时才用恒等式合成。
        volley_raw = _raw_attr(raw_attrs, A.ATTR_DAMAGE_VOLLEY)
        if volley_raw is not None:
            nominal_volley = units.fmt(volley_raw * count)
            volley_source = "engine(-21)"
        elif cycle_s is not None:
            nominal_volley = units.fmt(nominal * cycle_s)
            volley_source = "derived(dps x cycle)"
        else:
            nominal_volley = None
            volley_source = "unknown"

        entry = {
            "type_id": item.get("type_id"),
            "name": item.get("name"),
            "group_name": item.get("group_name"),
            "family": family,
            "count": count,
            "nominal_dps": units.fmt(nominal),
            "cycle_s": None if cycle_s is None else units.fmt(cycle_s),
            "nominal_volley": nominal_volley,
            "volley_source": volley_source,
            "damage_split": split,
            "chance_to_hit": None,
            "application": None,
            "effective_dps": None,
            "lower_bound_dps": None,
            "status": "ok",
            "reason": None,
            "inputs": {},
        }

        name = item.get("name") or ""
        group = item.get("group_name") or ""
        if DISINTEGRATOR_HINT in name or DISINTEGRATOR_HINT in group:
            entry.update(status="unsupported", lower_bound_dps=entry["nominal_dps"],
                         reason="entropic_disintegrator: 伤害有 ramp，炮台/导弹公式都不适用")
        elif family in TURRET_FAMILIES:
            _fill_turret(entry, raw_attrs, charge_attrs)
        elif family == "missile":
            _fill_missile(ctx, entry, raw_attrs, charge_attrs)
        elif family in ("drone", "fighter"):
            entry.update(status="unsupported", lower_bound_dps=entry["nominal_dps"],
                         reason=f"{family}: 机动/发射后行为未建模（下界=完美应用）")
        elif family == "smartbomb":
            entry.update(status="unsupported", lower_bound_dps=entry["nominal_dps"],
                         reason="smartbomb: 范围伤害，未建模")
        else:
            entry.update(status="unsupported", lower_bound_dps=entry["nominal_dps"],
                         reason="weapon_family_unknown: 未溯源公式（下界=完美应用）")
        entries.append(entry)
    return entries


def _fill_turret(entry: dict, raw_attrs: dict[int, float], charge_attrs: dict[int, float]) -> None:
    """炮台：命中率 × 伤害倍率。"""
    tracking = _raw_attr(raw_attrs, A.ATTR_TRACKING_SPEED)
    optimal_sig = _raw_attr(raw_attrs, A.ATTR_OPTIMAL_SIG_RADIUS)
    if optimal_sig is None:
        optimal_sig = TURRET_SIGNATURE_RESOLUTION
        entry["reason"] = "optimal_sig_radius_missing: 用 40000 常量兜底"
    elif optimal_sig < 1000.0:
        # 防御：raw 口径下 620 一定是 40000；出现 40 说明上游把物理值混进来了。
        entry["status"] = "degraded"
        entry["reason"] = f"optimal_sig_radius_unexpected_scale({optimal_sig:g})"

    entry["inputs"] = {
        "tracking_rad_s": None if tracking is None else units.fmt(tracking, 6),
        "optimal_sig_radius": None if optimal_sig is None else units.fmt(optimal_sig, 1),
        "optimal_m": _raw_attr(raw_attrs, A.ATTR_MAX_RANGE),
        "falloff_m": _raw_attr(raw_attrs, A.ATTR_FALLOFF),
    }
    entry["_profile"] = (tracking, optimal_sig, _raw_attr(raw_attrs, A.ATTR_MAX_RANGE),
                         _raw_attr(raw_attrs, A.ATTR_FALLOFF))


def _fill_missile(ctx, entry: dict, raw_attrs: dict[int, float],
                  charge_attrs: dict[int, float]) -> None:
    """导弹：射程门 + 应用系数（没有命中率概念，`chance_to_hit` 固定 1.0）。"""
    charge_attrs = charge_attrs or {}
    entry["inputs"] = {
        "explosion_radius_m": _raw_attr(charge_attrs, A.ATTR_AOE_CLOUD_SIZE),
        "explosion_velocity_mps": _raw_attr(charge_attrs, A.ATTR_AOE_VELOCITY),
        "damage_reduction_factor": _raw_attr(charge_attrs, A.ATTR_AOE_DAMAGE_REDUCTION_FACTOR),
        "missile_range_m": _missile_range_m(raw_attrs, charge_attrs),
    }
    entry["_profile"] = (
        entry["inputs"]["explosion_radius_m"],
        entry["inputs"]["explosion_velocity_mps"],
        entry["inputs"]["damage_reduction_factor"],
        entry["inputs"]["missile_range_m"],
    )


def _missile_range_m(raw_attrs: dict[int, float], charge_attrs: dict[int, float]) -> float | None:
    """导弹最大射程 ≈ 弹药 `maxVelocity`（37）× 飞行时间（281，**毫秒**）。

    简化：没有算加速段、没有算 1 秒离散 tick、没有算船体半径加成（pyfa 那套更细）。
    这是**乐观上界**，在响应里由 `assumptions.missile_range` 声明。
    """
    velocity = _raw_attr(charge_attrs, A.ATTR_MAX_VELOCITY)
    flight_ms = _raw_attr(charge_attrs, A.ATTR_EXPLOSION_DELAY)
    if velocity is None or flight_ms is None:
        return None
    return velocity * units.ms_to_s(flight_ms)


# =============================================================================
# 一次交战
# =============================================================================


def engagement(
    attacker_ctx,
    defender_ctx,
    *,
    range_m: float,
    angular_vel: float = 0.0,
    target_velocity: float = 0.0,
    include_wrecking: bool = True,
    ammo=None,
) -> dict:
    """A 打 B：命中率 / 应用系数 / 有效 DPS / 理论击穿时间。

    Args:
        attacker_ctx / defender_ctx: 两个 `ViewContext`（属性只读）。
        range_m: 面到面距离（m）。角速度由调用方给，本模块不复刻几何假设。
        angular_vel: 角速度（rad/s）。**0 是乐观上界**（迎头/静止）。
        target_velocity: 守方绝对速度（m/s）。**0 是乐观上界**（导弹应用最好）；
            实际场景由调用方给，或调 `exchange()` 让它按两侧 maxVelocity 自动填。
        include_wrecking: 炮台伤害倍率是否含 wrecking（默认 True，与 pyfa 一致）。
            False 时归一到纸面口径（完美应用 = 1.0）。
        ammo: 只接受 `None`（用配装里已装填的弹药）；显式指定其它弹药会
            `ValueError` —— 换弹属于「重算属性」的第 ① 层，不在本层职责内。

    Returns:
        dict（见模块 docstring；`hp_pool` 与 `ehp` 两个口径都给，并各自注明用途）。
    """
    if ammo is not None:
        raise ValueError(
            "ammo 只支持 None（使用配装已装填的弹药）：换弹要重算属性，属第 ① 层（引擎）职责"
        )
    if range_m is None or range_m < 0:
        raise ValueError(f"range_m 必须是非负数，收到 {range_m!r}")

    weapons = _weapon_entries(attacker_ctx)
    defender_sig = _raw_ship_attrs(defender_ctx).get(A.ATTR_SIGNATURE_RADIUS) or 0.0
    flags: list[str] = []
    if not weapons:
        flags.append("no_weapons: 攻方没有任何可识别武器 → 有效 DPS 为 0")
    if defender_sig <= 0.0:
        flags.append("defender_signature_unknown: 炮台命中率按 0 处理")
    if target_velocity is None:
        target_velocity = 0.0

    for entry in weapons:
        status = entry["status"]
        profile = entry.pop("_profile", None)
        split = entry["damage_split"]
        entry["damage_share"] = _split_fractions(split)
        if profile is None or entry["nominal_dps"] <= 0.0:
            if entry["status"] == "ok":
                entry["status"] = "degraded"
                entry["reason"] = entry["reason"] or "no_damage"
            continue

        if entry["family"] in TURRET_FAMILIES:
            tracking, optimal_sig, optimal, falloff = profile
            cth = turret_chance_to_hit(
                tracking=tracking,
                optimal_sig_radius=optimal_sig,
                angular_vel=angular_vel,
                target_signature=defender_sig,
                range_m=range_m,
                optimal_m=optimal,
                falloff_m=falloff,
            )
            entry["chance_to_hit"] = units.fmt(cth, 6)
            entry["application"] = units.fmt(
                turret_damage_multiplier(cth, include_wrecking=include_wrecking), 6
            )
            entry["effective_dps"] = units.fmt(entry["nominal_dps"] * entry["application"])
        elif entry["family"] == "missile":
            er, ev, drf, missile_range = profile
            if missile_range is not None and range_m > missile_range:
                entry["status"] = "degraded"
                entry["reason"] = "out_of_missile_range"
                entry["chance_to_hit"] = 1.0
                entry["application"] = 0.0
                entry["effective_dps"] = 0.0
                continue
            factor = missile_application_factor(
                explosion_radius_m=er,
                explosion_velocity_mps=ev,
                damage_reduction_factor=drf,
                target_velocity=target_velocity,
                target_signature=defender_sig,
            )
            entry["chance_to_hit"] = 1.0
            entry["application"] = units.fmt(factor, 6)
            entry["effective_dps"] = units.fmt(entry["nominal_dps"] * entry["application"])

    totals = _totals(weapons)
    survival = _survival(defender_ctx, totals["effective_dps"], totals["coverage"], flags)

    return {
        "kind": "engagement",
        "attacker": {"label": _ship_label(attacker_ctx), "ship": _ship_block(attacker_ctx)},
        "defender": {"label": _ship_label(defender_ctx), "ship": _ship_block(defender_ctx)},
        "inputs": {
            "range_m": units.fmt(range_m),
            "angular_vel": units.fmt(angular_vel, 6),
            "target_velocity": units.fmt(target_velocity),
            "include_wrecking": bool(include_wrecking),
            "ammo": "loaded",
        },
        "assumptions": _assumptions(attacker_ctx, defender_ctx, include_wrecking),
        "weapons": weapons,
        "totals": totals,
        "defender_survival": survival,
        "flags": flags,
    }


def _totals(weapons: list[dict]) -> dict:
    """汇总：名义/有效/未支持 DPS、按 DPS 加权的命中率与伤害占比、覆盖率。"""
    nominal = sum(entry["nominal_dps"] or 0.0 for entry in weapons)
    effective = sum(entry["effective_dps"] or 0.0 for entry in weapons)
    unsupported = sum(entry["lower_bound_dps"] or 0.0 for entry in weapons
                      if entry["status"] == "unsupported")
    supported_nominal = sum(entry["nominal_dps"] or 0.0 for entry in weapons
                            if entry["effective_dps"] is not None)

    parts: list[tuple[float, float]] = []
    split_sum: dict[str, float] = {}
    for entry in weapons:
        weight = entry["nominal_dps"] or 0.0
        if entry["effective_dps"] is None or weight <= 0.0:
            continue
        cth = entry["chance_to_hit"]
        if cth is not None:
            parts.append((cth, weight))
        contribution = (entry["effective_dps"] or 0.0)
        share = entry.get("damage_share") or {}
        for name in ("em", "explosive", "kinetic", "thermal"):
            split_sum[name] = split_sum.get(name, 0.0) + share.get(name, 0.0) * contribution

    weighted_cth = (
        units.fmt(sum(cth * weight for cth, weight in parts) / sum(weight for _, weight in parts), 6)
        if parts and sum(weight for _, weight in parts) > 0.0 else None
    )
    split_total = sum(split_sum.values())
    damage_split = (
        {name: units.fmt(value / split_total, 6) for name, value in split_sum.items()}
        if split_total > 0.0 else None
    )
    coverage = 1.0 if nominal <= 0.0 else min(1.0, supported_nominal / nominal)

    return {
        "nominal_dps": units.fmt(nominal),
        "effective_dps": units.fmt(effective),
        "unsupported_dps": units.fmt(unsupported),
        "coverage": units.fmt(coverage, 6),
        "weighted_chance_to_hit": weighted_cth,
        "damage_split": damage_split,
        "weapon_count": len(weapons),
        "supported_weapon_count": sum(1 for e in weapons if e["effective_dps"] is not None),
    }


def _survival(defender_ctx, effective_dps: float, coverage: float, flags: list[str]) -> dict:
    """守方的两个血量口径 + 击穿时间。

    - `hp_pool` = 盾/甲/结构 HP 之和，**与战报 `damage_taken` 同量纲**（P6-B1 实测：
      3 条样本 damage_taken/HP池 = 0.50/0.84/1.02，而 /EHP = 0.16/0.40/0.42）；
    - `ehp` = 引擎 `-43`（uniform 伤害剖面），是**原始伤害**口径，用来算击穿时间；
    - `time_to_kill_s` = `ehp / effective_dps`（两者都是原始伤害口径，量纲一致）。
    """
    ship = _raw_ship_attrs(defender_ctx)
    shield = ship.get(A.ATTR_SHIELD_CAPACITY) or 0.0
    armor = ship.get(A.ATTR_ARMOR_HP) or 0.0
    hull = ship.get(A.ATTR_HULL_HP) or 0.0
    hp_pool = shield + armor + hull
    ehp = ship.get(A.ATTR_EHP)

    ttk = None
    if ehp is not None and effective_dps > 0.0:
        ttk = units.fmt(ehp / effective_dps)
    if coverage < 1.0:
        flags.append("partial_coverage: 有武器未建模 → 有效 DPS 偏低，击穿时间是上界")

    return {
        "hp_pool": {
            "value": units.fmt(hp_pool),
            "unit": "hp",
            "layers": {"shield": units.fmt(shield), "armor": units.fmt(armor), "hull": units.fmt(hull)},
            "compare_with": "战报 damage_taken（同一量纲：扣掉的血）",
        },
        "ehp": {
            "value": None if ehp is None else units.fmt(ehp),
            "unit": "hp",
            "profile": "uniform",
            "compare_with": "原始伤害与击穿时间（EHP / 有效 DPS）",
        },
        "time_to_kill_s": {
            "value": ttk,
            "method": "ehp / effective_dps（都是原始伤害口径）",
            "partial": coverage < 1.0,
        },
    }


def _assumptions(attacker_ctx, defender_ctx, include_wrecking: bool) -> dict:
    base = attacker_ctx.assumptions or {}
    return {
        "skill_tier": base.get("skill_tier_name", "unknown"),
        "damage_profile": base.get("damage_profile"),
        "module_states": base.get("module_states", "engine_default"),
        "ammo": "loaded_in_fit",
        "wrecking": "included" if include_wrecking else "normalized_to_paper",
        "angular_geometry": "caller_supplied (no center-to-center assumption inside)",
        "missile_range": "velocity × flight_time (no acceleration/discrete-tick/radius bonus)",
        "tolerance_rel": TOLERANCE_REL,
        "defender_speed_source": "caller_supplied target_velocity",
        "attacker_ship": _ship_block(attacker_ctx),
        "defender_ship": _ship_block(defender_ctx),
    }


# =============================================================================
# 双向换血
# =============================================================================


def exchange(
    a_ctx,
    b_ctx,
    *,
    range_m: float,
    angular_vel: float = 0.0,
    include_wrecking: bool = True,
    a_velocity: float | None = None,
    b_velocity: float | None = None,
) -> dict:
    """A ↔ B 双向：各自打对方的有效 DPS 与击穿时间，给出换血比。

    `a_velocity` / `b_velocity` 缺省时取各自船体的 `maxVelocity`（引擎值），
    作为对方向导弹应用里的「目标绝对速度」——即**双方都按最大速度机动**的假设。
    角速度两侧相同（EVE 里两船互相看到的角速度恒等）。
    """
    av = _ship_speed(a_ctx) if a_velocity is None else a_velocity
    bv = _ship_speed(b_ctx) if b_velocity is None else b_velocity

    a_on_b = engagement(
        a_ctx, b_ctx, range_m=range_m, angular_vel=angular_vel,
        target_velocity=bv, include_wrecking=include_wrecking,
    )
    b_on_a = engagement(
        b_ctx, a_ctx, range_m=range_m, angular_vel=angular_vel,
        target_velocity=av, include_wrecking=include_wrecking,
    )

    a_ttk = a_on_b["defender_survival"]["time_to_kill_s"]["value"]
    b_ttk = b_on_a["defender_survival"]["time_to_kill_s"]["value"]
    ratio = None
    if a_ttk is not None and b_ttk not in (None, 0.0):
        ratio = units.fmt(a_ttk / b_ttk, 6)

    return {
        "kind": "exchange",
        "inputs": {
            "range_m": units.fmt(range_m),
            "angular_vel": units.fmt(angular_vel, 6),
            "include_wrecking": bool(include_wrecking),
            "a_velocity": units.fmt(av),
            "b_velocity": units.fmt(bv),
        },
        "a_on_b": a_on_b,
        "b_on_a": b_on_a,
        "race": {
            "a_time_to_kill_s": a_ttk,
            "b_time_to_kill_s": b_ttk,
            "ratio_a_over_b": ratio,
            "faster": (
                None if ratio is None else ("a" if ratio < 1.0 else "b" if ratio > 1.0 else "even")
            ),
            "first_to_die": _first_to_die(a_ttk, b_ttk),
            "note": "ratio = A 击穿 B 所需时间 ÷ B 击穿 A 所需时间；<1 表示 A 先打穿。"
                    "first_to_die 由「谁的时间更短」决定（不含电容/距离控制/后勤）。",
        },
    }


def _first_to_die(a_ttk: float | None, b_ttk: float | None) -> str | None:
    """谁先死：`a_time_to_kill_s` 是 A 打死 B 的时间（B 先死），反之亦然。"""
    if a_ttk is None or b_ttk is None:
        return None
    if a_ttk < b_ttk:
        return "b"
    if b_ttk < a_ttk:
        return "a"
    return "even"


# =============================================================================
# 场景扫描 / 时间线 / 战报锚点（P6-B3）
# =============================================================================

DEFAULT_ANGLES: tuple[float, ...] = (0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.3)
"""默认角速度采样（rad/s）。0 是乐观上界；0.3 是「近距高速绕圈」的极端。"""

LAYER_RESONANCES: tuple[tuple[str, int, tuple[int, int, int, int]], ...] = (
    ("shield", A.ATTR_SHIELD_CAPACITY,
     (A.ATTR_SHIELD_EM_RESONANCE, A.ATTR_SHIELD_EXPLOSIVE_RESONANCE,
      A.ATTR_SHIELD_KINETIC_RESONANCE, A.ATTR_SHIELD_THERMAL_RESONANCE)),
    ("armor", A.ATTR_ARMOR_HP,
     (A.ATTR_ARMOR_EM_RESONANCE, A.ATTR_ARMOR_EXPLOSIVE_RESONANCE,
      A.ATTR_ARMOR_KINETIC_RESONANCE, A.ATTR_ARMOR_THERMAL_RESONANCE)),
    ("hull", A.ATTR_HULL_HP,
     (A.ATTR_HULL_EM_RESONANCE, A.ATTR_HULL_EXPLOSIVE_RESONANCE,
      A.ATTR_HULL_KINETIC_RESONANCE, A.ATTR_HULL_THERMAL_RESONANCE)),
)
"""三层 HP 与四系共振率（顺序与 `shared.DAMAGE_TYPES` 一致：em/explosive/kinetic/thermal）。

**共振率是「承伤系数」不是「抗性百分比」**（0.875 = 承受 87.5%）。所以
「打掉 H 点该层血」需要的**原始伤害** = H ÷ 加权共振率。
这条是 P6-B1 锚点核算的同一个模型（damage_taken 是扣掉的血）。
"""


def _damage_weights(split: Mapping[str, float] | None) -> tuple[float, ...]:
    """伤害占比 → 四元组（顺序同 DAMAGE_TYPES）；缺省按均匀剖面。"""
    names = [name for name, _attribute_id in shared.DAMAGE_TYPES]
    if not split:
        return (0.25, 0.25, 0.25, 0.25)
    total = sum(split.get(name, 0.0) for name in names)
    if total <= 0.0:
        return (0.25, 0.25, 0.25, 0.25)
    return tuple(split.get(name, 0.0) / total for name in names)


def _weighted_resonance(resonances: tuple[float, ...], weights: tuple[float, ...]) -> float:
    """该层对当前伤害构成的**等效承伤系数** Σ(wᵢ × rᵢ)。"""
    return sum(weight * resonance for weight, resonance in zip(weights, resonances))


def _defender_layers(ctx) -> list[dict]:
    """守方三层：`{name, hp, resonances}`（缺层跳过，不补 0）。"""
    ship = _raw_ship_attrs(ctx)
    layers: list[dict] = []
    for name, hp_attribute, resonance_attributes in LAYER_RESONANCES:
        hp = ship.get(hp_attribute) or 0.0
        if hp <= 0.0:
            continue
        layers.append({
            "name": name,
            "hp": float(hp),
            "resonances": tuple(ship.get(attribute_id, 1.0) for attribute_id in resonance_attributes),
        })
    return layers


def _apply_damage(raw_damage: float, weights: tuple[float, ...],
                  layers: list[dict]) -> tuple[float, list[dict]]:
    """把一段**原始伤害**按层灌进去，返回（实际扣血, 分层明细）。

    顺序：护盾 → 装甲 → 结构；每层「打掉 H 点血」消耗 `H ÷ 等效承伤系数` 的原始伤害。
    这是「战报 damage_taken」的同量纲模型（不含护盾被动回充/遥修/电容）。
    """
    remaining_raw = max(0.0, float(raw_damage))
    removed_hp = 0.0
    detail: list[dict] = []
    for layer in layers:
        resonance = _weighted_resonance(layer["resonances"], weights)
        if resonance <= 0.0:
            detail.append({"layer": layer["name"], "hp_before": layer["hp"],
                           "hp_removed": 0.0, "note": "resonance<=0: 该伤害构成打不动这一层"})
            continue
        raw_for_layer = layer["hp"] / resonance
        used = min(remaining_raw, raw_for_layer)
        removed = used * resonance
        remaining_raw -= used
        removed_hp += removed
        detail.append({"layer": layer["name"], "hp_before": layer["hp"],
                       "hp_removed": removed, "raw_used": used})
        if remaining_raw <= 0.0:
            break
    return removed_hp, detail


def _raw_needed_for_hp(target_hp: float, weights: tuple[float, ...],
                       layers: list[dict]) -> float | None:
    """打掉 `target_hp` 点血需要多少**原始伤害**；血池不够返回 `None`。"""
    remaining = float(target_hp)
    raw = 0.0
    for layer in layers:
        resonance = _weighted_resonance(layer["resonances"], weights)
        if resonance <= 0.0:
            return None
        take = min(remaining, layer["hp"])
        raw += take / resonance
        remaining -= take
        if remaining <= 1e-12:
            return raw
    return None


def _default_ranges(attacker_ctx) -> list[float]:
    """按攻方主武器的射程参数生成默认采样距离。"""
    for entry in _weapon_entries(attacker_ctx):
        if entry["status"] == "unsupported":
            continue
        inputs = entry.get("inputs") or {}
        if entry["family"] in TURRET_FAMILIES:
            optimal = float(inputs.get("optimal_m") or 0.0)
            falloff = float(inputs.get("falloff_m") or 0.0)
            if optimal > 0.0:
                grid = [0.5 * optimal, optimal]
                for step in (0.5, 1.0, 2.0, 3.0):
                    grid.append(optimal + step * falloff)
                return [units.fmt(max(0.0, value)) for value in grid]
        elif entry["family"] == "missile":
            reach = float(inputs.get("missile_range_m") or 0.0)
            if reach > 0.0:
                return [units.fmt(reach * fraction) for fraction in (0.05, 0.25, 0.5, 0.75, 0.9, 1.0)]
    return [1000.0, 5000.0, 10000.0, 20000.0, 50000.0]


def sweep(
    attacker_ctx,
    defender_ctx,
    *,
    ranges_m: list[float] | tuple[float, ...] | None = None,
    angulars: list[float] | tuple[float, ...] | None = None,
    target_velocity: float = 0.0,
    include_wrecking: bool = True,
) -> dict:
    """距离 × 角速度二维扫描（采样曲面）。

    每格只回**四个数**（命中率 / 有效 DPS / 击穿时间 / 覆盖率），不重复整份
    `engagement` 响应——否则 63 格会变成几千行 JSON，没人看得动。
    """
    ranges = list(ranges_m) if ranges_m is not None else _default_ranges(attacker_ctx)
    angles = list(angulars) if angulars is not None else list(DEFAULT_ANGLES)

    cells: list[dict] = []
    for range_m in ranges:
        for angular_vel in angles:
            result = engagement(
                attacker_ctx, defender_ctx, range_m=range_m, angular_vel=angular_vel,
                target_velocity=target_velocity, include_wrecking=include_wrecking,
            )
            cells.append({
                "range_m": units.fmt(range_m),
                "angular_vel": units.fmt(angular_vel, 6),
                "chance_to_hit": result["totals"]["weighted_chance_to_hit"],
                "effective_dps": result["totals"]["effective_dps"],
                "ttk_s": result["defender_survival"]["time_to_kill_s"]["value"],
                "coverage": result["totals"]["coverage"],
            })

    best = max(cells, key=lambda cell: cell["effective_dps"]) if cells else None
    return {
        "kind": "sweep",
        "ranges_m": [units.fmt(value) for value in ranges],
        "angulars": [units.fmt(value, 6) for value in angles],
        "shape": [len(ranges), len(angles)],
        "cells": cells,
        "best": best,
        "assumptions": {
            "sampled": True,
            "target_velocity": units.fmt(target_velocity),
            "wrecking": "included" if include_wrecking else "normalized_to_paper",
        },
    }


def timeline(
    a_ctx,
    b_ctx,
    *,
    range_m: float,
    angular_vel: float = 0.0,
    duration_s: float | None = None,
    points: int = 6,
    include_wrecking: bool = True,
) -> dict:
    """一次战斗的简化时间线：双方同时开火、无外部干扰、按有效 DPS 线性扣血。

    「剩余血」用 **EHP 口径**（与击穿时间一致：原始伤害 / 原始承伤能力）；
    同时给 `hp_fraction` 与等效 `hp_pool` 剩余量，方便与战报 `damage_taken` 对照。
    """
    if points < 2:
        raise ValueError(f"points 至少 2，收到 {points!r}")

    battle = exchange(a_ctx, b_ctx, range_m=range_m, angular_vel=angular_vel,
                      include_wrecking=include_wrecking)
    a_dps = battle["a_on_b"]["totals"]["effective_dps"]
    b_dps = battle["b_on_a"]["totals"]["effective_dps"]
    b_survival = battle["a_on_b"]["defender_survival"]
    a_survival = battle["b_on_a"]["defender_survival"]
    a_ehp = a_survival["ehp"]["value"] or 0.0
    b_ehp = b_survival["ehp"]["value"] or 0.0
    # 死亡时刻用「EHP ÷ 有效 DPS」的**全精度**值（`time_to_kill_s` 是 3 位小数，
    # 直接拿它当时间线终点会在最后一格留下 0.05 点残血，看着像 bug）。
    b_ttk = units.fmt(b_ehp / a_dps) if (a_dps > 0.0 and b_ehp > 0.0) else None  # A 打死 B
    a_ttk = units.fmt(a_ehp / b_dps) if (b_dps > 0.0 and a_ehp > 0.0) else None  # B 打死 A
    b_death = b_ehp / a_dps if (a_dps > 0.0 and b_ehp > 0.0) else None
    a_death = a_ehp / b_dps if (b_dps > 0.0 and a_ehp > 0.0) else None

    candidates = [value for value in (a_death, b_death) if value is not None]
    horizon = duration_s if duration_s is not None else (max(candidates) if candidates else 60.0)
    horizon = max(float(horizon), 1e-9)

    samples: list[dict] = []
    for index in range(points):
        t = horizon * index / (points - 1)
        b_remaining = max(0.0, b_ehp - a_dps * t)
        a_remaining = max(0.0, a_ehp - b_dps * t)
        samples.append({
            "t_s": units.fmt(t),
            "a_remaining_ehp": units.fmt(a_remaining),
            "b_remaining_ehp": units.fmt(b_remaining),
            "a_hp_fraction": units.fmt(a_remaining / a_ehp, 6) if a_ehp else None,
            "b_hp_fraction": units.fmt(b_remaining / b_ehp, 6) if b_ehp else None,
            "a_hp_pool_remaining": units.fmt(a_remaining / a_ehp * (a_survival["hp_pool"]["value"] or 0.0))
            if a_ehp else None,
            "b_hp_pool_remaining": units.fmt(b_remaining / b_ehp * (b_survival["hp_pool"]["value"] or 0.0))
            if b_ehp else None,
        })

    return {
        "kind": "timeline",
        "inputs": {
            "range_m": units.fmt(range_m),
            "angular_vel": units.fmt(angular_vel, 6),
            "horizon_s": units.fmt(horizon),
            "include_wrecking": bool(include_wrecking),
        },
        "rates": {
            "a_dps_on_b": a_dps,
            "b_dps_on_a": b_dps,
        },
        "endpoints": {
            "a_dies_at_s": a_ttk,
            "b_dies_at_s": b_ttk,
            "first_to_die": _first_to_die(b_ttk, a_ttk),
        },
        "samples": samples,
        "assumptions": {
            "model": "linear_depletion（双方同时开火、无遥修/无电容/无跑位）",
            "ehp_profile": "uniform（引擎 -43）",
            "hp_pool_equivalent": "按 EHP 比例折算到 raw HP 池，仅用于和战报对拍",
        },
    }


def damage_window(
    attacker_ctx,
    defender_ctx,
    *,
    window_s: float,
    range_m: float,
    angular_vel: float = 0.0,
    target_velocity: float = 0.0,
    include_wrecking: bool = True,
) -> dict:
    """在 `window_s` 秒里能打掉守方多少**血**（战报 `damage_taken` 同量纲）。

    先用 `engagement` 拿原始有效 DPS 与伤害构成，再按层「原始伤害 ÷ 等效承伤系数」
    换成实际扣血——这一步就是 P6-B1 锚点核算用的同一个模型。
    """
    result = engagement(
        attacker_ctx, defender_ctx, range_m=range_m, angular_vel=angular_vel,
        target_velocity=target_velocity, include_wrecking=include_wrecking,
    )
    raw_dps = result["totals"]["effective_dps"]
    weights = _damage_weights(result["totals"]["damage_split"])
    layers = _defender_layers(defender_ctx)
    raw_total = raw_dps * window_s
    removed, detail = _apply_damage(raw_total, weights, layers)
    return {
        "kind": "damage_window",
        "window_s": units.fmt(window_s),
        "range_m": units.fmt(range_m),
        "angular_vel": units.fmt(angular_vel, 6),
        "raw_dps": raw_dps,
        "raw_damage": units.fmt(raw_total),
        "hp_removed": units.fmt(removed),
        "hp_dps": units.fmt(removed / window_s) if window_s > 0 else None,
        "damage_split": result["totals"]["damage_split"],
        "layers": detail,
        "coverage": result["totals"]["coverage"],
    }


def explain_anchor(
    attacker_ctx,
    defender_ctx,
    *,
    observed_hp: float,
    window_s: float,
    ranges_m: list[float] | tuple[float, ...] | None = None,
    angulars: list[float] | tuple[float, ...] | None = None,
    target_velocity: float = 0.0,
    include_wrecking: bool = True,
) -> dict:
    """战报锚点：模型能否在 `window_s` 内打出 `observed_hp` 点血？在哪些距离/角速度？

    **不做任何拟合**：`range_m` / `angular_vel` 是**扫描变量**，不是用来凑数的参数；
    结论只回答「存在 / 不存在这样的几何区间」，不存在就如实说不存在并给出最短所需时间。

    Returns:
        `explainable` / `region`（满足窗口的格子）/ `best`（最短时间的那一格）/
        `required_hp_dps` / `reasons`（不可解释时的原因）/ `caveats`（永远存在的限制）。
    """
    ranges = list(ranges_m) if ranges_m is not None else _default_ranges(attacker_ctx)
    angles = list(angulars) if angulars is not None else list(DEFAULT_ANGLES)
    layers = _defender_layers(defender_ctx)

    cells: list[dict] = []
    for range_m in ranges:
        for angular_vel in angles:
            result = engagement(
                attacker_ctx, defender_ctx, range_m=range_m, angular_vel=angular_vel,
                target_velocity=target_velocity, include_wrecking=include_wrecking,
            )
            raw_dps = result["totals"]["effective_dps"]
            weights = _damage_weights(result["totals"]["damage_split"])
            raw_needed = _raw_needed_for_hp(observed_hp, weights, layers)
            window_needed = None
            if raw_needed is not None and raw_dps > 0.0:
                window_needed = raw_needed / raw_dps
            removed_in_window = _apply_damage(raw_dps * window_s, weights, layers)[0]
            cells.append({
                "range_m": units.fmt(range_m),
                "angular_vel": units.fmt(angular_vel, 6),
                "raw_dps": raw_dps,
                "hp_removed_in_window": units.fmt(removed_in_window),
                "window_needed_s": None if window_needed is None else units.fmt(window_needed),
                "coverage": result["totals"]["coverage"],
            })

    region = [cell for cell in cells
              if cell["window_needed_s"] is not None and cell["window_needed_s"] <= window_s]
    feasible = [cell for cell in cells if cell["window_needed_s"] is not None]
    best = min(feasible, key=lambda cell: cell["window_needed_s"]) if feasible else None
    max_in_window = max((cell["hp_removed_in_window"] for cell in cells), default=0.0)

    reasons: list[str] = []
    if not region:
        if best is None:
            reasons.append("没有可用的有效 DPS（攻方无已建模武器或全部超出射程）")
        else:
            reasons.append(
                f"窗口 {window_s:g}s 内最多只能打掉 {max_in_window:.0f} 点血"
                f"（需要 {observed_hp:.0f}）；最短需要 {best['window_needed_s']:.1f}s"
                f"（range={best['range_m']:g} m, angular={best['angular_vel']:g} rad/s）"
            )
        reasons.append("本层未建模：护盾被动回充、遥修/后勤、电容、跑位与目标选择")
        reasons.append("战报的「9 秒」是两次死亡的**间隔**，不一定是交战时长；"
                       "实际开打时刻不可知")

    return {
        "kind": "anchor_check",
        "observed_hp": units.fmt(observed_hp),
        "window_s": units.fmt(window_s),
        "required_hp_dps": units.fmt(observed_hp / window_s) if window_s > 0 else None,
        "explainable": bool(region),
        "region_count": len(region),
        "region": region,
        "best": best,
        "max_hp_removed_in_window": units.fmt(max_in_window),
        "grid": {"ranges_m": [units.fmt(v) for v in ranges],
                 "angulars": [units.fmt(v, 6) for v in angles]},
        "reasons": reasons,
        "caveats": [
            "参数是扫描变量，不是拟合变量：没有为了让锚点对上而调过任何数",
            "血量口径 = 战报 damage_taken（扣掉的血），不是 EHP",
            "护盾被动回充与遥修未建模 → 真实战斗里能承受的伤害会更多",
            "技能档按 all4+min_required 假设（战报不记录技能）",
            "伤害构成取引擎 uniform 剖面之外的**实际武器四系占比**；"
            "若攻方有 unsupported 武器，覆盖率会 < 1",
        ],
    }
