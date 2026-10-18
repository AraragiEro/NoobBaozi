import json
import urllib.request

import esf_dogma_engine as dogma
from eveshipfit_sde import sde_path

UA = {"User-Agent": "dsh-eve-smoke/0.1 (feasibility test)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


dogma.load_sde_from_file(sde_path())

HECATE = 35683
DC2 = 2048


def cal(fit):
    c = dogma.calculate(fit)
    return {int(k): (v["value"] if isinstance(v, dict) else v)
            for k, v in c["ship"]["attributes"].items()}, c


# 1) ESI 官方数据：Hecate 的基础抗性属性（原始共振率，越小抗性越高）
t = get(f"https://esi.evetech.net/latest/universe/types/{HECATE}/")
official = {a["attribute_id"]: a["value"] for a in t["dogma_attributes"]}
print("=== ESI 官方 Hecate 属性条数:", len(official))

cand = [aid for aid, v in official.items() if 0.02 <= v <= 0.99]
names = {}
for aid in cand[:30]:
    try:
        names[aid] = get(f"https://esi.evetech.net/latest/dogma/attributes/{aid}/")["name"]
    except Exception as e:
        names[aid] = f"ERR {e}"

print("\n=== 官方属性中的共振类（含 resonance） ===")
for aid, nm in names.items():
    if "esonance" in nm:
        print(f"  {aid:5d} {nm:44s} {official[aid]}")

# 2) 引擎：裸船
bare, _ = cal({"ship": {"type_id": HECATE}, "items": []})
PER = {(-31): "armorEM", (-32): "armorEXP", (-33): "armorKIN", (-34): "armorTHM",
       (-35): "hullEM", (-36): "hullEXP", (-37): "hullKIN", (-38): "hullTHM",
       (-39): "shieldEM", (-40): "shieldEXP", (-41): "shieldKIN", (-42): "shieldTHM"}

print("\n=== 引擎裸船（无装备无技能）：真实共振率 = per-type x 4 ===")
for aid, nm in PER.items():
    v = bare.get(aid)
    print(f"  {nm:10s} 加权值={v!r:12s} -> 真实共振率={v * 4 if v is not None else None}")

# 3) 引擎：只加 Damage Control II
dc, c = cal({"ship": {"type_id": HECATE},
             "items": [{"type_id": DC2, "slot": {"type": "low", "index": 0}, "state": "online"}]})
print("\n=== 加 DC II 后：倍数关系（应等于 1 - 抗性加成）===")
for aid, nm in PER.items():
    b, d = bare.get(aid), dc.get(aid)
    if b and d:
        print(f"  {nm:10s} 裸={b * 4:.6f}  加DC={d * 4:.6f}  倍数={d / b:.6f}  隐含抗性加成={(1 - d / b) * 100:.4f}%")

# 4) 用 sources 看 DC II 到底施加了什么
with_src = dogma.calculate({"ship": {"type_id": HECATE},
                            "items": [{"type_id": DC2, "slot": {"type": "low", "index": 0}, "state": "online"}]},
                           {"sources": True})
ship_attrs = with_src["ship"]["attributes"]
print("\n=== sources：护盾热能共振上的每条修改 ===")
key = None
for k in ship_attrs:
    if int(k) == -42:
        key = k
print(json.dumps(ship_attrs[key], ensure_ascii=False, indent=2)[:1500] if key else "  (-42 not found)")
