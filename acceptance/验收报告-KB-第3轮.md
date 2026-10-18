# KB 样本验收报告 · 第 3 轮（f27–f36 · 极端形态覆盖）

- 执行：`noobbaozi-tester`（独立验收，不改产品代码）
- 时间：2026-10-18
- 环境：SDE **3607705** · 引擎 **13.6.0** · 技能档 **`all4+min_required` / 512**
- 实现侧基线（我自己跑）：`pytest` **410 passed**
- 本轮同时完成 T-27（值缺失 ⇒ `unknown`）与 T-28（载机在机库不出伤害）的语义同步

## 0. 本轮结论

| 项 | 结果 |
|---|---|
| 本轮 10 条样本落盘 | ✅ 全部成功（原始 killmail JSON，T3C 复用已标注） |
| 六维度 + 43 字段 | ✅ **10/10 样本 × 43/43** |
| 累计字段通过率 | ✅ **1548/1548 = 100.00%**（36 样本 × 43 字段） |
| 新增缺陷 | **0** |
| 观察项（low） | 17 条：D4 ×2 + X4（I5）×13 + budget 截断 ×1 + I3 覆盖度 ×1 |
| 快照门禁 | ✅ `--enforce` 0 DIFF，`enforced` 保持 |

## 1. 覆盖矩阵（第 3 轮目标）

| 目标 | 实际抓到 | 实证 | 状态 |
|---|---|---|---|
| 双推进（AB + MWD） | **f27 Brutix / Combat BC** | `AB=1 + MWD=1` 同时装 | ✅ |
| 工业舰 / 运输舰 | **f28 Bestower / Hauler** | 12 模块、几乎无武器 | ✅ |
| 只带无人机、无武器 | **f29 Vexor / Cruiser** | `drone_bay` 3 架，高槽 4 个**全不带弹**、无武器族 | ✅ |
| 智能炸弹为主 | **f30 Praxis / Battleship** | **7 门 Smartbomb** | ✅ |
| bulkhead 结构坦 | **f31 Megathron / Battleship** | **10 件 Reinforced Bulkheads**，EHP 295k | ✅ |
| 带子系统不带隐形的 T3C | **f32 Legion / Strategic Cruiser** | `subsystem` 槽 4 个、无 Cloaking（复用船型，已注明） | ✅ |
| 全货舱装载 | **f33 Iteron Mark V / Hauler** | **货舱 98 项**（> 50） | ✅ |
| 超载/损伤态（弹药为空） | **f34 Maller / Cruiser** | 高槽 5、**带弹 0** → `ammo_name = null` | ✅ 以「空弹」表达 |
| T3D Jackdaw / Skua | **f35 Jackdaw / T3D** | `modes = 3` | ✅ |
| Apocalypse / Megathron | **f36 Apocalypse / Battleship** | 22 模块（本样本是 8 门 smartbomb 的 Apoc） | ✅ |

> **「超载态」的说明**：killmail 不记录 overheat/损伤状态，无法从 KB 表达。
> 本轮用**弹药为空**的配装替代（`ammo_name = null`），正好压测 T-27 的「值缺失 ⇒ unknown」与
> 「数组元素混装」口径。这一项在「仍未覆盖」里保留为真实缺口。

## 2. 新增语义同步（T-27 / T-28）

| 语义 | 我的实现 |
|---|---|
| **值缺失 ⇒ `unknown`**（T-27） | 新增 `check_provenance_instances`：按**路径聚合**（一条路径一个标签），任一元素为 `None` → 要求 `unknown`；否则按静态表 `tag()` |
| **数组元素口径** | 同上：`weapons[].ammo_name` 混装（一门有弹、一门没有）→ 判 `unknown`；**不按元素逐个要求** |
| `field_spec.json` | 新增 `provenance_rules`（`instance_none` / `array_elements` / `value_present` 三条），并给 5 个字段加 `provenance_when_missing: "unknown"` |
| **`dps.fighter` 只反映已发射中队**（T-28） | `check_fighter_chain` 重写：`-62 fighterTubesUsed > 0` 且非支援型才要求 `> 0`；带 `fighters_in_bay_only` flag 视为合法 |

实测：36 个样本里，**值为 `None` 的叶子全部 `unknown`（0 条违反）**；
`f1/f4/f5` 分别有 16/17/14 条 None 叶子（含按元素展开的重复路径）。

## 3. 本轮缺陷清单：**0 条**

| 类别 | 数量 |
|---|---|
| 字段缺失 / 口径错误 / 单位错误 / provenance 错误 | **0 / 0 / 0 / 0**（medium 及以上） |
| 不变式失败 | 0（medium 及以上；I5 的 13 条为 low 观察） |
| **新** | **0** |
| **已知** | 0 条复现 |

### 3.1 两条 low 观察（非缺陷）

| 位置 | 现象 | 判断 |
|---|---|---|
| `f22 budget.degraded = True`（chars 15,684） | 预算护栏触发，`cap_by_module` 等列表被截断 | **设计内行为**，覆盖度观察 |
| `f31 I3` 混合 EHP 与算术平均仅差 **0.54%** | 该船抗性近乎均匀（hull 四系全 0.402） | **样本性质**，不是实现问题；已把该断言从 blocker 降为 low |

## 4. 本轮「没炸」的项（重点：极端形态）

| 覆盖项 | 结论 |
|---|---|
| **双推进（AB + MWD，通常非法）** | 不崩：`propulsion_states` 四态齐全，MWD 的 `sig` 放大与 AB 的速度加成各自生效；`feasible` 正常 |
| **工业舰（Bestower）** | 几乎无武器：`dps = None`、`dph = None`；`cargo.total` 正常，`slots` 与 `empty_slots` 齐 |
| **只带无人机（Vexor）** | `dps = 306.4` **全部来自无人机**（无武器），且 `dph = None`，不误报 I2（无人机 volley 已排除） |
| **智能炸弹（Praxis 7 门 / Apoc 8 门）** | `weapons[]` 归为 `family: "smartbomb"`；`dph` 与 `alpha` 自洽 |
| **bulkhead 结构坦（Megathron）** | 结构 HP 主导时 `layers`/`ehp_mixed`/`ehp_by_damage` 正常；I3 的公式复算成立 |
| **T3C 无隐形（Legion 复用）** | `slots.subsystem = {used:4, total:5}`、`empty_slots.subsystem` 正确；无 Cloaking 时 CPU 正常（不再出现 Loki 那种 10100） |
| **全货舱装载（Iteron V 98 项）** | `cargo.used` 正常增长、无崩溃；`budget` 触发截断（low 观察） |
| **空弹配装（Maller）** | `weapons[].ammo_name = null` → provenance `unknown`（T-27 生效）；`consumables` 各项为 null 也 `unknown` |
| **T3D Jackdaw** | `modes = 3` 全输出，命名正确（不再有硬编码船名的假缺陷） |

## 5. 我自己的工具链本轮修了什么

| # | 问题 | 影响 | 修法 |
|---|---|---|---|
| 1 | `check_fighter_chain` 把「机库载机」当「已发射」 | 1 条 medium 误报（f26） | 改为 `-62 fighterTubesUsed > 0` + 非支援型（`-61`）才要求伤害；认 `fighters_in_bay_only` |
| 2 | I3 的「与算术平均差 >1%」被当成红线 | 1 条 blocker 误报（f31 结构坦） | 降为 low 覆盖度观察；真正的不变式仍保留「Σ HP/Σ(w·r) 复算」 |
| 3 | `fetch_fixtures.py` 不支持船型复用 | T3C 只有 4 艘，f32 抓不到 | 加 `allow_reuse`（仅 f32 启用，报告注明） |
| 4 | 判据缺「双推进 / 货舱数 / 空弹 / 计数」 | 第 3 轮多数目标无法表达 | 加 `require_all` / `min_count` / `min_cargo` / `require_no_charge` |

## 6. 累计统计

| 项 | 数值 |
|---|---|
| 样本总数 | **36**（f1–f36） |
| 字段数/样本 | **43** |
| 字段检查总数 | 36 × 43 = **1548** |
| **累计通过率** | **1548 / 1548 = 100.00%** |
| 缺陷 | **0**（medium 及以上）；17 条 low 观察 |
| 不变式 | I1–I7 全过（I3/I5 的 low 观察已按样本性质归因） |
| 技能档 | 36/36 `all4+min_required` / 512 |
| 项目测试 | 收尾 **417 passed**（中途出现过 T-29 编辑瞬态，已恢复绿） |
| 快照 | 22 行 + 四模式 0 DIFF |

## 7. 仍未覆盖（诚实清单）

| 项 | 原因 |
|---|---|
| **真实超载 / 损伤态** | killmail 不含 overheat/损伤信息，KB 通道无法表达（本轮用空弹替代） |
| 航母**已发射中队**的伤害链路 | 需要 `fighter_tube` 里有作战中队（不能是支援型）；本轮 f26 是机库样本，T-29 后可用新 flag 定位 |
| Skua（`modes` 的 Primary/Secondary/Tertiary 命名） | 本轮只抓到 Jackdaw |
| 多种 T3C 子系统组合（5 种里只覆盖 4 种槽位） | 样本限制 |
| 非 MWD/AB 的推进（如无推进 + 惯性稳定） | 极端组合未找到合适样本 |
| 货舱 > 100 项 | 98 项已触发预算截断，更大样本未取 |
| `render_detail` / `explain` / `sensitivity`（P3） | 未开始 |
| `cap_sensitivity` 数值正确性 | 仍只验形状 |
| P4 / P5 | 未开始 |

## 8. 复现命令

```powershell
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
$E = '--with "eveshipfit-dogma-engine[sde]==13.6.0"'
$PY = "uv run --python 3.13 --no-project $E python"

Invoke-Expression "$PY noobbaozi/acceptance/fetch_fixtures.py --round 3"
Invoke-Expression "$PY noobbaozi/acceptance/fixtures/prepare_expected.py"
Invoke-Expression "$PY noobbaozi/acceptance/run_acceptance.py --entry noobbaozi.project.render:render_overview --label R3"
Invoke-Expression "$PY noobbaozi/acceptance/check_snapshot.py --enforce"
```
