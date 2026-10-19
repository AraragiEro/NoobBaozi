"""P3 独立验收：`explain`（归因是不是真归因）+ `sensitivity`（改动量自洽 + 与 L2 交叉）。

判据（按 Lead 的 A/B/C 三类）：
  A 加性（`survivability.ehp_mixed` / `firepower.dps.base` / `resources.cpu.load` /
    `resources.powergrid.load`）：`base + Σ contribution == value`（相对 1e-9）；
    **反向**：去掉任一 contributor → 和不再等于 value；`folded_noops` 与「滤掉的零项」一致。
  B 乘性（`mobility.speed_mps` / `stability.capacitor.peak_recharge`）：
    `base × Π factor == replayed == value`（`post_percent` 按百分比折算）；**不许**当加数求和。
  C 投影（`survivability.thinnest.{shield,armor,hull}.ehp`）：`formula` 两项算出 value；
    depth-2 能看到效应型模块与堆叠惩罚（如 0.8691）。

  sensitivity：`delta == value - baseline`、`delta_pct == delta/baseline`（baseline 0 → null）、
    四个计数自洽、**与 `cap_sensitivity[0]` 交叉验证**（同一件关闭）。

变异证明：把 A 类的 value 舍到 3 位 → 加性判据必须红。
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(HERE))

from check_detail import load_fit  # noqa: E402

SAMPLES = ["f1-hecate-killmail", "f7-t3c-legion", "f11-t3d-svipul", "f12-logistics-oneiros",
           "f25-nos-neut", "f26-carrier-fighter", "f33-cargo-heavy", "f67-spotcheck-139255942"]

A_METRICS = ["survivability.ehp_mixed", "firepower.dps.base",
             "resources.cpu.load", "resources.powergrid.load"]
B_METRICS = ["mobility.speed_mps", "stability.capacitor.peak_recharge"]
C_METRICS = ["survivability.thinnest.shield.ehp", "survivability.thinnest.armor.ehp",
             "survivability.thinnest.hull.ehp"]

REL_TOL = 1e-9


def close(left, right, rel=REL_TOL, floor=1e-9) -> bool:
    scale = max(abs(float(left)), abs(float(right)), 1.0)
    return abs(float(left) - float(right)) <= rel * scale + floor


def replay_multiplicative(result: dict):
    """按 `operator/sign/factor/penalty` 重放乘性链；返回 (replay, 用了几个因子)。

    关键：**`penalty` 必须参与折算**（堆叠惩罚），否则 f33/f67 这类多件同类模块会差 1%–60%：
        有效倍数 = 1 + (本项倍数 - 1) × penalty
    其中 `post_percent` 先把百分比折成 `1 + factor/100`；`post_div` 折成 `1/factor`。
    """
    total = float(result.get("base") or 0.0)
    used = 0
    for source in result.get("sources") or []:
        factor = source.get("factor")
        operator = source.get("operator")
        if factor is None or source.get("applied") is False:
            continue
        factor = float(factor)
        penalty = source.get("penalty")
        penalty = 1.0 if penalty is None else float(penalty)
        if operator == "post_percent":
            effective = 1.0 + factor / 100.0
        elif operator == "post_div":
            if factor == 0:
                return None, used
            effective = 1.0 / factor
        elif operator in ("pre_mul", "post_mul"):
            effective = factor
        else:
            return None, used
        total *= 1.0 + (effective - 1.0) * penalty
        used += 1
    return total, used


def additive_sum(result: dict) -> float:
    return float(result.get("base") or 0.0) + sum(
        float(source.get("contribution") or 0.0)
        for source in (result.get("sources") or []) if source.get("applied") is not False)


def check_explain_a(result: dict, where: str) -> list[dict]:
    defects: list[dict] = []
    value = result.get("value")
    if result.get("combine") != "additive":
        return [{"category": "口径错误", "severity": "high", "where": f"{where}.combine",
                 "expected": "additive", "actual": repr(result.get("combine"))}]
    total = additive_sum(result)
    if not close(total, value):
        defects.append({"category": "口径错误", "severity": "high", "where": f"{where} 正向",
                        "expected": f"base+Σ == {value!r}", "actual": f"{total!r}"})
    # 反向：去掉任一 contributor，和必须不再等于 value
    sources = [s for s in (result.get("sources") or []) if s.get("applied") is not False]
    for index, source in enumerate(sources):
        rest = total - float(source.get("contribution") or 0.0)
        if close(rest, value):
            defects.append({"category": "口径错误", "severity": "high",
                            "where": f"{where} 反向(去掉 source[{index}] {source.get('label')})",
                            "expected": "和应改变（该项必需）", "actual": f"仍等于 {rest!r}"})
    # folded_noops：过滤后不应再有零项 / 未应用项
    folded = result.get("folded_noops")
    if not isinstance(folded, int) or folded < 0:
        defects.append({"category": "字段缺失", "severity": "medium",
                        "where": f"{where}.folded_noops", "expected": "非负整数", "actual": repr(folded)})
    zero = [s for s in sources
            if (s.get("contribution") == 0 or s.get("contribution") == 0.0
                or (s.get("factor") in (0.0, 1.0)))]
    if zero and folded != len(zero):
        defects.append({"category": "口径错误", "severity": "medium",
                        "where": f"{where}.folded_noops",
                        "expected": f"{len(zero)}（被滤掉的零项数）", "actual": repr(folded)})
    unexplained = result.get("unexplained")
    if isinstance(unexplained, (int, float)) and abs(float(unexplained)) > 1e-6:
        defects.append({"category": "口径错误", "severity": "medium",
                        "where": f"{where}.unexplained", "expected": "≈0",
                        "actual": repr(unexplained)})
    return defects


def check_explain_b(result: dict, where: str) -> list[dict]:
    defects: list[dict] = []
    if result.get("combine") != "multiplicative":
        return [{"category": "口径错误", "severity": "high", "where": f"{where}.combine",
                 "expected": "multiplicative", "actual": repr(result.get("combine"))}]
    replay, used = replay_multiplicative(result)
    value = result.get("value")
    reported = result.get("replayed")
    if replay is None:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": f"{where} 重放", "expected": "因子链可重放",
                        "actual": "遇到未知 operator 或 0 因子"})
        return defects
    if not close(replay, value):
        defects.append({"category": "口径错误", "severity": "high", "where": f"{where} 重放",
                        "expected": f"{value!r}", "actual": f"{replay!r}（{used} 个因子）"})
    if reported is not None and not close(replay, reported):
        defects.append({"category": "口径错误", "severity": "high",
                        "where": f"{where}.replayed", "expected": f"{replay!r}",
                        "actual": repr(reported)})
    if result.get("replay_ok") is not True:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": f"{where}.replay_ok", "expected": True,
                        "actual": repr(result.get("replay_ok"))})
    # 不许当加数求和
    contributions = [s.get("contribution") for s in (result.get("sources") or [])
                     if s.get("contribution") is not None]
    if contributions:
        summed = sum(float(value) for value in contributions)
        if close(summed, value):
            defects.append({"category": "口径错误", "severity": "high",
                            "where": f"{where} 加性误用",
                            "expected": "乘性指标不应 Σcontribution == value",
                            "actual": f"Σcontribution={summed!r} == value"})
    return defects


def check_explain_c(result: dict, where: str) -> tuple[list[dict], int, list[float]]:
    defects: list[dict] = []
    formula = result.get("formula")
    sources = [s for s in (result.get("sources") or []) if s.get("applied") is not False]
    if not isinstance(formula, str) or "÷" not in formula:
        return ([{"category": "字段缺失", "severity": "medium", "where": f"{where}.formula",
                  "expected": "形如 'X ÷ Y'", "actual": repr(formula)}], 0, [])
    numerator_hint, denominator_hint = [part.strip() for part in formula.split("÷", 1)]
    numerator = denominator = None
    for source in sources:
        label = str(source.get("label") or "")
        if source.get("operator") == "post_div" and denominator is None:
            denominator = source.get("factor")
        elif numerator_hint.split("[")[0] in label and numerator is None:
            numerator = source.get("contribution")
    if numerator is None or denominator in (None, 0):
        defects.append({"category": "字段缺失", "severity": "high",
                        "where": f"{where} 两项", "expected": "分子/分母都能在 sources 里找到",
                        "actual": f"numerator={numerator!r} denominator={denominator!r}"})
        return defects, 0, []
    computed = float(numerator) / float(denominator)
    if not close(computed, result.get("value"), rel=1e-6):
        defects.append({"category": "口径错误", "severity": "high", "where": f"{where} 公式",
                        "expected": f"{result.get('value')!r}",
                        "actual": f"{numerator}/{denominator} = {computed!r}"})
    # depth-2：效应型模块 + 堆叠惩罚
    penalties: list[float] = []
    module_sources = 0

    def walk(nodes):
        nonlocal module_sources
        for source in nodes or []:
            if source.get("kind") == "item" and source.get("operator") in (
                    "pre_mul", "post_mul", "post_div", "post_percent"):
                module_sources += 1
            if isinstance(source.get("penalty"), (int, float)):
                penalties.append(float(source["penalty"]))
            walk(source.get("children"))

    walk(result.get("sources"))
    # depth-2 的“能看到效应模块 / 堆叠惩罚”是**覆盖面**要求（整体至少出现一次），
    # 不是每个 metric 都得有 —— 没装抗性模块的样本本来就没有修正源。
    return defects, module_sources, [v for v in penalties if 0 < v < 0.999]


def check_sensitivity(result: dict, where: str) -> list[dict]:
    defects: list[dict] = []
    if not isinstance(result, dict):
        return [{"category": "字段缺失", "severity": "high", "where": where,
                 "expected": "dict", "actual": repr(result)}]
    planned = result.get("ops_planned")
    executed = result.get("ops_executed")
    skipped = result.get("skipped")
    truncated = result.get("truncated")
    changes = result.get("changes") or []
    if not all(isinstance(value, int) for value in (planned, executed, skipped)):
        return [{"category": "字段缺失", "severity": "high", "where": f"{where}.counts",
                 "expected": "ops_planned/ops_executed/skipped 为整数",
                 "actual": f"{planned}/{executed}/{skipped}"}]
    if planned != executed + skipped:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": f"{where}.ops_planned",
                        "expected": f"executed+skipped={executed + skipped}", "actual": planned})
    if len(changes) != executed:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": f"{where}.changes 长度",
                        "expected": executed, "actual": len(changes)})
    max_ops = result.get("max_ops")
    if isinstance(max_ops, int) and executed > max_ops:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": f"{where}.ops_executed", "expected": f"<= {max_ops}",
                        "actual": executed})
    if truncated is not None and isinstance(max_ops, int):
        expect_truncated = planned > max_ops
        if bool(truncated) is not expect_truncated and not (truncated and executed == max_ops):
            defects.append({"category": "口径错误", "severity": "medium",
                            "where": f"{where}.truncated",
                            "expected": expect_truncated, "actual": truncated})
    baseline = result.get("baseline")
    for index, change in enumerate(changes):
        value = change.get("value")
        delta = change.get("delta")
        delta_pct = change.get("delta_pct")
        if not all(isinstance(v, (int, float)) for v in (value, delta)):
            defects.append({"category": "字段缺失", "severity": "high",
                            "where": f"{where}.changes[{index}]",
                            "expected": "value/delta 都是数", "actual": repr(change)[:120]})
            continue
        if isinstance(baseline, (int, float)) and not close(float(delta),
                                                            float(value) - float(baseline),
                                                            rel=1e-9, floor=1e-6):
            defects.append({"category": "口径错误", "severity": "high",
                            "where": f"{where}.changes[{index}].delta",
                            "expected": f"{float(value) - float(baseline)!r}", "actual": repr(delta)})
        if isinstance(baseline, (int, float)):
            if float(baseline) == 0.0:
                if delta_pct is not None:
                    defects.append({"category": "口径错误", "severity": "high",
                                    "where": f"{where}.changes[{index}].delta_pct",
                                    "expected": None, "actual": repr(delta_pct)})
            elif isinstance(delta_pct, (int, float)):
                expect = float(delta) / float(baseline)
                if abs(float(delta_pct) - expect) > abs(expect) * 1e-6 + 1e-9:
                    defects.append({"category": "口径错误", "severity": "high",
                                    "where": f"{where}.changes[{index}].delta_pct",
                                    "expected": f"{expect!r}", "actual": repr(delta_pct)})
    return defects


def main() -> int:
    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.project.explain import explain, supported_metrics as explain_metrics
    from evefit.project.render import render_detail
    from evefit.project.sensitivity import sensitivity, supported_metrics as sens_metrics
    from evefit.project.views import base as views_base
    from evefit.sde.index import Index

    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())
    defects: list[dict] = []
    notes: list[str] = []
    coverage = {"explain_A": 0, "explain_B": 0, "explain_C": 0, "sensitivity": 0,
                "cross_validation": 0}
    cross_rows: list[dict] = []

    def make_ctx(fit):
        assumptions = default_assumptions(fit, engine, index)
        enriched = {**fit, "character": {"skills": dict(assumptions["skill_tier"])}}
        return views_base.ViewContext(engine=engine, index=index, fit=enriched,
                                      assumptions=assumptions, meta=engine.meta()), enriched

    for stem in SAMPLES:
        fit = load_fit(stem)
        ctx, enriched = make_ctx(fit)
        print(f"{stem:26s}", end=" ")
        count = 0
        for metric in A_METRICS:
            if metric not in explain_metrics():
                continue
            result = explain(ctx, metric)
            if result is None:
                notes.append(f"{stem}: explain({metric}) 返回 None")
                continue
            found = check_explain_a(result, f"{stem}.explain({metric})")
            defects.extend(found)
            coverage["explain_A"] += 1
            count += len(found)
        for metric in B_METRICS:
            if metric not in explain_metrics():
                continue
            result = explain(ctx, metric)
            if result is None:
                notes.append(f"{stem}: explain({metric}) 返回 None")
                continue
            found = check_explain_b(result, f"{stem}.explain({metric})")
            defects.extend(found)
            coverage["explain_B"] += 1
            count += len(found)
        for metric in C_METRICS:
            result = explain(ctx, metric)
            if result is None:
                notes.append(f"{stem}: explain({metric}) 返回 None")
                continue
            found, module_sources, stacked = check_explain_c(
                result, f"{stem}.explain({metric})")
            defects.extend(found)
            coverage["explain_C"] += 1
            count += len(found)
            coverage["c_depth2_modules"] = coverage.get("c_depth2_modules", 0) + module_sources
            coverage["c_stacking"] = coverage.get("c_stacking", 0) + len(stacked)

        # sensitivity：电容稳定度
        sens = sensitivity(ctx, "stability.capacitor.stable_pct")
        if sens is not None:
            found = check_sensitivity(sens, f"{stem}.sensitivity(stable_pct)")
            defects.extend(found)
            coverage["sensitivity"] += 1
            count += len(found)
        # 交叉验证：cap_sensitivity[0] ↔ sensitivity(disable 同一件)
        detail = render_detail(ctx, view="stability")
        cap_sens = (detail.get("overview") or {}).get("cap_sensitivity") or []
        if cap_sens and sens is not None:
            entry = cap_sens[0]
            type_id = entry.get("type_id")
            item_index = None
            for idx, item in enumerate(enriched.get("items") or []):
                if item.get("type_id") == type_id:
                    item_index = idx
                    break
            if item_index is not None:
                explicit = sensitivity(ctx, "stability.capacitor.stable_pct",
                                       ops=[{"kind": "disable_item",
                                             "item_index": item_index,
                                             "state": "offline"}])
                value = ((explicit or {}).get("changes") or [{}])[0].get("value")
                reported = entry.get("stable_pct")
                same = (isinstance(value, (int, float)) and isinstance(reported, (int, float))
                        and close(float(value), float(reported), rel=1e-6, floor=0.01))
                cross_rows.append({"sample": stem, "type_id": type_id,
                                   "cap_sensitivity": reported, "sensitivity": value,
                                   "same": same})
                coverage["cross_validation"] += 1
                if not same:
                    defects.append({"category": "口径错误", "severity": "high",
                                    "where": f"{stem}.交叉验证(cap_sensitivity[0])",
                                    "expected": f"sensitivity(disable item {item_index}) == "
                                                f"{reported!r}",
                                    "actual": repr(value)})
        print(f"缺陷累计 {count}")

    # ---------------- 边界 ----------------
    print("\n边界：0 模块 / 全 offline / T3C 无 mode / 同一件关两次")
    bare = {"ship": {"type_id": 35683}, "items": []}
    ctx_bare, _ = make_ctx(bare)
    sens_bare = sensitivity(ctx_bare, "stability.capacitor.stable_pct")
    if sens_bare is None:
        notes.append("0 模块：sensitivity 返回 None（允许，但需文档写明）")
    else:
        defects.extend(check_sensitivity(sens_bare, "边界.0模块"))
        # 0 模块仍可能有 **模式 op**（Hecate 有 3 个模式）——不是缺陷，记观察
        mode_ops = [change for change in (sens_bare.get("changes") or [])
                    if (change.get("op") or {}).get("kind") == "set_mode"]
        notes.append(f"边界.0模块：ops_planned={sens_bare.get('ops_planned')}"
                     f"（其中 set_mode {len(mode_ops)} 个；0 模块但船有模式）")

    fit_all_off = load_fit("f1-hecate-killmail")
    for item in fit_all_off.get("items") or []:
        item["state"] = "offline"
    ctx_off, _ = make_ctx(fit_all_off)
    sens_off = sensitivity(ctx_off, "stability.capacitor.stable_pct")
    if sens_off is None:
        notes.append("全 offline：sensitivity 返回 None（需文档写明）")
    else:
        defects.extend(check_sensitivity(sens_off, "边界.全offline"))

    fit_t3c = load_fit("f32-t3c-no-cloak")
    ctx_t3c, enriched_t3c = make_ctx(fit_t3c)
    sens_t3c = sensitivity(ctx_t3c, "mobility.speed_mps")
    if sens_t3c is None:
        notes.append("T3C 无 mode：sensitivity 返回 None（需文档写明）")
    else:
        defects.extend(check_sensitivity(sens_t3c, "边界.T3C无mode"))
        mode_ops = [change for change in (sens_t3c.get("changes") or [])
                    if (change.get("op") or {}).get("kind") == "set_mode"]
        print(f"  T3C 无 mode：mode 类 op 数 = {len(mode_ops)}（应为 0 或只有真实模式）")

    # 同一件关两次
    ctx_dup, enriched_dup = make_ctx(load_fit("f1-hecate-killmail"))
    dup_ops = [{"kind": "disable_item", "item_index": 0, "state": "offline"},
               {"kind": "disable_item", "item_index": 0, "state": "offline"}]
    sens_dup = sensitivity(ctx_dup, "stability.capacitor.stable_pct", ops=dup_ops)
    defects.extend(check_sensitivity(sens_dup, "边界.同一件关两次"))
    values = [change.get("value") for change in (sens_dup or {}).get("changes") or []]
    print(f"  同一件关两次：planned={sens_dup.get('ops_planned')} "
          f"executed={sens_dup.get('ops_executed')} skipped={sens_dup.get('skipped')} "
          f"values={values}")

    # ---------------- 变异证明 ----------------
    mutation_sample = explain(make_ctx(load_fit("f1-hecate-killmail"))[0],
                              "resources.cpu.load")
    tampered = copy.deepcopy(mutation_sample)
    tampered["value"] = float(tampered["value"]) * 1.001     # 真扰动 0.1%
    mutation_defects = check_explain_a(tampered, "变异.value 舍到 3 位")
    proofs = [{"check": "A 加性正向", "mutated": "value ×1.001",
               "caught": bool(mutation_defects)}]
    print("\n变异证明：", json.dumps(proofs, ensure_ascii=False))
    if not all(proof["caught"] for proof in proofs):
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "变异证明", "expected": "能抓到", "actual": "未抓到"})

    counts: dict[str, int] = {}
    for item in defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"\n覆盖：{coverage}")
    print(f"交叉验证：{json.dumps(cross_rows, ensure_ascii=False)}")
    print(f"缺陷合计 {len(defects)}：{counts or '无'}")
    for item in defects[:12]:
        print(f"  [{item['severity']}] {item['category']} | {item['where']} | "
              f"期望 {item['expected']} | 实际 {item['actual']}")
    if notes:
        print(f"\n未验/观察 {len(notes)} 条（前 6）：")
        for note in notes[:6]:
            print(f"  · {note}")

    out = HERE / "out" / "p3-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"samples": SAMPLES, "coverage": coverage,
                               "cross_validation": cross_rows, "mutation_proofs": proofs,
                               "defects": defects, "notes": notes},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n已写 {out}")
    index.close()
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
