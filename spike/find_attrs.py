import json
import os
import time
import urllib.request

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

UA = {"User-Agent": "dsh-eve-smoke/0.1 (feasibility test)"}
CACHE = "evefit/spike/attr_names.json"


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


names = json.load(open(CACHE, encoding="utf-8")) if os.path.exists(CACHE) else {}

t = get("https://esi.evetech.net/latest/universe/types/35683/")
attrs = {a["attribute_id"]: a["value"] for a in t["dogma_attributes"]}
print("Hecate 属性数:", len(attrs))

todo = [i for i in attrs if str(i) not in names]
print("需查询名称:", len(todo))
for n, aid in enumerate(todo, 1):
    try:
        names[str(aid)] = get(f"https://esi.evetech.net/latest/dogma/attributes/{aid}/")["name"]
    except Exception:
        names[str(aid)] = "?"
    if n % 20 == 0:
        print(f"  ...{n}/{len(todo)}")
        json.dump(names, open(CACHE, "w", encoding="utf-8"))
    time.sleep(0.02)
json.dump(names, open(CACHE, "w", encoding="utf-8"), ensure_ascii=False)

print("\n=== 含 heat / drone / launcher / slotsLeft 的属性 ===")
KEY = ("heat", "drone", "launcher", "slotsleft", "turret", "bandwidth", "capacity")
for aid, v in sorted(attrs.items()):
    nm = names.get(str(aid), "?")
    low = nm.lower()
    if any(k in low for k in KEY):
        print(f"  {aid:6d} {nm:38s} = {v}")

print("\n=== 全部属性（便于人工扫一遍）===")
for aid, v in sorted(attrs.items()):
    print(f"  {aid:6d} {names.get(str(aid), '?'):38s} = {v}")
