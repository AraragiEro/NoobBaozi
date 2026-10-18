"""runner 自测：证明验收器**真的能抓到缺陷**，而不是永远报通过。

做法：先用引擎事实拼一份**合规**的六视角 JSON（当作"实现正确时的样子"），
再逐条注入已知缺陷，断言 runner 把它归到预期的那一类。

这份文件有两个用途：
1. QA 自证 runner 有效（没有它，`status=pass` 没有任何意义）；
2. 给实现方一份**可通过验收的最小形状样例**（`_good_payload()` 就是）。

用法：
    $env:UV_CACHE_DIR = (Join-Path $PWD "evefit/spike/uvcache")
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python evefit/acceptance/selftest.py
"""
from __future__ import annotations

import copy
import json
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT.parent))
sys.path.insert(0, str(HERE))

import run_acceptance as ra  # noqa: E402


def _make_tv(oracle):
    """按**权威表**生成 TypedValue：有 source_attr 时 provenance 取 oracle.tag()。

    这正是 Lead 裁决要求的姿态——不硬编码期望 provenance，投影层换表我就跟着换。
    """

    def tv(value, provenance=None, unit=None, source_attr=None):
        if provenance is None:
            if oracle.available and isinstance(source_attr, int):
                provenance = oracle.tag(source_attr)
            provenance = provenance or "measured"
        return {
            "value": value,
            "provenance": provenance,
            "unit": unit,
            "source_attr": source_attr,
        }

    return tv


def _item_attr(facts: ra.CaseFacts, attr_id: int) -> float | None:
    for item in facts.items:
        value = item.get(attr_id)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _good_payload(facts: ra.CaseFacts, oracle) -> dict:
    """按字段规格拼一份**合规**的 overview（供自测与作为形状样例）。"""
    tv = _make_tv(oracle)
    ship = facts.ship
    gun_tracking_raw = _item_attr(facts, 160)
    gun_range = _item_attr(facts, 54) or 0.0
    gun_falloff = _item_attr(facts, 158) or 0.0
    cycle_raw = _item_attr(facts, -3) or 0.0
    volley = ship.get(-11) or 0.0
    dps = ship.get(-12) or 0.0

    view_firepower = {
        "weapons": [
            {
                "provenance": "measured",
                "family": "hybrid",
                "name": "Light Neutron Blaster II",
                "count": 5,
                "ammo_name": "Void S",
            }
        ],
        "dps": {
            "base": tv(dps, unit="hp/s", source_attr=-12),
            "with_reload": tv(ship.get(-13) or 0.0, unit="hp/s", source_attr=-13),
        },
        "dph": tv(volley, unit="hp", source_attr=-11),
        "damage_split": {
            "em": tv(0.0),
            "explosive": tv(0.0),
            "kinetic": tv(0.6),
            "thermal": tv(0.4),
        },
        "range": {
            "optimal_m": tv(gun_range, unit="m", source_attr=54),
            "optimal_plus_falloff_m": tv(gun_range + gun_falloff, unit="m"),
            "optimal_plus_2falloff_m": tv(gun_range + 2 * gun_falloff, unit="m"),
        },
        "application": {
            "provenance": "assumed",
            "confidence": "E",
            "tracking_rad_s": tv((gun_tracking_raw or 0.0) / 1000.0, unit="rad/s", source_attr=160),
        },
        "ammo_endurance_s": tv(5.0, unit="s"),
        "overload": {
            "off": {"dps": tv(dps, unit="hp/s"), "range_m": tv(gun_range, unit="m")},
            "on": {"dps": tv(dps * 1.15, unit="hp/s"), "range_m": tv(gun_range, unit="m")},
            "delta": {"dps": tv(dps * 0.15, unit="hp/s"), "range_m": tv(0.0, unit="m")},
        },
        "hardpoints_free": {
            "turret": tv(ship.get(102) or 0, unit="个", source_attr=102),
            "launcher": tv(ship.get(101) or 0, unit="个", source_attr=101),
        },
    }

    view_survivability = {
        "layers": {
            "shield_hp": tv(ship.get(263), unit="hp", source_attr=263),
            "armor_hp": tv(ship.get(265), unit="hp", source_attr=265),
            "hull_hp": tv(ship.get(9), unit="hp", source_attr=9),
        },
        "layer_ehp": {
            "shield": tv(ship.get(-30), unit="hp", source_attr=-30),
            "armor": tv(ship.get(-28), unit="hp", source_attr=-28),
            "hull": tv(ship.get(-29), unit="hp", source_attr=-29),
        },
        "ehp_by_damage": {
            dmg: tv(0.0, unit="hp")
            for dmg in ("em", "explosive", "kinetic", "thermal")
        },
        "ehp_mixed": tv(ship.get(-43), unit="hp", source_attr=-43),
        "thinnest": {
            "provenance": "measured",
            "shield": {"damage_type": "kinetic", "layer_ehp": tv(ship.get(-30), unit="hp")},
        },
        "repair": {
            "provenance": "measured",
            "armor": {
                "raw_hp_s": tv(ship.get(-45) or 0.0, unit="hp/s", source_attr=-45),
                "effective_ehp_s": tv(ship.get(-48) or 0.0, unit="hp/s", source_attr=-48),
            },
            "shield": {
                "raw_hp_s": tv(ship.get(-47) or 0.0, unit="hp/s", source_attr=-47),
                "effective_ehp_s": tv(ship.get(-50) or 0.0, unit="hp/s", source_attr=-50),
            },
            "hull": {
                "raw_hp_s": tv(ship.get(-46) or 0.0, unit="hp/s", source_attr=-46),
                "effective_ehp_s": tv(ship.get(-49) or 0.0, unit="hp/s", source_attr=-49),
            },
        },
        "overload": {
            "provenance": "measured",
            "ehp_delta": tv(0.0, unit="hp"),
        },
    }

    view_mobility = {
        "speed_mps": tv(ship.get(37), unit="m/s", source_attr=37),
        "align_time_s": tv(ship.get(-1), unit="s", source_attr=-1),
        "signature_radius_m": tv(ship.get(552), unit="m", source_attr=552),
        "mass_kg": tv(ship.get(4), unit="kg", source_attr=4),
        "agility": tv(ship.get(70), unit="-", source_attr=70),
        "warp_speed_au_s": tv(ship.get(600), unit="AU/s", source_attr=600),
        "propulsion_states": {
            "provenance": "measured",
            "off": {"speed_mps": tv(ship.get(37), unit="m/s"),
                    "sig_m": tv(ship.get(552), unit="m"),
                    "align_time_s": tv(ship.get(-1), unit="s")},
            "active": {"speed_mps": tv(ship.get(37), unit="m/s"),
                       "sig_m": tv(ship.get(552), unit="m"),
                       "align_time_s": tv(ship.get(-1), unit="s")},
            "overload": {"speed_mps": tv(ship.get(37), unit="m/s"),
                         "sig_m": tv(ship.get(552), unit="m"),
                         "align_time_s": tv(ship.get(-1), unit="s")},
        },
    }

    hi_total, med_total = ship.get(14) or 0, ship.get(13) or 0
    low_total, rig_total = ship.get(12) or 0, ship.get(1137) or 0
    hi_used = sum(1 for item, state in zip(facts.items, facts.item_states)
                  if state not in (None, "offline") and item.get(54) is None)
    view_resources = {
        "cpu": {
            "output": tv(ship.get(48), unit="tf", source_attr=48),
            "load": tv(ship.get(49), unit="tf", source_attr=49),
            "free": tv((ship.get(48) or 0.0) - (ship.get(49) or 0.0), unit="tf"),
        },
        "powergrid": {
            "output": tv(ship.get(11), unit="MW", source_attr=11),
            "load": tv(ship.get(15), unit="MW", source_attr=15),
            "free": tv((ship.get(11) or 0.0) - (ship.get(15) or 0.0), unit="MW"),
        },
        "calibration": {
            "output": tv(ship.get(1132), unit="点", source_attr=1132),
            "load": tv(ship.get(1152), unit="点", source_attr=1152),
            "free": tv((ship.get(1132) or 0.0) - (ship.get(1152) or 0.0), unit="点"),
        },
        "slots": {
            "high": {"used": tv(min(hi_used, int(hi_total)), unit="个"), "total": tv(hi_total, unit="个")},
            "medium": {"used": tv(3, unit="个"), "total": tv(med_total, unit="个")},
            "low": {"used": tv(4, unit="个"), "total": tv(low_total, unit="个")},
            "rig": {"used": tv(3, unit="个"), "total": tv(rig_total, unit="个")},
        },
        "hardpoints_free": {
            "turret": tv(ship.get(102) or 0, unit="个", source_attr=102),
            "launcher": tv(ship.get(101) or 0, unit="个", source_attr=101),
        },
        "drone": {
            "bandwidth": {"used": tv(0.0, unit="Mbit/s"), "total": tv(ship.get(1271), unit="Mbit/s", source_attr=1271)},
            "capacity": {"used": tv(0.0, unit="m3"), "total": tv(ship.get(283), unit="m3", source_attr=283)},
        },
        "cargo": {
            "used": tv(0.0, unit="m3"),
            "total": tv(ship.get(38), unit="m3", source_attr=38),
        },
        "empty_slots": {
            "high": tv(max(0.0, hi_total - hi_used), unit="个"),
            "medium": tv(max(0.0, med_total - 3), unit="个"),
            "low": tv(max(0.0, low_total - 4), unit="个"),
            "rig": tv(max(0.0, rig_total - 3), unit="个"),
        },
        "upgrade_space": [],
    }

    tier_violations = [
        {"resource": "powergrid", "over_by": tv(18.0, unit="MW"),
         "detail": tv(18.0, unit="MW"), "provenance": "measured"}
    ]
    view_constraints = {
        "hard_violations": [],
        "tier_violations": tier_violations,
        "skill_floor": {
            "max_level": tv(5, unit="级"),
            "count_by_level": tv({4: 17, 5: 2}, unit="个"),
            "distinct_skills": tv(len(facts.skills), unit="个"),
        },
        "needs_implant": tv(False, "assumed", None),
    }

    view_stability = {
        "capacitor": {
            "capacity": tv(ship.get(482), unit="GJ", source_attr=482),
            "recharge_s": tv((ship.get(55) or 0.0) / 1000.0, unit="s", source_attr=55),
            "stable_pct": tv(ship.get(-72), unit="%", source_attr=-72),
            "depletes_in_s": tv(ship.get(-7), unit="s", source_attr=-7),
            "peak_load": tv(ship.get(-4), unit="GJ/s", source_attr=-4),
            "peak_recharge": tv(ship.get(-2), unit="GJ/s", source_attr=-2),
            "delta": tv(ship.get(-5), unit="GJ/s", source_attr=-5),
        },
        "cap_by_module": [
            {"module": "Light Neutron Blaster II", "cap_use_gj_s": tv(_item_attr(facts, -4), unit="GJ/s", source_attr=-4)}
        ],
        "cap_sensitivity": [],
        "external_dependency": {
            "provenance": "measured",
            "remote_rep": tv(False),
            "cap_transfer": tv(False),
        },
        "consumables": {
            "provenance": "measured",
            "ammo_endurance_s": tv(5.0, unit="s"),
            "paste_endurance_s": tv(0.0, unit="s"),
            "cap_booster": tv(False),
        },
    }

    return {
        "meta": dict(facts.meta),
        "assumptions": {
            "skill_tier_name": facts.skill_tier_name,
            "skill_tier": {str(k): v for k, v in sorted(facts.skills.items())},
            "damage_profile": facts.assumptions["damage_profile"],
            "mode_id": facts.assumptions["mode_id"],
            "implants": facts.assumptions["implants"],
        },
        "views": {
            "firepower": {"overview": view_firepower},
            "survivability": {"overview": view_survivability},
            "mobility": {"overview": view_mobility},
            "resources": {"overview": view_resources},
            "constraints": {"overview": view_constraints},
            "stability": {"overview": view_stability},
        },
    }


# ---------------------------------------------------------------------------
# 形态 A：裸值 + 平行 provenance 表（Lead 裁决 2026-10-18 的对外形态）
# ---------------------------------------------------------------------------


def _strip_typed(node, prefix: str, table: dict, inherited: str = "measured"):
    """形态 B（TypedValue）→ 形态 A（裸值 + 平行表），按渲染层键空间约定。

    键空间规则与 `project/render._leaf_paths` 一致：
    对象 → `prefix.key`；非空对象列表 → `prefix[].key`；其余 → `prefix` 本身。
    """
    is_typed = (
        isinstance(node, dict)
        and "value" in node
        and ("provenance" in node or "unit" in node or "source_attr" in node)
    )
    if is_typed:
        value = node["value"]
        provenance = node.get("provenance") or inherited
        if isinstance(value, (dict, list)) and value:
            # TypedValue 包裹的 dict/list 要**展开成逐叶子键**：
            # 渲染层键空间是 `count_by_level.4` / `weapons[].name`，不是容器路径。
            return _strip_typed(value, prefix, table, provenance)
        table[prefix] = provenance
        return value
    if isinstance(node, dict):
        local = node["provenance"] if isinstance(node.get("provenance"), str) else inherited
        result = {}
        for key, child in node.items():
            if key in ("provenance", "unit", "source_attr", "confidence"):
                continue
            child_path = f"{prefix}.{key}" if prefix else str(key)
            result[key] = _strip_typed(child, child_path, table, local)
        return result
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        return [_strip_typed(item, f"{prefix}[]", table, inherited) for item in node]
    if prefix:
        table[prefix] = inherited
    return node


def _bare_payload(payload: dict) -> dict:
    """把合规样例转成形态 A（裸值 + 平行表 + **稀疏** confidence 表）。

    confidence 按新约定用**叶子路径键**，且只放非 A 的项（这里只有 application.tracking_rad_s = E）。
    """
    bare = {"meta": payload.get("meta", {}), "views": {}, "flags": [], "budget": {}}
    for view_name, entry in payload["views"].items():
        overview = entry.get("overview", entry)
        table: dict[str, str] = {}
        stripped = _strip_typed(overview, "", table)
        confidence: dict[str, str] = {}
        if view_name == "firepower":
            confidence["application.tracking_rad_s"] = "E"
        bare["views"][view_name] = {
            "overview": stripped,
            "provenance": table,
            "flags": [],
            "confidence": confidence,
        }
    return bare


def mutation_bare_missing_table_key(payload, facts):
    """形态 A 下删掉一条 provenance 键 —— 这才是真缺陷（I6 违反）。"""
    table = payload["views"]["firepower"]["provenance"]
    assert "dps.with_reload" in table, f"键空间不含 dps.with_reload：{sorted(table)[:8]}"
    del table["dps.with_reload"]
    return payload, "provenance 错误"


def mutation_bare_illegal_state(payload, facts):
    payload["views"]["mobility"]["provenance"]["speed_mps"] = "estimated"
    return payload, "provenance 错误"


def mutation_bare_ms_raw(payload, facts):
    """形态 A 下把 recharge_s 写成毫秒原值（裸值形态也要能抓）。"""
    raw = facts.ship.get(55)
    assert raw is not None, "样本缺少 rechargeRate(55)"
    payload["views"]["stability"]["overview"]["capacitor"]["recharge_s"] = float(raw)
    return payload, "单位错误"


def mutation_bare_damage_split_percent(payload, facts):
    split = payload["views"]["firepower"]["overview"]["damage_split"]
    split["kinetic"] = 0.6 * 100.0
    split["thermal"] = 0.4 * 100.0
    return payload, "口径错误"


def mutation_sparse_missing_non_default(payload, facts):
    """稀疏表漏掉一条**非 A 登记项**（application.tracking_rad_s = E）。

    漏了会被下游当成默认 A，把「未建模」误读成「口径已对拍」——必须 high。
    """
    table = payload["views"]["firepower"]["confidence"]
    assert "application.tracking_rad_s" in table, f"confidence 表不含该键：{sorted(table)}"
    del table["application.tracking_rad_s"]
    return payload, "字段缺失"


def mutation_sparse_wrong_level(payload, facts):
    payload["views"]["firepower"]["confidence"]["application.tracking_rad_s"] = "A"
    return payload, "字段缺失"


def mutation_sparse_key_not_in_overview(payload, facts):
    payload["views"]["firepower"]["confidence"]["no.such.path"] = "E"
    return payload, "字段缺失"


CONFIDENCE_MUTATIONS = (
    ("稀疏表漏掉非A登记项", mutation_sparse_missing_non_default),
    ("稀疏表把 E 写成 A", mutation_sparse_wrong_level),
    ("稀疏表键不在 overview 里", mutation_sparse_key_not_in_overview),
)


BARE_MUTATIONS = (
    ("形态A：删掉一条 provenance 键", mutation_bare_missing_table_key),
    ("形态A：provenance 值非法", mutation_bare_illegal_state),
    ("形态A：recharge_s 写毫秒原值", mutation_bare_ms_raw),
    ("形态A：damage_split 百分比口径", mutation_bare_damage_split_percent),
)


# ---------------------------------------------------------------------------
# 注入缺陷
# ---------------------------------------------------------------------------


def mutation_missing_field(payload, facts):
    del payload["views"]["firepower"]["overview"]["ammo_endurance_s"]
    return payload, "字段缺失"


def mutation_missing_view(payload, facts):
    del payload["views"]["stability"]
    return payload, "字段缺失"


def mutation_mixed_as_average(payload, facts):
    """把混合 EHP 写成四系平均 —— 红线 N6 的典型违反。

    这里不动引擎（引擎是对的），而是把**实现输出**改成"四系算术平均"，
    用于验证 runner 的 provenance/口径链路能发现"值与来源属性不符"。
    """
    overview = payload["views"]["survivability"]["overview"]
    # 用算术平均冒充（真实的四系纯 EHP 由引擎算，这里只需一个明显不同的数）
    overview["ehp_mixed"]["value"] = overview["ehp_mixed"]["value"] * 1.056
    return payload, "口径错误"


def mutation_ms_raw_value(payload, facts):
    """把 recharge_s 写成**当前档位下**的毫秒存储原值（动态取，不写死）。

    T-12 后回充由 280 s 变 224 s；写死 280000 就不再等于原值，
    会退化成"口径错误"——所以必须从 facts 取真实原值。
    """
    raw = facts.ship.get(55)
    assert raw is not None, "样本缺少 rechargeRate(55)"
    payload["views"]["stability"]["overview"]["capacitor"]["recharge_s"]["value"] = float(raw)
    return payload, "单位错误"


def mutation_warp_multiplier_raw(payload, facts):
    """把 warp_speed_au_s 写成属性原值 600。"""
    payload["views"]["mobility"]["overview"]["warp_speed_au_s"]["value"] = 600.0
    return payload, "单位错误"


def mutation_tracking_x1000(payload, facts):
    """把 tracking_rad_s 写成 ×1000 原值——**已按 3 位小数舍入**（贴近真实实现）。

    同样动态取原值：tracking 受 Motion Prediction 等技能影响，会随技能档变化。
    """
    raw = _item_attr(facts, 160)
    assert raw is not None, "样本缺少 trackingSpeed(160)"
    payload["views"]["firepower"]["overview"]["application"]["tracking_rad_s"]["value"] = round(raw, 3)
    return payload, "单位错误"


def mutation_recharge_rounded_ms(payload, facts):
    """recharge_s 写成**舍入后**的毫秒原值 —— 检验「舍入后仍能抓到」。"""
    raw = facts.ship.get(55)
    assert raw is not None, "样本缺少 rechargeRate(55)"
    payload["views"]["stability"]["overview"]["capacitor"]["recharge_s"]["value"] = round(float(raw), 0)
    return payload, "单位错误"


def mutation_missing_provenance(payload, facts):
    """删掉 speed_mps 的 provenance —— 红线 N3。"""
    del payload["views"]["mobility"]["overview"]["speed_mps"]["provenance"]
    return payload, "provenance 错误"


def mutation_bad_provenance_value(payload, facts):
    payload["views"]["mobility"]["overview"]["speed_mps"]["provenance"] = "estimated"
    return payload, "provenance 错误"


def mutation_damage_split_percent(payload, facts):
    """damage_split 用百分比口径（60/40）——渲染层不应 ×100。"""
    split = payload["views"]["firepower"]["overview"]["damage_split"]
    split["kinetic"]["value"] = 60.0
    split["thermal"]["value"] = 40.0
    return payload, "口径错误"


def mutation_missing_confidence(payload, facts):
    """未建模项（application）漏标 confidence=E。

    Lead 裁决：provenance 只表达「是否依赖假设」，未建模属可信度维度，
    必须用独立字段表达；漏标会让 agent 把参数当成命中结论。
    """
    del payload["views"]["firepower"]["overview"]["application"]["confidence"]
    return payload, "字段缺失"


def mutation_bad_confidence(payload, facts):
    payload["views"]["firepower"]["overview"]["application"]["confidence"] = "F"
    return payload, "字段缺失"


def mutation_alignment_as_ms(payload, facts):
    """align_time_s 被再 ÷1000（-1 引擎已给秒）。"""
    node = payload["views"]["mobility"]["overview"]["align_time_s"]
    node["value"] = node["value"] / 1000.0
    return payload, "口径错误"


def mutation_hardpoints_disagree(payload, facts):
    payload["views"]["resources"]["overview"]["hardpoints_free"]["turret"]["value"] = 99.0
    return payload, "口径错误"


MUTATIONS = (
    ("删掉一个 overview 字段", mutation_missing_field),
    ("删掉整个视角", mutation_missing_view),
    ("混合 EHP 当四系平均（×1.056）", mutation_mixed_as_average),
    ("recharge_s 输出毫秒原值", mutation_ms_raw_value),
    ("warp_speed_au_s 输出属性原值 600", mutation_warp_multiplier_raw),
    ("tracking_rad_s 输出 ×1000 原值", mutation_tracking_x1000),
    ("删掉一个 provenance", mutation_missing_provenance),
    ("provenance 值非法（estimated）", mutation_bad_provenance_value),
    ("未建模项漏标 confidence=E", mutation_missing_confidence),
    ("confidence 值非法（F）", mutation_bad_confidence),
    ("align_time_s 再 ÷1000", mutation_alignment_as_ms),
    ("两视角同名字段不一致", mutation_hardpoints_disagree),
    ("damage_split 用百分比口径", mutation_damage_split_percent),
)


def main() -> int:
    spec = ra.load_field_spec()
    eng = ra.EngineFacts()
    fixture = ra.FIXTURE_DIR / "f1-hecate-killmail.eft"
    facts = eng.build("f1", fixture.read_text(encoding="utf-8"), fixture)

    print("=" * 78)
    print("A) 合规样本应当零缺陷")
    print("=" * 78)
    oracle = ra.ProvenanceOracle()
    conf_oracle = ra.ConfidenceOracle()
    print(f"  provenance 权威表: {'可用 ' + oracle.source if oracle.available else '未就绪 → 仅结构校验'}")
    print(f"  confidence 权威表: {'可用 ' + conf_oracle.source + '（默认 ' + conf_oracle.default + '）' if conf_oracle.available else '未就绪 → 仅合法性校验'}")
    good = _good_payload(facts, oracle)
    defects = ra.check_view_structure("f1", good, spec, facts, oracle, conf_oracle)
    print(f"  缺陷条数 = {len(defects)}")
    for defect in defects:
        print(f"    [意外] {defect.category} {defect.severity} {defect.where}: "
              f"期望 {defect.expected} 实际 {defect.actual} ({defect.note})")
    baseline_ok = not defects

    print()
    print("=" * 78)
    print("B) 注入缺陷必须被抓到，且归类正确")
    print("=" * 78)
    print(f"  {'注入的缺陷':34s} {'期望类别':14s} {'实际命中':20s} 判定")
    print("  " + "-" * 80)
    all_ok = True
    for label, mutation in MUTATIONS:
        mutated = copy.deepcopy(good)
        payload, expected_category = mutation(mutated, facts)
        found = ra.check_view_structure("f1", payload, spec, facts, oracle, conf_oracle)
        hit = {d.category for d in found}
        ok = expected_category in hit
        all_ok = all_ok and ok
        print(f"  {label:34s} {expected_category:14s} "
              f"{('、'.join(sorted(hit)) or '（未命中）'):20s} {'✅' if ok else '❌'}")

    print()
    print("=" * 78)
    print("B2) 形态 A（裸值 + 平行 provenance 表）—— 渲染层的实际形态")
    print("=" * 78)
    bare = _bare_payload(good)
    bare_total = sum(len(entry["provenance"]) for entry in bare["views"].values())
    print(f"  平行表条目总数 = {bare_total}（每个 overview 叶子一条）")
    bare_defects = ra.check_view_structure("f1", bare, spec, facts, oracle, conf_oracle)
    # 只有 application 的 confidence=low 观察允许存在；其余一律算失败
    unexpected = [d for d in bare_defects if d.severity != "low"]
    print(f"  合规形态A 缺陷条数 = {len(bare_defects)}"
          f"（其中 low 观察 {len(bare_defects) - len(unexpected)} 条）")
    for defect in unexpected:
        print(f"    [意外] {defect.category} {defect.severity} {defect.where}: "
              f"期望 {defect.expected} 实际 {defect.actual}")
    bare_ok = not unexpected
    print()
    print(f"  {'注入的缺陷':34s} {'期望类别':14s} {'实际命中':20s} 判定")
    print("  " + "-" * 80)
    bare_mutations_ok = True
    for label, mutation in BARE_MUTATIONS:
        mutated = copy.deepcopy(bare)
        payload, expected_category = mutation(mutated, facts)
        found = ra.check_view_structure("f1", payload, spec, facts, oracle, conf_oracle)
        hit = {d.category for d in found}
        ok = expected_category in hit
        bare_mutations_ok = bare_mutations_ok and ok
        print(f"  {label:34s} {expected_category:14s} "
              f"{('、'.join(sorted(hit)) or '（未命中）'):20s} {'✅' if ok else '❌'}")

    print()
    print(f"  {'注入的缺陷':34s} {'期望类别':14s} {'实际命中':20s} 判定")
    print("  " + "-" * 80)
    conf_ok = True
    for label, mutation in CONFIDENCE_MUTATIONS:
        mutated = copy.deepcopy(bare)
        payload, expected_category = mutation(mutated, facts)
        found = ra.check_view_structure("f1", payload, spec, facts, oracle, conf_oracle)
        hit = {d.category for d in found}
        ok = expected_category in hit
        conf_ok = conf_ok and ok
        print(f"  {label:34s} {expected_category:14s} "
              f"{('、'.join(sorted(hit)) or '（未命中）'):20s} {'✅' if ok else '❌'}")

    print()
    print("=" * 78)
    print("C) 形状样例落盘（给实现方对照）：形态A 与形态B 各一份")
    print("=" * 78)
    sample_path = HERE / "out" / "views-样例.json"
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    sample_path.write_text(
        json.dumps({"note": "runner 自测用的合规样例（形态B=TypedValue）；"
                            "实现方可用 --views-json 喂回来验证",
                    "cases": {"f1-hecate-killmail": good}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    bare_path = HERE / "out" / "views-样例-形态A.json"
    bare_path.write_text(
        json.dumps({"note": "形态A=裸值 + 平行 provenance 表（渲染层的对外形态）",
                    "cases": {"f1-hecate-killmail": bare}},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  已写入 {sample_path}")
    print(f"  已写入 {bare_path}")

    print()
    print("=" * 78)
    passed = baseline_ok and all_ok and bare_ok and bare_mutations_ok and conf_ok
    print(f"自测结论：{'全部通过' if passed else '存在失败项'}"
          f"（形态B 合规零缺陷={baseline_ok} / 形态B 注入全中={all_ok} / "
          f"形态A 合规零缺陷={bare_ok} / 形态A 注入全中={bare_mutations_ok} / "
          f"稀疏confidence 注入全中={conf_ok}）")
    print("=" * 78)
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

