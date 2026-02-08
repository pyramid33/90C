import requests, json

resp = requests.get(
    "https://gamma-api.polymarket.com/assets?limit=5&listed=true&closed=false",
    timeout=15,
)
text = resp.text
prefix = ")]}',"
if text.startswith(prefix):
    text = text[len(prefix) :]
data = json.loads(text)
print("keys:", data.keys())
assets = data.get("data") or data.get("assets") or []
print("count:", len(assets))
for asset in assets:
    print(asset.get("conditionId"), asset.get("question"), asset.get("tags"))

