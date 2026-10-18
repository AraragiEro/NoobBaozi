"""三态 provenance 判定（L3）—— **表由实测生成，不得手改**。

> 本表由 `evefit/tools/derive_provenance.py` 的**多样本 × 三轴敏感性实验**实测生成，
> 并由该脚本**重写**这三张 frozenset。不要手改表内容；SDE / 引擎 / 样本变更后重跑
> 该脚本（它会同时重写本模块并落证据 `tools/provenance_report.json`）。
> 生成日期与样本见 `DERIVED_AT` / `DERIVED_SAMPLES`（`audit()` 会回显）。

背景（红线 N3 / N4，字段规格 §0.2，第 16 轮裁决）：

- **引擎不给 provenance**——`dogma.calculate` 只回数值，不回「这个数是怎么来的」。
- **provenance 只表示「这个数在本次计算里是否依赖假设」**，与「可信度 A–E」是
  **正交维度**。第 16 轮之前那张手写表把两者混成一列，与验收员的
  `field_spec.json` 冲突了 7 处；根因就是这种混淆，而不是规则算错。
- 判定必须**默认 `unknown`**：新增属性、未复测属性、表外属性一律不得被误标成
  `measured`（红线 N4：不用默认值冒充 unknown）。

两級模型（PROGRESS §3 第 60 条，T-14 落地）
------------------------------------------
1. **属性级基线类**（本模块）：该属性的值是否随技能 / 状态 / 伤害剖面变化 —— 由
   **多样本**三轴实验得出。至少在一个样本里出现、且在出现它的每个样本上三轴都不变
   才是 `measured`；任一轴在任一出现过它的样本上变化就是 `assumed`。
2. **实例级覆盖**（投影层）：某次计算里这个属性**没有键** → 那次计算里它是未知。
   这一级不在本表里，也不该由本表表达 —— 否则「本样本没有 T3C 子系统」会被误解成
   「`1367` 这个属性不可知」。

三态的定义
----------
- `measured`：在**出现过它的每个样本**上都不随三轴假设变化（技能档、模块状态、
  伤害剖面都不改变它）。例：槽位/槽位余量/结构层与装甲层共振率/过载热参数/技能要求/
  `chargeRate`/`reloadTime`/`volume`/`aoeVelocity`/T3C 的 `1367 maxSubSystems`。
- `assumed`：依赖上述任一假设。四类来源：
  1. **负 ID**（引擎 pass 4 派生值，`-1` … `-72`）—— 一律 `assumed`，即使全部样本里
     都没有这个键（派生值天然建立在技能 / 状态 / 剖面之上）；
  2. **技能轴变化**（全4+补最低 → 全0）：例 `9 hp`、`11 powerOutput`、`48 cpuOutput`、
     `51 speed`、`263 shieldCapacity`（`Shield Management`）、`265 armorHP`；
  3. **状态轴变化**（全active → 全online）：例 `4 mass`（MWD 推力质量加成）、
     `552 signatureRadius`（MWD 信号膨胀）、**`271`–`274` 护盾共振率**（盾抗模块
     `active` 才给抗性 —— T-14 加样本后才暴露，Hecate 样本没有盾抗，看不到）；
  4. **伤害剖面轴变化**：只影响负 ID 派生值（分层 EHP、有效共振等）。
- `unknown`：**全部样本里都不存在**（引擎没输出）或引擎根本没算的属性：`120
  weaponRangeMultiplier`、`655 aoeFalloff`、`1288/1290 requiredSkill6(*Level)`、
  模式后除数 `1997`–`2000` / `2030`（四个样本都没有 mode），以及 `-70 maxTargets`
  的手工覆盖。**表外的一切 ID 仍然默认 `unknown`**（`tag()` 的默认分支）。

三条硬规则（与实测表一起构成不变量）
------------------------------------
1. **默认值是 `unknown`**（红线 N4）：`tag()` 的默认分支必须落在 `unknown`。
2. **分类表与原属性表分离**：本模块只做「ID → 三态」的一维映射，不复制名称 /
   单位 / 缩放（那些在 `sde/attribute_dict.py`）。重复一份就会漂移。
3. **手工覆盖必须写理由**：见 `_MANUAL_OVERRIDES`，每条都要有实测依据。

已知的**轴覆盖局限**（实测取证，不影响上面的表，但必须知道）
-----------------------------------------------------------
技能轴的 B 变体是「全部技能 0」，A 变体是 `all4+min_required`（实测 512 个技能）；
技能**等级上限**只有 4/5 两档。`derive_provenance.py` 的第六节用抽样技能升一级做
取证（例：`Power Grid Management` 4→5 → `11 powerOutput` 上升），把该局限写成可
复跑的证词 —— 但**不据此改表**。要收紧，得**先改轴的定义**再重跑，不能在表里手工拍。

T-14 修掉的两个**静默退化**（后来者不要再犯）
--------------------------------------------
1. **技能档必须传 `index`**：`derive_skill_tier(fit, engine)` 不传 `index` 会退化成
   `min_required_only`（19 个技能）且**不报错**，技能轴整条失效（旧表把 `263
   shieldCapacity` 等判成 measured 就是这个原因）。工具现在有硬护栏：档名必须是
   `all4+min_required` 且技能数 > 100，否则 `RuntimeError`。
2. **单样本推导会误判 `unknown`**：只有「全部样本都没有」才是 `unknown`；`1367`
   （T3C）、`281`（导弹）、`1272`/`-14`/`-55…-62`（无人机/战斗机）都因样本不足被
   困成 `unknown` 过。现在四个样本覆盖基础/子系统/导弹/战斗机+无人机。

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

#: 实验用的样本（多样本 × 三轴敏感性扫描的对象，由脚本重写）。
DERIVED_SAMPLE: str = "f1-hecate-killmail.eft (killmail 139246907, Hecate T3D) + f4-non-hecate.json (killmail 139254056, Loki T3C) + f15-battleship-raven.json (Raven, 弹道 + 导弹) + 构造样本：Nyx + 6×Templar II + 6×Gungnir II + 3×Cenobite II + 5×Hornet II"

#: 样本清单（每个样本各自跑三轴，取并集判定；由脚本重写）。
DERIVED_SAMPLES: tuple[str, ...] = ("f1-hecate-killmail.eft (killmail 139246907, Hecate T3D)", "f4-non-hecate.json (killmail 139254056, Loki T3C)", "f15-battleship-raven.json (Raven, 弹道 + 导弹)", "构造样本：Nyx + 6×Templar II + 6×Gungnir II + 3×Cenobite II + 5×Hornet II")

#: `-70` = `maxTargets`（实测恒为 0，引擎没有在算它）。字段规格未登记、
#: `config/attributes.py` 也还没有常量（该文件本轮为只读），故常量就近定义在这里，
#: 并在 `audit()` 里露出。等 `config/attributes.py` 可写时应迁过去（红线 N12）。
ATTR_MAX_TARGETS_UNVERIFIED: int = -70

# =============================================================================
# 一、measured：至少在一个样本里出现，且在出现它的每个样本上三轴都不变
#
#    **内容由 `tools/derive_provenance.py` 生成**（按属性 ID 升序，便于与报告 diff）；
#    计数见 `audit()` 与 `tools/provenance_report.json`。
# =============================================================================
MEASURED_ATTRS: frozenset[int] = frozenset(
    {
        _a.ATTR_LOW_SLOTS,  # 12  lowSlots  （4/4 个样本出现，三轴不变）
        _a.ATTR_MED_SLOTS,  # 13  medSlots  （4/4 个样本出现，三轴不变）
        _a.ATTR_HI_SLOTS,  # 14  hiSlots  （4/4 个样本出现，三轴不变）
        _a.ATTR_CHARGE_RATE,  # 56  chargeRate  （3/4 个样本出现，三轴不变）
        _a.ATTR_LAUNCHER_SLOTS_LEFT,  # 101  launcherSlotsLeft  （4/4 个样本出现，三轴不变）
        _a.ATTR_TURRET_SLOTS_LEFT,  # 102  turretSlotsLeft  （4/4 个样本出现，三轴不变）
        _a.ATTR_HULL_KINETIC_RESONANCE,  # 109  kineticDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_HULL_THERMAL_RESONANCE,  # 110  thermalDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_HULL_EXPLOSIVE_RESONANCE,  # 111  explosiveDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_HULL_EM_RESONANCE,  # 113  emDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_EM_DAMAGE,  # 114  emDamage  （3/4 个样本出现，三轴不变）
        _a.ATTR_EXPLOSIVE_DAMAGE,  # 116  explosiveDamage  （3/4 个样本出现，三轴不变）
        _a.ATTR_KINETIC_DAMAGE,  # 117  kineticDamage  （3/4 个样本出现，三轴不变）
        _a.ATTR_THERMAL_DAMAGE,  # 118  thermalDamage  （3/4 个样本出现，三轴不变）
        _a.ATTR_CHARGE_SIZE,  # 128  chargeSize  （2/4 个样本出现，三轴不变）
        _a.ATTR_VOLUME,  # 161  volume  （4/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_1,  # 182  requiredSkill1  （4/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_2,  # 183  requiredSkill2  （4/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_3,  # 184  requiredSkill3  （3/4 个样本出现，三轴不变）
        _a.ATTR_MAX_LOCKED_TARGETS,  # 192  maxLockedTargets  （4/4 个样本出现，三轴不变）
        _a.ATTR_ARMOR_EM_RESONANCE,  # 267  armorEmDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_ARMOR_EXPLOSIVE_RESONANCE,  # 268  armorExplosiveDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_ARMOR_KINETIC_RESONANCE,  # 269  armorKineticDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_ARMOR_THERMAL_RESONANCE,  # 270  armorThermalDamageResonance  （4/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_1_LEVEL,  # 277  requiredSkill1Level  （4/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_2_LEVEL,  # 278  requiredSkill2Level  （4/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_3_LEVEL,  # 279  requiredSkill3Level  （3/4 个样本出现，三轴不变）
        _a.ATTR_DRONE_CAPACITY,  # 283  droneCapacity  （4/4 个样本出现，三轴不变）
        _a.ATTR_WARP_SPEED_MULTIPLIER,  # 600  warpSpeedMultiplier  （4/4 个样本出现，三轴不变）
        _a.ATTR_CHARGE_GROUP_1,  # 604  chargeGroup1  （3/4 个样本出现，三轴不变）
        _a.ATTR_CHARGE_GROUP_2,  # 605  chargeGroup2  （3/4 个样本出现，三轴不变）
        _a.ATTR_OPTIMAL_SIG_RADIUS,  # 620  optimalSigRadius  （3/4 个样本出现，三轴不变）
        _a.ATTR_AOE_VELOCITY,  # 653  aoeVelocity  （2/4 个样本出现，三轴不变）
        _a.ATTR_CALIBRATION_OUTPUT,  # 1132  upgradeCapacity  （4/4 个样本出现，三轴不变）
        _a.ATTR_RIG_SLOTS,  # 1137  rigSlots  （4/4 个样本出现，三轴不变）
        _a.ATTR_CALIBRATION_LOAD,  # 1152  upgradeLoad  （3/4 个样本出现，三轴不变）
        _a.ATTR_UPGRADE_SLOTS_LEFT,  # 1154  upgradeSlotsLeft  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_CAPACITY_HI,  # 1178  heatCapacityHi  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_DISSIPATION_RATE_HI,  # 1179  heatDissipationRateHi  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_DISSIPATION_RATE_MED,  # 1196  heatDissipationRateMed  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_DISSIPATION_RATE_LOW,  # 1198  heatDissipationRateLow  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_CAPACITY_MED,  # 1199  heatCapacityMed  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_CAPACITY_LOW,  # 1200  heatCapacityLow  （4/4 个样本出现，三轴不变）
        _a.ATTR_OVERLOAD_DAMAGE_MODIFIER,  # 1210  overloadDamageModifier  （1/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_GENERATION_MULTIPLIER,  # 1224  heatGenerationMultiplier  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_ATTENUATION_HI,  # 1259  heatAttenuationHi  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_ATTENUATION_MED,  # 1261  heatAttenuationMed  （4/4 个样本出现，三轴不变）
        _a.ATTR_HEAT_ATTENUATION_LOW,  # 1262  heatAttenuationLow  （4/4 个样本出现，三轴不变）
        _a.ATTR_DRONE_BANDWIDTH,  # 1271  droneBandwidth  （4/4 个样本出现，三轴不变）
        _a.ATTR_DRONE_BANDWIDTH_USED,  # 1272  droneBandwidthUsed  （2/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_4,  # 1285  requiredSkill4  （1/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_4_LEVEL,  # 1286  requiredSkill4Level  （1/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_5_LEVEL,  # 1287  requiredSkill5Level  （1/4 个样本出现，三轴不变）
        _a.ATTR_REQUIRED_SKILL_5,  # 1289  requiredSkill5  （1/4 个样本出现，三轴不变）
        _a.ATTR_MAX_SUBSYSTEMS,  # 1367  maxSubSystems  （1/4 个样本出现，三轴不变）
        _a.ATTR_RELOAD_TIME,  # 1795  reloadTime  （3/4 个样本出现，三轴不变）
        _a.ATTR_STANCE_SWITCH_TIME,  # 1985  stanceSwitchTime  （1/4 个样本出现，三轴不变）
        _a.ATTR_ENERGY_WARFARE_RESISTANCE,  # 2045  energyWarfareResistance  （4/4 个样本出现，三轴不变）
        _a.ATTR_SENSOR_DAMPENER_RESISTANCE,  # 2112  sensorDampenerResistance  （4/4 个样本出现，三轴不变）
        _a.ATTR_WEAPON_DISRUPTION_RESISTANCE,  # 2113  weaponDisruptionResistance  （4/4 个样本出现，三轴不变）
        _a.ATTR_STASIS_WEBIFIER_RESISTANCE,  # 2115  stasisWebifierResistance  （4/4 个样本出现，三轴不变）
        _a.ATTR_FIGHTER_SQUADRON_MAX_SIZE,  # 2215  fighterSquadronMaxSize  （1/4 个样本出现，三轴不变）
        _a.ATTR_FIGHTER_TUBES,  # 2216  fighterTubes  （1/4 个样本出现，三轴不变）
        _a.ATTR_LIGHT_FIGHTER_SLOTS,  # 2217  fighterLightSlots  （1/4 个样本出现，三轴不变）
        _a.ATTR_SUPPORT_FIGHTER_SLOTS,  # 2218  fighterSupportSlots  （1/4 个样本出现，三轴不变）
        _a.ATTR_HEAVY_FIGHTER_SLOTS,  # 2219  fighterHeavySlots  （1/4 个样本出现，三轴不变）
    }
)

# =============================================================================
# 二、assumed：依赖假设（负 ID / 技能档 / 状态 / 伤害剖面）
#
#    **内容由 `tools/derive_provenance.py` 生成**，按「命中哪条判定规则」分组
#    （规则顺序即归因优先级）；行尾注释写「哪个样本 × 哪一轴」触发的。
# =============================================================================
ASSUMED_ATTRS: frozenset[int] = frozenset(
    {
        # ---- 规则 2 · 负 ID（引擎 pass 4 派生）：40 个 ----
        _a.ATTR_CAP_STABLE_PCT,  # -72  capStablePct  ← 技能轴: f4；状态轴: f1/f4/f15
        _a.ATTR_CALIBRATION_FREE,  # -69  calibrationFree  ← 负 ID：pass 4 派生值，一律 assumed；4/4 个样本里出现
        _a.ATTR_FIGHTER_TUBES_USED,  # -62  fighterTubesUsed  ← 负 ID：pass 4 派生值，一律 assumed；1/4 个样本里出现
        _a.ATTR_SUPPORT_FIGHTER_SLOTS_USED,  # -61  fighterSupportSlotsUsed  ← 负 ID：pass 4 派生值，一律 assumed；1/4 个样本里出现
        _a.ATTR_LIGHT_FIGHTER_SLOTS_USED,  # -60  fighterLightSlotsUsed  ← 负 ID：pass 4 派生值，一律 assumed；1/4 个样本里出现
        _a.ATTR_HEAVY_FIGHTER_SLOTS_USED,  # -59  fighterHeavySlotsUsed  ← 负 ID：pass 4 派生值，一律 assumed；1/4 个样本里出现
        _a.ATTR_FIGHTER_DPS,  # -58  fighterDps  ← 技能轴: carrier
        _a.ATTR_FIGHTER_BAY_USED,  # -57  fighterCapacityLoad  ← 负 ID：pass 4 派生值，一律 assumed；1/4 个样本里出现
        _a.ATTR_FIGHTER_ABILITY_MISSILES_DPS,  # -56  fighterAbilityMissilesDamagePerSecond  ← 技能轴: carrier
        _a.ATTR_FIGHTER_ABILITY_ATTACK_DPS,  # -55  fighterAbilityAttackMissileDamagePerSecond  ← 技能轴: carrier
        _a.ATTR_PASSIVE_SHIELD_EFFECTIVE_RECHARGE,  # -52  passiveShieldEffectiveRecharge  ← 技能轴: f1/f4/f15/carrier；状态轴: f4/f15；剖面轴: f1/f4/f15/carrier
        _a.ATTR_PASSIVE_SHIELD_RECHARGE,  # -51  passiveShieldRecharge  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_SHIELD_EFFECTIVE_BOOST_RATE,  # -50  shieldEffectiveBoostRate  ← 技能轴: f4；状态轴: f4/f15；剖面轴: f4/f15
        _a.ATTR_HULL_EFFECTIVE_REPAIR_RATE,  # -49  hullEffectiveRepairRate  ← 负 ID：pass 4 派生值，一律 assumed；4/4 个样本里出现
        _a.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE,  # -48  armorEffectiveRepairRate  ← 技能轴: f1；状态轴: f1；剖面轴: f1
        _a.ATTR_SHIELD_BOOST_RATE,  # -47  shieldBoostRate  ← 技能轴: f4；状态轴: f4/f15
        _a.ATTR_HULL_REPAIR_RATE,  # -46  hullRepairRate  ← 负 ID：pass 4 派生值，一律 assumed；0/4 个样本里出现
        _a.ATTR_ARMOR_REPAIR_RATE,  # -45  armorRepairRate  ← 技能轴: f1；状态轴: f1
        _a.ATTR_VELOCITY_BOOST,  # -44  velocityBoost  ← 技能轴: f1；状态轴: f1
        _a.ATTR_EHP,  # -43  ehp  ← 技能轴: f1/f4/f15/carrier；状态轴: f4/f15；剖面轴: f1/f4/f15/carrier
        _a.ATTR_EHP_SHIELD,  # -30  ehpShield  ← 技能轴: f1/f4/f15/carrier；状态轴: f4/f15；剖面轴: f1/f4/f15/carrier
        _a.ATTR_EHP_HULL,  # -29  ehpHull  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_EHP_ARMOR,  # -28  ehpArmor  ← 技能轴: f1/f4/f15/carrier；剖面轴: f1/f4/f15/carrier
        _a.ATTR_SHIELD_EFFECTIVE_RESONANCE,  # -27  shieldEffectiveResonance  ← 状态轴: f4/f15；剖面轴: f1/f4/f15/carrier
        _a.ATTR_HULL_EFFECTIVE_RESONANCE,  # -26  hullEffectiveResonance  ← 负 ID：pass 4 派生值，一律 assumed；4/4 个样本里出现
        _a.ATTR_ARMOR_EFFECTIVE_RESONANCE,  # -25  armorEffectiveResonance  ← 剖面轴: f1/f4/f15/carrier
        _a.ATTR_DAMAGE_VOLLEY,  # -21  damageVolley  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_DRONE_DPS,  # -14  droneDps  ← 技能轴: f4/carrier
        _a.ATTR_DAMAGE_DPS_WITH_RELOAD,  # -13  damageDpsWithReload  ← 技能轴: f1/f4/f15/carrier；状态轴: f1/f15
        _a.ATTR_DAMAGE_DPS,  # -12  damageDps  ← 技能轴: f1/f4/f15/carrier；状态轴: f1/f15
        _a.ATTR_DAMAGE_ALPHA,  # -11  damageAlpha  ← 技能轴: f1/f15；状态轴: f1/f15
        _a.ATTR_POWER_FREE,  # -10  powerFree  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_CPU_FREE,  # -9  cpuFree  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_CAP_DEPLETES_IN,  # -7  capDepletesIn  ← 技能轴: f1/f4/f15；状态轴: f1/f15
        _a.ATTR_CAP_DELTA_PEAK_PCT,  # -6  capDeltaPeakPct  ← 技能轴: f1/f4/f15；状态轴: f1/f4/f15
        _a.ATTR_CAP_DELTA_PEAK,  # -5  capDeltaPeak  ← 技能轴: f1/f4/f15/carrier；状态轴: f1/f4/f15
        _a.ATTR_CAP_PEAK_LOAD,  # -4  capPeakLoad  ← 技能轴: f1/f4/f15；状态轴: f1/f4/f15
        _a.ATTR_DERIVED_CYCLE_TIME,  # -3  derivedCycleTime  ← 技能轴: f1/f15；状态轴: f1/f4/f15
        _a.ATTR_CAP_PEAK_RECHARGE,  # -2  capPeakRecharge  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_ALIGN_TIME,  # -1  alignTime  ← 技能轴: f1/f4/f15/carrier；状态轴: f1
        # ---- 规则 3 · 技能轴变化：26 个 ----
        _a.ATTR_HULL_HP,  # 9  hp  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_POWER_OUTPUT,  # 11  powerOutput  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_POWER_LOAD,  # 15  powerLoad  ← 技能轴: f1/f4/f15
        _a.ATTR_MAX_VELOCITY,  # 37  maxVelocity  ← 技能轴: f1/f4/f15/carrier；状态轴: f1
        _a.ATTR_CARGO_CAPACITY,  # 38  capacity  ← 技能轴: f1
        _a.ATTR_CPU_OUTPUT,  # 48  cpuOutput  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_CPU_LOAD,  # 49  cpuLoad  ← 技能轴: f1/f4/f15
        _a.ATTR_CYCLE_TIME,  # 51  speed  ← 技能轴: f1/f4/f15
        _a.ATTR_MAX_RANGE,  # 54  maxRange  ← 技能轴: f1/f4/carrier
        _a.ATTR_CAPACITOR_RECHARGE_RATE,  # 55  rechargeRate  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_DAMAGE_MULTIPLIER,  # 64  damageMultiplier  ← 技能轴: f1/f4/carrier
        _a.ATTR_AGILITY,  # 70  agility  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_MODULE_DURATION,  # 73  duration  ← 技能轴: f1/f4/f15
        _a.ATTR_MAX_TARGET_RANGE,  # 76  maxTargetRange  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_SCAN_SPEED,  # 79  scanSpeed  ← 技能轴: f1/f15/carrier
        _a.ATTR_FALLOFF,  # 158  falloff  ← 技能轴: f1
        _a.ATTR_TRACKING_SPEED,  # 160  trackingSpeed  ← 技能轴: f1
        _a.ATTR_SHIELD_CAPACITY,  # 263  shieldCapacity  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_ARMOR_HP,  # 265  armorHP  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_EXPLOSION_DELAY,  # 281  explosionDelay  ← 技能轴: f4
        _a.ATTR_SHIELD_RECHARGE_RATE,  # 479  shieldRechargeRate  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_CAPACITOR_CAPACITY,  # 482  capacitorCapacity  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_SIGNATURE_RADIUS,  # 552  signatureRadius  ← 技能轴: f15；状态轴: f1/f15
        _a.ATTR_SCAN_RESOLUTION,  # 564  scanResolution  ← 技能轴: f1/f4/f15/carrier
        _a.ATTR_AOE_CLOUD_SIZE,  # 654  aoeCloudSize  ← 技能轴: f4
        _a.ATTR_HEAT_DAMAGE,  # 1211  heatDamage  ← 技能轴: f1/f4/f15/carrier
        # ---- 规则 4 · 状态轴变化：5 个 ----
        _a.ATTR_MASS,  # 4  mass  ← 状态轴: f1
        _a.ATTR_SHIELD_EM_RESONANCE,  # 271  shieldEmDamageResonance  ← 状态轴: f4/f15
        _a.ATTR_SHIELD_EXPLOSIVE_RESONANCE,  # 272  shieldExplosiveDamageResonance  ← 状态轴: f4/f15
        _a.ATTR_SHIELD_KINETIC_RESONANCE,  # 273  shieldKineticDamageResonance  ← 状态轴: f4/f15
        _a.ATTR_SHIELD_THERMAL_RESONANCE,  # 274  shieldThermalDamageResonance  ← 状态轴: f4/f15
    }
)

# =============================================================================
# 三、unknown：**全部样本**里都不存在 / 引擎没算（**默认分支也在这里**）
#
#    **内容由 `tools/derive_provenance.py` 生成**。注意「某个样本里没有」**不再**
#    等于 unknown（T-14 修掉的单样本缺陷）—— 只有四个样本全都没有才算。
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
    set(_MANUAL_OVERRIDES) | {
        _a.ATTR_WEAPON_RANGE_MULTIPLIER,  # 120  weaponRangeMultiplier  （4 个样本里都没有这个键）
        _a.ATTR_AOE_FALLOFF,  # 655  aoeFalloff  （4 个样本里都没有这个键）
        _a.ATTR_REQUIRED_SKILL_6_LEVEL,  # 1288  requiredSkill6Level  （4 个样本里都没有这个键）
        _a.ATTR_REQUIRED_SKILL_6,  # 1290  requiredSkill6  （4 个样本里都没有这个键）
        _a.ATTR_MODE_EM_RESISTANCE_POST_DIV,  # 1997  modeEmResistancePostDiv  （4 个样本里都没有这个键）
        _a.ATTR_MODE_EXPLOSIVE_RESISTANCE_POST_DIV,  # 1998  modeExplosiveResistancePostDiv  （4 个样本里都没有这个键）
        _a.ATTR_MODE_THERMIC_RESISTANCE_POST_DIV,  # 1999  modeThermicResistancePostDiv  （4 个样本里都没有这个键）
        _a.ATTR_MODE_KINETIC_RESISTANCE_POST_DIV,  # 2000  modeKineticResistancePostDiv  （4 个样本里都没有这个键）
        _a.ATTR_MODE_ARMOR_REP_DURATION_POST_DIV,  # 2030  modeArmorRepDurationPostDiv  （4 个样本里都没有这个键）
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
    - `derived_at` / `sample` / `samples`：这张表是哪天、用哪些样本跑出来的（可复跑性）；
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
        "samples": list(DERIVED_SAMPLES),
        "overrides": {aid: why for aid, why in sorted(_MANUAL_OVERRIDES.items())},
        "overlap": overlap,
        "unnamed": [aid for aid in unnamed if aid not in UNKNOWN_ATTRS],
        "unregistered": unregistered,
        "consistent": not overlap and not (set(unnamed) - UNKNOWN_ATTRS),
    }
