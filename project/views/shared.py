"""两视角共用的派生计算 —— **口径只写一份**（T-20 起）。

为什么必须抽出来（T-20 / D5 实测）：

`firepower.ammo_endurance_s` 与 `stability.consumables.ammo_endurance_s` 表达的是
**同一个事实**（一门武器打完一个弹匣要多久），历史上两个视角各写了一份实现，
判定条件还不一样：

- 火力侧：`51 cycleTime` + `chargeRate` + 弹药体积（来自装备级计算节点的 `charge.attributes`），
  于是 f4 = **172.663 s**、f5 = **250.323 s**；
- 不稳定侧：要求模块**同时**有 `51 cycleTime` 与 `64 damageMultiplier` 才算武器 ——
  导弹发射器在装备级根本没有 `64`（硬结论 §3 第 71 条：导弹伤害加成在 212/213 上），
  于是同一份配装的弹药续航成了 `None`。

同一个概念两个视角不一致，是**口径缺陷**（不是数据缺失）。现在这个文件是唯一实现，
两个视角都调用它，并且都从**同一份** `weapon_records()` 记录里取值——所以两边的
`ammo_endurance_s` 是同一个浮点数过同一个 `units.fmt()`，逐位相同由构造保证
（`tests/test_views_a.py` / `test_views_b.py` 另有跨视角断言兜底）。

武器判定（`classify_weapon`）也一并搬到这里：不稳定视角要数「哪些已装模块算武器」，
如果它另写一套判定，就会重演上面那种「两个视角各有一套口径」的缺陷。

**载机槽位的两种语义（T-29）**：`fighter_tube`（159–163）= 已发射、参战；
`fighter_bay`（158）= 机库库存、不参战。混同的后果是把机库里的载机列进 `weapons[]`，
下游据此以为有战斗机在输出，而 `dps.fighter` 恒为 0 —— 那是判定误判，不是配装事实。
机库侧的事实由 `fighter_bay_summary()` 单独表达。

`depletes_in_s()` 收口 D6：引擎 `-7 capDepletesIn` 用 **`-1.0` 这个哨兵**表示
「电容永不耗尽」（与 `-72 capStablePercentage > 0` 同义）。它不是物理时长，
当成秒数输出就是伪事实，所以这里统一归一到 `None`。

**推进模块的「关闭」态（T-35）**：`mobility.propulsion_states.propulsion_off` 与诊断规则
`mwd_sig_increase` 的「关闭时」说的是**同一件事**（只把推进模块关掉、其余模块保持当前
状态），历史上两边各算一次，于是同一视角里出现了 `65.72 m` 与 `off(全模块离线) 60.0 m` 两个
互不相等的「关闭时信号半径」。现在变体的构造（`with_propulsion_off()`）与判定（group 名
`Propulsion Module`）都只写在这里，两个调用方拿到的是同一个浮点数。

**T-43 新增三件事，都在这一个文件里收口**：

1. **「单推进」态**（`with_single_propulsion()` / `propulsion_single_attrs()`）：只启用一个
   推进模块、其余置 offline，其余模块保持当前状态。它回答的是「玩家真实会用的状态」，
   与 `propulsion_states.base`（引擎按导入规则把能开的全开）不互相冒充；
2. **状态互斥规则表**（`activation_groups()`）：读 `config/activation_groups.json`，
   补引擎 `max_group` 覆盖不到的**跨组**互斥（MWD+MJD 这类）。文件缺失 → 空规则、不崩；
3. **拦截弹发射器不是对敌武器**（`NON_DAMAGE_WEAPON_GROUPS` / `classify_weapon` /
   `non_damage_weapon_dps()`）：判定只此一份，`weapons[]` 与船级 `dps` 的扣减同源。

数值纪律（与其他视角一致）：入参已是**物理单位**（秒 / m³），本模块不再缩放；
缺值给 `None` 不补 0；浮点一律过 `units.fmt`。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from noobbaozi.config import attributes as A
from noobbaozi.config import paths as _paths
from noobbaozi.config.attributes import ATTR_CHARGE_RATE, ATTR_EXPLOSION_DELAY
from noobbaozi.project import units
from noobbaozi.project.views.base import (
    BASE,
    SLOT_CARGO,
    ViewContext,
    item_attributes,
    ship_attributes,
)

# =============================================================================
# 武器判定用常量（SDE 实测值）
# =============================================================================
# `ATTR_EXPLOSION_DELAY`（281）已登记进 `config/attributes.py`（T-23 索引库核实
# `unit_id = 101` = Milliseconds），故这里**从常量表导入**，不再就近定义（红线 N12）。
# 注意：它同时进了 `SCALE_BY_ATTR`，所以 `item_attributes()` 返回的 281 **已经是秒**，
# 本模块绝不能再 `ms_to_s()`（那就是双换算，T-25 修掉的正是这处）。

DAMAGE_TYPES: tuple[tuple[str, int], ...] = (
    ("em", A.ATTR_EM_DAMAGE),
    ("explosive", A.ATTR_EXPLOSIVE_DAMAGE),
    ("kinetic", A.ATTR_KINETIC_DAMAGE),
    ("thermal", A.ATTR_THERMAL_DAMAGE),
)
"""四系伤害 → 弹药侧属性 ID（读弹药，不读武器）。"""

FAMILY_BY_GROUP: dict[str, str] = {
    "Hybrid Weapon": "hybrid",
    "Energy Weapon": "laser",
    "Projectile Weapon": "projectile",
    "Smart Bomb": "smartbomb",
}
"""SDE group 名 → 字段规格 §1 的家族。名字是索引库里的实测值（`groups.name`）。

刻意只放**精确**的模块 group：`Rig Energy Weapon` / `Energy Weapon Blueprint` 之类
名字相近的 group 不属于武器，不能被前缀匹配误伤。
"""

MISSILE_GROUP_PREFIX: str = "Missile Launcher"
"""导弹发射器的 group 名前缀（实测 `Missile Launcher` / `Missile Launcher Light` /
`Missile Launcher Rapid Heavy` / `Missile Launcher XL Cruise` 等 14 个 group 都以此开头）。"""

NON_DAMAGE_WEAPON_GROUPS: frozenset[str] = frozenset({"Missile Launcher Defender"})
"""**不产生对敌伤害**的「武器」group 名 —— 目前只有拦截弹发射器（T-43）。

业务事实（不是「引擎给不给键」）：
- `Defender Launcher I`（type **44102**，SDE group **512** = `Missile Launcher Defender`）装的
  弹药是 `Defender Missile I`（type **32782**，group 88 `Defender Missiles`）——**拦截弹**，
  用来打掉来袭导弹，不打船；
- 引擎照给 `-12 = 160`、`-21 = 800`（f11 实测），因为这两个派生属性是「模块级伤害 DPS /
  齐射量」，**不区分对敌与否**；pyfa/eos 同一件给 `0`（T-42 报告 §4.2 表 A 唯一例外）。
- 所以判据用**业务事实**（这批发射器的弹药是拦截弹 → 不是对敌武器），并且必须是
  **精确 group 名**而不是 `Missile Launcher` 前缀：前缀会把 `Missile Launcher Rapid Light`
  这类真武器一起误伤（红线 N4 的反面：不猜）。
"""

DRONE_SLOT_TYPE: str = "drone_bay"
"""无人机槽位类别（软件设计 §7.3 的合法 slot.type）。"""

FIGHTER_TUBE_SLOT_TYPE: str = "fighter_tube"
"""战斗机**发射管**槽位（ESI flag 159–163）—— 已发射、参与伤害计算。"""

FIGHTER_BAY_SLOT_TYPE: str = "fighter_bay"
"""战斗机**机库**槽位（ESI flag 158 = FighterBay）—— 只是库存，**不参战**。"""

FIGHTER_SLOT_TYPES: frozenset[str] = frozenset({FIGHTER_TUBE_SLOT_TYPE, FIGHTER_BAY_SLOT_TYPE})
"""载机槽位类别（发射管 / 机库）。

**这两个槽位的语义不同，不能一起当武器**（T-29 的根因就是这个混同）：

- `fighter_tube`（159–163）：中队已发射，逐机 `-12` 汇总成舰船级 `-58`，是**参战**的；
- `fighter_bay`（158）：载机停在机库，只计入 `-57 fighterCapacityLoad`（机库占用 m³），
  对 `-58`/`-59..-62` 与任何槽位伤害都**零贡献**。改造实验（T-28 S5）：把 Templar 从
  `fighter_bay` 移进 `fighter_tube` 后 `-58` 从 `0` 变成 `240.846`，A/B 两组唯一的差别
  就是 `slot.type` → 零值成因是「在机库」，不是引擎限制、也不是 state=offline。

所以本常量只用于**识别载机槽位**（做机库摘要、诊断），**不再**作为「算不算武器」的判据。"""

WEAPON_SLOT_TYPES: frozenset[str] = frozenset({"high", "medium", "low"})
"""**武器挂架**槽位类别（高/中/低槽）。

`dph`（首轮齐射伤）只聚合这三类槽位里的武器：`drone_bay` / `fighter_bay` /
`cargo` / `implant` / `booster` / `service` 都不算——无人机与战斗机的中队伤害走
`dps.drone` / `dps.fighter`（`-14` / `-58`），把它们算进「齐射」会让 alpha 与
舰船级 `-11` 对不上（f4 实测：drone 的 `-21 = 83.866`，而 `ship -11 = 0.0`）。
"""

CHARGE_RATE_DEFAULT: float = 1.0
"""`chargeRate`（56）缺失时的回退值：实测所有炮与发射器都是 1 发/周期。"""

ENDURANCE_ROUND_TOLERANCE: float = 1e-9
"""弹匣发数取整的容差。`0.2 / 0.0025` 这类浮点除法可能算出 79.99999999，
不加容差会把 80 发算成 79 发（红线的精神：不用浮点噪声改事实）。"""


# =============================================================================
# 小工具
# =============================================================================


def type_name(ctx: ViewContext, type_id: int | None) -> str | None:
    """`type_id` → 名字；没有索引时返回 `None`（不猜）。"""
    if ctx.index is None or not isinstance(type_id, int):
        return None
    haystack = ctx.index
    row = haystack.get_type(type_id)  # type: ignore[union-attr]
    return (row or {}).get("name")


def damage_source(item: dict, raw_item: dict) -> dict[int, float]:
    """伤害参数的来源：**优先弹药**（物理属性表），没有弹药则退回模块自身。

    炮/发射器的四系伤害在弹药上；智能炸弹没有弹药，伤害直接写在模块上。
    """
    charge = raw_item.get("charge")
    if isinstance(charge, dict) and charge.get("attributes"):
        return item_attributes(charge)
    return item.get("attributes") or {}


def classify_weapon(item: dict, source: dict[int, float]) -> tuple[bool, str | None]:
    """判断一个已装物品是不是**参战**武器，并给出 family。

    - 无人机 / 战斗机按**槽位类别**判（不靠名字：无人机群里也有非战斗型号）；
    - 炮 / 发射器 / 智能炸弹按 **SDE group 名**判（需要索引库）；
    - 没有索引（`ctx.index is None`）时退化到**属性特征**：弹药带爆炸半径（654）与
      爆炸速度（653）→ 导弹；同时有最大射程（54）与周期（51）→ 炮（但分不出
      laser/hybrid/projectile，`family` 保持 `None`）；
    - 表外武器（如 `Precursor Weapon`）同样走最后这条，`family=None`，不硬塞规格外的
      标签，但也不把炮丢掉。

    **T-29：`fighter_bay`（机库）不再是武器。** 这是修正一个把「库存」当「参战」的
    启发式误判：

    - `fighter_tube`（159–163）→ `family: "fighter"`：已发射的中队，`-58` 会真的出伤害；
    - `fighter_bay`（158）→ **`(False, None)`**：载机停在机库，不参与任何伤害/槽位计算，
      只计入 `-57 fighterCapacityLoad`（m³）。把它列进 `weapons[]` 会让下游读成「有战斗机
      在输出」，而 `dps.fighter` 恒为 0 —— 这个自相矛盾不是配装问题，是本判定的误判
      （验收不变式「`weapons[]` 有 family=fighter → `dps.fighter > 0`」因此恒假）。
      机库里的载机**不丢**：火力视角用 `fighter_bay` 字段 + `fighters_in_bay_only` flag
      如实表达「有载机但没参战」。

    这套判定把「扫描发射器」正确地排除在外：它有周期（51）与弹药，但没有射程（54）、
    弹药也没有爆炸半径，且不在家族的 group 表里。

    **T-43：拦截弹发射器（`Defender Launcher`）不是对敌武器。** 判据是业务事实 ——
    它的弹药是拦截弹（`Defender Missile`），用来打掉来袭导弹、不打船（`NON_DAMAGE_WEAPON_GROUPS`
    里的实测依据）。引擎仍会为它派生 `-12 = 160` / `-21 = 800`（f11 实测），所以只排除
    `classify_weapon` 是不够的：`firepower` 的 `dps.base` / `dps.with_reload` 读的是
    **船级** `-12` / `-13`，那里面照样含这 160 → 视角侧要按同一张表把它的贡献扣掉
    （`firepower._non_damage_dps()` 调 `non_damage_weapon_dps()`，判定只此一份）。

    判定只此一份：`firepower.weapons[]` 与两个视角的 `ammo_endurance_s` 都走它，
    否则「哪些模块算武器」一旦分叉，弹药续航会再次出现两个视角不一致（D5 的根因之一）。
    """
    slot_type = item.get("slot_type")
    if slot_type == DRONE_SLOT_TYPE:
        return True, "drone"
    if slot_type == FIGHTER_TUBE_SLOT_TYPE:
        return True, "fighter"
    if slot_type == FIGHTER_BAY_SLOT_TYPE:
        # 机库里的载机不是武器（只占 -57 机库体积，见 `FIGHTER_SLOT_TYPES` 的说明）。
        return False, None

    group = item.get("group_name") or ""
    if group in NON_DAMAGE_WEAPON_GROUPS:
        # 拦截弹发射器（T-43）：引擎照给 `-12`/`-21`，但它不产生对敌伤害 → 不是武器。
        # 必须排在导弹前缀判定**之前**：`Missile Launcher Defender` 也以该前缀开头，
        # 排在后面就会被当成导弹发射器收进 `weapons[]`（f11 实测 160 DPS 的来源）。
        return False, None
    if group.startswith(MISSILE_GROUP_PREFIX):
        return True, "missile"
    family = FAMILY_BY_GROUP.get(group)
    if family is not None:
        return True, family

    attrs = item.get("attributes") or {}
    if (
        source.get(A.ATTR_AOE_CLOUD_SIZE) is not None
        and source.get(A.ATTR_AOE_VELOCITY) is not None
    ):
        return True, "missile"
    if attrs.get(A.ATTR_MAX_RANGE) is not None and attrs.get(A.ATTR_CYCLE_TIME) is not None:
        return True, None
    return False, None


def _missile_range_m(attrs: dict[int, float], source: dict[int, float]) -> float | None:
    """导弹最大射程 = 弹药最大速度（37）× 最大飞行时间（281）。

    两个入参都来自 `item_attributes()`，即**已经是物理单位**（37 是 m/s，281 是秒：
    它已登记进 `SCALE_BY_ATTR`，存储毫秒 `5000` 在投影层就换算成 `5.0` 秒）。
    所以这里只做一次乘法，**不得**再 `ms_to_s()`——那会算出 51.45 m 而不是 51450 m
    （T-25 修掉的正是这处双换算；REDLINE N5 / PROGRESS §6 第 17、20 条）。

    只对**没有** `maxRange`（54）的武器计算：炮的 54 已经是最终最佳射程，不能再趟这条
    路径；扫描探针的弹药也带 37/281，但它没有 54 也不在武器家族里，早在 `classify_weapon`
    就被排除了。
    """
    if attrs.get(A.ATTR_MAX_RANGE) is not None:
        return None
    velocity = source.get(A.ATTR_MAX_VELOCITY)
    flight_s = source.get(ATTR_EXPLOSION_DELAY)
    if velocity is None or flight_s is None:
        return None
    return velocity * flight_s


def _endurance(attrs: dict[int, float], source: dict[int, float]) -> tuple[int, float, float] | None:
    """单弹匣 → `(发数, 周期秒, 续航秒)`；任一环节缺失返回 `None`（不猜）。

    弹匣发数 = `capacity`（38）÷（弹药 `volume`（161）× `chargeRate`（56））。
    说明：38 在船体上叫「货舱容积」，在武器模块上就是「弹匣容积」——同一个属性 ID，
    语义随宿主类型走。

    周期只认 `51 speed`（武器周期，投影层已换算成秒）。**不回退 `73 duration`**：
    实测武器恒有 51，而 73 是「模块生效时长」（纳米胶修甲器那类），拿它当射击周期
    会把非武器也混进弹药续航。
    """
    capacity = attrs.get(A.ATTR_CARGO_CAPACITY)
    cycle = attrs.get(A.ATTR_CYCLE_TIME)
    volume = source.get(A.ATTR_VOLUME)
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
    return rounds, float(cycle), rounds * float(cycle)


# =============================================================================
# 武器记录（扫描 + 弹药续航）—— 火力 / 不稳定两个视角共用的唯一来源
# =============================================================================


def weapon_records(ctx: ViewContext, variant: str = BASE) -> list[dict]:
    """扫出**每一件**武器（一件一条记录），带上火力与续航需要的全部参数。

    记录字段（`endurance_s` / `rounds` / `cycle_s` 三个是 D5 的公共部分）：

    - 身份：`family` / `type_id` / `name` / `slot_type` / `charge_type_id` / `ammo_name`
    - 数量与伤害：`count` / `volley`（装备级 `-21`）/ `damage`（四系，按弹药 × 64）
    - 射程与应用层：`optimal_m` / `falloff_m` / `max_range_m` / `explosion_radius_m` /
      `explosion_velocity_mps` / `tracking_rad_s` / `optimal_sig_m`
    - 续航：`rounds`（弹匣发数）/ `cycle_s`（周期秒）/ `endurance_s`（单弹匣时长）

    **机库里的载机不在本表里**（T-29）：它们由 `fighter_bay_summary()` 单独表达，
    不参与 `weapons[]`、`dph`、射程或弹药续航的任何聚合。
    """
    merged = ctx.items(variant)
    raw_items = ctx.variant(variant).get("items") or []
    records: list[dict] = []

    for position, item in enumerate(merged):
        if item.get("slot_type") == SLOT_CARGO:
            continue
        raw_item = raw_items[position] if position < len(raw_items) else {}
        attrs = item.get("attributes") or {}
        source = damage_source(item, raw_item)

        is_weapon, family = classify_weapon(item, source)
        if not is_weapon:
            continue

        # 导弹发射器没有 damageMultiplier（伤害全在弹药上）→ 乘子取 1。
        multiplier = attrs.get(A.ATTR_DAMAGE_MULTIPLIER)
        if multiplier is None:
            multiplier = 1.0

        endurance = _endurance(attrs, source)
        records.append(
            {
                "family": family,
                "type_id": item.get("type_id"),
                "name": item.get("name"),
                "slot_type": item.get("slot_type"),
                "charge_type_id": item.get("charge_type_id"),
                "ammo_name": type_name(ctx, item.get("charge_type_id")),
                "count": max(int(item.get("quantity") or 1), 1),
                "volley": attrs.get(A.ATTR_DAMAGE_VOLLEY),
                "damage": {
                    name: (source.get(attribute_id) or 0.0) * multiplier
                    for name, attribute_id in DAMAGE_TYPES
                },
                "tracking_rad_s": attrs.get(A.ATTR_TRACKING_SPEED),
                "optimal_sig_m": attrs.get(A.ATTR_OPTIMAL_SIG_RADIUS),
                "optimal_m": attrs.get(A.ATTR_MAX_RANGE),
                "falloff_m": attrs.get(A.ATTR_FALLOFF),
                "max_range_m": _missile_range_m(attrs, source),
                "explosion_radius_m": source.get(A.ATTR_AOE_CLOUD_SIZE),
                "explosion_velocity_mps": source.get(A.ATTR_AOE_VELOCITY),
                "rounds": None if endurance is None else endurance[0],
                "cycle_s": None if endurance is None else endurance[1],
                "endurance_s": None if endurance is None else endurance[2],
            }
        )
    return records


def shortest_endurance(records: list[dict]) -> dict | None:
    """多组武器里**最先打空**的那一条记录（`endurance_s` 最小）；没有则 `None`。"""
    best: dict | None = None
    for record in records:
        value = record.get("endurance_s")
        if value is None:
            continue
        if best is None or value < best["endurance_s"]:
            best = record
    return best


def ammo_endurance_s(records: list[dict]) -> float | None:
    """「单弹匣」时长（秒，3 位小数）：多组武器取**最短者**（最先打空的那一组）。

    火力视角的 `firepower.ammo_endurance_s` 与不稳定视角的
    `stability.consumables.ammo_endurance_s` 都调这一个函数（入参是同一份
    `weapon_records()`），所以两个视角的数值逐位相同。
    """
    best = shortest_endurance(records)
    return None if best is None else units.fmt(best["endurance_s"])


# =============================================================================
# 机库载机摘要（T-29：机库 = 库存，不是武器）
# =============================================================================


def fighter_bay_summary(ctx: ViewContext, variant: str = BASE) -> dict:
    """机库（`fighter_bay`）里的载机摘要：`{count, types, capacity_used_m3}`。

    **唯一实现**：火力视角的 `firepower.fighter_bay` 字段与诊断规则
    `fighters_in_bay_only` 都调它，两边对「机库里到底有几架、哪几种」不会分叉。

    取值口径：

    - `count`：机库条目的 `quantity` 之和（引擎把一条载机物品按架数记量）；
    - `types`：按**首次出现顺序**去重的 type 名（成品输出 `["Siren I", "Templar I"]`）；
    - `capacity_used_m3`：**船级 `-57 fighterCapacityLoad`**（注意 `-57` 只在船级有值，
      item 级没有——T-28 的实测修正），不是架数、也不是带宽；
    - 无载机时给空结构 `{"count": 0, "types": [], "capacity_used_m3": 0.0}`，保证 schema
      稳定（非航母样本也恒有该字段）。

    为什么单独表达：机库里的载机**完全不参与**伤害计算，把它放进 `weapons[]` 会把
    「库存」误读成「参战」。这里只陈述事实（有几架、占多少 m³），「有没有在打」由
    `dps.fighter`（`-58`）表达，二者不互相冒充。
    """
    bay_items = [
        item for item in ctx.items(variant) if item.get("slot_type") == FIGHTER_BAY_SLOT_TYPE
    ]
    count = sum(max(int(item.get("quantity") or 1), 1) for item in bay_items)

    types: list[str] = []
    for item in bay_items:
        name = item.get("name") or f"type_id {item.get('type_id')}"
        if name not in types:
            types.append(name)

    capacity = ctx.attrs(variant).get(A.ATTR_FIGHTER_BAY_USED)
    if capacity is None:
        # 有载机却没有 -57 → 机库占用未知（不拿 0 冒充）；无载机时 0.0 才是真值。
        capacity_used = 0.0 if count == 0 else None
    else:
        capacity_used = units.fmt(capacity)

    return {"count": count, "types": types, "capacity_used_m3": capacity_used}


# =============================================================================
# 电容耗尽时长（哨兵归一）
# =============================================================================

CAP_DEPLETES_SENTINEL: float = -1.0
"""引擎 `-7 capDepletesIn` 对「电容稳定、永不耗尽」给的哨兵值（实测 f4/f5）。

它不是物理时长：`-1.0` 秒没有意义，而 `stable_pct > 0` 才是「稳定」的表达。
"""


def depletes_in_s(stable_pct: float | None, seconds: float | None) -> float | None:
    """电容耗尽秒数归一（D6）：**永不耗尽 / 哨兵 → `None`**。

    三种情形（与 `tests/test_views_b.py` 的单测一一对应）：

    1. `stable_pct > 0`（电容稳定）→ `None`；引擎此时给 `-7 = -1.0`，不是时长；
    2. 引擎给了**负数**（任何负值都是哨兵，不只 `-1.0`）→ `None`；
    3. 否则照实给秒数（过 `units.fmt`）。

    `stable_pct` 为 `None`（拿不到稳定百分比）时不参与判定——此时负值仍按哨兵处理，
    正数照实输出，绝不因为「不知道是否稳定」就把一个真实秒数抹成 `None`。
    """
    if stable_pct is not None and stable_pct > 0.0:
        return None
    if seconds is None or seconds < 0.0:
        return None
    return units.fmt(seconds)


# =============================================================================
# 「推进模块关闭」态（T-35：flag 文案与 `propulsion_states.*` 必须是同一个数）
# =============================================================================

PROPULSION_GROUP: str = "Propulsion Module"
"""推进模块的 SDE group 名（实测 AB / MWD 同组，与 `diagnostics.PROPULSION_GROUP` 同一个值）。

放在 `shared.py` 是因为**判定只此一份**：机动视角的 `propulsion_states.propulsion_off`
与诊断规则 `mwd_sig_increase` 都要回答「哪些模块是推进模块」，两边各写一套 group 判据
就会重演 T-20 的「同一事实两套口径」。
"""

PROPULSION_OFF_VARIANT: str = "shared:propulsion_off"
"""「推进模块整体关闭」变体的缓存 key —— 机动视角与诊断规则共用同一次计算。"""

OPEN_STATES: frozenset[str] = frozenset({"active", "overload"})
"""算「启用」的 state 集合（overload 也是启用，只是带过载）。"""


def group_name(ctx: ViewContext, type_id: object) -> str | None:
    """type_id → SDE group 名；没有索引或查不到时 `None`（不猜）。"""
    if ctx.index is None or not isinstance(type_id, int):
        return None
    row = ctx.index.get_type(type_id)
    return (row or {}).get("group_name")


def propulsion_positions(ctx: ViewContext, variant: str = BASE) -> list[int] | None:
    """已装推进模块在 `fit["items"]` 里的**原下标**；没有索引时 `None`（判定不了）。

    下标与 `ctx.fit["items"]` 一一对应（硬结论 44），`_merge_items()` 保序合并，
    所以直接对合并视图 `enumerate` 得到的就是原下标（摘除/改状态时**不能**用别的列表）。
    """
    if ctx.index is None:
        return None
    return [
        position
        for position, item in enumerate(ctx.items(variant))
        if item.get("slot_type") != SLOT_CARGO and item.get("group_name") == PROPULSION_GROUP
    ]


def with_propulsion_off(fit: dict, positions: list[int]) -> dict:
    """把指定下标的推进模块置 `offline`（其余模块**保持原状**，不改入参）。

    为什么是「离线」而不是「从 fit 里摘掉」：两者在实测里给出**逐位相同**的速度 / 信号 /
    对齐 / 质量（f67: 49.076 / 65.72 / 2.958 / 1235000，摘除与离线完全一致——离线态既不
    吃 `massAddition` 也不加成），但「关闭」这个词的语义是模块还在船上，所以不改装配清单。
    """
    targets = set(positions)
    items = [
        ({**item, "state": "offline"} if position in targets else item)
        for position, item in enumerate(fit.get("items") or [])
    ]
    return {**fit, "items": items}


def propulsion_off_attrs(ctx: ViewContext) -> dict[int, float] | None:
    """推进模块**全部关闭**后的船体属性（其余模块保持当前状态）。

    - 没有索引 → `None`：查不到 group 名就判定不了「哪些是推进模块」，不猜（红线 N4）；
    - 没有推进模块 → 当前状态属性：没东西可关，「关闭前后同一状态」是**真值**不是未知；
    - 否则把推进模块置 offline 后重算一次（缓存 key 全项目唯一）。
    """
    positions = propulsion_positions(ctx)
    if positions is None:
        return None
    if not positions:
        return ctx.attrs(BASE)
    fit = with_propulsion_off(ctx.fit, positions)
    return ship_attributes(ctx.calculate_variant(PROPULSION_OFF_VARIANT, fit))


# =============================================================================
# 「单推进」态（T-43：玩家真实会用的状态，而不是全部同时启用）
# =============================================================================

SINGLE_PROPULSION_VARIANT_TEMPLATE: str = "shared:single:{position}"
"""「只启用第 N 个推进模块」变体的缓存 key 模板（`{position}` = `fit["items"]` 原下标）。"""


def propulsion_class_members() -> frozenset[int]:
    """「推进类」的 SDE group_id 集合（= 配置表里登记的全部组成员）。

    为什么单推进分支要用它而不是 `PROPULSION_GROUP`（那只是 group 46 的**名字**）：
    `Micro Jump Drive`(1189) / `Micro Jump Field Generators`(1533) 与 `Propulsion Module`(46)
    在游戏里同属「同一时刻只能启用其一」的一类（`config/activation_groups.json` 的
    `propulsion` 组），但它们的 SDE group 名完全不同。Panther 的 `MWD + MJD` 就是两条
    不同组的推进模块 —— 只按 group 名数，会把「2 个推进模块」数成 1 个。

    配置表读不到时返回**空集合**（由调用方退回 group 名判据，不猜）。
    """
    members: set[int] = set()
    for group in activation_groups():
        members |= group.members
    return frozenset(members)


def propulsion_class_positions(ctx: ViewContext, variant: str = BASE) -> list[int] | None:
    """已装**推进类**模块在 `fit["items"]` 里的原下标（跨 SDE group）；没有索引时 `None`。

    成员判定优先用配置表登记的 group_id（这才是「同一时刻只能启用其一」的那一类）；
    配置表读不到时退回 group 名 `Propulsion Module` —— 那是**降级**路径，只覆盖 AB/MWD。
    """
    if ctx.index is None:
        return None
    members = propulsion_class_members()
    positions: list[int] = []
    for position, item in enumerate(ctx.items(variant)):
        if item.get("slot_type") == SLOT_CARGO:
            continue
        group_id = item.get("group_id")
        if members:
            if isinstance(group_id, int) and not isinstance(group_id, bool) and group_id in members:
                positions.append(position)
        elif item.get("group_name") == PROPULSION_GROUP:
            positions.append(position)
    return positions


def with_single_propulsion(fit: dict, positions: list[int], keep: int) -> dict:
    """只把 `keep` 这一个推进模块置 `active`，其余推进模块置 `offline`（不改入参）。

    其它模块（护盾扩展、装甲、无人机…）**保持当前状态**，与 `with_propulsion_off()` 同一口径：
    这里回答的是「玩家只开这一个推进模块时是什么样」，不是「整船重置」。

    为什么 `keep` 一律置 `active`（而不是保持它原本的 state）：这是**玩家可选状态**的枚举，
    不是当前状态的快照。f1 的 AB 当前是 `offline`，但「只开 AB」仍是玩家真会做的一个选择，
    必须能算出来；当前状态由 `propulsion_states.base` 表达，两者互不冒充。
    """
    targets = set(positions)
    items: list[dict] = []
    for position, item in enumerate(fit.get("items") or []):
        if position in targets:
            items.append({**item, "state": "active" if position == keep else "offline"})
        else:
            items.append(item)
    return {**fit, "items": items}


def propulsion_single_attrs(ctx: ViewContext, position: int) -> dict[int, float] | None:
    """只启用下标 `position` 那个推进类模块时的船体属性。

    「推进类」的成员判定见 `propulsion_class_positions()`（配置表登记的 group，跨 SDE group）。
    - 没有索引 / `position` 不是推进类模块 → `None`（判定不了，不猜）；
    - 否则重算一次（缓存 key 按下标区分，同一 ctx 只算一次）。
    """
    positions = propulsion_class_positions(ctx)
    if positions is None or position not in positions:
        return None
    fit = with_single_propulsion(ctx.fit, positions, position)
    key = SINGLE_PROPULSION_VARIANT_TEMPLATE.format(position=position)
    return ship_attributes(ctx.calculate_variant(key, fit))


# =============================================================================
# 不产生对敌伤害的「武器」（T-43：拦截弹发射器）
# =============================================================================


def non_damage_weapon_dps(
    ctx: ViewContext, attribute_id: int, variant: str = BASE
) -> float:
    """装上的拦截弹发射器对**船级 DPS 属性**（`-12` / `-13`）的贡献合计。

    为什么必须单独扣：`classify_weapon()` 只能把拦截弹发射器挡在 `weapons[]` 外，
    而 `dps.base` / `dps.with_reload` 读的是**船级** `-12 damageDps` / `-13
    damageDpsWithReload` —— 引擎把拦截弹的 160 / 133.33 也加进去了（f11 实测，
    逐件 `-12` 之和 = 船级值，差 < 1e-12，所以直接相减不是近似）。

    判定与「算不算武器」**同一张表**（`NON_DAMAGE_WEAPON_GROUPS`），不另写一套。
    单独成函数而不是塞进 `weapon_records()`：这份贡献是**要扣除的噪声**，
    不是一条武器记录，混进记录表会让 `weapons[]` 与 `dph` 又把它算回来。
    """
    total = 0.0
    for item in ctx.items(variant):
        if item.get("group_name") not in NON_DAMAGE_WEAPON_GROUPS:
            continue
        value = (item.get("attributes") or {}).get(attribute_id)
        if value is None:
            continue
        total += float(value) * max(int(item.get("quantity") or 1), 1)
    return total


# =============================================================================
# 「状态互斥」规则表（T-43：`config/activation_groups.json`）
# =============================================================================
# 为什么规则放在**配置**里而不是代码里（T-42 报告 §5.2）：引擎原生 `max_group` 只按 SDE
# group 计数（同组才互斥），SDE 里**没有任何**「A 组禁 B 组」的字段，pyfa/eos 也一样
# （`max_group.py:96-99`）。这条知识属于「知道但数据里没有」，与 `config/attributes.py`
# 的常量表同一类资产：显式登记、可审计、每条带来源与核实日期。
# 引擎的 `max_group` 违规仍然由引擎判（那是它的强项），本表只补它覆盖不到的部分；
# 两者的**输出形状统一**成一条诊断 `state_exclusive`（见 `project/diagnostics.py`）。

ACTIVATION_GROUPS_PATH: Path = _paths.PROJECT_ROOT / "config" / "activation_groups.json"
"""规则表路径（走 `config/paths.py`，不写死绝对路径）。"""


@dataclass(frozen=True, slots=True)
class ActivationGroup:
    """一组「同一时刻只能启用其一」的模块组（按 SDE group_id 登记）。

    字段与 `config/activation_groups.json` 一一对应；`label_short` 只用于诊断文案
    （L1 体积预算紧，完整口径留在 `label`），缺失时回退到 `label`。
    """

    id: str
    label: str
    label_short: str
    members: frozenset[int]
    limit: str
    source: str
    verified_by: str
    verified_at: str
    note: str


def _activation_group_from_raw(raw: object) -> ActivationGroup | None:
    """一条 JSON 记录 → `ActivationGroup`；结构不对返回 `None`（跳过，不猜）。

    容错是刻意的（本函数在**运行时**被每次诊断调用）：规则表是人工维护的资产，
    多一条写错的记录不该让整份报告炸；但**结构错误不静默猜**——缺 id / members 的
    条目直接跳过，`source` / `verified_at` 留给测试去钉（测试跑在真实表上）。
    """
    if not isinstance(raw, dict):
        return None
    group_id = raw.get("id")
    members = raw.get("members")
    limit = raw.get("limit")
    if not isinstance(group_id, str) or not group_id:
        return None
    if not isinstance(members, list) or not members:
        return None
    if not isinstance(limit, str) or not limit:
        return None
    parsed = [member for member in members if isinstance(member, int) and not isinstance(member, bool)]
    if not parsed:
        return None
    label = raw.get("label") if isinstance(raw.get("label"), str) else group_id
    label_short = raw.get("label_short") if isinstance(raw.get("label_short"), str) else label
    return ActivationGroup(
        id=group_id,
        label=label or group_id,
        label_short=label_short or label or group_id,
        members=frozenset(parsed),
        limit=limit,
        source=raw.get("source") if isinstance(raw.get("source"), str) else "",
        verified_by=raw.get("verified_by") if isinstance(raw.get("verified_by"), str) else "",
        verified_at=raw.get("verified_at") if isinstance(raw.get("verified_at"), str) else "",
        note=raw.get("note") if isinstance(raw.get("note"), str) else "",
    )


@lru_cache(maxsize=8)
def _load_activation_groups_cached(path_text: str) -> tuple[ActivationGroup, ...]:
    """读规则表（按路径缓存）。**文件缺失 / JSON 坏 / 顶层结构不对 → 空规则，不抛**。"""
    try:
        data = json.loads(Path(path_text).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(data, dict) or not isinstance(data.get("groups"), list):
        return ()
    groups: list[ActivationGroup] = []
    for raw in data["groups"]:
        parsed = _activation_group_from_raw(raw)
        if parsed is not None:
            groups.append(parsed)
    return tuple(groups)


def activation_groups(path: Path | str | None = None) -> tuple[ActivationGroup, ...]:
    """「状态互斥」组列表（默认读 `config/activation_groups.json`）。

    容错口径：规则表读不到时返回**空元组** —— 于是「跨组互斥检测」整体退化为
    「只有引擎原生 `max_group`」，而不是抛异常把整份报告打断。这是本表的**降级**路径，
    不是正常路径（`tests/test_views_a.py` 用「清空 members → flag 消失」证明它真的驱动输出）。
    """
    target = Path(path) if path is not None else ACTIVATION_GROUPS_PATH
    return _load_activation_groups_cached(str(target))

