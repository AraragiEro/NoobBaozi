"""T3 回归测试：战斗机（fighters）DPS 与槽位占用语义 —— **T-06 补测**。

**欠账来源**：`PROGRESS.md` §5 第 5 条「`-58 fighterDps` 无航母样本未复测」。
本文件第一次在**真航母样本**上验证 `-58` 与同族派生 ID，并把结论钉成**语义断言**
（不钉精确数值）：游戏平衡会变、SDE 每日重建，精确数值属快照（开发规范 §5.2）。

## 实测语义（SDE build 3607705 / 引擎 13.6.0，仅记录在注释，不作断言）

| 派生 ID | 语义 | 证据（实测） |
|---|---|---|
| `-55` | 逐战斗机「攻击（炮/导弹）」能力 DPS | Templar II = 52.488 |
| `-56` | 逐战斗机「导弹」能力 DPS | Templar II = 34.107（重战 Gungnir II 无此项） |
| `-57` | 战斗机机库占用（m3）= Σ 数量 × `type.volume` | 6 × Templar II(1000) = 6000 |
| `-58` | **战斗机 DPS 汇总（hp/s，含技能档与舰船加成）** | Nidhoggur + 6×Templar II = 519.5687 |
| `-59` / `-60` / `-61` | 已占用的 **重 / 轻 / 支援** 中队槽位 | 轻=1 → `-60`=1；重=1 → `-59`=1；支援=1 → `-61`=1 |
| `-62` | 已占用的战斗机发射管（`fighter_tube`）数 | 1 个中队 = 1 |

三条容易踩错、因此都写成断言的语义：

1. **层次**：`-55` / `-56` / `-12` / `-13` 在**装备级是逐战斗机值**，不是中队合计；
   舰船级 `-58` 才是 `Σ 数量 × 逐战斗机 -12`。把逐机值当合计会差一个中队规模。
2. **缺失 ≠ 0**：无战斗机时引擎**不输出** `-58`（不是输出 0），所以「去掉战斗机 → 归零」
   必须按「缺失或 0」断言（见 `_value(..., 0.0)` 的默认值）。
3. **槽位与 DPS 语义独立**：支援中队**占槽位但不出 DPS**（`-61 = 1` 而 `-58 = 0`），
   不能用一个去推另一个。
4. **跨中队线性相加**：`-58` / `-57` / `-60` / `-62` 都随中队数线性增长
   （实测 1/2/3 个满编轻战中队 → `-58` = 519.57 / 1039.14 / 1558.71），
   所以 `-62` 数的是**中队（发射管内的一条 FitItem）**，不是「架数」。

## 断言的取法

- 主断言是**语义**：`> 0` / 去掉后为 0 / 槽位占用 `== 1` / `-58 == Σ 数量 × 逐机 DPS`；
- 唯一的数值区间 `100 < -58 < 10000` 是**量级护栏**（防「逐机值当合计」「毫秒当秒」
  「×1000 缩放写错」这类单位级错误），**不是精确值**，不要据此调参。

依赖：只读 `evefit.sde.index` / `evefit.config.paths` / `evefit.engine.*`；无网络、无随机（红线 N1）。

技能档：T-12 起 `derive_skill_tier` 要传索引（全 4 档）。实测新增的那批技能**不影响**
`-55/-56/-58`（战斗机技能原本就已被 requiredSkill 补到 4），所以本文件的语义断言与
量级护栏都不需要改，只是把调用从旧档切到新档。
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from pathlib import Path

import pytest

from evefit.config.attributes import ATTR_DAMAGE_DPS, ATTR_DRONE_DPS, ATTR_FIGHTER_DPS
from evefit.config.paths import db_path
from evefit.engine.adapter import Engine
from evefit.engine.assumptions import derive_skill_tier
from evefit.sde.index import Index

# ---- 派生属性 ID（引擎 pass 4 输出）-----------------------------------------
# `-58` 已在 `config/attributes.py` 登记为 `ATTR_FIGHTER_DPS`，故直接 import。
# 下面这些**尚未登记**：按开发规范 §4.2「不得裸写属性 ID」先在本文件用局部常量，
# TODO: 建议加入 config/attributes.py（T-06 报告已提出；该文件属并行修改范围，本轮不动）。
ATTR_FIGHTER_ABILITY_ATTACK_DPS = -55
"""`-55`：逐战斗机攻击（炮/导弹）能力 DPS。实测名 fighterAbilityAttackMissileDamagePerSecond。"""
ATTR_FIGHTER_ABILITY_MISSILES_DPS = -56
"""`-56`：逐战斗机导弹能力 DPS。实测名 fighterAbilityMissilesDamagePerSecond。"""
ATTR_FIGHTER_BAY_USED = -57
"""`-57`：战斗机机库占用（m3）。实测 = Σ 数量 × `types.volume`（6×Templar II = 6000）。"""
ATTR_HEAVY_FIGHTER_SLOTS_USED = -59
"""`-59`：已占用的**重型**战斗机中队槽位（实测：重战中队 → 1）。"""
ATTR_LIGHT_FIGHTER_SLOTS_USED = -60
"""`-60`：已占用的**轻型**战斗机中队槽位（实测：轻战中队 → 1）。"""
ATTR_SUPPORT_FIGHTER_SLOTS_USED = -61
"""`-61`：已占用的**支援型**战斗机中队槽位（实测：支援中队 → 1 且 DPS 为 0）。"""
ATTR_FIGHTER_TUBES_USED = -62
"""`-62`：已占用的战斗机发射管数（`fighter_tube` 槽位数，实测 1 中队 = 1）。"""

# ---- 正向 SDE 属性 ID（船 / 战斗机自身属性，同样未登记）--------------------
# TODO: 建议加入 config/attributes.py（现表里只有 droneBandwidth/droneCapacity 这类）。
ATTR_FIGHTER_SQUADRON_MAX_SIZE = 2215
"""`fighterSquadronMaxSize`：一个中队的满编架数（决定 FitItem.quantity）。"""
ATTR_FIGHTER_TUBES = 2216
"""`fighterTubes`：船体提供的战斗机发射管数。"""
ATTR_LIGHT_FIGHTER_SLOTS = 2217
"""`fighterLightSlots`：船体提供的轻型中队槽位上限。"""
ATTR_SUPPORT_FIGHTER_SLOTS = 2218
"""`fighterSupportSlots`：船体提供的支援中队槽位上限。"""
ATTR_HEAVY_FIGHTER_SLOTS = 2219
"""`fighterHeavySlots`：船体提供的重型中队槽位上限（航母无此项 → 缺省 0）。"""

# ---- 样本（按名 + group 双条件解析，不写死 type_id）------------------------
CARRIER_HULL = ("Nidhoggur", "Carrier")
"""能带战斗机的船 A：普通航母（4 发射管 / 3 轻 / 2 支援）。"""
SUPERCARRIER_HULL = ("Nyx", "Supercarrier")
"""能带战斗机的船 B：超级航母（有重型槽位，用来验 `-59`）。"""
LIGHT_FIGHTER = ("Templar II", "Light Fighter")
"""轻战：攻击 + 导弹两种能力都有（`-55` 与 `-56` 同时出现）。"""
HEAVY_FIGHTER = ("Gungnir II", "Heavy Fighter")
"""重战：只有攻击能力（`-56` 缺失），用来验 `-55` 缺失即 0 的求和语义。"""
SUPPORT_FIGHTER = ("Cenobite II", "Support Fighter")
"""支援战机：占槽位、不出 DPS。"""

# ---- 量级护栏（**不是精确值**，见模块 docstring「断言的取法」）--------------
FIGHTER_DPS_SCALE_MIN = 100.0
FIGHTER_DPS_SCALE_MAX = 10000.0
"""一个满编中队的 `-58` 合理量级：逐机 DPS 约 50~110 hp/s × 6 架 ≈ 300~700。
上下界只用来挡住单位级错误（逐机值当合计 → 偏低；×1000/毫秒写错 → 偏高）。"""

REL_TOL = 1e-9
"""同一量在引擎内部两条路径上取值时的相对容差（开发规范 §4.2：不许 float ==）。"""
ABS_TOL = 1e-12
"""与 0 比较时的绝对容差。"""


# ---- 基础设施 ---------------------------------------------------------------


def _resolve_db() -> Path:
    try:
        return db_path()
    except FileNotFoundError as exc:  # 索引库没建：跳过而不是失败（同 test_sde.py）
        pytest.skip(f"索引库未构建，跳过战斗机集成测试：{exc}")
        raise  # pragma: no cover - pytest.skip 已抛异常


def _type_id(ix: Index, sample: tuple[str, str]) -> int:
    """按「英文名 + group 名」双条件解析 type_id。

    只认精确名字，**不放宽成模糊命中**：名字变了（SDE 改版 / 平衡重命名）就该红，
    提示需要复核样本，而不是静默换成别的船或别的战斗机。
    """
    name, group_name = sample
    hits = [
        row
        for row in ix.search_types(name, 10)
        if row["name"] == name and row["group_name"] == group_name
    ]
    assert hits, (
        f"索引库里找不到 {name!r}（group={group_name!r}）；"
        f"近似命中：{[(r['name'], r['group_name']) for r in ix.search_types(name, 5)]}；"
        "SDE 改名或分组调整后需要复核本测试的样本"
    )
    return int(hits[0]["type_id"])


@pytest.fixture(scope="module")
def ix() -> Iterator[Index]:
    """模块级只读索引（多测试共用一条库，别让每个测试都 open 一次）。"""
    index = Index.open(_resolve_db())
    try:
        yield index
    finally:
        index.close()


@pytest.fixture(scope="module")
def engine() -> Engine:
    """模块级引擎（SDE 单例守卫在 adapter 里，重复构造不会二次加载）。"""
    return Engine()


@pytest.fixture(scope="module")
def carrier(ix: Index) -> int:
    return _type_id(ix, CARRIER_HULL)


@pytest.fixture(scope="module")
def supercarrier(ix: Index) -> int:
    return _type_id(ix, SUPERCARRIER_HULL)


@pytest.fixture(scope="module")
def light_fighter(ix: Index) -> int:
    return _type_id(ix, LIGHT_FIGHTER)


@pytest.fixture(scope="module")
def heavy_fighter(ix: Index) -> int:
    return _type_id(ix, HEAVY_FIGHTER)


@pytest.fixture(scope="module")
def support_fighter(ix: Index) -> int:
    return _type_id(ix, SUPPORT_FIGHTER)


# ---- 配装构造与读取 ---------------------------------------------------------


def _fighter_fit(
    ship_id: int,
    fighter_id: int,
    quantity: int,
    tube_index: int = 0,
) -> dict:
    """最小可行配装：一条船 + 一个战斗机中队塞进 `fighter_tube[index]`。

    结构照 `contracts.py` 的 `FitItem`：`slot.type` 必须是枚举值 `fighter_tube`
    （写错不报错、只会静默失效，见 PROGRESS §6 第 1 条），`quantity` 是中队的架数。
    """
    return {
        "ship": {"type_id": ship_id},
        "items": [
            {
                "type_id": fighter_id,
                "slot": {"type": "fighter_tube", "index": tube_index},
                "quantity": quantity,
                "state": "active",
            }
        ],
    }


def _calculated(engine: Engine, fit: dict, index: Index) -> dict:
    """推导技能档后计算（任务要求：技能表必须由 `derive_skill_tier` 推，不手写）。

    T-12 起必须传索引：不传只能得到「只补最低要求」的旧档，会与默认口径不符。
    """
    skills = derive_skill_tier(fit, engine, index=index)
    return engine.calculate({**fit, "character": {"skills": skills}}, validate=True)


def _value(attributes: dict, attr_id: int, default: float | None = None) -> float | None:
    """读一条属性的计算后值；属性缺失时返回 `default`。

    引擎对「本配装不涉及」的派生量**直接省略**（例如无战斗机时没有 `-58`），
    所以判定「归零」必须走 `default=0.0`，不能假设键一定在。
    """
    entry = attributes.get(attr_id)
    return default if entry is None else float(entry["value"])


def _squadron_size(engine: Engine, ship_id: int, fighter_id: int) -> int:
    """取该战斗机的满编中队架数（`fighterSquadronMaxSize`），不写死 6/9。

    先用 1 架探一次：中队的满编架数是战斗机自身属性，与数量无关，
    所以拿 `quantity=1` 读出来再按满编重算，即可不依赖任何硬编码数字。
    """
    probe = engine.calculate(_fighter_fit(ship_id, fighter_id, quantity=1))
    size = _value(probe["items"][0]["attributes"], ATTR_FIGHTER_SQUADRON_MAX_SIZE)
    assert size is not None and size > 0, (
        f"战斗机 {fighter_id} 没有 fighterSquadronMaxSize（{ATTR_FIGHTER_SQUADRON_MAX_SIZE}）；"
        "无法确定中队规模，不能继续"
    )
    return int(size)


@pytest.fixture(scope="module")
def light_sample(
    engine: Engine, ix: Index, carrier: int, light_fighter: int
) -> tuple[int, dict]:
    """（满编架数, 计算结果）：航母 + 一个满编轻型中队。"""
    quantity = _squadron_size(engine, carrier, light_fighter)
    return quantity, _calculated(engine, _fighter_fit(carrier, light_fighter, quantity), ix)


@pytest.fixture(scope="module")
def heavy_sample(
    engine: Engine, ix: Index, supercarrier: int, heavy_fighter: int
) -> tuple[int, dict]:
    """（满编架数, 计算结果）：超级航母 + 一个满编重型中队（航母没有重型槽位）。"""
    quantity = _squadron_size(engine, supercarrier, heavy_fighter)
    return quantity, _calculated(
        engine, _fighter_fit(supercarrier, heavy_fighter, quantity), ix
    )


@pytest.fixture(scope="module")
def support_sample(
    engine: Engine, ix: Index, carrier: int, support_fighter: int
) -> tuple[int, dict]:
    """（满编架数, 计算结果）：航母 + 一个满编支援中队。"""
    quantity = _squadron_size(engine, carrier, support_fighter)
    return quantity, _calculated(engine, _fighter_fit(carrier, support_fighter, quantity), ix)


# ---- 1. `-58` 有值、且已并入总 DPS ------------------------------------------


def test_light_squadron_reports_positive_fighter_dps(light_sample: tuple[int, dict]) -> None:
    """有战斗机时 `-58` 必须 > 0，且总 DPS（`-12`）在纯战斗机配装下等于 `-58`。

    「纯战斗机」指高/中/低槽全空：此时 `-12` 没有任何其他来源，
    两者必须相等，否则说明 `-58` 统计的不是同一口径（漏算或多算）。
    """
    quantity, result = light_sample
    ship = result["ship"]["attributes"]
    dps = _value(ship, ATTR_FIGHTER_DPS)
    total = _value(ship, ATTR_DAMAGE_DPS)

    assert quantity > 0
    assert dps is not None, "有战斗机时舰船层必须有 -58 fighterDps"
    assert dps > 0, f"-58 实测 {dps}，有战斗机时必须为正"
    assert total is not None
    assert math.isclose(total, dps, rel_tol=REL_TOL), (
        f"纯战斗机配装下 -12（{total}）应等于 -58（{dps}）：战斗机 DPS 必须并入总 DPS"
    )


def test_fighter_dps_magnitude_is_plausible(light_sample: tuple[int, dict]) -> None:
    """`-58` 的量级护栏（**不是精确值**，只是挡住单位级错误）。

    实测（SDE 3607705）：6×Templar II 在 Nidhoggur 上 `-58 = 519.5687` hp/s。
    区间取 100~10000：把逐机值当合计（约 50~110）会被下界挡住；
    把毫秒/×1000 缩放搞错（差 1000 倍）会被上界挡住。
    游戏平衡改动会移动这个数，但不会移动它的量级。
    """
    _, result = light_sample
    dps = _value(result["ship"]["attributes"], ATTR_FIGHTER_DPS, 0.0)
    assert FIGHTER_DPS_SCALE_MIN < dps < FIGHTER_DPS_SCALE_MAX, (
        f"-58 = {dps} 掉出合理量级 ({FIGHTER_DPS_SCALE_MIN}, {FIGHTER_DPS_SCALE_MAX})："
        "大概率是单位/口径写错（逐机 vs 汇总、毫秒 vs 秒、×1000），请先查单位再查平衡"
    )


# ---- 2. 去掉战斗机后归零（证明不是残留值）----------------------------------


def test_fighter_dps_vanishes_without_fighters(
    engine: Engine, ix: Index, carrier: int, light_sample: tuple[int, dict]
) -> None:
    """同一艘船去掉战斗机后，`-58` / `-62` / `-57` 必须归零。

    **实测细节**：引擎在无战斗机时**不输出** `-58`（而不是输出 0），
    所以按「缺失或 0」断言 —— 这不是放水，而是引擎口径；`-62` 则稳定输出 0.0。
    这一条是「`-58` 不是残留值、也不是别的系统漏进来的」的唯一硬证据。
    """
    _, result = light_sample
    assert _value(result["ship"]["attributes"], ATTR_FIGHTER_DPS, 0.0) > 0

    bare = _calculated(engine, {"ship": {"type_id": carrier}, "items": []}, ix)
    ship = bare["ship"]["attributes"]

    assert math.isclose(_value(ship, ATTR_FIGHTER_DPS, 0.0), 0.0, abs_tol=ABS_TOL), (
        "去掉战斗机后 -58 必须归零（缺失或 0），否则它是别处的残留值"
    )
    assert math.isclose(_value(ship, ATTR_FIGHTER_TUBES_USED, 0.0), 0.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_FIGHTER_BAY_USED, 0.0), 0.0, abs_tol=ABS_TOL)


# ---- 3. 槽位占用语义（`-59/-60/-61/-62`）-----------------------------------


def test_light_squadron_occupies_one_light_slot(
    light_sample: tuple[int, dict],
) -> None:
    """一个轻型中队 → `-60 = 1`、`-62 = 1`，重型/支援槽位仍为 0。

    同时校验占用不越界（占用数 ≤ 船体给的槽位/发射管上限），
    这样「飞机装进去了」就不是靠 violations 为空来间接证明的。
    """
    _, result = light_sample
    ship = result["ship"]["attributes"]

    assert math.isclose(_value(ship, ATTR_LIGHT_FIGHTER_SLOTS_USED, 0.0), 1.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_FIGHTER_TUBES_USED, 0.0), 1.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_HEAVY_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_SUPPORT_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL)

    tubes_available = _value(ship, ATTR_FIGHTER_TUBES, 0.0)
    light_available = _value(ship, ATTR_LIGHT_FIGHTER_SLOTS, 0.0)
    assert tubes_available > 0, "样本船必须能带战斗机（fighterTubes > 0）"
    assert light_available > 0, "样本船必须有轻型战斗机槽位"
    assert _value(ship, ATTR_FIGHTER_TUBES_USED, 0.0) <= tubes_available
    assert _value(ship, ATTR_LIGHT_FIGHTER_SLOTS_USED, 0.0) <= light_available


def test_heavy_squadron_occupies_one_heavy_slot(heavy_sample: tuple[int, dict]) -> None:
    """一个重型中队 → `-59 = 1`，轻型/支援槽位为 0。

    样本必须是**超级航母**：实测普通航母 `fighterHeavySlots`（2219）缺失（= 0），
    硬塞重战会得到 `resource: heavy_fighter_tubes used=1 available=0` 违规，
    那不是「槽位语义」的证据，而是「装错了」。
    """
    quantity, result = heavy_sample
    ship = result["ship"]["attributes"]

    assert quantity > 0
    assert _value(ship, ATTR_FIGHTER_DPS, 0.0) > 0, "重战也有 DPS"
    assert math.isclose(_value(ship, ATTR_HEAVY_FIGHTER_SLOTS_USED, 0.0), 1.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_FIGHTER_TUBES_USED, 0.0), 1.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_LIGHT_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_SUPPORT_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL)


def test_two_squadrons_aggregate_linearly(
    engine: Engine,
    ix: Index,
    carrier: int,
    light_fighter: int,
    light_sample: tuple[int, dict],
) -> None:
    """两个满编轻战中队 → `-58` 翻倍、`-60 = 2`、`-62 = 2`、`-57` 也翻倍。

    这条把「`-62` 数的是**中队**（一条 FitItem = 一个发射管）而不是架数」钉死：
    一个中队是 6 架，如果 `-62` 按架数计早就是 6 了。
    `-58` 线性叠加也说明它是**求和**而不是取最大/取平均。
    """
    quantity, single = light_sample
    single_ship = single["ship"]["attributes"]
    single_dps = _value(single_ship, ATTR_FIGHTER_DPS, 0.0)
    tube_limit = int(_value(single_ship, ATTR_FIGHTER_TUBES, 0.0))
    light_limit = int(_value(single_ship, ATTR_LIGHT_FIGHTER_SLOTS, 0.0))
    squadrons = min(2, tube_limit, light_limit)
    assert squadrons == 2, (
        f"样本船至少要能同时装 2 个轻型中队（发射管 {tube_limit} / 轻型槽位 {light_limit}），"
        "否则换样本船"
    )

    fit = {
        "ship": {"type_id": carrier},
        "items": [
            {
                "type_id": light_fighter,
                "slot": {"type": "fighter_tube", "index": index},
                "quantity": quantity,
                "state": "active",
            }
            for index in range(squadrons)
        ],
    }
    result = _calculated(engine, fit, ix)
    ship = result["ship"]["attributes"]
    volume = ix.get_type(light_fighter)["volume"]

    assert math.isclose(
        _value(ship, ATTR_FIGHTER_DPS, 0.0), squadrons * single_dps, rel_tol=REL_TOL
    ), f"{squadrons} 个中队的 -58 应等于 {squadrons} × 单中队 {single_dps}"
    assert math.isclose(
        _value(ship, ATTR_LIGHT_FIGHTER_SLOTS_USED, 0.0), float(squadrons), abs_tol=ABS_TOL
    )
    assert math.isclose(
        _value(ship, ATTR_FIGHTER_TUBES_USED, 0.0), float(squadrons), abs_tol=ABS_TOL
    )
    assert math.isclose(
        _value(ship, ATTR_FIGHTER_BAY_USED, 0.0),
        squadrons * quantity * volume,
        rel_tol=REL_TOL,
    )
    assert math.isclose(
        _value(ship, ATTR_HEAVY_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL
    )
    assert math.isclose(
        _value(ship, ATTR_SUPPORT_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL
    )


def test_support_squadron_occupies_support_slot_without_dps(
    support_sample: tuple[int, dict],
) -> None:
    """一个支援中队 → `-61 = 1`、`-62 = 1`，但 `-58` **严格为 0**。

    这是「槽位占用」与「DPS」两个语义互相独立的关键样本：
    支援机（Cenobite II）不出伤害，却确实占了一个支援槽位和一条发射管。
    反过来说，`-62 = 1` 不能推出 `-58 > 0`。
    """
    quantity, result = support_sample
    ship = result["ship"]["attributes"]

    assert quantity > 0
    assert math.isclose(_value(ship, ATTR_SUPPORT_FIGHTER_SLOTS_USED, 0.0), 1.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_FIGHTER_TUBES_USED, 0.0), 1.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_LIGHT_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_HEAVY_FIGHTER_SLOTS_USED, 0.0), 0.0, abs_tol=ABS_TOL)
    assert math.isclose(_value(ship, ATTR_FIGHTER_DPS, 0.0), 0.0, abs_tol=ABS_TOL), (
        "支援中队不出 DPS：-61 占槽位但 -58 必须为 0"
    )


# ---- 4. 汇总口径：`-58` = Σ 数量 × 逐机 DPS；机库占用 = Σ 数量 × 体积 --------


def test_ship_fighter_dps_is_squadron_size_times_per_fighter_dps(
    light_sample: tuple[int, dict], heavy_sample: tuple[int, dict]
) -> None:
    """舰船 `-58` == 数量 × 装备级 `-12`（逐机 DPS）。

    装备级 `-12` 实测与数量无关（q=1/3/6 都是同一个逐机值），
    所以这条等式直接把「逐机」与「汇总」两个层次钉住：
    任何一边改成合计口径，等式立刻崩。
    """
    for label, (quantity, result) in (("light", light_sample), ("heavy", heavy_sample)):
        ship = result["ship"]["attributes"]
        per_fighter = _value(result["items"][0]["attributes"], ATTR_DAMAGE_DPS)
        assert per_fighter is not None and per_fighter > 0, f"{label}: 装备级应有逐机 DPS"
        assert math.isclose(
            _value(ship, ATTR_FIGHTER_DPS, 0.0), quantity * per_fighter, rel_tol=REL_TOL
        ), f"{label}: 舰船 -58 应等于 {quantity} × 逐机 {per_fighter}"


def test_per_fighter_dps_is_sum_of_ability_dps(
    light_sample: tuple[int, dict], heavy_sample: tuple[int, dict]
) -> None:
    """逐机 `-12` == `-55`（攻击能力）+ `-56`（导弹能力），缺失的能力项按 0 计。

    轻战 Templar II 两种能力都有；重战 Gungnir II 只有攻击能力（无 `-56`），
    所以这里用 `default=0.0` 读缺失项 —— 这就是「缺失即 0」的正确用法，
    而不是把缺失当未知去猜。
    """
    for label, (_quantity, result) in (("light", light_sample), ("heavy", heavy_sample)):
        item = result["items"][0]["attributes"]
        per_fighter = _value(item, ATTR_DAMAGE_DPS, 0.0)
        attack = _value(item, ATTR_FIGHTER_ABILITY_ATTACK_DPS, 0.0)
        missiles = _value(item, ATTR_FIGHTER_ABILITY_MISSILES_DPS, 0.0)
        assert per_fighter > 0, f"{label}: 逐机 DPS 必须为正"
        assert math.isclose(per_fighter, attack + missiles, rel_tol=REL_TOL), (
            f"{label}: 逐机 DPS（{per_fighter}）应等于攻击（{attack}）+ 导弹（{missiles}）"
        )


def test_fighter_bay_usage_is_squadron_volume(
    ix: Index, light_sample: tuple[int, dict], light_fighter: int
) -> None:
    """机库占用 `-57` == Σ 数量 × `types.volume`（m3），与 SDE 体积表严格一致。

    实测：6 × Templar II（1000 m3）= 6000；重战 2000 m3 / 支援机 3000 m3 同理。
    这条把 `-57` 的**单位**钉死为 m3（不是「架数」也不是「带宽」）。
    """
    quantity, result = light_sample
    volume = ix.get_type(light_fighter)["volume"]
    assert volume > 0
    assert math.isclose(
        _value(result["ship"]["attributes"], ATTR_FIGHTER_BAY_USED, 0.0),
        quantity * volume,
        rel_tol=REL_TOL,
    )


# ---- 5. 与无人机 DPS 互不污染 --------------------------------------------


def test_fighter_dps_does_not_leak_into_drone_dps(light_sample: tuple[int, dict]) -> None:
    """纯战斗机配装下 `-14`（droneDps）必须为 0 或缺失。

    `-14` 与 `-58` 是两条独立管线（PROGRESS §3 第 32 条：`-14` 实测 48.0 并已并入 `-12`）。
    战斗机不该让 `-14` 有值，否则无人机视角会凭空多出伤害。
    """
    _, result = light_sample
    drone_dps = _value(result["ship"]["attributes"], ATTR_DRONE_DPS, 0.0)
    assert math.isclose(drone_dps, 0.0, abs_tol=ABS_TOL), (
        f"纯战斗机配装的 -14 = {drone_dps}，战斗机 DPS 不应流进无人机口径"
    )


# ---- 6. 技能档：战斗机需求也必须被 derive_skill_tier 覆盖 ------------------


def test_derived_skill_tier_clears_fighter_skill_violations(
    light_sample: tuple[int, dict],
) -> None:
    """用 `derive_skill_tier` 推出来的技能档必须让技能类违规清零。

    战斗机需要「Fighters / Light Fighters」这类技能，手写技能表很容易漏；
    这里断言的是「技能档推导能自洽」，而不是具体技能 ID 或数量（那是快照）。
    """
    _, result = light_sample
    skill_rules = [
        violation["rule"]
        for violation in result.get("violations") or []
        if violation.get("rule", {}).get("type") == "skill"
    ]
    assert skill_rules == [], f"技能档推导后仍有技能类违规：{skill_rules}"


# ---- 7. `-58` 是引擎派生 ID，不属于 SDE 属性表 -----------------------------


def test_fighter_dps_id_is_engine_derived_not_sde(ix: Index) -> None:
    """`-58` 不在 SDE 属性表里（引擎 pass 4 派生），名字只能靠约定。

    这条记录 provenance 事实：负 ID **不可从 SDE / ESI 反查名字**
    （PROGRESS §3 第 1 条、§6 第 6 条），所以常量表是唯一事实源；
    若将来有人在索引库里「补」出负 ID，这条会红，提示该做法越界。
    """
    assert ix.attribute(ATTR_FIGHTER_DPS) is None
    assert ix.attribute(ATTR_FIGHTER_ABILITY_ATTACK_DPS) is None
    assert ix.attribute(ATTR_FIGHTER_TUBES_USED) is None
