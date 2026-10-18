"""输出分层的第一阶段（L0 摘要 / L1 单视角 / 按路径取数）—— **只组装，不计算**。

权威文档：`evefit/docs/输出分层与工具集-v1.md`。三个入口：

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
   数值都还在**（`provenance_counts` 给三态计数、`flags` 给完整 id 清单、`meta.skill_tier`
   给 `{name, count, hash}`），并且都能用 `get_path()` 原路取回、逐位相同。
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
| `resources` | `cpu{output, load, free}` / `powergrid{...}` / `feasible` / `slots{high: "3/6", …}` |
| `constraints` | `hard_violations{count, kinds}` / `tier_violations{count, kinds}` / `needs_implant` |
| `stability` | `cap_stable_pct` / `cap_depletes_in_s` / `cap_load_gj_s` / `cap_recharge_gj_s` / `has_external_dependency` |
| 顶层 | `ship{name, group, type_id}` / `meta`（**技能档只给 `{name, count, hash}`**）/ `flags[{id}]` / `provenance_counts` / `budget{chars, degraded}` |

两处相对文档 §3 的**形态取舍**（都有实测依据，见下；数值一个没丢）

1. **`flags` 只给 `id`**（文档写 `[{id, message}]`）。实测 f47 有 **7 条**、f67 有 **6 条**
   flag：带 message 时 `flags` 单块 688 / 555 字符（六视角合计才 960），无论把 message
   截到多短都过不了 2,000（cap=8 时 f47 仍有 1,989，且正文被截成碎句）。**一条不丢**
   优于「留几条完整的」——渲染层的红线是「绝不丢弃诊断，丢一条就少一条可疑点」，
   而少一段解释不丢任何一条可疑点。完整 message 与 evidence 都在
   `render_view()["flags"]` / `get_path("flags[0].message")`。
2. **`provenance_counts` 用顺序三元组 `[measured, assumed, unknown]`**（文档写
   `{measured, assumed, unknown}`）。**同一组数、同一顺序**，省 204 字符（58 → 20 每视角）；
   `get_path("provenance_counts.mobility.assumed")` 按名字照样取得动。

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

`budget` 的口径（`{chars, degraded}`）

- `chars` = **L0 正文**（`ship` / `meta` / 六视角 / `flags` / `provenance_counts`）的
  `len(json.dumps(..., ensure_ascii=False))`，**不含 `budget` 自身**——与渲染层
  「`budget.chars` 只量 `views` + `modes`、不量自己」的口径同源；
- `degraded` = `chars > INDEX_BUDGET_CHARS`。**L0 的降级是上报式、不是裁剪式**：
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

from evefit.project import render
from evefit.project.views import available_views
from evefit.project.views.base import ViewContext
from evefit.project.views.loader import load_views

# =============================================================================
# 一、常量与口径
# =============================================================================

INDEX_BUDGET_CHARS: int = 2000
"""L0 的字符预算（`len(json.dumps(L0 正文))`）。

**实测重定，不是拍的**（四个真实样本，`tests/test_summary.py` 逐个钉住）：

| 样本 | L0 字符数（含 budget 块） |
|---|---|
| `f67-spotcheck-139255942` | **1,918** |
| `f47-random-raptor` | **1,965** |
| `f12-logistics-oneiros` | **1,934** |
| `f1-hecate-killmail` | **1,884** |

对比 `render_overview` 的 f67 **23,256**（views 16,723 + meta 6,439）→ 降幅 **91.8%**，
约 7,000 token 的「概览」变成约 600 token 的「索引」。

**文档 §6 的 ~1,220 估不出来**，原因是两处系统性低估（不是本层没压干净）：

1. `flags`：文档按「2 条 × 110」估 220，实测 f47 有 **7 条**、f67 有 **6 条**
   （每条 id 约 25–40 字符），这一块 217 / 204；
2. 六视角 4–5 个字段实测 **约 940**（文档估 620）——`damage_split` 四系、`cpu`/`powergrid`
   三元组、`slots` 五槽这些「字段名 + 真实数值」本身就压不动（而红线禁止为省字符丢数值）；
3. `meta` 实测 **377**（文档估 180，与 `ship` 合并算）：`module_states`（104）与
   `damage_profile`（65）都是文档明列的项，不能砍。

于是预算按实测重定：`ceil(1,965 × 1.02 ÷ 1000) × 1000 = 2,000`。四个样本都留 **≥35**
字符余量（f47 最紧——它有 7 条 flag）；`degraded` 只在更肥的配装上置位
（**只上报，不裁剪**——见模块 docstring 末尾「`budget` 的口径」）。
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
            写 `None` 表示「纯派生值」——没有单一对应叶子，三态记 `assumed`。
        read: 派生函数 `overview -> value`；`None` 表示按 `source` 取值。
        omit_if_none: 值为 `None` 时**不写这个键**（只给 `range_m.max`：没有导弹时
            文档 §3 明确「只给 optimal」，`None` 的 max 是噪声）。
        synthetic: 纯派生（与 `source is None` 等价），保留独立标记让 `_field_provenance`
            的意图更好读。
    """

    path: str
    source: str | None = None
    read: Callable[[Mapping[str, object]], object] | None = None
    omit_if_none: bool = False
    synthetic: bool = False


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
        IndexField("damage_split", source="damage_split"),
    ),
    "survivability": (
        IndexField("ehp_mixed", source="ehp_mixed"),
        IndexField("layers", read=_layers_block, synthetic=True),
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


def _meta_block(ctx: ViewContext) -> dict:
    """`meta` → 版本三元组 + **压缩后的**假设块（技能档只给 `{name, count, hash}`）。

    与 `render._meta()` 的区别只有一处：`assumptions` 被压成
    `skill_tier / module_states / damage_profile / mode` 四项。不做假设推导
    （红线 N9：`ctx.assumptions` 是唯一来源，这里只读不改）。
    """
    engine_meta = ctx.meta if isinstance(ctx.meta, dict) else {}
    assumptions = ctx.assumptions if isinstance(ctx.assumptions, dict) else {}
    return {
        "sde_build": engine_meta.get("sde_build"),
        "engine_version": engine_meta.get("engine_version"),
        "spec_version": engine_meta.get("spec_version"),
        "skill_tier": _skill_tier_block(assumptions),
        "module_states": assumptions.get("module_states"),
        "damage_profile": assumptions.get("damage_profile"),
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


def _flag_pairs(entries: Mapping[str, dict], residual: list[dict] | None = None) -> list[dict]:
    """所有 flag → `[{id}]`（**不带 `message`、不带 `evidence`**）。

    为什么 L0 连 `message` 都不带（这是相对文档 §3「`[{id, message}]`」的**取舍之一**，
    另一处见 `_provenance_counts()`）：

    - **一条不丢**：L0 的 flag 清单是**完整**的（视角顺序 + 顶层兜底桶），只是把
      「为什么」留给 L1——`id` 已经自解释（`resist_hole` / `resource_overflow` /
      `cap_sensitive` …），L0 的读者要回答的是「要不要继续看」，不是「为什么」；
    - **换成 message 就装不下**：实测 f47 有 7 条 flag，带 message 时 `flags` 单块
      **688 字符**（六视角合计才 960）；把 message 留到 L1 后 f47 的 L0 是 1,965，
      **带上 message 无论怎么截都过不了 2,000**（cap=8 已是 1,989，且正文被截成碎句）；
    - 反过来「只留前 N 条」更糟：那会**真的丢诊断**（渲染层 docstring 的红线是
      「绝不丢弃，丢一条就少一条可疑点」），而少一段解释不丢任何一条可疑点。

    `render_view(v)["flags"]` / `get_path("flags[0].message")` 都拿得到完整 message
    与 evidence。

    顺序 = 视角顺序 + 顶层兜底桶（与 `render_overview` 的归位一致）。
    """
    pairs: list[dict] = []
    for entry in entries.values():
        for flag in entry.get("flags") or []:
            if isinstance(flag, dict):
                pairs.append({"id": flag.get("id")})
    for flag in residual or []:
        if isinstance(flag, dict):
            pairs.append({"id": flag.get("id")})
    return pairs


def _provenance_counts(entries: Mapping[str, dict]) -> dict[str, list[int]]:
    """每视角的 **`[measured, assumed, unknown]` 计数三元组**（逐叶子表属 L1，文档 §2）。

    文档 §3 写的是 `{measured, assumed, unknown}`；这里用**同一组数、固定顺序**的
    三元组（省 204 字符：`{"measured": 2, "assumed": 9, "unknown": 19}` 58 → 20）。
    没有丢任何信息：`PROVENANCE_STATES` 就是这三个状态的**声明顺序**，
    按名字取也读得通（`get_path("provenance_counts.mobility.assumed")`，见
    `_get_simple_path()` 里的 named 回退）。
    """
    counts: dict[str, list[int]] = {}
    for name, entry in entries.items():
        bucket = {state: 0 for state in PROVENANCE_STATES}
        for state in (entry.get("provenance") or {}).values():
            if state in bucket:
                bucket[state] += 1
        counts[name] = [bucket[state] for state in PROVENANCE_STATES]
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
        - `budget` = `{chars, degraded}`，`degraded` 超预算时置位（**只上报不裁剪**）。
    """
    body, _entries_used = _index_body(ctx)
    chars = len(json.dumps(body, ensure_ascii=False))
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


def _field_provenance(
    view: str, l0_path: str, entry: Mapping[str, object]
) -> str | None:
    """L0 路径 → 三态（**从 L1 的逐叶子表查**，不在 L0 里另存一份）。

    三种情况：

    - 请求的是**一条字段**（`range_m.max`）：有 `source` 就查 L1 的那条叶子；
      派生字段（`weapons` / `thinnest` / `slots` …）记 `assumed`（合成值语义）；
    - 请求的是**一个节点**（`range_m` / `layers` / `damage_split`）：聚合成最弱的那一项
      （`_weakest()`，与数组元素约定「不许比最弱的出现更自信」同口径）；
    - 不是 L0 的路径（真的没有）→ `None`（不猜）。
    """
    fields = INDEX_SPEC.get(view, ())
    matched = [
        field for field in fields if field.path == l0_path or field.path.startswith(f"{l0_path}.")
    ]
    if not matched:
        return None
    provenance = entry.get("provenance") if isinstance(entry.get("provenance"), dict) else {}
    states: list[str] = []
    for field in matched:
        if field.synthetic or field.source is None:
            states.append(DERIVED_PROVENANCE)
            continue
        states.append(_state_of(provenance, field.source))
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
    """L0 路径 → L1 叶子路径（派生字段没有单一来源时给 `None`）。

    只用于 `unknown_reasons` 的查询：派生值记 `assumed`，不会落到 `unknown` 分支，
    所以 `None` 不影响答案。
    """
    for field in INDEX_SPEC.get(view, ()):
        if field.path == l0_path and field.source is not None:
            return field.source
    return None


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

    if head == "provenance_counts":
        # 三态计数在 L0 里是位置三元组；按名字取（`.measured` / `.assumed` / `.unknown`）
        # 也读得通——名字顺序就是 `PROVENANCE_STATES`，两种写法同一组数。
        named = {view: dict(zip(PROVENANCE_STATES, triple))
                 for view, triple in (body.get(head) or {}).items()}
        found, value, _canonical = _resolve({head: named}, parts, "")
        if found:
            return _answer_value(value, None, None, None, None)

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
    "DERIVED_PROVENANCE",
    "INDEX_BUDGET_CHARS",
    "INDEX_SPEC",
    "IndexField",
    "get_path",
    "render_index",
    "render_view",
    "skill_tier_hash",
]
