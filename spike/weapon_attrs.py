import json
import urllib.request

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

UA = {"User-Agent": "dsh-eve-smoke/0.1 (feasibility test)"}
_cache = {}


def name_of(aid):
    if aid in _cache:
        return _cache[aid]
    try:
        req = urllib.request.Request(
            f"https://esi.evetech.net/latest/dogma/attributes/{aid}/", headers=UA)
        with urllib.request.urlopen(req, timeout=30) as r:
            nm = json.loads(r.read())["name"]
    except Exception:
        nm = "?"
    _cache[aid] = nm
    return nm


dogma.load_sde_from_file(sde_path())

km = json.load(open("evefit/spike/kill.json", encoding="utf-8"))[0]
km.pop("zkb", None)
fit = dogma.load_killmail(km)

SKILLS = {3300: 4, 3301: 4, 3318: 4, 3319: 4, 3327: 4, 3328: 4, 3392: 5, 3394: 5,
          3402: 4, 3426: 4, 3413: 4, 35685: 5, 33093: 4, 20211: 4, 21668: 4}

c = dogma.calculate({**fit, "character": {"skills": SKILLS}})

ship = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in c["ship"]["attributes"].items()}
print("=== ship 级·非负属性（前 60 个有值的）===")
pos = [(k, v) for k, v in sorted(ship.items()) if k > 0 and v]
for k, v in pos[:60]:
    print(f"  {k:6d} {name_of(k):44s} {v}")

print("\n=== 单件武器（blaster）的全部属性 ===")
gun = None
for i, item in enumerate(c["items"]):
    if fit["items"][i]["type_id"] == 3178:
        gun = (i, item)
        break
idx, gi = gun
ga = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in gi["attributes"].items()}
print(f"  item index={idx} type=3178 charge={gi.get('charge') is not None}")
for k, v in sorted(ga.items()):
    if v:
        print(f"  {k:6d} {name_of(k):44s} {v}")

print("\n=== 弹药（Void S）的四系伤害 ===")
ch = gi.get("charge")
if ch:
    ca = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in ch["attributes"].items()}
    for k, v in sorted(ca.items()):
        if v and k in (114, 116, 117, 118, 119, 120, 121, 122, 123, 124, 125, 126, 127, 128, 129):
            print(f"  {k:6d} {name_of(k):44s} {v}")
    print("  全部非零：")
    for k, v in sorted(ca.items()):
        if v:
            print(f"   {k:6d} {name_of(k):42s} {v}")
