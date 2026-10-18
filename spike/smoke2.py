import json
import time

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

dogma.load_sde_from_file(sde_path())

km = json.load(open("evefit/spike/kill.json", encoding="utf-8"))[0]
km.pop("zkb", None)
fit = dogma.load_killmail(km)


def ship_attrs(calc):
    out = {}
    for k, v in calc["ship"]["attributes"].items():
        out[int(k)] = v["value"] if isinstance(v, dict) else v
    return out


print("=" * 60)
print("实验 1：伤害剖面是否影响 per-type 抗性属性")
print("=" * 60)
base = ship_attrs(dogma.calculate(fit))
pure_em = ship_attrs(dogma.calculate(
    {**fit, "environment": {"damage_profile": {"em": 1, "explosive": 0, "kinetic": 0, "thermal": 0}}}))
for aid, name in [(-27, "shieldEffReso"), (-26, "hullEffReso"), (-25, "armorEffReso"),
                  (-43, "EHP"), (-39, "shieldEmReso"), (-40, "shieldExpReso"),
                  (-42, "shieldThmReso"), (-31, "armorEmReso")]:
    print(f"  {name:16s} 25/25/25/25={base.get(aid):.6f}   pure_EM={pure_em.get(aid):.6f}")
print("  per-type 之和 (25/25/25/25):",
      round(sum(base.get(i) for i in (-31, -32, -33, -34)), 6), "vs armorEff", round(base.get(-25), 6))

print()
print("=" * 60)
print("实验 2：技能缺失对合法性判断的影响")
print("=" * 60)
skills = {}
for rnd in range(6):
    c = dogma.calculate({**fit, "character": {"skills": skills}}, {"validate": True})
    viol = c.get("violations") or []
    skill_v = [v["rule"] for v in viol if v["rule"]["type"] == "skill"]
    other = [v["rule"] for v in viol if v["rule"]["type"] != "skill"]
    print(f"  轮 {rnd}: 总违规 {len(viol)}  技能类 {len(skill_v)}  其他 {len(other)}")
    if not skill_v:
        break
    changed = False
    for r in skill_v:
        if skills.get(r["type_id"], 0) < r["required"]:
            skills[r["type_id"]] = r["required"]
            changed = True
    if not changed:
        break

print(f"  收集到技能 {len(skills)} 个（按最低要求）")
c_req = dogma.calculate({**fit, "character": {"skills": skills}}, {"validate": True})
print("  按最低要求 ->", len(c_req.get("violations") or []), "条违规")
for v in (c_req.get("violations") or [])[:5]:
    print("    ", json.dumps(v["rule"], ensure_ascii=False))

all5 = {sid: 5 for sid in skills}
c_all5 = dogma.calculate({**fit, "character": {"skills": all5}}, {"validate": True})
print("  技能全 5 ->", len(c_all5.get("violations") or []), "条违规")
for v in (c_all5.get("violations") or [])[:8]:
    print("    ", json.dumps(v["rule"], ensure_ascii=False))

a_req = ship_attrs(c_req)
a_all5 = ship_attrs(c_all5)
print("\n  指标差异（最低要求 -> 全5）：")
for aid, name in [(-43, "EHP"), (-12, "DPSnoReload"), (-1, "alignTime"),
                  (-72, "capStable%"), (-7, "capDepletesIn"), (-70, "maxTargets"),
                  (-9, "cpuFree"), (-10, "powerFree")]:
    print(f"    {name:14s} {a_req.get(aid)}  ->  {a_all5.get(aid)}")

print()
print("=" * 60)
print("实验 3：逐装备的 volley / dps 与状态")
print("=" * 60)
c = dogma.calculate({**fit, "character": {"skills": all5}})
for i, item in enumerate(c["items"]):
    att = {int(k): (v["value"] if isinstance(v, dict) else v) for k, v in item["attributes"].items()}
    if -21 in att or -12 in att:
        print(f"  [{i}] type={fit['items'][i]['type_id']:6d} state={item.get('state'):7s} "
              f"volley={att.get(-21)} dps={att.get(-12)} rateOfFire={att.get(-3)}")
print("  ship volley attr(-21):", ship_attrs(c).get(-21))

print()
print("=" * 60)
print("实验 4：EFT 读回（load_eft）与耗时")
print("=" * 60)
eft = dogma.save_eft(fit)
back = dogma.load_eft(eft)
print("  原始 items:", len(fit["items"]), " 读回 items:", len(back["items"]))
print("  船型一致:", fit["ship"]["type_id"] == back["ship"]["type_id"])
orig = sorted(x["type_id"] for x in fit["items"])
again = sorted(x["type_id"] for x in back["items"])
print("  type_id 集合一致:", orig == again)

t0 = time.perf_counter()
N = 200
for _ in range(N):
    dogma.calculate({**fit, "character": {"skills": all5}})
print(f"  200 次平均 {(time.perf_counter() - t0) / N * 1000:.3f} ms/次")
