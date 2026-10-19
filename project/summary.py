"""输出分层的第一阶段（L0 摘要 / L1 单视角 / 按路径取数）—— **只组装，不计算**。

权威文档：`noobbaozi/docs/输出分层与工具集-v1.md`。三个入口：

```python
render_index(ctx)          -> dict   # L0 摘要（≤ INDEX_BUDGET_CHARS 字符）
render_view(ctx, view)     -> dict   # L1 单视角（= render_overview()["views"][view]）
get_path(ctx, "mobility.speed_mps")  -> {value, provenance, reason?, source_view}
```

一句话动机：`render_overview` 对 f67 实测 **23,256 字符**（views 16,723 + meta 6,439，
其中 512 条技能占 6,007），一份「概览」要约 7,000 token，30 条船的 BR 就是 20 万 token
—— **不可用**。文档记的 29,213 与它对不上：16,723 + 6,439 + 6,007 = 29,169（差 44 是
顶层键名的开销），也就是**技能块被重复计了一次**。所以不是省几个字节的问题，而是
**同一个结果要按消费者的需要分层提供**。本模块就是那三层的组装层，`render_overview` /
`render_detail` 的行为一个字都不改（验收基线 64 样本 × 44 字段不得漂移）。

四条纪律（每条都对应一条红线）

1. **L0 不重算**：`render_index` 与 `render_view` 都走渲染层同一个构建路径
   （`render._collect_flags` + `render._view_entry` + `render.state_sensitive_attrs`），
   本模块**不碰视角数值、不做单位换算、不取整**。因此
   `render_view(ctx, v) == render_overview(ctx)["views"][v]` 是逐字节相等的
   （唯一例外见下一条）。
2. **L0 只砍元信息，不砍数值**：L0 丢掉的只有「逐叶子 provenance / unknown_reasons /
   confidence / 逐模块列表 / evidence / flag 的 message / 512 条技能明细」——**每一个业务
   数值都还在**（`provenance_counts` 给三态计数、`flags` 给完整清单 + 6–10 字 `hint`、
   `meta.skill_tier` 给 `{name, count, hash}`），并且都能用 `get_path()` 原路取回、逐位相同。
3. **L1 不降级**：单视角本身就是「全量」，`render_view` 不做预算裁剪（预算护栏只属于
   `render_overview` 的六视角合计）。因此当 overview 因超预算而降级时，
   `render_view(v)` 会比 `render_overview()["views"][v]` **多出** evidence / 未截断的
   列表——这是有意的：降到 L1 就是为了看全。默认预算下（实测 14,083 < 17,000）
   两者逐字节相同，`tests/test_summary.py` 用「先断言未降级、再逐字节比对」钉住。
4. **`get_path` 在 L1 语义上取数**：路径表达式在 L0/L1 里一致（文档 §1 的关键约束），
   所以 agent 从 L0 看到 `mobility.speed_mps` 就能直接 `get_path("mobility.speed_mps")`。
   解析顺序是 **先 L0、后 L1**：L0 里有的路径按 L0 语义返回（agent 看到什么就拿到什么），
   L0 里没有的落回 L1（`firepower.weapons[].name`、`survivability.thinnest.shield.ehp`
   这类 L1 才有的形状都取得动）。

L0 的字段清单（文档 §3，每视角 3–5 个）

| 视角 | L0 字段 |
|---|---|
| `firepower` | `weapons{count, families}` / `dps_base` / `dph` / `range_m{optimal[, max]}` / `damage_split` |
| `survivability` | `ehp_mixed` / `layers{shield, armor, hull}` / `thinnest{layer, damage_type}` / `repair_total_hp_s` |
| `mobility` | `speed_mps` / `align_time_s` / `signature_radius_m` / `propulsion_state` |
| `resources` | `cpu{output, load, free}` / `powergrid{...}` / `feasible` / `slots{high: "3/6", …}` / `hardpoints{turret{total,used,free}, launcher{…}, non_hardpoint, non_hardpoint_modules, unattributed}` |
| `constraints` | `hard_violations{count, kinds}` / `tier_violations{count, kinds}` / `needs_implant` |
| `stability` | `cap_stable_pct` / `cap_depletes_in_s` / `cap_load_gj_s` / `cap_recharge_gj_s` / `has_external_dependency` |
| 顶层 | `ship{name, group, type_id}` / `meta`（**技能档只给 `{name, count, hash}`**）/ `flags[{id, hint}]` / `provenance_counts{measured, assumed, unknown}` / `budget{chars, degraded}` |

T-40 之后的三处**形态**（都是验收方实测后裁决的，数值一个没丢）

1. **`flags` 给 `{id, hint}`**（T-39 只给 `{id}`）：`message` 太长（f47 七条合计 688 字符），
   但只有 id 又猜不出来（`propulsion_state_conflict` / `resist_hole` 这类 id 光看没有信息量）
   → 取 **6–10 字**的 `hint`，**由当次 evidence 生成**（见 `_flag_hint()`：形状规则 +
   领域词表，**没有任何「flag id → 文案」的常量表**，所以 hint 永远带当次的真实数字）。
   无 evidence 可生成时 `hint` 给 `null`。实测成本见 `INDEX_BUDGET_CHARS` 的 docstring。
2. **`provenance_counts` 用带标签对象 `{measured, assumed, unknown}`**（T-39 用的是顺序
   三元组 `[measured, assumed, unknown]`）。T-40 改回对象：**未标序的数组对 agent 是隐藏
   契约**（不查文档就不知道第 0 位是什么），多花约 230 字符值得；
   `get_path("provenance_counts.mobility.assumed")` 照样取得动。
3. **两处可逆压缩**（省字符但**不丢数值**，靠 `get_path` 还原；**字段形状不变**）：
   - `meta.module_states` 四槽位同值 → 压成单值（如 `"engine_default"`）；
   - `meta.damage_profile` 四系全 `0.25` → 压成 `"uniform"`；
     **两者都能还原**：`get_path("meta.module_states.high")` / `meta.damage_profile.em`
     在压缩形态下仍取得到原值（`META_COMPRESSED_ALIASES` → `meta.assumptions.*`）。

   > **`firepower.damage_split` 不压缩**（T-40 收敛时 Lead 的验收裁决）：L0 的**字段形状
   > 必须恒定**（对象永远对象、数组永远数组，可变的只有值），所以四系全 `None` 时仍是
   > `{"em": null, "explosive": null, "kinetic": null, "thermal": null}`，**不许整块
   > `null`**——形状一变，下游按 `damage_split.em` 取数就得先判 null，契约就退化了。
   > 30 字符换形状恒定，值。

**别名表（L0 名 → L1 底层路径，`IndexField.source` / `IndexField.aliases` 显式登记）**

| L0 字段 | 底层路径（L1） | 三态来源 |
|---|---|---|
| `firepower.dps_base` | `dps.base` | 底层叶子 |
| `firepower.dph` | `dph` | 底层叶子 |
| `firepower.range_m.optimal` / `.max` | `range.optimal_m` / `range.max_range_m` | 底层叶子 |
| `firepower.damage_split.<系>` | `damage_split.<系>` | 底层叶子（同名下钻） |
| `firepower.weapons.count` / `.families` | `weapons[].count` / `weapons[].family` **聚合** | `assumed`（合成值） |
| `survivability.layers.shield` / `.armor` / `.hull` | `layers.shield_hp` / `armor_hp` / `hull_hp` | 底层叶子（**改名**，走 `aliases`） |
| `resources.cpu.*` / `powergrid.*` | `cpu.*` / `powergrid.*` | 底层叶子（同名下钻） |
| `constraints.hard_violations.count` / `tier_violations.count` | 清单长度 **聚合** | `assumed`（合成值） |

**没有单一来源的聚合值一律给 `assumed`（合成值），绝不给 `null`** —— 这条是 T-40 修的
缺陷 2：968 处 `provenance: null` 的根因是 T-39 的 `_field_provenance()` 只认「字段路径
完全相等」，L0 用别名（`dps_base` ← `dps.base`、`layers.shield` ← `layers.shield_hp`、
`weapons.count` ← `weapons[]` 聚合）时一条都匹配不上，于是「值都对，只有可信度丢了」。
现在按**底层路径**查表，两种写法（`firepower.dps_base` 与 `firepower.dps.base`）都带三态。

三处细节是**派生值**（L1 里没有同名叶子，由本模块从 L1 可见字段组装）：

- `survivability.repair_total_hp_s` = `repair.{shield,armor,hull}.raw_hp_s` 之和
  （三层修复速率；`shield_passive` 不计入「三层」——它是被动回充，不是修复模块）；
- `mobility.propulsion_state` = 把 `propulsion_states.base` 的 `(speed, sig, align)`
  与 `active` / `propulsion_off` / `off` 逐位比对，**按这个优先级**取第一个命中的；
  都不命中给 `base`（例如数据形状变化时老实说「就是基础档」，不猜）；
- `constraints.hard_violations.kinds` 取 `hard_violations[].rule.type`、
  `tier_violations.kinds` 取 `tier_violations[].kind`；`stability.has_external_dependency`
  = `external_dependency.remote_rep or .cap_transfer`。

派生值的三态统一登记为 `assumed`（= `render.SUMMARY_PROVENANCE` 的语义：**建立在同一份
L1 数据之上的合成值**，不是「无法推断」）；它们的输入全部来自 L1 可见叶子，所以派生的
每一步都能用 `get_path()` 单独核对。

**`modes` 不在 L0**（文档 §3 的顶层清单里没有它）：模式对比是 T3D/T3C 才有的横切块，
L0 只用 `meta.mode` 交代「当前在哪个模式」；要看三模式对比走
`render_overview(ctx)["modes"]`（`get_path` 也够不到它——那是横切块，不属于任何视角）。

`budget` 的口径（`{chars, degraded}`）—— **T-41 定稿**

- `chars` = **L0 正文长度**，**不含 `budget` 块自身**。一句话定义：
  *把 `budget` 块摘掉之后，这份 L0 序列化有多长*。**没有自指问题**：它量的不是自己
  所在的那棵树，所以不需要解方程、更不需要固定点迭代。
  `render_index()` 里的三步顺序（读代码时就按这个顺序理解，**不是循环依赖**）：

  1. `body = _index_body(ctx)` —— 此刻 `body` 里**还没有** `budget` 这个键；
  2. `chars = len(json.dumps(body, ensure_ascii=False))` —— 量这个**没有** `budget` 的 body；
  3. `body["budget"] = {"chars": chars, "degraded": chars > INDEX_BUDGET_CHARS}` ——
     **最后**才把 `budget` 块塞回去；它自然不在 `chars` 里。

  逐样本恒等式（`tests/test_summary.py` 用 f1 / f12 / f47 / f67 四个样本逐位钉住，
  并带一条「改回旧算法就变红」的反证）：

  ```python
  doc["budget"]["chars"] == len(json.dumps(
      {k: v for k, v in doc.items() if k != "budget"}, ensure_ascii=False))
  ```

- **与旧 `render_overview` 的口径不同，这是一次有意的拆分（T-41）**：渲染层那条是
  「只量 `views` + `modes`」，不含顶层键与 `meta`。**直接搬到 L0 会少算近一半** ——
  实测四个样本：L0 正文 1,996–2,118 字符，而只量六个视角块只有 1,040–1,066 字符
  （差 956–1,052：`ship` / `meta` / `flags` / `provenance_counts` 与顶层键名全被漏掉）。
  所以 L0 不沿用那条公式，改为量「去掉 `budget` 块后的整棵 L0 正文」。
- `degraded` = `chars > INDEX_BUDGET_CHARS`（**严格大于**；正好等于预算**不**降级）。
  **L0 的降级是上报式、不是裁剪式**：
  红线明确禁止「为省字符丢字段值」（只能丢元信息明细），而 L0 本来就已经只含元信息，
  所以超预算时**不删任何数值**，只把 `degraded` 置位，让读者/上层自己决定是否改用
  L1/L2。这也正是文档 §7 那条红线要的效果：`degraded` 若为 true 必须在 L0 显式出现，
  不能让读者以为看到了全部。
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Callable, Mapping

from noobbaozi.project import render
from noobbaozi.project.views import available_views
from noobbaozi.project.views.base import ViewContext
from noobbaozi.project.views.loader import load_views
# 硬点类别 → 中文短名：**从投影层导入，不在这里另抄一份**（T-54 审查②：两份同值表迟早漂移；
# `tests/test_t54_audit.py` 用 `is` 断言同源）。方向是 summary → views.resources，不成环：
# `views.resources` 只依赖 `views` 包与 `views.base`，从不 import `summary`。
from noobbaozi.project.views.resources import HARDPOINT_LABELS

# =============================================================================
# 一、常量与口径
# =============================================================================

INDEX_BUDGET_CHARS: int = 2500
"""L0 的字符预算（`len(json.dumps(L0 正文))`）。

**T-40 上调：2,000 → 2,500**（验收方实测后裁决，不是拍的）：

| 时点 | 实测 L0 字符数（65 样本，**含** `budget` 块） | 预算 |
|---|---|---|
| T-39 | min **1,722** / median **1,859** / max **1,987** | 2,000 |
| **T-40** | min **1,801** / median **2,000** / max **2,183** | **2,500** |

T-41 定稿后，判据量的是**正文**（不含 `budget` 块），上表数字减去 `budget` 块的固定开销
就是预算真正比较的那个数 —— 65 样本实测这笔开销**恒为 46 字符**
（`, "budget": {"chars": N, "degraded": false}`，正文四位数、`degraded` 为 false 时），
即正文区间：T-39 **1,676–1,941**、T-40 **1,755–2,137**。口径定义见模块 docstring
末尾「`budget` 的口径」。

原 2,000 是按 T-39 的四个样本重定的，而验收方跑 65 样本的实测区间是 **1,722–1,987**；
再叠加 T-40 的两项**净增**（`flags` 的 `hint`、`provenance_counts` 改带标签对象）必然
越过 2,000。**2,500 仍比旧 `render_overview` 的 f67 实测 23,256 低 89%**（护栏意义不变：
一份「概览」约 7,000 token → 约 800 token）。

T-40 的两处压缩（`module_states` 同值 → 单值、`damage_profile` 均匀 → `"uniform"`）是
**净省**（`meta` 占 L0 的最高占比从 21.9% 降到 **12.9%**），但它们的作用是抵消新增开销，
而不是「为省字符丢数值」—— 见模块 docstring 第 3 条：压缩都可逆，`get_path` 原路取回原值。
净增的开销实测：`hint` **均 86.5 字符/样本**（276 条 flag、hint 正文均 7.9 字，
最肥的 7 条 flag 样本 146）、`provenance_counts` 改对象 **约 +190**（6 视角 × 32）。

`degraded` 只在更肥的配装上置位（**只上报，不裁剪** —— 见模块 docstring 末尾「`budget`
的口径」；这条分支由 `tests/test_summary.py` 用 monkeypatch 把预算调小来实测，
验收脚本 `check_layers.py` 也会把预算压到 1 跑一遍）。
"""

HINT_MIN_CHARS: int = 6
"""`flags[].hint` 的下限（文档 §3：6–10 字，够看清「是什么」，又放得进预算）。"""

HINT_MAX_CHARS: int = 10
"""`flags[].hint` 的上限（超过就截断——hint 是提示，不是诊断正文；正文在 L1）。"""

DAMAGE_PROFILE_UNIFORM: str = "uniform"
"""`meta.damage_profile` 的四系**全相等**时压缩成的单值（默认 25/25/25/25）。"""

META_COMPRESSED_ALIASES: dict[str, str] = {
    "module_states": "assumptions.module_states",
    "damage_profile": "assumptions.damage_profile",
}
"""`meta` 里**可压缩块 → 完整版路径**（`get_path` 的还原通道）。

L0 把这两个块压成单值（`"engine_default"` / `"uniform"`）之后，`meta.module_states.high`
这种「按槽位取」的路径在 L0 里就不再存在；本表把它翻译成 `meta.assumptions.module_states.high`
（`render._meta()` 的完整形状）去取 —— **压缩可逆**是 T-40 的硬要求，
`tests/test_summary.py` 用「压缩前后取到的值逐位相同」钉住。
"""

SKILL_HASH_DIGITS: int = 12
"""技能档摘要的前缀长度（sha1 十六进制前 12 位）。"""

DERIVED_PROVENANCE: str = "assumed"
"""派生值（L0 独有、由 L1 可见叶子组装）的三态，与 `render.SUMMARY_PROVENANCE` 同义。"""

PROVENANCE_STATES: tuple[str, ...] = ("measured", "assumed", "unknown")
"""三态取值；聚合时按「最弱的那个」算（见 `_weakest()`）。"""

_WEAKEST_RANK: dict[str, int] = {"measured": 0, "assumed": 1, "unknown": 2}
"""三态的保守序：节点聚合一律取**最弱**的状态（不允许比最弱的出现更自信）。"""

LAYER_KEYS: tuple[str, ...] = ("shield", "armor", "hull")
"""三层结构（`survivability.layers` / `repair` 的层名，文档 §3「生存」）。"""

PROPULSION_STATES: tuple[str, ...] = ("active", "propulsion_off", "off")
"""`mobility.propulsion_state` 可能命中的推进档（文档 §3「机动」）。

**按这个优先级**取第一个与 `base` 逐位相同的档：`active` 优先，其次「只关推进」，
最后「全模块离线」。都不命中给 `base`。
"""

PROPULSION_TUPLE: tuple[str, ...] = ("speed_mps", "sig_m", "align_time_s")
"""比对两档推进状态时用的三个量（速度 / 信号半径 / 对齐时间，都是物理单位）。"""

REPAIR_DIGITS: int = 6
"""`repair_total_hp_s` 求和后保留的小数位（只用来吃掉浮点累加误差，不改 3 位小数的数据）。"""

TOP_LEVEL_KEYS: tuple[str, ...] = ("ship", "meta", "flags", "provenance_counts", "budget")
"""L0 的顶层非视角块（文档 §3「顶层」）。`budget` 由 `render_index` 最后补上。"""

_MISSING: object = object()
"""字段没取到值的哨兵（`None` 是合法值：`dph` / `stable_pct` 都可能真的是 `None`）。"""


# =============================================================================
# 二、L0 字段登记表（唯一的事实来源：组装与取数都读它）
# =============================================================================


@dataclass(frozen=True, slots=True)
class IndexField:
    """一条 L0 字段的登记：L0 路径 → L1 来源（或一个纯派生函数）。

    Attributes:
        path: L0 里的点路径（相对视角，如 `range_m.max`）；点号表示嵌套。
        source: L1 里对应叶子的点路径（相对视角 overview，如 `range.max_range_m`）；
            写 `None` 表示「纯派生 / 聚合值」——没有单一对应叶子，三态记 `assumed`。
        read: 派生函数 `overview -> value`；`None` 表示按 `source` 取值。
        omit_if_none: 值为 `None` 时**不写这个键**（只给 `range_m.max`：没有导弹时
            文档 §3 明确「只给 optimal」，`None` 的 max 是噪声）。
        synthetic: 纯派生（与 `source is None` 等价），保留独立标记让 `_field_provenance`
            的意图更好读。
        aliases: **子键级别名表** `({L0 子键: L1 叶子})`，只给「L0 换了键名」的块用
            （`layers.shield` ← `layers.shield_hp`）。L0 与 L1 同名的块（`cpu` /
            `powergrid` / `damage_split`）不需要它 —— `source` 本身就能当下钻前缀，
            见 `_underlying_paths()`。
    """

    path: str
    source: str | None = None
    read: Callable[[Mapping[str, object]], object] | None = None
    omit_if_none: bool = False
    synthetic: bool = False
    aliases: tuple[tuple[str, str], ...] = ()


def _weapons_block(overview: Mapping[str, object]) -> dict:
    """`weapons` 清单 → `{count, families}`（**不列名字**，文档 §3「火力」）。

    `count` 是**武器件数之和**（`weapons[].count` 是同一 `(type_id, 弹药)` 的合并条数，
    f1 的 5 门 Light Neutron Blaster II 是一条 `count: 5` 的记录），不是清单长度。
    `families` 去重且排序（同一份输入恒得同一个列表，便于对比战报）。
    """
    rows = [row for row in (overview.get("weapons") or []) if isinstance(row, dict)]
    total = 0
    families: list[str] = []
    for row in rows:
        count = row.get("count")
        total += int(count) if isinstance(count, (int, float)) else 1
        family = row.get("family")
        if family is not None and str(family) not in families:
            families.append(str(family))
    return {"count": total, "families": sorted(families)}


def _layers_block(overview: Mapping[str, object]) -> dict:
    """`layers.{shield_hp, armor_hp, hull_hp}` → `{shield, armor, hull}`（**只改键名**）。"""
    layers = overview.get("layers") if isinstance(overview.get("layers"), dict) else {}
    return {layer: layers.get(f"{layer}_hp") for layer in LAYER_KEYS}


def _thinnest_block(overview: Mapping[str, object]) -> dict:
    """三层里**最薄的一层**（该层最弱抗性的 EHP 最小）与该层的伤害类型。

    `survivability.thinnest` 是**按层**给「最弱系 + 该系下的层 EHP」；L0 只要一层，
    取 `ehp` 最小的那层（并列时保留前面的层，顺序 = 视角给的 shield/armor/hull）。
    该层四系共振率并列时视角给 `damage_type: None`（不存在「最薄系」）——这里照实透传，
    不编一个伤害类型出来。
    """
    rows = overview.get("thinnest") if isinstance(overview.get("thinnest"), dict) else {}
    best_layer: str | None = None
    best_type: object = None
    best_ehp: float | None = None
    for layer, row in rows.items():
        if not isinstance(row, dict):
            continue
        ehp = row.get("ehp")
        if not isinstance(ehp, (int, float)):
            continue
        if best_ehp is None or ehp < best_ehp:
            best_layer, best_ehp, best_type = str(layer), float(ehp), row.get("damage_type")
    return {"layer": best_layer, "damage_type": best_type}


def _repair_total(overview: Mapping[str, object]) -> float:
    """三层修复速率之和（hp/s，0 也照给；`shield_passive` 不是「三层」故不计入）。"""
    repair = overview.get("repair") if isinstance(overview.get("repair"), dict) else {}
    rates: list[float] = []
    for layer in LAYER_KEYS:
        row = repair.get(layer)
        rate = row.get("raw_hp_s") if isinstance(row, dict) else None
        rates.append(float(rate) if isinstance(rate, (int, float)) else 0.0)
    return round(math.fsum(rates), REPAIR_DIGITS)


def _same_propulsion_state(current: Mapping[str, object], other: Mapping[str, object]) -> bool:
    """两档推进状态是否**逐位相同**（速度 / 信号半径 / 对齐时间）。"""
    compared = 0
    for key in PROPULSION_TUPLE:
        if current.get(key) != other.get(key):
            return False
        if current.get(key) is not None:
            compared += 1
    return compared > 0


def _propulsion_state(overview: Mapping[str, object]) -> str:
    """当前推进档（`active` / `propulsion_off` / `off`，都不命中给 `base`）。

    判据只有「`base` 与哪一档逐位相同」，**不猜模块名**：`propulsion_states` 是视角
    已经算好的五个档（base / off / propulsion_off / active / overload），
    当前实际处于哪一档由「基础变体等于哪一档」唯一决定。
    """
    states = overview.get("propulsion_states")
    if not isinstance(states, dict):
        return "base"
    current = states.get("base")
    if not isinstance(current, dict):
        return "base"
    for name in PROPULSION_STATES:
        other = states.get(name)
        if isinstance(other, dict) and _same_propulsion_state(current, other):
            return name
    return "base"


def _slots_block(overview: Mapping[str, object]) -> dict:
    """`slots` → `{high: "3/6", …}`（`已用/上限`，字符串；类型来自数据，不硬编码四类）。"""
    slots = overview.get("slots") if isinstance(overview.get("slots"), dict) else {}
    result: dict[str, str] = {}
    for name, row in slots.items():
        if not isinstance(row, dict):
            continue
        result[str(name)] = f"{_slot_number(row.get('used'))}/{_slot_number(row.get('total'))}"
    return result


def _slot_number(value: object) -> str:
    """槽位数 → 字符串（整数不写 `.0`，非数字老实 `str()`）。"""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        return str(int(value)) if float(value).is_integer() else str(value)
    return "0" if value is None else str(value)


def _hard_violations_block(overview: Mapping[str, object]) -> dict:
    """`hard_violations` → `{count, kinds}`（kind 取 `rule.type`，如 `max_group`）。"""
    rows = overview.get("hard_violations")
    return _violations_block(
        rows,
        lambda row: (row.get("rule") or {}).get("type") if isinstance(row, dict) else None,
    )


def _tier_violations_block(overview: Mapping[str, object]) -> dict:
    """`tier_violations` → `{count, kinds}`（kind 取元素自带的 `kind`，如 `resource`）。"""
    return _violations_block(
        overview.get("tier_violations"),
        lambda row: row.get("kind") if isinstance(row, dict) else None,
    )


def _violations_block(rows: object, kind_of: Callable[[object], object]) -> dict:
    """违约清单 → `{count, kinds}`（`kinds` 去重保序，`count` = 条数）。"""
    items = [row for row in (rows or []) if isinstance(row, dict)] if isinstance(rows, list) else []
    kinds: list[str] = []
    for row in items:
        kind = kind_of(row)
        if kind is not None and str(kind) not in kinds:
            kinds.append(str(kind))
    return {"count": len(items), "kinds": kinds}


def _has_external_dependency(overview: Mapping[str, object]) -> bool:
    """是否依赖外部来源（遥修 / 传电任一为真）。"""
    external = overview.get("external_dependency")
    if not isinstance(external, dict):
        return False
    return bool(external.get("remote_rep")) or bool(external.get("cap_transfer"))


INDEX_SPEC: dict[str, tuple[IndexField, ...]] = {
    "firepower": (
        IndexField("weapons", read=_weapons_block, synthetic=True),
        IndexField("dps_base", source="dps.base"),
        IndexField("dph", source="dph"),
        IndexField("range_m.optimal", source="range.optimal_m"),
        IndexField("range_m.max", source="range.max_range_m", omit_if_none=True),
        # `damage_split` **不做任何压缩**：四系全 None 时仍是四键 + 四个 null
        # （Lead 裁决：L0 的字段形状恒定，对象永远对象）。见模块 docstring 第 3 条。
        IndexField("damage_split", source="damage_split"),
    ),
    "survivability": (
        IndexField("ehp_mixed", source="ehp_mixed"),
        IndexField(
            "layers",
            read=_layers_block,
            synthetic=True,
            # L0 换了键名（`shield` ← `shield_hp`）：别名表是**必需**的，否则
            # `get_path("survivability.layers.shield")` 查不到底层叶子（缺陷 2 的 3/16 类）。
            aliases=(
                ("shield", "layers.shield_hp"),
                ("armor", "layers.armor_hp"),
                ("hull", "layers.hull_hp"),
            ),
        ),
        IndexField("thinnest", read=_thinnest_block, synthetic=True),
        IndexField("repair_total_hp_s", read=_repair_total, synthetic=True),
    ),
    "mobility": (
        IndexField("speed_mps", source="speed_mps"),
        IndexField("align_time_s", source="align_time_s"),
        IndexField("signature_radius_m", source="signature_radius_m"),
        IndexField("propulsion_state", read=_propulsion_state, synthetic=True),
    ),
    "resources": (
        IndexField("cpu", source="cpu"),
        IndexField("powergrid", source="powergrid"),
        IndexField("feasible", source="feasible"),
        IndexField("slots", read=_slots_block, synthetic=True),
        # 炮位（T-54）：**必须出现在 L0**——只看到 `slots.high` 时 agent 会把「高槽 5 个」
        # 当成「5 个发射器挂点」，进而得出「白扔两个发射器」（战报 139274776 的实际缺陷）。
        # 形状原样透传（与 L1 同一份 dict）：`turret` / `launcher` 各 `{total, used, free}`，
        # `free` 可以为负（负数 = 非法装配，`hardpoint_violation` flag 报）。
        IndexField(
            "hardpoints",
            source="hardpoints",
            aliases=(
                ("turret.total", "hardpoints.turret.total"),
                ("turret.used", "hardpoints.turret.used"),
                ("turret.free", "hardpoints.turret.free"),
                ("launcher.total", "hardpoints.launcher.total"),
                ("launcher.used", "hardpoints.launcher.used"),
                ("launcher.free", "hardpoints.launcher.free"),
                # T-54 审查①：两种「不计入硬点」的计数都要能在 L0 取到
                # （`non_hardpoint` = 确定不占硬点；`unattributed` = 真分不清，语义收窄；
                # T-1/5 改名：旧名 `unclassified` 与 `unknown_reasons` 的兜底值撞名，已删）
                ("non_hardpoint", "hardpoints.non_hardpoint"),
                ("non_hardpoint_modules", "hardpoints.non_hardpoint_modules"),
                ("unattributed", "hardpoints.unattributed"),
            ),
        ),
    ),
    "constraints": (
        IndexField("hard_violations", read=_hard_violations_block, synthetic=True),
        IndexField("tier_violations", read=_tier_violations_block, synthetic=True),
        IndexField("needs_implant", source="needs_implant"),
    ),
    "stability": (
        IndexField("cap_stable_pct", source="capacitor.stable_pct"),
        IndexField("cap_depletes_in_s", source="capacitor.depletes_in_s"),
        IndexField("cap_load_gj_s", source="capacitor.peak_load"),
        IndexField("cap_recharge_gj_s", source="capacitor.peak_recharge"),
        IndexField("has_external_dependency", read=_has_external_dependency, synthetic=True),
    ),
}
"""L0 的字段登记表（**唯一事实来源**）：视角名 → 字段列表。

`render_index` 按它组装 L0，`get_path` 按它查三态——两处读同一张表，所以
「L0 的每个数值都能被 `get_path` 取到」不是靠测试对齐，而是**构造上不可能漂移**。
每个视角的**顶层键**必须落在文档 §3 的 3–5 个之间（`tests/test_summary.py` 钉住）。

**别名 → 底层路径的三种形态**（`get_path` 的 provenance 解析只认这三种，见
`_underlying_paths()`）：

1. **整字段改名**：`source` 就是底层路径 —— `dps_base` ← `dps.base`、
   `dph` ← `dph`、`range_m.optimal` ← `range.optimal_m`、`cap_load_gj_s` ←
   `capacitor.peak_load` …（`source` 为 `None` 的不属于这一类）；
2. **块内子键改名**：`aliases` 给出「L0 子键 → L1 叶子」—— `survivability.layers`
   （`shield` ← `layers.shield_hp`）与 `resources.hardpoints`（`turret.total` ←
   `hardpoints.turret.total`，T-54：块形状原样透传，但**逐子键**登记别名才能在
   `get_path("resources.hardpoints.launcher.free")` 上取到真正的三态——`total` 是
   `measured`、`used` / `free` 是 `assumed`，一整块只记一条会把 `measured` 也拖成 `assumed`）；
3. **同名块下钻**：`source` 本身当下钻前缀 —— `damage_split.em` ← `damage_split.em`、
   `resources.cpu.output` ← `cpu.output`、`powergrid.load` ← `powergrid.load`。
   这三块**不需要** `aliases`（键名一模一样），但**必须**能下钻，否则
   `get_path("firepower.damage_split.em")` 之类的 16 类路径里最粗的那几类会返回 `null`。

**聚合值（没有单一底层叶子）**：`weapons.count` / `weapons.families`（`weapons[]` 求和 /
去重）、`hard_violations.count` / `.kinds`、`tier_violations.count` / `.kinds`、
`slots.*`、`thinnest.*`、`repair_total_hp_s`、`propulsion_state`、`has_external_dependency`
—— 它们登记为 `synthetic=True` 且 `source=None`，三态一律 `assumed`（**合成值**语义：
建立在同一份 L1 数据之上），**绝不给 `null`**。
"""


# =============================================================================
# 三、L0 的组装
# =============================================================================


def skill_tier_hash(skills: Mapping[object, object]) -> str:
    """技能档的**稳定摘要**：按 `type_id` 排序后 `type_id:level` 拼起来取 sha1 前 12 位。

    - 排序键是 `int(type_id)`（`assumptions.skill_tier` 是 `{技能 type_id: 等级}`），
      所以同一份技能档恒得同一个 hash（**与字典插入顺序、`PYTHONHASHSEED` 无关**）；
    - 只摘要**内容**，不含键名/路径，换 SDE 不改协议；
    - 完整技能表仍在 `render_detail` / `get_path("meta.assumptions.skill_tier")`，
      L0 只给 `{name, count, hash}`（文档 §4：512 条键值对 = 6,007 字符 → 约 60）。
    """
    canonical = ",".join(
        f"{int(key)}:{int(value)}"
        for key, value in sorted(skills.items(), key=lambda item: int(item[0]))
    )
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:SKILL_HASH_DIGITS]


def _skill_tier_block(assumptions: Mapping[str, object]) -> dict:
    """`assumptions.skill_tier` → `{name, count, hash}`（**不含 512 条明细**）。"""
    skills = assumptions.get("skill_tier")
    if not isinstance(skills, dict) or not skills:
        return {"name": assumptions.get("skill_tier_name"), "count": 0, "hash": None}
    return {
        "name": assumptions.get("skill_tier_name"),
        "count": len(skills),
        "hash": skill_tier_hash(skills),
    }


def _ship_block(ctx: ViewContext) -> dict:
    """`ship` → `{name, group, type_id}`（名字与组名走 SDE 索引，缺失给 `None`）。"""
    fit = ctx.fit if isinstance(ctx.fit, dict) else {}
    ship = fit.get("ship") if isinstance(fit.get("ship"), dict) else {}
    type_id = ship.get("type_id")
    row = ctx.index.get_type(type_id) if _is_index_ready(ctx, type_id) else None
    return {
        "name": (row or {}).get("name"),
        "group": (row or {}).get("group_name"),
        "type_id": type_id,
    }


def _is_index_ready(ctx: ViewContext, type_id: object) -> bool:
    """索引能用且 `type_id` 是整数（`index=None` 的测试路径照样跑）。"""
    return isinstance(type_id, int) and getattr(ctx, "index", None) is not None


def _module_states_block(assumptions: Mapping[str, object]) -> object:
    """`assumptions.module_states` → **四槽位同值就压成单值**（T-40 压缩②，省约 100 字符）。

    现状（`engine/assumptions.derive_module_states()`）恒为
    `{"high": "engine_default", "medium": "engine_default", "low": "engine_default",
    "rig": "engine_default"}` —— 四个键重复同一个字符串，压成 `"engine_default"` 后
    语义不变（「所有槽位的状态来源都是引擎默认」）。

    **只在四值全同时压缩**：不同值（未来真按槽位记来源时）原样保留整张表。
    还原通道：`get_path("meta.module_states.high")` → `META_COMPRESSED_ALIASES` →
    `meta.assumptions.module_states.high`（压缩不改真值）。
    """
    states = assumptions.get("module_states")
    if isinstance(states, dict) and states:
        values = list(states.values())
        if all(value == values[0] for value in values):
            return values[0]
    return states


def _damage_profile_block(assumptions: Mapping[str, object]) -> object:
    """`assumptions.damage_profile` → **四系全相等就压成 `"uniform"`**（T-40 压缩③）。

    默认剖面是 25/25/25/25（`engine/assumptions.DEFAULT_DAMAGE_PROFILE`），四个 `0.25`
    在 L0 里只是重复声明「均匀」；压成 `"uniform"` 省约 50 字符，且语义更直接。

    **只在四值全相等时压缩**（`0.25/0.25/0.25/0.25` 与 `0.5/0.5/0/0` 不同：后者不均匀）。
    还原通道：`get_path("meta.damage_profile.em")` → `meta.assumptions.damage_profile.em`。
    """
    profile = assumptions.get("damage_profile")
    if isinstance(profile, dict) and profile:
        values = list(profile.values())
        if all(value == values[0] for value in values):
            return DAMAGE_PROFILE_UNIFORM
    return profile


def _meta_block(ctx: ViewContext) -> dict:
    """`meta` → 版本三元组 + **压缩后的**假设块（技能档只给 `{name, count, hash}`）。

    与 `render._meta()` 的区别只有一处：`assumptions` 被压成
    `skill_tier / module_states / damage_profile / mode` 四项。不做假设推导
    （红线 N9：`ctx.assumptions` 是唯一来源，这里只读不改）。

    `module_states` / `damage_profile` 再各走一层**可逆压缩**（见上面两个函数）：
    L0 只留单值，完整形状与逐槽位取值走 `get_path`。
    """
    engine_meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    assumptions = ctx.assumptions if isinstance(ctx.assumptions, dict) else {}
    return {
        "sde_build": engine_meta.get("sde_build"),
        "engine_version": engine_meta.get("engine_version"),
        "spec_version": engine_meta.get("spec_version"),
        "skill_tier": _skill_tier_block(assumptions),
        "module_states": _module_states_block(assumptions),
        "damage_profile": _damage_profile_block(assumptions),
        "mode": assumptions.get("mode_id"),
    }


def _entries(ctx: ViewContext, targets: tuple[str, ...]) -> tuple[dict[str, dict], list[dict]]:
    """按需构建视角条目（**只构建被请求的那些**），返回 `(entries, residual_flags)`。

    复用渲染层同一条路径（`_collect_flags` 只算一次诊断 + `_view_entry` +
    一次状态敏感性探测），所以拿到的条目与 `render_overview()["views"]` 逐字节相同。
    """
    attributed, residual = render._collect_flags(ctx, targets)
    state_sensitive = render.state_sensitive_attrs(ctx)
    entries = {
        name: render._view_entry(
            name, ctx, attributed, scope="overview", state_sensitive=state_sensitive
        )
        for name in targets
    }
    return entries, residual


# -----------------------------------------------------------------------------
# flags 的 `hint`（T-40）：从**当次 evidence** 生成 6–10 字摘要
# -----------------------------------------------------------------------------

_LAYER_LABELS: dict[str, str] = {"shield": "护盾", "armor": "装甲", "hull": "结构"}
"""三层的短名（`survability.layers` 的层名 → 中文）。**只翻译领域词**，不含任何文案。"""

_DAMAGE_LABELS: dict[str, str] = {
    "em": "电磁",
    "explosive": "爆炸",
    "kinetic": "动能",
    "thermal": "热能",
}
"""四系伤害的短名（与 `views/shared.DAMAGE_TYPES` 同一组词，只做中文转写）。"""

_RESOURCE_LABELS: dict[str, str] = {"cpu": "CPU", "power": "PG", "powergrid": "PG", "calibration": "标定"}
"""资源三项的短名（`*_deficit` 的键去掉后缀之后 → 显示名）。"""

# 硬点类别的中文短名统一由 `views.resources.HARDPOINT_LABELS` 提供（见顶部 import）。


def _hint_label(table: Mapping[str, str], value: object) -> str:
    """领域词 → 短名；**表外一律原样返回**（不猜、不编一个中文名）。"""
    text = str(value)
    return table.get(text, text)


def _hint_number(value: object) -> str:
    """数字 → 极短字符串（整数不写 `.0`，浮点保持 `str()` 的精度，非数字老实 `str()`）。"""
    if isinstance(value, bool):
        return str(int(value))
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:g}"
    return str(value)


def _hint_compact(value: object) -> str:
    """去掉所有空白（`"MWD 与 AB"` → `"MWD与AB"`）：hint 的字数预算很紧。"""
    return "".join(str(value).split())


def _hint_token(value: object) -> str:
    """取一个名字的**首个词**再截到 5 字符（模块全名太长：`5MN Y-T8 Compact MWD` → `5MN`）。"""
    parts = str(value).split()
    return parts[0][:5] if parts else ""


def _hint_deficit(evidence: Mapping[str, object]) -> str:
    """`*_deficit` 里**最大**的那一项 → `"CPU超14.3"` / `"PG超64.71"`（超得最多的是主因）。"""
    deficits = [
        (key, value)
        for key, value in evidence.items()
        if key.endswith("_deficit")
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > 0
    ]
    if not deficits:
        return ""
    key, value = max(deficits, key=lambda pair: pair[1])
    label = _hint_label(_RESOURCE_LABELS, key[: -len("_deficit")])
    return f"{label}超{_hint_number(value)}"


def _hint_from_scalars(evidence: Mapping[str, object]) -> str:
    """兜底：把**前两个标量**事实拼成 `键=值`（新 flag 复用已知形状就不会走到这里）。"""
    parts: list[str] = []
    for key, value in evidence.items():
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            continue
        parts.append(f"{key}={_hint_number(value) if not isinstance(value, str) else value}")
        if len(parts) == 2:
            break
    return " ".join(parts)


_HINT_SHAPES: tuple[tuple[tuple[str, ...], Callable[[Mapping[str, object]], str]], ...] = (
    # 推进状态冲突：evidence.conflict 已经是人话（"AB 与 MWD"）→ "AB与MWD同时启用"
    (("conflict",), lambda e: f"{_hint_compact(e['conflict'])}同时启用"),
    # 抗性洞：最薄的那一层 + 最弱的系 → "装甲最弱:爆炸"
    (
        ("layer", "weakest_damage"),
        lambda e: (
            f"{_hint_label(_LAYER_LABELS, e['layer'])}最弱:"
            f"{_hint_label(_DAMAGE_LABELS, e['weakest_damage'])}"
        ),
    ),
    # 资源超预算（按全 active 算装不下）：开着的模块数是这条的专属证据 → "11件全active"
    (("open_state_modules",), lambda e: f"{_hint_number(e['open_state_modules'])}件全active"),
    # 资源溢出（余量为负）→ "CPU超11" / "PG超64.71"
    (("cpu_deficit",), _hint_deficit),
    # MWD 把信号半径撑大 → "信号×5.5"
    (("sig_ratio",), lambda e: f"信号×{_hint_number(e['sig_ratio'])}"),
    # 高槽空 + **已在用的那一类**硬点没装满 → "高槽空3;发射器1"
    # （T-54 审查：旧的 ("high_empty","high_total") 形状只给 "高槽空1/5"，把
    #  「哪一类硬点还有余」整个丢掉了 —— 而那正是这条 flag 的全部信息量）
    (
        ("free_kinds", "high_empty"),
        lambda e: f"高槽空{_hint_number(e['high_empty'])};{_hint_compact(e['free_kinds'])}",
    ),
    # 高槽有挂点但空着 → "高槽空3/4"（没有 free_kinds 时的兜底）
    (
        ("high_empty", "high_total"),
        lambda e: f"高槽空{_hint_number(e['high_empty'])}/{_hint_number(e['high_total'])}",
    ),
    # 炮/发射器超装（free 为负）→ "发射器超2" / 两类都超 → "炮位超1+发射器超1"
    # （`over_kinds` 是预渲染的全部类别；没有它的旧数据退回只报最严重的那一类）
    (("over_kinds",), lambda e: _hint_compact(e["over_kinds"])),
    (
        ("kind", "over"),
        lambda e: f"{_hint_label(HARDPOINT_LABELS, e['kind'])}超{_hint_number(e['over'])}",
    ),
    # 关掉这一件电容就稳 → "关5MN就稳"
    (("module", "stable_pct_after"), lambda e: f"关{_hint_token(e['module'])}就稳"),
    # 机库里有载机但没起飞 → "机库8架未飞"
    (("fighter_bay_count",), lambda e: f"机库{_hint_number(e['fighter_bay_count'])}架未飞"),
    # 有弹药却零齐射 → "5门有弹零伤害"
    (("armed_weapons", "dph_hp"), lambda e: f"{_hint_number(e['armed_weapons'])}门有弹零伤害"),
    # 电容会耗尽（有修复速率也不稳）→ "电容38.4s耗尽"
    (("cap_depletes_in_s",), lambda e: f"电容{_hint_number(e['cap_depletes_in_s'])}s耗尽"),
    # 无人机带宽闲着 → "带宽50未用"
    (("bandwidth_total", "drones_carried"), lambda e: f"带宽{_hint_number(e['bandwidth_total'])}未用"),
    # 一件推进模块都没有 → "无推进模块22件"
    (
        ("propulsion_modules", "fitted_modules"),
        lambda e: f"无推进模块{_hint_number(e['fitted_modules'])}件",
    ),
    # 齐射/秒伤与武器周期不符 → "周期比0.0s"
    (("ratio_s", "cycle_max_s"), lambda e: f"周期比{_hint_number(e['ratio_s'])}s"),
    # 空槽闲置 → "空槽16个闲置"
    (("empty_slots",), lambda e: f"空槽{_hint_number(e['empty_slots'])}个闲置"),
)
"""evidence **形状** → hint 生成器（**表的键是 evidence 的字段名组合，不是 flag id**）。

为什么按形状而不按 id：hint 必须**跟着当次数据走**（`"装甲最弱:爆炸"` 换成另一条船就是
`"护盾最弱:电磁"`），所以不能写一张 `{flag_id: 文案}` 的常量表——那种表第一次换样本就过时。
按形状登记之后：① 同一个 flag 在不同配装上得到**不同的真实数字**；② 新 flag 只要复用
已有的 evidence 形状就自动有 hint；③ 表的每一条都只在「这些键同时存在」时命中，
不存在的键不会编出数字。域词表（`_LAYER_LABELS` / `_DAMAGE_LABELS` / `_RESOURCE_LABELS`）
只翻译 **evidence 里本来就有的值**（`armor` / `explosive` / `cpu`），不产生任何新事实。
"""


def _hint_from_evidence(evidence: Mapping[str, object]) -> str:
    """按形状表生成 hint（全都不命中时退回前两个标量事实；仍为空 → 调用方给 `null`）。"""
    for keys, formatter in _HINT_SHAPES:
        if all(key in evidence for key in keys):
            text = formatter(evidence)
            if text:
                return text
    return _hint_from_scalars(evidence)


def _flag_hint(flag: Mapping[str, object]) -> str | None:
    """一条 flag → **6–10 字**的 hint（`None` = 没有 evidence 可生成）。

    长度口径：形状表里的模板设计上都落在 6–10 之间；超过 `HINT_MAX_CHARS` 截断
    （hint 是「要不要展开看」的提示，不是诊断正文），更短就照实给——**不填充假字**。
    """
    evidence = flag.get("evidence")
    if not isinstance(evidence, dict) or not evidence:
        return None
    text = " ".join(str(_hint_from_evidence(evidence)).split())
    if not text:
        return None
    return text[:HINT_MAX_CHARS]


def _flag_pairs(entries: Mapping[str, dict], residual: list[dict] | None = None) -> list[dict]:
    """所有 flag → `[{id, hint}]`（**不带 `message`、不带 `evidence`**）。

    为什么 L0 连 `message` 都不带：

    - **一条不丢**：L0 的 flag 清单是**完整**的（视角顺序 + 顶层兜底桶），只是把
      「为什么」留给 L1——`id` + 6–10 字的 `hint` 已经够回答「要不要继续看」；
    - **换成 message 就装不下**：实测 f47 有 7 条 flag，带 message 时 `flags` 单块
      **688 字符**（六视角合计才 960），无论怎么截都过不了预算，且正文被截成碎句；
    - 反过来「只留前 N 条」更糟：那会**真的丢诊断**（渲染层 docstring 的红线是
      「绝不丢弃，丢一条就少一条可疑点」）。

    `hint` 由 `_flag_hint()` **从当次 evidence 生成**（T-40）：没有 evidence 的 flag
    给 `null`（不编内容）。完整 message 与 evidence 都在
    `render_view()["flags"]` / `get_path("flags[0].message")`。

    顺序 = 视角顺序 + 顶层兜底桶（与 `render_overview` 的归位一致）。
    """
    pairs: list[dict] = []
    for entry in entries.values():
        for flag in entry.get("flags") or []:
            if isinstance(flag, dict):
                pairs.append({"id": flag.get("id"), "hint": _flag_hint(flag)})
    for flag in residual or []:
        if isinstance(flag, dict):
            pairs.append({"id": flag.get("id"), "hint": _flag_hint(flag)})
    return pairs


def _provenance_counts(entries: Mapping[str, dict]) -> dict[str, dict[str, int]]:
    """每视角的 **`{measured, assumed, unknown}` 计数**（逐叶子表属 L1，文档 §2）。

    文档 §3（T-40 裁决后）就是**带标签对象**。T-39 曾用顺序三元组
    `[2, 9, 19]` 省 204 字符，但**未标序的数组对 agent 是隐藏契约**（不查文档就不知道
    第 0 位是哪个态）——多花约 230 字符换一个自解释的形状，值得。
    `get_path("provenance_counts.mobility.assumed")` 按名字直接取得动（键就是名字，
    不再需要「按下标猜」的回退）。
    """
    counts: dict[str, dict[str, int]] = {}
    for name, entry in entries.items():
        bucket = {state: 0 for state in PROVENANCE_STATES}
        for state in (entry.get("provenance") or {}).values():
            if state in bucket:
                bucket[state] += 1
        counts[name] = bucket
    return counts


def _index_body(ctx: ViewContext) -> tuple[dict, dict[str, dict]]:
    """L0 正文（不含 `budget`）与本次构建的视角条目（供 `get_path` 复用同一份数据）。"""
    targets = tuple(load_views())
    entries, residual = _entries(ctx, targets)
    body: dict = {"ship": _ship_block(ctx), "meta": _meta_block(ctx)}
    for name in targets:
        body[name] = _view_index(name, entries[name].get("overview") or {})
    body["flags"] = _flag_pairs(entries, residual)
    body["provenance_counts"] = _provenance_counts(entries)
    return body, entries


def render_index(ctx: ViewContext) -> dict:
    """L0 摘要：六视角各 3–5 个数 + 精简 flag + provenance **计数** + 压缩 meta。

    Args:
        ctx: 视角上下文（六视角共用同一个；变体缓存在它身上）。

    Returns:
        `{"ship", "meta", "firepower", …, "stability", "flags", "provenance_counts",
        "budget"}`，即文档 §3 的 L0 字段清单。

        - **不含** `evidence`、逐叶子 `provenance` / `unknown_reasons` / `confidence`、
          逐模块列表、512 条技能明细（红线：L0 不得包含它们）；
        - `meta.skill_tier` 是 `{name, count, hash}`（hash = 技能档的稳定摘要）；
        - `budget` = `{chars, degraded}`：`chars` 是 **L0 正文长度、不含 `budget` 块自身**
          （T-41 定稿口径，见模块 docstring 末尾）；`degraded` 超预算时置位
          （**只上报不裁剪**）。

    `chars` 的算法是**三步、不是循环依赖**（顺序在代码里一行一步，别读成自指）：

    1. `_index_body()` 先给出**没有 `budget` 键**的正文；
    2. 用这份正文量长度 —— 定义就是「摘掉 `budget` 块之后序列化有多长」；
    3. 最后才把 `budget` 块塞回正文（它不在第 2 步的长度里）。
    """
    # ① 构造正文：此刻 body 里还没有 budget 键。
    body, _entries_used = _index_body(ctx)
    # ② 量正文：定义 = 去掉 budget 块之后的 L0 序列化长度（与既有序列化约定一致）。
    chars = len(json.dumps(body, ensure_ascii=False))
    # ③ 最后把 budget 块塞回去：它不参与第②步的长度，所以不存在自指。
    body["budget"] = {"chars": chars, "degraded": chars > INDEX_BUDGET_CHARS}
    return body


# =============================================================================
# 四、L0 单个视角块（组装与取数共用）
# =============================================================================


def _set_nested(target: dict, parts: list[str], value: object) -> None:
    """按点路径写进嵌套字典（只在 L0 的 1–2 层小树上用）。"""
    head, rest = parts[0], parts[1:]
    if not rest:
        target[head] = value
        return
    bucket = target.get(head)
    if not isinstance(bucket, dict):
        bucket = {}
        target[head] = bucket
    _set_nested(bucket, rest, value)


def _field_value(field: IndexField, overview: Mapping[str, object]) -> object:
    """一条登记字段 → 值（取不到给 `_MISSING`，`omit_if_none` 的 `None` 也算取不到）。"""
    if field.read is not None:
        value = field.read(overview)
    elif field.source is not None:
        found, value, _canonical = _resolve(overview, _split_path(field.source), "")
        if not found:
            return _MISSING
    else:  # pragma: no cover - 登记表自检会拦住这种写法
        return _MISSING
    if value is None and field.omit_if_none:
        return _MISSING
    return value


def _view_index(view: str, overview: Mapping[str, object]) -> dict:
    """单个视角的 L0 块（按 `INDEX_SPEC` 组装；**不重算任何视角数值**）。"""
    block: dict = {}
    for field in INDEX_SPEC.get(view, ()):
        value = _field_value(field, overview)
        if value is _MISSING:
            continue
        _set_nested(block, field.path.split("."), value)
    return block


def _underlying_paths(field: IndexField, l0_path: str) -> list[str]:
    """L0 路径 → 它对应的**一条或多条 L1 底层叶子路径**（查三态用）。

    三种形态（与 `INDEX_SPEC` 的 docstring 一一对应）：

    - **整字段改名**（`l0_path == field.path`）：就是 `field.source`；
    - **块内子键改名**（`l0_path` 是 `field.path` 的子键）：先查 `field.aliases`
      （`layers.shield` → `layers.shield_hp`），没有别名就按 `source` 当下钻前缀
      （`cpu.output` → `cpu.output`、`damage_split.em` → `damage_split.em`）；
    - **节点请求**（`l0_path` 是 `field.path` 的前缀，如 `range_m` / `layers`）：
      返回该字段的**全部**底层路径（有别名就每个子键一条），由调用方取最弱的那个。

    都取不到时返回空列表 —— 调用方据此判「纯派生 / 聚合值」（→ `assumed`），
    **不猜**一个来源。
    """
    if l0_path == field.path:
        if field.aliases:
            return [target for _key, target in field.aliases]
        return [field.source] if field.source is not None else []

    prefix = f"{field.path}."
    if not l0_path.startswith(prefix):
        return []
    child = l0_path[len(prefix):]
    for alias, target in field.aliases:
        if child == alias:
            return [target]
    if field.source is None:
        return []
    return [f"{field.source}.{child}"]


def _field_state(field: IndexField, l0_path: str, provenance: Mapping[str, str]) -> str | None:
    """一条字段在某个 L0 路径上的三态：**先查底层路径，查不到再看是不是合成值**。"""
    states = [_state_of(provenance, path) for path in _underlying_paths(field, l0_path)]
    weakest = _weakest(states)
    if weakest is not None:
        return weakest
    if field.synthetic or field.source is None:
        return DERIVED_PROVENANCE  # 聚合 / 纯派生 → 合成值（**不是** null）
    return None


def _field_provenance(
    view: str, l0_path: str, entry: Mapping[str, object]
) -> str | None:
    """L0 路径 → 三态（**按底层路径查 L1 的逐叶子表**，不在 L0 里另存一份）。

    匹配规则（T-40 修正的正是这里）：

    - 请求的是**一条字段**（`dps_base` / `range_m.max`）：按该字段的底层路径查；
    - 请求的是**字段下的子键**（`weapons.count` / `layers.shield` / `damage_split.em` /
      `cpu.output`）：`l0_path` 以 `field.path + "."` 开头也算命中 —— T-39 只认「完全
      相等」，于是 16 类别名路径一条都匹配不上，返回 `null`（缺陷 2 的根因）；
    - 请求的是**一个节点**（`range_m` / `layers` / `damage_split`）：聚合成最弱的那一项
      （`_weakest()`，与数组元素约定「不许比最弱的出现更自信」同口径）；
    - 不是 L0 的路径（真的没有）→ `None`（不猜）。

    聚合 / 纯派生字段走 `DERIVED_PROVENANCE`（`assumed`），**绝不返回 `null`**。
    """
    fields = INDEX_SPEC.get(view, ())
    provenance = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
    states: list[str | None] = []
    for field in fields:
        if field.path == l0_path or l0_path.startswith(f"{field.path}."):
            states.append(_field_state(field, l0_path, provenance))
        elif field.path.startswith(f"{l0_path}."):
            states.append(_field_state(field, field.path, provenance))
    return _weakest(states)


def _state_of(provenance: Mapping[str, str], path: str) -> str | None:
    """L1 逐叶子表里按路径查三态；查不到时按**容器 / 节点**聚合（不猜、也不留空）。"""
    state = provenance.get(path)
    if state in PROVENANCE_STATES:
        return state
    children = [
        value
        for key, value in provenance.items()
        if key.startswith(f"{path}.") or key.startswith(f"{path}[]")
    ]
    return _weakest([value for value in children if value in PROVENANCE_STATES])


def _weakest(states: list[str | None]) -> str | None:
    """一组三态 → 最弱的那个（`unknown` > `assumed` > `measured`）；空集照实给 `None`。"""
    known = [state for state in states if state in _WEAKEST_RANK]
    if not known:
        return None
    return max(known, key=lambda state: _WEAKEST_RANK[state])


# =============================================================================
# 五、路径解析（L0 / L1 共用一套寻址：`a.b` / `a[]` / `a[0]`）
# =============================================================================

_SEGMENT_RE = re.compile(r"[^.\[\]]+(?:\[\d*\])?")
"""路径段：普通键、`a[]`（数组元素约定）、`a[3]`（按下标取一个元素）。"""

ARRAY_MARKER: str = "[]"
"""数组元素约定里的标记（与 `render.ARRAY_MARKER` 同义，这里只需字符串本身）。"""


def _split_path(path: str) -> list[str]:
    """`"a.b[0].c[].d"` → `["a", "b[0]", "c[]", "d"]`（空路径给空列表）。"""
    return _SEGMENT_RE.findall(str(path or ""))


def _split_segment(segment: str) -> tuple[str, str | None]:
    """`"weapons[0]"` → `("weapons", "0")`；`"weapons[]"` → `("weapons", "")`；纯键 → `(key, None)`。"""
    head, marker, tail = segment.partition("[")
    if not marker:
        return segment, None
    return head, tail[:-1] if tail.endswith("]") else tail


def _resolve(node: object, parts: list[str], prefix: str) -> tuple[bool, object, str | None]:
    """按路径取值，同时返回**规范化路径**（下标统一成 `[]`，用于查 provenance）。

    Returns:
        `(found, value, canonical)`：`canonical` 是 `a[].b` 形态的点路径；
        取不到时 `(False, None, None)`。
    """
    if not parts:
        return True, node, prefix or None

    head, rest = parts[0], parts[1:]
    key, bracket = _split_segment(head)

    if bracket is None:
        if isinstance(node, dict):
            # **整数键字典**（`skill_floor.count_by_level` = `{等级: 个数}`）：视角里键是
            # `int`，而路径 / provenance 键空间一律用字符串，所以数字段要回退到 int 键。
            if key in node:
                return _resolve(node[key], rest, f"{prefix}.{key}" if prefix else key)
            if key.isdigit() and int(key) in node:
                return _resolve(node[int(key)], rest, f"{prefix}.{key}" if prefix else key)
        return False, None, None

    items = node.get(key) if isinstance(node, dict) else None
    if not isinstance(items, list):
        return False, None, None
    canonical = f"{prefix}.{key}{ARRAY_MARKER}" if prefix else f"{key}{ARRAY_MARKER}"

    if bracket == "":  # 数组元素约定：对每个元素套用余下路径
        values: list[object] = []
        for item in items:
            found, value, _canonical = _resolve(item, rest, canonical)
            if not found:
                return False, None, None
            values.append(value)
        return True, values, canonical

    try:
        index = int(bracket)
    except ValueError:  # pragma: no cover - 正则只放行数字
        return False, None, None
    if index < 0 or index >= len(items):
        return False, None, None
    return _resolve(items[index], rest, canonical)


# =============================================================================
# 六、L1 单视角
# =============================================================================


def render_view(ctx: ViewContext, view_name: str) -> dict:
    """L1 单视角：`{overview, provenance, unknown_reasons, confidence, flags}`（**含 evidence**）。

    **只构建被请求的那一个视角**（`render._collect_flags` 的归位目标只有它），
    不重算六视角再丢弃；构建路径与 `render_overview` 完全相同，所以在 overview 未降级的
    样本上返回值与 `render_overview(ctx)["views"][view_name]` **逐字节相同**
    （`tests/test_summary.py` 钉住；降级时 L1 会**多出** evidence / 未截断列表，见模块
    docstring 第 3 条）。

    Args:
        ctx: 视角上下文。
        view_name: 视角名（必须已注册）。

    Returns:
        单视角条目，键固定为 `overview` / `provenance` / `unknown_reasons` /
        `confidence` / `flags`（`provenance` 与 `unknown_reasons` 都是稀疏语法，
        `confidence` 缺省 `A` 不写出——与渲染层同一口径）。

    **没有 `meta`**（T-54 追加，测试员 L2 验收指出）：三层里 `meta` 只在两处出现 ——
    L0 的 `result.meta`（压缩版，含 `skill_tier.hash`）与**所有入口**信封的 `meta`；
    detail 的信封 `meta` 额外多带 `assumptions`（512 条完整技能表）。
    所以「去 L1 找 `skill_tier` / `assumptions`」是找不到的：技能档看信封 `meta.skill_tier`，
    完整技能表只在 `fit_detail` 的信封 `meta.assumptions`。

    Raises:
        ValueError: 视角名未注册（消息里列出可用视角，便于调用方纠正）。
    """
    registered = tuple(available_views())
    if view_name not in registered:
        raise ValueError(f"视角 {view_name!r} 未注册；可用视角：{list(registered)}")
    entries, _residual = _entries(ctx, (view_name,))
    return entries[view_name]


# =============================================================================
# 七、按路径取数
# =============================================================================


def get_path(ctx: ViewContext, path: str) -> dict:
    """按稳定路径取任意节点（L0/L1 同一套路径表达式，文档 §1 的关键约束）。

    Args:
        ctx: 视角上下文。
        path: 点路径，支持 `a.b` / `a[]`（数组元素约定）/ `a[0]`（按下标取一个）。
            首段是视角名（`mobility.speed_mps`）或顶层块（`ship.name` / `meta.sde_build` /
            `flags[0].id` / `provenance_counts.mobility.measured` / `budget.chars`）。

    Returns:
        `{"value", "provenance", "source_view"}`，`unknown` 时多一个 `reason`
        （取自该视角的 `unknown_reasons` 稀疏表）：

        - `value`：取到的值；**路径不存在时是 `None`**（不抛异常）；
        - `provenance`：该路径的三态（L0 派生值记 `assumed`；不是三态字段的元信息给
          `None`）；
        - `source_view`：这个数属于哪个视角（顶层块给 `None`）；
        - `reason`：仅当三态是 `unknown` 时出现，与 `render_view` 的 `unknown_reasons`
          逐条一致（`input_incomplete` / `not_applicable` / `ambiguous` /
          `engine_omits` / …）；
        - `hint`：**仅路径不存在时出现**，给出可用前缀与（若首段是视角）该视角的
          L0 字段名，让 agent 一次改对。

    解析顺序是**先 L0、后 L1**：L0 里有的路径按 L0 语义返回（agent 从 L0 看到什么就拿到
    什么，`firepower.weapons` 给的是 `{count, families}`）；L0 里没有的落到 L1
    （`firepower.weapons[].name`、`mobility.propulsion_states.off.sig_m`、
    `constraints.hard_violations[0].rule.group_id`、`meta.assumptions.skill_tier`
    这类形状都取得动）。

    成本：**只构建被请求的那一个视角**（顶层块 `flags` / `provenance_counts` /
    `budget` 需要汇总六视角，此时才全建；其余路径不空转）。
    """
    text = str(path or "").strip()
    parts = _split_path(text)
    if not parts:
        return _miss(text, "路径为空")

    registered = tuple(available_views())
    head_key, bracket = _split_segment(parts[0])

    if head_key in registered:
        if bracket is not None:  # 视角名本身不会被索引（`mobility[0].x` 是笔误）
            return _miss(text, f"{head_key} 是视角名，不能带下标；试试 {head_key}.<字段>")
        return _get_view_path(ctx, head_key, text, parts[1:])
    if head_key in TOP_LEVEL_KEYS:
        return _get_simple_path(ctx, text, head_key, parts)
    return _miss(text, f"顶层只有 {list(TOP_LEVEL_KEYS)} 与六视角 {list(registered)}")


def _get_view_path(ctx: ViewContext, view: str, text: str, rest: list[str]) -> dict:
    """视角内路径：**先 L0、后 L1**（两条都走同一个 `render_view` 构建）。"""
    entry = render_view(ctx, view)
    overview = entry.get("overview") if isinstance(entry.get("overview"), dict) else {}
    provenance = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}

    l0_block = _view_index(view, overview)
    found, value, _canonical = _resolve(l0_block, rest, "")
    if found:
        state = _field_provenance(view, ".".join(rest), entry)
        return _answer_value(value, state, view, entry, _l1_path_of(view, ".".join(rest)))

    found, value, canonical = _resolve(overview, rest, "")
    if found:
        return _answer_value(value, _state_of(provenance, canonical) if canonical else None,
                             view, entry, canonical)

    return _miss(text, f"{view} 的 L0 字段：{list(l0_block)}；L1 也找不到这条路径")


def _l1_path_of(view: str, l0_path: str) -> str | None:
    """L0 路径 → L1 叶子路径（**用于 `unknown_reasons` 的查询**）。

    与查三态走同一张别名表（`_underlying_paths()`），所以「三态说 unknown、原因却查不到」
    这种自相矛盾不会出现。只有**唯一**底层路径时才返回（节点 / 聚合值返回 `None`：
    没有单一原因可归）。
    """
    paths: list[str] = []
    for field in INDEX_SPEC.get(view, ()):
        paths.extend(_underlying_paths(field, l0_path))
    return paths[0] if len(paths) == 1 else None


def _get_simple_path(ctx: ViewContext, text: str, head: str, parts: list[str]) -> dict:
    """顶层块路径（`ship` / `meta` / `flags` / `provenance_counts` / `budget`）。

    先查 **L0**（`{"<head>": 块}` 上按完整路径取，`flags[0].id` 这样带下标也走得通），
    取不到再落到「**完整版**」兜底：
    `meta` → `render._meta()`（含 `assumptions`，**完整技能表在这里取得动**）、
    `flags` → 带 evidence 的全量 flag 列表、`ship` → SDE 的类型行。
    L0 只放摘要，要原始形状时走这条路（文档 §4「完整技能表只在 detail 时给」——
    `get_path` 的兜底就是那条「按需取」的通道）。
    """
    body, entries = _index_body(ctx)
    body["budget"] = _budget_block(body)
    found, value, _canonical = _resolve({head: body.get(head)}, parts, "")
    if found:
        return _answer_value(value, None, None, None, None)

    if head == "meta":
        # 压缩形态的**还原通道**（T-40）：`meta.module_states` 在 L0 里是单值
        # `"engine_default"`、`meta.damage_profile` 是 `"uniform"`，所以
        # `meta.module_states.high` / `meta.damage_profile.em` 在 L0 上取不到 ——
        # 按 `META_COMPRESSED_ALIASES` 翻到完整版（`meta.assumptions.*`）去取。
        answer = _get_compressed_meta_path(ctx, parts)
        if answer is not None:
            return answer

    full = _full_block(ctx, head, entries)
    found, value, _canonical = _resolve({head: full}, parts, "")
    if found:
        return _answer_value(value, None, None, None, None)

    block = body.get(head)
    if isinstance(block, dict):
        hint = f"{head} 在 L0 里只有 {list(block)}"
    elif isinstance(block, list):
        hint = f"{head} 是列表（{len(block)} 项），试试 {head}[0] 或 {head}[0].id"
    else:
        hint = f"{head} 是标量"
    return _miss(text, hint)


def _get_compressed_meta_path(ctx: ViewContext, parts: list[str]) -> dict | None:
    """`meta.<被压缩块>.<子键>` → 从**完整版** `meta.assumptions.<同路径>` 取（不可逆就不是压缩）。

    只在「L0 取不到」时调用：`meta.module_states` 命中 L0 时返回的就是压缩值
    （`"engine_default"`）——那是 L0 语义，agent 看到什么就拿到什么；
    而 `meta.module_states.high` 这种**下钻**必须给原值，否则压缩就丢了信息。
    """
    if len(parts) < 2:
        return None
    head_key, bracket = _split_segment(parts[1])
    if bracket is not None:  # `meta.module_states[0]` 不是这套压缩的形状
        return None
    alias = META_COMPRESSED_ALIASES.get(head_key)
    if alias is None:
        return None
    target = _split_path(f"meta.{alias}") + parts[2:]
    full = render._meta(ctx, "overview")
    found, value, _canonical = _resolve(full, target[1:], "")
    if not found:
        return None
    return _answer_value(value, None, None, None, None)


def _full_block(ctx: ViewContext, head: str, entries: Mapping[str, dict]) -> object:
    """L0 摘要之外的「完整版」顶层块（只给 `get_path` 的兜底用）。"""
    if head == "meta":
        return render._meta(ctx, "overview")
    if head == "flags":
        flags: list[dict] = []
        for entry in entries.values():
            flags.extend(flag for flag in (entry.get("flags") or []) if isinstance(flag, dict))
        return flags
    if head == "ship":
        fit = ctx.fit if isinstance(ctx.fit, dict) else {}
        ship = fit.get("ship") if isinstance(fit.get("ship"), dict) else {}
        type_id = ship.get("type_id")
        row = ctx.index.get_type(type_id) if _is_index_ready(ctx, type_id) else None
        return dict(row or {}, type_id=type_id)
    return {}


def _budget_block(body: Mapping[str, object]) -> dict:
    """L0 的 `budget`（与 `render_index` 同一口径：量正文、不含自己）。"""
    chars = len(json.dumps(body, ensure_ascii=False))
    return {"chars": chars, "degraded": chars > INDEX_BUDGET_CHARS}


def _answer_value(
    value: object,
    state: str | None,
    view: str | None,
    entry: Mapping[str, object] | None,
    canonical: str | None,
) -> dict:
    """统一答案：`{value, provenance, reason?, source_view}`（`reason` 只在 unknown 时出现）。"""
    answer: dict = {"value": value, "provenance": state, "source_view": view}
    if state == "unknown" and entry is not None and canonical:
        reasons = entry.get("unknown_reasons")
        if isinstance(reasons, dict) and canonical in reasons:
            answer["reason"] = reasons[canonical]
    return answer


def _miss(text: str, hint: str | None) -> dict:
    """路径不存在：`value=None` + 可用前缀提示（**不抛异常**，文档要求）。"""
    prefix = text.split(".")[0] if text else ""
    hint_text = f"路径 {text!r} 不存在"
    if hint:
        hint_text = f"{hint_text}；{hint}"
    else:
        hint_text = f"{hint_text}；可用前缀：{list(TOP_LEVEL_KEYS)} 与六视角"
    if prefix:
        hint_text = f"{hint_text}（首段 {prefix!r}）"
    return {"value": None, "provenance": None, "source_view": None, "hint": hint_text}


__all__ = [
    "DAMAGE_PROFILE_UNIFORM",
    "DERIVED_PROVENANCE",
    "HINT_MAX_CHARS",
    "HINT_MIN_CHARS",
    "INDEX_BUDGET_CHARS",
    "INDEX_SPEC",
    "IndexField",
    "META_COMPRESSED_ALIASES",
    "get_path",
    "render_index",
    "render_view",
    "skill_tier_hash",
]
