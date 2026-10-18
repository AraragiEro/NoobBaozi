import json
import urllib.request

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

UA = {"User-Agent": "dsh-eve-smoke/0.1"}
HECATE = 35683
MODES = {35686: "Defense", 35687: "Propulsion", 35688: "Sharpshooter"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


print("=== 模式类型的属性结构（以 Hecate Defense Mode 35686 为例）===")
d = get("https://esi.evetech.net/latest/universe/types/35686/")
print(f"  name={d.get('name')} group={d.get('group_id')} published={d.get('published')}")
print(f"  attributes: {len(d.get('dogma_attributes', []))}")
for a in d.get("dogma_attributes", []):
    aid, v = a["attribute_id"], a["value"]
    try:
        nm = get(f"https://esi.evetech.net/latest/dogma/attributes/{aid}/")["name"]
    except Exception:
        nm = "?"
    print(f"    {aid:6d} {nm:34s} = {v}")

print("\n=== 引擎验证：Hecate 传不同 mode ===")
dogma.load_sde_from_file(sde_path())
SK = {3300: 4, 3301: 5, 3318: 4, 3319: 4, 3327: 4, 3328: 4, 3392: 5, 3394: 5,
      3402: 4, 3426: 4, 3413: 4, 35685: 5, 33093: 4, 20211: 4, 21668: 4}
KEYS = [(-43, "EHP"), (-12, "DPS"), (-1, "alignTime"), (37, "maxVel"),
        (271, "shEM"), (273, "shKIN"), (267, "arEM"), (269, "arKIN"), (552, "sig")]


def calc(mode=None):
    fit = {"ship": {"type_id": HECATE}, "items": [
        {"type_id": 3178, "slot": {"type": "high", "index": 0}, "state": "active",
         "charge": {"type_id": 12612}}], "character": {"skills": SK}}
    if mode:
        fit["ship"]["mode"] = mode
    c = dogma.calculate(fit)
    a = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in c["ship"]["attributes"].items()}
    tag = "无模式" if mode is None else MODES.get(mode, str(mode))
    print(f"  {tag:14s} " + "  ".join(f"{n}={a.get(i):.3f}" for i, n in KEYS) +
          f"   mode节点={'有' if c.get('mode') else '无'}")


calc(None)
for mid in MODES:
    calc(mid)
