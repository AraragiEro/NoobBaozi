"""T1/T2 测试：P2 投影层基础设施（units / provenance / ViewContext / diagnostics）。

覆盖任务书要求：

1. `units.physical` 对 51 / 160 / 1985 的换算（2.226068 / 0.39167 / 10.0），
   并证明它**没有另写一套缩放表**（逐项与 `attribute_dict.to_physical` 对拍）；
2. `provenance.tag` 三态抽样：12→measured、-43→assumed、-70→unknown、未知 ID→unknown；
   并钉住**实测表**的结构性质：measured ∩ assumed 为空、负 ID 常量不得 measured、
   `audit()` 必须自报 `derived_at` / `sample(s)`；
2b. **T-14 多样本推导的护栏**：工具的技能档必须 `all4+min_required` 且技能数 > 100
   （否则 `RuntimeError`），且 `1367/281/1272/-14/-55…-62` 这 12 个属性不得再落 `unknown`；
2c. **T-27 实例级覆盖**：`provenance.resolve(id, value)` 在值为 `None` 时强制 `unknown`
   （覆盖静态表），值存在时逐条等于 `tag()`；`audit()` 必须自报这条口径；
2d. **T-30 第二条实例规则（状态敏感性）**：`resolve(..., state_sensitive=True)` 只把
   `measured` 升格成 `assumed`（`assumed` 不降级、`unknown` 不改判），缺值规则仍优先；
   不传标志时行为与 T-27 逐条相同；`audit()` 必须自报这条口径；
3. **默认 unknown 红线（N4）**：随便造一个不存在的属性 ID，必须 `unknown`；
4. `provenance.typed` 的 `TypedValue` 字段齐全、value 去噪；
5. `ViewContext.attrs("base")` 的 key 是 int、value 是 float（且已是物理单位）；
6. 同一变体调用两次只算一次（引擎缓存计数断言：第二次连引擎都没碰）；
7. `diagnostics` 至少 3 条规则命中 / 未命中（样本 `spike/kill.json`，killmail 139246907）；
8. 规则表（`config/diagnostics.json`）与 `diagnostics.py` 的实现必须一一对应。

约束：无网络、无随机（红线 N1）；样本只读；不碰任何被冻结的文件。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from noobbaozi.config import attributes as A
from noobbaozi.config.paths import db_path
from noobbaozi.engine.adapter import Engine
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier
from noobbaozi.project import diagnostics as D
from noobbaozi.project import provenance as P
from noobbaozi.project import reasons as R
from noobbaozi.project import units as U
from noobbaozi.project.views.base import (
    BASE,
    OFFLINE,
    OVERLOAD,
    VARIANTS,
    ViewContext,
    ship_attributes,
)
from noobbaozi.sde import attribute_dict as AD
from noobbaozi.sde.index import Index

EVE_ROOT = Path(__file__).resolve().parents[1]
KILL_PATH = EVE_ROOT / "spike" / "kill.json"

UNKNOWN_ATTR_ID: int = 10**9 + 7
"""一个**不存在**的属性 ID（不是真实属性，故意取得离谱；红线 N4 的探针）。"""

NUMERIC_KINDS = (int, float)


# ---- fixtures ----


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


@pytest.fixture(scope="module")
def killmail() -> dict:
    raw = json.loads(KILL_PATH.read_text(encoding="utf-8"))[0]
    raw.pop("zkb", None)  # zkillboard 附加字段，不是 killmail 字段
    return raw


@pytest.fixture(scope="module")
def sample_fit(killmail: dict, engine: Engine, index) -> dict:
    """样本配装 + 「全部技能 4 级 + 补最低」技能档（实测 **512** 个技能，T-12 硬结论 10/87）。

    技能档必须传 `index`：不传会退化成 `min_required_only` 旧档（19 个技能），
    数值全都不对（见 `tests/test_engine.py` 的同名判别与下面的 `test_skill_tier_*`）。
    """
    fit = dogma.load_killmail(killmail)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return fit


@pytest.fixture(scope="module")
def index():
    try:
        path = db_path()
    except FileNotFoundError:  # 索引库是构建产物，没建就跳过投影层集成测试
        pytest.skip("索引库未构建：先跑 noobbaozi.sde.ingest.build_index()")
    opened = Index.open(path)
    yield opened
    opened.close()


@pytest.fixture(scope="module")
def make_ctx(engine: Engine, index, sample_fit: dict):
    """工厂：每次调用给一个**缓存是空的**新上下文（用于「只算一次」类断言）。"""

    def factory() -> ViewContext:
        return ViewContext(
            engine=engine,
            index=index,
            fit=sample_fit,
            assumptions=default_assumptions(sample_fit, engine, index=index),
            meta=engine.meta(),
        )

    return factory


@pytest.fixture(scope="module")
def ctx(make_ctx) -> ViewContext:
    return make_ctx()


# ---- 小工具 ----


def _numbers(value) -> list[float]:
    """递归收集结构里所有数值（用于断言 evidence 一定带具体数值）。"""
    if isinstance(value, bool) or value is None:
        return []
    if isinstance(value, NUMERIC_KINDS):
        return [float(value)]
    if isinstance(value, dict):
        collected: list[float] = []
        for item in value.values():
            collected.extend(_numbers(item))
        return collected
    if isinstance(value, (list, tuple)):
        collected = []
        for item in value:
            collected.extend(_numbers(item))
        return collected
    return []


# =============================================================================
# 一、units（T1，纯函数）
# =============================================================================


def test_physical_scales_from_attribute_dictionary() -> None:
    """毫秒 → 秒、×1000 → 物理值；三条都是实测基线。"""
    assert math.isclose(U.physical(A.ATTR_CYCLE_TIME, 2226.068), 2.226068, rel_tol=1e-9)
    assert math.isclose(U.physical(A.ATTR_TRACKING_SPEED, 391.67), 0.39167, rel_tol=1e-9)
    assert math.isclose(U.physical(A.ATTR_STANCE_SWITCH_TIME, 10000.0), 10.0, rel_tol=1e-9)


def test_physical_delegates_no_second_scale_table() -> None:
    """投影层不允许有自己的缩放表：逐项与 `attribute_dict.to_physical` 一致。"""
    for attribute_id in sorted(A.SCALE_BY_ATTR):
        for raw in (0.0, 1.0, 1000.0, 391.67):
            assert math.isclose(
                U.physical(attribute_id, raw),
                AD.to_physical(attribute_id, raw),
                rel_tol=1e-12,
            )


def test_physical_unscaled_returns_raw() -> None:
    """未登记缩放的属性原值返回（`alignTime` 是秒，不该被乘 0.001）。"""
    assert math.isclose(U.physical(A.ATTR_ALIGN_TIME, 11.8611), 11.8611, rel_tol=1e-12)


def test_pct_is_resistance_percentage() -> None:
    """共振率 → 抗性百分比：0.875 → 12.5%。"""
    assert math.isclose(U.pct(0.875), 12.5, rel_tol=1e-9)
    assert math.isclose(U.pct(0.0), 100.0, rel_tol=1e-9)


def test_ms_s_explicit_conversion() -> None:
    assert math.isclose(U.ms_to_s(2304.636), 2.304636, rel_tol=1e-9)
    assert math.isclose(U.s_to_ms(U.ms_to_s(2304.636)), 2304.636, rel_tol=1e-9)


def test_fmt_removes_float_noise() -> None:
    """`0.1+0.2` 这类噪声不许进输出；`-0.0` 归一成 `0.0`。

    `9102.577816` 是 T-12 技能档修正后的 `ehp_mixed`（`tools/baseline_after_tierfix.json`；
    旧档是 `8854.958772`）。
    """
    assert math.isclose(U.fmt(0.1 + 0.2), 0.3, rel_tol=1e-12)
    assert math.isclose(U.fmt(9102.577816), 9102.578, rel_tol=1e-12)

    zero = U.fmt(-0.0)
    assert math.isclose(zero, 0.0, rel_tol=0.0, abs_tol=0.0)  # 符号位也归零
    assert math.copysign(1.0, zero) == 1.0


def test_fmt_rejects_non_finite_and_bad_digits() -> None:
    with pytest.raises(ValueError):
        U.fmt(float("nan"))
    with pytest.raises(ValueError):
        U.fmt(1.0, digits=-1)


# =============================================================================
# 二、provenance（T1，纯函数）
# =============================================================================


def test_tag_three_states_sampling() -> None:
    """四类 ID 的三态抽样 —— 值来自 `tools/derive_provenance.py` 的实测表。

    这四条是**实测结论**，不是拍脑袋：`12 lowSlots` 三轴不变（measured）；`-43 ehp`
    随技能档与伤害剖面变（assumed）；`-70` 恒 0 属不可知（unknown，手工覆盖）；表外 ID
    默认 unknown。SDE / 引擎 / 样本升级后重跑脚本，这条会随表一起变。

    **T-14 更正**：`263 shieldCapacity` 现在是 `assumed` —— 技能档修好后（512 技能，
    含 `Shield Management`），它随技能轴 780 → 650。旧表判 measured 是缺陷 A 的产物。
    """
    assert P.tag(A.ATTR_LOW_SLOTS) == "measured"  # 12
    assert P.tag(A.ATTR_EHP) == "assumed"  # -43
    assert P.tag(P.ATTR_MAX_TARGETS_UNVERIFIED) == "unknown"  # -70
    assert P.tag(UNKNOWN_ATTR_ID) == "unknown"


def test_tag_default_is_unknown_red_line() -> None:
    """红线 N4：新属性不得被误标成 measured（默认分支必须落在 unknown）。"""
    assert P.tag(UNKNOWN_ATTR_ID) == "unknown"
    assert UNKNOWN_ATTR_ID not in P.MEASURED_ATTRS
    assert UNKNOWN_ATTR_ID not in P.ASSUMED_ATTRS

    audit = P.audit()
    assert audit["overlap"] == []
    assert audit["measured"] > 0 and audit["assumed"] > 0
    assert audit["consistent"] is True
    # 两态互斥；unknown 是兜底而不是垃圾桶：被覆盖成 unknown 的 ID 不得同时躺在
    # measured / assumed 里（否则 tag() 的结果取决于判断顺序）。
    assert not (P.MEASURED_ATTRS & P.ASSUMED_ATTRS)
    assert not (P.MEASURED_ATTRS & P.UNKNOWN_ATTRS)
    assert not (P.ASSUMED_ATTRS & P.UNKNOWN_ATTRS)
    assert P.ATTR_MAX_TARGETS_UNVERIFIED not in P.MEASURED_ATTRS
    assert P.ATTR_MAX_TARGETS_UNVERIFIED not in P.ASSUMED_ATTRS


def test_no_negative_derived_id_is_measured() -> None:
    """负 ID 是引擎 pass 4 派生值 —— 全都建立在技能/状态/剖面之上，不得 measured。

    枚举 `config/attributes.py` 里 `-72 … -1` 区间的常量（当前 40 个），逐个断言。
    """
    negative_ids = sorted(aid for aid in A.ATTRIBUTES if -72 <= aid <= -1)
    assert negative_ids, "常量表里的负 ID 不该消失（-1 alignTime 等）"
    # 确认这些 ID 真的在 `attribute_dict` 里（不是只在常量表里躺着）
    assert all(AD.name_of(aid) != f"attr_{aid}" for aid in negative_ids)

    offenders = [aid for aid in negative_ids if aid in P.MEASURED_ATTRS]
    assert offenders == [], f"负 ID 不得进 measured：{offenders}"
    assert all(P.tag(aid) != "measured" for aid in negative_ids)


def test_audit_reports_derivation_provenance() -> None:
    """`audit()` 必须自报「哪天、用哪些样本」跑出来的表（否则无法判断是否过期）。"""
    audit = P.audit()

    assert isinstance(audit["derived_at"], str) and audit["derived_at"]
    assert audit["derived_at"] == P.DERIVED_AT
    # 样本口径：T-14 起至少 4 条（基础 / 子系统 / 导弹 / 战斗机+无人机），
    # 第一条是 killmail 139246907 转存的 Hecate EFT。
    assert "f1-hecate-killmail.eft" in audit["sample"]
    assert "139246907" in audit["sample"]
    assert audit["sample"] == P.DERIVED_SAMPLE
    assert len(audit["samples"]) >= 4, audit["samples"]
    assert any("T3C" in label or "Loki" in label for label in audit["samples"])
    assert any("Raven" in label for label in audit["samples"])
    assert any("Nyx" in label for label in audit["samples"])
    assert any("Templar" in label or "Gungnir" in label for label in audit["samples"])
    # 手工覆盖必须留痕：ID → 理由，且理由不得为空
    assert audit["overrides"], "至少 -70 maxTargets 那条覆盖必须在 audit 里可见"
    assert all(isinstance(why, str) and why.strip() for why in audit["overrides"].values())


def test_provenance_pins_measured_evidence() -> None:
    """实测推翻手写表的 7 处，逐条钉住（`field_spec` 依赖这些数）：
    质量随 MWD 状态变、信号半径随 MWD 变、护甲/结构 HP 与 CPU 随技能档变、
    敏捷随技能档变 —— 它们都是 `assumed`，不是 `measured`。
    """
    for attribute_id in (
        A.ATTR_MASS,  # 4   状态轴：MWD active 的质量加成
        A.ATTR_SIGNATURE_RADIUS,  # 552 状态轴：MWD 信号膨胀 ×5.5
        A.ATTR_ARMOR_HP,  # 265 技能轴：Hull Upgrades
        A.ATTR_HULL_HP,  # 9   技能轴
        A.ATTR_CPU_OUTPUT,  # 48  技能轴：CPU Management
        A.ATTR_CPU_LOAD,  # 49  技能轴
        A.ATTR_AGILITY,  # 70  技能轴
    ):
        assert P.tag(attribute_id) == "assumed", f"{attribute_id} 实测随假设变化，不得标 measured"


# ---- T-14 护栏：推导工具的技能档 + 多样本覆盖（防缺陷 A / B 复发）----


def test_derive_provenance_rejects_degraded_skill_tier() -> None:
    """缺陷 A 的护栏：技能档退化必须抛 `RuntimeError`，**不允许**静默跑在旧档上。

    旧档 = `min_required_only`（`derive_skill_tier` 不传 `index` 时的静默退化，实测
    19 个技能）；新档 = `all4+min_required`（512 个）。两种退化都要炸：档名不对、
    档名对但技能数 `<= 100`。
    """
    from noobbaozi.engine.assumptions import SKILL_TIER_NAME, SKILL_TIER_NAME_MIN_ONLY
    from noobbaozi.tools import derive_provenance as DP

    assert DP.MIN_SKILL_TIER_SIZE == 100, "任务书口径：技能数必须 > 100"

    old_tier = {type_id: 4 for type_id in range(1, 20)}  # 旧档实测 19 个技能
    assert len(old_tier) < DP.MIN_SKILL_TIER_SIZE
    with pytest.raises(RuntimeError, match="技能档退化"):
        DP.require_full_skill_tier(SKILL_TIER_NAME_MIN_ONLY, old_tier)
    with pytest.raises(RuntimeError, match="技能档退化"):
        # 档名对但技能数只有 19 —— 同样必须炸（技能数才是「真的跑了新档」的证据）
        DP.require_full_skill_tier(SKILL_TIER_NAME, old_tier)

    DP.require_full_skill_tier(SKILL_TIER_NAME, {i: 4 for i in range(1, 513)})  # 正确档不抛


def test_load_sample_passes_index_or_dies(engine: Engine, index) -> None:
    """`load_sample()` 必须把 `index` 传进 `derive_skill_tier`；`index=None` 当场炸。

    这一条正对着缺陷 A：签名 `derive_skill_tier(fit, engine, index=None)` 不传 `index`
    **不报错**，只是静默退化。护栏要证明的是「退化在本工具里活不过加载」。
    """
    from noobbaozi.tools import derive_provenance as DP

    sample = DP.SAMPLES[0]
    with pytest.raises(RuntimeError, match="技能档退化"):
        DP.load_sample(engine, sample, None)  # 等价于「忘了传 index」

    fit, skills, tier_name = DP.load_sample(engine, sample, index)
    assert tier_name == "all4+min_required"
    assert len(skills) > DP.MIN_SKILL_TIER_SIZE
    assert fit["ship"]["type_id"] == 35683  # Hecate


def test_derive_provenance_scans_four_samples_on_full_tier(engine: Engine, index) -> None:
    """缺陷 B 的护栏：至少 4 条样本，每条都跑在 `all4+min_required` 档上。"""
    from noobbaozi.tools import derive_provenance as DP

    derivation = DP.derive(engine, index)

    assert len(derivation.runs) >= 4
    assert [run.key for run in derivation.runs] == ["f1", "f4", "f15", "carrier"]
    for run in derivation.runs:
        assert run.skill_tier_name == "all4+min_required", run.key
        assert run.skill_count > DP.MIN_SKILL_TIER_SIZE, run.key
        assert run.baseline, f"{run.key} 没算出任何属性"


def test_provenance_no_longer_unknown_for_former_sample_gaps() -> None:
    """T-14 验收点：样本够了以后，这 12 个属性**不再**是 `unknown`。

    `1367`（T3C 子系统槽）、`281`（导弹）、`1272`（无人机带宽）与战斗机/无人机族
    （`-14/-55…-62`）过去被单样本困成 unknown —— 那是「样本不够」，不是不可知。
    """
    formerly_unknown = (1367, 281, 1272, -14, -58, -55, -56, -57, -59, -60, -61, -62)
    still_unknown = [aid for aid in formerly_unknown if P.tag(aid) == "unknown"]
    assert still_unknown == [], f"这些属性应当是 measured / assumed：{still_unknown}"
    assert all(P.tag(aid) in {"measured", "assumed"} for aid in formerly_unknown)

    # 手工覆盖保留：-70 maxTargets 仍是 unknown（理由写在 `_MANUAL_OVERRIDES`）
    assert P.tag(-70) == "unknown"
    assert -70 in P.audit()["overrides"]
    # 两态互斥（红线：tag() 的结果不得取决于判断顺序）
    assert P.MEASURED_ATTRS & P.ASSUMED_ATTRS == frozenset()



# ---- T-27：实例级覆盖（两层模型的第二层：值缺失 ⇒ unknown）----


def test_resolve_missing_value_is_unknown_overrides_table() -> None:
    """**值缺失 ⇒ `unknown`**，与静态表怎么判无关（实例级覆盖静态表，T-27）。

    `-11 damageAlpha` 在表里是 `assumed`、`109 kineticDamageResonance` 是 `measured`
    —— 但只要本次计算里这个字段没有值，三态就必须落在 `unknown`：值都没有时，
    「依赖假设」与「实测稳定」都是无依据的宣称（f4 的 `dph` 正是这个情形：
    有带弹药武器却零伤害，突变模块数据缺失）。
    """
    # 先钉住静态表结论，避免「表变了导致下一条断言变成同义反复」
    assert P.tag(A.ATTR_DAMAGE_ALPHA) == "assumed"  # -11
    assert P.tag(A.ATTR_HULL_KINETIC_RESONANCE) == "measured"  # 109

    assert P.resolve(A.ATTR_DAMAGE_ALPHA, None) == "unknown"
    assert P.resolve(A.ATTR_HULL_KINETIC_RESONANCE, None) == "unknown"
    assert P.resolve(None, None) == "unknown"  # 合成值 + 缺值 → 仍按缺值
    assert P.resolve(P.ATTR_MAX_TARGETS_UNVERIFIED, None) == "unknown"


def test_resolve_keeps_static_table_when_value_exists() -> None:
    """值非 `None` 时**完全等于** `tag()`：不得因本轮改动漂移（f1/f5 的 `dph` 靠这条）。"""
    cases = {
        A.ATTR_LOW_SLOTS: 6.0,  # 12   measured
        A.ATTR_EHP: 9102.577816,  # -43  assumed
        A.ATTR_MASS: 1_550_000.0,  # 4    assumed（状态轴）
        A.ATTR_DRONE_CAPACITY: 50.0,  # 283  measured
        A.ATTR_DAMAGE_ALPHA: 943.668,  # -11  assumed（f1 的 dph）
        A.ATTR_HULL_KINETIC_RESONANCE: 0.5,  # 109  measured
        P.ATTR_MAX_TARGETS_UNVERIFIED: 0.0,  # -70  手工覆盖 unknown
        UNKNOWN_ATTR_ID: 1.0,  # 表外 → unknown
    }
    for attribute_id, value in cases.items():
        assert P.resolve(attribute_id, value) == P.tag(attribute_id), attribute_id

    # 合成值（没有属性 ID）仍然是 assumed —— 它是「算出来的」，不是「缺值」
    assert P.resolve(None, 0.0) == "assumed"
    assert P.resolve(None, "hybrid") == "assumed"
    # **0.0 是值、不是缺失**：零伤害照实走静态表，不能当 unknown
    assert P.resolve(A.ATTR_DAMAGE_ALPHA, 0.0) == "assumed"


def test_audit_reports_the_instance_rule() -> None:
    """`audit()` 必须自报「实例级覆盖：值缺失 → unknown」（排查口径的唯一入口）。"""
    audit = P.audit()

    assert audit["instance_missing_rule"] == P.INSTANCE_MISSING_RULE
    assert "实例级覆盖" in audit["instance_missing_rule"]
    assert "unknown" in audit["instance_missing_rule"]
    # 静态表的口径没被这条改动：三态计数仍是实测表自己的数字
    assert audit["consistent"] is True


# ---- T-30：第二条实例规则（状态敏感性 ⇒ assumed，只升格、不降级）----


def test_resolve_state_sensitive_promotes_measured_to_assumed() -> None:
    """**状态敏感 ⇒ `assumed`**：本次配装里值随模块状态变，静态表的 `measured` 便不成立。

    这就是 PROGRESS §3 第 166/167 条要的那条规则：同一属性（护盾共振率）在**没有**盾抗
    模块的船上可以是 `measured`，在**有**盾抗模块的船上必须是 `assumed` —— 所以它是按配装
    的性质，只能由实例级探测（`render.state_sensitive_attrs()`）提供这个布尔标记。
    """
    # 静态表判 measured 的对照属性（12 lowSlots）
    assert P.tag(A.ATTR_LOW_SLOTS) == "measured"
    assert P.resolve(A.ATTR_LOW_SLOTS, 6.0, state_sensitive=True) == "assumed"

    # **不传标志 = 完全走旧逻辑**（默认行为不得漂移）
    assert P.resolve(A.ATTR_LOW_SLOTS, 6.0) == "measured"
    assert P.resolve(A.ATTR_LOW_SLOTS, 6.0, state_sensitive=False) == "measured"

    # 271 shieldEmDamageResonance：第 14 轮的静态表已把它保守判成 assumed，所以
    # 无论探测结论如何，三态都只能是 assumed（不能"因为这次不敏感"就变成 measured）
    assert P.tag(A.ATTR_SHIELD_EM_RESONANCE) == "assumed"
    assert P.resolve(A.ATTR_SHIELD_EM_RESONANCE, 0.2969, state_sensitive=True) == "assumed"
    assert P.resolve(A.ATTR_SHIELD_EM_RESONANCE, 0.5, state_sensitive=False) == "assumed"


def test_resolve_state_sensitive_never_downgrades_or_rescues_unknown() -> None:
    """只允许**升格** `measured → assumed`：`assumed` 不降级，`unknown` 也不改判。

    `unknown` 被这条"救"成 `assumed` 同样是撒谎（把「不可知」说成「有假设支撑」），
    与红线 N4 同一条精神。
    """
    assert P.tag(A.ATTR_EHP) == "assumed"
    assert P.resolve(A.ATTR_EHP, 9102.577816, state_sensitive=True) == "assumed"  # 不降级
    assert P.resolve(P.ATTR_MAX_TARGETS_UNVERIFIED, 0.0, state_sensitive=True) == "unknown"
    assert P.resolve(UNKNOWN_ATTR_ID, 1.0, state_sensitive=True) == "unknown"
    # 合成值（指不到单一来源属性）与状态敏感性无关，仍是 assumed
    assert P.resolve(None, 1.0, state_sensitive=True) == "assumed"


def test_missing_value_rule_still_wins_over_state_sensitivity() -> None:
    """**第一条实例规则优先**：值 `None` → `unknown`，`state_sensitive` 不产生任何作用。

    否则「值都没有」会被说成「依赖假设」，把那句 `dph` 的教训（T-27）又丢回去。
    """
    assert P.resolve(A.ATTR_LOW_SLOTS, None, state_sensitive=True) == "unknown"
    assert P.resolve(A.ATTR_SHIELD_EM_RESONANCE, None, state_sensitive=True) == "unknown"
    assert P.resolve(None, None, state_sensitive=True) == "unknown"


def test_audit_reports_the_state_sensitivity_rule() -> None:
    """`audit()` 必须自报第二条实例规则（排查「静态表说 measured 却报 assumed」的人口）。"""
    audit = P.audit()

    assert audit["instance_state_rule"] == P.INSTANCE_STATE_RULE
    assert "状态" in audit["instance_state_rule"]
    assert "assumed" in audit["instance_state_rule"]
    assert "measured" in audit["instance_state_rule"]
    # 第一条规则的文案与表都没被第二条挤掉
    assert audit["instance_missing_rule"] == P.INSTANCE_MISSING_RULE
    assert audit["consistent"] is True


def test_typed_returns_full_contract_fields() -> None:
    """I6：任何数值都带 provenance；字段集合与契约逐字一致。

    数值用 T-12 后的 `ehp_mixed`（`tools/baseline_after_tierfix.json`：9102.577816）。
    """
    typed = P.typed(A.ATTR_EHP, 9102.577816)

    assert set(typed) == {"value", "provenance", "unit", "source_attr"}
    assert math.isclose(typed["value"], 9102.578, rel_tol=1e-12)  # 去噪
    assert typed["provenance"] == "assumed"
    assert typed["unit"] == "hp"
    assert typed["source_attr"] == A.ATTR_EHP


def test_typed_measured_and_unit_passthrough() -> None:
    # `283 droneCapacity`：四个样本里出现且三轴不变 → measured，单位 m3 原样透传。
    drone_capacity = P.typed(A.ATTR_DRONE_CAPACITY, 50.0)
    assert drone_capacity["provenance"] == "measured"
    assert drone_capacity["unit"] == "m3"
    assert math.isclose(drone_capacity["value"], 50.0, rel_tol=1e-12)

    # 单位确实给不出时必须留空，不得猜一个（红线 N4 的同一条精神）。
    # 1997 modeEmResistancePostDiv：四个样本都没有 mode → 实测扫不到 → unknown，单位也留空。
    unnamed_unit = P.typed(A.ATTR_MODE_EM_RESISTANCE_POST_DIV, 100.0)
    assert unnamed_unit["unit"] is None
    assert unnamed_unit["provenance"] == "unknown"


def test_typed_value_is_physical_not_raw() -> None:
    """typed 内部必须走 units.physical（毫秒属性不能把 2304.636 原样吐出去）。"""
    typed = P.typed(A.ATTR_CYCLE_TIME, 2304.636)
    assert math.isclose(typed["value"], 2.305, rel_tol=1e-9)  # fmt 默认 3 位
    assert typed["value"] < 100


# =============================================================================
# 三、ViewContext（T2，需要引擎 + 索引库）
# =============================================================================


def test_skill_tier_is_all_four_only_when_index_is_passed(
    killmail: dict, engine: Engine, index
) -> None:
    """判别特征：传索引 = 全部技能 4 级（**技能数 > 100**）；不传 = 旧档（19 个）。

    单看某个数值不够稳（SDE 更新会变），但「技能数 > 100」是「全部技能 4 级」这个口径的
    **结构性判别特征**：一旦有人把 `index` 丢了，档位会静默退回 `min_required_only`，
    这条立刻炸，而不是让一堆数值悄悄变化之后才被发现（T-12 后的抓漏断言）。
    """
    fit = dogma.load_killmail(killmail)
    tier = derive_skill_tier(fit, engine, index=index)
    legacy = derive_skill_tier(fit, engine)

    assert len(tier) > 100, f"全 4 档应含全部已发布技能（实测 512），实际 {len(tier)}"
    assert set(tier.values()) <= {4, 5}, "全 4 档只允许 4/5 级（5 级是补模块最低要求）"
    assert sum(1 for level in tier.values() if level == 4) > 100
    # 旧档只有「被模块要求的」技能：两种口径必须可区分，不得静默冒充新档
    assert len(legacy) < 100
    assert set(legacy) <= set(tier)


def test_attrs_keys_are_int_and_values_are_float(ctx: ViewContext) -> None:
    attrs = ctx.attrs(BASE)

    assert attrs, "base 变体不该是空属性表"
    assert all(isinstance(key, int) for key in attrs)
    assert all(isinstance(value, float) for value in attrs.values())
    assert A.ATTR_EHP in attrs
    assert attrs[A.ATTR_EHP] > 0.0


def test_attrs_are_already_physical_units(ctx: ViewContext) -> None:
    """`items()` 里武器 cycleTime 必须是秒（2.3 量级），不是毫秒（2300 量级）。"""
    weapons = [
        item
        for item in ctx.items(BASE)
        if item["slot_type"] == "high" and item["group_name"] == "Hybrid Weapon"
    ]
    assert weapons, "样本里应有中子炮"

    cycles = [item["attributes"].get(A.ATTR_CYCLE_TIME) for item in weapons]
    assert all(cycle is not None and 0.1 < cycle < 60.0 for cycle in cycles)


def test_items_carry_type_id_and_slot(ctx: ViewContext) -> None:
    """引擎计算侧不再回 type_id，必须由投影层按序合并回来（实测 31/31）。"""
    items = ctx.items(BASE)

    assert len(items) == len(ctx.fit["items"])
    assert all(item["type_id"] is not None for item in items)
    assert {item["slot_type"] for item in items} >= {"high", "medium", "low", "rig", "cargo"}
    assert any(item["group_name"] == "Propulsion Module" for item in items)


def test_same_variant_computed_once(make_ctx, engine: Engine) -> None:
    """同名变体只算一次：第二次连 `engine.calculate` 都不该被调用。"""
    fresh = make_ctx()
    engine.clear_cache()

    before = engine.cache_stats()
    first = fresh.attrs(OVERLOAD)
    after_first = engine.cache_stats()
    second = fresh.attrs(OVERLOAD)
    after_second = engine.cache_stats()

    assert after_first["misses"] == before["misses"] + 1  # 第一次真算
    assert after_second["misses"] == after_first["misses"]  # 第二次没有新计算
    assert after_second["hits"] == after_first["hits"]  # 连引擎缓存都没碰（走 ctx 缓存）

    assert set(first) == set(second)
    for attribute_id in (A.ATTR_EHP, A.ATTR_DAMAGE_DPS):
        assert math.isclose(first[attribute_id], second[attribute_id], rel_tol=1e-12)


def test_projection_matches_raw_calculation(ctx: ViewContext) -> None:
    """投影值与原始 calculation 对得上（只换单位，不改数值）。"""
    raw = ctx.variant(BASE)
    projected = ctx.attrs(BASE)

    raw_attrs = raw["ship"]["attributes"]
    raw_entry = raw_attrs.get(A.ATTR_EHP) or raw_attrs[str(A.ATTR_EHP)]
    assert math.isclose(projected[A.ATTR_EHP], float(raw_entry["value"]), rel_tol=1e-12)
    assert math.isclose(
        ship_attributes(raw)[A.ATTR_SIGNATURE_RADIUS],
        projected[A.ATTR_SIGNATURE_RADIUS],
        rel_tol=1e-12,
    )


def test_variant_names_are_preset_and_semantically_different(ctx: ViewContext) -> None:
    assert set(VARIANTS) == {BASE, OVERLOAD, OFFLINE}

    base_dps = ctx.attrs(BASE)[A.ATTR_DAMAGE_DPS]
    offline_dps = ctx.attrs(OFFLINE)[A.ATTR_DAMAGE_DPS]

    assert base_dps > 0.0
    assert math.isclose(offline_dps, 0.0, abs_tol=1e-12)  # 全 offline → DPS 归零（硬结论 9）


def test_unknown_variant_name_raises(ctx: ViewContext) -> None:
    with pytest.raises(KeyError):
        ctx.attrs("no-such-variant")


def test_calculate_variant_rejects_preset_name(ctx: ViewContext) -> None:
    with pytest.raises(ValueError):
        ctx.calculate_variant(BASE, ctx.fit)


# =============================================================================
# 四、diagnostics（T2/T3：样本 killmail 139246907）
# =============================================================================


def test_config_rules_match_implementation() -> None:
    """规则表与实现必须一一对应（配置写了没实现 → 必须炸，不能静默跳过）。"""
    config_ids = {rule.id for rule in D.load_rules()}
    implemented = set(D.implemented_rules())

    assert config_ids == implemented
    assert all(rule.view for rule in D.load_rules())


def _flag_ids(flags: list[dict]) -> set[str]:
    return {flag["id"] for flag in flags}


def test_flags_structure_and_numeric_evidence(ctx: ViewContext) -> None:
    flags = D.evaluate(ctx)

    assert flags, "样本配装（CPU/PG 超载 + MWD + 抗性洞）不该一条可疑点都没有"
    for flag in flags:
        assert set(flag) == {"id", "view", "message", "evidence"}
        assert isinstance(flag["message"], str) and flag["message"]
        assert isinstance(flag["evidence"], dict) and flag["evidence"]
        assert _numbers(flag["evidence"]), f"{flag['id']} 的 evidence 必须带具体数值"
        assert flag["message"] == flag["message"].strip()


def test_expected_hits_and_misses(ctx: ViewContext) -> None:
    ids = _flag_ids(D.evaluate(ctx))

    # 命中：CPU/PG 余量为负、MWD 撑信号、护甲/护盾抗性洞、有修复但电容不稳定
    assert {"resource_overflow", "mwd_sig_increase", "resist_hole", "repaired_but_unstable"} <= ids

    # 未命中：高槽已满、带宽为 0（无余量）、有推进模块、武器周期与 dph/dps 一致、
    #         单个模块关闭都救不回电容稳定性、余量远小于总量
    assert "empty_hardpoint" not in ids
    assert "drone_bandwidth_idle" not in ids
    assert "no_propulsion" not in ids
    assert "alpha_dph_ratio" not in ids
    assert "cap_sensitive" not in ids
    assert "resource_slack" not in ids

    assert len({flag["view"] for flag in D.evaluate(ctx)} & {"resources", "mobility", "stability"}) >= 3


def test_resource_overflow_reports_real_deficit(ctx: ViewContext) -> None:
    flag = next(flag for flag in D.evaluate(ctx) if flag["id"] == "resource_overflow")
    evidence = flag["evidence"]

    assert evidence["cpu_free"] < 0.0
    assert evidence["power_free"] < 0.0
    assert math.isclose(evidence["cpu_deficit"], -evidence["cpu_free"], rel_tol=1e-9)
    assert math.isclose(evidence["power_deficit"], -evidence["power_free"], rel_tol=1e-9)
    assert evidence["cpu_deficit"] > 0.0 and evidence["power_deficit"] > 0.0


def test_mwd_sig_increase_is_relative(ctx: ViewContext) -> None:
    flag = next(flag for flag in D.evaluate(ctx) if flag["id"] == "mwd_sig_increase")
    evidence = flag["evidence"]

    assert evidence["sig_ratio"] > D.SIG_INCREASE_RATIO
    assert math.isclose(
        evidence["signature_radius_off_m"],
        ctx.attrs(BASE)[A.ATTR_SIGNATURE_RADIUS] / evidence["sig_ratio"],
        rel_tol=1e-3,
    )
    assert evidence["signature_radius_m"] > evidence["signature_radius_off_m"]


def test_resist_hole_uses_resonance_ratio(ctx: ViewContext) -> None:
    flag = next(flag for flag in D.evaluate(ctx) if flag["id"] == "resist_hole")
    layers = flag["evidence"]["layers"]

    assert layers, "样本护甲抗性 EM 0.425 vs 爆炸 0.765，必然有洞"
    for layer in layers:
        assert layer["ratio"] >= D.RESIST_HOLE_RATIO
        assert layer["weakest_resonance"] > layer["strongest_resonance"]
        assert 0.0 <= layer["weakest_resist_pct"] <= 100.0


def test_disabled_rule_is_skipped(ctx: ViewContext) -> None:
    rules = (D.Rule(id="resource_overflow", view="resources", enabled=False, message="x"),)
    assert D.evaluate(ctx, rules=rules) == []


def test_unimplemented_rule_id_fails_loudly(ctx: ViewContext) -> None:
    rules = (D.Rule(id="no-such-rule", view="resources", enabled=True, message="x"),)
    with pytest.raises(KeyError):
        D.evaluate(ctx, rules=rules)


# ---- 命中路径：样本上未命中的规则，用极简配装证明它们不是死规则 ----

MINIMAL_EFT = "[Hecate, 极简]\nDamage Control II"
"""只装一件 DC II：高槽全空、无推进模块、CPU/PG 余量远大于一半。"""

HARD_POINT_EFT = "[Hecate, 炮位]\nDamage Control II\nLight Neutron Blaster II"
"""一件 DC II + 一门炮：`empty_hardpoint` 的命中形态。

**T-54 审查改判**：门槛收窄成「**已经在用**（`used > 0`）且没装满（`free > 0`）」的硬点类，
所以「高槽全空、一件武器都没装」的 `MINIMAL_EFT` 不该再命中它（纯导弹船 / 无人机船那次
是噪音，判据见 `project/diagnostics._rule_empty_hardpoint`）。要证明它不是死规则，
就得给它一个**已经在用炮位**的配装 —— 只加一门炮，其余不变。
"""

CAP_SENSITIVE_EFT = (
    "[Hecate, 电容敏感]\n"
    "50MN Quad LiF Restrained Microwarpdrive\n"
    "Light Neutron Blaster II\n"
    "Light Neutron Blaster II\n"
    "Light Neutron Blaster II"
)
"""大号 MWD + 3 门炮：电容不稳定，但关掉 MWD 就稳定（`cap_sensitive` 的命中形态）。

**为什么从 5MN 换成 50MN（T-15 / 新技能档）**：全 4 档把电容能力拉高了（`Capacitor
Systems Operation` / `Capacitor Management` 4 级），5MN MWD 的配装实测已经稳定
（`capStablePct` ≈ +62%），规则自然不命中；换成 50MN 后实测 `capStablePct = 0`
（`capDepletesIn` 30 s，真不稳定），关闭 MWD 后回到 +92.2%——规则要检的正是这个翻转。
这不是放宽断言：断言仍是「摘掉的那个必须是 MWD、前后余量必须一负一正」。
"""


@pytest.fixture(scope="module")
def ctx_from_eft(engine: Engine, index):
    def factory(text: str) -> ViewContext:
        fit = engine.load_eft(text)
        fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
        return ViewContext(
            engine=engine,
            index=index,
            fit=fit,
            assumptions=default_assumptions(fit, engine, index=index),
            meta=engine.meta(),
        )

    return factory


def test_slack_empty_hardpoint_and_no_propulsion_can_fire(ctx_from_eft) -> None:
    """三条规则都必须能命中（否则就是死规则）。

    `empty_hardpoint` 换用 `HARD_POINT_EFT`（装了 1 门炮、4 个炮位还空）：T-54 审查把它的门槛
    收窄成「已经在用且没装满」，所以「一件武器都没装」的极简配装**不该**再命中它。
    """
    flags = {flag["id"]: flag for flag in D.evaluate(ctx_from_eft(MINIMAL_EFT))}

    assert {"resource_slack", "no_propulsion"} <= set(flags)
    assert flags["no_propulsion"]["evidence"]["propulsion_modules"] == 0
    assert flags["resource_slack"]["evidence"]["empty_slots"] > 0
    assert "empty_hardpoint" not in flags, "一件武器都没装 → 不报「有空炮位」（这是设计选择）"

    point_flags = {flag["id"]: flag for flag in D.evaluate(ctx_from_eft(HARD_POINT_EFT))}
    assert "empty_hardpoint" in point_flags, "装了炮却没装满 + 高槽有空位 → 必须报"
    assert point_flags["empty_hardpoint"]["evidence"]["high_empty"] > 0
    assert point_flags["empty_hardpoint"]["evidence"]["turret_used"] == 1


def test_cap_sensitive_hit_path(ctx_from_eft) -> None:
    """关掉耗电最高的模块后电容转为稳定；被摘掉的应当正是 MWD。"""
    flags = {flag["id"]: flag for flag in D.evaluate(ctx_from_eft(CAP_SENSITIVE_EFT))}
    evidence = flags["cap_sensitive"]["evidence"]

    assert evidence["stable_pct_before"] <= 0.0
    assert evidence["stable_pct_after"] > 0.0
    assert "Microwarpdrive" in evidence["module"]
    assert evidence["candidates_checked"] >= 1


# =============================================================================
# 六、unknown 的原因（T-32：`project/reasons.py` 的纯函数判定）
# =============================================================================

REASON_PRIORITY_CASES: tuple[tuple[str, str, int | None, tuple[str, ...], str], ...] = (
    # (路径, 视角, 来源属性, flag id 序列, 期望 reason)
    # ---- 规则①：flag 直接解释该字段（最高优先级）----
    ("dph", "firepower", A.ATTR_DAMAGE_ALPHA, ("weapon_damage_zero",), "input_incomplete"),
    # ---- 规则②：条件字段（命中即停；即使来源是「引擎永不提供」的属性也不改判）----
    ("dph", "firepower", A.ATTR_DAMAGE_ALPHA, (), "not_applicable"),
    ("application.tracking_rad_s", "firepower", A.ATTR_MAX_TARGETS, (), "not_applicable"),
    ("tier_violations[].rule.available", "constraints", None, (), "not_applicable"),
    ("consumables.paste_endurance_s", "stability", None, (), "not_applicable"),
    # ---- 规则③：来源属性在「引擎永不提供」清单（**优先于**规则④）----
    ("thinnest.hull.damage_type", "survivability", A.ATTR_MAX_TARGETS, (), "engine_omits"),
    # ---- 规则④：多值并列，无唯一答案 ----
    ("thinnest.hull.damage_type", "survivability", None, (), "ambiguous"),
    ("thinnest.shield.ehp", "survivability", None, (), "ambiguous"),
    ("some.future.damage_type", "firepower", None, (), "ambiguous"),
    # ---- 规则⑤：兜底（出现即缺陷）----
    ("no.such.path", "firepower", None, (), "unclassified"),
    ("no.such.path", "firepower", A.ATTR_DAMAGE_ALPHA, (), "unclassified"),
)
"""判定优先级的**逐条**对照表（命中即停：顺序错了这里立刻红）。

刻意把「同一路径、不同输入 → 不同结论」的成对用例都放进来：
`dph` 有无 flag、`thinnest.hull.damage_type` 的来源是 `None` 还是 `-70` ——
只测单向的话，「规则没接上」和「规则接反了」这两种缺陷分不开。
"""


@pytest.mark.parametrize(("path", "view", "source", "flags", "expected"), REASON_PRIORITY_CASES)
def test_reason_priority_is_hit_and_stop(
    path: str, view: str, source: int | None, flags: tuple[str, ...], expected: str
) -> None:
    """`reasons.reason_for()` 按规格 §0.2.1 的顺序「命中即停」。"""
    reason = R.reason_for(path, view=view, source=source, flags=flags)

    assert reason == expected, (
        f"{view}.{path}（source={source} flags={flags}）应为 {expected}，实为 {reason}"
    )
    assert reason in R.UNKNOWN_REASONS


def test_engine_omits_list_is_pinned_to_the_static_table() -> None:
    """「引擎永不提供」清单必须与常量表 / 三态表**同源**（三处不得漂移）。"""
    from noobbaozi.project import provenance as P

    assert R.ENGINE_OMITS_ATTRS == frozenset({A.ATTR_MAX_TARGETS})
    for attribute_id in R.ENGINE_OMITS_ATTRS:
        assert attribute_id in A.ATTRIBUTES, f"{attribute_id} 没进常量表（红线 N12）"
        assert attribute_id in P.UNKNOWN_ATTRS, f"{attribute_id} 在静态表里不是 unknown"
        # 派生负 ID 取证：SDE 属性表里查不到（`Index.attribute()` 返回 None）
        assert AD.name_of(attribute_id) == "maxTargets", (
            "名字必须来自常量表（SDE 无记录），不能是 attr_<id> 兜底名"
        )


def test_reason_helpers_on_sparse_tables() -> None:
    """`unclassified()` / `distribution()`：守门与报表的判据（空表也要给满键）。"""
    table = {"a": "not_applicable", "b": "ambiguous", "c": "unclassified"}

    assert R.unclassified(table) == ["c"]
    assert R.unclassified({}) == []
    assert R.distribution(table) == {
        "input_incomplete": 0,
        "not_applicable": 1,
        "engine_omits": 0,
        "ambiguous": 1,
        "unclassified": 1,
    }
    assert R.distribution({}) == {reason: 0 for reason in R.UNKNOWN_REASONS}
