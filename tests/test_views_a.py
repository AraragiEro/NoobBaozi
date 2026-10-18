"""T2/T3 测试：六视角的前三个 —— 火力 / 生存 / 机动（`views/{firepower,survivability,mobility}.py`）。

样本：`evefit/spike/kill.json`（killmail 139246907，Hecate，17 个已装模块 + 5 门
Light Neutron Blaster II 装 Void S），技能档走 `engine.assumptions.derive_skill_tier`
并传入 SDE 索引（**T-12 技能档修正后重标**：全部已发布技能 4 级 + 补最低要求 = 512 个）。

覆盖任务书要求的六条断言 + 本项目关心的口径：

1. 三个视角 `build(ctx)` 都返回 `view` / `overview`，且 `flags == []`（诊断由渲染层
   统一算一次，视角层不许各算一遍）；
2. `FIELD_SOURCES` 的每个 key 都能在 overview 里按点路径取到（数组走 `[]` 元素约定，
   与 `test_views_b.py` 同一套寻址），且**每个数值叶子都有映射**（渲染层据此打
   provenance，漏一个就等于输出无 provenance 的数值）；
3. 火力：`dph == ship[-11]`、`dps.base == ship[-12]`、`damage_split` 四项之和 ≈ 1.0；
4. 生存：`Σ layer_ehp == ehp_mixed`（不变式 I1，容差 1e-6）；
5. 机动：`propulsion_states` 三档齐全，active / off 的信号半径之比 ≈ 5.5（有 MWD 时）；
6. 属性缺失时字段是 `None` 而不是 0（**唯一例外**：`repair.*` 无来源的层给 `0.0` +
   `has_source=False`，T-19 裁决 C4——「无来源的 0」与「有什么」由 `has_source` 区分，
   不靠 `None`，也不靠 provenance）。

约束：无网络、无随机（红线 N1）；样本只读；只碰本任务写入范围内的文件。
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
from evefit.project.views import (
    available_views,
    build_view,
    firepower,
    mobility,
    shared,
    stability,  # noqa: F401  （导入即注册：D5 的跨视角断言要读 stability）
    survivability,
)
from evefit.project.views.base import BASE, OVERLOAD, ViewContext, ship_attributes
from evefit.sde.index import Index

EVE_ROOT = Path(__file__).resolve().parents[1]
KILL_PATH = EVE_ROOT / "spike" / "kill.json"

VIEW_MODULES = {
    "firepower": firepower,
    "survivability": survivability,
    "mobility": mobility,
}
"""视角名 → 模块（用来直接读 `FIELD_SOURCES`）。"""

VIEW_NAMES = tuple(VIEW_MODULES)

DAMAGE_TYPES = ("em", "explosive", "kinetic", "thermal")

MS_PER_S = 1000.0
"""毫秒 / 秒换算（**仅测试用**：独立复算导弹射程，不参与生产代码）。"""

ARRAY_MARKER = "[]"
"""数组元素约定里的标记（`weapons[].family`）；与 `render.ARRAY_MARKER` 同一套语义。

刻意在这里**再写一遍**而不是 import：本文件的两个 walker 是与渲染层对拍的独立实现，
复用私有常量会让「两边都改错 = 测试仍绿」的盲区出现（同 `test_render.py` 的处理）。
"""

TURRET_GROUP = "Hybrid Weapon"
"""样本里的炮 group（用来从 items 里精确挑出那 5 门中子炮）。"""

MISSILE_EFT = (
    "[Kestrel, 导弹样本]\n"
    "Ballistic Control System II\n"
    "Light Missile Launcher II,Scourge Light Missile"
)
"""一个纯导弹配装：验证「导弹射程 = 飞行时间 × 速度」这条只对导弹成立的路径。"""

NO_WEAPON_EFT = "[Hecate, 只有一件]\nDamage Control II"
"""没有任何武器的配装：验证「没有武器时伤害占比给 None 而不是 0」。"""

NO_REPAIR_EFT = "[Hecate, 无修复模块]\nDamage Control II"
"""没有任何修复/回充模块的配装（DC II 只加抗性，不修血）：验证 T-19 的
「三层 `raw_hp_s == 0.0` 且 `has_source is False`」（不是 `None`）。"""


# ---- fixtures（与 tests/test_projection_core.py 同构）----


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
    """样本配装 + 「全 4 + 补最低」技能档（传索引才是新语义，T-12）。"""
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
def ctx(engine: Engine, index, sample_fit: dict) -> ViewContext:
    return ViewContext(
        engine=engine,
        index=index,
        fit=sample_fit,
        assumptions=default_assumptions(sample_fit, engine, index=index),
        meta=engine.meta(),
    )


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


# ---- 小工具 ----


_MISSING = object()
"""`_resolve()` 的「走不到」哨兵：与真实值（含 `None`）区分开。"""


def _resolve(overview: dict, path: str):
    """按点路径取值；`[]` 段 = 「数组每个元素取后续字段」（T-16 统一口径）。

    与 `test_views_b._reachable()` 同一套寻址语义：

    - 普通段：字典键；键名是数字时回退**整数键**（`count_by_level.4` → `4`）；
    - `名字[]` 段：元素是对象，对**每个**元素继续走后续段（任一元素命中即算可达）；
      空数组算「字段本身存在」（元素内部结构无从校验，与 `_reachable` 一致）；
    - 取不到就返回 `_MISSING`，由调用处让断言炸（测试里就是要它炸）。
    """
    parts = path.split(".")

    def walk(node: object, index: int, trail: str) -> object:
        if index >= len(parts):
            return node
        part = parts[index]
        here = f"{trail}.{part}" if trail else part
        if part.endswith(ARRAY_MARKER):
            key = part[: -len(ARRAY_MARKER)]
            assert isinstance(node, dict), f"{here!r} 不是对象：{type(node).__name__}"
            assert key in node, f"{path!r} 缺少子键 {key!r}（现有 {sorted(node)}）"
            items = node[key]
            assert isinstance(items, list), f"{path!r} 的 {key!r} 不是数组：{type(items).__name__}"
            if not items:  # 空数组：字段本身存在即算可达
                return []
            values = [
                walk(item, index + 1, f"{here}[{position}]") for position, item in enumerate(items)
            ]
            hits = [value for value in values if value is not _MISSING]
            assert hits, f"{path!r} 在任何数组元素上都取不到后续段"
            return hits[0] if len(hits) == 1 else hits
        if not isinstance(node, dict):
            return _MISSING
        if part in node:
            return walk(node[part], index + 1, here)
        if part.lstrip("-").isdigit() and int(part) in node:  # 整数键字典
            return walk(node[int(part)], index + 1, here)
        return _MISSING

    value = walk(overview, 0, "")
    assert value is not _MISSING, f"{path!r} 在 overview 里取不到"
    return value


def _numeric_leaf_paths(node: object, prefix: str = "") -> list[str]:
    """收集数值叶子的点路径；**对象数组按元素展开**（`weapons[].count` 元素约定）。

    寻址语义与 `render._leaf_paths()` 一致：对象逐键递归；元素全是对象的非空列表
    用 `ARRAY_MARKER` 段逐元素展开（T-16 之前这里把整张列表当一个叶子，于是必须
    登记容器路径 `weapons`——那是与 `test_views_b` 分叉的旧口径）。

    空列表不产生叶子：列表本身不是数值（渲染层会把空列表折叠成容器路径并兜底
    `unknown`，那是「没有数据」的诚实标签，不是漏登记）。
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_numeric_leaf_paths(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, list):
        if node and all(isinstance(item, dict) for item in node):
            for item in node:
                found.extend(_numeric_leaf_paths(item, f"{prefix}{ARRAY_MARKER}"))
        elif node:  # 标量列表：没有可寻址的字段，整体算一个叶子（旧口径保留）
            found.append(prefix)
    elif isinstance(node, bool):
        pass
    elif isinstance(node, (int, float)):
        found.append(prefix)
    return found


def _string_leaf_paths(node: object, prefix: str = "") -> list[str]:
    """收集字符串叶子的点路径（断言没有多余的定性判词）。"""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_string_leaf_paths(value, f"{prefix}.{key}" if prefix else str(key)))
    elif isinstance(node, list):
        for position, value in enumerate(node):
            found.extend(_string_leaf_paths(value, f"{prefix}[{position}]"))
    elif isinstance(node, str):
        found.append(prefix)
    return found


def _raw_value(attributes: dict, attribute_id: int) -> float | None:
    """从引擎**原始** attributes 里取一个值（兼容 `{base, value}` 与裸数两种形态）。

    缺失返回 `None`（不补 0）：`-21` 只在武器上有，非武器模块缺它是正常的。
    """
    raw = attributes.get(attribute_id)
    if raw is None:
        raw = attributes.get(str(attribute_id))
    if raw is None:
        return None
    return float(raw["value"] if isinstance(raw, dict) else raw)


def _close(left: float, right: float, rel_tol: float = 1e-9) -> bool:
    return math.isclose(float(left), float(right), rel_tol=rel_tol, abs_tol=1e-9)


def _fitted(ctx: ViewContext, group_name: str) -> list[dict]:
    """按 SDE group 名挑出已装模块（不靠下标，样本改了也不会误取）。"""
    return [item for item in ctx.items(BASE) if item.get("group_name") == group_name]


# =============================================================================
# 一、视角契约（三个视角共通）
# =============================================================================


def test_three_views_are_registered() -> None:
    """三视角已在注册表里（`views/__init__.py` 一行没改，靠各自的 @register）。"""
    assert set(VIEW_NAMES) <= set(available_views())


@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_build_returns_view_overview_and_empty_flags(view_name: str, ctx: ViewContext) -> None:
    """契约形状：`{"view", "overview", "flags"}`，且 `flags` 恒为空。"""
    view = build_view(view_name, ctx)

    assert set(view) == {"view", "overview", "flags"}
    assert view["view"] == view_name
    assert isinstance(view["overview"], dict) and view["overview"]
    assert view["flags"] == [], "诊断必须由渲染层统一算一次，视角层不许自己算"


@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_field_sources_resolve_in_overview(view_name: str, ctx: ViewContext) -> None:
    """`FIELD_SOURCES` 的每个 key 都必须能在 overview 里按点路径取到（含 `[]` 元素段）。"""
    module = VIEW_MODULES[view_name]
    overview = build_view(view_name, ctx)["overview"]

    assert module.FIELD_SOURCES, f"{view_name} 没声明 FIELD_SOURCES"
    for path in module.FIELD_SOURCES:
        _resolve(overview, path)


@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_field_sources_cover_every_numeric_leaf(view_name: str, ctx: ViewContext) -> None:
    """每个数值叶子都要有来源映射（否则渲染层会产出无 provenance 的数值，红线 N3）。

    列表**按元素展开**（`weapons[].count`），所以登记容器路径 `weapons` 不再能顶掉
    元素里的数值——覆盖率要求与渲染层的叶子键空间逐字同一套（T-16）。
    """
    module = VIEW_MODULES[view_name]
    overview = build_view(view_name, ctx)["overview"]

    missing = sorted(set(_numeric_leaf_paths(overview)) - set(module.FIELD_SOURCES))
    assert not missing, f"{view_name} 这些数值叶子没有 FIELD_SOURCES 映射：{missing}"


@pytest.mark.parametrize("view_name", VIEW_NAMES)
def test_field_sources_values_are_attribute_constants(view_name: str) -> None:
    """映射值只能是 `ATTR_*` 常量本身或 `None`（不许裸写数字，红线 N12）。"""
    module = VIEW_MODULES[view_name]
    known = {value for name, value in vars(A).items() if name.startswith(A.CONSTANT_PREFIX)}

    for path, attribute_id in module.FIELD_SOURCES.items():
        if attribute_id is None:
            continue
        assert attribute_id in known, f"{view_name}.{path} 映射到未登记的 ID {attribute_id}"


# =============================================================================
# 二、火力
# =============================================================================


def test_firepower_dps_and_dph_come_from_ship_attributes(ctx: ViewContext) -> None:
    """`dph == ship[-11]`、`dps.base == ship[-12]`、`dps.with_reload == ship[-13]`。"""
    overview = build_view("firepower", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert _close(overview["dph"], round(attrs[A.ATTR_DAMAGE_ALPHA], 3))
    assert _close(overview["dps"]["base"], round(attrs[A.ATTR_DAMAGE_DPS], 3))
    assert _close(overview["dps"]["with_reload"], round(attrs[A.ATTR_DAMAGE_DPS_WITH_RELOAD], 3))
    assert overview["dps"]["base"] > overview["dps"]["with_reload"] > 0.0


def test_firepower_dph_equals_sum_of_item_volleys(ctx: ViewContext) -> None:
    """不变式 I2：`-11` = Σ 逐装备 `-21`（volley 只在装备级，舰船级没有）。"""
    overview = build_view("firepower", ctx)["overview"]
    volleys = [
        _raw_value(item.get("attributes") or {}, A.ATTR_DAMAGE_VOLLEY)
        for item in ctx.variant(BASE)["items"]
    ]
    positive = [value for value in volleys if value]
    raw_dph = ctx.attrs(BASE)[A.ATTR_DAMAGE_ALPHA]

    assert positive, "样本里的炮应当有装备级 volley"
    assert math.isclose(sum(positive), raw_dph, rel_tol=0.0, abs_tol=1e-6)
    assert _close(overview["dph"], round(raw_dph, 3))


def test_firepower_weapons_list_matches_fitted_guns(ctx: ViewContext) -> None:
    """武器清单：5 门中子炮聚成一条，弹药名是 Void S，family = hybrid。"""
    overview = build_view("firepower", ctx)["overview"]

    weapons = overview["weapons"]
    assert len(weapons) == 1
    assert set(weapons[0]) == {"family", "name", "count", "ammo_name"}
    assert weapons[0]["family"] == "hybrid"
    assert weapons[0]["name"] == "Light Neutron Blaster II"
    assert weapons[0]["count"] == 5
    assert weapons[0]["ammo_name"] == "Void S"


def test_firepower_damage_split_is_damage_weighted_and_sums_to_one(ctx: ViewContext) -> None:
    """四系占比之和 = 1，且 Void S 是 50/50 动能热（不是单系，也不按弹种数量）。"""
    overview = build_view("firepower", ctx)["overview"]
    split = overview["damage_split"]

    assert set(split) == set(DAMAGE_TYPES)
    assert _close(sum(split.values()), 1.0, rel_tol=1e-6)
    assert _close(split["kinetic"], 0.5, rel_tol=1e-9)
    assert _close(split["thermal"], 0.5, rel_tol=1e-9)
    assert split["em"] == 0.0 and split["explosive"] == 0.0


def test_firepower_range_tiers_come_from_54_and_158(ctx: ViewContext) -> None:
    """三档射程：`54` 已是最终最佳射程（含弹药 120 修正），falloff 再叠加两次。"""
    overview = build_view("firepower", ctx)["overview"]
    weapon = _fitted(ctx, TURRET_GROUP)[0]
    optimal = weapon["attributes"][A.ATTR_MAX_RANGE]
    falloff = weapon["attributes"][A.ATTR_FALLOFF]

    assert _close(overview["range"]["optimal_m"], round(optimal, 3))
    assert _close(overview["range"]["optimal_plus_falloff_m"], round(optimal + falloff, 3))
    assert _close(overview["range"]["optimal_plus_2falloff_m"], round(optimal + 2.0 * falloff, 3))
    # 炮的 54 已经含 ammo 的 weaponRangeMultiplier，不得再乘一次：
    # 1800 × 0.75（Void S）× 1.2（Sharpshooter 4 级 +5%/级）= 1620
    # T-12 技能档修正后重标：旧值 1350 是「Sharpshooter 不在技能档里」的产物。
    assert _close(round(optimal, 3), 1620.0)
    assert overview["range"]["max_range_m"] is None, "炮没有导弹射程"


def test_firepower_ammo_endurance_is_single_magazine(ctx: ViewContext) -> None:
    """单弹匣时长 = 弹匣发数 × 周期（不是货舱总量）：0.2 / 0.0025 = 80 发 × 2.3046 s。"""
    overview = build_view("firepower", ctx)["overview"]
    weapon = _fitted(ctx, TURRET_GROUP)[0]

    capacity = weapon["attributes"][A.ATTR_CARGO_CAPACITY]  # 38：模块上的弹匣容积
    cycle = weapon["attributes"][A.ATTR_CYCLE_TIME]  # 51：物理秒
    raw_item = ctx.variant(BASE)["items"][ctx.items(BASE).index(weapon)]
    charge_volume = _raw_value(raw_item["charge"]["attributes"], A.ATTR_VOLUME)  # 161：m3
    assert charge_volume is not None

    rounds = math.floor(capacity / charge_volume + 1e-9)
    assert rounds == 80
    assert _close(overview["ammo_endurance_s"], round(rounds * cycle, 3), rel_tol=1e-6)


def test_firepower_application_gives_parameters_only(ctx: ViewContext) -> None:
    """应用层只给参数：跟踪速度取自炮，导弹两项为 None；没有命中结论。"""
    overview = build_view("firepower", ctx)["overview"]
    application = overview["application"]
    weapon = _fitted(ctx, TURRET_GROUP)[0]

    assert set(application) == {
        "tracking_rad_s",
        "optimal_signature_radius_m",
        "explosion_radius_m",
        "explosion_velocity_mps",
    }
    assert _close(
        application["tracking_rad_s"],
        round(weapon["attributes"][A.ATTR_TRACKING_SPEED], 3),
        rel_tol=1e-6,
    )
    assert _close(
        application["optimal_signature_radius_m"],
        round(weapon["attributes"][A.ATTR_OPTIMAL_SIG_RADIUS], 3),
        rel_tol=1e-6,
    )
    assert application["explosion_radius_m"] is None
    assert application["explosion_velocity_mps"] is None


def test_firepower_overload_matches_engine_variant(ctx: ViewContext) -> None:
    """过载：`on` 等于 overload 变体的 `-12`，`delta` 等于两次重算之差。"""
    overview = build_view("firepower", ctx)["overview"]
    base_dps = ctx.attrs(BASE)[A.ATTR_DAMAGE_DPS]
    overload_dps = ctx.attrs(OVERLOAD)[A.ATTR_DAMAGE_DPS]

    assert _close(overview["overload"]["off"]["dps"], round(base_dps, 3))
    assert _close(overview["overload"]["on"]["dps"], round(overload_dps, 3))
    assert _close(
        overview["overload"]["delta"]["dps"], round(overload_dps - base_dps, 3), rel_tol=1e-6
    )
    assert overview["overload"]["delta"]["dps"] > 0.0
    # 超载只加伤害乘子，不改射程（实测 54 不变）
    assert _close(overview["overload"]["delta"]["range_m"], 0.0)


def test_firepower_hardpoints_free_match_ship_attributes(ctx: ViewContext) -> None:
    """空闲挂点：102 / 101 直接取，与资源视角同名字段同源。"""
    overview = build_view("firepower", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert _close(overview["hardpoints_free"]["turret"], round(attrs[A.ATTR_TURRET_SLOTS_LEFT], 3))
    assert _close(
        overview["hardpoints_free"]["launcher"], round(attrs[A.ATTR_LAUNCHER_SLOTS_LEFT], 3)
    )


def test_firepower_missile_range_is_flight_time_times_velocity(ctx_from_eft) -> None:
    """导弹没有 `54`：射程 = 弹药 `maxVelocity`（37）× `explosionDelay`（281，毫秒）。"""
    ctx = ctx_from_eft(MISSILE_EFT)
    overview = build_view("firepower", ctx)["overview"]

    raw_charge = next(
        item["charge"]["attributes"]
        for item in ctx.variant(BASE)["items"]
        if item.get("charge") is not None
    )
    velocity = _raw_value(raw_charge, A.ATTR_MAX_VELOCITY)
    flight_ms = _raw_value(raw_charge, firepower.ATTR_EXPLOSION_DELAY)
    assert velocity is not None
    assert flight_ms is not None and flight_ms > 0.0

    assert overview["weapons"][0]["family"] == "missile"
    assert overview["range"]["optimal_m"] is None
    assert _close(
        overview["range"]["max_range_m"], round(velocity * flight_ms / MS_PER_S, 3), rel_tol=1e-6
    )
    assert overview["range"]["max_range_m"] > 0.0
    # 导弹的应用层参数来自弹药（爆炸半径 / 爆炸速度），炮的跟踪速度给 None
    assert overview["application"]["tracking_rad_s"] is None
    assert overview["application"]["explosion_radius_m"] is not None
    assert overview["application"]["explosion_velocity_mps"] is not None


# =============================================================================
# 三、生存
# =============================================================================


def test_survivability_layers_and_layer_ehp_match_ship_attributes(ctx: ViewContext) -> None:
    """`layers` = 263/265/9，`layer_ehp` = -30/-28/-29，全部直接取。"""
    overview = build_view("survivability", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert _close(overview["layers"]["shield_hp"], round(attrs[A.ATTR_SHIELD_CAPACITY], 3))
    assert _close(overview["layers"]["armor_hp"], round(attrs[A.ATTR_ARMOR_HP], 3))
    assert _close(overview["layers"]["hull_hp"], round(attrs[A.ATTR_HULL_HP], 3))
    assert _close(overview["layer_ehp"]["shield"], round(attrs[A.ATTR_EHP_SHIELD], 3))
    assert _close(overview["layer_ehp"]["armor"], round(attrs[A.ATTR_EHP_ARMOR], 3))
    assert _close(overview["layer_ehp"]["hull"], round(attrs[A.ATTR_EHP_HULL], 3))


def test_survivability_invariant_i1_layer_ehp_sums_to_mixed(ctx: ViewContext) -> None:
    """不变式 I1：`Σ layer_ehp == ehp_mixed`（容差 1e-6）。"""
    overview = build_view("survivability", ctx)["overview"]
    total = sum(overview["layer_ehp"].values())

    assert math.isclose(
        total, overview["ehp_mixed"], rel_tol=0.0, abs_tol=1e-6
    ), f"三层 EHP 之和 {total!r} != ehp_mixed {overview['ehp_mixed']!r}"


def test_survivability_i1_holds_exactly_on_raw_attributes(ctx: ViewContext) -> None:
    """I1 在**未舍入**的引擎值上严格成立（视角里 3 位小数只是展示口径）。"""
    attrs = ctx.attrs(BASE)
    raw_total = attrs[A.ATTR_EHP_SHIELD] + attrs[A.ATTR_EHP_ARMOR] + attrs[A.ATTR_EHP_HULL]

    assert math.isclose(raw_total, attrs[A.ATTR_EHP], rel_tol=0.0, abs_tol=1e-6)


def test_survivability_ehp_by_damage_matches_recompute(ctx: ViewContext) -> None:
    """`ehp_by_damage` 用按层复算：Σ_layer HP_layer / r_i,layer，正 ID（结构层不连号）。"""
    overview = build_view("survivability", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    layers = (
        (
            A.ATTR_SHIELD_CAPACITY,
            (
                A.ATTR_SHIELD_EM_RESONANCE,
                A.ATTR_SHIELD_EXPLOSIVE_RESONANCE,
                A.ATTR_SHIELD_KINETIC_RESONANCE,
                A.ATTR_SHIELD_THERMAL_RESONANCE,
            ),
        ),
        (
            A.ATTR_ARMOR_HP,
            (
                A.ATTR_ARMOR_EM_RESONANCE,
                A.ATTR_ARMOR_EXPLOSIVE_RESONANCE,
                A.ATTR_ARMOR_KINETIC_RESONANCE,
                A.ATTR_ARMOR_THERMAL_RESONANCE,
            ),
        ),
        (
            A.ATTR_HULL_HP,
            (
                A.ATTR_HULL_EM_RESONANCE,
                A.ATTR_HULL_EXPLOSIVE_RESONANCE,
                A.ATTR_HULL_KINETIC_RESONANCE,
                A.ATTR_HULL_THERMAL_RESONANCE,
            ),
        ),
    )

    for index, damage_type in enumerate(DAMAGE_TYPES):
        expected = sum(
            attrs[hp_attribute_id] / attrs[resonance_ids[index]]
            for hp_attribute_id, resonance_ids in layers
        )
        assert _close(
            overview["ehp_by_damage"][damage_type], round(expected, 3), rel_tol=1e-9
        ), f"{damage_type} 的纯伤 EHP 复算对不上"


def test_survivability_recompute_agrees_with_engine_single_profile(ctx: ViewContext) -> None:
    """复算口径与「换伤害剖面重算」等价：纯单系 profile 下引擎 `-43` 逐位相同。

    这条把「按层复算」这个选择钉死：两种口径都合法，结果必须一致，否则就是复算写错了。
    """
    overview = build_view("survivability", ctx)["overview"]

    for damage_type in DAMAGE_TYPES:
        profile = {name: (1.0 if name == damage_type else 0.0) for name in DAMAGE_TYPES}
        fit = {**ctx.fit, "environment": {"damage_profile": profile}}
        calculation = ctx.calculate_variant(f"test:profile:{damage_type}", fit)
        engine_ehp = ship_attributes(calculation)[A.ATTR_EHP]

        assert _close(
            overview["ehp_by_damage"][damage_type], round(engine_ehp, 3), rel_tol=1e-9
        ), f"{damage_type}：按层复算与引擎纯单系重算不一致"


def test_survivability_mixed_ehp_is_not_the_average(ctx: ViewContext) -> None:
    """混合 EHP 既不等于四系算术平均，也不等于调和平均（差异必须 > 1%）。"""
    overview = build_view("survivability", ctx)["overview"]
    values = [overview["ehp_by_damage"][damage_type] for damage_type in DAMAGE_TYPES]
    mixed = overview["ehp_mixed"]

    arithmetic = sum(values) / len(values)
    harmonic = len(values) / sum(1.0 / value for value in values)

    assert abs(mixed - arithmetic) / arithmetic > 0.01
    assert abs(mixed - harmonic) / harmonic > 0.01
    assert "不是四系平均" in overview["ehp_mixed_note"]


def test_survivability_thinnest_reports_layer_hole_and_ties(ctx: ViewContext) -> None:
    """最薄系 = 该层共振率最大的那一系；四系并列时必须给 None（不编「最薄」）。"""
    overview = build_view("survivability", ctx)["overview"]
    thinnest = overview["thinnest"]
    attrs = ctx.attrs(BASE)

    assert set(thinnest) == {"shield", "armor", "hull"}
    # 护盾最薄是 EM（共振率 0.875）、护甲最薄是爆炸（共振率 0.765）——与诊断规则同源
    assert thinnest["shield"]["damage_type"] == "em"
    assert thinnest["armor"]["damage_type"] == "explosive"
    assert _close(
        thinnest["armor"]["ehp"],
        round(attrs[A.ATTR_ARMOR_HP] / attrs[A.ATTR_ARMOR_EXPLOSIVE_RESONANCE], 3),
        rel_tol=1e-9,
    )
    # 一块 Damage Control II 把结构层四系压成同一个共振率 0.402 → 没有「最薄系」
    hull_resonances = [
        attrs[attribute_id]
        for attribute_id in (
            A.ATTR_HULL_EM_RESONANCE,
            A.ATTR_HULL_EXPLOSIVE_RESONANCE,
            A.ATTR_HULL_KINETIC_RESONANCE,
            A.ATTR_HULL_THERMAL_RESONANCE,
        )
    ]
    if len({round(value, 12) for value in hull_resonances}) == 1:
        assert thinnest["hull"]["damage_type"] is None
        assert thinnest["hull"]["ehp"] is not None


def test_survivability_repair_reads_engine_rates(ctx: ViewContext) -> None:
    """`repair` 直接用引擎值：armor 的 raw/effective 来自 -45/-48，护盾两条来源分开给。"""
    overview = build_view("survivability", ctx)["overview"]
    attrs = ctx.attrs(BASE)
    repair = overview["repair"]

    assert set(repair) == {"shield", "shield_passive", "armor", "hull"}
    assert _close(repair["armor"]["raw_hp_s"], round(attrs[A.ATTR_ARMOR_REPAIR_RATE], 3))
    assert _close(
        repair["armor"]["effective_ehp_s"], round(attrs[A.ATTR_ARMOR_EFFECTIVE_REPAIR_RATE], 3)
    )
    assert _close(
        repair["shield_passive"]["raw_hp_s"], round(attrs[A.ATTR_PASSIVE_SHIELD_RECHARGE], 3)
    )
    assert _close(
        repair["shield_passive"]["effective_ehp_s"],
        round(attrs[A.ATTR_PASSIVE_SHIELD_EFFECTIVE_RECHARGE], 3),
    )
    # 有效值 ≥ raw（有效 = raw ÷ 加权共振率，共振率 ≤ 1）
    assert repair["armor"]["effective_ehp_s"] > repair["armor"]["raw_hp_s"]


def test_survivability_repair_zero_value_and_has_source_are_two_facts(ctx: ViewContext) -> None:
    """f1 有护甲修复器：`armor` 有来源且 raw > 0；`shield`/`hull` 无模块 → `0.0` + `False`。

    T-19 裁决 C4：值统一给 `0.0`（下游数值 agent 直接算），「这一层有没有模块」由
    `has_source` 独立表达——**不得出现 `None`**，否则读三态的 agent 会把「没带
    盾修/结构修」误读成「不可知」。
    """
    overview = build_view("survivability", ctx)["overview"]
    attrs = ctx.attrs(BASE)
    repair = overview["repair"]

    assert A.ATTR_ARMOR_REPAIR_RATE in attrs, "样本应带护甲修复器（否则本断言失去意义）"
    assert repair["armor"]["has_source"] is True
    assert repair["armor"]["raw_hp_s"] > 0.0

    for layer, raw_attribute_id in (
        ("shield", A.ATTR_SHIELD_BOOST_RATE),
        ("hull", A.ATTR_HULL_REPAIR_RATE),
    ):
        assert raw_attribute_id not in attrs, "样本没有这一层的修复模块"
        assert repair[layer]["has_source"] is False
        assert isinstance(repair[layer]["raw_hp_s"], float), f"{layer}.raw_hp_s 不得是 None"
        assert repair[layer]["raw_hp_s"] == 0.0
        assert repair[layer]["effective_ehp_s"] == 0.0


def test_survivability_repair_without_module_is_zero_not_none(ctx_from_eft) -> None:
    """无修复模块的配装：三层 `raw_hp_s` 全是 `0.0`（float）且 `has_source is False`。"""
    ctx = ctx_from_eft(NO_REPAIR_EFT)
    attrs = ctx.attrs(BASE)
    repair = build_view("survivability", ctx)["overview"]["repair"]

    for raw_attribute_id in (
        A.ATTR_SHIELD_BOOST_RATE,
        A.ATTR_ARMOR_REPAIR_RATE,
        A.ATTR_HULL_REPAIR_RATE,
    ):
        assert raw_attribute_id not in attrs, "配装里不应有任何修复模块"

    for layer in ("shield", "armor", "hull"):
        assert repair[layer]["has_source"] is False
        assert isinstance(repair[layer]["raw_hp_s"], float), f"{layer}.raw_hp_s 不得是 None"
        assert repair[layer]["raw_hp_s"] == 0.0
        assert isinstance(repair[layer]["effective_ehp_s"], float)
        assert repair[layer]["effective_ehp_s"] == 0.0


def test_survivability_repair_never_emits_none(ctx: ViewContext) -> None:
    """`repair` 的**每个**数值叶子都是 float（含 shield_passive），`has_source` 是 bool。"""
    repair = build_view("survivability", ctx)["overview"]["repair"]

    assert set(repair) == {"shield", "shield_passive", "armor", "hull"}
    for layer, block in repair.items():
        assert set(block) == {"has_source", "raw_hp_s", "effective_ehp_s"}, layer
        assert isinstance(block["has_source"], bool), f"{layer}.has_source 必须是布尔"
        for field in ("raw_hp_s", "effective_ehp_s"):
            assert isinstance(block[field], float), f"{layer}.{field} 不是 float：{block[field]!r}"


def test_survivability_shield_layer_keeps_two_sources_apart(ctx: ViewContext) -> None:
    """护盾层两类来源各自一个 `has_source`：主动盾修（shield）与被动回充（shield_passive）。

    不合并的理由同模块 docstring §3：混在一起就分不清「模块修的」与「自己回的」。
    被动回充是舰体自带（有护盾容量就有）→ 样本里 `shield_passive.has_source` 为 `True`，
    而 `shield.has_source` 为 `False`（没带主动盾修）。
    """
    repair = build_view("survivability", ctx)["overview"]["repair"]
    attrs = ctx.attrs(BASE)

    assert A.ATTR_SHIELD_BOOST_RATE not in attrs
    assert repair["shield"]["has_source"] is False
    assert repair["shield"]["raw_hp_s"] == 0.0

    assert A.ATTR_PASSIVE_SHIELD_RECHARGE in attrs
    assert repair["shield_passive"]["has_source"] is True
    assert repair["shield_passive"]["raw_hp_s"] > 0.0
    assert _close(
        repair["shield_passive"]["raw_hp_s"], round(attrs[A.ATTR_PASSIVE_SHIELD_RECHARGE], 3)
    )


def test_survivability_overload_compares_two_variants(ctx: ViewContext) -> None:
    """过载：EHP 不变（不改共振率），修复速率上升。"""
    overview = build_view("survivability", ctx)["overview"]
    base_attrs = ctx.attrs(BASE)
    overload_attrs = ctx.attrs(OVERLOAD)

    assert _close(
        overview["overload"]["ehp_delta"],
        round(overload_attrs[A.ATTR_EHP] - base_attrs[A.ATTR_EHP], 3),
        rel_tol=1e-6,
    )
    assert _close(overview["overload"]["ehp_delta"], 0.0)
    assert overview["overload"]["repair_delta"] > 0.0


# =============================================================================
# 四、机动
# =============================================================================


def test_mobility_scalar_fields_match_ship_attributes(ctx: ViewContext) -> None:
    """六个标量字段直接取，且都是物理单位（对齐时间已是秒，跃迁速度即 AU/s）。"""
    overview = build_view("mobility", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert _close(overview["speed_mps"], round(attrs[A.ATTR_MAX_VELOCITY], 3))
    assert _close(overview["align_time_s"], round(attrs[A.ATTR_ALIGN_TIME], 3))
    assert _close(overview["signature_radius_m"], round(attrs[A.ATTR_SIGNATURE_RADIUS], 3))
    assert _close(overview["mass_kg"], round(attrs[A.ATTR_MASS], 3))
    assert _close(overview["agility"], round(attrs[A.ATTR_AGILITY], 3))
    assert _close(overview["warp_speed_au_s"], round(attrs[A.ATTR_WARP_SPEED_MULTIPLIER], 3))
    # 量级守卫：对齐时间是秒（不是毫秒原值），跃迁速度是 AU/s（不是 600）
    assert overview["align_time_s"] < 60.0
    assert overview["warp_speed_au_s"] < 50.0


def test_mobility_propulsion_states_have_three_tiers(ctx: ViewContext) -> None:
    """`propulsion_states` 四档齐全（base / off / active / overload），每档三个量。"""
    overview = build_view("mobility", ctx)["overview"]
    states = overview["propulsion_states"]

    assert {"base", "off", "overload"} <= set(states), "任务书要求的三档必须都在"
    assert "active" in states, "acceptance/field_spec.json 的 subpath 名是 active"
    for name in ("base", "off", "active", "overload"):
        assert set(states[name]) == {"speed_mps", "sig_m", "align_time_s"}
        assert all(value is not None for value in states[name].values())

    # base 就是「当前状态」，与顶层字段同值
    assert _close(states["base"]["speed_mps"], overview["speed_mps"])
    assert _close(states["base"]["sig_m"], overview["signature_radius_m"])
    assert _close(states["base"]["align_time_s"], overview["align_time_s"])


def test_mobility_mwd_changes_sig_radius_by_5_5(ctx: ViewContext) -> None:
    """有 MWD 时 active / off 的信号半径之比 ≈ 5.5（385 / 70）。

    T-12 技能档修正后重标：速度比从 5.89 变 **6.8645**——`Acceleration Control` 4 级把
    MWD 的速度加成再放大 20%（(5.8871−1)×1.2+1 = 6.8645）；信号半径比不变。
    """
    overview = build_view("mobility", ctx)["overview"]
    states = overview["propulsion_states"]

    if _close(states["active"]["sig_m"], states["off"]["sig_m"]):
        pytest.skip("该配装没有推进模块：信号半径开关比为 1")

    assert _close(states["active"]["sig_m"] / states["off"]["sig_m"], 5.5, rel_tol=1e-2)
    assert _close(states["active"]["speed_mps"] / states["off"]["speed_mps"], 6.8645, rel_tol=1e-2)
    assert states["off"]["align_time_s"] < states["active"]["align_time_s"]
    assert states["overload"]["speed_mps"] > states["base"]["speed_mps"]


# =============================================================================
# 五、缺值纪律（红线 N4：不许用默认值冒充 unknown）
# =============================================================================


def test_missing_attributes_become_none_not_zero(ctx: ViewContext) -> None:
    """样本没带无人机 / 战斗机 / 导弹：对应字段必须是 `None`，不是 0。"""
    overview = build_view("firepower", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert A.ATTR_DRONE_DPS not in attrs
    assert A.ATTR_FIGHTER_DPS not in attrs
    assert overview["dps"]["drone"] is None
    assert overview["dps"]["fighter"] is None
    assert overview["range"]["max_range_m"] is None


def test_no_weapon_fit_has_empty_weapons_and_none_split(ctx_from_eft) -> None:
    """没有武器时：`weapons` 空、四系占比全 `None`（分母为 0，不给假的 0）。"""
    ctx = ctx_from_eft(NO_WEAPON_EFT)
    overview = build_view("firepower", ctx)["overview"]

    assert overview["weapons"] == []
    assert set(overview["damage_split"].values()) == {None}
    assert overview["range"]["optimal_m"] is None
    assert overview["ammo_endurance_s"] is None


def test_repair_engine_missing_raw_becomes_zero_and_has_source_false(ctx: ViewContext) -> None:
    """引擎没输出的 raw（结构层）→ `0.0` + `has_source=False`；有效值 `0.0` 照实给。

    T-19 裁决 C4 就是替换掉这条旧断言（原口径是 `None`）：`None` 让下游数值运算必须
    自己补 0，而「这一层没有修复模块」这个事实现在由 `has_source` 表达——两种零由
    `has_source` 区分，不再由 `None` 冒充。
    """
    overview = build_view("survivability", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert A.ATTR_HULL_REPAIR_RATE not in attrs
    assert overview["repair"]["hull"]["raw_hp_s"] == 0.0
    assert overview["repair"]["hull"]["has_source"] is False
    assert A.ATTR_HULL_EFFECTIVE_REPAIR_RATE in attrs
    assert _close(overview["repair"]["hull"]["effective_ehp_s"], 0.0)


# =============================================================================
# 六、口径守卫：只给事实，不给判词；不改写单位
# =============================================================================


def test_views_only_emit_whitelisted_strings(ctx: ViewContext) -> None:
    """字符串只允许出现在名字 / 家族 / 伤害类型 / 口径提示里（红线：判词归 agent）。"""
    allowed = {
        "firepower": {"weapons[0].family", "weapons[0].name", "weapons[0].ammo_name"},
        "survivability": {
            "ehp_mixed_note",
            "thinnest.shield.damage_type",
            "thinnest.armor.damage_type",
            "thinnest.hull.damage_type",
        },
        "mobility": set(),
    }
    for view_name in VIEW_NAMES:
        overview = build_view(view_name, ctx)["overview"]
        found = set(_string_leaf_paths(overview))
        assert found <= allowed[view_name], f"{view_name} 出现计划外的字符串字段：{sorted(found)}"


def test_views_do_not_recompute_units(ctx: ViewContext) -> None:
    """视角值与 `ship_attributes()` 完全同源（视角层没有自己的换算表）。"""
    mobility_view = build_view("mobility", ctx)["overview"]
    firepower_view = build_view("firepower", ctx)["overview"]
    projected = ship_attributes(ctx.variant(BASE))

    assert _close(mobility_view["speed_mps"], round(projected[A.ATTR_MAX_VELOCITY], 3))
    assert _close(mobility_view["align_time_s"], round(projected[A.ATTR_ALIGN_TIME], 3))
    assert _close(firepower_view["dps"]["base"], round(projected[A.ATTR_DAMAGE_DPS], 3))


# =============================================================================
# 七、真实样本对照（T-20：D5 弹药续航跨视角一致 / D7 齐射口径）
# =============================================================================

ACCEPTANCE_FIXTURES = EVE_ROOT / "acceptance" / "fixtures"
"""验收样本（**只读**：`acceptance/` 不在本任务写入范围内）。"""

REAL_SAMPLES = ("f1-hecate-killmail.eft", "f4-non-hecate.json", "f5-no-mwd.json")
"""三个样本：正常炮船 / T3C 导弹船 / 无 MWD 导弹船（后两个就是 D5/D7 的暴露者）。"""

WEAPON_SLOTS = frozenset({"high", "medium", "low"})
"""齐射只算这三类挂架（无人机/战斗机的中队条目也带 `-21`，但舰船级 `-11` 不含它们）。"""

DAMAGE_GROUPS = frozenset({"Hybrid Weapon", "Energy Weapon", "Projectile Weapon", "Smart Bomb"})
"""测试侧独立写的武器 group 判据（不复用生产代码，避免「两边一起错」）。"""

MISSILE_GROUP_PREFIX = "Missile Launcher"
"""导弹发射器 group 名前缀（SDE 实测；测试侧独立写一遍）。"""

MAGAZINE_TOLERANCE = 1e-9
"""弹匣发数取整容差（与生产实现同一个理由：0.2 / 0.0025 可能算出 79.99999999）。"""


def _load_real_fit(engine: Engine, index, name: str) -> dict:
    """按后缀读验收样本（`.eft` 走 EFT，`.json` 走原始 killmail）并补技能档。"""
    path = ACCEPTANCE_FIXTURES / name
    if path.suffix == ".eft":
        fit = engine.load_eft(path.read_text(encoding="utf-8"))
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw = raw[0]
        raw.pop("zkb", None)  # zkillboard 附加字段
        fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return fit


@pytest.fixture(scope="module")
def real_ctx(engine: Engine, index):
    """样本名 → `ViewContext`（与验收命令完全同一条构造路径）。"""

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


def _is_weapon_group(group: str | None) -> bool:
    """测试侧武器判定：四类炮 group + 导弹发射器前缀（扫描发射器 / 隐身装置不算）。"""
    if not group:
        return False
    return group in DAMAGE_GROUPS or group.startswith(MISSILE_GROUP_PREFIX)


def _endurance_recomputed(ctx: ViewContext) -> float | None:
    """独立复算单弹匣时长：⌊capacity ÷ (弹药体积 × chargeRate)⌋ × 周期，取最短。

    刻意与 `views/shared.py` 分开写（自己读 `ctx.variant()` / `ctx.items()`），
    这样生产实现写错时这里会炸，而不是跟着一起错。
    """
    values: list[float] = []
    for raw_item, merged in zip(ctx.variant(BASE)["items"], ctx.items(BASE)):
        if merged.get("slot_type") == "cargo":
            continue
        if not _is_weapon_group(merged.get("group_name")):
            continue
        attrs = merged.get("attributes") or {}
        charge = raw_item.get("charge")
        source = {int(k): v for k, v in ((charge or {}).get("attributes") or {}).items()}
        source = {k: (v["value"] if isinstance(v, dict) else v) for k, v in source.items()}
        capacity = attrs.get(A.ATTR_CARGO_CAPACITY)
        cycle = attrs.get(A.ATTR_CYCLE_TIME)
        volume = source.get(A.ATTR_VOLUME)
        rate = attrs.get(A.ATTR_CHARGE_RATE, 1.0)
        if capacity is None or cycle is None or volume is None or volume <= 0.0 or rate <= 0.0:
            continue
        rounds = math.floor(capacity / (volume * rate) + MAGAZINE_TOLERANCE)
        if rounds <= 0:
            continue
        values.append(round(rounds * cycle, 3))
    return min(values) if values else None


def _weapon_volley_sum(ctx: ViewContext) -> float:
    """Σ 武器槽（high/medium/low）装备级 `-21`；无人机/战斗机/货舱不计。"""
    total = 0.0
    for raw_item, merged in zip(ctx.variant(BASE)["items"], ctx.items(BASE)):
        if merged.get("slot_type") not in WEAPON_SLOTS:
            continue
        value = _raw_value(raw_item.get("attributes") or {}, A.ATTR_DAMAGE_VOLLEY)
        if value:
            total += value
    return total


@pytest.mark.parametrize("name", REAL_SAMPLES)
def test_ammo_endurance_agrees_between_firepower_and_stability(real_ctx, name: str) -> None:
    """D5：同一份弹药续航在火力 / 不稳定两个视角**逐位相同**（三个样本各比一次）。

    历史缺陷：`firepower` 用 `51` + `chargeRate` + 弹药体积，`stability` 要求模块同时带
    `64 damageMultiplier`（导弹发射器没有）→ f4/f5 在稳定视角是 `None`、在火力视角是
    172.663 / 250.323。现在两处都调 `views/shared.py`，这个断言就是防回归的闸门。
    """
    ctx = real_ctx(name)
    firepower_value = build_view("firepower", ctx)["overview"]["ammo_endurance_s"]
    consumables = build_view("stability", ctx)["overview"]["consumables"]

    assert firepower_value is not None, f"{name}: 装了带弹药的武器就必须算得出续航"
    assert firepower_value == consumables["ammo_endurance_s"], (
        f"{name}: 两个视角必须逐位相同，实际 {firepower_value!r} vs "
        f"{consumables['ammo_endurance_s']!r}"
    )
    # 与独立复算对拍（弹匣发数 × 周期，取最短的那一门）
    assert firepower_value == _endurance_recomputed(ctx), f"{name}: 与独立复算不一致"
    # 回显口径自洽（rounds × cycle 已各自取 3 位小数，容差 1e-3）
    assert math.isclose(
        firepower_value,
        consumables["ammo_rounds"] * consumables["ammo_cycle_s"],
        rel_tol=1e-3,
    )


def test_ammo_endurance_known_values_on_real_samples(real_ctx) -> None:
    """D5 的实测值（验收报告点名的两个数）：f4 = 172.663、f5 = 250.323。"""
    f4 = build_view("firepower", real_ctx("f4-non-hecate.json"))["overview"]
    f5 = build_view("firepower", real_ctx("f5-no-mwd.json"))["overview"]

    assert f4["ammo_endurance_s"] == 172.663, "f4：40 发 × 4.31658 s"
    assert f5["ammo_endurance_s"] == 250.323, "f5：66 发 × 3.79277 s"


@pytest.mark.parametrize("name", REAL_SAMPLES)
def test_dph_is_weapon_volley_sum_and_matches_ship_alpha(real_ctx, name: str) -> None:
    """D7：`dph` = Σ 武器槽 `-21`；Σ 与舰船级 `-11` 同值（不变式 I2）。

    实测（本任务验证数据）：
    - f1：5 × 188.733536 = **943.667678** == `ship -11`；
    - f5：6 × 350.929885 = **2105.579310** == `ship -11`（**导弹船也成立** → 原先
      「`-11` 只聚合炮台」的假设被这两组数据直接证伪）；
    - f4：Σ = 0.0、`ship -11` = 0.0 —— **但装填了 5 门 HML**（`charge_type_id` 有值），
      所以 `dph` 必须是 `None`（输入不完整，T-22 / §0.6），不是 `0.0`。

    零伤害本身不触发 null：`total == 0` 且**没有带弹药的武器**时照实给 `0.0`。
    """
    ctx = real_ctx(name)
    overview = build_view("firepower", ctx)["overview"]
    total = _weapon_volley_sum(ctx)
    alpha = ctx.attrs(BASE).get(A.ATTR_DAMAGE_ALPHA)

    if total > 0.0:
        assert _close(overview["dph"], round(total, 3))
        assert alpha is not None and math.isclose(total, alpha, rel_tol=0.0, abs_tol=1e-6), (
            f"{name}: I2 破了（Σ 武器 -21 = {total!r}，ship -11 = {alpha!r}）"
        )
    else:
        armed = [
            record
            for record in shared.weapon_records(ctx, BASE)
            if record.get("slot_type") in shared.WEAPON_SLOT_TYPES
            and record.get("charge_type_id") is not None
        ]
        # 引擎把逐件 -21 和舰船级 -11 一起算成 0（f4 的突变数据缺失）
        assert alpha == 0.0
        if armed:
            assert overview["dph"] is None, f"{name}: 有带弹药的武器却零齐射 → 必须 null"
        else:
            assert overview["dph"] == 0.0, f"{name}: 没武器时 0 是真值"


def test_dph_known_values_are_bit_identical(real_ctx) -> None:
    """正常样本的 `dph` **逐位不变**（写死实测值，不放宽成范围）。"""
    f1 = build_view("firepower", real_ctx("f1-hecate-killmail.eft"))["overview"]
    f5 = build_view("firepower", real_ctx("f5-no-mwd.json"))["overview"]

    assert f1["dph"] == 943.668, "f1：5 × 188.733536 四舍五入到 3 位"
    assert f5["dph"] == 2105.579, "f5：6 × 350.929885 四舍五入到 3 位"


def test_f4_dph_is_null_because_mutation_data_is_missing(real_ctx) -> None:
    """T-22 / D7 的裁决：有带弹药的武器但齐射为 0 → `dph` 是 `null`，并带一条 flag。

    取代 T-20 留下的 `xfail(strict)`（那条期望「`dph > 0`」，被引擎侧的数据保真问题
    挡着）。现在期望改成**输入不完整的事实**：数值不可信 → `None`，可疑点显式给出。
    """
    ctx = real_ctx("f4-non-hecate.json")
    overview = build_view("firepower", ctx)["overview"]

    armed = [
        weapon
        for weapon in overview["weapons"]
        if weapon["ammo_name"] and weapon["count"] > 0 and weapon["family"] != "drone"
    ]
    assert len(armed) == 1 and armed[0]["count"] == 5, "f4 的武器清单里应有 5 门带弹药的 HML"

    assert overview["dph"] is None, "有弹药却零伤害：不得输出 0.0 伪事实"

    flags = {flag["id"]: flag for flag in D.evaluate(ctx)}
    assert "weapon_damage_zero" in flags, sorted(flags)
    flag = flags["weapon_damage_zero"]
    assert flag["view"] == "firepower"
    assert flag["message"] == (
        "有带弹药的武器但齐射伤害为 0：可能因突变模块数据缺失（killmail 不暴露 mutation）"
    )
    assert flag["evidence"]["armed_weapons"] == 5
    assert flag["evidence"]["volley_sum_hp"] == 0.0
    assert flag["evidence"]["dph_hp"] == 0.0


@pytest.mark.parametrize("name", ("f1-hecate-killmail.eft", "f5-no-mwd.json"))
def test_normal_samples_have_no_weapon_damage_zero_flag(real_ctx, name: str) -> None:
    """反面：正常样本（齐射 > 0）不得冒出 `weapon_damage_zero`（判据是矛盾，不是类型）。"""
    ctx = real_ctx(name)
    overview = build_view("firepower", ctx)["overview"]

    assert overview["dph"] is not None and overview["dph"] > 0.0
    assert "weapon_damage_zero" not in {flag["id"] for flag in D.evaluate(ctx)}
