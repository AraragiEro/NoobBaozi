-- noobbaozi SDE 索引库 schema（SQLite）
--
-- 由 noobbaozi/sde/ingest.py 用 executescript() 执行；不要手工改已建好的库结构，
-- 改这里之后用 build_index(force=True) 重建。
--
-- 设计依据：docs/配装理解-软件设计-v1.md §7.1（存储选型 = SQLite，五万行级、查询简单）
-- 单位约定：本库**只存 SDE 原值**，不做任何物理单位换算；
--           毫秒 / ×1000 缩放属投影层（config/attributes.py + project/units.py）职责。

PRAGMA user_version = 1;

-- 版本三元组里的 sde_build，以及构建时间、来源，用于复现（设计文档 D10）。
CREATE TABLE meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY,
    name        TEXT,
    published   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE groups (
    group_id    INTEGER PRIMARY KEY,
    name        TEXT,
    category_id INTEGER
);

-- 类型表。name 取 SDE 多语言字典的 en（EFT/ESI 口径），name_zh 供中文检索。
-- name_lower 是预计算的小写英文名：SQLite 的 LOWER() 只处理 ASCII，
-- 而且直接用列比较才能吃到索引（精确/前缀检索）。
CREATE TABLE types (
    type_id    INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL,
    name_lower TEXT    NOT NULL,
    name_zh    TEXT,
    group_id   INTEGER,
    meta_level INTEGER NOT NULL DEFAULT 0,
    published  INTEGER NOT NULL DEFAULT 0,
    volume     REAL    NOT NULL DEFAULT 0.0
);

-- dogmaUnits：unit_id → 量纲标识（Length / Mass / Milliseconds / Volume ...）。
CREATE TABLE units (
    unit_id      INTEGER PRIMARY KEY,
    name         TEXT,
    display_name TEXT
);

CREATE TABLE attributes (
    attribute_id  INTEGER PRIMARY KEY,
    name          TEXT,
    display_name  TEXT,
    description   TEXT,
    unit_id       INTEGER,
    high_is_good  INTEGER NOT NULL DEFAULT 0,
    default_value REAL    NOT NULL DEFAULT 0.0,
    published     INTEGER NOT NULL DEFAULT 0
);

-- T3D/T3C 模式映射：模式是独立 type（group 1306，published=false），
-- 无「船→模式」链接属性，只能靠命名约定 <ShipName> <Mode> Mode 反解。
CREATE TABLE modes (
    mode_type_id INTEGER PRIMARY KEY,
    ship_type_id INTEGER NOT NULL,
    mode_name    TEXT    NOT NULL
);

-- 必要索引（契约要求 + 实际查询路径）
CREATE INDEX idx_types_name       ON types(name);
CREATE INDEX idx_types_name_lower ON types(name_lower);
CREATE INDEX idx_types_name_zh    ON types(name_zh);
CREATE INDEX idx_types_group_id   ON types(group_id);
CREATE INDEX idx_types_meta_level ON types(meta_level);
CREATE INDEX idx_groups_category  ON groups(category_id);
CREATE INDEX idx_attributes_name  ON attributes(name);
CREATE INDEX idx_modes_ship       ON modes(ship_type_id);
