"""T2 契约测试：`noobbaozi/contracts.py` 的字段口径冻结检查。

覆盖：
1. 版本常量；
2. `Provenance` 三态齐全；
3. 每个 TypedDict 的字段名与顺序（防止后续被误改）；
4. `Meta` / `TypedValue` / `NormalizeError` 可构造；
5. `import noobbaozi.contracts` 无副作用。

约束：无网络、无外部 fixture 依赖（唯一读取的是被测模块自身源码，用于 AST 结构检查）。
"""

from __future__ import annotations

import ast
import importlib
import sys
import typing
from pathlib import Path

import noobbaozi.contracts as contracts

# ---- 期望值：与 docs/配装理解-字段规格-v1.md、contracts.py 逐字对齐 ----

EXPECTED_TYPEDDICTS: dict[str, tuple[str, ...]] = {
    "Meta": ("sde_build", "engine_version", "spec_version"),
    "NormalizeError": ("line", "raw", "failed_at", "candidates", "reason"),
    "NormalizeResult": ("fit", "source_format", "errors", "warnings"),
    "Assumptions": (
        "skill_tier",
        "skill_tier_name",
        "module_states",
        "mode_id",
        "damage_profile",
        "ammo_loaded",
        "implants",
    ),
    "TypedValue": ("value", "provenance", "unit", "source_attr"),
    "TypeRow": (
        "type_id",
        "name",
        "group_id",
        "group_name",
        "meta_level",
        "published",
        "volume",
    ),
    "AttributeRow": (
        "attribute_id",
        "name",
        "display_name",
        "unit",
        "scale",
        "high_is_good",
    ),
    "IndexStats": ("sde_build", "types", "groups", "attributes", "modes"),
    "ScenarioSpec": ("name", "states", "mode_id"),
}


# ---- 1. 版本常量 ----


def test_spec_version_is_v1() -> None:
    assert contracts.SPEC_VERSION == "v1"


def test_engine_version_is_pinned_13_6_0() -> None:
    assert contracts.ENGINE_VERSION == "13.6.0"


def test_version_constants_are_fed_into_meta() -> None:
    meta: contracts.Meta = {
        "sde_build": 3607705,
        "engine_version": contracts.ENGINE_VERSION,
        "spec_version": contracts.SPEC_VERSION,
    }
    assert meta["spec_version"] == "v1"
    assert meta["engine_version"] == "13.6.0"


# ---- 2. Provenance 三态 ----


def test_provenance_has_exactly_three_literals() -> None:
    args = typing.get_args(contracts.Provenance)
    assert len(args) == 3
    assert set(args) == {"measured", "assumed", "unknown"}


def test_provenance_literal_order_is_stable() -> None:
    # 契约文件里的书写顺序即文档顺序：measured / assumed / unknown
    assert typing.get_args(contracts.Provenance) == ("measured", "assumed", "unknown")


def test_provenance_is_accepted_by_typed_value() -> None:
    for state in typing.get_args(contracts.Provenance):
        tv: contracts.TypedValue = {
            "value": 1.0,
            "provenance": state,
            "unit": "s",
            "source_attr": None,
        }
        assert tv["provenance"] == state


# ---- 3. TypedDict 字段冻结 ----


def test_no_unexpected_typeddicts() -> None:
    found = {
        name
        for name in vars(contracts)
        if isinstance(getattr(contracts, name), type)
        and typing.is_typeddict(getattr(contracts, name))
    }
    assert found == set(EXPECTED_TYPEDDICTS), f"契约中的 TypedDict 集合变了: {found}"


def test_every_typeddict_matches_expected_keys() -> None:
    for name, expected in EXPECTED_TYPEDDICTS.items():
        obj = getattr(contracts, name)
        assert typing.is_typeddict(obj), f"{name} 不是 TypedDict"
        assert set(obj.__annotations__) == set(expected), (
            f"{name} 字段集合被改动：期望 {sorted(expected)}，实际 {sorted(obj.__annotations__)}"
        )


def test_every_typeddict_field_order_matches_contract() -> None:
    for name, expected in EXPECTED_TYPEDDICTS.items():
        obj = getattr(contracts, name)
        assert tuple(obj.__annotations__) == expected, f"{name} 字段顺序被改动"


def test_typeddicts_are_all_closed_totality_by_default() -> None:
    # 契约里全部字段必填（非 total=False、非 closed），这里只确认没被改成可选的
    for name in EXPECTED_TYPEDDICTS:
        obj = getattr(contracts, name)
        assert obj.__required_keys__ == frozenset(obj.__annotations__), (
            f"{name} 出现了可选字段"
        )
        assert obj.__optional_keys__ == frozenset()


# ---- 4. 可构造样例 ----


def test_can_construct_meta() -> None:
    meta: contracts.Meta = {
        "sde_build": 3607705,
        "engine_version": "13.6.0",
        "spec_version": "v1",
    }
    assert meta == {
        "sde_build": 3607705,
        "engine_version": "13.6.0",
        "spec_version": "v1",
    }


def test_can_construct_typed_value() -> None:
    # 毫秒换算后的秒值必须带单位后缀与来源属性
    align_time: contracts.TypedValue = {
        "value": 11.861,
        "provenance": "measured",
        "unit": "s",
        "source_attr": 70,
    }
    assert align_time["unit"] == "s"
    assert align_time["source_attr"] == 70

    assumed: contracts.TypedValue = {
        "value": 25.0,
        "provenance": "assumed",
        "unit": "pct",
        "source_attr": None,
    }
    assert assumed["provenance"] == "assumed"


def test_can_construct_normalize_error() -> None:
    no_line: contracts.NormalizeError = {
        "line": None,
        "raw": "Some Typo Ship",
        "failed_at": "format",
        "candidates": [],
        "reason": "malformed",
    }
    with_candidates: contracts.NormalizeError = {
        "line": 3,
        "raw": "[Light Neutron Blaster II]",
        "failed_at": "module_name",
        "candidates": ["Light Neutron Blaster I", "Light Neutron Blaster II"],
        "reason": "ambiguous",
    }
    assert no_line["line"] is None
    assert with_candidates["failed_at"] == "module_name"
    assert with_candidates["candidates"][0].endswith("I")


def test_normalize_result_round_trips_error_structure() -> None:
    err: contracts.NormalizeError = {
        "line": 1,
        "raw": "Hecate",
        "failed_at": "ship_name",
        "candidates": ["Hecate"],
        "reason": "ambiguous",
    }
    result: contracts.NormalizeResult = {
        "fit": None,
        "source_format": "eft",
        "errors": [err],
        "warnings": [],
    }
    assert result["fit"] is None
    assert result["errors"][0]["reason"] == "ambiguous"
    assert result["source_format"] == "eft"


# ---- 5. import 无副作用 ----


def _module_source_tree() -> ast.Module:
    source = Path(contracts.__file__).read_text(encoding="utf-8")
    return ast.parse(source, filename=contracts.__file__)


def test_contracts_module_level_code_is_declaration_only() -> None:
    tree = _module_source_tree()
    allowed = (
        ast.Import,
        ast.ImportFrom,
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.Assign,
        ast.AnnAssign,
        ast.Expr,  # 模块 docstring
    )
    for node in tree.body:
        assert isinstance(node, allowed), f"模块级出现了非声明语句: {type(node).__name__}"
        if isinstance(node, ast.Expr):
            assert isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ), "模块级 Expr 只允许 docstring"


def test_contracts_module_level_has_no_calls() -> None:
    # 导入期不执行任何函数/构造调用 => 不读网络、不读文件、不写磁盘
    tree = _module_source_tree()
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    assert calls == [], f"模块级出现调用（可能有副作用）: {[ast.dump(c) for c in calls]}"


def test_import_is_idempotent_and_creates_no_extra_modules() -> None:
    before = set(sys.modules)
    first = importlib.import_module("noobbaozi.contracts")
    second = importlib.import_module("noobbaozi.contracts")
    assert first is second is contracts
    assert sys.modules["noobbaozi.contracts"] is contracts
    added = set(sys.modules) - before
    assert added == set(), f"重复 import 产生了新的模块条目: {sorted(added)}"


def test_module_exposes_exact_public_surface() -> None:
    public = {name for name in vars(contracts) if not name.startswith("_")}
    expected = {"SPEC_VERSION", "ENGINE_VERSION", "Provenance", *EXPECTED_TYPEDDICTS}
    assert expected <= public, f"缺少公开名字: {sorted(expected - public)}"
    # 允许 typing 导入名与未来注解相关名字存在，但不得出现业务运行时对象
    assert not any(
        isinstance(getattr(contracts, name), type(sys)) for name in public
    ), "contracts 不应导入并暴露模块对象"


# ---- 6. 注释与实现同步（T-04 契约漂移修复）-----------------------------------
#
# 只校验注释/文档字符串（字段与常量一律不动）：防止实现已经返回某个取值、
# 而契约注释仍停在旧口径。字段名与顺序由上面第 3 节的冻结用例守着。


def _comment_block_after(field_line: str, span: int = 3) -> str:
    """取字段声明行及其后几行（契约用行尾注释说明口径）。"""
    lines = Path(contracts.__file__).read_text(encoding="utf-8").splitlines()
    position = next(
        (i for i, line in enumerate(lines) if field_line in line), None
    )
    assert position is not None, f"契约里找不到字段行: {field_line}"
    return "\n".join(lines[position : position + span])


def test_source_format_comment_lists_esf() -> None:
    # 实现（engine.normalize）会返回 "esf"，注释枚举必须同步，否则调用方会漏处理
    block = _comment_block_after("source_format: str")
    assert "esf" in block, block


def test_attribute_scale_comment_points_at_attribute_dict() -> None:
    # scale 由属性字典层填充，索引层不得恒返回 1.0
    block = _comment_block_after("scale: float")
    assert "scale_of" in block, block


def test_attribute_unit_comment_names_sde_unit_not_display_abbreviation() -> None:
    # unit 是 SDE 单位名（Milliseconds / Length），不是显示缩写（s / m）
    block = _comment_block_after("unit: str | None")
    assert "Milliseconds" in block, block
    assert "Length" in block, block
    assert "缩写" in block, block


def test_normalize_contract_documents_optional_index_injection() -> None:
    # candidates 接线方式是"可选注入"，契约注释必须写明，避免被当成必填依赖
    source = Path(contracts.__file__).read_text(encoding="utf-8")
    assert "def set_index(" in source
    assert "def clear_index(" in source
