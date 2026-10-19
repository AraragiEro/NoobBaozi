"""归因引擎（L3 / P3 `explain`）：回答「这个指标**为什么**是这个数」。

设计依据：`docs/配装理解-软件设计-v1.md` §3.3（`fit_explain(fit, metric) → sources 链，限深 2 层`）
与 L3 硬边界「**不解释到单条 modifier 之外**」。引擎能力取证见
`noobbaozi/.tmp/explain_contract.md`（3/5 取证单，未入库）。

## 三类指标，口径**不许混**

| 类 | 指标 | 判据 | `combine` |
|---|---|---|---|
| **A 可加** | `survivability.ehp_mixed` / `firepower.dps.base` / `resources.cpu.load` / `resources.powergrid.load` | 引擎该属性的源全是 `mod_add` / `mod_sub` | `additive` |
| **B 乘性** | `mobility.speed_mps` / `stability.capacitor.peak_recharge` | 源是 `pre_mul` / `post_mul` / `post_div` / `post_percent` | `multiplicative` |
| **C 投影** | `survivability.thinnest.{shield,armor,hull}.ehp` | 投影层自己算，引擎**没有**这个指标属性 | `multiplicative`（公式回放） |

**A 类**：`Σ contribution == value`（`unexplained` 显式给，实测 0）。
**B 类**：给 `base` + **有序因子链**（`factor` / `operator` / `penalty`）+ `replayed` / `replay_ok`；
**因子不能相加**（`contribution` 一律 `None`）——把因子当「贡献量」求和是错的。
**C 类**：`value = layer_Hp ÷ 该层最大共振率`，depth 1 是公式两项，depth 2 展开这两项各自的引擎源。

## 三条纪律（与项目其余部分一致）

1. **不编**：引擎没给数、路径不存在、指标不在登记表 → 返回 `None`（由接线层报 `invalid_path`），
   绝不返回一个看起来合理的空壳。
2. **不重复口径**：A 类不用「我们自己算的 used」去比大小，直接读引擎该属性的 `sources`；
   C 类复用 `views/survivability.py` 的 `LAYERS` / `RESONANCE_ATTRS` / `DAMAGE_TYPES`（同一份口径）。
3. **体积**：`sources=True` 全量 payload 约 **210 KB**（plain 的 4.8×）、**2.95 ms/次**（不走引擎 LRU），
   所以本模块**按 metric 裁剪**（只取一个属性 + 深度 2 的子源），并自带进程内 LRU（key = fit 内容哈希 + metric + depth）。

## 深度与裁剪

- `depth` 上限 **2**（设计边界），传其它值直接 `ValueError`（不静默截断）。
- depth 1 = 指标的直接贡献者；depth 2 = 每个贡献者**自己的** `sources`。
- 子源解析必须按 `from.type` 分流：`ship` 查船体属性、**`item` 查 `items[index].attributes`**
  （`dps.base` 的源 `from=item[9]` 的 `source_attribute_id=-12` 指**那件武器自己**的属性，
  照 ship 查会把同一份数据算两遍）、`skill` 是叶子、`buff`/`projected` 折叠。
- 超过 `depth` 的一律 `children: []` + `truncated: true`，并计入 `truncated_nodes`。

## 噪声过滤（`folded_noops`）

引擎会为「贡献 0」的模块也发一条源（实测：`cpu.load` 最多 5 条零项，`dps` 里探针发射器贡献 0.0），
乘性侧还有 `post_mul 1.0` 的恒等项。它们让「去掉某项、和不变」的**反向检查**失效，
所以默认过滤掉：`mod_add`/`mod_sub`/`post_percent` 的 `0.0`、乘性算子的 `1.0`、以及 `applied=false`
的源；过滤个数写进 `folded_noops`，返回的 `sources` 里**不会再有零项**。

## 与 provenance 的关系

`provenance` 回答「这个数从哪来的」（三态），`explain` 回答「为什么是这个数」（源链）。
两者共用**同一套点路径语言**（`summary.get_path`），`result.provenance` 直接取三态，
不新增第四态。`unknown` 的值不该有源链 —— 本模块遇到取不到值的指标返回 `None`。

**注意：`provenance` 与 `combine` 正交，不要拿它判断可加性。** 实测
`survivability.ehp_mixed` / `firepower.dps.base` / `stability.capacitor.peak_recharge` 都是
`assumed` —— 因为它们映射的是**负 ID 的引擎 pass-4 派生值**（`provenance.py` 的铁律：
负 ID 一律 `assumed`），`resources.cpu.load` 则是**状态敏感**（模块 offline/active 会变）。
真正的「可加」判据只有一条：**该属性的源全是 `mod_add` / `mod_sub`**。
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict
from typing import Mapping

from noobbaozi.config import attributes as A
from noobbaozi.project import summary, units
from noobbaozi.project.views import survivability as _survivability
from noobbaozi.project.views.base import BASE, ViewContext

MAX_DEPTH: int = 2
"""深度上限（设计边界：不解释到单条 modifier 之外）。"""

MAX_CHILDREN_PER_SOURCE: int = 8
"""单个 depth-1 贡献者最多展开几个子源（再多只是噪音）。"""

MAX_DEPTH2_NODES: int = 48
"""一次 explain 的 depth-2 节点总预算（体积护栏；超出的节点 `truncated: true`）。"""

CACHE_MAX_ENTRIES: int = 64
"""进程内 LRU 容量（key = fit 内容哈希 + metric + depth）。只缓存成功结果。"""

TOLERANCE: dict[str, float] = {"relative": 1e-9, "absolute": 1e-9}
"""「加得上 / 回放得上」的相对+绝对容差（实测残差最大 9.1e-13，留了 6 个数量级）。"""

_ADDITIVE_METRICS: dict[str, int] = {
    "survivability.ehp_mixed": A.ATTR_EHP,
    "firepower.dps.base": A.ATTR_DAMAGE_DPS,
    "resources.cpu.load": A.ATTR_CPU_LOAD,
    "resources.powergrid.load": A.ATTR_POWER_LOAD,
}
"""A 类：指标路径 → **引擎属性 ID**（值是这些属性的直接读数）。"""

_MULTIPLICATIVE_METRICS: dict[str, int] = {
    "mobility.speed_mps": A.ATTR_MAX_VELOCITY,
    "stability.capacitor.peak_recharge": A.ATTR_CAP_PEAK_RECHARGE,
}
"""B 类：指标路径 → 引擎属性 ID（乘法链，`value = base × Π factor`）。"""

_PROJECTION_METRICS: dict[str, str] = {
    "survivability.thinnest.shield.ehp": "shield",
    "survivability.thinnest.armor.ehp": "armor",
    "survivability.thinnest.hull.ehp": "hull",
}
"""C 类：指标路径 → 层名（公式见 `views/survivability._thinnest`）。"""

_ADDITIVE_OPS: frozenset[str] = frozenset({"mod_add", "mod_sub"})

_OP_ORDER: tuple[str, ...] = (
    "pre_assign",
    "pre_mul",
    "post_mul",
    "post_div",
    "post_percent",
    "post_assign",
    "mod_add",
    "mod_sub",
)
"""回放顺序（dogma 的算子阶段）。实测 f1 442/444、f7 446/457 精确；失败的是引擎派生便利属性。"""

_MULTIPLICATIVE_OPS: frozenset[str] = frozenset(
    {"pre_mul", "post_mul", "post_div", "post_percent"}
)

_DRONE_SLOT: str = "drone_bay"
_FIGHTER_SLOT: str = "fighter_bay"

_CACHE: "OrderedDict[str, dict]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
"""缓存锁：`mcp_http` 是多线程的，缓存读写必须原子（与 `engine/adapter` 的 LRU 同一考量）。"""


# =============================================================================
# 对外入口
# =============================================================================


def supported_metrics() -> tuple[str, ...]:
    """本模块能归因的指标路径（顺序：A → B → C；接线层可据此报可选项）。"""
    return tuple(_ADDITIVE_METRICS) + tuple(_MULTIPLICATIVE_METRICS) + tuple(_PROJECTION_METRICS)


def clear_cache() -> None:
    """清空进程内 LRU（测试 monkeypatch 之后必须调用；与 `api.clear_caches` 同一纪律）。"""
    with _CACHE_LOCK:
        _CACHE.clear()


def explain(ctx: ViewContext, metric: str, *, depth: int = MAX_DEPTH) -> dict | None:
    """某指标的归因结果；**不支持的路径返回 `None`**（不抛异常、不编空壳）。

    Args:
        ctx: 视角上下文（与 `render_view` / `get_path` 同一个）。
        metric: L0/L1 的点路径（`a.b` / `a[].b` / `a[0].b`），例如 `survivability.ehp_mixed`。
        depth: 1 或 2（默认 2）。

    Returns:
        `None`（路径不存在 / 指标不在登记表），或契约信封（`metric` / `value` / `combine` /
        `base` / `sources` / `folded_noops` / `unexplained` / `truncated_at_depth` /
        `truncated_nodes` / `minimality` / `has_offsets` / `provenance` / `tolerance`，
        B / C 类另带 `replayed` / `replay_ok`）。

    Raises:
        ValueError: `depth` 不在 `1..MAX_DEPTH`。

    Note:
        返回值**与其他调用共享同一个缓存对象**（`sources=True` 2.95 ms/次，不该重复算）。
        接线层请视为**只读**（与 `engine.calculate` 的约定一致）；要改先 `copy.deepcopy`。
    """
    if not isinstance(depth, int) or isinstance(depth, bool) or not 1 <= depth <= MAX_DEPTH:
        raise ValueError(f"depth 只能是 1..{MAX_DEPTH}，收到 {depth!r}")

    text = str(metric or "").strip()
    if text not in supported_metrics():
        return None

    key = _cache_key(ctx, text, depth)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached is not None:
            _CACHE.move_to_end(key)
            return cached

    built = _build(ctx, text, depth)
    if built is None:
        return None  # 只缓存成功结果（失败可能是样本/引擎状态问题，不该被记住）

    with _CACHE_LOCK:
        _CACHE[key] = built
        while len(_CACHE) > CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)
    return built


# =============================================================================
# 构建
# =============================================================================


def _build(ctx: ViewContext, metric: str, depth: int) -> dict | None:
    """按类分派。`None` = 这个指标在当前上下文里取不到值（不编）。"""
    if metric in _ADDITIVE_METRICS:
        return _explain_attribute(ctx, metric, _ADDITIVE_METRICS[metric], depth, "additive")
    if metric in _MULTIPLICATIVE_METRICS:
        return _explain_attribute(
            ctx, metric, _MULTIPLICATIVE_METRICS[metric], depth, "multiplicative"
        )
    layer = _PROJECTION_METRICS[metric]
    return _explain_projection(ctx, metric, layer, depth)


def _explain_attribute(
    ctx: ViewContext, metric: str, attribute_id: int, depth: int, combine: str
) -> dict | None:
    """A / B 类：值取自引擎属性，源链取该属性的 `sources`。"""
    calculation = ctx.engine.calculate(ctx.fit, sources=True)
    node = _attribute_node(calculation, {"type": "ship"}, attribute_id)
    if not isinstance(node, dict):
        return None

    value = _as_float(node.get("value"))
    base = _as_float(node.get("base"))
    if value is None:
        return None

    kept, folded = _filter_sources(node.get("sources") or [])
    budget: dict[str, int] = {"depth2_nodes": 0, "truncated": 0, "folded": folded}
    sources = [
        _entry(ctx, calculation, source, depth=1, depth_limit=depth, budget=budget)
        for source in kept
    ]

    if combine == "additive":
        sources = _mark_offsets(sources)
        total = 0.0
        additive_only = True
        for entry in sources:
            if entry["contribution"] is None:
                additive_only = False
                continue
            total += float(entry["contribution"])
        unexplained = _clean_residual(value - total)
        base_value = base if base is not None else 0.0
        replay = None
        replay_ok = None
    else:
        # B 类：按算子阶段排序（「有序因子链」不是装饰，回放就按这个顺序）
        sources.sort(key=_operator_rank)
        replayed = _replay(base or 0.0, kept)
        replay = replayed
        replay_ok = _close(replayed, value)
        unexplained = _clean_residual(value - replayed)
        total = None
        additive_only = False
        base_value = base if base is not None else 0.0

    result: dict = {
        "metric": metric,
        "view": metric.split(".", 1)[0],
        "value": value,
        "unit": _unit_of(attribute_id),
        "combine": combine,
        "includes_base": True,
        "base": base_value,
        "sources": sources,
        "folded_noops": budget["folded"],
        "unexplained": unexplained,
        "truncated_at_depth": depth,
        "truncated_nodes": budget["truncated"],
        "minimality": _minimality(sources, budget["truncated"]),
        "has_offsets": any(entry["role"] == "offset" for entry in sources),
        "tolerance": dict(TOLERANCE),
        "provenance": _provenance(ctx, metric),
    }
    if combine == "additive":
        result["sum_of_contributions"] = total
        result["additive_only"] = additive_only
        if not additive_only:
            # 出现非 mod 算子时不能宣称「可加」：如实降级（实测四指标全是 mod_add，不会走到）
            result["combine"] = "mixed"
    else:
        result["replayed"] = replay
        result["replay_ok"] = replay_ok

    if metric == "firepower.dps.base":
        result["includes_drones"] = True
        result["subtotals"] = _dps_subtotals(ctx, sources)
    return result


def _explain_projection(ctx: ViewContext, metric: str, layer: str, depth: int) -> dict | None:
    """C 类：`value = HP_layer ÷ 该层最大共振率`（与 `views/survivability._thinnest` 同一口径）。"""
    attrs = ctx.attrs(BASE)
    hp_attribute = _layer_hp_attribute(layer)
    hp_value = _as_float(attrs.get(hp_attribute))
    resonances = _layer_resonances(attrs, layer)
    if hp_value is None or resonances is None:
        return None
    damage_index = max(range(len(resonances)), key=lambda position: resonances[position])
    resonance = float(resonances[damage_index])
    if resonance <= 0.0:
        return None
    value = hp_value / resonance

    damage_types = _survivability.DAMAGE_TYPES
    tied = sum(
        1
        for other in resonances
        if abs(other - resonance) <= 1e-12
    ) > 1
    damage_type = None if tied else damage_types[damage_index]
    resonance_attribute = _survivability.RESONANCE_ATTRS[layer][damage_index]

    calculation = ctx.engine.calculate(ctx.fit, sources=True)
    budget: dict[str, int] = {"depth2_nodes": 0, "truncated": 0, "folded": 0}

    hp_children, _hp_folded, hp_truncated = _children(
        ctx, calculation, {"type": "ship"}, hp_attribute, depth, budget
    )
    resonance_children, _res_folded, res_truncated = _children(
        ctx, calculation, {"type": "ship"}, resonance_attribute, depth, budget
    )

    sources = [
        {
            "kind": "projection",
            "role": "primary",
            "label": f"{_attribute_name(hp_attribute)}（该层 HP）",
            "type_id": None,
            "item_index": None,
            "attribute_id": hp_attribute,
            "operator": "pre_assign",
            "contribution": hp_value,
            "sign": 1,
            "factor": None,
            "penalty": None,
            "applied": True,
            "depth": 1,
            "truncated": hp_truncated,
            "children": hp_children,
        },
        {
            "kind": "projection",
            "role": "primary",
            "label": (
                f"{layer} 共振（四系并列 {units.fmt(resonance)}）"
                if tied
                else f"{layer} 共振（最差系 {damage_type}）"
            ),
            "type_id": None,
            "item_index": None,
            "attribute_id": resonance_attribute,
            "operator": "post_div",
            "contribution": None,
            "sign": 1,
            "factor": resonance,
            "penalty": None,
            "applied": True,
            "depth": 1,
            "truncated": res_truncated,
            "children": resonance_children,
        },
    ]

    replayed = hp_value / resonance
    return {
        "metric": metric,
        "view": metric.split(".", 1)[0],
        "value": value,
        "unit": _unit_of(hp_attribute),
        "combine": "multiplicative",
        "includes_base": True,
        "base": hp_value,
        "sources": sources,
        "folded_noops": budget["folded"],
        "unexplained": _clean_residual(value - replayed),
        "truncated_at_depth": depth,
        "truncated_nodes": budget["truncated"],
        "minimality": _minimality(sources, budget["truncated"]),
        "has_offsets": False,
        "tolerance": dict(TOLERANCE),
        "provenance": _provenance(ctx, metric),
        "replayed": replayed,
        "replay_ok": _close(replayed, value),
        "formula": (
            f"{_attribute_name(hp_attribute)} ÷ "
            f"{'max(四系共振)' if tied else f'共振[{damage_type}]'}"
        ),
        "damage_type": damage_type,
        "tied": tied,
    }


# =============================================================================
# 源 → 输出条目
# =============================================================================


def _entry(
    ctx: ViewContext,
    calculation: dict,
    source: Mapping[str, object],
    *,
    depth: int,
    depth_limit: int,
    budget: dict,
) -> dict:
    """一条引擎源 → 输出条目（含 depth 2 的 children）。"""
    operator = source.get("operator")
    quantity = int(source.get("quantity") or 1)
    raw = _as_float(source.get("value")) or 0.0
    origin = source.get("from") if isinstance(source.get("from"), dict) else {}
    kind, label, type_id, item_index = _identity(ctx, origin, source)

    contribution: float | None = None
    factor: float | None = None
    if operator in _ADDITIVE_OPS:
        contribution = raw * quantity if operator == "mod_add" else -(raw * quantity)
    elif operator in _MULTIPLICATIVE_OPS:
        factor = raw
    else:  # pre_assign / post_assign：直接设定值（当作贡献披露，不当因子乘）
        contribution = raw

    children: list[dict] = []
    truncated = depth < depth_limit
    if depth < depth_limit:
        children, _folded, child_truncated = _children(
            ctx, calculation, origin, source.get("source_attribute_id"), depth_limit, budget
        )
        truncated = child_truncated
    elif depth < MAX_DEPTH:
        # 调用方只要 depth 1：子层是**故意**没展开 → 对读者就是「这里还有更深的东西」
        truncated = True

    return {
        "kind": kind,
        "role": "primary",
        "label": label,
        "type_id": type_id,
        "item_index": item_index,
        "attribute_id": source.get("source_attribute_id"),
        "operator": operator,
        # **不做 3 位小数舍入**：贡献值必须能精确相加回 `value`（舍入会让残差升到 1e-7 量级，
        # 验收的「Σ == value（rel 1e-9）」立刻不成立）。展示用舍入由调用方决定。
        "contribution": contribution,
        "sign": _sign_of(operator, raw),
        "factor": factor,
        "penalty": source.get("penalty"),
        "applied": bool(source.get("applied", True)),
        "depth": depth,
        "truncated": truncated,
        "children": children,
    }


def _children(
    ctx: ViewContext,
    calculation: dict,
    origin: Mapping[str, object],
    source_attribute_id: object,
    depth_limit: int,
    budget: dict,
) -> tuple[list[dict], int, bool]:
    """展开某条源的**子源**（depth 2）。返回 `(children, folded, truncated)`。

    `from.type == "item"` 时 `source_attribute_id` 指**该模块自己**的属性
    （`items[index].attributes[...]`）—— 照船体属性查会把同一份数据算两遍。

    计数口径：`budget["depth2_nodes"]` 是**保留**的节点数，`budget["truncated"]` 是
    **被省略**的源数（两者不同，混用会让「没截断」被报成「截断了」），`budget["folded"]`
    累计子层的噪声过滤数。
    """
    budget.setdefault("truncated", 0)
    budget.setdefault("folded", 0)
    if depth_limit < 2:
        return [], 0, True  # 调用方只要 depth 1 → 子层是**故意**没展开
    node = _attribute_node(calculation, origin, source_attribute_id)
    if not isinstance(node, dict):
        return [], 0, False  # 叶子：该属性没有源，不是截断

    kept, folded = _filter_sources(node.get("sources") or [])
    budget["folded"] = int(budget["folded"]) + folded
    remaining = max(MAX_DEPTH2_NODES - int(budget.get("depth2_nodes") or 0), 0)
    selected = kept[: min(MAX_CHILDREN_PER_SOURCE, remaining)]
    omitted = len(kept) - len(selected)
    budget["depth2_nodes"] = int(budget.get("depth2_nodes") or 0) + len(selected)
    budget["truncated"] = int(budget["truncated"]) + omitted

    children = [
        _entry(
            ctx,
            calculation,
            source,
            depth=2,
            depth_limit=2,  # 子源自己不再展开（深度上限）
            budget=budget,
        )
        for source in selected
    ]
    return children, folded, omitted > 0


def _identity(
    ctx: ViewContext, origin: Mapping[str, object], source: Mapping[str, object]
) -> tuple[str, str, int | None, int | None]:
    """源 → `(kind, label, type_id, item_index)`；名字查不到就**原样给 id**（不编名字）。"""
    origin_type = str(origin.get("type") or "")
    source_attribute = source.get("source_attribute_id")
    attribute_label = _attribute_name(source_attribute) if isinstance(source_attribute, int) else "?"

    if origin_type == "item":
        item_index = origin.get("index")
        item = _item_row(ctx, item_index)
        type_id = item.get("type_id") if isinstance(item.get("type_id"), int) else None
        name = item.get("name")
        label = f"{name} · {attribute_label}" if isinstance(name, str) and name else attribute_label
        return "item", label, type_id, item_index if isinstance(item_index, int) else None

    if origin_type == "skill":
        type_id = origin.get("type_id")
        name = _type_name(ctx, type_id)
        label = f"技能 {name}" if name else f"技能 {type_id}"
        return "skill", label, type_id if isinstance(type_id, int) else None, None

    if origin_type in ("buff", "projected"):
        return origin_type, f"{origin_type} · {attribute_label}", None, None

    return "ship_attribute", attribute_label, None, None


def _attribute_node(
    calculation: Mapping[str, object], origin: Mapping[str, object], attribute_id: object
) -> dict | None:
    """按 `from.type` 挑对容器取属性节点。"""
    if not isinstance(attribute_id, int):
        return None
    origin_type = str(origin.get("type") or "ship")
    if origin_type == "ship":
        attributes = (calculation.get("ship") or {}).get("attributes") or {}
        node = attributes.get(attribute_id)
        return node if isinstance(node, dict) else None
    if origin_type == "item":
        index = origin.get("index")
        items = calculation.get("items") or []
        if not isinstance(index, int) or not 0 <= index < len(items):
            return None
        attributes = (items[index] or {}).get("attributes") or {}
        node = attributes.get(attribute_id)
        return node if isinstance(node, dict) else None
    return None  # skill / buff / projected：叶子


# =============================================================================
# 过滤 / 回放 / 残差
# =============================================================================


def _filter_sources(sources: list) -> tuple[list[dict], int]:
    """滤掉零项 / 恒等项 / 未生效项，返回 `(保留的源, 过滤个数)`。

    为什么必须滤：零项让「去掉这一项、和不变」的反向检查失去意义（验收判据破防），
    而且它们对读者只是记账噪声。
    """
    kept: list[dict] = []
    folded = 0
    for source in sources:
        if not isinstance(source, dict):
            folded += 1
            continue
        if not source.get("applied", True):
            folded += 1
            continue
        operator = source.get("operator")
        value = _as_float(source.get("value")) or 0.0
        if operator in _ADDITIVE_OPS or operator == "post_percent":
            if value == 0.0:
                folded += 1
                continue
        elif operator in ("pre_mul", "post_mul", "post_div"):
            if value == 1.0:
                folded += 1
                continue
        kept.append(source)
    return kept, folded


def _replay(base: float, sources: list[dict]) -> float:
    """按算子阶段回放 `value`（与引擎实测一致：f1 442/444、f7 446/457）。"""
    value = float(base)
    for operator in _OP_ORDER:
        for source in sources:
            if source.get("operator") != operator:
                continue
            raw = _as_float(source.get("value")) or 0.0
            quantity = int(source.get("quantity") or 1)
            penalty = source.get("penalty")
            penalty_value = 1.0 if penalty is None else float(penalty)
            if operator in ("pre_assign", "post_assign"):
                value = raw
            elif operator in ("pre_mul", "post_mul"):
                effective = 1.0 + (raw - 1.0) * penalty_value
                value *= effective**quantity
            elif operator == "post_div":
                effective = 1.0 + (raw - 1.0) * penalty_value
                if effective:
                    value /= effective**quantity
            elif operator == "post_percent":
                value *= 1.0 + (raw / 100.0) * penalty_value * quantity
            elif operator == "mod_add":
                value += raw * quantity
            elif operator == "mod_sub":
                value -= raw * quantity
    return value


def _mark_offsets(entries: list[dict]) -> list[dict]:
    """A 类：与主导方向相反的项打 `role: "offset"`（实测 0 例；出现即如实标注）。"""
    signs = [entry["sign"] for entry in entries if entry["contribution"] not in (None, 0.0)]
    if not signs:
        return entries
    dominant = 1 if sum(1 for sign in signs if sign > 0) >= sum(1 for sign in signs if sign < 0) else -1
    for entry in entries:
        if entry["contribution"] not in (None, 0.0) and entry["sign"] != dominant:
            entry["role"] = "offset"
    return entries


def _sign_of(operator: object, raw: float) -> int:
    """这一项对 `value` 的方向：+1 抬高 / -1 压低。"""
    if operator == "mod_sub":
        return -1 if raw >= 0.0 else 1
    if operator == "post_div":
        return 1 if raw <= 1.0 else -1
    if operator in ("pre_mul", "post_mul"):
        return 1 if raw >= 1.0 else -1
    return 1 if raw >= 0.0 else -1


# =============================================================================
# 小工具
# =============================================================================


def _cache_key(ctx: ViewContext, metric: str, depth: int) -> str:
    """`fit` 内容哈希 + metric + depth（同内容同结果；`ctx.engine` 的版本熵由 fit 自带）。"""
    payload = json.dumps(ctx.fit, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
    return f"{digest}|{metric}|{depth}"


def _minimality(entries: list[dict], truncated_nodes: int) -> str:
    """最小性声明：过滤过零项且没被截断 → `filtered_noops`；否则 `not_minimal`。"""
    if truncated_nodes > 0 or any(entry["role"] == "offset" for entry in entries):
        return "not_minimal"
    return "filtered_noops"


def _provenance(ctx: ViewContext, metric: str) -> str | None:
    """三态（与 `get_path` 同一来源；取不到给 `None`，不冒充）。"""
    answer = summary.get_path(ctx, metric)
    state = answer.get("provenance") if isinstance(answer, dict) else None
    return state if isinstance(state, str) else None


def _dps_subtotals(ctx: ViewContext, entries: list[dict]) -> dict[str, float]:
    """`dps.base` 的武器/无人机小计（`-12` **已含**无人机与舰载机，见 `firepower.py:69-70`）。"""
    subtotals = {"weapons": 0.0, "drones": 0.0, "fighters": 0.0}
    for entry in entries:
        if entry["contribution"] is None:
            continue
        index = entry.get("item_index")
        item = _item_row(ctx, index)
        slot = item.get("slot_type")
        if slot == _DRONE_SLOT:
            subtotals["drones"] += float(entry["contribution"])
        elif slot == _FIGHTER_SLOT:
            subtotals["fighters"] += float(entry["contribution"])
        else:
            subtotals["weapons"] += float(entry["contribution"])
    return subtotals


def _item_row(ctx: ViewContext, index: object) -> dict:
    """`ctx.items()` 里第 index 项（合并视图，带 `name` / `slot_type`）；取不到给空 dict。"""
    if not isinstance(index, int):
        return {}
    items = ctx.items(BASE)
    if 0 <= index < len(items):
        return items[index] if isinstance(items[index], dict) else {}
    return {}


def _type_name(ctx: ViewContext, type_id: object) -> str | None:
    """SDE 索引里的名字；没有索引或查不到给 `None`（不编名字）。"""
    if ctx.index is None or not isinstance(type_id, int):
        return None
    row = ctx.index.get_type(type_id)
    name = (row or {}).get("name")
    return name if isinstance(name, str) else None


def _attribute_name(attribute_id: int) -> str:
    """属性 ID → 英文名（`config/attributes.ATTRIBUTES` 的表名，查不到给 `attr N`）。"""
    row = A.ATTRIBUTES.get(attribute_id)
    return row[0] if row else f"attr {attribute_id}"


def _unit_of(attribute_id: int) -> str | None:
    """属性 ID → 物理单位标签（查不到给 `None`，不猜）。"""
    return A.UNIT_BY_ATTR.get(attribute_id)


def _layer_hp_attribute(layer: str) -> int:
    for name, hp_attribute, _ehp_attribute in _survivability.LAYERS:
        if name == layer:
            return hp_attribute
    raise KeyError(f"未知层 {layer!r}")


def _layer_resonances(attrs: Mapping[int, float], layer: str) -> tuple[float, ...] | None:
    """该层四系共振率（与 `views/survivability._resonances` 同一份口径）。"""
    values = tuple(_as_float(attrs.get(attribute_id)) for attribute_id in _survivability.RESONANCE_ATTRS[layer])
    if any(value is None for value in values):
        return None
    return tuple(value for value in values if value is not None)


def _operator_rank(entry: Mapping[str, object]) -> int:
    """因子链的排序键：按 dogma 的算子阶段（deterministic 回放顺序）。"""
    operator = str(entry.get("operator") or "")
    return _OP_ORDER.index(operator) if operator in _OP_ORDER else len(_OP_ORDER)


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _close(left: float, right: float) -> bool:
    """相对+绝对容差比较（`TOLERANCE`）。"""
    return abs(left - right) <= max(
        TOLERANCE["absolute"], TOLERANCE["relative"] * max(abs(left), abs(right))
    )


def _clean_residual(value: float) -> float:
    """残差落在容差内就是**真 0**（不输出 `-1.1e-13` 这种噪声冒充「解释不了」）。"""
    if _close(value, 0.0):
        return 0.0
    return value
