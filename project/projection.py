"""投射影响（P6-A）：把一艘船的 `outgoing` 投到另一艘船的 `incoming` 上。

设计依据：`.tmp/projection_contract.md`（3/5 取证单）。引擎原生支持「船与船互相影响」：

```
Calculation.outgoing : Projection = {buffs: [{id, value}], effects: [{type_id, effect_id, attributes}]}
Fit.incoming         : Projection     # "What one fit hands to another."（引擎 docstring）
```

本模块**不重算任何 dogma**：每个来源船算一次 `calculate` 取 `outgoing`，合并后塞进目标的
`incoming`，再算一次目标。所有数值都是引擎给的。

## 三条实测语义（决定了实现，别「顺手改」）

1. **`effects` 严格按 `state == "active"` 产生**：offline / online 的 web、遥修、点都不出 effect。
   所以 effects 不需要我们过滤 —— 引擎已经过滤了。
2. **`buffs` 只看「模块在 + 弹头在」，与 `state` 无关**：offline 的指挥爆发器照样产 buff。
   → `strict_state=True`（默认）时，本模块用「只保留 active 模块」的变体重算一遍来源船，
   只采信那份 buffs（照 EVE：不运行的模块不提供加队加成）；`strict_state=False` 时原样采信引擎。
   **两种取值都必须留 warning**（差异要说出口，不能静默）。
3. **命令舰会被自己的脉冲加成**（引擎行为，实测 Absolution armorHP 15192 → 17659.181，+16.24%）。
   本模块**保留**，并在 `warnings` 里注明「未与游戏内行为对拍」——不替引擎猜。

## 另外三条边界

- **`outgoing` 是唯一可信的投影来源**。`dogma.beacon(type_id)` 返回该 type 的**全部** effect
  （含模块自用的 CPU/电网/质量效果），整包塞进目标会把 `cpuLoad 955.2 → 985.2` 这类副作用带上。
  本模块只用它做**编号目录**（`_effect_catalog`），绝不整包应用。
- **`effect_id` 是信任边界**：引擎对未知 `effect_id` **静默忽略**（无异常、无 warning）。
  所以调用方显式给的 effects 会先查目录，不在目录里 → `unknown_effect_id` warning 并跳过。
- **没有链式传递**：`incoming` 不改变 `outgoing`（实测 B 收了 A 的 web 后 outgoing 逐项不变）。
  编队只能「并联」：每个目标收所有来源的并集，边由调用方枚举。

## 成本

真实 `dogma.calculate` 实测 1.3–1.6 ms/次（**不要用 `Engine.calculate` 的 LRU 命中值 0.089 ms
当基准**）。本模块一次调用 = 目标基线 1 次（`sources=True`，拿归因）+ 目标投影 1 次
+ 每个有 buffs 的来源 1 次（strict 变体确认）。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from noobbaozi.config import attributes as A
from noobbaozi.project import explain
from noobbaozi.project.views.base import ViewContext, ship_attributes

STRICT_STATE_DEFAULT: bool = True
"""默认按 EVE 过滤：非 active 的投射源不提供加成（引擎对 buffs 不看 state，见模块 docstring）。"""

METRIC_PATHS: tuple[str, ...] = (
    "survivability.ehp_mixed",
    "survivability.thinnest.shield.ehp",
    "survivability.thinnest.armor.ehp",
    "survivability.thinnest.hull.ehp",
    "mobility.speed_mps",
    "stability.capacitor.capacity",
    "stability.capacitor.recharge_s",
    "stability.capacitor.stable_pct",
    "stability.capacitor.peak_load",
    "stability.capacitor.peak_recharge",
)
"""`delta.metrics` 的固定顺序：EHP（混合 + 三层）/ 速度 / 电容六项。"""

_METRIC_UNITS: dict[str, str] = {
    "survivability.ehp_mixed": "hp",
    "survivability.thinnest.shield.ehp": "hp",
    "survivability.thinnest.armor.ehp": "hp",
    "survivability.thinnest.hull.ehp": "hp",
    "mobility.speed_mps": "m/s",
    "stability.capacitor.capacity": "GJ",
    "stability.capacitor.recharge_s": "s",
    "stability.capacitor.stable_pct": "%",
    "stability.capacitor.peak_load": "GJ/s",
    "stability.capacitor.peak_recharge": "GJ/s",
}

_EXTRA_METRIC_ATTRS: dict[str, int] = {
    "stability.capacitor.capacity": A.ATTR_CAPACITOR_CAPACITY,
    "stability.capacitor.recharge_s": A.ATTR_CAPACITOR_RECHARGE_RATE,
    "stability.capacitor.stable_pct": A.ATTR_CAP_STABLE_PCT,
    "stability.capacitor.peak_load": A.ATTR_CAP_PEAK_LOAD,
    "stability.capacitor.peak_recharge": A.ATTR_CAP_PEAK_RECHARGE,
}

_REPAIR_ATTRS: dict[str, tuple[int, int | None]] = {
    "armor": (A.ATTR_ARMOR_REPAIR_RATE, A.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE),
    "hull": (A.ATTR_HULL_REPAIR_RATE, A.ATTR_HULL_EFFECTIVE_REPAIR_RATE),
    "shield": (A.ATTR_SHIELD_BOOST_RATE, None),
}
"""遥修落点：`-45/-46/-47` 是 raw rate，`-48/-49` 是引擎按抗性折算的**有效**修复率。"""

_EFFECT_CATALOG: dict[int, frozenset[int]] = {}
"""`type_id → 该 type 的全部 effect_id`（来自 `dogma.beacon()`，**只当目录用**）。"""


# =============================================================================
# 对外入口
# =============================================================================

def project(
    target_ctx: ViewContext,
    sources: Sequence[object] | None = None,
    effects: Sequence[Mapping[str, object]] | None = None,
    buffs: Sequence[Mapping[str, object]] | None = None,
    *,
    strict_state: bool = STRICT_STATE_DEFAULT,
) -> dict:
    """把来源船的投射影响投到目标上，返回「谁给了什么 + 前后差多少」。

    Args:
        target_ctx: 目标配装的 `ViewContext`（与 `render_view` / `explain` 同一个）。
        sources: 来源船，逐项可以是：
            - `ViewContext`（标签取 `fit["name"]`）；
            - `{"ctx": ViewContext, "label": "指挥舰 A"}`。
        effects: 显式 `ProjectedEffect` 列表（`{type_id, effect_id, attributes}`）。
            **会先查编号目录**：`effect_id` 不在该 type 的目录里 → `unknown_effect_id` warning 并跳过
            （引擎对未知 effect_id 静默忽略，我们不能静默）。
        buffs: 显式 `ProjectedBuff` 列表（`{id, value}`）。
        strict_state: `True`（默认）= 非 `active` 的模块不提供加成（照 EVE）；
            `False` = 引擎原样（引擎对 buffs 忽略 state）。

    Returns:
        `{strict_state, target, applied, sources, delta, repaired, warnings}`：

        - `applied.effects[]`：合并后的 effect（下标即 `projected.index` 的语义），每条带
          `from`（`source` / `explicit` 与原始下标）；
        - `applied.buffs[]`：按 `id` 取 max 后的 buff，带 `from`（提供该值的来源标签）；
        - `delta.metrics[]`：`{path, before, after, delta, delta_pct, unit}`，
          顺序 = `METRIC_PATHS`（EHP 混合 + 三层 / 速度 / 电容六项）；
        - `delta.attributes[]`：**每个真的变了的**船体属性 `{id, name, before, after, delta,
          attributed_to[]}`（`attributed_to` 把 `projected.index` 映射回「谁造成的」）；
        - `repaired`：遥修落点（raw / 有效修复率的前后与增量；缺值给 `None`，不补 0）；
        - `warnings[]`：见模块 docstring 的语义说明（`buff_state_filtered` /
          `buff_state_not_filtered` / `unknown_effect_id` / `self_buff_included` / …）。

    Raises:
        TypeError: `target_ctx` 不是 `ViewContext`，或某个 source 既不是 `ViewContext`
            也不是 `{"ctx": ViewContext}`。
        ValueError: `strict_state` 不是 `bool`。

    Note:
        不做「命中率 / 有效 DPS」（那是交战数学，另模块）；不做距离 / 视线 / 持续时间 /
        叠加上限（引擎本来就没有这些维度）。
    """
    if not isinstance(target_ctx, ViewContext):
        raise TypeError(f"target_ctx 必须是 ViewContext，收到 {type(target_ctx).__name__}")
    if not isinstance(strict_state, bool):
        raise ValueError(f"strict_state 必须是 bool，收到 {strict_state!r}")

    warnings: list[dict] = []
    source_items = _normalize_sources(sources, warnings)

    merged = _merge_sources(source_items, strict_state, warnings)
    _merge_explicit_effects(target_ctx, effects, merged, warnings)
    _merge_explicit_buffs(buffs, merged, warnings)

    # 基线用**普通** calculate（走引擎 LRU：命中 0.09 ms / 冷启动 1.7 ms）。
    # `sources=True` 实测 3.3 ms（普通调用的 ~2 倍），只给「真的有投射」的投影侧用 ——
    # 归因（`projected.index → 谁造成的`）只需要投影侧，基线侧不需要。
    baseline_calc = target_ctx.engine.calculate(target_ctx.fit)
    projected_fit = _with_incoming(target_ctx.fit, merged)
    if merged["effects"] or merged["buffs"]:
        projected_calc = target_ctx.engine.calculate(projected_fit, sources=True)
    else:
        projected_calc = baseline_calc  # 没有任何投射：恒等，不必再算一次

    if _target_self_buffs(target_ctx, baseline_calc):
        warnings.append(
            {
                "kind": "self_buff_included",
                "message": (
                    "目标自身也带着指挥脉冲：本数值包含发射者自己受到的脉冲加成。"
                    "引擎行为如此，未与游戏内行为对拍。"
                ),
            }
        )

    delta = _delta_block(target_ctx, baseline_calc, projected_calc, merged, warnings)

    return {
        "strict_state": strict_state,
        "target": _target_block(target_ctx),
        "applied": {
            "effects": [
                {
                    "index": position,
                    "type_id": effect["type_id"],
                    "type_name": _type_name(target_ctx, effect["type_id"]),
                    "effect_id": effect["effect_id"],
                    "attributes": {str(k): v for k, v in effect.get("attributes", {}).items()},
                    "from": origin,
                }
                for position, (effect, origin) in enumerate(
                    zip(merged["effects"], merged["effect_origins"])
                )
            ],
            "buffs": [
                {"id": buff_id, "value": value, "from": merged["buff_origins"][buff_id]}
                for buff_id, value in sorted(merged["buffs"].items())
            ],
        },
        "sources": merged["source_rows"],
        "delta": delta,
        "repaired": _repaired_block(baseline_calc, projected_calc),
        "warnings": warnings,
    }


def effect_catalog(type_id: int) -> frozenset[int]:
    """某 `type_id` 的全部 effect 编号（`dogma.beacon()` 的编号目录；本模块只当校验用）。

    `beacon()` 返回的是**该 type 的全部 effect**（含模块自用效果），不能整包当投影用 ——
    这里只取 `effect_id` 当集合。查不到（未知 type / 引擎异常）→ 空集合。
    """
    key = int(type_id)
    cached = _EFFECT_CATALOG.get(key)
    if cached is not None:
        return cached
    try:
        import esf_dogma_engine as dogma  # noqa: PLC0415 - 只在这里用一次，见模块 docstring

        projection = dogma.beacon(key)
        ids = frozenset(
            int(effect["effect_id"])
            for effect in (projection.get("effects") or [])
            if isinstance(effect.get("effect_id"), int)
        )
    except Exception:  # noqa: BLE001 - 未知 type / 引擎拒绝都按「没有目录」处理
        ids = frozenset()
    _EFFECT_CATALOG[key] = ids
    return ids


def clear_effect_catalog() -> None:
    """清空编号目录缓存（测试用；目录是纯函数结果，正常不需要清）。"""
    _EFFECT_CATALOG.clear()


# =============================================================================
# sources：逐个取 outgoing
# =============================================================================

def _normalize_sources(
    sources: Sequence[object] | None, warnings: list[dict]
) -> list[dict]:
    if sources is None:
        return []
    if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
        raise TypeError("sources 必须是数组（每项 ViewContext 或 {'ctx': ViewContext}）")

    normalized: list[dict] = []
    for position, raw in enumerate(sources):
        if isinstance(raw, ViewContext):
            ctx = raw
            label = _fit_label(raw.fit) or f"source[{position}]"
        elif isinstance(raw, Mapping) and isinstance(raw.get("ctx"), ViewContext):
            ctx = raw["ctx"]
            label = str(raw.get("label") or _fit_label(ctx.fit) or f"source[{position}]")
        else:
            raise TypeError(
                f"sources[{position}] 必须是 ViewContext 或 {{'ctx': ViewContext}}，"
                f"收到 {type(raw).__name__}"
            )
        normalized.append({"ctx": ctx, "label": label})
    return normalized


def _source_outgoing(
    source: dict,
    strict_state: bool,
    warnings: list[dict],
) -> dict:
    """来源船的 `outgoing`（按 `strict_state` 决定 buffs 是否过滤非 active 源）。"""
    ctx: ViewContext = source["ctx"]
    label = source["label"]
    outgoing = ctx.engine.calculate(ctx.fit).get("outgoing") or {}
    effects = [dict(effect) for effect in (outgoing.get("effects") or [])]
    buffs = {int(buff["id"]): float(buff["value"]) for buff in (outgoing.get("buffs") or [])}

    filtered = None
    if buffs:
        strict_fit = _active_only_fit(ctx.fit)
        if strict_fit is not None:
            strict_outgoing = ctx.engine.calculate(strict_fit).get("outgoing") or {}
            strict_buffs = {
                int(buff["id"]): float(buff["value"])
                for buff in (strict_outgoing.get("buffs") or [])
            }
            if strict_buffs != buffs:
                dropped = sorted(set(buffs) - set(strict_buffs))
                weakened = {
                    buff_id: {"engine": buffs[buff_id], "active_only": strict_buffs[buff_id]}
                    for buff_id in sorted(set(buffs) & set(strict_buffs))
                    if strict_buffs[buff_id] != buffs[buff_id]
                }
                filtered = {
                    "dropped_ids": dropped,
                    "weakened": weakened,
                    "count": len(dropped) + len(weakened),
                }
                if strict_state:
                    buffs = strict_buffs
                    warnings.append(
                        {
                            "kind": "buff_state_filtered",
                            "message": (
                                f"{label}: 已按 state 过滤 —— {filtered['count']} 个来自非 active "
                                "投射源的 buff 未计入"
                                f"（整条丢弃 {len(dropped)} 条 / 取值下调 {len(weakened)} 条；"
                                "引擎本身不看 state）"
                            ),
                            "source": label,
                            "filtered": filtered,
                        }
                    )
                else:
                    warnings.append(
                        {
                            "kind": "buff_state_not_filtered",
                            "message": (
                                f"{label}: 按引擎原样 —— 非 active 源也计入 buffs"
                                f"（这些 id 本应被过滤：{filtered['dropped_ids']}）"
                            ),
                            "source": label,
                            "filtered": filtered,
                        }
                    )
    elif not effects:
        warnings.append(
            {
                "kind": "no_outgoing",
                "message": f"{label}: 没有任何投射模块（outgoing 的 effects 与 buffs 都为空）",
                "source": label,
            }
        )

    return {
        "label": label,
        "effects": effects,
        "buffs": buffs,
        "filtered": filtered,
    }


def _merge_sources(
    sources: list[dict],
    strict_state: bool,
    warnings: list[dict],
) -> dict:
    """来源并集：effects 拼接（保持顺序），buffs 按 id 取 max。"""
    merged: dict = {
        "effects": [],
        "effect_origins": [],
        "buffs": {},
        "buff_origins": {},
        "source_rows": [],
    }

    for source in sources:
        outgoing = _source_outgoing(source, strict_state, warnings)
        for effect_position, effect in enumerate(outgoing["effects"]):
            merged["effects"].append(effect)
            merged["effect_origins"].append(
                {
                    "kind": "source",
                    "source": outgoing["label"],
                    "source_effect_index": effect_position,
                }
            )
        _merge_buffs(merged, outgoing["buffs"], {"kind": "source", "source": outgoing["label"]})
        merged["source_rows"].append(
            {
                "label": outgoing["label"],
                "ship": _ship_block(source["ctx"]),
                "effects": len(outgoing["effects"]),
                "buffs": len(outgoing["buffs"]),
                "filtered": outgoing["filtered"],
            }
        )
    return merged


def _merge_buffs(merged: dict, buffs: Mapping[int, float], origin: dict) -> None:
    """按 `id` 取 **max**（引擎自己也取 max：5 / 16.24 / 20 / 30 / 50 → max 生效，顺序无关）。"""
    for buff_id, value in buffs.items():
        current = merged["buffs"].get(buff_id)
        if current is None or value > current:
            merged["buffs"][buff_id] = value
            merged["buff_origins"][buff_id] = [dict(origin)]
        elif value == current:
            merged["buff_origins"][buff_id].append(dict(origin))


# =============================================================================
# 显式 effects / buffs（调用方手工构造的那部分）
# =============================================================================

def _merge_explicit_effects(
    target_ctx: ViewContext,
    effects: Sequence[Mapping[str, object]] | None,
    merged: dict,
    warnings: list[dict],
) -> None:
    if effects is None:
        return
    if isinstance(effects, (str, bytes)) or not isinstance(effects, Sequence):
        warnings.append({"kind": "invalid_effect", "message": "effects 必须是数组"})
        return

    for position, raw in enumerate(effects):
        if not isinstance(raw, Mapping):
            warnings.append(
                {"kind": "invalid_effect", "message": f"effects[{position}] 不是对象：{raw!r}"}
            )
            continue
        type_id = raw.get("type_id")
        effect_id = raw.get("effect_id")
        if not _is_int(type_id) or not _is_int(effect_id):
            warnings.append(
                {
                    "kind": "invalid_effect",
                    "message": f"effects[{position}] 的 type_id / effect_id 必须是整数",
                }
            )
            continue
        attributes = raw.get("attributes")
        if attributes is None:
            attributes = {}
        if not isinstance(attributes, Mapping):
            warnings.append(
                {
                    "kind": "invalid_effect",
                    "message": f"effects[{position}] 的 attributes 必须是对象",
                }
            )
            continue
        normalized_attrs: dict[int, float] = {}
        bad = False
        for key, value in attributes.items():
            attribute_id = int(key) if _is_int(key) or str(key).lstrip("-").isdigit() else None
            if attribute_id is None or isinstance(value, bool) or not isinstance(value, (int, float)):
                bad = True
                break
            normalized_attrs[attribute_id] = float(value)
        if bad:
            warnings.append(
                {
                    "kind": "invalid_effect",
                    "message": f"effects[{position}] 的 attributes 键/值不合法：{dict(attributes)!r}",
                }
            )
            continue

        catalog = effect_catalog(type_id)
        if not catalog:
            warnings.append(
                {
                    "kind": "effect_catalog_unavailable",
                    "message": (
                        f"effects[{position}]：type_id={type_id} 查不到 effect 目录"
                        "（未知 type）→ 无法校验 effect_id，按原样交给引擎"
                    ),
                }
            )
        elif effect_id not in catalog:
            warnings.append(
                {
                    "kind": "unknown_effect_id",
                    "message": (
                        f"effects[{position}]：effect_id={effect_id} 不在 type_id={type_id} 的 "
                        f"effect 目录里（可用示例：{sorted(catalog)[:8]}）→ 跳过"
                        "（引擎会静默忽略它，我们不能静默）"
                    ),
                }
            )
            continue

        merged["effects"].append(
            {"type_id": type_id, "effect_id": effect_id, "attributes": normalized_attrs}
        )
        merged["effect_origins"].append(
            {"kind": "explicit", "source": None, "effect_index": position}
        )


def _merge_explicit_buffs(
    buffs: Sequence[Mapping[str, object]] | None, merged: dict, warnings: list[dict]
) -> None:
    if buffs is None:
        return
    if isinstance(buffs, (str, bytes)) or not isinstance(buffs, Sequence):
        warnings.append({"kind": "invalid_buff", "message": "buffs 必须是数组"})
        return
    for position, raw in enumerate(buffs):
        if not isinstance(raw, Mapping):
            warnings.append(
                {"kind": "invalid_buff", "message": f"buffs[{position}] 不是对象：{raw!r}"}
            )
            continue
        buff_id = raw.get("id")
        value = raw.get("value")
        if not _is_int(buff_id) or isinstance(value, bool) or not isinstance(value, (int, float)):
            warnings.append(
                {
                    "kind": "invalid_buff",
                    "message": f"buffs[{position}] 需要整数 id 与数值 value：{dict(raw)!r}",
                }
            )
            continue
        _merge_buffs(merged, {int(buff_id): float(value)}, {"kind": "explicit", "source": None})


# =============================================================================
# delta / repaired
# =============================================================================

def _delta_block(
    target_ctx: ViewContext,
    baseline: dict,
    projected: dict,
    merged: dict,
    warnings: list[dict],
) -> dict:
    baseline_values = ship_attributes(baseline)
    projected_values = ship_attributes(projected)

    metrics = []
    for path in METRIC_PATHS:
        before = _metric_value(baseline, baseline_values, path)
        after = _metric_value(projected, projected_values, path)
        metrics.append(_delta_entry(path, before, after, _METRIC_UNITS[path]))

    changed = sorted(
        attribute_id
        for attribute_id in set(baseline_values) | set(projected_values)
        if baseline_values.get(attribute_id) != projected_values.get(attribute_id)
    )
    attributes = [
        {
            "id": attribute_id,
            "name": _attribute_name(target_ctx, attribute_id),
            "before": baseline_values.get(attribute_id),
            "after": projected_values.get(attribute_id),
            "delta": (
                None
                if baseline_values.get(attribute_id) is None
                or projected_values.get(attribute_id) is None
                else projected_values[attribute_id] - baseline_values[attribute_id]
            ),
            "attributed_to": _attribution(projected, attribute_id, merged),
        }
        for attribute_id in changed
    ]
    return {"metrics": metrics, "attributes": attributes}


def _metric_value(calculation: dict, values: Mapping[int, float], path: str) -> float | None:
    if path in _EXTRA_METRIC_ATTRS:
        return values.get(_EXTRA_METRIC_ATTRS[path])
    return explain.metric_value(calculation, path)


def _delta_entry(path: str, before: float | None, after: float | None, unit: str | None) -> dict:
    if before is None or after is None:
        delta = None
        delta_pct = None
    else:
        delta = after - before
        delta_pct = None if before == 0.0 else delta / before
    return {
        "path": path,
        "before": before,
        "after": after,
        "delta": delta,
        "delta_pct": delta_pct,
        "unit": unit,
    }


def _attribution(projected: dict, attribute_id: int, merged: dict) -> list[dict]:
    """把 `from.type == "projected"` 的源映射回「谁造成的」（index = `incoming.effects` 下标）。"""
    node = _raw_attribute(projected, attribute_id)
    if not isinstance(node, dict):
        return []
    rows: list[dict] = []
    for source in node.get("sources") or []:
        origin = source.get("from") or {}
        if origin.get("type") != "projected":
            continue
        index = origin.get("index")
        applied = merged["effect_origins"][index] if isinstance(index, int) and 0 <= index < len(merged["effect_origins"]) else None
        rows.append(
            {
                "effect_index": index,
                "operator": source.get("operator"),
                "value": source.get("value"),
                "penalty": source.get("penalty"),
                "source": applied,
            }
        )
    return rows


def _repaired_block(baseline: dict, projected: dict) -> dict:
    baseline_values = ship_attributes(baseline)
    projected_values = ship_attributes(projected)
    result: dict[str, dict] = {}
    for layer, (raw_attribute, effective_attribute) in _REPAIR_ATTRS.items():
        result[layer] = {
            "rate": _delta_entry(
                f"{layer}.repair_rate",
                baseline_values.get(raw_attribute),
                projected_values.get(raw_attribute),
                "hp/s",
            ),
            "effective": None
            if effective_attribute is None
            else _delta_entry(
                f"{layer}.effective_repair_rate",
                baseline_values.get(effective_attribute),
                projected_values.get(effective_attribute),
                "hp/s",
            ),
        }
    return result


# =============================================================================
# 小工具
# =============================================================================

def _with_incoming(fit: dict, merged: dict) -> dict:
    incoming: dict = {}
    if merged["effects"]:
        incoming["effects"] = merged["effects"]
    if merged["buffs"]:
        incoming["buffs"] = [
            {"id": buff_id, "value": value} for buff_id, value in sorted(merged["buffs"].items())
        ]
    return {**fit, "incoming": incoming} if incoming else fit


def _active_only_fit(fit: dict) -> dict | None:
    """只保留 `state == "active"` 的模块；全 active 时返回 `None`（不必重算）。"""
    items = fit.get("items") or []
    active = [item for item in items if str(item.get("state") or "active") == "active"]
    if len(active) == len(items):
        return None
    return {**fit, "items": active}


def _target_self_buffs(ctx: ViewContext, calculation: dict) -> bool:
    outgoing = calculation.get("outgoing") or {}
    return bool(outgoing.get("buffs")) or bool(calculation.get("buffs"))


def _target_block(ctx: ViewContext) -> dict:
    ship = ctx.fit.get("ship") or {}
    return {
        "name": _fit_label(ctx.fit),
        "ship": {
            "type_id": ship.get("type_id"),
            "name": _type_name(ctx, ship.get("type_id")),
        },
    }


def _ship_block(ctx: ViewContext) -> dict:
    ship = ctx.fit.get("ship") or {}
    return {
        "type_id": ship.get("type_id"),
        "name": _type_name(ctx, ship.get("type_id")),
    }


def _fit_label(fit: Mapping[str, object]) -> str | None:
    name = fit.get("name")
    return name if isinstance(name, str) and name else None


def _type_name(ctx: ViewContext, type_id: object) -> str | None:
    if ctx.index is None or not isinstance(type_id, int):
        return None
    row = ctx.index.get_type(type_id) or {}
    name = row.get("name")
    return name if isinstance(name, str) else None


def _attribute_name(ctx: ViewContext, attribute_id: int) -> str | None:
    row = A.ATTRIBUTES.get(attribute_id)
    if row:
        return row[0]
    if ctx.index is not None:
        sde_row = ctx.index.attribute(attribute_id) or {}
        name = sde_row.get("name")
        if isinstance(name, str):
            return name
    return None


def _raw_attribute(calculation: dict, attribute_id: int) -> dict | None:
    attributes = (calculation.get("ship") or {}).get("attributes") or {}
    node = attributes.get(attribute_id)
    if node is None:
        node = attributes.get(str(attribute_id))
    return node if isinstance(node, dict) else None


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
