"""可疑点规则引擎（L3）：**只做结构性事实检查，不设绝对数值阈值**。

主人对这个模块的要求只有一句（PROGRESS §7 决策日志 / 软件设计 §7.7）：

> 可疑点**不设绝对阈值**——快慢取决于同一场战斗里的其他人，属场景相对判断。

所以本模块的判定只有三种形状：

1. **「是否有」**：是否为空（无推进模块、无无人机）、是否为负（余量缺口）、是否不稳定；
2. **「相对倍数」**：信号半径开关比、抗性最低/最高比、dph/dps 与武器周期比、余量与总量比；
3. **「是否自相矛盾」**（T-22 / 字段规格 §0.6，输入保真）：有弹药却零伤害、按全 active
   算装不下却仍全额计费——这类矛盾永远意味着**输入或状态有问题**，与模块类型无关，所以
   判据写成矛盾而不是「检测 Abyssal/mutated 类型」（后者会随版本增加而失效）：

   - `weapon_damage_zero`：有带弹药的武器，但 `-11` 齐射伤害为 0 → 对应 `dph = null`；
   - `resource_over_budget_unflyable`：CPU/PG/标定任一余量为负 → 对应 `feasible = false`。

   这两条**只给事实与提示**，不做求解（不会自动把模块降级成 offline 去凑预算）。

另有 `fighters_in_bay_only`（T-29）：机库里有载机、但 `-62` 发射管占用为 0 —— 它属于第 1 类
「是否有」（有货但没起飞），**不是**矛盾也不是缺陷：`dps.fighter = 0.0` 在这个场景就是
正确值（载机停在机库不参战，T-28 改造实验已证）。它存在的唯一目的，是让下游知道
「`weapons[]` 里没有战斗机、`dps.fighter` 是 0」**并非**数据缺失。

还有 `propulsion_state_conflict`（T-35 / T-38 改名）：推进模块触发了引擎 `max_group … limit=active`
硬违规（f67 是历史上第一条真实存在的双推进配置）。它的形状是**第 1 类 + 第 2 类的组合**：
「是否有」状态互斥（同组只能启用其一）+ 「相对」事实回显（被叠加出来的速度是多少）。
**它不重算、不降级、不挑模块**：数值全部保持引擎原样，只把「这些数基于叠加」说出口——
否则下游 agent 会把 f67 的 `59,928.363 m/s` 当成有效数据（驱逐舰不可能有这个速度）。

**口径（T-38 主人裁决）**：双推进一起带是**很常见的事**，只是确实**不能同时启用**——
所以这不是「配装有问题」，而是**状态冲突**：id 与文案都不许出现判词，只说「同一时刻只能启用其一」
与「这组数基于多个推进同时启用的非真实状态」。
同类历史缺陷：X1（Loki covert cloak 让 CPU 占用虚高）修在资源视角，这次暴露在机动视角
——**根因都是「状态语义缺失」，不是数值读错**。

倍数常量集中在本文件顶部，全部注明「相对量，非绝对门槛」，并且**只用于判断"明显"**，
不用于给出「够快 / 够硬 / 够用」这类判词。规则开关与文案在
`evefit/config/diagnostics.json`（只存 id / view / enabled / message，判定逻辑全在这里）。

每条命中输出：

```python
{"id": "...", "view": "...", "message": "...", "evidence": {...}}
```

`evidence` 一律带具体数值，且都是**可从 `ViewContext` 复算溯源**的值（属性 ID 见注释），
`message` 只是把 evidence 里的关键项串成一句话。

为什么不用 YAML：标准库没有 YAML，引入 PyYAML 属于新增依赖（开发规范 §10 要登记）。
规则表用 JSON，标准库 `json` 直接读。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Callable

from evefit.config import attributes as _a
from evefit.config import paths as _paths
from evefit.project import units
from evefit.project.views import shared as _shared
from evefit.project.views.base import SLOT_CARGO, ViewContext, ship_attributes

RULES_PATH: Path = _paths.PROJECT_ROOT / "config" / "diagnostics.json"
"""规则表路径（走 `config/paths.py`，不写死绝对路径）。"""

# ---- 相对倍数常量（**都不是绝对数值门槛**，只回答「是否明显」）----

SLACK_FREE_FRACTION: float = 0.5
"""`resource_slack`：余量相对**自身总量**的比例。CPU 与 PG 余量都超过各自总量的一半
才算「余量大」（0.5 是比例，不是 CPU/PG 的绝对数值）。"""

SIG_INCREASE_RATIO: float = 1.25
"""`mwd_sig_increase`：推进模块启用时信号半径相对关闭时的**倍数**下限。"""

RESIST_HOLE_RATIO: float = 1.2
"""`resist_hole`：同一层内最弱系与最强系的**共振率比值**下限（等效 EHP 差 20%）。"""

DPH_DPS_FACTOR: float = 1.5
"""`alpha_dph_ratio`：`dph/dps` 允许偏离主要武器周期的**倍数**区间（1.5 倍以内视为一致）。"""

_OPEN_STATES: frozenset[str] = frozenset({"active", "overload"})
"""算「启用」的 state 集合（overload 也是启用，只是带过载）。"""


# =============================================================================
# 输入保真（T-22 / 字段规格 §0.6）：判据是**自相矛盾**，不是模块类型穷举
# =============================================================================


def _armed_weapon_parts(facts: _Facts) -> dict | None:
    """扫描**带弹药的挂架武器**，返回 `{armed, volley_sum, names}`。

    与 `views/firepower._dph()` 用**同一个记录源**（`views/shared.weapon_records()`）：
    视图据此把 `dph` 判成 `null`，本模块据此发 `weapon_damage_zero` flag，
    两边的「有带弹药的武器」判据不会分叉（`armed = charge_type_id 有值`，不看类型）。
    """
    records = _shared.weapon_records(facts.ctx)
    armed = [
        record
        for record in records
        if record.get("slot_type") in _shared.WEAPON_SLOT_TYPES
        and record.get("charge_type_id") is not None
    ]
    if not armed:
        return None
    return {
        "armed": len(armed),
        "volley_sum": sum(
            record["volley"]
            for record in records
            if record.get("slot_type") in _shared.WEAPON_SLOT_TYPES
            and record.get("volley")
        ),
        "names": [record.get("name") or f"type_id {record.get('type_id')}" for record in armed],
    }


# =============================================================================
# 规则表
# =============================================================================


@dataclass(frozen=True, slots=True)
class Rule:
    """一条规则：`id` 用来索引判定逻辑，`message` 是文案模板。"""

    id: str
    view: str
    enabled: bool
    message: str


@lru_cache(maxsize=8)
def _load_rules_cached(path_text: str) -> tuple[Rule, ...]:
    data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("rules"), list):
        raise ValueError(f"{path_text}：顶层必须是 {{\"rules\": [...]}}")

    rules: list[Rule] = []
    seen: set[str] = set()
    for raw in data["rules"]:
        if not isinstance(raw, dict):
            raise ValueError(f"{path_text}：规则项必须是对象，实际是 {type(raw).__name__}")
        rule_id = raw.get("id")
        if not isinstance(rule_id, str) or not rule_id:
            raise ValueError(f"{path_text}：规则缺少非空 id：{raw!r}")
        if rule_id in seen:
            raise ValueError(f"{path_text}：重复的规则 id {rule_id!r}")
        seen.add(rule_id)

        view = raw.get("view")
        message = raw.get("message")
        enabled = raw.get("enabled", True)
        if not isinstance(view, str) or not view:
            raise ValueError(f"{path_text}：规则 {rule_id!r} 缺少 view")
        if not isinstance(message, str) or not message:
            raise ValueError(f"{path_text}：规则 {rule_id!r} 缺少 message")
        if not isinstance(enabled, bool):
            raise ValueError(f"{path_text}：规则 {rule_id!r} 的 enabled 必须是布尔值")
        rules.append(Rule(id=rule_id, view=view, enabled=enabled, message=message))

    unknown = sorted({rule.id for rule in rules} - set(_HANDLERS))
    if unknown:
        raise ValueError(f"{path_text}：这些规则 id 没有实现：{unknown}；已实现：{sorted(_HANDLERS)}")
    return tuple(rules)


def load_rules(path: Path | str | None = None) -> tuple[Rule, ...]:
    """读取规则表（默认 `config/diagnostics.json`）。

    Raises:
        ValueError: 结构不合法、id 重复、或 id 在 `diagnostics.py` 里没有实现
            （配置与代码不同步时必须炸，不能静默跳过）。
    """
    target = Path(path) if path is not None else RULES_PATH
    return _load_rules_cached(str(target))


def implemented_rules() -> tuple[str, ...]:
    """`diagnostics.py` 里真正实现了的规则 id（供测试与配置对拍）。"""
    return tuple(_HANDLERS)


# =============================================================================
# 事实收集（一次 evaluate 只取一次 base 属性/物品）
# =============================================================================


def _num(values: dict[int, float], attribute_id: int) -> float | None:
    """取一个属性值；缺失返回 `None`（**不补 0**，红线 N4）。"""
    return values.get(attribute_id)


def _as_count(value: float | None, default: int = 0) -> int:
    """数值 → 整数计数（槽位/挂点这类量天然是整数）。"""
    return default if value is None else int(round(value))


def _key(tag: str) -> str:
    """临时变体的缓存 key（加前缀，避免与预置变体名相撞）。"""
    return f"rule:{tag}"


class _Facts:
    """一次 `evaluate()` 内共享的事实快照（惰性取，避免重复投影）。"""

    __slots__ = ("ctx", "_attrs", "_items")

    def __init__(self, ctx: ViewContext) -> None:
        self.ctx = ctx
        self._attrs: dict[int, float] | None = None
        self._items: list[dict] | None = None

    @property
    def attrs(self) -> dict[int, float]:
        if self._attrs is None:
            self._attrs = self.ctx.attrs("base")
        return self._attrs

    @property
    def items(self) -> list[dict]:
        if self._items is None:
            self._items = self.ctx.items("base")
        return self._items

    @property
    def index_available(self) -> bool:
        """能不能查 group 名。`None` 表示查询能力缺失（此时依赖 group 的规则一律跳过）。"""
        return self.ctx.index is not None

    def group_name(self, type_id: int | None) -> str | None:
        if not self.index_available or not isinstance(type_id, int):
            return None
        row = self.ctx.index.get_type(type_id)  # type: ignore[union-attr]
        return (row or {}).get("group_name")

    def fitted(self) -> list[tuple[int, dict]]:
        """已装模块（排除货舱），返回 `(在 fit["items"] 里的下标, 合并视图)`。

        下标与 `ctx.fit["items"]` 严格对应（引擎按序回 items，实测 31/31）。
        """
        return [
            (position, item)
            for position, item in enumerate(self.items)
            if item.get("slot_type") != SLOT_CARGO
        ]

    def slot_usage(self) -> dict[str, dict[str, int]]:
        """各槽位 `{used, total, empty}`（总量来自 14/13/12/1137 四个属性）。"""
        totals = {
            "high": _a.ATTR_HI_SLOTS,
            "medium": _a.ATTR_MED_SLOTS,
            "low": _a.ATTR_LOW_SLOTS,
            "rig": _a.ATTR_RIG_SLOTS,
        }
        usage: dict[str, dict[str, int]] = {}
        for slot_name, attribute_id in totals.items():
            total = _as_count(_num(self.attrs, attribute_id))
            used = sum(1 for _pos, item in self.fitted() if item.get("slot_type") == slot_name)
            usage[slot_name] = {"used": used, "total": total, "empty": max(total - used, 0)}
        return usage

    def drones_carried(self) -> int:
        """携带的无人机数量（槽位类别 `drone_bay` 的数量合计）。"""
        return sum(
            int(item.get("quantity") or 1)
            for _pos, item in self.fitted()
            if item.get("slot_type") == "drone_bay"
        )

    def propulsions(self) -> list[tuple[int, dict]]:
        """推进模块（group 名 `Propulsion Module`，来自索引库；没有索引则空列表）。"""
        return [
            (position, item)
            for position, item in self.fitted()
            if self.group_name(item.get("type_id")) == PROPULSION_GROUP
        ]

    def cap_stable(self) -> bool | None:
        """电容是否稳定：`capStablePct`（-72）> 0 为稳定；属性缺失返回 `None`。"""
        pct_value = _num(self.attrs, _a.ATTR_CAP_STABLE_PCT)
        if pct_value is None:
            return None
        return pct_value > 0.0

    def fit_without(self, drop: set[int]) -> dict:
        """把指定下标的模块从 fit 里摘掉（不改入参）。"""
        items = [
            item for position, item in enumerate(self.ctx.fit.get("items") or []) if position not in drop
        ]
        return {**self.ctx.fit, "items": items}


PROPULSION_GROUP = _shared.PROPULSION_GROUP
"""推进模块的 SDE group 名（实测：5MN MWD / 1MN AB 的 group 都是这个名字）。

**同一个常量只在 `views/shared.py` 定义一次**（T-35）：机动视角要把「推进模块关闭」
单独列一档，两边必须用同一套 group 判据，否则「哪些模块算推进」会分叉。
"""


def _usage_summary(usage: dict[str, dict[str, int]]) -> tuple[int, str]:
    """`(空槽总数, "high 1, low 2, rig 0" 这样的摘要)`。"""
    total_empty = sum(slot["empty"] for slot in usage.values())
    summary = ", ".join(f"{name} {slot['empty']}" for name, slot in usage.items())
    return total_empty, summary


# =============================================================================
# 各条规则的判定（全部返回 evidence dict；未命中返回 None）
# =============================================================================


def _rule_resource_overflow(facts: _Facts) -> dict | None:
    """CPU 或 PG 余量为负 → 报告具体缺口。属性：-9 cpuFree / -10 powerFree。"""
    cpu_free = _num(facts.attrs, _a.ATTR_CPU_FREE)
    power_free = _num(facts.attrs, _a.ATTR_POWER_FREE)
    if cpu_free is None or power_free is None:
        return None
    if cpu_free >= 0.0 and power_free >= 0.0:
        return None

    return {
        "cpu_output": units.fmt(_num(facts.attrs, _a.ATTR_CPU_OUTPUT) or 0.0),
        "cpu_load": units.fmt(_num(facts.attrs, _a.ATTR_CPU_LOAD) or 0.0),
        "cpu_free": units.fmt(cpu_free),
        "cpu_deficit": units.fmt(max(0.0, -cpu_free)),
        "power_output": units.fmt(_num(facts.attrs, _a.ATTR_POWER_OUTPUT) or 0.0),
        "power_load": units.fmt(_num(facts.attrs, _a.ATTR_POWER_LOAD) or 0.0),
        "power_free": units.fmt(power_free),
        "power_deficit": units.fmt(max(0.0, -power_free)),
    }


def _rule_resource_over_budget_unflyable(facts: _Facts) -> dict | None:
    """资源超预算 → 当前占用**按全 active 计算**，部分模块在真实游戏中不可能在线。

    T-22 / 字段规格 §0.6 的第二类不完整输入（**状态未知**）。判据仍是自相矛盾：
    三项余量（`-9` CPU / `-10` PG / `-69` 标定）**任一为负**就说明「按全 active 装不下」。
    本规则**只给事实与提示**，不判定该关哪个模块（降级是求解，不在本期）。

    实测背景（f4 / Loki）：`cpu.load 10572.1` vs 真实可飞 **472.1**；差的是 covert cloak
    的 10000 罚值，而唯一能免罚的子系统躺在 flag 177 舱里（见 `engine/normalize.py`
    的舱内提示）。数字没读错，是**「状态没落地」**。
    """
    cpu_free = _num(facts.attrs, _a.ATTR_CPU_FREE)
    power_free = _num(facts.attrs, _a.ATTR_POWER_FREE)
    calibration_free = _num(facts.attrs, _a.ATTR_CALIBRATION_FREE)
    if cpu_free is None or power_free is None or calibration_free is None:
        return None  # 任一余量未知 → 不下「飞不起来」的结论（N4）

    over_budget = {
        "cpu": max(0.0, -cpu_free),
        "powergrid": max(0.0, -power_free),
        "calibration": max(0.0, -calibration_free),
    }
    if not any(value > 0.0 for value in over_budget.values()):
        return None

    open_modules = sum(
        1 for _pos, item in facts.fitted() if item.get("state") in _OPEN_STATES
    )
    return {
        "cpu_output": units.fmt(_num(facts.attrs, _a.ATTR_CPU_OUTPUT) or 0.0),
        "cpu_load": units.fmt(_num(facts.attrs, _a.ATTR_CPU_LOAD) or 0.0),
        "cpu_free": units.fmt(cpu_free),
        "cpu_deficit": units.fmt(over_budget["cpu"]),
        "power_output": units.fmt(_num(facts.attrs, _a.ATTR_POWER_OUTPUT) or 0.0),
        "power_load": units.fmt(_num(facts.attrs, _a.ATTR_POWER_LOAD) or 0.0),
        "power_free": units.fmt(power_free),
        "power_deficit": units.fmt(over_budget["powergrid"]),
        "calibration_free": units.fmt(calibration_free),
        "calibration_deficit": units.fmt(over_budget["calibration"]),
        # 「按全 active 算」的直接证据：多少件模块处于开启态（state 由导入规则推定）
        "open_state_modules": open_modules,
    }


def _rule_weapon_damage_zero(facts: _Facts) -> dict | None:
    """有带弹药的武器，但齐射伤害（`-11`）为 0 → 输入不完整（突变数据缺失）。

    T-22 / 字段规格 §0.6 的第一类不完整输入（**突变数据缺失**）。killmail **不暴露**
    Abyssal/mutated 的 `mutation`，引擎只能按「未变异」算：f4 的 Abyssal BCS（type 49738）
    把 5 门 HML 的逐件 `-21`/`-12` 与舰船级 `-11` **一起压成 0**（实测摘掉该件后
    `-11` 从 0 → 1427.588）。

    判据是**自相矛盾**（有弹药却零伤害），不是「检测 Abyssal 类型」：后者会随版本失效，
    前者永远意味着输入或状态有问题。对应视图行为：`firepower.dph` 输出 `null`。
    """
    parts = _armed_weapon_parts(facts)
    if parts is None:
        return None
    dph = _num(facts.attrs, _a.ATTR_DAMAGE_ALPHA)
    if dph is None or dph > 0.0:
        return None

    return {
        "armed_weapons": parts["armed"],
        "volley_sum_hp": units.fmt(parts["volley_sum"]),
        "dph_hp": units.fmt(dph),
        "dps_hp_s": units.fmt(_num(facts.attrs, _a.ATTR_DAMAGE_DPS) or 0.0),
        "weapons": parts["names"][:3],
    }


def _rule_fighters_in_bay_only(facts: _Facts) -> dict | None:
    """机库里有载机、但发射管占用（`-62`）为 0 → 载机没参战，其伤害未计入。

    **两条判据都与视图同源**（`views/shared.fighter_bay_summary()`，机库口径只有一份）：

    - 机库（`fighter_bay` / ESI flag 158）里确实有载机；
    - `-62 fighterTubesUsed == 0`（没有中队在发射管里）。

    **这不是缺陷，是事实提示**：T-28 的改造实验证明载机停在机库时 `-58/-59..-62` 全 0 是
    正确行为（A/B 两组唯一的差别就是 `slot.type`；把 Templar 移进 `fighter_tube` 后
    `-58` 从 0 → 240.846）。所以本规则**不同于** `weapon_damage_zero`（那是输入自相矛盾）：
    它描述「有货但没起飞」，视图侧的 `dps.fighter = 0.0` 是**正确的 0**，不得改成 `null`
    或报错——那会丢掉「确定没有载机在飞」这个事实。

    另一条独立原因（T-28）：支援战斗机（如 `Siren I`）进管后只记 `-61` 支援槽占用，
    仍不出 `-58`。那种情况 `-62` 已经不是 0，本规则**不**命中也**不**判缺陷。
    """
    bay = _shared.fighter_bay_summary(facts.ctx)
    if bay["count"] <= 0:
        return None

    tubes_used = _num(facts.attrs, _a.ATTR_FIGHTER_TUBES_USED)
    if tubes_used is None or tubes_used > 0.0:
        # 未知 → 不下「未发射」的结论（N4）；>0 → 已有中队在管里，无需提示。
        return None

    fighter_dps = _num(facts.attrs, _a.ATTR_FIGHTER_DPS)
    return {
        "fighter_bay_count": bay["count"],
        "fighter_tubes_used": _as_count(tubes_used),
        "fighter_bay_types": bay["types"],
        "capacity_used_m3": bay["capacity_used_m3"],
        # `-58` 缺失时给 None，不拿 0 冒充（这是事实提示，不是数值证据）。
        "fighter_dps_hp_s": None if fighter_dps is None else units.fmt(fighter_dps),
    }


def _rule_resource_slack(facts: _Facts) -> dict | None:
    """CPU 与 PG 余量都超过各自总量的一半，且存在空槽位。"""
    cpu_output = _num(facts.attrs, _a.ATTR_CPU_OUTPUT)
    power_output = _num(facts.attrs, _a.ATTR_POWER_OUTPUT)
    cpu_free = _num(facts.attrs, _a.ATTR_CPU_FREE)
    power_free = _num(facts.attrs, _a.ATTR_POWER_FREE)
    if any(value is None for value in (cpu_output, power_output, cpu_free, power_free)):
        return None
    if cpu_free <= SLACK_FREE_FRACTION * cpu_output:
        return None
    if power_free <= SLACK_FREE_FRACTION * power_output:
        return None

    empty_total, empty_summary = _usage_summary(facts.slot_usage())
    if empty_total <= 0:
        return None

    return {
        "cpu_output": units.fmt(cpu_output),
        "cpu_free": units.fmt(cpu_free),
        "power_output": units.fmt(power_output),
        "power_free": units.fmt(power_free),
        "empty_slots": empty_total,
        "empty_slots_by_slot": empty_summary,
        "free_fraction": SLACK_FREE_FRACTION,
    }


def _rule_empty_hardpoint(facts: _Facts) -> dict | None:
    """炮台/发射器挂点有余，但高槽有空位。属性：102 turretSlotsLeft / 101 launcherSlotsLeft。"""
    turret_free = _num(facts.attrs, _a.ATTR_TURRET_SLOTS_LEFT)
    launcher_free = _num(facts.attrs, _a.ATTR_LAUNCHER_SLOTS_LEFT)
    if turret_free is None and launcher_free is None:
        return None
    turret = _as_count(turret_free)
    launcher = _as_count(launcher_free)
    if turret <= 0 and launcher <= 0:
        return None

    high = facts.slot_usage()["high"]
    if high["empty"] <= 0:
        return None

    return {
        "turret_free": turret,
        "launcher_free": launcher,
        "high_used": high["used"],
        "high_total": high["total"],
        "high_empty": high["empty"],
    }


def _rule_drone_bandwidth_idle(facts: _Facts) -> dict | None:
    """带宽有余量但未携带无人机。属性：1271 droneBandwidth / 283 droneCapacity。"""
    bandwidth = _num(facts.attrs, _a.ATTR_DRONE_BANDWIDTH)
    if bandwidth is None or bandwidth <= 0.0:
        return None
    carried = facts.drones_carried()
    if carried > 0:
        return None

    return {
        "bandwidth_total": units.fmt(bandwidth),
        "drones_carried": carried,
        "bandwidth_used": units.fmt(0.0),
        "capacity_total": units.fmt(_num(facts.attrs, _a.ATTR_DRONE_CAPACITY) or 0.0),
    }


def _rule_no_propulsion(facts: _Facts) -> dict | None:
    """没有任何推进模块（无脱离能力）。"""
    if not facts.index_available:
        # 没有索引就查不了 group 名 → 无法判定，**不猜**（N4）。
        return None
    if facts.propulsions():
        return None
    fitted = facts.fitted()
    return {
        "fitted_modules": len(fitted),
        "propulsion_modules": 0,
        "groups_looked_up": len(fitted),
    }


def _rule_mwd_sig_increase(facts: _Facts) -> dict | None:
    """推进模块启用时信号半径显著大于关闭时（相对倍数，不给绝对值门槛）。

    关闭态 = **只把推进模块置 offline、其余模块保持当前状态**（T-35 起口径只有一份：
    `views/shared.py::propulsion_off_attrs()`，与机动视角的
    `propulsion_states.propulsion_off` 是**同一次变体计算**）。

    历史缺陷（T-35 / f67）：这里以前用「把推进模块从 fit 里摘掉」自己造变体，而机动视角的
    `propulsion_states.off` 是**全模块离线** —— 于是同一视角里出现两个「关闭时信号半径」：
    文案写 `65.72 m`（摘推进后，仍含 `Small Shield Extender II` 的 5.72 m 罚值），
    `off.sig_m` 写 `60.0 m`（全离线，船体裸值）。两个数各自都对，但读者无从判断该信哪个。
    现在「推进关闭」有独立字段，本规则的数字与 `propulsion_off.*` 逐位相同
    （`tests/test_views_a.py` 钉住「文案里的数字 == propulsion_states 对应字段」）。
    """
    if not facts.index_available:
        return None
    propulsions = facts.propulsions()
    if not propulsions:
        return None

    states = {item.get("state") for _pos, item in propulsions}
    if not states & _OPEN_STATES:
        return None

    sig_on = _num(facts.attrs, _a.ATTR_SIGNATURE_RADIUS)
    if sig_on is None or sig_on <= 0.0:
        return None

    attrs_off = _shared.propulsion_off_attrs(facts.ctx)
    if attrs_off is None:
        return None
    sig_off = _num(attrs_off, _a.ATTR_SIGNATURE_RADIUS)
    if sig_off is None or sig_off <= 0.0:
        return None

    ratio = sig_on / sig_off
    if ratio < SIG_INCREASE_RATIO:
        return None

    return {
        "signature_radius_m": units.fmt(sig_on),
        "signature_radius_off_m": units.fmt(sig_off),
        "sig_ratio": units.fmt(ratio),
        "ratio_floor": SIG_INCREASE_RATIO,
        "propulsion_state": ",".join(sorted(states)),
        "propulsion": [item.get("name") or item.get("type_id") for _pos, item in propulsions],
    }


def _propulsion_label(name: str) -> str:
    """推进模块名 → 短标签（`AB` / `MWD`）；认不出的**原样返回名字**（不猜）。"""
    if "Afterburner" in name:
        return "AB"
    if "Microwarpdrive" in name:
        return "MWD"
    return name


def _conflict_text(names: list[str]) -> str:
    """多个推进模块 → 「AB 与 MWD」这种并列说法（>2 个用顿号，顺序同装配顺序）。"""
    labels = [_propulsion_label(name) for name in names]
    return " 与 ".join(labels) if len(labels) == 2 else "、".join(labels)


def _propulsion_active_limit_violation(facts: _Facts) -> dict | None:
    """在引擎 `validate()` 里找「推进模块同组超过 active 上限」那条硬违规。

    判据**用引擎的结论，不自己数**：`rule.type == "max_group"` 且 `limit == "active"` 且
    `used > allowed`，并且违规**目标模块**的 group 名是 `Propulsion Module`
    （同一套 group 判据在 `views/shared.py`，两边不会分叉）。
    其它 `max_group`（例如 f22 的 group 1770 `limit=active` 违规、挂点/装备组）不命中。
    """
    violations = facts.ctx.engine.validate(facts.ctx.fit)
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        rule = violation.get("rule")
        if not isinstance(rule, dict) or rule.get("type") != "max_group":
            continue
        if rule.get("limit") != "active":
            continue
        used = rule.get("used")
        allowed = rule.get("allowed")
        if isinstance(used, bool) or isinstance(allowed, bool):
            continue
        if not isinstance(used, (int, float)) or not isinstance(allowed, (int, float)):
            continue
        if used <= allowed:
            continue
        target = violation.get("target")
        index = target.get("index") if isinstance(target, dict) else None
        if not isinstance(index, int) or isinstance(index, bool):
            continue
        if not 0 <= index < len(facts.items):
            continue
        if facts.group_name(facts.items[index].get("type_id")) != _shared.PROPULSION_GROUP:
            continue
        return {
            "group_id": rule.get("group_id"),
            "used": int(round(float(used))),
            "allowed": int(round(float(allowed))),
        }
    return None


def _rule_propulsion_state_conflict(facts: _Facts) -> dict | None:
    """推进模块触发了 `max_group … limit=active` 硬违规 → 速度/信号/对齐基于**状态冲突**。

    T-35 / f67（历史上第一条真实存在的双推进配置：`1MN AB` + `5MN MWD` 同时 active）：

    - 引擎**不会**因为 `max_group` 违规自动把模块降级，于是 `-37 maxVelocity` 把
      AB 的 `speedFactor 156.25` 与 MWD 的 `631.25` **一起叠上**：f67 算出
      `59,928.363 m/s`（overload `134,777.472`）——驱逐舰不可能的速度；
    - 数值**保持引擎原样**（本规则一个数都不改、也不挑一个模块停用——降级是求解，不在本期），
      它只负责把「这组数基于多个推进同时启用」这件事**说出口**，否则下游 agent 会把
      59,928 m/s 当成有效数据；
    - 判据是引擎的硬违规 + 装配事实（有几件推进模块处于开启态），**不设数值阈值**。
    - **口径（T-38 主人裁决）**：双推进一起带很常见，只是**不能同时启用**——所以这不是
      「配装有问题」，而是**状态冲突**；id、文案、注释一律不写判词。

    与 `no_propulsion` 同属机动视角；`mwd_sig_increase` 也会在双推进时命中，
    两条 flag 不冲突：一条说「信号半径被撑大」，一条说「撑大的那个数是叠加出来的」。
    """
    if not facts.index_available:
        return None
    over = _propulsion_active_limit_violation(facts)
    if over is None:
        return None

    open_modules = [
        item for _position, item in facts.propulsions() if item.get("state") in _OPEN_STATES
    ]
    if len(open_modules) < 2:  # 违规存在但开启态不足两件 → 事实对不上，宁可不报（不猜）
        return None

    names = [item.get("name") or f"type_id {item.get('type_id')}" for item in open_modules]
    # evidence **只放别处拿不到的事实**（体积预算 + 不重复）：
    # - 模块名在 `mwd_sig_increase.evidence.propulsion` 里已有，不再抄一遍；
    # - 被叠加出来的速度/信号就是同一视角的 `overview.speed_mps` / `signature_radius_m`，
    #   再放进 evidence 只是让同一份输出多一份拷贝（f47 会因此挤破 17000 chars 预算）。
    return {
        "conflict": _conflict_text(names),
        "group_id": over["group_id"],
        "used": over["used"],
        "allowed": over["allowed"],
    }


def _rule_resist_hole(facts: _Facts) -> dict | None:
    """某层某系抗性显著低于该层其它三系（**相对**判定：最低者与最高者之比）。

    抗性 = 1 − 共振率；所以「抗性最弱」= **共振率最高**（受到该系伤害最多）。
    共振率 ID 必须是正 ID（护盾 271–274 / 护甲 267–270 / 结构 113·111·109·110）。
    """
    layers = (
        ("shield", _a.ATTR_SHIELD_EM_RESONANCE, _a.ATTR_SHIELD_EXPLOSIVE_RESONANCE,
         _a.ATTR_SHIELD_KINETIC_RESONANCE, _a.ATTR_SHIELD_THERMAL_RESONANCE),
        ("armor", _a.ATTR_ARMOR_EM_RESONANCE, _a.ATTR_ARMOR_EXPLOSIVE_RESONANCE,
         _a.ATTR_ARMOR_KINETIC_RESONANCE, _a.ATTR_ARMOR_THERMAL_RESONANCE),
        ("hull", _a.ATTR_HULL_EM_RESONANCE, _a.ATTR_HULL_EXPLOSIVE_RESONANCE,
         _a.ATTR_HULL_KINETIC_RESONANCE, _a.ATTR_HULL_THERMAL_RESONANCE),
    )
    damage_names = ("em", "explosive", "kinetic", "thermal")

    offending: list[dict] = []
    for layer_name, *attribute_ids in layers:
        resonances: list[tuple[str, float]] = []
        for damage, attribute_id in zip(damage_names, attribute_ids):
            value = _num(facts.attrs, attribute_id)
            if value is None or value <= 0.0:
                resonances = []
                break
            resonances.append((damage, value))
        if not resonances:
            continue

        weakest_damage, weakest_resonance = max(resonances, key=lambda pair: pair[1])
        strongest_damage, strongest_resonance = min(resonances, key=lambda pair: pair[1])
        ratio = weakest_resonance / strongest_resonance
        if ratio < RESIST_HOLE_RATIO:
            continue
        offending.append(
            {
                "layer": layer_name,
                "ratio": units.fmt(ratio),
                "resonance_ratio_floor": RESIST_HOLE_RATIO,
                "weakest_damage": weakest_damage,
                "weakest_resonance": units.fmt(weakest_resonance),
                "weakest_resist_pct": units.fmt(units.pct(weakest_resonance)),
                "strongest_damage": strongest_damage,
                "strongest_resonance": units.fmt(strongest_resonance),
                "strongest_resist_pct": units.fmt(units.pct(strongest_resonance)),
            }
        )

    if not offending:
        return None

    worst = max(offending, key=lambda layer: layer["ratio"])
    evidence = {
        "layers": offending,
        "offending_layers": len(offending),
        # 打平最关键的一层，供文案模板取用
        "layer": worst["layer"],
        "ratio": worst["ratio"],
        "weakest_damage": worst["weakest_damage"],
        "weakest_resist_pct": worst["weakest_resist_pct"],
        "strongest_damage": worst["strongest_damage"],
        "strongest_resist_pct": worst["strongest_resist_pct"],
    }
    return evidence


def _rule_repaired_but_unstable(facts: _Facts) -> dict | None:
    """有修复速率但电容不稳定。修复速率：-45/-46/-47/-51（raw hp/s）。"""
    repair_sources = (
        ("armor", _a.ATTR_ARMOR_REPAIR_RATE),
        ("hull", _a.ATTR_HULL_REPAIR_RATE),
        ("shield_boost", _a.ATTR_SHIELD_BOOST_RATE),
        ("shield_passive", _a.ATTR_PASSIVE_SHIELD_RECHARGE),
    )
    positive = {
        name: units.fmt(value)
        for name, attribute_id in repair_sources
        if (value := _num(facts.attrs, attribute_id)) is not None and value > 0.0
    }
    if not positive:
        return None
    if facts.cap_stable() is not False:  # None（未知）或 True（稳定）都不算
        return None

    depletes = _num(facts.attrs, _a.ATTR_CAP_DEPLETES_IN)
    return {
        "repair_rates": positive,
        "repair_layers": "、".join(f"{name} {value} hp/s" for name, value in positive.items()),
        "cap_stable_pct": units.fmt(_num(facts.attrs, _a.ATTR_CAP_STABLE_PCT) or 0.0),
        # 哨兵归一口径只写一份：`-7` 的 `-1.0` 是「永不耗尽」，不是秒数（T-22 / D6）
        "cap_depletes_in_s": _shared.depletes_in_s(
            _num(facts.attrs, _a.ATTR_CAP_STABLE_PCT), depletes
        ),
    }


def _rule_alpha_dph_ratio(facts: _Facts) -> dict | None:
    """`dph/dps` 与主要武器周期明显不符（**相对**判定）。

    `alpha` = 首轮齐射（-11），`dps` = 秒伤（-12）；两者相除得到的是「秒」，
    量纲上应当落在**武器激活周期**附近（实测样本 dph/dps = 2.3046 s，与中子炮
    cycle 2304.636 ms 去掉毫秒后完全一致）——这是不变式 I2 的直接推论。

    「武器」用属性判定，不靠名字：同时带 `cycleTime`（51）与 `damageMultiplier`（64）
    的已装模块（磁场稳定器只有 64 没有 51，扫描发射器只有 51 没有 64，都被排除）。
    """
    dph = _num(facts.attrs, _a.ATTR_DAMAGE_ALPHA)
    dps = _num(facts.attrs, _a.ATTR_DAMAGE_DPS)
    if dph is None or dps is None or dps <= 0.0:
        return None

    cycles = sorted(
        cycle
        for _pos, item in facts.fitted()
        if item.get("state") in _OPEN_STATES
        and (cycle := item["attributes"].get(_a.ATTR_CYCLE_TIME)) is not None
        and item["attributes"].get(_a.ATTR_DAMAGE_MULTIPLIER) is not None
        and cycle > 0.0
    )
    if not cycles:
        return None

    ratio = dph / dps
    cycle_min = cycles[0]
    cycle_max = cycles[-1]
    if cycle_min / DPH_DPS_FACTOR <= ratio <= cycle_max * DPH_DPS_FACTOR:
        return None

    return {
        "dph_hp": units.fmt(dph),
        "dps_hp_s": units.fmt(dps),
        "ratio_s": units.fmt(ratio),
        "cycle_min_s": units.fmt(cycle_min),
        "cycle_max_s": units.fmt(cycle_max),
        "cycle_samples": len(cycles),
        "factor": DPH_DPS_FACTOR,
    }


def _rule_cap_sensitive(facts: _Facts) -> dict | None:
    """关闭某一模块后电容从不稳定变为稳定。

    「耗电最高」不用猜：逐个摘掉再算，**摘掉后稳定余量最大的那个就是耗电最高的那个**
    （相对排序，没有绝对阈值）。样本配装逐个摘除都仍不稳定 → 本规则不命中。
    """
    if facts.cap_stable() is not False:
        return None

    best: tuple[dict, float] | None = None
    checked = 0
    for position, item in facts.fitted():
        checked += 1
        without = facts.fit_without({position})
        attrs = ship_attributes(
            facts.ctx.calculate_variant(_key(f"without:{position}"), without)
        )
        stable_pct = _num(attrs, _a.ATTR_CAP_STABLE_PCT)
        if stable_pct is None or stable_pct <= 0.0:
            continue
        if best is None or stable_pct > best[1]:
            best = (item, stable_pct)

    if best is None:
        return None

    module, stable_pct = best
    depletes = _num(facts.attrs, _a.ATTR_CAP_DEPLETES_IN)
    return {
        "module": module.get("name") or f"type_id {module.get('type_id')}",
        "module_type_id": module.get("type_id"),
        "stable_pct_before": units.fmt(_num(facts.attrs, _a.ATTR_CAP_STABLE_PCT) or 0.0),
        "stable_pct_after": units.fmt(stable_pct),
        # 同上：`-7` 的 `-1.0` 是哨兵（永不耗尽），不是秒数（T-22 / D6）
        "cap_depletes_in_s_before": _shared.depletes_in_s(
            _num(facts.attrs, _a.ATTR_CAP_STABLE_PCT), depletes
        ),
        "candidates_checked": checked,
    }


_HANDLERS: dict[str, Callable[[_Facts], dict | None]] = {
    "resource_overflow": _rule_resource_overflow,
    "resource_over_budget_unflyable": _rule_resource_over_budget_unflyable,
    "resource_slack": _rule_resource_slack,
    "empty_hardpoint": _rule_empty_hardpoint,
    "drone_bandwidth_idle": _rule_drone_bandwidth_idle,
    "no_propulsion": _rule_no_propulsion,
    "mwd_sig_increase": _rule_mwd_sig_increase,
    "propulsion_state_conflict": _rule_propulsion_state_conflict,
    "resist_hole": _rule_resist_hole,
    "repaired_but_unstable": _rule_repaired_but_unstable,
    "alpha_dph_ratio": _rule_alpha_dph_ratio,
    "weapon_damage_zero": _rule_weapon_damage_zero,
    "fighters_in_bay_only": _rule_fighters_in_bay_only,
    "cap_sensitive": _rule_cap_sensitive,
}


# =============================================================================
# 对外入口
# =============================================================================


def _render(template: str, evidence: dict) -> str:
    """用 evidence 同名字段填文案；填不了就原样返回（不吞异常、不编数字）。"""
    try:
        return template.format(**evidence)
    except (KeyError, IndexError, ValueError):
        return template


def evaluate(ctx: ViewContext, *, rules: tuple[Rule, ...] | list[Rule] | None = None) -> list[dict]:
    """跑规则表，返回命中的 flag 列表（顺序 = 规则表顺序）。

    Args:
        ctx: 视角上下文（六视角共用同一个）。
        rules: 显式传入的规则表（测试用）；默认读 `config/diagnostics.json`。

    Raises:
        KeyError: 规则表里有 `diagnostics.py` 未实现的 id（正常由 `load_rules` 提前拦住）。
    """
    rule_list = load_rules() if rules is None else tuple(rules)
    facts = _Facts(ctx)

    flags: list[dict] = []
    for rule in rule_list:
        if not rule.enabled:
            continue
        handler = _HANDLERS.get(rule.id)
        if handler is None:
            raise KeyError(f"规则 {rule.id!r} 没有实现；已实现：{sorted(_HANDLERS)}")
        evidence = handler(facts)
        if evidence is None:
            continue
        flags.append(
            {
                "id": rule.id,
                "view": rule.view,
                "message": _render(rule.message, evidence),
                "evidence": evidence,
            }
        )
    return flags
