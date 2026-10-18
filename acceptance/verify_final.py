"""复验取证：把报告要用的每项证据一次算出来（只读，不改产品代码）。

- 六视角字段齐全度（41 × 样本）
- provenance 覆盖（叶子路径 ↔ 平行表，双向）
- confidence 稀疏口径（缺省 A、非 A 必现、显式 A 计数）
- 不变式 I1–I5 独立复算
- modes 结构与方向（含相对基准的变化幅度）
- 技能档护栏
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(HERE))

import run_acceptance as ra  # noqa: E402

VIEW_LABEL = {
    "firepower": "火力", "survivability": "生存", "mobility": "机动",
    "resources": "资源", "constraints": "约束", "stability": "不稳定",
}


def main() -> int:
    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.engine.normalize import normalize_fit
    from evefit.project.render import render_overview
    from evefit.project.views import base as views_base
    from evefit.sde.index import Index

    spec = ra.load_field_spec()
    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())
    facts_engine = ra.EngineFacts()
    conf_oracle = ra.ConfidenceOracle()
    prov_oracle = ra.ProvenanceOracle()
    all_fields = ra.all_fields(spec)
    print(f"字段清单 {len(all_fields)} 个 | confidence 默认={conf_oracle.default} 权威={conf_oracle.source}")
    print()

    targets = [(p.stem, p.read_text(encoding="utf-8")) for p in sorted((HERE / "fixtures").glob("*.eft"))]
    targets.append(("kill.json(KB)", (ROOT / "spike" / "kill.json").read_text(encoding="utf-8")))

    try:
        for case_name, text in targets:
            facts = facts_engine.build(case_name, text, None)
            fit = dict(facts.fit)
            fit["character"] = {"skills": dict(facts.skills)}
            ctx = views_base.ViewContext(engine=engine, index=index, fit=fit,
                                         assumptions=facts.assumptions, meta=facts.meta)
            payload = render_overview(ctx)

            print("=" * 86)
            print(f"{case_name}  形态={facts.source_format}  技能档={facts.skill_tier_name} "
                  f"({len(facts.skills)})  budget={payload.get('budget')}")
            print("=" * 86)

            # 1) 字段齐全
            views = payload["views"]
            ok = missing = 0
            for view_name, spec_field in ((v, f) for v, f, _m in all_fields):
                overview, _t = ra.resolve_view_entry(views, view_name)
                present = False
                if isinstance(overview, dict):
                    present, _v = ra.lookup_field(overview, spec_field)
                ok += bool(present)
                missing += (not present)
            print(f"  字段齐全      : {ok}/{len(all_fields)}（缺 {missing}）")

            # 2) provenance + confidence
            leaf_total = prov_total = 0
            conf_total = conf_nonA = conf_explicitA = 0
            problems = []
            for view_name in spec["views"]:
                overview, table = ra.resolve_view_entry(views, view_name)
                leaves = ra.collect_leaves(overview, table)
                paths = {leaf.path for leaf in leaves}
                leaf_total += len(leaves)
                prov_total += len(table or {})
                if paths != set(table or {}):
                    problems.append(f"{view_name}: 键空间不一致")
                missing_prov = [p for p in paths if not (table or {}).get(p)]
                if missing_prov:
                    problems.append(f"{view_name}: provenance 缺 {len(missing_prov)}")
                conf_table = (views.get(view_name) or {}).get("confidence") or {}
                expected = conf_oracle.expected_non_default(view_name, sorted(paths))
                for path, want in expected.items():
                    if conf_table.get(path) != want:
                        problems.append(f"{view_name}.confidence.{path}: 期望 {want} 实际 {conf_table.get(path)}")
                extra = set(conf_table) - paths
                if extra:
                    problems.append(f"{view_name}: confidence 多键 {sorted(extra)[:3]}")
                for path, level in conf_table.items():
                    conf_total += 1
                    if level != conf_oracle.default:
                        conf_nonA += 1
                    else:
                        conf_explicitA += 1
            print(f"  provenance    : 叶子 {leaf_total} 条 = 平行表 {prov_total} 条"
                  f"（缺键/多键问题的：{problems or '无'}）")
            print(f"  confidence    : 表内 {conf_total} 条（非A {conf_nonA} / 显式A {conf_explicitA}）"
                  f"；缺省补 A 后覆盖 {leaf_total} 条叶子")

            # 3) 不变式（用 runner 的独立复核）
            inv = ra.check_invariants(facts, facts_engine)
            print(f"  不变式 I1–I5   : {'全部成立' if not inv else [d.where for d in inv]}")

            # 4) modes
            modes = payload.get("modes") or []
            base_ehp = facts.ship.get(-43)
            base_dps = facts.ship.get(-12)
            base_align = facts.ship.get(-1)
            print(f"  modes         : {len(modes)} 条（基准：EHP {base_ehp:.3f} / DPS {base_dps:.3f} / 对齐 {base_align:.3f}）")
            for entry in modes:
                d_ehp = (entry["ehp_mixed"] / base_ehp - 1) * 100
                d_dps = (entry["dps_base"] / base_dps - 1) * 100
                d_align = (entry["align_time_s"] / base_align - 1) * 100
                print(f"      {entry['mode_name']:14s} ehp {entry['ehp_mixed']:>10.3f} ({d_ehp:+6.1f}%) "
                      f"dps {entry['dps_base']:>8.3f} ({d_dps:+6.1f}%) "
                      f"align {entry['align_time_s']:>7.3f} ({d_align:+6.1f}%)")
            print()
    finally:
        index.close()
        facts_engine.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
