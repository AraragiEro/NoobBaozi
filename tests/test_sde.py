"""T2/T3 契约测试：SDE 索引库（P0 数据层）。

覆盖（任务 T-01 验收项）：

1. 索引能打开、`stats()` 数字合理（types > 50000、attributes > 2000）；
2. `search_types("Hecate")` 能命中 Hecate = 35683，且精确命中排在第一位；
3. `types_in_group(1306)` 非空；
4. `modes_for_ship(35683)` 恰好 3 个，名字含 Defense / Propulsion / Sharpshooter；
   `modes_for_ship(34317)`（Confessor）同样恰好 3 个；
5. 单位表（`units`）存在且非空；
6. 契约细节：`get_type` / `attribute` 的命中与缺失、`close()` 幂等、线程安全。

前置：索引库必须**已经构建好**（建库是 `sde/ingest.py` 的职责）：

    python -c "from evefit.sde.ingest import build_index; build_index()"

库不存在 → 整模块 `pytest.skip`。本测试**永不触网、永不重建索引**（T2 只吃固定产物）。
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from evefit.config import attributes as A
from evefit.config.paths import db_path
from evefit.sde.index import DEFAULT_SCALE, Index

# ---- 实测基准（build 3607705，见 PROGRESS.md §3）----

MODE_GROUP_ID = 1306
HECATE_TYPE_ID = 35683
CONFESSOR_TYPE_ID = 34317
#: Svipul（T3D）—— 与 Confessor 同组；T-23 起专门钉住「非 Hecate 的 T3D 也有模式」。
SVIPUL_TYPE_ID = 34562
#: Hecate 的三个模式 type（快照：与 SDE build 绑定，改了要按规范 §6.2 走 changelog）。
HECATE_MODE_IDS = (35686, 35687, 35688)
HECATE_MODE_TOKENS = ("Defense", "Propulsion", "Sharpshooter")
#: 实测 group 1306 成员数（快照）。
MODE_GROUP_MEMBER_COUNT = 18
#: 属性样本：552 signatureRadius（unit=Length，high_is_good=False，有 displayName）。
ATTR_SIGNATURE_RADIUS = 552
#: 属性样本：79 scanSpeed（无 displayName → 回退 name；unit=Milliseconds）。
ATTR_SCAN_SPEED = 79
#: T-23 登记的两个常量（值必须与 `config/attributes.py` 一致；这里直接用常量，不写裸数字）。
ATTR_MAX_SUBSYSTEMS = A.ATTR_MAX_SUBSYSTEMS  # 1367 maxSubSystems
ATTR_EXPLOSION_DELAY = A.ATTR_EXPLOSION_DELAY  # 281 explosionDelay（存储毫秒）
#: dogmaUnits 里 101 = Milliseconds（displayName 写的是 s）。
UNIT_MILLISECONDS = 101


def _resolve_db() -> Path:
    try:
        return db_path()
    except FileNotFoundError as exc:  # 索引库没建：跳过而不是失败
        pytest.skip(f"索引库未构建，跳过 SDE 集成测试：{exc}")
        raise  # pragma: no cover - pytest.skip 已抛异常


@pytest.fixture(scope="module")
def ix() -> Iterator[Index]:
    """模块级索引（只读，多测试共用一条库）。"""
    index = Index.open(_resolve_db())
    try:
        yield index
    finally:
        index.close()


@pytest.fixture(scope="module")
def raw_conn(ix: Index) -> Iterator[sqlite3.Connection]:
    """直连索引库，用于检查 `Index` 契约没暴露的表（如 `units`）。"""
    conn = sqlite3.connect(f"{ix.path.as_uri()}?mode=ro", uri=True)
    try:
        yield conn
    finally:
        conn.close()


# ---- 1. 能打开 + stats 合理 --------------------------------------------------


def test_open_returns_index_bound_to_existing_file(ix: Index) -> None:
    assert ix.path.is_file()
    assert ix.path.suffix == ".sqlite3"


def test_schema_has_all_required_tables(raw_conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in raw_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"meta", "types", "groups", "categories", "attributes", "units", "modes"} <= tables


def test_stats_are_plausible(ix: Index) -> None:
    stats = ix.stats()
    # 实测 build 3607705：types 53,288 / groups 1,611 / attributes 2,871 / modes 18
    assert stats["types"] > 50_000, stats
    assert stats["attributes"] > 2_000, stats
    assert stats["groups"] > 1_000, stats
    assert stats["modes"] > 0, stats
    assert stats["sde_build"] > 0, stats


def test_meta_table_carries_build_and_timestamp(raw_conn: sqlite3.Connection) -> None:
    meta = dict(raw_conn.execute("SELECT key, value FROM meta"))
    assert meta["sde_build"].isdigit()
    assert int(meta["sde_build"]) > 0
    assert meta["generated_at"].endswith("Z")
    assert meta["source_url"].endswith("-jsonl.zip") or "static-data" in meta["source_url"]


def test_meta_build_matches_filename(raw_conn: sqlite3.Connection) -> None:
    build = int(dict(raw_conn.execute("SELECT key, value FROM meta"))["sde_build"])
    assert f"evefit-{build}.sqlite3" in str(db_path(build))


# ---- 2. search_types：精确优先 + 前缀 + 包含，大小写不敏感 --------------------


def test_search_hecate_hits_35683(ix: Index) -> None:
    hits = ix.search_types("Hecate", 10)
    assert hits, "search_types('Hecate') 没有任何命中"
    assert HECATE_TYPE_ID in [hit["type_id"] for hit in hits]


def test_search_exact_match_ranks_first(ix: Index) -> None:
    hits = ix.search_types("Hecate", 10)
    assert hits[0]["type_id"] == HECATE_TYPE_ID, hits
    assert hits[0]["name"] == "Hecate"
    assert hits[0]["published"] is True


def test_search_returns_contract_typed_rows(ix: Index) -> None:
    row = ix.search_types("Hecate", 1)[0]
    assert set(row) == {
        "type_id",
        "name",
        "group_id",
        "group_name",
        "meta_level",
        "published",
        "volume",
    }
    assert isinstance(row["type_id"], int)
    assert isinstance(row["name"], str)
    assert isinstance(row["published"], bool)
    assert isinstance(row["volume"], float)


def test_search_is_case_insensitive(ix: Index) -> None:
    for query in ("hecate", "HECATE", "HeCaTe", "  Hecate  "):
        hits = ix.search_types(query, 3)
        assert hits and hits[0]["type_id"] == HECATE_TYPE_ID, (query, hits)


def test_search_prefix_and_contains_both_work(ix: Index) -> None:
    # 前缀：Hecat → 命中 Hecate 及其模式 type
    prefix_hits = ix.search_types("Hecat", 20)
    assert HECATE_TYPE_ID in [hit["type_id"] for hit in prefix_hits]
    # 包含（不是前缀）：ecate
    contains_hits = ix.search_types("ecate", 20)
    assert HECATE_TYPE_ID in [hit["type_id"] for hit in contains_hits]


def test_search_ranks_exact_before_contains(ix: Index) -> None:
    hits = ix.search_types("Hecate", 30)
    assert hits[0]["name"] == "Hecate"
    contains_only = [hit for hit in hits[1:] if hit["name"] != "Hecate"]
    assert contains_only, "预期还有 Hecate 开头的其它类型（如模式 / SKIN）"
    assert all(hit["name"].lower() != "hecate" for hit in contains_only)


def test_search_respects_limit(ix: Index) -> None:
    assert len(ix.search_types("Hecate", 3)) == 3
    assert len(ix.search_types("Hecate", 1)) == 1
    assert ix.search_types("Hecate", 0) == []


def test_search_blank_query_returns_empty(ix: Index) -> None:
    assert ix.search_types("", 10) == []
    assert ix.search_types("   ", 10) == []


def test_search_supports_chinese_names(ix: Index) -> None:
    # SDE 内嵌中文名（实测 35683 zh = 赫卡特级）；中文查询走 name_zh
    hits = ix.search_types("赫卡特", 10)
    assert HECATE_TYPE_ID in [hit["type_id"] for hit in hits], hits


def test_search_escapes_like_wildcards(ix: Index) -> None:
    # `%` 与 `_` 必须按**字面**匹配，不能当通配符（否则 "_" 会命中几乎全表）
    assert ix.search_types("%", 10) == []  # 没有任何类型名里含 '%'
    underscore_hits = ix.search_types("_", 10)
    assert underscore_hits, "预期存在带下划线的类型名（如 Able_PLACEHOLDER）"
    assert all("_" in hit["name"] for hit in underscore_hits), underscore_hits
    # 若 `_` 被当单字符通配符，"Hecate_" 会命中 "Hecate"；按字面匹配则无命中
    assert ix.search_types("Hecate_", 10) == []


def test_search_no_match_returns_empty(ix: Index) -> None:
    assert ix.search_types("NoSuchTypeXyzzy", 10) == []


# ---- 3. get_type / types_in_group -------------------------------------------


def test_get_type_by_id(ix: Index) -> None:
    row = ix.get_type(HECATE_TYPE_ID)
    assert row is not None
    assert row["name"] == "Hecate"
    assert row["published"] is True
    assert row["group_id"] == 1305
    assert row["group_name"]


def test_get_type_missing_returns_none(ix: Index) -> None:
    assert ix.get_type(-1) is None


def test_types_in_group_1306_is_not_empty(ix: Index) -> None:
    members = ix.types_in_group(MODE_GROUP_ID)
    assert members, "group 1306（模式）为空"
    assert len(members) == MODE_GROUP_MEMBER_COUNT, [m["name"] for m in members]
    assert all(member["group_id"] == MODE_GROUP_ID for member in members)
    # 模式 type 的特征：published=false（PROGRESS.md §3 第 3 条）
    assert all(member["published"] is False for member in members)


def test_types_in_group_unknown_group_returns_empty(ix: Index) -> None:
    assert ix.types_in_group(-12345) == []


# ---- 4. modes_for_ship ------------------------------------------------------


def test_modes_for_hecate_is_exactly_three_modes(ix: Index) -> None:
    modes = ix.modes_for_ship(HECATE_TYPE_ID)
    assert len(modes) == 3, [mode["name"] for mode in modes]
    names = [mode["name"] for mode in modes]
    for token in HECATE_MODE_TOKENS:
        assert any(token in name for name in names), (token, names)
    assert tuple(mode["type_id"] for mode in modes) == HECATE_MODE_IDS


def test_modes_for_confessor_is_exactly_three_modes(ix: Index) -> None:
    modes = ix.modes_for_ship(CONFESSOR_TYPE_ID)
    assert len(modes) == 3, [mode["name"] for mode in modes]
    names = [mode["name"] for mode in modes]
    for token in HECATE_MODE_TOKENS:
        assert any(token in name for name in names), (token, names)


def test_modes_for_svipul_is_exactly_three_modes(ix: Index) -> None:
    """Svipul（34562）与 Confessor 同组：**非 Hecate 的 T3D 也必须能查到 3 个模式**。

    T-23 的取证：验收报过「f10/f11 的 modes 缺失 6 条 high」，根因是验收器的
    模式名归一化写死 `"Hecate "`（假缺陷），产品侧从来没断。这条把产品侧钉死。
    """
    modes = ix.modes_for_ship(SVIPUL_TYPE_ID)
    assert len(modes) == 3, [mode["name"] for mode in modes]
    names = [mode["name"] for mode in modes]
    for token in HECATE_MODE_TOKENS:
        assert any(token in name for name in names), (token, names)
    # 每条模式的完整名都必须以**本船名**开头（命名约定反解的产物，不是 Hecate 的残留）
    assert all(name.startswith("Svipul ") for name in names), names


def test_every_mode_ship_resolves_to_its_own_three_modes(ix: Index, raw_conn: sqlite3.Connection) -> None:
    """`modes` 表里的 6 艘船，每艘的 3 个模式都必须**反解到它自己**（不串船）。

    检查三件事，缺一即失败：
    1. 每艘船恰好 3 条，且 `modes_for_ship()` 与表里的 `mode_type_id` 集合一致；
    2. 每条模式的全名以该船名开头、以 ` Mode` 结尾（`_mode_name` 的剥名前提）；
    3. 模式 type 全部躺在 group 1306 且 `published=false`（与 Hecate 同口径）。
    """
    rows = raw_conn.execute(
        "SELECT m.ship_type_id, m.mode_type_id, t.name, t.group_id, t.published"
        "  FROM modes m JOIN types t ON t.type_id = m.mode_type_id"
        " ORDER BY m.ship_type_id, m.mode_type_id"
    ).fetchall()
    assert rows, "modes 表为空（索引库没写模式？）"

    expected: dict[int, set[int]] = {}
    for ship_type_id, mode_type_id, _name, _group_id, _published in rows:
        expected.setdefault(ship_type_id, set()).add(mode_type_id)

    assert len(expected) == 6, sorted(expected)  # Confessor/Svipul/Jackdaw/Hecate/Skua/Anhinga
    for ship_type_id, mode_ids in expected.items():
        ship = ix.get_type(ship_type_id)
        assert ship is not None, f"模式所属船 {ship_type_id} 不在 types 表里"
        assert len(mode_ids) == 3, f"{ship['name']} 的模式数不是 3：{sorted(mode_ids)}"
        got = ix.modes_for_ship(ship_type_id)
        assert {mode["type_id"] for mode in got} == mode_ids, ship["name"]
        for mode in got:
            assert mode["name"].startswith(f"{ship['name']} "), (ship["name"], mode["name"])
            assert mode["name"].endswith(" Mode"), mode["name"]
            assert mode["group_id"] == MODE_GROUP_ID
            assert mode["published"] is False


def test_modes_rows_use_type_row_contract(ix: Index) -> None:
    for mode in ix.modes_for_ship(HECATE_TYPE_ID):
        assert set(mode) == {
            "type_id",
            "name",
            "group_id",
            "group_name",
            "meta_level",
            "published",
            "volume",
        }
        assert mode["group_id"] == MODE_GROUP_ID


def test_modes_for_ship_without_modes_returns_empty(ix: Index) -> None:
    # 35683 之外的一艘普通船（实测 group 6 = 天体，绝无模式）
    assert ix.modes_for_ship(6) == []


def test_modes_table_covers_all_six_mode_ships(raw_conn: sqlite3.Connection) -> None:
    ships = [row[0] for row in raw_conn.execute(
        "SELECT DISTINCT ship_type_id FROM modes ORDER BY ship_type_id"
    )]
    # 实测：Confessor / Svipul / Jackdaw / Hecate / Skua / Anhinga = 6 艘，每艘 3 个模式
    assert len(ships) == 6, ships
    counts = dict(raw_conn.execute(
        "SELECT ship_type_id, COUNT(*) FROM modes GROUP BY ship_type_id"
    ))
    assert set(counts.values()) == {3}, counts


# ---- 5. 单位表与属性字典 ----------------------------------------------------


def test_units_table_exists_and_is_not_empty(raw_conn: sqlite3.Connection) -> None:
    count = raw_conn.execute("SELECT COUNT(*) FROM units").fetchone()[0]
    assert count > 0
    # 实测 dogmaUnits 60 条；101 = Milliseconds（displayName 写 "s"）
    row = raw_conn.execute(
        "SELECT name, display_name FROM units WHERE unit_id = ?", (UNIT_MILLISECONDS,)
    ).fetchone()
    assert row is not None
    assert row[0] == "Milliseconds"
    assert row[1] == "s"


def test_attribute_returns_contract_row(ix: Index) -> None:
    row = ix.attribute(ATTR_SIGNATURE_RADIUS)
    assert row is not None
    assert set(row) == {
        "attribute_id",
        "name",
        "display_name",
        "unit",
        "scale",
        "high_is_good",
    }
    assert row["name"] == "signatureRadius"
    assert row["display_name"] == "Signature Radius"
    assert row["unit"] == "Length"
    assert row["high_is_good"] is False
    # 缩放表归 P0-3（config/attributes.py + sde/attribute_dict.py），索引层恒等
    assert row["scale"] == DEFAULT_SCALE


def test_attribute_falls_back_to_name_when_display_name_missing(ix: Index) -> None:
    row = ix.attribute(ATTR_SCAN_SPEED)
    assert row is not None
    assert row["name"] == "scanSpeed"
    assert row["display_name"] == "scanSpeed"  # SDE 没有 displayName
    assert row["unit"] == "Milliseconds"


def test_attribute_missing_returns_none(ix: Index) -> None:
    assert ix.attribute(10**9) is None


def test_t23_registered_attributes_match_the_index(ix: Index) -> None:
    """T-23 登记的两个常量必须与索引库的**真名 / displayName / 单位 / 缩放**逐项一致。

    - `1367 maxSubSystems`（displayName `Subsystem Slots`，ESI 无 unit_id → unit `None`），
      槽位件数 ⇒ **不缩放**（`scale == DEFAULT_SCALE`）；
    - `281 explosionDelay`（displayName `Maximum Flight Time`，unit `Milliseconds`）⇒
      索引层的 `scale` 必须来自常量表（`SCALE_MILLI`），这是 `281 必须进 SCALE_BY_ATTR`
      这条要求在索引层的可观测证据。
    """
    subsystems = ix.attribute(ATTR_MAX_SUBSYSTEMS)
    assert subsystems is not None
    assert subsystems["name"] == "maxSubSystems"
    assert subsystems["display_name"] == "Subsystem Slots"
    assert subsystems["unit"] is None  # ESI 未给单位；件数语义由常量表标 "count"
    assert subsystems["scale"] == DEFAULT_SCALE  # 不缩放

    delay = ix.attribute(ATTR_EXPLOSION_DELAY)
    assert delay is not None
    assert delay["name"] == "explosionDelay"
    assert delay["display_name"] == "Maximum Flight Time"
    assert delay["unit"] == "Milliseconds"  # 存储毫秒
    assert delay["scale"] == SCALE_MILLI  # ← 常量表登记后的实测证据（未登记时恒 1.0）


def test_units_and_attributes_join_is_consistent(raw_conn: sqlite3.Connection) -> None:
    orphans = raw_conn.execute(
        "SELECT COUNT(*) FROM attributes a WHERE a.unit_id IS NOT NULL"
        " AND a.unit_id NOT IN (SELECT unit_id FROM units)"
    ).fetchone()[0]
    assert orphans == 0


# ---- 6. 生命周期与线程安全 --------------------------------------------------


def test_close_is_idempotent_and_blocks_further_queries() -> None:
    index = Index.open(_resolve_db())
    index.close()
    index.close()  # 幂等
    with pytest.raises(RuntimeError):
        index.get_type(HECATE_TYPE_ID)


def test_open_missing_path_raises_file_not_found() -> None:
    # 不用 tmp_path：沙箱下 pytest 的临时目录不可扫描（WinError 5）。
    missing = _resolve_db().with_name("evefit-does-not-exist.sqlite3")
    assert not missing.exists()
    with pytest.raises(FileNotFoundError):
        Index.open(missing)


def test_index_is_usable_from_multiple_threads(ix: Index) -> None:
    queries = ["Hecate", "Confessor", "Svipul", "Jackdaw", "Thorax", "Rifter", "Merlin"]
    expected = [ix.search_types(query, 5) for query in queries]
    with ThreadPoolExecutor(max_workers=4) as pool:
        actual = list(pool.map(lambda query: ix.search_types(query, 5), queries))
    assert actual == expected


def test_same_query_is_deterministic(ix: Index) -> None:
    first = ix.search_types("Hecate", 20)
    second = ix.search_types("Hecate", 20)
    assert first == second


# ---- 7. AttributeRow.scale 接线（T-04 契约漂移修复）---------------------------
#
# 修复前：`Index.attribute()` 恒返回 DEFAULT_SCALE，缩放表形同虚设。
# 修复后：scale 来自 `sde/attribute_dict.scale_of()`（表在 `config/attributes.py`），
# 未登记属性仍回 1.0 且不抛异常。属性 ID 一律走常量（红线 N12）。

import math  # noqa: E402  （追加段落，保持原文件顶部不动）

from evefit.config.attributes import (  # noqa: E402
    ATTR_CYCLE_TIME,
    ATTR_TRACKING_SPEED,
    SCALE_KILO,
    SCALE_MILLI,
    SCALE_ONE,
)
from evefit.sde.attribute_dict import scale_of  # noqa: E402

#: 未登记缩放的属性样本：552 signatureRadius（在常量表里但不在缩放表里）。
ATTR_UNSCALED = ATTR_SIGNATURE_RADIUS


def test_attribute_scale_is_milliseconds_for_cycle_time(ix: Index) -> None:
    row = ix.attribute(ATTR_CYCLE_TIME)  # 51 = speed（模块激活周期，存储毫秒）
    assert row is not None
    assert math.isclose(row["scale"], SCALE_MILLI, rel_tol=1e-9), row
    assert math.isclose(row["scale"], 0.001, rel_tol=1e-9), row


def test_attribute_scale_is_kilo_for_tracking_speed(ix: Index) -> None:
    row = ix.attribute(ATTR_TRACKING_SPEED)  # 160 = trackingSpeed（存储值 ×1000）
    assert row is not None
    assert math.isclose(row["scale"], SCALE_KILO, rel_tol=1e-9), row
    assert math.isclose(row["scale"], 1000.0, rel_tol=1e-9), row


def test_attribute_scale_matches_attribute_dict_layer(ix: Index) -> None:
    # 索引层不得自己发明缩放：逐项与属性字典层对齐（索引层只做搬运）
    for attr_id in (ATTR_CYCLE_TIME, ATTR_TRACKING_SPEED, ATTR_UNSCALED):
        row = ix.attribute(attr_id)
        assert row is not None, attr_id
        assert math.isclose(row["scale"], scale_of(attr_id), rel_tol=1e-9), attr_id


def test_attribute_scale_defaults_to_one_for_unscaled_attribute(ix: Index) -> None:
    row = ix.attribute(ATTR_UNSCALED)
    assert row is not None
    assert math.isclose(row["scale"], SCALE_ONE, rel_tol=1e-9), row
    assert math.isclose(row["scale"], DEFAULT_SCALE, rel_tol=1e-9), row
    # 未登记 → 不缩放、不抛异常（既有行为保持）
    assert math.isclose(scale_of(ATTR_UNSCALED), SCALE_ONE, rel_tol=1e-9)


def test_attribute_unit_is_sde_unit_name_not_display_abbreviation(
    ix: Index, raw_conn: sqlite3.Connection
) -> None:
    # 契约：unit 返回 SDE 单位名（Milliseconds），不是显示缩写（s）；缩写只在 units.display_name
    row = ix.attribute(ATTR_CYCLE_TIME)
    assert row is not None
    assert row["unit"] == "Milliseconds", row
    display_name = raw_conn.execute(
        "SELECT display_name FROM units WHERE name = ?", (row["unit"],)
    ).fetchone()
    assert display_name is not None and display_name[0] == "s"
    assert row["unit"] != display_name[0]
