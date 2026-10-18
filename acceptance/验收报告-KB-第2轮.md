# KB 样本验收报告 · 第 2 轮（f17–f26 · 武器族与防御族覆盖）

- 执行：`noobbaozi-tester`（独立验收，不改产品代码）
- 时间：2026-10-18
- 环境：SDE **3607705** · 引擎 **13.6.0** · 技能档 **`all4+min_required` / 512**
- 实现侧基线（我自己跑）：`pytest` **391 passed**
- 本轮同时完成 T-22 / T-23 契约同步（字段 41 → **43**，新增 3 条检查）

## 0. 本轮结论

| 项 | 结果 |
|---|---|
| 本轮 10 条样本落盘 | ✅ 全部成功（原始 killmail JSON，去重通过） |
| 六维度 + 43 字段 | ✅ **10/10 样本 × 43/43** |
| 累计字段通过率 | ✅ **1118/1118 = 100.00%**（26 样本 × 43 字段） |
| 新增**新**缺陷 | **1 条 medium**（f26 载机伤害链路静默为 0） |
| 已知缺陷 | **0 条复现**（D5/D6/D7 全部消失，T-20/T-22 有效） |
| 观察项（low） | 12 条：D4 ×2 + X4（I5 效应型/堆叠惩罚）×10 |
| 快照门禁 | ✅ `--enforce` 0 DIFF，`enforced` 保持 |

## 1. 契约同步（T-22 / T-23）

| 变更 | 我的处理 |
|---|---|
| `resources.feasible`（bool） | 已入 `field_spec.json`；检查器新增 `check_feasible` 验一致性 |
| `resources.over_budget{cpu,powergrid}` | 已入 spec；校验 `= max(0, -free)`，可行时两项 0.0 |
| `slots.subsystem` / `empty_slots.subsystem` | 标为**条件字段**（`conditional_subpaths`），只有 `maxSubSystems(1367) > 0` 的船才要求 |
| `dph = null` + `weapon_damage_zero` flag | 修掉旧短路（`if not is_number(dph): return`）：null 时必须**有**该 flag |
| 字段总数 41 → **43** | 累计分母同步改成 43 |
| `f1` 也是 `feasible: false` | ✅ 实测一致（CPU −11.0 / PG −3.6） |

自测同步扩展（全部通过）：
- `feasible` 3 条注入（余量不符 / over_budget 不符 / 不可行缺 flag）
- `dph=null` 契约 2 条（带 flag → 无缺陷；缺 flag → 字段缺失）

## 2. 本轮覆盖矩阵（对照目标）

| 目标 | 要求 | 本轮实际抓到 | 状态 |
|---|---|---|---|
| 激光为主（新船型） | ≥1 | **Zealot f17**（Armor HAC，19 模块） | ✅ |
| 弹射为主（新船型） | ≥1 | **Vagabond f18** + **Sleipnir f21** | ✅ |
| 导弹为主（新船型） | ≥1 | **Cerberus f19** + **Prophecy f24** | ✅ |
| 无人机为主（新船型） | ≥1 | **Dominix f20**（3× Drone Damage Amplifier II）—— **但实船没带无人机** | ⚠️ 部分（无人机的伤害覆盖靠第 1 轮 Ishtar f16） |
| 护盾坦 | ≥2 | Vagabond / Cerberus / Sleipnir / Maelstrom(f23) | ✅ |
| 装甲坦 | ≥2 | Zealot / Absolution / Dominix / Prophecy / VNI | ✅ |
| cap booster | ≥1 | **Maelstrom f23**（Heavy F-RX Compact Capacitor Booster） | ✅ |
| ancillary rep | ≥1 | **Prophecy f24**（Medium Ancillary Armor Repairer） | ✅ |
| nos/neut | ≥1 | **Vexor Navy Issue f25** + Maelstrom f23 | ✅ |
| 航母战斗机（`-55/-56/-58`） | ≥1 | **Thanatos f26**（fighter_bay: Siren I ×3 + Templar I ×5） | ✅ 覆盖到了，但**链路产出 0**（见 §3） |

### 2.1 本轮样本

| 文件 | 船 / 组 | 模块 | EHP | DPS | feasible |
|---|---|---|---|---|---|
| f17-laser-zealot | Zealot / HAC | 19 | 71,941.0 | 631.7 | false |
| f18-projectile-vagabond | Vagabond / HAC | 17 | 80,461.0 | 480.2 | true |
| f19-missile-cerberus | Cerberus / HAC | 17 | 21,072.2 | 360.2 | true |
| f20-drone-dominix | Dominix / Battleship | 21 | 45,499.7 | 40.0（只有智能炸弹） | true |
| f21-shield-sleipnir | Sleipnir / Command Ship | 19 | 37,099.7 | 645.9 | true |
| f22-armor-absolution | Absolution / Command Ship | 19 | 186,163.6 | 7.8（只有智能炸弹） | false |
| f23-cap-booster | Maelstrom / Battleship | 22 | 69,995.2 | 489.2 | true |
| f24-ancillary-rep | Prophecy / Combat BC | 24 | 105,540.2 | 510.2 | false |
| f25-nos-neut | Vexor Navy Issue / Cruiser | 18 | 24,572.0 | 332.3 | true |
| f26-carrier-fighter | Thanatos / Carrier | 21 | 653,845.1 | **0.0（载机 8 架）** | true |

> 模块数含 `drone_bay` / `fighter_bay` 内的条目（与第 1 轮口径略有差别，第 1 轮只数"已装槽位"）。

## 3. 新增缺陷清单：**0 条**

> ⚠️ **T-28 更正**：本轮初报的「f26 载机伤害静默为 0」是**我的误报**，已降级为覆盖说明，
> 见 §3.1。这里保留误报的成因，作为验收器演进的记录。

| 项 | 结论 |
|---|---|
| 新增缺陷 | **0** |
| 已知缺陷复现 | **0**（D5 / D6 / D7 全部消失） |
| 观察项（low） | 12 条：D4 ×2 + X4（I5 效应型/堆叠惩罚）×10 |

### 3.1 【误报记录】f26 载机 `dps.fighter = 0.0` —— 这是**正确行为**

- **我的初判**：`weapons[]` 列出 2 组 `family: "fighter"` 载机（8 架），但 `dps.fighter = 0.0`，
  判为「载机伤害链路静默零值」（medium）。
- **T-28 实证**：`f26-carrier-fighter.json` 里 8 架载机的 flag 是 **158 = FighterBay（机库）**，
  不是 159–163 的 FighterTube。**机库里的载机完全不参与伤害/槽位计算**，只计入船级
  `-57`（机库占用 m³）。改造实验把 Templar 从机库移进发射管后，`-58` 从 **0 → 240.846**。
  所以 `-58 = 0` **是正确的**，不应输出 `null` + flag。
- **我的两个误报根因**：
  1. 我方启发式 `shared.classify_weapon` 把 `fighter_bay` 也判成 `family: "fighter"`
     （T-29 修：机库载机不再进 `weapons[]`，改为新字段 `fighter_bay` + 诊断 flag
     `fighters_in_bay_only`）；
  2. 我的判据过严：即使载机在发射管，`family=fighter` 也不保证 `-58 > 0`
     —— `Siren I` 是**支援战斗机**，进管后只记 `-61`（支援槽），仍不出伤害。
- **已改**：`check_fighter_chain` 现在只在「`-62 fighterTubesUsed > 0` **且** 进管中队
  **非支援型**（item 级 `-61 < 1`）」时才要求 `dps.fighter > 0`；带
  `fighters_in_bay_only` flag 时直接视为合法。
- **数据描述更正（T-28 指出）**：
  - `-57`（fighterCapacityLoad）**只出现在船级**，item 级没有 —— 初报里
    「item 级 `-57 = 14000`」的说法不成立；
  - item 级 `-55/-56 = 0.0` **只对 Templar 成立**；`Siren I` 连这两个键都没有。

## 4. 本轮「没炸」的项

| 覆盖项 | 结论 |
|---|---|
| 四种武器族各一条新船型 | 激光（Zealot）/ 弹射（Vagabond）/ 导弹（Cerberus）/ 智能炸弹（Absolution、Dominix）都输出正常，`weapons[].family` 新增了 `smartbomb`、`fighter` 两个取值 |
| 护盾坦 ×4 / 装甲坦 ×5 | `ehp_mixed`、`ehp_by_damage`、`repair.*` 全部正常；`has_source` 分层正确 |
| cap booster（f23/f24/f25） | 出现在 `cap_by_module` / `cap_sensitivity`，`capacitor` 数值自洽 |
| ancillary rep（f24） | `repair.armor.has_source=true` + 续药量进 `consumables` |
| nos/neut（f23/f25） | 计入 `cap_by_module` 的能耗侧，不炸 |
| `feasible` / `over_budget` | 26/26 自洽（`feasible == (cpu.free>=0 ∧ pg.free>=0 ∧ cal.free>=0)`），不可行的都有 `resource_over_budget_unflyable` flag |
| `slots.subsystem` 条件字段 | 只有 T3C（Loki f4）出现，其余 25 条不出现且**不报缺失** ✅ |
| `dph = null` + flag | f4 实测：`dph=None` + `weapon_damage_zero` flag，符合 T-22 裁决 |

## 5. 我自己的工具链修了 3 处（先修尺子再报别人）

| # | 问题 | 影响 | 修法 |
|---|---|---|---|
| 1 | selftest 合成 payload 缺 `feasible`/`over_budget` | 自测两形态全报"合规零缺陷=False" | 补字段 |
| 2 | `fetch_fixtures.py` 重复运行会**覆盖**已有样本 | f24/f25 被换成另一条船 | 改为幂等（存在即跳过）+ `--only` |
| 3 | f23/f26 判据写太窄 | "Cap Booster" 匹配不到 "Capacitor Booster"；航母槽名不一定是 `fighter_tube` | 判据放宽为 `Cap Booster|Capacitor Booster`、`fighter_tube|fighter_bay` |

## 6. 累计统计

| 项 | 数值 |
|---|---|
| 样本总数 | **26**（f1–f26） |
| 字段数/样本 | **43**（T-23 后：41 + feasible + over_budget） |
| 字段检查总数 | 26 × 43 = **1118** |
| **累计通过率** | **1118 / 1118 = 100.00%** |
| 缺陷 | **1 medium**（f26 载机）+ 12 low 观察 |
| 不变式 | I1–I5、I7 全过（I5 按单模块口径）；I6 provenance 全覆盖 |
| 技能档 | 26/26 `all4+min_required` / 512 |
| 快照 | 22 行 + 四模式 0 DIFF |

## 7. 复现命令

```powershell
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
$E = '--with "eveshipfit-dogma-engine[sde]==13.6.0"'
$PY = "uv run --python 3.13 --no-project $E python"

Invoke-Expression "$PY noobbaozi/acceptance/fetch_fixtures.py --round 2"
Invoke-Expression "$PY noobbaozi/acceptance/fixtures/prepare_expected.py"
Invoke-Expression "$PY noobbaozi/acceptance/run_acceptance.py --entry noobbaozi.project.render:render_overview --label R2"
Invoke-Expression "$PY noobbaozi/acceptance/check_snapshot.py --enforce"
```
