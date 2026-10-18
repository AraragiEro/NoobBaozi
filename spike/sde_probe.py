import json
import urllib.request

UA = {"User-Agent": "dsh-eve-smoke/0.1"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


print("=== /dogma/attributes/160/ 完整字段（trackingSpeed）===")
print(json.dumps(get("https://esi.evetech.net/latest/dogma/attributes/160/"), indent=2, ensure_ascii=False))

print("\n=== /dogma/attributes/54/ (maxRange) ===")
print(json.dumps(get("https://esi.evetech.net/latest/dogma/attributes/54/"), indent=2, ensure_ascii=False))

print("\n=== 是否能列出某 group 的 type（供升级空间用）===")
t = get("https://esi.evetech.net/latest/universe/types/3178/")
print("  Light Neutron Blaster II -> group_id =", t.get("group_id"), " name =", t.get("name"))
g = get(f"https://esi.evetech.net/latest/universe/groups/{t['group_id']}/")
print("  group name:", g.get("name"), " types:", len(g.get("types", [])))

print("\n=== 中文名是否可得（names.dat / ESI 语言）===")
req = urllib.request.Request(
    "https://esi.evetech.net/latest/universe/types/3178/?language=zh",
    headers={**UA, "Accept-Language": "zh"})
with urllib.request.urlopen(req, timeout=30) as r:
    zh = json.loads(r.read())
print("  zh name =", zh.get("name"))
