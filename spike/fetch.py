import json
import urllib.request

URL = "https://zkillboard.com/api/killID/139246907/"
UA = "dsh-eve-smoke/0.1 (local feasibility test)"

req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept-Encoding": "identity"})
with urllib.request.urlopen(req, timeout=30) as resp:
    raw = resp.read()

data = json.loads(raw)
with open("evefit/spike/kill.json", "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False)

k = data[0]
print("bytes:", len(raw))
print("id:", k["killmail_id"], "time:", k["killmail_time"], "system:", k["solar_system_id"])
print("victim_ship:", k["victim"]["ship_type_id"])
print("victim_items:", len(k["victim"].get("items", [])))
print("attackers:", len(k["attackers"]))
print("zkb_value:", k.get("zkb", {}).get("totalValue"))
print("attacker_ships:", [a["ship_type_id"] for a in k["attackers"]])
