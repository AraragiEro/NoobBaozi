"""T1 单元测试：属性字典与单位/缩放表。

被测：
- `noobbaozi/config/attributes.py`（常量 + 单位/缩放表）
- `noobbaozi/sde/attribute_dict.py`（纯函数查询层）

覆盖：
1. 常量表完整性：每个 `ATTR_*` 值唯一、为 `int`、且都有元数据行；
2. 缩放语义：毫秒 ×0.001、×1000 ÷1000、不缩放原值返回（容差 1e-9）；
3. 未知 ID 行为：`name_of` 回 `attr_<id>`，`unit_of` 回 `None`，**都不抛异常**；
4. `audit()` 计数自洽、无重复值、无孤儿行、无单位标签不一致；
5. 纯函数约束：被测模块不 import os/io/sqlite3/urllib 等，模块级无调用；
6. 战斗机族（T-07 补登）：12 个新常量的 ID / 单位 / 元数据行，5 个正 ID 与
   SDE 索引库的真名逐字对上（防串号），派生负 ID 仍不在 SDE 表里（provenance），
   且 `audit()` 仍自洽、常量数恰好 +12。
7. T-13 补登 3 个常量（1272 `droneBandwidthUsed` / 56 `chargeRate` / 73 `duration`）
   并修正 `-3 derivedCycleTime` 的单位标注：三个正 ID 与索引库真名 + `unit_id` 对拍，
   `73` / `-3` 进缩放表（毫秒，`4800.0 → 4.8`），常量数 141 → 144、缩放 8 → 10。
8. T-23 补登 2 个常量（1367 `maxSubSystems` / 281 `explosionDelay`）：两个正 ID 与索引库
   真名 + `unit_id` + displayName 对拍（`Subsystem Slots` / `Maximum Flight Time`），
   `281` 进缩放表（毫秒，`7000.0 → 7.0`），`1367` **不缩放**（槽位件数），
   常量数 144 → 146、缩放 10 → 11、毫秒 8 → 9。
9. T-32 补登 1 个常量（`-70 maxTargets`，**派生，SDE 无记录**：`Index.attribute(-70)=None`），
   并把「常量数」从三处硬编码（`== 146`）改成**由表推导**：`audit()` 计数 ==
   `len(CONSTANT_NAMES)` == `len(ATTRIBUTES)` 自洽断言 + `CONSTANT_COUNT_LEDGER` 显式增量台账
   （129 → 141 → 144 → 146 → 147）。以后补常量只需在台账里加一行。

测试规范：无网络、无磁盘 fixture；只用标准库 + pytest（开发规范 §1 / §5.1 T1）。
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from noobbaozi.config import attributes
from noobbaozi.sde import attribute_dict

# 全部 ATTR_* 常量名（顺序稳定，便于失败时定位）
CONSTANT_NAMES: list[str] = sorted(
    name for name in dir(attributes) if name.startswith(attributes.CONSTANT_PREFIX)
)

# 缩放容差：契约要求 1e-9（开发规范 §4.2）
REL_TOL = 1e-9
ABS_TOL = 1e-12


def _close(got: float, want: float) -> bool:
    return math.isclose(got, want, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _exactly(got: float, want: float) -> bool:
    """严格相等（容差为 0）——用于断言"确实没有被缩放"。"""
    return math.isclose(got, want, rel_tol=0.0, abs_tol=0.0)


# ---------------------------------------------------------------------------
# 1. 常量表完整性
# ---------------------------------------------------------------------------


def test_constant_table_is_not_trivially_small() -> None:
    # 字段规格六视角用到的属性 + 派生负 ID 一共上百个；少于 100 说明表被截断
    assert len(CONSTANT_NAMES) >= 100


def test_every_constant_is_an_int() -> None:
    for name in CONSTANT_NAMES:
        value = getattr(attributes, name)
        assert isinstance(value, int) and not isinstance(value, bool), (
            f"{name} 不是整数（实际 {value!r}）"
        )


def test_every_constant_value_is_unique() -> None:
    seen: dict[int, str] = {}
    for name in CONSTANT_NAMES:
        value = getattr(attributes, name)
        assert value not in seen, f"{name}={value} 与 {seen[value]} 撞值（复制粘贴写错？）"
        seen[value] = name


def test_every_constant_has_a_metadata_row() -> None:
    constant_ids = {getattr(attributes, name) for name in CONSTANT_NAMES}
    assert constant_ids == set(attributes.ATTRIBUTES), (
        "常量与元数据表不一致：缺行 "
        f"{sorted(constant_ids - set(attributes.ATTRIBUTES))}，多行 "
        f"{sorted(set(attributes.ATTRIBUTES) - constant_ids)}"
    )


def test_metadata_rows_fit_the_tuple_contract() -> None:
    for attr_id, row in attributes.ATTRIBUTES.items():
        assert len(row) == 3, f"{attr_id} 的元数据不是 (name, unit, unit_id)"
        name, unit, unit_id = row
        assert isinstance(name, str) and name, f"{attr_id} 缺少属性名"
        assert unit is None or isinstance(unit, str), f"{attr_id} 单位类型不对"
        assert unit_id is None or isinstance(unit_id, int), f"{attr_id} unit_id 类型不对"


def test_derived_negative_ids_are_all_covered() -> None:
    # 派生负 ID 必须全部有名字，否则下游只能拿到 attr_-43 这种字符串
    negative = [attr_id for attr_id in attributes.ATTRIBUTES if attr_id < 0]
    assert len(negative) >= 30
    for attr_id in negative:
        assert not attribute_dict.name_of(attr_id).startswith("attr_"), (
            f"派生负 ID {attr_id} 没有登记名字"
        )


def test_skill_id_trap_is_documented_in_the_table() -> None:
    # requiredSkill4..6 不是 185/186/187（那是 attributePoints /
    # warpCapacitorNeedMultiplier），ESI 实测为 1285/1289/1290
    assert attributes.ATTR_REQUIRED_SKILL_1 == 182
    assert attributes.ATTR_REQUIRED_SKILL_2 == 183
    assert attributes.ATTR_REQUIRED_SKILL_3 == 184
    assert attributes.ATTR_REQUIRED_SKILL_4 == 1285
    assert attributes.ATTR_REQUIRED_SKILL_5 == 1289
    assert attributes.ATTR_REQUIRED_SKILL_6 == 1290
    assert attributes.ATTR_REQUIRED_SKILL_1_LEVEL == 277
    assert attributes.ATTR_REQUIRED_SKILL_2_LEVEL == 278
    assert attributes.ATTR_REQUIRED_SKILL_3_LEVEL == 279
    assert attributes.ATTR_REQUIRED_SKILL_4_LEVEL == 1286
    assert attributes.ATTR_REQUIRED_SKILL_5_LEVEL == 1287
    assert attributes.ATTR_REQUIRED_SKILL_6_LEVEL == 1288


def test_resistance_ids_are_positive_not_negative() -> None:
    # 红线：真实抗性必须读正 ID，不能用负的加权值
    evidence = {
        attributes.ATTR_SHIELD_EM_RESONANCE: 271,
        attributes.ATTR_SHIELD_EXPLOSIVE_RESONANCE: 272,
        attributes.ATTR_SHIELD_KINETIC_RESONANCE: 273,
        attributes.ATTR_SHIELD_THERMAL_RESONANCE: 274,
        attributes.ATTR_ARMOR_EM_RESONANCE: 267,
        attributes.ATTR_ARMOR_EXPLOSIVE_RESONANCE: 268,
        attributes.ATTR_ARMOR_KINETIC_RESONANCE: 269,
        attributes.ATTR_ARMOR_THERMAL_RESONANCE: 270,
        attributes.ATTR_HULL_EM_RESONANCE: 113,
        attributes.ATTR_HULL_EXPLOSIVE_RESONANCE: 111,
        attributes.ATTR_HULL_KINETIC_RESONANCE: 109,
        attributes.ATTR_HULL_THERMAL_RESONANCE: 110,
    }
    for attr_id, expected in evidence.items():
        assert attr_id == expected > 0


# ---------------------------------------------------------------------------
# 2. 缩放表
# ---------------------------------------------------------------------------


def test_millisecond_attributes_match_the_measured_list() -> None:
    expected = {
        attributes.ATTR_CYCLE_TIME,               # 51
        attributes.ATTR_MODULE_DURATION,          # 73   （T-13：ANP 4800 → 4.8 s）
        attributes.ATTR_DERIVED_CYCLE_TIME,       # -3   （T-10 硬结论 65：装备级派生周期是毫秒）
        attributes.ATTR_RELOAD_TIME,              # 1795
        attributes.ATTR_SCAN_SPEED,               # 79
        attributes.ATTR_SHIELD_RECHARGE_RATE,      # 479
        attributes.ATTR_CAPACITOR_RECHARGE_RATE,  # 55
        attributes.ATTR_STANCE_SWITCH_TIME,       # 1985
        attributes.ATTR_EXPLOSION_DELAY,          # 281  （T-23：索引库核实 unit_id 101 Milliseconds）
    }
    got = {
        attr_id
        for attr_id, scale in attributes.SCALE_BY_ATTR.items()
        if math.isclose(scale, attributes.SCALE_MILLI, rel_tol=REL_TOL)
    }
    assert got == expected
    assert len(got) == 9, f"毫秒项应为 9 个（8 → +281），实际 {sorted(got)}"


def test_kilo_attributes_match_the_measured_list() -> None:
    expected = {attributes.ATTR_TRACKING_SPEED, attributes.ATTR_OPTIMAL_SIG_RADIUS}
    got = {
        attr_id
        for attr_id, scale in attributes.SCALE_BY_ATTR.items()
        if math.isclose(scale, attributes.SCALE_KILO, rel_tol=REL_TOL)
    }
    assert got == expected


def test_scale_table_only_contains_known_conventions() -> None:
    for attr_id, scale in attributes.SCALE_BY_ATTR.items():
        assert math.isclose(scale, attributes.SCALE_MILLI, rel_tol=REL_TOL) or math.isclose(
            scale, attributes.SCALE_KILO, rel_tol=REL_TOL
        ), f"{attr_id} 的缩放系数 {scale} 不是已确认的约定"
        assert attr_id in attributes.ATTRIBUTES, f"缩放表里的 {attr_id} 没有元数据行"


def test_unscaled_attribute_reports_one() -> None:
    assert math.isclose(
        attribute_dict.scale_of(attributes.ATTR_ALIGN_TIME), attributes.SCALE_ONE, rel_tol=REL_TOL
    )
    assert attribute_dict.is_scaled(attributes.ATTR_ALIGN_TIME) is False
    assert attribute_dict.is_scaled(attributes.ATTR_CYCLE_TIME) is True
    assert attribute_dict.is_scaled(attributes.ATTR_TRACKING_SPEED) is True


def test_unknown_attribute_is_treated_as_unscaled() -> None:
    unknown_id = 987654
    assert math.isclose(
        attribute_dict.scale_of(unknown_id), attributes.SCALE_ONE, rel_tol=REL_TOL
    )
    assert attribute_dict.is_scaled(unknown_id) is False


# ---------------------------------------------------------------------------
# 3. to_physical：验收用例（容差 1e-9）
# ---------------------------------------------------------------------------


def test_cycle_time_ms_to_seconds() -> None:
    # 51 实测 2226.068 ms —— 一个 2.226 s 的激活周期
    got = attribute_dict.to_physical(attributes.ATTR_CYCLE_TIME, 2226.068)
    assert isinstance(got, float)
    assert _close(got, 2.226068), got


def test_tracking_speed_kilo_to_physical() -> None:
    # 160 实测 391.67（×1000）→ 0.39167 rad/s
    got = attribute_dict.to_physical(attributes.ATTR_TRACKING_SPEED, 391.67)
    assert isinstance(got, float)
    assert _close(got, 0.39167), got


def test_optimal_sig_radius_kilo_to_physical() -> None:
    # 620 实测 40000（×1000）→ 40 m
    got = attribute_dict.to_physical(attributes.ATTR_OPTIMAL_SIG_RADIUS, 40000.0)
    assert _close(got, 40.0), got


def test_align_time_is_not_scaled() -> None:
    got = attribute_dict.to_physical(attributes.ATTR_ALIGN_TIME, 11.8611)
    assert isinstance(got, float)
    # 不缩放 = 原值，必须严格相等（不是"差不多"）
    assert _exactly(got, 11.8611), got


def test_capacitor_recharge_rate_ms_to_seconds() -> None:
    # 55 实测 280000 ms → 280 s
    assert _close(attribute_dict.to_physical(attributes.ATTR_CAPACITOR_RECHARGE_RATE, 280000.0), 280.0)


def test_integer_input_is_returned_as_float() -> None:
    got = attribute_dict.to_physical(attributes.ATTR_SHIELD_CAPACITY, 650)
    assert isinstance(got, float)
    assert _exactly(got, 650.0)


def test_zero_and_negative_raw_values_survive() -> None:
    assert _exactly(attribute_dict.to_physical(attributes.ATTR_CYCLE_TIME, 0.0), 0.0)
    assert _close(attribute_dict.to_physical(attributes.ATTR_POWER_FREE, -4.5), -4.5)


# ---------------------------------------------------------------------------
# 4. 名称 / 单位查询
# ---------------------------------------------------------------------------


def test_name_of_known_attribute() -> None:
    assert attribute_dict.name_of(attributes.ATTR_ALIGN_TIME) == "alignTime"
    assert attribute_dict.name_of(attributes.ATTR_CYCLE_TIME) == "speed"
    assert attribute_dict.name_of(attributes.ATTR_HULL_HP) == "hp"
    assert attribute_dict.name_of(attributes.ATTR_EHP) == "ehp"


def test_name_of_unknown_returns_attr_id_without_raising() -> None:
    assert attribute_dict.name_of(987654) == "attr_987654"
    assert attribute_dict.name_of(-424242) == "attr_-424242"
    assert attribute_dict.name_of(0) == "attr_0"


def test_unit_of_known_and_unknown() -> None:
    assert attribute_dict.unit_of(attributes.ATTR_CYCLE_TIME) == "s"
    assert attribute_dict.unit_of(attributes.ATTR_TRACKING_SPEED) == "rad/s"
    assert attribute_dict.unit_of(attributes.ATTR_ALIGN_TIME) == "s"
    assert attribute_dict.unit_of(attributes.ATTR_EHP) == "hp"
    # 无量纲且 SDE 未给单位的量：宁可 None，不猜（红线 N4 同理）
    assert attribute_dict.unit_of(attributes.ATTR_HEAT_CAPACITY_HI) is None
    assert attribute_dict.unit_of(987654) is None


def test_unit_id_of_mirrors_esi_measurement() -> None:
    assert attribute_dict.unit_id_of(attributes.ATTR_CYCLE_TIME) == 101
    assert attribute_dict.unit_id_of(attributes.ATTR_HULL_HP) == 113
    assert attribute_dict.unit_id_of(attributes.ATTR_SHIELD_EM_RESONANCE) == 108
    assert attribute_dict.unit_id_of(attributes.ATTR_ALIGN_TIME) is None
    assert attribute_dict.unit_id_of(987654) is None


# ---------------------------------------------------------------------------
# 5. audit() 自洽
# ---------------------------------------------------------------------------


def test_audit_counts_are_self_consistent() -> None:
    report = attribute_dict.audit()

    assert report["constant_count"] == len(CONSTANT_NAMES)
    assert report["attribute_count"] == len(attributes.ATTRIBUTES)
    assert report["constant_count"] == report["attribute_count"]
    assert report["scaled_count"] == len(attributes.SCALE_BY_ATTR)
    assert report["scaled_count"] == len(
        [attr_id for attr_id in attributes.ATTRIBUTES if attribute_dict.is_scaled(attr_id)]
    )
    assert report["milli_count"] + report["kilo_count"] == report["scaled_count"]
    assert report["milli_count"] == 9  # T-23：8 → 9（+281 explosionDelay）
    assert report["kilo_count"] == 2
    assert report["scaled_count"] == 11  # T-23：10 → 11


def test_audit_reports_no_problems() -> None:
    report = attribute_dict.audit()
    assert report["duplicate_values"] == []
    assert report["non_int_constants"] == []
    assert report["orphan_constants"] == []
    assert report["orphan_rows"] == []
    assert report["unknown_scale_values"] == []
    assert report["unit_label_mismatches"] == []
    assert report["consistent"] is True


def test_audit_is_pure_and_repeatable() -> None:
    first = attribute_dict.audit()
    second = attribute_dict.audit()
    assert first == second
    # 不允许返回内部可变表本体（否则调用方能改坏常量字典）
    assert first["duplicate_values"] is not second["duplicate_values"]


# ---------------------------------------------------------------------------
# 6. 纯函数约束（AST 静态检查）
# ---------------------------------------------------------------------------

_ALLOWED_IMPORTS = {"__future__", "math", "noobbaozi.config", "noobbaozi.config.attributes"}
_FORBIDDEN_MODULE_PREFIXES = ("os", "io", "sys", "pathlib", "sqlite3", "urllib", "socket", "json")


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
    return found


def test_attribute_dict_imports_only_pure_modules() -> None:
    imports = _imports_of(Path(attribute_dict.__file__))
    assert imports <= _ALLOWED_IMPORTS, f"attribute_dict 引入了非纯依赖: {sorted(imports)}"
    for name in imports:
        assert not name.startswith(_FORBIDDEN_MODULE_PREFIXES), f"禁止的依赖: {name}"


def test_attributes_config_has_no_io_imports() -> None:
    imports = _imports_of(Path(attributes.__file__))
    for name in imports:
        assert not name.startswith(_FORBIDDEN_MODULE_PREFIXES), f"禁止的依赖: {name}"


def test_attribute_dict_has_no_module_level_calls() -> None:
    # 模块级无调用 => import 期不读文件、不连数据库、不打网络
    path = Path(attribute_dict.__file__)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    calls = [node for node in tree.body if isinstance(node, ast.Call)]
    assert calls == [], "attribute_dict 模块级出现了调用（可能有副作用）"


# ---------------------------------------------------------------------------
# 7. 战斗机族（T-06 实测、T-07 补登 12 个常量）
#
# 覆盖：
# 1. 12 个新常量的 ID 与 T-06 实测层次一致，且都进了 ATTRIBUTES；
# 2. 5 个正 ID 与 SDE 索引库的 `name` 逐字一致（**防串号的关键断言**）；
# 3. 7 个派生负 ID 仍不在 SDE 属性表里（provenance：负 ID 查不到名字）；
# 4. `audit()` 仍 `consistent=True`，且 `constant_count` 恰好比补登前多 12。
#
# 值唯一 / 为整数由上文 `test_every_constant_value_is_unique` /
# `test_every_constant_is_an_int` 全表覆盖，本节不重复实现。
# ---------------------------------------------------------------------------

# 常量名 -> 期望 ID（ID 来源见 PROGRESS §3 第 39–42 条与 T-06 实测）。
FIGHTER_CONSTANTS: dict[str, int] = {
    "ATTR_FIGHTER_ABILITY_ATTACK_DPS": -55,
    "ATTR_FIGHTER_ABILITY_MISSILES_DPS": -56,
    "ATTR_FIGHTER_BAY_USED": -57,
    "ATTR_HEAVY_FIGHTER_SLOTS_USED": -59,
    "ATTR_LIGHT_FIGHTER_SLOTS_USED": -60,
    "ATTR_SUPPORT_FIGHTER_SLOTS_USED": -61,
    "ATTR_FIGHTER_TUBES_USED": -62,
    "ATTR_FIGHTER_SQUADRON_MAX_SIZE": 2215,
    "ATTR_FIGHTER_TUBES": 2216,
    "ATTR_LIGHT_FIGHTER_SLOTS": 2217,
    "ATTR_SUPPORT_FIGHTER_SLOTS": 2218,
    "ATTR_HEAVY_FIGHTER_SLOTS": 2219,
}

# 5 个正向船体属性在 SDE 索引库里的真名（`Index.attribute(id)["name"]` 实测）。
FIGHTER_POSITIVE_NAMES: dict[int, str] = {
    2215: "fighterSquadronMaxSize",
    2216: "fighterTubes",
    2217: "fighterLightSlots",
    2218: "fighterSupportSlots",
    2219: "fighterHeavySlots",
}

BASELINE_CONSTANT_COUNT = 129
"""T-07 补登前的 `ATTR_*` 常量个数（PROGRESS §3 / P0-3 台账：129 常量 + 8 项缩放）。"""

T07_ADDED_CONSTANT_COUNT = 12
"""T-07 一次补进 7 个派生负 ID + 5 个船体正 ID。"""

FIGHTER_UNITS = {"hp/s", "m3", "count"}
"""战斗机族只允许这三种物理单位（DPS / 机库体积 / 件数）。"""


def test_fighter_constants_hold_the_measured_ids() -> None:
    for name, expected in FIGHTER_CONSTANTS.items():
        got = getattr(attributes, name)
        assert got == expected, f"{name} 期望 {expected}，实际 {got}"
        assert isinstance(got, int) and not isinstance(got, bool)


def test_fighter_constants_are_split_into_negative_and_positive() -> None:
    # 派生占用侧（-55..-62）与船体上限侧（2215..2219）不能混：混了就是整组串号
    derived = {name: value for name, value in FIGHTER_CONSTANTS.items() if value < 0}
    hull = {name: value for name, value in FIGHTER_CONSTANTS.items() if value > 0}
    assert len(derived) == 7, f"派生负 ID 应为 7 个，实际 {sorted(derived.items())}"
    assert len(hull) == 5, f"船体正 ID 应为 5 个，实际 {sorted(hull.items())}"


def test_every_fighter_constant_has_a_named_metadata_row() -> None:
    for name, attr_id in FIGHTER_CONSTANTS.items():
        assert attr_id in attributes.ATTRIBUTES, f"{name}={attr_id} 没有元数据行（audit 会报孤儿常量）"
        canon_name, unit, unit_id = attributes.ATTRIBUTES[attr_id]
        assert canon_name and not canon_name.startswith("attr_"), f"{attr_id} 缺少真实属性名"
        assert unit in FIGHTER_UNITS, f"{attr_id} 的单位 {unit!r} 不在 {sorted(FIGHTER_UNITS)} 里"
        assert unit_id is None or isinstance(unit_id, int), f"{attr_id} 的 unit_id 类型不对"


def test_fighter_dps_ids_use_hp_per_second() -> None:
    # DPS 类必须是 hp/s：写成 "hp" 或 "x" 会让下游把速率当存量
    for attr_id in (
        attributes.ATTR_FIGHTER_ABILITY_ATTACK_DPS,
        attributes.ATTR_FIGHTER_ABILITY_MISSILES_DPS,
        attributes.ATTR_FIGHTER_DPS,
    ):
        assert attribute_dict.unit_of(attr_id) == "hp/s", f"{attr_id} 的单位应为 hp/s"
    # 机库占用是体积，不是架数也不是带宽
    assert attribute_dict.unit_of(attributes.ATTR_FIGHTER_BAY_USED) == "m3"
    assert attribute_dict.unit_id_of(attributes.ATTR_FIGHTER_BAY_USED) == 9  # Volume


def test_fighter_derived_ids_have_semantic_names() -> None:
    # 派生负 ID 取不到 SDE 名字，但常量表必须给出语义名（不能是 attr_-62）
    for attr_id in (
        attributes.ATTR_FIGHTER_ABILITY_ATTACK_DPS,
        attributes.ATTR_FIGHTER_ABILITY_MISSILES_DPS,
        attributes.ATTR_FIGHTER_BAY_USED,
        attributes.ATTR_HEAVY_FIGHTER_SLOTS_USED,
        attributes.ATTR_LIGHT_FIGHTER_SLOTS_USED,
        attributes.ATTR_SUPPORT_FIGHTER_SLOTS_USED,
        attributes.ATTR_FIGHTER_TUBES_USED,
    ):
        assert not attribute_dict.name_of(attr_id).startswith("attr_"), (
            f"派生负 ID {attr_id} 没有登记语义名"
        )


def test_fighter_positive_ids_match_the_index_names() -> None:
    """5 个正 ID 的**真名**必须与索引库一致 —— 这是防串号的关键断言。

    索引库不可用（未 ingest / 路径缺失）时 `pytest.skip`，不把环境问题当失败；
    但只要能打开就必须逐字对上，否则说明常量表写错了 ID 或名字。
    """
    from noobbaozi.config.paths import db_path
    from noobbaozi.sde.index import Index

    try:
        index = Index.open(db_path())
    except Exception as exc:  # noqa: BLE001 —— 索引库缺失是环境问题，跳过而非失败
        pytest.skip(f"SDE 索引库不可用，跳过防串号对拍：{exc}")

    try:
        for attr_id, expected_name in FIGHTER_POSITIVE_NAMES.items():
            row = index.attribute(attr_id)
            assert row is not None, f"{attr_id} 不在 SDE 属性表里（SDE 版本与常量表不一致？）"
            assert row["name"] == expected_name, (
                f"{attr_id} 在索引库里叫 {row['name']!r}，常量表写的是 {expected_name!r}（串号？）"
            )
            # 常量表的 canon 名必须与索引库同名（audit 不查这条，靠这里兜住）
            assert attribute_dict.name_of(attr_id) == expected_name
            # ESI/SDE 未给单位：留 None，不猜（红线 N4）
            assert row["unit"] is None, f"{attr_id} 的 SDE 单位是 {row['unit']!r}，常量表应同步"
            assert attribute_dict.unit_id_of(attr_id) is None
    finally:
        index.close()


def test_fighter_derived_ids_stay_out_of_the_sde_attribute_table() -> None:
    """负 ID 属引擎 pass 4 派生，SDE 里查不到 —— 若"补"出来了说明有人越界改索引。"""
    from noobbaozi.config.paths import db_path
    from noobbaozi.sde.index import Index

    try:
        index = Index.open(db_path())
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"SDE 索引库不可用，跳过 provenance 检查：{exc}")

    try:
        for attr_id in (
            attributes.ATTR_FIGHTER_ABILITY_ATTACK_DPS,
            attributes.ATTR_FIGHTER_ABILITY_MISSILES_DPS,
            attributes.ATTR_FIGHTER_BAY_USED,
            attributes.ATTR_HEAVY_FIGHTER_SLOTS_USED,
            attributes.ATTR_LIGHT_FIGHTER_SLOTS_USED,
            attributes.ATTR_SUPPORT_FIGHTER_SLOTS_USED,
            attributes.ATTR_FIGHTER_TUBES_USED,
            attributes.ATTR_FIGHTER_DPS,
        ):
            assert index.attribute(attr_id) is None, (
                f"派生负 ID {attr_id} 竟然出现在 SDE 属性表里（provenance 被破坏）"
            )
    finally:
        index.close()


def test_audit_constant_count_grew_by_exactly_the_new_fighter_ids() -> None:
    report = attribute_dict.audit()
    assert report["consistent"] is True
    assert report["attribute_count"] == report["constant_count"]
    # 常量总数**由表推导**（不再硬编码数字）：自洽 + 显式增量台账，见第 10 节。
    assert_constant_count_is_derived_from_the_table()
    # T-07 补的全是"不缩放"的项；T-13 / T-23 之后缩放表 = 9 毫秒 + 2 ×1000
    assert report["scaled_count"] == 11
    assert report["milli_count"] == 9
    assert report["kilo_count"] == 2


# ---------------------------------------------------------------------------
# 8. T-13：补 3 个属性常量（1272 / 56 / 73）+ 修正 `-3 derivedCycleTime` 的单位标注
#
# 覆盖：
# 1. 三个新常量值唯一、为整数、都有元数据行（值唯一/整数由第一节全表覆盖，本节再显式钉一次）；
# 2. 三个 **正 ID** 的名字与 SDE 索引库逐字一致（`Index.attribute()` 实测），并核对
#    `unit_id` 与单位标签（1272 → 128 / 73 → 101 / 56 → None）；
# 3. `73`、`-3` 都进缩放表（毫秒）：`to_physical(…, 4800.0) == 4.8`；
# 4. `-3` 的 unit 列是**物理**单位 `s`（存储毫秒 ÷1000 之后），unit_id 留空（派生负 ID
#    查不到 ESI 单位），且它现在是"已缩放"的；
# 5. `audit()` 仍 `consistent=True`，常量数 +3、缩放项 8 → 10（毫秒 6 → 8）。
# ---------------------------------------------------------------------------

#: T-13 补登的 3 个常量 ↔ ID（名称以 SDE 索引库为准，见下方对拍测试）。
T13_CONSTANTS: dict[str, int] = {
    "ATTR_DRONE_BANDWIDTH_USED": 1272,
    "ATTR_CHARGE_RATE": 56,
    "ATTR_MODULE_DURATION": 73,
}

#: T-13 后常量总数：T-07 的 141（129 基线 + 12 战斗机族）再 +3。
T13_ADDED_CONSTANT_COUNT = 3
T13_TOTAL_CONSTANT_COUNT = BASELINE_CONSTANT_COUNT + T07_ADDED_CONSTANT_COUNT + T13_ADDED_CONSTANT_COUNT

#: 正 ID 在索引库里的真名与 `unit_id`（2026-10-18 实测，SDE build 3607705）。
#: 注意 `73 duration` 的 ESI `unit_id = 101`（Milliseconds），与 `51 speed` 同族；
#: `56 chargeRate` 的 `unit_id` 为 `None`，故常量表单位只能是"件/循环"。
T13_INDEX_FACTS: dict[int, tuple[str, int | None]] = {
    1272: ("droneBandwidthUsed", 128),  # Drone bandwidth（与 1271 同 unit）
    56: ("chargeRate", None),           # Charges Per Cycle（ESI 未给单位）
    73: ("duration", 101),              # Activation time / duration（Milliseconds）
}

#: 每循环消耗数（件）——ESU 未给单位，语义上是件数，故用已确立的 `count` 标签。
CHARGE_RATE_UNIT = "count"


def test_t13_constants_are_unique_ints_with_metadata_rows() -> None:
    values = [getattr(attributes, name) for name in T13_CONSTANTS]
    for name, expected in T13_CONSTANTS.items():
        got = getattr(attributes, name)
        assert got == expected, f"{name} 期望 {expected}，实际 {got}"
        assert isinstance(got, int) and not isinstance(got, bool), f"{name} 不是整数"
        assert got in attributes.ATTRIBUTES, f"{name}={got} 没有元数据行（audit 会报孤儿常量）"
    # 三个新值彼此不撞，且各自在全表里只出现一次（撞值 = 复制粘贴写错）
    assert len(set(values)) == T13_ADDED_CONSTANT_COUNT
    all_values = [getattr(attributes, name) for name in CONSTANT_NAMES]
    for value in values:
        assert all_values.count(value) == 1, f"{value} 在常量表里出现了 {all_values.count(value)} 次"


def test_t13_new_ids_match_the_index_names() -> None:
    """三个正 ID 的**真名 / unit_id** 必须与索引库一致 —— 防串号的关键断言。

    索引库不可用（未 ingest / 路径缺失）时 `pytest.skip`，不把环境问题当失败。
    """
    from noobbaozi.config.paths import db_path
    from noobbaozi.sde.index import Index

    try:
        index = Index.open(db_path())
    except Exception as exc:  # noqa: BLE001 —— 索引缺失是环境问题，跳过而非失败
        pytest.skip(f"SDE 索引库不可用，跳过 T-13 防串号对拍：{exc}")

    try:
        for attr_id, (expected_name, expected_unit_id) in T13_INDEX_FACTS.items():
            row = index.attribute(attr_id)
            assert row is not None, f"{attr_id} 不在 SDE 属性表里（SDE 版本漂移？）"
            assert row["name"] == expected_name, (
                f"{attr_id} 在索引库里叫 {row['name']!r}，期望 {expected_name!r}（串号？）"
            )
            # 常量表的 canon 名必须与索引库同名（audit 只查计数，不查这条）
            assert attribute_dict.name_of(attr_id) == expected_name
            # unit_id 是 ESI 实测值；单位标签由 `UNIT_LABELS` 归纳（audit 交叉校验）
            assert attribute_dict.unit_id_of(attr_id) == expected_unit_id, (
                f"{attr_id} 的 unit_id 期望 {expected_unit_id}，实际 {attribute_dict.unit_id_of(attr_id)}"
            )
    finally:
        index.close()


def test_t13_units_follow_the_physical_unit_convention() -> None:
    # 1272 与 1271 同族（Mbit/s）；73 存储毫秒 → 物理秒；56 是件数
    assert attribute_dict.unit_of(attributes.ATTR_DRONE_BANDWIDTH_USED) == "Mbit/s"
    assert attribute_dict.unit_of(attributes.ATTR_MODULE_DURATION) == "s"
    assert attribute_dict.unit_of(attributes.ATTR_CHARGE_RATE) == CHARGE_RATE_UNIT
    # 单位与 unit_id 推出的标签必须一致（audit 会报 mismatch，这里显式钉住）
    assert attributes.UNIT_LABELS[101] == "s"
    assert attributes.UNIT_LABELS[128] == "Mbit/s"


def test_t13_module_duration_is_milliseconds() -> None:
    # T-13 实测：Small Ancillary Armor Repairer 的 duration=4800 → 4.8 s
    got = attribute_dict.to_physical(attributes.ATTR_MODULE_DURATION, 4800.0)
    assert isinstance(got, float)
    assert _close(got, 4.8), got
    assert attribute_dict.is_scaled(attributes.ATTR_MODULE_DURATION) is True
    assert math.isclose(
        attribute_dict.scale_of(attributes.ATTR_MODULE_DURATION),
        attributes.SCALE_MILLI,
        rel_tol=REL_TOL,
    )


def test_t13_derived_cycle_time_is_milliseconds_not_seconds() -> None:
    """`-3 derivedCycleTime`：T-10 硬结论 65 —— 装备级是毫秒，常量表原先标 `s` 且未缩放。

    unit 列是**换算之后**的物理单位，所以它仍是 `s`；错的是"标了 `s` 却没进缩放表"，
    修法是补 `SCALE_BY_ATTR`（本轮）+ 说明存储毫秒（注释）。
    """
    assert attribute_dict.unit_of(attributes.ATTR_DERIVED_CYCLE_TIME) == "s"  # 物理单位：秒
    assert attribute_dict.unit_id_of(attributes.ATTR_DERIVED_CYCLE_TIME) is None  # 派生负 ID 无 ESI 单位
    assert attribute_dict.is_scaled(attributes.ATTR_DERIVED_CYCLE_TIME) is True
    got = attribute_dict.to_physical(attributes.ATTR_DERIVED_CYCLE_TIME, 4800.0)
    assert _close(got, 4.8), got
    # 与 `51 speed` 同口径：两者对同一原始值给出同一物理值
    assert _exactly(got, attribute_dict.to_physical(attributes.ATTR_CYCLE_TIME, 4800.0))


def test_t13_scale_table_grew_by_two_millisecond_entries() -> None:
    milli = {
        attr_id
        for attr_id, scale in attributes.SCALE_BY_ATTR.items()
        if math.isclose(scale, attributes.SCALE_MILLI, rel_tol=REL_TOL)
    }
    # 6 → 8（T-13 新增 73 / -3）；T-23 再 +281 → 9
    assert len(milli) == 9, f"毫秒项应为 9，实际 {len(milli)}：{sorted(milli)}"
    assert attributes.ATTR_MODULE_DURATION in milli
    assert attributes.ATTR_DERIVED_CYCLE_TIME in milli
    # 两个新项都必须在元数据表里（否则 audit 的 unknown_scale_values / 孤儿检查会漏）
    assert attributes.ATTR_MODULE_DURATION in attributes.ATTRIBUTES
    assert attributes.ATTR_DERIVED_CYCLE_TIME in attributes.ATTRIBUTES
    assert len(attributes.SCALE_BY_ATTR) == 11  # T-13 的 10 + T-23 的 281


def test_t13_audit_counts_and_consistency() -> None:
    report = attribute_dict.audit()
    assert report["consistent"] is True
    # 常量总数由表推导（T-32 起不再写 `== 146`：那个数字每补一个常量就要改三处）
    assert_constant_count_is_derived_from_the_table()
    assert report["attribute_count"] == report["constant_count"]
    # T-13 增量：常量 +3（1272 / 56 / 73）、缩放 +2（73 / -3）；T-23 再 +1 缩放（281）
    assert report["scaled_count"] == 11
    assert report["milli_count"] == 9
    assert report["kilo_count"] == 2
    assert report["duplicate_values"] == []
    assert report["orphan_constants"] == []
    assert report["orphan_rows"] == []
    assert report["unknown_scale_values"] == []


# ---------------------------------------------------------------------------
# 9. T-23：补 2 个属性常量（1367 `maxSubSystems` / 281 `explosionDelay`）
#
# 覆盖：
# 1. 两个常量值唯一、为整数、都有元数据行；
# 2. **正 ID 的真名 / `unit_id` 与 SDE 索引库逐字对拍**（1367 → maxSubSystems / None；
#    281 → explosionDelay / 101 = Milliseconds），不符即失败（防串号）；
# 3. `281` 必须进 `SCALE_BY_ATTR`（毫秒）：`to_physical(281, 7000.0) == 7.0`；
#    1367 是件数，**不得**进缩放表（槽位总量不需要换算）；
# 4. 语义锚：displayName 分别是 `Subsystem Slots` / `Maximum Flight Time`（索引库实测）。
# ---------------------------------------------------------------------------

#: T-23 补登的 2 个常量 ↔ ID（名称以 SDE 索引库为准，见下方对拍测试）。
T23_CONSTANTS: dict[str, int] = {
    "ATTR_MAX_SUBSYSTEMS": 1367,
    "ATTR_EXPLOSION_DELAY": 281,
}

T23_ADDED_CONSTANT_COUNT = 2
"""T-23 补进 1367 / 281 两个常量（146 = 144 + 2）。"""

#: 正 ID 在索引库里的真名 / `unit_id` / displayName（2026-10-18 实测，SDE build 3607705）。
T23_INDEX_FACTS: dict[int, tuple[str, int | None, str]] = {
    1367: ("maxSubSystems", None, "Subsystem Slots"),
    281: ("explosionDelay", 101, "Maximum Flight Time"),
}


def test_t23_constants_are_unique_ints_with_metadata_rows() -> None:
    values = [getattr(attributes, name) for name in T23_CONSTANTS]
    for name, expected in T23_CONSTANTS.items():
        got = getattr(attributes, name)
        assert got == expected, f"{name} 期望 {expected}，实际 {got}"
        assert isinstance(got, int) and not isinstance(got, bool), f"{name} 不是整数"
        assert got in attributes.ATTRIBUTES, f"{name}={got} 没有元数据行（audit 会报孤儿常量）"
    assert len(set(values)) == T23_ADDED_CONSTANT_COUNT
    all_values = [getattr(attributes, name) for name in CONSTANT_NAMES]
    for value in values:
        assert all_values.count(value) == 1, f"{value} 在常量表里出现了 {all_values.count(value)} 次"


def test_t23_new_ids_match_the_index_names_and_units() -> None:
    """1367 / 281 的**真名 / `unit_id` / displayName** 必须与索引库一致（防串号）。

    索引库不可用（未 ingest / 路径缺失）时 `pytest.skip`，不把环境问题当失败。
    """
    from noobbaozi.config.paths import db_path
    from noobbaozi.sde.index import Index

    try:
        index = Index.open(db_path())
    except Exception as exc:  # noqa: BLE001 —— 索引缺失是环境问题，跳过而非失败
        pytest.skip(f"SDE 索引库不可用，跳过 T-23 防串号对拍：{exc}")

    try:
        for attr_id, (expected_name, expected_unit_id, expected_display) in T23_INDEX_FACTS.items():
            row = index.attribute(attr_id)
            assert row is not None, f"{attr_id} 不在 SDE 属性表里（SDE 版本漂移？）"
            assert row["name"] == expected_name, (
                f"{attr_id} 在索引库里叫 {row['name']!r}，期望 {expected_name!r}（串号？）"
            )
            assert row["display_name"] == expected_display
            # 常量表的 canon 名必须与索引库同名
            assert attribute_dict.name_of(attr_id) == expected_name
            assert attribute_dict.unit_id_of(attr_id) == expected_unit_id, (
                f"{attr_id} 的 unit_id 期望 {expected_unit_id}，实际 {attribute_dict.unit_id_of(attr_id)}"
            )
    finally:
        index.close()


def test_t23_units_follow_the_physical_unit_convention() -> None:
    # 1367 = 槽位件数（unit_id None，沿用 count 标签）；281 = 存储毫秒 → 物理秒
    assert attribute_dict.unit_of(attributes.ATTR_MAX_SUBSYSTEMS) == "count"
    assert attribute_dict.unit_of(attributes.ATTR_EXPLOSION_DELAY) == "s"
    assert attributes.UNIT_LABELS[101] == "s"


def test_t23_explosion_delay_is_milliseconds_and_subsystems_are_not_scaled() -> None:
    # 281：Scourge Light Missile 实测 5000 ms → 5 s（导弹射程 = 37 × 该秒数）
    got = attribute_dict.to_physical(attributes.ATTR_EXPLOSION_DELAY, 7000.0)
    assert isinstance(got, float)
    assert _close(got, 7.0), got
    assert attribute_dict.is_scaled(attributes.ATTR_EXPLOSION_DELAY) is True
    assert math.isclose(
        attribute_dict.scale_of(attributes.ATTR_EXPLOSION_DELAY),
        attributes.SCALE_MILLI,
        rel_tol=REL_TOL,
    )
    # 1367 是槽位数（Loki = 5），**不得**缩放 —— 缩放会让槽位变成 0.005
    assert attribute_dict.is_scaled(attributes.ATTR_MAX_SUBSYSTEMS) is False
    assert _exactly(attribute_dict.to_physical(attributes.ATTR_MAX_SUBSYSTEMS, 5), 5.0)


def test_t23_audit_counts_and_consistency() -> None:
    report = attribute_dict.audit()
    assert report["consistent"] is True
    # 常量总数由表推导（T-32 起不再写 `== 146`）
    assert_constant_count_is_derived_from_the_table()
    assert report["scaled_count"] == 11
    assert report["milli_count"] == 9
    assert report["kilo_count"] == 2
    assert report["unit_label_mismatches"] == []
    assert report["duplicate_values"] == []
    assert report["orphan_constants"] == []
    assert report["orphan_rows"] == []


# ---------------------------------------------------------------------------
# 10. T-32：补 1 个常量（-70 `maxTargets`）+ **常量总数改由表推导**
#
# 为什么不再硬编码数字（PROGRESS §3 第 201 条）：此前有三处断言写着「常量数 = 146」，
# 于是「补常量」与「守护测试」互相卡住 —— 补一个常量要同时改三处，忘一处就红；那正是
# `-70` 迁移被挡住整整两轮的直接原因。现在改成两条互补的断言：
#
# ① **自洽**：`audit()` 的计数 == `len(CONSTANT_NAMES)` == `len(ATTRIBUTES)`（表推导）；
# ② **显式增量台账**：`CONSTANT_COUNT_LEDGER` 把每轮补登一条条写出来（可审计的增量说明）。
#
# 补常量时只需在台账里加一行，断言自动跟着走；**忘了加**会让总数对不上 —— 那正是我们要的
# （「多出来的常量」不该被静默接受）。
# ---------------------------------------------------------------------------

T32_CONSTANTS: dict[str, int] = {"ATTR_MAX_TARGETS": -70}
"""T-32 补登的 1 个常量：`-70 maxTargets`（**派生，SDE 无记录**；`Index.attribute(-70)` = None）。"""

T32_ADDED_CONSTANT_COUNT = 1
"""T-32 补进 `-70` 一个常量（147 = 146 + 1）。"""

P6_ADDED_CONSTANTS: dict[str, int] = {"ATTR_AOE_DAMAGE_REDUCTION_FACTOR": 1353}
"""P6 交战层补登的 1 个常量：`1353 aoeDamageReductionFactor`（导弹 DRF，伤害应用指数）。"""

P6_ADDED_CONSTANT_COUNT = 1
"""P6 补进 `1353` 一个常量（148 = 147 + 1）。"""

CONSTANT_COUNT_LEDGER = (
    BASELINE_CONSTANT_COUNT  # 129  P0-3 台账
    + T07_ADDED_CONSTANT_COUNT  # +12 战斗机族（T-07）
    + T13_ADDED_CONSTANT_COUNT  # +3  droneBandwidthUsed / chargeRate / duration（T-13）
    + T23_ADDED_CONSTANT_COUNT  # +2  maxSubSystems / explosionDelay（T-23）
    + T32_ADDED_CONSTANT_COUNT  # +1  maxTargets（T-32）
    + P6_ADDED_CONSTANT_COUNT  # +1  aoeDamageReductionFactor（P6 交战层）
)
"""各轮补登的**显式增量台账**（129 → 141 → 144 → 146 → 147 → 148）。

这条链是「常量总数」的**唯一**书面依据：数字不再散落在三处断言里，而是集中在台账 + 表。
"""


def assert_constant_count_is_derived_from_the_table() -> None:
    """三项断言：表自身自洽（不写死数字）、`audit()` 与表一致、总数 == 增量台账。

    抽成函数供三处引用（T-07 / T-13 / T-23 的计数测试），避免又出现「同一个数字抄三份」。
    """
    report = attribute_dict.audit()

    # ① 表自洽：常量名 → 值 → 元数据行三方一致（其余用例单独钉，这里只钉计数口径）
    assert report["constant_count"] == len(CONSTANT_NAMES) > 0
    assert report["attribute_count"] == len(attributes.ATTRIBUTES)
    assert report["constant_count"] == report["attribute_count"]
    assert report["consistent"] is True

    # ② 增量台账：总数必须与「基线 + 各轮补登」逐项对上（补了却忘了记账 → 这里红）
    assert report["constant_count"] == CONSTANT_COUNT_LEDGER, (
        f"常量数 {report['constant_count']} != 台账 {CONSTANT_COUNT_LEDGER}："
        "补常量后请在 CONSTANT_COUNT_LEDGER 里加一行增量（别改断言）"
    )


def test_constant_count_is_derived_from_the_table() -> None:
    """常量总数**不再硬编码**：改由「表自洽 + 增量台账」两条断言共同决定。"""
    assert_constant_count_is_derived_from_the_table()
    # 台账本身也要有非空转前置（否则等式两边都可能是同一个写死的数字）
    assert BASELINE_CONSTANT_COUNT >= 100
    assert CONSTANT_COUNT_LEDGER == 148, f"台账落点应为 148，实为 {CONSTANT_COUNT_LEDGER}"


def test_constant_count_guard_is_not_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """**变异自检**：多出一行没有常量名的元数据时，上面那条自洽断言必须变红。

    没有这条，「常量数由表推导」可能只是把 146 换成了一个永远成立的同义反复。
    """
    monkeypatch.setitem(attributes.ATTRIBUTES, 987654, ("mutationProbe", "x", None))

    with pytest.raises(AssertionError):
        assert_constant_count_is_derived_from_the_table()


def test_t32_max_targets_constant_is_registered() -> None:
    """`-70 maxTargets` 已迁入常量表：值 / 元数据行 / 语义名 / 不缩放，四条都要对上。"""
    assert attributes.ATTR_MAX_TARGETS == T32_CONSTANTS["ATTR_MAX_TARGETS"] == -70
    assert attributes.ATTR_MAX_TARGETS in attributes.ATTRIBUTES, "没有元数据行（audit 会报孤儿常量）"

    name, unit, unit_id = attributes.ATTRIBUTES[attributes.ATTR_MAX_TARGETS]
    assert name == "maxTargets", f"语义名应为 maxTargets，实为 {name!r}"
    assert unit == "count"  # 与同族 maxLockedTargets(192) 同口径
    assert unit_id is None, "派生负 ID 没有 ESI unit_id（「SDE 无记录」，不猜）"
    # 真的登记了：名字不是兜底名 `attr_-70`
    assert attribute_dict.name_of(-70) == "maxTargets"
    assert attribute_dict.unit_of(-70) == "count"
    # 目标个数不需要换算：不得进缩放表（进了会把 8 变成 0.008）
    assert attribute_dict.is_scaled(attributes.ATTR_MAX_TARGETS) is False
    assert _exactly(attribute_dict.to_physical(attributes.ATTR_MAX_TARGETS, 8), 8.0)


def test_t32_max_targets_is_a_derived_id_absent_from_the_sde() -> None:
    """`Index.attribute(-70)` = None —— 这就是「派生，SDE 无记录」的实证（也是它要单独登记的原因）。"""
    from noobbaozi.config.paths import db_path
    from noobbaozi.sde.index import Index

    try:
        index = Index.open(db_path())
    except Exception as exc:  # noqa: BLE001 —— 索引库缺失是环境问题，跳过而非失败
        pytest.skip(f"SDE 索引库不可用，跳过派生 ID 取证：{exc}")

    try:
        assert index.attribute(attributes.ATTR_MAX_TARGETS) is None, (
            "-70 竟然出现在 SDE 属性表里：那么它不该被当成「派生负 ID」登记"
        )
        # 对照组：正 ID 查得到（证明上面那条不是「索引库打不开」造成的假绿）
        assert index.attribute(attributes.ATTR_MAX_LOCKED_TARGETS) is not None
    finally:
        index.close()


def test_t32_provenance_side_is_pinned() -> None:
    """补登之后 provenance 侧的三条事实：`-70` 仍 unknown、仍是手工覆盖、不再有未登记项。"""
    from noobbaozi.project import provenance as P

    assert P.tag(attributes.ATTR_MAX_TARGETS) == "unknown"
    assert attributes.ATTR_MAX_TARGETS in P._MANUAL_OVERRIDES
    audit = P.audit()
    assert audit["unregistered"] == [], (
        f"常量表已登记，不该再有「未登记」的 unknown 项：{audit['unregistered']}"
    )
    assert audit["consistent"] is True

