"""三态 provenance 判定（L3）—— **表由实测生成，不得手改**。

> 本表由 `evefit/tools/derive_provenance.py` 的**三轴敏感性实验**实测生成。
> 不要手改；SDE / 引擎 / 样本变更后重跑该脚本，diff 报告（
> `evefit/tools/provenance_report.json`）再审。生成日期与样本见 `DERIVED_AT` /
> `DERIVED_SAMPLE`（`audit()` 会回显）。

背景（红线 N3 / N4，字段规格 §0.2，第 16 轮裁决）：

- **引擎不给 provenance**——`dogma.calculate` 只回数值，不回「这个数是怎么来的」。
- **provenance 只表示「这个数在本次计算里是否依赖假设」**，与「可信度 A–E」是
  **正交维度**。第 16 轮之前那张手写表把两者混成一列，与验收员的
  `field_spec.json` 冲突了 7 处；根因就是这种混淆，而不是规则算错。
- 判定必须**默认 `unknown`**：新增属性、未复测属性、表外属性一律不得被误标成
  `measured`（红线 N4：不用默认值冒充 unknown）。

三态的定义（由实测落成，见下三张表）
------------------------------------
- `measured`：在**本次计算**里不随三轴假设变化 —— 技能档、模块状态、伤害剖面
  都不改变它。样本实测 65 个（壳/槽位/共振率/槽位余量/热参数/技能要求 …）。
- `assumed`：依赖上述任一假设。三类来源，各自带实测证据：
  1. **负 ID**（引擎 pass 4 派生值）：29 个，`-1` … `-72`。派生值天然建立在
     技能/状态/剖面之上（例：`-43 ehp` 实测 8854.96 → 纯 EM 剖面 8549.55）；
  2. **技能轴变化**：10 个，例 `9 hp` 2343.75 → 1953.12、`265 armorHP` 840 →
     700、`48 cpuOutput` 276 → 230、`70 agility` 5.52 → 6、`51 speed` 2304.64 →
     2761.24（都是「全4+补最低 → 全0」）；
  3. **状态轴变化**：2 个，`4 mass` 1550000 → 1050000（MWD 推力的质量加成）、
     `552 signatureRadius` 385 → 70（MWD 信号膨胀）。
- `unknown`：本样本里**不存在**（引擎没输出）或引擎根本没算的属性 —— 35 个已登记
  常量（战斗机族、模式后除数、弹药伤害、`-14/-46/-47` …：本样本没有战斗机 /
  没有模式 / 弹药属货舱物品 / 没有对应模块），加上 `-70 maxTargets` 的手工覆盖。
  **表外的一切 ID 仍然默认 `unknown`**（`tag()` 的默认分支）。

三条硬规则（与实测表一起构成不变量）
------------------------------------
1. **默认值是 `unknown`**（红线 N4）：`tag()` 的默认分支必须落在 `unknown`。
2. **分类表与原属性表分离**：本模块只做「ID → 三态」的一维映射，不复制名称 /
   单位 / 缩放（那些在 `sde/attribute_dict.py`）。重复一份就会漂移。
3. **手工覆盖必须写理由**：见 `_MANUAL_OVERRIDES`，每条都要有实测依据。

已知的**轴覆盖局限**（实测取证，不影响上面的表，但必须知道）
------------------------------------------------------------
任务书把技能轴钉死为「`derive_skill_tier`（全 4 + 补最低）→ 全 0」，而
`derive_skill_tier` 只覆盖**模块要求**的 19 个技能。因此「技能轴不变」只能证明
「**在这个技能档下**不变」。`derive_provenance.py` 的第五节用轴外技能抽样取证，
已实测到的反例（当前仍按规则落在 `measured`）：

- `Shield Management=5` → `263 shieldCapacity` 650 → 812.5
- `Power Grid Management=5` → `11 powerOutput` 54 → 67.5
- `Shield Operation=5` → `479 shieldRechargeRate` 800000 → 600000
- `Signature Analysis=5` → `564 scanResolution` 510 → 637.5

要收紧这几个，得**先改轴的定义**（例如 A 变体改成「全部技能 4 级」）再重跑，
不能在表里手工拍。这是下一个任务的事，写在这里是为了后来者不再踩一次。

投影层**不得**自行改写这三态（红线 N9：默认假设必须来自 Assumption Engine）；
本模块只负责把「哪些属性依赖假设」这件事本身钉死，不定义任何假设的具体取值。
"""

from __future__ import annotations

from evefit import contracts
from evefit.config import attributes as _a
from evefit.project import units as _units
from evefit.sde import attribute_dict as _dict

Provenance = contracts.Provenance

#: 跑实验的日期（`derive_provenance.py` 写出的日期，不是模块导入时间）。
DERIVED_AT: str = "2026-10-18"

#: 实验用的样本（三轴敏感性扫描的对象）。
DERIVED_SAMPLE: str = "spike/kill.json (killmail 139246907, Hecate)"

#: `-70` = `maxTargets`（实测恒为 0，引擎没有在算它）。字段规格未登记、
#: `config/attributes.py` 也还没有常量（该文件本轮为只读），故常量就近定义在这里，
#: 并在 `audit()` 里露出。等 `config/attributes.py` 可写时应迁过去（红线 N12）。
ATTR_MAX_TARGETS_UNVERIFIED: int = -70

# =============================================================================
# 一、measured：三轴（技能 / 状态 / 伤害剖面）都不变，且在本样本里存在
#
#    实测 65 个。生成方式见模块 docstring；按属性 ID 升序排列，便于与报告 diff。
# =============================================================================
MEASURED_ATTRS: frozenset[int] = frozenset(
    {
        # ---- 资源与槽位 ----
        _a.ATTR_POWER_OUTPUT,  # 11  powerOutput（见 docstring「轴覆盖局限」）
        _a.ATTR_LOW_SLOTS,  # 12  lowSlots
        _a.ATTR_MED_SLOTS,  # 13  medSlots
        _a.ATTR_HI_SLOTS,  # 14  hiSlots
        _a.ATTR_POWER_LOAD,  # 15  powerLoad
        _a.ATTR_LAUNCHER_SLOTS_LEFT,  # 101 launcherSlotsLeft
        _a.ATTR_TURRET_SLOTS_LEFT,  # 102 turretSlotsLeft
        _a.ATTR_CALIBRATION_OUTPUT,  # 1132 upgradeCapacity
        _a.ATTR_RIG_SLOTS,  # 1137 rigSlots
        _a.ATTR_CALIBRATION_LOAD,  # 1152 upgradeLoad
        _a.ATTR_UPGRADE_SLOTS_LEFT,  # 1154 upgradeSlotsLeft
        _a.ATTR_DRONE_CAPACITY,  # 283 droneCapacity
        _a.ATTR_DRONE_BANDWIDTH,  # 1271 droneBandwidth
        # ---- 壳层容量与共振率（正 ID）----
        _a.ATTR_SHIELD_CAPACITY,  # 263 shieldCapacity（见 docstring「轴覆盖局限」）
        _a.ATTR_HULL_KINETIC_RESONANCE,  # 109 kineticDamageResonance
        _a.ATTR_HULL_THERMAL_RESONANCE,  # 110 thermalDamageResonance
        _a.ATTR_HULL_EXPLOSIVE_RESONANCE,  # 111 explosiveDamageResonance
        _a.ATTR_HULL_EM_RESONANCE,  # 113 emDamageResonance
        _a.ATTR_ARMOR_EM_RESONANCE,  # 267 armorEmDamageResonance
        _a.ATTR_ARMOR_EXPLOSIVE_RESONANCE,  # 268 armorExplosiveDamageResonance
        _a.ATTR_ARMOR_KINETIC_RESONANCE,  # 269 armorKineticDamageResonance
        _a.ATTR_ARMOR_THERMAL_RESONANCE,  # 270 armorThermalDamageResonance
        _a.ATTR_SHIELD_EM_RESONANCE,  # 271 shieldEmDamageResonance
        _a.ATTR_SHIELD_EXPLOSIVE_RESONANCE,  # 272 shieldExplosiveDamageResonance
        _a.ATTR_SHIELD_KINETIC_RESONANCE,  # 273 shieldKineticDamageResonance
        _a.ATTR_SHIELD_THERMAL_RESONANCE,  # 274 shieldThermalDamageResonance
        # ---- 容量 / 体积 / 扫描 / 跃迁 ----
        _a.ATTR_CARGO_CAPACITY,  # 38  capacity（货舱）
        _a.ATTR_VOLUME,  # 161 volume
        _a.ATTR_CAPACITOR_CAPACITY,  # 482 capacitorCapacity
        _a.ATTR_CAPACITOR_RECHARGE_RATE,  # 55 rechargeRate
        _a.ATTR_SHIELD_RECHARGE_RATE,  # 479 shieldRechargeRate（见「轴覆盖局限」）
        _a.ATTR_SCAN_RESOLUTION,  # 564 scanResolution（见「轴覆盖局限」）
        _a.ATTR_SCAN_SPEED,  # 79  scanSpeed
        _a.ATTR_MAX_LOCKED_TARGETS,  # 192 maxLockedTargets
        _a.ATTR_MAX_TARGET_RANGE,  # 76  maxTargetRange
        _a.ATTR_WARP_SPEED_MULTIPLIER,  # 600 warpSpeedMultiplier
        # ---- 武器 / 弹药（静态输入项）----
        _a.ATTR_MAX_RANGE,  # 54  maxRange
        _a.ATTR_FALLOFF,  # 158 falloff
        _a.ATTR_OPTIMAL_SIG_RADIUS,  # 620 optimalSigRadius
        _a.ATTR_CHARGE_SIZE,  # 128 chargeSize
        _a.ATTR_CHARGE_GROUP_1,  # 604 chargeGroup1
        _a.ATTR_CHARGE_GROUP_2,  # 605 chargeGroup2
        _a.ATTR_RELOAD_TIME,  # 1795 reloadTime
        # ---- 过载热（静态输入项）----
        _a.ATTR_HEAT_CAPACITY_HI,  # 1178 heatCapacityHi
        _a.ATTR_HEAT_CAPACITY_MED,  # 1199 heatCapacityMed
        _a.ATTR_HEAT_CAPACITY_LOW,  # 1200 heatCapacityLow
        _a.ATTR_HEAT_DISSIPATION_RATE_HI,  # 1179 heatDissipationRateHi
        _a.ATTR_HEAT_DISSIPATION_RATE_MED,  # 1196 heatDissipationRateMed
        _a.ATTR_HEAT_DISSIPATION_RATE_LOW,  # 1198 heatDissipationRateLow
        _a.ATTR_HEAT_ATTENUATION_HI,  # 1259 heatAttenuationHi
        _a.ATTR_HEAT_ATTENUATION_MED,  # 1261 heatAttenuationMed
        _a.ATTR_HEAT_ATTENUATION_LOW,  # 1262 heatAttenuationLow
        _a.ATTR_HEAT_GENERATION_MULTIPLIER,  # 1224 heatGenerationMultiplier
        _a.ATTR_OVERLOAD_DAMAGE_MODIFIER,  # 1210 overloadDamageModifier
        # ---- 被扰抗性 ----
        _a.ATTR_ENERGY_WARFARE_RESISTANCE,  # 2045 energyWarfareResistance
        _a.ATTR_SENSOR_DAMPENER_RESISTANCE,  # 2112 sensorDampenerResistance
        _a.ATTR_WEAPON_DISRUPTION_RESISTANCE,  # 2113 weaponDisruptionResistance
        _a.ATTR_STASIS_WEBIFIER_RESISTANCE,  # 2115 stasisWebifierResistance
        # ---- 技能要求（静态值）----
        _a.ATTR_REQUIRED_SKILL_1,  # 182 requiredSkill1
        _a.ATTR_REQUIRED_SKILL_2,  # 183 requiredSkill2
        _a.ATTR_REQUIRED_SKILL_3,  # 184 requiredSkill3
        _a.ATTR_REQUIRED_SKILL_1_LEVEL,  # 277 requiredSkill1Level
        _a.ATTR_REQUIRED_SKILL_2_LEVEL,  # 278 requiredSkill2Level
        _a.ATTR_REQUIRED_SKILL_3_LEVEL,  # 279 requiredSkill3Level
        # ---- 模式 ----
        _a.ATTR_STANCE_SWITCH_TIME,  # 1985 stanceSwitchTime
    }
)

# =============================================================================
# 二、assumed：依赖假设（技能档 / 状态 / 伤害剖面）
#
#    实测 41 个，按「命中哪条判定规则」分三组（规则顺序即归因优先级）：
#    负 ID 29 / 技能轴 10 / 状态轴 2。每组的实测 A→B 值抄在行尾注释里。
# =============================================================================
ASSUMED_ATTRS: frozenset[int] = frozenset(
    {
        # ---- 规则 2：负 ID（引擎 pass 4 派生；本样本里出现过，故 29 个而不是 33 个）----
        _a.ATTR_ALIGN_TIME,  # -1  alignTime     技能 11.8611 → 12.8925；状态 → 8.03496
        _a.ATTR_CAP_PEAK_RECHARGE,  # -2  capPeakRecharge
        _a.ATTR_DERIVED_CYCLE_TIME,  # -3  derivedCycleTime  技能 2304.64 → 2761.24
        _a.ATTR_CAP_PEAK_LOAD,  # -4  capPeakLoad   技能 22.1737 → 22.372；状态 → 0
        _a.ATTR_CAP_DELTA_PEAK,  # -5  capDeltaPeak  技能 -17.1737 → -17.372
        _a.ATTR_CAP_DELTA_PEAK_PCT,  # -6  capDeltaPeakPct
        _a.ATTR_CAP_DEPLETES_IN,  # -7  capDepletesIn  技能 28.8 → 25；状态 → -1
        _a.ATTR_CPU_FREE,  # -9  cpuFree      技能 -11 → -75
        _a.ATTR_POWER_FREE,  # -10 powerFree
        _a.ATTR_DAMAGE_ALPHA,  # -11 damageAlpha   技能 842.56 → 624.119；状态 → 0
        _a.ATTR_DAMAGE_DPS,  # -12 damageDps     技能 365.594 → 226.029；状态 → 0
        _a.ATTR_DAMAGE_DPS_WITH_RELOAD,  # -13 damageDpsWithReload
        _a.ATTR_DAMAGE_VOLLEY,  # -21 damageVolley（装备级）技能 168.512 → 124.824
        _a.ATTR_ARMOR_EFFECTIVE_RESONANCE,  # -25 剖面 0.470156 → 0.425
        _a.ATTR_HULL_EFFECTIVE_RESONANCE,  # -26
        _a.ATTR_SHIELD_EFFECTIVE_RESONANCE,  # -27 剖面 0.525 → 0.875
        _a.ATTR_EHP_ARMOR,  # -28 ehpArmor      技能 1786.64 → 1488.87；剖面 → 1976.47
        _a.ATTR_EHP_HULL,  # -29 ehpHull      技能 5830.22 → 4858.52
        _a.ATTR_EHP_SHIELD,  # -30 ehpShield     剖面 1238.1 → 742.857
        _a.ATTR_EHP,  # -43 ehp           技能 8854.96 → 7585.48；剖面 → 8549.55
        _a.ATTR_VELOCITY_BOOST,  # -44 velocityBoost  状态 488.71 → 0
        _a.ATTR_ARMOR_REPAIR_RATE,  # -45 armorRepairRate
        _a.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE,  # -48  技能 69.126 → 55.3008；状态 → 0
        _a.ATTR_HULL_EFFECTIVE_REPAIR_RATE,  # -49 hullEffectiveRepairRate
        _a.ATTR_SHIELD_EFFECTIVE_BOOST_RATE,  # -50 shieldEffectiveBoostRate
        _a.ATTR_PASSIVE_SHIELD_RECHARGE,  # -51
        _a.ATTR_PASSIVE_SHIELD_EFFECTIVE_RECHARGE,  # -52 剖面 3.86905 → 2.32143
        _a.ATTR_CALIBRATION_FREE,  # -69 calibrationFree
        _a.ATTR_CAP_STABLE_PCT,  # -72 capStablePct   状态 0 → 100
        # ---- 规则 3：技能轴变化（10 个）----
        _a.ATTR_HULL_HP,  # 9   hp           2343.75 → 1953.12（DC II 的结构层加成吃技能）
        _a.ATTR_MAX_VELOCITY,  # 37  maxVelocity  1271.61 → 1059.68
        _a.ATTR_CPU_OUTPUT,  # 48  cpuOutput    276 → 230（CPU Management）
        _a.ATTR_CPU_LOAD,  # 49  cpuLoad      287 → 305
        _a.ATTR_CYCLE_TIME,  # 51  speed        2304.64 → 2761.24（射速技能）
        _a.ATTR_DAMAGE_MULTIPLIER,  # 64 damageMultiplier  9.46697 → 7.01257
        _a.ATTR_AGILITY,  # 70  agility      5.52 → 6
        _a.ATTR_TRACKING_SPEED,  # 160 trackingSpeed  444.366 → 306.214
        _a.ATTR_ARMOR_HP,  # 265 armorHP      840 → 700（Hull Upgrades）
        _a.ATTR_HEAT_DAMAGE,  # 1211 heatDamage   装备级 0.64 → 0.76、4 → 4.75
        # ---- 规则 4：状态轴变化（2 个）----
        _a.ATTR_MASS,  # 4   mass             1550000 → 1050000（MWD active 的质量加成）
        _a.ATTR_SIGNATURE_RADIUS,  # 552 signatureRadius  385 → 70（MWD 信号膨胀）
    }
)

# =============================================================================
# 三、unknown：本样本里不存在 / 引擎没算（**默认分支也在这里**）
#
#    下面 35 个登记常量是「实测扫不到」的：无战斗机（-55…-62）、无模式
#    （1997–2000 / 2030）、弹药属货舱物品（114–118）、没有对应模块
#    （-14 / -46 / -47）…… 它们**没有**被复测，按红线 N4 一律不得标 measured。
# =============================================================================

#: 手工覆盖：唯一一条。`-70 maxTargets` 实测三轴都恒为 0 —— 那是「引擎没在算」，
#: 属不可知，不是「依赖假设」，所以既不能 measured（会冒充实测值）也不能 assumed
#: （会冒充"有假设支撑"）。`audit()["overrides"]` 会回显。
_MANUAL_OVERRIDES: dict[int, str] = {
    ATTR_MAX_TARGETS_UNVERIFIED: (
        "-70 maxTargets：三轴实测恒为 0（无变化），引擎未实现该派生 → 不可知，判 unknown"
    ),
}

UNKNOWN_ATTRS: frozenset[int] = frozenset(
    set(_MANUAL_OVERRIDES)
    | {
        # ---- 战斗机族（本样本无战斗机 → 引擎不输出）----
        _a.ATTR_FIGHTER_ABILITY_ATTACK_DPS,  # -55
        _a.ATTR_FIGHTER_ABILITY_MISSILES_DPS,  # -56
        _a.ATTR_FIGHTER_BAY_USED,  # -57
        _a.ATTR_FIGHTER_DPS,  # -58（T-06 在航母上实测过；本样本没有，故不得标）
        _a.ATTR_HEAVY_FIGHTER_SLOTS_USED,  # -59
        _a.ATTR_LIGHT_FIGHTER_SLOTS_USED,  # -60
        _a.ATTR_SUPPORT_FIGHTER_SLOTS_USED,  # -61
        _a.ATTR_FIGHTER_TUBES_USED,  # -62
        _a.ATTR_FIGHTER_SQUADRON_MAX_SIZE,  # 2215
        _a.ATTR_FIGHTER_TUBES,  # 2216
        _a.ATTR_LIGHT_FIGHTER_SLOTS,  # 2217
        _a.ATTR_SUPPORT_FIGHTER_SLOTS,  # 2218
        _a.ATTR_HEAVY_FIGHTER_SLOTS,  # 2219
        # ---- 无对应模块 → 引擎不输出该派生值 ----
        _a.ATTR_DRONE_DPS,  # -14（本样本无无人机）
        _a.ATTR_HULL_REPAIR_RATE,  # -46（无结构修复）
        _a.ATTR_SHIELD_BOOST_RATE,  # -47（无盾修）
        # ---- 弹药是货舱物品，其属性不经 `calculate` 输出 ----
        _a.ATTR_EM_DAMAGE,  # 114
        _a.ATTR_EXPLOSIVE_DAMAGE,  # 116
        _a.ATTR_KINETIC_DAMAGE,  # 117
        _a.ATTR_THERMAL_DAMAGE,  # 118
        _a.ATTR_WEAPON_RANGE_MULTIPLIER,  # 120
        _a.ATTR_AOE_VELOCITY,  # 653
        _a.ATTR_AOE_CLOUD_SIZE,  # 654
        _a.ATTR_AOE_FALLOFF,  # 655
        # ---- requiredSkill4..6（本样本的模块只用到 1..3）----
        _a.ATTR_REQUIRED_SKILL_4,  # 1285
        _a.ATTR_REQUIRED_SKILL_5,  # 1289
        _a.ATTR_REQUIRED_SKILL_6,  # 1290
        _a.ATTR_REQUIRED_SKILL_4_LEVEL,  # 1286
        _a.ATTR_REQUIRED_SKILL_5_LEVEL,  # 1287
        _a.ATTR_REQUIRED_SKILL_6_LEVEL,  # 1288
        # ---- 模式后除数（本样本无 mode）----
        _a.ATTR_MODE_EM_RESISTANCE_POST_DIV,  # 1997
        _a.ATTR_MODE_EXPLOSIVE_RESISTANCE_POST_DIV,  # 1998
        _a.ATTR_MODE_THERMIC_RESISTANCE_POST_DIV,  # 1999
        _a.ATTR_MODE_KINETIC_RESISTANCE_POST_DIV,  # 2000
        _a.ATTR_MODE_ARMOR_REP_DURATION_POST_DIV,  # 2030
    }
)


def _check_tables() -> None:
    """分类表自检：三态不得重叠。重叠会让 `tag()` 的结果取决于判断顺序。"""
    overlap = (MEASURED_ATTRS & ASSUMED_ATTRS) | (MEASURED_ATTRS & UNKNOWN_ATTRS) | (
        ASSUMED_ATTRS & UNKNOWN_ATTRS
    )
    if overlap:
        raise RuntimeError(f"provenance 分类表重叠（同一 ID 落在两态）：{sorted(overlap)}")
    stray = set(_MANUAL_OVERRIDES) - UNKNOWN_ATTRS
    if stray:
        raise RuntimeError(f"手工覆盖的 ID 没有进 UNKNOWN_ATTRS：{sorted(stray)}")


_check_tables()


def tag(attribute_id: int) -> Provenance:
    """按实测分类表返回三态。

    **默认 `unknown`**：表外的一切 ID（包括引擎将来新加的派生属性）都不会被误标成
    `measured`（红线 N4）。这是本函数最重要的性质，有专门单测。
    """
    if attribute_id in MEASURED_ATTRS:
        return "measured"
    if attribute_id in ASSUMED_ATTRS:
        return "assumed"
    return "unknown"


def typed(attribute_id: int, raw: float) -> contracts.TypedValue:
    """原始值 → `contracts.TypedValue`（换算 + 去噪 + 三态 + 单位 + 溯源 ID）。

    字段含义（契约 `TypedValue`）：
    - `value`：`units.physical(attribute_id, raw)` 之后经 `units.fmt` 去噪的物理值；
    - `provenance`：`tag(attribute_id)`；
    - `unit`：`attribute_dict.unit_of(attribute_id)`。契约写的是 `str`，但
      `attribute_dict` 对「SDE 与语义都给不出」的属性返回 `None`——这里**原样透传**，
      不猜单位（红线 N4 的同一条精神：不用默认值冒充 unknown）；
    - `source_attr`：该属性 ID 本身，供 `explain` 回查。

    去噪只发生在展示值上：需要参与计算的数值请用 `ViewContext.attrs()`（它不取整）。
    """
    return {
        "value": _units.fmt(_units.physical(attribute_id, raw)),
        "provenance": tag(attribute_id),
        "unit": _dict.unit_of(attribute_id),
        "source_attr": int(attribute_id),
    }


def audit() -> dict:
    """分类表自检视图（供测试与后续台账核对，纯计算）。

    返回：
    - `measured` / `assumed` / `unknown`：各类 ID 个数（`unknown` 含手工覆盖项）；
    - `derived_at` / `sample`：这张表是哪天、用哪份样本跑出来的（可复跑性）；
    - `overrides`：手工覆盖的 ID → 理由（必须逐条有据）；
    - `overlap`：重叠 ID（正常为空）；
    - `unnamed`：在分类表里但 `attribute_dict` 没有名字来源的 ID（正常为空；`-70`
      除外——它本来就未登记，单列在 `unregistered`）；
    - `unregistered`：`unknown` 类里尚未进 `config/attributes.py` 的 ID；
    - `consistent`：上述错误项全空时为 True。
    """
    overlap = sorted(
        (MEASURED_ATTRS & ASSUMED_ATTRS)
        | (MEASURED_ATTRS & UNKNOWN_ATTRS)
        | (ASSUMED_ATTRS & UNKNOWN_ATTRS)
    )
    all_ids = MEASURED_ATTRS | ASSUMED_ATTRS | UNKNOWN_ATTRS
    unnamed = sorted(aid for aid in all_ids if _dict.name_of(aid) == f"attr_{aid}")
    unregistered = sorted(UNKNOWN_ATTRS & set(unnamed))

    return {
        "measured": len(MEASURED_ATTRS),
        "assumed": len(ASSUMED_ATTRS),
        "unknown": len(UNKNOWN_ATTRS),
        "derived_at": DERIVED_AT,
        "sample": DERIVED_SAMPLE,
        "overrides": {aid: why for aid, why in sorted(_MANUAL_OVERRIDES.items())},
        "overlap": overlap,
        "unnamed": [aid for aid in unnamed if aid not in UNKNOWN_ATTRS],
        "unregistered": unregistered,
        "consistent": not overlap and not (set(unnamed) - UNKNOWN_ATTRS),
    }
