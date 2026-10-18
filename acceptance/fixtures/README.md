# fixtures 说明（不依赖网络 / 不依赖 zkillboard）

三个固定样本，全部以 **EFT 文本**落盘，期望值另存 `<name>.expected.json`。
期望值由 `prepare_expected.py` 在 **SDE build 3607705 + 引擎 13.6.0** 下生成后冻结，
属于「快照」而非「不变式」——SDE 更新后需重新标定并人工 diff（开发规范 §6.2）。

| 文件 | 用途 | 关键特征 |
|---|---|---|
| `f1-hecate-killmail.eft` | 正常战斗舰（由 `spike/kill.json` 的 killmail 139246907 转存） | 5×中子炮 + MWD + 3 rig + 14 项货舱物；CPU 超 11、PG 超 18；无模式 |
| `f2-hecate-tier-violation.eft` | 故意超载 / 档位违规 | **2×MWD**（同组超量→硬违规）+ PG/CPU 双超（档位违规）；技能门槛含 5 级 |
| `f3-hecate-modes.eft` | 带模式的船（Hecate） | 单门中子炮，最小化干扰；用于验证 `mode` 维度（Defense/Propulsion/Sharpshooter） |

## 硬约束

1. **不得联网**：三者都是本地 EFT 文本，runner 不访问 zkillboard / ESI。
2. **不得依赖 pytest 的 `tmp_path`**：沙箱下不可用（PROGRESS §6 第 10 条）。
3. `f1` 的 EFT 文本由 `engine.save_eft(load_killmail(kill))` 直接产出，因此它同时也是
   **I4 往返无损**的输入样本；改这个文件等于改基线，必须走 changelog。

## 期望字段

`*.expected.json` 包含：

- `sde_build` / `engine_version`：生成期望值时的版本（三元组的另两项）
- `skill_tier`：默认技能档的完整 `{skill_type_id: level}`
- `ship` / `snapshot`：关键指标的期望值
- `modes`：`{mode_type_id: {name, ehp, dps, align_time_s}}`（Hecate 三模式；非模式船为空）
- `violations`：`validate` 在**默认技能档下**返回的违规（用来验证「档位违规 vs 硬违规」分类）

## 技能档：T-12 之后 = 512（已重标）

这三个 `.expected.json` 已按 **T-12 修复后的技能档**重标：`all4+min_required` = **512 个技能**
（510×4 + 2×5）。T-12 之前分别是 19 / 16 / 8 个（只补被模块要求的技能），那批旧值已作废。

重新生成（技能档或 SDE 变了才需要）：

```powershell
$PY = 'uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python'
# ① 先看漂移（只读）
Invoke-Expression "$PY noobbaozi/acceptance/check_snapshot.py"     # D) fixtures 期望值漂移
# ② 重写（rebaseline 会先自检技能档，未修复则拒绝）
Invoke-Expression "$PY noobbaozi/acceptance/rebaseline.py --rewrite-fixtures"
# ③ 也可直接重标三个样本
Invoke-Expression "$PY noobbaozi/acceptance/fixtures/prepare_expected.py"
```

**纪律**：重写后必须人工 diff 三个 `.expected.json`，确认每处变化都能由「技能档/SDE 变更」
解释；不得为让比对通过而改期望值（红线 N7）。

> ⚠️ **`prepare_expected.py` 必须把 `Index` 传给 `default_assumptions`**，
> 否则会静默退回旧档（19 技能），期望值就标错了。

另外：`run_acceptance.py` 的校验**不依赖**这些 `.expected.json`——它拿**实时引擎事实**
做比对，所以技能档变更不会让 runner 误报；`.expected.json` 只用于 `check_snapshot.py`
的漂移探测与人工复核。

## 真实 KB 样本（f4 / f5 / f6，2026-10-18 从 zkillboard 抓取）

补的是「非 Hecate / 非 MWD / 极简配装」三个覆盖空白。**全部是原始 killmail JSON**
（不转 EFT），走与 `--eft killmail.json` 相同的 KB 通道。

| 文件 | 船 / 组 | 覆盖目标 | 已装模块 |
|---|---|---|---|
| `f4-non-hecate.json` | **Loki / Strategic Cruiser（T3C）** | 非 Hecate + 不同槽位布局（含子系统槽） | 24 |
| `f5-no-mwd.json` | **Drake / Combat Battlecruiser** | 非 MWD 推进（10MN Afterburner II） | 20 |
| `f6-minimal-fit.json` | **Griffin / Frigate** | 极简配装（只有船体，0 模块） | 0 |

重新抓取：`python noobbaozi/acceptance/fetch_fixtures.py`（脚本里记了 4 条踩坑：

1. `/api/kills/shipTypeID/` 按「涉入」过滤，**必须用 `/api/losses/`**（按受害者）；
2. 仍要硬校验 `victim.ship_type_id == 查询船型`；
3. 植入体/增效体也算 `items`，按"已装模块"计数会误选「只有植入体的船」；
4. Capsule（type 670）不是船，要排除。）

期望值由 `prepare_expected.py` 统一生成（`all4+min_required` / 512 技能），
`.expected.json` 与样本一一对应。

## 第 1 轮：船体结构覆盖（f7–f16，2026-10-18 抓取）

| 文件 | 船 / 组 | 已装模块 | modes |
|---|---|---|---|
| `f7-t3c-legion.json` | Legion / Strategic Cruiser（T3C） | 24 | 0 |
| `f8-t3c-tengu.json` | Tengu / Strategic Cruiser（T3C） | 22 | 0 |
| `f9-t3c-proteus.json` | Proteus / Strategic Cruiser（T3C） | 22 | 0 |
| `f10-t3d-confessor.json` | Confessor / Tactical Destroyer（T3D） | 16 | **3** |
| `f11-t3d-svipul.json` | Svipul / Tactical Destroyer（T3D） | 16 | **3** |
| `f12-logistics-oneiros.json` | Oneiros / Logistics（装甲遥修） | 15 | 0 |
| `f13-logistics-scimitar.json` | Scimitar / Logistics（护盾遥修） | 15 | 0 |
| `f14-command-damnation.json` | Damnation / Command Ship | 19 | 0 |
| `f15-battleship-raven.json` | Raven / Battleship | 22 | 0 |
| `f16-hac-ishtar.json` | Ishtar / Heavy Assault Cruiser | 13 | 0 |

抓取命令：`python noobbaozi/acceptance/fetch_fixtures.py --round 1`
（脚本内置去重：与已有样本、同轮内都不允许同型船；**按覆盖目标分批，不随机抓**。）

## 第 2 轮：武器族与防御族覆盖（f17–f26）

| 文件 | 船 / 组 | 覆盖点 | 模块 |
|---|---|---|---|
| `f17-laser-zealot.json` | Zealot / HAC | 激光 + 装甲坦 | 19 |
| `f18-projectile-vagabond.json` | Vagabond / HAC | 弹射 + 护盾坦 | 17 |
| `f19-missile-cerberus.json` | Cerberus / HAC | 导弹 + 护盾坦 | 17 |
| `f20-drone-dominix.json` | Dominix / Battleship | 无人机支援（实船未带无人机） | 21 |
| `f21-shield-sleipnir.json` | Sleipnir / Command Ship | 弹射 + 护盾坦 | 19 |
| `f22-armor-absolution.json` | Absolution / Command Ship | 装甲坦 + 智能炸弹 | 19 |
| `f23-cap-booster.json` | Maelstrom / Battleship | 电容注射器 + nos/neut | 22 |
| `f24-ancillary-rep.json` | Prophecy / Combat BC | 辅助修 + 装甲坦 | 24 |
| `f25-nos-neut.json` | Vexor Navy Issue / Cruiser | 吸电/毁电 + 辅助修 | 18 |
| `f26-carrier-fighter.json` | Thanatos / Carrier | 舰载机（fighter_bay） | 21 |

抓取：`python noobbaozi/acceptance/fetch_fixtures.py --round 2`
（幂等：已有样本自动跳过；`--only <stem>` 可补抓单条。）

## 第 3 轮：极端形态（f27-f36）

| 文件 | 船 / 组 | 覆盖点 |
|---|---|---|
| f27-dual-prop.json | Brutix / Combat BC | 双推进 AB+MWD |
| f28-industrial.json | Bestower / Hauler | 工业舰 |
| f29-drones-only.json | Vexor / Cruiser | 只带无人机、无武器 |
| f30-smartbomb.json | Praxis / Battleship | 7 门智能炸弹 |
| f31-bulkhead.json | Megathron / Battleship | 10 件 bulkhead 结构坦 |
| f32-t3c-no-cloak.json | Legion / Strategic Cruiser | 子系统但不带隐形（复用船型） |
| f33-cargo-heavy.json | Iteron Mark V / Hauler | 货舱 98 项 |
| f34-no-charge.json | Maller / Cruiser | 武器空弹（ammo_name=null） |
| f35-t3d-jackdaw.json | Jackdaw / T3D | modes 命名 |
| f36-battleship-apoc.json | Apocalypse / Battleship | 另一种战列 |

## 第 4–6 轮（f37–f66）

- **第 4 轮（定向补空白）**：`fetch_fixtures.py --round 4`
  —— T3C covert（f39/f43/f45）、无畏（f40/f46）、FAX（f41）、航母机库满载（f44, fighter_bay=27）、
  货舱 80+（f42）。**f37（发射管载机）与 f38（Skua）在 7 日窗口内无样本**。
- **第 5 轮（随机抽样）**：`fetch_random_samples.py` —— 10 条不加筛选的真实 killmail（f47–f56）。
- **第 6 轮（合成构造）**：`make_synthetic_fixtures.py` —— 10 条 EFT（f57–f66）：
  双推进、空配装、全槽位、无推进、无改装件、混搭坦、智能炸弹、注射器无药、武器空弹、单模块。
  注意：`超载态 / 极端货舱 / 极端无人机` 在 EFT 里**无法表达**（引擎拒绝 cargo/drone 段）。

