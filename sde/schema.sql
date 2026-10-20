-- noobbaozi SDE 索引库 schema（SQLite）
--
-- 由 noobbaozi/sde/ingest.py 用 executescript() 执行；不要手工改已建好的库结构，
-- 改这里之后用 build_index(force=True) 重建。
--
-- 设计依据：docs/配装理解-软件设计-v1.md §7.1（存储选型 = SQLite，五万行级、查询简单）
-- 单位约定：本库**只存 SDE 原值**，不做任何物理单位换算；
--           毫秒 / ×1000 缩放属投影层（config/attributes.py + project/units.py）职责。

PRAGMA user_version = 2;

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
    -- v2：SDE 的 `displayName.zh`。**允许 NULL** —— 实测 2871 条属性里 1,620 条没有
    -- displayName（连英文都没有），别把 NULL 当空串往文案里拼。
    display_name_zh TEXT,
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

-- =============================================================================
-- v2（T-4/5）：dogma 数据。来自 typeDogma / dogmaEffects / typeBonus / dbuffCollections。
-- =============================================================================

-- 类型 → 属性值（`typeDogma.dogmaAttributes`）。**SDE 原值**，不做单位换算。
CREATE TABLE type_attributes (
    type_id      INTEGER NOT NULL,
    attribute_id INTEGER NOT NULL,
    value        REAL    NOT NULL,
    PRIMARY KEY (type_id, attribute_id)
);

-- effect 定义（`dogmaEffects`）。SDE **没有** effect 的中文名（实测：字段里没有
-- `displayName`），所以 `display_name_zh` 恒为 NULL —— 列留着是为了以后 SDE 补上时不用再改 schema。
CREATE TABLE effects (
    effect_id              INTEGER PRIMARY KEY,
    name                   TEXT,
    display_name_zh        TEXT,
    duration_attribute_id  INTEGER,
    discharge_attribute_id INTEGER,
    is_offensive           INTEGER NOT NULL DEFAULT 0,
    is_assistance          INTEGER NOT NULL DEFAULT 0,
    published              INTEGER NOT NULL DEFAULT 0
);

-- 类型 → effect（`typeDogma.dogmaEffects`）。`is_default` 是 SDE 的 `isDefault`。
CREATE TABLE type_effects (
    type_id   INTEGER NOT NULL,
    effect_id INTEGER NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (type_id, effect_id)
);

-- 船体 / 模块加成（`typeBonus`）。SDE 的结构是**两层**：
--   `{_key: 加成集合 id, roleBonuses: [...], types: [{_key: type_id, _value: [...]}]}`
-- 所以一行 = (type_id, 来源, 数值, 文案)。
-- `skill_type_id` 留 NULL：SDE 的 typeBonus **不直接给「是哪个技能给的」**（文案里的
-- `showinfo:` 链接指向模块而不是技能），不猜。
CREATE TABLE type_bonuses (
    type_id       INTEGER NOT NULL,
    skill_type_id INTEGER,
    bonus_type    TEXT    NOT NULL,   -- `role`（roleBonuses）/ `type`（types[]._value）
    value         REAL,
    text_en       TEXT,
    text_zh       TEXT
);

-- 增益编号 → 名字（`dbuffCollections`）：`fit_project` 的 buff id 现在只能给 `{id:15}`。
CREATE TABLE buffs (
    buff_id  INTEGER PRIMARY KEY,
    name     TEXT,
    name_zh  TEXT
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
-- v2：**反查**才是主要访问路径（按 attribute 找哪些类型有它），type_id 那一侧是主键前缀。
CREATE INDEX idx_type_attributes_attr ON type_attributes(attribute_id);
CREATE INDEX idx_type_effects_effect  ON type_effects(effect_id);
CREATE INDEX idx_type_bonuses_type    ON type_bonuses(type_id);
