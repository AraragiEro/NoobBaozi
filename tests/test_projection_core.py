"""T1/T2 测试：P2 投影层基础设施（units / provenance / ViewContext / diagnostics）。

覆盖任务书要求：

1. `units.physical` 对 51 / 160 / 1985 的换算（2.226068 / 0.39167 / 10.0），
   并证明它**没有另写一套缩放表**（逐项与 `attribute_dict.to_physical` 对拍）；
2. `provenance.tag` 三态抽样：263→measured、-43→assumed、-70→unknown、未知 ID→unknown；
   并钉住**实测表**的三条结构性质：measured ∩ assumed 为空、负 ID 常量不得 measured、
   `audit()` 必须自报 `derived_at` / `sample`；
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

from evefit.config import attributes as A
from evefit.config.paths import db_path
from evefit.engine.adapter import Engine
from evefit.engine.assumptions import default_assumptions, derive_skill_tier
from evefit.project import diagnostics as D
from evefit.project import provenance as P
from evefit.project import units as U
from evefit.project.views.base import (
    BASE,
    OFFLINE,
    OVERLOAD,
    VARIANTS,
    ViewContext,
    ship_attributes,
)
from evefit.sde import attribute_dict as AD
from evefit.sde.index import Index

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
        pytest.skip("索引库未构建：先跑 evefit.sde.ingest.build_index()")
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

    这四条是**实测结论**，不是拍脑袋：263 三轴不变（measured）；-43 随技能档与
    伤害剖面变（assumed）；-70 恒 0 属不可知（unknown，手工覆盖）；表外 ID 默认
    unknown。SDE / 引擎升级后重跑脚本，这条会随表一起变。
    """
    assert P.tag(A.ATTR_SHIELD_CAPACITY) == "measured"  # 263
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
    """`audit()` 必须自报「哪天、用哪份样本」跑出来的表（否则无法判断是否过期）。"""
    audit = P.audit()

    assert isinstance(audit["derived_at"], str) and audit["derived_at"]
    assert audit["derived_at"] == P.DERIVED_AT
    assert "kill.json" in audit["sample"] and "139246907" in audit["sample"]
    assert audit["sample"] == P.DERIVED_SAMPLE
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
    shield = P.typed(A.ATTR_SHIELD_CAPACITY, 650.0)
    assert shield["provenance"] == "measured"
    assert shield["unit"] == "hp"
    assert math.isclose(shield["value"], 650.0, rel_tol=1e-12)

    # 单位确实给不出时必须留空，不得猜一个（红线 N4 的同一条精神）。
    # 1997 modeEmResistancePostDiv：本样本无 mode → 实测扫不到 → unknown，单位也留空。
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
    """极简配装必须同时命中这三条（否则就是死规则）。"""
    flags = {flag["id"]: flag for flag in D.evaluate(ctx_from_eft(MINIMAL_EFT))}

    assert {"resource_slack", "empty_hardpoint", "no_propulsion"} <= set(flags)
    assert flags["no_propulsion"]["evidence"]["propulsion_modules"] == 0
    assert flags["empty_hardpoint"]["evidence"]["high_empty"] > 0
    assert flags["resource_slack"]["evidence"]["empty_slots"] > 0


def test_cap_sensitive_hit_path(ctx_from_eft) -> None:
    """关掉耗电最高的模块后电容转为稳定；被摘掉的应当正是 MWD。"""
    flags = {flag["id"]: flag for flag in D.evaluate(ctx_from_eft(CAP_SENSITIVE_EFT))}
    evidence = flags["cap_sensitive"]["evidence"]

    assert evidence["stable_pct_before"] <= 0.0
    assert evidence["stable_pct_after"] > 0.0
    assert "Microwarpdrive" in evidence["module"]
    assert evidence["candidates_checked"] >= 1
