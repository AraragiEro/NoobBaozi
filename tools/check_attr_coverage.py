"""T-31 属性 ID 覆盖检查器：把「视角用了未登记的属性 ID」变成可自动兜住的事实。

为什么需要它（真实事故，不是假想）：

项目里已经**四次**出现「某个视角用了未登记的属性 ID，事后才发现」——

- `281 explosionDelay`（导弹射程）
- `56 chargeRate`（弹药续航）
- `1272 droneBandwidthUsed`（无人机带宽）
- `1367 maxSubSystems`（T3C 子系统槽）

每一次都靠人工 review 发现，而**漏掉的那个 ID 恰恰是没人用的那个**（没人用 →
没人报错 → 没有输出 → 没人看见）。所以不能靠「下次注意」，必须让机器兜住。

三项检查（对应 `tests/test_attr_coverage.py` 的三条硬门）：

A. `FIELD_SOURCES` 覆盖 —— 六个视角的 `FIELD_SOURCES` 里的来源属性：
   - 值为 `None` → 跳过（明确登记为「合成值」，不是属性直读）；
   - 值为 `int` → 必须在 `evefit/config/attributes.ATTRIBUTES` 里，否则报 `unregistered`；
   - 值为其它类型（字符串、`bool` 等）→ 报 `invalid`（红线 N12 的另一种写法）。

B. 裸属性 ID 扫描（AST，纯静态）——「看起来被当成属性 ID 用」的整数**字面量**：
   - `FIELD_SOURCES = {"x": 50}` 里的 `50`（值位，最危险的形态：它可能正好在表里，
     A 检查看不出来）；
   - `{50: ...}` 这样的整数**字典键**（红线 N12 点名的形态）；
   - `MODE_EHP_ATTR = -43` 这样名字里带 `ATTR` 的常量赋值；
   - `attrs.get(50)` / `ship_attributes(...)` 这类属性相关调用里的整数实参；
   - `if 50 in attrs` 这类「把数字当属性键去探测」的比较。
   范围限定在 `evefit/project/views/*.py` + `evefit/project/render.py`（视角与渲染层是
   属性 ID 的消费面）。**B 的结果是「疑似」而不是硬失败**：本检查器把疑似清单交出来，
   由 `tests/test_attr_coverage.py` 用显式白名单 `ALLOWED_BARE_IDS`（每条写理由）收口。
   为什么要这样分层：误报会让人直接关掉这条检查，而漏报会让这条检查变成安慰剂——
   所以「机器给疑似 + 人给理由」是唯一能长期活着的形式。

B2（附注，不参与硬门）. `evefit/project/` 下**其它**模块（`provenance.py` 等）的裸属性 ID
   ——它们不在本任务的写入范围内，只能在报告里点名，不能因此让检查失败。

C. 常量表自洽 —— `config/attributes.py` 里的三张表互相对齐：
   - `ATTR_*` 常量集合 ↔ `ATTRIBUTES` 键集合（双向差异都要报）；
   - `SCALE_BY_ATTR` 的键必须都在 `ATTRIBUTES` 里；
   - 撞值 / 非整数常量；
   - 复用 `sde/attribute_dict.audit()` 的结论（`consistent` 必须为真）。

用法：

    python -m evefit.tools.check_attr_coverage            # 可读报表，硬门失败时退出码 1
    python -m evefit.tools.check_attr_coverage --json     # 机器可读（测试与 CI 用）
    python -m evefit.tools.check_attr_coverage --strict-bare   # 把 B 的疑似也当硬门

退出码：0 = 硬门（A + C，`--strict-bare` 时含 B）通过；1 = 有硬门缺口。

纪律：只用标准库（`argparse` / `ast` / `importlib` / `json`），无网络、无随机（红线 N1）；
本模块**只读**，不改任何被检查的文件（红线 N15：连 git 写操作都没有）。
"""

from __future__ import annotations

import argparse
import ast
import importlib
import json
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping, Sequence

# =============================================================================
# 路径与常量
# =============================================================================

EVE_ROOT: Path = Path(__file__).resolve().parents[1]
"""`evefit/` 目录（本文件在 `evefit/tools/` 下）。"""

REPO_ROOT: Path = EVE_ROOT.parent
"""仓库根 —— 报告里的文件路径统一相对它显示，稳定且短。"""

PROJECT_ROOT: Path = EVE_ROOT / "project"
VIEWS_DIR: Path = PROJECT_ROOT / "views"
RENDER_PATH: Path = PROJECT_ROOT / "render.py"

VIEW_MODULE_NAMES: tuple[str, ...] = (
    "mobility",
    "survivability",
    "stability",
    "resources",
    "constraints",
    "firepower",
)
"""六个视角的模块名（顺序 = `views.VIEW_ORDER`）。

正常路径走 `views/loader.VIEW_MODULES`（唯一允许 import 六视角的地方），
这份名单只在 loader 不可 import 时兜底，避免检查器本身因为一个 import 问题变成静默空转。
"""

BARE_ID_MIN: int = -100
"""裸属性 ID 扫描的整数下界（派生负 ID 最小到 `-72`，留余量）。"""

BARE_ID_MAX: int = 3000
"""裸属性 ID 扫描的整数上界（最大已登记 SDE 属性是 2219 `fighterHeavySlots`，留余量）。"""

SKIP_BARE_VALUES: frozenset[int] = frozenset({0})
"""永远不算属性 ID 的值：`0` 不是合法属性（ESI 的属性 ID 从 1 起）。

只排这一个值，**不做**「1/2/3 看着像计数就跳过」这类猜测 —— 值域排除是最容易
把真问题一起排掉的做法（本任务要防的正是「没人用的那个 ID」）。
"""

ATTRIBUTE_NAME_TOKENS: tuple[str, ...] = ("attr", "attribute")
"""名字里出现这些片段的标识符，视为「在说属性」。

启发式的唯一依据 —— 宁可漏（漏了还有 A 检查兜未登记的那部分），也不要因为
「所有整数都当属性」把检查变成噪音制造机。
"""

ROLE_DICT_KEY: str = "dict-key"
ROLE_FIELD_SOURCE_VALUE: str = "field-sources-value"


# =============================================================================
# 小工具
# =============================================================================


def rel_path(path: Path) -> str:
    """绝对路径 → 相对仓库根的 posix 路径（报告与白名单键都用它，跨平台稳定）。"""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:  # 仓库外的临时文件（测试会用到）
        return path.resolve().as_posix()


def _is_int_literal(node: ast.AST) -> bool:
    """`isinstance(x, int)` 会把 `True` 也算进去 —— `bool` 在这里必须被排掉。"""
    return (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int)
        and not isinstance(node.value, bool)
    )


def _parent_map(tree: ast.AST) -> dict[ast.AST, ast.AST]:
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def _signed_int(node: ast.AST, parents: Mapping[ast.AST, ast.AST]) -> tuple[int, ast.AST | None]:
    """把 `-43` 折回数值 `-43`，并返回**真正有意义**的父节点。

    `-43` 在 AST 里是 `UnaryOp(USub, Constant(43))`：不折符号就会把「负 ID 常量」
    看成一堆正数 `43`，那正是本检查器最该抓到的一类写法（派生 ID 全是负数）。
    """
    assert isinstance(node, ast.Constant)
    value = int(node.value)
    current: ast.AST = node
    parent = parents.get(current)
    while isinstance(parent, ast.UnaryOp) and isinstance(parent.op, (ast.USub, ast.UAdd)):
        if isinstance(parent.op, ast.USub):
            value = -value
        current = parent
        parent = parents.get(current)
    return value, parent


def _is_attribute_name(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in ATTRIBUTE_NAME_TOKENS)


def _assignment_targets(node: ast.Assign | ast.AnnAssign) -> list[ast.AST]:
    return list(node.targets) if isinstance(node, ast.Assign) else [node.target]


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _dict_holder_name(dict_node: ast.Dict, parents: Mapping[ast.AST, ast.AST]) -> str | None:
    """`FIELD_SOURCES = {...}` → `"FIELD_SOURCES"`；其它形态回 `None`。

    只认**直接**赋值给 `*SOURCES` 名字的字典字面量：`{"x": 50}` 里那个 `50` 是
    「属性 ID 出现在属性位置上」的最直接证据，比任何调用形态都可靠。
    """
    parent = parents.get(dict_node)
    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
        for target in _assignment_targets(parent):
            if isinstance(target, ast.Name) and target.id.endswith("SOURCES"):
                return target.id
    return None


def _classify_role(
    node: ast.AST,
    parent: ast.AST | None,
    parents: Mapping[ast.AST, ast.AST],
) -> str | None:
    """这个整数是不是「被当成属性 ID 用」？是则返回角色名，否则 `None`。"""
    if isinstance(parent, ast.Dict):
        if node in parent.keys:
            return ROLE_DICT_KEY
        if _dict_holder_name(parent, parents) is not None:
            return ROLE_FIELD_SOURCE_VALUE
        return None

    if isinstance(parent, (ast.Assign, ast.AnnAssign)):
        for target in _assignment_targets(parent):
            if isinstance(target, ast.Name) and _is_attribute_name(target.id):
                return f"assign->{target.id}"
        return None

    if isinstance(parent, ast.Call) and node in parent.args:
        name = _call_name(parent.func)
        if _is_attribute_name(name):
            return f"call->{name}"
        # `attrs.get(50)`：函数名本身无害（`get`），但接收者是属性字典
        if (
            isinstance(parent.func, ast.Attribute)
            and parent.func.attr in {"get", "__getitem__", "pop", "setdefault"}
            and isinstance(parent.func.value, ast.Name)
            and _is_attribute_name(parent.func.value.id)
        ):
            return f"call->{parent.func.value.id}.{parent.func.attr}"
        return None

    if isinstance(parent, ast.Compare):
        # `if 50 in attrs:` —— 拿数字去属性字典里探测，只可能是属性 ID
        probes_attributes = any(
            isinstance(side, ast.Name) and _is_attribute_name(side.id) for side in parent.comparators
        ) or (isinstance(parent.left, ast.Name) and _is_attribute_name(parent.left.id))
        membership = any(
            isinstance(op, (ast.In, ast.NotIn)) for op in parent.ops
        )
        if probes_attributes and membership:
            return "compare->in"
        return None

    return None


def _line_snippet(source: str, lineno: int, width: int = 120) -> str:
    lines = source.splitlines()
    if 1 <= lineno <= len(lines):
        text = lines[lineno - 1].strip()
        return text if len(text) <= width else text[:width] + "…"
    return ""


# =============================================================================
# A. FIELD_SOURCES 覆盖
# =============================================================================


def load_view_modules() -> dict[str, ModuleType]:
    """六个视角模块：优先 `views/loader.VIEW_MODULES`（唯一 sanctioned import 点）。"""
    try:
        from evefit.project.views.loader import VIEW_MODULES  # noqa: PLC0415 —— 避免顶层拉引擎

        return dict(VIEW_MODULES)
    except ImportError:  # loader 依赖引擎；缺依赖时仍要能检查（用固定名单）
        return {
            name: importlib.import_module(f"evefit.project.views.{name}")
            for name in VIEW_MODULE_NAMES
        }


def known_attribute_ids(attributes: ModuleType | None = None) -> set[int]:
    """合法属性 ID 集合 = `config/attributes.ATTRIBUTES` 的键。"""
    if attributes is None:
        from evefit.config import attributes as attributes_module  # noqa: PLC0415

        attributes = attributes_module
    return set(attributes.ATTRIBUTES)


def check_field_sources(
    modules: Mapping[str, ModuleType] | None = None,
    known_ids: Iterable[int] | None = None,
) -> dict[str, Any]:
    """A 检查：六视角 `FIELD_SOURCES` 的来源必须是已登记属性 ID。

    参数可注入 —— 测试用 `modules=` / `known_ids=` 注入假数据，证明断言**非永真**
    （`tests/test_attr_coverage.py` 的「变异自检」）。
    """
    views = dict(modules) if modules is not None else load_view_modules()
    known = set(known_ids) if known_ids is not None else known_attribute_ids()

    unregistered: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    field_count = 0
    none_count = 0
    int_count = 0
    views_without_sources: list[str] = []

    for view_name, module in views.items():
        sources = getattr(module, "FIELD_SOURCES", None)
        if not isinstance(sources, dict) or not sources:
            views_without_sources.append(view_name)
            continue
        for path, source in sources.items():
            field_count += 1
            if source is None:
                none_count += 1
                continue
            if isinstance(source, bool) or not isinstance(source, int):
                invalid.append(
                    {"view": view_name, "path": path, "value": repr(source), "type": type(source).__name__}
                )
                continue
            int_count += 1
            if source not in known:
                unregistered.append({"view": view_name, "path": path, "value": source})

    return {
        "view_count": len(views),
        "field_count": field_count,
        "none_count": none_count,
        "int_count": int_count,
        "unregistered": unregistered,
        "invalid": invalid,
        "views_without_sources": views_without_sources,
        "ok": not unregistered and not invalid and not views_without_sources,
    }


# =============================================================================
# B. 裸属性 ID 扫描（AST）
# =============================================================================


def scan_bare_ids(paths: Iterable[Path] | None = None) -> dict[str, Any]:
    """B 检查：在给定文件里找「被当成属性 ID 用」的整数**字面量**。

    返回的 `suspects` 是**疑似**清单（每条带 `file` / `line` / `value` / `role` /
    `name` / `snippet`）。硬门在 `tests/test_attr_coverage.py`：白名单之外一律失败。
    """
    if paths is None:
        targets = sorted(VIEWS_DIR.glob("*.py")) + [RENDER_PATH]
    else:
        targets = [Path(path) for path in paths]
    targets = [path for path in targets if path.exists()]

    suspects: list[dict[str, Any]] = []
    for path in targets:
        suspects.extend(scan_file_for_bare_ids(path))

    suspects.sort(key=lambda item: (item["file"], item["line"], item["value"]))
    return {
        "files": [rel_path(path) for path in targets],
        "file_count": len(targets),
        "suspects": suspects,
        "suspect_count": len(suspects),
    }


def scan_source_for_bare_ids(source: str, display: str) -> list[dict[str, Any]]:
    """源码**字符串**的疑似清单（纯函数：不碰磁盘、只做 AST 分析）。

    拆出这一层是为了让测试能在**内存里**构造危险写法（`FIELD_SOURCES = {"x": 50}` 之类）
    做变异自检，而不必往磁盘上写临时文件 —— 也顺带证明扫描器本身没有 IO 依赖。
    """
    tree = ast.parse(source, filename=display)
    parents = _parent_map(tree)
    found: list[dict[str, Any]] = []

    for node in ast.walk(tree):
        if not _is_int_literal(node):
            continue
        value, parent = _signed_int(node, parents)
        if value in SKIP_BARE_VALUES:
            continue
        if not (BARE_ID_MIN <= value <= BARE_ID_MAX):
            continue
        role = _classify_role(node, parent, parents)
        if role is None:
            continue
        found.append(
            {
                "file": display,
                "line": node.lineno,
                "value": value,
                "role": role,
                "name": role.split("->", 1)[1] if role.startswith("assign->") else None,
                "snippet": _line_snippet(source, node.lineno),
            }
        )
    return found


def scan_file_for_bare_ids(path: Path) -> list[dict[str, Any]]:
    """单个文件的疑似清单（读文件 → `scan_source_for_bare_ids`）。"""
    return scan_source_for_bare_ids(path.read_text(encoding="utf-8"), rel_path(path))


def suspect_key(suspect: Mapping[str, Any]) -> str:
    """疑似项 → **稳定**白名单键（不含行号：行号会随无关改动漂移）。

    形态：`<相对路径>::<常量名或角色>::<数值>`，例如
    `evefit/project/render.py::MODE_EHP_ATTR::-43`。
    """
    name = suspect.get("name") or suspect["role"]
    return f"{suspect['file']}::{name}::{suspect['value']}"


OTHER_PROJECT_SCAN_EXCLUDE: frozenset[str] = frozenset(
    {"render.py", "__init__.py"}
)
"""B2 附注扫描时排除的文件：`render.py` 已在 B 里，`__init__.py` 只是包声明。"""


def scan_other_project_modules() -> dict[str, Any]:
    """B2（附注，不参与硬门）：`project/` 下其它模块的裸属性 ID。

    为什么单独列：这些文件（`provenance.py` / `diagnostics.py` / …）不在本任务的写入
    范围内，出现了也只能点名、由后续任务修——把它并进硬门等于让检查器永远红着，
    那和没有检查器一样。
    """
    targets = [
        path
        for path in sorted(PROJECT_ROOT.glob("*.py"))
        if path.name not in OTHER_PROJECT_SCAN_EXCLUDE
    ]
    suspects: list[dict[str, Any]] = []
    for path in targets:
        suspects.extend(scan_file_for_bare_ids(path))
    suspects.sort(key=lambda item: (item["file"], item["line"], item["value"]))
    return {
        "files": [rel_path(path) for path in targets],
        "file_count": len(targets),
        "suspects": suspects,
        "suspect_count": len(suspects),
    }


# =============================================================================
# C. 常量表自洽
# =============================================================================


def check_constant_table(attributes: ModuleType | None = None) -> dict[str, Any]:
    """C 检查：`ATTR_*` 常量 ↔ `ATTRIBUTES` 元数据 ↔ `SCALE_BY_ATTR` 三方对齐。

    `audit()`（`sde/attribute_dict.py`）的结论一并纳入：那个函数已经覆盖撞值、非整数
    常量、单位标签不一致等；本检查不重写它，只把它的 `consistent` 当硬门的一部分。
    """
    if attributes is None:
        from evefit.config import attributes as attributes_module  # noqa: PLC0415

        attributes = attributes_module

    from evefit.sde import attribute_dict  # noqa: PLC0415 —— 只读纯函数，无 IO

    prefix = attributes.CONSTANT_PREFIX
    constants: dict[str, Any] = {
        name: getattr(attributes, name) for name in dir(attributes) if name.startswith(prefix)
    }
    table: dict[int, Any] = dict(attributes.ATTRIBUTES)
    constant_ids = {value for value in constants.values() if isinstance(value, int)}

    orphan_constants = sorted(
        name for name, value in constants.items() if value not in table
    )
    orphan_rows = sorted(attr_id for attr_id in table if attr_id not in constant_ids)

    values = [value for value in constants.values() if isinstance(value, int)]
    duplicate_values = sorted({value for value in values if values.count(value) > 1})
    non_int_constants = sorted(
        name
        for name, value in constants.items()
        if not isinstance(value, int) or isinstance(value, bool)
    )
    scale_orphans = sorted(attr_id for attr_id in attributes.SCALE_BY_ATTR if attr_id not in table)

    report = attribute_dict.audit()

    return {
        "constant_count": len(constants),
        "attribute_count": len(table),
        "scale_count": len(attributes.SCALE_BY_ATTR),
        "orphan_constants": orphan_constants,
        "orphan_rows": orphan_rows,
        "duplicate_values": duplicate_values,
        "non_int_constants": non_int_constants,
        "scale_orphans": scale_orphans,
        "audit": report,
        "ok": not (
            orphan_constants
            or orphan_rows
            or duplicate_values
            or non_int_constants
            or scale_orphans
        )
        and bool(report.get("consistent")),
    }


# =============================================================================
# 汇总与报表
# =============================================================================


def run_checks(
    *,
    modules: Mapping[str, ModuleType] | None = None,
    known_ids: Iterable[int] | None = None,
    attributes: ModuleType | None = None,
    paths: Iterable[Path] | None = None,
    include_advisory: bool = True,
) -> dict[str, Any]:
    """跑 A / B / C（+ 附注 B2），返回一份可 JSON 化的报表。

    **硬门只有 A 与 C**（加 `strict_bare=True` 时才算 B）：B 的误报率天然高于 0，
    把它做成硬失败会让人直接关掉这条检查 —— 那才是真正的失败模式。
    """
    section_a = check_field_sources(modules=modules, known_ids=known_ids)
    section_b = scan_bare_ids(paths)
    section_c = check_constant_table(attributes)
    advisory = scan_other_project_modules() if include_advisory else None

    return {
        "A": section_a,
        "B": section_b,
        "C": section_c,
        "B2": advisory,
        "ok": bool(section_a["ok"] and section_c["ok"]),
    }


def _fmt_status(ok: bool, suspect: bool = False) -> str:
    if ok:
        return "通过"
    return "疑似（不参与硬门）" if suspect else "**失败**"


def render_text(report: Mapping[str, Any]) -> str:
    """可读报表（人看的那一份）。"""
    section_a = report["A"]
    section_b = report["B"]
    section_c = report["C"]
    advisory = report.get("B2") or {}

    lines: list[str] = []
    add = lines.append
    add("=" * 78)
    add("T-31 属性覆盖检查：FIELD_SOURCES 覆盖 / 裸属性 ID / 常量表自洽")
    add("=" * 78)

    add(
        f"[A] FIELD_SOURCES 覆盖   "
        f"视角 {section_a['view_count']} | 登记 {section_a['field_count']} 条"
        f"（int {section_a['int_count']} / None {section_a['none_count']}）"
        f" → {_fmt_status(section_a['ok'])}"
    )
    if section_a["unregistered"]:
        add(f"    ✗ 未登记属性 ID {len(section_a['unregistered'])} 条"
            "（补进 config/attributes.py：ATTR_* 常量 + ATTRIBUTES 行，名字用 Index.attribute(id) 核实）")
        for item in section_a["unregistered"]:
            add(f"      - {item['view']}.{item['path']} → {item['value']}（不在 ATTRIBUTES 里）")
    if section_a["invalid"]:
        add(f"    ✗ 来源类型非法 {len(section_a['invalid'])} 条（红线 N12：只允许 int 或 None）")
        for item in section_a["invalid"]:
            add(f"      - {item['view']}.{item['path']} → {item['value']}（{item['type']}）")
    if section_a["views_without_sources"]:
        add(f"    ✗ 没有 FIELD_SOURCES 的视角：{section_a['views_without_sources']}")
    if section_a["ok"]:
        add("    未登记 0 | 类型非法 0")

    add(
        f"[B] 裸属性 ID 扫描       文件 {section_b['file_count']} | 疑似 {section_b['suspect_count']} 条"
        f" → {_fmt_status(not section_b['suspects'], suspect=True)}"
    )
    for item in section_b["suspects"]:
        where = f"{item['file']}:{item['line']}"
        add(f"      ? {where}  值 {item['value']}  角色 {item['role']}")
        add(f"        {item['snippet']}")
    if section_b["suspects"]:
        add("    → 收口方式：白名单 `ALLOWED_BARE_IDS`（tests/test_attr_coverage.py，每条写理由）")

    add(
        f"[C] 常量表自洽           "
        f"常量 {section_c['constant_count']} | 元数据 {section_c['attribute_count']}"
        f" | 缩放 {section_c['scale_count']} → {_fmt_status(section_c['ok'])}"
    )
    if section_c["orphan_constants"]:
        add(f"    ✗ 有常量但无元数据行：{section_c['orphan_constants']}")
    if section_c["orphan_rows"]:
        add(f"    ✗ 有元数据行但无常量：{section_c['orphan_rows']}")
    if section_c["duplicate_values"]:
        add(f"    ✗ 撞值（复制粘贴写错）：{section_c['duplicate_values']}")
    if section_c["non_int_constants"]:
        add(f"    ✗ 非整数常量：{section_c['non_int_constants']}")
    if section_c["scale_orphans"]:
        add(f"    ✗ 缩放表里有、元数据表没有：{section_c['scale_orphans']}")
    if section_c["ok"]:
        audit = section_c["audit"]
        add(f"    audit(): consistent={audit.get('consistent')}"
            f" | 撞值 {len(audit.get('duplicate_values') or [])}"
            f" | 孤儿常量 {len(audit.get('orphan_constants') or [])}"
            f" | 孤儿行 {len(audit.get('orphan_rows') or [])}"
            f" | 单位标签不一致 {len(audit.get('unit_label_mismatches') or [])}")

    if advisory:
        add(
            f"[B2] 附注（不参与硬门）   文件 {advisory['file_count']}"
            f" | 疑似 {advisory['suspect_count']} 条（project/ 下其它模块，本任务写入范围之外）"
        )
        for item in advisory["suspects"]:
            add(f"      ? {item['file']}:{item['line']}  值 {item['value']}  角色 {item['role']}")
            add(f"        {item['snippet']}")
        if advisory["suspects"]:
            add("    → 迁移建议：先在 `Index.attribute(id)` 核实（派生负 ID 查不到 → 登记时注明")
            add("      「派生，SDE 无记录」），并把 `project/` 里的就近常量改成 import；")
            add("      注意 `tests/test_attribute_dict.py` 有三处硬编码「常量数 = 146」，要一起改。")

    add("-" * 78)
    verdict = "通过" if report["ok"] else "有缺口"
    add(f"硬门结论（A + C）: {verdict}")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m evefit.tools.check_attr_coverage",
        description="T-31 属性 ID 覆盖检查（FIELD_SOURCES / 裸 ID / 常量表自洽）",
    )
    parser.add_argument("--json", action="store_true", help="输出机器可读 JSON")
    parser.add_argument(
        "--strict-bare",
        action="store_true",
        help="把裸属性 ID 的疑似也当硬门（默认只报疑似，由测试白名单收口）",
    )
    parser.add_argument(
        "--no-advisory",
        action="store_true",
        help="跳过 B2 附注扫描（project/ 下其它模块）",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    report = run_checks(include_advisory=not args.no_advisory)
    ok = bool(report["ok"])
    if args.strict_bare:
        ok = ok and not report["B"]["suspects"]

    if args.json:
        payload = dict(report)
        payload["ok"] = ok
        payload["strict_bare"] = bool(args.strict_bare)
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(render_text(report))
        if args.strict_bare and report["B"]["suspects"]:
            print(f"（--strict-bare）：裸 ID 疑似 {report['B']['suspect_count']} 条也算硬门失败")
    return 0 if ok else 1


if __name__ == "__main__":  # pragma: no cover —— 入口在测试里走 main()
    raise SystemExit(main())
