"""T2/T3 测试：六视角里的 **资源 / 约束 / 不稳定** 三个（T-10）。

样本：`evefit/spike/kill.json`（killmail 139246907，Hecate + 全 4 + 补最低技能档）。
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

from evefit.config import attributes as A
from evefit.config.paths import db_path
from evefit.engine.adapter import Engine
from evefit.engine.assumptions import default_assumptions, derive_skill_tier
from evefit.project import units
from evefit.project.views import build_view
from evefit.project.views.base import BASE, ViewContext
from evefit.sde.index import Index

# 三个视角模块必须显式 import 才会注册（注册表刻意不 import 它们，见 views/__init__.py）
import evefit.project.views.constraints  # noqa: F401  （导入即注册）
import evefit.project.views.resources  # noqa: F401
import evefit.project.views.stability  # noqa: F401

EVEFIT_ROOT = Path(__file__).resolve().parents[1]
KILL_PATH = EVEFIT_ROOT / "spike" / "kill.json"

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
        pytest.skip("索引库未构建：先跑 evefit.sde.ingest.build_index()")
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
    from evefit.project.views import available_views

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
    """每个 key 都能在 overview 里走到；非 None 来源必须是**已登记**属性常量。"""
    import evefit.project.views.constraints as C
    import evefit.project.views.resources as R
    import evefit.project.views.stability as S

    for module, name in ((R, "resources"), (C, "constraints"), (S, "stability")):
        overview = views[name]["overview"]
        for dotted, attribute_id in _field_sources(module).items():
            assert _reachable(overview, dotted), f"{name}: FIELD_SOURCES 的 {dotted!r} 在 overview 里不可达"
            if attribute_id is None:
                continue
            assert isinstance(attribute_id, int), f"{name}.{dotted}: 来源必须是 int 或 None"
            # 红线 N12：来源必须是常量表里的常量（不是裸写的数字）
            assert attribute_id in A.ATTRIBUTES, f"{name}.{dotted}: 属性 {attribute_id} 未在常量表登记"


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
    """挂点 / 无人机 / 货舱：上限来自船体属性，占用来自 items（样本无无人机 → 真 0）。"""
    overview = views["resources"]["overview"]
    attrs = ctx.attrs(BASE)

    assert overview["hardpoints_free"]["turret"] == int(round(attrs[A.ATTR_TURRET_SLOTS_LEFT]))
    assert overview["hardpoints_free"]["launcher"] == int(round(attrs[A.ATTR_LAUNCHER_SLOTS_LEFT]))

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
    import evefit.project.views.stability as S

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
    assert resources["hardpoints_free"]["turret"] is None
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
