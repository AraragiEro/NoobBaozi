import json
import urllib.request

UA = {"User-Agent": "evefit/0.1 (index build probe)"}


def head(url):
    req = urllib.request.Request(url, headers=UA, method="HEAD")
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, r.geturl(), dict(r.headers)


print("=== latest.jsonl ===")
req = urllib.request.Request(
    "https://developers.eveonline.com/static-data/tranquility/latest.jsonl", headers=UA)
with urllib.request.urlopen(req, timeout=60) as r:
    body = r.read().decode("utf-8", "replace")
print(body[:400])

build = None
for line in body.strip().splitlines():
    try:
        rec = json.loads(line)
    except Exception:
        continue
    if "sde" in rec:
        build = rec["sde"]
print("build =", build)

for variant in ("jsonl", "yaml"):
    url = (f"https://developers.eveonline.com/static-data/tranquility/"
           f"eve-online-static-data-{build}-{variant}.zip")
    try:
        st, final, h = head(url)
        size = int(h.get("Content-Length", 0))
        print(f"\n{variant}: HTTP {st}  {size / 1024 / 1024:.1f} MB")
        print("  ETag:", h.get("ETag"), " Last-Modified:", h.get("Last-Modified"))
    except Exception as e:
        print(f"\n{variant}: ERROR {e}")
