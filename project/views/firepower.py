"""视角一 · 火力 `firepower`（字段规格 §1）。

本模块只产出**事实**（数值 + 口径），不做判断：诊断（可疑点）由渲染层统一调一次
`diagnostics.evaluate(ctx)`，所以 `flags` 恒为空列表——六个视角各算一遍会把冷启动
12.6 ms 变成 6 倍。

口径要点（每条都实测过，要推翻请重跑脚本）：

1. **单位**：`ctx.attrs()` / `ctx.items()` 给的值已经是物理单位（秒 / 米），本模块
   不做任何缩放。唯一例外是导弹 `explosionDelay`（281）：它单位是**毫秒**，但
   **不在** `SCALE_BY_ATTR`（8 项）里，故用 `units.ms_to_s()` 显式换算——这正是
   `project/units.py` 对「非缩放表数值」提供的合法入口（红线 N5 只禁止裸乘除 1000，
   不禁止这个函数）。
2. **dps / dph**：`dps.base` = `-12`、`dps.with_reload` = `-13`、`dph` = `-11`。
   `volley`（`-21`）只在**装备级**，舰船级没有；`-11 = Σ 逐装备 -21`
   （实测 5 × 168.51208543 = 842.56042716，与 `-11` 完全一致，即不变式 I2）。
3. **伤害构成按伤害量加权**（不是按弹种数量）：形状取弹药四系（114/116/117/118），
   量级取装备级 `volley`（`-21`；缺失时才退回「弹药 × 武器 `damageMultiplier`（64）」）。
   实测 Void S = 8.9 动能 + 8.9 热能（50/50），**不是**单系；弹药自身也带 64
   （Void S = 1.08），但引擎的 `-21` 证明它**不**参与：`(8.9 + 8.9) × 9.4669715 =
   168.51209` 正好等于装备级 volley。量级为什么优先用 `-21`：导弹的加成在
   `missileDamageMultiplier`（212/213）上、发射器没有 64，混装时用 64 会把导弹
   低估近一个数量级（详见 `_damage_split` 的 docstring）。
4. **射程**：`54 maxRange` 与 `158 falloff` **已经**含弹药 `weaponRangeMultiplier`
   （120）的修正，**不得**再乘一次。实测同一门 Light Neutron Blaster II 换弹药：
   Void S（120 = 0.75）→ 1350、Null S（1.4）→ 2520、Iron（1.6）→ 2880，基准 1800。
   导弹没有 54：射程由弹药 `maxVelocity`（37）× `explosionDelay`（281，毫秒）求出
   （实测 Scourge Light Missile：5250 m/s × 5 s = 26250 m）。
5. **application 只给参数、不给命中结论**（应用层命中率未建模）：`tracking_rad_s` /
   `optimal_signature_radius_m` / `explosion_radius_m` / `explosion_velocity_mps`。
   多组武器时取**最不利**值（跟踪最小、签名分辨率最大、爆炸半径最大、爆炸速度最小），
   这里只有参数，任何「打得中/打不中」的结论都不在本层。
6. **`ammo_endurance_s` 是单弹匣时长**，不是货舱总量：
   弹匣发数 = 模块 `capacity`（38）÷（弹药 `volume`（161）× `chargeRate`（56）），
   时长 = 发数 × 周期（51）。实测中子炮 0.2 ÷ 0.0025 = 80 发 × 2.304636 s = 184.371 s。
   多组武器取**最短者**（最先打空的那一组）。
7. **战斗机 / 无人机**：`-58`（fighterDps，舰船级 = Σ 架数 × 逐机 `-12`）与
   `-14`（droneDps）已经并入 `-12`，这里仍按字段单独给，避免下游把「纯无人机配装」
   误读成「没有火力」；缺值给 `None`（不补 0）。注意 `damage_split` 的来源是**弹药**
   四系伤害：无人机自带 `114/116/118` 这类四系值（实测 Hobgoblin II 全热能），能算；
   战斗机的中队条目**没有**四系伤害（伤害在能力 DPS `-55/-56` 里），所以战斗机配装的
   `damage_split` 四值全是 `None`——那是「无法推断」，不是「没有伤害」。
8. **`family` 只认规格列的 7 类**；表外武器（如 Precursor 分裂炮）`family=None` 但
   仍计入伤害与射程——宁可少一个标签，也不要丢掉一门炮。

`FIELD_SOURCES` 的映射约定：能指到**唯一来源属性**的给属性 ID（归一化/聚合的量指到
它的定义性属性，例如「四系伤害占比」→ 对应的弹药伤害属性）；由多个属性共同决定的
合成值给 `None`（渲染层据此标 `assumed`，而不是硬塞一个看起来像的来源）。

数组字段一律用 `[]` **元素约定**（与 `resources.upgrade_space[].fits` 同一套口径）。
`weapons` 的四个叶子按元素展开为 `weapons[].family` / `weapons[].name` /
`weapons[].count` / `weapons[].ammo_name`，四条都登记 `None`：整张武器清单是**合成值**
（由 `ctx.items()` 与 SDE 索引拼出，指不到单一来源属性），渲染层据 `None` 判 `assumed`，
不冒充实测。
（为什么是元素约定：上一轮登记容器路径 `weapons: None`，靠 `render._tag()` 的容器回退
把四个叶子一起覆盖——那是本视角独有的**第二套写法**，六视角对外必须一套口径。
T-16 起 `tests/test_views_a.py` 的 walker 已支持 `[]` 段，于是换回元素约定，provenance
的键空间与其它五个视角完全对齐。**walker 需支持 `[]` 段**：改动 `_resolve()` /
`_numeric_leaf_paths()` 时别退回「列表整块算一个叶子」，否则容器写法会重新被逼回来。）
"""

from __future__ import annotations

import math

from evefit.config import attributes as _a
from evefit.project import units
from evefit.project.views import register
from evefit.project.views.base import (
    BASE,
    OVERLOAD,
    SLOT_CARGO,
    ViewContext,
    item_attributes,
)

VIEW_NAME = "firepower"

ATTR_EXPLOSION_DELAY: int = 281
"""`explosionDelay` —— 导弹最大飞行时间，**存储毫秒**。

`config/attributes.py` 本轮只读且尚未登记该 ID（PROGRESS §3 第 28 条只记了
「281 = explosionDelay」这一结论），故常量就近定义在这里并注明来源：
ESI 名 `explosionDelay`、SDE 单位 `Milliseconds`、实测 Scourge Light Missile = 5000。
等该文件可写时必须迁进 `config/attributes.py`（红线 N12 的归属要求），处置方式与
`project/provenance.py` 里的 `ATTR_MAX_TARGETS_UNVERIFIED` 相同。
"""

ATTR_CHARGE_RATE: int = 56
"""`chargeRate` —— 每个激活周期消耗几发弹药。同样是本轮未登记的正 ID（实测炮 /
发射器恒为 1），归属处理见 `ATTR_EXPLOSION_DELAY`。"""

CHARGE_RATE_DEFAULT: float = 1.0
"""`chargeRate` 缺失时的回退值：实测所有炮与发射器都是 1 发/周期。"""

ENDURANCE_ROUND_TOLERANCE: float = 1e-9
"""弹匣发数取整的容差。`0.2 / 0.0025` 这类浮点除法可能算出 79.99999999，
不加容差会把 80 发算成 79 发（红线的精神：不用浮点噪声改事实）。"""

DAMAGE_TYPES: tuple[tuple[str, int], ...] = (
    ("em", _a.ATTR_EM_DAMAGE),
    ("explosive", _a.ATTR_EXPLOSIVE_DAMAGE),
    ("kinetic", _a.ATTR_KINETIC_DAMAGE),
    ("thermal", _a.ATTR_THERMAL_DAMAGE),
)
"""四系伤害 → 弹药侧属性 ID（读弹药，不读武器）。"""

FAMILY_BY_GROUP: dict[str, str] = {
    "Hybrid Weapon": "hybrid",
    "Energy Weapon": "laser",
    "Projectile Weapon": "projectile",
    "Smart Bomb": "smartbomb",
}
"""SDE group 名 → 规格 §1 的家族。名字是索引库里的实测值（`groups.name`）。

刻意只放**精确**的模块 group：`Rig Energy Weapon` / `Energy Weapon Blueprint` 之类
名字相近的 group 不属于武器，不能被前缀匹配误伤。
"""

MISSILE_GROUP_PREFIX: str = "Missile Launcher"
"""导弹发射器的 group 名前缀（实测 `Missile Launcher` / `Missile Launcher Light` /
`Missile Launcher Rapid Heavy` / `Missile Launcher XL Cruise` 等 14 个 group 都以此开头）。"""

DRONE_SLOT_TYPE: str = "drone_bay"
"""无人机槽位类别（软件设计 §7.3 的合法 slot.type）。"""

FIGHTER_SLOT_TYPES: frozenset[str] = frozenset({"fighter_tube", "fighter_bay"})
"""战斗机槽位类别（发射管 / 机库）。"""

FIELD_SOURCES: dict[str, int | None] = {
    "weapons[].family": None,
    "weapons[].name": None,
    "weapons[].count": None,
    "weapons[].ammo_name": None,
    "dps.base": _a.ATTR_DAMAGE_DPS,
    "dps.with_reload": _a.ATTR_DAMAGE_DPS_WITH_RELOAD,
    "dps.drone": _a.ATTR_DRONE_DPS,
    "dps.fighter": _a.ATTR_FIGHTER_DPS,
    "dph": _a.ATTR_DAMAGE_ALPHA,
    "damage_split.em": _a.ATTR_EM_DAMAGE,
    "damage_split.explosive": _a.ATTR_EXPLOSIVE_DAMAGE,
    "damage_split.kinetic": _a.ATTR_KINETIC_DAMAGE,
    "damage_split.thermal": _a.ATTR_THERMAL_DAMAGE,
    "range.optimal_m": _a.ATTR_MAX_RANGE,
    "range.optimal_plus_falloff_m": _a.ATTR_FALLOFF,
    "range.optimal_plus_2falloff_m": _a.ATTR_FALLOFF,
    "range.max_range_m": None,
    "application.tracking_rad_s": _a.ATTR_TRACKING_SPEED,
    "application.optimal_signature_radius_m": _a.ATTR_OPTIMAL_SIG_RADIUS,
    "application.explosion_radius_m": _a.ATTR_AOE_CLOUD_SIZE,
    "application.explosion_velocity_mps": _a.ATTR_AOE_VELOCITY,
    "ammo_endurance_s": None,
    "overload.off.dps": _a.ATTR_DAMAGE_DPS,
    "overload.off.range_m": _a.ATTR_MAX_RANGE,
    "overload.on.dps": _a.ATTR_DAMAGE_DPS,
    "overload.on.range_m": _a.ATTR_MAX_RANGE,
    "overload.delta.dps": None,
    "overload.delta.range_m": None,
    "hardpoints_free.turret": _a.ATTR_TURRET_SLOTS_LEFT,
    "hardpoints_free.launcher": _a.ATTR_LAUNCHER_SLOTS_LEFT,
}
"""overview 点路径 → 来源属性 ID（渲染层据此打 provenance）。

`weapons[].*` 四条是**合成值**：清单由已装模块 + SDE 索引聚合而来（`family` 来自 group
名、`ammo_name` 来自索引库），指不到单一来源属性，故 `None`（渲染层判 `assumed`）。
`range.max_range_m` / `ammo_endurance_s` / `overload.delta.*` 同样是**合成值**
（飞行时间 × 速度、弹匣 × 周期、两次重算之差），指不到单一属性，故 `None`。
"""


# =============================================================================
# 小工具
# =============================================================================


def _value(raw: float | None) -> float | None:
    """物理值 → 输出值（统一 3 位小数）；缺值保持 `None`（不补 0，红线 N4）。"""
    return None if raw is None else units.fmt(raw)


def _diff(on: float | None, off: float | None) -> float | None:
    """`on - off`；任一侧缺失就返回 `None`（不拿 0 冒充）。"""
    if on is None or off is None:
        return None
    return units.fmt(on - off)


def _type_name(ctx: ViewContext, type_id: int | None) -> str | None:
    """`type_id` → 名字；没有索引时返回 `None`（不猜）。"""
    if ctx.index is None or not isinstance(type_id, int):
        return None
    haystack = ctx.index
    row = haystack.get_type(type_id)  # type: ignore[union-attr]
    return (row or {}).get("name")


def _classify(item: dict, source: dict[int, float]) -> tuple[bool, str | None]:
    """判断一个已装物品是不是武器，并给出 family。

    - 无人机 / 战斗机按**槽位类别**判（不靠名字：无人机群里也有非战斗型号）；
    - 炮 / 发射器 / 智能炸弹按 **SDE group 名**判（需要索引库）；
    - 没有索引（`ctx.index is None`）时退化到**属性特征**：弹药带爆炸半径（654）与
      爆炸速度（653）→ 导弹；同时有最大射程（54）与周期（51）→ 炮（但分不出
      laser/hybrid/projectile，`family` 保持 `None`）；
    - 表外武器（如 `Precursor Weapon`）同样走最后这条，`family=None`，不硬塞规格外的
      标签，但也不把炮丢掉。

    这套判定把「扫描发射器」正确地排除在外：它有周期（51）与弹药，但没有射程（54）、
    弹药也没有爆炸半径，且不在家族的 group 表里。
    """
    slot_type = item.get("slot_type")
    if slot_type == DRONE_SLOT_TYPE:
        return True, "drone"
    if slot_type in FIGHTER_SLOT_TYPES:
        return True, "fighter"

    group = item.get("group_name") or ""
    if group.startswith(MISSILE_GROUP_PREFIX):
        return True, "missile"
    family = FAMILY_BY_GROUP.get(group)
    if family is not None:
        return True, family

    attrs = item.get("attributes") or {}
    if (
        source.get(_a.ATTR_AOE_CLOUD_SIZE) is not None
        and source.get(_a.ATTR_AOE_VELOCITY) is not None
    ):
        return True, "missile"
    if attrs.get(_a.ATTR_MAX_RANGE) is not None and attrs.get(_a.ATTR_CYCLE_TIME) is not None:
        return True, None
    return False, None


def _damage_source(item: dict, raw_item: dict) -> dict[int, float]:
    """伤害参数的来源：**优先弹药**（物理属性表），没有弹药则退回模块自身。

    炮/发射器的四系伤害在弹药上；智能炸弹没有弹药，伤害直接写在模块上。
    """
    charge = raw_item.get("charge")
    if isinstance(charge, dict) and charge.get("attributes"):
        return item_attributes(charge)
    return item.get("attributes") or {}


def _missile_range_m(attrs: dict[int, float], source: dict[int, float]) -> float | None:
    """导弹最大射程 = 弹药最大速度（37）× 最大飞行时间（281，毫秒→秒）。

    只对**没有** `maxRange`（54）的武器计算：炮的 54 已经是最终最佳射程，不能再趟这条
    路径；扫描探针的弹药也带 37/281，但它没有 54 也不在武器家族里，早在 `_classify`
    就被排除了。
    """
    if attrs.get(_a.ATTR_MAX_RANGE) is not None:
        return None
    velocity = source.get(_a.ATTR_MAX_VELOCITY)
    flight_ms = source.get(ATTR_EXPLOSION_DELAY)
    if velocity is None or flight_ms is None:
        return None
    return velocity * units.ms_to_s(flight_ms)


def _endurance_s(attrs: dict[int, float], source: dict[int, float]) -> float | None:
    """单弹匣持续射击时长 = 弹匣发数 × 周期。

    弹匣发数 = `capacity`（38）÷（弹药 `volume`（161）× `chargeRate`（56））。
    说明：38 在船体上叫「货舱容积」，在武器模块上就是「弹匣容积」——同一个属性 ID，
    语义随宿主类型走。
    """
    capacity = attrs.get(_a.ATTR_CARGO_CAPACITY)
    cycle = attrs.get(_a.ATTR_CYCLE_TIME)
    volume = source.get(_a.ATTR_VOLUME)
    if capacity is None or cycle is None or volume is None or volume <= 0.0:
        return None
    rate = attrs.get(ATTR_CHARGE_RATE)
    if rate is None:
        rate = CHARGE_RATE_DEFAULT
    if rate <= 0.0:
        return None
    rounds = math.floor(capacity / (volume * rate) + ENDURANCE_ROUND_TOLERANCE)
    if rounds <= 0:
        return None
    return rounds * cycle


def _scan(ctx: ViewContext, variant: str = BASE) -> list[dict]:
    """扫出**每一件**武器（一件一条记录），带上本视角需要的全部参数。"""
    merged = ctx.items(variant)
    raw_items = ctx.variant(variant).get("items") or []
    records: list[dict] = []

    for position, item in enumerate(merged):
        if item.get("slot_type") == SLOT_CARGO:
            continue
        raw_item = raw_items[position] if position < len(raw_items) else {}
        attrs = item.get("attributes") or {}
        source = _damage_source(item, raw_item)

        is_weapon, family = _classify(item, source)
        if not is_weapon:
            continue

        # 导弹发射器没有 damageMultiplier（伤害全在弹药上）→ 乘子取 1。
        multiplier = attrs.get(_a.ATTR_DAMAGE_MULTIPLIER)
        if multiplier is None:
            multiplier = 1.0

        records.append(
            {
                "family": family,
                "type_id": item.get("type_id"),
                "name": item.get("name"),
                "charge_type_id": item.get("charge_type_id"),
                "ammo_name": _type_name(ctx, item.get("charge_type_id")),
                "count": max(int(item.get("quantity") or 1), 1),
                "volley": attrs.get(_a.ATTR_DAMAGE_VOLLEY),
                "damage": {
                    name: (source.get(attribute_id) or 0.0) * multiplier
                    for name, attribute_id in DAMAGE_TYPES
                },
                "tracking_rad_s": attrs.get(_a.ATTR_TRACKING_SPEED),
                "optimal_sig_m": attrs.get(_a.ATTR_OPTIMAL_SIG_RADIUS),
                "optimal_m": attrs.get(_a.ATTR_MAX_RANGE),
                "falloff_m": attrs.get(_a.ATTR_FALLOFF),
                "max_range_m": _missile_range_m(attrs, source),
                "explosion_radius_m": source.get(_a.ATTR_AOE_CLOUD_SIZE),
                "explosion_velocity_mps": source.get(_a.ATTR_AOE_VELOCITY),
                "endurance_s": _endurance_s(attrs, source),
            }
        )
    return records


def _weapon_list(records: list[dict]) -> list[dict]:
    """按（type_id, 弹药）合并成 `{family, name, count, ammo_name}` 清单。

    顺序 = 首次出现顺序（与装配顺序一致，天然确定，不需要额外排序键）。
    """
    groups: dict[tuple[int | None, int | None], dict] = {}
    for record in records:
        key = (record["type_id"], record["charge_type_id"])
        group = groups.get(key)
        if group is None:
            groups[key] = {
                "family": record["family"],
                "name": record["name"],
                "count": record["count"],
                "ammo_name": record["ammo_name"],
            }
            continue
        group["count"] += record["count"]
    return list(groups.values())


def _damage_split(records: list[dict]) -> dict[str, float | None]:
    """四系伤害**占比**（按伤害量加权，四项之和 = 1）。

    两件事分开算：
    - **形状**（四系相对比例）来自弹药四系伤害 114/116/117/118；
    - **量级**（这一组武器占多少）优先用装备级 `volley`（`-21`，引擎已含全部乘子），
      缺失时才退化为「弹药四系 × `damageMultiplier`（64）」。

    为什么量级不直接用「弹药 × 64」：导弹的伤害加成在 `missileDamageMultiplier`
    （212/213）上、发射器本身没有 64（实测 Launcher 的 `-21 = 136.95`，而弹药只有
    124.5，差的 1.1 就是弹道控制）。混合炮 + 导弹配装若把炮乘 9.47、导弹乘 1，
    导弹的占比会被低估近一个数量级。炮这两种口径完全等价（实测
    `(8.9+8.9) × 9.4669715 = 168.51209` 正好等于 `-21`），所以换成 `-21` 不改变
    纯炮配装的结果，只把混合配装从错的改成对的。

    四系全为 0（无弹药 / 战斗机这类四系伤害不在条目上的武器）时四值给 `None`——
    分母是 0 的时候没有「占比」可言，给 0 会读成「这一系没伤害」。
    """
    totals = {name: 0.0 for name, _attribute_id in DAMAGE_TYPES}
    for record in records:
        damage_total = sum(record["damage"].values())
        if damage_total <= 0.0:
            continue  # 不知道形状就不猜（宁可不计入，也不按件数均分）
        magnitude = record["volley"]
        if magnitude is None or magnitude <= 0.0:
            magnitude = damage_total
        share = magnitude * record["count"]
        for name, _attribute_id in DAMAGE_TYPES:
            totals[name] += share * record["damage"][name] / damage_total

    grand_total = sum(totals.values())
    if grand_total <= 0.0:
        return {name: None for name, _attribute_id in DAMAGE_TYPES}
    return {name: units.fmt(totals[name] / grand_total) for name, _attribute_id in DAMAGE_TYPES}


def _range_block(records: list[dict]) -> dict[str, float | None]:
    """三档射程（多组武器时各档取**最远**值，即配装能覆盖的最远档）。

    `optimal_plus_falloff_m` 与 `optimal_plus_2falloff_m` 是在 `optimal_m` 上做加法，
    不是重新取来源属性。
    """
    optimals = [record["optimal_m"] for record in records if record["optimal_m"] is not None]
    falloffs = [record["falloff_m"] for record in records if record["falloff_m"] is not None]
    missiles = [record["max_range_m"] for record in records if record["max_range_m"] is not None]

    optimal = max(optimals) if optimals else None
    falloff = max(falloffs) if falloffs else None

    return {
        "optimal_m": _value(optimal),
        "optimal_plus_falloff_m": (
            None if optimal is None or falloff is None else units.fmt(optimal + falloff)
        ),
        "optimal_plus_2falloff_m": (
            None if optimal is None or falloff is None else units.fmt(optimal + 2.0 * falloff)
        ),
        "max_range_m": _value(max(missiles) if missiles else None),
    }


def _application_block(records: list[dict]) -> dict[str, float | None]:
    """应用层参数（**未建模**：只给参数，不给命中结论）。

    多组武器时取**最不利**值：跟踪速度取最小、签名分辨率取最大、爆炸半径取最大、
    爆炸速度取最小。这里刻意不做加权，也不判断「够不够用」。
    """
    trackings = [
        record["tracking_rad_s"] for record in records if record["tracking_rad_s"] is not None
    ]
    sig_radii = [
        record["optimal_sig_m"] for record in records if record["optimal_sig_m"] is not None
    ]
    radii = [
        record["explosion_radius_m"]
        for record in records
        if record["explosion_radius_m"] is not None
    ]
    velocities = [
        record["explosion_velocity_mps"]
        for record in records
        if record["explosion_velocity_mps"] is not None
    ]

    return {
        "tracking_rad_s": _value(min(trackings) if trackings else None),
        "optimal_signature_radius_m": _value(max(sig_radii) if sig_radii else None),
        "explosion_radius_m": _value(max(radii) if radii else None),
        "explosion_velocity_mps": _value(min(velocities) if velocities else None),
    }


def _ammo_endurance_s(records: list[dict]) -> float | None:
    """""单弹匣」时长：多组武器取最短者（最先打空的那一组）。"""
    values = [record["endurance_s"] for record in records if record["endurance_s"] is not None]
    return _value(min(values) if values else None)


def _overload_block(
    ctx: ViewContext, base_range: dict[str, float | None]
) -> dict[str, dict[str, float | None]]:
    """过载前后的 `dps` 与 `optimal_m` 变化（`overload` 变体 = 全部模块 overload）。

    实测：中子炮 `-12` 365.594 → 420.433（+54.839），射程不变（`54` 不变，超载只加
    伤害乘子）。`base_range` 由调用方传入，避免把 base 变体算第二遍。
    """
    base_attrs = ctx.attrs(BASE)
    overload_attrs = ctx.attrs(OVERLOAD)
    overload_range = _range_block(_scan(ctx, OVERLOAD))

    off_dps = base_attrs.get(_a.ATTR_DAMAGE_DPS)
    on_dps = overload_attrs.get(_a.ATTR_DAMAGE_DPS)
    off_range = base_range["optimal_m"]
    on_range = overload_range["optimal_m"]

    return {
        "off": {"dps": _value(off_dps), "range_m": off_range},
        "on": {"dps": _value(on_dps), "range_m": on_range},
        "delta": {"dps": _diff(on_dps, off_dps), "range_m": _diff(on_range, off_range)},
    }


def _hardpoints_free(attrs: dict[int, float]) -> dict[str, float | None]:
    """空闲挂点（102 turretSlotsLeft / 101 launcherSlotsLeft），给绝对个数。"""
    return {
        "turret": _value(attrs.get(_a.ATTR_TURRET_SLOTS_LEFT)),
        "launcher": _value(attrs.get(_a.ATTR_LAUNCHER_SLOTS_LEFT)),
    }


# =============================================================================
# 视角入口
# =============================================================================


@register(VIEW_NAME)
def build(ctx: ViewContext) -> dict:
    """构建火力视角 overview。

    Args:
        ctx: 视角上下文（只读）。

    Returns:
        `{"view": "firepower", "overview": {...}, "flags": []}`——
        `flags` **恒为空**：可疑点由渲染层统一算一次（见模块 docstring）。
    """
    attrs = ctx.attrs(BASE)
    records = _scan(ctx, BASE)
    range_block = _range_block(records)

    overview = {
        "weapons": _weapon_list(records),
        "dps": {
            "base": _value(attrs.get(_a.ATTR_DAMAGE_DPS)),
            "with_reload": _value(attrs.get(_a.ATTR_DAMAGE_DPS_WITH_RELOAD)),
            "drone": _value(attrs.get(_a.ATTR_DRONE_DPS)),
            "fighter": _value(attrs.get(_a.ATTR_FIGHTER_DPS)),
        },
        "dph": _value(attrs.get(_a.ATTR_DAMAGE_ALPHA)),
        "damage_split": _damage_split(records),
        "range": range_block,
        "application": _application_block(records),
        "ammo_endurance_s": _ammo_endurance_s(records),
        "overload": _overload_block(ctx, range_block),
        "hardpoints_free": _hardpoints_free(attrs),
    }
    return {"view": VIEW_NAME, "overview": overview, "flags": []}
