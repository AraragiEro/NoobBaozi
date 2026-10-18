import json
import time

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

dogma.load_sde_from_file(sde_path())

km = json.load(open("noobbaozi/spike/kill.json", encoding="utf-8"))[0]
km.pop("zkb", None)
fit = dogma.load_killmail(km)

SKILLS = {3300: 5, 3301: 5, 3318: 5, 3319: 5, 3327: 5, 3328: 5, 3392: 5, 3394: 5,
          3402: 5, 3426: 5, 3413: 5, 35685: 5, 33093: 5, 20211: 5, 21668: 5}
all5 = {**SKILLS}


def sa(calc):
    return {int(k): (v["value"] if isinstance(v, dict) else v)
            for k, v in calc["ship"]["attributes"].items()}


print("=" * 62)
print("A. 单位确认：dps x cycleTime 是否等于 volley")
print("=" * 62)
c = dogma.calculate({**fit, "character": {"skills": all5}})
for i, item in enumerate(c["items"]):
    a = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in item["attributes"].items()}
    if -21 in a and a[-21]:
        vol, dps, cyc = a[-21], a[-12], a.get(-3)
        print(f"  item{i}: volley={vol:.4f} dps={dps:.4f} cycleTime={cyc:.4f}")
        print(f"          volley/dps={vol / dps:.4f}  (若=cycleTime 的秒数则单位一致)")
        break

print()
print("=" * 62)
print("B. ship 级 alpha 的聚合规则")
print("=" * 62)
gun = next(x for x in fit["items"] if x["type_id"] == 3178 and x.get("charge"))
def mk(n):
    items = [dict(gun, slot={"type": "high", "index": i}) for i in range(n)]
    return {"ship": {"type_id": 35683}, "items": items, "character": {"skills": all5}}

for n in (1, 2, 5):
    cc = dogma.calculate(mk(n))
    s = sa(cc)
    vol_sum = sum(x["attributes"].get(-21, {}).get("value", 0) for x in cc["items"])
    print(f"  {n} 门炮: ship alpha={s.get(-11)!r:22s} ship dps={s.get(-12)!r:22s} 逐装备 volley 之和={vol_sum:.3f}")

full = sa(c)
vol_all = sum(x["attributes"].get(-21, {}).get("value", 0) for x in c["items"])
print(f"  完整配装: ship alpha={full.get(-11):.4f} ship dps={full.get(-12):.4f} 逐装备 volley 之和={vol_all:.4f}")
print(f"  比值 alpha/volley_sum = {full.get(-11) / vol_all:.4f}")

print()
print("=" * 62)
print("C. 读取层往返：数值是否无损")
print("=" * 62)
eft = dogma.save_eft(fit)
back = dogma.load_eft(eft)
a1 = sa(dogma.calculate({**fit, "character": {"skills": all5}}))
a2 = sa(dogma.calculate({**back, "character": {"skills": all5}}))
for aid, name in [(-43, "EHP"), (-12, "DPS"), (-11, "alpha"), (-1, "alignTime"),
                  (-72, "capStable%"), (-7, "capDepletesIn"), (-9, "cpuFree"), (-10, "powerFree")]:
    v1, v2 = a1.get(aid), a2.get(aid)
    ok = "OK" if (v1 is None and v2 is None) or (v1 is not None and v2 is not None and abs(v1 - v2) < 1e-6) else "DIFF"
    print(f"  {name:14s} orig={v1!r:24s} back={v2!r:24s} {ok}")

print()
print("=" * 62)
print("D. 状态假设的敏感性（哪些指标靠“引擎补的状态”）")
print("=" * 62)
online = {"ship": fit["ship"],
          "items": [dict(x, state="online") for x in fit["items"]],
          "character": {"skills": all5}}
a_on = sa(dogma.calculate(online))
for aid, name in [(-43, "EHP"), (-12, "DPS"), (-72, "capStable%"), (-7, "capDepletesIn"),
                  (-4, "capPeakLoad"), (-1, "alignTime"), (-44, "velocityBoost")]:
    print(f"  {name:14s} active状态={a_on.get(aid)!r:22s}")

print()
print("=" * 62)
print("E. 全部负 ID（派生属性）中实际有值的清单")
print("=" * 62)
neg = {k: v for k, v in full.items() if k < 0 and v is not None}
print(f"  共 {len(neg)} 个有值：")
print("  ", ", ".join(f"{k}={v:.4g}" if isinstance(v, float) else f"{k}={v}" for k, v in sorted(neg.items())))
