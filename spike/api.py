import esf_dogma_engine as dogma
import eveshipfit_sde as sde

print("=== esf_dogma_engine 公开接口 ===")
for n in sorted(dir(dogma)):
    if not n.startswith("_"):
        o = getattr(dogma, n)
        print(f"  {n:28s} {type(o).__name__}")

print("\n=== eveshipfit_sde 公开接口 ===")
for n in sorted(dir(sde)):
    if not n.startswith("_"):
        print(f"  {n:28s} {getattr(sde, n)}")

print("\n=== types 模块 ===")
try:
    from esf_dogma_engine import types as t
    print("  ", [x for x in sorted(dir(t)) if not x.startswith("_")][:40])
except Exception as e:
    print("  无 types 模块:", e)
