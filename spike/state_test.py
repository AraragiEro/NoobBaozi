import json

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

dogma.load_sde_from_file(sde_path())

HECATE = 35683
MWD = 35658  # 5MN Quad LiF Restrained Microwarpdrive
KEYS = {37: "maxVel", 4: "mass", 552: "sigRadius", 70: "agility", -1: "alignTime", -44: "velBoost"}

SK = {3300: 4, 3301: 5, 3318: 4, 3319: 4, 3327: 4, 3328: 4, 3392: 5, 3394: 5,
      3402: 4, 3426: 4, 3413: 4, 35685: 5, 33093: 4, 20211: 4, 21668: 4}


def show(label, fit):
    c = dogma.calculate({**fit, "character": {"skills": SK}})
    a = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in c["ship"]["attributes"].items()}
    items = c["items"]
    st = [i.get("state") for i in items]
    print(f"  {label:26s} " + " ".join(f"{n}={a.get(k):.3f}" if isinstance(a.get(k), float) else f"{n}={a.get(k)}"
                                       for k, n in KEYS.items()) + f"  item_states={st}")


print("=== 只装 MWD：state 是否影响 ===")
for s in ("offline", "online", "active", "overload"):
    show(f"MWD state={s}", {"ship": {"type_id": HECATE},
                            "items": [{"type_id": MWD, "slot": {"type": "medium", "index": 0}, "state": s}]})

print("\n=== 裸船对照 ===")
show("无装备", {"ship": {"type_id": HECATE}, "items": []})

print("\n=== 完整配装：MWD 分别 online / active（其余不变）===")
km = json.load(open("evefit/spike/kill.json", encoding="utf-8"))[0]
km.pop("zkb", None)
fit = dogma.load_killmail(km)
for s in ("online", "active"):
    items = []
    for x in fit["items"]:
        y = dict(x)
        if y["type_id"] == MWD:
            y["state"] = s
        items.append(y)
    show(f"完整配装 MWD={s}", {"ship": fit["ship"], "items": items})

