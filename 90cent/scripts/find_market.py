import sys
import requests

tag = sys.argv[1].lower() if len(sys.argv) > 1 else "15m"
asset = sys.argv[2].lower() if len(sys.argv) > 2 else "bitcoin"

resp = requests.get("https://gamma-api.polymarket.com/assets?limit=1000", timeout=15)
import json
data = json.loads(resp.content.decode("utf-8"))
markets = data.get("data") or data.get("assets") or data.get("markets") or data.get("results") or data

for market in markets:
    question = (market.get("question") or "").lower()
    tags = [str(t).lower() for t in (market.get("tags") or [])]
    slug = (market.get("market_slug") or "").lower()
    tokens = [t.get("token_id") for t in market.get("tokens", [])]
    if tag in tags and asset in question and "up or down" in tags:
        print("QUESTION:", market.get("question"))
        print("CID:", market.get("condition_id"))
        print("TAGS:", tags)
        print("SLUG:", slug)
        break
else:
    print("No matching market found.")

