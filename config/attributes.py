"""evefit 属性常量表 + 单位/缩放表（全项目唯一允许出现属性数字 ID 的地方）。

为什么必须集中（开发规范 §4.2，红线 N12）：
属性 ID 是纯数字魔法数，散落在业务代码里既无法审查，也必然写错——
例如 `requiredSkill4` 不是 185（185 是 `attributePoints`），而是 1285。
其它模块一律 `from evefit.config.attributes import ATTR_XXX`，禁止裸写数字。

来源标记（每一行 ID 都有下列之一作证，注释里标了具体哪一个）：
- `esi`   ESI `/dogma/attributes/{id}/` 实测返回的 name / unit_id / default_value
          （2026-10-18，SDE build 3607705）
- `spike` 本项目 `evefit/spike/*.py` 的实测脚本（见注释里的脚本名）
- `spec`  `docs/配装理解-字段规格-v1.md`、`PROGRESS.md` §3 的实测硬结论
- `task`  本任务规格给定但未独立复测（派生负 ID 无法从 ESI 反查，只能由
          引擎输出侧确认"有值"，语义来自 `spec`/`task`）

缩放约定（与 `contracts.AttributeRow.scale` 注释逐字对应）：
- `SCALE_ONE`   = `1.0`    存储值即物理值
- `SCALE_MILLI` = `0.001`  存储值是**毫秒**，×0.001 得秒
- `SCALE_KILO`  = `1000.0` 存储值被**放大 1000 倍**，÷1000 得物理值

两条非 1 约定在算术上都是 ×0.001，但语义不同，必须分开记：前者说"单位是
毫秒"，后者说"数值被放大了"。`attribute_dict.to_physical` 按语义分支，不按
数值猜测。

本模块与 `evefit/sde/attribute_dict.py` 都是**纯声明**：无 IO、无网络、无随机。
"""

from __future__ import annotations

# 常量名前缀：audit() 与测试据此枚举全表，避免在多处硬编码 "ATTR_"。
CONSTANT_PREFIX: str = "ATTR_"

# ---- 缩放约定（契约 AttributeRow.scale）----
SCALE_ONE: float = 1.0
SCALE_MILLI: float = 0.001
SCALE_KILO: float = 1000.0

# =============================================================================
# 一、船体 / 结构 / 容量 / 机动     来源：esi
# =============================================================================
ATTR_HULL_HP: int = 9                    # hp —— 结构 HP（字段规格 §2 明确用 9）
ATTR_SHIELD_CAPACITY: int = 263          # shieldCapacity —— 护盾层基础 HP
ATTR_SHIELD_RECHARGE_RATE: int = 479     # shieldRechargeRate —— 护盾回充时间（存储毫秒）
ATTR_ARMOR_HP: int = 265                 # armorHP —— 装甲层基础 HP
ATTR_MASS: int = 4                       # mass（kg；对齐时间公式入参）
ATTR_AGILITY: int = 70                   # agility（对齐时间公式入参）
ATTR_MAX_VELOCITY: int = 37              # maxVelocity（m/s）
ATTR_SIGNATURE_RADIUS: int = 552         # signatureRadius（m）
ATTR_WARP_SPEED_MULTIPLIER: int = 600    # warpSpeedMultiplier（×；AU/s 倍数）
ATTR_CAPACITOR_RECHARGE_RATE: int = 55   # rechargeRate —— 电容回充时间（存储毫秒）
ATTR_CAPACITOR_CAPACITY: int = 482       # capacitorCapacity（GJ）
ATTR_CARGO_CAPACITY: int = 38            # capacity —— 货舱容量（m3）
ATTR_VOLUME: int = 161                   # volume —— 包装体积（m3）
ATTR_SCAN_RESOLUTION: int = 564          # scanResolution（mm；spike/mobility.py）
ATTR_MAX_TARGET_RANGE: int = 76          # maxTargetRange（m；spike/mobility.py）
ATTR_MAX_LOCKED_TARGETS: int = 192       # maxLockedTargets（个；esi 无 unit，推定）
ATTR_SCAN_SPEED: int = 79                # scanSpeed —— 扫描分辨率重扫时间（存储毫秒）

# =============================================================================
# 二、资源与槽位                   来源：esi
# =============================================================================
ATTR_POWER_OUTPUT: int = 11              # powerOutput（MW）
ATTR_POWER_LOAD: int = 15                # powerLoad（MW）
ATTR_CPU_OUTPUT: int = 48                # cpuOutput（tf）
ATTR_CPU_LOAD: int = 49                  # cpuLoad（tf）
ATTR_CALIBRATION_OUTPUT: int = 1132      # upgradeCapacity —— 标定总量
ATTR_CALIBRATION_LOAD: int = 1152        # upgradeLoad —— 标定占用（esi 无 unit，同 1132 推定）
ATTR_HI_SLOTS: int = 14                  # hiSlots
ATTR_MED_SLOTS: int = 13                 # medSlots
ATTR_LOW_SLOTS: int = 12                 # lowSlots
ATTR_RIG_SLOTS: int = 1137               # rigSlots
ATTR_UPGRADE_SLOTS_LEFT: int = 1154      # upgradeSlotsLeft —— rig 槽剩余（PROGRESS §3）
ATTR_MAX_SUBSYSTEMS: int = 1367          # maxSubSystems —— T3C 船体的子系统槽总量（SDE displayName = `Subsystem Slots`；Loki = 5）
ATTR_TURRET_SLOTS_LEFT: int = 102        # turretSlotsLeft —— 炮台挂点剩余
ATTR_LAUNCHER_SLOTS_LEFT: int = 101      # launcherSlotsLeft —— 发射器挂点剩余
ATTR_DRONE_BANDWIDTH: int = 1271         # droneBandwidth（Mbit/s；PROGRESS §3 确认）
ATTR_DRONE_BANDWIDTH_USED: int = 1272    # droneBandwidthUsed —— **逐架**无人机占用的带宽（Mbit/s；T-13 索引库核实，unit_id 128 与 1271 同族）
ATTR_DRONE_CAPACITY: int = 283           # droneCapacity（m3；PROGRESS §3 确认）

# =============================================================================
# 三、真实抗性 = 共振率（**必须用正 ID**，不要用负的加权值）  来源：esi + spec §2
#     注意结构层四个 ID 不连号：EM=113 / EXP=111 / KIN=109 / THM=110。
# =============================================================================
ATTR_SHIELD_EM_RESONANCE: int = 271       # shieldEmDamageResonance
ATTR_SHIELD_EXPLOSIVE_RESONANCE: int = 272  # shieldExplosiveDamageResonance
ATTR_SHIELD_KINETIC_RESONANCE: int = 273    # shieldKineticDamageResonance
ATTR_SHIELD_THERMAL_RESONANCE: int = 274    # shieldThermalDamageResonance
ATTR_ARMOR_EM_RESONANCE: int = 267         # armorEmDamageResonance
ATTR_ARMOR_EXPLOSIVE_RESONANCE: int = 268  # armorExplosiveDamageResonance
ATTR_ARMOR_KINETIC_RESONANCE: int = 269     # armorKineticDamageResonance
ATTR_ARMOR_THERMAL_RESONANCE: int = 270     # armorThermalDamageResonance
ATTR_HULL_EM_RESONANCE: int = 113          # emDamageResonance（结构层）
ATTR_HULL_EXPLOSIVE_RESONANCE: int = 111   # explosiveDamageResonance（结构层）
ATTR_HULL_KINETIC_RESONANCE: int = 109     # kineticDamageResonance（结构层）
ATTR_HULL_THERMAL_RESONANCE: int = 110     # thermalDamageResonance（结构层）

# =============================================================================
# 四、武器 / 弹药                   来源：esi + spike/weapon_attrs.py 实测
# =============================================================================
ATTR_MAX_RANGE: int = 54                 # maxRange —— 最佳射程（m）
ATTR_FALLOFF: int = 158                  # falloff（m）
ATTR_TRACKING_SPEED: int = 160           # trackingSpeed（存储值 ×1000，见缩放表）
ATTR_OPTIMAL_SIG_RADIUS: int = 620       # optimalSigRadius（存储值 ×1000，见缩放表）
ATTR_DAMAGE_MULTIPLIER: int = 64         # damageMultiplier（×；弹药伤害的乘子）
ATTR_CYCLE_TIME: int = 51                # speed —— 模块激活周期（存储毫秒）
ATTR_MODULE_DURATION: int = 73           # duration —— **非武器模块**的激活周期（**存储毫秒**；T-13 实测 ANP 4800 = 4.8 s，unit_id 101）
ATTR_CHARGE_RATE: int = 56               # chargeRate —— 每循环消耗的弹药/纳米胶数量（件/循环；T-13 索引库核实）
ATTR_RELOAD_TIME: int = 1795             # reloadTime（存储毫秒）
ATTR_OVERLOAD_DAMAGE_MODIFIER: int = 1210  # overloadDamageModifier（%；过载伤害加成）
ATTR_HEAT_DAMAGE: int = 1211             # heatDamage —— 每循环热损
ATTR_CHARGE_SIZE: int = 128              # chargeSize（弹药尺寸档）
ATTR_CHARGE_GROUP_1: int = 604           # chargeGroup1（可装弹药组 1）
ATTR_CHARGE_GROUP_2: int = 605           # chargeGroup2（可装弹药组 2）
ATTR_AOE_VELOCITY: int = 653             # aoeVelocity（m/s；导弹应用，字段规格 §1 未建模）
ATTR_AOE_CLOUD_SIZE: int = 654           # aoeCloudSize（m；导弹爆炸半径）
ATTR_AOE_FALLOFF: int = 655              # aoeFalloff（m；esi 无 unit，推定同 aoeCloudSize）
ATTR_EXPLOSION_DELAY: int = 281          # explosionDelay —— 导弹最大飞行时间（**存储毫秒**；SDE displayName = `Maximum Flight Time`）

# 弹药四系伤害 + 射程乘子（读弹药物件，不读武器）
ATTR_EM_DAMAGE: int = 114                # emDamage
ATTR_EXPLOSIVE_DAMAGE: int = 116         # explosiveDamage
ATTR_KINETIC_DAMAGE: int = 117           # kineticDamage
ATTR_THERMAL_DAMAGE: int = 118           # thermalDamage
ATTR_WEAPON_RANGE_MULTIPLIER: int = 120  # weaponRangeMultiplier（×；弹药对射程的影响）

# =============================================================================
# 五、过载热（过载可持续时长的全部原料，PROGRESS §3 硬结论 12）  来源：esi
# =============================================================================
ATTR_HEAT_CAPACITY_HI: int = 1178        # heatCapacityHi
ATTR_HEAT_CAPACITY_MED: int = 1199       # heatCapacityMed
ATTR_HEAT_CAPACITY_LOW: int = 1200       # heatCapacityLow
ATTR_HEAT_DISSIPATION_RATE_HI: int = 1179   # heatDissipationRateHi
ATTR_HEAT_DISSIPATION_RATE_MED: int = 1196  # heatDissipationRateMed
ATTR_HEAT_DISSIPATION_RATE_LOW: int = 1198  # heatDissipationRateLow
ATTR_HEAT_ATTENUATION_HI: int = 1259     # heatAttenuationHi —— 多模块热分摊
ATTR_HEAT_ATTENUATION_MED: int = 1261    # heatAttenuationMed
ATTR_HEAT_ATTENUATION_LOW: int = 1262    # heatAttenuationLow
ATTR_HEAT_GENERATION_MULTIPLIER: int = 1224  # heatGenerationMultiplier

# =============================================================================
# 六、被扰抗性（对算接口输入，字段规格 §10）  来源：esi
# =============================================================================
ATTR_ENERGY_WARFARE_RESISTANCE: int = 2045   # energyWarfareResistance —— 电容战抵抗
ATTR_SENSOR_DAMPENER_RESISTANCE: int = 2112  # sensorDampenerResistance —— 感应抑阻抵抗
ATTR_WEAPON_DISRUPTION_RESISTANCE: int = 2113  # weaponDisruptionResistance —— 武器扰断抵抗
ATTR_STASIS_WEBIFIER_RESISTANCE: int = 2115    # stasisWebifierResistance —— 停滞缠绕抵抗

# =============================================================================
# 七、模式（stance，T3D/T3C；字段规格 §10）  来源：esi
#     PostDiv 语义 = "共振率 ÷ 该值"，已由引擎结果反证（字段规格 §10.1）。
# =============================================================================
ATTR_MODE_EM_RESISTANCE_POST_DIV: int = 1997           # modeEmResistancePostDiv
ATTR_MODE_EXPLOSIVE_RESISTANCE_POST_DIV: int = 1998    # modeExplosiveResistancePostDiv
ATTR_MODE_THERMIC_RESISTANCE_POST_DIV: int = 1999      # modeThermicResistancePostDiv
ATTR_MODE_KINETIC_RESISTANCE_POST_DIV: int = 2000      # modeKineticResistancePostDiv
ATTR_MODE_ARMOR_REP_DURATION_POST_DIV: int = 2030      # modeArmorRepDurationPostDiv
ATTR_STANCE_SWITCH_TIME: int = 1985                    # stanceSwitchTime（存储毫秒！见缩放表）

# =============================================================================
# 八、技能要求                      来源：esi（**这里是最容易写错的一组**）
#     requiredSkill1..3 = 182/183/184，但 4..6 不是 185/186/187：
#     185 = attributePoints、186 = warpCapacitorNeedMultiplier。
#     正确值：4→1285、5→1289、6→1290；
#     等级同理：1..3→277/278/279，4→1286、5→1287、6→1288（编号不连号）。
# =============================================================================
ATTR_REQUIRED_SKILL_1: int = 182          # requiredSkill1（"Primary Skill required"）
ATTR_REQUIRED_SKILL_2: int = 183          # requiredSkill2（"Secondary Skill required"）
ATTR_REQUIRED_SKILL_3: int = 184          # requiredSkill3（"Tertiary Skill required"）
ATTR_REQUIRED_SKILL_4: int = 1285         # requiredSkill4（"Quaternary Skill required"）
ATTR_REQUIRED_SKILL_5: int = 1289         # requiredSkill5（"Quinary Skill required"）
ATTR_REQUIRED_SKILL_6: int = 1290         # requiredSkill6（"Senary Skill required"）
ATTR_REQUIRED_SKILL_1_LEVEL: int = 277    # requiredSkill1Level
ATTR_REQUIRED_SKILL_2_LEVEL: int = 278    # requiredSkill2Level
ATTR_REQUIRED_SKILL_3_LEVEL: int = 279    # requiredSkill3Level
ATTR_REQUIRED_SKILL_4_LEVEL: int = 1286   # requiredSkill4Level
ATTR_REQUIRED_SKILL_5_LEVEL: int = 1287   # requiredSkill5Level
ATTR_REQUIRED_SKILL_6_LEVEL: int = 1288   # requiredSkill6Level

# =============================================================================
# 九、引擎派生负 ID（dogma pass 4 输出；**不能从 ESI 反查名字**）
#     来源：`task` 命名 + `spike/smoke3.py` §E / `spike/survival.py` / `spike/mobility.py`
#     实测确认"确实有值"，括号内为该脚本实测值（SDE 3607705 / 引擎 13.6.0）。
# =============================================================================
ATTR_ALIGN_TIME: int = -1                 # alignTime（s；mobility.py 实测 11.6033）
ATTR_CAP_PEAK_RECHARGE: int = -2          # capPeakRecharge（GJ/s；smoke3 §E 实测 5.0）
ATTR_DERIVED_CYCLE_TIME: int = -3         # 派生周期（**存储毫秒**，物理单位 = 秒；T-10 实测装备级 4800 = 4.8 s，与 51 同口径）
ATTR_CAP_PEAK_LOAD: int = -4              # capPeakLoad（GJ/s；smoke3 §D 实测 23.231）
ATTR_CAP_DELTA_PEAK: int = -5             # 峰值处净差（GJ/s；实测 -18.231 = -4 减 -2）
ATTR_CAP_DELTA_PEAK_PCT: int = -6         # 净差百分比（%；实测 -364.618 = -5/-2×100）
ATTR_CAP_DEPLETES_IN: int = -7            # capDepletesIn（s；smoke3 实测 25.0）
ATTR_CPU_FREE: int = -9                   # cpuFree（tf；smoke3 实测 5.0）
ATTR_POWER_FREE: int = -10                # powerFree（MW；smoke3 实测 -4.5）
ATTR_DAMAGE_ALPHA: int = -11              # damageAlpha —— 首轮齐射（hp；smoke3 §B）
ATTR_DAMAGE_DPS: int = -12                # damageDps（hp/s；smoke3 §B）
ATTR_DAMAGE_DPS_WITH_RELOAD: int = -13    # damageDpsWithReload（hp/s；含装填）
ATTR_DRONE_DPS: int = -14                 # droneDps（hp/s；实测 5×Hobgoblin II = 48.0）
ATTR_DAMAGE_VOLLEY: int = -21             # damageVolley —— **装备级** volley（hp）
ATTR_ARMOR_EFFECTIVE_RESONANCE: int = -25   # 装甲有效共振（×；survival.py 反证 1/-25）
ATTR_HULL_EFFECTIVE_RESONANCE: int = -26    # 结构有效共振（×）
ATTR_SHIELD_EFFECTIVE_RESONANCE: int = -27  # 护盾有效共振（×；survival.py 反证 1/-27）
ATTR_EHP_ARMOR: int = -28                 # 分层 EHP：装甲（hp；实测 1861.083）
ATTR_EHP_HULL: int = -29                  # 分层 EHP：结构（hp；实测 6073.149）
ATTR_EHP_SHIELD: int = -30                # 分层 EHP：护盾（hp；实测 1238.095）
ATTR_EHP: int = -43                       # 混合 EHP（hp；= -28+-29+-30，实测 9172.328）
ATTR_VELOCITY_BOOST: int = -44            # velocityBoost（%；mobility.py 实测 488.71）
ATTR_ARMOR_REPAIR_RATE: int = -45         # 装甲修复 raw（hp/s）
ATTR_HULL_REPAIR_RATE: int = -46          # 结构修复 raw（hp/s）
ATTR_SHIELD_BOOST_RATE: int = -47         # 护盾回充/盾修 raw（hp/s）
ATTR_ARMOR_EFFECTIVE_REPAIR_RATE: int = -48  # 装甲有效修复（hp/s）
ATTR_HULL_EFFECTIVE_REPAIR_RATE: int = -49   # 结构有效修复（hp/s）
ATTR_SHIELD_EFFECTIVE_BOOST_RATE: int = -50  # 护盾有效回充（hp/s）
ATTR_PASSIVE_SHIELD_RECHARGE: int = -51   # 被动盾回充 raw（hp/s；实测 2.031）
ATTR_PASSIVE_SHIELD_EFFECTIVE_RECHARGE: int = -52  # 被动盾有效回充（hp/s；实测 3.869）
ATTR_FIGHTER_DPS: int = -58               # fighterDps（hp/s；T-06 实测：Nidhoggur + 6×Templar II = 519.5687，含技能档与舰船加成；舰船级 = Σ 架数 × 逐机 -12）
ATTR_CALIBRATION_FREE: int = -69          # 标定空闲（实测 175 = 400-225）
ATTR_CAP_STABLE_PCT: int = -72            # capStablePct（%；smoke3 实测 0.0）

# =============================================================================
# 九·附、战斗机族（此前只登记了 `-58`；本组由 T-06 实测、T-07 补登）
#
#     来源：
#     - `spec`  `PROGRESS.md` §3 第 38–42 条（T-06 在真航母 / 超级航母上的实测层次）
#     - `spike` 引擎本体 `esf_dogma_engine/_esf_dogma_engine.pyd` 的符号表里逐个核对
#               到的名字（正 ID 另与 SDE 索引库 `Index.attribute()` 对拍，见测试）
#
#     层次（PROGRESS §3 第 38 条，别把两层混用）：
#       装备级 `-55` / `-56` 是**逐架**能力 DPS，`-12 = -55 + -56`（缺失的能力项按 0）；
#       舰船级 `-58 = Σ 架数 × 逐机 -12`（含技能档与舰船加成）。
#       实测 Templar II：`-55 = 52.488`、`-56 = 34.107` → 逐机 86.595 → ×6 = 519.5687。
#
#     船体给上限（2215–2219 正 ID），引擎回报占用（-57 / -59～-62 派生负 ID），
#     两者成对使用；`-62` 数的是**中队**（一条 FitItem = 一管），不是架数。
#     支援中队占槽位但不出 DPS（`-61 = 1` 而 `-58 = 0`），所以不能用 `-58` 判是否带机。
#
#     反查限制：负 ID 属引擎 pass 4 派生，SDE / ESI **查不到名字**（PROGRESS §3 第 1 条），
#     故其名字是"引擎侧语义名"，本表是唯一事实源。
# =============================================================================

# ---- 船体上限（正向 SDE 属性；名字已与 Index.attribute() 对拍，unit 均为 ESI 未给）----
ATTR_FIGHTER_SQUADRON_MAX_SIZE: int = 2215   # fighterSquadronMaxSize —— 一个中队的满编架数（轻/重战 6、支援 3；别写死）
ATTR_FIGHTER_TUBES: int = 2216               # fighterTubes —— 船体提供的战斗机发射管数
ATTR_LIGHT_FIGHTER_SLOTS: int = 2217         # fighterLightSlots —— 船体提供的轻型中队槽位上限
ATTR_SUPPORT_FIGHTER_SLOTS: int = 2218       # fighterSupportSlots —— 船体提供的支援中队槽位上限
ATTR_HEAVY_FIGHTER_SLOTS: int = 2219         # fighterHeavySlots —— 船体提供的重型中队槽位上限（普通航母缺失 = 0，验槽位要用超级航母）

# ---- 占用侧（引擎派生负 ID）----
ATTR_FIGHTER_ABILITY_ATTACK_DPS: int = -55   # fighterAbilityAttackMissileDamagePerSecond —— 逐架战斗机·攻击能力 DPS（Templar II 实测 52.488）
ATTR_FIGHTER_ABILITY_MISSILES_DPS: int = -56  # fighterAbilityMissilesDamagePerSecond —— 逐架战斗机·导弹能力 DPS（Templar II 实测 34.107；重战 Gungnir II 缺失 = 0）
ATTR_FIGHTER_BAY_USED: int = -57             # fighterCapacityLoad —— 机库占用 m3 = Σ 架数 × types.volume（6×Templar II = 6000）
ATTR_HEAVY_FIGHTER_SLOTS_USED: int = -59     # fighterHeavySlotsUsed —— 已占用的重型中队槽位
ATTR_LIGHT_FIGHTER_SLOTS_USED: int = -60     # fighterLightSlotsUsed —— 已占用的轻型中队槽位
ATTR_SUPPORT_FIGHTER_SLOTS_USED: int = -61   # fighterSupportSlotsUsed —— 已占用的支援中队槽位
ATTR_FIGHTER_TUBES_USED: int = -62           # fighterTubesUsed —— 已占用的发射管（**按中队计**：1 个中队 = 1 管，不按架数）

# =============================================================================
# 十、属性元数据表：attribute_id -> (SDE canon 名, 物理单位标签, ESI unit_id)
#     - 名字取 ESI `name` 原样（大小写、复数都不改），负 ID 用引擎侧语义名。
#     - 单位是**换算之后**的单位（秒 / 米 / hp / GJ ...）；`None` 表示 ESI 与
#       语义都给不出，宁可空着也不猜（红线 N4 同理）。
#     - unit_id 是 ESI 实测值，供 P0-1/P0-2 的 ingest 对拍；`None` 表示 ESI 未给。
#       派生负 ID 本没有 ESI unit_id，唯一例外是 `-57`：按同族 `fighterCapacity`
#       （2055，unit_id 9 = Volume）推定为 9，注释里标了「推定」，不是实测值。
# =============================================================================
ATTRIBUTES: dict[int, tuple[str, str | None, int | None]] = {
    # ---- 船体 / 结构 / 容量 / 机动 ----
    ATTR_HULL_HP: ("hp", "hp", 113),
    ATTR_SHIELD_CAPACITY: ("shieldCapacity", "hp", 113),
    ATTR_SHIELD_RECHARGE_RATE: ("shieldRechargeRate", "s", 101),
    ATTR_ARMOR_HP: ("armorHP", "hp", 113),
    ATTR_MASS: ("mass", "kg", 2),
    ATTR_AGILITY: ("agility", "x", 104),
    ATTR_MAX_VELOCITY: ("maxVelocity", "m/s", 11),
    ATTR_SIGNATURE_RADIUS: ("signatureRadius", "m", 1),
    ATTR_WARP_SPEED_MULTIPLIER: ("warpSpeedMultiplier", "x", 104),
    ATTR_CAPACITOR_RECHARGE_RATE: ("rechargeRate", "s", 101),
    ATTR_CAPACITOR_CAPACITY: ("capacitorCapacity", "GJ", 114),
    ATTR_CARGO_CAPACITY: ("capacity", "m3", 9),
    ATTR_VOLUME: ("volume", "m3", 9),
    ATTR_SCAN_RESOLUTION: ("scanResolution", "mm", 102),
    ATTR_MAX_TARGET_RANGE: ("maxTargetRange", "m", 1),
    ATTR_MAX_LOCKED_TARGETS: ("maxLockedTargets", "count", None),
    ATTR_SCAN_SPEED: ("scanSpeed", "s", 101),
    # ---- 资源与槽位 ----
    ATTR_POWER_OUTPUT: ("powerOutput", "MW", 107),
    ATTR_POWER_LOAD: ("powerLoad", "MW", 107),
    ATTR_CPU_OUTPUT: ("cpuOutput", "tf", 106),
    ATTR_CPU_LOAD: ("cpuLoad", "tf", 106),
    ATTR_CALIBRATION_OUTPUT: ("upgradeCapacity", "calibration", 120),
    ATTR_CALIBRATION_LOAD: ("upgradeLoad", "calibration", None),
    ATTR_HI_SLOTS: ("hiSlots", "count", 122),
    ATTR_MED_SLOTS: ("medSlots", "count", 122),
    ATTR_LOW_SLOTS: ("lowSlots", "count", 122),
    ATTR_RIG_SLOTS: ("rigSlots", "count", 122),
    ATTR_UPGRADE_SLOTS_LEFT: ("upgradeSlotsLeft", "count", 122),
    ATTR_MAX_SUBSYSTEMS: ("maxSubSystems", "count", None),
    ATTR_TURRET_SLOTS_LEFT: ("turretSlotsLeft", "count", 141),
    ATTR_LAUNCHER_SLOTS_LEFT: ("launcherSlotsLeft", "count", 141),
    ATTR_DRONE_BANDWIDTH: ("droneBandwidth", "Mbit/s", 128),
    ATTR_DRONE_BANDWIDTH_USED: ("droneBandwidthUsed", "Mbit/s", 128),
    ATTR_DRONE_CAPACITY: ("droneCapacity", "m3", 9),
    # ---- 真实抗性（共振率，正 ID）----
    ATTR_SHIELD_EM_RESONANCE: ("shieldEmDamageResonance", "x", 108),
    ATTR_SHIELD_EXPLOSIVE_RESONANCE: ("shieldExplosiveDamageResonance", "x", 108),
    ATTR_SHIELD_KINETIC_RESONANCE: ("shieldKineticDamageResonance", "x", 108),
    ATTR_SHIELD_THERMAL_RESONANCE: ("shieldThermalDamageResonance", "x", 108),
    ATTR_ARMOR_EM_RESONANCE: ("armorEmDamageResonance", "x", 108),
    ATTR_ARMOR_EXPLOSIVE_RESONANCE: ("armorExplosiveDamageResonance", "x", 108),
    ATTR_ARMOR_KINETIC_RESONANCE: ("armorKineticDamageResonance", "x", 108),
    ATTR_ARMOR_THERMAL_RESONANCE: ("armorThermalDamageResonance", "x", 108),
    ATTR_HULL_EM_RESONANCE: ("emDamageResonance", "x", 108),
    ATTR_HULL_EXPLOSIVE_RESONANCE: ("explosiveDamageResonance", "x", 108),
    ATTR_HULL_KINETIC_RESONANCE: ("kineticDamageResonance", "x", 108),
    ATTR_HULL_THERMAL_RESONANCE: ("thermalDamageResonance", "x", 108),
    # ---- 武器 / 弹药 ----
    ATTR_MAX_RANGE: ("maxRange", "m", 1),
    ATTR_FALLOFF: ("falloff", "m", 1),
    ATTR_TRACKING_SPEED: ("trackingSpeed", "rad/s", None),
    ATTR_OPTIMAL_SIG_RADIUS: ("optimalSigRadius", "m", 1),
    ATTR_DAMAGE_MULTIPLIER: ("damageMultiplier", "x", 104),
    ATTR_CYCLE_TIME: ("speed", "s", 101),
    ATTR_MODULE_DURATION: ("duration", "s", 101),
    ATTR_CHARGE_RATE: ("chargeRate", "count", None),
    ATTR_RELOAD_TIME: ("reloadTime", "s", 101),
    ATTR_OVERLOAD_DAMAGE_MODIFIER: ("overloadDamageModifier", "pct", 105),
    ATTR_HEAT_DAMAGE: ("heatDamage", "hp", 113),
    ATTR_CHARGE_SIZE: ("chargeSize", "size", 117),
    ATTR_CHARGE_GROUP_1: ("chargeGroup1", "group_id", 115),
    ATTR_CHARGE_GROUP_2: ("chargeGroup2", "group_id", 115),
    ATTR_AOE_VELOCITY: ("aoeVelocity", "m/s", 10),
    ATTR_AOE_CLOUD_SIZE: ("aoeCloudSize", "m", 1),
    ATTR_AOE_FALLOFF: ("aoeFalloff", "m", None),
    ATTR_EXPLOSION_DELAY: ("explosionDelay", "s", 101),
    ATTR_EM_DAMAGE: ("emDamage", "hp", 113),
    ATTR_EXPLOSIVE_DAMAGE: ("explosiveDamage", "hp", 113),
    ATTR_KINETIC_DAMAGE: ("kineticDamage", "hp", 113),
    ATTR_THERMAL_DAMAGE: ("thermalDamage", "hp", 113),
    ATTR_WEAPON_RANGE_MULTIPLIER: ("weaponRangeMultiplier", "x", 109),
    # ---- 过载热（ESI 全部未给 unit，无量纲量；宁空不猜）----
    ATTR_HEAT_CAPACITY_HI: ("heatCapacityHi", None, None),
    ATTR_HEAT_CAPACITY_MED: ("heatCapacityMed", None, None),
    ATTR_HEAT_CAPACITY_LOW: ("heatCapacityLow", None, None),
    ATTR_HEAT_DISSIPATION_RATE_HI: ("heatDissipationRateHi", None, None),
    ATTR_HEAT_DISSIPATION_RATE_MED: ("heatDissipationRateMed", None, None),
    ATTR_HEAT_DISSIPATION_RATE_LOW: ("heatDissipationRateLow", None, None),
    ATTR_HEAT_ATTENUATION_HI: ("heatAttenuationHi", None, None),
    ATTR_HEAT_ATTENUATION_MED: ("heatAttenuationMed", None, None),
    ATTR_HEAT_ATTENUATION_LOW: ("heatAttenuationLow", None, None),
    ATTR_HEAT_GENERATION_MULTIPLIER: ("heatGenerationMultiplier", None, None),
    # ---- 被扰抗性 ----
    ATTR_ENERGY_WARFARE_RESISTANCE: ("energyWarfareResistance", "x", 108),
    ATTR_SENSOR_DAMPENER_RESISTANCE: ("sensorDampenerResistance", "x", 108),
    ATTR_WEAPON_DISRUPTION_RESISTANCE: ("weaponDisruptionResistance", "x", 108),
    ATTR_STASIS_WEBIFIER_RESISTANCE: ("stasisWebifierResistance", "x", 108),
    # ---- 模式 ----
    ATTR_MODE_EM_RESISTANCE_POST_DIV: ("modeEmResistancePostDiv", None, None),
    ATTR_MODE_EXPLOSIVE_RESISTANCE_POST_DIV: ("modeExplosiveResistancePostDiv", None, None),
    ATTR_MODE_THERMIC_RESISTANCE_POST_DIV: ("modeThermicResistancePostDiv", None, None),
    ATTR_MODE_KINETIC_RESISTANCE_POST_DIV: ("modeKineticResistancePostDiv", None, None),
    ATTR_MODE_ARMOR_REP_DURATION_POST_DIV: ("modeArmorRepDurationPostDiv", None, None),
    ATTR_STANCE_SWITCH_TIME: ("stanceSwitchTime", "s", 101),
    # ---- 技能要求（ID 不连号，见第八节注释）----
    ATTR_REQUIRED_SKILL_1: ("requiredSkill1", "type_id", 116),
    ATTR_REQUIRED_SKILL_2: ("requiredSkill2", "type_id", 116),
    ATTR_REQUIRED_SKILL_3: ("requiredSkill3", "type_id", 116),
    ATTR_REQUIRED_SKILL_4: ("requiredSkill4", "type_id", 116),
    ATTR_REQUIRED_SKILL_5: ("requiredSkill5", "type_id", 116),
    ATTR_REQUIRED_SKILL_6: ("requiredSkill6", "type_id", 116),
    ATTR_REQUIRED_SKILL_1_LEVEL: ("requiredSkill1Level", "level", None),
    ATTR_REQUIRED_SKILL_2_LEVEL: ("requiredSkill2Level", "level", None),
    ATTR_REQUIRED_SKILL_3_LEVEL: ("requiredSkill3Level", "level", None),
    ATTR_REQUIRED_SKILL_4_LEVEL: ("requiredSkill4Level", "level", None),
    ATTR_REQUIRED_SKILL_5_LEVEL: ("requiredSkill5Level", "level", None),
    ATTR_REQUIRED_SKILL_6_LEVEL: ("requiredSkill6Level", "level", None),
    # ---- 引擎派生负 ID ----
    ATTR_ALIGN_TIME: ("alignTime", "s", None),
    ATTR_CAP_PEAK_RECHARGE: ("capPeakRecharge", "GJ/s", None),
    ATTR_DERIVED_CYCLE_TIME: ("derivedCycleTime", "s", None),  # 第三列是**物理**单位：存储毫秒 ÷1000（见缩放表）
    ATTR_CAP_PEAK_LOAD: ("capPeakLoad", "GJ/s", None),
    ATTR_CAP_DELTA_PEAK: ("capDeltaPeak", "GJ/s", None),
    ATTR_CAP_DELTA_PEAK_PCT: ("capDeltaPeakPct", "pct", None),
    ATTR_CAP_DEPLETES_IN: ("capDepletesIn", "s", None),
    ATTR_CPU_FREE: ("cpuFree", "tf", None),
    ATTR_POWER_FREE: ("powerFree", "MW", None),
    ATTR_DAMAGE_ALPHA: ("damageAlpha", "hp", None),
    ATTR_DAMAGE_DPS: ("damageDps", "hp/s", None),
    ATTR_DAMAGE_DPS_WITH_RELOAD: ("damageDpsWithReload", "hp/s", None),
    ATTR_DRONE_DPS: ("droneDps", "hp/s", None),
    ATTR_DAMAGE_VOLLEY: ("damageVolley", "hp", None),
    ATTR_ARMOR_EFFECTIVE_RESONANCE: ("armorEffectiveResonance", "x", None),
    ATTR_HULL_EFFECTIVE_RESONANCE: ("hullEffectiveResonance", "x", None),
    ATTR_SHIELD_EFFECTIVE_RESONANCE: ("shieldEffectiveResonance", "x", None),
    ATTR_EHP_ARMOR: ("ehpArmor", "hp", None),
    ATTR_EHP_HULL: ("ehpHull", "hp", None),
    ATTR_EHP_SHIELD: ("ehpShield", "hp", None),
    ATTR_EHP: ("ehp", "hp", None),
    ATTR_VELOCITY_BOOST: ("velocityBoost", "pct", None),
    ATTR_ARMOR_REPAIR_RATE: ("armorRepairRate", "hp/s", None),
    ATTR_HULL_REPAIR_RATE: ("hullRepairRate", "hp/s", None),
    ATTR_SHIELD_BOOST_RATE: ("shieldBoostRate", "hp/s", None),
    ATTR_ARMOR_EFFECTIVE_REPAIR_RATE: ("armorEffectiveRepairRate", "hp/s", None),
    ATTR_HULL_EFFECTIVE_REPAIR_RATE: ("hullEffectiveRepairRate", "hp/s", None),
    ATTR_SHIELD_EFFECTIVE_BOOST_RATE: ("shieldEffectiveBoostRate", "hp/s", None),
    ATTR_PASSIVE_SHIELD_RECHARGE: ("passiveShieldRecharge", "hp/s", None),
    ATTR_PASSIVE_SHIELD_EFFECTIVE_RECHARGE: ("passiveShieldEffectiveRecharge", "hp/s", None),
    ATTR_FIGHTER_DPS: ("fighterDps", "hp/s", None),
    ATTR_CALIBRATION_FREE: ("calibrationFree", "calibration", None),
    ATTR_CAP_STABLE_PCT: ("capStablePct", "pct", None),
    # ---- 战斗机族（T-06 实测 / T-07 补登；正 ID 名字已与 Index.attribute() 对拍）----
    # 单位口径：DPS 类 = hp/s；机库占用 = m3（Volume，unit_id 9 为推定）；
    # 槽位 / 发射管 / 满编架数 = 件数（ESI 未给 unit_id，故留 None，不猜）。
    ATTR_FIGHTER_SQUADRON_MAX_SIZE: ("fighterSquadronMaxSize", "count", None),
    ATTR_FIGHTER_TUBES: ("fighterTubes", "count", None),
    ATTR_LIGHT_FIGHTER_SLOTS: ("fighterLightSlots", "count", None),
    ATTR_SUPPORT_FIGHTER_SLOTS: ("fighterSupportSlots", "count", None),
    ATTR_HEAVY_FIGHTER_SLOTS: ("fighterHeavySlots", "count", None),
    ATTR_FIGHTER_ABILITY_ATTACK_DPS: ("fighterAbilityAttackMissileDamagePerSecond", "hp/s", None),
    ATTR_FIGHTER_ABILITY_MISSILES_DPS: ("fighterAbilityMissilesDamagePerSecond", "hp/s", None),
    ATTR_FIGHTER_BAY_USED: ("fighterCapacityLoad", "m3", 9),
    ATTR_HEAVY_FIGHTER_SLOTS_USED: ("fighterHeavySlotsUsed", "count", None),
    ATTR_LIGHT_FIGHTER_SLOTS_USED: ("fighterLightSlotsUsed", "count", None),
    ATTR_SUPPORT_FIGHTER_SLOTS_USED: ("fighterSupportSlotsUsed", "count", None),
    ATTR_FIGHTER_TUBES_USED: ("fighterTubesUsed", "count", None),
}

# =============================================================================
# 十一、缩放表（**只登记非 1.0 的项**，其余一律 SCALE_ONE）
#
# 依据：字段规格 §0.3 + PROGRESS.md §3 硬结论 5，以及 2026-10-18 对引擎原始值
# 的实测（口径同 `spike/weapon_attrs.py`、`spike/smoke3.py`）：
#   51   speed             实测 2177.676 → 2.177676 s（2 s 激活周期）
#   73   duration          实测 4800     → 4.8 s（T-13：ANP 周期，与 `6 ÷ -4` 交叉自证）
#   -3   derivedCycleTime  实测 4800     → 4.8 s（T-10 硬结论 65：装备级派生周期与 73 同口径，**都是毫秒**）
#   1795 reloadTime        实测 5000     → 5 s
#   79   scanSpeed         实测 5000     → 5 s
#   479  shieldRechargeRate 实测 800000  → 800 s
#   55   rechargeRate      实测 280000   → 280 s
#   1985 stanceSwitchTime  实测 10000    → 10 s（字段规格 §10 亦标注「（ms）」）
#   160  trackingSpeed     实测 391.6687 → 0.3916687 rad/s
#   620  optimalSigRadius  实测 40000    → 40 m
#   281  explosionDelay   **索引库核实**（T-23）：`attributes.unit_id = 101`
#                        （`dogmaUnits.name = Milliseconds`）→ 存储值是毫秒，×0.001 得秒；
#                        数值本身未在样本上复测（本样本没有导弹），故只登记语义、不改别的
# 前九项是"存储毫秒"，后两项是"存储值被放大 1000 倍"，语义不同故分开登记。
# `-3` 是**派生负 ID**（引擎 pass 4 输出，SDE/ESI 查不到名字与单位），它进缩放表
# 的唯一依据是实测（PROGRESS §3 第 65 条），不是 ESI unit_id。
# =============================================================================
SCALE_BY_ATTR: dict[int, float] = {
    ATTR_CYCLE_TIME: SCALE_MILLI,               # 51   —— spec §0.3 + spike/smoke3.py / weapon_attrs.py
    ATTR_MODULE_DURATION: SCALE_MILLI,          # 73   —— T-13 实测（ANP 4800 → 4.8 s）+ ESI unit_id 101
    ATTR_DERIVED_CYCLE_TIME: SCALE_MILLI,       # -3   —— T-10 实测（装备级 4800 → 4.8 s）
    ATTR_RELOAD_TIME: SCALE_MILLI,              # 1795 —— spec §0.3 + spike/weapon_attrs.py
    ATTR_SCAN_SPEED: SCALE_MILLI,               # 79   —— spec §0.3 + spike/mobility.py
    ATTR_SHIELD_RECHARGE_RATE: SCALE_MILLI,     # 479  —— spec §0.3（PROGRESS §3 硬结论 5）
    ATTR_CAPACITOR_RECHARGE_RATE: SCALE_MILLI,  # 55   —— spike/smoke3.py 实测 280000 → 280 s
    ATTR_STANCE_SWITCH_TIME: SCALE_MILLI,       # 1985 —— spec §10 标注「（ms）」+ ESI unit_id 101
    ATTR_EXPLOSION_DELAY: SCALE_MILLI,          # 281  —— T-23 索引库核实 unit_id 101（Milliseconds；显示名 Maximum Flight Time）
    ATTR_TRACKING_SPEED: SCALE_KILO,            # 160  —— spec §0.3 + spike/weapon_attrs.py（391.67）
    ATTR_OPTIMAL_SIG_RADIUS: SCALE_KILO,        # 620  —— spec §0.3 + spike/weapon_attrs.py（实测 40000）
}

# =============================================================================
# 十二、ESI dogmaUnits 的 unit_id → 单位标签
#
# 说明：SDE 的 `dogmaUnits` 名称无法从离线 SDE / ESI 直接取到（ESI 只回
# `unit_id` 数字），因此这里是**按同 unit_id 分组的实测语义**归纳出来的标签，
# 并与 ATTRIBUTES 第三列的 unit_id 交叉校验（`attribute_dict.audit()` 会报
# 不一致）。若将来 ingest 能从 SDE 拿到官方单位名，只需替换这张表。
# =============================================================================
UNIT_LABELS: dict[int, str] = {
    1: "m",            # signatureRadius / maxRange / falloff / optimalSigRadius
    2: "kg",           # mass
    9: "m3",           # capacity / volume / droneCapacity
    10: "m/s",         # aoeVelocity
    11: "m/s",         # maxVelocity
    101: "s",          # 存储毫秒：speed / reloadTime / scanSpeed / *rechargeRate / stanceSwitchTime
    102: "mm",         # scanResolution
    104: "x",          # agility / warpSpeedMultiplier / damageMultiplier
    105: "pct",        # overloadDamageModifier
    106: "tf",         # cpuOutput / cpuLoad
    107: "MW",         # powerOutput / powerLoad
    108: "x",          # 全部共振率 / 被扰抗性
    109: "x",          # weaponRangeMultiplier
    113: "hp",         # hp / shieldCapacity / armorHP / heatDamage / 弹药伤害
    114: "GJ",         # capacitorCapacity
    115: "group_id",   # chargeGroup1 / chargeGroup2
    116: "type_id",    # requiredSkill1..6
    117: "size",       # chargeSize
    120: "calibration",  # upgradeCapacity
    122: "count",      # *Slots / upgradeSlotsLeft
    128: "Mbit/s",     # droneBandwidth
    141: "count",      # turretSlotsLeft / launcherSlotsLeft
}

# ---- 派生视图（由 ATTRIBUTES 单向导出，不另存数据，避免两份真相）----
NAME_BY_ATTR: dict[int, str] = {aid: row[0] for aid, row in ATTRIBUTES.items()}
UNIT_BY_ATTR: dict[int, str | None] = {aid: row[1] for aid, row in ATTRIBUTES.items()}
UNIT_ID_BY_ATTR: dict[int, int | None] = {aid: row[2] for aid, row in ATTRIBUTES.items()}
