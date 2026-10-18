import json
import urllib.request

import esf_dogma_engine as dogma
from esf_dogma_engine import types as T

UA = {"User-Agent": "dsh-eve-smoke/0.1"}


def pages(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        body = json.loads(r.read())
        return len(body), r.headers.get("X-Pages")


print("=== 引擎支持的环境字段（权威 schema）===")
print("  Environment:", list(T.Environment.__annotations__.keys()))
print("  Fit        :", list(T.Fit.__annotations__.keys()))
print("  Ship       :", list(T.Ship.__annotations__.keys()))
print("  SlotKind   :", list(T.SlotKind.__annotations__.keys()) if hasattr(T.SlotKind, "__annotations__") else T.SlotKind)
print("  State      :", T.State)
print("  Security   :", getattr(T, "Security", "?"))
print("  ReactiveArmor:", getattr(T, "ReactiveArmor", "?"))
print("  Options    :", list(T.Options.__annotations__.keys()))
print("  Projection :", list(T.Projection.__annotations__.keys()))

print("\n=== SDE 规模（决定存储选型）===")
for label, url in [
    ("types", "https://esi.evetech.net/latest/universe/types/?page=1"),
    ("groups", "https://esi.evetech.net/latest/universe/groups/?page=1"),
    ("categories", "https://esi.evetech.net/latest/universe/categories/?page=1"),
    ("dogmaAttributes", "https://esi.evetech.net/latest/dogma/attributes/?page=1"),
    ("dogmaEffects", "https://esi.evetech.net/latest/dogma/effects/?page=1"),
    ("solarSystems", "https://esi.evetech.net/latest/universe/systems/?page=1"),
]:
    try:
        n, p = pages(url)
        print(f"  {label:16s} 每页 {n:5d}  X-Pages = {p}  -> 约 {int(p) * n if p else '?'} 条")
    except Exception as e:
        print(f"  {label:16s} ERROR {e}")
