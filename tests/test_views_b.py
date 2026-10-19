"""T2/T3 测试：六视角里的 **资源 / 约束 / 不稳定** 三个（T-10）。

样本：`noobbaozi/spike/kill.json`（killmail 139246907，Hecate + 全 4 + 补最低技能档）。
覆盖任务书要求的断言：

1. 三个视角都返回 `{view, overview, flags}` 且 `flags == []`（诊断统一在渲染层算一次）；
2. `FIELD_SOURCES` 的每个 key 都能在 `overview` 里走到（`[]` 表示数组元素字段），
   且每个非 `None` 来源都是**已登记**的属性常量（防裸写属性 ID，红线 N12）；
3. 资源：`cpu.free == ship[-9]`、四类槽位齐全、`used` 由 items 数出来且 0 ≤ used ≤ total；
4. 约束：本样本 `tier_violations` 非空（CPU 超 11.0）、`hard_violations` 里不含
   `resource` / `skill` 类、`skill_floor.distinct_skills ≥ 1`；
5. 不稳定：`capacitor` 字段齐全、`cap_sensitivity` ≤ 5 项且每项带模块名与 `stable_pct`；
6. **缺值给 `None` 而不是 0**（红线 N4）：用一个「什么属性都没有」的 stub 上下文验证。

注意：`11.0` 这类是**与 SDE build + 技能档绑定的快照**（开发规范 §5.2 / §6.2）。
本文件在 **T-12 技能档修正后重标**（全 4 档 = 512 个技能）：CPU 仍超 11.0，
PG 从「超 18.0」变成「超 3.6」（64.8 输出 / 68.4 占用），全 5 档则刚好装满。
重新标定必须走 `docs/回归快照-changelog.md` 的流程，不得为了让测试通过而改基线。
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
from noobbaozi.project import render
from noobbaozi.project import units
from noobbaozi.project.views import build_view
from noobbaozi.project.views.base import BASE, ViewContext
from noobbaozi.sde.index import Index

# 三个视角模块必须显式 import 才会注册（注册表刻意不 import 它们，见 views/__init__.py）
import noobbaozi.project.views.constraints  # noqa: F401  （导入即注册）
import noobbaozi.project.views.resources  # noqa: F401
import noobbaozi.project.views.stability  # noqa: F401

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
KILL_PATH = NOOBBAOZI_ROOT / "spike" / "kill.json"

VIEW_NAMES = ("resources", "constraints", "stability")
FORBIDDEN_WORDS = ("非法", "不合规", "不合法")


# =============================================================================
# fixtures（写法同 tests/test_projection_core.py）
# =============================================================================


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
    """样本配装 + 「全 4 + 补最低」技能档（T-12 技能档修正后重标，512 个技能）。"""
    fit = dogma.load_killmail(killmail)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return fit


@pytest.fixture(scope="module")
def index():
    try:
        path = db_path()
    except FileNotFoundError:
        pytest.skip("索引库未构建：先跑 noobbaozi.sde.ingest.build_index()")
    opened = Index.open(path)
    yield opened
    opened.close()


@pytest.fixture(scope="module")
def ctx(engine: Engine, index, sample_fit: dict) -> ViewContext:
    return ViewContext(
        engine=engine,
        index=index,
        fit=sample_fit,
        assumptions=default_assumptions(sample_fit, engine, index=index),
        meta=engine.meta(),
    )


@pytest.fixture(scope="module")
def views(ctx: ViewContext) -> dict[str, dict]:
    return {name: build_view(name, ctx) for name in VIEW_NAMES}


# =============================================================================
# 结构工具
# =============================================================================


def _leaves(node: object, path: str = "") -> dict[str, object]:
    """把 overview 摊平成 `{点路径: 值}`；列表元素路径用 `名字[0]` 形式。"""
    flat: dict[str, object] = {}
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            flat[f"{child}"] = value  # 容器本身也可达（FIELD_SOURCES 可能指向整段）
            flat.update(_leaves(value, child))
        return flat
    if isinstance(node, (list, tuple)):
        for position, value in enumerate(node):
            flat.update(_leaves(value, f"{path}[{position}]"))
        return flat
    return flat


def _reachable(overview: dict, dotted: str) -> bool:
    """`FIELD_SOURCES` 的 key 是否可达；`[]` 段表示「数组元素字段」（任一元素命中即可）。

    例：`slots.high.used`、`cap_by_module[].stable_pct`（数组里只要有元素带这个键就算可达，
    因为引擎只会对触发的那类违规填 `required` / `level` 这类键）。
    """
    parts = dotted.split(".")

    def walk(node: object, index: int) -> bool:
        if index >= len(parts):
            return True
        part = parts[index]
        if part.endswith("[]"):
            key = part[: -len("[]")]
            if not isinstance(node, dict) or key not in node:
                return False
            items = node[key]
            if not isinstance(items, list):
                return False
            if not items:  # 空数组：字段本身存在即算可达
                return True
            return any(walk(item, index + 1) for item in items)
        if not isinstance(node, dict) or part not in node:
            return False
        return walk(node[part], index + 1)

    return walk(overview, 0)


def _field_sources(module) -> dict[str, int | None]:
    sources = getattr(module, "FIELD_SOURCES", None)
    assert isinstance(sources, dict) and sources, f"{module.__name__} 缺 FIELD_SOURCES"
    return sources


# =============================================================================
# 一、三视角公共契约
# =============================================================================


def test_three_views_registered_and_called() -> None:
    from noobbaozi.project.views import available_views

    names = set(available_views())
    assert set(VIEW_NAMES) <= names, f"三个视角必须已注册，实际：{sorted(names)}"


def test_three_views_shape_and_empty_flags(views: dict[str, dict]) -> None:
    """`{view, overview, flags}`；**flags 一律空列表**（诊断只在渲染层算一次）。"""
    for name in VIEW_NAMES:
        view = views[name]
        assert set(view) == {"view", "overview", "flags"}
        assert view["view"] == name
        assert isinstance(view["overview"], dict) and view["overview"]
        assert view["flags"] == [], f"{name} 不得自己算可疑点"


def test_field_sources_reachable_and_registered(views: dict[str, dict]) -> None:
    """每个 key 都能在 overview 里走到；非 None 来源必须是**已登记**属性常量。

    两类例外（都是本轮 T-20 引入的，且**各配一条更强的断言**，不是放宽）：

    1. `CONDITIONAL_SOURCES`（`resources` 的子系统槽）：只在 T3C 船上有叶子。本样本是
       Hecate（无子系统槽），所以这里反过来断言它**不可达**——「条件字段不得在无该槽位的
       船上凭空出现」比原来的无条件可达更强的约束；正向断言在
       `test_resources_subsystem_slots_only_on_t3c` 里用 Loki 样本做。
    2. `PENDING_ATTRIBUTES`：`config/attributes.py` 本轮只读，`maxSubSystems(1367)` 只能在
       视角模块里以具名常量 + 「待登记」注释声明。这里只放行**模块里显式登记的那几个值**，
       裸写数字照样抓得住。
    """
    import noobbaozi.project.views.constraints as C
    import noobbaozi.project.views.resources as R
    import noobbaozi.project.views.stability as S

    for module, name in ((R, "resources"), (C, "constraints"), (S, "stability")):
        overview = views[name]["overview"]
        conditional = getattr(module, "CONDITIONAL_SOURCES", frozenset())
        pending = set((getattr(module, "PENDING_ATTRIBUTES", {}) or {}).values())
        for dotted, attribute_id in _field_sources(module).items():
            if dotted in conditional:
                assert not _reachable(overview, dotted), (
                    f"{name}: 条件字段 {dotted!r} 不该出现在没有该槽位的船上"
                )
                continue
            assert _reachable(overview, dotted), f"{name}: FIELD_SOURCES 的 {dotted!r} 在 overview 里不可达"
            if attribute_id is None:
                continue
            assert isinstance(attribute_id, int), f"{name}.{dotted}: 来源必须是 int 或 None"
            # 红线 N12：来源必须是常量表里的常量（或本模块显式登记的待登记常量）
            assert attribute_id in A.ATTRIBUTES or attribute_id in pending, (
                f"{name}.{dotted}: 属性 {attribute_id} 未在常量表登记"
            )


def test_no_absolute_verdict_wording(views: dict[str, dict]) -> None:
    """措辞禁令：不得输出「配装非法」这类判词。"""
    text = json.dumps(views, ensure_ascii=False)
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"输出里出现了判词 {word!r}"


# =============================================================================
# 二、资源
# =============================================================================


def test_resources_free_matches_engine_attributes(views: dict[str, dict], ctx: ViewContext) -> None:
    """余量直取引擎派生值：`cpu.free == ship[-9]`、PG/-10、标定/-69。"""
    overview = views["resources"]["overview"]
    attrs = ctx.attrs(BASE)

    assert math.isclose(overview["cpu"]["free"], attrs[A.ATTR_CPU_FREE], rel_tol=1e-9)
    assert math.isclose(overview["powergrid"]["free"], attrs[A.ATTR_POWER_FREE], rel_tol=1e-9)
    assert math.isclose(overview["calibration"]["free"], attrs[A.ATTR_CALIBRATION_FREE], rel_tol=1e-9)

    # 余量必须是**绝对值**：只有 output / load / free 三个键，没有百分比字段
    for key, output_attr, load_attr in (
        ("cpu", A.ATTR_CPU_OUTPUT, A.ATTR_CPU_LOAD),
        ("powergrid", A.ATTR_POWER_OUTPUT, A.ATTR_POWER_LOAD),
        ("calibration", A.ATTR_CALIBRATION_OUTPUT, A.ATTR_CALIBRATION_LOAD),
    ):
        block = overview[key]
        assert set(block) == {"output", "load", "free"}
        assert math.isclose(block["output"], units.fmt(attrs[output_attr]), rel_tol=1e-12)
        assert math.isclose(block["load"], units.fmt(attrs[load_attr]), rel_tol=1e-12)
        assert math.isclose(block["free"], block["output"] - block["load"], rel_tol=1e-6, abs_tol=1e-3)


def test_resources_slots_are_complete_and_counted_from_items(
    views: dict[str, dict], ctx: ViewContext
) -> None:
    """四类槽位齐全；`used` 从 items 数出来，0 ≤ used ≤ total（不用总量−剩余反推）。"""
    overview = views["resources"]["overview"]
    attrs = ctx.attrs(BASE)
    slots = overview["slots"]

    assert set(slots) == {"high", "medium", "low", "rig"}
    expected_totals = {
        "high": attrs[A.ATTR_HI_SLOTS],
        "medium": attrs[A.ATTR_MED_SLOTS],
        "low": attrs[A.ATTR_LOW_SLOTS],
        "rig": attrs[A.ATTR_RIG_SLOTS],
    }
    fitted = [item for item in ctx.items(BASE) if item.get("slot_type") != "cargo"]
    for name, slot in slots.items():
        assert set(slot) == {"used", "total"}
        assert slot["total"] == int(round(expected_totals[name]))
        counted = sum(1 for item in fitted if item.get("slot_type") == name)
        assert slot["used"] == counted, f"{name} 的 used 必须来自 items 统计"
        assert 0 <= slot["used"] <= slot["total"]
        assert overview["empty_slots"][name] == slot["total"] - slot["used"]


def test_resources_hardpoints_drones_cargo(views: dict[str, dict], ctx: ViewContext) -> None:
    """炮位 / 无人机 / 货舱：`total` 来自船体属性，`used` 数 items，`free = total - used`。

    **T-54 改口径**：旧断言把 102 / 101（**船体基准炮位数**）直接当「剩余」，实测与装配
    无关（同一艘船装 0 / 3 / 5 门发射器都输出同一个数）。现在旧键 `hardpoints_free` 已删除，
    改成 `hardpoints.{turret,launcher}.{total,used,free}` + `unclassified` 件数。
    """
    overview = views["resources"]["overview"]
    attrs = ctx.attrs(BASE)
    hardpoints = overview["hardpoints"]

    assert "hardpoints_free" not in overview, "旧口径字段已删除（T-54）"
    assert hardpoints["turret"]["total"] == int(round(attrs[A.ATTR_TURRET_SLOTS_LEFT]))
    assert hardpoints["launcher"]["total"] == int(round(attrs[A.ATTR_LAUNCHER_SLOTS_LEFT]))
    for kind in ("turret", "launcher"):
        row = hardpoints[kind]
        assert set(row) == {"total", "used", "free"}
        assert row["used"] >= 0
        # free 是算出来的，**可以为负**（负数 = 非法装配），不许用 max(…, 0) 夹
        assert row["free"] == row["total"] - row["used"]
    # 已装的炮/发射器必须真的被数进去（样本 Hecate 装了 5 门 Light Neutron Blaster II）
    assert hardpoints["turret"]["used"] == 5
    assert isinstance(hardpoints["unclassified"], int) and hardpoints["unclassified"] >= 0

    bandwidth = overview["drone"]["bandwidth"]
    capacity = overview["drone"]["capacity"]
    assert math.isclose(bandwidth["total"], attrs[A.ATTR_DRONE_BANDWIDTH], rel_tol=1e-12)
    assert math.isclose(capacity["total"], attrs[A.ATTR_DRONE_CAPACITY], rel_tol=1e-12)
    # Hecate 没有无人机舱：0 是**真值**，不能变成 None
    assert bandwidth["used"] == 0.0 and capacity["used"] == 0.0

    cargo = overview["cargo"]
    assert math.isclose(cargo["total"], units.fmt(attrs[A.ATTR_CARGO_CAPACITY]), rel_tol=1e-12)
    assert cargo["used"] is not None and cargo["used"] > 0.0


def test_resources_upgrade_space_is_honest(views: dict[str, dict]) -> None:
    """升级空间：候选来自同组索引；本样本 PG 已超 → 没有任何候选装得下。"""
    entries = views["resources"]["overview"]["upgrade_space"]
    assert isinstance(entries, list)
    for entry in entries:
        assert entry["meta_level"] > entry["meta_level_now"], "候选 meta 必须更高"
        assert isinstance(entry["fits"], bool)
        assert entry["candidate"] and entry["module"]
        if entry["fits"]:
            # 装得下 ⇒ 三项余量都不为负（与引擎口径一致）
            assert entry["cpu_free"] >= 0.0
            assert entry["power_free"] >= 0.0
            assert entry["calibration_free"] >= 0.0
    # T-12 重标：PG 超 3.6、CPU 超 11.0 —— CPU 仍无余量，任何候选都装不下
    assert not any(entry["fits"] for entry in entries)


def test_resources_floats_are_rounded(views: dict[str, dict]) -> None:
    """数值纪律：浮点统一 `round(x, 3)`（过 `units.fmt`，不给毫秒/×1000 原值）。

    `rule` 子树例外：那是**引擎原始结构**，按要求原样保留（不重新取整）。
    """
    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "rule":
                    continue
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        elif isinstance(node, float):
            assert math.isclose(node, round(node, 3), rel_tol=0.0, abs_tol=1e-12)

    walk(views["resources"]["overview"])
    walk(views["constraints"]["overview"])
    walk(views["stability"]["overview"])


# =============================================================================
# 三、约束
# =============================================================================


def test_constraints_tier_violations_have_numbers(views: dict[str, dict], ctx: ViewContext) -> None:
    """当前档位下超载必须带具体数值；本样本实测 CPU 超 11.0（SDE 绑定快照）。"""
    overview = views["constraints"]["overview"]
    tier = overview["tier_violations"]

    assert tier, "样本 CPU/PG 都超，tier_violations 不该为空"
    resources = {entry["resource"]: entry for entry in tier if entry["kind"] == "resource"}
    assert "cpu" in resources and "powergrid" in resources

    cpu = resources["cpu"]
    attrs = ctx.attrs(BASE)
    assert math.isclose(cpu["used"], units.fmt(attrs[A.ATTR_CPU_LOAD]), rel_tol=1e-6)
    assert math.isclose(cpu["available"], units.fmt(attrs[A.ATTR_CPU_OUTPUT]), rel_tol=1e-6)
    assert math.isclose(cpu["shortfall"], 11.0, rel_tol=0.05), f"实测 11.0，实际 {cpu['shortfall']}"
    assert math.isclose(cpu["shortfall"], cpu["used"] - cpu["available"], rel_tol=1e-6)

    pg = resources["powergrid"]
    assert math.isclose(pg["shortfall"], pg["used"] - pg["available"], rel_tol=1e-6)
    assert pg["shortfall"] > 0.0
    for entry in tier:
        assert entry["message"] and "超载" in entry["message"]
        assert entry["message"] == entry["message"].strip()


def test_constraints_hard_violations_exclude_tier_types(views: dict[str, dict]) -> None:
    """二分必须干净：`hard_violations` 里不得出现 `resource` / `skill` 类。"""
    overview = views["constraints"]["overview"]
    for entry in overview["hard_violations"]:
        assert isinstance(entry["rule"], dict)
        assert entry["rule"].get("type") not in {"resource", "skill"}
        assert entry["message"]


def test_constraints_skill_floor_is_aggregated(views: dict[str, dict], ctx: ViewContext) -> None:
    """门槛画像只做聚合：max_level / count_by_level / distinct_skills。"""
    floor = views["constraints"]["overview"]["skill_floor"]
    assert set(floor) == {"max_level", "count_by_level", "distinct_skills"}
    assert floor["distinct_skills"] >= 1
    assert floor["max_level"] == max(floor["count_by_level"])
    assert sum(floor["count_by_level"].values()) == floor["distinct_skills"]
    assert floor["max_level"] <= 5

    # 全 4 + 补最低的技能档里至少有 4 级技能（硬结论 10）
    assert ctx.assumptions["skill_tier"]


def test_constraints_needs_implant_uses_all_five(views: dict[str, dict], ctx: ViewContext) -> None:
    """全 5 档重算后无资源缺口 → 不需要植入体（T-12 技能档修正后重标）。

    旧结论「全 5 档仍超 PG 18.0 → `needs_implant = True`」是技能档缺陷的产物：
    旧档缺 `Power Grid Management`（PG 输出 54）与 `Advanced Weapon Upgrades`
    （PG 占用 72）。新档下全 5 = 输出 67.5 / 占用 67.5（PG 刚好装满）、
    CPU 输出 287.5 / 占用 282.5（余 5），引擎不再报 `resource` 违规。
    """
    overview = views["constraints"]["overview"]
    assert overview["needs_implant"] is False
    assert "无资源缺口" in overview["needs_implant_basis"]

    # 交叉验证：把技能档整体拉到 5 级，引擎确实不再报 powergrid 资源违规
    all_five = {skip: 5 for skip in ctx.assumptions["skill_tier"]}
    character = dict(ctx.fit.get("character") or {})
    character["skills"] = all_five
    violations = ctx.engine.validate({**ctx.fit, "character": character})
    types = {(violation.get("rule") or {}).get("type") for violation in violations}
    assert "resource" not in types, f"全 5 档仍有资源违规：{violations}"


# =============================================================================
# 四、不稳定
# =============================================================================


def test_stability_capacitor_fields(views: dict[str, dict], ctx: ViewContext) -> None:
    """电容七字段齐全，且直取引擎值（`recharge_s` 是秒，不是毫秒）。"""
    capacitor = views["stability"]["overview"]["capacitor"]
    attrs = ctx.attrs(BASE)

    assert {
        "capacity",
        "recharge_s",
        "stable_pct",
        "depletes_in_s",
        "peak_load",
        "peak_recharge",
        "delta",
    } <= set(capacitor)

    assert math.isclose(capacitor["capacity"], units.fmt(attrs[A.ATTR_CAPACITOR_CAPACITY]), rel_tol=1e-12)
    # 55 的存储值是毫秒（280000），投影层已换算成秒（280.0）——红线 N5
    assert math.isclose(capacitor["recharge_s"], units.fmt(attrs[A.ATTR_CAPACITOR_RECHARGE_RATE]), rel_tol=1e-12)
    assert capacitor["recharge_s"] > 1.0, "秒量级；毫秒原值会是 280000 这一级"
    assert math.isclose(capacitor["delta"], units.fmt(attrs[A.ATTR_CAP_DELTA_PEAK]), rel_tol=1e-12)
    assert math.isclose(
        capacitor["delta"], capacitor["peak_recharge"] - capacitor["peak_load"], rel_tol=1e-6, abs_tol=1e-3
    )


def test_stability_cap_by_module_matches_peak_load(views: dict[str, dict], ctx: ViewContext) -> None:
    """耗电排行：逐模块 `-4` 求和应等于船体峰值负载（样本模块数 ≤ 8，全部入榜）。"""
    overview = views["stability"]["overview"]
    entries = overview["cap_by_module"]
    assert entries, "样本有耗电模块"

    uses = [entry["cap_use_gj_s"] for entry in entries]
    assert uses == sorted(uses, reverse=True), "必须按耗电降序"
    attrs = ctx.attrs(BASE)
    assert math.isclose(sum(uses), attrs[A.ATTR_CAP_PEAK_LOAD], rel_tol=1e-3)
    for entry in entries:
        assert entry["module"] and entry["count"] >= 1


def test_stability_cap_sensitivity_bounded(views: dict[str, dict], ctx: ViewContext) -> None:
    """逐模块关闭：最多 5 项，每项带模块名与 stable_pct；摘除用原下标（摘到炮会变更差）。"""
    overview = views["stability"]["overview"]
    entries = overview["cap_sensitivity"]

    assert 0 < len(entries) <= 5
    for entry in entries:
        assert entry["module"]
        assert "stable_pct" in entry and entry["stable_pct"] is not None
        assert entry["cap_use_gj_s"] > 0.0
        assert entry["depletes_in_s"] is None or entry["depletes_in_s"] > 0.0

    # 摘掉最耗电的（样本是 Small Ancillary Armor Repairer）→ 耗尽时间明显变长
    before = overview["capacitor"]["depletes_in_s"]
    assert entries[0]["depletes_in_s"] > before


def test_stability_external_dependency_and_consumables(views: dict[str, dict], ctx: ViewContext) -> None:
    """外部依赖是结构判定（样本无遥修/传电）；消耗品给出弹药与纳米胶续航。"""
    overview = views["stability"]["overview"]
    dependency = overview["external_dependency"]
    assert dependency["remote_rep"] is False
    assert dependency["cap_transfer"] is False

    consumables = overview["consumables"]
    assert consumables["ammo_endurance_s"] > 0.0
    assert consumables["ammo_rounds"] >= 1
    assert math.isclose(
        consumables["ammo_endurance_s"],
        consumables["ammo_rounds"] * consumables["ammo_cycle_s"],
        rel_tol=1e-3,
    )
    # 货舱里 139 管纳米胶 ÷ 每循环 1 管 × 周期 4.8 s
    assert consumables["paste_units"] == 139.0
    assert consumables["paste_per_cycle"] == 1.0
    assert math.isclose(consumables["paste_endurance_s"], 139.0 * 4.8, rel_tol=1e-6)
    assert consumables["cap_booster"] is False


def test_paste_consumer_requires_paste_charge(ctx: ViewContext) -> None:
    """纳米胶消费者必须**真的装填了纳米胶**：装普通弹的炮不算（否则会凭空算出续航）。

    同时验证周期口径：非武器模块没有 `speed(51)`，只有 `duration(73)`。
    T-13 起 `73` 已在 `SCALE_BY_ATTR`（毫秒缩放），`_cycle_s` 的入参是**物理秒**，
    所以这里的 stub 也必须给投影后的 4.8（曾按原始毫秒 4800 喂，属测试夹具未跟上 T-13）。
    """
    import noobbaozi.project.views.stability as S

    blaster = {
        "type_id": 3178,
        "name": "Light Neutron Blaster II",
        "charge_type_id": 12612,  # Void S，不是纳米胶
        "attributes": {S.ATTR_CHARGE_RATE: 1.0, A.ATTR_CYCLE_TIME: 2.304636},
    }
    repairer = {
        "type_id": 33076,
        "name": "Small Ancillary Armor Repairer",
        "charge_type_id": 28668,  # Nanite Repair Paste
        "attributes": {S.ATTR_CHARGE_RATE: 1.0, S.ATTR_MODULE_DURATION: 4.8},
    }

    assert S._paste_consumer(ctx, [(0, blaster)]) is None

    consumer = S._paste_consumer(ctx, [(0, blaster), (1, repairer)])
    assert consumer is not None
    rate, cycle = consumer
    assert math.isclose(rate, 1.0, rel_tol=1e-12)
    assert math.isclose(cycle, 4.8, rel_tol=1e-9), "duration 是毫秒，必须换算成秒"


# =============================================================================
# 五、缺值不补 0（红线 N4）
# =============================================================================


class _StubEngine:
    """一个**什么都不给**的引擎：validate 无违规，calculate 不该被调用。"""

    def validate(self, fit: dict) -> list[dict]:  # noqa: ARG002 - 桩
        return []

    def calculate(self, fit: dict, **_kwargs) -> dict:  # noqa: ARG002 - 桩
        raise AssertionError("缺值用例不该真算变体")


class _StubContext:
    """鸭子类型的 `ViewContext`：属性表为空、没有索引库。"""

    def __init__(self) -> None:
        self.engine = _StubEngine()
        self.index = None
        self.fit = {"ship": {"type_id": 1}, "items": []}
        self.assumptions = {"skill_tier": {}}
        self.meta = {"sde_build": 0, "engine_version": "test", "spec_version": "v1"}

    def attrs(self, variant: str = BASE) -> dict[int, float]:  # noqa: ARG002 - 桩
        return {}

    def items(self, variant: str = BASE) -> list[dict]:  # noqa: ARG002 - 桩
        return []

    def variant(self, name: str = BASE) -> dict:  # noqa: ARG002 - 桩
        return {}

    def variant_fit(self, name: str = BASE) -> dict:  # noqa: ARG002 - 桩
        return self.fit

    def calculate_variant(self, key: str, fit: dict) -> dict:  # noqa: ARG002 - 桩
        raise AssertionError("缺值用例不该真算变体")


def test_missing_values_stay_none_not_zero() -> None:
    """属性全缺 + 无索引：每个数值字段都必须是 `None`，**不得补 0 冒充已知值**。"""
    stub = _StubContext()

    resources = build_view("resources", stub)["overview"]
    for key in ("output", "load", "free"):
        assert resources["cpu"][key] is None
        assert resources["powergrid"][key] is None
        assert resources["calibration"][key] is None
    # 炮位：总量来自船体属性（缺 → None），`used` 是数出来的真 0，`free` 跟着总量缺
    for kind in ("turret", "launcher"):
        assert resources["hardpoints"][kind]["total"] is None
        assert resources["hardpoints"][kind]["used"] == 0
        assert resources["hardpoints"][kind]["free"] is None, "总量未知时 free 不得补 0"
    assert resources["hardpoints"]["unclassified"] == 0
    assert resources["drone"]["bandwidth"]["total"] is None
    assert resources["cargo"]["used"] is None, "没有索引就查不到体积，必须给 None"
    for name in ("high", "medium", "low", "rig"):
        assert resources["slots"][name]["total"] is None
        assert resources["slots"][name]["used"] == 0  # 一件都没装是**真 0**，不是缺值
        assert resources["empty_slots"][name] is None
    assert resources["upgrade_space"] == [], "没有索引就没有候选（不伪造）"

    stability = build_view("stability", stub)["overview"]
    for key, value in stability["capacitor"].items():
        assert value is None, f"capacitor.{key} 缺值必须是 None"
    assert stability["cap_by_module"] == []
    assert stability["cap_sensitivity"] == []
    assert stability["external_dependency"]["remote_rep"] is None
    assert stability["external_dependency"]["cap_transfer"] is None
    assert stability["consumables"]["ammo_endurance_s"] is None
    assert stability["consumables"]["paste_endurance_s"] is None

    constraints = build_view("constraints", stub)["overview"]
    assert constraints["hard_violations"] == []
    assert constraints["tier_violations"] == []
    assert constraints["skill_floor"]["max_level"] is None
    assert constraints["skill_floor"]["distinct_skills"] == 0
    assert constraints["needs_implant"] is False


def test_no_index_still_builds_three_views(engine: Engine, sample_fit: dict) -> None:
    """索引库缺失（`ctx.index is None`）时三个视角仍可构建，只是依赖 group/体积的字段为 None。"""
    no_index = ViewContext(
        engine=engine,
        index=None,
        fit=sample_fit,
        assumptions=default_assumptions(sample_fit, engine),
        meta=engine.meta(),
    )
    for name in VIEW_NAMES:
        view = build_view(name, no_index)
        assert view["view"] == name
        assert isinstance(view["overview"], dict)
        assert view["flags"] == []

    resources = build_view("resources", no_index)["overview"]
    assert resources["cargo"]["used"] is None
    assert resources["upgrade_space"] == []
    assert resources["cpu"]["free"] is not None  # 不依赖索引的字段照常给

    stability = build_view("stability", no_index)["overview"]
    assert stability["external_dependency"]["remote_rep"] is None
    assert stability["consumables"]["paste_endurance_s"] is None

# =============================================================================
# 七、真实样本对照（T-20：D5 / D6 / X2）
# =============================================================================

ACCEPTANCE_FIXTURES = NOOBBAOZI_ROOT / "acceptance" / "fixtures"
"""验收样本（**只读**：`acceptance/` 不在本任务写入范围内）。"""

T3C_SAMPLE = "f4-non-hecate.json"
"""T3C（Loki）样本：唯一带子系统槽的样本，D5/D6/X2 都靠它暴露。"""

MISSILE_SAMPLE = "f5-no-mwd.json"
"""纯导弹船（Drake + 6×HAM）：D5 的第二个暴露者，且无子系统槽。"""

REAL_SAMPLES = ("f1-hecate-killmail.eft", T3C_SAMPLE, MISSILE_SAMPLE)


def _load_real_fit(engine: Engine, index, name: str) -> dict:
    """按后缀读验收样本并补技能档（与验收命令同一条构造路径）。"""
    path = ACCEPTANCE_FIXTURES / name
    if path.suffix == ".eft":
        fit = engine.load_eft(path.read_text(encoding="utf-8"))
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw = raw[0]
        raw.pop("zkb", None)
        fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return fit


@pytest.fixture(scope="module")
def real_ctx(engine: Engine, index):
    """样本名 → `ViewContext`。"""

    def factory(name: str) -> ViewContext:
        fit = _load_real_fit(engine, index, name)
        return ViewContext(
            engine=engine,
            index=index,
            fit=fit,
            assumptions=default_assumptions(fit, engine, index=index),
            meta=engine.meta(),
        )

    return factory


# ---- D6：电容耗尽时长的哨兵归一 -------------------------------------------


def test_depletes_in_s_normalizer_covers_three_cases() -> None:
    """`shared.depletes_in_s()` 三种情形（D6 的核心口径，单独锁一遍）。

    1. 电容稳定（`stable_pct > 0`）→ `None`，不管引擎给的是 -1.0 还是别的；
    2. 引擎给负数（任何负数都是「不耗尽」哨兵，不只 -1.0）→ `None`；
    3. 否则照实给秒数（过 `units.fmt`，3 位小数）。
    """
    from noobbaozi.project.views import shared as views_shared

    # 1) 稳定：f4 实测 stable 60.969 / 引擎 -7 = -1.0
    assert views_shared.depletes_in_s(60.9688, -1.0) is None
    assert views_shared.depletes_in_s(0.5, 12.0) is None
    # 2) 不稳定但引擎仍是负数（哨兵的任何取值）→ None
    assert views_shared.depletes_in_s(0.0, -1.0) is None
    assert views_shared.depletes_in_s(0.0, -42.0) is None
    assert views_shared.depletes_in_s(None, -1.0) is None
    # 3) 不稳定 + 正数 → 照实（f1 实测 38.4）
    assert views_shared.depletes_in_s(0.0, 38.4) == 38.4
    assert views_shared.depletes_in_s(0.0, 38.4000001) == 38.4
    assert views_shared.depletes_in_s(None, 511.0762) == 511.076


def test_stability_depletes_is_null_when_capacitor_is_stable(views: dict[str, dict], real_ctx) -> None:
    """D6：稳定样本 `depletes_in_s is None`；不稳定样本仍是**正数**（f1 ≈ 38.4）。

    实测（-72 stable / -7 原始值）：
    f3 96.801 / -1.0、f4 60.969 / -1.0、f5 38.505 / -1.0、f6 100.0 / -1.0 —— 四个样本
    都是「稳定 + 哨兵」，旧实现原样输出 `-1.0`（agent 会读成「负一秒」）。
    """
    f1 = views["stability"]["overview"]["capacitor"]
    assert f1["stable_pct"] == 0.0, "f1 是**不稳定**样本（stable_pct 0）"
    assert f1["depletes_in_s"] is not None and f1["depletes_in_s"] > 0.0
    assert math.isclose(f1["depletes_in_s"], 38.4, rel_tol=1e-3)

    for name in ("f3-hecate-modes.eft", T3C_SAMPLE, MISSILE_SAMPLE, "f6-minimal-fit.json"):
        capacitor = build_view("stability", real_ctx(name))["overview"]["capacitor"]
        assert capacitor["stable_pct"] is not None and capacitor["stable_pct"] > 0.0, name
        assert capacitor["depletes_in_s"] is None, f"{name}: 电容稳定时必须给 None"


def test_stability_cap_sensitivity_depletes_uses_same_normalizer(real_ctx) -> None:
    """同一份哨兵归一也覆盖 `cap_sensitivity[].depletes_in_s`（摘掉模块后变稳定）。"""
    sensitivity = build_view("stability", real_ctx(T3C_SAMPLE))["overview"]["cap_sensitivity"]

    assert sensitivity, "f4 应有敏感度条目"
    for entry in sensitivity:
        assert entry["stable_pct"] is not None
        if entry["stable_pct"] > 0.0:
            assert entry["depletes_in_s"] is None, f"{entry['module']}: 稳定后不得给 -1.0"
        else:
            assert entry["depletes_in_s"] is None or entry["depletes_in_s"] > 0.0


# ---- X2：子系统槽位 --------------------------------------------------------


def test_resources_subsystem_slots_only_on_t3c(views: dict[str, dict], real_ctx) -> None:
    """X2：T3C 的 `slots` 含 `subsystem`（Loki 实测 used 4 / total 5）；非 T3C 不含。

    正反两向都断言（比「只验 T3C 有」更强）：
    - f4（Loki）：`slots.subsystem = {used: 4, total: 5}`、`empty_slots.subsystem = 1`，
      且三个条件字段在 overview 里**可达**（`FIELD_SOURCES` 有映射）；
    - f1（Hecate，T3D）/ f5（Drake）：键集合仍是四类，`empty_slots` 同步不多一类。
    """
    import noobbaozi.project.views.resources as R

    f4 = build_view("resources", real_ctx(T3C_SAMPLE))["overview"]
    assert "subsystem" in f4["slots"], "T3C 必须体现子系统槽"
    assert f4["slots"]["subsystem"] == {"used": 4, "total": 5}, "Loki：4 个已装 / 5 个槽位"
    assert f4["empty_slots"]["subsystem"] == 1
    for dotted in R.CONDITIONAL_SOURCES:
        assert _reachable(f4, dotted), f"T3C 样本上条件字段 {dotted!r} 必须可达"

    assert set(f4["slots"]) == {"high", "medium", "low", "rig", "subsystem"}
    assert set(f4["empty_slots"]) == set(f4["slots"])

    for name in ("f1-hecate-killmail.eft", MISSILE_SAMPLE, "f6-minimal-fit.json"):
        overview = build_view("resources", real_ctx(name))["overview"]
        assert "subsystem" not in overview["slots"], f"{name}: 非 T3C 不得凭空多一类槽位"
        assert "subsystem" not in overview["empty_slots"]
        assert set(overview["slots"]) == {"high", "medium", "low", "rig"}


def test_t3c_subsystem_total_comes_from_ship_attribute(real_ctx) -> None:
    """总量来自船体 `maxSubSystems(1367)`，不是常量或按已装数量反推。"""
    import noobbaozi.project.views.resources as R

    ctx = real_ctx(T3C_SAMPLE)
    total = ctx.attrs(BASE)[R.ATTR_MAX_SUBSYSTEMS]
    used = sum(
        1 for item in ctx.items(BASE) if item.get("slot_type") == R.SUBSYSTEM_SLOT_TYPE
    )
    overview = build_view("resources", ctx)["overview"]

    assert int(round(total)) == 5
    assert used == 4
    assert overview["slots"]["subsystem"] == {"used": used, "total": int(round(total))}


def test_stability_ammo_endurance_is_not_none_on_missile_ships(real_ctx) -> None:
    """D5 的不稳定视角侧：导弹船（f4/f5）的 `consumables` 必须给值，并带模块名/发数。"""
    for name, module in (
        (T3C_SAMPLE, "Heavy Missile Launcher II"),
        (MISSILE_SAMPLE, "Heavy Assault Missile Launcher II"),
    ):
        consumables = build_view("stability", real_ctx(name))["overview"]["consumables"]
        assert consumables["ammo_endurance_s"] is not None, name
        assert consumables["ammo_module"] == module
        assert consumables["ammo_rounds"] >= 1
        assert consumables["ammo_cycle_s"] is not None and consumables["ammo_cycle_s"] > 0.0


# =============================================================================
# 八、资源可行性（T-22 / 字段规格 §0.6 的第二类：状态未知）
# =============================================================================
#
# 判据是**自相矛盾**：按全 active 算装不下（任一余量为负）→ `feasible = False`。
# 视图**不做求解**：模块状态与 CPU/PG 数值一个都不改（降级 offline 不在本期）。


def test_resources_feasible_false_when_over_budget(real_ctx) -> None:
    """f4（Loki）：CPU 超一万 → `feasible is False`，缺口就是 `-cpu.free`。

    实测（§0.6 的第二类不完整输入）：`cpu.load 10572.1` vs 真实可飞 **472.1** ——
    差的 10000 是 covert cloak 的罚值（能免罚的子系统躺在 flag 177 舱里）。
    数字没读错，是「状态没落地」。
    """
    overview = build_view("resources", real_ctx(T3C_SAMPLE))["overview"]

    assert overview["feasible"] is False
    assert overview["cpu"]["load"] == 10572.1, "理论值必须原样保留（不许自动降级模块）"
    assert overview["cpu"]["free"] < 0.0
    assert overview["over_budget"]["cpu"] == round(-overview["cpu"]["free"], 3)
    assert overview["over_budget"]["cpu"] > 10000.0
    # PG 没超就是真 0（不是 None，也不是「有余量」的另一种说法）
    assert overview["powergrid"]["free"] > 0.0
    assert overview["over_budget"]["powergrid"] == 0.0


def test_resources_feasible_true_when_all_three_have_slack(real_ctx) -> None:
    """f3（Hecate 三模式配装）：CPU / PG / 标定三项余量都 ≥ 0 → `feasible is True`。

    反面断言同一条规则：`over_budget` 两项都必须是 `0.0`（只在 False 时非零）。
    """
    overview = build_view("resources", real_ctx("f3-hecate-modes.eft"))["overview"]

    assert overview["cpu"]["free"] >= 0.0
    assert overview["powergrid"]["free"] >= 0.0
    assert overview["calibration"]["free"] >= 0.0
    assert overview["feasible"] is True
    assert overview["over_budget"] == {"cpu": 0.0, "powergrid": 0.0}


@pytest.mark.parametrize("name", REAL_SAMPLES)
def test_resources_over_budget_only_meaningful_when_infeasible(real_ctx, name: str) -> None:
    """`feasible` 与 `over_budget` 必须自洽（三个真实样本各验一次）。

    注意 f1 是**实测超预算**样本（CPU 余量 −11.0 / PG −3.6）：全 4 技能档下这艘 Hecate
    就是装不下，所以断言的是「与余量同号」而不是某个固定布尔值——把 f1 写成 `True`
    等于伪造事实（见 T-22 报告的「偏离」一节）。
    """
    ctx = real_ctx(name)
    overview = build_view("resources", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    frees = (
        attrs[A.ATTR_CPU_FREE],
        attrs[A.ATTR_POWER_FREE],
        attrs[A.ATTR_CALIBRATION_FREE],
    )
    assert overview["feasible"] is all(value >= 0.0 for value in frees)

    for key, free in (
        ("cpu", attrs[A.ATTR_CPU_FREE]),
        ("powergrid", attrs[A.ATTR_POWER_FREE]),
    ):
        deficit = overview["over_budget"][key]
        assert deficit == units.fmt(max(0.0, -free)), (name, key)
        if overview["feasible"] is True:
            assert deficit == 0.0, f"{name}: 可行时 {key} 不得报缺口"
        if deficit > 0.0:
            assert overview["feasible"] is False, f"{name}: 有缺口就不可能 feasible"


def test_resources_over_budget_flag_is_emitted(real_ctx) -> None:
    """超预算时 `resources` 必须带一条 `resource_over_budget_unflyable`（解释「为什么假」）。"""
    ctx = real_ctx(T3C_SAMPLE)
    flags = {flag["id"]: flag for flag in D.evaluate(ctx)}

    assert "resource_over_budget_unflyable" in flags, sorted(flags)
    flag = flags["resource_over_budget_unflyable"]
    assert flag["view"] == "resources"
    assert flag["message"] == (
        "资源超预算：当前占用按全 active 计算，部分模块在真实游戏中不可能在线"
    )
    evidence = flag["evidence"]
    assert evidence["cpu_load"] > evidence["cpu_output"]
    assert evidence["cpu_deficit"] > 0.0
    assert evidence["open_state_modules"] >= 1

    # 反面：三项余量都为正的样本不产出这条 flag
    feasible_ctx = real_ctx("f3-hecate-modes.eft")
    assert "resource_over_budget_unflyable" not in {
        flag["id"] for flag in D.evaluate(feasible_ctx)
    }


def test_resources_feasible_is_none_when_free_values_missing() -> None:
    """余量缺失 → `feasible = None`（未知不下结论），`over_budget` 也保持 `None`。

    红线 N4：`None >= 0` 在 Python 里是 False，但「不知道」不等于「超预算」——
    这里必须显式给 `None`，否则会凭空造出「飞不起来」的判词。
    """
    resources = build_view("resources", _StubContext())["overview"]

    assert resources["feasible"] is None
    assert resources["over_budget"] == {"cpu": None, "powergrid": None}


# =============================================================================
# 九、诊断证据不得直出哨兵值（T-22 的第四件）
# =============================================================================


class _SentinelStubContext(_StubContext):
    """带「电容不稳定 + 引擎哨兵 -1.0」的桩：模拟 §0.6 那类自相矛盾的原始数据。"""

    ATTR_ARMOR_REPAIR_RATE = A.ATTR_ARMOR_REPAIR_RATE
    ATTR_CAP_STABLE_PCT = A.ATTR_CAP_STABLE_PCT
    ATTR_CAP_DEPLETES_IN = A.ATTR_CAP_DEPLETES_IN

    def __init__(self, stable_pct: float, depletes: float) -> None:
        super().__init__()
        self._attrs = {
            A.ATTR_ARMOR_REPAIR_RATE: 20.0,
            A.ATTR_CAP_STABLE_PCT: stable_pct,
            A.ATTR_CAP_DEPLETES_IN: depletes,
        }

    def attrs(self, variant: str = BASE) -> dict[int, float]:  # noqa: ARG002 - 桩
        return dict(self._attrs)


def test_diagnostics_evidence_never_emits_the_cap_sentinel() -> None:
    """`-7 capDepletesIn = -1.0` 是「永不耗尽」的哨兵，**不是秒数**（D6 / T-22）。

    证据必须走 `shared.depletes_in_s()`：不稳定（`-72 = 0`）但引擎给 `-1.0` 时给 `None`，
    正数照实。旧实现会把 `-1.0` 原样放进 evidence，读起来像「负一秒」。
    """
    flags = {flag["id"]: flag for flag in D.evaluate(_SentinelStubContext(0.0, -1.0))}

    assert "repaired_but_unstable" in flags, "前提：有修复速率且电容不稳定"
    evidence = flags["repaired_but_unstable"]["evidence"]
    assert evidence["cap_depletes_in_s"] is None, evidence
    assert evidence["cap_stable_pct"] == 0.0

    # 照实路径不被削掉：不稳定 + 正数 → 原样（过 units.fmt）
    positive = {flag["id"]: flag for flag in D.evaluate(_SentinelStubContext(0.0, 38.4))}
    assert positive["repaired_but_unstable"]["evidence"]["cap_depletes_in_s"] == 38.4


@pytest.mark.parametrize("name", REAL_SAMPLES)
def test_diagnostics_evidence_has_no_negative_seconds(real_ctx, name: str) -> None:
    """真实样本的 `cap_depletes_in_s*` 证据只能是 `None` 或**正**秒数（回归闸门）。"""
    for flag in D.evaluate(real_ctx(name)):
        for key, value in flag["evidence"].items():
            if "depletes" not in key:
                continue
            assert value is None or value > 0.0, f"{name}: {flag['id']}.{key} = {value!r}"


# =============================================================================
# 十、T-35：电容台账自洽（`peak_load` 与 `cap_by_module` 必须同进同出）
# =============================================================================
#
# 随堂测试 f67（Bifrost / killmail 139255942）暴露的三件事之一：`cap_by_module` 四项合计
# 5.813 GJ/s，而 `capacitor.peak_load` 只有 2.213 —— 同视角两个互不相等的「峰值负载」。
#
# 实测结论（66 个验收样本逐一跑过，误差 0）：船体 `-4 capPeakLoad` **就是**所有已装模块
# 装备级 `-4` 的**代数和**，含负数（吸血 / 传电 / 注射器类向电网**输入**电容）。f67 的差额
# 3.6 一直是那条 `Small Ghoul Compact Energy Nosferatu = −3.6 GJ/s`，**不是** MWD。
# 两条口径现在同进同出：都读 `BASE` 变体、同一批模块状态、含负项、不截断。

F67_SPOTCHECK = "f67-spotcheck-139255942.json"
"""Bifrost 指挥驱逐舰（killmail 139255942）：第一条真实存在的双推进配置。"""

CAP_LEDGER_SAMPLES = (
    F67_SPOTCHECK,
    "f1-hecate-killmail.eft",
    "f3-hecate-modes.eft",
    MISSILE_SAMPLE,
)
"""台账恒等式必须成立的四个样本：f67（含负项）+ f1/f3/f5（任务书点名的对拍样本）。"""

ATTR_CAPACITOR_NEED = 6
"""`capacitorNeed`（GJ）。**刻意不登记进 `config/attributes.py`**：本视角不自算
`capacitorNeed ÷ 周期`（T-35 硬结论，见 `views/stability.py` 模块 docstring）。
这里只为在测试里证明「引擎的 `-4` = `6 ÷ derivedCycleTime(-3)`」。
"""

ATTR_DERIVED_CYCLE_TIME = -3
"""`derivedCycleTime`（秒，引擎派生；常量表里已登记为 `('derivedCycleTime', 's', None)`）。"""


@pytest.mark.parametrize("name", CAP_LEDGER_SAMPLES)
def test_peak_load_equals_sum_of_cap_by_module(real_ctx, name: str) -> None:
    """`peak_load == Σ cap_by_module[].cap_use_gj_s`（同 state 口径：都是 `BASE` 变体）。

    容差只留给**逐项 `units.fmt` 的舍入**（每项 ≤ 5e-4，n 项合计 ≤ 5e-4·n），
    不是用来掩盖口径差异：f67 上旧的差额是 **3.6**，比这个容差大三个量级。
    """
    ctx = real_ctx(name)
    overview = build_view("stability", ctx)["overview"]
    capacitor = overview["capacitor"]
    entries = overview["cap_by_module"]

    assert entries, f"{name}: 有耗电模块就必须有台账"
    assert math.isclose(
        capacitor["peak_load"],
        units.fmt(ctx.attrs(BASE)[A.ATTR_CAP_PEAK_LOAD]),
        rel_tol=1e-12,
    ), f"{name}: peak_load 必须是引擎船体 -4 原值（不许自算）"

    total = sum(entry["cap_use_gj_s"] for entry in entries)
    slack = 1e-3 * (len(entries) + 1)
    assert math.isclose(total, capacitor["peak_load"], abs_tol=slack), (
        f"{name}: Σ cap_by_module = {total!r} ≠ peak_load {capacitor['peak_load']!r}"
        f"（逐项合计：{[entry['cap_use_gj_s'] for entry in entries]}）"
    )


def test_f67_cap_by_module_includes_negative_cap_sources(real_ctx) -> None:
    """f67 的差额一直是那条**负项**：`Small Ghoul Compact Energy Nosferatu = −3.6 GJ/s`。

    旧实现用 `cap_use_gj_s <= 0` 过滤，于是正项合计 5.813 与 `peak_load 2.213` 差 3.6；
    负数不是"坏数据"，它是吸血模块**向电网输入**的电容，必须入账才对得上。
    """
    ctx = real_ctx(F67_SPOTCHECK)
    overview = build_view("stability", ctx)["overview"]
    uses = {entry["module"]: entry["cap_use_gj_s"] for entry in overview["cap_by_module"]}

    assert uses["Small Ghoul Compact Energy Nosferatu"] == -3.6
    positives = sum(value for value in uses.values() if value > 0.0)
    negatives = sum(value for value in uses.values() if value < 0.0)
    peak = overview["capacitor"]["peak_load"]

    assert math.isclose(positives, 5.813, rel_tol=1e-9), "四项正项合计（任务书里的 5.813）"
    assert math.isclose(negatives, -3.6, rel_tol=1e-9)
    assert math.isclose(positives + negatives, peak, abs_tol=1e-3)
    assert math.isclose(peak, 2.213, abs_tol=1e-3), "快照：f67 的 peak_load"
    # 丢掉负项就正好多出 3.6 —— 这就是历史差额的来源，写成断言免得被"优化"掉
    assert math.isclose(positives - peak, 3.6, abs_tol=1e-3)


@pytest.mark.parametrize("name", (F67_SPOTCHECK, "f40-dreadnought.json"))
def test_cap_by_module_is_the_complete_ledger(real_ctx, name: str) -> None:
    """台账**不缺项也不截断**：每一个装备级 `-4` 非零的模块类型都在榜单里。

    测试侧独立复算（自己读 `ctx.items()`，不复用生产的过滤/分组规则）：类型集合必须
    逐一相等、数值必须是同一个浮点数。f40 有 **10 组**，旧的 `MAX_CAP_BY_MODULE = 8`
    会静默截掉两组，那时 `Σ` 就不再等于 `peak_load`。
    """
    ctx = real_ctx(name)
    entries = build_view("stability", ctx)["overview"]["cap_by_module"]

    expected: dict[int, float] = {}
    for item in ctx.items(BASE):
        if item.get("slot_type") == "cargo":
            continue
        use = (item.get("attributes") or {}).get(A.ATTR_CAP_PEAK_LOAD)
        if use is None or use == 0.0:
            continue
        type_id = item.get("type_id")
        expected[type_id] = expected.get(type_id, 0.0) + use

    got = {entry["type_id"]: entry["cap_use_gj_s"] for entry in entries}
    assert set(got) == set(expected), f"{name}: 台账漏了类型 {sorted(set(expected) - set(got))}"
    for type_id, use in expected.items():
        assert math.isclose(got[type_id], units.fmt(use), rel_tol=1e-12), (name, type_id)

    if name.startswith("f40"):
        assert len(entries) > 8, "f40 有 10 组：截断到 8 组会让台账和对不上 peak_load"


def test_mjfg_cap_load_is_period_average_not_instantaneous(real_ctx) -> None:
    """MJFG 的 `0.513 GJ/s` 是引擎的**整周期平均**，不是 `need / duration(73)` 的 14.8。

    引擎的装备级 `-4 = capacitorNeed(6) / derivedCycleTime(-3)`（f67 逐件实测：AB 7.2/8.0
    = 0.9、扰频 4.0/5.0 = 0.8、MWD 36/10 = 3.6）。MJFG 的 `-3 = 165.76 s` 而
    `duration(73) = 5.76 s`：差的是 `moduleReactivationDelay(669) = 160 s` 冷却 ——
    一发热 5.76 s 蓄力 + 160 s 冷却，整周期 165.76 s，所以平均负载只有 0.513。
    「`85 / 5.76 ≈ 14.8`」是那 5.76 秒内的**瞬时**速率，两者都对，但只有平均值参与
    `-4`、稳定百分比与船体 `-4` 的求和。**这是硬结论，别再把 0.513 当 bug 改掉。**
    """
    ctx = real_ctx(F67_SPOTCHECK)
    overview = build_view("stability", ctx)["overview"]
    uses = {entry["module"]: entry["cap_use_gj_s"] for entry in overview["cap_by_module"]}

    item = next(
        item for item in ctx.items(BASE) if item.get("name") == "Micro Jump Field Generator"
    )
    attrs = item["attributes"]
    need = attrs[ATTR_CAPACITOR_NEED]
    duration = attrs[A.ATTR_MODULE_DURATION]
    derived_cycle = attrs[ATTR_DERIVED_CYCLE_TIME]

    assert math.isclose(need, 85.0, rel_tol=1e-12), "capacitorNeed(6) = 85 GJ"
    assert math.isclose(duration, 5.76, rel_tol=1e-12), "显示时长 duration(73) = 5.76 s"
    assert math.isclose(derived_cycle, 165.76, rel_tol=1e-12), "派生周期 = 蓄力 + 160 s 冷却"

    value = uses["Micro Jump Field Generator"]
    assert math.isclose(need / derived_cycle, value, rel_tol=1e-3), (
        "引擎的 -4 就是 need ÷ 派生周期"
    )
    assert not math.isclose(need / duration, value, rel_tol=1e-2), (
        "不能是 need ÷ duration(73)：那是瞬时速率，会大一个量级"
    )
    assert value == 0.513, "快照：f67 的 MJFG 装备级 -4（3 位小数）"


# =============================================================================
# 十、T-43：`state_exclusive`（历史 id：`propulsion_illegal_stack` → `propulsion_state_conflict`）
# =============================================================================
#
# 口径（主人裁决，T-38 定、T-43 沿用）：**双推进一起带是很常见的事，只是确实不能同时开** ——
# 所以这**不是**配装问题，而是**状态互斥**：
#
# 1. id 从 `propulsion_state_conflict` 统一成 **`state_exclusive`**（旧 id 不得再出现）：
#    引擎原生同组互斥（AB+MWD）与配置表跨组互斥（MWD+MJD）现在是**同一个规则族**；
#    T-44 起 `group_ids` / `modules` 取并集、`sources`（`engine` / `config` 的并集）
#    说明每一部分分别是谁发现的（f67 的 MJFG(1533) 只有配置表覆盖得到）。
# 2. 文案是「{label}同时启用（{modules}），游戏内同一时刻只能启用其一；顶层数值按这些
#    模块同时启用计算（不可达状态）」，**全文不得出现判词**（`FORBIDDEN_WORDS`）；
# 3. 判据与数值一个字都不许动：引擎不降级、我们也不降级（那是求解，不在本期）。

import noobbaozi.project.views.mobility  # noqa: F401  （导入即注册）

STATE_EXCLUSIVE_FLAG = "state_exclusive"
"""T-43 后的唯一 id（同组 + 跨组，一个规则族一条输出）。"""

LEGACY_STACK_FLAGS = ("propulsion_state_conflict", "propulsion_illegal_stack")
"""历史 id（T-38 / T-35）：**只用来断言它们已经不存在**，不要再拿它们当 flag 名。"""

STATE_EXCLUSIVE_MESSAGE = (
    "推进类同时启用（AB、MJFG、MWD），游戏内同一时刻只能启用其一；"
    "顶层数值按这些模块同时启用计算（不可达状态）"
)
"""f67 逐字文案（`{label}` / `{modules}` 渲染成 `推进类` / `AB、MJFG、MWD`）。

T-44 起 `modules` 列**全部已启用的推进类模块**（f67 = AB + MJFG + MWD，按装配顺序），
不再是引擎报的那一组（旧文案 `AB 与 MWD` 把 MJFG 吞掉了）。"""

DUAL_PROPULSION_SAMPLES = (F67_SPOTCHECK, "f27-dual-prop.json", "f57-dual-prop.eft")
"""两个推进模块**都是 active** 的样本（引擎报 `group_id=46 used=2 / allowed=1`）。

⚠️ f27 名字叫 dual-prop，两个推进模块确实都 active —— 它属于这一组（T-35 已实测），
不是「单推进」样本。
"""

SINGLE_PROPULSION_SAMPLES = ("f1-hecate-killmail.eft", "f5-no-mwd.json", "f45-t3c-subsys-3.json")
"""单推进（或带两件但只有一件 active）：**不得**出现状态互斥 flag。"""


def test_config_rule_and_implementation_are_renamed_together() -> None:
    """config 与实现必须同口径：新 id 在两边都在、旧 id 在两边都不在。

    `test_projection_core.py::test_config_rules_match_implementation` 校验「集合相等」，
    这里补的是**名字本身**：改名涉及的 config / diagnostics 谁漏了都会红。
    """
    rules = {rule.id: rule for rule in D.load_rules()}
    implemented = set(D.implemented_rules())

    assert STATE_EXCLUSIVE_FLAG in rules, f"config 缺新 id，实际：{sorted(rules)}"
    assert STATE_EXCLUSIVE_FLAG in implemented
    for legacy in LEGACY_STACK_FLAGS:
        assert legacy not in rules, f"config 里旧 id {legacy!r} 必须删掉（改 id 不是加别名）"
        assert legacy not in implemented

    rule = rules[STATE_EXCLUSIVE_FLAG]
    assert rule.view == "mobility", "推进类互斥只影响机动视角的数值"
    assert rule.enabled is True
    # 模板只依赖 {label} / {modules}（evidence 另外三项 group_ids/limit/source 不进文案）
    assert "{label}" in rule.message and "{modules}" in rule.message
    assert "游戏内同一时刻只能启用其一" in rule.message
    assert "不可达状态" in rule.message
    for word in FORBIDDEN_WORDS:
        assert word not in rule.message, f"文案里出现了判词 {word!r}"


def _state_exclusive_labels(flag: dict) -> set[str]:
    """`evidence.modules` → 短标签集合。

    两个模块用 `" 与 "` 连接、三个及以上用 `"、"`（`diagnostics._conflict_text()`），
    这里两种分隔符都拆，只比集合、不比顺序（顺序 = 装配顺序，由 f67 分支单独逐字钉）。
    """
    return set(str(flag["evidence"]["modules"]).replace("、", " 与 ").split(" 与 "))


@pytest.mark.parametrize("name", DUAL_PROPULSION_SAMPLES)
def test_dual_propulsion_emits_state_exclusive_flag(real_ctx, name: str) -> None:
    """双推进同时启用：必须有一条 `mobility` flag 说明「这组数基于状态互斥」。"""
    ctx = real_ctx(name)
    flags = [flag for flag in D.evaluate(ctx) if flag["id"] == STATE_EXCLUSIVE_FLAG]
    ids = {flag["id"] for flag in D.evaluate(ctx)}

    assert len(flags) == 1, f"{name}: 同一冲突必须恰好一条，实际 {len(flags)}"
    assert not (set(LEGACY_STACK_FLAGS) & ids), "旧 id 不得再产出（改 id 不是加别名）"

    flag = flags[0]
    assert flag["view"] == "mobility"
    assert flag["evidence"]["source"] == "engine", "主因仍是引擎（`sources` 才给完整口径）"
    assert 46 in flag["evidence"]["group_ids"], "AB 与 MWD 同属 group 46"
    assert flag["evidence"]["limit"] == "active=1"
    # 短标签顺序 = 装配顺序（f27 是 MWD 先装、f67 是 AB 先装）→ 集合比；f67 逐字钉
    assert {"AB", "MWD"} <= _state_exclusive_labels(flag), "引擎那一组的两件必须都在 modules 里"
    assert "游戏内同一时刻只能启用其一" in flag["message"]
    for word in FORBIDDEN_WORDS:
        assert word not in flag["message"], f"{name}: 文案里出现了判词 {word!r}"

    # 体积预算：叠加双方的**模块名**不重复进本 flag 的 evidence（`mwd_sig_increase` 已有）
    mwd_flag = next(flag for flag in D.evaluate(ctx) if flag["id"] == "mwd_sig_increase")
    assert len(mwd_flag["evidence"]["propulsion"]) == 2
    assert "propulsion" not in flag["evidence"]

    if name == F67_SPOTCHECK:
        # T-44：evidence 必须合并引擎组与配置表组，MJFG(1533) 一件都不许漏
        assert flag["evidence"]["group_ids"] == [46, 1533], "46 来自引擎、1533 来自配置表"
        assert flag["evidence"]["sources"] == ["engine", "config"]
        assert _state_exclusive_labels(flag) == {"AB", "MWD", "MJFG"}, "f67 的 MJFG 也同时启用"
        assert flag["message"] == STATE_EXCLUSIVE_MESSAGE
    else:
        # f27 / f57 只有同组两件：并集 == 引擎那一组，`sources` 不该凭空多出 `config`
        assert flag["evidence"]["group_ids"] == [46]
        assert flag["evidence"]["sources"] == ["engine"]
        assert _state_exclusive_labels(flag) == {"AB", "MWD"}


PANTHER_EFT = (
    "[Panther, 跨组推进]\n"
    "500MN Microwarpdrive II\n"
    "Large Micro Jump Drive\n"
    "Damage Control II\n"
)
"""Panther + `500MN MWD`(46) + `Large MJD`(1189)：玩家实测「不能同时开」的那一对。

两件属于**不同** SDE group，引擎与 pyfa/eos 都报 0 条 `max_group`（T-42 报告 §3.4）
→ 这条 flag 只能来自 `config/activation_groups.json`。
"""


@pytest.fixture(scope="module")
def ctx_from_eft(engine: Engine, index):
    """EFT 文本 → `ViewContext`（技能档同 `real_ctx`，写法同 `tests/test_views_a.py`）。"""

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


def test_panther_cross_group_state_exclusive_sources(ctx_from_eft) -> None:
    """Panther（MWD + MJD）：**只有配置表**覆盖 → `sources == ["config"]`。"""
    ctx = ctx_from_eft(PANTHER_EFT)
    flags = [flag for flag in D.evaluate(ctx) if flag["id"] == STATE_EXCLUSIVE_FLAG]

    assert len(flags) == 1, f"Panther: 应当恰好一条 state_exclusive，实际 {len(flags)}"
    evidence = flags[0]["evidence"]
    assert evidence["group_ids"] == [46, 1189]
    assert evidence["sources"] == ["config"]
    assert evidence["source"] == "config", "主因是配置表（引擎一条都不报）"
    assert evidence["modules"] == "MWD 与 MJD"
    assert evidence["limit"] == "active=1"
    # 前置：两件都真的装上了、都在启用态（否则这条测试是空转）
    propulsion = [
        item
        for item in ctx.items(BASE)
        if item.get("slot_type") != "cargo" and item.get("group_id") in (46, 1189)
    ]
    assert [item.get("state") for item in propulsion] == ["active", "active"]


def test_f67_group_ids_lose_mjfg_when_table_drops_the_group(
    real_ctx, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**变异自检**：配置表不再登记 1533 → f67 的 `group_ids` 不再含 1533。

    这条钉的是「1533 完完全全由配置表驱动」：引擎 `max_group` 只数 group 46，
    它从来不会报 MJFG。两种收窄各验一次：

    1. `members` 收成只剩 `{46}` → flag 仍在（引擎那条 46 的冲突），但 `group_ids == [46]`；
    2. `members` **整个清空** → 配置表不再驱动任何东西，flag 整体消失（`group_ids` 自然也没有 1533）。
    """
    from noobbaozi.project.views import shared as views_shared

    def with_members(groups: tuple, members: frozenset[int]) -> tuple:
        """把每条登记组的 members 换掉（其余字段原样，label/limit 不影响判定）。

        `groups` 由调用方先取好：下面会 monkeypatch `activation_groups` 本身，
        在替身里再回调它就会无限递归。
        """
        return tuple(
            views_shared.ActivationGroup(
                id=group.id,
                label=group.label,
                label_short=group.label_short,
                members=members,
                limit=group.limit,
                source=group.source,
                verified_by=group.verified_by,
                verified_at=group.verified_at,
                note=group.note,
            )
            for group in groups
        )

    ctx = real_ctx(F67_SPOTCHECK)
    registered = views_shared.activation_groups()
    assert registered, "前提：规则表必须能读到"
    before = [
        flag["evidence"]["group_ids"]
        for flag in D.evaluate(ctx)
        if flag["id"] == STATE_EXCLUSIVE_FLAG
    ]
    assert before == [[46, 1533]], f"前提：正常加载规则表时 f67 必须含 1533，实际 {before}"

    # ① 只删 1533：46 的冲突还在（引擎报的），但 MJFG 必须从 evidence 里消失
    trimmed = with_members(registered, frozenset({46}))
    monkeypatch.setattr(views_shared, "activation_groups", lambda path=None: trimmed)

    flags = [flag for flag in D.evaluate(ctx) if flag["id"] == STATE_EXCLUSIVE_FLAG]
    assert flags, "引擎原生 46 那条冲突不该因为配置表变窄而消失"
    assert [flag["evidence"]["group_ids"] for flag in flags] == [[46]], (
        "1533 从配置表消失后，不再出现在 group_ids 里（否则它是写死在代码里的）"
    )
    assert "MJFG" not in flags[0]["evidence"]["modules"]
    assert flags[0]["evidence"]["sources"] == ["engine"]

    # ② 整个清空：配置表不驱动任何东西 → flag 不出现（group_ids 也就没有 1533）
    blank = with_members(registered, frozenset())
    monkeypatch.setattr(views_shared, "activation_groups", lambda path=None: blank)
    assert [flag for flag in D.evaluate(ctx) if flag["id"] == STATE_EXCLUSIVE_FLAG] == [], (
        "members 清空后不该再报（否则这条 flag 不是配置表驱动的）"
    )


@pytest.mark.parametrize("name", SINGLE_PROPULSION_SAMPLES)
def test_single_propulsion_stays_silent_about_state_exclusive(real_ctx, name: str) -> None:
    """单推进（或两件但只有一件 active）**不得**报状态互斥（否则就是把正常配装说成冲突）。"""
    ids = {flag["id"] for flag in D.evaluate(real_ctx(name))}
    assert STATE_EXCLUSIVE_FLAG not in ids, f"{name}: 单推进不该报状态互斥"


def test_cross_group_sample_is_reported_through_the_config_source(real_ctx) -> None:
    """跨组样本（f48：MWD + MJD）在渲染层也走同一条 flag，`source` 是 `config`。

    只测 `engine` 方向的话，配置表哪天读不到（空规则）也不会有测试变红。
    """
    from noobbaozi.project import render as R

    rendered = R.render_overview(real_ctx("f48-random-gnosis.json"))
    mobility_flags = [
        flag
        for flag in rendered["views"]["mobility"]["flags"]
        if flag["id"] == STATE_EXCLUSIVE_FLAG
    ]

    assert len(mobility_flags) == 1
    assert mobility_flags[0]["evidence"]["source"] == "config"
    assert mobility_flags[0]["evidence"]["sources"] == ["config"]
    assert mobility_flags[0]["evidence"]["group_ids"] == [46, 1189]
    for word in FORBIDDEN_WORDS:
        assert word not in json.dumps(mobility_flags[0], ensure_ascii=False), word


def test_rendered_overview_carries_the_renamed_flag_in_mobility(real_ctx) -> None:
    """渲染层归位：新 id 出现在 `views.mobility.flags` 里，且整份输出**没有判词**。

    `flags` 归位失败会掉进顶层 `flags` 兜底桶（不丢但不归位），所以两条都断言：
    机动视角里有它，顶层兜底桶里没有它。
    """
    from noobbaozi.project import render as R

    overview_output = R.render_overview(real_ctx(F67_SPOTCHECK))
    mobility_flags = {flag["id"]: flag for flag in overview_output["views"]["mobility"]["flags"]}

    assert STATE_EXCLUSIVE_FLAG in mobility_flags
    assert not (set(LEGACY_STACK_FLAGS) & set(mobility_flags))
    assert STATE_EXCLUSIVE_FLAG not in {
        flag["id"] for flag in overview_output["flags"]
    }, "mobility 的 flag 不该掉进顶层兜底桶"
    assert mobility_flags[STATE_EXCLUSIVE_FLAG]["message"] == STATE_EXCLUSIVE_MESSAGE

    text = json.dumps(overview_output["views"], ensure_ascii=False)
    for word in FORBIDDEN_WORDS:
        assert word not in text, f"渲染输出里出现了判词 {word!r}"



# =============================================================================
# 十二、两条 L2 语义修正（dev-engine 4/5 追加）：`becomes_stable` / `blocked_by`
# =============================================================================

CAP_FLIP_EFT = """[Hecate, cap-flip]

50MN Quad LiF Restrained Microwarpdrive
Light Neutron Blaster II
Light Neutron Blaster II
Light Neutron Blaster II
"""
"""`cap_sensitive` 的**命中形态**：摘掉 MWD 真的稳了（92.2%），摘掉炮仍然 0%。"""

RIFTER_UPGRADE_EFT = """[Rifter, upgrade]

200mm AutoCannon I
200mm AutoCannon I
200mm AutoCannon I

Small Shield Extender I
"""
"""装得下的升级候选样本（T1 炮 → T2 炮，余量充足）。"""

F7_PATH = NOOBBAOZI_ROOT / "acceptance" / "fixtures" / "f7-t3c-legion.json"
"""测试员报的那个样本：16 个候选全部 `fits=false`，但 CPU/PG/标定都够。"""

F1_PATH = NOOBBAOZI_ROOT / "acceptance" / "fixtures" / "f1-hecate-killmail.eft"
"""本来就超载的样本：摘掉任何一件都仍然不稳。"""


def test_cap_sensitivity_becomes_stable_is_the_flip_boolean(ctx_from_eft) -> None:
    """`becomes_stable` = `stable_pct > 0`：**摘掉后仍不稳**的行必须 `False`。

    这条字段存在的理由：`cap_sensitivity` 报的是「摘掉这一件**之后的状态**」，不是
    「摘掉就稳」。测试员实测 29 条里只有 17 条真的翻转，不给布尔就会被读成「照做就稳」。
    """
    view = render.render_view(ctx_from_eft(CAP_FLIP_EFT), "stability")
    rows = view["overview"]["cap_sensitivity"]
    assert rows, "反证：样本必须有敏感度明细"

    mwd = [row for row in rows if "Microwarpdrive" in (row["module"] or "")]
    assert len(mwd) == 1
    assert mwd[0]["becomes_stable"] is True, "摘掉 MWD 真的稳了"
    assert mwd[0]["stable_pct"] > 0.0

    others = [row for row in rows if row is not mwd[0]]
    assert others and all(row["becomes_stable"] is False for row in others), (
        "摘掉炮仍然不稳 → 必须是 False（否则 agent 会把「摘掉」当成「就稳」）"
    )
    # 口径一致：不许和 stable_pct 各算一套；未知（None）不许当 False
    for row in rows:
        if row["stable_pct"] is None:
            assert row["becomes_stable"] is None, "stable_pct 未知时 becomes_stable 也必须未知"
        else:
            assert row["becomes_stable"] is (row["stable_pct"] > 0.0)
    # 只有真翻转才出 flag（它的文案才敢写「由不稳定转为稳定」）
    assert "cap_sensitive" in {flag["id"] for flag in view["flags"]}


def test_cap_sensitivity_does_not_claim_stability_when_removal_does_not_help(
    ctx_from_eft,
) -> None:
    """超载样本（f1）：摘掉任何一件都仍然不稳 → 全 `False`，且**不许**出 `cap_sensitive` flag。"""
    if not F1_PATH.is_file():
        pytest.skip(f"缺少样本：{F1_PATH}")
    view = render.render_view(
        ctx_from_eft(F1_PATH.read_text(encoding="utf-8-sig")), "stability"
    )
    rows = view["overview"]["cap_sensitivity"]

    assert rows, "反证：f1 必须有敏感度明细"
    assert all(row["becomes_stable"] is False for row in rows), "f1 摘掉任何一件都仍不稳"
    assert all(row["stable_pct"] is not None for row in rows)
    ids = {flag["id"] for flag in view["flags"]}
    assert "cap_sensitive" not in ids, "没翻转就不许报「关掉就稳」"
    assert "repaired_but_unstable" in ids, "反证：样本确实不稳定（不是恰好稳定）"


def test_becomes_stable_has_provenance(ctx_from_eft) -> None:
    """新字段必须登记来源（合成判断 → `assumed`），否则会掉成 `unknown`。"""
    view = render.render_view(ctx_from_eft(CAP_FLIP_EFT), "stability")
    assert view["overview"]["cap_sensitivity"], "反证：有明细才有这个叶子"
    assert view["provenance"]["cap_sensitivity[].becomes_stable"] == "assumed"


def test_upgrade_space_blocked_by_names_the_dimension_beyond_cpu_pg_calibration() -> None:
    """`fits=false` 必须带 `blocked_by`，并点名**不在那三个数里**的资源维度。

    测试员实测（f7）：4 个候选 `fits=false` 但 `cpu_free` / `power_free` / `calibration_free`
    都 ≥ 0 —— 卡它们的是 `drone_bandwidth` / `launched_drones`。只给 `fits=false` 的话，
    agent 只能得出「资源够却装不下」的矛盾结论。
    """
    if not F7_PATH.is_file():
        pytest.skip(f"缺少样本：{F7_PATH}")
    from noobbaozi import api

    api.clear_caches()
    payload = json.loads(F7_PATH.read_text(encoding="utf-8"))
    envelope = api.fit_detail(payload, path="resources.upgrade_space")
    assert envelope["ok"] is True, envelope.get("error")
    entry = envelope["result"]["views"]["resources"]
    rows = entry["overview"]["upgrade_space"]

    assert rows, "反证：f7 必须有升级候选"
    false_rows = [row for row in rows if not row["fits"]]
    assert false_rows, "反证：f7 的候选都装不下"

    reported = {21484, 521, 19023, 11215}
    hits = [row for row in false_rows if row["candidate_type_id"] in reported]
    assert hits, "测试员点名的 4 个候选必须还在样本里"

    for row in hits:
        assert row["cpu_free"] >= 0.0 and row["power_free"] >= 0.0
        assert row["calibration_free"] >= 0.0, "三个主资源都够 —— 这正是要解释的怪象"
        blocked = row["blocked_by"]
        assert isinstance(blocked, list) and blocked, "fits=false 必须给出原因"
        assert blocked == sorted(blocked), "原因清单要排序稳定"
        assert all(isinstance(item, str) and item for item in blocked)
        assert any(item not in {"cpu", "powergrid", "calibration"} for item in blocked), (
            f"必须点名那个「不在 CPU/PG/标定里」的维度，实际 {blocked}"
        )

    assert entry["provenance"]["upgrade_space[].blocked_by"] == "assumed"


def test_upgrade_space_fits_true_rows_do_not_carry_blocked_by(ctx_from_eft) -> None:
    """形状口径：**只有 `fits=false` 带 `blocked_by`**（它是原因字段；true 的行没有原因）。"""
    view = build_view("resources", ctx_from_eft(RIFTER_UPGRADE_EFT))
    rows = view["overview"]["upgrade_space"]

    assert rows, "反证：样本必须有候选"
    fits_rows = [row for row in rows if row["fits"]]
    assert fits_rows, "反证：样本必须有装得下的候选（否则比的是两堆 false）"
    for row in fits_rows:
        assert "blocked_by" not in row, "fits=true 的行不许带原因字段"
    for row in rows:
        if not row["fits"]:
            assert row.get("blocked_by"), "fits=false 的行必须带原因"
