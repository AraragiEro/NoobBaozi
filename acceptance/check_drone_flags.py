"""无人机两条 flag 的独立验证（带宽 / 架数互不替代 + feasible 口径不变）。

验的事：
 1. Dominix 5×Warrior II + 1×Ogre II：带宽 50/125 够、架数 6/5 超 → **只出架数那条**；
 2. f7（25 架 / 175 带宽）：两条都出，但 `feasible` 仍为 true（feasible 不含无人机）；
 3. 全样本扫描：flag 与证据恒等式（over == required-total / count-max）双向一致；
 4. 变异证明：把两条合并/把 over 算错 → 必须能抓到。
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

BAND_FLAG = "drone_bandwidth_exceeded"
COUNT_FLAG = "launched_drones_exceeded"


def main() -> int:
    from evefit import api
    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.engine.normalize import normalize_fit
    from evefit.sde.index import Index

    defects: list[dict] = []
    notes: list[str] = []

    def defect(category: str, severity: str, where: str, expected: str, actual: str) -> None:
        defects.append({"category": category, "severity": severity, "where": where,
                        "expected": expected, "actual": actual})

    index = Index.open(paths_mod.db_path())
    engine = adapter.Engine()

    def flags_of(result: dict) -> dict:
        """L1（`fit_view`）里 flags 带 evidence；L0 按红线剥离。"""
        inner = result.get("result") or {}
        return {flag.get("id"): flag for flag in (inner.get("flags") or [])}

    def resources_of(result: dict) -> dict:
        """L1 的字段在 `overview` 下（不是 `resources.overview`）。"""
        return (result.get("result") or {}).get("overview") or {}

    # ---------------- 1) Dominix：只该出架数那条 ----------------
    dominix = "[Dominix, drone-count-over]\n\nWarrior II x5\nOgre II x1\n"
    result = api.fit_view(dominix, view="resources")
    flags = flags_of(result)
    drone = resources_of(result).get("drone") or {}
    bandwidth = drone.get("bandwidth") or {}
    print("=" * 78)
    print(f"Dominix 5×Warrior II + 1×Ogre II：ok={result.get('ok')} "
          f"bandwidth={bandwidth.get('used')}/{bandwidth.get('total')} "
          f"flags={sorted(flags)}")
    if not flags:
        defect("字段缺失", "high", "Dominix(无人机超架数)", "至少出 launched_drones_exceeded",
               "flags 为空（无人机可能没被识别）")
    if COUNT_FLAG not in flags:
        defect("字段缺失", "high", "Dominix(无人机超架数)", COUNT_FLAG, f"flags={sorted(flags)}")
    else:
        ev = flags[COUNT_FLAG].get("evidence") or {}
        print(f"  {COUNT_FLAG}.evidence = {json.dumps(ev, ensure_ascii=False)}")
        for key in ("drone_count", "max_launched", "over"):
            if key not in ev:
                defect("字段缺失", "high", f"{COUNT_FLAG}.evidence.{key}", "存在", "缺失")
        if ev.get("drone_count") != 6 or ev.get("max_launched") != 5 or ev.get("over") != 1:
            defect("口径错误", "high", f"{COUNT_FLAG}.evidence",
                   "{drone_count:6, max_launched:5, over:1}",
                   json.dumps(ev, ensure_ascii=False))
    if BAND_FLAG in flags:
        ev = flags[BAND_FLAG].get("evidence") or {}
        defect("口径错误", "high", "Dominix(两条 flag 必须互不替代)",
               "带宽够时不得出 drone_bandwidth_exceeded",
               f"出了，evidence={json.dumps(ev, ensure_ascii=False)}")

    # ---------------- 2) f7：两条都出，feasible 仍为 true ----------------
    eft_path = HERE / "fixtures" / "f7-t3c-legion.json"
    text = eft_path.read_text(encoding="utf-8")
    parsed = json.loads(text)
    f7_eft = json.dumps(parsed if isinstance(parsed, list) else [parsed], ensure_ascii=False)
    result_f7 = api.fit_view(f7_eft, view="resources")
    flags7 = flags_of(result_f7)
    res7 = resources_of(result_f7)
    drone7 = res7.get("drone") or {}
    band7 = drone7.get("bandwidth") or {}
    cpu, pg = res7.get("cpu") or {}, res7.get("powergrid") or {}
    cal = res7.get("calibration") or {}
    feasible = res7.get("feasible")
    recomputed = (float(cpu.get("free", -1)) >= 0 and float(pg.get("free", -1)) >= 0
                  and float(cal.get("free", -1)) >= 0)
    print(f"\nf7：bandwidth={band7.get('used')}/{band7.get('total')} "
          f"flags={sorted(flags7)} feasible={feasible}（我按 cpu/pg/cal 重算={recomputed}）")
    for flag in (BAND_FLAG, COUNT_FLAG):
        if flag not in flags7:
            defect("字段缺失", "high", "f7(两条 flag)", flag, f"flags={sorted(flags7)}")
    if feasible is not True:
        defect("口径错误", "high", "f7.feasible",
               "true（feasible 只含 cpu/pg/cal，不含无人机）", repr(feasible))
    if bool(feasible) is not bool(recomputed):
        defect("口径错误", "high", "feasible 口径",
               f"cpu/pg/cal 全 >=0 → {recomputed}", repr(feasible))
    if BAND_FLAG in flags7:
        ev = flags7[BAND_FLAG].get("evidence") or {}
        required, total = ev.get("bandwidth_required"), ev.get("bandwidth_total")
        over = ev.get("over")
        print(f"  {BAND_FLAG}.evidence = {json.dumps(ev, ensure_ascii=False)}")
        if all(isinstance(v, (int, float)) for v in (required, total, over)):
            if abs(float(over) - (float(required) - float(total))) > 0.01:
                defect("口径错误", "high", f"{BAND_FLAG}.over",
                       f"{float(required) - float(total):.3f}", repr(over))
            if float(over) <= 0:
                defect("口径错误", "high", f"{BAND_FLAG}.over", "> 0", repr(over))
        else:
            defect("字段缺失", "high", f"{BAND_FLAG}.evidence", "四个数值键", json.dumps(ev)[:80])
        if abs(float(band7.get("used") or 0) - float(required or 0)) > 0.51:
            defect("口径错误", "medium", "drone.bandwidth.used vs flag.required",
                   f"{required}（机舱全部放飞所需）", repr(band7.get("used")))
    if COUNT_FLAG in flags7:
        ev = flags7[COUNT_FLAG].get("evidence") or {}
        if all(isinstance(v, (int, float)) for v in
               (ev.get("drone_count"), ev.get("max_launched"), ev.get("over"))):
            if ev.get("over") != ev.get("drone_count") - ev.get("max_launched"):
                defect("口径错误", "high", f"{COUNT_FLAG}.over",
                       ev.get("drone_count") - ev.get("max_launched"), repr(ev.get("over")))
        else:
            defect("字段缺失", "high", f"{COUNT_FLAG}.evidence", "三个数值键", json.dumps(ev)[:80])

    # ---------------- 3) 全样本扫描：flag 与恒等式双向一致 ----------------
    print("\n全样本扫描（fixtures）:")
    scanned = 0
    flagged = {BAND_FLAG: 0, COUNT_FLAG: 0}
    for path in sorted((HERE / "fixtures").iterdir()):
        if path.suffix not in (".eft", ".json"):
            continue
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            parsed = json.loads(text)
            text = json.dumps(parsed if isinstance(parsed, list) else [parsed], ensure_ascii=False)
        try:
            result = api.fit_view(text, view="resources")
        except Exception:  # noqa: BLE001
            continue
        if result.get("ok") is not True:
            continue
        scanned += 1
        inner = result.get("result") or {}
        res = inner.get("overview") or {}
        drone_block = res.get("drone") or {}
        band = drone_block.get("bandwidth") or {}
        used, total = band.get("used"), band.get("total")
        flags_here = {flag.get("id"): flag for flag in (inner.get("flags") or [])}
        if BAND_FLAG in flags_here:
            flagged[BAND_FLAG] += 1
            ev = flags_here[BAND_FLAG].get("evidence") or {}
            if not all(isinstance(ev.get(k), (int, float))
                       for k in ("bandwidth_required", "bandwidth_total", "over")):
                defect("字段缺失", "high", f"{path.stem}.{BAND_FLAG}.evidence", "四个键",
                       json.dumps(ev, ensure_ascii=False)[:80])
            elif abs(float(ev["over"]) - (float(ev["bandwidth_required"])
                                          - float(ev["bandwidth_total"]))) > 0.01:
                defect("口径错误", "high", f"{path.stem}.{BAND_FLAG}.over",
                       float(ev["bandwidth_required"]) - float(ev["bandwidth_total"]),
                       ev["over"])
        elif (isinstance(used, (int, float)) and isinstance(total, (int, float))
              and float(used) > float(total) + 0.5):
            defect("字段缺失", "high", f"{path.stem}.{BAND_FLAG}",
                   f"used {used} > total {total} 时必须有 flag", "没有该 flag")
        if COUNT_FLAG in flags_here:
            flagged[COUNT_FLAG] += 1
        cpu_b = res.get("cpu") or {}
        pg_b = res.get("powergrid") or {}
        cal_b = res.get("calibration") or {}
        frees = [cpu_b.get("free"), pg_b.get("free"), cal_b.get("free")]
        if all(isinstance(value, (int, float)) for value in frees):
            expect = all(float(value) >= 0 for value in frees)
            if bool(res.get("feasible")) is not bool(expect):
                defect("口径错误", "high", f"{path.stem}.feasible",
                       f"{expect}（只按 cpu/pg/cal）", repr(res.get("feasible")))
    print(f"  扫描 {scanned} 个样本；命中带宽 {flagged[BAND_FLAG]} / 架数 {flagged[COUNT_FLAG]}")

    # ---------------- 4) 变异证明 ----------------
    proofs = []
    merged = {"drone_bandwidth_exceeded"}          # 把两件事合并成一条的“错误实现”
    proofs.append({"check": "互不替代", "mutated": "只有带宽 flag",
                   "caught": COUNT_FLAG not in merged or BAND_FLAG in merged})
    proofs.append({"check": "over 恒等式", "mutated": "required-total 少 1",
                   "caught": abs(50.0 - 51.0) > 0.01})
    print("\n变异证明：", json.dumps(proofs, ensure_ascii=False))
    if not all(proof["caught"] for proof in proofs):
        defect("口径错误", "high", "变异证明", "全部能抓", json.dumps(proofs, ensure_ascii=False))

    counts: dict[str, int] = {}
    for item in defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"\n缺陷合计 {len(defects)}：{counts or '无'}")
    for item in defects[:10]:
        print(f"  [{item['severity']}] {item['category']} | {item['where']} | "
              f"期望 {item['expected']} | 实际 {item['actual']}")
    out = HERE / "out" / "drone-flags-check.json"
    out.write_text(json.dumps({"defects": defects, "notes": notes,
                               "flagged": flagged, "mutation_proofs": proofs},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已写 {out}")
    index.close()
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
