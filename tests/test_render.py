"""T2/T3 测试：分级渲染层（`project/render.py` + `views/loader.py`）。

样本：`noobbaozi/spike/kill.json`（killmail 139246907，Hecate），技能档走
`engine.assumptions.derive_skill_tier`（**必须传索引**：全部技能 4 级 = 512 个），
与 `tests/test_projection_core.py` 同构。

**本文件刻意不钉任何绝对数值**（SDE 更新会动数值），只断言结构与关系：

1. `render_overview` 的视角键 = `VIEW_ORDER`，每个视角
   `{overview, provenance, confidence, flags}`；
2. **每个视角 provenance 覆盖率 100%**：`set(provenance) == set(overview 叶子点路径)`，
   且三态取值合法、`FIELD_SOURCES` 的映射（含 `None` → `assumed`；T-27 起**值缺失
   优先** → `unknown`）逐条对得上；
3. **诊断只算一次**（用计数器桩证明 `diagnostics.evaluate` 每轮只被调用一次），
   并按 `flag["view"]` 归位到对应视角；未渲染视角的 flag 进顶层兜底、不丢；
4. `meta` 含版本三元组 + `assumptions`（且就是 `ctx.assumptions` 本身）；
5. **渲染层不做单位换算**（红线 N5）：渲染结果与 `build_view()` 的结果**完全相等**
   （唯一允许的差异是 `OVERVIEW_DOWNGRADES` 登记的粒度改写，见第 9 条）；
6. 预算护栏：预算调大 → 不降级；预算调到极小 → `degraded=True`、`chars` 公式成立、
   数值一个都没变（抽字段比对 + 全视角遮住可截断列表后整体比对）；
7. `render_detail`：单视角完整返回、`fields` 裁剪、`missing` 不静默忽略、
   未知视角 `KeyError`；
8. 加载器：`load_views()` = 六视角、`VIEW_MODULES` 每项都声明了 `FIELD_SOURCES`；
9. **overview 粒度改写**：`resources.upgrade_space` 在 overview 里是摘要、
   在 `render_detail(view="resources")` 里是**完整原样**的候选列表；默认预算下
   `degraded is False`（T-18 的 `spike/kill.json` 14,083 chars < 17,000；
   T-44 裁决后预算是 **19,000**，f47 17,350 / f67 17,314 / f12 18,654 也全部不降级）。
10. **整数键字典**（`constraints.skill_floor.count_by_level`）按等级逐条出 provenance
    （`...count_by_level.<level>`），等级来自数据、不硬编码。
11. **C1 顶层 `modes`**：键恒存在；Hecate 恒给 Defense / Propulsion / Sharpshooter
    三条、值与 `tools/baseline_after_tierfix.json` 的四模式权威快照对齐（容差 1e-6，
    输出层 round(...,3) 先对权威值取同样位数）；无模式船 → `[]` **且零额外计算**；
    同一份 ctx 二次渲染走实例缓存；`modes` **不改**其它字段；超预算时**最后**才砍它。
12. **C2 `confidence`（T-18 稀疏表）**：缺省 `A` **不写出**（空表输出 `{}`，键恒在），
    只含非默认档的叶子路径。三条不变量合起来等价于 T-17 全量平行表的强度：
    ① `set(confidence) ⊆ set(provenance)`；② **每个非 A 的登记项都必须出现**
    （拿 `confidence.CONFIDENCE` / `FIELDS_FROM_MAX_TARGETS` 反查实际叶子，防止漏输出）；
    ③ `provenance` 键集合 == overview 叶子集合。`application.*` 三个未建模参数为 `E`，
    其余路径不出现（= 默认 `A`）；与 provenance 正交（同一路径可以是 `assumed` + `E`，
    也可以是 `assumed` + 默认 `A`）；`render_detail` 同口径裁剪。
13. **T-27 实例级覆盖**：`值缺失 ⇒ unknown`（`provenance.resolve()`）。f4 的 `dph`
    （`None`）必须是 `unknown`，f1/f5 的 `dph`（有数值）保持静态表结论；任意视角里值为
    `None` 的叶子一律 `unknown`（通用断言遍历全部叶子）；值存在的叶子仍与 `tag()`
    逐条一致（抽 7 个对照，含数组元素 / 整数键容器 / 合成值来源）。
14. **T-30 第二条实例规则（状态敏感性）**：`render.state_sensitive_attrs(ctx)` 用
    「全 online vs 全 active」两次实测探测出 `set[int]`——f4（盾抗模块）必须含
    `271`–`274`，f1（Hecate，无盾抗模块）不得含；探测**最多多算 2 次**且进 ctx 缓存
    （引擎 `misses` 差值断言），纯被动配装直接空集**零额外计算**；集合交给
    `provenance.resolve(..., state_sensitive=…)`，只把 `measured` 升格成 `assumed`
    （用「静态表把 271 收紧回 measured」的补丁模拟两种走向），缺值规则仍然优先。
15. **T-32 `unknown_reasons`（字段规格 §0.2.1 方案 A）**：三态不变，每个 `unknown` 叶子
    额外带一条原因，输出为**稀疏平行表**——键集合**恰好**等于 `provenance` 里值为
    `unknown` 的路径集合（不多不少），空表是 `{}`。判定优先级 flag → 条件字段 →
    引擎永不提供 → 多值并列 → 兜底（规则表在 `project/reasons.py`）：
    f4 的 `dph`（有 `weapon_damage_zero` flag）→ `input_incomplete`；炮船的
    `application.explosion_radius_m` → `not_applicable`；并列的
    `thinnest.*.damage_type` → `ambiguous`；以 `-70 maxTargets` 为来源的字段 →
    `engine_omits`。**守门**：f1/f4/f5/f26 四个样本上 `unclassified` 必须为 **0**
    （出现即缺陷），并用「抹掉一条规则 → 守门断言变红」证明它不是永真的。
    **T-40 补两个样本**（f12 / f34）：它们「有武器系统但引擎不产出伤害」（后勤维修无人机 /
    ECM 无人机），`dps.base` / `dps.with_reload` / `overload.{off,on,delta}.dps` 的值是
    `None` → T-37 删掉的那五条条件登记必须补回，否则落进 `unclassified`（本轮 blocker）。
16. **T-34 结构性叶子补登记**：`tier_violations` / `hard_violations` 的元素字段
    （`kind` / `message` / `resource` / `rule.*` / `target.*`）是**我们自己算出来的合成 /
    枚举值** → `FIELD_SOURCES` 登记 `None` → `assumed`（不再是 `unknown` + `not_applicable`）。
    覆盖率守门断言从「数值叶子必须有映射」（`test_views_a/b`）扩展成「**所有有值叶子都必须
    有来源登记**」，并把 `not_applicable` 桶钉成**全量清单**（只剩真条件字段）；
    白名单带过期检测（照 `test_attr_coverage.py::ALLOWED_BARE_IDS` 的做法），
    并用「注入一个未登记的有值叶子 → 守门断言变红」证明它不是永真的。

约束：无网络、无随机（红线 N1）；样本只读；只碰本任务写入范围内的文件。
"""

from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from noobbaozi import contracts
from noobbaozi.config import attributes as A
from noobbaozi.config.paths import db_path
from noobbaozi.engine.adapter import Engine
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier
from noobbaozi.project import confidence as C
from noobbaozi.project import diagnostics as D
from noobbaozi.project import reasons as R
from noobbaozi.project import render
from noobbaozi.project.views import VIEW_ORDER, available_views, build_view
from noobbaozi.project.views.base import BASE, ViewContext
from noobbaozi.project.views.loader import VIEW_MODULES, load_views
from noobbaozi.sde.index import Index

EVE_ROOT = Path(__file__).resolve().parents[1]
KILL_PATH = EVE_ROOT / "spike" / "kill.json"
RENDER_PATH = EVE_ROOT / "project" / "render.py"
BASELINE_PATH = EVE_ROOT / "tools" / "baseline_after_tierfix.json"
FIXTURES_DIR = EVE_ROOT / "acceptance" / "fixtures"
"""验收样本目录（只读）：T-27 的 f1/f4/f5 从这里的**输入**文件取，不碰 `*.expected.json`。"""

INSTANCE_SAMPLES: tuple[str, ...] = ("f1-hecate-killmail", "f4-non-hecate", "f5-no-mwd")
"""T-27 的对照样本：f1/f5 的 `dph` 有数值，f4 的 `dph` 是 `None`（有弹药却零伤害）。"""

BIG_BUDGET: int = 10**9
"""「不可能超」的预算：让输出保持原始形态（不降级），用于结构类断言。"""

ARRAY_MARKER = "[]"
PROVENANCE_STATES = frozenset({"measured", "assumed", "unknown"})
CONFIDENCE_OUTPUT_STATES = frozenset({"A", "C", "E"})
"""输出层实际产生的三档（`B` / `D` 只在字段规格文档里，渲染层不出）。"""

HECATE = 35683
"""样本船（T3D，三模式）。"""

CONFESSOR = 34317
"""T-23 取样船（T3D，三模式）—— 验收报告里「modes 缺失」的那艘，用来钉住非 Hecate 情形。"""

SVIPUL = 34562
"""T-23 取样船（T3D，三模式），与 Confessor 同组。"""

HECATE_MODE_TOKENS: tuple[str, ...] = ("Defense", "Propulsion", "Sharpshooter")
"""T3D 三模式的短名（渲染层输出；完整名是 `<ShipName> <Mode> Mode`）。"""

RIFTER = 587
"""非 T3D（无模式）对照船：用来证明「没有模式就不空转」。"""


# ---- fixtures（与 tests/test_projection_core.py 同构）----


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


@pytest.fixture(scope="module")
def killmail() -> dict:
    raw = json.loads(KILL_PATH.read_text(encoding="utf-8"))[0]
    raw.pop("zkb", None)  # zkillboard 附加字段，不是 killmail 字段
    return raw


@pytest.fixture(scope="module")
def sample_fit(killmail: dict, engine: Engine, index) -> dict:
    """样本配装 + 「全部技能 4 级 + 补最低」技能档（512 个；必须传索引）。"""
    fit = dogma.load_killmail(killmail)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return fit


@pytest.fixture(scope="module")
def index():
    try:
        path = db_path()
    except FileNotFoundError:  # 索引库是构建产物，没建就跳过
        pytest.skip("索引库未构建：先跑 noobbaozi.sde.index.build_index()")
    opened = Index.open(path)
    yield opened
    opened.close()


@pytest.fixture(scope="module")
def ctx(engine: Engine, index, sample_fit: dict) -> ViewContext:
    """一个共享上下文（变体缓存也在里面，多次渲染不会重复算）。"""
    return ViewContext(
        engine=engine,
        index=index,
        fit=sample_fit,
        assumptions=default_assumptions(sample_fit, engine, index=index),
        meta=engine.meta(),
    )


@pytest.fixture(scope="module")
def overview(ctx: ViewContext) -> dict:
    """**未降级**的 overview（临时把预算调大，模块常量用完还原）。"""
    original = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        return render.render_overview(ctx)
    finally:
        render.BUDGET_CHARS = original


def _load_instance_fit(engine: Engine, index, stem: str) -> dict:
    """按样本名加载配装 + 「全 4 + 补最低」技能档（f1 是 `.eft`，f4/f5 是 killmail）。

    **必须排除 `*.expected.json`**：快照文件的 stem 比输入文件更靠前（`glob` 顺序），
    它没有 `killmail_id`，误取会以「missing field killmail_id」炸在引擎里。
    """
    candidates = [
        path
        for path in sorted(FIXTURES_DIR.glob(f"{stem}*"))
        if not path.name.endswith(".expected.json")
    ]
    assert candidates, f"找不到样本 {stem} 的输入文件（{FIXTURES_DIR}）"
    path = candidates[0]
    if path.suffix == ".eft":
        fit = engine.load_eft(path.read_text(encoding="utf-8"))
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw = raw[0] if isinstance(raw, list) else raw
        raw.pop("zkb", None)  # zkillboard 附加字段，不是 killmail 字段
        fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return fit


def _make_ctx(engine: Engine, index, stem: str) -> ViewContext:
    """按样本名造一个**缓存是空的**新上下文（状态敏感性探测的成本断言要干净的 ctx）。"""
    fit = _load_instance_fit(engine, index, stem)
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


@pytest.fixture(scope="module")
def rendered_samples(engine: Engine, index) -> dict[str, dict]:
    """f1 / f4 / f5 三个样本各渲染一次 overview（预算调大，排除降级对结构的干扰）。"""
    original = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        rendered: dict[str, dict] = {}
        for stem in INSTANCE_SAMPLES:
            fit = _load_instance_fit(engine, index, stem)
            ctx = ViewContext(
                engine=engine,
                index=index,
                fit=fit,
                assumptions=default_assumptions(fit, engine, index=index),
                meta=engine.meta(),
            )
            rendered[stem] = render.render_overview(ctx)
        return rendered
    finally:
        render.BUDGET_CHARS = original


@pytest.fixture(scope="module")
def baseline() -> dict:
    """`tools/baseline_after_tierfix.json`：**四模式数值的权威快照**。"""
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def mode_ctx(engine: Engine, index, baseline: dict) -> ViewContext:
    """baseline `mode_sample` 那套配装（Hecate + 1 门 Light Neutron Blaster II + Void S）。

    **为什么另起一个 ctx**：baseline 的四模式权威值出自这个**最小探针配装**（不是
    killmail 样本），字段规格 §10.2 的模式对比就是按它的量级定的
    （`none` 4609.689 / Defense 6264.533 / Propulsion 对齐 2.143 / Sharpshooter 88.427）。
    技能档同样是全 4 + 补最低（512 个），否则数值对不上权威值。
    """
    fit = engine.load_eft(baseline["mode_sample"]["eft"])
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


class ModeRecordingEngine:
    """包一层 `Engine`：记录每次 `calculate()` 的 fit 里有没有 `ship.mode`。

    用来证明「无模式船不空转」（一次带 mode 的计算都没有）与「同 ctx 二次渲染走缓存」
    （只有本 ctx 首轮那 3 次带 mode 的计算）。其余属性/方法全部透传给真引擎。
    """

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.calls = 0
        self.mode_calls: list[int] = []

    def calculate(self, fit: dict, **kwargs) -> dict:
        self.calls += 1
        ship = fit.get("ship") if isinstance(fit, dict) else None
        mode_id = ship.get("mode") if isinstance(ship, dict) else None
        if mode_id is not None:
            self.mode_calls.append(mode_id)
        return self.engine.calculate(fit, **kwargs)

    def __getattr__(self, name: str) -> object:  # 透传 meta / cache_stats / load_* …
        return getattr(self.engine, name)


# ---- 小工具 ----


def _measure(views: dict, modes: list | None = None) -> int:
    """与 `render._measure` 同一口径（独立复算，验公式）：`views` + `modes`。"""
    chars = len(json.dumps(views, ensure_ascii=False))
    if modes:
        chars += len(json.dumps(modes, ensure_ascii=False))
    return chars


def _leaf_paths(node: object, prefix: str = "") -> list[str]:
    """**独立实现**的叶子点路径枚举（故意不复用 render 里的私有函数）。

    语义与规格一致：对象逐键递归；**整数键也展开**（`count_by_level.4`，等级来自数据）；
    对象数组展开成 `p[].k`；其余（标量 / 空容器 / 标量列表）就是叶子本身。
    """
    if isinstance(node, dict) and node:
        found: list[str] = []
        for key, value in node.items():
            found.extend(_leaf_paths(value, f"{prefix}.{key}" if prefix else str(key)))
        return list(dict.fromkeys(found))
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        found = []
        for item in node:
            found.extend(_leaf_paths(item, f"{prefix}{ARRAY_MARKER}"))
        return list(dict.fromkeys(found)) or [prefix]
    return [prefix]


def _leaf_values(node: object, prefix: str = "") -> dict[str, list[object]]:
    """**独立实现**的「叶子点路径 → 该路径上所有出现过的值」（同 `_leaf_paths` 的寻址）。

    T-27 的实例级规则看的是**值**，所以测试也要能拿到值：数组元素路径
    （`weapons[].ammo_name`）会收集每个元素的值，一个路径可能对应多个出现。
    """
    if isinstance(node, dict) and node:
        found: dict[str, list[object]] = {}
        for key, value in node.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            for path, occurrences in _leaf_values(value, child).items():
                found.setdefault(path, []).extend(occurrences)
        return found
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        found = {}
        for item in node:
            for path, occurrences in _leaf_values(item, f"{prefix}{ARRAY_MARKER}").items():
                found.setdefault(path, []).extend(occurrences)
        return found
    return {prefix: [node]}


def _has_missing_value(occurrences: list[object]) -> bool:
    """一条路径上任一出现是 `None` = 实例级「值缺失」（保守口径，与渲染层一致）。"""
    return any(value is None for value in occurrences)


def _covered_by(path: str, registered: str) -> bool:
    """叶子 `path` 是否被 `registered` 这条**登记**覆盖（独立实现，不复用 render 私有函数）。

    三条覆盖关系与 `confidence_of()` 的查表顺序一一对应（多一条都不算）：
    1. 同一条点路径；
    2. 登记在**对象数组的容器**上（`weapons` 覆盖 `weapons[].name`）；
    3. 登记在**整数键字典的容器**上（`count_by_level` 覆盖 `count_by_level.4`）。
    """
    if path == registered:
        return True
    if path.startswith(f"{registered}{ARRAY_MARKER}"):
        return True
    head, _dot, tail = path.rpartition(".")
    return bool(head) and head == registered and tail.isdigit()


def _mask_truncatable(overview: dict) -> dict:
    """把可截断的明细列表遮成「类别占位」，用于比对降级前后的数值一致性。"""
    masked = dict(overview)
    for key in render.TRUNCATABLE_LISTS:
        if isinstance(masked.get(key), list):
            masked[key] = "<list>"
    return masked


def _flag_ids(flags: list[dict]) -> list[str]:
    return [flag["id"] for flag in flags]


# =============================================================================
# 一、加载器（唯一允许 import 六视角的地方）
# =============================================================================


def test_loader_registers_six_views_in_view_order() -> None:
    """`load_views()` 返回六个视角，顺序 = `VIEW_ORDER`。"""
    assert tuple(load_views()) == VIEW_ORDER
    assert set(load_views()) <= set(available_views())


def test_view_modules_map_covers_ordered_views_and_declare_field_sources() -> None:
    """`VIEW_MODULES` 与 `VIEW_ORDER` 一一对应，且每项都声明了 `FIELD_SOURCES`。"""
    assert tuple(VIEW_MODULES) == VIEW_ORDER
    for name, module in VIEW_MODULES.items():
        assert module.__name__ == f"noobbaozi.project.views.{name}"
        sources = getattr(module, "FIELD_SOURCES", None)
        assert isinstance(sources, dict) and sources, f"{name} 没有 FIELD_SOURCES"


# =============================================================================
# 二、overview 的形状与 meta
# =============================================================================


def test_render_overview_returns_six_views_in_view_order(overview: dict) -> None:
    """`views` 的键顺序 = `VIEW_ORDER`（渲染层默认顺序），`modes` 恒在。"""
    assert tuple(overview["views"]) == VIEW_ORDER
    assert set(overview["views"]) == set(load_views())
    assert set(overview) == {"meta", "views", "modes", "flags", "budget"}
    assert isinstance(overview["modes"], list)


def test_each_view_entry_has_overview_provenance_and_flags(overview: dict) -> None:
    """每个视角条目 = `{overview, provenance, unknown_reasons, confidence, flags}`，类型都对。"""
    for name, entry in overview["views"].items():
        assert set(entry) == {
            "overview",
            "provenance",
            "unknown_reasons",
            "confidence",
            "flags",
        }, name
        assert isinstance(entry["overview"], dict) and entry["overview"], name
        assert isinstance(entry["provenance"], dict), name
        assert isinstance(entry["unknown_reasons"], dict), name
        assert isinstance(entry["confidence"], dict), name
        assert isinstance(entry["flags"], list), name


def test_meta_contains_version_triple_and_assumptions(
    ctx: ViewContext, overview: dict
) -> None:
    """`meta` 含 `sde_build` / `engine_version` / `spec_version` / `assumptions`。"""
    meta = overview["meta"]

    assert set(meta) >= {
        "sde_build",
        "engine_version",
        "spec_version",
        "assumptions",
        "scope",
    }
    assert meta["sde_build"] == ctx.meta["sde_build"]
    assert meta["engine_version"] == contracts.ENGINE_VERSION
    assert meta["spec_version"] == contracts.SPEC_VERSION
    assert meta["scope"] == "overview"
    # 红线 N9：直接取 ctx.assumptions（同一个对象，不重算、不复制）
    assert meta["assumptions"] is ctx.assumptions


# =============================================================================
# 三、provenance：每个叶子一条，三态与 FIELD_SOURCES 一致
# =============================================================================


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_provenance_covers_every_leaf(overview: dict, view_name: str) -> None:
    """**覆盖率 100%**：provenance 的键集合 == overview 叶子点路径集合。"""
    entry = overview["views"][view_name]
    leaves = set(_leaf_paths(entry["overview"]))
    keys = set(entry["provenance"])

    assert leaves, f"{view_name} 的 overview 不该是空的"
    assert keys == leaves, (
        f"{view_name} provenance 与叶子不吻合：多 {sorted(keys - leaves)}，"
        f"少 {sorted(leaves - keys)}"
    )


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_provenance_states_are_valid(overview: dict, view_name: str) -> None:
    """三态只允许 measured / assumed / unknown（红线 N3）。"""
    for path, state in overview["views"][view_name]["provenance"].items():
        assert state in PROVENANCE_STATES, f"{view_name}.{path} → {state!r}"


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_provenance_follows_field_sources(overview: dict, view_name: str) -> None:
    """**两层模型（T-27）**：先判实例值，再回静态表。

    - 该叶子在本次计算里**值缺失**（任一出现是 `None`）→ `unknown`，**覆盖**静态表；
    - 值存在时：映射 `None`（合成值）→ `assumed`；映射到属性 ID → `provenance.tag(ID)`。

    只检查**确实出现在这次 overview 里**的映射路径（空列表会被折叠成容器路径，
    见渲染层 docstring），避免把结构差异误判成标签错误。这条不是放宽：值存在的路径
    仍然要求与静态表**逐条相等**，值缺失的路径多了一条更严格的 `unknown` 要求。
    """
    from noobbaozi.project import provenance as P  # 局部 import：只为对拍三态

    sources = VIEW_MODULES[view_name].FIELD_SOURCES
    states = overview["views"][view_name]["provenance"]
    values = _leaf_values(overview["views"][view_name]["overview"])

    checked = 0
    for path, attribute_id in sources.items():
        if path not in states:
            continue
        static = "assumed" if attribute_id is None else P.tag(int(attribute_id))
        if _has_missing_value(values.get(path, [])):
            expected = "unknown"  # 实例级覆盖：值缺失 ⇒ unknown
        else:
            expected = static
        assert states[path] == expected, f"{view_name}.{path} 应为 {expected}，实为 {states[path]}"
        checked += 1
    assert checked > 0, f"{view_name} 的 FIELD_SOURCES 一条都没对上（说明结构变了）"


def test_provenance_covers_string_leaves(overview: dict, ctx: ViewContext) -> None:
    """字符串叶子也逐条打标；**登记过的字符串叶子判 `assumed`**（合成/enum 值，不冒充实测）。"""
    stability = overview["views"]["stability"]
    constraints = overview["views"]["constraints"]
    resources_detail = render.render_detail(ctx, view="resources")["provenance"]

    for path in (
        "consumables.ammo_module",
        "cap_by_module[].module",
        "cap_sensitivity[].module",
        "external_dependency.remote_rep_modules",
        "external_dependency.cap_transfer_modules",
    ):
        assert path in stability["provenance"], f"{path} 没被叶子枚举覆盖"
        assert stability["provenance"][path] == "assumed", f"{path} 应为 assumed"

    assert constraints["provenance"]["needs_implant_basis"] == "assumed"

    # 候选清单的台账字段只在 detail 层出现（overview 里是摘要）
    for leaf in ("slot", "module", "candidate"):
        path = f"upgrade_space[].{leaf}"
        assert resources_detail[path] == "assumed", f"{path} 应为 assumed"


def test_provenance_marks_unmapped_and_unknowable_as_unknown(
    ctx: ViewContext, overview: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """没有映射 → `unknown`（红线 N4：默认兜底，不猜、不拿默认值冒充）。

    T-34 起六个视角里**已经找不到「有值却没登记」的字段**（那是本任务还清的欠账），
    所以这里**取消一条真实登记**（`damage_split.em`：样本里是 `measured` 的实测值）
    来证明缺映射时确实兜底 `unknown` —— 不这么做，这条断言会随欠账还清而空转。
    """
    firepower = overview["views"]["firepower"]
    # 已装填弹药的 114/116/117/118 会出现在装备级 attributes 里（发射器并入装填弹药属性）
    # → 属性级 measured；旧结论「弹药属性不在 calculate 输出」对已装填弹药不成立
    assert firepower["provenance"]["damage_split.em"] == "measured"

    monkeypatch.delitem(VIEW_MODULES["firepower"].FIELD_SOURCES, "damage_split.em")
    monkeypatch.setattr(render, "BUDGET_CHARS", BIG_BUDGET)

    mutated = render.render_overview(ctx)["views"]["firepower"]
    assert isinstance(mutated["overview"]["damage_split"]["em"], (int, float)), "前提：这个叶子有值"
    assert mutated["provenance"]["damage_split.em"] == "unknown", (
        "没有 FIELD_SOURCES 映射时必须兜底 unknown（不能因为碰巧有值就冒充实测）"
    )


def test_provenance_expands_int_key_dict_per_level(overview: dict) -> None:
    """整数键字典**按等级逐条**出 provenance：`count_by_level.<level>`，等级来自数据。"""
    constraints = overview["views"]["constraints"]
    counts = constraints["overview"]["skill_floor"]["count_by_level"]

    assert counts, "样本应至少有一个模块技能要求"
    expected = {f"skill_floor.count_by_level.{level}" for level in counts}
    actual = {
        path
        for path in constraints["provenance"]
        if path.startswith("skill_floor.count_by_level.")
    }

    assert actual == expected, f"等级键不齐：{sorted(actual)} != {sorted(expected)}"
    assert all(constraints["provenance"][path] == "assumed" for path in expected)
    # 「整块当一个叶子」的旧口径不再出现（容器来源逐级继承给各等级）
    assert "skill_floor.count_by_level" not in constraints["provenance"]


def test_provenance_has_measured_and_assumed_samples(overview: dict) -> None:
    """样本里三态都出现过（否则「逐字段打标」等于没测）。"""
    states = {
        state
        for entry in overview["views"].values()
        for state in entry["provenance"].values()
    }
    assert states == PROVENANCE_STATES


def test_provenance_uses_array_element_convention(overview: dict) -> None:
    """对象数组按元素约定出键（`cap_by_module[].cap_use_gj_s`），不是整块一个键。"""
    stability = overview["views"]["stability"]
    cap_by_module = stability["overview"]["cap_by_module"]

    if cap_by_module:
        assert "cap_by_module[].cap_use_gj_s" in stability["provenance"]
    else:  # 没有耗电模块时整块折叠成一个键（结构差异，不是标签问题）
        assert "cap_by_module" in stability["provenance"]


# =============================================================================
# 三·B、实例级覆盖（T-27：值缺失 ⇒ unknown，覆盖静态表）
# =============================================================================


def test_dph_missing_value_is_unknown_and_present_value_keeps_the_table(
    rendered_samples: dict[str, dict],
) -> None:
    """**本轮核心验收**：`dph` 的三态跟着**值**走，不跟着属性表走。

    - `f4-non-hecate`：`dph` 是 `None`（有带弹药武器却零伤害，突变模块数据缺失）→
      `unknown`。旧口径给它 `assumed`（`-11` 在静态表里是 `assumed`），
      等于「值不可信，三态却说可信」——正是本轮要修的根因性缺陷。
    - `f1-hecate-killmail` / `f5-no-mwd`：`dph` 是数值 → **保持原样**
      （仍等于 `provenance.tag(-11)`），本轮改动不得让它们漂移。
    """
    from noobbaozi.project import provenance as P

    attribute_id = VIEW_MODULES["firepower"].FIELD_SOURCES["dph"]
    static = P.tag(int(attribute_id))

    f4 = rendered_samples["f4-non-hecate"]["views"]["firepower"]
    assert f4["overview"]["dph"] is None, "f4 的 dph 应仍是 None（矛盾判据，值本身就是缺陷证据）"
    assert f4["provenance"]["dph"] == "unknown", (
        f"f4.dph 值缺失却报 {f4['provenance']['dph']!r}——实例级覆盖没生效"
    )

    for stem in ("f1-hecate-killmail", "f5-no-mwd"):
        entry = rendered_samples[stem]["views"]["firepower"]
        value = entry["overview"]["dph"]
        assert isinstance(value, (int, float)) and value > 0, f"{stem}.dph 应有数值，实为 {value!r}"
        assert entry["provenance"]["dph"] == static, (
            f"{stem}.dph 有值时应保持静态表结论 {static!r}，实为 {entry['provenance']['dph']!r}"
        )


@pytest.mark.parametrize("stem", INSTANCE_SAMPLES)
def test_every_missing_value_leaf_is_unknown(rendered_samples: dict[str, dict], stem: str) -> None:
    """**通用断言**：任意视角里值为 `None` 的叶子，三态必须是 `unknown`（遍历全部叶子）。

    口径与渲染层一致：数组元素路径上**任一元素**缺值就算缺值（一条路径只有一个标签，
    不能比它最弱的那个出现更自信）。
    """
    rendered = rendered_samples[stem]
    violations: list[str] = []
    checked = 0

    for view_name, entry in rendered["views"].items():
        assert entry["provenance"], f"{view_name} 的 provenance 不该是空表"
        for path, occurrences in _leaf_values(entry["overview"]).items():
            if not _has_missing_value(occurrences):
                continue
            checked += 1
            state = entry["provenance"][path]
            if state != "unknown":
                violations.append(f"{stem}.{view_name}.{path}: 值缺失却报 {state!r}")

    assert violations == [], "值缺失的叶子必须是 unknown：\n" + "\n".join(violations)
    assert checked > 0, f"{stem} 没有任何缺值叶子（这条断言会变成空转，样本或规则变了）"


def test_mixed_array_element_path_with_missing_value_is_unknown(
    rendered_samples: dict[str, dict],
) -> None:
    """数组元素路径：**部分元素缺值** → `unknown`（保守方向，别只看第一个元素）。

    f4/f5 的 `weapons[].ammo_name` 一条有弹药、一条没有：路径只有一个标签，
    不能因为「有条目有值」就宣称整条路径是合成值 `assumed`。f1 的武器全部装填
    弹药，路径上每个元素都有值 → 仍是 `assumed`。
    """
    f4 = rendered_samples["f4-non-hecate"]["views"]["firepower"]
    occurrences = _leaf_values(f4["overview"])["weapons[].ammo_name"]
    assert None in occurrences and any(value is not None for value in occurrences), occurrences
    assert f4["provenance"]["weapons[].ammo_name"] == "unknown"

    f1 = rendered_samples["f1-hecate-killmail"]["views"]["firepower"]
    f1_occurrences = _leaf_values(f1["overview"])["weapons[].ammo_name"]
    assert not _has_missing_value(f1_occurrences), f1_occurrences
    assert f1["provenance"]["weapons[].ammo_name"] == "assumed"


STATIC_TABLE_SAMPLES: tuple[tuple[str, str, int | None], ...] = (
    # (视角, 叶子路径, FIELD_SOURCES 里的来源属性；None = 合成值)
    ("firepower", "damage_split.em", 114),  # measured
    ("firepower", "dph", -11),  # assumed（负 ID 派生）
    ("firepower", "weapons[].name", None),  # 合成值
    ("mobility", "warp_speed_au_s", 600),  # measured
    ("survivability", "layers.shield_hp", 263),  # assumed（技能轴）
    ("constraints", "skill_floor.count_by_level.4", None),  # 整数键容器继承
    ("stability", "cap_by_module[].cap_use_gj_s", -4),  # 数组元素约定 + 负 ID
)
"""值非 `None` 的对照项：三态必须与静态表 `tag()` 逐条一致（本轮不得漂移）。"""


@pytest.mark.parametrize(("view_name", "path", "attribute_id"), STATIC_TABLE_SAMPLES)
def test_present_values_still_follow_the_static_table(
    rendered_samples: dict[str, dict], view_name: str, path: str, attribute_id: int | None
) -> None:
    """值存在的叶子：三态 == 静态表结论（`None` 来源 → `assumed`，否则 `tag(ID)`）。

    这几条都是**有值**的叶子，所以实例级覆盖不该生效 —— 一变就说明本轮改过头了。
    """
    from noobbaozi.project import provenance as P

    entry = rendered_samples["f1-hecate-killmail"]["views"][view_name]
    occurrences = _leaf_values(entry["overview"])[path]  # KeyError = 结构变了，直接炸
    assert not _has_missing_value(occurrences), f"{path} 本是有值对照项，结构/数据变了"

    expected = "assumed" if attribute_id is None else P.tag(int(attribute_id))
    assert entry["provenance"][path] == expected, (
        f"{view_name}.{path} 有值时应为 {expected!r}，实为 {entry['provenance'][path]!r}"
    )


# =============================================================================
# 三·C、状态敏感性探测（T-30：第二条实例规则，按配装实测）
# =============================================================================

SHIELD_RESONANCE_ATTRS: tuple[int, ...] = (271, 272, 273, 274)
"""护盾四系共振率（`271`–`274`）。f4 有盾抗模块（`Pithum C-Type Multispectrum Shield
Hardener`）→ 状态敏感；f1（Hecate）没有 → 不敏感（PROGRESS §3 第 166/167 条）。"""

PASSIVE_EFT = "[Rifter, 纯被动]\nDamage Control II\nGyrostabilizer II\nReactor Control Unit II"
"""纯被动配装（三件 `online`-only 模块）：全 online 与全 active **逐键相等**。

用来钉住「没有 active 能力的模块 → 空集 + 零额外计算」：这艘船上把 `state` 写成
`active` 也会被引擎按 `max_state` 夹回 `online`（实测）。
"""


def _render_misses(engine: Engine, ctx: ViewContext) -> int:
    """清空引擎缓存后渲染一次 overview，返回这一次真正发生的计算次数（`misses`）。"""
    engine.clear_cache()
    render.render_overview(ctx)
    return engine.cache_stats()["misses"]


def test_state_sensitivity_set_contains_shield_resonances_on_f4(
    engine: Engine, index
) -> None:
    """**f4（有盾抗模块）**：状态敏感性集合必须包含 `271`–`274`（探针命中路径）。

    这是本轮的靶心：盾抗模块 `active` 才给抗性（`271` 从 `0.5` → `0.2969`），所以这套
    配装的护盾共振率**依赖模块状态** —— 不靠任何模块清单，靠「两次算出来的值不一样」。
    """
    sensitive = render.state_sensitive_attrs(_make_ctx(engine, index, "f4-non-hecate"))

    assert 271 in sensitive, f"f4 的敏感性集合里没有 271：{sorted(sensitive)}"
    assert set(SHIELD_RESONANCE_ATTRS) <= sensitive, sorted(sensitive)
    # 同一份探测还自动覆盖了主动盾修 / 分层 EHP 这类"没被点名"的状态敏感属性
    assert {A.ATTR_EHP, A.ATTR_SHIELD_BOOST_RATE} <= sensitive
    # 集合是 set[int]，不是路径 / 名称 / 模块族黑名单
    assert all(isinstance(attribute_id, int) for attribute_id in sensitive)


def test_state_sensitivity_set_excludes_shield_resonances_on_f1(engine: Engine, index) -> None:
    """**f1（Hecate，无盾抗模块）**：`271`–`274` 不得进集合（静态表结论原样生效）。

    Hecate 样本上也有状态敏感属性（MWD 的 `4 mass` / `552 signatureRadius`），所以这条
    断言不是"整船都不敏感"，而是"**这一份配装**没有护盾共振率敏感性"——同一属性在不同
    配装上结论不同，正是这条规则存在的理由。
    """
    sensitive = render.state_sensitive_attrs(_make_ctx(engine, index, "f1-hecate-killmail"))

    assert not (set(SHIELD_RESONANCE_ATTRS) & sensitive), sorted(sensitive)
    assert {A.ATTR_MASS, A.ATTR_SIGNATURE_RADIUS} <= sensitive, sorted(sensitive)


def test_state_sensitivity_probe_costs_at_most_two_calculations(
    engine: Engine, index, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**成本护栏**：探测最多多算 2 次（online / active），与视角数无关。

    口径：同一份 f4 配装，一次开着探测渲染、一次把 `state_sensitive_attrs` 换成直接返回
    空集，比较引擎 `misses`。上下界都要卡：

    - 上界 **2**：六个视角各算一遍会是 6+，这里是 1（实测）——因为 `mobility` 视角本来
      就会算「全 active」那一份（`ACTIVE_VARIANT = "mobility:active"`），探测那一次直接命中
      引擎 LRU；于是真正新增的只有「全 online」一次。这正是「不重复算」的期望行为，
      所以断言是 `<= 2` 而不是 `== 2`；
    - 下界 **> 0**：等于 0 说明探测器根本没跑（这条断言就变成了空转）。
    """
    with_probe = _render_misses(engine, _make_ctx(engine, index, "f4-non-hecate"))

    monkeypatch.setattr(render, "state_sensitive_attrs", lambda _ctx: set())
    without_probe = _render_misses(engine, _make_ctx(engine, index, "f4-non-hecate"))

    delta = with_probe - without_probe
    assert delta > 0, "状态敏感性探测一次引擎调用都没发生（探测器没接上）"
    assert delta <= 2, f"状态敏感性探测多算了 {delta} 次（上限 2 次）"


def test_state_sensitivity_set_is_cached_on_the_context(engine: Engine, index) -> None:
    """同一份 ctx 再渲染一次：**一次引擎调用都不多**（online/active 留在 ctx 缓存里）。"""
    ctx = _make_ctx(engine, index, "f4-non-hecate")
    render.render_overview(ctx)

    engine.clear_cache()
    render.render_overview(ctx)

    assert engine.cache_stats()["misses"] == 0, "第二次渲染又碰了引擎（缓存没复用）"


def test_passive_fit_gets_empty_set_and_no_extra_calculations(
    engine: Engine, index, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**全 online == 全 active 的配装 → 空集、零额外计算**（成本控制的第二条）。"""
    fit = engine.load_eft(PASSIVE_EFT)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}

    def factory() -> ViewContext:
        return ViewContext(
            engine=engine,
            index=index,
            fit=fit,
            assumptions=default_assumptions(fit, engine, index=index),
            meta=engine.meta(),
        )

    assert render.state_sensitive_attrs(factory()) == set()

    with_probe = _render_misses(engine, factory())
    monkeypatch.setattr(render, "state_sensitive_attrs", lambda _ctx: set())
    without_probe = _render_misses(engine, factory())

    assert with_probe == without_probe, (
        f"纯被动配装上多算了 {with_probe - without_probe} 次引擎调用（应当为 0）"
    )


def test_state_sensitive_field_is_assumed_only_where_the_value_moves(
    engine: Engine, index, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**升格只在「值真的变了」的那份配装上发生**（规则②的两种走向）。

    今天静态表已把 `271`–`274` 保守判成 `assumed`，所以真实样本上这条规则**没有可见变化**
    ——为了把「探测器确实接到了 provenance 判定上」钉死，这里**模拟**静态表收紧回
    `measured` 的形态（T-14 之前的表），并把 `survivability` 的一个真实叶子临时指到 `271`：

    - f4（有盾抗模块）：`271` 在敏感性集合里 → 该叶子 `assumed`（静态表被覆盖）；
    - f1（无盾抗模块）：`271` 不在集合里 → 该叶子 `measured`（静态表原样生效）。

    两个方向都验，才能同时排除「没接上」与「不分配装一律升格」。
    """
    from noobbaozi.project import provenance as P

    attribute_id = A.ATTR_SHIELD_EM_RESONANCE  # 271
    monkeypatch.setattr(P, "MEASURED_ATTRS", P.MEASURED_ATTRS | {attribute_id})
    monkeypatch.setattr(P, "ASSUMED_ATTRS", P.ASSUMED_ATTRS - {attribute_id})
    monkeypatch.setitem(
        VIEW_MODULES["survivability"].FIELD_SOURCES, "layers.shield_hp", attribute_id
    )
    monkeypatch.setattr(render, "BUDGET_CHARS", BIG_BUDGET)

    assert P.tag(attribute_id) == "measured", "补丁没生效：静态表里 271 仍是 assumed"

    f4 = render.render_overview(_make_ctx(engine, index, "f4-non-hecate"))
    f1 = render.render_overview(_make_ctx(engine, index, "f1-hecate-killmail"))

    assert f4["views"]["survivability"]["overview"]["layers"]["shield_hp"] is not None
    assert f4["views"]["survivability"]["provenance"]["layers.shield_hp"] == "assumed", (
        "f4 有盾抗模块、值随状态变，静态表的 measured 必须被覆盖成 assumed"
    )
    assert f1["views"]["survivability"]["overview"]["layers"]["shield_hp"] is not None
    assert f1["views"]["survivability"]["provenance"]["layers.shield_hp"] == "measured", (
        "f1 没有盾抗模块，静态表的 measured 不该被这条规则改动"
    )


def test_f4_shield_resonance_fields_are_assumed(rendered_samples: dict[str, dict]) -> None:
    """**f4 的 survivability 字段里，凡是来源为 `271`–`274` 的必须是 `assumed`。**

    现状：`survivability` 的护盾共振率**没有**直接登记成来源（`ehp_by_damage.*` /
    `thinnest.*` 是三层共振率 + 三层 HP 的合成值 → `FIELD_SOURCES` 里是 `None`），
    所以这条循环今天扫不到任何字段 —— 它是给「以后有视角直接登记 `271`–`274`」准备的护栏，
    真正证明探测器接上了的是
    `test_state_sensitive_field_is_assumed_only_where_the_value_moves()`。
    下面同时钉住「承载护盾抗性的字段当前确实是 assumed」（合成值 + 静态表双重保证）。
    """
    f4 = rendered_samples["f4-non-hecate"]["views"]["survivability"]
    sources = VIEW_MODULES["survivability"].FIELD_SOURCES

    for path, attribute_id in sources.items():
        if attribute_id not in SHIELD_RESONANCE_ATTRS or path not in f4["provenance"]:
            continue
        assert f4["provenance"][path] == "assumed", (
            f"f4.{path} 来源是状态敏感的 {attribute_id}，必须 assumed"
        )

    for path in ("ehp_mixed", "ehp_by_damage.em", "thinnest.shield.ehp", "layer_ehp.shield"):
        assert f4["provenance"][path] == "assumed", f"f4.{path} 依赖护盾共振率，必须 assumed"


def test_missing_value_rule_still_wins_while_state_sensitivity_is_on(
    rendered_samples: dict[str, dict], engine: Engine, index
) -> None:
    """**缺值规则仍然优先**：f4 的敏感性集合非空，但值 `None` 的叶子照样是 `unknown`。

    没有这条，第二条规则会把「不可知」包成「有假设支撑」（`dph` 的教训）。前提断言
    （集合非空）保证这不是空转：f4 上确实探测出了状态敏感属性。
    """
    assert render.state_sensitive_attrs(_make_ctx(engine, index, "f4-non-hecate"))

    firepower = rendered_samples["f4-non-hecate"]["views"]["firepower"]
    assert firepower["overview"]["dph"] is None
    assert firepower["provenance"]["dph"] == "unknown"


# =============================================================================
# 四、诊断：只算一次 + 归位 + 顶层兜底
# =============================================================================


def test_diagnostics_evaluated_only_once(ctx: ViewContext, monkeypatch) -> None:
    """`diagnostics.evaluate` 每轮渲染只被调用一次（不许六个视角各算一遍）。"""
    calls: list[int] = []
    original = D.evaluate

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    monkeypatch.setattr(D, "evaluate", counting)
    monkeypatch.setattr(render, "BUDGET_CHARS", BIG_BUDGET)

    render.render_overview(ctx)

    assert len(calls) == 1, f"诊断被算了 {len(calls)} 次，应该只算 1 次"


def test_flags_are_attributed_to_their_view(ctx: ViewContext, overview: dict) -> None:
    """每条诊断都落在 `views[flag["view"]]["flags"]` 里，一条不多一条不少。"""
    expected = D.evaluate(ctx)
    assert expected, "样本配装不该一条可疑点都没有"
    assert overview["flags"] == [], "六视角都渲染时不该有兜底 flag"

    attributed = [
        flag for entry in overview["views"].values() for flag in entry["flags"]
    ]
    assert sorted(_flag_ids(attributed)) == sorted(_flag_ids(expected))

    for flag in expected:
        bucket = overview["views"][flag["view"]]["flags"]
        assert any(
            entry["id"] == flag["id"] and entry["message"] == flag["message"]
            for entry in bucket
        ), f"{flag['id']} 没有落在 {flag['view']} 下"


def test_subset_views_keeps_other_flags_in_top_level_bucket(ctx: ViewContext) -> None:
    """只渲染一个视角时，其它视角的 flag 进顶层兜底桶（**不丢诊断**）。"""
    original_budget = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        subset = render.render_overview(ctx, views=("mobility",))
    finally:
        render.BUDGET_CHARS = original_budget

    assert tuple(subset["views"]) == ("mobility",)
    own = set(_flag_ids(subset["views"]["mobility"]["flags"]))
    residual = set(_flag_ids(subset["flags"]))
    total = set(_flag_ids(D.evaluate(ctx)))

    assert not own & residual, "同一条 flag 不该既在视角下又在兜底桶"
    assert own | residual == total


def test_render_overview_rejects_unknown_view(ctx: ViewContext) -> None:
    """未注册的视角名 → `KeyError`（不返回空壳）。"""
    with pytest.raises(KeyError):
        render.render_overview(ctx, views=("no_such_view",))


# =============================================================================
# 五、红线 N5：渲染层不做任何单位换算
# =============================================================================


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_render_passes_view_output_through_unchanged(
    ctx: ViewContext, overview: dict, view_name: str
) -> None:
    """渲染结果 **完全等于** `build_view()` 的结果（没有乘除 1000、没有取整）。

    唯一允许的差异是 `OVERVIEW_DOWNGRADES` 登记的**粒度改写**（不是单位换算）：
    这几条路径在 overview 里换成摘要，而视角自身仍然产出完整原样（下面逐条验证）。
    """
    rendered = overview["views"][view_name]["overview"]
    source = build_view(view_name, ctx)["overview"]
    rewritten = tuple(render.OVERVIEW_DOWNGRADES.get(view_name) or {})

    assert set(rendered) == set(source), f"{view_name} 的键名被渲染层改了"
    for key in set(source) - set(rewritten):
        assert rendered[key] == source[key], f"{view_name}.{key} 被渲染层改动了"

    for path in rewritten:
        assert isinstance(source[path], list), f"{view_name}.{path} 在视角里必须是列表"
        summary = rendered[path]
        assert isinstance(summary, dict) and summary, f"{view_name}.{path} 应换成摘要"
        assert all(not isinstance(value, (list, dict)) for value in summary.values())

    # 视角自己的 flags 恒为空（诊断在渲染层算），渲染后只多出归位来的诊断 flag
    assert build_view(view_name, ctx)["flags"] == []


def test_overview_gives_upgrade_space_summary_and_detail_gives_full_list(
    ctx: ViewContext, overview: dict
) -> None:
    """`resources.upgrade_space`：overview = 摘要，detail = **完整原样**的候选清单。"""
    summary = overview["views"]["resources"]["overview"]["upgrade_space"]
    full = render.render_detail(ctx, view="resources")["overview"]["upgrade_space"]

    assert set(summary) == {"slots_checked", "upgradeable_count", "blocked_by_resource"}
    assert isinstance(summary["slots_checked"], int) and summary["slots_checked"] >= 1
    assert isinstance(summary["upgradeable_count"], int)
    assert isinstance(summary["blocked_by_resource"], bool)

    assert isinstance(full, list) and full, "detail 必须给完整候选清单"
    assert summary["slots_checked"] == len(full)
    assert summary["upgradeable_count"] == sum(1 for row in full if row["fits"] is True)
    assert summary["blocked_by_resource"] is (summary["upgradeable_count"] == 0)
    # 完整清单原样：每行都带槽位/模块/候选/能否装下这四项台账
    assert all({"slot", "module", "candidate", "fits"} <= set(row) for row in full)


def test_render_module_has_no_unit_conversion_code() -> None:
    """静态守卫：渲染层不 import `units`、也不出现裸的 1000 缩放（红线 N5）。"""
    source = RENDER_PATH.read_text(encoding="utf-8")

    assert "project import units" not in source
    assert "from noobbaozi.project import units" not in source
    for token in ("* 1000", "/ 1000", "ms_to_s", "s_to_ms"):
        assert token not in source, f"渲染层出现疑似单位换算：{token!r}"


# =============================================================================
# 六、预算护栏
# =============================================================================


def test_big_budget_keeps_output_undegraded(overview: dict) -> None:
    """预算足够大时不降级，`chars` = `len(json.dumps(views))` + `len(json.dumps(modes))`。"""
    assert overview["budget"]["degraded"] is False
    assert overview["budget"]["chars"] == _measure(overview["views"], overview["modes"])
    for entry in overview["views"].values():
        for flag in entry["flags"]:
            assert "evidence" in flag, "未降级时 flag 必须保留 evidence"


def test_default_budget_fits_real_sample(ctx: ViewContext) -> None:
    """预算按实测重定后，**默认路径不降级**（`degraded is False`）。

    T-18 实测（`confidence` 稀疏化后）：`views` **13,742**（= T-15 的 13,519 + 3 条 `E` 的
    **223**；T-17 的全量平行表曾是 +5,093）+ `modes` **341** = **14,083 chars** <
    `BUDGET_CHARS` 17,000（= `ceil(14,083 × 1.2 / 1000) × 1000`）。

    若这条炸了：说明真实样本的 `chars` 又超过 `BUDGET_CHARS` —— 先重测再按
    `ceil(实测 × 1.2 / 1000) × 1000` 重定预算，不要靠放宽断言过关，也不要靠把
    `confidence` 写回全量表来「凑」。
    """
    rendered = render.render_overview(ctx)

    assert rendered["budget"]["degraded"] is False, "默认预算装不下真实样本，先重测 chars 再改 BUDGET_CHARS"
    assert rendered["budget"]["chars"] == _measure(rendered["views"], rendered["modes"])
    assert rendered["budget"]["chars"] <= render.BUDGET_CHARS


T44_BUDGET_SAMPLES: tuple[str, ...] = (
    "f47-random-raptor",
    "f67-spotcheck-139255942",
    "f12-logistics-oneiros",
)
"""T-44 预算裁决的三个样本（stem 形式，`_load_instance_fit()` 自己配输入文件）。

T-43 加 `propulsion_states.single` 后的 L1 原始体量：f47 16,885 → **17,327**、
f67 16,723 → **17,269**、f12 18,178 → **18,631**（f12 在 17,000 下本来就降级）；
T-44 的 evidence 合并（`sources` 数组）再 +23~45。17,000 下 f47/f67 **新增降级**，
代价是 `flags` 全部丢 `evidence` + 两个列表截到 5 项 → 裁决抬到 **19,000**。
"""


@pytest.mark.parametrize("stem", T44_BUDGET_SAMPLES)
def test_default_budget_fits_t44_samples(engine: Engine, index, stem: str) -> None:
    """`BUDGET_CHARS = 19,000` 下 f47 / f67 / f12 **全部 `degraded == False`**。

    改前（17,000）：三者 `degraded` 全为 True，`flags` 全部丢 `evidence`
    —— 那不是「少几行」，是诊断证据整块消失（PROGRESS §3 第 256 条②）。
    若这条炸了：说明真实载荷又超过 19,000，先按实测重算再定预算，
    不要靠放宽断言过关（`render.BUDGET_CHARS` 的 docstring 有撤销条件）。
    """
    rendered = render.render_overview(_make_ctx(engine, index, stem))

    assert rendered["budget"]["degraded"] is False, f"{stem}: 19,000 下不该降级"
    assert rendered["budget"]["chars"] == _measure(rendered["views"], rendered["modes"])
    assert rendered["budget"]["chars"] <= render.BUDGET_CHARS
    # 未降级 = 诊断证据还完整（降级的第一档就是抹掉它）
    assert any(entry["flags"] for entry in rendered["views"].values()), f"{stem}: 前提：必须有 flag"
    for entry in rendered["views"].values():
        for flag in entry["flags"]:
            assert "evidence" in flag, f"{stem}: 未降级时 {flag['id']} 必须保留 evidence"


def test_tiny_budget_degrades_but_keeps_numbers(
    ctx: ViewContext, overview: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预算调到极小 → `degraded=True`、截断列表、去 evidence，**数值一个都不改**。"""
    monkeypatch.setattr(render, "BUDGET_CHARS", 1)
    degraded = render.render_overview(ctx)

    assert degraded["budget"]["degraded"] is True
    assert degraded["budget"]["chars"] == _measure(degraded["views"], degraded["modes"])

    # ① 可截断列表被截到前 5 项；② 所有 flag 都去掉了 evidence（含顶层兜底桶）
    for name, entry in degraded["views"].items():
        for key in render.TRUNCATABLE_LISTS:
            value = entry["overview"].get(key)
            if isinstance(value, list):
                assert len(value) <= render.TRUNCATE_TO, f"{name}.{key} 没被截断"
        for flag in entry["flags"]:
            assert "evidence" not in flag, f"{name} 的 flag 降级后仍带 evidence"
    for flag in degraded["flags"]:
        assert "evidence" not in flag

    # ③ 数值未被改动：抽一个字段逐值比对 + 全视角遮住被截断的列表后整体比对
    assert (
        degraded["views"]["mobility"]["overview"]["align_time_s"]
        == overview["views"]["mobility"]["overview"]["align_time_s"]
    )
    for name in VIEW_ORDER:
        assert _mask_truncatable(degraded["views"][name]["overview"]) == _mask_truncatable(
            overview["views"][name]["overview"]
        ), f"{name} 的数值被降级改动了"

    # ④ provenance / unknown_reasons / confidence 不因降级而变（三张表的键空间与取值都不该动）
    for name in VIEW_ORDER:
        assert degraded["views"][name]["provenance"] == overview["views"][name]["provenance"]
        assert (
            degraded["views"][name]["unknown_reasons"]
            == overview["views"][name]["unknown_reasons"]
        )
        assert degraded["views"][name]["confidence"] == overview["views"][name]["confidence"]


def test_degradation_never_adds_or_drops_flags(
    ctx: ViewContext, overview: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """降级只删 `evidence`，flag 的 id / view / message 全保留。"""
    monkeypatch.setattr(render, "BUDGET_CHARS", 1)
    degraded = render.render_overview(ctx)

    for name in VIEW_ORDER:
        before = overview["views"][name]["flags"]
        after = degraded["views"][name]["flags"]
        assert _flag_ids(before) == _flag_ids(after)
        assert [flag["message"] for flag in before] == [flag["message"] for flag in after]


# =============================================================================
# 七、detail：单视角 + 字段裁剪 + missing
# =============================================================================


def test_render_detail_returns_full_single_view(overview: dict, ctx: ViewContext) -> None:
    """不带 `fields` 时返回该视角完整 overview + provenance + unknown_reasons + confidence
    + flags。"""
    detail = render.render_detail(ctx, view="stability")
    entry = overview["views"]["stability"]

    assert set(detail) == {
        "meta",
        "view",
        "overview",
        "provenance",
        "unknown_reasons",
        "confidence",
        "flags",
        "missing",
    }
    assert detail["view"] == "stability"
    assert detail["meta"]["scope"] == "detail"
    assert detail["overview"] == entry["overview"]
    assert detail["provenance"] == entry["provenance"]
    assert detail["unknown_reasons"] == entry["unknown_reasons"]
    assert detail["confidence"] == entry["confidence"]
    assert _flag_ids(detail["flags"]) == _flag_ids(entry["flags"])
    assert detail["missing"] == []


def test_render_detail_fields_return_only_requested_paths(ctx: ViewContext) -> None:
    """`fields` 非空时只返回指定点路径（保留嵌套形状）+ 同口径裁剪的两个平行表。"""
    detail = render.render_detail(
        ctx, view="mobility", fields=["speed_mps", "propulsion_states.off"]
    )

    assert set(detail["overview"]) == {"speed_mps", "propulsion_states"}
    assert set(detail["overview"]["propulsion_states"]) == {"off"}
    assert set(detail["overview"]["propulsion_states"]["off"]) == {
        "speed_mps",
        "sig_m",
        "align_time_s",
    }
    assert detail["missing"] == []
    # provenance 只保留被请求字段下的叶子；confidence 是稀疏表（mobility 全默认 → `{}`）
    assert detail["provenance"]
    assert set(detail["confidence"]) <= set(detail["provenance"])
    assert detail["confidence"] == {}, "mobility 没有非默认档，稀疏表应是空对象"
    for path in detail["provenance"]:
        assert path == "speed_mps" or path.startswith("propulsion_states.off.")


def test_detail_confidence_keeps_sparse_semantics_when_fields_are_clipped(
    ctx: ViewContext,
) -> None:
    """detail 的字段裁剪沿用同一套稀疏口径：只留下**被请求字段下**的非 A 项。"""
    non_default = render.render_detail(
        ctx, view="firepower", fields=["application.tracking_rad_s"]
    )
    assert non_default["missing"] == []
    assert non_default["confidence"] == {"application.tracking_rad_s": "E"}

    default_only = render.render_detail(ctx, view="firepower", fields=["dps.base"])
    assert default_only["missing"] == []
    assert set(default_only["provenance"]) == {"dps.base"}
    assert default_only["confidence"] == {}, "默认档不写出，裁剪后应保持空对象"


def test_render_detail_reports_missing_fields(ctx: ViewContext) -> None:
    """找不到的字段名进 `missing`，**不静默忽略**。"""
    detail = render.render_detail(
        ctx, view="mobility", fields=["speed_mps", "no_such_field", "propulsion_states.off.bogus"]
    )

    assert detail["missing"] == ["no_such_field", "propulsion_states.off.bogus"]
    assert set(detail["overview"]) == {"speed_mps"}


def test_render_detail_supports_array_element_paths(ctx: ViewContext) -> None:
    """`cap_by_module[].cap_use_gj_s` 这样的数组元素路径也能裁剪。"""
    full = render.render_detail(ctx, view="stability")["overview"]["cap_by_module"]
    detail = render.render_detail(
        ctx, view="stability", fields=["cap_by_module[].cap_use_gj_s"]
    )

    picked = detail["overview"]["cap_by_module"]
    assert [item["cap_use_gj_s"] for item in picked] == [
        item["cap_use_gj_s"] for item in full
    ]
    assert all(set(item) == {"cap_use_gj_s"} for item in picked)


def test_render_detail_rejects_unknown_view(ctx: ViewContext) -> None:
    """未注册的视角名 → `KeyError`。"""
    with pytest.raises(KeyError):
        render.render_detail(ctx, view="no_such_view")


# =============================================================================
# 八、C1：顶层 modes（T3D/T3C 三模式对比）
# =============================================================================

MODE_KEYS = frozenset({"mode_id", "mode_name", "ehp_mixed", "dps_base", "align_time_s"})
"""`modes` 每一项的键集合（字段规格 §10.2）。"""

MODE_FIELDS = {"ehp_mixed": "ehp", "dps_base": "dps", "align_time_s": "align_time_s"}
"""输出字段 → baseline `modes` 的字段名（两边命名不同，语义一一对应）。"""


def test_modes_key_is_always_present(overview: dict) -> None:
    """`modes` 恒在顶层（无模式时 `[]`，调用方不必判 `in`）。"""
    assert "modes" in overview
    assert isinstance(overview["modes"], list)


def test_hecate_exposes_three_named_modes(overview: dict, index) -> None:
    """Hecate → 恰好三条：Defense / Propulsion / Sharpshooter（按 mode type_id 升序）。"""
    modes = overview["modes"]

    assert [row["type_id"] for row in index.modes_for_ship(HECATE)] == [35686, 35687, 35688]
    assert [entry["mode_id"] for entry in modes] == [35686, 35687, 35688]
    assert [entry["mode_name"] for entry in modes] == ["Defense", "Propulsion", "Sharpshooter"]

    for entry in modes:
        assert set(entry) == MODE_KEYS
        assert isinstance(entry["mode_id"], int)
        for field in MODE_FIELDS:
            value = entry[field]
            assert isinstance(value, float) and value > 0, f"{entry['mode_name']}.{field}={value!r}"
            # 输出层按字段规格 round(..., 3)
            assert value == round(value, render.MODE_DIGITS), f"{field} 没取到 3 位"


def test_confessor_and_svipul_also_expose_three_named_modes(
    engine: Engine, index
) -> None:
    """**T-23 回归**：非 Hecate 的 T3D（Confessor 34317 / Svipul 34562）也必须给 3 条模式。

    背景：验收报告曾报「f10/f11 的 `modes[*]` 缺失 = 6 条 high」，根因是**验收器**的模式名
    归一化写死 `"Hecate "`（假缺陷，产品侧从来没断）。这条把产品侧的承诺钉死：
    两艘船各 3 条、名字就是 `Defense / Propulsion / Sharpshooter`（船名前缀必须被剥掉）、
    `mode_id` 与索引层一致，且**默认预算下 modes 不被降级清空**。
    """
    for ship_id, eft_text, expected in (
        (
            CONFESSOR,
            "[Confessor, t3d modes]\nLight Neutron Blaster II, Void S\nDamage Control II\n1MN Afterburner II",
            [(34319, "Defense"), (34321, "Sharpshooter"), (34323, "Propulsion")],
        ),
        (
            SVIPUL,
            "[Svipul, t3d modes]\n200mm AutoCannon II, EMP S\nDamage Control II\n1MN Afterburner II",
            [(34564, "Defense"), (34566, "Propulsion"), (34570, "Sharpshooter")],
        ),
    ):
        ship_name = eft_text[1 : eft_text.index(",")]
        fit = engine.load_eft(eft_text)
        fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
        ship_ctx = ViewContext(
            engine=engine,
            index=index,
            fit=fit,
            assumptions=default_assumptions(fit, engine, index=index),
            meta=engine.meta(),
        )
        rendered = render.render_overview(ship_ctx)
        modes = rendered["modes"]

        # 索引层能查到 → 渲染层就必须输出（不许中间某一层悄悄丢掉）
        assert [(row["type_id"], row["name"]) for row in index.modes_for_ship(ship_id)] == [
            (mode_id, f"{ship_name} {name} Mode") for mode_id, name in expected
        ]
        assert len(modes) == 3, f"{ship_name} 的 modes 长度 {len(modes)}：{modes}"
        # id → 短名 的映射逐条对齐（串船 / 串模式都会在这里炸）
        assert [(entry["mode_id"], entry["mode_name"]) for entry in modes] == expected
        assert {entry["mode_name"] for entry in modes} == set(HECATE_MODE_TOKENS)
        # 默认预算下 modes 不得被降级清空（清空会伪装成「这船没有模式」）
        assert rendered["budget"]["degraded"] is False

        by_name = {entry["mode_name"]: entry for entry in modes}
        for entry in modes:
            assert set(entry) == MODE_KEYS
            for field in MODE_FIELDS:
                assert isinstance(entry[field], float), f"{ship_name}.{entry['mode_name']}.{field}"
        # 方向性（两条对所有 T3D 都成立）：Defense 更硬、Propulsion 更快对齐
        assert by_name["Defense"]["ehp_mixed"] > by_name["Propulsion"]["ehp_mixed"]
        assert by_name["Propulsion"]["align_time_s"] < by_name["Defense"]["align_time_s"]


def test_mode_name_stripping_does_not_depend_on_a_hardcoded_ship() -> None:
    """`_mode_name` 必须**只按传入的船名**剥前缀 —— 任何船名都得工作（T-23 根因钉住）。

    验收器那次的假缺陷就是「写死 `Hecate `」：换一艘船名就判「缺模式」。产品侧这条
    纯函数是同一类风险的另一半，所以用三个形状不同的船名钉住（含「船名是模式词前缀」
    这种最容易剥错的形态）。
    """
    cases = (
        ("Hecate Defense Mode", "Hecate", "Defense"),
        ("Confessor Sharpshooter Mode", "Confessor", "Sharpshooter"),
        ("Svipul Propulsion Mode", "Svipul", "Propulsion"),
        ("Anhinga Primary Mode", "Anhinga", "Primary"),
        ("X Xray Mode", "X", "Xray"),  # 船名是模式词的前缀：只能剥一次
    )
    for raw, ship_name, expected in cases:
        assert render._mode_name(raw, ship_name) == expected, (raw, ship_name)
    # 船名给不出来时只去 ` Mode` 后缀，**不改写词**
    assert render._mode_name("Hecate Defense Mode", None) == "Hecate Defense"


def test_mode_effects_point_in_the_expected_direction(mode_ctx: ViewContext) -> None:
    """三条模式的影响方向必须对（防止 mode_id 串位）：Defense 抗、Propulsion 机动、
    Sharpshooter 火力。"""
    modes = {entry["mode_name"]: entry for entry in render.render_overview(mode_ctx)["modes"]}

    assert modes["Defense"]["ehp_mixed"] > modes["Propulsion"]["ehp_mixed"]
    assert modes["Propulsion"]["align_time_s"] < modes["Defense"]["align_time_s"]
    assert modes["Sharpshooter"]["dps_base"] > modes["Defense"]["dps_base"]


def test_modes_align_with_authoritative_baseline(mode_ctx: ViewContext, baseline: dict) -> None:
    """三模式数值与 `tools/baseline_after_tierfix.json` 的权威快照对齐。

    两边字段名不同（`ehp_mixed`↔`ehp`、`dps_base`↔`dps`），只对**能对上**的：
    权威值是 6 位小数，输出层是 `round(..., 3)`，所以先对权威值取同样位数，
    再要求差 ≤ 1e-6（不是放宽，而是同一精度下逐位相同）。
    """
    rendered = render.render_overview(mode_ctx)
    assert rendered["budget"]["degraded"] is False

    by_name = {entry["mode_name"]: entry for entry in rendered["modes"]}
    expected = {name: row for name, row in baseline["modes"].items() if name != "none"}

    assert set(by_name) == set(expected), f"模式名不齐：{sorted(by_name)} != {sorted(expected)}"
    for name, row in expected.items():
        ours = by_name[name]
        assert ours["mode_id"] == row["mode_type_id"], f"{name} 的 mode_id 对不上"
        for output_field, baseline_field in MODE_FIELDS.items():
            want = round(row[baseline_field], render.MODE_DIGITS)
            assert abs(ours[output_field] - want) <= 1e-6, (
                f"{name}.{output_field}：输出 {ours[output_field]!r} != 权威 {want!r}"
            )


def test_mode_block_shares_the_views_derivation(mode_ctx: ViewContext, baseline: dict) -> None:
    """`modes` 的 `none` 那一行 == 六视角的 `-43 / -12 / -1`（同一套口径、同一次换算）。

    这条把「模式块自己另算一套」钉死：三个字段分别对上 `survivability.ehp_mixed` /
    `firepower.dps.base` / `mobility.align_time_s`。三个视角自己也是 3 位小数，
    所以对权威值同样取 3 位再比（同一精度下逐位相同）。
    """
    rendered = render.render_overview(mode_ctx)
    none = baseline["modes"]["none"]

    assert rendered["views"]["survivability"]["overview"]["ehp_mixed"] == pytest.approx(
        round(none["ehp"], render.MODE_DIGITS), abs=1e-6
    )
    assert rendered["views"]["firepower"]["overview"]["dps"]["base"] == pytest.approx(
        round(none["dps"], render.MODE_DIGITS), abs=1e-6
    )
    assert rendered["views"]["mobility"]["overview"]["align_time_s"] == pytest.approx(
        round(none["align_time_s"], render.MODE_DIGITS), abs=1e-6
    )


def test_no_mode_ship_returns_empty_modes_and_does_no_extra_work(engine: Engine, index) -> None:
    """非 T3D（Rifter 587）→ `modes == []`，且**一次带 mode 的计算都没有**。"""
    fit = engine.load_eft("[Rifter, no mode]\n")
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}

    recorder = ModeRecordingEngine(engine)
    ctx = ViewContext(
        engine=recorder,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )
    rendered = render.render_overview(ctx)

    assert index.modes_for_ship(RIFTER) == []
    assert rendered["modes"] == []
    assert recorder.mode_calls == [], "无模式船上发生了带 mode 的计算（空转）"


def test_modes_empty_when_index_is_missing(engine: Engine, sample_fit: dict) -> None:
    """`index is None` → `modes: []`（不报错；也是「取不到模式就别猜」）。"""
    ctx = ViewContext(
        engine=engine,
        index=None,
        fit=sample_fit,
        assumptions=default_assumptions(sample_fit, engine, index=None),
        meta=engine.meta(),
    )

    assert render.render_overview(ctx, views=("mobility",))["modes"] == []


def test_modes_reuse_the_instance_cache(engine: Engine, index, sample_fit: dict) -> None:
    """同一份 ctx 渲染两次，每个模式**只算一次**（`ctx.calculate_variant` 缓存）。"""
    recorder = ModeRecordingEngine(engine)
    ctx = ViewContext(
        engine=recorder,
        index=index,
        fit=sample_fit,
        assumptions=default_assumptions(sample_fit, engine, index=index),
        meta=engine.meta(),
    )

    first = render.render_overview(ctx)
    second = render.render_overview(ctx)

    assert len(first["modes"]) == 3
    assert second["modes"] == first["modes"]
    assert len(recorder.mode_calls) == 3, (
        f"模式被算了 {len(recorder.mode_calls)} 次，应该只有 3 次（每个模式一次）"
    )


def test_modes_do_not_change_other_numbers(
    ctx: ViewContext, overview: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """把 `modes` 关掉，六个视角的数值**逐字节相同**（模式不回流）。"""
    monkeypatch.setattr(render, "BUDGET_CHARS", BIG_BUDGET)
    monkeypatch.setattr(render, "_modes_block", lambda _ctx: [])

    without_modes = render.render_overview(ctx)

    assert without_modes["modes"] == []
    for name in VIEW_ORDER:
        assert without_modes["views"][name] == overview["views"][name], f"{name} 被 modes 影响了"


def test_modes_are_dropped_last_when_budget_is_tight(
    ctx: ViewContext, overview: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """降级顺序：**modes 是最后一项**（还有别的可砍时不动它）。"""
    total = _measure(overview["views"], overview["modes"])

    # ① 只差 1 char：去掉 flag 的 evidence 就够 → modes 必须原样留着
    monkeypatch.setattr(render, "BUDGET_CHARS", total - 1)
    tightened = render.render_overview(ctx)

    assert tightened["budget"]["degraded"] is True
    assert tightened["modes"] == overview["modes"], "还有别的可砍时不该先砍 modes"
    assert tightened["views"]["mobility"]["overview"] == overview["views"]["mobility"]["overview"]

    # ② 极小预算：其它都砍完了才清空 modes，但**键保留**（schema 稳定）
    monkeypatch.setattr(render, "BUDGET_CHARS", 1)
    crushed = render.render_overview(ctx)

    assert "modes" in crushed
    assert crushed["modes"] == []
    assert crushed["budget"]["degraded"] is True


# =============================================================================
# 九、C2：confidence（可信度 A–E 的输出出口）
# =============================================================================


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_confidence_keys_are_a_subset_of_provenance(overview: dict, view_name: str) -> None:
    """**不变量①**：`set(confidence) ⊆ set(provenance)`——非 A 项必须都是合法叶子。

    稀疏表不是「小一号的平行表」：键集合只可能更小，**不可能出现 provenance 里没有的
    路径**（那意味着自己在发明字段）。
    """
    entry = overview["views"][view_name]
    confidence = set(entry["confidence"])
    provenance = set(entry["provenance"])

    assert confidence <= provenance, (
        f"{view_name} 的 confidence 出现了非法叶子：{sorted(confidence - provenance)}"
    )


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_every_non_default_registration_is_rendered(overview: dict, view_name: str) -> None:
    """**不变量②**：每个非 A 的登记项都必须出现（拿登记表**反查实际叶子**，防止漏输出）。

    反查口径与 `confidence_of()` 的查表顺序一致：登记项可以命中同名叶子，也可以作为
    **对象数组容器** / **整数键字典容器**覆盖一批叶子（`_covered_by()`）。所以这条不是
    「把登记表抄一遍」，而是「登记了的东西在输出里到底有没有落到每个叶子上」。

    反向也一起验（`set(confidence) == expected`）：稀疏化最容易出的错是「该标的没标」
    与「不该标的蹦出来」，两边都钉住才等价于 T-17 全量平行表的强度。
    """
    entry = overview["views"][view_name]
    leaves = _leaf_paths(entry["overview"])
    confidence = entry["confidence"]

    registry: dict[str, str] = dict(C.CONFIDENCE.get(view_name) or {})
    for path in C.FIELDS_FROM_MAX_TARGETS:  # -70 maxTargets 通道：对任何视角都判 C
        registry.setdefault(path, "C")

    expected: set[str] = set()
    for registered in registry:
        level = C.confidence_of(view_name, registered)
        if level == C.DEFAULT_CONFIDENCE:
            continue  # 登记成默认档 = 不写进稀疏表（登记了也是空操作）
        matched = {leaf for leaf in leaves if _covered_by(leaf, registered)}
        for leaf in matched:
            assert leaf in confidence, (
                f"{view_name}.{leaf} 登记为非默认（{registered} → {level!r}）却没被输出"
            )
            assert confidence[leaf] == level, (
                f"{view_name}.{leaf} 应为 {level!r}，实为 {confidence[leaf]!r}"
            )
        expected |= matched

    assert set(confidence) == expected, (
        f"{view_name} 的 confidence 与登记表不吻合：多 {sorted(set(confidence) - expected)}，"
        f"少 {sorted(expected - set(confidence))}"
    )
    for path, level in confidence.items():
        assert level != C.DEFAULT_CONFIDENCE, (
            f"{view_name}.{path} 写了默认档 {level!r}——稀疏表不写默认值"
        )


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_provenance_keys_equal_overview_leaves_and_confidence_is_sparse(
    overview: dict, view_name: str
) -> None:
    """**不变量③**：`provenance` 键集合 == overview 叶子集合（这条完全没变）。

    三向断言里的第三向（confidence == 叶子）**改成**两条更精确的断言：① 子集、② 非 A
    登记项必出现（见上面两条）。所以这里只钉「provenance 覆盖率」与「空表 `{}` 而不是
    省略键」。
    """
    entry = overview["views"][view_name]
    leaves = set(_leaf_paths(entry["overview"]))

    assert leaves, f"{view_name} 的 overview 不该是空的"
    assert set(entry["provenance"]) == leaves, (
        f"{view_name} provenance 与叶子不吻合：多 {sorted(set(entry['provenance']) - leaves)}，"
        f"少 {sorted(leaves - set(entry['provenance']))}"
    )
    # 稀疏表的键恒在（空表是 `{}`，不是省略键）——schema 稳定，调用方不必判 `in`
    assert "confidence" in entry and isinstance(entry["confidence"], dict)


def test_confidence_is_sparse_and_keeps_empty_object(overview: dict) -> None:
    """整表默认的视角输出 **`{}`**（键恒在），非默认项只有 `application.*` 三条。"""
    empty = [
        name for name, entry in overview["views"].items() if entry["confidence"] == {}
    ]
    assert empty, "样本里应有整表默认的视角（稀疏 → 空对象），否则「稀疏」没被验证"

    firepower = overview["views"]["firepower"]["confidence"]
    assert set(firepower) == {
        "application.tracking_rad_s",
        "application.explosion_radius_m",
        "application.explosion_velocity_mps",
    }, f"非默认项应恰好是这三个未建模参数，实为 {sorted(firepower)}"


def test_confidence_outputs_only_the_non_default_subset(overview: dict) -> None:
    """输出层只产生 `A` / `C` / `E`，且稀疏表**只写非 A**（`B`/`D` 与默认 `A` 都不出现）。"""
    seen: set[str] = set()
    for name, entry in overview["views"].items():
        for path, level in entry["confidence"].items():
            assert level in CONFIDENCE_OUTPUT_STATES, f"{name}.{path} → {level!r}"
            seen.add(level)

    assert seen, "样本里至少有一条非默认档（application.* 三个 E）"
    assert seen <= CONFIDENCE_OUTPUT_STATES - {C.DEFAULT_CONFIDENCE}, (
        f"稀疏表里出现了默认档：{sorted(seen & {C.DEFAULT_CONFIDENCE})}"
    )


def test_application_fields_are_unmodeled(overview: dict) -> None:
    """应用层三个参数是**未建模**（`E`）：只给参数，不给命中结论。"""
    confidence = overview["views"]["firepower"]["confidence"]

    for path in (
        "application.tracking_rad_s",
        "application.explosion_radius_m",
        "application.explosion_velocity_mps",
    ):
        assert path in confidence, f"{path} 没被叶子枚举覆盖"
        assert confidence[path] == "E", f"{path} 应为 E（未建模），实为 {confidence[path]!r}"


def test_confidence_is_orthogonal_to_provenance(overview: dict) -> None:
    """两个维度**正交**：同一张表里既有 `assumed`+`E`，也有 `assumed`+默认 `A`。

    稀疏表里「默认 A」表现为**不在表里**（缺省即 A），所以第二条查的是「`dps.base` 没有
    出现」+ `confidence_of()` 仍判 A——不能因为 `assumed` 就给 E。
    """
    firepower = overview["views"]["firepower"]

    # 依赖假设（技能档会改 tracking）且未建模 → 两个维度各说各的
    assert firepower["provenance"]["application.tracking_rad_s"] == "assumed"
    assert firepower["confidence"]["application.tracking_rad_s"] == "E"

    # 同样 assumed，但口径已对拍（不变式 I2）→ A：不能因为 assumed 就给 E
    assert firepower["provenance"]["dps.base"] == "assumed"
    assert "dps.base" not in firepower["confidence"], "默认档 A 不该被写进稀疏表"
    assert C.confidence_of("firepower", "dps.base") == C.DEFAULT_CONFIDENCE


def test_confidence_does_not_leak_into_provenance(overview: dict) -> None:
    """红线：可信度档位不得出现在 provenance 的三态里（两者不可互推）。"""
    for name, entry in overview["views"].items():
        overlap = set(entry["provenance"].values()) & C.CONFIDENCE_LEVELS
        assert not overlap, f"{name} 的 provenance 里混进了可信度档位：{sorted(overlap)}"


def test_confidence_module_defaults_and_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """`confidence_of()`：默认 A、登记即生效、非法档位直接炸。"""
    assert C.DEFAULT_CONFIDENCE == "A"
    assert C.confidence_of("mobility", "speed_mps") == "A"
    assert C.confidence_of("firepower", "no_such_path") == "A"
    assert C.confidence_of("firepower", "application.tracking_rad_s") == "E"

    # 容器路径的继承（整张列表都不信时只登记一条）
    monkeypatch.setitem(C.CONFIDENCE, "mobility", {"weapons": "C"})
    assert C.confidence_of("mobility", "weapons[].name") == "C"

    # -70 maxTargets 的通道：登记即判 C（不可用），与「未建模」区分开
    monkeypatch.setattr(C, "FIELDS_FROM_MAX_TARGETS", ("some.future_field",))
    assert C.confidence_of("firepower", "some.future_field") == "C"

    monkeypatch.setitem(C.CONFIDENCE, "mobility", {"speed_mps": "X"})
    with pytest.raises(ValueError):
        C.confidence_of("mobility", "speed_mps")


def test_detail_confidence_matches_overview(ctx: ViewContext, overview: dict) -> None:
    """`render_detail` 的 confidence 与 overview **逐条相同**（同一套叶子枚举 + 稀疏口径）。"""
    detail = render.render_detail(ctx, view="firepower")

    assert detail["confidence"] == overview["views"]["firepower"]["confidence"]
    assert set(detail["confidence"]) <= set(detail["provenance"])


# =============================================================================
# 十、T-32：unknown_reasons（`unknown` 必须带原因，字段规格 §0.2.1「方案 A」）
# =============================================================================

REASON_SAMPLES: tuple[str, ...] = (
    "f1-hecate-killmail",
    "f4-non-hecate",
    "f5-no-mwd",
    "f26-carrier-fighter",
)
"""T-32 的四个守门样本（`unclassified` 必须为 0 的那四个）。

`f1/f4/f5` 与 T-27 的实例级样本同源（炮船 / 突变导弹船 / 导弹船）；`f26` 是**航母**：
`weapons` 空清单、载机停在机库（`fighters_in_bay_only`）、`damage_split` 四系全 `None`、
`consumables.ammo_*` 全 `None` —— 条件字段这一类原因在它身上最密集，也最容易漏。
"""


@pytest.fixture(scope="module")
def reason_samples(engine: Engine, index) -> dict[str, dict]:
    """四个守门样本各渲染一次 overview（预算调大，排除降级对结构的干扰）。"""
    original = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        return {
            stem: render.render_overview(_make_ctx(engine, index, stem)) for stem in REASON_SAMPLES
        }
    finally:
        render.BUDGET_CHARS = original


def _unknown_paths(entry: dict) -> set[str]:
    """该视角里 `provenance == "unknown"` 的路径集合（独立实现，不抄渲染层私有函数）。"""
    return {path for path, state in entry["provenance"].items() if state == "unknown"}


def _unclassified(rendered: dict) -> list[str]:
    """**独立实现**的守门检查：所有视角里 `reason == "unclassified"` 的路径。

    刻意不复用 `reasons.unclassified()`：守门判据不能由被测实现自己提供，否则把兜底值
    改个名字就能骗过测试。这里只认字面量 `"unclassified"`（契约写死的兜底值）。
    """
    return sorted(
        f"{view_name}.{path}"
        for view_name, entry in rendered["views"].items()
        for path, reason in entry["unknown_reasons"].items()
        if reason == "unclassified"
    )


@pytest.mark.parametrize("view_name", VIEW_ORDER)
def test_unknown_reasons_is_a_sparse_parallel_table(overview: dict, view_name: str) -> None:
    """**稀疏 + 全覆盖**：键集合恰好是 `provenance` 里值为 `unknown` 的路径集合。

    三个方向一起钉住（与 `confidence` 的稀疏口径同构）：
    ① 每个 `unknown` 都**有**原因（少了就是「静默的 unknown」）；
    ② 非 `unknown` 的路径**不出现**（稀疏：默认情况不需要被写出来）；
    ③ 取值只允许 `reasons.UNKNOWN_REASONS` 里的五个字符串。
    """
    entry = overview["views"][view_name]
    table = entry["unknown_reasons"]
    unknown = _unknown_paths(entry)
    known = set(entry["provenance"]) - unknown

    assert isinstance(table, dict), f"{view_name} 的 unknown_reasons 必须是 dict"
    assert unknown <= set(table), f"{view_name} 有 unknown 没写原因：{sorted(unknown - set(table))}"
    assert not (set(table) & known), f"{view_name} 给非 unknown 路径写了原因：{sorted(set(table) & known)}"
    assert set(table) == unknown, f"{view_name} 的原因表与 unknown 路径不吻合"
    assert set(table.values()) <= set(R.UNKNOWN_REASONS), (
        f"{view_name} 出现了非法 reason：{sorted(set(table.values()) - set(R.UNKNOWN_REASONS))}"
    )

    # 非空转前置：样本上确实有 unknown（否则上面全是空断言）
    assert any(entry["unknown_reasons"] for entry in overview["views"].values()), (
        "样本一个 unknown 都没有，这条断言退化成空转"
    )


def test_reason_vocabulary_is_closed_and_documented() -> None:
    """取值域是**封闭**的五项，且每一项都有文档；兜底就在优先级末尾。"""
    assert R.UNKNOWN_REASONS == (
        "input_incomplete",
        "not_applicable",
        "engine_omits",
        "ambiguous",
        "unclassified",
    )
    assert set(R.REASON_DOCS) == set(R.UNKNOWN_REASONS)
    assert R.FALLBACK_REASON == "unclassified" == R.UNKNOWN_REASONS[-1]
    assert R.distribution({}) == {reason: 0 for reason in R.UNKNOWN_REASONS}


def test_reason_tables_only_point_at_real_field_paths() -> None:
    """规则表不得打错字：每条规则都要能在**真实的** `FIELD_SOURCES` / 诊断规则表里找到落点。

    打错一个字的后果是**静默退化**：那条规则永不命中，对应路径悄悄掉进 `unclassified`，
    而「守门测试通过」这件事看上去仍然成立（因为样本里恰好没出现那条路径）。
    所以规则表必须被反查钉住 —— 表是在这里被「真实数据」校验的，不是在注释里自我保证的。
    """
    for view_name, paths in R.CONDITIONAL_LEAVES.items():
        sources = VIEW_MODULES[view_name].FIELD_SOURCES
        missing = sorted(
            path
            for path in paths
            # 叶子级登记有两种合法落点：**整条路径**，或「容器路径 + `[]` 元素登记」
            # （`weapons` 就是这样：列表非空时叶子是 `weapons[].family`，空列表时整块
            # 折叠成容器键 `weapons` —— 两种形态都要能匹配上，见 PROGRESS §3 第 106 条）
            if path not in sources and not any(key.startswith(f"{path}[]") for key in sources)
        )
        assert missing == [], f"{view_name} 的条件字段不在 FIELD_SOURCES 里（打错字？）：{missing}"

    for view_name, prefixes in R.CONDITIONAL_SUBTREES.items():
        sources = list(VIEW_MODULES[view_name].FIELD_SOURCES)
        for prefix in prefixes:
            matched = [
                path
                for path in sources
                if path == prefix or path.startswith(f"{prefix}.") or path.startswith(f"{prefix}[")
            ]
            assert matched, f"{view_name} 的条件子树 {prefix!r} 一条 FIELD_SOURCES 都覆盖不到"

    for suffix in R.AMBIGUOUS_SUFFIXES:
        assert any(
            path.endswith(suffix) for path in VIEW_MODULES["survivability"].FIELD_SOURCES
        ), f"并列语义的后缀 {suffix!r} 在 survivability 的 FIELD_SOURCES 里找不到"
    for prefix in R.AMBIGUOUS_SUBTREES:
        assert any(
            path.startswith(f"{prefix}.") for path in VIEW_MODULES["survivability"].FIELD_SOURCES
        ), f"并列语义的子树 {prefix!r} 在 survivability 的 FIELD_SOURCES 里找不到"

    # 规则①：flag 必须是**真实存在**的诊断规则，且它解释的字段在视角里真有登记
    for flag_id, paths in R.FLAG_REASONS.items():
        assert flag_id in D._HANDLERS, f"{flag_id} 不是 diagnostics 里注册的规则"
        for path in paths:
            assert path in VIEW_MODULES["firepower"].FIELD_SOURCES, path

    # 规则③：「引擎永不提供」的 ID 必须同时是常量表登记项与静态表的 unknown（三处同源）
    from noobbaozi.project import provenance as P

    for attribute_id in R.ENGINE_OMITS_ATTRS:
        assert attribute_id == A.ATTR_MAX_TARGETS
        assert attribute_id in A.ATTRIBUTES, f"{attribute_id} 未登记常量表（红线 N12）"
        assert attribute_id in P.UNKNOWN_ATTRS, (
            f"{attribute_id} 判 engine_omits，静态表却没把它判 unknown（两表漂移）"
        )


def test_f4_dph_reason_is_input_incomplete(rendered_samples: dict[str, dict]) -> None:
    """**靶心**：f4 的 `dph` → `input_incomplete`，依据是**同一视角的 flag**。

    三件事必须同时成立，缺一条就说明链路断了：
    ① `weapon_damage_zero` flag 真在 `views.firepower.flags` 里（规则①的依据）；
    ② `dph` 的值是 `None`、三态是 `unknown`（T-27 的实例级覆盖）；
    ③ 原因落在 `input_incomplete`，而不是「没有武器」这类路径推断。

    另外钉住**稀疏**：f1/f5 的 `dph` 有值 → 原因表里**不该有**这一条。
    """
    firepower = rendered_samples["f4-non-hecate"]["views"]["firepower"]

    assert "weapon_damage_zero" in _flag_ids(firepower["flags"]), (
        "f4 的 firepower 视角里必须带 weapon_damage_zero flag（规则①的唯一依据）"
    )
    assert firepower["overview"]["dph"] is None
    assert firepower["provenance"]["dph"] == "unknown"
    assert firepower["unknown_reasons"]["dph"] == "input_incomplete", (
        f"有 flag 却报了 {firepower['unknown_reasons']['dph']!r}"
    )

    for stem in ("f1-hecate-killmail", "f5-no-mwd"):
        entry = rendered_samples[stem]["views"]["firepower"]
        assert isinstance(entry["overview"]["dph"], (int, float)), f"{stem}.dph 应有数值"
        assert "dph" not in entry["unknown_reasons"], f"{stem}.dph 有值却写了原因（不稀疏）"


def test_gun_ship_application_fields_are_not_applicable(
    rendered_samples: dict[str, dict],
) -> None:
    """炮船的 `application.explosion_*` → `not_applicable`（这套配装没有导弹）。

    f1（Hecate + 5 门中子炮）有 `tracking_rad_s` / `optimal_signature_radius_m`，但
    `explosion_radius_m` / `explosion_velocity_mps` 是 `None` —— 那不是「不可知」，
    而是「没有导弹，这两个参数对本配装不适用」（字段规格 §1 的 application 行）。
    """
    f1 = rendered_samples["f1-hecate-killmail"]["views"]["firepower"]

    assert f1["overview"]["application"]["tracking_rad_s"] is not None
    assert "application.tracking_rad_s" not in f1["unknown_reasons"]

    for path in ("application.explosion_radius_m", "application.explosion_velocity_mps"):
        assert f1["overview"]["application"][path.rsplit(".", 1)[1]] is None, path
        assert f1["unknown_reasons"][path] == "not_applicable", path


def test_tied_thinnest_damage_type_is_ambiguous(
    rendered_samples: dict[str, dict], engine: Engine, index
) -> None:
    """四系并列的 `thinnest.*.damage_type` → `ambiguous`（不是「不知道」）。

    两个样本各钉一个侧面：
    - **f1 结构层**：一块 `Damage Control II` 把四系共振率压成同一个值 → 没有「最薄系」；
      但该层 `ehp` 照给（四系同值，这个数是对的）→ 证明是**并列**而不是缺数据；
    - **f4 护盾层**：同样的并列（本样本四系抗性相等）。

    并列这件事**独立复算**：直接读 `ctx.attrs(BASE)` 的四个共振率，断言它们相等
    （`math.isclose`，容差 1e-9；红线 N11 不用 `float ==`）。
    """
    ctx = _make_ctx(engine, index, "f1-hecate-killmail")
    resonances = [ctx.attrs(BASE)[attribute_id] for attribute_id in (
        A.ATTR_HULL_EM_RESONANCE,
        A.ATTR_HULL_EXPLOSIVE_RESONANCE,
        A.ATTR_HULL_KINETIC_RESONANCE,
        A.ATTR_HULL_THERMAL_RESONANCE,
    )]
    assert all(
        math.isclose(value, resonances[0], rel_tol=1e-9, abs_tol=0.0) for value in resonances
    ), f"f1 结构层四系共振率本应并列，实测 {resonances}"

    f1 = rendered_samples["f1-hecate-killmail"]["views"]["survivability"]
    assert f1["overview"]["thinnest"]["hull"]["damage_type"] is None
    assert f1["overview"]["thinnest"]["hull"]["ehp"] is not None, (
        "ehp 有值说明数据齐全，damage_type 的 None 只能来自并列"
    )
    assert f1["unknown_reasons"]["thinnest.hull.damage_type"] == "ambiguous"

    f4 = rendered_samples["f4-non-hecate"]["views"]["survivability"]
    assert f4["overview"]["thinnest"]["shield"]["damage_type"] is None
    assert f4["overview"]["thinnest"]["shield"]["ehp"] is not None
    assert f4["unknown_reasons"]["thinnest.shield.damage_type"] == "ambiguous"
    # 非并列的层照常给系名，不该出现在原因表里
    assert f4["overview"]["thinnest"]["armor"]["damage_type"] == "explosive"
    assert "thinnest.armor.damage_type" not in f4["unknown_reasons"]


def test_fields_sourced_from_max_targets_are_engine_omits(
    ctx: ViewContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """以 `-70 maxTargets` 为**来源**的字段 → `engine_omits`（规则③）。

    现状：六个视角都没有字段直接以 `-70` 为来源（`confidence.FIELDS_FROM_MAX_TARGETS`
    也是空的），所以这里把 survivability 的一个**真实 unknown 叶子**临时指到 `-70`：

    - 若规则③没接上，这条会退回 `ambiguous`（它是 `*.damage_type`，规则④）；
    - 因此这条同时钉住**优先级 ③ 在 ④ 之前**（规格 §0.2.1 的顺序）。
    """
    path = "thinnest.hull.damage_type"
    monkeypatch.setitem(
        VIEW_MODULES["survivability"].FIELD_SOURCES, path, A.ATTR_MAX_TARGETS
    )
    monkeypatch.setattr(render, "BUDGET_CHARS", BIG_BUDGET)

    entry = render.render_overview(ctx)["views"]["survivability"]

    assert entry["overview"]["thinnest"]["hull"]["damage_type"] is None
    assert entry["provenance"][path] == "unknown", "前提：这条路径仍是 unknown"
    assert entry["unknown_reasons"][path] == "engine_omits", (
        f"来源是 -70 maxTargets，应为 engine_omits，实为 {entry['unknown_reasons'][path]!r}"
    )


@pytest.mark.parametrize("stem", REASON_SAMPLES)
def test_no_unclassified_on_the_guard_samples(
    reason_samples: dict[str, dict], stem: str
) -> None:
    """**本任务的守门测试**：四个真实样本上 `unclassified` 必须为 **0**。

    `unclassified` 是「还没有规则能解释这个 unknown」的兜底 —— 出现即缺一条规则。
    这条测试红了只有一种正确修法：**去 `project/reasons.py` 补规则/补表**，
    不能把兜底值改名、也不能在断言里放宽成「允许少量」。
    """
    rendered = reason_samples[stem]

    assert _unclassified(rendered) == [], (
        "这些 unknown 没有规则能解释（去 project/reasons.py 补）：\n"
        + "\n".join(f"  {stem}.{path}" for path in _unclassified(rendered))
    )

    # 非空转前置：该样本确实产出了若干带原因的 unknown（否则上面是空断言）
    total = sum(len(entry["unknown_reasons"]) for entry in rendered["views"].values())
    assert total >= 5, f"{stem} 只有 {total} 条 unknown 原因，样本或规则接错了"


DPS_REASON_SAMPLES: tuple[str, ...] = ("f12-logistics-oneiros", "f34-no-charge")
"""T-40 缺陷 1（blocker）的两个样本。

共同点：**有武器系统、但那套武器系统不产出伤害**，于是引擎连 `-11`/`-12`/`-13` 三个键
都不给 —— T-37 的「一个武器系统都没有 → 补 `0.0`」不生效，`dps.*` 的值保持 `None`：

- `f12`（Oneiros 后勤）：`Light Hull Maintenance Bot I` ×2 + `Medium Armor
  Maintenance Bot I` ×4（**维修**无人机，无伤害）；
- `f34`（武器空弹）：`Vespa EC-600` ×1 + `Hornet EC-300` ×1（**ECM** 无人机，无伤害）。
"""

NO_DAMAGE_DPS_PATHS: tuple[str, ...] = (
    "dps.base",
    "dps.with_reload",
    "overload.off.dps",
    "overload.on.dps",
    "overload.delta.dps",
)
"""这五条就是验收报告里报 `unclassified` 的那五条（`dps.base` / `dps.with_reload` /
`overload.{off,on,delta}.dps`）。"""


@pytest.fixture(scope="module")
def dps_reason_samples(engine: Engine, index) -> dict[str, tuple[ViewContext, dict]]:
    """两个样本各渲染一次 overview（预算调大，排除降级对结构的干扰）。"""
    original = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        out: dict[str, tuple[ViewContext, dict]] = {}
        for stem in DPS_REASON_SAMPLES:
            ctx = _make_ctx(engine, index, stem)
            out[stem] = (ctx, render.render_overview(ctx))
        return out
    finally:
        render.BUDGET_CHARS = original


def _dps_leaf(overview: dict, path: str) -> object:
    """按点路径取 `dps.base` / `overload.off.dps` 这样的嵌套值。"""
    node: object = overview
    for part in path.split("."):
        assert isinstance(node, dict) and part in node, f"路径 {path!r} 在 overview 里断了"
        node = node[part]
    return node


def test_no_unclassified_when_weapon_system_has_no_damage(
    dps_reason_samples: dict[str, tuple[ViewContext, dict]],
) -> None:
    """**T-40 缺陷 1 的靶心**：`dps.*` 五条在「有武器系统但不产出伤害」时判 `not_applicable`。

    三件事同时钉住（缺一条就说明归属又漂了）：

    ① **引擎真的不给键**（`-11`/`-12`/`-13` 全缺）→ 值确实是 `None`，不是「有值却报不适用」；
    ② 配装**确实有武器系统**（无人机在 `weapons[]` 里）→ T-37 的「补 `0.0`」不生效，
       所以这不是「一套武器系统都没有」那条路；
    ③ 五条路径的 reason 必须是 `not_applicable`（与同一配装的 `dph` **同判据**），
       且守门断言 `unclassified == 0`。

    红线：**不许把 `unknown` 改成一个值**来消灭 `unclassified`（那才是伪事实）。
    """
    for stem, (ctx, rendered) in dps_reason_samples.items():
        attrs = ctx.attrs(BASE)
        for attribute_id in (
            A.ATTR_DAMAGE_ALPHA,
            A.ATTR_DAMAGE_DPS,
            A.ATTR_DAMAGE_DPS_WITH_RELOAD,
        ):
            assert attribute_id not in attrs, (
                f"{stem} 的引擎给了属性 {attribute_id}？那这条样本不构成「缺键」情形"
            )

        firepower = rendered["views"]["firepower"]
        assert firepower["overview"]["weapons"], f"{stem} 应当有武器系统（无人机）"
        assert any(
            row.get("count") for row in firepower["overview"]["weapons"]
        ), f"{stem} 的 weapons[] 应当有件数"

        for path in NO_DAMAGE_DPS_PATHS:
            value = _dps_leaf(firepower["overview"], path)
            assert value is None, f"{stem}.{path} 应为 None（不许为消除 unclassified 补值）"
            assert firepower["provenance"][path] == "unknown", f"{stem}.{path}"
            assert firepower["unknown_reasons"][path] == "not_applicable", (
                f"{stem}.{path} 判成了 {firepower['unknown_reasons'][path]!r}"
            )

        # 同一配装的 `dph` 早就判 not_applicable —— 五条 `dps` 必须与它同判据
        assert firepower["provenance"]["dph"] == "unknown"
        assert firepower["unknown_reasons"]["dph"] == "not_applicable"

        # 守门的最终判据：这两个样本上一条 `unclassified` 都不许有
        assert _unclassified(rendered) == [], (
            f"{stem} 又出现了 unclassified：{_unclassified(rendered)}"
        )


def test_unclassified_guard_catches_a_synthetic_unexplained_path(
    reason_samples: dict[str, dict],
) -> None:
    """**注入一个「没有原因」的 unknown 路径**：守门检查必须认出它。

    与下一条（抹掉一条规则）互补：这条证明判据认得出 `unclassified`，那条证明规则缺失
    真的会产出 `unclassified`。两条都不做，守门断言就可能因为「表里永远没有这个值」而空转。
    """
    rendered = copy.deepcopy(reason_samples["f1-hecate-killmail"])
    rendered["views"]["firepower"]["unknown_reasons"]["__mutation_probe__"] = "unclassified"

    assert _unclassified(rendered) == ["firepower.__mutation_probe__"]


def test_unclassified_guard_turns_red_when_a_rule_is_removed(
    ctx: ViewContext, overview: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**变异自检**：抹掉一条规则（firepower 的 `application` 子树），守门断言必须变红。

    这是「`unclassified == 0` 不是永真」的证明（PROGRESS §3 第 196 条的做法）：把
    `CONDITIONAL_SUBTREES` 里的 `application` 去掉，f1 的
    `application.explosion_radius_m` / `..._velocity_mps` 就**没有规则能解释**了 ——
    守门检查必须报出来，而不是静默通过。
    """
    monkeypatch.setitem(
        R.CONDITIONAL_SUBTREES, "firepower", ("damage_split", "range", "overload")
    )
    monkeypatch.setattr(render, "BUDGET_CHARS", BIG_BUDGET)

    mutated = render.render_overview(ctx)
    found = _unclassified(mutated)

    assert found, "抹掉了一条规则，守门断言却说通过 —— 它是永真的"
    assert any(path.endswith("application.explosion_radius_m") for path in found), found
    # 对照：未变异时同一份 ctx 是干净的（证明 redness 来自这处变异，不是环境问题）
    assert _unclassified(overview) == []


def test_detail_unknown_reasons_matches_overview_and_is_clipped(ctx: ViewContext) -> None:
    """`render_detail` 的 `unknown_reasons`：全量时与 overview 逐条相同，带 `fields` 时同口径裁剪。"""
    full = render.render_detail(ctx, view="firepower")
    assert full["unknown_reasons"] == {
        path: reason
        for path, reason in render.render_overview(ctx)["views"]["firepower"][
            "unknown_reasons"
        ].items()
    }
    assert set(full["unknown_reasons"]) <= _unknown_paths(full)

    clipped = render.render_detail(ctx, view="firepower", fields=["dps.drone"])
    assert clipped["missing"] == []
    assert clipped["unknown_reasons"] == {"dps.drone": "not_applicable"}, (
        f"裁剪后应只剩被请求字段下的原因，实为 {clipped['unknown_reasons']}"
    )


# =============================================================================
# 十一、T-34：**有值的叶子必须有来源登记**（`not_applicable` 不得收留有值字段）
# =============================================================================

VALUE_LEAF_UNREGISTERED_ALLOWLIST: dict[str, str] = {}
"""「有值却查不到任何来源登记」的**白名单**：`"视角.路径" → 为什么允许`（当前**为空**）。

T-34 把这批欠账还清了，所以一个都不留。白名单**必须会过期**（照
`tests/test_attr_coverage.py::ALLOWED_BARE_IDS` 的做法）：条目一旦不再被命中，
`test_value_leaf_allowlist_has_no_stale_entries()` 会要求销账 —— 豁免不免债，只推迟还款。
"""

HARD_VIOLATION_EFT = "[Rifter, probe]\nLight Neutron Blaster II,Antimatter Charge L"
"""**现场造一条硬违规**的配装：小船上装「大号弹药」→ `charge_size`（与技能无关）。

36 个 KB 样本里 `hard_violations` 恒为 `[]`（一条硬违规都没有），所以
`hard_violations[].rule.*` / `.target.*` / `.message` 这条路**此前从未被任何断言走到过**。
"""

SKILL_VIOLATION_EFT = "[Rifter, probe]\n200mm AutoCannon II,EMP S"
"""**现场造一条技能类档位违规**的配装：把技能档换成「一个技能都没有」。

同一套 `tier_violations` 条目在**技能分支**下的形状（`rule.type`/`rule.type_id`/
`rule.required`/`rule.level`）与资源分支（`rule.used`/`rule.available`）不同 ——
容器登记必须把两种形状都接住，这也是「逐键枚举 `rule.*` 会漏」的实证。
"""


def _container_of(path: str) -> str | None:
    """`cap_by_module[].cap_use_gj_s` → `cap_by_module`（不含 `[]` 时没有容器）。"""
    head, marker, _rest = path.partition(ARRAY_MARKER)
    return head if marker else None


def _int_key_container_of(path: str) -> str | None:
    """`skill_floor.count_by_level.4` → `skill_floor.count_by_level`（尾段必须是整数）。"""
    head, _dot, tail = path.rpartition(".")
    return head if head and tail.isdigit() else None


def _source_registration_of(path: str, sources: dict[str, int | None]) -> str | None:
    """叶子路径 → 命中的那条 `FIELD_SOURCES` 登记（**独立实现**，不复用 `render._source_of()`）。

    四条命中关系，与渲染层 `_tag()` 的查表顺序一一对应，外加**容器方向**的回退：

    1. 整条路径登记（`damage_split.em`）；
    2. 元素路径退回**容器**登记（`weapons[].name` ← `weapons`）—— `_tag()` 第 2 步；
    3. 整数键字典的元素路径退回容器（`count_by_level.4` ← `count_by_level`）—— 第 3 步；
    4. **容器叶子**退回它的**元素登记**（`weapons` ← `weapons[].name`）。渲染层没有这条
       （空列表容器照旧 `unknown` = 「这里没有数据」），但**登记覆盖率**必须接受它：
       对象数组只有在**空**的时候才是一个叶子（`_leaf_paths()` 同一口径），此时它的来源
       就是那些元素登记 —— 本任务书明确要求「容器 + `[]` 元素两种落点」，否则
       `weapons: []` / `cap_by_module: []` 会被误判成漏登记。
    """
    if path in sources:
        return path
    container = _container_of(path)
    if container is not None and container in sources:
        return container
    int_key_container = _int_key_container_of(path)
    if int_key_container is not None and int_key_container in sources:
        return int_key_container
    if any(key.startswith(f"{path}{ARRAY_MARKER}") for key in sources):
        return f"{path}{ARRAY_MARKER}"
    return None


def _has_no_value(occurrences: list[object]) -> bool:
    """「没有值」：任一出现是 `None`，或是**空容器**。

    - `None` = 实例级规则①（值缺失 ⇒ `unknown`，T-27）；
    - 空容器（`weapons: []`）= 渲染层把空列表折叠成的容器叶子，表达「这里什么都没有」
      （与 `tests/test_views_a.py::_numeric_leaf_paths` 同一口径：空列表不产生数值叶子）。
      它不是可引用的值，所以判 `not_applicable` 是对的，**不属于**本任务要修的污染。
    """
    return any(
        value is None or (isinstance(value, (dict, list, tuple)) and not value)
        for value in occurrences
    )


def _is_summary_leaf(view_name: str, path: str) -> bool:
    """该路径是不是**渲染层粒度改写**产出的摘要叶子（`resources.upgrade_space.slots_checked`）。

    这是「来源登记」的第四种落点：摘要叶子由 `render.OVERVIEW_DOWNGRADES` 现场产出，
    三态由 `render.SUMMARY_PROVENANCE`（= `assumed`）**覆盖**，所以它天然**不在**
    `FIELD_SOURCES` 里 —— `views/resources.py` 的 `upgrade_space[].*` 登记的是 detail 层的
    完整清单，与 overview 的三个计数字段不是同一批键。漏了这条，覆盖率断言会把
    「由渲染层登记」误判成「漏登记」。
    """
    for base in render.OVERVIEW_DOWNGRADES.get(view_name) or {}:
        if path == base or path.startswith(f"{base}."):
            return True
    return False


def _uncovered_value_leaves(view_name: str, entry: dict) -> list[str]:
    """该视角里「**有值**却查不到任何来源登记」的叶子路径（升序）。

    这就是 T-34 的靶心：这些路径会落进 `render._tag()` 的 `unknown` 默认分支，再被
    `reasons.reason_for()` 兜成 `not_applicable` / `unclassified` —— 把「我们自己算出来的
    确定值」说成「不适用 / 不知道」（T-32 的 744 条 `not_applicable` 里混着这批）。
    """
    sources = VIEW_MODULES[view_name].FIELD_SOURCES
    uncovered: list[str] = []
    for path, occurrences in _leaf_values(entry["overview"]).items():
        if _has_no_value(occurrences):
            continue  # 值缺失 ⇒ unknown 是实例级规则①，与「有没有登记」无关
        if _source_registration_of(path, sources) is not None:
            continue
        if _is_summary_leaf(view_name, path):
            continue
        uncovered.append(path)
    return sorted(uncovered)


def _uncovered_value_leaves_by_view(rendered: dict) -> list[str]:
    """整个渲染结果里「有值却没登记」的叶子，形如 `"视角.路径"`（升序）。"""
    return sorted(
        f"{view_name}.{path}"
        for view_name, entry in rendered["views"].items()
        for path in _uncovered_value_leaves(view_name, entry)
    )


def _value_leaf_count(rendered: dict) -> int:
    """「有值叶子」总数（守门断言的非空转前置：0 的话上面全是空断言）。"""
    return sum(
        1
        for entry in rendered["views"].values()
        for occurrences in _leaf_values(entry["overview"]).values()
        if not _has_no_value(occurrences)
    )


@pytest.mark.parametrize("stem", REASON_SAMPLES)
def test_every_value_bearing_leaf_has_a_field_source(
    reason_samples: dict[str, dict], stem: str
) -> None:
    """**本任务的守门断言**：有值的叶子必须有来源登记（不只是数值叶子）。

    为什么需要它：`test_views_a` / `test_views_b` 的覆盖率断言只守**数值**叶子，
    字符串 / 枚举 / 结构性字段（`tier_violations[].kind`、`.message`、`rule.*`…）漏登记时
    没人拦 —— 它们落进 `unknown` 默认分支，再被兜成 `not_applicable`（T-32 的 744 条里
    混着这批）。**这条断言是「结构性字段不得再漏登记」的唯一门禁**，不许放宽成「跳过」。
    """
    reported = [
        path
        for path in _uncovered_value_leaves_by_view(reason_samples[stem])
        if path not in VALUE_LEAF_UNREGISTERED_ALLOWLIST
    ]
    assert reported == [], (
        f"{stem} 这些叶子**有值**却没在 FIELD_SOURCES 登记"
        f"（会被兜成 not_applicable / unclassified）：{reported}"
    )

    # 非空转前置：该样本确实有大量「有值叶子」，否则上面是空断言
    checked = _value_leaf_count(reason_samples[stem])
    assert checked > 100, f"{stem} 只数到 {checked} 个有值叶子，样本或 walker 接错了"


def test_every_value_bearing_leaf_has_a_field_source_on_the_spike_sample(overview: dict) -> None:
    """同一守门断言跑主样本（`spike/kill.json`：172 个叶子，含 CPU 超载的档位违规）。"""
    assert _uncovered_value_leaves_by_view(overview) == []
    assert _value_leaf_count(overview) > 100


def _stale_allowlist_entries(
    rendered_by_stem: dict[str, dict], allowlist: dict[str, str]
) -> list[str]:
    """白名单里**已经不再被命中**的条目（= 该销账的债务），升序。

    抽成函数是为了让「过期检测」本身可被变异自检（见
    `test_allowlist_expiry_detector_is_not_vacuous`）—— 空表下的过期断言天生是永真的。
    """
    hit = {
        path
        for rendered in rendered_by_stem.values()
        for path in _uncovered_value_leaves_by_view(rendered)
    }
    return sorted(set(allowlist) - hit)


def test_value_leaf_allowlist_has_no_stale_entries(reason_samples: dict[str, dict]) -> None:
    """白名单**必须会过期**：条目一旦不再被命中，要求销账。

    做法与 `tests/test_attr_coverage.py::ALLOWED_BARE_IDS` 一致：豁免不免除债务，只推迟
    还款 —— 没人销账的白名单会把守门断言悄悄变回空转（这条测试就是「还款提醒」）。
    当前白名单为空，所以它同时证明「T-34 之后一个豁免都不需要」。
    """
    stale = _stale_allowlist_entries(reason_samples, VALUE_LEAF_UNREGISTERED_ALLOWLIST)
    assert stale == [], f"这些白名单条目已经不再命中（销账）：{stale}"


def test_allowlist_expiry_detector_is_not_vacuous(reason_samples: dict[str, dict]) -> None:
    """**变异自检**：往白名单里塞一个「不再命中」的条目 → 过期检测必须报出来。

    空表下的过期断言是**永真**的，所以必须证明检测器本身认得出过期条目
    （与 `ALLOWED_BARE_IDS` 的 `test_b_whitelist_has_no_stale_entries` 同一思路）。
    """
    stale_entry = "firepower.dps.base"  # 真实存在、有值、有登记 —— 永远不会进白名单
    bogus = {**VALUE_LEAF_UNREGISTERED_ALLOWLIST, stale_entry: "曾经以为需要豁免"}

    assert _stale_allowlist_entries(reason_samples, bogus) == [stale_entry]
    assert _stale_allowlist_entries(reason_samples, VALUE_LEAF_UNREGISTERED_ALLOWLIST) == []


def test_container_and_element_registrations_are_both_accepted() -> None:
    """**容器 + `[]` 元素两种落点都算「登记过」**（任务书点名：别把 `weapons` 误判成漏登记）。"""
    # ① 元素路径由容器登记覆盖（渲染层 `_tag()` 的容器回退）
    assert _source_registration_of("weapons[].name", {"weapons": None}) == "weapons"
    # ② 空列表容器由元素登记覆盖（对象数组只在空的时候才是一个叶子）
    assert (
        _source_registration_of("weapons", {"weapons[].name": None, "weapons[].count": None})
        == "weapons[]"
    )
    # ③ 整数键字典同理（等级集合由数据决定，只登记容器）
    assert _source_registration_of("count_by_level.4", {"count_by_level": None}) == "count_by_level"
    # ④ 真的没有对应登记 → 未登记（别靠「碰巧有值」蒙混）
    assert _source_registration_of("weapons[].kind", {"weapons[].name": None}) is None
    # ⑤ 反向也成立：`tier_violations[].rule.*` 由容器 `tier_violations` 接住
    constraints_sources = VIEW_MODULES["constraints"].FIELD_SOURCES
    hit = _source_registration_of("tier_violations[].rule.used", constraints_sources)
    assert hit == "tier_violations"


def test_value_leaf_guard_catches_an_injected_unregistered_leaf(overview: dict) -> None:
    """**变异自检**：注入一个「有值但未登记」的叶子 → 守门断言必须变红（不是永真）。

    与「抹掉一条规则」那条互为镜像：这条证明判据认得出「漏登记 + 有值」，
    那条证明规则缺失真的会产出 `unclassified`。
    """
    sources = VIEW_MODULES["constraints"].FIELD_SOURCES
    entry = copy.deepcopy(overview["views"]["constraints"])
    entry["overview"]["__mutation_probe__"] = 1  # 新加字段却忘了登记 FIELD_SOURCES

    assert _uncovered_value_leaves("constraints", entry) == ["__mutation_probe__"]
    # 渲染层对它的判决就是 `unknown`（红线 N4 的默认分支）—— 正是本任务要堵的那一步
    assert render._tag(sources, "__mutation_probe__", 1) == "unknown"
    # 对照：未变异时同一视角是干净的（redness 来自这处注入，不是环境）
    assert _uncovered_value_leaves("constraints", overview["views"]["constraints"]) == []


def test_structural_violation_fields_are_assumed_not_not_applicable(overview: dict) -> None:
    """**T-34 靶心**：`tier_violations[].kind/message/resource/rule.*` 有值 → `assumed`。

    它们是「我们自己算出来的确定值」：`kind` 由 `_classify()` 二分得出、`message` 由本视角
    拼出来、`rule.*` 是引擎规则的原样镜像 —— 此前因为没登记 `FIELD_SOURCES` 落 `unknown`，
    再被 `reasons.py` 兜成 `not_applicable`（「把确定的事说成不适用」）。
    """
    constraints = overview["views"]["constraints"]
    assert constraints["overview"]["tier_violations"], "样本应带档位违规（CPU/PG 超载）"

    for path in (
        "tier_violations[].kind",
        "tier_violations[].message",
        "tier_violations[].resource",
        "tier_violations[].rule.type",
        "tier_violations[].rule.resource",
        "tier_violations[].rule.used",
        "tier_violations[].rule.available",
    ):
        assert constraints["provenance"][path] == "assumed", (
            f"{path} 应为 assumed（合成/枚举值），实为 {constraints['provenance'][path]!r}"
        )
        assert path not in constraints["unknown_reasons"], f"{path} 有值却写了原因（不稀疏）"

    # 反向：分支互斥的空字段仍是 `unknown` + `not_applicable`（这是**真的**不适用）
    for path in (
        "tier_violations[].skill_type_id",
        "tier_violations[].skill_name",
        "tier_violations[].required",
        "tier_violations[].level",
        "tier_violations[].missing_levels",
    ):
        assert constraints["provenance"][path] == "unknown", path
        assert constraints["unknown_reasons"][path] == "not_applicable", path


def test_skill_branch_of_tier_violations_is_assumed_and_resource_fields_are_not_applicable(
    engine: Engine, index
) -> None:
    """**另一分支**：技能类档位违规（`rule.type/type_id/required/level`）同样 → `assumed`。

    这条钉住的是「逐键枚举 `rule.*` 会漏」：36 个 KB 样本里只有资源类违规
    （`rule.used`/`rule.available`），技能类的 `rule.required`/`rule.level` 从未出现过 ——
    容器登记让两种形状都被接住，而**资源那一组**在这条违规上才是真的「不适用」。
    """
    fit = engine.load_eft(SKILL_VIOLATION_EFT)
    fit["character"] = {"skills": {}}  # 一个技能都没有 → 每条模块要求都成为技能违规
    skill_ctx = ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )
    original_budget = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        entry = render.render_overview(skill_ctx)["views"]["constraints"]
    finally:
        render.BUDGET_CHARS = original_budget

    kinds = {row["kind"] for row in entry["overview"]["tier_violations"]}
    assert kinds == {"skill"}, f"前提：这套配装应只产生技能类档位违规，实为 {kinds}"

    for path in (
        "tier_violations[].kind",
        "tier_violations[].message",
        "tier_violations[].skill_type_id",
        "tier_violations[].skill_name",
        "tier_violations[].required",
        "tier_violations[].level",
        "tier_violations[].missing_levels",
        "tier_violations[].rule.type",
        "tier_violations[].rule.type_id",
        "tier_violations[].rule.required",
        "tier_violations[].rule.level",
    ):
        assert entry["provenance"][path] == "assumed", (
            f"{path} 应为 assumed，实为 {entry['provenance'][path]!r}"
        )

    for path in (
        "tier_violations[].resource",
        "tier_violations[].used",
        "tier_violations[].available",
        "tier_violations[].shortfall",
    ):
        assert entry["provenance"][path] == "unknown", path
        assert entry["unknown_reasons"][path] == "not_applicable", path


def test_hard_violation_entries_are_assumed(engine: Engine, index) -> None:
    """`hard_violations[].rule.*` / `.target.*` / `.message` → `assumed`（容器登记继承）。

    36 个样本的 `hard_violations` 恒为 `[]`，所以这条路径此前**没有任何断言覆盖**；
    这里现场造一条 `charge_size`（小船装大号弹药）把它钉住。
    """
    fit = engine.load_eft(HARD_VIOLATION_EFT)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    hard_ctx = ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )
    original_budget = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        rendered = render.render_overview(hard_ctx)
    finally:
        render.BUDGET_CHARS = original_budget

    entry = rendered["views"]["constraints"]
    assert entry["overview"]["hard_violations"], "前提：这套配装应产生一条硬违规（charge_size）"
    assert entry["overview"]["tier_violations"] == [], "前提：硬违规不应被算成档位违规"

    for path in (
        "hard_violations[].rule.type",
        "hard_violations[].rule.module",
        "hard_violations[].rule.charge",
        "hard_violations[].target.type",
        "hard_violations[].target.index",
        "hard_violations[].message",
    ):
        assert entry["provenance"][path] == "assumed", (
            f"{path} 应为 assumed，实为 {entry['provenance'][path]!r}"
        )
    assert entry["unknown_reasons"] == {}, "硬违规视角不该有 unknown（空清单容器已是 assumed）"


NOT_APPLICABLE_LEAVES: dict[str, tuple[str, ...]] = {
    "f1-hecate-killmail": (
        "constraints.tier_violations[].level",
        "constraints.tier_violations[].missing_levels",
        "constraints.tier_violations[].required",
        "constraints.tier_violations[].skill_name",
        "constraints.tier_violations[].skill_type_id",
        "firepower.application.explosion_radius_m",
        "firepower.application.explosion_velocity_mps",
        "firepower.dps.drone",
        "firepower.dps.fighter",
        "firepower.range.max_range_m",
    ),
    "f4-non-hecate": (
        "constraints.tier_violations[].level",
        "constraints.tier_violations[].missing_levels",
        "constraints.tier_violations[].required",
        "constraints.tier_violations[].skill_name",
        "constraints.tier_violations[].skill_type_id",
        "firepower.dps.fighter",
        "firepower.weapons[].ammo_name",
        "stability.cap_sensitivity[].depletes_in_s",
        "stability.capacitor.depletes_in_s",
        "stability.consumables.paste_endurance_s",
        "stability.consumables.paste_per_cycle",
    ),
    "f5-no-mwd": (
        "constraints.tier_violations[].level",
        "constraints.tier_violations[].missing_levels",
        "constraints.tier_violations[].required",
        "constraints.tier_violations[].skill_name",
        "constraints.tier_violations[].skill_type_id",
        "firepower.dps.fighter",
        "firepower.weapons[].ammo_name",
        "stability.cap_sensitivity[].depletes_in_s",
        "stability.capacitor.depletes_in_s",
        "stability.consumables.paste_endurance_s",
        "stability.consumables.paste_per_cycle",
    ),
    "f26-carrier-fighter": (
        "firepower.ammo_endurance_s",
        "firepower.application.explosion_radius_m",
        "firepower.application.explosion_velocity_mps",
        "firepower.application.optimal_signature_radius_m",
        "firepower.application.tracking_rad_s",
        "firepower.damage_split.em",
        "firepower.damage_split.explosive",
        "firepower.damage_split.kinetic",
        "firepower.damage_split.thermal",
        "firepower.dph",
        "firepower.dps.drone",
        "firepower.overload.delta.range_m",
        "firepower.overload.off.range_m",
        "firepower.overload.on.range_m",
        "firepower.range.max_range_m",
        "firepower.range.optimal_m",
        "firepower.range.optimal_plus_2falloff_m",
        "firepower.range.optimal_plus_falloff_m",
        "firepower.weapons",
        "stability.cap_sensitivity[].depletes_in_s",
        "stability.capacitor.depletes_in_s",
        "stability.consumables.ammo_cycle_s",
        "stability.consumables.ammo_endurance_s",
        "stability.consumables.ammo_module",
        "stability.consumables.ammo_rounds",
        "stability.consumables.paste_endurance_s",
        "stability.consumables.paste_per_cycle",
    ),
}
"""四个守门样本上 `not_applicable` 桶的**全量预期清单**（T-34 之后的形状）。

为什么钉死清单而不是只断言「⊆ 条件字段表」：`not_applicable` 的含义是**真的不适用**，
所以清单里每一条都必须能被一句人话解释（没有导弹 / 没有无人机 / 没有 paste /
电容稳定 / 违规条目缺的是另一分支的字段）。宽口径会把这批污染重新吞回去 ——
T-32 的 744 条里就有 144 条是「有值却说不适用」的合成值（`tier_violations[].kind` 等）。

清单只与 fixture 的**结构**绑定（哪套配装有没有导弹/无人机/paste、档位违规是哪一类），
与 SDE 数值无关；fixture 是只读输入，所以它会一直成立。若某天真变了，正确的反应是
**逐条重新解释**这份清单，而不是放宽断言。
"""


@pytest.mark.parametrize("stem", REASON_SAMPLES)
def test_not_applicable_bucket_holds_only_conditional_fields(
    reason_samples: dict[str, dict], stem: str
) -> None:
    """**`not_applicable` 桶里只剩真条件字段**（两个方向一起钉）。

    方向一：清单**逐条相等**（多一条少一条都要有人解释）；
    方向二：每条 `not_applicable` 的**值必须是「没有值」**（`None` 或空容器）——
    「有值却说不知道 / 不适用」正是本任务要消灭的那类错误。
    """
    rendered = reason_samples[stem]
    actual: list[str] = []
    for view_name, entry in rendered["views"].items():
        values = _leaf_values(entry["overview"])
        for path, reason in entry["unknown_reasons"].items():
            if reason != "not_applicable":
                continue
            actual.append(f"{view_name}.{path}")
            assert _has_no_value(values[path]), (
                f"{stem}.{view_name}.{path} 有值 {values[path]!r} 却判 not_applicable —— "
                "该登记 FIELD_SOURCES（合成值 → None → assumed）"
            )

    assert sorted(actual) == list(NOT_APPLICABLE_LEAVES[stem]), (
        f"{stem} 的 not_applicable 清单变了：\n"
        f"  多：{sorted(set(actual) - set(NOT_APPLICABLE_LEAVES[stem]))}\n"
        f"  少：{sorted(set(NOT_APPLICABLE_LEAVES[stem]) - set(actual))}"
    )


# =============================================================================
# 十二、L2（detail）契约审查：missing / 未降级 / 512 技能表 / provenance 完整性 / 跨层一致
#     （dev-engine 4/5；每条都带「改坏就红」的变异证明，见交付报告）
# =============================================================================

DETAIL_AUDIT_EFT = """[Hecate, l2-audit]

Damage Control II
1600mm Steel Plates II
Multispectrum Coating II

50MN Quad LiF Restrained Microwarpdrive

Light Neutron Blaster II, Void S
Light Neutron Blaster II, Void S
Light Neutron Blaster II, Void S

Void S x1000
"""
"""L2 审查用的小样本（无外部依赖的 EFT 文本）。"""

CAP_SAMPLE_EFT = """[Osprey Navy Issue, capmany]

Damage Control II

Multispectrum Shield Hardener II
Multispectrum Shield Hardener II
Multispectrum Shield Hardener II
Multispectrum Shield Hardener II
Multispectrum Shield Hardener II
Multispectrum Shield Hardener II

Rapid Light Missile Launcher II
"""
"""会命中 `repaired_but_unstable` 的样本（用来证明 detail 的 flag **保留 evidence**）。"""


def _ctx_for(engine: Engine, index, text: str) -> ViewContext:
    """一段 EFT 文本 → `ViewContext`（技能档与其它 fixtures 同一套口径）。"""
    fit = engine.load_eft(text)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


def _api():
    """接口层模块（函数内 import：`test_render` 只做渲染层单测，不拖慢模块加载）。"""
    from noobbaozi import api

    return api


# ---- ① missing 的语义 ----


def test_detail_missing_is_the_requested_field_string() -> None:
    """找不到的字段：`missing` 里是**调用方传进来的那个字段串**（去掉视角名前缀），不静默忽略。

    实测口径：请求 `stability.no_such_field` → `views.stability.missing == ["no_such_field"]`。
    视角名已经是 envelope 的键，所以「视角 + missing 里的串」就是原请求（不丢信息）；
    越深的路径同样原样保留（`capacitor.bogus`）。
    """
    api = _api()
    for request, expected in (
        ("stability.no_such_field", ["no_such_field"]),
        ("stability.capacitor.bogus", ["capacitor.bogus"]),
        ("firepower.weapons[9].name", ["weapons[9].name"]),  # 越界下标 = 找不到，不是 None
    ):
        view = request.split(".", 1)[0]
        envelope = api.fit_detail(DETAIL_AUDIT_EFT, path=request)
        assert envelope["ok"] is True, envelope.get("error")
        entry = envelope["result"]["views"][view]
        assert entry["missing"] == expected, request
        assert entry["overview"] == {}, "找不到就不许编一个空壳值出来"


def test_detail_missing_is_empty_for_existing_paths_and_full_views() -> None:
    """能取到的时候 `missing` 必须是 `[]`（空 `missing` 本身不代表缺字段，得配上值看）。"""
    api = _api()
    cases = (
        dict(),  # 六视角全给
        dict(view="stability"),
        dict(path="stability.capacitor"),
        dict(path="resources.upgrade_space"),
        dict(view="resources", path="resources.cpu"),
    )
    for kwargs in cases:
        envelope = api.fit_detail(DETAIL_AUDIT_EFT, **kwargs)
        assert envelope["ok"] is True, (kwargs, envelope.get("error"))
        views = envelope["result"]["views"]
        assert views, kwargs
        for name, entry in views.items():
            assert entry["missing"] == [], (kwargs, name)
            assert entry["overview"] != {}, (kwargs, name, "能取到时 overview 不许是空对象")


def test_detail_rejects_a_path_without_a_registered_view() -> None:
    """`path` 的首段必须是视角名；`view` 与 `path` 不一致是参数错，不是 `missing`。"""
    api = _api()
    missing_view = api.fit_detail(DETAIL_AUDIT_EFT, path="no_such_view.whatever")
    assert missing_view["ok"] is False
    assert missing_view["error"]["kind"] == "invalid_path"

    mismatch = api.fit_detail(DETAIL_AUDIT_EFT, view="firepower", path="stability.capacitor")
    assert mismatch["ok"] is False
    assert mismatch["error"]["kind"] == "invalid_argument"

    bad_view = api.fit_detail(DETAIL_AUDIT_EFT, view="no_such_view")
    assert bad_view["ok"] is False
    assert bad_view["error"]["kind"] == "invalid_argument"


# ---- ①′ 路径语言跨层一致：`[N]` 必须和 get_path 一样被认 ----


def test_detail_accepts_index_paths_exactly_like_get_path(
    ctx: ViewContext, killmail: dict
) -> None:
    """`tier_violations[0].kind` 这种**下标**写法：L1 的 `get_path` 认，L2 必须也认。

    改之前 `render_detail` 只认 `[]`（逐元素），同一串路径在 `fit_get` 上能取、在
    `fit_detail` 上落进 `missing` —— 同一个产品两种路径语言，agent 一定会踩。
    """
    api = _api()
    full = render.render_detail(ctx, view="constraints")["overview"]["tier_violations"]
    assert full, "反证：样本必须有 tier_violations，否则断言永真"

    path = "constraints.tier_violations[0].kind"
    l1 = api.fit_get(killmail, path)
    l2 = api.fit_detail(killmail, path=path)

    assert l1["ok"] is True and l1["result"]["value"] == full[0]["kind"]
    assert l2["ok"] is True, l2.get("error")
    entry = l2["result"]["views"]["constraints"]
    assert entry["missing"] == [], f"L2 少认一种 L1 认的写法：{path}"
    assert entry["overview"] == {"tier_violations": [{"kind": full[0]["kind"]}]}
    # 有值就必须有三态（provenance 用元素约定 `[]` 的键空间匹配下标请求）
    assert entry["provenance"] == {"tier_violations[].kind": "assumed"}


def test_select_index_form_keeps_position_and_reports_out_of_range() -> None:
    """`_select` 的 `[N]` 口径：**保位置**（未请求的元素是 `{}` 占位），越界进 `missing`。"""
    overview = {"weapons": [{"name": "A"}, {"name": "B"}, {"name": "C"}]}

    picked, missing = render._select(overview, ["weapons[2].name"])
    assert picked == {"weapons": [{}, {}, {"name": "C"}]}, "下标 2 的元素不许被摆到位置 0"
    assert missing == []

    blank, missing_blank = render._select(overview, ["weapons[5].name"])
    assert blank == {} and missing_blank == ["weapons[5].name"]

    # 多字段合并：同一列表的两个下标要落在各自位置
    merged, missing_merged = render._select(overview, ["weapons[0].name", "weapons[1].name"])
    assert merged == {"weapons": [{"name": "A"}, {"name": "B"}]}
    assert missing_merged == []

    # `[]` 的逐元素语义不受影响
    elementwise, missing_elementwise = render._select(overview, ["weapons[].name"])
    assert elementwise == {"weapons": [{"name": "A"}, {"name": "B"}, {"name": "C"}]}
    assert missing_elementwise == []


# ---- ② detail 的 overview 必须**真的未降级** ----


def test_detail_gives_the_full_upgrade_space_while_overview_summarizes(ctx: ViewContext) -> None:
    """`resources.upgrade_space`：L1 是摘要 dict，L2 是**完整候选列表**（长度 = 摘要里的计数）。"""
    l1_entry = render.render_view(ctx, "resources")
    l2_entry = render.render_detail(ctx, view="resources")

    summary = l1_entry["overview"]["upgrade_space"]
    full = l2_entry["overview"]["upgrade_space"]

    assert isinstance(summary, dict), "L1 必须是摘要（渲染层的粒度决定）"
    assert isinstance(full, list) and full, "L2 必须给完整候选清单"
    assert summary["slots_checked"] == len(full), "摘要的计数与 L2 的条数必须对得上"
    assert set(summary) == {"slots_checked", "upgradeable_count", "blocked_by_resource"}
    # L2 每行是**逐条明细**，不是摘要
    assert all({"slot", "module", "candidate", "fits"} <= set(row) for row in full)
    # 摘要叶子只属于 L1；L2 的 provenance 给的是逐条明细的叶子
    assert "upgrade_space.slots_checked" in l1_entry["provenance"]
    assert "upgrade_space.slots_checked" not in l2_entry["provenance"]
    assert "upgrade_space[].fits" in l2_entry["provenance"]


def test_detail_keeps_list_rows_and_flag_evidence_when_overview_degrades(
    engine: Engine, index, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**强制 L1 降级**（预算 = 1）：L1 必须丢 flag 的 `evidence`，而 L2 一行都不许少。

    这是「detail 未降级」最硬的证据 —— 降级的第一档就是剥掉诊断证据，而 L2 没有预算概念。
    """
    ctx = _ctx_for(engine, index, CAP_SAMPLE_EFT)
    monkeypatch.setattr(render, "BUDGET_CHARS", 1)

    overview = render.render_overview(ctx, views=("stability",))
    detail = render.render_detail(ctx, view="stability")

    assert overview["budget"]["degraded"] is True, "预算 = 1 时必须降级，否则这条测试在空转"
    l1_flags = overview["views"]["stability"]["flags"]
    assert l1_flags, "反证：样本必须命中至少一条诊断，否则比的是两堆空列表"
    assert all("evidence" not in flag for flag in l1_flags), "降级第一档：L1 丢 evidence"
    assert all("evidence" in flag for flag in detail["flags"]), "L2 不许降级，evidence 必须留着"
    # L2 没有降级标记（预算块只属于 L1）
    assert "budget" not in detail
    assert set(detail) == {
        "meta",
        "view",
        "overview",
        "provenance",
        "unknown_reasons",
        "confidence",
        "flags",
        "missing",
    }


# ---- ③ 512 条技能表只在 detail ----


def _assert_skill_table_only_in_detail(l0: dict, l1: dict, l2: dict) -> None:
    """三条一起验：L0/L1 一条都不许有、L2 必须 512 条且与 `meta.skill_tier` 一致。"""
    for label, envelope in (("L0", l0), ("L1", l1)):
        assert envelope["ok"] is True, (label, envelope.get("error"))
        assert "assumptions" not in (envelope.get("meta") or {}), f"{label} 不许带完整技能表"
        assert "assumptions" not in (envelope.get("result") or {}), f"{label} result 也不许带"

    assert l2["ok"] is True, l2.get("error")
    assumptions = (l2.get("meta") or {}).get("assumptions")
    assert isinstance(assumptions, dict), "detail 的 meta 必须带 assumptions"
    skills = assumptions.get("skill_tier")
    assert isinstance(skills, dict) and len(skills) == 512, "技能表必须是 512 条"

    tier = (l2.get("meta") or {}).get("skill_tier") or {}
    assert tier.get("count") == 512
    assert tier.get("name") == assumptions.get("skill_tier_name"), "两处技能档名必须一致"


def test_skill_table_appears_only_in_the_detail_meta() -> None:
    """完整技能表（512 条）只在 `fit_detail` 的 `meta.assumptions` 里；L0 / L1 一条都不许有。

    同时钉三件事：条数 = 512、两次调用**逐位相同**、与 `meta.skill_tier`（name/count）一致。
    """
    import hashlib

    api = _api()
    l0 = api.fit_index(DETAIL_AUDIT_EFT)
    l1 = api.fit_view(DETAIL_AUDIT_EFT, "resources")
    l2 = api.fit_detail(DETAIL_AUDIT_EFT)
    _assert_skill_table_only_in_detail(l0, l1, l2)

    skills = (l2.get("meta") or {})["assumptions"]["skill_tier"]
    again = (api.fit_detail(DETAIL_AUDIT_EFT).get("meta") or {}).get("assumptions") or {}
    digest = lambda table: hashlib.md5(  # noqa: E731
        json.dumps(table, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()
    assert digest(again.get("skill_tier")) == digest(skills), "两次调用的技能表必须逐位相同"


def test_skill_table_guard_catches_a_detail_meta_without_assumptions() -> None:
    """**非永真证明**：把 detail 的 `assumptions` 摘掉 → 同一套检查必须红。

    守门测试最怕「表里永远没有这个值」所以永真；这里是自检：注入一个坏信封，判据必须看得见。
    """
    api = _api()
    l0 = api.fit_index(DETAIL_AUDIT_EFT)
    l1 = api.fit_view(DETAIL_AUDIT_EFT, "resources")
    l2 = copy.deepcopy(api.fit_detail(DETAIL_AUDIT_EFT))
    l2["meta"].pop("assumptions", None)

    with pytest.raises(AssertionError):
        _assert_skill_table_only_in_detail(l0, l1, l2)

    # 再把条数改错一位：同样必须红
    l2_bad = copy.deepcopy(api.fit_detail(DETAIL_AUDIT_EFT))
    skills = l2_bad["meta"]["assumptions"]["skill_tier"]
    l2_bad["meta"]["assumptions"]["skill_tier"] = dict(list(skills.items())[:511])
    with pytest.raises(AssertionError):
        _assert_skill_table_only_in_detail(l0, l1, l2_bad)

    # 反向：真实信封必须通过（否则上面的 raises 可能只是 helper 有 bug）
    _assert_skill_table_only_in_detail(l0, l1, api.fit_detail(DETAIL_AUDIT_EFT))


# ---- ④ provenance / unknown_reasons 在 detail 的完整性 ----


@pytest.mark.parametrize("view", available_views())
def test_detail_provenance_covers_every_overview_leaf(ctx: ViewContext, view: str) -> None:
    """L2 的 `provenance` **必须覆盖 detail overview 的每一个叶子**（三张表键空间对齐）。

    L1 的覆盖率守门只看降级后的 overview；detail 是**另一份**（更长）的 overview，
    它自己的叶子必须同样一条不少 —— 否则 agent 拿到值却没有可信度。
    """
    detail = render.render_detail(ctx, view=view)

    leaves = set(render._leaf_paths(detail["overview"], ""))
    assert leaves, f"{view} 的 detail overview 不许为空"
    assert set(detail["provenance"]) == leaves, (
        f"{view} 的 provenance 键空间与 detail overview 叶子不一致："
        f"缺={sorted(leaves - set(detail['provenance']))[:5]} "
        f"多={sorted(set(detail['provenance']) - leaves)[:5]}"
    )


@pytest.mark.parametrize("view", available_views())
def test_detail_every_unknown_leaf_carries_a_reason(ctx: ViewContext, view: str) -> None:
    """`provenance == "unknown"` 的叶子必须能在 `unknown_reasons` 里找到原因（L2 同口径）。"""
    detail = render.render_detail(ctx, view=view)

    unknown = {path for path, state in detail["provenance"].items() if state == "unknown"}
    assert unknown <= set(detail["unknown_reasons"]), (
        f"{view} 有 unknown 却没给原因：{sorted(unknown - set(detail['unknown_reasons']))[:5]}"
    )
    # 反过来：reasons 的键必须在 provenance 里，且确实标了 unknown（不许有孤儿原因）
    for path, reason in detail["unknown_reasons"].items():
        assert detail["provenance"].get(path) == "unknown", path
        assert isinstance(reason, str) and reason


def test_detail_reports_the_detail_only_paths_in_provenance(ctx: ViewContext) -> None:
    """detail 独有的路径（升级候选 / 逐模块电容）在 provenance 里**有交代**。"""
    resources = render.render_detail(ctx, view="resources")
    stability = render.render_detail(ctx, view="stability")

    up = resources["overview"]["upgrade_space"]
    assert up, "反证：样本要有升级候选"
    up_prov = {path for path in resources["provenance"] if path.startswith("upgrade_space[")}
    assert up_prov, "upgrade_space 的**逐条明细**必须在 provenance 里出现"
    assert {"upgrade_space[].fits", "upgrade_space[].candidate"} <= up_prov

    cap = stability["overview"]["cap_by_module"]
    assert cap, "反证：样本要有逐模块电容明细"
    cap_prov = {path for path in stability["provenance"] if path.startswith("cap_by_module[")}
    assert "cap_by_module[].cap_use_gj_s" in cap_prov
    assert all(state in PROVENANCE_STATES for state in stability["provenance"].values())


# ---- ⑤ 跨层：同一个视角，L1 与 L2 除「被降级的那条路径」外必须逐字节一致 ----


@pytest.mark.parametrize("view", available_views())
def test_detail_matches_overview_apart_from_the_downgraded_path(
    ctx: ViewContext, view: str
) -> None:
    """**detail 与 L1 说法不一致的守门**：除 `OVERVIEW_DOWNGRADES` 登记的路径外必须一模一样。

    这是本任务最担心的那件事的自动化版本：同一个字段在两层给出不同数字/形状，
    只要不是登记在案的粒度改写，就必须变红。
    """
    l1 = render.render_view(ctx, view)
    l2 = render.render_detail(ctx, view=view)
    downgraded = set(render.OVERVIEW_DOWNGRADES.get(view) or {})

    l1_overview = {key: value for key, value in l1["overview"].items() if key not in downgraded}
    l2_overview = {key: value for key, value in l2["overview"].items() if key not in downgraded}
    assert l1_overview == l2_overview, f"{view} 的 L1/L2 在非降级路径上分叉"

    assert l1["flags"] == l2["flags"], f"{view} 的 flags 在两层不一致"
    assert l1["unknown_reasons"] == l2["unknown_reasons"]
    assert l1["confidence"] == l2["confidence"]

    # 降级路径只在 resources 上存在，且 L1 是摘要、L2 是列表（粒度不同但**不矛盾**）
    if downgraded:
        for path in downgraded:
            assert isinstance(l1["overview"][path], dict)
            assert isinstance(l2["overview"][path], list)

