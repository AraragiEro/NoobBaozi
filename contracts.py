"""noobbaozi 跨模块契约。全员只读，修改需审查。"""
from __future__ import annotations
from typing import Literal, TypedDict

SPEC_VERSION = "v1"
ENGINE_VERSION = "13.6.0"

Provenance = Literal["measured", "assumed", "unknown"]

class Meta(TypedDict):
    sde_build: int
    engine_version: str
    spec_version: str

class NormalizeError(TypedDict):
    line: int | None
    raw: str
    failed_at: str          # ship_name / module_name / quantity / slot / format
    candidates: list[str]
    reason: str             # ambiguous / unknown / malformed / duplicate_slot

class NormalizeResult(TypedDict):
    fit: dict | None
    source_format: str      # eft / dna / esf / esi_fitting / killmail / structured
                            # （`esf` = `%esf/1` 文本；`engine.normalize` 实际会返回它）
    errors: list[NormalizeError]
    warnings: list[str]

class Assumptions(TypedDict):
    skill_tier: dict[int, int]
    skill_tier_name: str
    module_states: dict[str, str]
    mode_id: int | None
    damage_profile: dict[str, float]
    ammo_loaded: bool
    implants: str

class TypedValue(TypedDict):
    value: float
    provenance: Provenance
    unit: str
    source_attr: int | None

class TypeRow(TypedDict):
    type_id: int
    name: str
    group_id: int | None
    group_name: str | None
    meta_level: int
    published: bool
    volume: float

class AttributeRow(TypedDict):
    attribute_id: int
    name: str
    display_name: str
    unit: str | None        # **SDE 单位名**（`Milliseconds` / `Length` / `Mass` ...），
                            # 不是显示缩写（`s` / `m`）；物理换算在属性字典层
                            # （`sde/attribute_dict.to_physical`），本字段不做换算
    scale: float            # 由属性字典层填充（`sde/attribute_dict.scale_of`），索引层
                            # **不得**恒返回 1.0。1.0 原值；0.001 表示存储值是毫秒；
                            # 1000.0 表示存储值被放大 1000 倍
    high_is_good: bool

class IndexStats(TypedDict):
    sde_build: int
    types: int
    groups: int
    attributes: int
    modes: int

class ScenarioSpec(TypedDict):
    name: str
    states: dict[str, str]
    mode_id: int | None


# ---- 契约：sde.index.Index ----
# class Index:
#     @classmethod
#     def open(cls, path: str | Path) -> "Index"
#     def search_types(self, query: str, limit: int = 10) -> list[TypeRow]
#     def get_type(self, type_id: int) -> TypeRow | None
#     def types_in_group(self, group_id: int) -> list[TypeRow]
#     def modes_for_ship(self, ship_type_id: int) -> list[TypeRow]
#     def attribute(self, attribute_id: int) -> AttributeRow | None
#     def stats(self) -> IndexStats
#     def close(self) -> None
#
# ---- 契约：engine.adapter.Engine ----
# class Engine:
#     def __init__(self, sde_path: str | Path | None = None) -> None
#     def calculate(self, fit: dict, *, sources: bool = False, validate: bool = False) -> dict
#     def validate(self, fit: dict) -> list[dict]
#     def save_eft(self, fit: dict) -> str
#     def load_eft(self, text: str) -> dict
#     def sde_build(self) -> int
#
# ---- 契约：engine.normalize ----
# def normalize_fit(text: str) -> NormalizeResult
# def set_index(index: "Index") -> None   # 可选注入：仅为失败行补 candidates，未注入时行为不变
# def clear_index() -> None               # 撤销注入（幂等）
#
# ---- 契约：engine.assumptions ----
# def derive_skill_tier(fit: dict, engine: "Engine", index: "Index | None" = None) -> dict[int, int]
# def default_assumptions(fit: dict, engine: "Engine", index: "Index | None" = None) -> Assumptions
#   `index` 不传时 `derive_skill_tier` **退化为 `min_required_only` 旧档**（只补被模块要求的
#   技能 = 19 个），档名由 `assumptions["skill_tier_name"]` 诚实标出：**不得静默冒充新档**
#   （全 4 + 补最低 = 512 个技能）。要新档必须传索引。
#
# ---- 契约：engine.scenarios ----
# def run_variants(fit: dict, specs: list[ScenarioSpec], engine: "Engine") -> dict[str, dict]
