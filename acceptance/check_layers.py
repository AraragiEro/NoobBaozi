"""L0/L1/get_path 的独立验收（不 import 实现的 INDEX_SPEC，按文档自己列期望清单）。

五组检查：
  ① 既有基线回归（由 run_acceptance.py 承担）
  ② L0 契约：体量 / 字段清单 / 红线 / degraded / 路径可达（逐位相同）
  ③ L1 一致性：与 render_overview 逐字节相同（降级样本例外）/ 四表齐全 / flags 带 evidence / 未知视角报错
  ④ get_path 行为：缺失路径 / int 键回退 / 返回结构
  ⑤ 可用性数据采集（供人工裁决，不判 pass/fail）

设计原则（Lead 裁决）：
- L0 **字段形状必须恒定**（对象永远对象、数组永远数组），可变的是值；
  因此 `damage_split` 永远是**四键 dict**，无武器时四值全 `null`，**不允许整块 null**。
- 验收脚本**自己不许崩**：任何一处意外形状都记「口径错误」并继续，保证整场有结论。

用法：
    uv run --python 3.13 --no-project python evefit/acceptance/check_layers.py
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(HERE))

VIEWS = ("firepower", "survivability", "mobility", "resources", "constraints", "stability")

#: **契约额度 2,500**（Lead 裁决：文档与实现都用 2,500）。
#: T-40 会加 `flags.hint`（+~50/样本）与 `provenance_counts` 带标签对象（+~200），
#: max 预计从 1,987 涨到 ≈2,140 —— 2,000 会被打爆，所以两边都定 2,500。
INDEX_BUDGET_CONTRACT = 2500
DOC_INDEX_BUDGET = INDEX_BUDGET_CONTRACT

#: 按 `docs/输出分层与工具集-v1.md` §3 **手工**抄下来的期望清单（不 import INDEX_SPEC）。
L0_CHECKLIST: dict[str, dict] = {
    "firepower": {
        "weapons": {"type": dict, "keys": ("count", "families")},
        "dps_base": {"type": (int, float, type(None))},
        "dph": {"type": (int, float, type(None))},
        "range_m": {"type": dict, "keys_any": ("optimal",), "keys_allowed": ("optimal", "max")},
        # 形状契约：四键 dict 永远存在，值可为 null；不许整块压成 null。
        "damage_split": {"type": dict, "keys": ("em", "explosive", "kinetic", "thermal"),
                         "value_may_be_null": True},
    },
    "survivability": {
        "ehp_mixed": {"type": (int, float, type(None))},
        "layers": {"type": dict, "keys": ("shield", "armor", "hull")},
        "thinnest": {"type": dict, "keys": ("layer", "damage_type")},
        "repair_total_hp_s": {"type": (int, float, type(None))},
    },
    "mobility": {
        "speed_mps": {"type": (int, float, type(None))},
        "align_time_s": {"type": (int, float, type(None))},
        "signature_radius_m": {"type": (int, float, type(None))},
        "propulsion_state": {"type": (str, type(None))},
    },
    "resources": {
        "cpu": {"type": dict, "keys": ("load", "output", "free")},
        "powergrid": {"type": dict, "keys": ("load", "output", "free")},
        "feasible": {"type": bool},
        "slots": {"type": dict, "keys": ("high", "medium", "low", "rig")},
    },
    "constraints": {
        "hard_violations": {"type": dict, "keys": ("count", "kinds")},
        "tier_violations": {"type": dict, "keys": ("count", "kinds")},
        "needs_implant": {"type": bool},
    },
    "stability": {
        "cap_stable_pct": {"type": (int, float, type(None))},
        "cap_depletes_in_s": {"type": (int, float, type(None))},
        "cap_load_gj_s": {"type": (int, float, type(None))},
        "cap_recharge_gj_s": {"type": (int, float, type(None))},
        "has_external_dependency": {"type": bool},
    },
}

L0_TOP_CHECKLIST = {
    "ship": {"type": dict, "keys": ("name", "group", "type_id")},
    "meta": {"type": dict, "keys": ("sde_build", "engine_version", "spec_version",
                                    "skill_tier", "module_states", "damage_profile", "mode")},
    "flags": {"type": list},
    "provenance_counts": {"type": dict},
    "budget": {"type": dict, "keys": ("chars", "degraded")},
}


def is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def numeric_leaves(node: object, prefix: str, out: list[tuple[str, float]]) -> None:
    if isinstance(node, dict):
        for key, child in node.items():
            numeric_leaves(child, f"{prefix}.{key}" if prefix else str(key), out)
    elif isinstance(node, list):
        for index, child in enumerate(node):
            numeric_leaves(child, f"{prefix}.{index}", out)
    elif is_number(node):
        out.append((prefix, float(node)))


def find_key(node: object, target: str, found: list[str]) -> None:
    if isinstance(node, dict):
        for key, child in node.items():
            if key == target:
                found.append(key)
            find_key(child, target, found)
    elif isinstance(node, list):
        for child in node:
            find_key(child, target, found)


def load_cases() -> list[tuple[str, object]]:
    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.engine.normalize import normalize_fit
    from evefit.project.views import base as views_base
    from evefit.sde.index import Index

    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())
    cases = []
    for path in sorted((HERE / "fixtures").glob("f*")):
        if path.name.endswith(".expected.json") or path.suffix not in (".json", ".eft"):
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            parsed = json.loads(text)
            text = json.dumps(parsed if isinstance(parsed, list) else [parsed], ensure_ascii=False)
        fit = normalize_fit(text)["fit"]
        if fit is None:
            continue
        asm = default_assumptions(fit, engine, index)
        enriched = dict(fit)
        enriched["character"] = {"skills": dict(asm["skill_tier"])}
        ctx = views_base.ViewContext(engine=engine, index=index, fit=enriched,
                                     assumptions=asm, meta=engine.meta())
        cases.append((path.stem, ctx))
    return cases


def main() -> int:
    from evefit.project import render as R
    from evefit.project.summary import INDEX_BUDGET_CHARS as IMPL_BUDGET

    defects: list[dict] = []
    notes: list[str] = []

    def defect(category: str, severity: str, case: str, where: str,
               expected: str, actual: str) -> None:
        defects.append({"category": category, "severity": severity, "case": case,
                        "where": where, "expected": expected, "actual": actual})

    print(f"契约额度={INDEX_BUDGET_CONTRACT}  文档额度={DOC_INDEX_BUDGET}  实现常量={IMPL_BUDGET}")
    cases = load_cases()
    print(f"样本数={len(cases)}\n")

    # ---------------- ② L0 契约 ----------------
    sizes: list[tuple[str, int]] = []
    stats = {"over_impl": 0, "over_doc": 0, "degraded": [], "budget_mismatch": [],
             "flags_total": 0, "flags_no_message": 0, "split_all_null": 0, "hints": 0,
             "meta_share_max": (0.0, "")}

    for name, ctx in cases:
        try:
            idx = R.render_index(ctx)
        except Exception as exc:  # noqa: BLE001 - 脚本不许崩
            defect("口径错误", "blocker", name, "render_index",
                   "可渲染", f"{type(exc).__name__}: {exc}")
            continue
        try:
            chars = len(json.dumps(idx, ensure_ascii=False))
            sizes.append((name, chars))
            if chars > INDEX_BUDGET_CONTRACT:
                stats["over_impl"] += 1
                defect("字段缺失", "high", name, "L0 体量", f"≤ {INDEX_BUDGET_CONTRACT}", str(chars))
            if chars > DOC_INDEX_BUDGET:
                stats["over_doc"] += 1

            budget = idx.get("budget") if isinstance(idx.get("budget"), dict) else {}
            if not isinstance(budget.get("degraded"), bool):
                defect("字段缺失", "high", name, "L0 budget.degraded",
                       "bool（L0 自身是否超预算）", repr(budget.get("degraded")))
            if budget.get("degraded"):
                stats["degraded"].append(name)
            reported = budget.get("chars")
            if is_number(reported) and int(reported) != chars:
                stats["budget_mismatch"].append((name, int(reported), chars))

            # 字段清单（按文档 §3 逐项对拍）
            for view, fields in L0_CHECKLIST.items():
                block = idx.get(view)
                if not isinstance(block, dict):
                    defect("字段缺失", "high", name, f"L0.{view}", "视角块存在", repr(block)[:60])
                    continue
                extra = set(block) - set(fields)
                if extra:
                    notes.append(f"{name} L0.{view} 多了文档未列字段：{sorted(extra)}")
                for field, rule in fields.items():
                    if field not in block:
                        defect("字段缺失", "high", name, f"L0.{view}.{field}",
                               "存在（文档 §3）", "缺失")
                        continue
                    value = block[field]
                    if not isinstance(value, rule["type"]):
                        defect("口径错误", "high", name, f"L0.{view}.{field}",
                               f"类型 {rule['type']}", type(value).__name__)
                        continue
                    if isinstance(value, dict):
                        for key in rule.get("keys", ()):
                            if key not in value:
                                defect("字段缺失", "high", name, f"L0.{view}.{field}.{key}",
                                       "子键存在（文档 §3）", "缺失")
                        for key in rule.get("keys_any", ()):
                            if not any(k in value for k in rule["keys_any"]):
                                defect("字段缺失", "high", name, f"L0.{view}.{field}",
                                       f"至少含 {rule['keys_any']}", sorted(value))
                        allowed = rule.get("keys_allowed")
                        if allowed:
                            unexpected = set(value) - set(allowed)
                            if unexpected:
                                defect("口径错误", "medium", name, f"L0.{view}.{field}",
                                       f"只允许 {allowed}", sorted(unexpected))
            for field, rule in L0_TOP_CHECKLIST.items():
                if field not in idx:
                    defect("字段缺失", "high", name, f"L0.{field}", "存在（文档 §3 顶层）", "缺失")

            counts_map = idx.get("provenance_counts")
            if not isinstance(counts_map, dict):
                defect("字段缺失", "high", name, "L0.provenance_counts", "dict", repr(counts_map)[:60])
            else:
                for view in VIEWS:
                    counts = counts_map.get(view)
                    if counts is None:
                        defect("字段缺失", "high", name, f"L0.provenance_counts.{view}",
                               "每视角三态计数", "缺失")
                    elif isinstance(counts, dict) and set(counts) != {"measured", "assumed", "unknown"}:
                        defect("口径错误", "medium", name, f"L0.provenance_counts.{view}",
                               "{measured, assumed, unknown}", sorted(counts))
                    elif isinstance(counts, list) and len(counts) != 3:
                        defect("口径错误", "medium", name, f"L0.provenance_counts.{view}",
                               "三元素（实现）/ 三键（文档）", repr(counts))

            # 红线：evidence / 逐叶子 provenance / 逐模块列表
            hits: list[str] = []
            find_key(idx, "evidence", hits)
            if hits:
                defect("口径错误", "blocker", name, "L0", "不含 evidence（文档 §7）",
                       f"出现 {len(hits)} 处")
            for view in VIEWS:
                block = idx.get(view)
                if isinstance(block, dict) and "provenance" in block:
                    defect("口径错误", "blocker", name, f"L0.{view}.provenance",
                           "L0 只给计数（文档 §2/§7）", "出现逐叶子表")
            for banned in ("cap_by_module", "cap_sensitivity", "weapons_detail", "module_list"):
                found: list[str] = []
                find_key(idx, banned, found)
                if found:
                    defect("口径错误", "blocker", name, f"L0.{banned}",
                           "不含逐模块列表（文档 §7）", "出现")

            flags = idx.get("flags") if isinstance(idx.get("flags"), list) else []
            stats["flags_total"] += len(flags)
            for flag in flags:
                if not isinstance(flag, dict):
                    defect("口径错误", "medium", name, "L0.flags", "元素是 dict", repr(flag)[:40])
                    continue
                if "message" not in flag:
                    stats["flags_no_message"] += 1
                if "evidence" in flag:
                    defect("口径错误", "blocker", name, "L0.flags",
                           "不带 evidence（文档 §7）", "带 evidence")
                if "hint" in flag:
                    hint = flag.get("hint")
                    stats["hints"] += 1
                    if hint is not None:
                        if not isinstance(hint, str):
                            defect("口径错误", "medium", name, f"L0.flags[{flag.get('id')}].hint",
                                   "str 或 null", type(hint).__name__)
                        elif len(hint) > 10:
                            defect("口径错误", "medium", name, f"L0.flags[{flag.get('id')}].hint",
                                   "≤ 10 字", f"{len(hint)} 字：{hint}")

            split = (idx.get("firepower") or {}).get("damage_split") if isinstance(
                idx.get("firepower"), dict) else None
            if isinstance(split, dict) and all(v is None for v in split.values()):
                stats["split_all_null"] += 1

            # 红线：L0 每个数值都要能被 get_path 取到，且逐位相同
            leaves: list[tuple[str, float]] = []
            for view in VIEWS:
                if isinstance(idx.get(view), dict):
                    numeric_leaves(idx[view], view, leaves)
            for path, value in leaves:
                result = R.get_path(ctx, path)
                if not isinstance(result, dict) or result.get("value") is None:
                    defect("口径错误", "high", name, f"L0.{path}", "get_path 可取到",
                           f"取不到（{result!r}）"[:80])
                    continue
                got = result.get("value")
                if not (is_number(got) and float(got) == value):
                    defect("口径错误", "high", name, f"L0.{path}",
                           f"逐位相同 {value!r}", f"{got!r}")
                elif result.get("provenance") is None:
                    defect("provenance 错误", "medium", name, f"L0.{path}",
                           "provenance 非空（get_path 契约）", "None")

            meta_chars = len(json.dumps(idx.get("meta"), ensure_ascii=False))
            if meta_chars / max(chars, 1) > stats["meta_share_max"][0]:
                stats["meta_share_max"] = (meta_chars / max(chars, 1), name)
        except Exception as exc:  # noqa: BLE001 - 单样本内部异常不得中断整场
            defect("口径错误", "high", name, "check_layers.L0",
                   "单样本 L0 检查可完成", f"{type(exc).__name__}: {exc}")

    # budget.chars 量错对象（Lead 判 medium，已派 T-41）
    if stats["budget_mismatch"]:
        name0, reported0, chars0 = stats["budget_mismatch"][0]
        defect("口径错误", "medium", name0, "L0.budget.chars",
               "自身正文长度（不含 budget 块）",
               f"{len(stats['budget_mismatch'])}/{len(cases)} 个样本不符；"
               f"例：{name0} 报 {reported0}、实际 {chars0}（差 {chars0 - reported0}）")

    # ── degraded=True 分支：把预算临时压到 1，字段数值必须一字不变 ──
    degraded_ok = "未执行"
    try:
        from evefit.project import summary as summary_mod
        probe_name, probe_ctx = cases[0]
        normal = R.render_index(probe_ctx)
        original = summary_mod.INDEX_BUDGET_CHARS
        summary_mod.INDEX_BUDGET_CHARS = 1
        try:
            degraded_idx = R.render_index(probe_ctx)
        finally:
            summary_mod.INDEX_BUDGET_CHARS = original
        flag = bool((degraded_idx.get("budget") or {}).get("degraded"))
        same = all(json.dumps(degraded_idx.get(k), ensure_ascii=False, sort_keys=True)
                   == json.dumps(normal.get(k), ensure_ascii=False, sort_keys=True)
                   for k in VIEWS)
        degraded_ok = f"degraded={flag}，六视角数值一字不变={same}"
        if not flag:
            defect("口径错误", "high", probe_name, "L0.budget.degraded",
                   "预算压到 1 时必须为 True", str(flag))
        if not same:
            defect("口径错误", "high", probe_name, "L0.degraded 分支",
                   "字段数值一字不变", "降级后数值有变化")
    except Exception as exc:  # noqa: BLE001
        degraded_ok = f"执行失败：{type(exc).__name__}: {exc}"

    print("② L0 契约")
    if sizes:
        print(f"  体量：min={min(s for _, s in sizes)} "
              f"median={int(statistics.median([s for _, s in sizes]))} "
              f"max={max(s for _, s in sizes)}")
    print(f"  超契约额度({INDEX_BUDGET_CONTRACT})={stats['over_impl']}；"
          f"超文档额度({DOC_INDEX_BUDGET})={stats['over_doc']}；"
          f"实现常量={IMPL_BUDGET}" + ("" if IMPL_BUDGET == INDEX_BUDGET_CONTRACT
                                     else " ⏳（T-40 收尾后会改成 2,500）"))
    print(f"  degraded=True：{stats['degraded'] or '无'}")
    print(f"  budget.chars 与实际长度不符：{len(stats['budget_mismatch'])}"
          + (f"（例 {stats['budget_mismatch'][0]}）" if stats["budget_mismatch"] else ""))
    print(f"  flags：{stats['flags_total']} 条，其中无 message 的 {stats['flags_no_message']} 条")
    print(f"  damage_split 四值全 null：{stats['split_all_null']}")
    print(f"  meta 占 L0 最高：{stats['meta_share_max'][0]:.1%}（{stats['meta_share_max'][1]}）")
    print(f"  flags 带 hint 条数：{stats['hints']}/{stats['flags_total']}")
    print(f"  degraded=True 分支：{degraded_ok}\n")

    # ---------------- ③ L1 一致性 ----------------
    diff_non, diff_deg, miss_table, miss_evi = [], [], [], []
    for name, ctx in cases:
        try:
            overview = R.render_overview(ctx)
            degraded = bool((overview.get("budget") or {}).get("degraded"))
            for view in VIEWS:
                l1 = R.render_view(ctx, view)
                same = json.dumps(l1, ensure_ascii=False, sort_keys=True) == json.dumps(
                    overview["views"][view], ensure_ascii=False, sort_keys=True)
                if not same:
                    (diff_deg if degraded else diff_non).append(f"{name}.{view}")
                for table in ("provenance", "unknown_reasons", "confidence", "flags"):
                    if table not in l1:
                        miss_table.append(f"{name}.{view}.{table}")
                for flag in l1.get("flags") or []:
                    if isinstance(flag, dict) and "evidence" not in flag:
                        miss_evi.append(f"{name}.{view}.{flag.get('id')}")
        except Exception as exc:  # noqa: BLE001
            defect("口径错误", "high", name, "check_layers.L1",
                   "L1 检查可完成", f"{type(exc).__name__}: {exc}")
    try:
        R.render_view(cases[0][1], "not_a_view")
        unknown_view = "未抛错"
    except ValueError as exc:
        unknown_view = str(exc)
    except Exception as exc:  # noqa: BLE001
        unknown_view = f"抛了 {type(exc).__name__}: {exc}"

    print("③ L1 一致性")
    print(f"  未降级样本不同视角：{len(diff_non)}{'（例 ' + str(diff_non[:4]) + '）' if diff_non else ''}")
    print(f"  降级样本不同视角（允许）：{len(diff_deg)}")
    print(f"  缺平行表：{len(miss_table)}；flags 缺 evidence：{len(miss_evi)}")
    print(f"  未知视角：{unknown_view[:120]}")
    if diff_non:
        defect("口径错误", "high", "多个", "L1 vs overview", "未降级样本逐字节相同",
               f"{len(diff_non)} 个不同：{diff_non[:6]}")
    if miss_table:
        defect("字段缺失", "high", "多个", "L1 平行表", "四表齐全", str(miss_table[:5]))
    if miss_evi:
        defect("字段缺失", "medium", "多个", "L1.flags.evidence", "带 evidence", str(len(miss_evi)))
    print()

    # ---------------- ④ get_path ----------------
    ctx = cases[0][1]
    missing_result = R.get_path(ctx, "no.such.path")
    int_key = R.get_path(ctx, "constraints.skill_floor.count_by_level.1")
    normal = R.get_path(ctx, "mobility.speed_mps")
    print("④ get_path")
    print(f"  缺失路径：{json.dumps(missing_result, ensure_ascii=False)[:180]}")
    print(f"  int 键回退：{json.dumps(int_key, ensure_ascii=False)[:110]}")
    print(f"  正常路径：{json.dumps(normal, ensure_ascii=False)[:110]}")
    if not isinstance(missing_result, dict) or "hint" not in missing_result:
        defect("口径错误", "medium", cases[0][0], "get_path(missing)",
               "返回 {value: None, hint}", repr(missing_result)[:80])
    if not (isinstance(int_key, dict) and is_number(int_key.get("value"))):
        defect("口径错误", "high", cases[0][0], "get_path(int 键)", "能取到整数键", repr(int_key)[:80])
    if isinstance(normal, dict):
        for key in ("value", "provenance", "source_view"):
            if key not in normal:
                defect("字段缺失", "medium", cases[0][0], f"get_path.{key}", "存在", "缺失")
    print()

    # ---------------- 汇总 ----------------
    counts: dict[str, int] = {}
    for item in defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print("=" * 78)
    print(f"缺陷合计 {len(defects)}：{counts or '无'}")
    for item in defects[:10]:
        print(f"  [{item['severity']}] {item['category']} | {item['case']} | {item['where']}"
              f" | 期望 {item['expected']} | 实际 {item['actual']}")
    if notes:
        print(f"观察 {len(notes)} 条：{notes[:3]}")

    out = HERE / "out" / "layers-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "samples": len(cases), "index_budget_contract": INDEX_BUDGET_CONTRACT,
        "impl_budget": IMPL_BUDGET,
        "doc_index_budget": DOC_INDEX_BUDGET,
        "l0_sizes": {"min": min(s for _, s in sizes) if sizes else None,
                     "median": int(statistics.median([s for _, s in sizes])) if sizes else None,
                     "max": max(s for _, s in sizes) if sizes else None},
        "stats": {k: v for k, v in stats.items() if k != "budget_mismatch"},
        "budget_mismatch": stats["budget_mismatch"],
        "l1_diff_non_degraded": diff_non, "l1_diff_degraded": diff_deg,
        "defects": defects, "notes": notes,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {out}")
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
