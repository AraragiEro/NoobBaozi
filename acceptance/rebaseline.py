"""快照重标定工具（T-12 之后使用）。

用途：`derive_skill_tier` 修好之后，一次性产出
  1. `开发规范 §6.2` 主样本 22 行新值（markdown，可直接粘进文档）
  2. 模式快照 4 行新值（Defense EHP / Propulsion 对齐 / Sharpshooter DPS）
  3. 旧值 → 新值 diff（供 `docs/回归快照-changelog.md`）
  4. fixtures 期望值是否漂移的信号（`--rewrite-fixtures` 才真的重写 `.expected.json`）

**为什么默认拒绝运行**：Lead 裁决「先修实现再重标，别标两遍」。
本脚本用启发式自检挡住"还没修就重标"：

    T-12 前：默认档只有 19 个技能（只补被模块要求的）
    T-12 后：默认档应为「SDE 内全部技能 = 4」起步（数百个）

技能数 < `EXPECTED_MIN_SKILLS` 时直接罢工，除非显式 `--force`。

用法：
    $env:UV_CACHE_DIR = (Join-Path $PWD "noobbaozi/spike/uvcache")
    $PY = 'uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python'

    Invoke-Expression "$PY noobbaozi/acceptance/rebaseline.py"                    # 自检后罢工/出表
    Invoke-Expression "$PY noobbaozi/acceptance/rebaseline.py --rewrite-fixtures" # 同时重写 fixtures
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT_DIR = HERE / "out"
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(ROOT.parent))

#: T-12 修好后默认技能档至少应该有这么多技能（SDE 全部技能 = 4 级）。
#: 当前（未修）主样本只有 19 个 → 用来挡住"还没修就重标"。
EXPECTED_MIN_SKILLS = 100

#: 旧值（文档现值），用于输出 changelog 的 旧→新 diff
OLD_SNAPSHOT: tuple[tuple[str, int | str, float], ...] = (
    ("分层 HP / shield", 263, 650.0),
    ("分层 HP / armor", 265, 840.0),
    ("分层 HP / hull", 9, 2343.75),
    ("混合 EHP", -43, 8854.96),
    ("四系 EHP / EM", "pure:em", 8549.6),
    ("四系 EHP / EXP", "pure:explosive", 8414.0),
    ("四系 EHP / KIN", "pure:kinetic", 11347.1),
    ("四系 EHP / THM", "pure:thermal", 9095.5),
    ("护甲修复 raw", -45, 32.5),
    ("护甲修复 effective", -48, 69.126),
    ("被动盾回充 raw", -51, 2.031),
    ("被动盾回充 effective", -52, 3.869),
    ("CPU 输出", 48, 276.0),
    ("CPU 占用", 49, 287.0),
    ("PG 输出", 11, 64.8),
    ("PG 占用", 15, 72.0),
    ("标定 输出", 1132, 400.0),
    ("标定 占用", 1152, 225.0),
    ("MWD active 速度", 37, 1059.68),
    ("MWD active 信号半径", 552, 385.0),
    ("MWD active 对齐", -1, 11.861),
    ("DPS", -12, 365.59),
)

OLD_MODES: tuple[tuple[str, int | None, float, float, float], ...] = (
    ("无", None, 4530.93, 47.681, 8.035),
    ("Defense", 35686, 6254.72, 47.681, 8.035),
    ("Propulsion", 35687, 4530.93, 47.681, 2.678),
    ("Sharpshooter", 35688, 4530.93, 63.575, 8.035),
)

SINGLE_GUN_EFT = "[Hecate, mode snapshot]\nLight Neutron Blaster II, Void S\n"

RESONANCES = {
    "shield": {"em": 271, "explosive": 272, "kinetic": 273, "thermal": 274},
    "armor": {"em": 267, "explosive": 268, "kinetic": 269, "thermal": 270},
    "hull": {"em": 113, "explosive": 111, "kinetic": 109, "thermal": 110},
}
LAYER_HP = {"shield": 263, "armor": 265, "hull": 9}


def attr_map(node: dict | None) -> dict[int, float | None]:
    return {
        int(k): (v["value"] if isinstance(v, dict) else v)
        for k, v in (node or {}).items()
    }


def pure_ehp(ship: dict[int, float | None]) -> dict[str, float]:
    return {
        damage: math.fsum(
            float(ship[LAYER_HP[layer]]) / float(ship[attrs[damage]])  # type: ignore[index]
            for layer, attrs in RESONANCES.items()
        )
        for damage, _ in RESONANCES["shield"].items()
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="快照重标定（T-12 之后）")
    parser.add_argument("--force", action="store_true",
                        help="即使技能档自检不通过也出表（仅用于预演格式）")
    parser.add_argument("--rewrite-fixtures", action="store_true",
                        help="同时重写 fixtures/*.expected.json")
    parser.add_argument("--out", default=str(OUT_DIR))
    parser.add_argument("--label", default="T-12")
    args = parser.parse_args(argv)

    from noobbaozi.config import paths as paths_mod
    from noobbaozi.engine import adapter
    from noobbaozi.engine.assumptions import default_assumptions
    from noobbaozi.engine.normalize import normalize_fit
    from noobbaozi.sde.index import Index

    engine = adapter.Engine()
    meta = engine.meta()
    # index 必传：不传会静默退回旧档（19 技能），重标出来的就是旧档基线
    index = Index.open(paths_mod.db_path())

    # ---- 主样本 ----
    km = json.loads((ROOT / "spike" / "kill.json").read_text(encoding="utf-8"))[0]
    km.pop("zkb", None)
    normalized = normalize_fit(json.dumps([km], ensure_ascii=False))
    if normalized["fit"] is None:
        print(f"kill.json 归一化失败: {normalized['errors']}", file=sys.stderr)
        index.close()
        return 2
    fit = normalized["fit"]
    assumptions = default_assumptions(fit, engine, index)
    skills = assumptions["skill_tier"]
    ship = attr_map(engine.calculate({**fit, "character": {"skills": skills}})["ship"]["attributes"])
    pure = pure_ehp(ship)

    # ---- 技能档自检（挡住"还没修就重标"）----
    skill_count = len(skills)
    ready = skill_count >= EXPECTED_MIN_SKILLS
    print("=" * 84)
    print("前提自检：技能档是否已按「全部技能 4 级」修复（T-12）")
    print("=" * 84)
    print(f"  skill_tier_name = {assumptions['skill_tier_name']}")
    print(f"  默认档技能数     = {skill_count}（阈值 {EXPECTED_MIN_SKILLS}）")
    print(f"  判定             = {'✅ 已修复，可重标定' if ready else '❌ 未修复（只补了被模块要求的技能）'}")
    if not ready and not args.force:
        print()
        print("  拒绝重标定：Lead 裁决「先修实现再重标，别标两遍」。")
        print("  如只想预演输出格式，加 --force（产出必须标注为预演、不得写入文档）。")
        return 2
    if not ready:
        print("  ⚠️ --force 预演模式：下面的数值**不是**重标定结果，禁止写入文档。")
    print()

    # ---- A) §6.2 新基线 ----
    print("=" * 84)
    print("A) 开发规范 §6.2 主样本 · 新基线（markdown，可直接粘）")
    print("=" * 84)
    rows: list[dict] = []
    md_lines = ["| 指标 | 旧值（过期） | 新值 | 变化 |", "|---|---|---|---|"]
    for label, key, old in OLD_SNAPSHOT:
        new = pure[key.split(":", 1)[1]] if isinstance(key, str) else ship.get(key)
        if new is None:
            print(f"  ⚠️ {label}: 引擎未提供该属性")
            rows.append({"label": label, "old": old, "new": None})
            continue
        new_value = float(new)
        rel = (new_value - old) / old if old else 0.0
        rows.append({"label": label, "old": old, "new": round(new_value, 6),
                     "change_pct": round(rel * 100, 4)})
        md_lines.append(f"| {label} | {old:g} | {new_value:.6f} | {rel:+.2%} |")
        print(f"  {label:24s} 旧 {old:>12.6g}  新 {new_value:>14.6f}  {rel:>+9.2%}")
    print()
    print("\n".join(md_lines))

    # ---- B) 模式快照 ----
    print()
    print("=" * 84)
    print("B) 模式快照 · 新基线（默认档；必须写明技能档）")
    print("=" * 84)
    single = normalize_fit(SINGLE_GUN_EFT)
    if single["fit"] is None:
        print(f"  单炮样本归一化失败: {single['errors']}", file=sys.stderr)
        return 2
    fit_single = single["fit"]
    mode_skills = default_assumptions(fit_single, engine, index)["skill_tier"]
    mode_lines = ["| 模式 | 旧 EHP | 新 EHP | 旧 DPS | 新 DPS | 旧对齐 | 新对齐 |",
                  "|---|---|---|---|---|---|---|"]
    mode_rows: list[dict] = []
    for label, mode_id, old_ehp, old_dps, old_align in OLD_MODES:
        ship_node = {"type_id": 35683} if mode_id is None else {"type_id": 35683, "mode": mode_id}
        calc = engine.calculate({"ship": ship_node, "items": fit_single["items"],
                                 "character": {"skills": mode_skills}})
        s = attr_map(calc["ship"]["attributes"])
        new_ehp, new_dps, new_align = float(s[-43]), float(s[-12]), float(s[-1])
        print(f"  {label:14s} EHP {old_ehp:>10.2f} → {new_ehp:>10.3f}   "
              f"DPS {old_dps:>7.3f} → {new_dps:>7.3f}   对齐 {old_align:>6.3f} → {new_align:>7.3f}")
        mode_rows.append({"mode": label, "mode_id": mode_id,
                          "old": {"ehp": old_ehp, "dps": old_dps, "align_time_s": old_align},
                          "new": {"ehp": round(new_ehp, 6), "dps": round(new_dps, 6),
                                  "align_time_s": round(new_align, 6)}})
        mode_lines.append(f"| {label} | {old_ehp:g} | {new_ehp:.3f} | {old_dps:g} | {new_dps:.3f} "
                          f"| {old_align:g} | {new_align:.3f} |")
    print()
    print(f"  技能档：{assumptions['skill_tier_name']}（{len(mode_skills)} 个技能）——**必须写进快照说明**")
    print()
    print("\n".join(mode_lines))

    # ---- C) fixtures 漂移 / 重写 ----
    print()
    print("=" * 84)
    print("C) fixtures 期望值")
    print("=" * 84)
    drift: list[dict] = []
    for eft_path in sorted(FIXTURES.glob("*.eft")):
        expected_path = eft_path.with_suffix(".expected.json")
        if not expected_path.is_file():
            continue
        expected = json.loads(expected_path.read_text(encoding="utf-8"))
        built = normalize_fit(eft_path.read_text(encoding="utf-8"))
        if built["fit"] is None:
            print(f"  {eft_path.stem}: 归一化失败")
            continue
        asm = default_assumptions(built["fit"], engine, index)
        drift.append({"fixture": eft_path.stem,
                      "old_skill_count": len(expected.get("skill_tier") or {}),
                      "new_skill_count": len(asm["skill_tier"])})
        print(f"  {eft_path.stem:28s} 技能数 {len(expected.get('skill_tier') or {}):>4d} → "
              f"{len(asm['skill_tier']):>4d}")
    if args.rewrite_fixtures:
        print("  → 重写 fixtures/*.expected.json（调用 prepare_expected.py）")
        import subprocess

        result = subprocess.run(
            [sys.executable, str(FIXTURES / "prepare_expected.py")],
            cwd=str(ROOT.parent), check=False,
        )
        if result.returncode != 0:
            print("  ⚠️ prepare_expected.py 失败，fixtures 未重写", file=sys.stderr)
    else:
        print("  → 未重写（加 --rewrite-fixtures 才写；重写后必须人工 diff 再入库）")

    # ---- 输出 ----
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "label": args.label,
        "preview_only": not ready,
        "skill_tier_name": assumptions["skill_tier_name"],
        "skill_count": skill_count,
        "meta": dict(meta),
        "main_rows": rows,
        "mode_rows": mode_rows,
        "fixture_drift": drift,
        "markdown": {"main": md_lines, "modes": mode_lines},
    }
    suffix = "预演-勿用" if not ready else args.label
    json_path = out_dir / f"rebaseline-{suffix}.json"
    md_path = out_dir / f"rebaseline-{suffix}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    header = [
        f"# 快照重标定 · {suffix}",
        "",
        f"- 时间：{payload['generated_at']}",
        f"- SDE build：{meta['sde_build']} · 引擎：{meta['engine_version']}",
        f"- 技能档：`{assumptions['skill_tier_name']}`（{skill_count} 个技能）",
    ]
    if not ready:
        header += ["", "> ⚠️ **预演产物**：技能档自检未通过（T-12 未完成），"
                       "本文件数值禁止写入文档。"]
    md_path.write_text("\n".join(header + ["", "## §6.2 主样本", ""] + md_lines
                                + ["", "## 模式快照", ""] + mode_lines + [""]),
                       encoding="utf-8")
    print()
    print(f"  JSON: {json_path}")
    print(f"  MD  : {md_path}")
    index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
