"""P3 `explain`（归因引擎）测试：A 可加 / B 乘性 / C 投影三类，各有独立的验收口径。

**为什么要分三类**：`Σ contribution == value` 只对 A 类成立；B 类是乘法链（因子相加没有意义）；
C 类是投影层自己算的（引擎没有这个属性）。把三类混成一个判据，验收会立刻变成永真或永假。

样本：
- `f1-hecate-killmail.eft`（单武器 / 单抗性模块，A + B 两类都有）
- `f7-t3c-legion.json`（DC II + Reactive Armor Hardener + 2× Multispectrum Membrane，
  含**堆叠惩罚**与**效应型**抗性模块 —— C 类与 X4 的取证样本）

本文件无网络（内联样本 + 本地 SDE 索引）。
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from noobbaozi import api
from noobbaozi.config import attributes as A
from noobbaozi.engine import adapter
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier
from noobbaozi.project import explain
from noobbaozi.project.views.base import ViewContext
from noobbaozi.sde.index import Index

FIXTURES = Path(__file__).resolve().parents[1] / "acceptance" / "fixtures"
ADDITIVE_METRICS = (
    "survivability.ehp_mixed",
    "firepower.dps.base",
    "resources.cpu.load",
    "resources.powergrid.load",
)
MULTIPLICATIVE_METRICS = (
    "mobility.speed_mps",
    "stability.capacitor.peak_recharge",
)
PROJECTION_METRICS = (
    "survivability.thinnest.shield.ehp",
    "survivability.thinnest.armor.ehp",
    "survivability.thinnest.hull.ehp",
)
REL = 1e-9


# =============================================================================
# fixtures
# =============================================================================


@pytest.fixture(scope="module")
def engine() -> adapter.Engine:
    return adapter.Engine()


@pytest.fixture(scope="module")
def index(engine):
    from noobbaozi.config.paths import db_path

    try:
        opened = Index.open(db_path())
    except FileNotFoundError:  # pragma: no cover - 环境缺库时跳过
        pytest.skip("索引库未构建：先跑 noobbaozi.sde.ingest.build_index()")
    yield opened
    opened.close()


def _ctx(engine, index, name: str) -> ViewContext:
    text = (FIXTURES / name).read_text(encoding="utf-8")
    if name.endswith(".eft"):
        fit = engine.load_eft(text)
    else:
        raw = json.loads(text)
        if isinstance(raw, list):
            raw = raw[0]
        raw.pop("zkb", None)
        fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


@pytest.fixture(scope="module")
def hecate(engine, index) -> ViewContext:
    return _ctx(engine, index, "f1-hecate-killmail.eft")


@pytest.fixture(scope="module")
def legion(engine, index) -> ViewContext:
    return _ctx(engine, index, "f7-t3c-legion.json")


@pytest.fixture(scope="module")
def damnation(engine, index) -> ViewContext:
    """f14（Command Damnation）：多模块堆叠惩罚的**二次验证**样本。"""
    return _ctx(engine, index, "f14-command-damnation.json")


@pytest.fixture(scope="module")
def empty_fit(engine, index) -> ViewContext:
    """f58：**0 模块**（只有船头一行）。"""
    return _ctx(engine, index, "f58-empty-fit.eft")


@pytest.fixture(scope="module")
def drones_only(engine, index) -> ViewContext:
    """f29：火力全来自无人机（`-12` 含无人机，见 `views/firepower.py:69-70`）。"""
    return _ctx(engine, index, "f29-drones-only.json")


@pytest.fixture(scope="module")
def no_charge(engine, index) -> ViewContext:
    """f65：有武器、**没有弹药**（`dps.base` 该是 0，不是「取不到」）。"""
    return _ctx(engine, index, "f65-weapon-no-charge.eft")


@pytest.fixture(autouse=True)
def _fresh_cache():
    """每个用例前清 LRU（否则 monkeypatch / 第二个样本会命中旧结果，断言变永真）。"""
    explain.clear_cache()
    yield
    explain.clear_cache()


def _sum_contributions(result: dict) -> float:
    return sum(
        entry["contribution"] for entry in result["sources"] if entry["contribution"] is not None
    )


def _close(left: float, right: float, rel: float = REL) -> bool:
    return math.isclose(left, right, rel_tol=rel, abs_tol=1e-9)


# =============================================================================
# 一、A 类可加：Σ == value，且每一项都必需（反向检查）
# =============================================================================


@pytest.mark.parametrize("metric", ADDITIVE_METRICS)
@pytest.mark.parametrize("sample", ("hecate", "legion"))
def test_additive_metrics_sum_to_value(metric: str, sample: str, request) -> None:
    """A 类：`Σ contribution == value`（相对 1e-9），且 `unexplained` 是**真 0**。"""
    ctx = request.getfixturevalue(sample)
    result = explain.explain(ctx, metric)
    if result is None:
        pytest.skip(f"{sample} 没有 {metric}（无武器/无人机时该指标不适用）")

    assert result["combine"] == "additive"
    assert result["includes_base"] is True
    assert result["base"] == 0.0, "A 类的基值就是 0（值全部由贡献项相加得到）"
    assert result["sources"], "A 类必须有贡献者"
    total = _sum_contributions(result)
    assert _close(total, result["value"]), f"Σ={total!r} != value={result['value']!r}"
    assert result["unexplained"] == 0.0
    assert result["additive_only"] is True
    assert result["truncated_at_depth"] == 2


@pytest.mark.parametrize("metric", ADDITIVE_METRICS)
def test_additive_contributors_are_all_necessary(metric: str, hecate: ViewContext) -> None:
    """**反向检查**（测试员要求）：去掉任一 contributor，和都**不再等于** value。

    这条只有在「返回的 sources 里没有零项」时才成立 —— 零项去掉后和不变，
    正是「多算一项恰好抵消」骗过验收的方式。所以同时断言每项非零。
    """
    result = explain.explain(hecate, metric)
    if result is None:
        pytest.skip(f"f1 没有 {metric}")

    value = result["value"]
    total = _sum_contributions(result)
    assert _close(total, value)

    for position, entry in enumerate(result["sources"]):
        assert entry["contribution"] not in (None, 0.0), (
            f"第 {position} 项贡献为 0/None —— 反向检查会被它骗过（应该在 folded_noops 里）"
        )
        remaining = total - entry["contribution"]
        assert not _close(remaining, value), (
            f"去掉第 {position} 项后 Σ={remaining!r} 仍等于 value —— 这一项不是必需的"
        )


@pytest.mark.parametrize("metric", ADDITIVE_METRICS)
def test_additive_contributions_share_one_sign(metric: str, hecate: ViewContext) -> None:
    """A 类**同号**（实测 0 例外）：异号就说明出现了 offset，`has_offsets` 必须为真。"""
    result = explain.explain(hecate, metric)
    if result is None:
        pytest.skip(f"f1 没有 {metric}")

    signs = {entry["sign"] for entry in result["sources"] if entry["contribution"] is not None}
    assert len(signs) == 1, f"贡献出现异号 {signs}：契约要求 has_offsets + role=offset"
    assert result["has_offsets"] is False
    assert all(entry["role"] == "primary" for entry in result["sources"])


def test_dps_base_declares_the_drone_part(hecate: ViewContext, legion: ViewContext) -> None:
    """`dps.base`（`-12`）**已含**无人机/舰载机 —— 归因必须把小计说出来，否则 agent 会重复相加。"""
    for ctx, has_drones in ((hecate, False), (legion, True)):
        result = explain.explain(ctx, "firepower.dps.base")
        assert result is not None
        assert result["includes_drones"] is True
        subtotals = result["subtotals"]
        assert set(subtotals) == {"weapons", "drones", "fighters"}
        if has_drones:
            assert subtotals["drones"] > 0.0, "f7 带 4 组无人机"
        else:
            assert subtotals["drones"] == 0.0, "f1 没有无人机（反向：小计不是凭空写死的）"
            assert subtotals["weapons"] == pytest.approx(result["value"], rel=1e-12)
        assert _close(
            subtotals["weapons"] + subtotals["drones"] + subtotals["fighters"], result["value"]
        )


# =============================================================================
# 二、B 类乘性：给 base + 有序因子链 + 回放结果
# =============================================================================


@pytest.mark.parametrize("metric", MULTIPLICATIVE_METRICS)
@pytest.mark.parametrize("sample", ("hecate", "legion"))
def test_multiplicative_metrics_replay_to_value(metric: str, sample: str, request) -> None:
    """B 类：`value == base × Π factor`（回放），`replay_ok` 必须为真。"""
    ctx = request.getfixturevalue(sample)
    result = explain.explain(ctx, metric)
    assert result is not None, f"{sample} 缺 {metric}"

    assert result["combine"] == "multiplicative"
    assert result["includes_base"] is True
    assert result["base"] is not None
    assert result["sources"], "B 类必须有因子链"
    assert all(entry["contribution"] is None for entry in result["sources"]), (
        "乘性因子**不是贡献量**，不许给 contribution（相加没有意义）"
    )
    assert all(entry["factor"] is not None for entry in result["sources"])
    assert result["replay_ok"] is True
    assert _close(result["replayed"], result["value"])
    assert result["unexplained"] == 0.0
    # 「有序」不是装饰：因子按 dogma 的算子阶段排列
    order = [explain._OP_ORDER.index(entry["operator"]) for entry in result["sources"]]
    assert order == sorted(order)


def test_speed_factor_chain_is_the_measured_one(hecate: ViewContext) -> None:
    """f1 实测：`180 × (1 + 5.8645) × 1.20 = 1482.735`（两个 `post_percent` 因子）。"""
    result = explain.explain(hecate, "mobility.speed_mps")
    assert result["base"] == pytest.approx(180.0)
    percents = sorted(
        entry["factor"] for entry in result["sources"] if entry["operator"] == "post_percent"
    )
    assert percents == pytest.approx([20.0, 586.4516128647468], rel=1e-9)
    expected = 180.0 * (1.0 + 586.4516128647468 / 100.0) * (1.0 + 20.0 / 100.0)
    assert result["replayed"] == pytest.approx(expected, rel=1e-12)
    assert result["value"] == pytest.approx(1482.7354837878534, rel=1e-12)


def test_identity_factors_are_folded_away(legion: ViewContext) -> None:
    """恒等因子（`×1.0`）必须进 `folded_noops`，不许出现在返回的链里。

    f7 的 `speed_mps` 源里就有一条 `post_mul 1.0`（某个模块的 ×1.0 修饰）。
    """
    result = explain.explain(legion, "mobility.speed_mps")
    assert all(entry["factor"] != 1.0 for entry in result["sources"])
    assert result["folded_noops"] >= 1, "那条 ×1.0 必须被计数"


# =============================================================================
# 三、C 类投影：公式项 + depth-2 展开各自的引擎源
# =============================================================================


@pytest.mark.parametrize("metric", PROJECTION_METRICS)
def test_projection_formula_reproduces_the_value(metric: str, legion: ViewContext) -> None:
    """C 类：`value = 该层 HP ÷ 该层最大共振率`（公式项 + 回放都要对得上）。"""
    result = explain.explain(legion, metric)
    assert result is not None
    assert result["combine"] == "multiplicative"
    assert result["formula"]
    assert [entry["kind"] for entry in result["sources"]] == ["projection", "projection"]

    hp_entry, resonance_entry = result["sources"]
    assert hp_entry["operator"] == "pre_assign"
    assert resonance_entry["operator"] == "post_div"
    assert hp_entry["contribution"] == result["base"]
    assert result["replayed"] == pytest.approx(
        hp_entry["contribution"] / resonance_entry["factor"], rel=1e-12
    )
    assert _close(result["replayed"], result["value"])
    assert result["replay_ok"] is True


def test_projection_value_matches_the_view_rounding(hecate: ViewContext) -> None:
    """`explain.value` 是**全精度**；与 L1 的 3 位小数只该差在舍入位（相对 1e-6 内）。"""
    from noobbaozi.project import summary

    result = explain.explain(hecate, "survivability.thinnest.armor.ehp")
    view_value = summary.get_path(hecate, "survivability.thinnest.armor.ehp")["value"]
    assert result["value"] == pytest.approx(view_value, rel=1e-6)


def test_projection_tie_is_reported_honestly(hecate: ViewContext) -> None:
    """结构层四系并列（DC II 压平）：`damage_type` 为 None、`tied` 为真，标签说「四系并列」。"""
    result = explain.explain(hecate, "survivability.thinnest.hull.ehp")
    assert result["tied"] is True
    assert result["damage_type"] is None
    assert "四系并列" in result["sources"][1]["label"]


# =============================================================================
# 四、X4（I5 那 11 项的根因）：效应型抗性模块能在 depth-2 里看见
# =============================================================================


def test_x4_effect_type_hardener_is_visible_at_depth_two(legion: ViewContext) -> None:
    """f7 护甲共振链必须完整列出 4 条源：两件 `pre_mul`（含堆叠惩罚）+ 两件 `post_percent`。

    X4 的根因就是这一层：效应型模块（Multispectrum / Reactive Armor Hardener）**不改基础共振率**，
    所以 I5「基础 × 模块共振率」永远对不上。`explain` 把它们放在 `post_percent` 源里，
    堆叠惩罚（0.8691）也在源级可见。
    """
    result = explain.explain(legion, "survivability.thinnest.armor.ehp")
    resonance = result["sources"][1]
    children = resonance["children"]
    assert len(children) == 4, f"护甲共振应有 4 条源，实际 {len(children)}"

    pre = [child for child in children if child["operator"] == "pre_mul"]
    post = [child for child in children if child["operator"] == "post_percent"]
    assert len(pre) == 2 and len(post) == 2

    assert any("Damage Control" in child["label"] for child in pre)
    assert any("Reactive Armor Hardener" in child["label"] for child in children), (
        "效应型模块必须出现（它才是 I5 对不上的原因）"
    )
    penalties = {child["penalty"] for child in children}
    assert 1.0 in penalties and any(
        penalty is not None and not math.isclose(penalty, 1.0) for penalty in penalties
    ), f"第二件同族必须带堆叠惩罚，实际 {penalties}"
    assert all(child["factor"] is not None for child in children)
    assert {child["depth"] for child in children} == {2}


# =============================================================================
# 五、`penalty` 折算的二次验证（f14 Command Damnation）
# =============================================================================


def test_penalty_conversion_is_verified_on_f14(damnation: ViewContext) -> None:
    """f14 的 12 个逐系共振属性回放必须精确（相对 1e-9）—— `penalty` 折算公式的二次验证。

    公式（实验推导，f7 单点 + f14 全量）：
    - `pre_mul` / `post_mul`：有效因子 `1 + (v − 1) × penalty`（v < 1 时是「按惩罚缩放折扣」）
    - `post_percent`：`× (1 + v/100 × penalty)`

    f7 只有护甲层带 `penalty = 0.8691`；f14 的护甲四系全带，且四系**方向不同**，
    所以这是比 f7 更强的验证（单点验证不足以当契约）。
    """
    calculation = damnation.engine.calculate(damnation.fit, sources=True)
    attributes = calculation["ship"]["attributes"]
    checked = 0
    penalised = 0
    for attribute_id in (
        A.ATTR_SHIELD_EM_RESONANCE,
        A.ATTR_SHIELD_EXPLOSIVE_RESONANCE,
        A.ATTR_SHIELD_KINETIC_RESONANCE,
        A.ATTR_SHIELD_THERMAL_RESONANCE,
        A.ATTR_ARMOR_EM_RESONANCE,
        A.ATTR_ARMOR_EXPLOSIVE_RESONANCE,
        A.ATTR_ARMOR_KINETIC_RESONANCE,
        A.ATTR_ARMOR_THERMAL_RESONANCE,
        A.ATTR_HULL_EM_RESONANCE,
        A.ATTR_HULL_EXPLOSIVE_RESONANCE,
        A.ATTR_HULL_KINETIC_RESONANCE,
        A.ATTR_HULL_THERMAL_RESONANCE,
    ):
        node = attributes.get(attribute_id)
        if not isinstance(node, dict):
            continue
        kept, _folded = explain._filter_sources(node.get("sources") or [])
        replayed = explain._replay(float(node.get("base") or 0.0), kept)
        expected = float(node.get("value") or 0.0)
        assert math.isclose(replayed, expected, rel_tol=REL, abs_tol=1e-12), (
            f"attr {attribute_id}: 回放 {replayed!r} != 引擎 {expected!r}"
        )
        checked += 1
        if any(
            source.get("penalty") not in (None, 1.0)
            for source in kept
        ):
            penalised += 1

    assert checked == 12, "三层四系共振必须全部验过"
    assert penalised == 4, "f14 的护甲四系各带一条 penalty=0.8691 的源"


# =============================================================================
# 六、过滤与最小性（`folded_noops` / 反向检查的前提）
# =============================================================================


@pytest.mark.parametrize("metric", ADDITIVE_METRICS)
def test_folded_noops_are_counted_and_gone(metric: str, hecate: ViewContext) -> None:
    """零项 / 恒等项被滤掉并**报数**：返回的项里再没有 0 贡献、没有 ×1 因子。"""
    result = explain.explain(hecate, metric)
    if result is None:
        pytest.skip(f"f1 没有 {metric}")

    assert isinstance(result["folded_noops"], int) and result["folded_noops"] >= 0
    for entry in result["sources"]:
        if entry["contribution"] is not None:
            assert entry["contribution"] != 0.0
        if entry["factor"] is not None:
            assert entry["factor"] != 1.0


def test_cpu_load_really_had_zero_noops(hecate: ViewContext) -> None:
    """f1 的 `cpu.load` 实测有 **3 条零项**（引擎为贡献 0 的模块也发源）—— 过滤必须生效。"""
    result = explain.explain(hecate, "resources.cpu.load")
    assert result["folded_noops"] == 3
    assert result["minimality"] == "filtered_noops"
    assert result["truncated_nodes"] == 0


# =============================================================================
# 六、深度 / 预算 / 边界
# =============================================================================


def test_depth_one_does_not_expand_children(hecate: ViewContext) -> None:
    """`depth=1`：只给直接贡献者，每项 `truncated: true`、`children == []`。"""
    result = explain.explain(hecate, "survivability.ehp_mixed", depth=1)
    assert result["truncated_at_depth"] == 1
    for entry in result["sources"]:
        assert entry["children"] == []
        assert entry["truncated"] is True
    assert _close(_sum_contributions(result), result["value"]), "depth 不影响加和"


def test_depth_two_expands_children(hecate: ViewContext) -> None:
    """`depth=2`：每个贡献者的子源都展开（EHP 的每层 = 容量 + 共振）。"""
    result = explain.explain(hecate, "survivability.ehp_mixed", depth=2)
    for entry in result["sources"]:
        assert entry["children"], f"{entry['label']} 应该有 depth-2 子源"
        assert entry["truncated"] is False
        assert {child["depth"] for child in entry["children"]} == {2}


@pytest.mark.parametrize("depth", (0, 3, -1, None, True, "2"))
def test_depth_out_of_range_is_rejected(depth: object, hecate: ViewContext) -> None:
    """深度上限 2 是**设计边界**：越界直接拒绝，不静默截断。"""
    with pytest.raises(ValueError):
        explain.explain(hecate, "survivability.ehp_mixed", depth=depth)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "metric",
    (
        "survivability.no_such_field",
        "survivability.layers.shield_hp",  # 路径**存在**，但不是归因登记表里的指标
        "resources.cpu.free",
        "",
        None,
    ),
)
def test_unsupported_metric_returns_none(metric: object, hecate: ViewContext) -> None:
    """不在登记表里的指标返回 `None`（由接线层报 `invalid_path`），不编空壳。"""
    assert explain.explain(hecate, metric) is None  # type: ignore[arg-type]


def test_supported_metrics_are_all_explainable(hecate: ViewContext) -> None:
    """`supported_metrics()` 是**可执行**的清单：报出来的每个都要能算。"""
    metrics = explain.supported_metrics()
    assert len(metrics) == len(set(metrics)) == 9
    for metric in metrics:
        assert explain.explain(hecate, metric) is not None, metric


# =============================================================================
# 七、缓存（2.95 ms/次的调用不该被重复付钱）
# =============================================================================


def test_cache_avoids_rebuilding(monkeypatch: pytest.MonkeyPatch, hecate: ViewContext) -> None:
    """同 (fit, metric, depth) 第二次走缓存：`_build` 只被调用一次。"""
    calls: list[str] = []
    real_build = explain._build

    def counting_build(ctx, metric, depth):  # noqa: ANN001 - 探针
        calls.append(metric)
        return real_build(ctx, metric, depth)

    monkeypatch.setattr(explain, "_build", counting_build)

    first = explain.explain(hecate, "survivability.ehp_mixed")
    second = explain.explain(hecate, "survivability.ehp_mixed")
    assert first == second
    assert calls == ["survivability.ehp_mixed"], "第二次必须命中缓存"

    explain.explain(hecate, "survivability.ehp_mixed", depth=1)
    assert len(calls) == 2, "depth 不同是另一个 key"

    explain.clear_cache()
    explain.explain(hecate, "survivability.ehp_mixed")
    assert len(calls) == 3, "clear_cache 之后必须重建"


def test_cache_does_not_leak_between_fits(hecate: ViewContext, legion: ViewContext) -> None:
    """不同配装的同名指标必须各算各的（key 含 fit 内容哈希）。"""
    first = explain.explain(hecate, "survivability.ehp_mixed")
    second = explain.explain(legion, "survivability.ehp_mixed")
    assert first["value"] != second["value"]
    assert first["sources"] != second["sources"]


# =============================================================================
# 八、非永真：把一项贡献改 10%，正向与反向检查都要红
# =============================================================================


def test_checks_reject_a_ten_percent_error(hecate: ViewContext) -> None:
    """**非永真证明**：篡改一项贡献 10% → 正向 Σ 立刻对不上；把一项改成 0 → 反向检查失效。

    这条不做，上文的正向/反向断言就可能是「无论数据多烂都绿」的空转。
    """
    result = explain.explain(hecate, "survivability.ehp_mixed")
    value = result["value"]

    tampered = copy.deepcopy(result)
    tampered["sources"][0]["contribution"] *= 1.10
    assert not _close(_sum_contributions(tampered), value), "正向检查必须变红"

    zeroed = copy.deepcopy(result)
    zeroed["sources"][1]["contribution"] = 0.0
    total = _sum_contributions(zeroed)
    # 零项的存在正是反向检查的盲区：去掉它和值不变（这正是过滤要解决的）
    assert _close(total - zeroed["sources"][1]["contribution"], total), "零项让反向检查失效"
    # 而真实输出里**没有**零项（见 test_folded_noops_are_counted_and_gone）
    assert all(
        entry["contribution"] != 0.0
        for entry in result["sources"]
        if entry["contribution"] is not None
    )


# =============================================================================
# 九、provenance 与 combine 正交（别拿三态判断可加性）
# =============================================================================


def test_provenance_is_reported_but_orthogonal(hecate: ViewContext) -> None:
    """`ehp_mixed` 的三态是 `assumed`（负 ID 派生值），但它**是**可加的。

    这条钉住一个容易误用的直觉：「assumed ⇒ 投影 ⇒ 不可加」是错的。
    """
    result = explain.explain(hecate, "survivability.ehp_mixed")
    assert result["provenance"] == "assumed", "−43 是引擎 pass-4 派生值 → 三态恒 assumed"
    assert result["combine"] == "additive"
    assert _close(_sum_contributions(result), result["value"])


def test_envelope_has_every_contract_field(hecate: ViewContext) -> None:
    """契约信封的必备字段一个都不能少（接线层与验收都按这个形状读）。"""
    required = {
        "metric",
        "view",
        "value",
        "combine",
        "includes_base",
        "base",
        "sources",
        "folded_noops",
        "unexplained",
        "truncated_at_depth",
        "truncated_nodes",
        "minimality",
        "has_offsets",
        "tolerance",
        "provenance",
    }
    for metric in explain.supported_metrics():
        result = explain.explain(hecate, metric)
        assert required <= set(result), f"{metric} 缺字段 {required - set(result)}"
        for entry in result["sources"]:
            assert {
                "kind",
                "role",
                "label",
                "type_id",
                "item_index",
                "attribute_id",
                "operator",
                "contribution",
                "sign",
                "factor",
                "penalty",
                "applied",
                "depth",
                "truncated",
                "children",
            } <= set(entry), f"{metric} 的 source 缺字段"


# =============================================================================
# 十、边界样本（0 模块 / 全 offline / 只 drone / 无 charge）
# =============================================================================


def test_zero_module_fit_keeps_additive_identity(empty_fit: ViewContext) -> None:
    """0 模块：`cpu.load` / `powergrid.load` 的值是 **0**，源链是空数组 —— 加和恒等式照样成立。

    「A 类还给不给项」的答案：**给一个空数组**（刻意不给零项，见 `folded_noops`），
    所以 `Σ contributions = 0`、`unexplained = 0`、`minimality = filtered_noops`。
    """
    for metric in ("resources.cpu.load", "resources.powergrid.load"):
        result = explain.explain(empty_fit, metric)
        assert result is not None, metric
        assert result["value"] == 0.0
        assert result["sources"] == []
        assert result["combine"] == "additive"
        assert result["sum_of_contributions"] == 0.0
        assert _close(_sum_contributions(result), result["value"])
        assert result["unexplained"] == 0.0
        assert result["minimality"] == "filtered_noops"


def test_zero_module_fit_still_explains_ship_body_metrics(empty_fit: ViewContext) -> None:
    """0 模块时，船体自带的指标仍然可归因（EHP / 速度 / 电容 / 三层投影）。"""
    for metric in (
        "survivability.ehp_mixed",
        "mobility.speed_mps",
        "stability.capacitor.peak_recharge",
        "survivability.thinnest.shield.ehp",
        "survivability.thinnest.armor.ehp",
        "survivability.thinnest.hull.ehp",
    ):
        result = explain.explain(empty_fit, metric)
        assert result is not None, metric
        assert result["value"] > 0.0
        assert result["unexplained"] == 0.0


def test_zero_module_fit_dps_has_no_engine_source_and_is_not_faked(
    empty_fit: ViewContext,
) -> None:
    """0 模块时 `firepower.dps.base` **没有引擎源** → `explain` 明明在登记表里也给 `None`。

    这是**刻意的差异**（不是 bug）：
    - `explain` 需要引擎 `sources`，没有武器的船体压根不发 `-12` 属性 → `invalid_path`；
    - `fit_get` 走投影层的 `firepower.dps` 字段，能给出 `0.0`（「确实没有火力」）。
    把 `explain` 补一个 0 空壳，就等于宣称「这个 0 可归因」，那是编。
    """
    assert (empty_fit.fit.get("items") or []) == []
    assert explain.explain(empty_fit, "firepower.dps.base") is None

    text = (FIXTURES / "f58-empty-fit.eft").read_text(encoding="utf-8")
    envelope = api.fit_explain(text, "firepower.dps.base")
    assert envelope.get("ok") is False
    assert envelope["error"]["kind"] == "invalid_path"

    get_envelope = api.fit_get(text, "firepower.dps.base")
    assert get_envelope.get("ok") is True, "投影层对「没有火力」是有答案的（0.0）"
    assert get_envelope["result"]["value"] == 0.0


def test_all_offline_fit_behaves_like_a_ship_without_modules(hecate: ViewContext) -> None:
    """全 offline ≡ 空配装：船体指标逐位相同（模块不生效时既不贡献也不生产）。

    唯一的例外是 `firepower.dps.base`：空配装**取不到**源（`None`），全 offline 配装取到
    `0.0`（引擎仍然发了那条 `-12`，只是所有来源都不生效）—— 两种「0」来源不同，都如实给。
    """
    items = [{**item, "state": "offline"} for item in hecate.fit["items"]]
    offline = ViewContext(
        engine=hecate.engine,
        index=hecate.index,
        fit={**hecate.fit, "items": items},
        assumptions=hecate.assumptions,
        meta=hecate.meta,
    )
    empty = _ctx(hecate.engine, hecate.index, "f58-empty-fit.eft")
    assert empty.fit["ship"]["type_id"] == offline.fit["ship"]["type_id"], "前提：同一条船"

    same = (
        "survivability.ehp_mixed",
        "resources.cpu.load",
        "resources.powergrid.load",
        "mobility.speed_mps",
        "stability.capacitor.peak_recharge",
        "survivability.thinnest.shield.ehp",
        "survivability.thinnest.armor.ehp",
        "survivability.thinnest.hull.ehp",
    )
    for metric in same:
        left = explain.explain(offline, metric)
        right = explain.explain(empty, metric)
        assert left is not None and right is not None, metric
        assert left["value"] == pytest.approx(right["value"], rel=REL), (
            f"{metric}: 全 offline={left['value']!r} vs 空配装={right['value']!r}"
        )
        assert left["unexplained"] == 0.0 and right["unexplained"] == 0.0

    assert explain.explain(offline, "firepower.dps.base")["value"] == 0.0
    assert explain.explain(empty, "firepower.dps.base") is None


def test_drones_only_dps_attribution_aligns(drones_only: ViewContext) -> None:
    """只有无人机：`dps.base` 的源**全部**来自无人机，`subtotals.drones == value`。"""
    result = explain.explain(drones_only, "firepower.dps.base")
    assert result is not None
    assert result["includes_drones"] is True
    assert _close(_sum_contributions(result), result["value"])
    assert result["unexplained"] == 0.0

    subtotals = result["subtotals"]
    assert subtotals["drones"] == pytest.approx(result["value"], rel=REL)
    assert subtotals["weapons"] == 0.0
    assert subtotals["fighters"] == 0.0
    assert sum(subtotals.values()) == pytest.approx(result["value"], rel=REL)
    assert all(entry["kind"] == "item" for entry in result["sources"]), "无人机是 item 源"


def test_weapon_without_charge_reports_zero_not_missing(no_charge: ViewContext) -> None:
    """有武器、无弹药：`dps.base` 是 **0.0**（引擎有源但贡献 0），不是「取不到」。"""
    result = explain.explain(no_charge, "firepower.dps.base")
    assert result is not None
    assert result["value"] == 0.0
    assert result["sources"] == []
    assert result["unexplained"] == 0.0
    assert result["subtotals"] == {"weapons": 0.0, "drones": 0.0, "fighters": 0.0}


# =============================================================================
# 十一、`supported_metrics()` 的每个串都必须是 `fit_get` 能解析的路径
# =============================================================================


def test_every_explain_metric_is_a_fit_get_path() -> None:
    """9 个指标串逐个过 `api.fit_get`：登记了却取不到 = 坏契约（agent 会直接踩空）。"""
    text = (FIXTURES / "f1-hecate-killmail.eft").read_text(encoding="utf-8")
    for metric in explain.supported_metrics():
        envelope = api.fit_get(text, metric)
        assert envelope.get("ok") is True, (
            f"{metric}: fit_get 拒绝了这个已登记的指标 → {envelope.get('error')}"
        )
        result = envelope["result"]
        assert result["path"] == metric
        assert result["value"] is not None
        assert result["provenance"] in {"measured", "assumed", "unknown"}


def test_explain_metric_contract_check_is_not_vacuous() -> None:
    """**非永真证明**：不存在的路径必须被拒绝（证明上一条不是恒真）。"""
    text = (FIXTURES / "f1-hecate-killmail.eft").read_text(encoding="utf-8")
    envelope = api.fit_get(text, "survivability.thinnest.shield.ehpX")
    assert envelope.get("ok") is False
    assert envelope["error"]["kind"] == "invalid_path"
