# 验收报告 · P4 增量（炮位口径 / EFT 往返 / zkb_stats）

- 执行：`evefit-tester`（独立验收；断言全部自己写，未复用开发方测试）
- 字段同步：`hardpoints_free` → **`hardpoints`**（`resources` 唯一出处）
- 字段总数：**44 → 43**（`firepower.hardpoints_free` 已随口径修正删除）
- 样本：**65**（f1–f67）｜分母 **65 × 43 = 2,795**

## 0. 数字

| 项 | 结果 |
|---|---|
| 字段通过率 | **2,795 / 2,795 = 100.00%** |
| 基线 status | **pass**（28 条 low，**0 条 medium+**） |
| `pytest`（我自己跑） | **1,023 passed** |
| `hardpoints` 三态 | ✅ 0/3/5 门 → `used` 0/3/5、`free` **3/0/−2**，5 门出 `hardpoint_violation` |
| EFT 往返 | ⚠️ 模块集合一致；**装填弹药丢失**（medium） |
| `zkb_stats` | ✅ 三形态可区分；⚠️ **`danger_ratio` 被取整**（high） |

## 1. 字段同步（我的地盘）

| 文件 | 改动 |
|---|---|
| `field_spec.json` | `resources.hardpoints_free` → `hardpoints`（subpaths `turret/launcher/unclassified`）；**删除** `firepower.hardpoints_free`；新增 `hardpoints_rules`（单一出处 / `free == total - used` / 负值出 flag） |
| `run_acceptance.py` | 属性映射 `102/101` → `hardpoints.*.total`（**不是 free**）；交叉校验段重写为「单一出处 + 三态自洽」 |
| `selftest.py` | 删除 firepower 载荷里的 `hardpoints_free`；resources 载荷改为三态结构；mutation 改为「free ≠ total − used」 |

**我自己踩的坑**：先把 `102/101` 映射成 `free`，跑出 **39 条假 high**（引擎 102 是基准炮位数 = 新字段的 `total`）。
改成 `total` 后归零 —— 又一次「先按语义核对，再写断言」。

## 2. A. `hardpoints` 三态：✅ PASS

载体扫描：`Index.types_in_group(group_id)` 遍历 96 艘（group id 才是参数），命中 **Dragoon**（高槽 5、发射器基准 3）。

| 装载 | `total` | `used` | `free` | `hardpoint_violation` |
|---|---|---|---|---|
| 0 门 RLML | 3 | 0 | **3** | 否 |
| 3 门 RLML | 3 | 3 | **0** | 否 |
| 5 门 RLML | 3 | 5 | **−2** | **是** ✅ |

与 Lead 给的期望值逐项一致（含负值场景）。

## 3. B. `parse_fit().result.eft` 往返：⚠️ 部分

| 用例 | 模块集合（slot+type_id） | 含装填弹药 |
|---|---|---|
| f1（Hecate，5 门带弹） | ✅ 17/17 一致 | ❌ **不一致** |
| 空名配装 `[Hecate]` | ✅ 0/0 一致 | ✅ 一致 |

**证据**：emit 出的模块行是 `Light Neutron Blaster II`（**不带 `, Void S`**），弹药被写成**货舱行** `Void S x1044`；
再次 parse 时 5 门炮的 `charge` 全变 `None`。

- 判定：**medium**（`emit → parse` 对「已装集合」若含装填弹药则不无损）
- 需要你裁决口径：`已装集合`是否包含弹药？
  - 若含 → 这是缺陷（建议 emit 时把 charge 写回模块行）；
  - 若不含 → 建议在文档写明「EFT 往返只保证模块级，弹药按货舱处理」。

## 4. C. `zkb_stats`：✅ 主体通过，⚠️ 1 条 high

### 4.1 三种错误形态：✅ 可区分

| 注入 | `ok` | `kind` | 消息 |
|---|---|---|---|
| `{"error": "not found"}` | false | `unknown_entity` | 「zkb 不认这个实体：not found（id 不存在或与 entity_type 不匹配）」 |
| 0 字节空体 | false | `network_failed` | 「zkb 返回的不是对象：bytes」 |
| 空壳实体 `{}` | false | `unknown_entity` | 「返回了空壳画像（没有 name、也没有任何战斗字段）」 |

三者 `(kind, message)` 两两不同 → **分得清** ✅；且业务失败**没有**抛异常。

### 4.2 `danger_ratio` 只转述：❌ **被取整**

注入 `dangerRatio: 12.5` → 输出 `style.danger_ratio = **12**`（`gangRatio: 45.5 → 45`）。

- 判定：**high** —— 与「只转述」冲突，且**取整不报错**（静默精度损失）；
- 若取整是有意（zkb 指标按整数百分比呈现），需在 `style.notes`/文档写明，并说明精度损失。

### 4.3 体量 ≤ 4 KB：**未验**

离线注入无法代表真实体量；真实实体调用（id 1 / 90000001 / 1633215779）都返回 `ok=false`（实体不存在），
没有拿到可用的真实画像 → 该项**不默认通过**，需给一个有效实体 id 复验。

## 5. 仍未验 / 需裁决

- `zkb_stats` 真实体量（需要一个有效 role/corp/alliance id）
- EFT 往返还「含弹药」的口径（见 §3）
- `danger_ratio` 取整是否有意（见 §4.2）
- `hardpoints` 的 `non_hardpoint` / `unclassified` 分类正确性：我只验了三态算术与 flag，
  没对「哪些模块算 non_hardpoint」逐类对拍

## 6. 复现

```powershell
$env:UV_CACHE_DIR = "$PWD\evefit\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
  python evefit/acceptance/check_p4b.py
```

产物：`out/p4b-check.json`、`out/acceptance-FINAL-HP.json`。

## 7. 补充：`non_hardpoint` / `unclassified` 分类矩阵（✅ 7/7）

载体：Megathron（高槽充足）。每例只装一个模块，读 `resources.hardpoints`。

| 类别 | 模块 | 结果 |
|---|---|---|
| 隐身 | `Improved Cloaking Device II` | **non_hardpoint** ✅ |
| 智能炸弹 | `Small EMP Smartbomb II` | **non_hardpoint** ✅ |
| 吸电 | `Small Ghoul Compact Energy Nosferatu` | **non_hardpoint** ✅ |
| 遥修 | `Large I-ax Enduring Remote Armor Repairer` | **non_hardpoint** ✅ |
| 牵引 | `Small Tractor Beam II` | **non_hardpoint** ✅ |
| 探针发射器 | `Expanded Probe Launcher I` | **non_hardpoint** ✅ |
| 节日发射器 | `Festival Launcher` | **unclassified**（`non_hardpoint=0`、`unclassified=1`、`modules=[]`）✅ |

> 两个模块名需注意：SDE 里是 `Small Tractor Beam II`（没有 `Tractor Beam II`）与
> `Festival Launcher`（没有 `Festival Launcher I`）—— 我第一版按 Lead 给的名字找不到，已按 SDE 实名修正。

## 8. 补充：`zkb_stats` 真实体量（✅ 全 ≤ 4 KB）

用 Lead 给的有效实体 id（离线注入无法代表真实体量，故必须真实调用）：

| 实体 | 类型 | ok | 序列化体量 | `danger_ratio` |
|---|---|---|---|---|
| SixtyEverSnake（2117515414） | character | true | **2,280 bytes** | 69 |
| The big boys（98821127） | corporation | true | **3,164 bytes** | 84 |
| Goonswarm Federation（1354830081） | alliance | true | **3,719 bytes** | 47 |

上限 4,096 → **三条全部通过** ✅

**顺带一个观察（待裁决，不算缺陷）**：连续调用被 zkb 限流时，`zkb_stats` 返回
`kind=internal_error`（不是 `network_failed`），退避 5–15s 后恢复。
建议把「上游限流 / 响应畸形」归到 `network_failed`，`internal_error` 留给真正的自身异常
—— 否则 agent 会把「等一会儿再试」误当成「我们这边坏了」。

## 9. 待修复后复验（dev-engine / dev-surface 交卷后）

| # | 项 | 当前状态 |
|---|---|---|
| 1 | EFT emit 带回 `武器名, 弹药名`（已升 **high**） | 待修；复验点：`parse→emit→parse` 的 charge 一致 |
| 2 | `dangerRatio 12.5 → 12` 等 ratio/mean 字段原样透传（已确认 **high**） | 待修；复验点：注入浮点 → 输出逐位相同（含 gangRatio / soloRatio / avgGangSize） |
| 3 | `hardpoints` 其余分类（如 `unclassified` 是否只该有节日发射器一类） | 已覆盖 Lead 指定的 7 类；更多类别可再扩 |

## 10. 「限流 → `internal_error`」的原始证据（Lead 索要）

### 10.1 调用参数与序列

- 接口：**`api.zkb_stats(...)`（Python 直调，不经过 MCP 层）**
- 参数：`entity_id` ∈ {2117515414, 98821127, 1354830081}，`entity_type` ∈
  {character, corporation, alliance}，**`include` 未传（用默认）**
- 序列：**同一脚本第 2 次执行**（第 1 次刚成功跑完同样 3 次调用），
  **3 实体连跑、间隔 0**，全部 3 次都返回 `ok=false kind=internal_error`

### 10.2 我看到的到底是什么

**是 `error.kind`，不是 MCP 的 `isError`。** 那次调用根本没走 MCP 服务器，
所以不存在“把布尔标志当 kind”的问题。

### 10.3 错误对象全文：**我没保留，如实说明**

当时脚本只落了 `kind` 与 `message[:60]`（`out/hardpoints-classes.json` 随后被
成功运行覆盖），**没有保存完整 `{"ok":false,"error":{...}}`**。这是我的取证缺失，不狡辩。

### 10.4 现在能否复现：**不能**

刚才按「6 次连跑、间隔 0」重放：

| 序 | 实体 | 结果 | 体量 | 耗时 |
|---|---|---|---|---|
| 1 | SixtyEverSnake | ok=True | 2,280 B | 1,791 ms |
| 2 | The big boys | ok=True | 3,164 B | 2,508 ms |
| 3 | Goonswarm Federation | ok=True | 3,719 B | 2,817 ms |
| 4–6 | 同上重放 | ok=True | 同上 | 1,762 / 1,668 / 4,090 ms |

**失败 0 次** → 与开发方“复现不出来”一致。倾向判断：**上游瞬态**（而非 `rate_limited` 接线缺失）。

### 10.5 构建版本

仓库根在 `evefit/`（不是工作区根），当前 **`dfdaa7a`**。
**失败时刻的确切 HEAD 我没记录** —— 无法回溯，只能确认当前版本正常。

## 11. 体量口径纠正（重要）

我此前报的 **2,280 / 3,164 / 3,719** 是 **`api` 信封**大小，**不是 MCP 线上**。实测四口径：

| 口径 | SixtyEverSnake | The big boys | Goonswarm | ≤4KB? |
|---|---|---|---|---|
| `result` 紧凑（开发方口径） | ~1,910 | ~2,706 | ~3,215 | ✅ |
| `result` 默认 dumps | 2,062 | 2,946 | 3,502 | ✅ |
| api 信封（我先前报的） | 2,280 | 3,164 | 3,719 | ✅ |
| **MCP 线上**（content + structuredContent） | **4,891** | **6,771** | **7,941** | ❌ **全部超** |

原因：`structuredContent` 与 `content[0].text` **各放一份完整信封** → 约 2×。

**结论**：`≤4 KB` 只在 `result` 口径成立；按 Lead 建议的「MCP 线上（agent 实际吃进去的量）」口径**不成立**。
建议二选一：
1. 把预算口径明确写成「`result` 紧凑 JSON ≤ 4 KB」，并在 MCP 层注明双发的倍数；
2. MCP 层不要双发（老版本只给文本、新版本只给 structuredContent，或 structuredContent 只放精简版）。

## 12. 三项修复复验（✅ 全过）

| # | 项 | 结果 |
|---|---|---|
| 1 | EFT 模块行带回装填弹药 | ✅ emit 行变为 `Light Neutron Blaster II, Void S`；`parse→emit→parse` **含弹药一致=True**；eft 长度回到 1,015 |
| 2 | ratio / mean 原样透传 | ✅ `dangerRatio 12.5 → 12.5`、`gangRatio 45.5 → 45.5`；真实数据 `avg_gang_size=3.6`、`top.characters[0].isk=182078148.24` 均原样 |
| 3 | 错误分类共用判定 | ✅ 精确错误体 `"Invalid type or id"` → `unknown_entity`；**真实 HTTP 路径**抛 429 → **`rate_limited` + `hint.min_interval_s=1.2`** |

**一个注入缝的边界（观察，待裁决）**：用 `zkb_stats(..., requester=fn)` 让 `fn` 直接抛
`urllib.error.HTTPError(429)` 时，得到的是 `internal_error` 而非 `rate_limited` ——
说明限流判定在 HTTP 层，`requester` 注入缝绕过了它。
建议文档写明「`requester` 只注入响应正文，不模拟 HTTP 错误」；或让注入缝也识别 `HTTPError`。

## 13. 分类矩阵重跑（修复后 ✅ 7/7）

隐身 / 智能炸弹 / 吸电 / 遥修 / 牵引 / 探针发射器 → **non_hardpoint**；
`Festival Launcher` → **unclassified**（`non_hardpoint=0`、`unclassified=1`、`modules=[]`）。
**修复没有碰坏分类。**

## 14. 当前基线

| 项 | 值 |
|---|---|
| 基线 | **65 × 43 = 2,795/2,795 = 100%**，`status=pass`（27 条 low，**0 条 medium+**） |
| `pytest`（我自己跑） | **1,042 passed** |
| 构建 | `dfdaa7a`（仓库根 `evefit/`） |

## 15. L2 `fit_detail` 字段级验收（本轮新增，独立重算）

工具：`acceptance/check_detail.py`（可一键重跑）｜产物：`out/detail-check.json`
样本 **10 个**：f1（炮）、f7（T3C）、f11（炮/导弹混装）、f12（后勤）、f15（导弹 BB）、
f25（电容敏感，nos/neut）、f26（航母）、f33（货舱）、f48（Gnosis）、f67（双推进）。
**结论：0 缺陷**；四类全部独立重算通过。

### 15.1 覆盖与抽样数量

| 类 | 抽样 | 说明 |
|---|---|---|
| `cap_by_module` | **51 个模块条目**（10 样本全量） | 每样本都有 |
| `cap_sensitivity` | **29 条**（每样本最多 3 条） | 17 条摘掉后真的稳定，12 条不翻转 |
| `upgrade_space` | **32 个候选**（每样本最多 4 个） | f12 / f67 无候选（空清单，如实计 0） |
| `skill_tier` | **10 样本 × 512 条 = 5,120 条** | 逐条查等级域 + 跨层一致性 + hash 确定性 |

### 15.2 独立重算方法与结论

**① `cap_by_module`**：源头是**装备级 `-4`（capPeakLoad 贡献）**，我逐模块从引擎原始属性聚合，
再与 `cap_by_module` 逐条对比（含 `count`）：

- 51/51 条目**逐值吻合**（容差 max(0.02, 0.5%)）；
- **公式复核**：对 `attr6 > 0` 的消费者，`装备级 -4 == 6 / cycle` 全部成立
  （f1：SAR 40/4.8=8.333、Warp Disruptor 24/5=4.8、web 4/5=0.8×2、MWD 36/10=3.6、
  blaster 1.4161/1.9359=0.7315×5=3.657）；
- **负消耗覆盖**：f7 `Medium Energy Nosferatu II = -10.08`、`Medium Capacitor Booster II = -33.333`，
  符号与量级都与我按 `-4` 的独立聚合一致；
- **恒等式**：10/10 样本 `peak_load == Σ cap_by_module`（含负数）全部成立
  （f25：66.515 == 66.515）。

**② `cap_sensitivity`**：把该模块**摘掉一件**后用引擎重算 `-72`/`-7`，与报告值比对：

- 29/29 的 `stable_pct` 与我的独立重算一致（±0.6）；
- `depletes_in_s` 在“摘后仍不稳定”时也一致（f1：38.4 → 80.0 等）；
- `module` 全部存在于已装配列表；
- **口径差异（需你裁）**：你说「断言 `stable_pct_before <= 0` 且 `after > 0`」，
  但该字段的语义是「摘掉后的状态」，**不是**「摘掉就变稳定」：
  29 条里 **17 条才真的翻转**，12 条摘掉后仍不稳定（f1/f12/f25 这类本来就超载的配装）。
  我没有把“不翻转”判成缺陷（那是语义误解），但建议文档写明，或加 `becomes_stable: bool`。

**③ `upgrade_space`**：把候选件真的换上去重算资源余量：

- 32/32 的 `cpu_free` / `power_free` / `calibration_free` 与我的重算一致（±0.06）；
- **单向强断言**：`fits == true` 时资源必须真的够 —— 0 例违反；
- `fits` 的含义是「换上去后**没有资源类硬规则**违反」（读 `resources.py:790` 确认），
  所以 `fits=false` 但资源够是可能的：f7 有 **4 例**（候选 21484/521/19023/11215），
  由**非资源类硬规则**触发，但 detail **没有给出原因字段** → 记为**建议**：
  加 `blocked_by`（否则 agent 看到“资源够却说装不上”会困惑）。

**④ `skill_tier`**：detail 给 512 条逐技能；L0 给 `{name,count,hash}`：

- 10/10 样本 `count == 512`；等级全部 ∈ {4,5}（T3C/资本模块要求 5，f7 有 11 项 5 级，合理）；
- **hash 确定性**：同一 ctx 二次渲染 `hash` 相同（`ac3d2941c96a` 等）；
- **跨层一致**：L0 `count/name` 与 detail 的 512 条/`skill_tier_name` 一致；
- **L1 没有 meta**（`render_view` 返回里无 `meta` 键）→ 不是缺陷，但三层“同名不同层”的取名
  容易让 agent 误以为 L1 也有 skill_tier，建议文档注明 L1 不含 meta。

### 15.3 变异证明（不是「写了断言就算」）

| 类 | 变异 | 是否被抓 |
|---|---|---|
| `cap_by_module` | 10.0 → 10.5 | ✅ |
| `cap_sensitivity` | stable_pct 0.0 → 12.0 | ✅ |
| `upgrade_space` | fits=true 但 free=-5 | ✅ |
| `skill_tier` | 等级 4 → 3 | ✅ |

另有**实测证据**：开发过程中我自己的两处断言写错（公式符号、取整排序）各产生
40+ / 1 条红 —— 证明这些比较是**活的**，不是永真。

### 15.4 未验（不默认通过）

| 项 | 原因 |
|---|---|
| `upgrade_space` 的**非资源类硬规则**（哪些规则让它 false） | 需要产品暴露 `blocked_by` / 规则类型，当前无法独立复算 |
| `cap_sensitivity` 与 `candidates_checked` 的对应 | 产品**没有** `candidates_checked` 字段（上限是 `MAX_CAP_SENSITIVITY=5`），无法按“查了 N 个”抽样 |
| nos / 电容注射器的**精确产出量** | `attr6 == 0`，其 `-4` 由效果脚本决定；我只能验证符号、量级与 `Σ == peak_load`，无法从属性表独立复算 50.4 / 200 |

### 15.5 一个过程事实（非缺陷）

验收期间 `noobbaozi/project/render.py`（junction 到 `evefit/`）出现一次
`NameError: name 're' is not defined` —— 是**未提交改动写盘中途**被读到（`import re` 在 166 行、
用它的正则在 270 行），几分钟后自行消失。**不是稳定缺陷**，但提醒：验收跑在“编辑中的树”上时，
瞬时红可能是假信号，**必须复跑确认**。

## 16. 改名 `unattributed` + 两条无人机 flag（本轮同步与验收）

### 16.1 改名（已同步）

| 文件 | 改动 |
|---|---|
| `field_spec.json` | `resources.hardpoints.subpaths` 的 `unclassified` → **`unattributed`**（L578）；`meaning` 同步（L588） |
| `check_hardpoints_classes.py` | 判据、取值、打印、缺陷文案全部改（L5/25/67/72/73/74/78） |
| `selftest.py` | 硬点载荷 `"unclassified": tv(0)` → **`"unattributed"`**（L223） |

**一处偏差（我按你的原则没改，请确认）**：`field_spec.json` L982/985 的 `unclassified`
**不是炮位字段**，而是 `unknown_reasons.values` 的**兜底原因**（`hard_gate: "unclassified 必须报缺陷"`），
语义与 `hardpoints.unattributed` 无关、与 `run_acceptance.py` L1520-1584 那处同类。
按你「不要动 unknown_reasons 的 unclassified」的原则，我只改了炮位那两处，并在 spec 里加了说明。
**结果：`git diff` 里 `unknown_reasons` 一字未动。**

### 16.2 两条新 flag 验证（✅ 0 缺陷）

**① 互不替代（Dominix 5×Warrior II + 1×Ogre II）**

```
bandwidth = 50.0 / 125.0（够）
flags     = ['launched_drones_exceeded', 'resource_slack']
evidence  = {"drone_count": 6, "max_launched": 5, "over": 1}
```

→ **只出架数那条**，没有 `drone_bandwidth_exceeded`、也没有「over = −75」的自相矛盾 ✅

**② f7（25 架 / 175 带宽）**

```
bandwidth = 175.0 / 50.0
flags     = ['drone_bandwidth_exceeded', 'launched_drones_exceeded']
evidence  = {"bandwidth_total": 50.0, "bandwidth_required": 175.0, "over": 125.0,
             "source": "engine:drone_bandwidth"}
feasible  = True   （我按 cpu/pg/cal 重算 = True）
```

→ 两条同时命中、**`feasible` 仍为 true**（口径未变，缺口由两条 flag 表达）✅

**③ 全样本扫描（65 个 fixture）**

- 命中 `drone_bandwidth_exceeded` **6** 个、`launched_drones_exceeded` **8** 个；
- 6/6 带宽 flag 的证据满足 `over == bandwidth_required − bandwidth_total`；
- 凡 `bandwidth.used > total` 的样本**都有**该 flag（无漏报）；
- 65/65 样本 `feasible == (cpu.free ≥ 0 ∧ pg.free ≥ 0 ∧ cal.free ≥ 0)` —— 无人机不参与 feasible ✅

**变异证明**：把两条合并成一条 → 抓到；把 `over` 少算 1 → 抓到。

### 16.3 口径写进报告（你要求的 ③）

`drone.bandwidth.used` = **机舱全部放飞所需**，不是「当前已放飞」；本产品没有「已放飞」维度。
所以 f7 的 `175 / 50` 正确读法是：**如果 25 架全部放飞**需要 175 Mbit/s，总带宽只有 50。
验收里我按这个口径解释所有带宽数字（`required == used` 已校验，±0.5）。

### 16.4 附带发现：`upgrade_space[].fits` 的语义变了（T-54）

上一轮我验到的 `fits=false`（f1 那 16 个候选）在这一轮变成 `fits=true` —— 读代码确认这是
**T-54 的追加裁决**，不是回归：

- `fits = 这次换装**没有新增**资源违规`（不再要求「变体上一条违规都没有」）；
- `inherits` = 基础配装本来就有、**换装后仍然**超的维度（换装修好的不算继承）；
- `blocked_by` = 该候选**自己引入**的维度（仅 `fits=false` 带）。

我据此把 L2 的 ③ 断言重写成 `fits == (新增维度为空)`、`blocked_by == 新增维度`、
`inherits == 基础 ∩ 候选`，**15 条假缺陷归零**。
这条**语义变更没有出现在你给我的 changelog 里**，建议记进台账（否则下一个验收员还会踩）。

### 16.5 一键脚本数字（本轮全部重跑）

| 脚本 | 结果 |
|---|---|
| `selftest.py` | exit 0（全绿） |
| `check_hardpoints_classes.py` | **7/7**，0 缺陷（改名后 `unattributed` 生效） |
| `check_detail.py` | **0 缺陷**（51 cap 条目 / 29 敏感 / 32 候选 / 5,120 条技能） |
| `check_drone_flags.py` | **0 缺陷**（Dominix 互不替代 + f7 两条 + 65 样本扫描） |
| `run_acceptance.py`（65×43） | **2,795/2,795**，`status=pass`（27 条 low，0 条 medium+） |
| `pytest`（我自己跑） | **1,101 passed** |

