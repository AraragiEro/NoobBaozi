"""实测推导 provenance 三态表（T-08 首版 → **T-14 多样本重推**）。

背景（第 16 轮裁决 + 字段规格 §0.2）：**provenance 只表示「这个数在本次计算里是否
依赖假设」**，与「可信度 A–E」是正交维度。这张表从「拍脑袋」改成「跑出来」：对样本
配装做**三轴敏感性扫描**，逐属性判定它是否随假设变化。

T-14 修的两个缺陷（都是**静默退化**，必须显式炸出来）
----------------------------------------------------
**缺陷 A：工具跑在旧的 19 技能档上。** `derive_skill_tier(fit, engine, index=None)`
会静默退化成 `min_required_only`（只补被模块要求的技能，实测 19 个）；T-12 起正确档是
`all4+min_required`（全部已发布技能 4 级 + 补最低，实测 512 个）。所以本脚本：

1. `load_sample()` **必须**把 `index` 传给 `derive_skill_tier`；
2. 拿到技能档后**立刻**过 `require_full_skill_tier()` 护栏 —— 档名不是
   `all4+min_required`、或技能数 `<= MIN_SKILL_TIER_SIZE`（100）→ **抛 `RuntimeError`**，
   绝不静默跑在旧档上（与 `acceptance/run_acceptance.py` 的 blocker 同一口径）；
3. 报告与标准输出都写出「每个样本的技能档名 + 技能数」。

**缺陷 B：单样本推导把大量属性困成 `unknown`。** 旧规则 1 是「基准样本里不存在 →
`unknown`」，于是 `1367 maxSubSystems`（T3C 才有）、`281 explosionDelay`（导弹才有）、
`1272 droneBandwidthUsed`、`-14`、`-55…-62`（无人机 / 战斗机才有）全部落成 `unknown`
—— 这是**样本不够**，不是属性不可知（PROGRESS §3 第 60 条：判定必须两级）。
所以本脚本改成**多样本取并集**：

    样本                                        覆盖目标
    f1-hecate-killmail.eft（killmail 139246907）基础：炮台 / 装甲 / MWD / 货舱 / 热参数
    f4-non-hecate.json（killmail 139254056）    Loki T3C：子系统槽 1367、无人机 -14、导弹装备级
    f15-battleship-raven.json（Raven）          导弹 281 explosionDelay（装备级）
    构造：Nyx + 战斗机 + 无人机                 -55/-56/-57/-58/-59/-60/-61/-62、1272

判定规则（每个样本各跑一遍三轴，**取并集**，按顺序命中即停）
-----------------------------------------------------------
    轴        变体 A（基准）                        变体 B
    技能      derive_skill_tier（全4+补最低）        全部技能 0（空 skills）
    状态      全 active                             全 online
    伤害剖面  25/25/25/25（默认）                     纯 EM（em=1 其余 0）

1. 手工覆盖（`OVERRIDES`，每条必写理由）→ 表里那一条（当前只有 `-70`）
2. 属性 ID 为**负**（引擎 pass 4 派生值）→ `assumed`（**一律**；即使在全部样本里都
   缺失也算「建立在假设之上」，不是「样本不够」）
3. **任一**轴的**任一**样本上发生变化 → `assumed`（证据记「哪个样本 × 哪一轴」）
4. 至少在一个样本里出现、且它在**所有出现它的样本**上三轴都不变 → `measured`
5. **全部样本都不存在**（且非负 ID、非覆盖）→ `unknown`

规则 4/5 是 T-14 的关键修正：`unknown` 的含义收窄为「**全部样本都没有**」，而不是
「**这个样本没有**」。这也是 PROGRESS §3 第 60 条两级模型的第一级（属性级基线类）；
第二级（实例级缺失 → 本次计算里该属性没有键，由投影层按 `unknown` 处理）不在本表里。

为什么规则 4 写成「在所有**出现它的**样本上不变」而不是字面的「在所有样本上出现」：
`1367 maxSubSystems` 只有 T3C 有、`281 explosionDelay` 只有导弹有 —— 要求「四个
样本都出现」会让这类属性永远当不成 `measured`（甚至被迫落 `unknown`），这正是缺陷 B
的形态。属性级基线类只回答「这个值变不变」，「这条配装有没有这个部件」是第二级的事。

轴的定义细节
------------
- 状态轴的 A 变体写死成全 active，是为了让「A/B 一对」在代码里显式可见；脚本会打印
  自检 `state_axis_a_equals_baseline`（对 Hecate 货舱物品而言 A 与真基准不同，差异
  本身也是**状态维度**的差异，故一并计入该轴敏感）。
- 每个轴的变化判定是「A ≠ B **或** A ≠ 真基准」：后者覆盖「A 变体相对真基准已经动了」
  的情形（只可能出现在状态轴，因为技能轴与剖面轴的 A 就是真基准）。

扫描范围
--------
`ship.attributes` **与** `items[i].attributes` 一起扫（`provenance.typed()` 是「属性 ID
→ 三态」的一维映射，装备级属性也打三态）。比较方式是**逐属性**比对「船体值 + 全部
装备值（排序后的多重集）」，与装备顺序无关。

写进产品表的范围
----------------
只包含 `config/attributes.py` **已登记常量名**的属性（红线 N12 禁止裸写 ID，
`audit()` 的 `unnamed` 自检也要求如此）。扫到但未登记的属性单独一节列出。

产物
----
- 标准输出：人类复核用（分样本 × 分轴证据、三态计数）；
- `evefit/tools/provenance_report.json`：机器可复核用的完整证据（按样本 × 轴分组）；
- `evefit/project/provenance.py`：**三张表由本脚本重写**（人不再手抄；`_MANUAL_OVERRIDES`
  与理由、`audit()` 元信息仍是人维护的部分）。写回前会先 `compile()` 语法护栏。

轴外风险（补一节取证，不改表）
------------------------------
`derive_skill_tier` 现在覆盖全部已发布技能（512），但技能**等级**只有 4/5 两档。
脚本末尾用几个轴外技能做抽样取证（`SUPPLEMENTARY_SKILLS`），把该局限写成可复跑的
证词 —— **不据此改表**（轴的定义是另一个任务的事）。
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from evefit.config import attributes as _a
from evefit.config.paths import db_path
from evefit.engine.adapter import Engine, load_eft, load_killmail
from evefit.engine.assumptions import (
    DEFAULT_DAMAGE_PROFILE,
    SKILL_TIER_NAME,
    SKILL_TIER_NAME_MIN_ONLY,
    derive_skill_tier,
)
from evefit.sde import attribute_dict as _dict
from evefit.sde.index import Index

# =============================================================================
# 零、常量
# =============================================================================

EVE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = EVE_ROOT / "acceptance" / "fixtures"

REPORT_PATH: Path = Path(__file__).resolve().parent / "provenance_report.json"
"""证据落盘位置（人工复核 + diff 用；不进产品链路）。"""

PROVENANCE_PATH: Path = EVE_ROOT / "project" / "provenance.py"
"""产品表的位置（三张 frozenset 由本脚本重写）。"""

MIN_SKILL_TIER_SIZE: int = 100
"""技能档护栏：技能数必须 **> 100**（正确档实测 512，旧档只有 19）。"""

AXIS_SKILL: str = "skill"
AXIS_STATE: str = "state"
AXIS_PROFILE: str = "profile"
AXES: tuple[str, str, str] = (AXIS_SKILL, AXIS_STATE, AXIS_PROFILE)

AXIS_LABEL: dict[str, str] = {
    AXIS_SKILL: "技能轴（全4+补最低 vs 全0）",
    AXIS_STATE: "状态轴（全active vs 全online）",
    AXIS_PROFILE: "伤害剖面轴（25/25/25/25 vs 纯EM）",
}

AXIS_SHORT: dict[str, str] = {
    AXIS_SKILL: "技能",
    AXIS_STATE: "状态",
    AXIS_PROFILE: "剖面",
}

PURE_EM_PROFILE: dict[str, float] = {"em": 1.0, "explosive": 0.0, "kinetic": 0.0, "thermal": 0.0}

#: 浮点比较容差。引擎是确定性的，同一输入复跑逐位相同；给 1e-12 只是不依赖
#: 「浮点相等」这种脆假设（红线 N11）。
REL_TOL: float = 1e-12
ABS_TOL: float = 1e-12

MEASURED: str = "measured"
ASSUMED: str = "assumed"
UNKNOWN: str = "unknown"
STATES: tuple[str, str, str] = (MEASURED, ASSUMED, UNKNOWN)

REASON_NOT_PRESENT: str = "not_present_in_any_sample"
REASON_NEGATIVE: str = "negative_derived_id"
REASON_SKILL: str = "skill_axis"
REASON_STATE: str = "state_axis"
REASON_PROFILE: str = "profile_axis"
REASON_INVARIANT: str = "invariant"
REASON_OVERRIDE: str = "manual_override"

REASON_LABEL: dict[str, str] = {
    REASON_OVERRIDE: "手工覆盖（理由逐条写在 provenance.py 的 `_MANUAL_OVERRIDES`）",
    REASON_NEGATIVE: "规则 2 · 负 ID（引擎 pass 4 派生）",
    REASON_SKILL: "规则 3 · 技能轴变化",
    REASON_STATE: "规则 4 · 状态轴变化",
    REASON_PROFILE: "规则 5 · 伤害剖面轴变化",
    REASON_INVARIANT: "规则 4 · 出现在样本里且三轴都不变",
    REASON_NOT_PRESENT: "规则 5 · 全部样本里都不存在",
}

REASON_ORDER: tuple[str, ...] = (
    REASON_NEGATIVE,
    REASON_SKILL,
    REASON_STATE,
    REASON_PROFILE,
    REASON_OVERRIDE,
)

UNVERIFIED_MAX_TARGETS: int = -70
"""`-70` = `maxTargets`。`config/attributes.py` 本轮为只读、没有登记这个 ID，
所以常量就近定义（与 `project/provenance.py` 的 `ATTR_MAX_TARGETS_UNVERIFIED`
是同一个 ID、同一个理由；两边改动必须同步）。"""

OVERRIDES: dict[int, tuple[str, str]] = {
    UNVERIFIED_MAX_TARGETS: (
        UNKNOWN,
        "实测三轴都不变且恒为 0：引擎没有在算 maxTargets（不是「依赖假设」，是「没算」）"
        "→ 按红线 N4，不用 0 冒充 measured，也不冒充 assumed，判 unknown",
    ),
}
"""手工覆盖（ID → (目标三态, 理由)）。**只允许**写有实测证据、且规则给不出正确答案的项。"""

SUPPLEMENTARY_SKILLS: tuple[str, ...] = (
    "Power Grid Management",
    "Shield Management",
    "Shield Operation",
    "Energy Management",
    "Energy Systems Operation",
    "Signature Analysis",
)
"""抽样取证用的技能名。**只用于取证，不参与判定。**

T-14 起技能档是「全部已发布技能 4 级」，所以抽样不再是「档外技能」而是「4 级 → 5 级」：
轴的局限从「覆盖哪些技能」变成「技能等级只到 4/5」。
"""


# =============================================================================
# 一、样本
# =============================================================================


@dataclass(frozen=True, slots=True)
class Sample:
    """一条真实配装样本（文件或本脚本构造）。"""

    key: str
    label: str
    covers: str
    kind: str  # "eft" | "killmail" | "carrier"
    path: Path | None = None

    @property
    def source(self) -> str:
        if self.kind == "carrier":
            return "inline（本脚本构造，见 CARRIER_ITEMS）"
        assert self.path is not None  # noqa: S101 - 常量表自身的完整性
        return self.path.relative_to(EVE_ROOT).as_posix()

    def as_json(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "covers": self.covers,
            "source": self.source,
        }


CARRIER_SHIP_TYPE_ID: int = 23913
"""Nyx（超级航母）：`2219 fighterHeavySlots = 4` —— 普通航母该属性缺失 = 0，验不了 `-59`
（PROGRESS §3 第 42 条）。"""

CARRIER_ITEMS: tuple[tuple[int, str, int, int], ...] = (
    (40556, "fighter_tube", 0, 6),  # Templar II 轻型战斗机：装备级 -55/-56、舰船级 -60/-62
    (40564, "fighter_tube", 1, 6),  # Gungnir II 重型战斗机：-59
    (40568, "fighter_tube", 2, 3),  # Cenobite II 支援战斗机：-61（占槽不出 DPS）
    (2466, "drone_bay", 0, 5),  # Hornet II 无人机：-14/-24、装备级 1272
)
"""构造的航母样本：战斗机必须放 `fighter_tube` 槽，放 `fighter_bay` 只算机库占用（`-57`），
槽位占用 `-59/-60/-61/-62` 与 `-58` 全是 0 —— 实测踩过，写在这里防止后来者再踩。"""


SAMPLES: tuple[Sample, ...] = (
    Sample(
        key="f1",
        label="f1-hecate-killmail.eft (killmail 139246907, Hecate T3D)",
        covers="基础：炮台 / 装甲 / MWD / 货舱 / 热参数",
        kind="eft",
        path=FIXTURES / "f1-hecate-killmail.eft",
    ),
    Sample(
        key="f4",
        label="f4-non-hecate.json (killmail 139254056, Loki T3C)",
        covers="子系统槽 1367、非 Hecate 布局、无人机 -14/-24",
        kind="killmail",
        path=FIXTURES / "f4-non-hecate.json",
    ),
    Sample(
        key="f15",
        label="f15-battleship-raven.json (Raven, 弹道 + 导弹)",
        covers="导弹 281 explosionDelay（装备级）",
        kind="killmail",
        path=FIXTURES / "f15-battleship-raven.json",
    ),
    Sample(
        key="carrier",
        label="构造样本：Nyx + 6×Templar II + 6×Gungnir II + 3×Cenobite II + 5×Hornet II",
        covers="战斗机 -55/-56/-57/-58/-59/-60/-61/-62、无人机 1272",
        kind="carrier",
    ),
)
"""至少 4 条样本，覆盖「基础 / 子系统 / 导弹 / 战斗机 + 无人机」四个属性族。"""


def _carrier_fit() -> dict:
    """构造航母样本（每次返回新对象，避免下游改动污染常量）。"""
    fit = load_eft("[Nyx, T-14 carrier sample]")
    fit["items"] = [
        {
            "type_id": type_id,
            "slot": {"type": slot, "index": index},
            "quantity": quantity,
            "state": "active",
            "charge": None,
        }
        for type_id, slot, index, quantity in CARRIER_ITEMS
    ]
    return fit


def load_fit(sample: Sample) -> dict:
    """按样本种类读出归一化 fit（不联网、不随机）。"""
    if sample.kind == "carrier":
        return _carrier_fit()
    if sample.path is None:  # pragma: no cover - 常量表自身的完整性
        raise RuntimeError(f"样本 {sample.key} 既不是构造样本也没有文件路径")
    text = sample.path.read_text(encoding="utf-8")
    if sample.kind == "eft":
        return load_eft(text)
    return load_killmail(json.loads(text))


# =============================================================================
# 二、技能档护栏（缺陷 A）
# =============================================================================


def require_full_skill_tier(tier_name: str, skills: dict[int, int] | object) -> None:
    """护栏：技能档必须是 `all4+min_required` 且技能数 **> 100**，否则抛 `RuntimeError`。

    `index=None` 时 `derive_skill_tier` 会静默退化成 `min_required_only`（19 个技能），
    **不报错** —— T-14 之前这个工具就是这么跑在旧档上的（缺陷 A）。这里把两种退化
    （档名不对 / 技能数太少）都变成硬错误，宁可炸也不产出一张错的表。

    Args:
        tier_name: 技能档名（`SKILL_TIER_NAME` 或 `SKILL_TIER_NAME_MIN_ONLY`）。
        skills: `{skill_type_id: level}`。

    Raises:
        RuntimeError: 档名不是 `all4+min_required`，或技能数 `<= MIN_SKILL_TIER_SIZE`。
    """
    size = len(skills)  # type: ignore[arg-type]
    if tier_name != SKILL_TIER_NAME:
        raise RuntimeError(
            f"技能档退化：期望 {SKILL_TIER_NAME!r}，实际 {tier_name!r}。"
            "`derive_skill_tier(fit, engine, index)` 的 index 没传进去？"
            "（旧档只有 19 个技能，provenance 的技能轴会整条失效）"
        )
    if size <= MIN_SKILL_TIER_SIZE:
        raise RuntimeError(
            f"技能档退化：只有 {size} 个技能（要求 > {MIN_SKILL_TIER_SIZE}）。"
            f"期望 {SKILL_TIER_NAME!r} 档实测 512 个；这看起来是 {SKILL_TIER_NAME_MIN_ONLY!r} 旧档。"
        )


def _tier_name(index: "Index | None") -> str:
    """与 `engine.assumptions.default_assumptions` 同一口径的档名（传了 index 才是新档）。"""
    return SKILL_TIER_NAME if index is not None else SKILL_TIER_NAME_MIN_ONLY


def load_sample(
    engine: Engine, sample: Sample, index: "Index | None"
) -> tuple[dict, dict[int, int], str]:
    """读出样本配装 + 技能档（**必须传 index**，并当场过护栏）。

    Returns:
        `(fit, skills, tier_name)`；`skills` 是 `all4+min_required` 档（实测 512 个）。

    Raises:
        RuntimeError: 技能档退化（见 `require_full_skill_tier`）。
    """
    fit = load_fit(sample)
    skills = derive_skill_tier(fit, engine, index)  # ← 缺陷 A 的修法：index 必须传
    tier_name = _tier_name(index)
    require_full_skill_tier(tier_name, skills)  # 加载后立刻断言，绝不静默跑在旧档上
    return fit, skills, tier_name


# =============================================================================
# 三、观测与比对
# =============================================================================


@dataclass(frozen=True, slots=True)
class Observation:
    """一个属性 ID 在某个变体里的全部观测值（船体 + 全部装备）。"""

    ship: float | None
    items: tuple[float, ...]

    def as_json(self) -> dict:
        return {"ship": self.ship, "items": list(self.items)}

    def describe(self) -> str:
        parts: list[str] = []
        if self.ship is not None:
            parts.append(f"ship={_num(self.ship)}")
        if self.items:
            parts.append("items=[" + _numbers(self.items) + "]")
        return "  ".join(parts) if parts else "—"


MAX_SHOWN_ITEMS: int = 8
"""证据里装备值列表最多展开几项（超了截断，只影响可读性）。"""


def _numbers(values: tuple[float, ...]) -> str:
    shown = ", ".join(_num(value) for value in values[:MAX_SHOWN_ITEMS])
    if len(values) > MAX_SHOWN_ITEMS:
        shown += f", … 共{len(values)}项"
    return shown


def _num(value: float) -> str:
    """数值展示：整数样子的去掉 `.0`，其余 6 位有效位（只为可读，不参与判定）。"""
    if math.isclose(value, round(value), rel_tol=0.0, abs_tol=1e-9):
        return str(int(round(value)))
    return f"{value:.6g}"


def _close(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return math.isclose(left, right, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _same_observation(left: Observation, right: Observation) -> bool:
    if not _close(left.ship, right.ship):
        return False
    if len(left.items) != len(right.items):
        return False
    return all(_close(x, y) for x, y in zip(left.items, right.items))


def _optional_same(left: Observation | None, right: Observation | None) -> bool:
    if left is None or right is None:
        return left is None and right is None
    return _same_observation(left, right)


def snapshot(calculation: dict) -> dict[int, Observation]:
    """把一次 `calculation` 压成 `{attribute_id: Observation}`（原始值，不换单位）。

    不换单位是刻意的：判定「变不变」与单位无关，而缩放只允许出现在
    `project/units.py` / `sde/attribute_dict.py`（红线 N5）。
    """
    ship: dict[int, float] = {}
    raw_ship = (calculation.get("ship") or {}).get("attributes") or {}
    for key, entry in raw_ship.items():
        value = entry.get("value") if isinstance(entry, dict) else entry
        if value is None:
            continue
        ship[int(key)] = float(value)

    items: dict[int, list[float]] = {}
    for item in calculation.get("items") or []:
        for key, entry in (item.get("attributes") or {}).items():
            value = entry.get("value") if isinstance(entry, dict) else entry
            if value is None:
                continue
            items.setdefault(int(key), []).append(float(value))

    return {
        aid: Observation(ship.get(aid), tuple(sorted(items.get(aid, ()))))
        for aid in set(ship) | set(items)
    }


# =============================================================================
# 四、变体构造
# =============================================================================


def _with_skills(fit: dict, skills: dict[int, int]) -> dict:
    return {**fit, "character": {**(fit.get("character") or {}), "skills": dict(skills)}}


def _with_all_states(fit: dict, state: str) -> dict:
    return {**fit, "items": [{**item, "state": state} for item in fit.get("items") or []]}


def _with_profile(fit: dict, profile: dict) -> dict:
    environment = {**(fit.get("environment") or {}), "damage_profile": dict(profile)}
    return {**fit, "environment": environment}


def variants(fit: dict, skills: dict[int, int]) -> dict[str, tuple[dict, dict]]:
    """三轴的 (A, B) 变体 fit。A 一律是基准语义，B 是被换掉的那一项。"""
    base = _with_skills(fit, skills)
    return {
        AXIS_SKILL: (base, _with_skills(fit, {})),
        AXIS_STATE: (_with_all_states(base, "active"), _with_all_states(base, "online")),
        AXIS_PROFILE: (
            _with_profile(base, DEFAULT_DAMAGE_PROFILE),
            _with_profile(base, PURE_EM_PROFILE),
        ),
    }


# =============================================================================
# 五、单个样本的一次三轴扫描
# =============================================================================


@dataclass(slots=True)
class SampleRun:
    """一个样本的三轴扫描结果（`baseline` / `axis_observations` 不进报告，只用于判定）。"""

    sample: Sample
    skill_tier_name: str
    skill_count: int
    skills: dict[int, int]
    baseline: dict[int, Observation]
    axis_observations: dict[str, tuple[dict[int, Observation], dict[int, Observation]]]
    axis_equals_baseline: dict[str, bool]
    axis_change_counts: dict[str, int]

    @property
    def key(self) -> str:
        return self.sample.key

    def as_json(self) -> dict:
        return {
            **self.sample.as_json(),
            "skill_tier_name": self.skill_tier_name,
            "skill_tier_size": self.skill_count,
            "baseline_attribute_count": len(self.baseline),
            "axis_equals_baseline": self.axis_equals_baseline,
            "axis_change_counts": self.axis_change_counts,
        }


def run_sample(engine: Engine, index: Index, sample: Sample) -> SampleRun:
    """对一个样本跑三轴扫描（A / B / 真基准），返回该样本的全部观测。"""
    fit, skills, tier_name = load_sample(engine, sample, index)
    base_fit = _with_skills(fit, skills)
    baseline = snapshot(engine.calculate(base_fit))

    axis_observations: dict[str, tuple[dict[int, Observation], dict[int, Observation]]] = {}
    axis_equals_baseline: dict[str, bool] = {}
    for axis, (variant_a, variant_b) in variants(fit, skills).items():
        # A 变体：与基准完全相同的 fit 直接复用快照，否则真算一遍再对拍。
        obs_a = baseline if variant_a == base_fit else snapshot(engine.calculate(variant_a))
        obs_b = snapshot(engine.calculate(variant_b))
        axis_observations[axis] = (obs_a, obs_b)
        axis_equals_baseline[axis] = set(obs_a) == set(baseline) and all(
            _same_observation(obs_a[aid], baseline[aid]) for aid in baseline
        )

    axis_change_counts = {
        axis: sum(
            1 for aid in baseline if _axis_changed(aid, axis_observations[axis], baseline)
        )
        for axis in AXES
    }

    return SampleRun(
        sample=sample,
        skill_tier_name=tier_name,
        skill_count=len(skills),
        skills=skills,
        baseline=baseline,
        axis_observations=axis_observations,
        axis_equals_baseline=axis_equals_baseline,
        axis_change_counts=axis_change_counts,
    )


def _axis_changed(
    attribute_id: int,
    axis_pair: tuple[dict[int, Observation], dict[int, Observation]],
    baseline: dict[int, Observation],
) -> bool:
    """该属性在这条轴上是否变化：A ≠ B，或 A 相对**真基准**已经不同（只有状态轴可能）。"""
    obs_a_map, obs_b_map = axis_pair
    obs_a = obs_a_map.get(attribute_id)
    obs_b = obs_b_map.get(attribute_id)
    if not _optional_same(obs_a, obs_b):
        return True
    return not _optional_same(obs_a, baseline.get(attribute_id))


def _axis_evidence(run: SampleRun, attribute_id: int) -> dict[str, dict]:
    """该属性在该样本上哪些轴发生了变化（按固定轴序），附 A / B / 基准三侧实测值。"""
    evidence: dict[str, dict] = {}
    for axis in AXES:
        if not _axis_changed(attribute_id, run.axis_observations[axis], run.baseline):
            continue
        obs_a, obs_b = run.axis_observations[axis]
        value_a = obs_a.get(attribute_id)
        value_b = obs_b.get(attribute_id)
        value_base = run.baseline.get(attribute_id)
        evidence[axis] = {
            "a": value_a.as_json() if value_a is not None else None,
            "b": value_b.as_json() if value_b is not None else None,
            "baseline": value_base.as_json() if value_base is not None else None,
        }
    return evidence


# =============================================================================
# 六、判定（多样本并集）
# =============================================================================


@dataclass(slots=True)
class Finding:
    """一个属性的实测结论（自带可复核的证据：样本 × 轴）。"""

    attribute_id: int
    name: str
    registered: bool
    state: str
    reason: str
    present_in: list[str] = field(default_factory=list)
    absent_in: list[str] = field(default_factory=list)
    baseline_sample: str = ""
    baseline: Observation | None = None
    evidence: dict[str, dict[str, dict]] = field(default_factory=dict)
    trigger: dict[str, list[str]] = field(default_factory=dict)
    note: str = ""

    def as_json(self) -> dict:
        return {
            "attribute_id": self.attribute_id,
            "name": self.name,
            "registered": self.registered,
            "state": self.state,
            "reason": self.reason,
            "present_in": self.present_in,
            "absent_in": self.absent_in,
            "baseline_sample": self.baseline_sample,
            "baseline": self.baseline.as_json() if self.baseline is not None else None,
            "trigger": self.trigger,
            "evidence": self.evidence,
            "note": self.note,
        }

    def trigger_text(self) -> str:
        parts: list[str] = []
        for axis in AXES:
            keys = self.trigger.get(axis) or []
            if keys:
                parts.append(f"{AXIS_SHORT[axis]}轴: {'/'.join(keys)}")
        return "；".join(parts)


@dataclass(slots=True)
class Derivation:
    meta: dict
    derived_at: str
    runs: list[SampleRun]
    findings: dict[int, Finding]
    axis_union_change_counts: dict[str, int]
    supplementary_probe: list[dict] = field(default_factory=list)

    # ---- 导出 ----

    @property
    def sample_summary(self) -> str:
        return " + ".join(run.sample.label for run in self.runs)

    def by_state(self, state: str) -> list[Finding]:
        return [
            self.findings[aid] for aid in sorted(self.findings) if self.findings[aid].state == state
        ]

    def registered(self, state: str) -> list[Finding]:
        return [finding for finding in self.by_state(state) if finding.registered]

    def unregistered(self) -> list[Finding]:
        return [
            finding for _aid, finding in sorted(self.findings.items()) if not finding.registered
        ]

    def counts(self, *, registered_only: bool) -> dict[str, int]:
        pool = (
            [finding for finding in self.findings.values() if finding.registered]
            if registered_only
            else list(self.findings.values())
        )
        return {state: sum(1 for finding in pool if finding.state == state) for state in STATES}

    def assumed_by_reason(self) -> dict[str, int]:
        return {
            reason: sum(1 for f in self.registered(ASSUMED) if f.reason == reason)
            for reason in REASON_ORDER
        }

    def as_json(self) -> dict:
        return {
            "task": "T-14 provenance 多样本重推",
            "derived_at": self.derived_at,
            "sample": self.sample_summary,
            "engine": self.meta,
            "skill_tier": {
                "name": SKILL_TIER_NAME,
                "min_size_exclusive": MIN_SKILL_TIER_SIZE,
                "per_sample": {
                    run.key: {"name": run.skill_tier_name, "size": run.skill_count}
                    for run in self.runs
                },
            },
            "samples": [run.as_json() for run in self.runs],
            "axes": {axis: AXIS_LABEL[axis] for axis in AXES},
            "axis_union_change_counts": self.axis_union_change_counts,
            "counts": {
                "scanned": len(self.findings),
                "scanned_measured": self.counts(registered_only=False)[MEASURED],
                "scanned_assumed": self.counts(registered_only=False)[ASSUMED],
                "scanned_unknown": self.counts(registered_only=False)[UNKNOWN],
                "registered_measured": self.counts(registered_only=True)[MEASURED],
                "registered_assumed": self.counts(registered_only=True)[ASSUMED],
                "registered_unknown": self.counts(registered_only=True)[UNKNOWN],
            },
            "assumed_by_reason": self.assumed_by_reason(),
            "overrides": {str(aid): state for aid, (state, _why) in OVERRIDES.items()},
            "supplementary_probe": self.supplementary_probe,
            "findings": [self.findings[aid].as_json() for aid in sorted(self.findings)],
        }


def derive(engine: Engine, index: "Index | None" = None) -> Derivation:
    """跑多样本 × 三轴扫描并判定每个属性的三态（除读样本 / 索引外无 IO）。"""
    opened_here = index is None
    if index is None:
        index = Index.open(db_path())
    try:
        runs = [run_sample(engine, index, sample) for sample in SAMPLES]
        registered_ids = {int(value) for value in _attribute_constants().values()}
        scanned = set(registered_ids)
        for run in runs:
            scanned |= set(run.baseline)
        findings = {
            aid: _classify(aid, runs, registered_ids) for aid in sorted(scanned)
        }
        supplementary = probe_supplementary_skills(engine, index, runs[0])
    finally:
        if opened_here:
            index.close()

    union_counts = {
        axis: sum(1 for aid in scanned if any(
            _axis_changed(aid, run.axis_observations[axis], run.baseline) for run in runs
        ))
        for axis in AXES
    }

    return Derivation(
        meta=engine.meta(),
        derived_at=date.today().isoformat(),
        runs=runs,
        findings=findings,
        axis_union_change_counts=union_counts,
        supplementary_probe=supplementary,
    )


def _classify(attribute_id: int, runs: list[SampleRun], registered_ids: set[int]) -> Finding:
    """按 T-14 的 5 条规则判定（顺序命中即停）。"""
    name = _dict.name_of(attribute_id)
    registered = attribute_id in registered_ids

    present_in = [run.key for run in runs if attribute_id in run.baseline]
    absent_in = [run.key for run in runs if attribute_id not in run.baseline]
    baseline_sample = present_in[0] if present_in else ""
    baseline = (
        next(run.baseline[attribute_id] for run in runs if run.key == baseline_sample)
        if present_in
        else None
    )

    evidence: dict[str, dict[str, dict]] = {}
    trigger: dict[str, list[str]] = {}
    for run in runs:
        axes = _axis_evidence(run, attribute_id)
        if not axes:
            continue
        evidence[run.key] = axes
        for axis in axes:
            trigger.setdefault(axis, []).append(run.key)

    def build(state: str, reason: str, note: str = "") -> Finding:
        return Finding(
            attribute_id=attribute_id,
            name=name,
            registered=registered,
            state=state,
            reason=reason,
            present_in=present_in,
            absent_in=absent_in,
            baseline_sample=baseline_sample,
            baseline=baseline,
            evidence=evidence,
            trigger=trigger,
            note=note,
        )

    # 规则 1：手工覆盖（每条必写理由）
    if attribute_id in OVERRIDES:
        state, why = OVERRIDES[attribute_id]
        return build(state, REASON_OVERRIDE, why)

    # 规则 2：负 ID（引擎 pass 4 派生）—— 一律 assumed，连「全部样本都没有」也一样
    if attribute_id < 0:
        note = "" if present_in else "全部样本里都没有这个键，但负 ID 是 pass 4 派生值，仍判 assumed"
        return build(ASSUMED, REASON_NEGATIVE, note)

    # 规则 3：任一轴的任一样本上变化 → assumed
    for axis, reason in (
        (AXIS_SKILL, REASON_SKILL),
        (AXIS_STATE, REASON_STATE),
        (AXIS_PROFILE, REASON_PROFILE),
    ):
        if trigger.get(axis):
            return build(ASSUMED, reason)

    # 规则 4：至少一个样本里出现，且三轴都不变 → measured
    if present_in:
        return build(MEASURED, REASON_INVARIANT)

    # 规则 5：全部样本都不存在 → unknown
    return build(UNKNOWN, REASON_NOT_PRESENT)


def _attribute_constants() -> dict[str, int]:
    """`config/attributes.py` 里全部 `ATTR_*` 常量（名字 → ID）。"""
    return {
        name: getattr(_a, name) for name in sorted(dir(_a)) if name.startswith(_a.CONSTANT_PREFIX)
    }


def _constant_names_by_id() -> dict[int, str]:
    return {value: name for name, value in _attribute_constants().items()}


def probe_supplementary_skills(
    engine: Engine, index: Index, run: SampleRun
) -> list[dict]:
    """技能等级抽样取证：**不改表**，只为把「技能轴只扫 4 级 → 0 级」写成可复跑的证据。

    做法：在该样本的基准技能档上把某个技能从 4 级提到 **5 级**，看哪些属性相对基准动了。
    技能档现在含全部已发布技能（512），所以抽样点不再是「档外技能」，而是「等级上限」。
    索引库打不开 / 表缺失时返回空列表，表照样产出。
    """
    probe: list[dict] = []
    # 样本 fit 重新读一遍（不缓存 baseline 之外的中间态）
    fit = load_fit(run.sample)
    skills = dict(run.skills)
    base_fit = _with_skills(fit, skills)
    baseline = run.baseline
    try:
        for name in SUPPLEMENTARY_SKILLS:
            hits = [row for row in index.search_types(name, limit=5) if row.get("type_id")]
            if not hits:
                continue
            type_id = int(hits[0]["type_id"])
            current = skills.get(type_id)
            if current is None:
                # 已发布技能都应在技能档里；不在说明 SDE/索引口径变了，照实记下来。
                probe.append({"skill": name, "type_id": type_id, "tier_level": None,
                              "changed_attribute_ids": [], "changed": []})
                continue
            boosted = snapshot(
                engine.calculate(_with_skills(base_fit, {**skills, type_id: current + 1}))
            )
            changed = sorted(
                aid
                for aid in baseline
                if aid in boosted and not _same_observation(baseline[aid], boosted[aid])
            )
            probe.append(
                {
                    "skill": name,
                    "type_id": type_id,
                    "tier_level": current,
                    "changed_attribute_ids": changed,
                    "changed": [
                        {
                            "attribute_id": aid,
                            "name": _dict.name_of(aid),
                            "baseline": baseline[aid].as_json(),
                            "with_skill": boosted[aid].as_json(),
                        }
                        for aid in changed
                    ],
                }
            )
    except sqlite3.Error:  # 索引库半成品（表缺失）也不该拖垮整张表
        return probe
    return probe


def _skills_of(run: SampleRun) -> dict[int, int]:
    """该样本的技能档（`SampleRun.skills` 的只读副本）。"""
    return dict(run.skills)


# =============================================================================
# 七、输出：标准输出
# =============================================================================


def _paste_line(finding: Finding, names: dict[int, str]) -> str:
    """给 `provenance.py` 用的可粘贴行（一律常量名，红线 N12）。"""
    name = names.get(finding.attribute_id)
    if name is None:  # pragma: no cover - 产品表只收盘内常量
        return f"        {finding.attribute_id},  # {finding.name}（未登记，见覆盖理由）"
    return f"        _a.{name},  # {finding.attribute_id}  {finding.name}"


def _evidence_lines(finding: Finding) -> list[str]:
    """紧凑证据：每个「样本 × 轴」一行，写清 A / B / 基准三侧的实测值。"""
    lines: list[str] = []
    for sample_key in finding.present_in + finding.absent_in:
        axes = finding.evidence.get(sample_key) or {}
        for axis, evidence in axes.items():
            sides: list[str] = []
            for label, payload in (("A", evidence["a"]), ("B", evidence["b"])):
                if payload is None:
                    sides.append(f"{label}=<该变体里不存在>")
                    continue
                observed = Observation(payload["ship"], tuple(payload["items"]))
                sides.append(f"{label}: {observed.describe()}")
            lines.append(
                f"      [{sample_key}] 因 {AXIS_SHORT[axis]}轴变化"
                f"（{AXIS_LABEL[axis]}）：{' → '.join(sides)}"
            )
    return lines


def _print_table(out, derivation: Derivation) -> None:
    names = _constant_names_by_id()

    out("一、样本与技能档（技能档护栏：必须 all4+min_required 且技能数 > 100）")
    out("-" * 78)
    for run in derivation.runs:
        out(f"  {run.key:8s} 技能档 {run.skill_tier_name} / {run.skill_count} 个技能")
        out(f"           样本 {run.sample.label}")
        out(f"           覆盖 {run.sample.covers}")
        out(
            "           基线属性 {:3d} 个；各轴变化：技能 {:3d} / 状态 {:3d} / 剖面 {:3d}；"
            "A==基准 {}".format(
                len(run.baseline),
                run.axis_change_counts[AXIS_SKILL],
                run.axis_change_counts[AXIS_STATE],
                run.axis_change_counts[AXIS_PROFILE],
                {axis: run.axis_equals_baseline[axis] for axis in AXES},
            )
        )
    out("")
    out("  并集（任一轴在任一样本上变化）：")
    for axis in AXES:
        out(f"    {AXIS_LABEL[axis]:34s} {derivation.axis_union_change_counts[axis]:3d}")

    out("")
    out("=" * 78)
    out("二、measured（至少在一个样本里出现，且在所有出现它的样本上三轴都不变）")
    out("=" * 78)
    for finding in derivation.registered(MEASURED):
        out(_paste_line(finding, names))
    out(f"  合计 {len(derivation.registered(MEASURED))} 个")

    out("")
    out("=" * 78)
    out("三、assumed（按判定规则分组；逐条附「哪个样本 × 哪一轴」的实测证据）")
    out("=" * 78)
    for reason in REASON_ORDER:
        group = [f for f in derivation.registered(ASSUMED) if f.reason == reason]
        out("")
        out(f"-- {REASON_LABEL[reason]}：{len(group)} 个")
        for finding in group:
            out(_paste_line(finding, names))
            if finding.trigger_text():
                out(f"  触发：{finding.trigger_text()}")
            for line in _evidence_lines(finding):
                out(line)
    out("")
    out(f"  合计 {len(derivation.registered(ASSUMED))} 个")

    out("")
    out("=" * 78)
    out("四、unknown（规则 5：**全部样本**里都不存在；tag() 的默认兜底仍是 unknown）")
    out("=" * 78)
    unknown_registered = derivation.registered(UNKNOWN)
    out(
        f"  扫到的常量 {len(unknown_registered)} 个"
        f"（另加 {len(OVERRIDES)} 条手工覆盖 → `_MANUAL_OVERRIDES`）："
    )
    for finding in unknown_registered:
        note = f"    # {finding.note}" if finding.note else ""
        out(_paste_line(finding, names) + note)
    if not unknown_registered:  # pragma: no cover - 常量表变动时的兜底
        out("        （空）")
    out("  手工覆盖（恒在表里，理由见 `_MANUAL_OVERRIDES`）：")
    for aid, (state, why) in OVERRIDES.items():
        out(f"        {aid:6d}  {state:8s}  {_dict.name_of(aid)}  # {why}")

    out("")
    out("=" * 78)
    out("五、扫到但未在 config/attributes.py 登记（**不得**进产品表）")
    out("=" * 78)
    unregistered = derivation.unregistered()
    tally = {state: sum(1 for f in unregistered if f.state == state) for state in STATES}
    out(
        "  合计 {} 个：measured {} / assumed {} / unknown {}".format(
            len(unregistered), tally[MEASURED], tally[ASSUMED], tally[UNKNOWN]
        )
    )
    for finding in unregistered:
        line = f"  {finding.attribute_id:6d}  {finding.state:8s} {finding.reason:26s} {finding.name}"
        if finding.trigger_text():
            line += f"  ({finding.trigger_text()})"
        out(line)

    out("")
    out("=" * 78)
    out("六、技能等级抽样取证（技能轴只扫 4 级 → 0 级；**不改表**）")
    out("=" * 78)
    if not derivation.supplementary_probe:
        out("  索引库未构建 → 跳过（不影响上面的表）")
    for entry in derivation.supplementary_probe:
        level = entry.get("tier_level")
        level_text = f"{level} 级 → {level + 1} 级" if isinstance(level, int) else "不在技能档里"
        out(f"  {entry['skill']}（{entry['type_id']}）{level_text}：")
        if not entry["changed"]:
            out("      （无属性变化）")
            continue
        for change in entry["changed"]:
            finding = derivation.findings.get(change["attribute_id"])
            current = finding.state if finding is not None else "（不在表里）"
            out(
                "      {} {}  {} → {}   （当前判定：{}）".format(
                    f"{change['attribute_id']:5d}",
                    change["name"],
                    _num(change["baseline"]["ship"])
                    if change["baseline"]["ship"] is not None
                    else "—",
                    _num(change["with_skill"]["ship"])
                    if change["with_skill"]["ship"] is not None
                    else "—",
                    current,
                )
            )
    out("  说明：技能轴的局限从「覆盖哪些技能」变成「等级只扫 4 级 vs 0 级」。上面这些")
    out("        属性在 4 → 0 时就已经变化、被判 assumed，所以**等级上限只影响幅度、不影响")
    out("        三态**。要改成「等级也扫」得先改轴的定义（另一个任务），不能在表里手工拍。")

    out("")
    out("=" * 78)
    out("七、统计")
    out("=" * 78)
    scanned = derivation.counts(registered_only=False)
    registered = derivation.counts(registered_only=True)
    out(
        "  扫描全域（含未登记）  measured {:3d} / assumed {:3d} / unknown {:3d}  合计 {:3d}".format(
            scanned[MEASURED], scanned[ASSUMED], scanned[UNKNOWN], len(derivation.findings)
        )
    )
    out(
        "  已登记常量（产品表）  measured {:3d} / assumed {:3d} / unknown {:3d}  合计 {:3d}"
        "（unknown 不含 {} 条手工覆盖）".format(
            registered[MEASURED],
            registered[ASSUMED],
            registered[UNKNOWN],
            sum(registered.values()),
            len(OVERRIDES),
        )
    )
    reasons = derivation.assumed_by_reason()
    out(
        "  assumed 归因         负ID {} / 技能 {} / 状态 {} / 剖面 {} / 覆盖 {}".format(
            reasons[REASON_NEGATIVE],
            reasons[REASON_SKILL],
            reasons[REASON_STATE],
            reasons[REASON_PROFILE],
            reasons[REASON_OVERRIDE],
        )
    )
    out("")
    out("  T-14 验收点：")
    for attribute_id in (
        1367,
        281,
        1272,
        -14,
        -58,
        -55,
        -56,
        -57,
        -59,
        -60,
        -61,
        -62,
        -70,
    ):
        finding = derivation.findings.get(attribute_id)
        if finding is None:  # pragma: no cover - 常量表变动时的兜底
            out(f"    {attribute_id:6d}  （不在扫描集合里）")
            continue
        out(f"    {attribute_id:6d}  {finding.state:8s}  {finding.reason:26s}  {finding.name}")
    out("")
    out("  正 ID 共振率（109–113、267–274）必须仍是 measured：")
    resonance = (109, 110, 111, 113, 267, 268, 269, 270, 271, 272, 273, 274)
    out(
        "    "
        + " ".join(
            f"{aid}:{derivation.findings[aid].state}" for aid in resonance if aid in derivation.findings
        )
    )


# =============================================================================
# 八、输出：证据 JSON
# =============================================================================


def write_report(derivation: Derivation, path: Path = REPORT_PATH) -> Path:
    payload = derivation.as_json()
    payload["legend"] = {
        "measured": "至少在一个样本里出现，且在出现它的每个样本上三轴都不变",
        "assumed": "依赖技能档、模块状态、模式或伤害剖面之一（或负 ID 派生值）",
        "unknown": "全部样本里都不存在（tag() 的默认兜底）",
        "rules": [
            "1) 手工覆盖（理由见 provenance._MANUAL_OVERRIDES）",
            "2) 属性 ID 为负 → assumed（一律）",
            "3) 任一轴的任一样本上变化 → assumed",
            "4) 至少一个样本里出现且三轴不变 → measured",
            "5) 全部样本都不存在 → unknown",
        ],
        "reasons": REASON_LABEL,
        "scope": "只把 config/attributes.py 已登记常量名的属性写进产品表（红线 N12）",
        "overrides": {str(aid): why for aid, (_state, why) in OVERRIDES.items()},
        "skill_tier_guard": (
            f"技能档名必须是 {SKILL_TIER_NAME}，技能数必须 > {MIN_SKILL_TIER_SIZE}，"
            "否则本脚本抛 RuntimeError（缺陷 A 的护栏）"
        ),
        "plan": "SDE / 引擎 / 样本变更后重跑本脚本（它会同时重写 provenance.py）",
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return path


# =============================================================================
# 九、输出：重写 product 表（`project/provenance.py`）
# =============================================================================


def _measured_entries(findings: list[Finding], names: dict[int, str], total: int) -> list[str]:
    entries: list[str] = []
    for finding in findings:
        name = names.get(finding.attribute_id)
        if name is None:  # pragma: no cover - 产品表只收盘内常量
            continue
        entries.append(
            f"        _a.{name},  # {finding.attribute_id}  {finding.name}"
            f"  （{len(finding.present_in)}/{total} 个样本出现，三轴不变）"
        )
    return entries


def _assumed_entries(
    derivation: Derivation, names: dict[int, str]
) -> list[str]:
    entries: list[str] = []
    total = len(derivation.runs)
    for reason in REASON_ORDER:
        group = [f for f in derivation.registered(ASSUMED) if f.reason == reason]
        if not group:
            continue
        entries.append("        # ---- " + REASON_LABEL[reason] + f"：{len(group)} 个 ----")
        for finding in group:
            name = names.get(finding.attribute_id)
            if name is None:  # pragma: no cover
                continue
            suffix = finding.trigger_text()
            if not suffix:
                suffix = (
                    f"负 ID：pass 4 派生值，一律 assumed；{len(finding.present_in)}/{total} 个样本里出现"
                    if reason == REASON_NEGATIVE
                    else f"{len(finding.present_in)}/{total} 个样本里出现"
                )
            entries.append(
                f"        _a.{name},  # {finding.attribute_id}  {finding.name}  ← {suffix}"
            )
    return entries


def _unknown_entries(findings: list[Finding], names: dict[int, str], total: int) -> list[str]:
    entries: list[str] = []
    for finding in findings:
        name = names.get(finding.attribute_id)
        if name is None:  # pragma: no cover
            continue
        note = finding.note or f"{total} 个样本里都没有这个键"
        entries.append(f"        _a.{name},  # {finding.attribute_id}  {finding.name}  （{note}）")
    return entries


def _set_block(entries: list[str], *, union_overrides: bool = False) -> str:
    lines = ["    set(_MANUAL_OVERRIDES) | {"] if union_overrides else ["    {"]
    lines.extend(entries)
    lines.append("    }")
    return "\n".join(lines)


def _replace_set(source: str, name: str, body: str) -> str:
    pattern = re.compile(
        rf"^{name}: frozenset\[int\] = frozenset\(\n(?:.*?\n)\)\n",
        re.MULTILINE | re.DOTALL,
    )
    if not pattern.search(source):  # pragma: no cover - 模板被改动时的护栏
        raise RuntimeError(f"provenance.py 里找不到 `{name}` 的生成块（模板被改了？）")
    return pattern.sub(
        lambda _match: f"{name}: frozenset[int] = frozenset(\n{body}\n)\n", source, count=1
    )


def _replace_literal(source: str, pattern: str, replacement: str) -> str:
    regex = re.compile(pattern, re.MULTILINE)
    if not regex.search(source):  # pragma: no cover - 模板被改动时的护栏
        raise RuntimeError(f"provenance.py 里找不到要替换的元信息行（pattern={pattern!r}）")
    return regex.sub(lambda _match: replacement, source, count=1)


def write_product_module(derivation: Derivation, path: Path = PROVENANCE_PATH) -> Path:
    """把三张表与 `DERIVED_AT` / `DERIVED_SAMPLE(S)` 写回 `project/provenance.py`。

    **只有本脚本写表**（人不手改表内容）；`_MANUAL_OVERRIDES` 与理由、`audit()` 实现
    是人维护的部分，本函数不碰。写盘前先 `compile()` 做语法护栏。
    """
    names = _constant_names_by_id()
    total = len(derivation.runs)
    source = path.read_text(encoding="utf-8")

    source = _replace_set(
        source,
        "MEASURED_ATTRS",
        _set_block(
            _measured_entries(derivation.registered(MEASURED), names, total)
            or ["        # （空）"]
        ),
    )
    source = _replace_set(
        source,
        "ASSUMED_ATTRS",
        _set_block(_assumed_entries(derivation, names) or ["        # （空）"]),
    )
    source = _replace_set(
        source,
        "UNKNOWN_ATTRS",
        _set_block(
            _unknown_entries(derivation.registered(UNKNOWN), names, total) or ["        # （空）"],
            union_overrides=True,
        ),
    )
    source = _replace_literal(
        source,
        r'^DERIVED_AT: str = ".*?"$',
        f'DERIVED_AT: str = "{derivation.derived_at}"',
    )
    source = _replace_literal(
        source,
        r"^DERIVED_SAMPLE: str = .*$",
        "DERIVED_SAMPLE: str = " + json.dumps(derivation.sample_summary, ensure_ascii=False),
    )
    source = _replace_literal(
        source,
        r"^DERIVED_SAMPLES: tuple\[str, \.\.\.\] = .*$",
        "DERIVED_SAMPLES: tuple[str, ...] = ("
        + ", ".join(json.dumps(run.sample.label, ensure_ascii=False) for run in derivation.runs)
        + ")",
    )

    compile(source, str(path), "exec")  # 语法护栏：生成物坏了就炸，不写盘
    path.write_text(source, encoding="utf-8", newline="\n")
    return path


# =============================================================================
# 十、入口
# =============================================================================


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，中文会乱码
    except (AttributeError, OSError):  # pragma: no cover - 非 tty / 已配置
        pass

    out = print
    engine = Engine()
    derivation = derive(engine)

    out("=" * 78)
    out("T-14 · provenance 多样本 × 三轴敏感性实验（重推三态表）")
    out("=" * 78)
    out(f"样本      ：{derivation.sample_summary}")
    out(f"样本数    ：{len(derivation.runs)}（至少 4：基础 / 子系统 / 导弹 / 战斗机+无人机）")
    out(f"引擎/SDE  ：{derivation.meta}")
    out(f"跑表日期  ：{derivation.derived_at}")
    out("")
    _print_table(out, derivation)

    report = write_report(derivation)
    module = write_product_module(derivation)
    out("")
    out(f"证据已写入：{report.as_posix()}")
    out(f"三态表已重写：{module.as_posix()}（写盘前已过 compile() 语法护栏）")
    out("")
    out("人的动作（脚本只写表，不写理由）：")
    out("  1. `git diff evefit/project/provenance.py` 逐条复核三张表的变化；")
    out("  2. 手工覆盖逐条写理由（目前只有 -70，见 OVERRIDES / _MANUAL_OVERRIDES）；")
    out("  3. 跑 pytest（tests/test_projection_core.py 有 provenance 护栏断言）。")
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
