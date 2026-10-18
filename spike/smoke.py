import json
import time

import esf_dogma_engine as dogma
from eveshipfit_sde import build_number, sde_path

# --- 负 ID -> 名称（来自 EVEShipFit/sde-patched patches/ids.yaml） ---
NEG = {
    -1: "alignTime", -4: "capacitorPeakLoad", -7: "capacitorDepletesIn",
    -9: "cpuFree", -10: "powerFree", -11: "damageAlpha",
    -12: "dpsWithoutReload", -13: "dpsWithReload", -17: "dmgProfileEm",
    -18: "dmgProfileExplosive", -19: "dmgProfileKinetic", -20: "dmgProfileThermal",
    -21: "damageVolley", -25: "armorEffectiveResonance", -26: "hullEffectiveResonance",
    -27: "shieldEffectiveResonance", -28: "armorEhp", -29: "hullEhp", -30: "shieldEhp",
    -31: "armorEmReso", -32: "armorExpReso", -33: "armorKinReso", -34: "armorThmReso",
    -35: "hullEmReso", -36: "hullExpReso", -37: "hullKinReso", -38: "hullThmReso",
    -39: "shieldEmReso", -40: "shieldExpReso", -41: "shieldKinReso", -42: "shieldThmReso",
    -43: "EHP", -44: "velocityBoost", -53: "scanStrength",
    -69: "upgradeFree", -70: "maxTargets", -72: "capacitorStablePercentage",
}

print("SDE build:", build_number())
dogma.load_sde_from_file(sde_path())

km = json.load(open("noobbaozi/spike/kill.json", encoding="utf-8"))[0]
km.pop("zkb", None)

fit = dogma.load_killmail(km)
print("\n=== load_killmail -> EFT ===")
print(dogma.save_eft(fit))

calc = dogma.calculate(fit)
ship = calc["ship"]["attributes"]


def val(a):
    if a not in ship:
        return None
    v = ship[a]
    return v["value"] if isinstance(v, dict) else v


print("\n=== 船只聚合属性（pass 4 派生） ===")
rows = []
for aid, name in NEG.items():
    v = val(aid)
    if v is not None:
        rows.append((name, round(v, 4)))
for n, v in rows:
    print(f"  {n:26s} {v}")


def attr_total(prefix):
    return sum(v for n, v in rows if n.startswith(prefix))


print("\n=== 关键口径核对 ===")
print("  盾EHP+甲EHP+构EHP =", round(attr_total("shieldEhp") + attr_total("armorEhp") + attr_total("hullEhp"), 1))
print("  总 EHP(引擎)      =", val(-43))
print("  dpsWithoutReload  =", val(-12), " dpsWithReload =", val(-13))
print("  volley            =", val(-21), " alpha =", val(-11))
print("  电容稳定百分比    =", val(-72), " 耗尽秒 =", val(-7))

print("\n=== 装备状态（前 8 项） ===")
for i, item in enumerate(calc["items"][:8]):
    print(f"  [{i}] type={fit['items'][i]['type_id']} state={item.get('state')} "
          f"max_state={item.get('max_state')} has_charge={bool(item.get('charge'))}")

print("\n=== 校验（validate=True） ===")
v = dogma.calculate(fit, {"validate": True})
viol = v.get("violations") or []
print("  violations:", len(viol))
for item in viol[:10]:
    print("   ", json.dumps(item, ensure_ascii=False))

c1 = dogma.calculate(fit)
t0 = time.perf_counter()
N = 50
for _ in range(N):
    dogma.calculate(fit)
dt = (time.perf_counter() - t0) / N * 1000
print(f"\n=== 耗时：{N} 次平均 {dt:.3f} ms/次 ===")

t0 = time.perf_counter()
for _ in range(N):
    dogma.calculate(fit, {"validate": True})
dt2 = (time.perf_counter() - t0) / N * 1000
print(f"=== 含 validate：{dt2:.3f} ms/次 ===")

print("\n=== 读取层往返：EFT -> fit -> EFT ===")
eft = dogma.save_eft(fit)
fit2 = dogma.load_esf(eft) if hasattr(dogma, "load_esf") else None
print("  save_eft 长度:", len(eft), "行数:", eft.count("\n") + 1)
km2 = dogma.save_esi_fitting(fit)
print("  save_esi_fitting keys:", sorted(km2.keys()))
print("  items 数:", len(km2.get("items", [])))
