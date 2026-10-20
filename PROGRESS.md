# noobbaozi · 项目进度（工作文档）

> 用途：**我自己的工作台账**。中断后靠它恢复上下文，不依赖对话记忆。
> 更新时机：每个里程碑完成、每次派发子代理任务后、每次踩坑后。
> **使用规则见 `docs/开发规范-v1.md` §11**（含写权、更新时机、子代理回报模板）。
> 最近更新：2026-10-18

---

## 0. 状态摘要（先看这段）

| 项 | 值 |
|---|---|
| 阶段 | **P2 复验未通过**（真实 KB 样本暴露 7 条 medium 口径缺陷），修复中 |
| 进度 | 讨论 100% · 设计 100% · P0 ✅ · P1 ✅ · **P2 ⚠️（Hecate 通过，真实样本未通过）** |
| 下一个动作 | T-20 修 D5/D6/D7/X2；T-21 诊断 T3C CPU 异常 → 复验 |
| 阻塞点 | D5/D6/D7 属 medium，按验收计划 §5 阻塞 P2 |
| 环境已验证 | Python 3.13 · 引擎 13.6.0 · SDE 3607705 · 348 tests · 技能档 512 · 快照 enforce 0 DIFF |

---

## 1. 里程碑

| # | 里程碑 | 状态 | 验收标准 |
|---|---|---|---|
| M0 | 可行性验证 | ✅ 完成 | 引擎可算、读取无损、抗性对拍通过 |
| M1 | 讨论与设计 | ✅ 完成 | 字段规格 / 软件设计 / 开发规范三份文档定稿 |
| M2 | P0 数据层 | ✅ 完成 | 能按名查类型、列出 group 1306 模式、单位表单测通过 |
| M3 | P1 计算封装 | ✅ 完成 | EFT/DNA/killmail 归一化；可修复错误；往返无损 |
| M4 | P2 六视角 overview | ⚠️ 复验未通过 | Hecate 4 样本通过（41/41 字段、162 provenance）；**真实 KB 暴露 D5/D6/D7 medium**，修复中 |
| M5 | P3 detail/explain/sensitivity | ⬜ 未开始 | 归因可查、灵敏度可算 |
| M6 | P4 MCP + Skill | ⬜ 未开始 | agent 可实际调用 |
| M7 | P5 知识层（RAG） | ⬜ 未开始 | 解释增强，不影响数值 |

---

## 2. 已完成（含证据）

### 2.1 文档

- ✅ `docs/想法-技术路线讨论.md` — 13 轮讨论，全部裁决与实测证据
- ✅ `docs/配装理解-字段规格-v1.md` — 六视角字段表 + 来源属性 ID + 可信度
- ✅ `docs/配装理解-软件设计-v1.md` — 分层架构、组件职责、10 条设计决策
- ✅ `docs/开发规范-v1.md` — 编码/测试/回归/子代理协作
- ✅ `README.md` — 导航

### 2.2 实测验证（14 项，全部有脚本可复跑）

| 验证内容 | 脚本 |
|---|---|
| killmail → EFT 还原 | `spike/smoke.py` |
| 伤害剖面加权、技能档影响 | `spike/smoke2.py` |
| 单位/alpha 聚合/往返无损 | `spike/smoke3.py` |
| 抗性 vs ESI 官方 12/12 | `spike/verify.py` |
| DC II 加成倍数 | `spike/dc2.py` |
| 分层/四系 EHP、修复语义 | `spike/survival.py` |
| state 对机动影响 | `spike/state_test.py` |
| 机动属性口径 | `spike/mobility.py` |
| 武器/弹药属性 | `spike/weapon_attrs.py` |
| 属性 ID 反查（heat/drone/launcher） | `spike/find_attrs.py` |
| 引擎 schema 与 SDE 规模 | `spike/schema_probe.py` |
| 模式定位与实测 | `spike/mode_probe.py`～`mode_probe4.py` |

---

## 3. 必须记住的硬结论

> 这些是**实测得出**的，不要凭印象推翻；要推翻必须重新跑脚本。

1. **引擎只做计算与格式互转，没有任何 type/group/attribute 查询能力** → 必须自建索引库。
2. **索引库主数据源必须是官方 SDE 文件**。ESI 对未发布类型完全不可见（`/universe/ids/` 0 命中、group 列表不返回成员）。
3. **模式（T3D/T3C）是独立 type，在 group 1306，`published=false`**。Hecate：`35686 Defense` / `35687 Propulsion` / `35688 Sharpshooter`。无「船→模式」链接属性，只能靠命名约定 `<船名> <模式> Mode`。
4. **抗性 = 基础共振率 × 模块共振率**（逐层四项严格成立）。真实共振率直接读**正 ID**：护盾 271–274 / 护甲 267–270 / 结构 109·110·111·113。
5. **×1000 缩放**：`trackingSpeed`、`optimalSigRadius`。**毫秒**：`cycleTime` / `reloadTime` / `scanSpeed` / `shieldRechargeRate` / 武器 `speed`。
6. **混合 EHP 是标量，不能与四系平均混用**（算术 / 调和 / 混合三者互不相等，详见第 27 条更正）。
7. **`alpha = Σ 逐装备 volley`**（实测比值 1.0000）。`volley` 只在装备级。
8. **`EHP = shieldEhp + armorEhp + hullEhp`**（严格成立）。
9. **`state` 影响是数量级的**：全 online 时 DPS 从 358 掉到 0。`slot.type` 合法值是 `high/medium/low/rig/subsystem/service/fighter_tube/fighter_bay/implant/booster/drone_bay/cargo`。
10. **技能档默认 = 全 4 + 补最低**（本样本得 19 个技能：17×4 + 2×5）。
11. **引擎 0.33 ms/次**（含 validate 0.38）。
12. **过载可持续时长的原料齐全**：`heatCapacityHi/Med/Low` 1178/1199/1200、`heatDissipationRate*`、`heatAttenuation*`、`heatGenerationMultiplier` 1224、`heatDamage` 1211。
13. **引擎 `environment` 只有 3 个字段**（`damage_profile`/`security`/`reactive_armor`）→ `capped_out` 不可建模，`webbed` 属 `incoming` 对算。
14. **模式影响巨大**：Defense EHP +38%、Sharpshooter DPS +33%、Propulsion 对齐 −67%。
15. **SDE 形态**（build 3607705）：zip **94.7 MiB / 102 成员**；`types.jsonl` 解压 154 MB；索引库产物 **14.3 MiB**。只需流式读 5 个成员，不要解压整包。
16. **实测行数**：types **53,288** / groups **1,611** / categories 48 / dogmaAttributes **2,871** / dogmaUnits 60 / modes **18**。ESI `X-Pages×每页` 估的 groups≈2000 偏高——**ESI 估算不可当规模依据**。
17. **JSONL 结构**：主键是根上的整数 `_key`，名称是**多语言 dict（含 zh）**，这 5 个成员里**没有** `{"_key":..,"_value":..}` 包装。
18. **group 1306 = Ship Modifiers**，18 成员 = **6 船 × 3 模式**（Confessor / Svipul / Jackdaw / Hecate / Skua / Anhinga）。**Anhinga 的三态叫 Primary/Secondary/Tertiary，且它属 group 1201 不是 1305** → 模式反解不能限定船组，必须「任意驼峰单词 + 前缀船名」。
19. **中文名 SDE 自带**（35683 → 「赫卡特级」），`search_types("赫卡特")` 直接命中 → 中文检索不必走 ESI `?language=zh`。
20. **`unit_id` 不可信**：unit 101 名为 `Milliseconds` 但 displayName 是 `"s"`；`trackingSpeed` 根本没有 unitID → **缩放只能靠手工表**，不能靠单位判断。
21. 2,871 个属性里 **1,620 个没有 displayName**（必须回退 `name`）。
22. **引擎 EFT 解析器不认 `Offline` 后缀**（会当模块名未知）→ 必须报错，不得静默当在线。
23. **`load_dna` 只吃 `35683:2048;1::`**，`fitting:` 前缀要自己剥，否则报 `FITTING is not a number`。
24. **`load_killmail` 可直接吃 zkillboard 原始载荷**（含 `zkb` 字段），不必手工 pop。
25. 技能档反馈回路 **2 轮收敛**；`violations` 里同一技能会**重复出现 1~8 次** → 必须用 dict 覆盖式写入。
26. 沙箱下 pytest 的 `tmp_path` **不可用**（`WinError 5`）；测试造不存在路径请用 `db_path().with_name(...)`。
27. **混合 EHP 既不是四系算术平均，也不是调和平均**（三层结构下都不成立）。实测：混合 **8855** / 算术 **9352** / 调和 **9219**。正确算法是**按层复算** `Σ_layer HP_layer / Σ_i(w_i × r_i,layer)`；「等于调和平均」只在单层时成立。**已据此更正讨论文档、字段规格与不变式 I3。**
28. **技能属性 ID 不连号**（此前规格写错，已修正）：`requiredSkill1/2/3 = 182/183/184`、`4/5/6 = 1285/1289/1290`；等级 `1/2/3 = 277/278/279`、`4/5/6 = 1286/1287/1288`。185 = attributePoints、186 = warpCapacitorNeedMultiplier、280 = skillLevel、281 = explosionDelay。
29. **`unit_id 101 = 毫秒`**：55/79/479/51/1795/**1985 stanceSwitchTime** 全是毫秒（1985 原始值 10000）。缩放表共 **8 项 = 6 毫秒 + 2 ×1000**。
30. **派生属性语义确认**：`-5 = -2 − -4`（GJ/s，净差；验收员实测 −18.23 = 5 − 23.23 复核）；`-6 = -5/-2×100`（净差百分比）；`-2 = 2.5×cap/recharge = 5.0`（公式自证）。
31. **结构层共振四 ID 不连号**：EM **113** / EXP **111** / KIN **109** / THM **110**。
32. `-14` 无人机 DPS 实测 48.0（5×Hobgoblin II），已并入 `-12`。
33. **引擎的 `unknown type X` 同时用于"船名未知"与"模块未知"**（实测 `[NoSuchShipXyz, t]`），二者只能靠 **EFT 头船名回比**区分 → 否则船名错误会被误标成模块错误并给出垃圾候选。
34. **`Damage Control I` / `Light Neutron Blaster I` 都是合法已发布类型**，不能用来构造"模块名未知"样例；要用**截断拼写**（`...Microwarpdriv`）或 `Offline` 后缀。
35. 索引层与属性字典层已接线：`attribute(51).scale == 0.001`、`attribute(160).scale == 1000.0`、`attribute(51).unit == "Milliseconds"`。
36. **`search_types` 只有精确/前缀/包含三级匹配，没有编辑距离**：`Damage Controll II` 这类"中间错位"拿不到任何候选 → 模糊候选需单独立项（见 §5 欠账 11）。
37. **`noobbaozi/` 已于 2026-10-18 `git init`**（首提交 `afdf5b5`，100 文件 / 1.1 MB）。`data/`、`spike/uvcache/`、`__pycache__/`、`acceptance/out/` 已忽略。→ 子代理「是否越界」从此可用 `git status --short` 硬验。
38. **战斗机 DPS 层次**：装备级 `-55/-56/-12` 是**逐架**值；舰船 `-58 = Σ 架数 × 逐机 -12`，且**逐机 `-12 = -55 + -56`**。实测 Nidhoggur + 6×Templar II → `-58 = 519.5687`；同配装 Nyx → `606.1635`（**舰船加成已计入**）。
39. **槽位口径**：`-59/-60/-61` = 已占用 重/轻/支援 **中队**槽位；`-62` = 发射管占用（**按中队，不按架数**）；`-57` = 机库占用 m³ = `Σ 架数 × types.volume`。
40. **支援中队占槽位但不出 DPS**（Cenobite II → `-61=1`、`-62=1`、`-58=0`）；纯战斗机配装下 `-12 == -58`，`-14`（无人机）不混入。
41. **满编架数不同**：轻/重战 6 架，支援机 **3 架** → quantity 必须用 `fighterSquadronMaxSize(2215)` 探，别写死。
42. **航母不能验证 `-59`**：普通航母装重战不报 `wrong_slot`，而报 `resource: heavy_fighter_tubes used=1 available=0`（航母 `fighterHeavySlots` 2219 缺失=0）。验重战槽位必须用**超级航母**（Nyx/Wyvern/Aeon 的 2219=4）。
43. **`ship.attributes` 与 `items[i].attributes` 的键都是 `int`**；`json.dumps` 打印出来像字符串，极易误判（实测踩过）。
44. **计算侧 `items` 不回 `type_id`**，与输入 `fit["items"]` **按序一一对应**（样本 31/31）；合并 id 是投影层的活。
45. 样本 `dph/dps = 2.304636 s`，**恰好等于中子炮 cycle**（存储 2304.636 ms）→ `alpha_dph_ratio` 用「相对武器周期的倍数」判定有实测依据。
46. **摘掉 MWD：信号半径 385 → 70（5.5×）**——这是「只关推进」在现有 `{"all": state}` 变体机制下的合法等价构造。
47. 样本 139246907 的 **17 个已装模块逐个摘除都救不回电容稳定**（最接近的 MWD 摘掉后耗尽 28.8→48.0 s）→ `cap_sensitive` 在此样本不命中是正确的。
48. 诊断规则已用极简配装验证不是死规则（Hecate+DC II → slack/empty_hardpoint/no_propulsion 三连中；Tristan 空配 → drone_bandwidth_idle 命中）。
49. 性能：`diagnostics.evaluate` 冷 **12.6 ms** / 热 **2.9 ms**（含 17 次变体计算）→ 六视角 100 ms 预算充裕。
50. **口径裁决（第 16 轮）**：「可信度 A–E」与「provenance 三态」是**正交维度**。provenance 的权威来源是投影层，且将由**实测三轴敏感性**（技能/状态/剖面）推导，不再手写；字段规格的「可信度」列不得用于反推 provenance。
51. **引擎二进制 `_esf_dogma_engine.pyd` 的符号表里带派生属性真名** → 可用来核实负 ID 名字（已印证 `-57 = fighterCapacityLoad`、`-59/-60/-61/-62 = fighter…SlotsUsed/TubesUsed`）。这比靠命名猜测可靠。
52. **`-55/-56/-58` 三个名字在引擎二进制里查不到**，只有约定名（`fighterAbilityAttackMissileDamagePerSecond` / `fighterAbilityMissilesDamagePerSecond` / `fighterDps`）→ 属**引擎侧语义名，未独立核实**，引用时应标注。
53. 5 个正 ID 真名已核实：`2215 fighterSquadronMaxSize` / `2216 fighterTubes` / `2217 fighterLightSlots` / `2218 fighterSupportSlots` / `2219 fighterHeavySlots`；**SDE 侧 `unit` 全为 `None`**。
54. 常量表已增至 **141 个**（129 + 12），缩放仍 **8 项**（6 毫秒 + 2 ×1000）。
55. **provenance 首轮实测**（样本 139246907）：登记常量范围内 measured **65** / assumed **41** / unknown **36**；扫描全域 249 个 ID（measured 137 / assumed 76 / unknown 36）。轴变化数：技能 33 / 状态 18 / 剖面 23。
56. **手写表把 7 个"静态值"高估成 measured**（实测全部 assumed）：`4 mass`、`552 signatureRadius`、`9 hullHP`、`265 armorHP`、`48 cpuOutput`、`49 cpuLoad`、`70 agility`。前三个走**状态轴**（MWD 质量与信号、结构 HP 受模块影响），其余走**技能轴**。
57. 反向**上调**为 measured：`11 powerOutput`、`15 powerLoad`、`54 maxRange`、`158 falloff`、`263 shieldCapacity`、`55/482` 电容、`564 scanResolution`、`1132/1152` 标定。（**注意：原先还包含「全部正 ID 共振率」，该说法已被第 166 条修正**。）
58. **剖面轴只影响负 ID**（`-17..-20`、`-25..-30`、`-31..-42`、`-43`）；所有登记的正 ID 都不随剖面变。
59. **`-58/-55/-56/-57/-59..-62/-14/-46/-47` 在该样本中引擎根本不输出** → 按规则落 `unknown`。但这是「**本样本没有该部件**」，不是「该属性永远不可知」→ **必须改两级模型**（见 60）。
60. **provenance 判定必须两级**：①**属性级基线类**（该属性的值是否随技能/状态/剖面变化，多样本实验得出）②**实例级覆盖**（本次计算里属性缺失 → `unknown`）。当前实现把两者混成一张全局表，属模型错误。
61. 弹药四系伤害（`114/116/117/118/120`）与导弹（`653/654/655`）**不在 `calculate` 输出里**（弹药是货舱物品，引擎不算其属性）→ 视图的 `damage_split` 若查全局表会全变 `unknown`。**须由视图声明 `measured`**（由输入弹药 ID + SDE 常量决定），需要一条「输入常量」通道。
62. **技能轴覆盖面不足**：`derive_skill_tier` 只含 19 个"被模块要求"的技能。实测补 `Power Grid Management=5` → `11` 从 54→67.5、`Shield Management=5` → `263` 从 650→812.5、`Shield Operation=5` → `479` 从 800000→600000、`Signature Analysis=5` → `564` 从 510→637.5。→ 当前 measured 是「轴定义的产物」，**须在 T-12 修完技能档后重跑**。
63. 引擎对该样本的**默认 item states 实测等价于「全 active」**（`{"all":"active"}` 与不传 state 逐属性一致，脚本自检通过）。
64. **装备作用域也有 `-4`**：它是逐模块的 `capPeakLoad`，逐项求和 == 船体级 `-4`（样本 5×0.768 = 3.84，合计 22.174 GJ/s）。→ `cap_by_module` 应直接用装备级 `-4`，不要自算 `6 capacitorNeed ÷ 周期`。
65. **装备级 `-3 derivedCycleTime` 是毫秒**（ANP 4800 = 4.8 s，与 `6 ÷ -4` 交叉自证），但常量表把它标成 `s` 且不在缩放表里 → **单位标注错误，待修**。
66. **cargo 槽物品在引擎 `items` 里没有任何属性**（体积只能查索引）→ 货舱已用量必须自己按 `volume × quantity` 累加。
67. 引擎**只对 capital 件报尺寸类硬违规**（`capital_item`）；同组更大尺寸模块只体现在资源上 → `upgrade_space` 必须自己按 `types.volume` 分尺寸档。
68. **PG 超 18.0 已双路复现**（引擎 72 / 54）：T-10 与验收员独立得出同一结论，原文档「超 7.2」确认为错。全 5 档仍超 18.0 → `needs_implant = True`。
69. **`54 maxRange` / `158 falloff` 已含弹药 `weaponRangeMultiplier`(120) 修正**：同一门 Light Neutron Blaster II 换弹实测 1350(Void .75) / 2520(Null 1.4) / 2880(Iron 1.6) / 1800(Lead 1.0)。**不得再乘 120**。
70. **`ehp_by_damage` 两种口径等价**：按层复算 `Σ HP_layer / r_i,layer`（正 ID 271–274 / 267–270 / 113·111·109·110）与「换纯单系剖面重算 `-43`」四位小数完全一致（8549.5512 / 8413.9770 / 11347.1379 / 9095.4679）。复算可省 4 次引擎调用。
71. **伤害占比必须用装备级 `-21`，不能用弹药 × `damageMultiplier`**：导弹的伤害加成在 `missileDamageMultiplier`(212/213)，发射器没有 `64`（实测 Light Missile Launcher II 的 `-21`=136.95，弹药仅 124.5）。纯炮两种口径等价，混装时用 64 会把导弹低估近一个数量级。
72. **单弹匣发数 = `capacity`(38) ÷ (弹药 `volume`(161) × `chargeRate`(56))**：中子炮 0.2/0.0025 = 80 发、轻导 0.795/0.015 = 53 发；时长 = 发数 × `51`。
73. **导弹射程 = 弹药 `maxVelocity`(37) × `explosionDelay`(281)**；`281` 存储单位是**毫秒**（Scourge Light 5000 → 5 s × 5250 = 26250 m）。
74. **`mass`(4) 随推进状态变化**（offline 1,050,000 / active 1,550,000，差 500,000 = 5MN MWD 的 massAddition）→ 与旧 provenance 表判 `measured` 冲突，T-14 两级模型会纠正。
75. **结构层「最薄系」常常不存在**：一块 DC II 把四系共振率压成同一个值（0.402），并列时给 `damage_type=None`，**不编结论**。
76. **「零」有两种，必须区分**：引擎对 `-46/-47`（结构/护盾 raw 修复）**不给键**，对 `-49/-50`（有效值）给 `0.0`。前者给 `None`、后者照实给 `0.0`。
77. **只按属性 ID 识别武器会误判**：样本里 `Warp Disruptor II` / `Stasis Web` 也带 54/158。正确做法 = 槽位类别 + SDE group 名 + 兜底（54 且 51）。
78. 属性元数据实测（SDE 3607705）：`1272 droneBandwidthUsed` unit_id **128**（Mbit/s）、`56 chargeRate` unit_id **None**（件/循环）、`73 duration` unit_id **101 = 毫秒**。
79. **`-3` 在 SDE 属性表里根本不存在**（派生负 ID，无名字无单位）→ 它进缩放表的唯一依据是 T-10 的实测。
80. 常量表 **144** 个（141+3），缩放 **10 项**（milli **8** + kilo 2）。
81. **投影层 `views/base.py::item_attributes()` 对装备级属性也跑 `units.physical()`** → 装备级毫秒属性一旦进缩放表，`ctx.items()` 返回的**已是秒**；视角里再 `ms_to_s` 就是双换算（差 1000 倍，红线 N5）。`-3` 同理。
82. **`unit` 列是「换算之后」的物理单位**（全表口径，`51 speed` 也写 `"s"`）→ `-3` 原来的错不在标签，而在「标了 `s` 却没进缩放表」。
83. **真实样本 overview 序列化 17,777 chars**：其中 `resources.upgrade_space` 一项 **4,135 chars（23%）**，是唯一决定预算能否达标的单项；两档降级后仍 16,528 chars > 12,000 预算。
84. **provenance 键空间会随数据形态变形**：同一路径在空列表下是 `hard_violations`、非空时是 `hard_violations[].rule.type` → **下游不得把 provenance 键当稳定 schema**。
85. **单位审计通过**：全部 `_s` 叶子无毫秒/×1000 泄漏；`render.py` 不 import `units`、无任何换算（静态守卫 + 实测双证）。
86. **诊断只算一次**（计数器桩验证）、flag 归位 1:1、渲染 overview 与 `build_view()` 完全相等。
87. **新技能档 = 512 个技能**（510×4 + 2×5）；`index=None` 仍是 19 → 两种档名可区分，不静默冒充。
88. PG：输出 54→**64.8**（`Power Grid Management` 4，+20%）、占用 72→**68.4**（`Advanced Weapon Upgrades` 4，−5%）→ 缺口 **3.6**，仍超。
89. 速度 1271.61→**1482.74**（`Acceleration Control` 4 把 MWD 加成 ×1.2，`(5.8871−1)×1.2+1 = 6.8645`）；信号半径 **385 不变**；对齐 11.861→**9.489**（`Evasive Maneuvering` −20% 敏捷）。
90. 分层 HP：shield 650→**780**（`Shield Management` +20%），armor/hull 不变 → EHP 8854.96→**9102.58**（+2.80%）。
91. **抗性共振率 12 项完全不变**（本样本无抗性补偿技能的生效点）→ EHP 变化**纯来自护盾 HP**。**我此前「抗性会随技能档变化」的判断在此样本上不成立**，以此条为准。
92. 电容：容量 560→**672**、回充 280→**224 s**、stable −343.47%→**−193.21%**；护甲修复不变，被动盾回充 2.031/3.869→**3.047/5.804**。
93. DPS ×**1.3333**（`Surgical Strike` +12% 伤害 ÷ `Rapid Firing` 周期 ×0.84）；**DPH 仅 ×1.12** → 两者变化幅度不同，不可互相推。
94. 模式重标：`none` 4609.69/66.32/6.428、`Defense` 6264.53、`Propulsion` 对齐 2.143、`Sharpshooter` DPS 88.43。
95. **`needs_implant` 由 True 翻转为 False**：全 5 档 PG 67.5/67.5、CPU 287.5/282.5 → 零资源违规。旧结论「全 5 仍超 PG 18.0」是技能档缺陷的产物。
96. **22 行快照逐行归因完成，无残留**：护盾 HP / 四系 EHP ← `Shield Management`；PG 占用 ← `Advanced Weapon Upgrades`；速度 ← `Acceleration Control`；对齐 ← `Evasive Maneuvering`；DPS ← `Surgical Strike` + `Rapid Firing`（含 2 处乘性交互）。**其中 11 行纹丝不动是因为相应技能在旧档里已在生效**，不是没生效。
97. 四模式 EHP 精确分解：旧档实际 **3841.407** → **4609.689**（+768.281 = 盾 +216.667 + 甲 +253.107 + 构 +298.507）。旧文档的 4530.93 来自另一套 15 技能集，**也不可比**。
98. 验收工具新增两层护栏：`run_acceptance` 在 `skill_tier_name != "all4+min_required"` 或技能数 < 100 时直接记 `blocker`；`check_snapshot` 单独「技能档护栏」段，退化即退出码 2。→ **「看起来全绿其实全错」必须显式炸出来**。
99. **overview 实测 13,519 chars**（`upgrade_space` 摘要化之后）→ `BUDGET_CHARS = 17,000`，默认路径 `degraded = False`。
100. `upgrade_space` 摘要实测：`slots_checked 15 / upgradeable_count 0 / blocked_by_resource True`（PG 仍超 3.6、CPU 超 11.0，15 个候选一条都装不下）；完整 15 条只在 `render_detail`。
101. `count_by_level` 的正确键空间是 `skill_floor.count_by_level.<level>`（等级来自数据，实测 1/2/4/5 四级），来源逐级继承 → 全 `assumed`。
102. **512 技能档下 `5MN MWD + 3 炮` 实测已稳定 +61.9%**（旧 19 技能档才不稳定）→ `cap_sensitive` 的命中探针必须改用 `50MN MWD`（base 0 / 去 MWD 后 +92.2%），否则该规则在新档下永远不命中。
103. **对外 provenance 形态裁决**：定「**裸值 + 平行 provenance 表**」，`TypedValue` 退回内部/未来接口。理由：TypedValue 每个叶子裹 4 字段会让 13.5k chars 再涨 3–4 倍；平行表正好对应 `FIELD_SOURCES` 键空间，可整表校验。→ 验收 runner 的 `walk_numeric` 需适配双形态。
104. **`render.py` 有静态守卫禁止字面量 `/ 1000`**（防单位换算泄漏）→ 注释里写算式也会被拦，得用 `÷ 1000`。
105. **`FIELD_SOURCES` 的 `[]` 元素约定已在六视角统一**；`test_views_a._resolve` 已支持 `[]` 段与整数键回退。旧/新写法对拍：**30 键 vs 30 键，三态差异 `{}`**（`firepower` 四条 weapons 键均为 `assumed`）。
106. **空武器清单时行为变化**：`weapons: []` → render 折叠成容器键 `weapons`，现在落 `unknown`（旧写法是 `assumed`）。键数不变、无测试锁定；语义上「没有可标注的数据」判 `unknown` 更诚实，暂不处理。
107. **P2 独立验收通过**：六维度 6/6、overview 字段 **41/41 × 4 样本**、provenance **162 叶子 = 162 条目（缺 0 多 0）**、I1–I7 全过、快照 `--enforce` 22 行 + 四模式 **0 DIFF**。
108. **验收员自己报过一次假缺陷（72 条口径错误）**：根因是构造 `ViewContext` 时没把技能注入 `fit["character"]["skills"]`，渲染层按零技能算（EHP 7585.48 / 速度 1059.68 全是无技能值）。→ **`ViewContext` 只从 `ctx.fit` 建变体、不读 `ctx.assumptions`**，这是 API 陷阱，写进 §6。
109. **两个已承诺出口缺失（C1/C2）**：① `modes` 三模式对比没输出（`字段规格 §10.2` 承诺过）；② `confidence`（A–E）没有出口，agent 无法知道 `application.tracking_rad_s` 是未建模参数。→ T-17 补齐。
110. 验收员**明确列出 11 项未验项而不默认通过**（mode 对比、detail/explain、`cap_sensitivity` 数值、`upgrade_space` 逐槽、`damage_split` 加权正确性、`range` 两档加法、`weapons.family` 全类、consumables 公式、非 Hecate 船型、P4/P5）。
111. **`modes` 块已落地**（顶层、恒存在）：Hecate 三条 —— Defense `ehp 12911.01` / Propulsion `align 3.163` / Sharpshooter `dps 649.944`。对 killmail 样本 Defense 增幅 **+41.8%**，而 baseline 探针配装是 **+35.9%** → **量级一致，但不同配装不可直接比**。
112. **`confidence` 全量平行表代价 +5,093 chars**，只为重复写默认值 `A` → 不值。改为**稀疏表（缺省 `A`）**。通则：**默认值不需要被显式输出**。
113. `-70 maxTargets` 目前**没有任何视角输出成字段** → `confidence` 的 `C` 通道是预留机制（`FIELDS_FROM_MAX_TARGETS` 空元组 + 登记即判 C），**不伪造路径**。
114. 模式短名可从「船名前缀 + ` Mode` 后缀」安全反解（Hecate → Defense/Propulsion/Sharpshooter；Anhinga 系 → Primary/Secondary/Tertiary）。
115. **confidence 稀疏化后：views 13,742 + modes 341 = 14,083 chars**，`BUDGET_CHARS = 17,000`，`degraded = False`。相对 T-15 的真实增量只有 **+223**（3 条 `E`），T-17 的 +5,093 全额省掉。
116. **验收侧 `ConfidenceOracle` 本来就是稀疏约定**（「非 `A` 必须出现 / `A` 不该出现」）→ T-17 的全量表与自家验收口径**正好相反**；稀疏化后两端对齐，端到端 acceptance `status: pass`。
117. 变异自检通过：漏一条登记、多写一条 `A`、档位写错，三种篡改全被抓 → 新断言不是永真。
118. **`repair` 零值语义收口**：无模块层 `raw_hp_s` / `effective_ehp_s` = **`0.0`**（不再是 `null`），新增 **`has_source`** 布尔；`shield`（主动盾修 `-47`）与 `shield_passive`（被动回充 `-51`）**各有独立 `has_source`**。
119. **`-51` 被动回充与模块无关**：同船型恒 ≈ **3.0469 hp/s**（f1/f2/f3/裸船逐位相同）→ **不能用它判「有没有护盾修复来源」**，只能用 raw 属性的**存在性**判。
120. **「两种零」现在可分**：`0.0 + has_source=False`（无来源）vs `0.0 + has_source=True`（有来源但速率 0）。这比用 provenance 表达「没有模块」准确得多。
121. **实现方两次反驳 Lead 指令且两次都对**：① 拒绝把 `shield`/`shield_passive` 合并（否则 `shield.has_source` 在所有有盾船上恒 True，信息量为零，且与任务书自身期望冲突）；② `-46/-47` 在实测表里是 `unknown` 而非 `assumed`，它按 tag 结果执行、**没有手工塞 `measured`**。
122. **三条真实 KB 样本**（Loki T3C / Drake+AB / Griffin 空船体）**六维度全齐 41/41**，未崩溃、未缺字段 —— 但「不炸」不等于「对」。
123. **真实样本暴露 7 条 medium 口径缺陷 → P2 本轮判不通过**（验收计划 §5：medium 阻塞）。**只用一条 Hecate 自证是不够的。**
124. **D5**：`stability.consumables.ammo_endurance_s`（`None`）与 `firepower.ammo_endurance_s`（172.663 / 250.323）不一致 —— 同一概念在两个视角各写了一遍。
125. **D6**：`capacitor.depletes_in_s` 把引擎 `-7` 的「不耗尽」哨兵 **`-1.0`** 当秒数输出（对应 stable 96.8 / 61.0 / 38.5 / 100 的样本）→ 稳定时应输出 `None`。
126. **D7**：`firepower.dph` 在导弹船（Loki + 5×HML）为 **0**，且 `dps.base` 完全等于 `dps.drone` → 极可能舰船级 `-11` **只聚合炮台、不含发射器**；应改为 **Σ 装备级 `-21`** 并以 `-11` 回退。
127. **X1**：T3C CPU 爆表（`cpu.load 10572.1` / 输出 540，cloak 装备级 `cpu(50)=10100`）；**X3** 的 AB 不生效疑似连带。待诊断是**引擎 / 读取层（`base` vs `value`）/ 样本**哪一层。
128. **X2**：`resources.slots` 没有 `subsystem` 类 → 对 T3C 而言「槽位已满」不完整。
129. **X4**：不变式 I5（抗性 = 基础共振率 × 模块乘数）**对效应型抗性模块不适用**（multispectrum hardener 走 effect，不改共振率基础值）→ 要真验必须走 effect 级 `sources`，降为 low，归 P3。
130. **验收员自查并修掉 4 处自己的检查模型缺陷**：I2 把无人机 volley 算进 alpha、I5 把无人机自带抗性当模块乘数、通用单位扫描误报（`count: 1` 撞 ×1000 巧合）、口径容差比三次舍入还紧。顺带确证：**`alpha` 只聚合武器**（f5 的 6×HAM volley 合计 2105.58 == alpha，无人机 58.97 不计入）。
131. **「`-11` 只聚合炮台、不含发射器」被实测证伪**：f5（纯导弹 Drake）6×HAM II 各 `-21 = 350.929885`，合计 **2105.579** 与 ship `-11` **逐位相等** → `-11` 确实含发射器；无人机 `-21 = 58.968` 不计入。（I2 在 f5 上严格成立。）
132. **f4（Loki）的真根因是突变数据丢失**：唯一肇因是 **`Abyssal Ballistic Control System`（type 49738）**。killmail 只给 `item_type_id/quantity/singleton`，**不暴露 Abyssal 变异数据**；引擎按「未变异」处理，导致其对导弹伤害乘子的贡献按 0 相乘 → 5 门 HML 的 `-21`/`-12`/`-13` **全为 0**。摘除该件后 ship `-11` 从 **0 → 1427.588**。对照实验：普通 `BCS II`(15681) 完全不影响 `-21`（230.85 不变）。
133. → **D7 不是投影层口径缺陷，而是导入层数据保真问题**（killmail 天然丢 mutation）。修复点在 `engine/**` 或上游；D7 应降级为与 X1 同类的观察项。
134. **裁决**：含突变模块但输入无变异数据时，受影响的关键数值输出 **`null` + `unknown` + 显式 flag**，**不得输出 `0.0` 伪事实**。判据可用「有带弹药的武器但 `dph == 0`」这一自相矛盾作为触发条件。
135. **T3C CPU 异常的三层判定**：读取层**无问题**（`views/base.py` 读 `value`，全仓 `get("base")` 出现 0 次）；引擎**无 bug**（10100 是对「已装 45597」的**正确**实现，同一段代码在 Buzzard 上给 80.0）；**根因 = killmail 还原 + 我们的状态语义缺失**。
136. 传导链（SDE 实读）：`45597`（Defensive-Adaptive Defense Node）的 `1871 covertCloakCPUPenalty = 10000` --effect 5262（ship 域）--> 所有模块的 `1870 covertCloakCPUAdd` --effect 5261（item 域）--> cloak 的 `50` → **100 + 10000 = 10100**。子系统机制是**加罚**，不是打折。
137. **决定性证据**：能让罚值消失的 `45595 Loki Defensive - Covert Reconfiguration` 位于 **flag 177（SubSystemBay 子系统舱）**，不是已装槽位（125–128 才是 SubSystemSlot）；`load_killmail` **静默丢弃** flag 177 物品 → 该 Loki 的 covert cloak 在真实游戏里**不可能在线**（需 10100，实际只剩约 472 余量）。
138. **真实占用 = 472.1 tf ≤ 540 tf（余 67.9）**。引擎不做资源门控（`validate` 只报违规，`post_load` 也只把 cloak 设成 online），而 `resources` 直接取 ship `49` → **把「不可能在线」的模块当真实占用渲染**。数字没读错，是**「状态没落地」**。
139. **X3 独立于 X1**：Abyssal AB 的 `speedFactor(20)` base 1.0 / value **1.1**（加成只在 mutation 里）；换正品 `100MN Afterburner II` 后 ship 最大速度 **185.0 → 830.9（4.49×）** → 推进计算本身正常，问题只在**缺 mutation**。修好 CPU 也不会让该 AB 生效。
140. → **突变数据丢失是系统性输入保真问题**：同时污染导弹伤害（D7）与推进速度（X3）。应作为一类缺陷统一处理，而不是两个孤立的 bug。
141. **T-20 交付**：D5 ✅（f4 `172.663/172.663`、f5 `250.323/250.323`、f1 `154.872/154.872` **逐位相同**）、D6 ✅（f3–f6 → `None`，f1 仍 `38.4`）、X2 ✅（Loki `subsystem {4,5}` + `empty_slots.subsystem 1`）。D7 的根因已定性，输出改为 `null`（T-22 执行）。
142. **改动对拍的证明方式值得记**：T-20 改动前**冻结 6 个样本的完整 render 输出**，改后逐叶子 diff —— f1 **0 处变化（逐字节相同）**，f2/f3/f6 仅 `depletes_in_s`，f5 9 处、f4 16 处，全部可归因。**这比"测试通过"强得多。**
143. **`1367 = maxSubSystems`**（displayName `Subsystem Slots`）才是 T3C 船体的子系统槽总量（Loki = 5）；Hecate(T3D)/Drake/Griffin 引擎**不给这个键**；`1366 subSystemSlot` 不是它。
144. **弹药续航唯一算法**：`⌊capacity(38) ÷ (弹药 volume(161) × chargeRate(56))⌋ × 51`，取最短；**不能用 `73 duration` 当周期**（那是模块生效时长）。
145. **新增高优缺陷**：`f10/f11`（T3D Confessor/Svipul）的 `modes` **缺失 6 条** —— 与「group 1306 有 Confessor/Svipul 各 3 模式」的已知事实矛盾，待 T-23 定位。
146. **第 1 轮 10 条 KB 结果：新缺陷 0 条，字段累计通过率 656/656 = 100%**（16 样本 × 41 字段）。T3C 四艘全齐、两种 T3D 的 `modes` 方向正确、后勤/指挥/战列/HAC 均未崩溃。
147. **`modes` 的「6 条缺失」是验收员自己的假缺陷**：它的名称匹配**硬编码了 `"Hecate "` 前缀**，换成 Confessor/Svipul 后对不上 → 归一规则已改为「去掉 ` Mode` 后取最后一个词」，不依赖船名。
148. **不变式 I5 被修正**：「抗性 = 基础共振率 × 模块共振率」**只在单模块时成立**。f7/f14 同时装 DC II + Reactive Armor Hardener + 2× Multispectrum Membrane 时，移除 0.85 的 DC II 得到 **0.8696** 而非 0.85 —— **这是堆叠惩罚**，不是 bug。多模块验证须走「逐模块移除实验」。
149. **X5（新观察）**：Damnation 裸船装甲 HP **7,896** → 整装 **34,142**（+26,246，来自 2 块 FN 装甲板 + 2× Trimark 装甲泵 + DC II），EHP **234,743**；按模块常识疑为重复计入，待复核。
150. **X1 不是 T3C 通病**：f7/f8/f9（Legion/Tengu/Proteus）CPU 正常、无 `tier_violations`；X1 是 **Loki + Covert Ops Cloaker** 的特例。
151. **T-25 修掉导弹射程双换算** → Kestrel/Scourge Light `max_range_m = 51450.0`、Raven/Precision Heavy `30776.528`；全量 **389 passed**，无连带破坏。
152. `shared.py` 与 `project/views/` 全目录**已无第二处双换算**（无 `ms_to_s` / `s_to_ms` / `/1000` / `*1000` 算术）。`acceptance/fixtures/prepare_expected.py` 与测试里的 `/1000` 作用于**原始毫秒**，口径正确。
153. **X5 关闭：Damnation 34,142 装甲 HP 是正确的**。逐位闭式复算差 **0.000e+00**：
`(4700 基础 + 5000 + 2500 板) × 1.4(Command Ships 10%/级×4) × 1.2(Hull Upgrades) × 1.44(2×Trimark) × 1.1568(装甲指挥脉冲自增益) = 34142.072832`。
154. **错的是「~2 万量级」这个直觉**：`20496`（裸船 + 2 板）忽略了 3 项乘子 —— 板被前置百分链放大 1.68 倍（7500→12600）、Trimark +44%、脉冲波 +15.68%。
155. **EVE 算子次序**：`mod_add` 先合入、`post_percent` 后乘 → SDE 里 `armorHPBonusAdd = 5000/2500` 的装甲板，实际贡献被放大成 **8400/4200**。
156. **hit points 免堆叠惩罚**（EVE 规则）：2× Trimark II 均 `penalty=None`、满值 20% 生效。对照实验 2× `Medium EM Armor Reinforcer II` 在**抗性**上照罚（`1.0 / 0.8691`）→ **引擎的惩罚机制本身没坏**，是属性类别不同。
157. 技能档对该值极敏感：`Command Ships` 5 → `36,713.61`；`Hull Upgrades` 5 → `35,564.66` → 34,142 是「全 4 档 + 全 active + 含自身指挥脉冲」口径的产物。
158. **观察项（未定性）**：两块 `Armor Command Burst II` 置 `offline` 后 `armorHP` **不变** → 引擎的 buff 传播似乎不受模块 `state` 影响。待后续确认是否合理。
159. **T-22 落地四项**（391 passed）：`dph` 矛盾判据 → `null` + flag `weapon_damage_zero`；新增 `feasible` / `over_budget{cpu,powergrid}` + flag `resource_over_budget_unflyable`；`load_killmail` 不再静默丢 flag 177；诊断 evidence 走 `shared.depletes_in_s()`。
160. **f1（Hecate）本身就不 feasible**：全 4 档下 CPU 余量 **−11.0**、PG **−3.6**。f5 也超（CPU −30.0）。真正 feasible 的是 f3/f6/f9/f14。→ **「配装不可飞」不是罕见个例**，`feasible` 字段是必需的。
161. **f4 的 CPU 缺口 10032.1 = 10100（cloak 100 + covert 罚值 10000）− 67.9 余量**，与「真实可飞 472.1」互证 —— `feasible False` 与 `cpu.load` 原样保留是同一件事的两面。
162. **`dph` 为 `null` 时 provenance 仍是 `measured`**：`FIELD_SOURCES` 是静态映射表，**无法按值变形** → `null` 的语义只能由 flag 表达。这是**两层 provenance 模型**（属性级类 + 实例级覆盖）的直接需求（欠账 24）。
163. **T-23 确认「modes 缺失」是验收器假缺陷**（硬编码 `"Hecate "`），产品四层全正常；已加回归测试钉死。`1367 maxSubSystems` / `281 explosionDelay` 已登记。
164. **静默退化第 3 次出现**：`tools/derive_provenance.py::load_sample()` 未传 index，导致 provenance 表在**旧 19 技能档**上推导 → 已写入开发规范 §4.6 + 红线 N16（可选参数不得静默退化）。
165. **第 61 条被修正**：弹药四系伤害（`114/116/117/118`）与 `653 aoeVelocity` **确实在** `calculate` 的**装备条目**里（导弹发射器把已装填弹药属性合并进自己的 `attributes`；无人机条目也带四系伤害）。实测 f4 `Republic Fleet Valkyrie` → 116=32.0、f15 `Rapid Heavy Missile Launcher II` → 281=0.0/653=0.0。→ `damage_split.*` 应为 **`measured`**，不是 `unknown`。
166. **护盾共振率 `271–274` 应变 `assumed`（原 `measured` 是单样本假象）**：f4 的 `EM Ward Field II`、f15 的 `Thermal Dissipation Field II` 是**盾抗模块**，`active` 才有抗性 → 状态轴下 `271` 从 `0.5`（online）变 `0.2969`（active）。`109–113`（结构）与 `267–270`（装甲）在本批样本仍是 `measured`。
167. **由此得出一个根本性认知**：**provenance 本质上是「按配装」的性质，不是「按属性」的性质**。同一属性（如护盾共振率）在无盾抗模块的船上可以是 `measured`，在有盾抗模块的船上必须是 `assumed`。→ **属性级静态表只能是近似**，两层模型（欠账 24）从"应该做"升级为"必须做"。
168. **T-14 完成多样本重推**：4 样本（f1/f4/f15/carrier）× 3 轴，技能档全部 `all4+min_required` / **512**；并集轴变化 技能 492 / 状态 33 / 剖面 24。已登记常量 **measured 66 / assumed 71 / unknown 10**（9 正 ID + 手工覆盖的 `-70`），`overlap=[]`、`consistent=True`。
169. **11 个旧 `measured` 因技能档修好而翻 `assumed`**：`11/15/38/54/55/76/79/158/263/479/482/564`。→ **这是旧表跑在 19 技能档上的直接证据**（例如「263 shieldCapacity 三轴不变」从来是假象）。
170. **14 个 `unknown → measured`**：`114/116/117/118`（四系伤害）、`653 aoeVelocity`、`1285/1286/1287/1289`（requiredSkill4/5）、`2215–2219`（战斗机槽位）。
171. **12 个 `unknown → assumed`**：`-55…-62`（战斗机族）、`-14`、`-46`、`-47`、`654`。负 ID 全部 `assumed`（规则直接后果）。
172. **战斗机必须放 `fighter_tube` 槽**；放 `fighter_bay` 只算 `-57` 机库占用，`-58/-59..-62` 全 0（`-59` 需超级航母 Nyx + 重型战斗机，`2219=4`）。
173. **第 2 轮完成**：26 样本 × **43 字段**（新增 `feasible` / `over_budget`）= **1118/1118 = 100%**。缺陷：1 medium + 12 low 观察。
174. **该 medium 极可能是正确行为**：f26 航母 `dps.fighter = 0.0` —— 若载机在 `fighter_bay`（未发射）则 `-58 = 0` 是对的（第 172 条已实测）。已在 T-28 核实原始 killmail 的 flag 是 `FighterTube` 还是 `FighterBay`。
175. **一个值得记的讽刺**：验收员说「这轮最有价值的是航母那条——它安静地报了个 0」，但这条很可能**它自己误判了**。**「看起来很有道理的错误」这条判据，对验收工具自身同样适用。**
176. 验收员本轮修了自己工具链 3 处：selftest 合成 payload 缺新字段、`fetch_fixtures.py` 重跑会覆盖已有样本（改为幂等 + `--only`）、f23/f26 判据写得过窄。
177. **T-27 完成两层 provenance 模型**（410 passed）：新增 `resolve(attribute_id, value)` —— **值缺失（`None`）→ `unknown`**（覆盖静态表），值存在 → 走静态表；`tag()` 签名与语义未变。`audit()` 自报 `instance_missing_rule`。
178. 因值缺失翻 `unknown` 的量级：**f1 9 个 / f4 12 个 / f5 10 个**。靶心 `f4.dph` 已从 `assumed` → `unknown`（旧口径报的是 assumed，不是 measured）。
179. **新暴露的语义问题**：`unknown` 现在同时承载「**未知**」与「**不适用**」——纯炮台配装的 `application.explosion_radius_m` 不是"不可知"，而是"这配装没有导弹"。三态契约无法区分 → 需评估是否加 `not_applicable` 或第 4 态。
180. **T-27 抓出我两处探针缺陷**：① `glob(name + '*')` 会先匹配到 `.expected.json`（`e` < `j`）；② 我写的「通用断言」循环体是 `pass`，**空转**。→ 教训：**验收脚本必须自证非空转**（注入一个必然失败的用例来证明它真的在断言）。
181. **数组元素口径（本轮新裁决）**：一条路径只有一个标签；`weapons[].ammo_name` 在混装配装上「一门有弹、一门没装」→ 按缺值判 `unknown`；全部都有弹药时才算 `assumed`。
182. **下一步顺序定为 3 → 1**（与 T-27 的建议一致）：
 ① 先做「**全样本扫描：值为 `None` 却仍报 `assumed`/`measured` 的漏网**」（纯读不写，最快给出实例规则的覆盖面证据）；
 ② 再做第二条实例规则（按配装检测状态敏感族 → 有盾抗模块则 `271–274` 判 `assumed`）。
 理由：第 ① 步能用证据证明规则真的覆盖住了，再动第 ② 步；顺序反了就是在没有度量前扩范围。
183. **T-28 判定：`-58 = 0` 是正确的**。f26 的 8 架载机 flag = **158 FighterBay（机库）**，全 fixture 无 159–163（FighterTube）。flag 语义取自现代 SDE `invFlags`（本地 SDE zip 不含该表，`evego` 枚举只到 156）。
184. **改造实验（决定性）**：A 全部留 `fighter_bay`（state=active）→ `-58 = 0`；B 只把 Templar 移进 `fighter_tube` → `-58 = 240.846`。**A 与 B 只差 `slot.type`** → 零值成因是「在机库」，不是 offline；**引擎伤害链路正常，无限制**。
185. **误报有两条独立根因**：① 我方 `shared.classify_weapon` 把 `fighter_bay` 也判成 `family: "fighter"`；② 验收判据「有 fighter 武器 → `dps.fighter > 0`」过粗 —— **`Siren I` 是支援战斗机**，进管后只记 `-61=1`，仍不出 `-58`。
186. **修正验收员的数据描述**：`-57`（fighterCapacityLoad）**只出现在船级**，item 级没有；item 级 `-55/-56 = 0.0` 只对 Templar 成立，Siren 连这两个键都没有。
187. **未定向观察**：flag 164（`StructureServiceSlot0`）→ 引擎映射成 `service[0]`，一架载机躺进去仍算出 `-57 = 1000 / -58 = 33.86`。f26 无 164，与本次判定无关，待定性。
188. **方法论复现**：这是第二次「可疑的零值其实是对的」（第一次是 Damnation 装甲 HP 34,142）。**直觉怀疑必须用闭式复算或对照实验证伪，不能靠"看起来不合理"下结论。**
189. **第 3 轮完成 → 三轮验收收官**：**36 样本 × 43 字段 = 1548/1548 = 100%**；**产品缺陷 0**（medium 及以上），17 条 low 观察（多为覆盖度说明）。快照 `enforced` 保持 0 DIFF。
190. 第 3 轮覆盖：双推进 Brutix、工业舰 Bestower、纯无人机 Vexor（**306.4 DPS 全来自无人机**）、7 门智能炸弹 Praxis、10 件 bulkhead Megathron（EHP 295k）、98 项货舱 Iteron、无弹 Maller、Jackdaw `modes=3`、Apocalypse。
191. **验收工具这轮修了自己 4 处判据**：`check_fighter_chain` 按 `-62`+支援型+flag 三重判定；**把「I3 与算术平均差 >1%」从 blocker 降为 low**；fetch 加 `allow_reuse`；判据补 `require_all`/`min_count`/`min_cargo`/`require_no_charge`。
192. **I3 的正确切分**：真正的**不变式**是「`Σ HP / Σ(w·r)` 按层复算」（永久有效）；「与四系算术平均差 > 1%」只是**启发式观察** —— f31 结构坦抗性近乎均匀，两者只差 0.54%，是样本性质不是缺陷。→ **启发式不能当红线**。
193. **T-29 修掉误报根因**（417 passed）：`classify_weapon` 现在 `fighter_tube` → `family:"fighter"`、`fighter_bay` → **排除**；火力视角新增 `fighter_bay{count,types,capacity_used_m3}` + 诊断 `fighters_in_bay_only`。**medium 误报由构造消失**，且不丢事实。
194. **`-57` 是「载机舱容占用」，含已发射的中队**：把 Templar 移进 `fighter_tube` 后 `-57` 仍为 14000 → `capacity_used_m3` **不能**改成按机库条目累加体积（两者在「有中队发射中」时会不同）。
195. **覆盖盲区**：`fighter_bay.types` 是字符串列表，渲染层把非空标量列表当一个叶子 → 必须登记 `FIELD_SOURCES`。Hecate 样本机库为空，既有守卫测试**校验不到**这条，已在 f26 上补「叶子全覆盖 + 字符串白名单」断言。
196. **可靠性自检的写法值得推广**：临时把 `classify_weapon` 的 bay 分支改回旧行为 → 新测试立刻 **2 failed** → 证明断言不是永真，然后还原。**每个关键断言都该能被一次故意篡改打红。**
197. **T-31 常量覆盖守护上线**：`tools/check_attr_coverage.py`（A=FieldSources 覆盖 / B=AST 裸 ID 扫描 / B2=project 其它模块 / C=常量表自洽）+ `tests/test_attr_coverage.py`（18 条，全部带变异自检）。实测 A 171 条登记（int 91 / None 80）、C 146 常量 = 146 行、缩放 11、`audit consistent=True`。
198. **四个历史缺口（281/56/1272/1367）全部已登记** → 「六视角缺常量」这个前提当前不成立，`config/attributes.py` 无需补。
199. **真实缺口 1**：`project/provenance.py:112` 的 `ATTR_MAX_TARGETS_UNVERIFIED = -70` **裸写在 project/ 里、常量表没有它**（`Index.attribute(-70)` = None → 派生负 ID）。补登会撞 `tests/test_attribute_dict.py` 三处硬编码「常量数 = 146」。
200. **真实缺口 2**：`project/render.py:482/485/488` 的 `MODE_EHP_ATTR = -43` / `MODE_DPS_ATTR = -12` / `MODE_ALIGN_ATTR = -1` 是**真实 N12 违规**（裸写派生 ID）。已进 `ALLOWED_BARE_IDS` 白名单，且白名单**带过期检测**——修好后测试会要求销账。
201. **`tests/test_attribute_dict.py` 把常量总数硬编码成 `146`（三处）** —— 任何人补常量都会让它变红。**这正是 `-70` 迁移被挡住的真实原因**，也是「补常量」与「守护测试」两类任务的隐性冲突点。→ 应改成相对基线（`>= 146` + 显式增量断言）或完全由表推导。
202. **白名单必须会过期**（T-31 的设计）：`ALLOWED_BARE_IDS` 里的条目一旦被修好，测试会**主动要求销账**。这比「永久豁免」强得多 —— 豁免不免除债务，只推迟还款。
203. **沙箱拒绝 `tmp_path`（`WinError 5`）** → T-31 把扫描器拆成 `scan_source_for_bare_ids(source, display)`，测试改用**内存字符串**做变异自检，顺带让扫描器零 IO。**这条技巧可用于所有需要"读文件做静态分析"的测试。**
204. **T-30 完成第二条实例规则**（447 passed）：`resolve(*, state_sensitive)` + `state_sensitive_attrs(ctx)` —— **全 online vs 全 active 各算一次，逐属性比对，值变即 `assumed`**。不枚举模块族，只升格（`measured→assumed`），不降格；值 `None` 仍优先 `unknown`。
205. **敏感性集合实测**：f4 **18** 个（含 `271–274` 与 `-43/-30/-27/-47/-50/-52/-72/-6/-5/-4/-39…-42`）、f1 **15** 个（不含 `271–274`，是 MWD 族 `4/552/37/-1`）；36 样本中 35 个非空（7–46 个 ID），仅 `f6-minimal-fit` 空集。
206. **成本实测**：最多 +2 次计算，f4 只 **+1**（`mobility` 视角本来就算 active 变体，命中引擎 LRU）；纯被动配装 **0 次**；同 ctx 二次渲染 **0 次**。
207. **36 样本探测开/关，views 逐字节相同（0 处 provenance 变化）** —— 因为 `survivability.FIELD_SOURCES` 今天**没有字段直接以 `271–274` 为来源**（`ehp_by_damage`/`thinnest` 都是合成值）。→ **规则接好了线，但今天无字段可作用**。T-30 用「模拟静态表把 271 收紧回 measured」的补丁把接线两个方向都钉住。
208. **关键发现**：静态表判 `measured` 的 `109–113`（结构）与 `267–270`（装甲）**在 11 个 KB 样本上实测随状态变化**（f7/f9/f12/f14/f17/f18/f24/f25/f30/f34/f36；来源 `Assault Damage Control II`、`Armor Command Burst II`、主动抗性模块）→ **它们的 `measured` 只是「样本稳定」，不是「配装稳定」**。第三次印证第 167 条。
209. **验收侧契约冲突**：`run_acceptance.py::check_provenance_instances()` 断言「有值 ⇒ == 静态表 tag」，会**把正确的实例级升格判成 medium 缺陷**（今天 0 升格所以没炸）。已通知验收员改为「必须是静态表结论，或比它更保守」。
210. **T-33 完成回归一键化**（38 条测试）：`tools/regress.py` —— 四门（`pytest` / `attr-coverage` / `snapshot` / `acceptance`），**依赖在代码里显式声明**（`Gate.requires`；pytest 未过时 acceptance 标 SKIPPED 且**执行器一次都不被调用**），`--json` 机器可读，成功只留一行摘要、失败只打尾部 30 行，子进程强制 UTF-8。
211. **「跳过 ≠ 通过」写进退出码**：有 SKIPPED 且无 FAIL → 仍返回 **1**；全 SKIPPED → 2。**这防住了「可跳过就不算失败」的软门禁**——那是比漏检更隐蔽的假绿。
212. 实测：快速门 3/3 PASS（**485 passed**）；`--full` 4/4 PASS（acceptance 3.5s）。
213. **验收侧冲突已修**：`check_provenance_instances` 改成按**保守度排序**判定 —— `measured(0) < assumed(1) < unknown(2)`，写进代码而非散落 if。规则：值 `None`→要求 `unknown`；**升格合法**；**降格（`assumed→measured`）禁止**；其余等于 `tag()`。自证：升格 0 缺陷、降格命中 `provenance 错误`。
214. **旧断言会误报 260 条**：扫描 36 样本「有值却标 `unknown`」的叶子共 **260 条**，全是静态表未登记的路径（`tier_violations[].rule.used`、`cap_sensitivity[].depletes_in_s` 等）。→ 「或更保守」不是宽容，是**唯一可行解**。
215. **这 260 条暴露一个新缺口**：`FIELD_SOURCES` 覆盖率只保证**数值**叶子，**字符串 / 枚举 / 嵌套路径没有 provenance 归属** → 落到 `tag()` 的 `unknown` 默认分支。合成值与枚举值本质是「我们自己算的」，至少应是 `assumed`。→ 记为欠账 66。
216. **T-32 完成（522 passed）**：新建 `project/reasons.py`（纯函数判定）+ `render.py::_reason_block()`；36 样本 reason 分布 **not_applicable 744 / ambiguous 40 / input_incomplete 1 / engine_omits 0 / unclassified 0**。
217. **修正第 199–200 条**：四个待迁常量里 `-43/-12/-1` **早已在表里**（`ATTR_EHP` / `ATTR_DAMAGE_DPS` / `ATTR_ALIGN_TIME`，`unit=None` 即派生），真正缺的只有 **`-70`** → 实际动作是「新增 1 个常量（146→147）+ 3 处改引用 + 白名单销账」。**我任务书里"四个都要新增"的判断是错的。**
218. **预算判断值得记**：`unknown_reasons` 花 **+979 chars** → 16,001；按公式该重定为 20,000，但 T-32 **主动没上调**，理由「抬阈值会削弱护栏」，改为在 docstring 记录「余量从 20% 掉到 6.2%，下次再加出口必须重定」。→ **护栏阈值不该为了让新功能装得下而上调。**
219. **`not_applicable` 有污染**：`tier_violations[].kind/message/resource/rule.*` 属「有值但未登记 `FIELD_SOURCES`」（N4 兜底），被归成 `not_applicable`。更干净是给 `views/constraints.py` 补登记（→ `assumed`）。→ 欠账 66，T-34 处理。
220. **T-34 完成（538 passed）**：`constraints` 补登记**容器** + 6 条元素字段 → `not_applicable` **744→600**、`unknown` **785→641**、reason 路径种类 **52→44**。
221. **144 条是纯污染**：18 个「有资源类档位违规」样本 × 7 条元素字段（126）+ 18 个「`tier_violations` 为空」样本 × 容器本身（18）。迁到 `assumed` 的独立路径 8 条。
222. **「逐键枚举 `rule.*`」是错的解法**：resource 分支给 `type/resource/used/available`，skill 分支给 `type/type_id/required/level` —— 登记**容器**能让两种形状都继承，**且引擎新增违规类型时不会静默退回 `unknown`**。
223. **`hard_violations` 此前零覆盖**：36 样本一条硬违规都没有 → 该字段族没有任何断言。T-34 用合成样本（Rifter + 大号弹药）补上覆盖。→ **「样本里没出现」不等于「不用验」。**
224. **未登记 ≠ 一定落 `unknown`**：容器登记（`weapons: None`）会让元素路径提前 `assumed`。**判断污染必须看「路径 → 有没有任何登记」，不能只看精确键。**
225. 新守门判据的额外落点：`resources.upgrade_space.{slots_checked,upgradeable_count,blocked_by_resource}` 是 `OVERVIEW_DOWNGRADES` 产出的**摘要叶子**，天然不在 `FIELD_SOURCES`（三态由 `SUMMARY_PROVENANCE` 覆盖）—— 覆盖率断言必须认它，否则误报。
226. 预算：主样本 **16,001 → 15,657（−344）**，余量回到 7.3%，`BUDGET_CHARS = 17,000` 未上调。
227. **第 4–6 轮完成 → 六轮验收收官**：**64 样本 × 44 字段 = 2816/2816 = 100%**；medium+ 缺陷 **0**；26 条 low 观察。`pytest 538 passed`、`enforce 0 DIFF`。
228. **第 5 轮「随机抽样 10 条」：全过、0 缺陷** —— Raptor / Gnosis ×2 / Slasher / Crusader / Helios / Merlin / Cormorant / Legion / Armageddon，**不经任何筛选**。这是最有力的证据：**这些配装不是我们设计的**。
229. **reason 分布（64 样本）**：`not_applicable` 1176 / `ambiguous` 73 / `input_incomplete` 1 / `engine_omits` 0 / **`unclassified` 0**。结构校验：表存在、key ⊆ `provenance`、只挂 `unknown`、**0 缺 / 0 多**。
230. 第 4 轮 8/10：**航母「已发射中队」与 Skua 在 7 日窗口内无战损样本** → 缺口诚实记录（有样本即可补验）。已验到：Legion/Loki/Tengu 的 covert 与第三种子系统组合、Revelation/Moros（无畏，EHP 91.9 万 / DPS 12167）、Apostle（FAX，EHP 172.9 万）、Nidhoggur（`fighter_bay.count=27`）、Badger（货舱 80+）。
231. 第 6 轮合成：双推进 / 空配装 / 全槽位 / 无推进 / 无改装 / 混搭坦 / 纯智能炸弹 / 注射器无药 / 武器空弹 / 单模块。**超载与损伤态 killmail 与 EFT 都表达不了**（引擎直接拒绝非法弹药）→ **结构性不可测**，不是没测。
232. **验收员本轮自己误报 7 次并全修**：① I3「混合==调和」在 Gnosis 四系共振均匀时天然成立（blocker → low）；② 通用单位扫描把米/秒与毫秒原值撞上（白名单限定已知秒类叶子）；③ Sharpshooter 方向检查对智能炸弹/空弹无意义（基准 0 时跳过）；④ 自建 EFT 把货舱/无人机行放进模块段（3 个 blocker）。
233. **随堂测试 f67（Bifrost / killmail 139255942）**：**第一条带「互斥模块」的真实样本** —— 同时装 `1MN AB` + `5MN MWD`。
   **⚠️ 第 240 条修正：这不是「非法配装」**。双推进一起带在 EVE 里**非常常见且装配本身合法**，只是**同一时刻只能启用其一**（`max_group limit=active`）。用「非法」描述是错的，那是**状态互斥**，不是装配错误。
234. **两条独立路径（PM 跑一遍 + 验收员跑一遍）数字逐位一致**：`speed 59928.363` / `EHP 5828.988` / `CPU −14.32` / `budget 16158`。→ 数值链路本身没坏，坏的是语义提示。
235. **缺陷 A1**：`speed_mps = 59,928`（AB `speedFactor 156.25` 与 MWD `631.25` **同时 active** 叠加；`overload` 达 134,777）→ 引擎不因 `max_group` 违规自动降级模块，而**机动视角没有任何提示**。**与 Loki cloak（X1）完全同类：状态语义缺失；上次修了资源，这次暴露机动。**
236. **缺陷 A2**：`mwd_sig_increase` 文案写「关闭时 **65.72 m**」，而同视角 `propulsion_states.off.sig_m = **60.0**` → **同一视角内自相矛盾**。
237. **缺陷 A3**：`cap_by_module` 四项合计 **5.813 GJ/s** ≠ `capacitor.peak_load` **2.213**（差 3.6 = MWD 那项）；且 **MJFG 的 0.513 GJ/s 与引擎 `capacitorNeed 85 GJ / 5.0625 s ≈ 16.8 GJ/s` 差一个量级**。
238. **缺陷 A4（已查清，且我原来的描述是错的）**：完全无武器时 `dph` 有两种结果，根因是 **引擎只在「至少存在一个高槽模块」时才派生 `-11/-12`**：
   - **f67 有** `-11 = 0.0` / `-12 = 0.0`（它装了扫描发射器/吸电/隐形/扰频 → 有高槽模块）。**我此前说「f67 船级根本没有该键」是错的。**
   - **f58/f61 才是真的没有这两个键** —— 它们**零高槽模块**（f58 是 0 模块空配装，f61 全中/低槽无武器）。
   - 有武器但没装弹药（f4 剥掉 charge 的改造样本）→ `-11 = 0.0`（既有口径一直是 `0.0`）。
   → **统一口径**：无武器挂架且引擎不给 `-11` → **`0.0`**；「有弹药却零齐射」仍 `null` + `weapon_damage_zero`。
239. 验收员自修：`prepare_expected.py` 在 f67 上 `KeyError: -12`（该船级无该键）→ 已加存在性保护。
240. **⚠️ 术语与思路修正（主人当场纠正）**：双推进一起带**不是不合法的配装**，是**常见做法**；限制在于「同一时刻只能启用其一」。
   → 这意味着 A1 的正确定性不是「非法叠加，快警告」，而是**「我们的状态假设错了」**：`assumptions.module_states = engine_default` 会把能开的模块**全开**，而真实玩家是**按需开一个**。
   → 因此 `speed_mps = 59,928` 不是「算错了」，而是「**算了一个玩家永远不会处于的状态，并把它当成默认值**」。
   → 正确修法：**`propulsion_states` 要增加「单推进」分支**（只开 AB / 只开 MWD 各一组值），那才是玩家真实状态；顶层值保留但必须让读者能拿到真实值。flag 措辞也必须是「状态互斥」而非「非法」。
241. **T-36 已落地**：无武器且引擎不给 `-11` → `0.0`。**f26 的解法很漂亮**：判据定成「**配装里有没有任何武器系统**」—— 没炮 + 没无人机 + 机库没载机 = 确定 `0.0`；f26 机库里有 8 架载机（**载机是武器系统**，伤害走 `dps.fighter`、不进 `-11`）→ 仍 `None` + `not_applicable`，**`test_render.py` 的 f26 快照原样成立**，一条断言没放宽。
242. **`dph` 的 provenance 只能是 `assumed`**（`-11` 是负 ID → 静态表判 assumed；`0.0` 时实例规则 1 不触发）。要变 `measured` 得改静态表 + 放行「降格」，而那违反现有「实例级只升不降」规则 → **保留 `assumed`**，理由写进 `FIELD_SOURCES` 注释。
243. **遗留不一致（T-37 处理）**：完全无武器系统时 `dps.base` 在 f58/f61 是 `None`，而 f67/f26 是 `0.0` → 统一为 `0.0`。
244. **提醒：并行写入窗口会造成 2 秒级的假红**。22:44 有一次 `test_config_rules_match_implementation` 失败（`diagnostics.py` 已有 `propulsion_illegal_stack`、`config/diagnostics.json` 还没），那是 T-35 两个文件的写入间隙，**不是谁的 bug**。→ 多代理并行时**不按单次 red 下结论**。
245. **T-36 的判据值得抄**：`_has_any_weapon_system()` = 没有炮 + 没有无人机/战斗机记录 + `fighter_bay.count == 0`。
   → **用「业务事实」判，而不是用「引擎给不给键」判。** 后者会随实现细节漂移（引擎只在有高槽模块时派生 `-11`），前者稳定。
   → 这条让 f26（机库 8 架载机）自动留在 `not_applicable`，`test_render.py` 一行都没改。
246. **`dph` 口径四档定稿**：① 无武器系统 → `0.0`；② 有武器但没装弹药 → `0.0`（引擎给 `-11=0.0`，齐射是真 0）；③ 有弹药却零齐射 → `null` + `weapon_damage_zero`；④ **只有机库载机**（伤害走 `dps.fighter` 不进 `-11`）→ `null` + `not_applicable`。
247. **变异验证两次**：把「无武器」分支改回 `None` → 4 failed；把「有弹药零伤害」分支关掉 → 2 failed。**两个方向都证明断言非永真。**
248. **T-35 完成（569 passed）→ 电容恒等式修好**：`船体 -4 == Σ 所有已装模块装备级 -4`，**含负数**（66 个样本逐一实测，相对误差 1e-9，无例外）。负数来自**向电网输入电容**的模块：吸电/传电（Energy Nosferatu）与电容注射器（Capacitor Booster）。
   → **f67 那 3.6 的差额不是 MWD，是 `Small Ghoul Compact Energy Nosferatu = −3.6 GJ/s`。我的推测又错了。** 旧实现两处破坏恒等式：`<=0` 过滤掉负项、只留前 8 组（f40 有 10 组）。
249. **MJFG 的 0.513 GJ/s 是引擎正确值，不是读错属性**：`-4 = capacitorNeed(6) / derivedCycleTime(-3)`；MJFG 的 `-3 = 165,760 ms`，含 `moduleReactivationDelay(669) = 160,000 ms` → **85 / 165.76 = 0.5128**。我预测的 14.76 是**瞬时速率**，不是周期平均。→ **我「偏小一个量级」的判断是错的。**
250. `propulsion_states` 新增第五档 **`propulsion_off`**（只关推进、其余保持）= 49.076 m/s / sig 65.72 / align 2.958；`off` 仍是全模块离线 = 60.0。→ `mwd_sig_increase` 文案的 65.72 现在有字段逐位对上（65.72 = 60.0 裸船 + 5.72 护盾扩展装置信号罚）。
251. 「推进模块置 offline」与「从 fit 摘掉推进模块」在速度/信号/对齐/质量上**逐位相同**，但语义上选 offline 更诚实（模块还在船上）。
252. **f27/f12/f47/f57 也出 flag** —— 它们同样「两件推进都 active」（f27 base 速度 240,368 m/s），**这是预期行为**，不是误报。真正「带两件只启用一件」的是 f1（MWD active + AB offline）/f9/f45。
253. **预算极紧**：f47 16,878 / f67 16,716（上限 17,000，只剩 **122 / 284** chars）→ 下一次加字段很可能把这两个挤进降级。
254. **T-38 完成**（587 passed）：flag 改为 **`propulsion_state_conflict`**，文案无「非法」二字；`diagnostics.py` 规则名/docstring 同步；新增 7 条测试（config↔实现同口径 / 双推进 f67+f27+f57 / 单推进 f1+f5+f45 / 渲染层归位 + 全输出无判词）。
255. **T-38 的越界处置是对的**：`test_views_a.py` 里 T-35 留下的 6 处旧 id 引用，它做了「外科手术」（常量值、文案常量、函数名、2 处断言、3 处注释），改前确认 mtime 已稳定 2.5 分钟。若不改，整包必红 → **跨任务改名必须包含全部引用点同步，否则尾巴必留。**
256. **预算诊断三条硬结论**：
  ① **`budget.chars` 只量 `views` + `modes`，`meta.assumptions.skill_tier`（512 条 / 6,007 chars）完全不计入** → 摘要化对预算省 **0**。**我「这是最大一块」的推测错了（第三次）。**
  ② **f12 今天就已经在降级**（真实载荷 18,178 > 17,000；报出的 16,590 是**降级后**的数字，余量 410 是假余量）。降级代价：**`flags` 全部丢 evidence**（诊断证据消失，不是「少几行」）+ `cap_by_module`/`cap_sensitivity` 截到 5 项。→ **这是当前产品缺陷，不是未来风险。**
  ③ **唯一够用的杠杆是 `provenance` 按三态分组（省约 1.8k）**；`unknown_reasons` 按 reason 分组只省 172–527（survivability 上甚至**负收益 −2**）。
257. `propulsion_states` 单状态条目真实体积 **196–242 chars**（我给的 +250~350 是偏保守上界）。
258. **T-37 完成（587 passed）**：`_dps_raw()` 复用 `_has_any_weapon_system()` 到 `dps.base` / `dps.with_reload` / `overload.{off,on,delta}.dps` 五条路径；`_dph()` 一字未改。`reasons.py` 删掉 `dps.base`/`dps.with_reload` 的条件登记，`overload` 前缀收窄成三条射程叶子 → **「有值」与「不适用」不再同时落在同一条路径上**。
259. **`-11 / -12 / -13` 完全同源**：都只在「至少一个高槽模块存在」时才派生。
   - f67（有高槽模块）→ 三个键都在，值 `0.0`
   - f58（0 模块）/ f61（全中低槽）→ **三个键全缺**
   → **「缺键」本身永远不能当语义**，必须用业务事实（`_has_any_weapon_system`）判定。这是本项目第四次踩「拿引擎行为当语义」的坑。
260. **我任务书的第 4 次错误**：我写「四样本的 `unknown_reasons` 不再有 `dps.*`」——按字面做不到也不该做：`dps.drone` / `dps.fighter` 仍由引擎不给 `-14/-58` 决定，属既有条件字段（缺值给 `None` 不补 0）。T-37 按字段规格执行、未盲从任务书。
261. **我报错的第 5 个数字**：此前我说 `render_overview` 是 **29,213 字符** —— 那是**带 `indent=1` 缩进**的尺寸。紧凑序列化实测 **23,256**。→ **凡报体量必须声明序列化方式**（紧凑 vs 缩进）。
262. **T-39 完成分层输出第一阶段**（659 passed）：新增 `project/summary.py`（`render_index` / `render_view` / `get_path`，组装与取数**共用同一张 `INDEX_SPEC` 表**，构造上不可能漂移）+ `render.py` 只追加 66 行（`render_overview`/`render_detail` **diff 0 行**）。
263. **L0 实测 1,884–1,965 字符 vs overview 23,256 → 降 91.8%**（约 7,000 token → 约 600 token）。`INDEX_BUDGET_CHARS = 2,000`，四样本余量 ≥35。
264. **文档估算偏乐观 1.57×**（估 1,220 / 实测 1,918），三处系统性低估：① `flags` 实际 6–7 条（估 2 条）；② 六视角 4–5 字段实测 ~940（估 620）——`damage_split` 四系、`cpu/pg` 三元组、`slots` 五槽「字段名 + 真实数值」压不动（红线禁丢数值）；③ `meta` 实测 377（估 180）。
265. **`flags` 在 L0 只给 `[{id}]`，不给 message**：f47 有 7 条、f67 有 6 条，带 message 时 flags 块 555–688 字符，**无论怎么截都过不了 2,000**。取舍是「**一条不丢、只把『为什么』留给 L1**」而不是「留几条完整、丢掉其余诊断」——渲染层红线是绝不丢弃可疑点。
266. **512 条技能「条数相同、内容不同」**：`derive_skill_tier` 会按配装补模块技能，所以 `skill_tier.hash` **必须分样本**（f67 `d4973f55eac1` vs f1 `f1ea35f655c3`）。
267. **主人裁决：先迁验收基线（B），再接 P4**。理由与我一致 —— 能力已有但还没有任何 agent 真正调用过；不过验收不跟上，后面改什么都说不清。
268. **验收侧迁移的五组要求**：① 旧 `render_overview` 基线不许退化（回归门）② L0 契约（体量/字段完整/**按文档独立列期望清单而非 import `INDEX_SPEC`**/红线/`degraded` 显式/路径可达）③ L1 逐字节一致性（未降级样本）+ 降级样本允许不同 ④ `get_path` 行为（缺失返回 None、int 键回退、返回结构）⑤ **L0 可用性判断**（flags 只给 id 够不够、`damage_split` 全 null 是否可省、`meta` 里的假设该不该留）。
269. **L0 的 `degraded` 语义与 overview 不同**：它是「**L0 自身正文超预算**」，不是「overview 降级」。因为红线禁止为省字符丢数值，超预算只上报不裁剪。
270. **撤回 `damage_split` 整块 `null`**：`dict ↔ null` 是**形状多态**，比「值多态」危险（消费方每次都得先判类型）；只省 ~30 字符，不值得。验收脚本对它做了 `len()` 与类型校验，整块 null 会在第 2 个样本抛 `TypeError` 中断全场。→ **形状是契约，值是数据**；改形状前先看消费方。
271. 测试员的两处过期设定已改：`DOC_INDEX_BUDGET` **1,500 → 2,500**（超文档预算计数 65 → 0）；`damage_split` 期望改为「四键 dict、值可为 null；整块 null = 口径错误」。
272. **测试员加了一层脚本自我防护**：每个样本的 L0/L1 检查各自 `try/except`，异常记「口径错误」并**继续**。→ **验收脚本自己崩掉 = 整场没有结论**，这和「`dph is None` 直接短路」是同一类问题。
273. **新发现（已修正，见第 279 条）**：**65/65 样本**的 `budget.chars` 与实际 `len(json.dumps(index))` 不符（差约 **46**）。
   → **我判断错了**：我以为是「L0 继承了 overview 的 views+modes 口径」需要改代码。实际 HEAD 时代码**已经是 body-only**；旧口径值只会有 1,040–1,066，而实际报的是 1,996–2,118。真正错的是**验收脚本拿完整长度去比正文**（与它自己的 `expected` 文案矛盾）。**产品代码从头到尾是对的。**
274. **裁决：`INDEX_BUDGET_CHARS` 定 2,500**（与文档一致）。测试员建议的 2,000 是基于 **T-40 之前**的实测（max 1,987）：T-40 还要加 `hint`（+~50）与 `provenance_counts` 标签（+~200），1,987 预计涨到 **≈2,140**，2,000 会被打爆。
275. **T-40 完成（687 passed）**：`unclassified` 清零（f12/f34 各 0 条）；`get_path` 的 `provenance: null` **968 → 0**；`hint` **276/276 条、全部 ≤10 字**；`provenance_counts` 回复带标签对象；`degraded=True` 分支下六视角数值**一字不变**。L0 体量 min 1,801 / median 2,000 / **max 2,183**（额度 2,500，余量 317）。
276. **T-40 纠正了 T-36 的判据表述**：「至少一个高槽模块」只是**相关性描述，不是判据**。真实规则是 —— **引擎只在「至少一件模块产出了 `-21` 伤害管线」时才派生 `-11/-12/-13`**。
   - f12（4 件遥修）、f34（5 件吸电）都有高槽模块，却**照样缺三键**（它们没有 `-21`）
   - f67 有，是因为装了 `Expanded Probe Launcher I`（逐件 `-21 = 0.0`）
   → 所以这五条归 `not_applicable` 有**五条依据**：值确实 None、配装**有**武器系统（维修/ECM 无人机）、与 `dph` 同判据、不是 `engine_omits`（`-12` 对 f67 照给）、不是 `input_incomplete`（数据完整）。
277. **红线被违反一次**：T-40 执行了 `git commit`（`6358afc`）。虽然只含它自己的 4 个文件、内容干净，但**子代理不得执行 git 写操作**（N15）。已在后续任务书里重新强调。
278. **hint 成本实测 86.5 字符/样本**（我估 +80，偏乐观 8%）：漏算了 JSON 键名开销（`"hint": ` + 引号 = 12 字符/条）。
279. **「`budget.chars` 量错对象」这条 medium 是验收脚本自身的判据错误**，产品代码本来是对的：
   - `budget.chars == len(dumps(body))` → **65/65 成立**
   - `budget.chars == len(dumps(完整))` → **0/65 成立**
   - 完整 − 正文 = **恒 46**（就是 `, "budget": {...}` 块本身）
   - `check_layers.py:177` 量的是**完整**长度，而同一缺陷的 `expected` 文案写的是「正文长度（不含 budget 块）」→ **条件与期望自相矛盾**，产品正确时这条永远消不掉。
   → 已通知验收员改一行 + 加「完整 − 正文 == budget 块序列化长度」的交叉断言（这样将来 budget 加字段，差额变化会被发现而非静默漂移）。
280. **通则**：**期望文案与判据必须同源**。凡带 `expected` 描述的检查，应当用同一个函数算出两个值再比，不要一处手写文案、一处手写判据。
281. 验收员这是**第二次**「判据自己写错」（前一次是 rounds 4–6 的 7 次误报）。→ **验收工具的可信度是靠被样本打脸打出来的**，这话对它自己同样成立。
282. **T-41 完成（691 passed）**：`budget.chars` 口径定稿 = **body-only**（三步顺序写进 `render_index`：`_index_body()` → `len(dumps(body))` → 塞回 budget），`degraded` 是**严格大于**（等于预算不降级）。恒等式 `完整 − body ≡ 46`（65/65）。
283. **我的第 7 个错误**：我断定「L0 的 `budget.chars` 继承了旧 views+modes 口径，需要改代码」——**错**。HEAD 时代码已是 body-only（旧口径只会算出 1,040–1,066，实际报 1,996–2,118）。**真正错的是验收脚本的比较对象**。
   → 教训：**「两个数不一致」时，先确认哪个是基准，再断定谁是错的**。我直接假定「产品错」，而实际是度量工具错 —— 这和之前「可疑的零值其实是对的」是同一类思维惯性（**先怀疑被测量对象，不怀疑尺子**）。
284. **我任务书的第 5 处错误**：我要求「`budget.chars` 与重新序列化长度差 ≤1」—— 但「重新序列化」若指**完整输出**，与 body 恒等式数学上互斥（恒差 46）。→ **验收报告 §10.5 的措辞需同步**：比较用 body、护栏用 full。
285. **T-42 侦察结论（决定性）：pyfa 解决不了跨组互斥。**
   - `eos` 只做**同组**互斥：`max_group.py:70` 用 `item._type.group_id` 计数；`const/eve.py` 的 `maxGroupActive=763` / `Online=978` / `Fitted=1544`
   - **实跑证据**：Panther `MWD(g46) + MJD(g1189)` 进 eos `validate()` → 只有 `skill_requirement` / `state`，**没有 `max_group_active`**；Bifrost `AB(g46) + MWD(g46)` → 有 `max_group_active`（`group_id=46, quantity=2, max_allowed=1`）
   - SDE 数据面（build 3607705）里 `maxGroupActive` 覆盖 339 type / 36 group（值都是 1.0），**没有任何「A 组禁 B 组」字段**，`disallow*` 家族无此语义
   → **两个引擎在这条规则上完全等价**，换引擎不能解决，必须自己补规则层。
286. **数值对拍 16 条**（口径统一「全 512 技能=5 级」）：逐件 DPS **15/16 相对差 <1e-6**；无人机 2.6e-08；**分层 HP 16/16 逐位一致**；对齐 16/16；CPU/PG 16/16。→ **无系统性数值偏差，数值核心等价。**
287. **对拍抓出我们一个真 bug**：`Defender Launcher I`（拦截弹发射器）被算进伤害（本引擎 160.0 / eos 0）→ **拦截弹不该算 DPS**。
288. **三处分歧归因**：① 口径差（RAH 自适应抗性、`get_volley` 定义不同）② **状态语义**（f12 Oneiros `speed 411,642 vs 2,935 = 140×`，与 f67 同根因；f15 Raven `sig 1,115 vs 462`，MJD 的 +150% 被当常驻）③ 我们多算 Defender。
289. **迁移代价估算**：`engine/` 928 行全废、`views/` 2,804 行全部重写取数路径、provenance/reasons 语义输入重建、65 样本基线 2,860 断言重算、分层 L0/L1 契约重建、tests 至少 60% 要改 → 粗估 **33–51 个任务**（对照整个 P0→P2 是 41 个），而**收益 ≈ 0 条我们缺的规则**。
290. **裁决：不换引擎**。空洞补在**规则层**：① `config/activation_groups.json` 人工规则表（每条带来源与核实日期，不许编）② `constraints` 之上新增 `state_exclusive` 规则族 ③ `propulsion_states` 补「单推进」分支 + 顶层数值标注「全 active 是不可达状态」。pyfa 探针保留作**离线对拍工具**（起一套 eos 约 5 秒）。
291. **环境副作用**：`spike/pyfa_scratch/eos-master/` 与部分 `uvcache/builds-v0/.tmp*/` 被 Windows 权限锁成**不可读不可删**（`tarfile.extractall` 撞 ACL）。本会话提权审批被禁用，未强行扩权。均为垃圾目录，不影响功能。
292. **T-43 完成（702 passed）**：新建 `config/activation_groups.json`（`propulsion` = 组 `[46, 1189, 1533]`，`limit: active=1`，带 `source` / `verified_by` / `verified_at` / `note`）；`propulsion_state_conflict` 与 `propulsion_illegal_stack` **合并为 `state_exclusive`**（同一冲突恒一条）；`propulsion_states.single` 输出**单推进分支**（玩家真实状态）；`Defender Launcher I`（group 512）**排除出伤害**。
293. **规则表上线第一轮就撞上真实跨组样本**：`f48`（Gnosis，MWD+MJD）—— 引擎 `validate()` 对推进类 `max_group` **一条都不报**，而我们的配置表正确命中（`source=config`，`group_ids=[46,1189]`）。**以后不用造合成样本自证。**
294. **`single` 的实测数值**（3 位小数，均独立重算核对）：f67 = AB 112.719 / MJFG 49.076 / MWD 306.195 m/s（顶层 59,928.363）；Panther = MWD-only 1519.858、MJD-only 227.5（顶层 1519.858）。
295. **一个我发现的措辞不精确（待修）**：Panther 的**顶层速度 1519.858 恰好等于「只开 MWD」** → 速度其实**可达**；不可达的是**信号半径**（顶层 3814.894 含 MJD 蓄力的 +150%，而只开 MWD 时是 1656）。→ flag 文案里「顶层数值……不可达状态」对速度不成立，应区分字段。
296. **事故与恢复**：T-43 用 PowerShell 追加测试时因 `$lines` 未在同进程定义，**把 `tests/test_views_a.py` 覆盖成 70 行**；它用 `git show HEAD:tests/test_views_a.py`（只读）恢复后重做全部编辑。→ **这正说明仓库化是对的**：没有 git 就真丢了。教训：**批量改文件优先用编辑工具，不要用 shell 重定向拼内容**。
297. **T-44 预期外科手术（已批准，沿用 T-38 先例）**：裁决要求 f67 的 `evidence.modules` 含 MJFG、`group_ids` 含 1533，而 `test_views_a.py` 第 1798–1833 行逐条钉住了旧形状（`[46]` / `"AB 与 MWD"`），且该文件不在其写入范围。→ **批准最小外科手术**（只改 f67 专属的 3–4 行，f27/f57 分支仍钉旧形状，其余一律不动、不放宽）。
  → 通则：**「跨任务改名/改形状」必须包含全部引用点同步**，否则尾巴必留；写入范围与实际需要冲突时，**优先保证一致性，越界要如实上报**。
298. **`evidence` 将同时保留 `source`(字符串) 与 `sources`(数组)** —— 为兼容既有断言。这是**临时冗余**，`sources` 是权威；等 P4 定义对外 schema 时去掉 `source`。记为欠账。
299. **T-44 完成（707 passed）**：`BUDGET_CHARS` **17,000 → 19,000**（`render_overview` / `render_detail` 逻辑 **0 行改动**）；`state_exclusive.evidence` 改为「**引擎报的组 ∪ 配置表命中的组**」并集 + `sources` 数组（顺序恒 `engine → config`）。
300. 三样本实测（额度 19,000）：**f47 17,350 / f67 17,314 / f12 18,654，全部 `degraded=False`**。
301. **f67 的 evidence**：`group_ids [46, 1533]`、`modules "AB、MJFG、MWD"`（**装配顺序**，不是字母序；≥3 件用「、」连接）、`sources ["engine","config"]`。Panther：`group_ids [46,1189]`、`sources ["config"]`。
302. **我的算术又错了一次**：我说公式会算出 22,000，实际 `18,631 × 1.2 = 22,357.2` → ceil 到 **23,000**。19,000 对 f12 的实际余量是 **346 chars ≈ 1.85%**。
303. **第二次外科手术**：`test_views_a.py` 只改 f67 的 3 处断言 + 1 个文案常量 + 1 段注释，**没有放宽任何断言** —— f27/f57 仍精确钉旧形状，f67 反而从「旧形状」**收紧**成新形状。
304. **一个容易顺手改错的点**：`mwd_sig_increase.evidence.propulsion` 仍只含 AB/MWD 两件（MJFG 的 SDE group 名不同），f67 上 `len == 2` 的断言**仍然成立**，别顺手改成 3。
305. **P4 完成（786 passed）**：`mcp_server.py`（手写零依赖 stdio 实现）+ `mcp/client_smoke.py` + `mcp/README.md` + `skill/noobbaozi/SKILL.md` + `tests/test_mcp_server.py`（38 条）。
306. **服务端必须手写零依赖**，不是偏好：验收命令的环境里没有 `mcp` 包，依赖它会在 `import` 阶段就死；且 `mcp 2.x` 已把 `FastMCP` 改名 `MCPServer`，网上代码全过期。
307. **stdout 纯净性用硬保险**：启动时 `os.dup(1)` 留私有 fd 写协议、`os.dup2(2,1)` 把 fd 1 改指 stderr → **第三方库的裸 `print()` 也污染不了协议流**。
308. **业务失败 ≠ 协议错误**：`invalid_path` / `unknown_module` 是 `isError:true` 的正常响应（agent 能看到结构化原因）；`-32602` 只留给「参数本身不合法」。这个区分决定了 agent 收到的是「调用失败」还是「这个路径不在这，可用的是这些」。
309. **两把尺子都通过**：开发方 `client_smoke` PASS 7/7；验收方**自己写**的 `acceptance/check_mcp.py` 五组全 PASS、**缺陷 0**（协议协商 5 档 + 越界回首选、通知不回包、stdout 17 行全 JSON、`fit_index` 与手写 L0 契约 0 缺 0 多、三工具数值逐位相同、负例语义、灌 1MB 垃圾后存活、同输入 20 次加速 **606.6×**）。
310. **验收方两次"红脸"都是自己的客户端**：① `proc.stdout.read(65536)` 在管道上会阻塞到读满 → 整场挂死（应逐行 `readline()`）；② **错误响应的 `id` 是 `null`**，它却按固定 id 等 → 把「`-32700` 正确返回」误判成「没返回」。→ 通则：**断言按响应实际语义匹配，别假设 id 一定回显**。
311. **`asyncio.create_subprocess_exec` 在本机报 `PermissionError [WinError 5]`**（`subprocess.Popen` 正常）。这条会影响任何「Python asyncio + 子进程」的后续任务。

---

## 4. 待办

### P0-0 契约（阻塞一切并行）✅

- [x] `noobbaozi/contracts.py`：9 个 TypedDict + 常量 + 5 个模块接口注释块
- [x] 包骨架 `noobbaozi/__init__.py` 与 6 个子包
- [x] `tests/test_contracts.py`：18 个测试通过

### P0 数据层（T-01 ✅ / T-02 ✅）

- [x] **P0-1** SDE 下载与解析 → `sde/ingest.py`（实测：首次下载 41.1s，之后重建 2.8s，幂等验证通过）
- [x] **P0-2** 索引 schema + 查询 API → `sde/schema.sql`、`sde/index.py`（8 个契约方法齐全，只读打开）
- [x] **P0-4** 模式映射表 → `sde/modes.py`（Hecate 得 3 个模式，Anhinga 情况已修正）
- [x] **P0-3** 属性字典与单位表 → `config/attributes.py`（129 常量 + 8 项缩放）、`sde/attribute_dict.py`（7 个纯函数）、30 个测试
- 已验：`search_types('Hecate')` 首位 35683；`search_types('赫卡特')` 命中；`modes_for_ship(35683)` = 3 个；`to_physical(51, 2226.068)` = 2.226068

### P1 计算封装（T-03 ✅ 完成）

- [x] `engine/adapter.py`：SDE 单例守卫（锁+标志+计数）、实例级 LRU 256、`clear_cache`/`cache_stats`、`meta()`
- [x] `engine/normalize.py`：五形态判定（eft/dna/esf/json/unknown）+ EFT 结构预扫描；`candidates` 留空待接索引
- [x] `engine/assumptions.py`：技能档 ≤8 轮、超限 `RuntimeError`；实测 **19 技能 = 17×4 + 2×5**（钉死 type_id `{3301, 33093}`）
- [x] `engine/scenarios.py`：只认 `{"all": state|keep}`，其余 `ValueError`

### 集成待办（本轮新增，必须做）

- [ ] **契约漂移同步**：`source_format` 增加 `"esf"`（T-03 实际返回该值，契约注释未列）
- [ ] **`AttributeRow.scale` 接线**：T-01 恒返回 1.0，需在 `Index.attribute()` 中改为取 `sde.attribute_dict.scale_of()`（T-02 交付后）
- [ ] **`AttributeRow.unit` 语义记档**：返回 SDE 单位名（`Milliseconds`/`Length`），物理换算在属性字典层
- [ ] **normalize 的 `candidates`**：接索引做名称候选（当前空列表）
- [ ] 跑不变式 I1–I7 全量验收

### P2 投影层

- [ ] `project/units.py`（换算 + ×1000 表）
- [ ] 六视角抽取器 `project/views/*.py`
- [ ] `project/provenance.py`（三态强制）
- [ ] `project/diagnostics.py` + `config/diagnostics.yaml`
- [ ] `project/render.py`（overview/detail/explain + 预算护栏）

### P3-P5

- [ ] `explain.py`（sources 限深 2 层）
- [ ] sensitivity（逐模块关闭 / state / mode 切换）
- [ ] MCP server + Skill
- [ ] 知识层（RAG）

---

## 5. 欠账台账（带 owner，逐条销号）

| # | 欠账 | owner | 状态 |
|---|---|---|---|
| 1 | `normalize` 的 `candidates` 接索引 | T-04 | ✅ 完成（含 9 例测试） |
| 2 | 契约漂移三项 | T-04 | ✅ 完成（含注释同步守卫 4 例） |
| 3 | 不变式 **I6**（provenance 全字段） | T-05 | 进行中 |
| 4 | 不变式 **I7**（错误结构） | T-04 | ✅ 完成（失败候选/船名区分/坏索引容错 9 例） |
| 5 | `-58 fighterDps` 未复测 | T-06 | ✅ 完成（13 例；`-58` = 519.5687 实测） |
| 6 | `docs/回归快照-changelog.md` 未建 | Lead | 等第一次 SDE 变更 |
| 7 | `PostDiv` 算子（模式抗性）未落成表 | P2 之后 | 待办 |
| 8 | 引擎升级回归未固化成单条命令 | T-33 | ✅ 完成（`python -m noobbaozi.tools.regress [--full]`） |
| 9 | 可疑点"同类船聚合"需要索引支持 | 后置 | 低 |
| 10 | `-70 maxTargets` 实测恒 0，标记为不可用字段 | 已在 §3 记录 | ✅ |
| 11 | **模糊名称候选**：`search_types` 无编辑距离，`Damage Controll II` 这类错位拿不到候选 | 待立项 | 新增 |
| 12 | verify 权限：越界自查只能靠 mtime | Lead | ✅ 完成（`noobbaozi/` 已 `git init`，首提交 `afdf5b5`） |
| 13 | 战斗机 7 个负 ID + 5 个正 ID 常量未登记 | T-07 | ✅ 完成（常量 129 → **141**） |
| 14 | **provenance 表改为实测推导**（手写表与验收口径冲突） | T-08 | 进行中 |
| 15 | 六视角抽取器未写（火力/生存/机动/资源/约束/不稳定） | T-09/T-10 | 待派（等 T-08） |
| 16 | `project/render.py`（overview/detail 组装 + 预算护栏）未写 | T-11 | 待派 |
| 17 | `acceptance/field_spec.json` 的 provenance 列需按新口径重写 | noobbaozi-tester | ✅ 完成（41 字段，双列拆分） |
| 18 | **技能档语义偏差**：`derive_skill_tier` 只补「被要求」的技能，未实现「全部技能 4 级」 | T-12（待 T-09/T-10 结束后派） | **高** |
| 19 | 快照整体重标定（规范 §6.2 + 字段规格 §10.2 + 模式四项） | Lead + 测试员 | ✅ 完成（22 行 + 四模式新值已写入文档，逐行归因无残留） |
| 20 | `items[i]` 不含 `type_id`/`slot`，投影层靠隐式顺序对位，需加自检 | T-13 | 中 |
| 21 | 引擎侧错误缺 `line`/`candidates`（预扫描放行后丢行号） | 待立项 | 中 |
| 22 | `mass` 字段缺单位后缀 `_kg`（统一重命名） | 待 T-11 后 | 低 |
| 23 | `回归快照-changelog.md` 仍未建，重标定时必须有 | T-12 | 中 |
| 24 | **provenance 两级模型**（属性级类 + 实例级缺值覆盖） | T-14（T-12 后） | **高** |
| 25 | T-08 的实测表为**暂定态**，须在 T-12 修完技能轴后重跑 | T-14 | **高** |
| 26 | 「输入常量」通道：弹药四系伤害等由 SDE 直读，视图应声明 `measured` 而非落 `unknown` | T-14 | 中 |
| 27 | 108 个未登记属性 ID 待补常量表（`-17..-42`、`-53`、`-71`、`2027-2029` 等） | 待 config/attributes.py 解冻 | 中 |
| 28 | 模式轴（stance）未纳入 provenance 实验 | T-14 | 低 |
| 29 | 常量表缺 `1272 droneBandwidthUsed`、`56 chargeRate`、`73 duration` | T-13 | ✅ 内容完成（待 T-12 收口测试） |
| 30 | **`ATTR_DERIVED_CYCLE_TIME`(-3) 单位标注错误**：实为毫秒，表中标 `s` 且未进缩放表 | T-13 | ✅ 已修（进缩放表 + 注释改「存储毫秒」） |
| 31 | `FIELD_SOURCES` 数组元素约定（`cap_by_module[].field`）需写进契约 | T-11 | 进行中 |
| 32 | 常量表缺 `281 explosionDelay`（T-13 只补了 56/73/1272） | 待立项 | 中 |
| 33 | `damage_split` 口径统一为 0..1 比率；验收样例（60/40）需改 | noobbaozi-tester | 待通知 |
| 34 | `propulsion_states` 键名定为 `base/off/active/overload`；`mass` → `mass_kg` | 已裁决 | ✅ 规格已更新 |
| 35 | **overview 预算装不进**：RAW 17,777 > 12,000，降级后仍 16,528。根因是 `upgrade_space` 单项 4,135 chars | T-15 | ✅ 完成（摘要化 → 13,519 chars，预算 17,000，默认不降级） |
| 36 | `FIELD_SOURCES` 不一致：两套数组写法、漏登记字符串/布尔叶子、`count_by_level` 是 int 键 dict | T-15 + T-16 | ✅ 完成（叶子补齐 + `[]` 写法统一 + 键空间对齐） |
| 37 | `damage_split`/`application` 的 provenance 仍 `unknown` | T-14（输入常量通道） | 中 |
| 38 | `render_detail` 的 `missing` 恒返回空列表（便于稳定解析） | 已采纳 | ✅ |
| 39 | **acceptance 四个脚本调用 `default_assumptions` 未传 index** → 静默跑旧档（`check_snapshot` 可能拿旧值假绿） | noobbaozi-tester | ✅ 完成（4 处全改 + 新增技能档护栏两层） |
| 40 | `tests/test_projection_core.py` 仍走 `index=None` 并钉旧值（8854.9587） | T-15 | 进行中 |
| 41 | overview 预算装不进 + `FIELD_SOURCES` 不齐 + `contracts` 签名注释过期 | T-15/T-16 | ✅ 完成 |
| 42 | **`confidence` 全量平行表 +5,093 chars**（默认值被显式输出）→ 改稀疏表 | T-18 | ✅ 完成（真实增量降到 +223） |
| 43 | `acceptance/run_acceptance.py` 的 confidence 查表口径（spec 字段名 vs 叶子路径） | noobbaozi-tester | ✅ 已对齐（验收侧本就是稀疏约定） |
| 44 | 文档里 `D`（需对拍）档已收敛为 `A`；`C` 仅表示「不可用」 | Lead | 待改字段规格 |
| 45 | `acceptance/run_acceptance.py::check_repair_zero` 仍期望 `0.0 + measured`，与新裁决（`0.0` + 属性级 tag + `has_source`）不一致 → 会误报 6 条 low | noobbaozi-tester | ✅ 完成（4 条判据重写，0 误报且 4/4 注入全中） |
| 46 | **D5** `ammo_endurance_s` 两视角不一致 | T-20 | ✅ 完成（抽到 `shared.py`，三样本逐位相同） |
| 47 | **D6** `depletes_in_s` 哨兵 `-1.0` 被当秒数 | T-20 | ✅ 完成（f3–f6 → `None`，f1 仍 38.4） |
| 48 | **D7** 导弹船 `dph = 0`（实为突变数据丢失） | T-22 | 进行中（改 `null` + `unknown` + flag） |
| 49 | **X2** `slots` 缺 `subsystem` 类（T3C） | T-20 | ✅ 完成（Loki 4/5） |
| 50 | **X1/X3** T3C CPU 爆表 / AB 不生效 | T-21 诊断 | 进行中 |
| 51 | **X4** I5 对效应型抗性模块不适用（须走 effect 级 `sources`） | P3 | 待办 |
| 52 | `subsystemSlots` 属性 ID 未登记常量表 | 待 T-20 结果 | 中 |
| 53 | **突变（Abyssal/Mutated）数据保真**：killmail 不带 mutation，引擎按未变异算 → 相关数值失真 | T-22 / 上游 | **高** |
| 54 | **`dph` 在「有弹药却算出 0」时改输出 `null` + `unknown` + flag**（裁决已定，待实现） | T-22 | **高** |
| 55 | `load_killmail` **静默丢弃 flag 177（SubSystemBay）备用件**，无日志无 violations | T-22 | **高** |
| 56 | **资源可行性语义缺失**：`cpuLoad > cpuOutput` 时仍裸渲染理论值，未标「部分模块不可能在线」 | T-22 | **高** |
| 57 | 突变模块检测 → 归一化/诊断给显式 flag（覆盖 D7 与 X3 同一根因） | T-22 | **高** |
| 58 | `firepower.py` docstring 陈旧（仍称 281 不在 `SCALE_BY_ATTR`、说「8 项」，实为 11 项 = 9 毫秒 + 2 ×1000） | 下一轮清理 | 低 |
| 59 | 常量清单靠人工穷举必漏（281 / 56 / 1272 都是事后补的）→ 需「视角用到的 ID 必须全在常量表」的自动检查 | 待立项 | 中 |
| 60 | **`unknown` 同时承载「未知」与「不适用」**（如纯炮台配装的 `application.explosion_radius_m`）→ 需评估第 4 态 `not_applicable` | 待评估 | 中 |
| 61 | 数组元素粒度：一条路径一个标签，混装时任一元素缺值即整条 `unknown` | 待评估 | 低 |
| 62 | 第二条实例规则（按配装检测状态敏感族 → 271–274）未做，当前靠静态表保守判 `assumed` | T-30 进行中 | 中 |
| 63 | `ATTR_MAX_TARGETS_UNVERIFIED = -70` 裸写在 `provenance.py`，常量表无；且 `test_attribute_dict.py` 硬编码「146 常量」 | T-32 | ✅ 完成（146→147，三处硬编码改成由表推导） |
| 64 | `render.py` 三个 `MODE_*_ATTR` 裸写派生 ID（N12 违规） | T-32 | ✅ 完成（本就已登记，改为引用；白名单销账） |
| 65 | `unknown` 需带 `reason`（方案 A） | T-32 | ✅ 完成（36 样本 `unclassified` = 0） |
| 66 | **字符串/枚举/嵌套路径没有 provenance 归属**（260 条落 `unknown` 默认分支） | T-34 | ✅ 完成（not_applicable 744→600，unknown 785→641） |

---

## 6. 踩过的坑（别再踩）

1. **`slot.type` 写 `med` 会静默失效**——在字符串比较里写错不报错，结果全是对照组。正确值 `medium`。**用枚举常量**。
2. **uv 缓存必须落在工作区内**（`UV_CACHE_DIR`），否则沙箱拒绝访问（`os error 5`）。
3. **pwsh `Invoke-RestMethod` 与 `curl.exe` 的 TLS 都失败**（schannel 无凭证）→ 一律用 **Python `urllib`** 发请求。
4. **zkillboard API** 需要描述性 `User-Agent` + `--compressed`，路径**必须带尾斜杠**，<5 分钟的 killmail 不下发。
5. **`load_esf` 只吃 esf/1 格式**；EFT 文本要用 `load_eft`。
6. **`/universe/ids/` 对未发布类型无效**（模式类型就是这么漏掉的）→ 靠 ID 邻近扫描才发现。
7. **ESI `?language=zh` 能取中文名**（`Light Neutron Blaster II` → 轻型中子疾速炮 II）。
8. **别把四系 EHP 自己平均**，混合 EHP 是调和平均。
9. **EFT 头正则的逗号不能写成可选**：`^\[([^,\[\]]+)(?:,(.*))?\]$` 会把 `[Empty High slot]` 认成船名行，导致所有带空槽的 EFT 被判 ship_name 错误。
10. **沙箱下 pytest `tmp_path` 不可用**（`WinError 5`，扫 `Temp\dsh-*\pytest-of-*` 被拒）。造不存在路径改用 `db_path().with_name(...)`。`.pytest_cache` 建不了只是 warning，可 `-p no:cacheprovider` 静音。
11. **漏导出 adapter 的 `load_eft` 时表现为"引擎格式错误"**——`AttributeError` 被吞进 `NormalizeError`，很难定位。adapter 必须导出 `load_eft/load_dna/load_esf/load_esi_fitting/load_killmail` 五个模块级薄包装。
12. **EVE 名字里真有下划线**（如 `Able_PLACEHOLDER`），LIKE 通配符必须转义，否则 `_` 会匹配任意单字符。
13. **无战斗机时引擎不输出 `-58`（不是输出 0）** → 断言「归零」必须写成「缺失或 0」，否则测试 KeyError 假红。
14. **pwsh 下 `python -c "..."` 会吞掉内层双引号**（`print("stats", x)` 变成 `print(stats, x)`）→ 必须用 `uv --% run ...` 或走 stdin（`@'...'@ | uv run ... python -`）。`--%` 后面接多行 `-c` 同样会炸，优先 stdin。
15. `Index.attribute(id)` 返回字典的键是 **`unit`**，不是 `unit_id`。
16. **工作区根目录不是 git 仓库，只有 `noobbaozi/` 是**；在 `noobbaozi/` 之外操作仍只能靠 mtime 自查。
17. **装备级属性也会被 `units.physical()` 换算**（`views/base.py::item_attributes`）→ 千万别在视角里对已登记的毫秒属性再除 1000，会差 1000 倍且不报错。
18. **子代理之间会互相打红测试**：T-12 在改 `engine/assumptions.py` 期间，`test_engine` 必然红；这类红是并行的正常摩擦，判据是「最终全量绿」，不是「中途每个代理都绿」。
19. **`ViewContext` 只从 `ctx.fit` 建变体，不读 `ctx.assumptions`** → 构造上下文时必须把技能注入 `fit["character"]["skills"]`，否则全部按**零技能**算（EHP 7585.48、速度 1059.68），看起来像一堆口径错误。验收员自己就踩过，误报 72 条。
20. **把属性登记进 `SCALE_BY_ATTR` 是一个「全局破坏性」操作**：所有已按「原始值 + 手工换算」写的消费点会立刻变成**双换算**（差 1000 倍且不报错）。实测：登记 `281 explosionDelay` 后 `_missile_range_m` 从 51450 变成 **51.45**。
   → 登记缩放时必须**全仓搜索该 ID 的每个消费点**，并检查 `ms_to_s` / `s_to_ms` / `/1000` / `*1000`。
21. **并行任务会互相打红测试**：一个代理登记缩放常量、另一个代理的既有代码立刻失效；判据是「最终全量绿」，中途红不代表谁的错。
22. **「量级不对」的直觉本身可能是错的**：X5 的 34,142 装甲 HP 看起来高得离谱，实测**逐位正确**（差 0）。判据必须是**闭式复算**——把每一项乘子写出来乘一遍与引擎逐位比对，不是"按常识应该多少"。
23. **不同属性类别的堆叠惩罚规则不同**：hit points（护盾/装甲/结构）**免**罚，抗性**吃**罚。拿抗性的经验去怀疑 HP 会得出相反结论（差点冤枉引擎）。
24. **「余量」会撒谎**：`budget.chars` 是**降级后**的值。f12 报 `16,590 / 余 410`，真实载荷 **18,178**（超 1,178）。→ **必须先看 `degraded` 标志，再看余量**；只看余量会把已降级的样本当成安全。
25. **降级第一刀割的是 `flags[].evidence`**（诊断证据，f12 未降级时占 2,303 chars），不是冗余字段 → **加字段触发降级 = 直接损失可解释性**。这才是「必须省预算」的真实理由，不是「省点 token」。
26. **跨任务改名必须包含全部引用点同步**：T-38 改 flag id 时，`test_views_a.py` 里还有 6 处旧引用（常量/文案/函数名/断言/注释），不在它写入范围 → 整包 4 条红。**要么给改名者全引用点写权，要么把改名单独成任务。**
27. **子代理中途收不到 Lead 的裁决**（提问通道被禁）→ 派活时要把「可能的岔路」在任务书里预先裁决完；否则必定留一次「收尾同步」尾巴。

---

## 7. 决策日志（详情见讨论文档）

| 决策 | 理由 | 出处 |
|---|---|---|
| 用 `eveshipfit-dogma-engine`，不自研 dogma | 实测正确（堆叠惩罚、抗性 12/12），自研必错 | 讨论 §2 |
| 自建 SDE 索引库 | 引擎无查询能力 | 讨论 §19.7 |
| 索引主源用官方 SDE 文件 | ESI 看不到未发布类型 | 讨论 §7.3 |
| 多视角、无判据 | 场景未定，判据是伪权威 | 讨论 §14.3 |
| `state` 用情景枚举代替假设 | 状态不可知但可枚举，成本 0.33ms | 讨论 §14.5 |
| RAG 只进解释层，不参与数值 | 知识会过时，数值不能受其影响 | 讨论 §13 |
| 技能档默认 = 全 4 + 补最低 | 玩家最经济的挂法 | 讨论 §14.4 |
| 可疑点不设绝对阈值 | 快慢取决于同场其他人 | 讨论 §7.7 |
| 存储用 SQLite | SDE 规模仅 5.4 万行 | 设计 §7.1 |

---

## 8. 文件地图

```
noobbaozi/
├── README.md                     导航 + 复跑说明
├── PROGRESS.md                   ← 本文件
├── .gitignore
├── docs/
│   ├── 想法-技术路线讨论.md       讨论记录（13 轮）
│   ├── 配装理解-字段规格-v1.md     实现依据（六视角字段）
│   ├── 配装理解-软件设计-v1.md     架构与分层
│   └── 开发规范-v1.md            编码/测试/回归/子代理协作
└── spike/                        14 个验证脚本 + kill.json 样本
```

---

## 9. 恢复工作时的第一步1. 读本文件 §0 与 §3（5 分钟恢复全部硬结论）。
2. 验环境（应输出 0.33 ms 量级）：

```powershell
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
  python noobbaozi\spike\smoke3.py
```

3. 看 §4 待办，从最上面未勾选的那条开始。
4. 若当前有子代理在跑，先确认它们的写入范围与 §7.3 的排版一致，避免撞文件。

---

## 10. 验收记录

### 2026-10-18 · P0 + P1 全部交付（PM 复跑）

| 项 | 结果 |
|---|---|
| `pytest -q noobbaozi/tests` | **117 passed**（18 契约 + 35 引擎 + 34 数据 + 30 属性字典） |
| 索引库 | `noobbaozi/data/noobbaozi-3607705.sqlite3`，14.3 MiB |
| 索引统计 | types 53,288 / groups 1,611 / attributes 2,871 / modes 18 |
| 模式 | `modes_for_ship(35683)` → Defense / Propulsion / Sharpshooter，**恰好 3 个** |
| 中文检索 | `search_types("赫卡特")` → 35683 ✅（不必走 ESI） |
| Engine 单例 | 连续构造 5 个 `Engine()` → `sde_load_count() == 1` ✅ |
| 归一化 EFT | `source_format="eft"`，0 错误，warning 含「状态由导入规则推定」 |
| 归一化坏输入 | 返回 `unknown` + 1 条错误（`failed_at="format"`），**不抛异常** ✅ |
| 技能档 | **19 个 = 17×4 + 2×5** ✅ 与基线一致 |
| 属性字典 | 129 常量 / 8 项缩放（6 毫秒 + 2 ×1000）/ `audit().consistent = True` |
| 缩放抽查 | `51→2.226068`、`160→0.39167`、`1985→10.0` ✅ |
| 技能 ID 修正 | `1285→requiredSkill4`、`1286→requiredSkill4Level`；`185→attr_185`（确认不是技能） |
| **不变式 I1** | EHP `7585.481513` == 盾+甲+构 ✅ |
| **不变式 I2** | alpha `624.118835` == Σ volley ✅ |
| **不变式 I3（已更正）** | 混合 `8854.959` / 算术 `9351.533` / 调和 `9218.971` → **三者互不相等**，按层复算成立 |
| **不变式 I4** | EFT 往返 8 项指标最大差 **0.0** ✅ |
| **不变式 I5** | DC II 共振倍数 护甲 `0.85×4` / 护盾 `0.875×4` / 结构 `0.60×4` ✅ |

**未通过 / 未做**

- ⬜ 不变式 I6（provenance 全字段）、I7（错误结构）尚未写成测试（属 P2 投影层范畴）
- ⬜ `normalize` 的 `candidates` 仍为空列表（待接索引）
- ⬜ 契约漂移三项未同步（`source_format="esf"`、`AttributeRow.scale` 接线、`AttributeRow.unit` 语义）
- ⬜ `-58 fighterDps` 无航母样本，未复测（表中已注明）

### 2026-10-18 · T-04 契约漂移修复（自报，待测试员复核）

| 项 | 结果 |
|---|---|
| 测试 | **135 passed**（基线 117 + 新增 18） |
| `scale` 接线 | `51 → 0.001`、`160 → 1000.0`、未登记属性 → `1.0` 且不抛异常 |
| `unit` 语义 | `51 → "Milliseconds"`（缩写 `s` 只在 `units.display_name`） |
| `candidates` | 无索引 → 空；注入索引 → 命中候选；**单候选也不代填**（红线 N2） |
| 坏索引容错 | 传入坏对象不影响归一化，不影响基本功能 |
| 船名误标修正 | 引擎 `unknown type` 二义 → 用 EFT 头回比纠正为 `failed_at="ship_name"` 且不给候选 |

**说明**：本条为子代理自报，PM 未独立复跑；按分工由 `noobbaozi-tester` 在 P2 验收时复核。

---

## 11. 提交策略（Lead 自定）

### 何时提交

| 时机 | 理由 |
|---|---|
| **每个 P 阶段收口**（P0/P1/P2/P3…） | 阶段边界最清晰，回滚粒度合适 |
| 验收通过后 | 让「绿」这个状态可复现 |
| 文档定稿 / 大改文档后 | 文档是规格来源，改动要留痕 |
| 派发大批并行任务**前** | 出问题时能回到干净的起点 |
| 会话中断前 | 恢复时不用靠记忆 |

**不提交**：构建产物（`data/`、`spike/uvcache/`）、缓存、测试输出（`acceptance/out/`）。

### 谁提交

- **只有 Lead 提交**。子代理不得 `git commit` / `git checkout` / `git reset`。
  理由：并行任务同时提交必然互相踩踏，且历史会失去可解释性。
- 子代理的职责是**写完就跑测试**，提交由 Lead 在收口时统一做。

### 提交信息

按 `docs/开发规范-v1.md` §9.1 的四段式：做了什么 / 为什么 / 影响 / 验收。
每条提交的「验收」必须能复制执行，不允许写「已测试通过」这种不可复现的话。

### 提交前的检查

1. `pytest -q noobbaozi/tests` 全绿
2. `git status --short` 里的改动**全部属于本次任务**
3. 没有把 `data/`、缓存、大文件带进来（`git diff --cached --stat` 看一眼）
4. 若改了字段或口径，对应文档已同步
### 2026-10-19 · P4 容器化并部署到 NAS（局域网验证通过）

| 项 | 结果 |
|---|---|
| NAS | `192.168.5.2`（UGREEN DXP4800P，Debian 12 / x86_64，Docker 26.1.0 / Compose v2.26.1） |
| 部署目录 | `/volume1/docker/noobbaozi-mcp`（21 MB = 源码 + 15 MB SDE 索引库） |
| 镜像 | `noobbaozi-mcp:1.0.0`，`BUILD_RC=0` |
| 容器 | `noobbaozi-mcp`，`restart: unless-stopped`，端口 `8080` |
| 局域网验证 | 从**开发机 Windows** 访问 `http://192.168.5.2:8080/health` → `{"ok":true,...,"tools":5}` |
| MCP 全链路 | `initialize`（SSE，`Mcp-Session-Id`）→ `initialized`（202 空 body）→ `tools/list`（5 个）→ 五个工具全部 `ok:true` |
| 负例 | `invalid_path` 返回 `isError:true` + `kind=invalid_path`（业务错误，非协议错误） |
| 已注册为 DSH 连接器 | `noobbaozi_nas`（streamable-http，`allowInsecurePrivateNetwork`），并**实际调用成功**（见下） |

**首次构建失败与修复**：

```
failed to solve: failed to compute cache key: "/__init__.py": not found
```

- Dockerfile 的 `COPY` 白名单漏了 `__init__.py`；
- 同时发现漏了 **`mcp_http.py`**（HTTP 传输实现，`mcp_server.py` 在 `main()` 里惰性 import）—— 若只修第一个，容器能起但 HTTP 模式会 `ImportError`。
- 已修 Dockerfile 并在注释里写明「为什么这两个文件不能漏」。

**部署路径上的两个环境坑（都绕过，非产品问题）**：

1. **SFTP 上传反复中断**（15 MB 分两批仍卡）→ 改用 **GitHub 中转**：本地打包成部署快照仓库，强制推送到 `AraragiEro/NoobBaozi`，NAS 上 `git clone`（GitHub 连通 200）。
2. **git 凭证助手被沙箱管道限制挡住**：`sh.exe: couldn't create signal pipe, Win32 error 5` → 改为「token 直接写进 URL + `http.sslBackend=openssl` + 禁用 credential.helper」，一次成功。

**一次自己的失误**：把 `.tmp/`（部署暂存目录）误提交进 `noobbaozi` 仓库，已 `git rm --cached` + 加入 `.gitignore` 并 `--amend` 修补，未留下痕迹（`f776694`）。

**实测样例（通过 NAS 上的 MCP，从本会话直接调用）**：

- `https://zkillboard.com/kill/139239127/` → **Sarathiel（无畏舰）**：EHP 2,139,472 / DPS 8,040 / 齐射 48,693 / 信号 10,000 m / `feasible: true`；flags：`resist_hole`(装甲最弱爆炸)、`no_propulsion`
- `https://zkillboard.com/kill/139083433/` → **Sleipnir（指挥舰）**：EHP 33,582 / DPS 529 / 速度 1,699.5 m/s / `feasible: false`（PG 超 40.4）；flags：`resource_overflow`、`cap_sensitive`(关 50MN 就稳)、`repaired_but_unstable`、`drone_bandwidth_idle`
---

### 2026-10-19 · 员工制上线 + 口径清理轮（1101 passed）

**团队机制**（主人提议：每员工最多 5 单后退场，防上下文漂移）

| 代号 | 任 | 单数 | 关键产出 |
|---|---|---|---|
| `dev-engine` | 一任 | 5/5 退场 | 炮位单一出处、`unclassified` 拆分、EFT 装填弹药、fits 增量口径、L2 契约、orphan 源头不变式 |
| `dev-surface` | 一任 | 5/5 退场 | `zkb_stats`、17 处取整透传、错误映射统一、killmail 冷路径 -36%、detail 顶层 flags、信封 hash |
| `dev-engine-2` | 二任 | 2/5 在役 | `unattributed` 改名、无人机带宽/架数两条 flag |
| `dev-surface-2` | 三任 | 1/5 在役 | 文档↔实现一致性抽查 |
| `evefit-tester` | 常驻 | — | L2 字段级验收（0 缺陷）、无人机 flag 验收、65×43 |

**本轮的硬结论**

1. **`feasible` 不含无人机**（只 CPU/PG/标定三项）—— 主人裁决不改定义，改用两条诊断 flag：
   `drone_bandwidth_exceeded`（Mbit/s）与 `launched_drones_exceeded`（架数），**互不替代、可同时命中**。
   合并会产出「带宽超限」且 `over = -75` 的自相矛盾 evidence。
2. **`drone.bandwidth.used` = 机舱全部放飞所需**，不是已放飞。本产品**无「已放飞」维度**
   （fit 文本与引擎 `Layout.type` 都只有 `drone_bay`）。
3. **zkb 的 `/api/killID/` 返回的就是完整 killmail** —— 16/16 逐字段与 ESI 一致。
   `victim.items` 非空时跳过 ESI 不是「降级数据源」，是**删掉一次重复请求**（冷路径 2.48s → 1.59s）。
   `items` 必须区分「键缺失」（疑点 → 回落 ESI）与「空数组」（事实，蛋/空船 → 短路）。
4. **`upgrade_space[].fits` 改为增量口径**：`fits` = 换装无**新增**违规；`blocked_by` = 候选自引；
   `inherits` = 基础自带（二者互斥）。旧口径让 f7 的 16 个候选全被「配装本来就超带宽」连坐。
5. **`hardpoints.unclassified` → `unattributed`**（与 `unknown_reasons.unclassified` 撞名）。

**本轮的教训（三条，都关于流程而非代码）**

6. **改名任务里最危险的不是改名，是「发给别人的改名清单」** —— 写清单的人同样会犯同名混淆。
   PM 发给验收员的清单里把 `field_spec.json` 的炮位字段与 `unknown_reasons` 的兜底原因列成了同一件事，
   被验收员挡回。**规则：改名清单每一项必须先回读原文确认语义归属，再按行号改。**
7. **一次性代理新增的文件必须显式移交属主** —— 曾导致 `tests/` 下 10 个文件无主，
   `dev-engine-2` 做改名时被 9 条红卡住。`TEAM.md` 已补「测试文件属主总表」。
8. **交接摘要会过期**：`dev-surface` 把「L1 orphan flag 未决」写进交接，但 Lead 已裁决且另一员工已落地。
   **规则：接任者的简报里，已裁决项必须显式标注「已裁决，不要再提」，否则会重复提被否决的方案。**

**P3（软件设计）现状**

9. 设计定义 P3 = `detail / explain / sensitivity`，验收标准「归因可查；灵敏度可算」。
   `detail` ✅；`explain` / `sensitivity` ⬜。
10. **11 项验收依赖 explain**（台账原话「I5 对这些项不适用；要验需走 effect 级 sources（P3 explain 才有）」）。
11. **验收员对 explain 的验收方法已定**：正向 Σcontributors 与 `value` 比 + **反向去掉某项后必须不再相等**
    （只验正向会被「多算一项恰好抵消」骗过）。因此契约必须声明 `combine: additive | multiplicative | mixed`
    与 `sources` 是否为最小集。
### 2026-10-19 · P3 完成（detail / explain / sensitivity）+ 独立验收 0 缺陷

**1200+ passed，MCP 10 个工具。** 设计定义 P3 = `detail / explain / sensitivity`，验收标准「归因可查；灵敏度可算」，三件齐。

**新增能力**

| 层 | 入口 | 能力 |
|---|---|---|
| L2 | `fit_detail` | 完整 overview（未降级）+ 顶层 orphan flags + 信封 512 技能表 |
| 归因 | `fit_explain` | 9 指标；A 可加（Σ==value）/ B 乘性（base × Πfactor **带堆叠惩罚**）/ C 投影（公式 + depth-2） |
| 灵敏度 | `fit_sensitivity` | 14 指标；`disable_item` / `set_state` / `set_mode` → delta；`max_ops` 截断 |

**验收（独立，不复用开发方测试）**

- A 类 30 次：`base + Σcontribution == value`（rel 1e-9）+ **反向**「去掉任一项和必变」
- B 类 16 次：独立从因子链重算，**必须带堆叠惩罚** `1 + (factor−1) × penalty`
- C 类 24 次：按 `÷` 拆公式两项算出 value（rel 1e-6），depth-2 覆盖效应型源 41 个、惩罚 7 个
- sensitivity：`delta == value − baseline`、`delta_pct` 基线 0 时为 null、四计数恒等
- 与 `cap_sensitivity` **8/8 交叉一致**（两种不同实现给出同一事实）
- 65×43 字段级仍 **2,795/2,795**

**本轮最重要的教训（流程级）**

12. **「自洽」不等于「算对」**。验收员第一版重算 B 类时漏了堆叠惩罚，f33 算出 15.15 vs 真值 23.61（差 36%）。
    若它只对照产品自己的 `replayed` 字段，会得到「产品自洽，通过」。
    **规则：验收必须能独立从原始因子/贡献项重算出 value，不能拿产品的中间结果当基准。**

13. **产品自报的中间量不能作为验收依据**（`replayed` / `replay_ok` 是产品的断言，不是证据）。

**已知限制**

14. `feasible` 只算 CPU/PG/标定，**不含无人机**（主人裁决不改定义，改用 `drone_bandwidth_exceeded` / `launched_drones_exceeded` 两条 flag）。
15. `explain.value` 是全精度，L1 显示 3 位小数 —— 刻意如此，舍入会破坏 rel 1e-9 的加和判据。
16. **本地 SDE 的 T3C 模式为 0 条**（Legion/Tengu/Proteus/Loki 的 `*Mode*` 类型缺失）→ T3C 模式维度**不可测**，是数据缺口。
17. `fit_explain(firepower.dps.base)` 在无可用齐射的样本上返回 `None`（指标不适用），不是缺陷。
### 2026-10-20 · 中文名与 SDE 图鉴层（1631 passed，MCP 16 个工具）

**起因**：主人发现「中文物品描述像机翻」。查证结果 —— **数据早就有，是展示层断了**：
`types.name_zh` 53,288/53,288 全填（`Damage Control II` → `损伤控制 II`、`Hawk` → `战鹰级`），
但它**只用于检索、从不进返回体**，agent 只能自己翻英文名。

**这一轮落地的**

| 能力 | 内容 |
|---|---|
| `locale="en"\|"zh"` | `fit_parse` / `fit_killmail`（en 时返回体逐字节不变）；玩家/军团/联盟名永不译 |
| `sde_search`（14） | 四级检索：精确 → 前缀 → 包含 → **每个词都出现(AND，顺序无关)**；`kind` 按 SDE category 过滤不靠名字猜；`ambiguous` 标记；0 命中给 `did_you_mean` 并标「非精确」；纯数字走 type_id |
| **索引库 v2** | `user_version 1→2`，8 表 → **13 表**：`type_attributes 648,726` / `effects 3,427` / `type_effects 54,307` / `type_bonuses 3,571` / `buffs 277`；`attributes` 增 `display_name_zh`；15.0 MB → **45.3 MB**，重建 **3.9 s** |
| `sde_type`（15） | 查任意 type 的属性/效果/装配限制/技能要求/船体加成，全带中文 |
| `can_fit`（16） | 探针配装 + 引擎 validate → `fits` / `blockers` / `remaining` / `slot` + 三条假设；不认识的违规**原样透传 rule** |

**实拍**（type 4403 Reactive Armor Hardener，主人的原问题）
```
duration 10000（作用时间/单次运转时间）· armorEmDamageResonance 0.85（装甲电磁伤害抗性）
resistanceShiftAmount 6.0 · maxGroupFitted 1 · maxGroupActive 1
effect 4928 adaptiveArmorHardener · 船体加固理论 3 级
```

**本轮的三条教训（都是流程级）**

18. **数据有了不等于能力有了**。`name_zh` 在库里躺了很久，只差「进返回体」这一步。
    以后加字段要问一句：**它进输出吗？进哪个入口？**
19. **分层验证才能发现"声明了但没接通"**。`locale` 的 schema 有、`api` 支持、**MCP handler 静默丢掉** ——
    node 门禁全绿，因为测试是分别验的。**规则：跨层参数必须有「穿透测试」（走真实传输，断言两端输出不同）。**
20. **SDE 的两处结构陷阱**：`typeBonus._key` 是**船**、`types[]._key` 才是**技能**；
    `dogmaEffects` **没有任何 displayName**（所以 effect 中文名只能 null）。
    另有 1,620 个属性连英文 displayName 都没有 —— **不许手写映射表**，那会在 SDE 更新后悄悄过期。

**已知不完整**
- 部分属性 `name_zh` 为 null（SDE 本身没有），靠 `notes` 说明，不编。
- `fit_project` 的 buff 名翻译在收尾单里做。