"""把所有 fixture 用真实渲染层渲染成一份 views JSON（供 `--views-json` 预演/复验）。

用途：把「实现产出」冻结成证据，避免每次复验都重跑渲染（也让报告可复核）。
这不是产品代码，只在验收时用。

用法：
    $env:UV_CACHE_DIR = (Join-Path $PWD "noobbaozi/spike/uvcache")
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python noobbaozi/acceptance/dump_real_views.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))


def main() -> int:
    from noobbaozi.config import paths as paths_mod
    from noobbaozi.engine import adapter
    from noobbaozi.engine.assumptions import default_assumptions
    from noobbaozi.engine.normalize import normalize_fit
    from noobbaozi.project.render import render_overview
    from noobbaozi.project.views import base as views_base
    from noobbaozi.sde.index import Index

    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())
    try:
        cases: dict[str, dict] = {}
        meta = None
        for eft_path in sorted((HERE / "fixtures").glob("*.eft")):
            fit = normalize_fit(eft_path.read_text(encoding="utf-8"))["fit"]
            if fit is None:
                print(f"{eft_path.stem}: 归一化失败", file=sys.stderr)
                return 2
            assumptions = default_assumptions(fit, engine, index)
            # 关键：ViewContext 只从 ctx.fit 建变体，技能必须注入 fit["character"]
            enriched = dict(fit)
            enriched["character"] = {"skills": dict(assumptions["skill_tier"])}
            ctx = views_base.ViewContext(
                engine=engine, index=index, fit=enriched,
                assumptions=assumptions, meta=engine.meta(),
            )
            payload = render_overview(ctx)
            cases[eft_path.stem] = payload
            meta = payload.get("meta")
            print(f"{eft_path.stem}: budget={payload.get('budget')} "
                  f"modes={len(payload.get('modes') or [])}")
        out_path = HERE / "out" / "views-真实.json"
        out_path.write_text(
            json.dumps({"note": "真实渲染层输出（由 dump_real_views.py 冻结）",
                        "meta": meta, "cases": cases},
                       ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        print(f"已写入 {out_path}")
    finally:
        index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
