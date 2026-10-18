import json
import time
import urllib.request

UA = {"User-Agent": "dsh-eve-smoke/0.1 (feasibility test)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


print("=== Ship 分类下的所有组 ===")
cat = get("https://esi.evetech.net/latest/universe/categories/6/")
groups = cat.get("groups", [])
print(f"  分类 {cat.get('name')} 下 {len(groups)} 个组")

hits = []
for gid in groups:
    try:
        g = get(f"https://esi.evetech.net/latest/universe/groups/{gid}/")
    except Exception as e:
        print(f"  group {gid} ERR {e}")
        continue
    nm = g.get("name", "?")
    low = nm.lower()
    if any(k in low for k in ("mode", "modifier", "stance", "tactical")):
        hits.append((gid, nm, len(g.get("types", []))))
    time.sleep(0.01)

print(f"\n  命中 {len(hits)} 个组:")
for gid, nm, n in hits:
    print(f"    {gid:6d} {nm:36s} types={n}")

print("\n=== 命中组里的类型 ===")
for gid, nm, n in hits:
    g = get(f"https://esi.evetech.net/latest/universe/groups/{gid}/")
    print(f"\n  [{gid}] {nm}")
    for tid in g.get("types", [])[:30]:
        try:
            d = get(f"https://esi.evetech.net/latest/universe/types/{tid}/")
            print(f"    {tid:8d} {d.get('name')}")
        except Exception as e:
            print(f"    {tid:8d} ERR {e}")
        time.sleep(0.01)
