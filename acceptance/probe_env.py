"""环境探针：确认 QA 对 P0/P1 接口与引擎输出结构的理解（非验收本体，仅侦察）。

用法（PowerShell）：
    $env:UV_CACHE_DIR = "$PWD" + "\\noobbaozi\\spike\\uvcache"
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python noobbaozi/acceptance/probe_env.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # noobbaozi/
WORKSPACE = ROOT.parent
sys.path.insert(0, str(WORKSPACE))

from noobbaozi.config import paths as paths_mod          # noqa: E402


def attr_map(node: dict) -> dict[int, object]:
    """引擎 attributes 节点 → {attr_id: 裸值}。"""
    out: dict[int, object] = {}
    for key, value in (node or {}).items():
        out[int(key)] = value["value"] if isinstance(value, dict) else value
    return out


def main() -> int:
    from noobbaozi.engine import adapter
    from noobbaozi.engine.assumptions import default_assumptions
    from noobbaozi.engine.normalize import normalize_fit
    from noobbaozi.sde.index import Index

    print("=" * 70)
    print("1) 索引库")
    print("=" * 70)
    db = paths_mod.db_path()
    print(f"  db_path() -> {db}")
    with Index.open(db) as idx:
        print(f"  stats    -> {idx.stats()}")
        print(f"  search 'Hecate' -> {[(r['type_id'], r['name']) for r in idx.search_types('Hecate', 3)]}")
        print(f"  modes_for_ship(35683) -> {[(r['type_id'], r['name']) for r in idx.modes_for_ship(35683)]}")

    print()
    print("=" * 70)
    print("2) kill.json → 归一化 → EFT 文本（供 fixture）")
    print("=" * 70)
    km = json.loads((ROOT / "spike" / "kill.json").read_text(encoding="utf-8"))[0]
    km.pop("zkb", None)
    res = normalize_fit(json.dumps([km], ensure_ascii=False))
    print(f"  source_format={res['source_format']}  errors={res['errors']}  warnings={res['warnings']}")
    fit = res["fit"]
    engine = adapter.Engine()
    eft = engine.save_eft(fit)
    print("  --- EFT BEGIN ---")
    print(eft)
    print("  --- EFT END ---")

    print()
    print("=" * 70)
    print("3) 默认假设（技能档）")
    print("=" * 70)
    # index 必传：T-12 后不传会退回 min_required_only（旧档 19 技能）
    with Index.open(db) as idx_for_tier:
        asm = default_assumptions(fit, engine, idx_for_tier)
    tier = asm["skill_tier"]
    dist: dict[int, int] = {}
    for level in tier.values():
        dist[level] = dist.get(level, 0) + 1
    print(f"  skill_tier_name={asm['skill_tier_name']}  count={len(tier)}  dist={dict(sorted(dist.items()))}")
    if len(tier) <= 25:
        print(f"  skill_tier={dict(sorted(tier.items()))}")
    else:
        print(f"  skill_tier 前 10 项={dict(sorted(tier.items())[:10])} …（共 {len(tier)} 项，略）")
    print(f"  module_states={asm['module_states']}  mode_id={asm['mode_id']}")
    print(f"  meta={engine.meta()}")

    print()
    print("=" * 70)
    print("4) calculate 输出结构")
    print("=" * 70)
    calc = engine.calculate({**fit, "character": {"skills": tier}})
    print(f"  top-level keys: {sorted(calc.keys())}")
    print(f"  ship keys:      {sorted((calc.get('ship') or {}).keys())}")
    ship = attr_map(calc["ship"]["attributes"])
    print(f"  ship 属性个数:  {len(ship)}")
    print(f"  items 个数:     {len(calc['items'])}")
    if calc["items"]:
        it = calc["items"][0]
        print(f"  item[0] keys:   {sorted(it.keys())}")
        print(f"  item[0]: type_id={it.get('type_id')} state={it.get('state')} slot={it.get('slot')} "
              f"charge={it.get('charge')}")
        ia = attr_map(it["attributes"])
        print(f"  item[0] 属性个数: {len(ia)}")
        print(f"  item[0] 有 -12(dps)={-12 in ia} -21(volley)={-21 in ia} -3(cycle)={-3 in ia}")

    print()
    print("  关注的正/负 ID 值（ship 级）：")
    for aid in (263, 265, 9, -30, -28, -29, -43, -1, 37, 552, 4, 70, 600,
                48, 49, 11, 15, 1132, 1152, 14, 13, 12, 1137, 102, 101, 1271, 283, 38,
                482, 55, -72, -7, -4, -2, -5, -12, -11, -45, -48, -47, -50, -46, -49, -51, -52):
        print(f"    {aid:6d} = {ship.get(aid)!r}")

    print()
    print("=" * 70)
    print("5) 逐 item 属性（看武器字段在装备级）")
    print("=" * 70)
    for i, item in enumerate(calc["items"]):
        ia = attr_map(item.get("attributes") or {})
        interesting = {k: ia[k] for k in (-12, -13, -11, -21, -3, -22, 54, 158, 160)
                       if k in ia}
        print(f"  item[{i}] type={item.get('type_id')} slot={item.get('slot', {}).get('type')} "
              f"state={item.get('state')} charge={(item.get('charge') or {}).get('type_id')} -> {interesting}")

    print()
    print("=" * 70)
    print("6) validate 的 violations 结构")
    print("=" * 70)
    violations = engine.validate(fit)          # 不带技能 → 应报缺技能
    print(f"  违规条数={len(violations)}")
    if violations:
        print(f"  violations[0] keys={sorted(violations[0].keys())}")
        print(f"  violations[0]={json.dumps(violations[0], ensure_ascii=False)[:400]}")

    print()
    print("=" * 70)
    print("7) 坏输入的 NormalizeError 结构（I7 用）")
    print("=" * 70)
    bad = normalize_fit("[Hecate, 坏配装]\n不存在的模块名字\n")
    print(f"  source_format={bad['source_format']} fit={bad['fit']}")
    print(f"  errors={json.dumps(bad['errors'], ensure_ascii=False)}")
    empty = normalize_fit("")
    print(f"  空输入: source_format={empty['source_format']} errors={json.dumps(empty['errors'], ensure_ascii=False)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
