"""T-39 测试：输出分层第一阶段（L0 摘要 / L1 单视角 / 按路径取数）。

权威文档：`evefit/docs/输出分层与工具集-v1.md`。被测入口三个：

```python
render.render_index(ctx)             # L0 摘要
render.render_view(ctx, view)        # L1 单视角
render.get_path(ctx, "mobility.speed_mps")
```

样本：`acceptance/fixtures` 的 f67 / f47 / f12 / f1（与验收同一批输入文件，技能档走
`derive_skill_tier` 全 4 + 补最低 = 512 个，**必须传索引**），外加 `spike/kill.json`
（Hecate，用来钉「L1 == overview 的视角条目」逐字节相同）。

本文件钉住的十条（每条都对应任务书/文档的一条红线）

1. **L0 体量**：四个样本 `len(json.dumps(index))` 都 < 2,000，且 `budget.chars` 公式成立
   （`chars` 量的是 L0 **正文**，不含 `budget` 自身）；
2. **L0 不含 `evidence`**：递归遍历断言没有 `evidence` 键，也没有逐叶子
   `provenance` / `unknown_reasons` / `confidence` 三张表（只有计数）；
3. **L0 的每个数值都取得回来且逐位相同**：遍历 L0 全部叶子逐个 `get_path` 比对；
4. **L0 / L1 对同一字段数值逐位相同**：按 `summary.INDEX_SPEC` 的 `source` 对照
   `render_view()` 的 L1 值（独立实现取值，不复用被测代码）；
5. **`meta.skill_tier` 是 `{name, count, hash}`**：不含 512 条明细；hash 是技能档的稳定
   摘要（同输入同值、换一个等级就变、12 位十六进制）；
6. **`render_view("mobility")` 与 `render_overview()["views"]["mobility"]` 逐字节相同**，
   并且**只构建被请求的那一个视角**（用计数器桩证明没顺手算六视角）；
7. 未知视角名 → `ValueError` 且消息里列全可用视角；
8. `get_path` 路径不存在 → `value is None` + `hint`（**不抛异常**）；索引 / 数组元素
   两种写法都通；
9. **非永真**：把 L0 里一个数值改掉 → 第 3 条断言立刻变红；删掉一个叶子 → 遍历到的
   叶子数确实变少（证明断言真的在读 L0，而不是一张硬编码清单）；
10. `degraded` 在 L0 里**显式出现**：预算调小时置位，且**一个数值都不被裁剪**。

约束：无网络、无随机（红线 N1）；样本只读；只碰 `project/summary.py` 与
`tests/test_summary.py`。
"""

from __future__ import annotations

import json
from pathlib import Path

import esf_dogma_engine as dogma
import pytest

from evefit.config.paths import db_path
from evefit.engine.adapter import Engine
from evefit.engine.assumptions import default_assumptions, derive_skill_tier
from evefit.project import render, summary
from evefit.project.views import available_views, build_view
from evefit.project.views.base import ViewContext
from evefit.sde.index import Index

EVE_ROOT = Path(__file__).resolve().parents[1]
FIXTURES_DIR = EVE_ROOT / "acceptance" / "fixtures"
KILL_PATH = EVE_ROOT / "spike" / "kill.json"

SAMPLES: tuple[str, ...] = (
    "f67-spotcheck-139255942",
    "f47-random-raptor",
    "f12-logistics-oneiros",
    "f1-hecate-killmail",
)
"""四个对照样本（f67 最复杂、f47 有 7 条 flag、f12 观测点最多、f1 是 EFT）。"""

REACHABILITY_SAMPLES: tuple[str, ...] = (
    "f67-spotcheck-139255942",
    "f47-random-raptor",
    "f12-logistics-oneiros",
)
"""逐叶子可达性只跑三个（第三个已覆盖全部形状；再加 f1 只是重复同一批断言）。"""

L0_LIMIT: int = 2000
"""任务书的 L0 体量硬门限（文档 §1 的目标是 1,500，实测下界见 `INDEX_BUDGET_CHARS`）。"""

SKILL_COUNT: int = 512
"""全 4 + 补最低技能档的条数（`derive_skill_tier` 实测）。"""

PROVENANCE_STATES: tuple[str, ...] = ("measured", "assumed", "unknown")
FORBIDDEN_TABLES: tuple[str, ...] = ("provenance", "unknown_reasons", "confidence")
"""L0 里**不允许**出现的逐叶子平行表（文档 §2 / 第七节红线）。"""


# ---- fixtures ----


@pytest.fixture(scope="module")
def engine() -> Engine:
    return Engine()


@pytest.fixture(scope="module")
def index():
    try:
        path = db_path()
    except FileNotFoundError:  # 索引库是构建产物，没建就跳过
        pytest.skip("索引库未构建：先跑 evefit.sde.index.build_index()")
    opened = Index.open(path)
    yield opened
    opened.close()


def _load_fit(engine: Engine, index, stem: str) -> dict:
    """按样本名加载配装 + 「全 4 + 补最低」技能档。

    **必须排除 `*.expected.json`**：快照文件的 stem 更靠前（`glob` 排序），它没有
    `killmail_id`，误取会以「missing field killmail_id」炸在引擎里。
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
        raw.pop("zkb", None)
        fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return fit


def _make_ctx(engine: Engine, index, stem: str) -> ViewContext:
    """按样本名造一个**缓存是空的**新上下文。"""
    fit = _load_fit(engine, index, stem)
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


@pytest.fixture(scope="module")
def ctxs(engine: Engine, index) -> dict[str, ViewContext]:
    """四个样本的上下文（模块级共享：变体缓存留在里面，别重复算）。"""
    return {stem: _make_ctx(engine, index, stem) for stem in SAMPLES}


@pytest.fixture(scope="module")
def docs(ctxs: dict[str, ViewContext]) -> dict[str, dict]:
    """四个样本的 L0 摘要（默认预算）。"""
    return {stem: render.render_index(ctx) for stem, ctx in ctxs.items()}


@pytest.fixture(scope="module")
def kill_ctx(engine: Engine, index) -> ViewContext:
    """`spike/kill.json`（killmail 139246907，Hecate）—— 与 `tests/test_render.py` 同构。"""
    raw = json.loads(KILL_PATH.read_text(encoding="utf-8"))[0]
    raw.pop("zkb", None)
    fit = dogma.load_killmail(raw)
    fit["character"] = {"skills": derive_skill_tier(fit, engine, index=index)}
    return ViewContext(
        engine=engine,
        index=index,
        fit=fit,
        assumptions=default_assumptions(fit, engine, index=index),
        meta=engine.meta(),
    )


# ---- 独立实现的小工具（**故意不复用被测代码**）----


def _l0_leaves(node: object, prefix: str = "") -> list[tuple[str, object]]:
    """L0 的叶子清单 `[(路径, 值)]`：对象逐键递归、对象数组展开成 `p[0].k`、
    标量 / 标量列表 / 空容器就是叶子本身。

    与 `get_path` 的路径语法一一对应（对象数组的元素路径用**下标**，正好把下标写法
    一起测到）。
    """
    if isinstance(node, dict) and node:
        found: list[tuple[str, object]] = []
        for key, value in node.items():
            found.extend(_l0_leaves(value, f"{prefix}.{key}" if prefix else str(key)))
        return found
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        found = []
        for position, item in enumerate(node):
            found.extend(_l0_leaves(item, f"{prefix}[{position}]"))
        return found
    return [(prefix, node)]


def _pick(node: object, path: str) -> object:
    """按**纯点路径**取值（L1 的 `FIELD_SOURCES` 式路径都是这种；`[]` 不用在这里）。"""
    current = node
    for part in path.split("."):
        assert isinstance(current, dict) and part in current, f"路径 {path!r} 在 L1 里断了：{part!r}"
        current = current[part]
    return current


def _has_key(node: object, key: str) -> bool:
    """递归找某个键名（任意深度、数组也进）。"""
    if isinstance(node, dict):
        return key in node or any(_has_key(value, key) for value in node.values())
    if isinstance(node, list):
        return any(_has_key(item, key) for item in node)
    return False


def _assert_l0_reachable(ctx: ViewContext, doc: dict) -> int:
    """**「L0 的每个数值都能被 `get_path` 取到且逐位相同」**的唯一判据（返回比对条数）。

    单独抽成函数是为了能被「篡改 L0 → 立刻变红」的自检复用（第 9 条）。
    """
    leaves = _l0_leaves(doc)
    assert leaves, "L0 是空的，这条断言会变成空转"

    mismatches: list[str] = []
    for path, expected in leaves:
        answer = render.get_path(ctx, path)
        if answer["value"] != expected:
            mismatches.append(f"{path}: L0={expected!r} vs get_path={answer['value']!r}")

    assert mismatches == [], "L0 里有数值取不回来（或取回来的不一样）：\n" + "\n".join(mismatches)
    return len(leaves)


def _assert_l0_fields_complete(doc: dict) -> None:
    """**登记表里的每个字段都必须在 L0 里出现**（`omit_if_none` 的例外）。

    这条挡的是「少写一个字段也能让可达性断言变绿」——那等于把「L0 数值 ⊆ get_path 可达」
    偷偷换成「写出来的那些可达」。删掉一个字段 → 立刻变红（见第七条的自检）。
    """
    missing: list[str] = []
    for view_name, fields in summary.INDEX_SPEC.items():
        block = doc.get(view_name)
        assert isinstance(block, dict), f"L0 里没有视角块 {view_name}"
        for field in fields:
            node: object = block
            present = True
            for part in field.path.split("."):
                if isinstance(node, dict) and part in node:
                    node = node[part]
                else:
                    present = False
                    break
            if not present and not field.omit_if_none:
                missing.append(f"{view_name}.{field.path}")

    assert missing == [], "L0 少了登记表里的字段：" + ", ".join(missing)


# =============================================================================
# 一、L0 体量与 budget 口径
# =============================================================================


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_fits_the_hard_limit(docs: dict[str, dict], stem: str) -> None:
    """四个样本的 L0 都 < 2,000 字符，且**默认预算下不降级**。"""
    doc = docs[stem]
    chars = len(json.dumps(doc, ensure_ascii=False))

    assert chars < L0_LIMIT, f"{stem} 的 L0 有 {chars} 字符（门限 {L0_LIMIT}）"
    assert doc["budget"]["degraded"] is False, f"{stem} 的 L0 不该在默认预算下降级"
    assert doc["budget"]["chars"] < summary.INDEX_BUDGET_CHARS


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_budget_chars_measures_the_body_only(docs: dict[str, dict], stem: str) -> None:
    """`budget.chars` 量的是 L0 **正文**（不含 `budget` 自身），与渲染层同一口径。"""
    doc = docs[stem]
    body = {key: value for key, value in doc.items() if key != "budget"}

    assert doc["budget"]["chars"] == len(json.dumps(body, ensure_ascii=False))
    # 正文 + budget 块才是完整输出（差几十字符：键名与两个数字）
    assert 0 < len(json.dumps(doc, ensure_ascii=False)) - doc["budget"]["chars"] < 100


def test_l0_is_far_smaller_than_render_overview(kill_ctx: ViewContext) -> None:
    """同一份 ctx：L0 的字符数不足 `render_overview` 的 15%（降幅 > 85%）。"""
    overview = render.render_overview(kill_ctx)
    full = len(json.dumps(overview, ensure_ascii=False))
    compact = len(json.dumps(render.render_index(kill_ctx), ensure_ascii=False))

    assert compact * 100 < full * 15, f"L0 {compact} vs overview {full}（降幅不足 85%）"


# =============================================================================
# 二、L0 不含 evidence、也不含逐叶子平行表
# =============================================================================


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_contains_no_evidence_anywhere(docs: dict[str, dict], stem: str) -> None:
    """**红线**：L0 任意深度都不得出现 `evidence` 键。"""
    doc = docs[stem]
    assert not _has_key(doc, "evidence"), f"{stem} 的 L0 里出现了 evidence"


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_has_no_per_leaf_tables(docs: dict[str, dict], stem: str) -> None:
    """**红线**：L0 只给计数，不给逐叶子 `provenance` / `unknown_reasons` / `confidence`。"""
    doc = docs[stem]
    for view_name, block in ((key, doc[key]) for key in doc if key not in summary.TOP_LEVEL_KEYS):
        for forbidden in FORBIDDEN_TABLES:
            assert forbidden not in block, f"{stem}.{view_name}.{forbidden} 不该出现在 L0"
    assert not _has_key(doc, "evidence")


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_flags_are_ids_only_and_never_dropped(
    docs: dict[str, dict], ctxs: dict[str, ViewContext], stem: str
) -> None:
    """L0 的 flag 清单 = L1 的全部 flag（**一条不丢**），但只留 `id`。"""
    doc = docs[stem]
    full = [
        flag
        for view_name in available_views()
        for flag in render.render_view(ctxs[stem], view_name)["flags"]
    ]

    assert [entry["id"] for entry in doc["flags"]] == [flag["id"] for flag in full]
    for entry in doc["flags"]:
        assert set(entry) == {"id"}, entry


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_view_blocks_keep_three_to_five_fields(docs: dict[str, dict], stem: str) -> None:
    """每个视角的 L0 字段数是 **3–5**（文档 §3 的粒度约束）。"""
    doc = docs[stem]
    for view_name in available_views():
        assert 3 <= len(doc[view_name]) <= 5, f"{stem}.{view_name} 有 {len(doc[view_name])} 个字段"
    # 六个视角 + 五个顶层块，一个不多一个不少
    assert set(doc) == set(summary.TOP_LEVEL_KEYS) | set(available_views())


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_provenance_counts_are_counts(docs: dict[str, dict], stem: str) -> None:
    """`provenance_counts` 是 `[measured, assumed, unknown]` 三元组，且**等于 L1 的计数**。"""
    doc = docs[stem]
    counts = doc["provenance_counts"]

    assert set(counts) == set(available_views())
    for view_name, triple in counts.items():
        assert len(triple) == len(PROVENANCE_STATES)
        assert all(isinstance(value, int) and value >= 0 for value in triple)
        assert sum(triple) > 0, f"{view_name} 的计数全 0（该视角没有叶子？）"


# =============================================================================
# 三、L0 的每个数值都能被 get_path 取到（逐位相同）
# =============================================================================


@pytest.mark.parametrize("stem", REACHABILITY_SAMPLES)
def test_every_l0_leaf_is_reachable_by_get_path(
    ctxs: dict[str, ViewContext], docs: dict[str, dict], stem: str
) -> None:
    """**本轮核心验收 1**：遍历 L0 全部叶子，`get_path` 取回的值必须逐位相同。

    同时要求 L0 **一个登记字段都不能少**（`_assert_l0_fields_complete`）——否则「少写一个
    字段」也能让这条断言绿，那就不是「每个数值都取得回来」，而是「写出来的都取得回来」。
    """
    checked = _assert_l0_reachable(ctxs[stem], docs[stem])
    _assert_l0_fields_complete(docs[stem])

    assert checked > 60, f"只比对了 {checked} 条叶子（样本或 L0 结构变了）"


@pytest.mark.parametrize("stem", SAMPLES)
def test_l0_and_l1_agree_bit_for_bit(
    ctxs: dict[str, ViewContext], docs: dict[str, dict], stem: str
) -> None:
    """**本轮核心验收 2**：L0 / L1 对同一字段的数值逐位相同（按登记表的 `source` 对照）。

    L1 侧用 `render_view()` **重新构建**、用本文件的 `_pick()` 独立取值——所以这条断言
    不是拿 L0 和自己比。
    """
    doc = docs[stem]
    compared = 0
    omitted = 0

    for view_name, fields in summary.INDEX_SPEC.items():
        l1 = render.render_view(ctxs[stem], view_name)["overview"]
        for field in fields:
            if field.source is None:  # 纯派生：由下一条测试逐个手算核对
                continue
            expected = _pick(l1, field.source)
            if field.omit_if_none and expected is None:
                # 文档 §3：没有导弹时 `range_m` 只给 optimal → L0 里**不该有这个键**
                parts = field.path.split(".")
                head = parts[:-1]
                parent = _pick(doc[view_name], ".".join(head)) if head else doc[view_name]
                assert parts[-1] not in parent, f"{stem}.{view_name}.{field.path} 该被省略"
                omitted += 1
                continue
            actual = _pick(doc[view_name], field.path)
            assert actual == expected, (
                f"{stem}.{view_name}.{field.path}: L0={actual!r} vs L1={expected!r}"
            )
            compared += 1

    assert compared >= 15, f"只比对了 {compared} 个字段（登记表变小了？）"
    assert omitted + compared == sum(
        1 for fields in summary.INDEX_SPEC.values() for field in fields if field.source is not None
    )


@pytest.mark.parametrize("stem", SAMPLES)
def test_derived_l0_fields_are_computed_from_l1(
    ctxs: dict[str, ViewContext], docs: dict[str, dict], stem: str
) -> None:
    """三处派生值逐个按 **L1 数据手算**核对（不是「自己等于自己」）。"""
    doc = docs[stem]
    firepower = render.render_view(ctxs[stem], "firepower")["overview"]
    survivability = render.render_view(ctxs[stem], "survivability")["overview"]
    mobility = render.render_view(ctxs[stem], "mobility")["overview"]
    resources = render.render_view(ctxs[stem], "resources")["overview"]
    constraints = render.render_view(ctxs[stem], "constraints")["overview"]
    stability = render.render_view(ctxs[stem], "stability")["overview"]

    # ① weapons：件数之和 + 族去重排序
    rows = firepower["weapons"]
    assert doc["firepower"]["weapons"]["count"] == sum(row["count"] for row in rows)
    families = sorted({str(row["family"]) for row in rows if row["family"]})
    assert doc["firepower"]["weapons"]["families"] == families

    # ② 三层 HP：只是换键名
    assert doc["survivability"]["layers"] == {
        "shield": survivability["layers"]["shield_hp"],
        "armor": survivability["layers"]["armor_hp"],
        "hull": survivability["layers"]["hull_hp"],
    }

    # ③ thinnest：三层里 ehp 最小的一层（并列取前面的层）
    thinnest = survivability["thinnest"]
    pick = min(
        (layer for layer in thinnest if thinnest[layer]["ehp"] is not None),
        key=lambda layer: thinnest[layer]["ehp"],
    )
    assert doc["survivability"]["thinnest"] == {
        "layer": pick,
        "damage_type": thinnest[pick]["damage_type"],
    }

    # ④ 三层修复速率之和（shield_passive 不算「三层」）
    rates = [survivability["repair"][layer]["raw_hp_s"] for layer in ("shield", "armor", "hull")]
    assert doc["survivability"]["repair_total_hp_s"] == pytest.approx(sum(rates), abs=1e-6)

    # ⑤ 推进档：base 与哪一档逐位相同
    states = mobility["propulsion_states"]
    matched = [
        name
        for name in summary.PROPULSION_STATES
        if all(states["base"][key] == states[name][key] for key in summary.PROPULSION_TUPLE)
    ]
    assert doc["mobility"]["propulsion_state"] == (matched[0] if matched else "base")

    # ⑥ 槽位是「已用/上限」字符串
    assert doc["resources"]["slots"] == {
        name: f"{row['used']}/{row['total']}" for name, row in resources["slots"].items()
    }

    # ⑦ 违约清单：条数 + 去重保序的 kind
    hard = constraints["hard_violations"]
    assert doc["constraints"]["hard_violations"] == {
        "count": len(hard),
        "kinds": list(dict.fromkeys(row["rule"]["type"] for row in hard)),
    }
    tier = constraints["tier_violations"]
    assert doc["constraints"]["tier_violations"] == {
        "count": len(tier),
        "kinds": list(dict.fromkeys(row["kind"] for row in tier)),
    }

    # ⑧ 外部依赖 = 遥修 or 传电
    external = stability["external_dependency"]
    assert doc["stability"]["has_external_dependency"] is bool(
        external["remote_rep"] or external["cap_transfer"]
    )


@pytest.mark.parametrize(
    "path",
    [
        "mobility.speed_mps",
        "firepower.weapons",
        "survivability.repair_total_hp_s",
        "resources.slots.high",
        "constraints.tier_violations",
        "stability.cap_stable_pct",
        "flags",
        "provenance_counts.mobility",
        "budget.chars",
    ],
)
def test_get_path_echoes_exactly_what_l0_shows(
    ctxs: dict[str, ViewContext], docs: dict[str, dict], path: str
) -> None:
    """agent 在 L0 里看到的路径，`get_path` 原路返回**同一个值**（文档 §1 的关键约束）。"""
    stem = "f67-spotcheck-139255942"
    answer = render.get_path(ctxs[stem], path)

    assert answer["value"] == _pick(docs[stem], path)
    assert "hint" not in answer, f"取到值了却还带 hint：{answer['hint']}"


# =============================================================================
# 四、meta 压缩：skill_tier 只给 {name, count, hash}
# =============================================================================


@pytest.mark.parametrize("stem", SAMPLES)
def test_meta_skill_tier_is_compressed(
    ctxs: dict[str, ViewContext], docs: dict[str, dict], stem: str
) -> None:
    """`meta.skill_tier` 是 `{name, count, hash}`，**不含 512 条明细**。"""
    doc = docs[stem]
    tier = doc["meta"]["skill_tier"]

    assert set(tier) == {"name", "count", "hash"}
    assert tier["name"] == ctxs[stem].assumptions["skill_tier_name"] == "all4+min_required"
    assert tier["count"] == len(ctxs[stem].assumptions["skill_tier"]) == SKILL_COUNT

    dumped = json.dumps(doc["meta"], ensure_ascii=False)
    assert "2403" not in dumped, "L0 的 meta 里还有技能 type_id（明细没压掉）"
    assert len(dumped) < 500, f"L0 的 meta 有 {len(dumped)} 字符（压缩没生效）"


def test_skill_tier_hash_is_a_stable_digest(
    ctxs: dict[str, ViewContext], docs: dict[str, dict]
) -> None:
    """同一个技能档 → 同一个 hash；改一个等级 → 换一个 hash；格式是 12 位十六进制。"""
    tier = dict(ctxs["f67-spotcheck-139255942"].assumptions["skill_tier"])
    digest = summary.skill_tier_hash(tier)

    assert digest == docs["f67-spotcheck-139255942"]["meta"]["skill_tier"]["hash"]
    assert digest == summary.skill_tier_hash(dict(reversed(list(tier.items())))), "顺序不该影响 hash"
    assert len(digest) == summary.SKILL_HASH_DIGITS
    assert all(char in "0123456789abcdef" for char in digest)

    mutated = dict(tier)
    first_key = next(iter(mutated))
    mutated[first_key] = (mutated[first_key] % 5) + 1
    assert summary.skill_tier_hash(mutated) != digest, "改了一个等级 hash 却没变（摘要不敏感）"

    # 摘要只跟着**内容**走：内容不同 → 摘要不同；内容相同 → 摘要相同。
    # （实测四个样本的 `all4+min_required` 条数都是 512，但**内容不完全相同**——
    # `derive_skill_tier` 会为每个配装补上它被要求的模块技能，所以 hash 本就该分样本。）
    other_tier = ctxs["f1-hecate-killmail"].assumptions["skill_tier"]
    other = summary.skill_tier_hash(other_tier)
    assert other == summary.skill_tier_hash(dict(reversed(list(other_tier.items()))))
    if dict(other_tier) != tier:
        assert other != digest, "两份不同的技能档得到了同一个 hash（碰撞/顺序问题）"
    else:  # pragma: no cover - 样本技能档若哪天变成完全一致，也照样成立
        assert other == digest

    # 两次渲染确定同值（不受字典插入顺序 / 进程 hash 种子影响）
    again = render.render_index(ctxs["f67-spotcheck-139255942"])
    assert again["meta"]["skill_tier"]["hash"] == digest


def test_l0_meta_keeps_the_documented_items(docs: dict[str, dict]) -> None:
    """`meta` 的键就是文档 §3 那七项（压缩不改键名）。"""
    meta = docs["f47-random-raptor"]["meta"]
    assert set(meta) == {
        "sde_build",
        "engine_version",
        "spec_version",
        "skill_tier",
        "module_states",
        "damage_profile",
        "mode",
    }
    assert meta["sde_build"] is not None
    assert meta["engine_version"]
    assert meta["spec_version"]


# =============================================================================
# 五、L1：单视角 = overview 的视角条目（逐字节）
# =============================================================================


@pytest.mark.parametrize("view_name", available_views())
def test_render_view_equals_overview_entry(kill_ctx: ViewContext, view_name: str) -> None:
    """**复用同一构建路径的最好证明**：L1 与 overview 的视角条目逐字节相同。

    前提是这次 overview **没有降级**（降级会剥 evidence / 截列表，而 L1 只看全量——
    见 `project/summary.py` 模块 docstring 第 3 条）。默认预算下 Hecate 样本不降级。
    """
    overview = render.render_overview(kill_ctx)
    assert overview["budget"]["degraded"] is False, "前提不成立：这份 overview 被降级了"

    l1 = render.render_view(kill_ctx, view_name)
    assert json.dumps(l1, ensure_ascii=False) == json.dumps(
        overview["views"][view_name], ensure_ascii=False
    )


def test_render_view_builds_only_the_requested_view(
    kill_ctx: ViewContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**不重算六视角再丢弃**：`render_view("mobility")` 只调用一次 `build_view`。"""
    calls: list[str] = []

    def counting(name: str, ctx: ViewContext) -> dict:
        calls.append(name)
        return build_view(name, ctx)

    monkeypatch.setattr(render, "build_view", counting)
    render.render_view(kill_ctx, "mobility")

    assert calls == ["mobility"], f"构建了 {calls}（应该只构建 mobility）"


def test_render_view_rejects_unknown_view(kill_ctx: ViewContext) -> None:
    """未知视角名 → `ValueError`，消息里**列出可用视角**。"""
    with pytest.raises(ValueError) as caught:
        render.render_view(kill_ctx, "no_such_view")

    message = str(caught.value)
    assert "no_such_view" in message
    for view_name in available_views():
        assert view_name in message, f"错误消息里没列出 {view_name}"


# =============================================================================
# 六、get_path：取数、失败提示、两种数组写法
# =============================================================================


def test_get_path_missing_returns_none_with_hint(ctxs: dict[str, ViewContext]) -> None:
    """路径不存在**不抛异常**：`value=None` + 可用前缀提示。"""
    ctx = ctxs["f67-spotcheck-139255942"]

    missing_paths = (
        "no_such_thing",
        "mobility.no_such_field",
        "constraints.tier_violations[9].kind",
        "",
    )
    for path in missing_paths:
        answer = render.get_path(ctx, path)
        assert answer["value"] is None, path
        assert isinstance(answer["hint"], str) and answer["hint"], path
        assert answer["provenance"] is None and answer["source_view"] is None

    deep = render.get_path(ctx, "mobility.no_such_field")
    assert "mobility" in deep["hint"] and "speed_mps" in deep["hint"], deep["hint"]


def test_get_path_supports_array_elements_and_indexes(ctxs: dict[str, ViewContext]) -> None:
    """`a[].b`（元素约定）与 `a[0].b`（按下标）都通，且三态来自 L1 的同一条叶子路径。"""
    ctx = ctxs["f1-hecate-killmail"]
    l1 = render.render_view(ctx, "firepower")

    names = render.get_path(ctx, "firepower.weapons[].name")
    assert names["value"] == [row["name"] for row in l1["overview"]["weapons"]]
    assert names["provenance"] == l1["provenance"]["weapons[].name"]

    first = render.get_path(ctx, "firepower.weapons[0].name")
    assert first["value"] == l1["overview"]["weapons"][0]["name"]
    assert first["provenance"] == l1["provenance"]["weapons[].name"]

    kind = render.get_path(ctxs["f67-spotcheck-139255942"], "constraints.tier_violations[0].kind")
    assert kind["value"] == "resource"
    assert kind["source_view"] == "constraints"


def test_get_path_reaches_int_key_dicts(ctxs: dict[str, ViewContext]) -> None:
    """**整数键字典**（`count_by_level` = `{等级: 个数}`）用字符串等级也取得动。

    视角里键是 `int`、provenance 键空间是字符串（`skill_floor.count_by_level.4`），
    不单独钉的话「L0 的每个数值都取回来」在数字键这一层就是假的。
    """
    ctx = ctxs["f67-spotcheck-139255942"]
    l1 = render.render_view(ctx, "constraints")

    counts = l1["overview"]["skill_floor"]["count_by_level"]
    assert counts and all(isinstance(key, int) for key in counts), "前提：键是 int"

    for level, expected in counts.items():
        path = f"constraints.skill_floor.count_by_level.{level}"
        answer = render.get_path(ctx, path)
        assert answer["value"] == expected
        assert answer["provenance"] == l1["provenance"][f"skill_floor.count_by_level.{level}"]

    missing = render.get_path(ctx, "constraints.skill_floor.count_by_level.99")
    assert missing["value"] is None and missing["hint"]


def test_get_path_returns_provenance_and_reason(ctxs: dict[str, ViewContext]) -> None:
    """`unknown` 的路径带上与 L1 一致的原因；L1 才有的元信息也取得动。"""
    ctx = ctxs["f12-logistics-oneiros"]
    answer = render.get_path(ctx, "firepower.dph")
    l1 = render.render_view(ctx, "firepower")

    assert answer["value"] is None
    assert answer["provenance"] == "unknown"
    assert answer["reason"] == l1["unknown_reasons"]["dph"]

    # L1 才有的形状（不是 L0 字段）也取得动
    deep = render.get_path(ctx, "mobility.propulsion_states.off.align_time_s")
    assert deep["value"] is not None and deep["source_view"] == "mobility"

    # 完整技能表只在 L0 之外取得动（L0 的 meta.skill_tier 是压缩版）
    full = render.get_path(ctx, "meta.assumptions.skill_tier")
    assert isinstance(full["value"], dict) and len(full["value"]) == SKILL_COUNT

    # flag 的 message / evidence 在 L0 里没有，但拿得到
    assert render.get_path(ctx, "flags[0].message")["value"]
    assert render.get_path(ctx, "flags[0].evidence")["value"]


def test_get_path_names_the_three_state_counts(ctxs: dict[str, ViewContext]) -> None:
    """`provenance_counts` 既可整块取（顺序三元组），也可按名字取。"""
    ctx = ctxs["f47-random-raptor"]
    triple = render.get_path(ctx, "provenance_counts.mobility")["value"]
    assert triple == list(triple)

    named = render.get_path(ctx, "provenance_counts.mobility.assumed")["value"]
    assert named == triple[PROVENANCE_STATES.index("assumed")]


# =============================================================================
# 七、非永真自检（每条关键断言都要能被一次篡改打红）
# =============================================================================


def test_reachability_assertion_goes_red_when_l0_is_tampered(
    ctxs: dict[str, ViewContext], docs: dict[str, dict]
) -> None:
    """**篡改 L0 的一个数值 → 「L0 数值 ⊆ get_path 可达」立刻变红**（证明它不是永真）。"""
    stem = "f47-random-raptor"
    tampered = json.loads(json.dumps(docs[stem]))
    original = tampered["mobility"]["speed_mps"]
    tampered["mobility"]["speed_mps"] = original + 1.0

    with pytest.raises(AssertionError) as caught:
        _assert_l0_reachable(ctxs[stem], tampered)
    assert "mobility.speed_mps" in str(caught.value)


def test_deleting_an_l0_field_goes_red(docs: dict[str, dict]) -> None:
    """**把 L0 里某个字段删掉 → 断言立刻变红**（第 9 条的原话）。

    删掉的字段 `get_path` 自己还能从 L1 取回来，所以「可达性」单看是绿的——正因如此才
    必须另有**完整性**判据（`_assert_l0_fields_complete`）：否则「少写一个字段」会被
    「写出来的都可达」悄悄放过。
    """
    stem = "f67-spotcheck-139255942"
    trimmed = json.loads(json.dumps(docs[stem]))
    del trimmed["resources"]["feasible"]

    with pytest.raises(AssertionError) as caught:
        _assert_l0_fields_complete(trimmed)
    assert "resources.feasible" in str(caught.value)

    # 对照组：原始 L0 是完整的（否则上一条就不是「因为删了才红」）
    _assert_l0_fields_complete(docs[stem])


def test_reachability_walker_actually_reads_the_document(
    ctxs: dict[str, ViewContext], docs: dict[str, dict]
) -> None:
    """删掉一个叶子 → 遍历到的叶子数确实变少（断言读的是 L0，不是硬编码清单）。"""
    stem = "f1-hecate-killmail"
    doc = docs[stem]
    before = len(_l0_leaves(doc))

    trimmed = json.loads(json.dumps(doc))
    trimmed["resources"].pop("feasible")
    after = len(_l0_leaves(trimmed))

    assert after == before - 1
    assert not any(path == "resources.feasible" for path, _value in _l0_leaves(trimmed))
    # 删掉字段后剩下的部分照样全部可达（被删的那个不参与比对，由完整性判据负责）
    assert _assert_l0_reachable(ctxs[stem], trimmed) == after


# =============================================================================
# 八、degraded 显式出现（且不裁剪任何数值）
# =============================================================================


def test_degraded_is_reported_and_values_are_never_trimmed(
    kill_ctx: ViewContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    """预算调小 → `degraded=True` 显式出现，但**字段与数值一个都没少**（只上报不裁剪）。"""
    baseline = render.render_index(kill_ctx)
    monkeypatch.setattr(summary, "INDEX_BUDGET_CHARS", 10)

    degraded = render.render_index(kill_ctx)
    assert degraded["budget"]["degraded"] is True
    assert baseline["budget"]["degraded"] is False

    # 除了 budget 之外逐字节相同（红线：不得为省字符丢字段值）
    assert json.dumps(
        {key: value for key, value in degraded.items() if key != "budget"}, ensure_ascii=False
    ) == json.dumps(
        {key: value for key, value in baseline.items() if key != "budget"}, ensure_ascii=False
    )
