"""实测推导 provenance 三态表（T-08）。

背景（第 16 轮裁决 + 字段规格 §0.2）：**provenance 只表示「这个数在本次计算里是否
依赖假设」**，与「可信度 A–E」是正交维度。第 16 轮之前那张手写分类表把两者混在
一起，与验收员的 `field_spec.json` 冲突了 7 处。这个脚本把分类表从「拍脑袋」改成
「跑出来」：对样本配装做**三轴敏感性扫描**，逐属性判定它是否随假设变化。

    轴        变体 A（基准）                        变体 B
    技能      derive_skill_tier（全 4 + 补最低）      全部技能 0（空 skills）
    状态      全 active（= 引擎默认，见下）           全 online（{"all": "online"}）
    伤害剖面  25/25/25/25（默认）                     纯 EM（em=1 其余 0）

判定规则（**按顺序，命中即停**）：

1. 该属性在基准变体里**不存在** → `unknown`
2. 属性 ID 为**负**（引擎 pass 4 派生值，如 -1 / -43 / -72） → `assumed`
3. 技能轴变化 → `assumed`
4. 状态轴变化 → `assumed`
5. 剖面轴变化 → `assumed`
6. 以上都不变且存在 → `measured`

规则之后还有一层**手工覆盖**（`OVERRIDES`），每条都必须写理由：目前只有 `-70`
（`maxTargets` 实测恒 0 —— 那是"引擎没算"，属不可知，不是"依赖假设"）。

为什么「状态」轴的 A 变体写成「全 active」：引擎的导入规则对本样本给出的就是
「已装模块 active / 货舱物品 offline」，实测 `{"all": "active"}` 与引擎默认
**逐属性完全一致**（脚本会打印自检 `state_axis_a_equals_default=True`）。
写死成全 active 是为了让「变体 A / 变体 B」这一对在代码里显式可见，而不是
依赖「不传 state」这种隐含语义。

扫描范围
--------
`ship.attributes` **与** `items[i].attributes` 一起扫。理由：`provenance.typed()`
是「属性 ID → 三态」的一维映射，投影层会给装备级属性（`cycleTime` / 每发伤害 …）
也打三态；只扫船体级会让一半属性悄悄落到 `unknown`。比较方式是**逐属性**比对
「船体值 + 全部装备值（排序后的多重集）」，与装备顺序无关。

写进产品表的范围
----------------
只包含 `config/attributes.py` **已登记常量名**的属性（红线 N12 禁止裸写 ID，
`audit()` 的 `unnamed` 自检也要求如此）。实验扫到的未登记属性单独一节列出，
等常量表解冻后补登再重跑。

轴外风险（补一节取证，不改表）
------------------------------
`derive_skill_tier` 只覆盖**模块要求**的 19 个技能，不是 EVE 的全部技能。所以
「技能轴不变」只能证明「在这个技能档下不变」。脚本末尾用几个轴外技能做**抽样
取证**（`SUPPLEMENTARY_SKILLS`），把该局限写成可复跑的证词 —— 但**不据此改表**，
因为任务书把技能轴钉死为 `derive_skill_tier` vs 空。要扩大覆盖，得先改轴的
定义（那是另一个任务）。

产物
----
- 标准输出：人类复核用（分轴证据、可粘贴的常量清单、三态计数）；
- `evefit/tools/provenance_report.json`：机器可复核用的完整证据。

本模块**只读**产品代码（不 import `project/provenance.py`——那样就成了自己证明
自己），把表写回产品代码是人的动作 + 复核：脚本不自动改源码。
"""

from __future__ import annotations

import json
import math
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from evefit.config import attributes as _a
from evefit.engine.adapter import Engine, load_killmail
from evefit.engine.assumptions import DEFAULT_DAMAGE_PROFILE, derive_skill_tier
from evefit.sde import attribute_dict as _dict

# ---- 常量 ----

EVE_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_PATH: Path = EVE_ROOT / "spike" / "kill.json"
"""样本配装：killmail 139246907（Hecate），与 §6.2 快照同一份。"""

SAMPLE_LABEL: str = "spike/kill.json (killmail 139246907)"

REPORT_PATH: Path = Path(__file__).resolve().parent / "provenance_report.json"
"""证据落盘位置（人工复核 + diff 用；不进产品链路）。"""

AXIS_SKILL: str = "skill"
AXIS_STATE: str = "state"
AXIS_PROFILE: str = "profile"
AXES: tuple[str, str, str] = (AXIS_SKILL, AXIS_STATE, AXIS_PROFILE)

AXIS_LABEL: dict[str, str] = {
    AXIS_SKILL: "技能轴（全4+补最低 vs 全0）",
    AXIS_STATE: "状态轴（全active vs 全online）",
    AXIS_PROFILE: "伤害剖面轴（25/25/25/25 vs 纯EM）",
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

REASON_NOT_PRESENT: str = "not_present_in_baseline"
REASON_NEGATIVE: str = "negative_derived_id"
REASON_SKILL: str = "skill_axis"
REASON_STATE: str = "state_axis"
REASON_PROFILE: str = "profile_axis"
REASON_INVARIANT: str = "invariant"
REASON_OVERRIDE: str = "manual_override"

REASON_LABEL: dict[str, str] = {
    REASON_NOT_PRESENT: "规则 1 · 基准变体里不存在",
    REASON_NEGATIVE: "规则 2 · 负 ID（引擎 pass 4 派生）",
    REASON_SKILL: "规则 3 · 技能轴变化",
    REASON_STATE: "规则 4 · 状态轴变化",
    REASON_PROFILE: "规则 5 · 伤害剖面轴变化",
    REASON_INVARIANT: "规则 6 · 三轴都不变",
    REASON_OVERRIDE: "手工覆盖（理由逐条写在 provenance.py）",
}

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
"""轴外技能抽样（`derive_skill_tier` 不含它们）。**只用于取证，不参与判定。**"""


# =============================================================================
# 一、观测与比对
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
# 二、变体构造
# =============================================================================


def load_sample(engine: Engine) -> tuple[dict, dict[int, int]]:
    """读出样本配装与技能档（`derive_skill_tier`：全 4 + 补最低）。"""
    raw = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))[0]
    raw.pop("zkb", None)  # zkillboard 附加字段，不是 killmail 字段
    fit = load_killmail(raw)
    return fit, derive_skill_tier(fit, engine)


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
# 三、判定
# =============================================================================


@dataclass(slots=True)
class Finding:
    """一个属性的实测结论（自带可复核的证据）。"""

    attribute_id: int
    name: str
    registered: bool
    state: str
    reason: str
    baseline: Observation | None = None
    evidence: dict[str, dict] = field(default_factory=dict)
    note: str = ""

    def as_json(self) -> dict:
        return {
            "attribute_id": self.attribute_id,
            "name": self.name,
            "registered": self.registered,
            "state": self.state,
            "reason": self.reason,
            "note": self.note,
            "baseline": self.baseline.as_json() if self.baseline is not None else None,
            "evidence": self.evidence,
        }


@dataclass(slots=True)
class Derivation:
    meta: dict
    derived_at: str
    skill_count: int
    state_axis_a_equals_default: bool
    profile_axis_a_equals_default: bool
    baseline_ids: list[int]
    findings: dict[int, Finding]
    axis_change_counts: dict[str, int]
    supplementary_probe: list[dict] = field(default_factory=list)

    # ---- 导出 ----

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
            for reason in (REASON_NEGATIVE, REASON_SKILL, REASON_STATE, REASON_PROFILE, REASON_OVERRIDE)
        }

    def as_json(self) -> dict:
        return {
            "task": "T-08 实测推导 provenance 表",
            "derived_at": self.derived_at,
            "sample": SAMPLE_LABEL,
            "engine": self.meta,
            "skill_tier_size": self.skill_count,
            "axes": {axis: AXIS_LABEL[axis] for axis in AXES},
            "axis_change_counts": self.axis_change_counts,
            "state_axis_a_equals_default": self.state_axis_a_equals_default,
            "profile_axis_a_equals_default": self.profile_axis_a_equals_default,
            "baseline_attribute_count": len(self.baseline_ids),
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


def derive(engine: Engine) -> Derivation:
    """跑三轴扫描并判定每个属性的三态（除读样本外无 IO，不发网络请求）。"""
    fit, skills = load_sample(engine)
    base_fit = _with_skills(fit, skills)
    baseline = snapshot(engine.calculate(base_fit))

    axis_observations: dict[str, tuple[dict[int, Observation], dict[int, Observation]]] = {}
    axis_a_equals_baseline: dict[str, bool] = {}
    for axis, (variant_a, variant_b) in variants(fit, skills).items():
        # A 变体：与基准完全相同的 fit 直接复用快照，否则真算一遍再对拍。
        obs_a = baseline if variant_a == base_fit else snapshot(engine.calculate(variant_a))
        obs_b = snapshot(engine.calculate(variant_b))
        axis_observations[axis] = (obs_a, obs_b)
        axis_a_equals_baseline[axis] = set(obs_a) == set(baseline) and all(
            _same_observation(obs_a[aid], baseline[aid]) for aid in baseline
        )

    registered_ids = {int(value) for value in _attribute_constants().values()}
    findings = {
        aid: _classify(aid, baseline, axis_observations, registered_ids)
        for aid in sorted(set(baseline) | registered_ids)
    }

    return Derivation(
        meta=engine.meta(),
        derived_at=date.today().isoformat(),
        skill_count=len(skills),
        state_axis_a_equals_default=axis_a_equals_baseline[AXIS_STATE],
        profile_axis_a_equals_default=axis_a_equals_baseline[AXIS_PROFILE],
        baseline_ids=sorted(baseline),
        findings=findings,
        axis_change_counts={
            axis: sum(
                1
                for aid in baseline
                if aid in axis_observations[axis][1]
                and not _same_observation(baseline[aid], axis_observations[axis][1][aid])
            )
            for axis in AXES
        },
        supplementary_probe=probe_supplementary_skills(engine, fit, skills, baseline),
    )


def _axis_evidence(
    attribute_id: int,
    axis_observations: dict[str, tuple[dict[int, Observation], dict[int, Observation]]],
) -> dict[str, dict]:
    """该属性在哪些轴上发生了变化（全部轴，按固定顺序）。"""
    evidence: dict[str, dict] = {}
    for axis in AXES:
        obs_a, obs_b = axis_observations[axis]
        value_a = obs_a.get(attribute_id)
        value_b = obs_b.get(attribute_id)
        changed = (value_a is None) != (value_b is None) or (
            value_a is not None and value_b is not None and not _same_observation(value_a, value_b)
        )
        if changed:
            evidence[axis] = {
                "a": value_a.as_json() if value_a is not None else None,
                "b": value_b.as_json() if value_b is not None else None,
            }
    return evidence


def _classify(
    attribute_id: int,
    baseline: dict[int, Observation],
    axis_observations: dict[str, tuple[dict[int, Observation], dict[int, Observation]]],
    registered_ids: set[int],
) -> Finding:
    """按任务书的 6 条规则判定（顺序命中即停），最后套手工覆盖。"""
    name = _dict.name_of(attribute_id)
    registered = attribute_id in registered_ids
    observed = baseline.get(attribute_id)

    if attribute_id in OVERRIDES:
        state, why = OVERRIDES[attribute_id]
        finding = Finding(attribute_id, name, registered, state, REASON_OVERRIDE, observed)
        finding.evidence = (
            _axis_evidence(attribute_id, axis_observations) if observed is not None else {}
        )
        finding.note = why
        return finding

    # 规则 1：基准变体里不存在 → unknown（含"本样本没有这个属性"的登记常量，
    # 例如无战斗机样本里的 -58：未复测就不得标，红线 N4）
    if observed is None:
        return Finding(attribute_id, name, registered, UNKNOWN, REASON_NOT_PRESENT)

    # 规则 2：负 ID（引擎 pass 4 派生值，全都建立在 技能/状态/模式/剖面 之上）
    if attribute_id < 0:
        finding = Finding(attribute_id, name, registered, ASSUMED, REASON_NEGATIVE, observed)
        # 证据照抄：负 ID 是"归因上被规则 2 截走"，但它在哪些轴上真的动了仍然有信息量
        finding.evidence = _axis_evidence(attribute_id, axis_observations)
        return finding

    # 规则 3–5：三轴敏感性（顺序即归因优先级）
    evidence = _axis_evidence(attribute_id, axis_observations)
    for axis, reason in (
        (AXIS_SKILL, REASON_SKILL),
        (AXIS_STATE, REASON_STATE),
        (AXIS_PROFILE, REASON_PROFILE),
    ):
        if axis in evidence:
            finding = Finding(attribute_id, name, registered, ASSUMED, reason, observed)
            finding.evidence = {axis: evidence[axis]}
            return finding

    # 规则 6：存在且三轴都不变
    return Finding(attribute_id, name, registered, MEASURED, REASON_INVARIANT, observed)


def _attribute_constants() -> dict[str, int]:
    """`config/attributes.py` 里全部 `ATTR_*` 常量（名字 → ID）。"""
    return {
        name: getattr(_a, name) for name in sorted(dir(_a)) if name.startswith(_a.CONSTANT_PREFIX)
    }


def probe_supplementary_skills(
    engine: Engine, fit: dict, skills: dict[int, int], baseline: dict[int, Observation]
) -> list[dict]:
    """轴外技能抽样取证：**不改表**，只为把「技能轴覆盖不全」写成可复跑的证据。

    做法：在现有技能档上再给某个轴外技能 5 级，看哪些属性相对基准动了。
    本函数**不参与判定**：索引库没构建 / 打不开时返回空列表，表照样产出。
    """
    try:
        from evefit.config.paths import db_path
        from evefit.sde.index import Index

        index = Index.open(db_path())
    except (FileNotFoundError, ImportError, OSError, sqlite3.Error):
        return []

    probe: list[dict] = []
    try:
        base_fit = _with_skills(fit, skills)
        for name in SUPPLEMENTARY_SKILLS:
            hits = [row for row in index.search_types(name, limit=5) if row.get("type_id")]
            if not hits:
                continue
            type_id = int(hits[0]["type_id"])
            if type_id in skills:  # 已在技能档里 → 这条不算「轴外」
                continue
            boosted = snapshot(engine.calculate(_with_skills(base_fit, {**skills, type_id: 5})))
            changed = sorted(
                aid
                for aid in baseline
                if aid in boosted and not _same_observation(baseline[aid], boosted[aid])
            )
            probe.append(
                {
                    "skill": name,
                    "type_id": type_id,
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
    finally:
        index.close()
    return probe


# =============================================================================
# 四、输出
# =============================================================================


def _constant_names_by_id() -> dict[int, str]:
    return {value: name for name, value in _attribute_constants().items()}


def _paste_line(attribute_id: int, names: dict[int, str]) -> str:
    """给 `provenance.py` 用的可粘贴行（一律常量名，红线 N12）。"""
    if attribute_id in names:
        return f"        _a.{names[attribute_id]},  # {attribute_id}  {_dict.name_of(attribute_id)}"
    return f"        {attribute_id},  # {_dict.name_of(attribute_id)}（未登记，见覆盖理由）"


def _evidence_lines(finding: Finding) -> list[str]:
    """紧凑证据：每个变化的轴一行，写清 A 侧与 B 侧的实测值。"""
    lines: list[str] = []
    for axis, evidence in finding.evidence.items():
        sides: list[str] = []
        for label, payload in (("A", evidence["a"]), ("B", evidence["b"])):
            if payload is None:
                sides.append(f"{label}=<该变体里不存在>")
                continue
            observed = Observation(payload["ship"], tuple(payload["items"]))
            sides.append(f"{label}: {observed.describe()}")
        lines.append(f"      因 {axis} 变化（{AXIS_LABEL[axis]}）：{' → '.join(sides)}")
    return lines


def _print_table(out, derivation: Derivation) -> None:
    names = _constant_names_by_id()

    out(f"基线属性数：{len(derivation.baseline_ids)}（船体级 + 装备级并集，全部实算得到）")
    out(f"技能档：{derivation.skill_count} 个技能（全 4 + 补最低）")
    out(
        "轴自检：全active == 引擎默认 → {} ；25/25/25/25 == 引擎默认 → {}".format(
            derivation.state_axis_a_equals_default,
            derivation.profile_axis_a_equals_default,
        )
    )
    out("")
    out("各轴变化的属性数（基准 vs 变体 B）：")
    for axis in AXES:
        out(f"  {AXIS_LABEL[axis]:34s} {derivation.axis_change_counts[axis]:3d}")

    out("")
    out("=" * 78)
    out("一、measured（三轴不变且存在）—— 可直接粘贴进 provenance.py")
    out("=" * 78)
    for finding in derivation.registered(MEASURED):
        out(_paste_line(finding.attribute_id, names))
    out(f"  合计 {len(derivation.registered(MEASURED))} 个")

    out("")
    out("=" * 78)
    out("二、assumed（按判定规则分组，逐条附 A→B 实测证据）")
    out("=" * 78)
    for reason in (REASON_NEGATIVE, REASON_SKILL, REASON_STATE, REASON_PROFILE, REASON_OVERRIDE):
        group = [f for f in derivation.registered(ASSUMED) if f.reason == reason]
        out("")
        out(f"-- {REASON_LABEL[reason]}：{len(group)} 个")
        for finding in group:
            out(_paste_line(finding.attribute_id, names))
            for line in _evidence_lines(finding):
                out(line)
    out("")
    out(f"  合计 {len(derivation.registered(ASSUMED))} 个")

    out("")
    out("=" * 78)
    out("三、unknown（规则 1：本样本里没有 → 未复测；tag() 的默认兜底仍是 unknown）")
    out("=" * 78)
    unknown_registered = derivation.registered(UNKNOWN)
    out(f"  已登记常量 {len(unknown_registered)} 个：")
    for finding in unknown_registered:
        note = f"    # {finding.note}" if finding.note else ""
        out(_paste_line(finding.attribute_id, names) + note)
    if not unknown_registered:  # pragma: no cover - 常量表变动时的兜底
        out("        （空）")

    out("")
    out("=" * 78)
    out("四、扫到但未在 config/attributes.py 登记（**不得**进产品表；等常量表解冻后补登再重跑）")
    out("=" * 78)
    unregistered = derivation.unregistered()
    tally = {state: sum(1 for f in unregistered if f.state == state) for state in STATES}
    out(
        "  合计 {} 个：measured {} / assumed {} / unknown {}".format(
            len(unregistered), tally[MEASURED], tally[ASSUMED], tally[UNKNOWN]
        )
    )
    for finding in unregistered:
        line = f"  {finding.attribute_id:6d}  {finding.state:8s} {finding.reason:24s} {finding.name}"
        if finding.evidence:
            axis_list = ",".join(finding.evidence)
            line += f"  (变化轴: {axis_list})"
        out(line)

    out("")
    out("=" * 78)
    out("五、轴外技能抽样取证（`derive_skill_tier` 不含这些技能；**不改表**）")
    out("=" * 78)
    if not derivation.supplementary_probe:
        out("  索引库未构建 → 跳过（不影响上面的表）")
    for entry in derivation.supplementary_probe:
        out(f"  {entry['skill']}（{entry['type_id']}）=5 级时：")
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
    out("  说明：这些属性只会被「更完整的技能档」改变。任务书把技能轴钉死为")
    out("        derive_skill_tier vs 空，所以它们按规则 6 落在 measured —— 这是**轴的")
    out("        定义**决定的，不是实测错。要收紧，得先改轴的定义（另一个任务）。")

    out("")
    out("=" * 78)
    out("六、统计")
    out("=" * 78)
    scanned = derivation.counts(registered_only=False)
    registered = derivation.counts(registered_only=True)
    out(
        "  扫描全域（含未登记）  measured {:3d} / assumed {:3d} / unknown {:3d}  合计 {:3d}".format(
            scanned[MEASURED], scanned[ASSUMED], scanned[UNKNOWN], len(derivation.findings)
        )
    )
    out(
        "  已登记常量（产品表）  measured {:3d} / assumed {:3d} / unknown {:3d}  合计 {:3d}".format(
            registered[MEASURED],
            registered[ASSUMED],
            registered[UNKNOWN],
            sum(registered.values()),
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
    out("  代表性 ID：")
    for attribute_id in (263, 265, 9, 48, 49, 11, 15, 4, 70, 552, 37, 54, -1, -43, -58, -70):
        finding = derivation.findings.get(attribute_id)
        if finding is None:  # pragma: no cover - 常量表变动时的兜底
            out(f"    {attribute_id:6d}  （不在扫描集合里）")
            continue
        out(f"    {attribute_id:6d}  {finding.state:8s}  {finding.reason:24s}  {finding.name}")


def write_report(derivation: Derivation, path: Path = REPORT_PATH) -> Path:
    payload = derivation.as_json()
    payload["legend"] = {
        "measured": "输入直接决定，且不随技能档 / 模块状态 / 模式 / 伤害剖面变化",
        "assumed": "依赖技能档、模块状态、模式或伤害剖面之一",
        "unknown": "引擎未提供 / 本样本无法判定（tag() 的默认兜底）",
        "rules": [
            "1) 基准变体里不存在 → unknown",
            "2) 属性 ID 为负 → assumed",
            "3) 技能轴变化 → assumed",
            "4) 状态轴变化 → assumed",
            "5) 剖面轴变化 → assumed",
            "6) 都不变且有值 → measured",
        ],
        "reasons": REASON_LABEL,
        "scope": "只把 config/attributes.py 已登记常量名的属性写进产品表（红线 N12）",
        "overrides": {str(aid): why for aid, (_state, why) in OVERRIDES.items()},
        "plan": "SDE / 引擎 / 样本变更后重跑本脚本，并重抄第一节/第二节/第三节",
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认 GBK，中文会乱码
    except (AttributeError, OSError):  # pragma: no cover - 非 tty / 已配置
        pass

    out = print
    engine = Engine()
    derivation = derive(engine)

    out("=" * 78)
    out("T-08 · provenance 三轴敏感性实验（实测推导，替代手写分类表）")
    out("=" * 78)
    out(f"样本      ：{SAMPLE_LABEL}")
    out(f"引擎/SDE  ：{derivation.meta}")
    out(f"跑表日期  ：{derivation.derived_at}")
    out("")
    _print_table(out, derivation)

    path = write_report(derivation)
    out("")
    out(f"证据已写入：{path.as_posix()}")
    out("")
    out("下一步（人的动作 —— 脚本不改源码，避免表被悄悄生成）：")
    out("  1. 把第 一/二/三 节的常量清单抄进 evefit/project/provenance.py；")
    out("  2. 手工覆盖逐条写理由（目前只有 -70，见 OVERRIDES）；")
    out("  3. 跑 pytest（tests/test_projection_core.py 有 provenance 断言）；")
    out("  4. SDE / 引擎升级后重跑本脚本，diff 报告再改表。")
    return 0


if __name__ == "__main__":  # pragma: no cover - 脚本入口
    raise SystemExit(main())
