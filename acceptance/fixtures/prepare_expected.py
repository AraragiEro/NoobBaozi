"""生成 fixtures 的期望值（`*.expected.json`）。

这不是验收本体，是**夹具标定**脚本：跑一次，把当前 SDE build + 引擎版本下的
实测值冻结成文件，之后 runner 拿它做「快照」比对。

标定口径（写死，避免以后重新生成时口径漂移）：

- 技能档 = `engine.assumptions.default_assumptions()` 的 `all4+min_required`；
- 伤害剖面 = 25/25/25/25（Assumptions 默认）；
- 模块 state = 引擎导入规则（不覆写）；
- 模式 = 无模式为基准；有模式的船额外枚举 `index.modes_for_ship()`。

重新标定的条件：SDE build 变化或引擎升级（开发规范 §6.2）。
重新标定后必须在报告里逐字段 diff 并说明原因，**不得为了让测试通过而改期望值**。

用法：
    $env:UV_CACHE_DIR = (Join-Path $PWD "evefit/spike/uvcache")
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python evefit/acceptance/fixtures/prepare_expected.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCEPTANCE = HERE.parent
ROOT = ACCEPTANCE.parent                       # evefit/
WORKSPACE = ROOT.parent
sys.path.insert(0, str(WORKSPACE))

def fixture_paths() -> list[Path]:
    """所有样本（`.eft` 与原始 killmail `.json`；`.expected.json` 是产出，不是输入）。"""
    items = [p for pattern in ("*.eft", "*.json") for p in HERE.glob(pattern)
             if not p.name.endswith(".expected.json")]
    return sorted(items, key=lambda item: item.stem)


def attr_value(node: dict, attribute_id: int) -> float | None:
    """引擎 attributes 节点取值（兼容 `{"value": v}` 与裸数两种形状）。"""
    item = (node or {}).get(str(attribute_id))
    if item is None:
        return None
    return item["value"] if isinstance(item, dict) else item


def attr_map(node: dict) -> dict[int, float | None]:
    return {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in (node or {}).items()}


#: 快照字段：报告里用到的 key -> (来源属性 ID, 单位)
SNAPSHOT_FIELDS: dict[str, tuple[int, str]] = {
    "shield_hp": (263, "hp"),
    "armor_hp": (265, "hp"),
    "hull_hp": (9, "hp"),
    "shield_ehp": (-30, "hp"),
    "armor_ehp": (-28, "hp"),
    "hull_ehp": (-29, "hp"),
    "ehp_mixed": (-43, "hp"),
    "align_time_s": (-1, "s"),
    "speed_mps": (37, "m/s"),
    "signature_radius_m": (552, "m"),
    "mass_kg": (4, "kg"),
    "agility": (70, "-"),
    "warp_speed_au_s": (600, "AU/s"),
    "cpu_output": (48, "tf"),
    "cpu_load": (49, "tf"),
    "powergrid_output": (11, "MW"),
    "powergrid_load": (15, "MW"),
    "calibration_output": (1132, "-"),
    "calibration_load": (1152, "-"),
    "cargo_capacity_m3": (38, "m3"),
    "drone_bandwidth": (1271, "Mbit/s"),
    "drone_capacity": (283, "m3"),
    "cap_capacity_gj": (482, "GJ"),
    "cap_recharge_ms": (55, "ms(原始)"),
    "cap_stable_pct": (-72, "%"),
    "cap_depletes_in_s": (-7, "s"),
    "cap_peak_load": (-4, "GJ/s"),
    "cap_peak_recharge": (-2, "GJ/s"),
    "cap_delta": (-5, "GJ/s"),
    "dps": (-12, "hp/s"),
    "dps_with_reload": (-13, "hp/s"),
    "alpha": (-11, "hp"),
    "vel_boost": (-44, "-"),
}


def main() -> int:
    from evefit.engine import adapter
    from evefit.engine.assumptions import default_assumptions
    from evefit.engine.normalize import normalize_fit
    from evefit.sde.index import Index
    from evefit.config import paths as paths_mod

    engine = adapter.Engine()
    meta = engine.meta()
    index = Index.open(paths_mod.db_path())
    try:
        for eft_path in fixture_paths():
            stem = eft_path.stem
            text = eft_path.read_text(encoding="utf-8")
            normalized = normalize_fit(text)
            if normalized["fit"] is None:
                print(f"[FATAL] {stem} 归一化失败: {normalized['errors']}", file=sys.stderr)
                return 1

            fit = normalized["fit"]
            # index 必传：T-12 后不传会退回 min_required_only（旧档），期望值就标错了
            assumptions = default_assumptions(fit, engine, index)
            skills = assumptions["skill_tier"]
            calc = engine.calculate({**fit, "character": {"skills": skills}})
            ship = attr_map(calc["ship"]["attributes"])

            snapshot = {
                key: (None if ship.get(aid) is None else round(float(ship[aid]), 6))
                for key, (aid, _unit) in SNAPSHOT_FIELDS.items()
            }
            charge_ms = snapshot["cap_recharge_ms"]
            snapshot["cap_recharge_s"] = (
                None if charge_ms is None else round(charge_ms / 1000.0, 6)
            )

            items = []
            for item in calc["items"]:
                ia = attr_map(item.get("attributes") or {})
                items.append(
                    {
                        "volley": ia.get(-21),
                        "dps": ia.get(-12),
                        "dps_with_reload": ia.get(-13),
                        "cycle_time_raw": ia.get(-3),
                        "max_range": ia.get(54),
                        "falloff": ia.get(158),
                        "tracking_raw": ia.get(160),
                        "state": item.get("state"),
                    }
                )

            violations = engine.validate({**fit, "character": {"skills": skills}})
            modes = {}
            ship_type_id = (fit.get("ship") or {}).get("type_id")
            for mode_row in index.modes_for_ship(ship_type_id):
                mode_fit = {
                    "ship": {**fit["ship"], "mode": mode_row["type_id"]},
                    "items": fit["items"],
                    "character": {"skills": skills},
                }
                m_ship = attr_map(engine.calculate(mode_fit)["ship"]["attributes"])
                modes[str(mode_row["type_id"])] = {
                    "name": mode_row["name"],
                    "ehp": round(float(m_ship[-43]), 6),
                    "dps": round(float(m_ship[-12]), 6),
                    "align_time_s": round(float(m_ship[-1]), 6),
                    "speed_mps": round(float(m_ship[37]), 6),
                }

            payload = {
                "_note": "由 prepare_expected.py 生成的快照；SDE/引擎变化后需重新标定（开发规范 §6.2）",
                "fixture": stem,
                "sde_build": meta["sde_build"],
                "engine_version": meta["engine_version"],
                "spec_version": meta["spec_version"],
                "source_format": normalized["source_format"],
                "ship_type_id": ship_type_id,
                "skill_tier_name": assumptions["skill_tier_name"],
                "skill_tier": {str(k): v for k, v in sorted(skills.items())},
                "skill_tier_histogram": {
                    str(level): list(skills.values()).count(level)
                    for level in sorted(set(skills.values()))
                },
                "damage_profile": assumptions["damage_profile"],
                "mode_id": assumptions["mode_id"],
                "snapshot": snapshot,
                "snapshot_units": {key: unit for key, (_aid, unit) in SNAPSHOT_FIELDS.items()},
                "items": items,
                "modes": modes,
                "violations": violations,
            }

            out_path = HERE / f"{stem}.expected.json"
            out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"[OK] {out_path.name}: skill_tier={len(skills)} "
                  f"ehp_mixed={snapshot['ehp_mixed']} dps={snapshot['dps']} "
                  f"modes={len(modes)} violations={len(violations)}")
    finally:
        index.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
