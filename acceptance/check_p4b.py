"""P4 增量独立验收（修正版）：hardpoints 三态 / EFT 往返 / zkb_stats。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(HERE))

RLML = "Rapid Light Missile Launcher II"
#: group_id（`Index.types_in_group` 要 id，不是名字）
GROUP_IDS = (25, 420, 26, 419, 1201, 27, 358, 540, 963, 832, 906, 833, 541, 894)


def fitted_set(fit_struct: dict, *, with_charge: bool) -> list[tuple]:
    out = []
    for item in (fit_struct or {}).get("items") or []:
        slot = (item.get("slot") or {}).get("type")
        if slot in ("cargo", "implant", "booster"):
            continue
        charge = (item.get("charge") or {}).get("type_id")
        row = (str(slot), int(item.get("type_id") or 0))
        out.append(row + ((int(charge),) if with_charge and charge else (None,)) if with_charge else row)
    return sorted(out)


def main() -> int:
    from evefit import api
    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.sde.index import Index

    defects: list[dict] = []
    notes: list[str] = []

    def defect(category: str, severity: str, where: str, expected: str, actual: str) -> None:
        defects.append({"category": category, "severity": severity, "where": where,
                        "expected": expected, "actual": actual})

    index = Index.open(paths_mod.db_path())
    engine = adapter.Engine()

    def base_attrs(type_id: int) -> dict:
        bare = {"ship": {"type_id": type_id}, "items": []}
        asm = default_assumptions(bare, engine, index)
        calc = engine.calculate({**bare, "character": {"skills": dict(asm["skill_tier"])}})
        return {int(k): (v["value"] if isinstance(v, dict) else v)
                for k, v in calc["ship"]["attributes"].items()}

    # ---------------- A. 找载体 ----------------
    print("=" * 78)
    print("A. hardpoints 三态")
    hull = None
    scanned = 0
    for group_id in GROUP_IDS:
        try:
            rows = index.types_in_group(group_id)
        except Exception:  # noqa: BLE001
            continue
        for row in rows:
            scanned += 1
            attrs = base_attrs(row["type_id"])
            if attrs.get(101) == 3 and (attrs.get(14) or 0) >= 5:
                hull = (row["name"], row["type_id"], attrs.get(14))
                break
        if hull:
            break
    print(f"  扫描 {scanned} 艘船；结果：{hull or '没有 launcher=3 且高槽≥5 的船'}")

    if hull is None:
        # 退化验证：用 launcher=3 的船证明「记账正确」，再说明「5 门超装」无载体
        notes.append("SDE 中没有 launcher 基准=3 且高槽≥5 的船："
                     "「装 5 门 → free=−2」这个用例在该数据集上无载体（不是实现缺陷）")
        for name in ("Arbitrator", "Blackbird"):
            row = next((r for r in index.search_types(name, 3) if r["name"] == name), None)
            if row:
                hull = (name, row["type_id"], base_attrs(row["type_id"]).get(14))
                break
    if hull:
        hi_slots = int(hull[2] or 0)
        counts = [copy for copy in (0, 3, 5) if copy <= hi_slots] + [hi_slots] if hi_slots else [0, 3]
        for count in sorted(set(counts)):
            eft = f"[{hull[0]}, hardpoints-{count}]\n" + "\n".join([RLML] * count) + "\n"
            result = api.fit_index(eft)
            inner = result.get("result") or {}
            launcher = ((inner.get("resources") or {}).get("hardpoints") or {}).get("launcher") or {}
            flags = [f.get("id") for f in (inner.get("flags") or [])]
            want_free = 3 - count
            print(f"  {hull[0]} 装 {count} 门 → total={launcher.get('total')} "
                  f"used={launcher.get('used')} free={launcher.get('free')} "
                  f"violation={'hardpoint_violation' in flags}")
            if (launcher.get("total"), launcher.get("used"), launcher.get("free")) != (
                    3, count, want_free):
                defect("口径错误", "high", f"hardpoints(装 {count} 门)",
                       f"total=3 used={count} free={want_free}",
                       f"{launcher.get('total')}/{launcher.get('used')}/{launcher.get('free')}")
            if count > 3 and "hardpoint_violation" not in flags:
                defect("字段缺失", "high", "hardpoints(超装 4 门)",
                       "hardpoint_violation flag", f"flags={flags}")
            if count == 0 and "hardpoint_violation" in flags:
                defect("口径错误", "medium", "hardpoints(装 0 门)",
                       "不超装时不应出 flag", f"flags={flags}")
        # 说明：高槽 4 时最多 4 门 → free 最小 −1；要 −2 需要高槽≥5 的载体
    print()

    # ---------------- B. EFT 往返 ----------------
    print("=" * 78)
    print("B. parse_fit().result.eft 往返")
    samples = {
        "f1（带弹药）": (HERE / "fixtures" / "f1-hecate-killmail.eft").read_text(encoding="utf-8"),
        "空名配装": "[Hecate]\n\n",
    }
    for label, text in samples.items():
        first = api.parse_fit(text)
        inner1 = first.get("result") or {}
        eft_out = inner1.get("eft")
        if not isinstance(eft_out, str):
            defect("字段缺失", "high", f"eft 往返({label})", "result.eft 存在", repr(eft_out)[:60])
            continue
        second = api.parse_fit(eft_out)
        inner2 = second.get("result") or {}
        modules_only = (fitted_set(inner1.get("fit") or {}, with_charge=False)
                        == fitted_set(inner2.get("fit") or {}, with_charge=False))
        with_charge = (fitted_set(inner1.get("fit") or {}, with_charge=True)
                       == fitted_set(inner2.get("fit") or {}, with_charge=True))
        print(f"  {label}: eft 长度={len(eft_out)} 模块集合一致={modules_only} "
              f"含弹药一致={with_charge}")
        if not modules_only:
            defect("口径错误", "high", f"eft 往返({label})", "已装模块集合一致",
                   "emit→parse 后模块集合不同")
        if not with_charge:
            defect("口径错误", "medium", f"eft 往返({label})",
                   "含弹药也一致（装填弹药随模块走）",
                   "emit 把弹药放进货舱（模块行不带 ', Ammo'）→ 再 parse 时装填丢失")
    emitted = (api.parse_fit(samples["f1（带弹药）"])["result"])["eft"]
    print("  证据：emit 出的模块行 =",
          [line for line in emitted.splitlines() if "Blaster" in line][:2])
    print()

    # ---------------- C. zkb_stats ----------------
    print("=" * 78)
    print("C. zkb_stats")

    def requester(payload):
        return lambda url, digest=None: payload

    shapes = {'{"error": ...}': {"error": "not found"},
              "0 字节空体": b"",
              "空壳实体": {}}
    seen: dict[str, str] = {}
    for label, payload in shapes.items():
        try:
            result = api.zkb_stats(12345, entity_type="character", requester=requester(payload))
            error = result.get("error") or {}
            seen[label] = f"{result.get('ok')}/{error.get('kind')}/{error.get('message')}"
            print(f"  {label:16s} → ok={result.get('ok')} kind={error.get('kind')} "
                  f"message={str(error.get('message'))[:40]!r}")
            if result.get("ok") is True:
                defect("口径错误", "high", f"zkb_stats({label})", "ok=false", "true")
        except Exception as exc:  # noqa: BLE001
            seen[label] = f"异常 {type(exc).__name__}"
            defect("口径错误", "high", f"zkb_stats({label})", "返回信封", f"抛 {type(exc).__name__}")
    print(f"  三种形态可区分：{len(set(seen.values())) == len(seen)}  {seen}")

    # danger_ratio 转述（用能区分「原样 / 取整」的值 12.5）
    canned = {"dangerRatio": 12.5, "gangRatio": 45.5, "soloRatio": 33.3,
              "shipsDestroyed": 10, "info": {"id": 1, "name": "X", "secStatus": 0.5},
              "topLists": []}
    try:
        result = api.zkb_stats(1, entity_type="character", requester=requester(canned))
        style = (result.get("result") or {}).get("style") or {}
        print(f"  danger_ratio={style.get('danger_ratio')!r} "
              f"gang_ratio={style.get('gang_ratio')!r}")
        if style.get("danger_ratio") != 12.5:
            defect("口径错误", "high", "zkb_stats(danger_ratio)",
                   "12.5（只转述，不取整/不换算）", repr(style.get("danger_ratio")))
    except Exception as exc:  # noqa: BLE001
        notes.append(f"danger_ratio 用例异常：{type(exc).__name__}: {exc}")

    # 体量：真实实体（网络可用时）
    for entity_id in (1633215779, 90000001, 1):
        try:
            real = api.zkb_stats(entity_id, entity_type="character")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"真实实体 {entity_id} 调用异常：{type(exc).__name__}")
            continue
        if real.get("ok") is True:
            size = len(json.dumps(real, ensure_ascii=False).encode("utf-8"))
            print(f"  真实实体 {entity_id}：序列化 {size} bytes（上限 4096）")
            if size > 4096:
                defect("口径错误", "high", "zkb_stats(体量)", "≤ 4096 bytes", f"{size}")
            break
        notes.append(f"真实实体 {entity_id} ok=false（可能是实体不存在）")
    else:
        notes.append("真实实体体量未验：没有可用的实体 id")
    index.close()

    print()
    print("=" * 78)
    counts: dict[str, int] = {}
    for item in defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"缺陷合计 {len(defects)}：{counts or '无'}")
    for item in defects[:10]:
        print(f"  [{item['severity']}] {item['category']} | {item['where']}"
              f" | 期望 {item['expected']} | 实际 {item['actual']}")
    if notes:
        print(f"未验/观察 {len(notes)} 条：")
        for note in notes[:6]:
            print(f"  · {note}")
    out_path = HERE / "out" / "p4b-check.json"
    out_path.write_text(json.dumps({"defects": defects, "notes": notes,
                                    "zkb_shapes": seen}, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\n已写 {out_path}")
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
