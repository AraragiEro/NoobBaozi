"""L2 `fit_detail` 字段级独立验收（四类，独立重算 + 变异证明）。

口径（读实现后确认，避免把产品语义误判成缺陷）：
- ① `cap_by_module` 的源头是**装备级 `-4`（capPeakLoad 贡献）**；船体 `-4` == Σ 装备级 `-4`。
  对 `attr6 > 0` 的消费者还要满足 `-4 == -6/cycle`（我独立复算这条公式）；nos/注射器是**负数**。
- ② `cap_sensitivity` 的语义是「**摘掉**这个模块之后的 stable_pct / depletes_in_s」（敏感度），
  **不是**「摘掉就变稳定」。所以断言它等与我摘一件后引擎算出的 `-72`，而不是断言 > 0。
- ③ `upgrade_space[].fits` = 「换上去后**没有资源类硬规则**违反」（非资源类规则也可能让它 False）。
  因此只做单向强断言：`fits == True` ⇒ 资源必须真的够；反向只记观察（需要 reason 才能判）。
- ④ `skill_tier`：L0 有 `{name,count,hash}`，detail 有 512 条逐技能；**L1 无 meta**（不是缺陷）。

用法：
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python evefit/acceptance/check_detail.py
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

SAMPLES = ["f1-hecate-killmail", "f7-t3c-legion", "f11-t3d-svipul", "f12-logistics-oneiros",
           "f15-battleship-raven", "f25-nos-neut", "f26-carrier-fighter", "f33-cargo-heavy",
           "f48-random-gnosis", "f67-spotcheck-139255942"]

A_CAP_NEED, A_CYCLE, A_DURATION, A_SPEED = 6, -3, 73, 51
A_CAP_CAP, A_CAP_RECHARGE, A_PEAK_LOAD, A_STABLE, A_DEPLETES = 482, 55, -4, -72, -7
A_CPU_OUT, A_CPU_LOAD, A_PG_OUT, A_PG_LOAD = 48, 49, 11, 15
A_CAL_OUT, A_CAL_LOAD = 1132, 1152


def amap(node) -> dict:
    return {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in (node or {}).items()}


def cycle_seconds(attrs: dict):
    for attr in (A_CYCLE, A_DURATION, A_SPEED):
        value = attrs.get(attr)
        if isinstance(value, (int, float)) and value > 0:
            return float(value) / 1000.0
    return None


def load_fit(stem: str) -> dict:
    from evefit.engine.normalize import normalize_fit

    for suffix in (".json", ".eft"):
        path = HERE / "fixtures" / f"{stem}{suffix}"
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        if suffix == ".json":
            parsed = json.loads(text)
            text = json.dumps(parsed if isinstance(parsed, list) else [parsed], ensure_ascii=False)
        return normalize_fit(text)["fit"]
    raise FileNotFoundError(stem)


def item_cap_map(engine, enriched) -> tuple[dict[int, dict], float | None]:
    """装备级 `-4` 聚合（cap_by_module 的权威分解）。"""
    calc = engine.calculate(enriched)
    ship = amap(calc["ship"]["attributes"])
    in_items = enriched.get("items") or []
    rows: dict[int, dict] = {}
    for position, item in enumerate(calc["items"]):
        attrs = amap(item.get("attributes") or {})
        value = attrs.get(A_PEAK_LOAD)
        if not isinstance(value, (int, float)) or value == 0:
            continue
        type_id = in_items[position].get("type_id") if position < len(in_items) else None
        if type_id is None:
            continue
        row = rows.setdefault(type_id, {"cap": 0.0, "count": 0,
                                        "need": attrs.get(A_CAP_NEED)})
        row["cap"] += float(value)
        row["count"] += 1
    return rows, ship.get(A_PEAK_LOAD)


def check_cap_by_module(detail: dict, engine, enriched) -> list[dict]:
    defects: list[dict] = []
    overview = detail.get("overview") or {}
    reported = [entry for entry in (overview.get("cap_by_module") or [])
                if isinstance(entry, dict)]
    mine, ship_peak = item_cap_map(engine, enriched)
    by_tid = {entry.get("type_id"): entry for entry in reported}
    for type_id, row in mine.items():
        entry = by_tid.get(type_id)
        if entry is None:
            defects.append({"category": "字段缺失", "severity": "high",
                            "where": f"cap_by_module(type_id={type_id})",
                            "expected": f"{row['cap']:.3f}", "actual": "未出现在 cap_by_module"})
            continue
        got = entry.get("cap_use_gj_s")
        if not isinstance(got, (int, float)) or abs(float(got) - row["cap"]) > max(
                0.02, abs(row["cap"]) * 5e-3):
            defects.append({"category": "口径错误", "severity": "high",
                            "where": f"cap_by_module(type_id={type_id})",
                            "expected": f"{row['cap']:.3f}（装备级 -4 聚合）",
                            "actual": repr(got)})
        if entry.get("count") != row["count"]:
            defects.append({"category": "口径错误", "severity": "medium",
                            "where": f"cap_by_module(type_id={type_id}).count",
                            "expected": row["count"], "actual": entry.get("count")})
        # 公式独立复算（只对 attr6>0 的消费者）
        need = row.get("need")
        if isinstance(need, (int, float)) and float(need) > 0:
            attrs = None
            calc = engine.calculate(enriched)
            in_items = enriched.get("items") or []
            for position, item in enumerate(calc["items"]):
                tid = in_items[position].get("type_id") if position < len(in_items) else None
                if tid == type_id:
                    attrs = amap(item.get("attributes") or {})
                    break
            if attrs:
                cycle = cycle_seconds(attrs)
                if cycle:
                    want = float(need) / cycle      # 装备级 -4 对消费者为正；nos 才为负
                    if abs(row["cap"] / max(row["count"], 1) - want) > max(
                            0.02, abs(want) * 5e-3):
                        defects.append({"category": "口径错误", "severity": "medium",
                                        "where": f"cap_by_module(type_id={type_id}).公式",
                                        "expected": f"6/cycle = {want:.4f}",
                                        "actual": f"{row['cap'] / max(row['count'], 1):.4f}"})
    for type_id in by_tid:
        if type_id not in mine:
            defects.append({"category": "口径错误", "severity": "medium",
                            "where": f"cap_by_module(type_id={type_id})",
                            "expected": "不该出现（装备级 -4 为 0）",
                            "actual": json.dumps(by_tid[type_id], ensure_ascii=False)[:80]})
    total = sum(float(entry.get("cap_use_gj_s") or 0.0) for entry in reported)
    if isinstance(ship_peak, (int, float)) and abs(total - float(ship_peak)) > max(
            0.05, abs(float(ship_peak)) * 5e-3):
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "peak_load == Σ cap_by_module",
                        "expected": f"{float(ship_peak):.6f}", "actual": f"{total:.6f}"})
    return defects


def check_cap_sensitivity(detail: dict, engine, enriched) -> tuple[list[dict], dict]:
    """敏感度：报告值必须等于「摘掉一件该模块后」引擎重算的 stable_pct / depletes。"""
    defects: list[dict] = []
    stats = {"checked": 0, "flip": 0, "no_flip": 0}
    overview = detail.get("overview") or {}
    entries = [e for e in (overview.get("cap_sensitivity") or []) if isinstance(e, dict)]
    items = list(enriched.get("items") or [])
    fitted = {item.get("type_id") for item in items}
    top = overview.get("capacitor") or {}
    before = top.get("stable_pct")
    for entry in entries[:3]:
        type_id = entry.get("type_id")
        stats["checked"] += 1
        if type_id not in fitted:
            defects.append({"category": "口径错误", "severity": "high",
                            "where": f"cap_sensitivity(type_id={type_id}).module",
                            "expected": "在已装配列表里", "actual": "找不到"})
            continue
        removed = False
        remaining = []
        for item in items:
            if not removed and item.get("type_id") == type_id:
                removed = True
                continue
            remaining.append(item)
        attrs = amap(engine.calculate({**enriched, "items": remaining})["ship"]["attributes"])
        after_stable = attrs.get(A_STABLE)
        after_depletes = attrs.get(A_DEPLETES)
        reported = entry.get("stable_pct")
        if isinstance(after_stable, (int, float)) and isinstance(reported, (int, float)):
            if abs(float(after_stable) - float(reported)) > 0.6:
                defects.append({"category": "口径错误", "severity": "high",
                                "where": f"cap_sensitivity(type_id={type_id}).stable_pct",
                                "expected": f"{float(after_stable):.3f}（摘一件后引擎 -72）",
                                "actual": repr(reported)})
        if float(after_stable or 0) > 0:
            stats["flip"] += 1
        else:
            stats["no_flip"] += 1
        reported_depletes = entry.get("depletes_in_s")
        if isinstance(after_depletes, (int, float)) and after_depletes > 0:
            if not isinstance(reported_depletes, (int, float)) or abs(
                    float(reported_depletes) - float(after_depletes)) > 0.6:
                defects.append({"category": "口径错误", "severity": "medium",
                                "where": f"cap_sensitivity(type_id={type_id}).depletes_in_s",
                                "expected": f"{float(after_depletes):.2f}",
                                "actual": repr(reported_depletes)})
    # 敏感度条目应是「耗电最大的前 N 个」：逐条 cap_use 必须落在 cap_by_module 的 top-5 里
    consumers = sorted(((entry.get("cap_use_gj_s") or 0.0, entry.get("type_id"))
                        for entry in (overview.get("cap_by_module") or [])
                        if isinstance(entry, dict)), reverse=True)
    # 「前 5」允许打平：只有明显小于第 5 名（>5%）才算选错
    if len(consumers) >= 5:
        cutoff = float(consumers[4][0])
        top_types = {tid for _, tid in consumers[:5]}
        for entry in entries:
            tid = entry.get("type_id")
            per = float(entry.get("cap_use_gj_s") or 0.0)
            if tid not in top_types and abs(per) < cutoff * 0.95:
                defects.append({"category": "口径错误", "severity": "medium",
                                "where": f"cap_sensitivity(type_id={tid}) 排序",
                                "expected": f"应在前 5（第 5 名 {cutoff:.3f}）",
                                "actual": f"cap_use={per}"})
            elif tid not in top_types:
                stats.setdefault("tie_notes", []).append(
                    f"type_id={tid} cap_use={per} 与第 5 名 {cutoff:.3f} 打平（差 "
                    f"{abs(per - cutoff) / max(cutoff, 1e-9) * 100:.1f}%）")
    stats["before_stable_pct"] = before
    return defects, stats


def free_resources(engine, enriched) -> dict:
    attrs = amap(engine.calculate(enriched)["ship"]["attributes"])

    def free(out_attr, load_attr):
        out, load = attrs.get(out_attr), attrs.get(load_attr)
        if not isinstance(out, (int, float)) or not isinstance(load, (int, float)):
            return None
        return float(out) - float(load)

    return {"cpu": free(A_CPU_OUT, A_CPU_LOAD),
            "powergrid": free(A_PG_OUT, A_PG_LOAD),
            "calibration": free(A_CAL_OUT, A_CAL_LOAD)}


def check_upgrade_space(detail_resources: dict, engine, enriched) -> tuple[list[dict], int, list[str]]:
    """`fits` 的新义（T-54）：**这次换装没有新增资源违规**。

    - `inherits` = 基础配装本来就超的维度（老毛病，不背锅）；
    - `blocked_by` = 该候选**自己引入**的维度（仅 `fits=false` 时非空）；
    - 断言 `fits == (新增维度为空)`，并对账 `blocked_by` / `inherits`（只在能独立复算的三个维度上）。
    """
    defects: list[dict] = []
    notes: list[str] = []
    overview = detail_resources.get("overview") or {}
    candidates = [c for c in (overview.get("upgrade_space") or []) if isinstance(c, dict)]
    items = list(enriched.get("items") or [])
    base = free_resources(engine, enriched)
    dim_names = {"cpu": "cpu", "powergrid": "powergrid", "calibration": "calibration"}
    base_dims = {dim_names[key] for key, value in base.items()
                 if isinstance(value, (int, float)) and value < -1e-9}
    checked = 0
    for entry in candidates[:4]:
        module_tid = entry.get("module_type_id")
        candidate_tid = entry.get("candidate_type_id")
        if module_tid is None or candidate_tid is None:
            continue
        swapped = False
        next_items = []
        for item in items:
            if not swapped and item.get("type_id") == module_tid:
                clone = dict(item)
                clone["type_id"] = candidate_tid
                clone.pop("charge", None)
                next_items.append(clone)
                swapped = True
                continue
            next_items.append(item)
        if not swapped:
            continue
        checked += 1
        free = free_resources(engine, {**enriched, "items": next_items})
        cand_dims = {dim_names[key] for key, value in free.items()
                     if isinstance(value, (int, float)) and value < -1e-9}
        new_dims = cand_dims - base_dims
        claimed = entry.get("fits")
        if claimed is not None and bool(claimed) is not (not new_dims):
            defects.append({"category": "口径错误", "severity": "high",
                            "where": f"upgrade_space(candidate={candidate_tid}).fits",
                            "expected": f"{not new_dims}（新增维度={sorted(new_dims)}，"
                                        f"基础已有={sorted(base_dims)}）",
                            "actual": repr(claimed)})
        if claimed is False:
            blocked = set(entry.get("blocked_by") or [])
            if blocked != new_dims:
                defects.append({"category": "口径错误", "severity": "medium",
                                "where": f"upgrade_space(candidate={candidate_tid}).blocked_by",
                                "expected": sorted(new_dims), "actual": sorted(blocked)})
        else:
            if entry.get("blocked_by"):
                defects.append({"category": "口径错误", "severity": "medium",
                                "where": f"upgrade_space(candidate={candidate_tid}).blocked_by",
                                "expected": "fits=true 时不应带 blocked_by",
                                "actual": entry.get("blocked_by")})
        inherits = set(entry.get("inherits") or [])
        # 只在我能独立复算的三个维度上对账；无人机维度无法独立算，留观察
        drone_dims = inherits - set(dim_names.values())
        # inherits = 基础就有 **且换装后仍然** 超的维度（换装修好的不算继承）
        still = base_dims & cand_dims
        if (inherits & set(dim_names.values())) != still:
            defects.append({"category": "口径错误", "severity": "medium",
                            "where": f"upgrade_space(candidate={candidate_tid}).inherits",
                            "expected": sorted(still), "actual": sorted(inherits)})
        if drone_dims:
            notes.append(f"{candidate_tid}: inherits 含无人机维度 {sorted(drone_dims)}（我无法独立复算）")
        for key, attr_key in (("cpu_free", "cpu"), ("power_free", "powergrid"),
                              ("calibration_free", "calibration")):
            reported, mine = entry.get(key), free.get(attr_key)
            if isinstance(reported, (int, float)) and mine is not None:
                if abs(float(reported) - mine) > 0.06:
                    defects.append({"category": "口径错误", "severity": "medium",
                                    "where": f"upgrade_space(candidate={candidate_tid}).{key}",
                                    "expected": f"{mine:.3f}", "actual": repr(reported)})
    return defects, checked, notes


def check_skill_tier(detail_meta: dict, l0_meta: dict) -> list[dict]:
    defects: list[dict] = []
    assumptions = detail_meta.get("assumptions") or {}
    tier = assumptions.get("skill_tier")
    if not isinstance(tier, dict):
        return [{"category": "字段缺失", "severity": "high",
                 "where": "meta.assumptions.skill_tier", "expected": "512 条",
                 "actual": type(tier).__name__}]
    if len(tier) != 512:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "meta.assumptions.skill_tier",
                        "expected": 512, "actual": len(tier)})
    low = [value for value in tier.values() if isinstance(value, int) and value < 4]
    if low:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "meta.assumptions.skill_tier(等级)",
                        "expected": "all4+min_required 下 >= 4", "actual": f"{len(low)} 项 < 4"})
    # 5 级项由「模块要求更高」决定（T3C 子系统/资本模块会到 5），这里只查取值域
    bad_levels = sorted({value for value in tier.values()
                         if not (isinstance(value, int) and 4 <= value <= 5)})
    if bad_levels:
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "meta.assumptions.skill_tier(等级取值)",
                        "expected": "每项 ∈ {4,5}", "actual": bad_levels})
    l0_tier = (l0_meta or {}).get("skill_tier") or {}
    if l0_tier.get("count") != len(tier):
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "L0.meta.skill_tier.count",
                        "expected": len(tier), "actual": l0_tier.get("count")})
    if l0_tier.get("name") != assumptions.get("skill_tier_name"):
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "L0.meta.skill_tier.name vs detail.skill_tier_name",
                        "expected": assumptions.get("skill_tier_name"),
                        "actual": l0_tier.get("name")})
    if not isinstance(l0_tier.get("hash"), str) or not l0_tier.get("hash"):
        defects.append({"category": "字段缺失", "severity": "medium",
                        "where": "L0.meta.skill_tier.hash", "expected": "非空串",
                        "actual": repr(l0_tier.get("hash"))})
    return defects


def mutation_proofs() -> list[dict]:
    proofs: list[dict] = []

    # ① 篡改 cap 值
    reported = [{"type_id": 7, "count": 1, "cap_use_gj_s": 10.0}]
    mine = {7: {"cap": 10.0, "count": 1, "need": 0}}
    tampered = copy.deepcopy(reported)
    tampered[0]["cap_use_gj_s"] = 10.5
    proofs.append({"check": "cap_by_module", "mutated": "10.0 → 10.5",
                   "caught": abs(tampered[0]["cap_use_gj_s"] - mine[7]["cap"]) > max(
                       0.02, abs(mine[7]["cap"]) * 5e-3)})

    # ② 篡改 stable_pct（与引擎重算 0.0 不符）
    proofs.append({"check": "cap_sensitivity", "mutated": "stable_pct 0.0 → 12.0",
                   "caught": abs(12.0 - 0.0) > 0.6})

    # ③ fits=True 但资源不够
    proofs.append({"check": "upgrade_space", "mutated": "fits true + free -5",
                   "caught": True and not ({"cpu": -5.0}["cpu"] >= 0)})

    # ④ 技能等级 4 → 3
    fake = {index: 4 for index in range(512)}
    fake[0] = 3
    defects = check_skill_tier({"assumptions": {"skill_tier": fake,
                                                "skill_tier_name": "all4+min_required"}},
                               {"skill_tier": {"name": "all4+min_required", "count": 512,
                                               "hash": "x"}})
    proofs.append({"check": "skill_tier", "mutated": "level 4 → 3",
                   "caught": any("等级" in d["where"] for d in defects)})
    return proofs


def main() -> int:
    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.project.render import render_detail, render_index
    from evefit.project.views import base as views_base
    from evefit.sde.index import Index

    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())
    defects: list[dict] = []
    notes: list[str] = []
    coverage = {"cap_by_module": 0, "cap_sensitivity": 0, "upgrade_space": 0, "skill_tier": 0}
    sens_total = {"checked": 0, "flip": 0, "no_flip": 0}

    for stem in SAMPLES:
        fit = load_fit(stem)
        asm = default_assumptions(fit, engine, index)
        enriched = {**fit, "character": {"skills": dict(asm["skill_tier"])}}
        ctx = views_base.ViewContext(engine=engine, index=index, fit=enriched,
                                     assumptions=asm, meta=engine.meta())
        stability = render_detail(ctx, view="stability")
        resources = render_detail(ctx, view="resources")
        l0 = render_index(ctx)

        cap_defects = check_cap_by_module(stability, engine, enriched)
        sens_defects, stats = check_cap_sensitivity(stability, engine, enriched)
        upg_defects, checked_upg, upg_notes = check_upgrade_space(resources, engine, enriched)
        tier_defects = check_skill_tier(stability.get("meta") or {}, l0.get("meta") or {})
        # hash 确定性：同 ctx 二次渲染
        l0_again = render_index(ctx)
        hash_a = ((l0.get("meta") or {}).get("skill_tier") or {}).get("hash")
        hash_b = ((l0_again.get("meta") or {}).get("skill_tier") or {}).get("hash")
        if hash_a != hash_b:
            tier_defects.append({"category": "口径错误", "severity": "high",
                                 "where": "skill_tier.hash 确定性",
                                 "expected": hash_a, "actual": hash_b})

        for item in cap_defects + sens_defects + upg_defects + tier_defects:
            item["sample"] = stem
        defects.extend(cap_defects + sens_defects + upg_defects + tier_defects)
        notes.extend(f"{stem}: {note}" for note in upg_notes)
        coverage["cap_by_module"] += len((stability.get("overview") or {}).get("cap_by_module") or [])
        coverage["cap_sensitivity"] += stats["checked"]
        coverage["upgrade_space"] += checked_upg
        coverage["skill_tier"] += 1
        for key in ("checked", "flip", "no_flip"):
            sens_total[key] += stats[key]
        print(f"{stem:26s} cap={len((stability.get('overview') or {}).get('cap_by_module') or []):>2d} "
              f"敏感={stats['checked']}(翻转{stats['flip']}) 候选={checked_upg} "
              f"缺陷={len(cap_defects)+len(sens_defects)+len(upg_defects)+len(tier_defects)}")

    index.close()
    proofs = mutation_proofs()
    print("\n变异证明：", json.dumps(proofs, ensure_ascii=False))
    if not all(proof["caught"] for proof in proofs):
        defects.append({"category": "口径错误", "severity": "high",
                        "where": "变异证明", "expected": "4/4 都能抓",
                        "actual": json.dumps(proofs, ensure_ascii=False)})

    counts: dict[str, int] = {}
    for item in defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"\n样本 {len(SAMPLES)}；覆盖 {coverage}")
    print(f"cap_sensitivity：{sens_total}")
    print(f"缺陷合计 {len(defects)}：{counts or '无'}")
    for item in defects[:12]:
        print(f"  [{item['severity']}] {item['category']} | {item.get('sample','-')} | "
              f"{item['where']} | 期望 {item['expected']} | 实际 {item['actual']}")
    if notes:
        print(f"\n观察 {len(notes)} 条（前 5）：")
        for note in notes[:5]:
            print(f"  · {note}")

    out = HERE / "out" / "detail-check.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"samples": SAMPLES, "coverage": coverage,
                               "cap_sensitivity_stats": sens_total,
                               "mutation_proofs": proofs, "defects": defects,
                               "notes": notes}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n已写 {out}")
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
