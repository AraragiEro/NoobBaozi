"""hardpoints 分类正确性 + zkb_stats 真实体量（用 Lead 给的有效实体 id）。

分类矩阵（Lead 指定）：
  隐身 / 智能炸弹 / 吸电 / 遥修 / 牵引 / 探针发射器 → `non_hardpoint`
  `Festival Launcher` → `unclassified`
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))

#: (标签, 模块名, 期望落点)
CASES = [
    ("隐身", "Improved Cloaking Device II", "non_hardpoint"),
    ("智能炸弹", "Small EMP Smartbomb II", "non_hardpoint"),
    ("吸电", "Small Ghoul Compact Energy Nosferatu", "non_hardpoint"),
    ("遥修", "Large I-ax Enduring Remote Armor Repairer", "non_hardpoint"),
    ("牵引", "Small Tractor Beam II", "non_hardpoint"),
    ("探针发射器", "Expanded Probe Launcher I", "non_hardpoint"),
    ("节日发射器", "Festival Launcher", "unclassified"),
]

HULLS = ["Megathron", "Dominix", "Armageddon", "Raven", "Apocalypse"]


def main() -> int:
    from evefit import api
    from evefit.config import paths as paths_mod
    from evefit.sde.index import Index

    defects: list[dict] = []
    notes: list[str] = []

    def defect(category: str, severity: str, where: str, expected: str, actual: str) -> None:
        defects.append({"category": category, "severity": severity, "where": where,
                        "expected": expected, "actual": actual})

    index = Index.open(paths_mod.db_path())

    def exists(name: str) -> bool:
        return any(r["name"] == name for r in index.search_types(name, 5))

    hull = next((name for name in HULLS if exists(name)), None)
    print("=" * 78)
    print(f"分类矩阵：hull={hull}")
    if hull is None:
        defect("口径错误", "high", "hardpoints 分类", "找到一艘高槽充足的船", "未找到")
        index.close()
        return 1

    for label, module, expected in CASES:
        if not exists(module):
            notes.append(f"模块不存在：{module}")
            print(f"  {label:8s} {module:44s} ⚠️ 模块不存在，跳过")
            continue
        eft = f"[{hull}, cls-{label}]\n{module}\n"
        result = api.fit_index(eft)
        inner = result.get("result") or {}
        hardpoints = ((inner.get("resources") or {}).get("hardpoints") or {})
        non_hp = hardpoints.get("non_hardpoint")
        modules = hardpoints.get("non_hardpoint_modules") or []
        unclassified = hardpoints.get("unclassified")
        flags = [f.get("id") for f in (inner.get("flags") or [])]
        got = None
        if isinstance(modules, list) and any(module in str(m) for m in modules):
            got = "non_hardpoint"
        elif isinstance(unclassified, int) and unclassified >= 1:
            got = "unclassified"
        print(f"  {label:8s} {module:44s} → non_hardpoint={non_hp} unclassified={unclassified} "
              f"modules={str(modules)[:60]} {'✅' if got == expected else '❌'}")
        if got != expected:
            defect("口径错误", "high", f"hardpoints 分类({label})", expected,
                   f"non_hardpoint={non_hp} unclassified={unclassified} "
                   f"modules={str(modules)[:80]} flags={flags}")
    print()

    # ---------------- zkb_stats 真实体量 ----------------
    print("=" * 78)
    print("zkb_stats 真实实体（体量 ≤ 4 KB + ratio 透传）")
    ENTITIES = [("character", 2117515414, "SixtyEverSnake"),
                ("corporation", 98821127, "The big boys"),
                ("alliance", 1354830081, "Goonswarm Federation")]
    import time

    for entity_type, entity_id, name in ENTITIES:
        result = None
        for attempt in range(3):
            try:
                result = api.zkb_stats(entity_id, entity_type=entity_type)
            except Exception as exc:  # noqa: BLE001
                notes.append(f"{name} 调用异常({attempt}): {type(exc).__name__}: {exc}")
                time.sleep(3 * (attempt + 1))
                continue
            if result.get("ok") is True:
                break
            kind = (result.get("error") or {}).get("kind")
            if kind == "internal_error" and attempt < 2:
                time.sleep(5 * (attempt + 1))       # 疑似限流，退避重试
                continue
            break
        if result is None:
            print(f"  {name:20s} 三次调用都异常")
            continue
        ok = result.get("ok")
        size = len(json.dumps(result, ensure_ascii=False).encode("utf-8"))
        style = (result.get("result") or {}).get("style") or {}
        print(f"  {name:20s} ok={ok} 序列化={size} bytes danger_ratio={style.get('danger_ratio')!r}")
        if ok is not True:
            error = result.get("error") or {}
            print(f"     错误详情：kind={error.get('kind')} message={str(error.get('message'))[:80]}")
            notes.append(f"{name} ok=false：{error.get('kind')} / {str(error.get('message'))[:60]}")
            continue
        if size > 4096:
            defect("口径错误", "high", f"zkb_stats 体量({name})", "≤ 4096 bytes", f"{size}")
        ratio = style.get("danger_ratio")
        if isinstance(ratio, float) and ratio == int(ratio):
            notes.append(f"{name}: danger_ratio={ratio}（整数，可能本身就是整数）")
    index.close()

    print()
    print("=" * 78)
    counts: dict[str, int] = {}
    for item in defects:
        counts[item["category"]] = counts.get(item["category"], 0) + 1
    print(f"缺陷合计 {len(defects)}：{counts or '无'}")
    for item in defects[:8]:
        print(f"  [{item['severity']}] {item['where']} | 期望 {item['expected']} | 实际 {item['actual']}")
    if notes:
        print(f"观察/未验 {len(notes)} 条：")
        for note in notes[:6]:
            print(f"  · {note}")
    out = HERE / "out" / "hardpoints-classes.json"
    out.write_text(json.dumps({"defects": defects, "notes": notes}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    print(f"\n已写 {out}")
    return 0 if not defects else 1


if __name__ == "__main__":
    raise SystemExit(main())
