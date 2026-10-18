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
Invoke-Expression "$PY evefit/acceptance/check_snapshot.py"     # D) fixtures 期望值漂移
# ② 重写（rebaseline 会先自检技能档，未修复则拒绝）
Invoke-Expression "$PY evefit/acceptance/rebaseline.py --rewrite-fixtures"
# ③ 也可直接重标三个样本
Invoke-Expression "$PY evefit/acceptance/fixtures/prepare_expected.py"
```

**纪律**：重写后必须人工 diff 三个 `.expected.json`，确认每处变化都能由「技能档/SDE 变更」
解释；不得为让比对通过而改期望值（红线 N7）。

> ⚠️ **`prepare_expected.py` 必须把 `Index` 传给 `default_assumptions`**，
> 否则会静默退回旧档（19 技能），期望值就标错了。

另外：`run_acceptance.py` 的校验**不依赖**这些 `.expected.json`——它拿**实时引擎事实**
做比对，所以技能档变更不会让 runner 误报；`.expected.json` 只用于 `check_snapshot.py`
的漂移探测与人工复核。
