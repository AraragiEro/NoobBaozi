"""第 5 轮：不加筛选的随机抽样（10 条）。

zkillboard 的 `/api/kills/` 直接返回**完整** killmail（含 victim.items），一次请求即可。
只剔除「不是玩家舰船」的样本（太空舱 / 建筑 / 无人机 / 可部署物），其余全收。
"""
from __future__ import annotations

import gzip
import json
import random
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(".").resolve()))

UA = "noobbaozi-acceptance-qa/1.0 (+https://github.com/noobbaozi; QA random sampling)"
HEADERS = {"User-Agent": UA, "Accept-Encoding": "gzip", "Accept": "application/json"}
COUNT = 10
FIXTURES = Path("noobbaozi/acceptance/fixtures")

BLOCK_GROUPS = ("capsule", "structure", "drone", "fighter", "deployable",
                "mobile", "starbase", "sentry", "orbital", "sovereignty")


def fetch(url: str):
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=40) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def main() -> int:
    from noobbaozi.config import paths as paths_mod
    from noobbaozi.engine.normalize import normalize_fit
    from noobbaozi.sde.index import Index

    index = Index.open(paths_mod.db_path())
    random.seed(20261018)

    existing = set()
    for path in FIXTURES.glob("*.json"):
        if path.name.endswith(".expected.json"):
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        km = raw[0] if isinstance(raw, list) and raw else raw
        if isinstance(km, dict) and isinstance(km.get("killmail_id"), int):
            existing.add(km["killmail_id"])

    pool: list[dict] = []
    for endpoint in ("kills", "losses"):
        try:
            data = fetch(f"https://zkillboard.com/api/{endpoint}/")
        except Exception as exc:  # noqa: BLE001
            print(f"{endpoint}: 拉取失败 {type(exc).__name__}")
            continue
        items = data if isinstance(data, list) else []
        print(f"{endpoint}: {len(items)} 条")
        pool.extend(items)

    random.shuffle(pool)
    saved = []
    seen: set[int] = set()
    for killmail in pool:
        if len(saved) >= COUNT:
            break
        if not isinstance(killmail, dict):
            continue
        killmail_id = killmail.get("killmail_id")
        if not isinstance(killmail_id, int) or killmail_id in existing or killmail_id in seen:
            continue
        victim = killmail.get("victim") or {}
        ship_id = victim.get("ship_type_id")
        row = index.get_type(ship_id) if ship_id else None
        if row is None:
            continue
        group = (row["group_name"] or "").lower()
        if any(block in group for block in BLOCK_GROUPS):
            print(f"  {killmail_id}: 非玩家舰船（{row['group_name']}），跳过")
            continue
        if not victim.get("items"):
            print(f"  {killmail_id}: victim 无 items，跳过")
            continue
        normalized = normalize_fit(json.dumps([killmail], ensure_ascii=False))
        if normalized["fit"] is None:
            print(f"  {killmail_id}: 归一化失败，跳过")
            continue
        seen.add(killmail_id)
        stem = f"f{47 + len(saved)}-random-{row['name'].lower().replace(' ', '-')}"
        out = FIXTURES / f"{stem}.json"
        out.write_text(json.dumps(killmail, ensure_ascii=False, indent=1), encoding="utf-8")
        fitted = [i for i in (normalized["fit"].get("items") or [])
                  if (i.get("slot") or {}).get("type") not in ("cargo", "implant", "booster")]
        saved.append({"file": out.name, "killmail_id": killmail_id,
                      "ship": row["name"], "group": row["group_name"],
                      "modules": len(fitted)})
        print(f"  ✅ {out.name}: {row['name']} / {row['group_name']} / {len(fitted)} 模块")

    index.close()
    print()
    print(json.dumps(saved, ensure_ascii=False, indent=1))
    return 0 if len(saved) == COUNT else 1


if __name__ == "__main__":
    raise SystemExit(main())
