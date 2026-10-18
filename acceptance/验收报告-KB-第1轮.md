# KB 样本验收报告 · 第 1 轮（f7–f16 · 船体结构覆盖）

- 执行：`evefit-tester`（独立验收，不改产品代码）
- 时间：2026-10-18
- 环境：SDE **3607705** · 引擎 **13.6.0** · 技能档 **`all4+min_required` / 512**
- 累计样本：**16 条**（f1–f3 自造 EFT + f4–f6 真实 KB + f7–f16 本轮真实 KB）
- 实现侧当轮状态：`pytest` **361 passed, 1 xfailed**（我自己跑）

## 0. 本轮结论

| 项 | 结果 |
|---|---|
| 本轮 10 条样本落盘 | ✅ 全部成功（原始 killmail JSON，去重通过） |
| 六维度 + 41 字段 | ✅ **10/10 样本 × 41/41** |
| 累计字段通过率 | ✅ **656/656 = 100.00%**（16 样本 × 41 字段） |
| 新增**新**缺陷 | **0**（本轮没有发现新的 blocker/high/medium） |
| 已知缺陷复现 | **1 条 medium**：D7（f4 `firepower.dph=0`，T-20 尚未清） |
| 观察项（low） | 9 条：D4 ×2 + X4（I5 效应型/堆叠惩罚）×7 |
| 快照门禁 | ✅ `--enforce` 0 DIFF，`SNAPSHOT_STATUS=enforced` 保持 |

> **第一轮是"扫船体结构"的一轮**：三个 T3C 全打到了，T3D 两种（含 modes）、后勤、指挥、战列、HAC 各一。
> 结论是**字段与六维度没有因为船型变化而崩**，唯一复现的是已知的 D7。

## 1. 覆盖矩阵（对照 Lead 给的目标）

| 目标类别 | 要求 | 本轮实际抓到 | 状态 |
|---|---|---|---|
| 其余 3 艘 T3C | Legion / Tengu / Proteus | **Legion f7 / Tengu f8 / Proteus f9**（Loki 已在 f4） | ✅ **4/4 T3C 全齐** |
| T3D ≥2 种 | Confessor / Svipul / Jackdaw / Skua | **Confessor f10 / Svipul f11**（Hecate 已在 f1–f3） | ✅ 2 种（Jackdaw / Skua 未覆盖） |
| 后勤舰（遥修） | Guardian / Scimitar / Oneiros | **Oneiros f12（装甲）/ Scimitar f13（护盾）** | ✅ 2 条（Guardian 未覆盖） |
| 指挥舰 | Sleipnir / Absolution / Damnation | **Damnation f14** | ✅ 1 条 |
| 战列舰 | Raven / Apocalypse / Megathron | **Raven f15** | ✅ 1 条 |
| 重型突击巡洋舰 | Ishtar / Zealot / Sacrilege | **Ishtar f16** | ✅ 1 条 |

**船级分布**：Strategic Cruiser ×3、Tactical Destroyer ×2、Logistics ×2、Command Ship ×1、Battleship ×1、HAC ×1。
**槽位布局**：T3C（含 3 个 subsystem 槽）/ T3D（6H4M4L3R）/ 后勤 / 指挥（含 warfare link）/ 战列 / HAC，差异足够大。

### 1.1 本轮样本清单

| 文件 | 船 / 组 | 模块数 | modes | EHP | DPS |
|---|---|---|---|---|---|
| f7-t3c-legion | Legion / Strategic Cruiser | 24 | 0 | 26,582.7 | 1,092.8 |
| f8-t3c-tengu | Tengu / Strategic Cruiser | 22 | 0 | 20,429.6 | 174.0 |
| f9-t3c-proteus | Proteus / Strategic Cruiser | 22 | 0 | 19,005.4 | 549.0 |
| f10-t3d-confessor | Confessor / Tactical Destroyer | 16 | **3** | 6,177.2 | 148.2 |
| f11-t3d-svipul | Svipul / Tactical Destroyer | 16 | **3** | 11,762.8 | 365.0 |
| f12-logistics-oneiros | Oneiros / Logistics | 15 | 0 | 44,818.8 | `None`（无武器） |
| f13-logistics-scimitar | Scimitar / Logistics | 15 | 0 | 63,959.0 | 59.0 |
| f14-command-damnation | Damnation / Command Ship | 19 | 0 | 234,743.5 | 159.7 |
| f15-battleship-raven | Raven / Battleship | 22 | 0 | 68,201.0 | 660.0 |
| f16-hac-ishtar | Ishtar / HAC | 13 | 0 | 14,701.8 | 448.8 |

## 2. 缺陷清单（按五类 + 严重度，标注已知/新）

| # | 严重度 | 类别 | 位置 | 归属 |
|---|---|---|---|---|
| 1 | medium | 口径错误 | `f4 firepower.dph = 0.0`（`weapons[]` 有带弹药武器） | **D7 已知**（T-20 未清） |
| 2–3 | low | 不变式失败 | `[Empty Medium Slot]` / `[Empty Cargo Slot]` 的 `line=None` | **D4 已知**（引擎侧欠账） |
| 4–10 | low | 不变式失败 | `I5` 观察 7 条（f4/f5/f7/f11/f12/f14/f16） | **X4 已知**（效应型抗性 / 多模块堆叠惩罚） |

**本轮没有发现新的 blocker / high / medium 缺陷。**

### 2.1 本轮也顺带确认了两件事（对 I5 的认知修正）

1. **I5「抗性 = 共振率相乘」只在单模块时成立**。多模块同时声明抗性时引擎施加**堆叠惩罚**：
   f7（DC II + Reactive Armor Hardener + 2× Multispectrum Membrane）移除 0.85 的 DC II 得到 **0.8696** 而非 0.85；
   f14 同样。这与 PROGRESS §3 第 4 条（单 DC II 上验证）并不矛盾——是适用范围问题。
   → 我已把 I5 限定为「单模块乘性对照」，多模块降为 low 观察。
2. **T3D 的 modes 正常**：Confessor（+21.2% EHP / +33.3% DPS / −33.3% 对齐）与 Svipul（+45.8% / +18.7% / −33.3%）
   都按方向正确输出，说明 modes 块不是只对 Hecate 生效。

### 2.2 我自己的尺子又歪了 2 处（已修，先修尺子再报别人）

| # | 问题 | 影响 | 修法 |
|---|---|---|---|
| 1 | `modes` 名称匹配**硬编码 "Hecate "** | 6 条假「缺 mode」（Confessor/Svipul） | 归一规则改为「去掉 ` Mode` 后取最后一个词」，不依赖船名 |
| 2 | I5 的乘积对照被**效应型模块 + 堆叠惩罚**污染 | 25 条假 high | ①只认真实模块（排除无人机/货舱/植入体）②改为**逐模块移除实验** ③多模块时降为 low |

## 3. 本轮「没炸」的项（覆盖到了，结论是好的）

| 覆盖项 | 结论 |
|---|---|
| 三个 T3C（Legion/Tengu/Proteus） | 41/41 字段齐；无 subsystem 特有的资源/槽位崩溃；X2（子系统槽位不在 `slots`）依旧存在但**不阻塞** |
| 两种 T3D（Confessor/Svipul） | **三级模式全部输出**，方向一致（Defense EHP↑ / Propulsion 对齐↓ / Sharpshooter DPS↑） |
| 后勤舰（Oneiros/Scimitar） | 无武器时 `dps=None`、`dph=None` 正常；遥修模块体现在 `repair`/`external_dependency`；41/41 字段齐 |
| 指挥舰（Damnation） | warfare link 类模块不炸；`hard_violations=[]`、`tier_violations=[]` |
| 战列舰（Raven） | 导弹 + 护盾坦，`damage_split`/`range.max_range_m`/`consumables` 正常 |
| HAC（Ishtar） | 无人机为主（DPS 448.8 全来自 drones 也不炸 I2，因 I2 已排除无人机 volley） |
| 空列表语义 | f12/f6 的 `weapons: []`、`cap_by_module: []`、`cap_sensitivity: []` 均正常落 `unknown`/空表 |

## 4. 仍未覆盖（下一轮/第三轮要补）

- **T3D**：Jackdaw、Skua（+ Anhinga 的 Primary/Secondary/Tertiary 模式命名）
- **后勤**：Guardian
- **指挥**：Sleipnir、Absolution
- **战列**：Apocalypse、Megathron
- **HAC**：Zealot、Sacrilege
- 第 2 轮的武器族/防御族：激光、弹射、无人机为主的新船型；护盾坦×2 / 装甲坦×2；cap booster、ancillary rep、nos/neut、**航母（fighter，验 `-55/-56/-58` 链路）**
- 第 3 轮的极端形态：双推进、工业舰、纯无人机、smartbomb、bulkhead 结构坦、无隐形 T3C、货舱 >50、超载/损伤态

## 5. 待 Lead 关注的两个观察（P2 之外，供 T-21 参考）

| # | 现象 | 证据 | 判断 |
|---|---|---|---|
| **X5** | **Damnation 装甲 HP 量级偏高** | 裸船装甲 HP **7,896** → 整装 **34,142**（+26,246，来自 2 块 FN 护甲板 + 2× Trimark 装甲泵 II + DC II）；EHP 234,743。按模块常识估算应在 ~2 万量级 | 需实现方/引擎侧复核「护甲板 + Trimark ×2」的 HP 叠加是否重复计入。**不记 P2 缺陷**（我无法证明它对错，只能报量级可疑） |
| **X1 复现** | T3C CPU 爆表在**本轮 3 个 T3C 上未复现**（Legion/Tengu/Proteus 的 CPU 均正常） | f7/f8/f9 的 `cpu.free` 有值且无 `tier_violations` | X1 是 **Loki + Covert Ops Cloaker 特例**（`cpu(50)=10100`），不是所有 T3C 的普遍问题 |

## 6. 累计统计

| 项 | 数值 |
|---|---|
| 样本总数 | **16**（f1–f16） |
| 字段检查总数 | 16 × 41 = **656** |
| 通过 | **656 / 656 = 100.00%** |
| 不变式 | I1–I5、I7 在 16 样本上全部成立（I5 按「单模块」口径；多模块为 low 观察）；I6 provenance 全覆盖 |
| confidence 稀疏表 | 16 样本均只有 `application.*` 三条 `E`，其余视角 `{}` |
| 技能档 | 16/16 样本 `all4+min_required` / 512，无一退化 |
| 快照 | 22 行 + 四模式 0 DIFF（`enforced`） |

## 7. 复现命令

```powershell
$env:UV_CACHE_DIR = "$PWD\evefit\spike\uvcache"
$E = '--with "eveshipfit-dogma-engine[sde]==13.6.0"'
$PY = "uv run --python 3.13 --no-project $E python"

# 抓第 1 轮样本（幂等：已存在的会按去重跳过）
Invoke-Expression "$PY evefit/acceptance/fetch_fixtures.py --round 1"
# 生成期望值（含新样本）
Invoke-Expression "$PY evefit/acceptance/fixtures/prepare_expected.py"
# 全量验收
Invoke-Expression "$PY evefit/acceptance/run_acceptance.py --entry evefit.project.render:render_overview --label R1"
# 快照门禁
Invoke-Expression "$PY evefit/acceptance/check_snapshot.py --enforce"
```
