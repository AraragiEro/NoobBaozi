r"""T-38 一次性探针：`budget.chars` 的字符构成分解 + 三种压缩方案的实际可省字符。

**只读探针**，不改任何产品代码；跑法（仓库根目录）：

```powershell
$env:UV_CACHE_DIR = "$PWD\noobbaozi\spike\uvcache"
uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python noobbaozi/spike/budget_breakdown.py
```

它回答三个问题：

1. `budget.chars` 由谁贡献（六视角 × 五块 + `modes` + 骨架），每个视角里最占字符的字段是哪几个；
2. 三个压缩方案 **A**（`unknown_reasons` 按 reason 分组）/ **B**（`provenance` 按三态分组）/
   **C**（`meta.assumptions.skill_tier` 摘要化）在**真实样本**上各能省多少字符；
3. 若要再加一个 `propulsion_states.single`（+250~350 chars）而**不降级**，最少要省多少。

口径与 `render._measure()` 逐字一致：`budget.chars = len(json.dumps(views)) + len(json.dumps(modes))`
（顶层 `meta` / `flags` / `budget` **不计入**）。所有「可省字符」都是用真实样本
**重新序列化再相减**得到的实测值，不是估算。
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

NOOBBAOZI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = NOOBBAOZI_ROOT.parent
for _path in (str(REPO_ROOT), str(NOOBBAOZI_ROOT)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import esf_dogma_engine as dogma  # noqa: E402

from noobbaozi.config.paths import db_path  # noqa: E402
from noobbaozi.engine.adapter import Engine  # noqa: E402
from noobbaozi.engine.assumptions import default_assumptions, derive_skill_tier  # noqa: E402
from noobbaozi.project import render as R  # noqa: E402
from noobbaozi.project.views.base import ViewContext  # noqa: E402
from noobbaozi.sde.index import Index  # noqa: E402

# 三个最大的样本（任务书指定）：f47 / f67 是两个贴近预算的，f12 是最大的非战斗船样本。
SAMPLES = (
    "f47-random-raptor.json",
    "f67-spotcheck-139255942.json",
    "f12-logistics-oneiros.json",
)
FIXTURES = NOOBBAOZI_ROOT / "acceptance" / "fixtures"

NEXT_FIELD_COST = (250, 350)
"""下一次要加的 `propulsion_states.single` 的预计开销（任务书给的区间）。

下面会用 `propulsion_states` 里**已有条目**的实测体积交叉验证这个区间。
"""


def dumps(value: Any) -> str:
    """与 `render._measure()` 同一套序列化参数（`ensure_ascii=False`）。"""
    return json.dumps(value, ensure_ascii=False)


def load_ctx(engine: Engine, index: Index, name: str) -> ViewContext:
    """与 `tests/test_views_b.py::_load_real_fit` 同一条构造路径（验收命令用的也是它）。"""
    path = FIXTURES / name
    if path.suffix == ".eft":
        fit = engine.load_eft(path.read_text(encoding="utf-8"))
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, list):
            raw = raw[0]
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


# =============================================================================
# 一、字符构成分解
# =============================================================================


def leaf_sizes(node: object, prefix: str = "", out: dict[str, int] | None = None) -> dict[str, int]:
    """叶子点路径 → **该路径所有出现**的 `"path": value` 序列化字符数合计。

    寻址规则与 `render._leaf_values()` 一致（对象逐键、对象列表用 `[]`、
    其余算叶子），这样「最占字符的字段」与 provenance 的键空间是同一套路径。
    """
    if out is None:
        out = {}
    if isinstance(node, dict) and node:
        for key, value in node.items():
            leaf_sizes(value, f"{prefix}.{key}" if prefix else str(key), out)
        return out
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        for item in node:
            leaf_sizes(item, f"{prefix}[]", out)
        return out
    cost = len(dumps(prefix)) + len(dumps(node)) + 2  # `"path": value,`
    out[prefix] = out.get(prefix, 0) + cost
    return out


def view_breakdown(entry: dict) -> dict[str, int]:
    """一个视角的字符构成：五块（overview 再摊到顶层字段）+ 表本身的键名开销。"""
    sections: dict[str, int] = {}
    overview = entry.get("overview") or {}
    for key, value in overview.items():
        sections[f"overview.{key}"] = len(dumps(key)) + len(dumps(value)) + 2
    for table in ("provenance", "unknown_reasons", "confidence", "flags"):
        sections[table] = len(dumps(table)) + len(dumps(entry.get(table))) + 2
    return sections


def measure(views: dict, modes: list) -> int:
    """与 `render._measure()` 完全相同。"""
    return len(dumps(views)) + (len(dumps(modes)) if modes else 0)


def skeleton_cost(views: dict) -> int:
    """`views` 的 JSON 骨架：`{}`、键名、逗号、冒号（= 总长 − 各视角条目内容之和）。"""
    total = len(dumps(views))
    inner = sum(len(dumps(name)) + len(dumps(entry)) + 2 for name, entry in views.items())
    return total - inner


# =============================================================================
# 二、三个压缩方案（都是「重新序列化后相减」的实测）
# =============================================================================


def group_by_value(table: dict[str, str]) -> dict[str, list[str]]:
    """`{path: 值}` → `{值: [path, ...]}`（A 与 B 共用的「按值分组」形状）。"""
    groups: dict[str, list[str]] = {}
    for path, value in table.items():
        groups.setdefault(value, []).append(path)
    return groups


def scheme_a(views: dict) -> tuple[dict, dict[str, int]]:
    """A：`unknown_reasons` 从 `{path: reason}` 改成 `{reason: [path...]}`。"""
    new_views = json.loads(dumps(views))
    per_view: dict[str, int] = {}
    for name, entry in new_views.items():
        before = len(dumps(entry["unknown_reasons"]))
        entry["unknown_reasons"] = group_by_value(entry["unknown_reasons"])
        per_view[name] = before - len(dumps(entry["unknown_reasons"]))
    return new_views, per_view


def scheme_b(views: dict) -> tuple[dict, dict[str, int]]:
    """B：`provenance` 从逐叶子平行表改成 `{三态: [path...]}`。"""
    new_views = json.loads(dumps(views))
    per_view: dict[str, int] = {}
    for name, entry in new_views.items():
        before = len(dumps(entry["provenance"]))
        entry["provenance"] = group_by_value(entry["provenance"])
        per_view[name] = before - len(dumps(entry["provenance"]))
    return new_views, per_view


def scheme_c(ctx: ViewContext) -> dict[str, int]:
    """C：`meta.assumptions.skill_tier`（512 条 `{技能ID: 等级}`）改成 `{name, count, hash}`。

    实际情况（实测）：`assumptions` 里 `skill_tier` 是**扁平**的 `{2403: 4, ...}` 512 条、
    6,007 chars，档位名在旁边的 `skill_tier_name`（`"all4+min_required"`）。

    注意：`meta` **不在** `budget.chars` 里（预算只量 `views` + `modes`），所以这里的
    「预算可省」恒为 0 —— 省的是**整份输出**的体积。探针把两者都算出来，免得误判。
    """
    assumptions = ctx.assumptions
    tier = assumptions.get("skill_tier")
    name = assumptions.get("skill_tier_name")
    count = len(tier) if isinstance(tier, (dict, list)) else 0
    digest = hashlib.sha256(dumps(tier).encode("utf-8")).hexdigest()[:16]

    before_assumptions = len(dumps(assumptions))
    summarized = json.loads(dumps(assumptions))
    summarized["skill_tier"] = {"name": name, "count": count, "hash": digest}
    after_assumptions = len(dumps(summarized))
    return {
        "assumptions_before": before_assumptions,
        "assumptions_after": after_assumptions,
        "meta_savings": before_assumptions - after_assumptions,
        "total_payload_savings": before_assumptions - after_assumptions,
        "budget_savings": 0,  # meta 不进 budget.chars —— 硬结论
        "skill_tier_count": count,
        "skill_tier_chars": len(dumps(tier)),
    }


# =============================================================================
# 三、`propulsion_states.single` 的真实体积参照
# =============================================================================


def propulsion_states_cost(entry: dict) -> dict[str, Any]:
    """用 `propulsion_states` 里**已有条目**实测「再加一个状态」要花多少字符。

    `propulsion_states.<state>` 在 overview 里是一小块 dict，同时在 `provenance` 里
    留下对应叶子 —— 两边一起算，才是新增字段的**真实**开销。
    """
    overview = entry.get("overview") or {}
    states = overview.get("propulsion_states")
    if not isinstance(states, dict):
        return {}
    sizes: dict[str, int] = {}
    provenance = entry.get("provenance") or {}
    for name, value in states.items():
        overview_cost = len(dumps(name)) + len(dumps(value)) + 2
        prefix = f"propulsion_states.{name}"
        prov_cost = sum(
            len(dumps(path)) + len(dumps(state)) + 2
            for path, state in provenance.items()
            if path == prefix or path.startswith(f"{prefix}.")
        )
        sizes[name] = overview_cost + prov_cost
    return {"states": sizes, "keys": sorted(states)}


# =============================================================================
# 四、报告
# =============================================================================


def report(sample: str, ctx: ViewContext, output: dict, raw: dict) -> dict[str, Any]:
    """`output` = 默认预算下的渲染结果；`raw` = 把 `BUDGET_CHARS` 临时放开后的未降级载荷。

    构成分解与三个方案都按**未降级载荷**算：否则「可省多少」会被已经砍掉的字段污染
    （降级① 已经去掉了 flags 的 evidence，降级② 已经把三个列表截到 5 项）。
    """
    views = raw["views"]
    modes = raw["modes"]
    declared = output["budget"]
    chars = declared["chars"]
    mode_chars = len(dumps(modes)) if modes else 0
    raw_chars = measure(views, modes)
    raw_mode_chars = mode_chars

    print(f"\n{'=' * 78}")
    print(f"样本 {sample}")
    print(f"{'=' * 78}")
    print(f"budget.chars = {chars:,} / {R.BUDGET_CHARS:,}   余量 {R.BUDGET_CHARS - chars:,}"
          f"   degraded={declared['degraded']}")
    print(f"  未降级载荷（放开预算后实测）= {raw_chars:,}   "
          f"超预算 {raw_chars - R.BUDGET_CHARS:,}")
    print(f"  views 子树 = {raw_chars - raw_mode_chars:,}   modes = {raw_mode_chars:,}")
    if declared["degraded"]:
        print(f"  ⚠ 默认预算下已降级：省下 {raw_chars - chars:,} chars"
              f"（① flags 去 evidence ② 截断三个明细列表 ③ 清空 modes）")

    meta_chars = len(dumps({"meta": output["meta"]}))
    assumptions = output["meta"]["assumptions"]
    print(f"  （不计入预算）meta = {meta_chars:,}   assumptions = {len(dumps(assumptions)):,}   "
          f"skill_tier = {len(dumps(assumptions.get('skill_tier'))):,} chars / "
          f"{len(assumptions.get('skill_tier') or {})} 条")
    leaked = "skill_tier" in dumps(views)
    print(f"  views 里是否泄漏 assumptions/skill_tier：{leaked}（False = meta 与预算完全隔离）")

    # --- 六视角构成 ---
    print(f"\n-- 六视角构成（views 子树 {raw_chars - raw_mode_chars:,} = "
          f"骨架+键名 {skeleton_cost(views) + sum(len(dumps(n)) for n in views):,} + 六视角条目）--")
    header = f"{'视角':<14}{'合计':>9}{'overview':>10}{'prov':>8}{'reasons':>9}{'conf':>7}{'flags':>7}"
    print(header)
    print("-" * len(header) * 2)
    total_entries = 0
    per_view_detail: dict[str, dict] = {}
    for name, entry in views.items():
        entry_chars = len(dumps(entry))
        total_entries += entry_chars
        blocks = {
            "overview": len(dumps(entry["overview"])),
            "provenance": len(dumps(entry["provenance"])),
            "unknown_reasons": len(dumps(entry["unknown_reasons"])),
            "confidence": len(dumps(entry["confidence"])),
            "flags": len(dumps(entry["flags"])),
        }
        per_view_detail[name] = {"chars": entry_chars, "blocks": blocks}
        print(
            f"{name:<14}{entry_chars:>9,}{blocks['overview']:>10,}"
            f"{blocks['provenance']:>8,}{blocks['unknown_reasons']:>9,}"
            f"{blocks['confidence']:>7,}{blocks['flags']:>7,}"
        )
    print(f"{'（标点骨架）':<14}{skeleton_cost(views):>9,}")
    print(f"{'modes':<14}{mode_chars:>9,}")

    # --- 每视角 top10 字段 ---
    for name, entry in views.items():
        sections = view_breakdown(entry)
        top_sections = sorted(sections.items(), key=lambda pair: -pair[1])[:10]
        leaves = leaf_sizes(entry["overview"])
        top_leaves = sorted(leaves.items(), key=lambda pair: -pair[1])[:10]
        print(f"\n-- {name}: 顶层块 top10 --")
        for path, cost in top_sections:
            print(f"   {cost:>8,}  {path}")
        print(f"-- {name}: overview 叶子 top10 --")
        for path, cost in top_leaves:
            print(f"   {cost:>8,}  {path}")

    # --- 方案实测 ---
    base_views_chars = len(dumps(views))
    a_views, a_per_view = scheme_a(views)
    b_views, b_per_view = scheme_b(views)
    a_saved = base_views_chars - len(dumps(a_views))
    b_saved = base_views_chars - len(dumps(b_views))
    ab_views = json.loads(dumps(a_views))
    for entry in ab_views.values():
        entry["provenance"] = group_by_value(entry["provenance"])
    ab_saved = base_views_chars - len(dumps(ab_views))
    c = scheme_c(ctx)

    print(f"\n-- 三种方案实测（同一份样本，views 子树 {base_views_chars:,}）--")
    print(f"A unknown_reasons 按 reason 分组   ：省 {a_saved:>7,}  " +
          "  ".join(f"{k}:{v:,}" for k, v in sorted(a_per_view.items())))
    print(f"B provenance 按三态分组           ：省 {b_saved:>7,}  " +
          "  ".join(f"{k}:{v:,}" for k, v in sorted(b_per_view.items())))
    print(f"C skill_tier 摘要化（{c['skill_tier_count']} 条技能）："
          f"整份输出省 {c['meta_savings']:,}（assumptions {c['assumptions_before']:,} → "
          f"{c['assumptions_after']:,}）；**预算可省 {c['budget_savings']:,}**（meta 不进 budget.chars）")
    print(f"A+B 组合                          ：省 {ab_saved:>7,}")

    prop = propulsion_states_cost(views.get("mobility") or {})
    if prop:
        print(f"\n-- propulsion_states 现有条目的实测体积（新增 single 的参照）--")
        for state, cost in sorted(prop["states"].items()):
            print(f"   {cost:>6,}  propulsion_states.{state}（overview + provenance 叶子）")
    print(f"\n-- 加 propulsion_states.single 的缺口（预计 +{NEXT_FIELD_COST[0]}~{NEXT_FIELD_COST[1]}）--")
    for cost in NEXT_FIELD_COST:
        need = raw_chars + cost - R.BUDGET_CHARS
        print(f"   +{cost}: 需省 {max(0, need):,}"
              f"{'（当前余量够）' if need <= 0 else ''}")

    return {
        "sample": sample,
        "chars": chars,
        "degraded": declared["degraded"],
        "raw_chars": raw_chars,
        "raw_slack": R.BUDGET_CHARS - raw_chars,
        "views_chars": raw_chars - raw_mode_chars,
        "modes": raw_mode_chars,
        "meta": meta_chars,
        "per_view": per_view_detail,
        "a_saved": a_saved,
        "b_saved": b_saved,
        "ab_saved": ab_saved,
        "c": c,
        "propulsion_states": prop,
        "unknown_reasons_total": sum(
            len(dumps(entry["unknown_reasons"])) for entry in views.values()
        ),
        "provenance_total": sum(len(dumps(entry["provenance"])) for entry in views.values()),
        "leaves_total": sum(len(entry["provenance"]) for entry in views.values()),
    }


def main() -> int:
    engine = Engine()
    opened = Index.open(db_path())
    saved_limit = R.BUDGET_CHARS
    try:
        results = []
        for sample in SAMPLES:
            ctx = load_ctx(engine, opened, sample)
            output = R.render_overview(ctx)
            # 放开预算再渲染一次：拿到**未降级**载荷（构成与可省字符都以它为准）
            R.BUDGET_CHARS = 10**9
            try:
                raw = R.render_overview(ctx)
            finally:
                R.BUDGET_CHARS = saved_limit
            results.append(report(sample, ctx, output, raw))
    finally:
        opened.close()

    print(f"\n{'=' * 78}")
    print("汇总结论")
    print(f"{'=' * 78}")
    print(f"{'样本':<30}{'未降级':>9}{'余量':>8}{'A可省':>9}{'B可省':>9}{'A+B':>9}")
    for row in results:
        print(f"{row['sample']:<30}{row['raw_chars']:>9,}{row['raw_slack']:>8,}"
              f"{row['a_saved']:>9,}{row['b_saved']:>9,}{row['ab_saved']:>9,}")
    worst = min(results, key=lambda row: row["raw_slack"])
    print(f"\n最紧样本 = {worst['sample']}（未降级 {worst['raw_chars']:,}，余量 {worst['raw_slack']:,}）")
    for cost in NEXT_FIELD_COST:
        need = max(0, cost - worst["raw_slack"])
        ok = "够" if worst["ab_saved"] >= need else "不够"
        print(f"  +{cost} chars ⇒ 最少要省 {need:,}；"
              f"A {worst['a_saved']:,} + B {worst['b_saved']:,} = {worst['ab_saved']:,} ⇒ {ok}")
        print(f"       只用 B：{worst['b_saved']:,} ⇒ "
              f"{'够' if worst['b_saved'] >= need else '不够'}"
              f"；只用 A：{worst['a_saved']:,} ⇒ "
              f"{'够' if worst['a_saved'] >= need else '不够'}")
    print(f"\nC 结论：skill_tier 摘要在**整份输出**里省 {results[0]['c']['meta_savings']:,} chars，"
          f"但 `budget.chars` 只量 views + modes，**预算可省 0** —— 不能拿来换字段。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
