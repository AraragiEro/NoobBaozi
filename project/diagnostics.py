"""可疑点规则引擎（L3）：**只做结构性事实检查，不设绝对数值阈值**。

主人对这个模块的要求只有一句（PROGRESS §7 决策日志 / 软件设计 §7.7）：

> 可疑点**不设绝对阈值**——快慢取决于同一场战斗里的其他人，属场景相对判断。

所以本模块的判定只有两种形状：

1. **「是否有」**：是否为空（无推进模块、无无人机）、是否为负（余量缺口）、是否不稳定；
2. **「相对倍数」**：信号半径开关比、抗性最低/最高比、dph/dps 与武器周期比、余量与总量比。

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


PROPULSION_GROUP = "Propulsion Module"
"""推进模块的 SDE group 名（实测：5MN MWD / 1MN AB 的 group 都是这个名字）。"""


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

    关闭态用「把推进模块整个摘掉」构造：推进模块对信号半径的作用只在启用时生效，
    摘掉等价于关闭，而不会连带关掉别的东西（`scenarios` 只支持 `{"all": state}`，
    做不到「只关推进」）。
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

    without = facts.fit_without({position for position, _item in propulsions})
    attrs_off = ship_attributes(facts.ctx.calculate_variant(_key("propulsion_off"), without))
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
        "cap_depletes_in_s": units.fmt(depletes) if depletes is not None else None,
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
        "cap_depletes_in_s_before": units.fmt(depletes) if depletes is not None else None,
        "candidates_checked": checked,
    }


_HANDLERS: dict[str, Callable[[_Facts], dict | None]] = {
    "resource_overflow": _rule_resource_overflow,
    "resource_slack": _rule_resource_slack,
    "empty_hardpoint": _rule_empty_hardpoint,
    "drone_bandwidth_idle": _rule_drone_bandwidth_idle,
    "no_propulsion": _rule_no_propulsion,
    "mwd_sig_increase": _rule_mwd_sig_increase,
    "resist_hole": _rule_resist_hole,
    "repaired_but_unstable": _rule_repaired_but_unstable,
    "alpha_dph_ratio": _rule_alpha_dph_ratio,
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
