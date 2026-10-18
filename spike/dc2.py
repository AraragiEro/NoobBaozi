import json
import urllib.request

UA = {"User-Agent": "dsh-eve-smoke/0.1 (feasibility test)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


t = get("https://esi.evetech.net/latest/universe/types/2048/")
print("Damage Control II 属性条数:", len(t["dogma_attributes"]))
targets = {0.125, 0.15, 0.4, 0.1250, 0.4000, 0.1500}
cand = [(a["attribute_id"], a["value"]) for a in t["dogma_attributes"]]
hits = [(i, v) for i, v in cand if any(abs(v - x) < 1e-9 for x in targets)]
print("值命中 0.125/0.15/0.4 的属性:", hits)
for aid, v in hits:
    try:
        nm = get(f"https://esi.evetech.net/latest/dogma/attributes/{aid}/")["name"]
    except Exception as e:
        nm = f"ERR {e}"
    print(f"  {aid:5d} {nm:40s} {v}")

print("\n全部数值型属性（0 < v < 1）：")
for aid, v in cand:
    if 0 < v < 1:
        try:
            nm = get(f"https://esi.evetech.net/latest/dogma/attributes/{aid}/")["name"]
        except Exception:
            nm = "?"
        print(f"  {aid:5d} {nm:40s} {v}")
