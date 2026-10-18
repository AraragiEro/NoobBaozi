# evefit · 项目进度（工作文档）

> 用途：**我自己的工作台账**。中断后靠它恢复上下文，不依赖对话记忆。
> 更新时机：每个里程碑完成、每次派发子代理任务后、每次踩坑后。
> **使用规则见 `docs/开发规范-v1.md` §11**（含写权、更新时机、子代理回报模板）。
> 最近更新：2026-10-18

---

## 0. 状态摘要（先看这段）

| 项 | 值 |
|---|---|
| 阶段 | **P2 已通过独立验收**；正在补两个已承诺出口（`modes` 块、`confidence` 表） |
| 进度 | 讨论 100% · 设计 100% · P0 ✅ · P1 ✅ · **P2 ✅（补口子中）** |
| 下一个动作 | T-17 收口 → 主人提供 KB 样本 → 用 acceptance runner 跑真实六维度 |
| 阻塞点 | 无 |
| 环境已验证 | Python 3.13 · 引擎 13.6.0 · SDE 3607705 · **308 tests 全绿** · 技能档 512 · 快照 enforce 0 DIFF |

---

## 1. 里程碑

| # | 里程碑 | 状态 | 验收标准 |
|---|---|---|---|
| M0 | 可行性验证 | ✅ 完成 | 引擎可算、读取无损、抗性对拍通过 |
| M1 | 讨论与设计 | ✅ 完成 | 字段规格 / 软件设计 / 开发规范三份文档定稿 |
| M2 | P0 数据层 | ⬜ 未开始 | 能按名查类型、列出 group 1306 模式、单位表单测通过 |
| M3 | P1 计算封装 | ⬜ 未开始 | EFT/DNA/killmail 归一化；可修复错误；往返无损 |
| M4 | P2 六视角 overview | ✅ 完成 | 六视角可出；三态齐全；41/41 字段 × 4 样本；162 条 provenance 全覆盖 |
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
37. **`evefit/` 已于 2026-10-18 `git init`**（首提交 `afdf5b5`，100 文件 / 1.1 MB）。`data/`、`spike/uvcache/`、`__pycache__/`、`acceptance/out/` 已忽略。→ 子代理「是否越界」从此可用 `git status --short` 硬验。
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
57. 反向**上调**为 measured：`11 powerOutput`、`15 powerLoad`、`54 maxRange`、`158 falloff`、`263 shieldCapacity`、`55/482` 电容、`564 scanResolution`、`1132/1152` 标定、以及**全部正 ID 共振率**（109–113、267–274）。
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

---

## 4. 待办

### P0-0 契约（阻塞一切并行）✅

- [x] `evefit/contracts.py`：9 个 TypedDict + 常量 + 5 个模块接口注释块
- [x] 包骨架 `evefit/__init__.py` 与 6 个子包
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
| 8 | 引擎升级回归未固化成单条命令 | Lead | P3 前 |
| 9 | 可疑点"同类船聚合"需要索引支持 | 后置 | 低 |
| 10 | `-70 maxTargets` 实测恒 0，标记为不可用字段 | 已在 §3 记录 | ✅ |
| 11 | **模糊名称候选**：`search_types` 无编辑距离，`Damage Controll II` 这类错位拿不到候选 | 待立项 | 新增 |
| 12 | verify 权限：越界自查只能靠 mtime | Lead | ✅ 完成（`evefit/` 已 `git init`，首提交 `afdf5b5`） |
| 13 | 战斗机 7 个负 ID + 5 个正 ID 常量未登记 | T-07 | ✅ 完成（常量 129 → **141**） |
| 14 | **provenance 表改为实测推导**（手写表与验收口径冲突） | T-08 | 进行中 |
| 15 | 六视角抽取器未写（火力/生存/机动/资源/约束/不稳定） | T-09/T-10 | 待派（等 T-08） |
| 16 | `project/render.py`（overview/detail 组装 + 预算护栏）未写 | T-11 | 待派 |
| 17 | `acceptance/field_spec.json` 的 provenance 列需按新口径重写 | evefit-tester | ✅ 完成（41 字段，双列拆分） |
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
| 33 | `damage_split` 口径统一为 0..1 比率；验收样例（60/40）需改 | evefit-tester | 待通知 |
| 34 | `propulsion_states` 键名定为 `base/off/active/overload`；`mass` → `mass_kg` | 已裁决 | ✅ 规格已更新 |
| 35 | **overview 预算装不进**：RAW 17,777 > 12,000，降级后仍 16,528。根因是 `upgrade_space` 单项 4,135 chars | T-15 | ✅ 完成（摘要化 → 13,519 chars，预算 17,000，默认不降级） |
| 36 | `FIELD_SOURCES` 不一致：两套数组写法、漏登记字符串/布尔叶子、`count_by_level` 是 int 键 dict | T-15 + T-16 | ✅ 完成（叶子补齐 + `[]` 写法统一 + 键空间对齐） |
| 37 | `damage_split`/`application` 的 provenance 仍 `unknown` | T-14（输入常量通道） | 中 |
| 38 | `render_detail` 的 `missing` 恒返回空列表（便于稳定解析） | 已采纳 | ✅ |
| 39 | **acceptance 四个脚本调用 `default_assumptions` 未传 index** → 静默跑旧档（`check_snapshot` 可能拿旧值假绿） | evefit-tester | ✅ 完成（4 处全改 + 新增技能档护栏两层） |
| 40 | `tests/test_projection_core.py` 仍走 `index=None` 并钉旧值（8854.9587） | T-15 | 进行中 |
| 41 | overview 预算装不进 + `FIELD_SOURCES` 不齐 + `contracts` 签名注释过期 | T-15/T-16 | ✅ 完成 |
| 42 | **`confidence` 全量平行表 +5,093 chars**（默认值被显式输出）→ 改稀疏表 | T-18 | ✅ 完成（真实增量降到 +223） |
| 43 | `acceptance/run_acceptance.py` 的 confidence 查表口径（spec 字段名 vs 叶子路径） | evefit-tester | ✅ 已对齐（验收侧本就是稀疏约定） |
| 44 | 文档里 `D`（需对拍）档已收敛为 `A`；`C` 仅表示「不可用」 | Lead | 待改字段规格 |
| 45 | `acceptance/run_acceptance.py::check_repair_zero` 仍期望 `0.0 + measured`，与新裁决（`0.0` + 属性级 tag + `has_source`）不一致 → 会误报 6 条 low | evefit-tester | 已通知 |

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
16. **工作区根目录不是 git 仓库，只有 `evefit/` 是**；在 `evefit/` 之外操作仍只能靠 mtime 自查。
17. **装备级属性也会被 `units.physical()` 换算**（`views/base.py::item_attributes`）→ 千万别在视角里对已登记的毫秒属性再除 1000，会差 1000 倍且不报错。
18. **子代理之间会互相打红测试**：T-12 在改 `engine/assumptions.py` 期间，`test_engine` 必然红；这类红是并行的正常摩擦，判据是「最终全量绿」，不是「中途每个代理都绿」。
19. **`ViewContext` 只从 `ctx.fit` 建变体，不读 `ctx.assumptions`** → 构造上下文时必须把技能注入 `fit["character"]["skills"]`，否则全部按**零技能**算（EHP 7585.48、速度 1059.68），看起来像一堆口径错误。验收员自己就踩过，误报 72 条。

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
evefit/
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
$env:UV_CACHE_DIR = "$PWD\evefit\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
  python evefit\spike\smoke3.py
```

3. 看 §4 待办，从最上面未勾选的那条开始。
4. 若当前有子代理在跑，先确认它们的写入范围与 §7.3 的排版一致，避免撞文件。

---

## 10. 验收记录

### 2026-10-18 · P0 + P1 全部交付（PM 复跑）

| 项 | 结果 |
|---|---|
| `pytest -q evefit/tests` | **117 passed**（18 契约 + 35 引擎 + 34 数据 + 30 属性字典） |
| 索引库 | `evefit/data/evefit-3607705.sqlite3`，14.3 MiB |
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

**说明**：本条为子代理自报，PM 未独立复跑；按分工由 `evefit-tester` 在 P2 验收时复核。

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

1. `pytest -q evefit/tests` 全绿
2. `git status --short` 里的改动**全部属于本次任务**
3. 没有把 `data/`、缓存、大文件带进来（`git diff --cached --stat` 看一眼）
4. 若改了字段或口径，对应文档已同步