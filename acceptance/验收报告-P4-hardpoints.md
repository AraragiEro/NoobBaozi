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

