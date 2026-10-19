"""探查 explain / sensitivity 的真实形状（A/B/C 三类 + 边界）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))
sys.path.insert(0, str((Path("evefit/acceptance")).resolve()))

from check_detail import load_fit


def main() -> int:
    from evefit.config import paths as paths_mod
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.project.explain import explain, supported_metrics as explain_metrics
    from evefit.project.sensitivity import sensitivity, supported_metrics as sens_metrics
    from evefit.project.views import base as views_base
    from evefit.sde.index import Index

    print("explain metrics:", len(explain_metrics()))
    print("  ", list(explain_metrics())[:40])
    print("sensitivity metrics:", len(sens_metrics()), list(sens_metrics())[:20])

    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())
    fit = load_fit("f1-hecate-killmail")
    asm = default_assumptions(fit, engine, index)
    enriched = {**fit, "character": {"skills": dict(asm["skill_tier"])}}
    ctx = views_base.ViewContext(engine=engine, index=index, fit=enriched,
                                 assumptions=asm, meta=engine.meta())

    for metric in ("survivability.ehp_mixed", "mobility.speed_mps",
                   "stability.capacitor.peak_recharge", "resources.cpu.load"):
        if metric not in explain_metrics():
            print(f"\n{metric}: 不在 explain 支持列表")
            continue
        result = explain(ctx, metric)
        print(f"\n=== explain({metric}) ===")
        print(json.dumps(result, ensure_ascii=False)[:1200])

    # sensitivity：关掉 cap 最高的模块
    detail = None
    from evefit.project.render import render_detail
    detail = render_detail(ctx, view="stability")
    cap_sens = (detail.get("overview") or {}).get("cap_sensitivity") or []
    print("\ncap_sensitivity[0]:", json.dumps(cap_sens[0], ensure_ascii=False) if cap_sens else "无")
    if cap_sens:
        target = cap_sens[0]
        result = sensitivity(ctx, "stability.capacitor.stable_pct",
                             disable_item=target.get("type_id"))
        print("sensitivity(stability.capacitor.stable_pct, disable_item="
              f"{target.get('type_id')}):")
        print(json.dumps(result, ensure_ascii=False)[:900])
    index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
