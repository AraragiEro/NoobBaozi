"""从 zkillboard 抓真实 killmail 作为验收样本（按轮次规格驱动，不做随机抓取）。

随机抓 30 条大概率全是同一类船，所以这里**按覆盖目标分批**、每批固定船型/判据，
并强制**去重**（与已有样本、同轮内都不允许同型船）。

**踩坑记录（全部实测过，别再踩）**
1. `/api/kills/shipTypeID/<id>/` 按「**涉入**」过滤，不是按受害者——会捞到一堆无关船；
   必须用 `/api/losses/shipTypeID/<id>/`（按受害者）。
2. 仍要**硬校验** `victim.ship_type_id == 查询船型`。
3. 植入体/增效体也算 `items`，按"已装模块"计数会把「只有植入体的船」误判成配装丰富
   → 只数真实模块槽位（high/medium/low/rig/subsystem/service/fighter_*）。
4. Capsule（太空舱，type 670）不是船也没有配装 → 排除。
5. 必须带描述性 `User-Agent`、路径带尾斜杠、`< 5 分钟` 的 killmail 不下发、响应 gzip。

用法：
    $env:UV_CACHE_DIR = (Join-Path $PWD "evefit/spike/uvcache")
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python evefit/acceptance/fetch_fixtures.py --round 2
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FIXTURES = HERE / "fixtures"
sys.path.insert(0, str(ROOT.parent))

UA = "evefit-acceptance-qa/1.0 (+https://github.com/evefit; QA fixture fetch)"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"}

COMBAT_SLOTS = ("high", "medium", "low", "rig", "subsystem", "service",
                "fighter_tube", "fighter_bay")

MAX_PER_TARGET = 10       # 每个目标最多看几条详情
SLEEP = 1.2               # 请求间隔（要求 1 请求/秒以上）
PREFER_MIN_MODULES = 5    # 优先"像一套真实配装"的样本

#: 第 0 轮（已落地，仅重建用）
ROUND0 = [
    {"stem": "f4-non-hecate", "ships": [29990], "label": "T3C Loki（非 Hecate）"},
    {"stem": "f5-no-mwd", "ships": [24698], "label": "BC Drake（AB，无 MWD）"},
    {"stem": "f6-minimal-fit", "ships": [584], "label": "Frigate Griffin（只有船体）"},
]

#: 第 1 轮：船体结构覆盖（已落地）
ROUND1 = [
    {"stem": "f7-t3c-legion", "ships": [29986], "label": "T3C Legion（Amarr）"},
    {"stem": "f8-t3c-tengu", "ships": [29984], "label": "T3C Tengu（Caldari）"},
    {"stem": "f9-t3c-proteus", "ships": [29988], "label": "T3C Proteus（Gallente）"},
    {"stem": "f10-t3d-confessor", "ships": [34317], "label": "T3D Confessor（有 mode）"},
    {"stem": "f11-t3d-svipul", "ships": [34562], "label": "T3D Svipul（有 mode）"},
    {"stem": "f12-logistics-oneiros", "ships": [11989], "label": "后勤 Oneiros"},
    {"stem": "f13-logistics-scimitar", "ships": [11978], "label": "后勤 Scimitar"},
    {"stem": "f14-command-damnation", "ships": [22474], "label": "指挥 Damnation"},
    {"stem": "f15-battleship-raven", "ships": [638], "label": "战列 Raven（导弹）"},
    {"stem": "f16-hac-ishtar", "ships": [12005], "label": "HAC Ishtar（无人机）"},
]

#: 第 2 轮：武器族与防御族覆盖（本轮）
ROUND2 = [
    {"stem": "f17-laser-zealot", "ships": [12003], "label": "激光 Zealot（装甲坦）",
     "require_any": ["Laser"]},
    {"stem": "f18-projectile-vagabond", "ships": [11999], "label": "弹射 Vagabond（护盾坦）",
     "require_any": ["Autocannon", "Artillery"]},
    {"stem": "f19-missile-cerberus", "ships": [11993], "label": "导弹 Cerberus（护盾坦）",
     "require_any": ["Launcher", "Missile"]},
    {"stem": "f20-drone-dominix", "ships": [645], "label": "无人机 Dominix（装甲坦）",
     "require_any": ["Drone Damage Amplifier", "Drone Link Augmentor", "Omnidirectional"]},
    {"stem": "f21-shield-sleipnir", "ships": [22444], "label": "指挥 Sleipnir（护盾坦）",
     "require_any": ["Shield"]},
    {"stem": "f22-armor-absolution", "ships": [22448], "label": "指挥 Absolution（装甲坦）",
     "require_any": ["Armor", "Plates", "Energized Membrane"]},
    {"stem": "f23-cap-booster", "ships": [11987, 24694, 16233],
     "label": "带电容注射器（cap booster）",
     "require_any": ["Cap Booster", "Capacitor Booster"]},
    {"stem": "f24-ancillary-rep", "ships": [24694, 16233, 12015, 17843],
     "label": "带辅助修（ancillary）", "require_any": ["Ancillary"]},
    {"stem": "f25-nos-neut", "ships": [17843, 16233, 12015, 4310],
     "label": "带吸电/毁电（nos/neut）", "require_any": ["Nosferatu", "Neutralizer"]},
    {"stem": "f26-carrier-fighter", "ships": [23911, 24483, 23757],
     "label": "航母 fighter（-55/-56/-58 链路）",
     "require_slot_any": ["fighter_tube", "fighter_bay"]},
]

#: 第 3 轮：极端形态
ROUND3 = [
    {"stem": "f27-dual-prop", "ships": [16229, 624, 644, 24688],
     "label": "双推进（AB + MWD，通常非法）",
     "require_all": ["Afterburner", "Microwarpdrive"]},
    {"stem": "f28-industrial", "ships": [1944, 657, 648, 655],
     "label": "工业舰 / 运输舰", "min_modules": 1},
    {"stem": "f29-drones-only", "ships": [626, 645, 16229],
     "label": "只带无人机、无武器", "require_slot_any": ["drone_bay"],
     "forbid_any": ["Blaster", "Railgun", "Autocannon", "Artillery", "Laser",
                    "Launcher", "Smartbomb"]},
    {"stem": "f30-smartbomb", "ships": [47466, 644, 17920, 645],
     "label": "智能炸弹为主", "min_count": {"Smartbomb": 2}},
    {"stem": "f31-bulkhead", "ships": [624, 24688, 641, 644],
     "label": "bulkhead 结构坦", "require_any": ["Bulkhead"]},
    {"stem": "f32-t3c-no-cloak", "ships": [29986, 29984, 29988, 29990],
     "label": "带子系统但不带隐形的 T3C（T3C 只有 4 艘，允许复用船型）",
     "allow_reuse": True,
     "require_slot_any": ["subsystem"], "forbid_any": ["Cloaking"]},
    {"stem": "f33-cargo-heavy", "ships": [1944, 657, 648, 655],
     "label": "全货舱装载（货舱物品 > 50）", "min_cargo": 50},
    {"stem": "f34-no-charge", "ships": [16229, 624, 644, 641, 642],
     "label": "武器空弹/损伤态（弹药为 null → 验 T-27 混装口径）",
     "require_no_charge": True},
    {"stem": "f35-t3d-jackdaw", "ships": [34828, 89808],
     "label": "T3D Jackdaw / Skua（modes 命名）"},
    {"stem": "f36-battleship-apoc", "ships": [642, 641],
     "label": "Apocalypse / Megathron（另两种战列）"},
]

ROUNDS = {0: ROUND0, 1: ROUND1, 2: ROUND2, 3: ROUND3}


def fetch(url: str) -> object:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=40) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def list_losses(ship_type_id: int) -> list[dict]:
    url = f"https://zkillboard.com/api/losses/shipTypeID/{ship_type_id}/pastSeconds/604800/"
    data = fetch(url)
    return data if isinstance(data, list) else []


def full_killmail(killmail_id: int) -> dict | None:
    data = fetch(f"https://zkillboard.com/api/killID/{killmail_id}/")
    if isinstance(data, list) and data:
        return data[0]
    return data if isinstance(data, dict) else None


def load_existing() -> tuple[set[int], set[int]]:
    """已有样本的 (killmail_id, victim ship_type_id)，用于**去重**。"""
    killmail_ids: set[int] = set()
    ship_ids: set[int] = set()
    for path in sorted(FIXTURES.glob("*.json")):
        if path.name.endswith(".expected.json"):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        killmail = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(killmail, dict):
            continue
        if isinstance(killmail.get("killmail_id"), int):
            killmail_ids.add(killmail["killmail_id"])
        victim_ship = (killmail.get("victim") or {}).get("ship_type_id")
        if isinstance(victim_ship, int):
            ship_ids.add(victim_ship)
    return killmail_ids, ship_ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="按轮次规格抓取 KB 样本")
    parser.add_argument("--round", type=int, default=2, choices=sorted(ROUNDS))
    parser.add_argument("--only", default="", help="逗号分隔的 stem 列表，只抓这些")
    parser.add_argument("--force", action="store_true", help="覆盖已存在的样本")
    args = parser.parse_args(argv)
    specs = ROUNDS[args.round]
    if args.only:
        wanted = {s.strip() for s in args.only.split(",") if s.strip()}
        specs = [s for s in specs if s["stem"] in wanted]
    if not args.force:
        skipped = [s["stem"] for s in specs if (FIXTURES / f"{s['stem']}.json").is_file()]
        if skipped:
            print(f"已有样本，跳过（幂等）：{skipped}")
        specs = [s for s in specs if (FIXTURES / f"{s['stem']}.json").is_file() is False]

    from evefit.config import paths as paths_mod
    from evefit.engine.assumptions import default_assumptions
    from evefit.engine import adapter
    from evefit.engine.normalize import normalize_fit
    from evefit.sde.index import Index

    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())
    existing_killmails, existing_ships = load_existing()
    print(f"已有样本：{len(existing_killmails)} 条 killmail / "
          f"{len(existing_ships)} 种船（用于去重）")

    def combat_items(fit: dict) -> list[dict]:
        return [item for item in (fit.get("items") or [])
                if (item.get("slot") or {}).get("type") in COMBAT_SLOTS]

    def module_names(fit: dict) -> list[str]:
        names = []
        for item in combat_items(fit):
            row = index.get_type(item.get("type_id"))
            names.append(row["name"] if row else str(item.get("type_id")))
        return names

    def matches(spec: dict, fit: dict) -> bool:
        joined = " | ".join(module_names(fit)).lower()
        require_slot_any = spec.get("require_slot_any") or (
            [spec["require_slot"]] if spec.get("require_slot") else [])
        if require_slot_any:
            slots = {(item.get("slot") or {}).get("type") for item in (fit.get("items") or [])}
            if not any(slot in slots for slot in require_slot_any):
                return False
        require_any = spec.get("require_any") or []
        if require_any and not any(key.lower() in joined for key in require_any):
            return False
        require_all = spec.get("require_all") or []
        if require_all and not all(key.lower() in joined for key in require_all):
            return False
        for key, minimum in (spec.get("min_count") or {}).items():
            if joined.lower().count(key.lower()) < minimum:
                return False
        min_cargo = spec.get("min_cargo")
        if min_cargo:
            cargo_count = sum(1 for item in (fit.get("items") or [])
                              if (item.get("slot") or {}).get("type") == "cargo")
            if cargo_count < min_cargo:
                return False
        if spec.get("require_no_charge"):
            weapons = [item for item in (fit.get("items") or [])
                       if (item.get("slot") or {}).get("type") == "high"]
            if not weapons:
                return False
            if any(item.get("charge") for item in weapons):
                return False
        forbid_any = spec.get("forbid_any") or []
        if forbid_any and any(key.lower() in joined for key in forbid_any):
            return False
        return True

    used_ships = set(existing_ships)
    saved: list[dict] = []
    stats = {"listed": 0, "detail": 0, "dup_ship": 0, "dup_kill": 0,
             "victim_mismatch": 0, "capsule": 0, "no_modules": 0,
             "criteria_miss": 0, "ok": 0}

    for spec in specs:
        stem = spec["stem"]
        print(f"--- {stem}：{spec['label']} ---")
        best: dict | None = None
        found_ship: dict | None = None
        for ship_id in spec["ships"]:
            if best is not None:
                break
            if ship_id in used_ships and not spec.get("allow_reuse"):
                print(f"  {ship_id}: 该船型已被覆盖，跳过（去重）")
                stats["dup_ship"] += 1
                continue
            ship_row = index.get_type(ship_id)
            ship_name = ship_row["name"] if ship_row else str(ship_id)
            try:
                losses = list_losses(ship_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  {ship_name}: 列表失败 {type(exc).__name__}: {exc}")
                continue
            stats["listed"] += len(losses)
            print(f"  {ship_name}({ship_id}): {len(losses)} 条损失候选")
            for entry in losses[:MAX_PER_TARGET]:
                killmail_id = entry.get("killmail_id")
                if not isinstance(killmail_id, int) or killmail_id in existing_killmails:
                    stats["dup_kill"] += 1
                    continue
                time.sleep(SLEEP)
                try:
                    killmail = full_killmail(killmail_id)
                except Exception as exc:  # noqa: BLE001
                    print(f"    {killmail_id}: 详情失败 {type(exc).__name__}")
                    continue
                stats["detail"] += 1
                if not isinstance(killmail, dict) or "victim" not in killmail:
                    continue
                victim_ship_id = (killmail.get("victim") or {}).get("ship_type_id")
                if victim_ship_id != ship_id:
                    stats["victim_mismatch"] += 1
                    continue
                victim_row = index.get_type(victim_ship_id)
                if not victim_row or victim_row["group_name"] == "Capsule":
                    stats["capsule"] += 1
                    continue
                normalized = normalize_fit(json.dumps([killmail], ensure_ascii=False))
                if normalized["fit"] is None:
                    continue
                fit = normalized["fit"]
                modules = combat_items(fit)
                if not modules:
                    stats["no_modules"] += 1
                    continue
                if not matches(spec, fit):
                    stats["criteria_miss"] += 1
                    continue
                record = {"killmail": killmail, "fit": fit, "modules": modules,
                          "killmail_id": killmail_id}
                if len(modules) >= spec.get("min_modules", PREFER_MIN_MODULES):
                    best = record
                    found_ship = ship_row
                    break
                if best is None:
                    best = record
                    found_ship = ship_row
        if best is None:
            print("  ⚠️ 未找到符合判据的样本")
            continue

        out_path = FIXTURES / f"{stem}.json"
        out_path.write_text(json.dumps(best["killmail"], ensure_ascii=False, indent=1),
                            encoding="utf-8")
        victim_ship = (best["killmail"].get("victim") or {}).get("ship_type_id")
        used_ships.add(victim_ship)
        existing_killmails.add(best["killmail_id"])
        asm = default_assumptions(best["fit"], engine, index)
        saved.append({
            "file": out_path.name,
            "killmail_id": best["killmail_id"],
            "ship": found_ship["name"] if found_ship else "?",
            "group": found_ship["group_name"] if found_ship else "?",
            "combat_modules": len(best["modules"]),
            "modules": module_names(best["fit"])[:10],
            "modes": [m["name"] for m in index.modes_for_ship(victim_ship or 0)],
            "skill_tier": f"{asm['skill_tier_name']}({len(asm['skill_tier'])})",
        })
        stats["ok"] += 1
        print(f"  ✅ {best['killmail_id']}：{len(best['modules'])} 个模块 "
              f"{module_names(best['fit'])[:6]}")

    index.close()
    print()
    print("=== 抓取统计 ===", stats)
    print("=== 本轮样本 ===")
    print(json.dumps(saved, ensure_ascii=False, indent=1))
    return 0 if len(saved) == len(specs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
