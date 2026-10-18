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

另含 T-29 的「机库 vs 发射管」区分：f26 真样本（Thanatos，8 架载机全在 `fighter_bay`
= ESI flag 158）与**改造样本**（把 Templar 移进 `fighter_tube`）的对照断言。

约束：无网络、无随机（红线 N1）；样本只读；只碰本任务写入范围内的文件。
"""

from __future__ import annotations

import copy
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
from evefit.project import render as R
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


# =============================================================================
# 七·B、T-36：`dph` 的「一个武器系统都没有」只有一种口径（0.0），不随引擎给不给 `-11` 变形
# =============================================================================

NO_WEAPON_SAMPLES = (
    "f67-spotcheck-139255942.json",
    "f58-empty-fit.eft",
    "f61-no-rig.eft",
)
"""三个**真的**一门武器都没有的样本（f58 / f61 来自验收的「无武器」族，f67 是随堂测试）。

它们刚好覆盖引擎的两种表现：f67 装了几个高槽模块（扫描发射器 / 吸电 / 隐形 / 扰频）
→ `-11 = 0.0`（**有键**）；f58 空配装、f61 只有低 / 中槽模块 → `-11` / `-12`
**两个键都不存在**。T-36 之前这就是「同一类情形两种输出」的来源。
"""

WEAPON_DAMAGE_ZERO_FLAG = "weapon_damage_zero"
"""T-22 的矛盾 flag（**有带弹药的武器**却零齐射）。零伤害**本身**不触发它。"""


def _weapon_slot_records(ctx: ViewContext) -> list[dict]:
    """武器挂架（high / medium / low）记录；无人机 / 战斗机 / 货舱不算。"""
    return [
        record
        for record in shared.weapon_records(ctx, BASE)
        if record.get("slot_type") in WEAPON_SLOTS
    ]


def _strip_charges(node: object) -> None:
    """原地递归删掉配装里的 `charge`（= 把弹药拆下来）；`acceptance/` 的文件不碰。"""
    if isinstance(node, dict):
        node.pop("charge", None)
        for value in list(node.values()):
            _strip_charges(value)
    elif isinstance(node, list):
        for value in node:
            _strip_charges(value)


def _ctx_from_fit(engine: Engine, index, fit: dict) -> ViewContext:
    """用改造后的配装建 `ViewContext`（与 `real_ctx` 的同一条构造路径）。"""
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


@pytest.mark.parametrize("name", NO_WEAPON_SAMPLES)
def test_dph_is_zero_when_fit_has_no_weapon_at_all(real_ctx, name: str) -> None:
    """**T-36 靶心**：一个武器系统都没有 → `dph == 0.0`，**不管引擎给不给 `-11` 键**。

    改前：f67（`-11 = 0.0`）是 `0.0`，f58 / f61（`-11` / `-12` 都不存在）是 `None` ——
    同一个事实两种输出。裁决（任务书 T-36）：`0.0` 是**确定的事实**（这套配装没有伤害），
    不是「不知道」；`null` 只留给「有带弹药的武器却齐射为 0」这一个自相矛盾（f4）。

    四条前提断言保证这不是空转：没有武器挂架、没有无人机 / 战斗机、机库也空、
    `-11` 要么缺键要么就是 0.0。**反向检查**：把 `_dph()` 里「没有武器系统且缺键」那条
    分支改成 `return None`，本测试立刻变红（f58 / f61 两条参数）。
    """
    ctx = real_ctx(name)
    overview = build_view("firepower", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert _weapon_slot_records(ctx) == [], f"{name}: 前提不成立——样本里还有武器挂架模块"
    assert shared.weapon_records(ctx, BASE) == [], f"{name}: 前提不成立——还有无人机/战斗机"
    assert overview["fighter_bay"]["count"] == 0, f"{name}: 前提不成立——机库里有载机"
    assert overview["weapons"] == [], f"{name}: 前提不成立——weapons[] 不空"
    alpha = attrs.get(A.ATTR_DAMAGE_ALPHA)
    assert alpha is None or alpha == 0.0, f"{name}: `-11` 既缺键又不是 0（{alpha!r}）"

    assert overview["dph"] == 0.0, f"{name}: 没有武器时 0.0 是事实，不是 null"
    assert overview["dph"] is not None, f"{name}: 不得把「确定没有伤害」说成 unknown"

    # 值是真的 0 → 三态不可能是 unknown（`-11` 是负 ID，静态表给 assumed；要 `measured`
    # 必须改 `project/provenance.py`，不在本任务写入范围，已作为偏离项上报）。
    assert P.resolve(firepower.FIELD_SOURCES["dph"], overview["dph"]) in {
        "assumed",
        "measured",
    }

    assert WEAPON_DAMAGE_ZERO_FLAG not in {flag["id"] for flag in D.evaluate(ctx)}, (
        f"{name}: 零伤害本身不是矛盾，不该冒 weapon_damage_zero"
    )


def test_dph_of_bay_fighters_is_not_applicable_not_zero(real_ctx) -> None:
    """**边界**（T-36 与 T-29 的交界）：装了载机就不是「没有武器系统」→ `dph` 仍是 `None`。

    f26（Thanatos）机库 8 架载机、炮位全空：载机是**武器系统**（它的伤害走 `dps.fighter`、
    不进 `-11`），所以「这套配装没有伤害」是伪事实，`dph` 对该配装**不适用**（渲染层据
    `reasons.CONDITIONAL_LEAVES["firepower"]` 的 `dph` 报 `not_applicable`）。与 f58 / f61 /
    f67（连载机都没有）形成对照 —— 这正是不把 `0.0` 一刀切到所有「没装炮」配装的理由。
    """
    ctx = real_ctx("f26-carrier-fighter.json")
    overview = build_view("firepower", ctx)["overview"]

    assert overview["fighter_bay"]["count"] == 8, "f26 前提：机库 8 架载机"
    assert _weapon_slot_records(ctx) == [], "f26 前提：炮位全空"
    assert A.ATTR_DAMAGE_ALPHA not in ctx.attrs(BASE), "f26 前提：引擎不给 -11"

    assert overview["dph"] is None, "有载机的配装：dph 不适用，不得报 0.0 伪事实"
    flags = {flag["id"]: flag for flag in D.evaluate(ctx)}
    assert BAY_FLAG_ID in flags, "载机停在机库的事实由 fighters_in_bay_only 表达"
    assert WEAPON_DAMAGE_ZERO_FLAG not in flags, "没有装填弹药的武器，不构成零齐射矛盾"


@pytest.mark.parametrize(
    ("name", "alpha_key_present"),
    (
        ("f67-spotcheck-139255942.json", True),
        ("f58-empty-fit.eft", False),
        ("f61-no-rig.eft", False),
    ),
)
def test_dph_no_weapon_ignores_whether_engine_emits_alpha(
    real_ctx, name: str, alpha_key_present: bool
) -> None:
    """根因钉住：引擎只在**有高槽模块**时派生 `-11`；`dph` 的口径不看这个键。

    实测（T-36 探针）：f67 有高槽模块 → `-11 = 0.0`；f58 空配装、f61 只有低 / 中槽模块
    → `-11` 根本不存在。两条路径的 `dph` 必须**逐位相同**（都是 `0.0`），否则就是
    「引擎少给一个键 = 视角改口径」那个缺陷。
    """
    ctx = real_ctx(name)
    attrs = ctx.attrs(BASE)

    assert (A.ATTR_DAMAGE_ALPHA in attrs) is alpha_key_present, (
        f"{name}: 引擎 `-11` 键的存在性变了（本来 {'有' if alpha_key_present else '没有'}）"
    )
    if alpha_key_present:
        assert attrs[A.ATTR_DAMAGE_ALPHA] == 0.0
    assert build_view("firepower", ctx)["overview"]["dph"] == 0.0


def test_dph_is_none_only_for_armed_weapon_with_zero_volley(real_ctx) -> None:
    """对照（既有 T-22 规则，不许被 T-36 抹平）：f4 有带弹药的武器 → `None` + flag。

    f4 的 5 门 HML 装填了 Nova HAM（`charge_type_id = 27453`），逐件 `-21` 与舰船级
    `-11` 一起是 0（killmail 不暴露 mutation）→ 输入不完整 → `None`。这条与
    「无武器 → 0.0」是**互斥的两个判据**：把 `_dph()` 改成「零伤害一律 0.0」，本测试
    立刻变红。
    """
    ctx = real_ctx("f4-non-hecate.json")
    overview = build_view("firepower", ctx)["overview"]

    armed = [record for record in _weapon_slot_records(ctx) if record.get("charge_type_id")]
    assert len(armed) == 5, "f4 前提：5 门 HML 都装了弹药"
    assert ctx.attrs(BASE)[A.ATTR_DAMAGE_ALPHA] == 0.0, "f4 前提：舰船级 -11 也是 0"

    assert overview["dph"] is None, "有带弹药的武器却零齐射：不许输出 0.0 伪事实"
    assert WEAPON_DAMAGE_ZERO_FLAG in {flag["id"] for flag in D.evaluate(ctx)}


def test_dph_is_zero_for_weapon_without_ammo(engine: Engine, index) -> None:
    """第三种情形：**有武器但没装弹药** → `dph == 0.0`（保持既有口径，不要改成 null）。

    改造样本（`acceptance/` 只读，所以对 f4 的深拷贝剥掉 `charge`）：5 门 HML 还装着、
    弹药全空。实测引擎此时**照样给** `-11 = 0.0`（有高槽模块），所以这条本来就是 0.0；
    T-36 把它显式钉住，免得「有武器 → null」的直觉把真 0 变成 unknown。与 f4（有弹药、
    零齐射）**只差一个 charge**：一个 0.0、一个 None。
    """
    fit = copy.deepcopy(_load_real_fit(engine, index, "f4-non-hecate.json"))
    _strip_charges(fit)
    ctx = _ctx_from_fit(engine, index, fit)
    overview = build_view("firepower", ctx)["overview"]

    assert {weapon["ammo_name"] for weapon in overview["weapons"]} == {None}, (
        "前提不成立：弹药没被剥掉"
    )
    assert {
        record.get("charge_type_id") for record in _weapon_slot_records(ctx)
    } == {None}, "前提不成立：还有武器装着弹药"
    assert A.ATTR_DAMAGE_ALPHA in ctx.attrs(BASE), "前提不成立：引擎没给 -11，情形就不是这一条"
    assert ctx.attrs(BASE)[A.ATTR_DAMAGE_ALPHA] == 0.0

    assert overview["dph"] == 0.0, "有武器没弹药：齐射是真 0，不是「不知道」"
    assert overview["dph"] is not None
    assert WEAPON_DAMAGE_ZERO_FLAG not in {flag["id"] for flag in D.evaluate(ctx)}, (
        "没有弹药就不构成「有弹药却零伤害」的矛盾"
    )


# =============================================================================
# 七·C、T-37：`dps` 系列的「没有武器系统」也只有一种口径（0.0），与 `dph` 对齐
# =============================================================================

BIG_BUDGET = 10**9
"""渲染预算调成「不可能超」，让 `provenance` / `unknown_reasons` 保持原始形态（不降级）。"""

T37_SAMPLES = (*NO_WEAPON_SAMPLES, "f26-carrier-fighter.json")
"""T-37 的四个对照样本：三个**没有**武器系统（f67 / f58 / f61）+ 一个有（f26 载机）。"""

T37_ZERO_PATHS = (
    "dps.base",
    "dps.with_reload",
    "overload.off.dps",
    "overload.on.dps",
    "overload.delta.dps",
)
"""T-37 统一成 `0.0` 的五条路径（`dph` 已在七·B 钉住，这里不重复）。"""


def _rendered_firepower(ctx: ViewContext) -> dict:
    """渲染一次火力视角条目（`{overview, provenance, unknown_reasons, confidence, flags}`）。"""
    original = R.BUDGET_CHARS
    R.BUDGET_CHARS = BIG_BUDGET
    try:
        return R.render_overview(ctx, views=("firepower",))["views"]["firepower"]
    finally:
        R.BUDGET_CHARS = original


@pytest.mark.parametrize("name", NO_WEAPON_SAMPLES)
def test_dps_series_is_zero_when_fit_has_no_weapon_at_all(real_ctx, name: str) -> None:
    """**T-37 靶心**：一个武器系统都没有 → 五条 `dps` 路径全是 `0.0`（缺 `-12` 也照给）。

    改前：f67 的 `-12` / `-13` 键在（`0.0`）→ `dps.base = 0.0`；f58（空配装）/ f61（全中 /
    低槽）两个键都不存在 → `dps.base = None`。同一事实两种输出，与 T-36 之前的 `dph`
    是同一个病，T-37 按同一判据（`_has_any_weapon_system()`）收口。

    四条前提断言保证它不是空转（样本真没有武器系统、引擎真缺或真给 0）。
    **反向检查**：把 `firepower._dps_raw()` 里「缺键且没有武器系统 → `0.0`」的补值去掉，
    f58 / f61 两条参数立刻变红（另有 `test_t37_zero_fill_is_not_vacuous` 当场演示）。
    """
    ctx = real_ctx(name)
    overview = build_view("firepower", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert _weapon_slot_records(ctx) == [], f"{name}: 前提不成立——还有武器挂架模块"
    assert shared.weapon_records(ctx, BASE) == [], f"{name}: 前提不成立——还有无人机/战斗机"
    assert overview["fighter_bay"]["count"] == 0, f"{name}: 前提不成立——机库里有载机"
    for attribute_id in (A.ATTR_DAMAGE_DPS, A.ATTR_DAMAGE_DPS_WITH_RELOAD):
        raw = attrs.get(attribute_id)
        assert raw is None or raw == 0.0, f"{name}: 引擎 {attribute_id} 既缺键又不是 0（{raw!r}）"

    for path in T37_ZERO_PATHS:
        assert _resolve(overview, path) == 0.0, f"{name}.{path} 没有武器系统时必须是 0.0"
    # 与七·B 的 `dph` 口径**逐项一致**：同一情形不许「一个 0.0、一个 None」
    assert overview["dph"] == 0.0, f"{name}: dph 与 dps 必须同口径（T-36 / T-37）"


def test_dps_series_keeps_engine_value_when_a_weapon_system_exists(real_ctx) -> None:
    """边界（T-36 与 T-37 的交界）：f26 有载机 = **有**武器系统 → `dps` 照引擎给的值。

    判据是「有没有任何武器系统」，不是「有没有炮」：机库里的载机也算（与 `dph` 判 f26
    为 `None` 用的是同一份事实），所以这里**不**补 0.0——引擎的 `-12 = 0.0` 才是值。
    任务书要求 f26 保持原样，这条断言就是「没被 T-37 抹平」的凭证。
    """
    ctx = real_ctx("f26-carrier-fighter.json")
    attrs = ctx.attrs(BASE)
    oattrs = ctx.attrs(OVERLOAD)
    overview = build_view("firepower", ctx)["overview"]

    assert overview["fighter_bay"]["count"] == 8, "f26 前提：机库 8 架载机"
    assert A.ATTR_DAMAGE_DPS in attrs, "f26 前提：引擎给了 -12"
    assert attrs[A.ATTR_DAMAGE_DPS] == 0.0

    assert overview["dps"]["base"] == round(attrs[A.ATTR_DAMAGE_DPS], 3)
    assert overview["dps"]["with_reload"] == round(attrs[A.ATTR_DAMAGE_DPS_WITH_RELOAD], 3)
    assert overview["overload"]["off"]["dps"] == round(attrs[A.ATTR_DAMAGE_DPS], 3)
    assert overview["overload"]["on"]["dps"] == round(oattrs[A.ATTR_DAMAGE_DPS], 3)
    assert overview["dph"] is None, "有载机的配装：dph 仍是不适用（T-36），没被 T-37 改掉"


def test_t37_does_not_touch_an_armed_fit_with_zero_volley(real_ctx) -> None:
    """f4（有弹药零伤害）：`dph is None` + `weapon_damage_zero` 原样，`dps` 不被补 0。

    任务书点名这条**必须不变**：T-37 只改「一个武器系统都没有」的缺键口径，有武器的样本
    连补值分支都不进（`_dps_raw()` 只看值在不在）。
    """
    ctx = real_ctx("f4-non-hecate.json")
    attrs = ctx.attrs(BASE)
    overview = build_view("firepower", ctx)["overview"]

    assert overview["dph"] is None, "有带弹药的武器却零齐射：仍是 None（T-22）"
    assert WEAPON_DAMAGE_ZERO_FLAG in {flag["id"] for flag in D.evaluate(ctx)}
    assert overview["dps"]["base"] == 83.866, "f4 的 dps 是引擎真值，T-37 不得改写"


@pytest.mark.parametrize("name", T37_SAMPLES)
def test_dps_series_never_falls_into_unknown_after_unification(real_ctx, name: str) -> None:
    """四样本：五条路径**有值**（`0.0`）→ `provenance` 不是 `unknown`，原因表里也没有它们。

    为什么必须一起钉：`unknown_reasons` 是 `provenance` 的**稀疏平行表**（`reasons.py`
    的判定优先级表），一旦某条路径有值却仍留一条原因，两张表的键空间就自相矛盾。
    T-37 因此把 `reasons.CONDITIONAL_LEAVES["firepower"]` 里的 `dps.base` /
    `dps.with_reload` 删掉，并把 `overload` 前缀收窄成三条射程叶子（`overload.*.dps`
    现在有值，留着前缀就是「有值却说不适用」——T-34 消灭过的那类污染）。

    反向：`dps.drone` / `dps.fighter` **仍**是条件字段（引擎不给 `-14` / `-58` 时是 `None`），
    这条断言防止有人顺手把它们也补成 `0.0`。f26 的 `-58 = 0.0` 是**有值**的 0（载机在
    机库未发射，`dps.fighter` 恒 0 是正确行为）→ 它不在原因表里，也不该被补值。
    """
    ctx = real_ctx(name)
    entry = _rendered_firepower(ctx)
    overview = entry["overview"]
    provenance = entry["provenance"]
    reasons = entry["unknown_reasons"]
    attrs = ctx.attrs(BASE)

    for path in T37_ZERO_PATHS:
        assert _resolve(overview, path) == 0.0, f"{name}.{path} 应为 0.0"
        assert path not in reasons, f"{name}.{path} 有值却写了一条 unknown 原因（不稀疏）"
        assert provenance[path] != "unknown", f"{name}.{path} 有值却报 unknown"
        assert provenance[path] in {"assumed", "measured"}, (
            f"{name}.{path} 的三态只能是 assumed / measured（-12/-13 是负 ID → assumed）"
        )

    for path, attribute_id in (
        ("dps.drone", A.ATTR_DRONE_DPS),
        ("dps.fighter", A.ATTR_FIGHTER_DPS),
    ):
        if attribute_id in attrs:  # 引擎给了值（f26 的 -58 = 0.0）→ 有值，不归条件字段管
            assert _resolve(overview, path) is not None
            assert path not in reasons, f"{name}.{path} 有值却在原因表里"
            continue
        assert _resolve(overview, path) is None, f"{name}.{path} 前提不成立：引擎给了值"
        assert reasons[path] == "not_applicable", f"{name}.{path} 仍是条件字段（T-37 没动它）"

    # 稀疏平行表自洽：每条原因都必须落在一条 unknown 的 provenance 上
    unknown_paths = {path for path, state in provenance.items() if state == "unknown"}
    assert set(reasons) <= unknown_paths, f"{name}: unknown_reasons 有 provenance 里不存在的键"


def test_t37_zero_fill_is_not_vacuous(real_ctx, monkeypatch: pytest.MonkeyPatch) -> None:
    """**变异自检**：把 `_dps_raw()` 退回「只读引擎、缺键就是 `None`」，断言必须变红。

    T-37 的靶心断言（五条路径 = `0.0`）只有在 f58 / f61 这类**缺键**样本上才有区分度；
    若哪天有人把补值分支删掉，本测试会以 `None != 0.0` 当场失败——证明它不是永真。
    """

    def engine_only(attrs: dict[int, float], attribute_id: int, *, has_weapon_system: bool):
        return attrs.get(attribute_id)

    monkeypatch.setattr(firepower, "_dps_raw", engine_only)
    ctx = real_ctx("f58-empty-fit.eft")
    overview = build_view("firepower", ctx)["overview"]

    assert A.ATTR_DAMAGE_DPS not in ctx.attrs(BASE), "前提：f58 引擎不给 -12"
    assert overview["dps"]["base"] is None, "变异后必须回到 None（否则靶心断言是永真的）"
    assert overview["overload"]["off"]["dps"] is None
    # 对照：未变异时同一份 ctx 是 0.0（redness 来自这处变异，不是环境问题）
    monkeypatch.undo()
    assert build_view("firepower", ctx)["overview"]["dps"]["base"] == 0.0


# =============================================================================
# 八、T-29：机库载机 ≠ 武器（`fighter_bay` 与 `fighter_tube` 必须分开）
# =============================================================================

F26_CARRIER = "f26-carrier-fighter.json"
"""Thanatos 真实样本：8 架载机全在 `fighter_bay`（ESI flag 158），159–163 一条都没有。"""

TEMPLAR_I_TYPE_ID = 23055
"""`Templar I`（本样本里 ×5 的那批；T-28 改造实验搬进发射管的就是它）。"""

SIREN_I_TYPE_ID = 40346
"""`Siren I`（×3，支援战斗机：进管后只记 `-61` 支援槽，仍不出 `-58`——与「在机库」无关）。"""

BAY_FLAG_ID = "fighters_in_bay_only"
"""「机库有货但未发射」的事实提示（不是缺陷，也不是自相矛盾）。"""


def test_classify_weapon_keeps_tube_and_drops_bay() -> None:
    """根因单测：`fighter_tube` 是武器（family=fighter），`fighter_bay` 不是。

    这是 T-29 的靶心——旧实现把两个槽位一起判成 `family: "fighter"`，于是机库里的
    载机被列进 `weapons[]`，而 `dps.fighter` 恒 0 → 验收不变式恒假。
    """
    tube = {"slot_type": "fighter_tube", "type_id": TEMPLAR_I_TYPE_ID}
    bay = {"slot_type": "fighter_bay", "type_id": TEMPLAR_I_TYPE_ID}

    assert shared.classify_weapon(tube, {}) == (True, "fighter")
    assert shared.classify_weapon(bay, {}) == (False, None), "机库里的载机不是武器"


def test_f26_bay_fighters_are_not_in_weapons_but_still_reported(real_ctx) -> None:
    """f26：`weapons[]` 不含 fighter 项；机库事实由 `fighter_bay` + flag 表达。

    三条一起断言，缺一条这个修复就退化成「丢掉事实」：
    ① `weapons[]` 不再冒出未参战的载机（旧行为：Siren I ×3 + Templar I ×5，family=fighter）；
    ② `fighter_bay` 如实给出 8 架 / 14000 m³ / 两个型号；
    ③ `dps.fighter` 仍是 **0.0**（正确的 0，不是 `None`，也不是报错）。
    """
    ctx = real_ctx(F26_CARRIER)
    overview = build_view("firepower", ctx)["overview"]

    assert [w for w in overview["weapons"] if w["family"] == "fighter"] == [], (
        "机库里的载机不得进 weapons[]（那是库存，不是参战武器）"
    )
    assert overview["weapons"] == [], "f26 没有别的武器，清单应为空"

    bay = overview["fighter_bay"]
    assert bay["count"] == 8, "Siren I ×3 + Templar I ×5"
    assert bay["types"] == ["Siren I", "Templar I"], "按首次出现顺序去重"
    assert bay["capacity_used_m3"] == 14000.0, "船级 -57 fighterCapacityLoad（m³）"

    assert overview["dps"]["fighter"] == 0.0, "载机在机库 → 0 是正确值，不得改成 None"

    flags = {flag["id"]: flag for flag in D.evaluate(ctx)}
    assert BAY_FLAG_ID in flags, sorted(flags)
    flag = flags[BAY_FLAG_ID]
    assert flag["view"] == "firepower"
    assert flag["message"] == "机库内有 8 架载机但未发射（发射管占用 0），其伤害未计入"
    assert flag["evidence"]["fighter_bay_count"] == 8
    assert flag["evidence"]["fighter_tubes_used"] == 0
    assert flag["evidence"]["capacity_used_m3"] == 14000.0
    assert flag["evidence"]["fighter_bay_types"] == ["Siren I", "Templar I"]
    assert flag["evidence"]["fighter_dps_hp_s"] == 0.0


def test_f26_bay_field_covers_its_leaves(real_ctx) -> None:
    """新字段的每个叶子都有 `FIELD_SOURCES` 映射（红线 N3：没映射 = 无 provenance）。

    `fighter_bay.types` 是**字符串列表**：渲染层把标量列表当一个叶子
    （`render._leaf_values` 的「其余」分支），本文件的 `_numeric_leaf_paths` 同样如此，
    所以它也必须登记（值 `None` → `assumed`），否则航母样本会多出无来源的叶子。
    """
    overview = build_view("firepower", real_ctx(F26_CARRIER))["overview"]

    missing = sorted(set(_numeric_leaf_paths(overview)) - set(firepower.FIELD_SOURCES))
    assert not missing, f"f26 这些叶子没有 FIELD_SOURCES 映射：{missing}"
    for path in ("fighter_bay.count", "fighter_bay.types", "fighter_bay.capacity_used_m3"):
        _resolve(overview, path)
    assert firepower.FIELD_SOURCES["fighter_bay.capacity_used_m3"] == A.ATTR_FIGHTER_BAY_USED

    # 字符串只允许是载机型号名（与 `test_views_only_emit_whitelisted_strings` 同一口径，
    # 只是样本换成航母：Hecate 样本的机库是空的，那边校验不到这两个名字）。
    assert set(_string_leaf_paths(overview)) == {
        "fighter_bay.types[0]",
        "fighter_bay.types[1]",
    }, "航母样本的字符串必须只有载机型号名"


def test_f26_launched_fighter_produces_dps_and_drops_the_flag(
    engine: Engine, index, real_ctx
) -> None:
    """改造样本（T-28 S5 的 B 变体）：把 Templar 移进 `fighter_tube` → 伤害链路通了。

    唯一变量是 `slot.type`（`fighter_bay` → `fighter_tube`），架数、技能档、其余模块全不动
    （`real_ctx` 只用来断言基线，改造后必须新建 ctx）。期望：

    - `weapons[]` 出现 `family=fighter` 的 Templar I（这时它**真的**参战了）；
    - `dps.fighter > 0`（技能档推导后 495.455；T-28 无技能档时 240.846，量级同阶）；
    - `fighters_in_bay_only` **消失**（`-62 = 1`，已有中队在管里）。
    """
    baseline = build_view("firepower", real_ctx(F26_CARRIER))["overview"]
    assert baseline["dps"]["fighter"] == 0.0 and baseline["weapons"] == [], "基线必须仍是机库状态"

    moved = copy.deepcopy(_load_real_fit(engine, index, F26_CARRIER))
    launched = 0
    for item in moved["items"]:
        if item.get("type_id") == TEMPLAR_I_TYPE_ID:
            item["slot"] = {"type": "fighter_tube", "index": 0}
            item["state"] = "active"
            launched += int(item.get("quantity") or 1)

    assert launched == 5, "改造成本必须是「5 架 Templar 移进发射管」，否则对照不成立"

    bay_left = {
        item.get("type_id")
        for item in moved["items"]
        if (item.get("slot") or {}).get("type") == "fighter_bay"
    }
    assert bay_left == {SIREN_I_TYPE_ID}, "机库里应当只剩 Siren I（支援机不改动）"

    ctx = ViewContext(
        engine=engine,
        index=index,
        fit=moved,
        assumptions=default_assumptions(moved, engine, index=index),
        meta=engine.meta(),
    )
    overview = build_view("firepower", ctx)["overview"]

    fighters = [w for w in overview["weapons"] if w["family"] == "fighter"]
    assert len(fighters) == 1 and fighters[0]["name"] == "Templar I"
    assert fighters[0]["count"] == 5

    assert overview["dps"]["fighter"] is not None
    assert overview["dps"]["fighter"] > 0.0, "载机进发射管后 -58 必须为正（链路是通的）"
    assert ctx.attrs(BASE)[A.ATTR_FIGHTER_TUBES_USED] > 0.0

    assert BAY_FLAG_ID not in {flag["id"] for flag in D.evaluate(ctx)}, (
        "已有中队在发射管里 → 不得再报「机库有货未发射」"
    )

    # 机库口径仍如实统计（不是被清零）：只剩 Siren I ×3。
    assert overview["fighter_bay"]["count"] == 3
    assert overview["fighter_bay"]["types"] == ["Siren I"]


@pytest.mark.parametrize("name", ("f1-hecate-killmail.eft", "f5-no-mwd.json"))
def test_non_carrier_samples_have_empty_fighter_bay(real_ctx, name: str) -> None:
    """非航母样本：`fighter_bay` 字段**存在但全零**（schema 稳定），且没有该 flag。"""
    ctx = real_ctx(name)
    overview = build_view("firepower", ctx)["overview"]

    assert overview["fighter_bay"] == {"count": 0, "types": [], "capacity_used_m3": 0.0}
    assert BAY_FLAG_ID not in {flag["id"] for flag in D.evaluate(ctx)}


def test_fighter_bay_summary_reports_count_types_and_ship_capacity(real_ctx) -> None:
    """机库摘要的口径单测：架数按 `quantity` 求和、型号去重、占用取船级 `-57`。

    另一条**实测约束**（T-29 探针）：`-57` 是「载机舱容占用」，把 Templar 移进发射管后
    它**仍是 14000**（S5 的 B 变体实测）——所以 `capacity_used_m3` 不等于「机库条目体积
    之和」，它是船级属性原值，别改成按 `fighter_bay` 条目累加体积。
    """
    all_bay = shared.fighter_bay_summary(real_ctx(F26_CARRIER))

    assert all_bay == {"count": 8, "types": ["Siren I", "Templar I"], "capacity_used_m3": 14000.0}


# =============================================================================
# 九、T-35：推进状态语义（双推进叠加 / 「推进关闭」口径）
# =============================================================================
#
# 随堂测试 f67 暴露的两件事（都属「状态语义缺失」，与 X1 的 Loki cloak 同类）：
#
# 1. 引擎**不会**因为 `max_group … limit=active` 硬违规自动降级模块，于是 AB 的
#    `speedFactor 156.25` 与 MWD 的 `631.25` 一起叠上 → 59,928.363 m/s，而机动视角
#    没有任何提示 → `propulsion_state_conflict`（T-38 改名，旧名已废弃）
#    只陈述事实（数值一个都不改）；
# 2. `mwd_sig_increase` 文案写「关闭时 65.72 m」，同视角 `propulsion_states.off.sig_m`
#    却是 60.0 —— 两个「关闭时」是两个不同的状态（只关推进 vs 全模块离线）。

F67_SPOTCHECK = "f67-spotcheck-139255942.json"
"""Bifrost 指挥驱逐舰（killmail 139255942）：历史上第一条真实存在的双推进配置。"""

PROPULSION_GROUP = "Propulsion Module"
"""推进模块的 SDE group 名（AB 与 MWD 同组；`shared.PROPULSION_GROUP` 的测试侧独立写法）。"""

PROPULSION_STACK_FLAG = "propulsion_state_conflict"
"""T-38 改名后的 id（旧名已废弃）：推进模块同组同时启用 → **状态冲突**。

口径（主人裁决）：双推进一起带很常见，只是**不能同时启用** —— 所以这不是配装问题。
"""

PROPULSION_STACK_MESSAGE = (
    "推进模块同组同时启用（AB 与 MWD），游戏内同一时刻只能启用其一；"
    "速度/信号/对齐基于「多个推进同时启用」的非真实状态"
)
"""T-38 裁决后的文案（f67 逐字）：`{conflict}` 由 evidence 渲染，f67 → `AB 与 MWD`。"""

DUAL_PROPULSION_ACTIVE = (
    F67_SPOTCHECK,
    "f27-dual-prop.json",
    "f57-dual-prop.eft",
)
"""两个推进模块**都是 active** 的样本：引擎报 `group_id=46 used=2 / allowed=1`。

⚠️ **f27 也在这组里**（实测）：它名字叫 dual-prop，两个推进模块确实都是 active，引擎
同样报 `max_group` 硬违规。任务书把 f27 列进「正常单推进样本」是笔误 —— 按字面把它写成
「无 flag」等于要求视角对引擎已经报出的硬违规闭嘴。真正「带两件推进但只有一件启用」的
样本是 f1（MWD active + AB offline）、f9 / f45（推进模块 offline）。
"""

SINGLE_PROPULSION = (
    "f1-hecate-killmail.eft",
    "f5-no-mwd.json",
    "f45-t3c-subsys-3.json",
)
"""单推进（或带两件但只有一件 active）样本：**不得**出现 `propulsion_state_conflict`。"""


def _propulsion_items(ctx: ViewContext) -> list[dict]:
    """已装推进模块（测试侧独立判定：读合并视图的 group 名，不查生产常量）。"""
    return [
        item
        for item in ctx.items(BASE)
        if item.get("slot_type") != "cargo" and item.get("group_name") == PROPULSION_GROUP
    ]


@pytest.mark.parametrize("name", DUAL_PROPULSION_ACTIVE)
def test_dual_propulsion_emits_state_conflict_flag(real_ctx, name: str) -> None:
    """双推进 active：必须有一条 `mobility` flag 说明「这组数是叠加出来的」。"""
    ctx = real_ctx(name)
    flags = {flag["id"]: flag for flag in D.evaluate(ctx)}

    assert PROPULSION_STACK_FLAG in flags, f"{name}: 缺 flag，实际 {sorted(flags)}"
    flag = flags[PROPULSION_STACK_FLAG]
    assert flag["view"] == "mobility"
    assert flag["evidence"]["used"] == 2 and flag["evidence"]["allowed"] == 1
    assert flag["evidence"]["group_id"] == 46
    assert flag["message"].startswith("推进模块同组同时启用（")
    assert "游戏内同一时刻只能启用其一；速度/信号/对齐基于「多个推进同时启用」的非真实状态" in flag["message"]

    # 叠加双方的**模块名**不重复进本 flag 的 evidence：`mwd_sig_increase` 已经给了
    # （体积预算 17000 chars，f47 就差这一点；见 diagnostics 里的注释）
    propulsion_flag = flags["mwd_sig_increase"]
    assert len(propulsion_flag["evidence"]["propulsion"]) == 2

    if name == F67_SPOTCHECK:
        assert flag["evidence"]["conflict"] == "AB 与 MWD"
        assert propulsion_flag["evidence"]["propulsion"] == [
            "1MN Y-S8 Compact Afterburner",
            "5MN Y-T8 Compact Microwarpdrive",
        ]
        assert flag["message"] == PROPULSION_STACK_MESSAGE


@pytest.mark.parametrize("name", SINGLE_PROPULSION)
def test_single_propulsion_samples_have_no_stack_flag(real_ctx, name: str) -> None:
    """单推进样本**不得**产出该 flag（否则就是把正常配装说成状态冲突）。"""
    ctx = real_ctx(name)
    ids = {flag["id"] for flag in D.evaluate(ctx)}
    assert PROPULSION_STACK_FLAG not in ids, f"{name}: 单推进不该报同组冲突"


def test_f67_stack_flag_keeps_engine_numbers_and_module_states(real_ctx) -> None:
    """`只给事实`：速度仍是引擎原值，模块状态一个都不许改（降级是求解，不在本期）。

    - `speed_mps = 59,928.363`（overload 134,777.472）：**不许**重算、不许挑一个模块停用；
    - 两件推进模块仍是 `active`（引擎不降级，我们也不降级）；
    - `mwd_sig_increase` 与 `propulsion_state_conflict` **并存**：一条说信号被撑大，
      一条说撑大的那个数是叠加出来的，两条不冲突。
    """
    ctx = real_ctx(F67_SPOTCHECK)
    overview = build_view("mobility", ctx)["overview"]
    attrs = ctx.attrs(BASE)

    assert _close(overview["speed_mps"], round(attrs[A.ATTR_MAX_VELOCITY], 3))
    assert overview["speed_mps"] == 59928.363, "快照：引擎按 AB+MWD 叠加算出的速度（驱逐舰不可能）"
    assert overview["propulsion_states"]["overload"]["speed_mps"] == 134777.472
    assert _close(
        overview["propulsion_states"]["active"]["speed_mps"], overview["speed_mps"]
    )

    assert [item.get("state") for item in _propulsion_items(ctx)] == ["active", "active"], (
        "不得为了让数值好看而自动停用模块"
    )

    ids = {flag["id"] for flag in D.evaluate(ctx)}
    assert {PROPULSION_STACK_FLAG, "mwd_sig_increase"} <= ids


@pytest.mark.parametrize("name", DUAL_PROPULSION_ACTIVE)
def test_flag_message_numbers_match_propulsion_states(real_ctx, name: str) -> None:
    """**T-35 靶心**：`mwd_sig_increase` 文案里的数字必须能在 `propulsion_states` 里找到。

    口径（选定并说明）：文案的「关闭时」= `propulsion_off.sig_m`（**只关推进模块**，
    其余模块保持当前状态），「启用时」= `base.sig_m`（当前状态）。两者都由
    `shared.propulsion_off_attrs()` 的同一次变体计算给出，与 flag 是同一个浮点数。
    `off`（全模块离线）是**另一个状态**，语义不变，不参与这条 flag。
    """
    ctx = real_ctx(name)
    overview = build_view("mobility", ctx)["overview"]
    states = overview["propulsion_states"]
    flag = {entry["id"]: entry for entry in D.evaluate(ctx)}["mwd_sig_increase"]
    evidence = flag["evidence"]

    assert evidence["signature_radius_m"] == states["base"]["sig_m"]
    assert evidence["signature_radius_off_m"] == states["propulsion_off"]["sig_m"]

    # 文案里的数字就是这两个（同一个 units.fmt，不另写格式化）
    on = states["base"]["sig_m"]
    off = states["propulsion_off"]["sig_m"]
    assert f"{on}" in flag["message"] and f"{off}" in flag["message"]
    assert flag["message"] == (
        f"推进模块 {evidence['propulsion_state']} 时信号半径 {on} m，"
        f"关闭时为 {off} m（{evidence['sig_ratio']} 倍）"
    )


def test_f67_two_off_tiers_are_two_different_states(real_ctx) -> None:
    """`off`（全模块离线）与 `propulsion_off`（只关推进）**必须不同**，且都如实给。

    f67 实测：`off.sig_m = 60.0`（船体裸值）、`propulsion_off.sig_m = 65.72` ——
    差的 5.72 m 是 `Small Shield Extender II` 这类模块的信号半径罚值，它不属于推进模块。
    历史上 flag 写 65.72、`off` 写 60.0，读者无从判断该信哪个；现在两个状态各有字段。
    """
    overview = build_view("mobility", real_ctx(F67_SPOTCHECK))["overview"]
    states = overview["propulsion_states"]

    assert states["off"]["sig_m"] == 60.0, "全模块离线 = 船体裸值"
    assert states["propulsion_off"]["sig_m"] == 65.72, "只关推进：其余模块的罚值仍在"
    assert states["propulsion_off"]["sig_m"] != states["off"]["sig_m"]

    # 速度同理：关推进 ≠ 全离线（扫描发射器等模块不影响速度，扰频器会影响质量/对齐）
    assert states["propulsion_off"]["speed_mps"] < states["off"]["speed_mps"]
    assert states["propulsion_off"]["align_time_s"] != states["off"]["align_time_s"]


def test_propulsion_off_tier_only_turns_propulsion_off(engine: Engine, index, real_ctx) -> None:
    """独立复算：`propulsion_off` 就是「只把推进模块置 offline」的那一次计算。

    测试侧自己挑模块、自己改 state、自己算（不复用 `shared.with_propulsion_off()`），
    生产实现改成「摘掉模块」「关全部模块」都会让它变红。
    """
    overview = build_view("mobility", real_ctx(F67_SPOTCHECK))["overview"]
    states = overview["propulsion_states"]

    fit = copy.deepcopy(_load_real_fit(engine, index, F67_SPOTCHECK))
    positions = [
        position
        for position, item in enumerate(fit["items"])
        if ((index.get_type(item.get("type_id")) or {}).get("group_name")) == PROPULSION_GROUP
    ]
    assert len(positions) == 2, "前提：f67 装了两件推进模块"
    for position in positions:
        fit["items"][position]["state"] = "offline"

    recomputed = ship_attributes(engine.calculate(fit))
    for field, attribute_id in (
        ("speed_mps", A.ATTR_MAX_VELOCITY),
        ("sig_m", A.ATTR_SIGNATURE_RADIUS),
        ("align_time_s", A.ATTR_ALIGN_TIME),
    ):
        assert states["propulsion_off"][field] == round(recomputed[attribute_id], 3), field


def test_propulsion_off_equals_base_when_fit_has_no_propulsion(real_ctx) -> None:
    """没有推进模块 → `propulsion_off == base`（没东西可关，这是**真值**不是未知）。"""
    overview = build_view("mobility", real_ctx("f60-no-propulsion.eft"))["overview"]
    states = overview["propulsion_states"]

    assert _propulsion_items(real_ctx("f60-no-propulsion.eft")) == []
    assert states["propulsion_off"] == states["base"]


def test_propulsion_off_is_none_without_index(engine: Engine) -> None:
    """没有索引 → 判定不了哪些是推进模块 → 三个字段 `None`（不用 base 冒充，红线 N4）。"""
    fit = engine.load_eft(
        "[Bifrost, 无索引]\n"
        "1MN Y-S8 Compact Afterburner\n"
        "5MN Y-T8 Compact Microwarpdrive\n"
    )
    ctx = ViewContext(
        engine=engine,
        index=None,
        fit=fit,
        assumptions=default_assumptions(fit, engine),
        meta=engine.meta(),
    )
    overview = build_view("mobility", ctx)["overview"]

    assert overview["propulsion_states"]["propulsion_off"] == {
        "speed_mps": None,
        "sig_m": None,
        "align_time_s": None,
    }, "查不到 group 名就该说不知道，而不是把当前状态抄一遍"
    assert overview["propulsion_states"]["base"]["speed_mps"] is not None, "其余档不受影响"

