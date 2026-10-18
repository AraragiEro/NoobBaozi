"""视角六 · 不稳定 `stability`（字段规格 §6）。

只输出事实：电容的容量/回充/稳定百分比/耗尽秒/峰值负载/峰值回充/净差、逐模块耗电排行、
逐模块关闭后的稳定百分比、是否依赖外来支援、消耗品支撑时长。

数值纪律：`ctx.attrs()` / `ctx.items()` 的值**已是物理单位**（`55` / `73` / `-3` 的毫秒都已由
`project/units.py` 按 `config/attributes.py` 的缩放表换算成秒），本模块不再乘除 1000；
缺值给 `None` 不补 0；
浮点一律过 `units.fmt`。**不设绝对阈值**——「不稳定」与否由引擎的 `capStablePct`(-72)
自己表达，本模块不下判词。`flags` 恒为 `[]`（诊断统一在渲染层算一次）。

**逐模块耗电为什么读装备级 `-4`，而不是自算 `capacitorNeed(6) / 周期`**：
实测（SDE build 3607705 / 引擎 13.6.0）引擎在**装备作用域**也回 `-4 capPeakLoad`
（`Light Neutron Blaster II` = 0.768 GJ/s，逐项求和 22.174 GJ/s 与船体 `-4`
22.1737 一致），它已经把装药种类、周期与状态算进去了，比自算更准；
`capacitorNeed(6)` 反而**不在常量表**里。

**摘模块必须用 `fit["items"]` 的原下标**：`ctx.items()` 是「已装 + 货舱」的合并视图，
样本里 17 个已装模块后面跟着 14 个货舱项，按下标摘除时一旦用错列表就会摘掉货舱物品
（已知坑）。本模块的 `_fit_without()` 只对 `ctx.fit["items"]` 操作。

**T-20 收口的两处口径**（都是真实样本暴露的，别改回去）：

1. **D5 · 弹药续航只有一份实现**：`consumables.ammo_endurance_s` 与
   `firepower.ammo_endurance_s` 是**同一个事实**，现在都走
   `views/shared.py::weapon_records()` + `ammo_endurance_s()`。以前这里自带一份
   （要求武器同时有 `51` 与 `64`，且用 `int(capacity / 体积)` 无浮点容差），
   导弹发射器没有装备级 `64` → f4/f5 报 `None`，而火力视角报 172.663 / 250.323。
2. **D6 · 电容耗尽时长归一**：引擎 `-7 capDepletesIn` 对「电容稳定、永不耗尽」
   给的是**哨兵 `-1.0`**（f3/f4/f5/f6 实测），不是秒数。`capacitor.depletes_in_s`
   与 `cap_sensitivity[].depletes_in_s` 都过 `shared.depletes_in_s()`：
   `stable_pct > 0` 或引擎给负数 → `None`；否则照实给秒数（f1 = 38.4 s）。

`cap_sensitivity` 只算耗电最高的 5 个模块（13.6 ms 冷启动预算内，最多 5 次变体）。
"""

from __future__ import annotations

from evefit.config import attributes as A
from evefit.config.attributes import ATTR_CHARGE_RATE, ATTR_MODULE_DURATION
from evefit.project import units
from evefit.project.views import register, shared
from evefit.project.views.base import BASE, SLOT_CARGO, ViewContext, ship_attributes

# =============================================================================
# 上限与结构判定用 group 名（SDE 实测）
# =============================================================================

MAX_CAP_BY_MODULE: int = 8
"""`cap_by_module` 最多列几组（按耗电降序）。"""

MAX_CAP_SENSITIVITY: int = 5
"""`cap_sensitivity` 最多摘几个模块（每次一个变体，实测 0.33 ms/次）。"""

REMOTE_REPAIR_GROUPS: frozenset[str] = frozenset(
    {
        "Remote Armor Repairer",
        "Ancillary Remote Armor Repairer",
        "Remote Shield Booster",
        "Remote Hull Repairer",
    }
)
"""遥修类 group 名（`Index.search_types` 实测：Small/Medium/Large 同组）。"""

CAP_TRANSFER_GROUPS: frozenset[str] = frozenset({"Remote Capacitor Transmitter"})
"""传电类 group 名（实测 `Medium Remote Capacitor Transmitter I` → 该组）。"""

CAP_BOOSTER_GROUP: str = "Capacitor Booster"
"""电容注射器模块的 group 名（`Cap Booster 400` 属 `Capacitor Booster Charge`，不是模块）。"""

PASTE_GROUP: str = "Nanite Repair Paste"
"""纳米修复胶的 group 名（既是物品组，也是弹药组）。"""

# =============================================================================
# 字段 → 属性 ID
# =============================================================================

FIELD_SOURCES: dict[str, int | None] = {
    "capacitor.capacity": A.ATTR_CAPACITOR_CAPACITY,
    "capacitor.recharge_s": A.ATTR_CAPACITOR_RECHARGE_RATE,
    "capacitor.stable_pct": A.ATTR_CAP_STABLE_PCT,
    "capacitor.depletes_in_s": A.ATTR_CAP_DEPLETES_IN,
    "capacitor.peak_load": A.ATTR_CAP_PEAK_LOAD,
    "capacitor.peak_recharge": A.ATTR_CAP_PEAK_RECHARGE,
    "capacitor.delta": A.ATTR_CAP_DELTA_PEAK,
    # 净差百分比（-6 = -5/-2×100）；字段规格只列 delta(-5)，此处额外给出百分比形式
    "capacitor.delta_pct": A.ATTR_CAP_DELTA_PEAK_PCT,
    # 耗电排行：装备作用域的 -4 就是逐模块耗电
    "cap_by_module[].module": None,
    "cap_by_module[].type_id": None,
    "cap_by_module[].count": None,
    "cap_by_module[].cap_use_gj_s": A.ATTR_CAP_PEAK_LOAD,
    # 敏感度：关闭该模块后重算出的 -72 / -7（差值是我方合成）
    "cap_sensitivity[].module": None,
    "cap_sensitivity[].type_id": None,
    "cap_sensitivity[].cap_use_gj_s": A.ATTR_CAP_PEAK_LOAD,
    "cap_sensitivity[].stable_pct": A.ATTR_CAP_STABLE_PCT,
    "cap_sensitivity[].depletes_in_s": A.ATTR_CAP_DEPLETES_IN,
    "cap_sensitivity[].stable_delta_pct": None,
    # 外部依赖是 group 名结构判定（不来自单个属性）；`*_modules` 是命中的模块名清单（字符串数组）
    "external_dependency.remote_rep": None,
    "external_dependency.cap_transfer": None,
    "external_dependency.remote_rep_modules": None,
    "external_dependency.cap_transfer_modules": None,
    # 消耗品续航是自算合成值；周期来自 51 / 73
    "consumables.ammo_endurance_s": None,
    "consumables.ammo_module": None,
    "consumables.ammo_rounds": None,
    "consumables.ammo_cycle_s": A.ATTR_CYCLE_TIME,
    "consumables.paste_endurance_s": None,
    "consumables.paste_units": None,
    "consumables.paste_per_cycle": None,
    "consumables.cap_booster": None,
}
"""overview 点路径 → 来源属性 ID（渲染层据此打 provenance）。

数组一律用 `[]` **元素约定**（`cap_by_module[].module`、`cap_sensitivity[].module`），
与其它五个视角同一套口径；字符串叶子（模块名 / 弹药名 / `*_modules` 名称清单）不是属性
直读，登记 `None` → 渲染层判 `assumed`（合成/enum 值），**不冒充** `measured`，
也别让它们落成 `unknown`（漏登记就是「输出无 provenance 的数值」，红线 N3/N4）。
"""


# =============================================================================
# 小工具
# =============================================================================


def _fmt(value: float | None) -> float | None:
    """浮点去噪（`round(x, 3)`）；`None` 原样传递。"""
    return None if value is None else units.fmt(value)


def _number(values: dict[int, float], attribute_id: int) -> float | None:
    """取一个物理值；缺失返回 `None`（**不补 0**，红线 N4）。"""
    return values.get(attribute_id)


def _fitted(items: list[dict]) -> list[tuple[int, dict]]:
    """已装模块（排除货舱）；下标与 `ctx.fit["items"]` 一一对应（硬结论 44）。"""
    return [
        (position, item)
        for position, item in enumerate(items)
        if item.get("slot_type") != SLOT_CARGO
    ]


def _group_name(ctx: ViewContext, type_id: object) -> str | None:
    """type_id → SDE group 名；没有索引或类型不存在时 `None`（不猜）。"""
    if ctx.index is None or not isinstance(type_id, int):
        return None
    row = ctx.index.get_type(type_id)
    return (row or {}).get("group_name")


def _fit_without(fit: dict, drop_position: int) -> dict:
    """摘掉 `fit["items"][drop_position]`（**原下标**，不改入参）。"""
    items = [
        item
        for position, item in enumerate(fit.get("items") or [])
        if position != drop_position
    ]
    return {**fit, "items": items}


def _cycle_s(attributes: dict[int, float]) -> float | None:
    """模块周期（秒）：优先 `speed(51)`，否则 `duration(73)`——**两者都是投影层给的物理值**。

    `73 duration` 自 T-13 起已在 `SCALE_BY_ATTR`（毫秒）里，`ctx.attrs()` / `ctx.items()`
    返回的就是秒；这里**不得**再乘除 1000（否则双换算，输出会小 1000 倍，红线 N5）。
    调用约定：入参必须是**物理单位**（秒）。传原始毫秒会得到 1000 倍的值。
    """
    cycle = attributes.get(A.ATTR_CYCLE_TIME)
    if cycle is not None:
        return float(cycle)
    duration = attributes.get(ATTR_MODULE_DURATION)
    if duration is None:
        return None
    return float(duration)


def _cap_use(item: dict) -> float | None:
    """装备级耗电（GJ/s）：引擎在装备作用域回 `-4 capPeakLoad`。"""
    value = item["attributes"].get(A.ATTR_CAP_PEAK_LOAD)
    return None if value is None else float(value)


# =============================================================================
# 电容本体 / 耗电排行 / 敏感度
# =============================================================================


def _capacitor(attrs: dict[int, float]) -> dict:
    """电容七个字段（`recharge_s` 已是秒：`55` 的毫秒由投影层换算）。

    `depletes_in_s` 走 `shared.depletes_in_s()` 归一（D6）：电容稳定时引擎给 `-7 = -1.0`
    这个**哨兵**，它不是秒数；稳定与否由 `stable_pct > 0` 表达，耗尽时长给 `None`。
    """
    stable = _number(attrs, A.ATTR_CAP_STABLE_PCT)
    return {
        "capacity": _fmt(_number(attrs, A.ATTR_CAPACITOR_CAPACITY)),
        "recharge_s": _fmt(_number(attrs, A.ATTR_CAPACITOR_RECHARGE_RATE)),
        "stable_pct": _fmt(stable),
        "depletes_in_s": shared.depletes_in_s(stable, _number(attrs, A.ATTR_CAP_DEPLETES_IN)),
        "peak_load": _fmt(_number(attrs, A.ATTR_CAP_PEAK_LOAD)),
        "peak_recharge": _fmt(_number(attrs, A.ATTR_CAP_PEAK_RECHARGE)),
        "delta": _fmt(_number(attrs, A.ATTR_CAP_DELTA_PEAK)),
        "delta_pct": _fmt(_number(attrs, A.ATTR_CAP_DELTA_PEAK_PCT)),
    }


def _cap_by_module(fitted: list[tuple[int, dict]]) -> list[dict]:
    """耗电排行：同型模块合并计数，按合计 GJ/s 降序取前 `MAX_CAP_BY_MODULE` 组。"""
    grouped: dict[object, dict] = {}
    for _position, item in fitted:
        use = _cap_use(item)
        if use is None or use <= 0.0:
            continue
        key = item.get("type_id") if isinstance(item.get("type_id"), int) else item.get("name")
        entry = grouped.setdefault(
            key,
            {
                "module": item.get("name"),
                "type_id": item.get("type_id"),
                "count": 0,
                "cap_use_gj_s": 0.0,
            },
        )
        entry["count"] += 1
        entry["cap_use_gj_s"] += use

    ranked = sorted(
        grouped.values(),
        key=lambda entry: (-entry["cap_use_gj_s"], entry.get("module") or ""),
    )[:MAX_CAP_BY_MODULE]
    for entry in ranked:
        entry["cap_use_gj_s"] = units.fmt(entry["cap_use_gj_s"])
    return ranked


def _cap_sensitivity(ctx: ViewContext, fitted: list[tuple[int, dict]], stable_now: float | None) -> list[dict]:
    """逐个摘掉耗电最高的 `MAX_CAP_SENSITIVITY` 个模块，报告关闭后的 `stable_pct`。

    用**原下标**摘除（见模块 docstring 的坑）；同一变体名带下标与 type_id，
    缓存 key 唯一且可复现。
    """
    ranked = sorted(
        ((position, item, _cap_use(item)) for position, item in fitted),
        key=lambda row: (-(row[2] if row[2] is not None else 0.0), row[1].get("name") or ""),
    )
    selected = [row for row in ranked if row[2] is not None and row[2] > 0.0][:MAX_CAP_SENSITIVITY]

    results: list[dict] = []
    for position, item, use in selected:
        calculation = ctx.calculate_variant(
            f"stability:sensitivity:{position}",
            _fit_without(ctx.fit, position),
        )
        after = ship_attributes(calculation)
        stable_after = _number(after, A.ATTR_CAP_STABLE_PCT)
        delta = None
        if stable_after is not None and stable_now is not None:
            delta = stable_after - stable_now
        results.append(
            {
                "module": item.get("name"),
                "type_id": item.get("type_id"),
                "cap_use_gj_s": _fmt(use),
                "stable_pct": _fmt(stable_after),
                # 同一口径：摘掉这个模块后若电容稳定，引擎仍给哨兵 -1.0 → None（D6）
                "depletes_in_s": shared.depletes_in_s(
                    stable_after, _number(after, A.ATTR_CAP_DEPLETES_IN)
                ),
                "stable_delta_pct": _fmt(delta),
            }
        )
    return results


# =============================================================================
# 外部依赖
# =============================================================================


def _external_dependency(ctx: ViewContext, fitted: list[tuple[int, dict]]) -> dict:
    """遥修 / 传电的结构判定（group 名）；没有索引时两个布尔都 `None`（未知）。"""
    if ctx.index is None:
        return {
            "remote_rep": None,
            "cap_transfer": None,
            "remote_rep_modules": [],
            "cap_transfer_modules": [],
        }

    remote: list[str] = []
    transfer: list[str] = []
    for _position, item in fitted:
        group = _group_name(ctx, item.get("type_id"))
        name = item.get("name") or f"type_id {item.get('type_id')}"
        if group in REMOTE_REPAIR_GROUPS:
            remote.append(name)
        elif group in CAP_TRANSFER_GROUPS:
            transfer.append(name)

    return {
        "remote_rep": bool(remote),
        "cap_transfer": bool(transfer),
        "remote_rep_modules": remote,
        "cap_transfer_modules": transfer,
    }


# =============================================================================
# 消耗品
# =============================================================================


def _ammo_endurance(ctx: ViewContext) -> dict:
    """单弹匣时长：**唯一实现**在 `views/shared.py`（T-20 / D5）。

    以前这里自己算一遍（`int(capacity / 弹药体积)` + 只看带 `64` 的「炮」），
    于是 f4/f5（导弹船）算出 `None`，而火力视角算出 172.663 / 250.323 —— 同一个事实
    两个视角不一致。现在两边都调 `shared.weapon_records()` + `shared.ammo_endurance_s()`：
    武器判定、弹匣发数（含 `chargeRate` 与浮点容差）、周期口径、取最短者全部同一份代码。

    回显口径不变：仍是**最先打空**的那一匣，并给出模块名 / 发数 / 周期，
    便于下游追「为什么是这个数」。
    """
    empty = {
        "ammo_endurance_s": None,
        "ammo_module": None,
        "ammo_rounds": None,
        "ammo_cycle_s": None,
    }
    best = shared.shortest_endurance(shared.weapon_records(ctx, BASE))
    if best is None:
        return empty
    return {
        "ammo_endurance_s": units.fmt(best["endurance_s"]),
        "ammo_module": best["name"] or f"type_id {best['type_id']}",
        "ammo_rounds": best["rounds"],
        "ammo_cycle_s": units.fmt(best["cycle_s"]),
    }


def _paste_units(ctx: ViewContext, items: list[dict]) -> float | None:
    """货舱里携带的纳米胶总数；没有索引 → `None`（物品组查不到）。"""
    if ctx.index is None:
        return None
    total = 0
    for item in items:
        if item.get("slot_type") != SLOT_CARGO:
            continue
        if _group_name(ctx, item.get("type_id")) != PASTE_GROUP:
            continue
        total += int(item.get("quantity") or 1)
    return float(total)


def _paste_consumer(ctx: ViewContext, fitted: list[tuple[int, dict]]) -> tuple[float | None, float | None] | None:
    """找**装填了纳米胶**的模块，返回 `(每循环消耗, 周期秒)`；没有则 `None`。

    只看弹药本身是不是纳米胶（按 group 名判定）——装填普通弹的武器不是纳米胶消费者，
    否则会把「带胶但没带修甲」的配装算出一条凭空的续航。
    """
    for _position, item in fitted:
        charge_type_id = item.get("charge_type_id")
        if not isinstance(charge_type_id, int):
            continue
        if _group_name(ctx, charge_type_id) != PASTE_GROUP:
            continue
        attributes = item["attributes"]
        rate = attributes.get(ATTR_CHARGE_RATE)
        cycle = _cycle_s(attributes)
        if rate is None or cycle is None:
            continue
        return float(rate), cycle
    return None


def _paste_endurance(ctx: ViewContext, fitted: list[tuple[int, dict]], items: list[dict]) -> dict:
    """纳米胶续航 = 携带量 ÷ 每循环消耗 × 周期（只数**货舱携带**，不含已装填的那一管）。"""
    result = {
        "paste_endurance_s": None,
        "paste_units": None,
        "paste_per_cycle": None,
    }
    units_carried = _paste_units(ctx, items)
    if units_carried is None:
        return result
    result["paste_units"] = units.fmt(units_carried)

    consumer = _paste_consumer(ctx, fitted)
    if consumer is None:
        return result
    rate, cycle = consumer
    result["paste_per_cycle"] = units.fmt(rate)
    if rate <= 0.0:
        return result
    result["paste_endurance_s"] = units.fmt(units_carried / rate * cycle)
    return result


def _carries_cap_booster(ctx: ViewContext, items: list[dict]) -> bool:
    """是否携带电容注射器（模块本体；`Cap Booster 400` 那种弹药不算）。

    有索引时按 `group == "Capacitor Booster"` 判；没有索引只能按名字兜底
    （名字里带 `Capacitor Booster`），并明确这是降级判定。
    """
    for item in items:
        group = _group_name(ctx, item.get("type_id"))
        if group == CAP_BOOSTER_GROUP:
            return True
        if group is None:
            name = item.get("name") or ""
            if CAP_BOOSTER_GROUP in name:
                return True
    return False


# =============================================================================
# 视图
# =============================================================================


@register("stability")
def build(ctx: ViewContext) -> dict:
    """不稳定视角：电容状态 / 耗电排行 / 关闭敏感度 / 外部依赖 / 消耗品。`flags` 恒为空。"""
    attrs = ctx.attrs(BASE)
    items = ctx.items(BASE)
    fitted = _fitted(items)

    capacitor = _capacitor(attrs)
    overview = {
        "capacitor": capacitor,
        "cap_by_module": _cap_by_module(fitted),
        "cap_sensitivity": _cap_sensitivity(ctx, fitted, capacitor["stable_pct"]),
        "external_dependency": _external_dependency(ctx, fitted),
        "consumables": {
            **_ammo_endurance(ctx),
            **_paste_endurance(ctx, fitted, items),
            "cap_booster": _carries_cap_booster(ctx, items),
        },
    }
    return {"view": "stability", "overview": overview, "flags": []}
