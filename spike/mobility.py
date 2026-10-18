import json

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

dogma.load_sde_from_file(sde_path())
km = json.load(open("evefit/spike/kill.json", encoding="utf-8"))[0]
km.pop("zkb", None)
fit = dogma.load_killmail(km)

SK = {3300: 4, 3301: 5, 3318: 4, 3319: 4, 3327: 4, 3328: 4, 3392: 5, 3394: 5,
      3402: 4, 3426: 4, 3413: 4, 35685: 5, 33093: 4, 20211: 4, 21668: 4}

KEYS = {37: "maxVelocity", 70: "agility", 4: "mass", 552: "signatureRadius",
        600: "warpSpeedMultiplier", 37: "maxVelocity", -1: "alignTime", -44: "velocityBoost",
        76: "maxTargetRange", 564: "scanResolution", 192: "maxLockedTargets", 79: "scanSpeed"}


def run(label, fix_state=None):
    items = []
    for x in fit["items"]:
        y = dict(x)
        st = y.get("slot", {}).get("type")
        if fix_state and st in ("high", "med", "low"):
            y["state"] = fix_state
        items.append(y)
    c = dogma.calculate({"ship": fit["ship"], "items": items, "character": {"skills": SK}})
    a = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in c["ship"]["attributes"].items()}
    print(f"  {label:22s} " + "  ".join(
        f"{n}={a.get(i):.3f}" if isinstance(a.get(i), float) else f"{n}={a.get(i)}"
        for i, n in KEYS.items()))
    return a


print("=== 机动相关：状态对速度/对齐/信号的影响 ===")
run("引擎默认(读killmail)")
run("全 online", "online")
run("全 active", "active")

print("\n=== 单独看 MWD 的作用 ===")
mwd_type = None
for x in fit["items"]:
    if x["type_id"] == 35658:
        mwd_type = x
print("  MWD 装备存在:", mwd_type is not None, "其 state =", mwd_type.get("state") if mwd_type else None)

print("\n=== 单位/量纲抽查（alignment）===")
c = dogma.calculate({"ship": fit["ship"], "items": fit["items"], "character": {"skills": SK}})
a = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in c["ship"]["attributes"].items()}
mass = a.get(4)
agil = a.get(70)
import math
align_calc = -math.log(0.25) * agil * mass / 1_000_000
print(f"  mass={mass}  agility={agil}")
print(f"  公式 alignTime = -ln(0.25)*agility*mass/1e6 = {align_calc:.4f}  vs 引擎 -1 = {a.get(-1):.4f}")
print(f"  maxVelocity={a.get(37)}  velocityBoost={a.get(-44)}")
print(f"  若 velocityBoost 是百分比：base = {a.get(37) / (1 + a.get(-44) / 100):.3f}")
