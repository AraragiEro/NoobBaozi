import json
import urllib.request

UA = {"User-Agent": "evefit/0.1 (index build probe)"}


def probe(url):
    req = urllib.request.Request(url, headers={**UA, "Range": "bytes=0-0"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            cr = r.headers.get("Content-Range")
            return f"HTTP {r.status}  Content-Range={cr}  final={r.geturl()}"
    except Exception as e:
        return f"ERROR {e}"


req = urllib.request.Request(
    "https://developers.eveonline.com/static-data/tranquility/latest.jsonl", headers=UA)
with urllib.request.urlopen(req, timeout=60) as r:
    rec = json.loads(r.read().decode().strip().splitlines()[0])
build = rec["buildNumber"]
print("build =", build, " release =", rec.get("releaseDate"))

urls = [
    f"https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-{build}-jsonl.zip",
    "https://developers.eveonline.com/static-data/eve-online-static-data-latest-jsonl.zip",
    f"https://developers.eveonline.com/static-data/tranquility/eve-online-static-data-{build}-yaml.zip",
    "https://developers.eveonline.com/static-data/eve-online-static-data-latest-yaml.zip",
]
for u in urls:
    print(f"\n{u}\n  -> {probe(u)}")
