import json
import time
import urllib.request

UA = {"User-Agent": "dsh-eve-smoke/0.1 (feasibility test)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except Exception:
        return None


print("=== 在 Hecate 附近扫 ID，找模式类型 ===")
found = []
for tid in range(35660, 35720):
    d = get(f"https://esi.evetech.net/latest/universe/types/{tid}/")
    if d and d.get("name"):
        nm = d["name"]
        if "mode" in nm.lower() or "Hecate" in nm:
            print(f"  {tid:8d} {nm}  group={d.get('group_id')}")
            found.append((tid, nm, d.get("group_id")))
    time.sleep(0.01)

print(f"\n  命中 {len(found)} 个")

print("\n=== 直接按可能名字再查（含大小写变体）===")
import urllib.request as ur
cands = ["Sharpshooter Mode", "Sharpshooter mode", "Defense Mode", "Defense mode",
         "Propulsion Mode", "Propulsion mode", "Defensive Mode", "Speed Mode",
         "Hecate Sharpshooter Mode", "Tactical Destroyer Sharpshooter Mode"]
data = json.dumps(cands).encode()
req = ur.Request("https://esi.evetech.net/latest/universe/ids/", data=data,
                 headers={**UA, "Content-Type": "application/json"})
with ur.urlopen(req, timeout=30) as r:
    res = json.loads(r.read())
print("  命中:", res.get("inventory_types", []))
