"""开发规范 §6.2 快照对拍（可复跑）。

**状态：`skip-until-rebaseline` —— 新基线已产出，等文档落地 + T-15 收口**

历史与现状（按时间读）：

1. **T-12 之前**：`derive_skill_tier` 只补「被模块要求的」技能到 4 级（19 个技能），
   与主人约定「全部技能 4 级 + 补最低」不符 → 文档里 2 行旧值与实测差 16.7% / 20%。
2. **T-12 之后**（现在）：技能档 = **512 个（510×4 + 2×5）**，`default_assumptions`
   必须传 `index` 才是新档；不传会退化到 `min_required_only`（19 个）。
   本脚本**强制传 index**，并校验档名——否则"拿旧值比旧值"会假绿。
3. **新基线**（`REBASELINED_SNAPSHOT` / `REBASELINED_MODES`，实测于 SDE 3607705 + 引擎 13.6.0）
   由 `rebaseline.py` 产出，已交 Lead 写入 `§6.2 / §10.2`。

默认 `skip` 语义：**不因 DIFF 变红**（退出码恒 0），但在输出与 JSON 里写明
「新基线等文档落地 + T-15 收口」。等 Lead 通知收口后，用 `--enforce` 运行
即恢复成真正的回归门禁（那时任何 DIFF = SDE 漂移或实现回归）。

用法：
    $env:UV_CACHE_DIR = (Join-Path $PWD "evefit/spike/uvcache")
    $PY = 'uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" python'

    Invoke-Expression "$PY evefit/acceptance/check_snapshot.py"            # 默认：skip 语义
    Invoke-Expression "$PY evefit/acceptance/check_snapshot.py --enforce"  # 收口后：DIFF 即失败
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
sys.path.insert(0, str(ROOT.parent))

#: 快照校验的生命周期状态。Lead 通知 T-15 收口后改为 `"enforced"`。
SNAPSHOT_STATUS = "enforced"
SNAPSHOT_BLOCKER = ""

#: T-12 之后的技能档契约（传了 index 才会拿到这个档名）
EXPECTED_TIER_NAME = "all4+min_required"
EXPECTED_MIN_SKILLS = 100

#: 旧值（T-12 前，已过期）——只用于产出 changelog 的「旧 → 新」diff
DOC_SNAPSHOT_V1: tuple[tuple[str, int | str, float], ...] = (
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

#: **T-12 后的新基线**（`rebaseline.py` 实测产出；门禁用这一组）
REBASELINED_SNAPSHOT: tuple[tuple[str, int | str, float], ...] = (
    ("分层 HP / shield", 263, 780.0),
    ("分层 HP / armor", 265, 840.0),
    ("分层 HP / hull", 9, 2343.75),
    ("混合 EHP", -43, 9102.58),
    ("四系 EHP / EM", "pure:em", 8698.12),
    ("四系 EHP / EXP", "pure:explosive", 8711.12),
    ("四系 EHP / KIN", "pure:kinetic", 11842.38),
    ("四系 EHP / THM", "pure:thermal", 9343.09),
    ("护甲修复 raw", -45, 32.5),
    ("护甲修复 effective", -48, 69.126),
    ("被动盾回充 raw", -51, 3.046875),
    ("被动盾回充 effective", -52, 5.803571),
    ("CPU 输出", 48, 276.0),
    ("CPU 占用", 49, 287.0),
    ("PG 输出", 11, 64.8),
    ("PG 占用", 15, 68.4),
    ("标定 输出", 1132, 400.0),
    ("标定 占用", 1152, 225.0),
    ("MWD active 速度", 37, 1482.74),
    ("MWD active 信号半径", 552, 385.0),
    ("MWD active 对齐", -1, 9.488908),
    ("DPS", -12, 487.458266),
)

#: 旧模式快照（T-12 前，未注明技能档）
DOC_MODES_V1: tuple[tuple[str, int | None, float, float, float], ...] = (
    ("无", None, 4530.93, 47.681, 8.035),
    ("Defense", 35686, 6254.72, 47.681, 8.035),
    ("Propulsion", 35687, 4530.93, 47.681, 2.678),
    ("Sharpshooter", 35688, 4530.93, 63.575, 8.035),
)

#: **T-12 后的四模式新基线**（技能档：all4+min_required = 512 技能）
REBASELINED_MODES: tuple[tuple[str, int | None, float, float, float], ...] = (
    ("无", None, 4609.688796, 66.319945, 6.42797),
    ("Defense", 35686, 6264.533204, 66.319945, 6.42797),
    ("Propulsion", 35687, 4609.688796, 66.319945, 2.142657),
    ("Sharpshooter", 35688, 4609.688796, 88.426594, 6.42797),
)

SINGLE_GUN_EFT = "[Hecate, mode snapshot]\nLight Neutron Blaster II, Void S\n"

RESONANCES = {
    "shield": {"em": 271, "explosive": 272, "kinetic": 273, "thermal": 274},
    "armor": {"em": 267, "explosive": 268, "kinetic": 269, "thermal": 270},
    "hull": {"em": 113, "explosive": 111, "kinetic": 109, "thermal": 110},
}
LAYER_HP = {"shield": 263, "armor": 265, "hull": 9}

FIXTURES = HERE / "fixtures"

#: fixtures `.expected.json` 的 key → 属性 ID（漂移探测用）
SNAPSHOT_ATTR = {
    "shield_hp": 263, "armor_hp": 265, "hull_hp": 9,
    "shield_ehp": -30, "armor_ehp": -28, "hull_ehp": -29, "ehp_mixed": -43,
    "align_time_s": -1, "speed_mps": 37, "signature_radius_m": 552,
    "mass_kg": 4, "agility": 70, "warp_speed_au_s": 600,
    "cpu_output": 48, "cpu_load": 49, "powergrid_output": 11, "powergrid_load": 15,
    "calibration_output": 1132, "calibration_load": 1152, "cargo_capacity_m3": 38,
    "drone_bandwidth": 1271, "drone_capacity": 283,
    "cap_capacity_gj": 482, "cap_recharge_ms": 55, "cap_stable_pct": -72,
    "cap_depletes_in_s": -7, "cap_peak_load": -4, "cap_peak_recharge": -2,
    "cap_delta": -5, "dps": -12, "dps_with_reload": -13, "alpha": -11, "vel_boost": -44,
}


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


def banner(text: str) -> None:
    print("=" * 88)
    print(text)
    print("=" * 88)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="§6.2 快照对拍（默认 skip-until-rebaseline）")
    parser.add_argument("--enforce", action="store_true",
                        help="把 DIFF 当失败（Lead 通知收口后使用）")
    parser.add_argument("--out", default=str(OUT_DIR), help="JSON 输出目录")
    args = parser.parse_args(argv)

    enforce = args.enforce or SNAPSHOT_STATUS == "enforced"

    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.engine.normalize import normalize_fit
    from evefit.sde.index import Index

    engine = adapter.Engine()
    meta = engine.meta()
    index = Index.open(paths_mod.db_path())

    if not enforce:
        banner("⏸ SKIP-UNTIL-REBASELINE —— 新基线已产出，等文档落地 + T-15 收口")
        print(f"  阻塞项：{SNAPSHOT_BLOCKER}")
        print("  本脚本已改用 T-12 后的新基线（技能档 512）做比对，不再拿旧值。")
        print("  下面的 DIFF 仅供参考，退出码恒 0；加 --enforce 才会变回回归门禁。")
        print()

    try:
        # ---- 主样本 ----
        km = json.loads((ROOT / "spike" / "kill.json").read_text(encoding="utf-8"))[0]
        km.pop("zkb", None)
        normalized = normalize_fit(json.dumps([km], ensure_ascii=False))
        if normalized["fit"] is None:
            print(f"kill.json 归一化失败: {normalized['errors']}", file=sys.stderr)
            return 2
        fit = normalized["fit"]
        # index 必传：不传会静默退回旧档（19 技能），"拿旧值比旧值"会假绿
        assumptions = default_assumptions(fit, engine, index)
        skills = assumptions["skill_tier"]
        ship = attr_map(
            engine.calculate({**fit, "character": {"skills": skills}})["ship"]["attributes"]
        )
        pure = pure_ehp(ship)

        banner("A) 技能档护栏（T-12 契约）")
        tier_ok = (assumptions["skill_tier_name"] == EXPECTED_TIER_NAME
                   and len(skills) >= EXPECTED_MIN_SKILLS)
        print(f"  skill_tier_name = {assumptions['skill_tier_name']}"
              f"（期望 {EXPECTED_TIER_NAME}）")
        print(f"  技能数           = {len(skills)}（下限 {EXPECTED_MIN_SKILLS}，新档实测 512）")
        dist = {level: list(skills.values()).count(level) for level in sorted(set(skills.values()))}
        print(f"  档位分布         = {dist}")
        print(f"  判定             = {'✅ 新档（512 全技能）' if tier_ok else '❌ 疑似退回旧档！'}")
        print()

        banner("B) §6.2 主样本：新基线（T-12 后）逐行对拍")
        print(f"  SDE build={meta['sde_build']}  engine={meta['engine_version']}  "
              f"技能档={assumptions['skill_tier_name']}（{len(skills)} 技能）")
        print()
        print(f"  {'指标':24s} {'T-12 前(旧)':>13s} {'新基线':>14s} {'实测':>16s} {'相对差':>9s}  判定")
        print("  " + "-" * 92)
        diffs: list[dict] = []
        for (label, key, old), (_l2, _k2, want) in zip(DOC_SNAPSHOT_V1, REBASELINED_SNAPSHOT):
            got = pure[key.split(":", 1)[1]] if isinstance(key, str) else ship.get(key)
            if got is None:
                print(f"  {label:24s} {old:>13.6g} {want:>14.6g} {'None':>16s} {'-':>9s}  ⚠️ 缺失")
                diffs.append({"label": label, "old": old, "new": want, "actual": None})
                continue
            rel = abs(float(got) - want) / want if want else abs(float(got) - want)
            ok = rel <= 2e-4
            change = (want - old) / old if old else 0.0
            print(f"  {label:24s} {old:>13.6g} {want:>14.6g} {float(got):>16.6f} "
                  f"{rel:>8.2%}  {'✅ OK' if ok else '↔ DIFF'}   (较旧 {change:+.1%})")
            if not ok:
                diffs.append({"label": label, "old": old, "new": want, "actual": round(float(got), 6)})

        # ---- 模式快照 ----
        banner("C) 模式快照：新基线（技能档 512，必须随文档注明）")
        single = normalize_fit(SINGLE_GUN_EFT)
        if single["fit"] is None:
            print(f"  单炮样本归一化失败: {single['errors']}", file=sys.stderr)
            return 2
        fit_single = single["fit"]
        mode_skills = default_assumptions(fit_single, engine, index)["skill_tier"]
        print(f"  技能档：{assumptions['skill_tier_name']}（{len(mode_skills)} 技能）")
        print()
        print(f"  {'模式':14s} {'旧 EHP':>10s} {'新 EHP':>11s} | {'旧 DPS':>8s} {'新 DPS':>9s} "
              f"| {'旧对齐':>7s} {'新对齐':>8s}  判定")
        mode_diffs: list[dict] = []
        for (label, mode_id, _oe, _od, _oa), (_l2, _m2, new_ehp, new_dps, new_align) in zip(
            DOC_MODES_V1, REBASELINED_MODES
        ):
            ship_node = {"type_id": 35683} if mode_id is None else {"type_id": 35683, "mode": mode_id}
            calc = engine.calculate({"ship": ship_node, "items": fit_single["items"],
                                     "character": {"skills": mode_skills}})
            s = attr_map(calc["ship"]["attributes"])
            actual = (float(s[-43]), float(s[-12]), float(s[-1]))
            ok = (math.isclose(actual[0], new_ehp, rel_tol=2e-4)
                  and math.isclose(actual[1], new_dps, rel_tol=2e-4)
                  and math.isclose(actual[2], new_align, rel_tol=2e-4))
            print(f"  {label:14s} {_oe:>10.2f} {new_ehp:>11.3f} | {_od:>8.3f} {new_dps:>9.3f} "
                  f"| {_oa:>7.3f} {new_align:>8.3f}  {'✅ OK' if ok else '↔ DIFF'}")
            if not ok:
                mode_diffs.append({"mode": label, "expected": [new_ehp, new_dps, new_align],
                                   "actual": [round(v, 6) for v in actual]})

        # ---- fixtures 漂移 ----
        banner("D) fixtures 期望值漂移（`.expected.json` ↔ 实测，技能档 512）")
        fixture_drift: list[dict] = []
        for eft_path in sorted(FIXTURES.glob("*.eft")):
            expected_path = eft_path.with_suffix(".expected.json")
            if not expected_path.is_file():
                continue
            expected = json.loads(expected_path.read_text(encoding="utf-8"))
            stem = eft_path.stem
            built = normalize_fit(eft_path.read_text(encoding="utf-8"))
            if built["fit"] is None:
                print(f"  {stem}: 归一化失败")
                continue
            asm = default_assumptions(built["fit"], engine, index)
            current = attr_map(engine.calculate(
                {**built["fit"], "character": {"skills": asm["skill_tier"]}})["ship"]["attributes"])
            drifted = []
            for key, want in (expected.get("snapshot") or {}).items():
                attr_id = SNAPSHOT_ATTR.get(key)
                if attr_id is None or want is None:
                    continue
                got = current.get(attr_id)
                if got is None:
                    continue
                if not math.isclose(float(got), float(want), rel_tol=1e-6, abs_tol=1e-9):
                    drifted.append((key, float(want), float(got)))
            expected_skill_count = len(expected.get("skill_tier") or {})
            skill_drift = len(asm["skill_tier"]) != expected_skill_count
            flag = "✅ 一致" if (not drifted and not skill_drift) else "↔ 漂移"
            print(f"  {stem:28s} 技能数 {len(asm['skill_tier']):>4d} "
                  f"(期望 {expected_skill_count:>4d}): 指标漂移 {len(drifted)} 项  {flag}")
            for key, want_value, got in drifted[:10]:
                print(f"      {key}: 期望 {want_value!r} / 实测 {got!r}")
            if drifted or skill_drift:
                fixture_drift.append({
                    "fixture": stem, "metrics": len(drifted),
                    "skill_count": len(asm["skill_tier"]),
                    "expected_skill_count": expected_skill_count,
                })
        if not fixture_drift:
            print("  → 三个样本与期望值一致")

        # ---- 汇总 ----
        banner("E) 汇总")
        print(f"  快照状态      : {SNAPSHOT_STATUS}")
        print(f"  阻塞项        : {SNAPSHOT_BLOCKER}")
        print(f"  技能档        : {assumptions['skill_tier_name']}（{len(skills)} 技能）"
              f"{'' if tier_ok else '  ❌ 退化！'}")
        print(f"  §6.2 主样本    : {len(REBASELINED_SNAPSHOT)} 行，{len(diffs)} 行 DIFF"
              f"（{'需要处理' if enforce else 'skip 状态，不作判据'}）")
        print(f"  模式快照      : {len(REBASELINED_MODES)} 行，{len(mode_diffs)} 行 DIFF")
        print(f"  fixtures 漂移 : {len(fixture_drift)} 个样本")
        for entry in diffs:
            print(f"  {'❌' if enforce else '↔'} DIFF {entry['label']}: "
                  f"新基线 {entry['new']} / 实测 {entry['actual']}")

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "status": "enforced" if enforce else SNAPSHOT_STATUS,
            "blocker": None if enforce else SNAPSHOT_BLOCKER,
            "meta": dict(meta),
            "skill_tier_name": assumptions["skill_tier_name"],
            "skill_count": len(skills),
            "tier_guard_ok": tier_ok,
            "main_snapshot_rows": len(REBASELINED_SNAPSHOT),
            "main_snapshot_diffs": diffs,
            "mode_diffs": mode_diffs,
            "fixture_drift": fixture_drift,
        }
        json_path = out_dir / "snapshot-check.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n  JSON: {json_path}")

        if not tier_ok:
            print("\n  → 退出码 2：技能档退化，结果不可信")
            return 2
        if not enforce:
            print("\n  → 退出码 0：skip-until-rebaseline，不做判据")
            return 0
        return 1 if (diffs or mode_diffs) else 0
    finally:
        index.close()


if __name__ == "__main__":
    raise SystemExit(main())
