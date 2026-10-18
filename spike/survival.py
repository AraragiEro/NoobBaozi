import json

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

dogma.load_sde_from_file(sde_path())
km = json.load(open("evefit/spike/kill.json", encoding="utf-8"))[0]
km.pop("zkb", None)
fit = dogma.load_killmail(km)


def ship(c, neg=False):
    return {int(k): (v["value"] if isinstance(v, dict) else v)
            for k, v in c["ship"]["attributes"].items()}


def skill_min4(f):
    """主人约定：全部技能 4 级，且补到每个模块的最低要求。"""
    skills = {}
    for _ in range(8):
        c = dogma.calculate({**f, "character": {"skills": skills}}, {"validate": True})
        reqs = [v["rule"] for v in (c.get("violations") or []) if v["rule"]["type"] == "skill"]
        changed = False
        for r in reqs:
            want = max(4, r["required"])
            if skills.get(r["type_id"], 0) < want:
                skills[r["type_id"]] = want
                changed = True
        if not changed:
            break
    return skills


sk = skill_min4(fit)
print("技能数:", len(sk), " 档位分布:", {v: list(sk.values()).count(v) for v in set(sk.values())})

c = dogma.calculate({**fit, "character": {"skills": sk}})
s = ship(c)

# 分层 HP 与真实共振率
LAYERS = {
    "shield": (263, {271: "EM", 272: "EXP", 273: "KIN", 274: "THM"}),
    "armor": (265, {267: "EM", 268: "EXP", 269: "KIN", 270: "THM"}),
    "hull": (9, {113: "EM", 111: "EXP", 109: "KIN", 110: "THM"}),
}
print("\n=== 分层 HP + 四系共振率 + 四系 EHP ===")
tot = {}
for layer, (hp_id, resos) in LAYERS.items():
    hp = s.get(hp_id)
    print(f"  {layer:7s} HP={hp}")
    for aid, nm in resos.items():
        r = s.get(aid)
        ehp = hp / r
        tot[nm] = tot.get(nm, 0) + ehp
        print(f"    {nm:4s} 共振={r:.6f} 抗性={(1 - r) * 100:6.2f}%  该层EHP={ehp:8.1f}")
print("  四系总 EHP:", {k: round(v, 1) for k, v in tot.items()})
print("  引擎 EHP(25/25/25/25):", round(s.get(-43), 2))

print("\n=== 修复/回充（raw 与 effective）===")
for aid, nm in [(-45, "armorRepairRate"), (-48, "armorEffectiveRepairRate"),
                (-47, "shieldBoostRate"), (-50, "shieldEffectiveBoostRate"),
                (-51, "passiveShieldRecharge"), (-52, "passiveShieldEffectiveRecharge"),
                (-46, "hullRepairRate"), (-49, "hullEffectiveRepairRate")]:
    print(f"  {nm:32s} {s.get(aid)}")
print("  校验 effective/raw (护甲):", round((s.get(-48) or 0) / (s.get(-45) or 1), 4),
      " vs 1/armorEffReso:", round(1 / s.get(-25), 4))
print("  校验 effective/raw (被动盾):", round((s.get(-52) or 0) / (s.get(-51) or 1), 4),
      " vs 1/shieldEffReso:", round(1 / s.get(-27), 4))

print("\n=== 换伤害剖面后的 EHP（同一套配装）===")
for label, prof in [("25/25/25/25", {"em": .25, "explosive": .25, "kinetic": .25, "thermal": .25}),
                    ("纯电磁", {"em": 1, "explosive": 0, "kinetic": 0, "thermal": 0}),
                    ("纯热能", {"em": 0, "explosive": 0, "kinetic": 0, "thermal": 1}),
                    ("纯动能", {"em": 0, "explosive": 0, "kinetic": 1, "thermal": 0}),
                    ("纯爆炸", {"em": 0, "explosive": 1, "kinetic": 0, "thermal": 0})]:
    cc = dogma.calculate({**fit, "character": {"skills": sk}, "environment": {"damage_profile": prof}})
    print(f"  {label:12s} EHP={ship(cc).get(-43):9.1f}")

print("\n=== 过载档：哪些东西会变 ===")
ov = {"ship": fit["ship"],
      "items": [dict(x, state=("overload" if x.get("slot", {}).get("type") in ("low", "med", "high") else x.get("state", "online"))) for x in fit["items"]],
      "character": {"skills": sk}}
try:
    co = dogma.calculate(ov)
    so = ship(co)
    for aid, nm in [(-43, "EHP"), (-12, "DPS"), (-45, "armorRepairRaw"), (-48, "armorRepairEff"),
                    (-72, "capStable%"), (-7, "capDepletesIn")]:
        print(f"  {nm:16s} 常规={s.get(aid)!r:22s} 全过载={so.get(aid)!r}")
except Exception as e:
    print("  过载计算失败:", e)
