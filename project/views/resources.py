"""视角四 · 资源 `resources`（字段规格 §4）。

只输出事实与边界：CPU / PG / 标定的绝对余量、四类槽位（+ T3C 的子系统槽）、挂点、
无人机带宽与舱容、货舱占用、空槽数、同组更高 meta 候选能否装下。**不设绝对阈值、不给判词**
（红线：可疑点判定统一由 `project/diagnostics.py` 算一次，本视角 `flags` 恒为 `[]`）。

数值纪律（`views/base.py` 三条纪律 + 字段规格 §0.3）：

1. `ctx.attrs()` / `ctx.items()` 给的**已经是物理单位**，本模块不再做任何缩放；
2. **缺值不补 0**：属性缺失给 `None`，由渲染层标 `unknown`（红线 N4）；
3. 余量一律**绝对值**（不给百分比，主人明确要求）；
4. 浮点一律过 `units.fmt`（`round(x, 3)` 并吃掉浮点噪声），件数保持 `int`。

字段 → 来源属性（`FIELD_SOURCES`，渲染层据此标 provenance）：

- `cpu.output/load/free` ← 48 / 49 / **-9**；`powergrid` ← 11 / 15 / **-10**；
  `calibration` ← 1132 / 1152 / **-69**；
- `slots.*.total` ← 14 / 13 / 12 / 1137（**属性 ID**，样本值分别是 6 / 4 / 4 / 3）；
  **子系统槽（T3C）**：`slots.subsystem.total` ← **1367 `maxSubSystems`**（常量已登记在
  `config/attributes.py`，T-23；红线 N12），仅当船体给值且 > 0 时出现（Loki 5 / 4 已装）。
  **为什么是 1367 而不是 1366 `subSystemSlot`**：1366 是**子系统模块自身**的标记
  （在这些船上取不到值），1367 才是船体侧的槽位上限。实测（SDE build 3607705 /
  引擎 13.6.0）：Loki（T3C）= 5，Hecate / Drake / Griffin 引擎连键都不给。
  这是 `FIELD_SOURCES` 里唯一的**条件字段**（见 `CONDITIONAL_SOURCES`）；
- `hardpoints_free` ← 102 `turretSlotsLeft` / 101 `launcherSlotsLeft`；
- `drone.*.total` ← 1271 `droneBandwidth` / 283 `droneCapacity`；`cargo.total` ← 38 `capacity`。

**`feasible` / `over_budget` 是 T-22 新增的可行性事实（字段规格 §0.6）**：

- `feasible`：`cpu.free >= 0 and powergrid.free >= 0 and calibration.free >= 0` 的三项合判
  ——**任一余量为负即 `False`**（超预算），任一余量**缺失**则整体 `None`（未知不下结论，
  红线 N4）；
- `over_budget`：`{cpu, powergrid}` = 各自缺口 `max(0, -free)`；没超就是 `0.0`
  （缺值仍是 `None`）。只有 `feasible is False` 时才可能出现非零值；
- **只给事实与提示，不做求解**：本视角**不会**把模块自动降级成 offline 去凑预算
  （那是求解，不在本期），`cpu` / `powergrid` 的数值也仍是引擎给的理论值（按全 active 计）。
  「为什么超预算」的解释由 `diagnostics` 的 `resource_over_budget_unflyable` flag 承担
  （f4 实测：`cpu.load 10572.1` vs 真实可飞 `472.1`，差的就是 covert cloak 的 10000 罚值，
  而能免罚的子系统躺在 flag 177 舱里——见 `engine/normalize.py` 的舱内提示）。

**`upgrade_space` 本视角始终给完整候选清单**（事实归视角），overview 里只留摘要、
完整清单走 `render_detail(view="resources")` 是**渲染层的粒度决定**
（`render.OVERVIEW_DOWNGRADES`），不在这里删字段、也不改任何数值。

**已用量一律从 `ctx.items()` 统计**，不用「总量 − 剩余」反推：

- 槽位：按 `slot_type` 过滤（`"cargo"` 不是已装，见 `base.SLOT_CARGO`）；
- 无人机：逐架 `droneBandwidthUsed`（带宽）+ `volume`（舱容 m³）× 携带数量；
- 货舱：`slot_type == "cargo"` 的项按 `数量 × types.volume` 累加（体积来自
  `ctx.index`；**没有索引就给 `None`**，绝不猜）。

三个坑（实测）：

- `rigSlots`(1137) 与 `upgradeSlotsLeft`(1154) 是**两回事**：前者是槽位总量，后者是
  装配后剩余。混用会让 rig 的 `used` 永远错，所以 `used` 只从 items 数。
- 样本 Hecate 的 `droneBandwidth`/`droneCapacity` 都是 **0**（无无人机舱）——
  `0` 是**真值**，不是缺值，不能当 `unknown`。
- **子系统槽（T3C）是条件字段**（T-20 / X2）：`maxSubSystems`(1367) 只出现在 T3C
  船体上（Loki = 5，Hecate / Drake / Griffin 引擎连键都不给）。所以
  `slots.subsystem` / `empty_slots.subsystem` **只在船真有子系统槽时出现**，
  非 T3C 的输出必须与加这个字段之前**逐字节相同**（不许拿 `total = 0` 冒充四类齐全）。
"""

from __future__ import annotations

import math

from evefit.config import attributes as A
from evefit.config.attributes import ATTR_DRONE_BANDWIDTH_USED, ATTR_MAX_SUBSYSTEMS
from evefit.project import units
from evefit.project.views import register
from evefit.project.views.base import BASE, SLOT_CARGO, ViewContext, ship_attributes

# =============================================================================
# 槽位与上限
# =============================================================================

SLOT_SOURCES: dict[str, int] = {
    "high": A.ATTR_HI_SLOTS,
    "medium": A.ATTR_MED_SLOTS,
    "low": A.ATTR_LOW_SLOTS,
    "rig": A.ATTR_RIG_SLOTS,
}
"""槽位类别 → 总量属性 ID（14 / 13 / 12 / 1137）。顺序即输出顺序。"""

SUBSYSTEM_SLOT_TYPE: str = "subsystem"
"""T3C 子系统槽位的 `slot.type`（`ctx.items()` 里 4 个子系统模块的槽位类别）。"""

CONDITIONAL_SOURCES: frozenset[str] = frozenset(
    {
        "slots.subsystem.used",
        "slots.subsystem.total",
        "empty_slots.subsystem",
    }
)
"""**条件字段**：只有船体有子系统槽（T3C）时才出现在 overview 里。

渲染层按叶子查 `FIELD_SOURCES`，所以这三条必须登记（否则 T3C 的子系统槽位是
「无 provenance 的数值」）；但非 T3C 船上它们**没有对应叶子**，因此不能像其它字段
那样无条件断言「可达」。`tests/test_views_b.py` 据此三分：无条件字段必须可达、
条件字段在本样本必须**不可达**、并在 T3C 样本上必须可达（含 `used == 4`）。
"""

PENDING_ATTRIBUTES: dict[str, int] = {}
"""**待登记**属性白名单 —— 现在是**空的**（T-23：`maxSubSystems(1367)` 已迁进
`config/attributes.py`，红线 N12 达成）。

保留这个空表是因为 `tests/test_views_b.py` 的「来源必须是常量表里的常量」断言仍会读它
（`getattr(module, "PENDING_ATTRIBUTES", {})`）：将来再有「常量表来不及登记」的情形，
按下表约定登记在**本模块以 `ATTR_` 前缀声明的具名常量**上，裸写数字仍会被抓住。
"""

SLOT_DRONE_BAY = "drone_bay"
"""无人机舱的 `slot.type`（`ctx.items()` 里无人机的槽位类别）。"""

MAX_UPGRADE_CHECKS: int = 16
"""`upgrade_space` 的**总**重算预算（带 validate 一次 0.38 ms → 上限约 6 ms）。"""

UPGRADE_CANDIDATES_PER_MODULE: int = 2
"""每个已装模块最多验几个更高 meta 候选（取 meta 最接近的两个，避免 Abyssal 噪声）。"""

BLOCKING_HARD_RULE_TYPES: frozenset[str] = frozenset(
    {
        "wrong_slot",
        "slot_taken",
        "subsystem_taken",
        "rig_size",
        "ship_restricted",
        "capital_item",
        "max_group",
        "max_type",
        "charge_group",
        "charge_size",
    }
)
"""换装后出现这些**与技能无关的硬违规**，就说明这件候选根本装不上，不算候选（直接跳过）。

`capital_item` 实测有效：把 `Small Ancillary Armor Repairer` 换成
`Capital Ancillary Armor Repairer` 时引擎报 `{"type": "capital_item"}`。
"""

# =============================================================================
# 字段 → 属性 ID
# =============================================================================

FIELD_SOURCES: dict[str, int | None] = {
    # CPU / PG / 标定：输出、占用、余量（余量是引擎派生的负 ID，绝对值）
    "cpu.output": A.ATTR_CPU_OUTPUT,
    "cpu.load": A.ATTR_CPU_LOAD,
    "cpu.free": A.ATTR_CPU_FREE,
    "powergrid.output": A.ATTR_POWER_OUTPUT,
    "powergrid.load": A.ATTR_POWER_LOAD,
    "powergrid.free": A.ATTR_POWER_FREE,
    "calibration.output": A.ATTR_CALIBRATION_OUTPUT,
    "calibration.load": A.ATTR_CALIBRATION_LOAD,
    "calibration.free": A.ATTR_CALIBRATION_FREE,
    # 可行性（T-22）：`feasible` 是三个余量的合判（合成值），缺口是 `max(0, -free)` 的派生值
    "feasible": None,
    "over_budget.cpu": None,
    "over_budget.powergrid": None,
    # 槽位总量来自船体属性；`used` 是从 items 数出来的合成值 → None
    "slots.high.used": None,
    "slots.high.total": A.ATTR_HI_SLOTS,
    "slots.medium.used": None,
    "slots.medium.total": A.ATTR_MED_SLOTS,
    "slots.low.used": None,
    "slots.low.total": A.ATTR_LOW_SLOTS,
    "slots.rig.used": None,
    "slots.rig.total": A.ATTR_RIG_SLOTS,
    # 子系统槽位（T3C）：总量来自船体 1367 maxSubSystems，已用从 items 数
    # 只在船体真有子系统槽时出现（见 CONDITIONAL_SOURCES）
    "slots.subsystem.used": None,
    "slots.subsystem.total": ATTR_MAX_SUBSYSTEMS,
    # 挂点：102 / 101
    "hardpoints_free.turret": A.ATTR_TURRET_SLOTS_LEFT,
    "hardpoints_free.launcher": A.ATTR_LAUNCHER_SLOTS_LEFT,
    # 无人机：上限来自船体，占用是从 items 合成的
    "drone.bandwidth.used": None,
    "drone.bandwidth.total": A.ATTR_DRONE_BANDWIDTH,
    "drone.capacity.used": None,
    "drone.capacity.total": A.ATTR_DRONE_CAPACITY,
    # 货舱：38 只给容量，占用需按体积累加 → 合成值
    "cargo.used": None,
    "cargo.total": A.ATTR_CARGO_CAPACITY,
    # 空槽 = 总量 − 已用（由 slots 推导）
    "empty_slots.high": None,
    "empty_slots.medium": None,
    "empty_slots.low": None,
    "empty_slots.rig": None,
    "empty_slots.subsystem": None,
    # 升级空间：候选是否装得下由重算合成（`[]` 段表示数组元素字段）
    "upgrade_space[].slot": None,
    "upgrade_space[].module": None,
    "upgrade_space[].module_type_id": None,
    "upgrade_space[].candidate": None,
    "upgrade_space[].candidate_type_id": None,
    "upgrade_space[].meta_level_now": None,
    "upgrade_space[].meta_level": None,
    "upgrade_space[].cpu_free": A.ATTR_CPU_FREE,
    "upgrade_space[].power_free": A.ATTR_POWER_FREE,
    "upgrade_space[].calibration_free": A.ATTR_CALIBRATION_FREE,
    "upgrade_space[].fits": None,
}
"""overview 点路径 → 来源属性 ID（渲染层据此打 provenance）。

`upgrade_space[].slot` / `.module` / `.candidate` 是**字符串叶子**（槽位类别 / 当前模块名 /
候选名，来自 `ctx.items()` 与 SDE 索引），不是单一属性直读 → 登记 `None`（合成/enum 值，
渲染层判 `assumed`，红线 N4：不冒充 `measured`）。

数组一律用 `[]` 元素约定（`upgrade_space[].fits`）：这是六视角的**同一套口径**，
渲染层的容器回退（`render._tag()`）与此兼容。

`feasible` / `over_budget.*` 一律登记 `None`：它们是**合成值**（三项余量的合判 / 缺口
`max(0, -free)`），不是单一属性直读；登记 `None` 让渲染层判 `assumed`，
不冒充 `measured`（红线 N4）。
"""


# =============================================================================
# 小工具
# =============================================================================


def _number(values: dict[int, float], attribute_id: int | None) -> float | None:
    """取一个物理值；属性 ID 为 `None` 或缺失时返回 `None`（**不补 0**）。"""
    if attribute_id is None:
        return None
    return values.get(attribute_id)


def _fmt(value: float | None) -> float | None:
    """浮点去噪（`round(x, 3)`）；`None` 原样传递。"""
    return None if value is None else units.fmt(value)


def _count(value: float | None) -> int | None:
    """件数类属性（槽位/挂点天然是整数）→ `int`；缺失仍返回 `None`。"""
    return None if value is None else int(round(value))


def _fitted(items: list[dict]) -> list[tuple[int, dict]]:
    """已装模块（排除货舱），返回 `(在 fit["items"] 里的下标, 合并视图)`。

    下标与 `ctx.fit["items"]` 严格对应（引擎按序回 items，硬结论 44）。
    """
    return [
        (position, item)
        for position, item in enumerate(items)
        if item.get("slot_type") != SLOT_CARGO
    ]


def _group_name(ctx: ViewContext, type_id: object) -> str | None:
    """type_id → SDE group 名；没有索引或类型不存在时返回 `None`。"""
    if ctx.index is None or not isinstance(type_id, int):
        return None
    row = ctx.index.get_type(type_id)
    return (row or {}).get("group_name")


# =============================================================================
# 各段计算
# =============================================================================


def _slots(attrs: dict[int, float], fitted: list[tuple[int, dict]]) -> dict:
    """槽位 `{used, total}`；`used` 只从 items 数，不用「总量 − 剩余」反推。

    **子系统槽（T3C）是条件字段**：只有船体给出 `maxSubSystems(1367) = n > 0` 时才
    追加 `subsystem` 一类，`total = n`（Loki 实测 5）、`used` = 已装子系统数（实测 4）。
    非 T3C 船（Hecate / Drake / Griffin）引擎**不给** 1367 这个键 → 该类别整体不出现，
    输出与 T-20 之前逐字节相同（`tests/test_views_b.py` 有正反两向断言）。
    """
    slots: dict[str, dict] = {}
    for name, attribute_id in SLOT_SOURCES.items():
        used = sum(1 for _position, item in fitted if item.get("slot_type") == name)
        slots[name] = {"used": used, "total": _count(_number(attrs, attribute_id))}

    total = _count(_number(attrs, ATTR_MAX_SUBSYSTEMS))
    if total is not None and total > 0:
        used = sum(
            1 for _position, item in fitted if item.get("slot_type") == SUBSYSTEM_SLOT_TYPE
        )
        slots[SUBSYSTEM_SLOT_TYPE] = {"used": used, "total": total}
    return slots


def _empty_slots(slots: dict) -> dict:
    """空槽数 = 总量 − 已用；任一未知则 `None`，超装时夹到 0（不当负数输出）。"""
    empty: dict[str, int | None] = {}
    for name, slot in slots.items():
        total = slot["total"]
        empty[name] = None if total is None else max(total - slot["used"], 0)
    return empty


def _drone_used(drones: list[tuple[int, dict]], attribute_id: int) -> float | None:
    """无人机占用合计 = Σ(数量 × 逐架属性值)；没有无人机时是**真 0**。

    任何一架取不到该属性就整体 `None`（不做「部分相加」的假合计，红线 N4）。
    """
    if not drones:
        return units.fmt(0.0)
    total = 0.0
    for _position, item in drones:
        per_unit = item["attributes"].get(attribute_id)
        if per_unit is None:
            return None
        total += float(per_unit) * int(item.get("quantity") or 1)
    return units.fmt(total)


def _cargo_used(ctx: ViewContext, items: list[dict]) -> float | None:
    """货舱占用 = Σ(数量 × types.volume)；没有索引或体积缺失 → `None`。"""
    if ctx.index is None:
        return None
    total = 0.0
    for item in items:
        if item.get("slot_type") != SLOT_CARGO:
            continue
        type_id = item.get("type_id")
        if not isinstance(type_id, int):
            return None
        row = ctx.index.get_type(type_id)
        if row is None or row.get("volume") is None:
            return None
        total += float(row["volume"]) * int(item.get("quantity") or 1)
    return units.fmt(total)


def _feasibility(attrs: dict[int, float]) -> tuple[bool | None, dict[str, float | None]]:
    """资源可行性 `(feasible, over_budget)`（T-22 / 字段规格 §0.6）。

    判据是**自相矛盾**，不是数值阈值：CPU、PG、标定三项余量（`-9` / `-10` / `-69`）
    **任一为负**就说明「按全 active 算装不下」→ `feasible = False`。
    这不是求解：模块状态与占用**一个都不改**（降级 offline 是求解，不在本期）。

    - 任一余量缺失 → `feasible = None`（未知不给结论，红线 N4：`None >= 0` 不成立
      但也不等于「超预算」）；
    - `over_budget` 是各自的**缺口** `max(0, -free)`：没超是 `0.0`（真事实，不是缺值），
      余量本身缺失时对应项仍是 `None`；
    - `calibration` 只参与 `feasible` 判定，不生成缺口项——字段规格的 `over_budget`
      只要 `cpu` / `powergrid` 两项（标定是「够不够装 rig」的布尔约束，不是预算分配）。
    """
    cpu_free = _number(attrs, A.ATTR_CPU_FREE)
    power_free = _number(attrs, A.ATTR_POWER_FREE)
    calibration_free = _number(attrs, A.ATTR_CALIBRATION_FREE)

    over_budget: dict[str, float | None] = {
        "cpu": None if cpu_free is None else _fmt(max(0.0, -cpu_free)),
        "powergrid": None if power_free is None else _fmt(max(0.0, -power_free)),
    }

    if cpu_free is None or power_free is None or calibration_free is None:
        return None, over_budget
    feasible = cpu_free >= 0.0 and power_free >= 0.0 and calibration_free >= 0.0
    return feasible, over_budget


def _fit_with_swap(fit: dict, position: int, type_id: int) -> dict:
    """把 `fit["items"][position]` 换成另一个 type_id（不改入参，保留弹药与数量）。"""
    items = [dict(item) for item in fit.get("items") or []]
    if 0 <= position < len(items):
        items[position] = {**items[position], "type_id": int(type_id)}
    return {**fit, "items": items}


def _upgrade_plans(ctx: ViewContext, fitted: list[tuple[int, dict]]) -> list[dict]:
    """每个**同型模块**一组候选（同型只建一份计划：换装后的资源余量与它在哪个槽位无关）。"""
    if ctx.index is None:
        return []

    plans: list[dict] = []
    seen_types: set[int] = set()
    for position, item in fitted:
        type_id = item.get("type_id")
        if not isinstance(type_id, int) or type_id in seen_types:
            continue
        row = ctx.index.get_type(type_id)
        if row is None or row.get("group_id") is None:
            continue
        seen_types.add(type_id)
        meta_now = int(row.get("meta_level") or 0)
        volume_now = row.get("volume")

        candidates = []
        for candidate in ctx.index.types_in_group(int(row["group_id"])):
            if not candidate.get("published") or candidate["type_id"] == type_id:
                continue
            if int(candidate.get("meta_level") or 0) <= meta_now:
                continue
            volume = candidate.get("volume")
            # 同体积档才算同尺寸（模块体积就是尺寸档：Small 5 / Medium 10 / Large 50 / Capital 4000）
            if volume_now is not None and volume is not None and not math.isclose(
                float(volume), float(volume_now), rel_tol=1e-9
            ):
                continue
            candidates.append(candidate)

        # 取 meta 最接近的更高档（asc），避免先把 Abyssal（meta 10）这类极端件当成「升级」
        candidates.sort(
            key=lambda entry: (
                int(entry.get("meta_level") or 0),
                entry.get("name") or "",
                entry["type_id"],
            )
        )
        if not candidates:
            continue
        plans.append(
            {
                "position": position,
                "item": item,
                "type_id": type_id,
                "meta_now": meta_now,
                "candidates": candidates[:UPGRADE_CANDIDATES_PER_MODULE],
            }
        )
    return plans


def _upgrade_space(ctx: ViewContext, fitted: list[tuple[int, dict]]) -> list[dict]:
    """同组更高 meta 候选（每型取两个最近档），重算后报告资源余量与是否装得下。

    候选口径（三关全部过才算候选，不做任何数值阈值判断）：

    1. **同 group、更高 `meta_level`、`published=true`**；
    2. **同体积档**：`types.volume` 与当前模块一致（模块体积就是尺寸档：Small 5 / Medium 10 /
       Large 50 / Capital 4000 m³；不按名字前缀猜，实测 `Ancillary Armor Repairer` 组内
       小中大三种体积）；
    3. 换装后**不出现与技能无关的硬违规**（如 `capital_item`）——出现就说明装不上，跳过。

    候选**按轮取**（每型先取第 1 个，还有预算再取第 2 个），避免预算被前两个槽位吃光；
    同型模块只验一次（资源占用是逐模块相加的，与具体槽位无关）。

    「装得下」= 引擎在该变体上**没有 `resource` 类违规**（CPU / PG / 标定的 used ≤ available
    由引擎自己判，不是我方阈值）；三个余量值一并回显便于溯源。

    没有索引库时返回 `[]`（字段规格 §4 允许：候选来源必须是自建索引，不能伪造）。
    """
    if ctx.index is None:
        return []

    plans = _upgrade_plans(ctx, fitted)
    results: list[dict] = []
    checks = 0
    for round_index in range(UPGRADE_CANDIDATES_PER_MODULE):
        for plan in plans:
            candidates = plan["candidates"]
            if round_index >= len(candidates):
                continue
            if checks >= MAX_UPGRADE_CHECKS:
                return results
            checks += 1

            item = plan["item"]
            candidate = candidates[round_index]
            # 换装判定要同时看属性与违规：`Engine.calculate(..., validate=True)` 一次给两边
            # （视角里允许直接走 `ctx.engine.calculate`，见 base.py 的 `calculate_variant` 说明）
            calculation = ctx.engine.calculate(
                _fit_with_swap(ctx.fit, plan["position"], int(candidate["type_id"])),
                validate=True,
            )
            rules = [
                violation.get("rule") or {}
                for violation in (calculation.get("violations") or [])
                if isinstance(violation, dict)
            ]
            if any(rule.get("type") in BLOCKING_HARD_RULE_TYPES for rule in rules):
                continue  # 尺寸/槽位就不对，不是候选

            after = ship_attributes(calculation)
            results.append(
                {
                    "slot": item.get("slot_type"),
                    "module": item.get("name"),
                    "module_type_id": plan["type_id"],
                    "candidate": candidate.get("name"),
                    "candidate_type_id": int(candidate["type_id"]),
                    "meta_level_now": plan["meta_now"],
                    "meta_level": int(candidate.get("meta_level") or 0),
                    "cpu_free": _fmt(after.get(A.ATTR_CPU_FREE)),
                    "power_free": _fmt(after.get(A.ATTR_POWER_FREE)),
                    "calibration_free": _fmt(after.get(A.ATTR_CALIBRATION_FREE)),
                    # 只看有没有资源类违规：有 = 装不下；没有 = 装得下（引擎判定）
                    "fits": not any(rule.get("type") == "resource" for rule in rules),
                }
            )
    return results


# =============================================================================
# 视图
# =============================================================================


@register("resources")
def build(ctx: ViewContext) -> dict:
    """资源视角：余量 / 可行性 / 槽位 / 挂点 / 无人机 / 货舱 / 升级空间。`flags` 恒为空。"""
    attrs = ctx.attrs(BASE)
    items = ctx.items(BASE)
    fitted = _fitted(items)

    slots = _slots(attrs, fitted)
    drones = [(position, item) for position, item in fitted if item.get("slot_type") == SLOT_DRONE_BAY]
    feasible, over_budget = _feasibility(attrs)

    overview = {
        "cpu": {
            "output": _fmt(_number(attrs, A.ATTR_CPU_OUTPUT)),
            "load": _fmt(_number(attrs, A.ATTR_CPU_LOAD)),
            "free": _fmt(_number(attrs, A.ATTR_CPU_FREE)),
        },
        "powergrid": {
            "output": _fmt(_number(attrs, A.ATTR_POWER_OUTPUT)),
            "load": _fmt(_number(attrs, A.ATTR_POWER_LOAD)),
            "free": _fmt(_number(attrs, A.ATTR_POWER_FREE)),
        },
        "calibration": {
            "output": _fmt(_number(attrs, A.ATTR_CALIBRATION_OUTPUT)),
            "load": _fmt(_number(attrs, A.ATTR_CALIBRATION_LOAD)),
            "free": _fmt(_number(attrs, A.ATTR_CALIBRATION_FREE)),
        },
        # 可行性事实（T-22，§0.6）：不降级模块、不改上面的数值，只把矛盾显式化
        "feasible": feasible,
        "over_budget": over_budget,
        "slots": slots,
        "hardpoints_free": {
            "turret": _count(_number(attrs, A.ATTR_TURRET_SLOTS_LEFT)),
            "launcher": _count(_number(attrs, A.ATTR_LAUNCHER_SLOTS_LEFT)),
        },
        "drone": {
            "bandwidth": {
                "used": _drone_used(drones, ATTR_DRONE_BANDWIDTH_USED),
                "total": _fmt(_number(attrs, A.ATTR_DRONE_BANDWIDTH)),
            },
            "capacity": {
                "used": _drone_used(drones, A.ATTR_VOLUME),
                "total": _fmt(_number(attrs, A.ATTR_DRONE_CAPACITY)),
            },
        },
        "cargo": {
            "used": _cargo_used(ctx, items),
            "total": _fmt(_number(attrs, A.ATTR_CARGO_CAPACITY)),
        },
        "empty_slots": _empty_slots(slots),
        "upgrade_space": _upgrade_space(ctx, fitted),
    }
    return {"view": "resources", "overview": overview, "flags": []}
