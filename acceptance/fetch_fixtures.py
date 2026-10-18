"""从 zkillboard 抓 3 条真实 killmail 作为验收样本（f4 / f5 / f6）。

为什么用 zkillboard：这是**真实玩家配装**的唯一低成本来源；本轮要补的三个覆盖空白
（非 Hecate 船型 / 非 MWD 推进 / 极简配装）都没法靠自造 EFT 代表真实分布。

落盘原则：
- 存**原始 killmail JSON**（不转 EFT），让验收走与 `--eft killmail.json` 同一条 KB 通道；
- 文件名 `f4-*.json` / `f5-*.json` / `f6-*.json`，`case_name` 即文件名 stem。

踩过的坑（照做，别重踩）：
- 必须带描述性 `User-Agent`，zkillboard 会拒匿名 UA；
- 路径**必须带尾斜杠**；
- `< 5 分钟` 的 killmail 不下发（查最近 7 天即可）；
- 响应用 gzip，得自己解压。

用法：
    $env:UV_CACHE_DIR = (Join-Path $PWD "evefit/spike/uvcache")
    uv run --python 3.13 --no-project --with "eveshipfit-dogma-engine[sde]==13.6.0" `
      python evefit/acceptance/fetch_fixtures.py
"""
from __future__ import annotations

import gzip
import io
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

#: 目标特征 → 候选船型（按优先级）
CANDIDATES = {
    "f4-non-hecate": [29990, 29984, 29986, 29988, 626, 621],   # T3C 优先，其次 T1 巡洋
    "f5-no-mwd": [621, 626, 629, 2006, 3766, 11196],           # 巡洋/BC，先看是否无 MWD
    "f6-minimal-fit": [582, 583, 584, 585, 586, 587],          # 护卫舰，容易极简
}

MAX_CANDIDATES = 8


def fetch(url: str) -> object:
    request = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(request, timeout=40) as response:
        raw = response.read()
        if response.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw.decode("utf-8"))


def recent_kills(ship_type_id: int, limit: int = 12) -> list[dict]:
    url = f"https://zkillboard.com/api/kills/shipTypeID/{ship_type_id}/pastSeconds/604800/"
    data = fetch(url)
    if not isinstance(data, list):
        return []
    return data[:limit]


def full_killmail(killmail_id: int) -> dict | None:
    url = f"https://zkillboard.com/api/killID/{killmail_id}/"
    data = fetch(url)
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None


def main() -> int:
    from evefit.config import paths as paths_mod
    from evefit.engine.assumptions import default_assumptions
    from evefit.engine import adapter
    from evefit.engine.normalize import normalize_fit
    from evefit.sde.index import Index

    engine = adapter.Engine()
    index = Index.open(paths_mod.db_path())

    def module_names(fit: dict) -> list[str]:
        names = []
        for item in fit.get("items") or []:
            slot = (item.get("slot") or {}).get("type")
            if slot == "cargo":
                continue
            row = index.get_type(item.get("type_id"))
            names.append(row["name"] if row else str(item.get("type_id")))
        return names

    def fitted_count(fit: dict) -> int:
        return len(module_names(fit))

    def accept_f4(fit: dict) -> bool:
        ship = index.get_type((fit.get("ship") or {}).get("type_id"))
        return bool(ship) and ship["name"] != "Hecate" and fitted_count(fit) >= 3

    def accept_f5(fit: dict) -> bool:
        names = " | ".join(module_names(fit)).lower()
        return "microwarpdrive" not in names

    def accept_f6(fit: dict) -> bool:
        return fitted_count(fit) <= 2

    checkers = {"f4-non-hecate": accept_f4, "f5-no-mwd": accept_f5, "f6-minimal-fit": accept_f6}
    saved: dict[str, dict] = {}

    for target, ship_ids in CANDIDATES.items():
        if target in saved:
            continue
        print(f"--- {target} ---")
        for ship_id in ship_ids:
            if target in saved:
                break
            ship_row = index.get_type(ship_id)
            ship_name = ship_row["name"] if ship_row else str(ship_id)
            try:
                kills = recent_kills(ship_id)
            except Exception as exc:  # noqa: BLE001
                print(f"  {ship_name}: 列表拉取失败 {type(exc).__name__}: {exc}")
                continue
            print(f"  {ship_name}: {len(kills)} 条候选")
            got = 0
            for entry in kills:
                if got >= MAX_CANDIDATES:
                    break
                kid = entry.get("killmail_id")
                if not isinstance(kid, int):
                    continue
                got += 1
                time.sleep(1.1)                     # 对 zkillboard 客气点
                try:
                    killmail = full_killmail(kid)
                except Exception as exc:  # noqa: BLE001
                    print(f"    {kid}: 详情拉取失败 {type(exc).__name__}")
                    continue
                if not isinstance(killmail, dict) or "victim" not in killmail:
                    continue
                normalized = normalize_fit(json.dumps([killmail], ensure_ascii=False))
                if normalized["fit"] is None:
                    continue
                fit = normalized["fit"]
                if not checkers[target](fit):
                    continue
                out_path = FIXTURES / f"{target}.json"
                out_path.write_text(json.dumps(killmail, ensure_ascii=False, indent=1), encoding="utf-8")
                saved[target] = {
                    "killmail_id": kid,
                    "ship": ship_name,
                    "fitted": fitted_count(fit),
                    "modules": module_names(fit)[:8],
                    "path": out_path.name,
                }
                print(f"    ✅ 采纳 killmail {kid}（{ship_name}，{fitted_count(fit)} 个已装模块）")
                break
        if target not in saved:
            print(f"  ⚠️ 未找到符合条件的样本：{target}")

    index.close()
    print()
    print("=== 结果 ===")
    print(json.dumps(saved, ensure_ascii=False, indent=1))
    return 0 if len(saved) == len(CANDIDATES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
