"""T2/T3 测试：分级渲染层（`project/render.py` + `views/loader.py`）。

样本：`evefit/spike/kill.json`（killmail 139246907，Hecate），技能档走
`engine.assumptions.derive_skill_tier`（**必须传索引**：全部技能 4 级 = 512 个），
与 `tests/test_projection_core.py` 同构。

**本文件刻意不钉任何绝对数值**（SDE 更新会动数值），只断言结构与关系：

1. `render_overview` 的视角键 = `VIEW_ORDER`，每个视角
   `{overview, provenance, confidence, flags}`；
2. **每个视角 provenance 覆盖率 100%**：`set(provenance) == set(overview 叶子点路径)`，
   且三态取值合法、`FIELD_SOURCES` 的映射（含 `None` → `assumed`）逐条对得上；
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
   `degraded is False`（T-18 实测 14,083 chars < 17,000）。
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

约束：无网络、无随机（红线 N1）；样本只读；只碰本任务写入范围内的文件。
"""

from __future__ import annotations

import json
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from evefit import contracts
from evefit.config.paths import db_path
from evefit.engine.adapter import Engine
from evefit.engine.assumptions import default_assumptions, derive_skill_tier
from evefit.project import confidence as C
from evefit.project import diagnostics as D
from evefit.project import render
from evefit.project.views import VIEW_ORDER, available_views, build_view
from evefit.project.views.base import ViewContext
from evefit.project.views.loader import VIEW_MODULES, load_views
from evefit.sde.index import Index

EVE_ROOT = Path(__file__).resolve().parents[1]
KILL_PATH = EVE_ROOT / "spike" / "kill.json"
RENDER_PATH = EVE_ROOT / "project" / "render.py"
BASELINE_PATH = EVE_ROOT / "tools" / "baseline_after_tierfix.json"

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
        pytest.skip("索引库未构建：先跑 evefit.sde.index.build_index()")
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
        assert module.__name__ == f"evefit.project.views.{name}"
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
    """每个视角条目 = `{overview, provenance, confidence, flags}`，类型都对。"""
    for name, entry in overview["views"].items():
        assert set(entry) == {"overview", "provenance", "confidence", "flags"}, name
        assert isinstance(entry["overview"], dict) and entry["overview"], name
        assert isinstance(entry["provenance"], dict), name
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
    """映射 `None` → `assumed`；映射到属性 ID → `provenance.tag(ID)`。

    只检查**确实出现在这次 overview 里**的映射路径（空列表会被折叠成容器路径，
    见渲染层 docstring），避免把结构差异误判成标签错误。
    """
    from evefit.project import provenance as P  # 局部 import：只为对拍三态

    sources = VIEW_MODULES[view_name].FIELD_SOURCES
    states = overview["views"][view_name]["provenance"]

    checked = 0
    for path, attribute_id in sources.items():
        if path not in states:
            continue
        expected = "assumed" if attribute_id is None else P.tag(int(attribute_id))
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


def test_provenance_marks_unmapped_and_unknowable_as_unknown(overview: dict) -> None:
    """没有映射 / 映射到不可知属性 → `unknown`（红线 N4：默认兜底，不猜、不拿默认值冒充）。"""
    firepower = overview["views"]["firepower"]
    # 已装填弹药的 114/116/117/118 会出现在装备级 attributes 里（发射器并入装填弹药属性）
    # → 属性级 measured；旧结论「弹药属性不在 calculate 输出」对已装填弹药不成立
    assert firepower["provenance"]["damage_split.em"] == "measured"

    # 未登记字段同样兜底 unknown（`tier_violations[].kind` 还没被 FIELD_SOURCES 收编）
    constraints = overview["views"]["constraints"]
    assert constraints["overview"]["tier_violations"], "样本应带档位违规（CPU/PG 超载）"
    assert constraints["provenance"]["tier_violations[].kind"] == "unknown"


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
    monkeypatch_budget = render.BUDGET_CHARS
    render.BUDGET_CHARS = BIG_BUDGET
    try:
        subset = render.render_overview(ctx, views=("mobility",))
    finally:
        render.BUDGET_CHARS = monkeypatch_budget

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
    assert "from evefit.project import units" not in source
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

    # ④ provenance / confidence 不因降级而变（叶子键空间与档位都不该动）
    for name in VIEW_ORDER:
        assert degraded["views"][name]["provenance"] == overview["views"][name]["provenance"]
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
    """不带 `fields` 时返回该视角完整 overview + provenance + confidence + flags。"""
    detail = render.render_detail(ctx, view="stability")
    entry = overview["views"]["stability"]

    assert set(detail) == {
        "meta",
        "view",
        "overview",
        "provenance",
        "confidence",
        "flags",
        "missing",
    }
    assert detail["view"] == "stability"
    assert detail["meta"]["scope"] == "detail"
    assert detail["overview"] == entry["overview"]
    assert detail["provenance"] == entry["provenance"]
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
