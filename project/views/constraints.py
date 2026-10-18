"""视角五 · 约束 `constraints`（字段规格 §5）。

本视角只做**分类与聚合**，不解方程、不猜：

1. **违规必须二分**（§5「分类映射」）：
   - `rule.type` ∈ {`resource`, `skill`} → `tier_violations`：**当前技能档下**装不下/技能不够，
     是**可解**的档位问题（挂号技能 / 上脑插 / 换低配三条路径）；
   - 其余（`wrong_slot` / `slot_taken` / `subsystem_taken` / `rig_size` / `ship_restricted` /
     `capital_item` / `max_group` / `max_type` / `charge_group` / `charge_size` …）→
     `hard_violations`：与技能无关的装配错误。
2. **措辞禁令**：不得输出「配装非法」之类的判词，只能表述为「当前档位下超载 X」。
   本模块的 `message` 一律是「当前档位下超载：CPU 占用 287 / 上限 276，超出 11.0 tf」
   这类**带具体数值的陈述**。
3. `skill_floor` 由各模块 `requiredSkill1..6` + 等级**聚合**得出，不做任何求解：
   `max_level`、`count_by_level`（各等级**去重后**的技能个数）、`distinct_skills`。
   **属性 ID 不连号**（硬结论 28）：`requiredSkill1/2/3 = 182/183/184`、
   `4/5/6 = 1285/1289/1290`；等级 `1/2/3 = 277/278/279`、`4/5/6 = 1286/1287/1288`。
4. `needs_implant`：用**全 5 档**重算，若仍存在 `resource` 类违规 → `True` 并给出缺口。
   （工程技能全 5 是玩家基础，这条判据已由主人定，不再讨论。）本判据**属推断**，
   字段规格 §9 要求标注，故附一句 `needs_implant_basis` 文字依据。

`flags` 恒为 `[]`：可疑点由渲染层统一算一次（`diagnostics.evaluate`）。

provenance 归属（`FIELD_SOURCES`，T-34 补齐）
-------------------------------------------
本视角**没有一个字段是属性直读**：违规条目是引擎 `validate()` 的结构化结论 + 本视角的
归一化（`kind` / `message` / `shortfall` 都是我们算的），门槛画像是 12 个 `requiredSkill*`
的聚合，植入体判据是「全 5 档重算」的推断 → 一律登记 `None`（渲染层判 `assumed`：
「依赖我们的计算逻辑」，既不是某个输入属性的实测，也不是「不可知」）。

`tier_violations` 额外登记**容器**：`rule.*` 的键集合由引擎**按违规类型**决定，逐键枚举
会在引擎新增一种违规时静默退回 `unknown`；容器来源会被 `render._tag()` 继承给每个元素。
"""

from __future__ import annotations

from evefit.config import attributes as A
from evefit.project import units
from evefit.project.views import register
from evefit.project.views.base import BASE, SLOT_CARGO, ViewContext

# =============================================================================
# 规则分类
# =============================================================================

TIER_RULE_TYPES: frozenset[str] = frozenset({"resource", "skill"})
"""「档位违规」的规则类型：当前技能档下可解的资源/技能缺口（其余都是硬违规）。"""

RESOURCE_ATTRS: dict[str, int] = {
    "cpu": A.ATTR_CPU_LOAD,
    "powergrid": A.ATTR_POWER_LOAD,
    "calibration": A.ATTR_CALIBRATION_LOAD,
}
"""引擎 `rule.resource` 名 → 该资源的占用属性（只用来取单位标签，不重算数值）。"""

RESOURCE_LABELS: dict[str, str] = {
    "cpu": "CPU",
    "powergrid": "PG",
    "calibration": "标定",
}
"""`rule.resource` 名 → 人话里的名字（未知资源直接回退到原名）。"""

MAX_SKILL_LEVEL: int = 5
"""全 5 档（`needs_implant` 的判据）：工程技能全 5 是玩家基础。"""

RESOURCE_UNIT_OVERRIDES: dict[str, str] = {"calibration": "点"}
"""常量表里 `calibration` 的单位标签是 SDE 的量纲名（`calibration`），对外用「点」更好读。"""

# 技能要求属性（**不连号**，硬结论 28）：(技能 ID 属性, 等级属性)
SKILL_REQUIREMENT_PAIRS: tuple[tuple[int, int], ...] = (
    (A.ATTR_REQUIRED_SKILL_1, A.ATTR_REQUIRED_SKILL_1_LEVEL),
    (A.ATTR_REQUIRED_SKILL_2, A.ATTR_REQUIRED_SKILL_2_LEVEL),
    (A.ATTR_REQUIRED_SKILL_3, A.ATTR_REQUIRED_SKILL_3_LEVEL),
    (A.ATTR_REQUIRED_SKILL_4, A.ATTR_REQUIRED_SKILL_4_LEVEL),
    (A.ATTR_REQUIRED_SKILL_5, A.ATTR_REQUIRED_SKILL_5_LEVEL),
    (A.ATTR_REQUIRED_SKILL_6, A.ATTR_REQUIRED_SKILL_6_LEVEL),
)

# =============================================================================
# 字段 → 属性 ID
# =============================================================================

FIELD_SOURCES: dict[str, int | None] = {
    # 违规清单是引擎 `validate()` 的结构化结论 + 本视角的归一化 → 整块来源 None。
    #
    # **登记容器而不是逐个 `[]` 元素**（T-34）：`rule.*` 的键集合由引擎按违规类型决定
    # （resource 类 → `type`/`resource`/`used`/`available`；skill 类 → `type`/`type_id`/
    # `required`/`level`），逐键枚举会在引擎新增一种违规类型时**静默退回 `unknown`**
    # —— 那正是 T-32 里 `tier_violations[].rule.*` 被兜成 `not_applicable` 的成因
    # （「把确定的事说成不知道」与「说成不适用」是同一类错误）。
    # 渲染层 `render._tag()` 的容器回退会把容器来源继承给每个元素子字段。
    "hard_violations": None,
    "tier_violations": None,
    # 条目里**不是属性直读**的元素字段逐条登记（路标：让「这条为什么是 assumed」有据可查，
    # 同时钉住「哪些字段是我们合成的」）：
    # `kind` 由 `_classify()` 二分得出；`message` 由 `_tier_resource()` / `_tier_skill()`
    # 拼出来；`resource` 是引擎规则里的资源**名**（枚举，不是属性值）；
    # `skill_type_id` / `skill_name` 是引擎规则里的 type_id 与查表得来的技能名。
    "tier_violations[].kind": None,
    "tier_violations[].resource": None,
    "tier_violations[].message": None,
    "tier_violations[].skill_type_id": None,
    "tier_violations[].skill_name": None,
    # 数值键仍逐条登记：数值叶子覆盖率断言要求**精确路径**（容器回退不参与那条断言），
    # 且它们与容器同源（引擎规则里的 used/available/required/level；另一类违规由
    # `_tier_base()` 先置 None）。
    "tier_violations[].used": None,
    "tier_violations[].available": None,
    "tier_violations[].shortfall": None,
    "tier_violations[].required": None,
    "tier_violations[].level": None,
    "tier_violations[].missing_levels": None,
    # 门槛画像是 12 个 requiredSkill* 属性的聚合值 → 合成，填 None
    "skill_floor.max_level": None,
    "skill_floor.count_by_level": None,
    "skill_floor.distinct_skills": None,
    # 植入体判据是「全 5 档重算」的结论，属推断（字段规格 §9）
    "needs_implant": None,
    "needs_implant_basis": None,
}
"""overview 点路径 → 来源属性 ID（渲染层据此打 provenance）。

四条约定：

1. **违规条目**是引擎判定的结构化结论（不是单一属性直读）→ 来源 `None`；
   **登记容器**（`tier_violations`）让 `rule.*` 这类「键集合由引擎决定」的元素子字段
   有来源可继承 —— 这正是 T-34 修的欠账（此前它们查不到映射，落 `unknown` 后被
   `reasons.py` 兜成 `not_applicable`，把「我们自己算出来的确定值」说成了「不适用」）；
2. **字符串 / 布尔叶子**（`needs_implant`、`needs_implant_basis`）不是属性直读，
   登记 `None`——渲染层判 `assumed`（多属性/多规则合成），**不冒充** `measured`，
   也不让它们落成 `unknown`（红线 N4 的另一半：登记过就得有来源，没有就说没有）；
3. **`skill_floor.count_by_level` 是整数键字典**（`{等级: 技能个数}`），对外点路径
   约定为 `skill_floor.count_by_level.<level>`（如 `skill_floor.count_by_level.4`）。
   等级集合由**实际数据**决定（技能档/模块要求里出现过的等级），**不硬编码等级列表**：
   容器路径上登记的 `None`（同一来源：12 个 `requiredSkill*` 属性的聚合）会被渲染层
   逐级继承给每个 `<level>` 叶子，见 `render._tag()` 的整数键容器回退。
"""


# =============================================================================
# 小工具
# =============================================================================


def _fmt(value: float | None) -> float | None:
    """浮点去噪（`round(x, 3)`）；`None` 原样传递。"""
    return None if value is None else units.fmt(value)


def _fitted(items: list[dict]) -> list[tuple[int, dict]]:
    """已装模块（排除货舱）；下标与 `ctx.fit["items"]` 对应（硬结论 44）。"""
    return [
        (position, item)
        for position, item in enumerate(items)
        if item.get("slot_type") != SLOT_CARGO
    ]


def _resource_label(resource: object) -> str:
    """`rule.resource` → 人话标签（未知资源回退原名，不隐藏信息）。"""
    if not isinstance(resource, str):
        return "资源"
    return RESOURCE_LABELS.get(resource, resource)


def _resource_unit(resource: object) -> str | None:
    """`rule.resource` → 单位标签（取自常量表，不另写一套单位）。"""
    if not isinstance(resource, str):
        return None
    if resource in RESOURCE_UNIT_OVERRIDES:
        return RESOURCE_UNIT_OVERRIDES[resource]
    attribute_id = RESOURCE_ATTRS.get(resource)
    if attribute_id is None:
        return None
    return A.UNIT_BY_ATTR.get(attribute_id)


def _skill_name(ctx: ViewContext, skill_type_id: object) -> str | None:
    """技能 type_id → 名字（没有索引时 `None`，不编造）。"""
    if ctx.index is None or not isinstance(skill_type_id, int):
        return None
    row = ctx.index.get_type(skill_type_id)
    return (row or {}).get("name")


def _quantity(rule: dict, key: str) -> float | None:
    """取 `rule` 里的数值字段；不是数就当缺失（引擎结构变化时宁可空着）。"""
    value = rule.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# =============================================================================
# 违规二分
# =============================================================================


def _iter_violations(violations: list[dict]) -> list[tuple[dict, dict]]:
    """把引擎 `violations` 拆成 `(target, rule)`；结构不对的条目跳过（不猜）。"""
    parsed: list[tuple[dict, dict]] = []
    for violation in violations:
        if not isinstance(violation, dict):
            continue
        rule = violation.get("rule")
        if not isinstance(rule, dict):
            continue
        target = violation.get("target")
        parsed.append((dict(target) if isinstance(target, dict) else {}, rule))
    return parsed


TIER_NUMERIC_KEYS: tuple[str, ...] = (
    "used",
    "available",
    "shortfall",
    "required",
    "level",
    "missing_levels",
)
"""档位违规条目里**恒定存在**的数值键。

不管这条是资源类还是技能类，两类键都出现在条目里（不适用的那组为 `None`），
这样 `FIELD_SOURCES` 的点路径在任何样本上都可达，渲染层不必猜「这条有没有这个键」。
"""


def _tier_base(rule: dict) -> dict:
    """档位违规条目的公共骨架（数值键全部先置 `None`，缺值不补 0）。"""
    return {
        "kind": None,
        "rule": rule,
        "resource": None,
        "skill_type_id": None,
        "skill_name": None,
        **{key: None for key in TIER_NUMERIC_KEYS},
        "message": "",
    }


def _tier_resource(rule: dict) -> dict:
    """资源类档位违规：带 used / available / shortfall 三个具体数值。"""
    used = _quantity(rule, "used")
    available = _quantity(rule, "available")
    shortfall = None
    if used is not None and available is not None:
        shortfall = max(used - available, 0.0)

    label = _resource_label(rule.get("resource"))
    unit_text = _resource_unit(rule.get("resource"))
    if used is not None and available is not None and shortfall is not None:
        message = (
            f"当前档位下超载：{label} 占用 {units.fmt(used)} / 上限 {units.fmt(available)}，"
            f"超出 {units.fmt(shortfall)}"
        )
        if unit_text:
            message += f" {unit_text}"
    else:
        message = f"当前档位下超载：{label}（引擎未给 used/available 明细）"

    return {
        **_tier_base(rule),
        "kind": "resource",
        "resource": rule.get("resource"),
        "used": _fmt(used),
        "available": _fmt(available),
        "shortfall": _fmt(shortfall),
        "message": message,
    }


def _tier_skill(ctx: ViewContext, rule: dict) -> dict:
    """技能类档位违规：带 required / level / missing_levels，并给出技能名。"""
    required = _quantity(rule, "required")
    level = _quantity(rule, "level")
    missing = None
    if required is not None and level is not None:
        missing = max(required - level, 0.0)

    skill_type_id = rule.get("type_id")
    name = _skill_name(ctx, skill_type_id)
    label = name if name else f"技能 {skill_type_id}"
    if required is not None and level is not None:
        message = (
            f"当前档位下技能不足：{label} 需要 {int(round(required))} 级，"
            f"当前 {int(round(level))} 级"
        )
    else:
        message = f"当前档位下技能不足：{label}（引擎未给等级明细）"

    return {
        **_tier_base(rule),
        "kind": "skill",
        "skill_type_id": skill_type_id if isinstance(skill_type_id, int) else None,
        "skill_name": name,
        "required": None if required is None else int(round(required)),
        "level": None if level is None else int(round(level)),
        "missing_levels": None if missing is None else int(round(missing)),
        "message": message,
    }


def _describe_hard(rule: dict) -> str:
    """硬违规的人话描述：只陈述事实，不写「非法」这类判词。"""
    rule_type = rule.get("type")
    details = "、".join(
        f"{key}={value}"
        for key, value in sorted(rule.items())
        if key != "type" and not isinstance(value, (dict, list))
    )
    head = f"与技能无关的装配错误：{rule_type}" if isinstance(rule_type, str) else "与技能无关的装配错误"
    return f"{head}（{details}）" if details else head


def _classify(ctx: ViewContext, violations: list[dict]) -> tuple[list[dict], list[dict]]:
    """`(hard_violations, tier_violations)`：按 `rule.type` 二分，各自保留原 `rule` 结构。"""
    hard: list[dict] = []
    tier: list[dict] = []
    for target, rule in _iter_violations(violations):
        rule_type = rule.get("type")
        if isinstance(rule_type, str) and rule_type in TIER_RULE_TYPES:
            if rule_type == "resource":
                tier.append(_tier_resource(rule))
            else:
                tier.append(_tier_skill(ctx, rule))
            continue
        hard.append({"rule": rule, "target": target, "message": _describe_hard(rule)})
    return hard, tier


# =============================================================================
# 技能门槛画像
# =============================================================================


def _skill_requirements(fitted: list[tuple[int, dict]]) -> dict[int, int]:
    """各模块 `requiredSkill1..6` + 等级 → `{skill_type_id: 最高需求等级}`（聚合）。

    去重口径：同一技能被多个模块要求时取**最高**等级（这就是「门槛」，加总没有意义）；
    `count_by_level` 因此数的是**去重后的技能个数**，不是「模块×技能」条目数。
    """
    requirements: dict[int, int] = {}
    for _position, item in fitted:
        attributes = item["attributes"]
        for skill_attr, level_attr in SKILL_REQUIREMENT_PAIRS:
            skill_type_id = attributes.get(skill_attr)
            level = attributes.get(level_attr)
            if skill_type_id is None or level is None:
                continue
            level_value = int(round(float(level)))
            if level_value <= 0:
                continue
            skill_id = int(round(float(skill_type_id)))
            if level_value > requirements.get(skill_id, 0):
                requirements[skill_id] = level_value
    return requirements


def _skill_floor(requirements: dict[int, int]) -> dict:
    """门槛画像：`max_level` / `count_by_level` / `distinct_skills`（不含技能名清单）。"""
    count_by_level: dict[int, int] = {}
    for level in requirements.values():
        count_by_level[level] = count_by_level.get(level, 0) + 1
    return {
        "max_level": max(requirements.values()) if requirements else None,
        "count_by_level": dict(sorted(count_by_level.items())),
        "distinct_skills": len(requirements),
    }


# =============================================================================
# 植入体判据
# =============================================================================


def _all_five_skills(
    ctx: ViewContext,
    requirements: dict[int, int],
    current_violations: list[dict],
) -> dict[int, int]:
    """全 5 档：当前技能档 ∪ 模块要求 ∪ 引擎报出的技能需求，全部拉到 5 级。

    第三个来源是为了覆盖**船体技能**（`requiredSkill*` 只在模块属性里，船体没有对应项）：
    当前档位若缺船体技能，`validate` 会以 skill 违规报出来，取它的 `type_id` 即可，
    不必另找「船体需要什么技能」的查询接口。
    """
    tier = ctx.assumptions.get("skill_tier") or {}
    skill_ids = {int(skill_id) for skill_id in tier} | set(requirements)
    for _target, rule in _iter_violations(current_violations):
        if rule.get("type") == "skill" and isinstance(rule.get("type_id"), int):
            skill_ids.add(int(rule["type_id"]))
    return {skill_id: MAX_SKILL_LEVEL for skill_id in skill_ids}


def _needs_implant(
    ctx: ViewContext,
    requirements: dict[int, int],
    current_violations: list[dict],
) -> tuple[bool, str]:
    """全 5 档仍出现 `resource` 类违规 → 需要植入体支持（推断，附文字依据）。"""
    all_five = _all_five_skills(ctx, requirements, current_violations)
    character = dict(ctx.fit.get("character") or {})
    character["skills"] = all_five
    violations = ctx.engine.validate({**ctx.fit, "character": character})

    _hard_violations, tier_violations = _classify(ctx, violations)  # 硬违规与植入体无关
    gaps = [entry for entry in tier_violations if entry.get("kind") == "resource"]
    if not gaps:
        return False, "全 5 档下无资源缺口（该结论属推断：植入体影响不可知）"

    parts: list[str] = []
    for gap in gaps:
        label = _resource_label(gap.get("resource"))
        unit_text = _resource_unit(gap.get("resource"))
        shortfall = gap.get("shortfall")
        if shortfall is None:
            parts.append(f"{label} 仍有缺口")
            continue
        text = f"{label} 超出 {shortfall}"
        if unit_text:
            text += f" {unit_text}"
        parts.append(text)
    return True, "全 5 档仍超载：" + "；".join(parts) + "（属推断，不可证实）"


# =============================================================================
# 视图
# =============================================================================


@register("constraints")
def build(ctx: ViewContext) -> dict:
    """约束视角：硬违规 / 档位违规 / 技能门槛 / 植入体判据。`flags` 恒为空。"""
    violations = ctx.engine.validate(ctx.fit)
    hard_violations, tier_violations = _classify(ctx, violations)

    fitted = _fitted(ctx.items(BASE))
    requirements = _skill_requirements(fitted)
    needs_implant, needs_implant_basis = _needs_implant(ctx, requirements, violations)

    overview = {
        "hard_violations": hard_violations,
        "tier_violations": tier_violations,
        "skill_floor": _skill_floor(requirements),
        "needs_implant": needs_implant,
        "needs_implant_basis": needs_implant_basis,
    }
    return {"view": "constraints", "overview": overview, "flags": []}
