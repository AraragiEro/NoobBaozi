import json
import urllib.request

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

UA = {"User-Agent": "dsh-eve-smoke/0.1 (feasibility test)"}
HECATE = 35683


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def post(url, payload):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={**UA, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


print("=== 1. 按名字反查类型 ID（/universe/ids/）===")
names = ["Defense Mode", "Propulsion Mode", "Sharpshooter Mode", "Assault Mode",
         "Defensive Mode", "Hecate Defense Mode", "Hecate Mode",
         "Sharpshooter Mode I", "Propulsion Mode I", "Defense Mode I"]
res = post("https://esi.evetech.net/latest/universe/ids/", names)
types = res.get("inventory_types", [])
print(f"  命中 {len(types)} 个:")
for t in types:
    print(f"    {t['id']:8d}  {t['name']}")

print("\n=== 2. 这些 type 的所属组 ===")
group_cache = {}
for t in types:
    d = get(f"https://esi.evetech.net/latest/universe/types/{t['id']}/")
    gid = d.get("group_id")
    if gid not in group_cache:
        g = get(f"https://esi.evetech.net/latest/universe/groups/{gid}/")
        group_cache[gid] = (g.get("name"), g.get("category_id"))
    gname, gcat = group_cache[gid]
    print(f"    {t['id']:8d} {t['name']:26s} group={gid} ({gname}) category={gcat}")

print("\n=== 3. 引擎：Hecate 传不同 mode 的结果 ===")
dogma.load_sde_from_file(sde_path())
SK = {3300: 4, 3301: 5, 3318: 4, 3319: 4, 3327: 4, 3328: 4, 3392: 5, 3394: 5,
      3402: 4, 3426: 4, 3413: 4, 35685: 5, 33093: 4, 20211: 4, 21668: 4}
GUN = 3178
AMMO = 12612
KEYS = {(-43): "EHP", (-12): "DPS", 271: "shieldEM", 273: "shieldKIN",
        37: "maxVel", 102: "turretSlots"}


def calc(mode=None):
    fit = {"ship": {"type_id": HECATE}, "items": [
        {"type_id": GUN, "slot": {"type": "high", "index": 0}, "state": "active",
         "charge": {"type_id": AMMO}}], "character": {"skills": SK}}
    if mode:
        fit["ship"]["mode"] = mode
    c = dogma.calculate(fit)
    a = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in c["ship"]["attributes"].items()}
    line = "  ".join(f"{n}={a.get(i):.3f}" if isinstance(a.get(i), float) else f"{n}={a.get(i)}"
                     for i, n in KEYS.items())
    print(f"  mode={str(mode):8s} {line}  彩蛋mode结果={c.get('mode') is not None}")


calc(None)
for t in types:
    try:
        calc(t["id"])
    except Exception as e:
        print(f"  mode={t['id']} 失败: {e}")
