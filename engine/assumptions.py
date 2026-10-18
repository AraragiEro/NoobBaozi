"""假设推定（L2）：技能档 / 模块状态 / 模式 / 伤害剖面，全部显式记录。

设计依据：软件设计 §7.2「技能档推导只在 L2 写一次」，字段规格 §0.5「默认假设」。

**技能档算法（T-12 修正，PROGRESS §3 第 62 条 / §5 欠账 18）**：主人的约定是
「**全部技能 4 级** + 逐模块补到 `max(4, required)`」，不是只补「被模块要求」的那几个。
做法是拿 `validate` 的 `violations` 当反馈回路：

    skills = {type_id: 4 for type_id in index.skills()}   # 基线：全部已发布技能
    loop:
        violations = engine.validate({**fit, "character": {"skills": skills}})
        reqs = [rule for rule in violations if rule["type"] == "skill"]
        若没有技能类违规 → 收敛，返回 skills
        否则把每个 type_id 设为 max(4, required)；一轮没有任何变化 → 收敛
    超过 MAX_SKILL_ROUNDS 轮仍未收敛 → 抛 RuntimeError（绝不静默返回半成品）

为什么要迭代：技能会改变资源（PG/CPU/标定），进而触发新的技能违规（例如 `required`
的技能本身占 PG），所以「刷一遍要求」不等于收敛。

**两种档位必须可区分（红线 N4）**：`index=None` 时拿不到「全部技能」，
`derive_skill_tier` **退化为旧行为**（只补被模块要求的技能）。这时
`default_assumptions()["skill_tier_name"]` 是 `SKILL_TIER_NAME_MIN_ONLY`，
绝不会用旧档冒充新档；只有传了 `index` 才是 `SKILL_TIER_NAME`。

实测（SDE build 3607705 / 引擎 13.6.0，样本 `spike/kill.json`）：

- 全 4 档（`index` 传入）：512 个已发布技能 → 510×4 + 2×5（两个技能最低要 5 级）；
- 最低要求档（`index=None`，旧行为）：19 个技能 = 17×4 + 2×5。

`module_states` 只记录**状态来源**（统一 `engine_default`），不记录具体状态值：
具体状态是引擎的导入规则给的，投影层要具体值时应读 `calculate` 结果的 `items[i].state`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from noobbaozi import contracts

if TYPE_CHECKING:  # 只为类型标注，运行时不产生 import 依赖
    from noobbaozi.engine.adapter import Engine
    from noobbaozi.sde.index import Index

DEFAULT_SKILL_LEVEL = 4
"""全员技能 4 级（玩家最经济的挂法）。"""

MAX_SKILL_ROUNDS = 8
"""技能档反馈回路的上限轮数；两种档位实测都是 2 轮收敛。超限说明引擎规则变了，必须炸出来。"""

SKILL_TIER_NAME = "all4+min_required"
"""新档位的名字（传了 `index`）：全部技能 4 级 + 补最低要求。"""

SKILL_TIER_NAME_MIN_ONLY = "min_required_only"
"""退化档位的名字（`index=None`）：只补被模块要求的技能（旧行为）。"""

MODULE_STATE_SOURCE = "engine_default"
"""模块状态的来源标记：由引擎导入规则推定，不是输入里给出的。"""

SLOT_CATEGORIES = ("high", "medium", "low", "rig")
"""本阶段记录状态来源的槽位类别（`slot.type` 的合法值见软件设计 §7.3）。"""

DEFAULT_DAMAGE_PROFILE = {
    "em": 0.25,
    "explosive": 0.25,
    "kinetic": 0.25,
    "thermal": 0.25,
}
"""25/25/25/25 均匀剖面（字段规格 §0.5）；**不是**唯一 EHP 输出，投影层要出四系。"""


def _with_skills(fit: dict, skills: dict[int, int]) -> dict:
    """返回带技能集的 fit 副本（不改动入参）。"""
    character = dict(fit.get("character") or {})
    character["skills"] = dict(skills)
    return {**fit, "character": character}


def _skill_rules(violations: list[dict]) -> list[dict]:
    """从 `violations` 里挑出技能类规则。"""
    rules: list[dict] = []
    for violation in violations:
        rule = violation.get("rule") if isinstance(violation, dict) else None
        if isinstance(rule, dict) and rule.get("type") == "skill":
            rules.append(rule)
    return rules


def _baseline_skills(index: "Index | None") -> dict[int, int]:
    """基线技能档：给了索引 → 全部已发布技能 4 级（`Index.skills()`，category 16）；没给 → 空。"""
    if index is None:
        return {}
    return {int(type_id): DEFAULT_SKILL_LEVEL for type_id in index.skills()}


def derive_skill_tier(
    fit: dict, engine: "Engine", index: "Index | None" = None
) -> dict[int, int]:
    """推导技能档：全部技能 4 级 + 逐模块补到 `max(4, required)`。

    Args:
        fit: 归一化后的配装；**不修改**入参。
        engine: 计算引擎（只用它的 `validate`）。
        index: SDE 索引。给了才取「全部技能」做基线（新语义）；`None` 时退回旧行为
            （只补被模块 `requiredSkillN` 要求的技能）。两者绝不静默互换：档位名字由
            `default_assumptions()["skill_tier_name"]` 区分。

    Returns:
        `{skill_type_id: level}`，未收敛时**不返回**半成品。

    Raises:
        RuntimeError: 技能类违规结构不符预期（缺 `type_id` / `required`），
            或 `MAX_SKILL_ROUNDS` 轮内未收敛。
    """
    skills = _baseline_skills(index)

    for _ in range(MAX_SKILL_ROUNDS):
        violations = engine.validate(_with_skills(fit, skills))
        rules = _skill_rules(violations)
        if not rules:
            return skills

        changed = False
        for rule in rules:
            type_id = rule.get("type_id")
            required = rule.get("required")
            if not isinstance(type_id, int) or not isinstance(required, int):
                raise RuntimeError(
                    f"技能类违规的结构不符合预期（type_id={type_id!r}, required={required!r}），"
                    "引擎规则可能已变更"
                )
            want = max(DEFAULT_SKILL_LEVEL, required)
            if skills.get(type_id, 0) < want:
                skills[type_id] = want
                changed = True

        if not changed:
            # 违规仍在但要求没有提高：说明这些技能已经满足，属于收敛点以外的噪声
            return skills

    raise RuntimeError(
        f"技能档推导在 {MAX_SKILL_ROUNDS} 轮内未收敛（最后得到 {len(skills)} 个技能），"
        "不返回半成品"
    )


def derive_module_states() -> dict[str, str]:
    """按槽位类别记录状态来源（本阶段统一 `engine_default`）。

    固定输出 `high/medium/low/rig` 四类；具体状态值请读 `calculate` 结果的
    `items[i].state`，这里只声明「这些值都是引擎给的，不是用户给的」。
    """
    return {category: MODULE_STATE_SOURCE for category in SLOT_CATEGORIES}


def derive_mode_id(fit: dict) -> int | None:
    """`fit["ship"]["mode"]` 存在则用，否则 None（模式是独立 type，见 PROGRESS §3 第 3 条）。"""
    ship = fit.get("ship") or {}
    mode = ship.get("mode") if isinstance(ship, dict) else None
    return int(mode) if isinstance(mode, int) else None


def default_assumptions(
    fit: dict, engine: "Engine", index: "Index | None" = None
) -> contracts.Assumptions:
    """返回默认假设集合（每项都对应字段规格 §0.5 的一行）。

    `index` 原样透传给 `derive_skill_tier`：
    传了 → 技能档 `SKILL_TIER_NAME`（全部技能 4 级 + 补最低要求）；
    没传 → `SKILL_TIER_NAME_MIN_ONLY`（只补最低要求，旧行为），
    名字不同就是让人一眼看出这**不是**主人的约定档，不能拿去冒充（红线 N4）。
    """
    return {
        "skill_tier": derive_skill_tier(fit, engine, index),
        "skill_tier_name": SKILL_TIER_NAME if index is not None else SKILL_TIER_NAME_MIN_ONLY,
        "module_states": derive_module_states(),
        "mode_id": derive_mode_id(fit),
        "damage_profile": dict(DEFAULT_DAMAGE_PROFILE),
        "ammo_loaded": True,
        "implants": "none",
    }
