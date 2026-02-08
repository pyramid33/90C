# slug_resolver.py
import time
from typing import Optional, Dict, Any, List
import re
import os

import requests

CLOB_BASE_URL = "https://clob.polymarket.com"
GAMMA_BASE_URL = "https://gamma-api.polymarket.com"

INTERVAL = 900  # 15 minutes in seconds


# -----------------------------------------
# Time → slug helpers
# -----------------------------------------

def current_bucket(ts: Optional[int] = None) -> int:
    """
    Return the UNIX timestamp used in the slug for the *current*
    15-minute window.

    Polymarket's slug timestamp corresponds to the END of the 15m window.
    A robust way to get that is to add one interval and floor:
        bucket = floor( (now + 900) / 900 ) * 900
    """
    if ts is None:
        ts = int(time.time())

    return ((ts + INTERVAL) // INTERVAL) * INTERVAL - 900


def slug_for_symbol(symbol: str, ts: Optional[int] = None) -> str:
    """
    Build slug like:
        eth-updown-15m-1764552600
        btc-updown-15m-1764552600
        sol-updown-15m-1764552600
    """
    bucket = current_bucket(ts)
    symbol = symbol.lower()
    return f"{symbol}-updown-15m-{bucket}"


# -----------------------------------------
# Gamma API: slug → conditionId
# -----------------------------------------

def condition_id_from_slug_gamma(slug: str) -> Optional[str]:
    """
    Preferred way: use Gamma API 'Get market by slug'.
    Docs: https://gamma-api.polymarket.com/markets/slug/{slug}
    Returns a JSON object with 'conditionId'.
    """
    url = f"{GAMMA_BASE_URL}/markets/slug/{slug}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            # Slug not created yet
            return None
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"[GAMMA] Error fetching slug {slug}: {e}")
        return None

    # According to docs, response is a single market object
    cid = data.get("conditionId") or data.get("condition_id")
    if cid:
        return cid

    # Fallback: sometimes wrapped in "data"
    if isinstance(data, dict) and "data" in data:
        d = data["data"]
        if isinstance(d, dict):
            cid = d.get("conditionId") or d.get("condition_id")
            if cid:
                return cid

    return None


# -----------------------------------------
# Fallback via CLOB markets list
# -----------------------------------------

def fetch_clob_markets() -> List[Dict[str, Any]]:
    """
    Fetch markets list from CLOB as a fallback and search by 'market_slug'.
    """
    url = f"{CLOB_BASE_URL}/markets"
    resp = requests.get(url, params={"limit": 2000}, timeout=10)
    resp.raise_for_status()

    data = resp.json()
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("markets") or data.get("data") or []
    return []


def condition_id_from_slug_clob(slug: str) -> Optional[str]:
    """
    Fallback: scan CLOB markets for matching 'market_slug'.
    """
    try:
        markets = fetch_clob_markets()
    except Exception as e:
        print(f"[CLOB] Error fetching markets for slug {slug}: {e}")
        return None

    for m in markets:
        mslug = m.get("market_slug") or m.get("slug")
        if mslug == slug:
            cid = m.get("condition_id") or m.get("conditionId")
            if cid:
                return cid

    return None


# -----------------------------------------
# High-level resolver
# -----------------------------------------

def resolve_current_condition_id(symbol: str, poll_every: int = 2) -> str:
    """
    Resolve the *current* 15-minute condition_id for one symbol (btc/eth/sol).

    Strategy:
      1. Build slug for current bucket.
      2. Try Gamma /markets/slug/{slug}.
      3. If not ready yet, retry after 'poll_every' seconds.
      4. If Gamma fails completely, fall back to scanning CLOB.
    """
    symbol = symbol.lower()

    while True:
        slug = slug_for_symbol(symbol)
        print(f"\n[{symbol.upper()}] Trying slug: {slug}")

        # 1) Preferred: Gamma
        cid = condition_id_from_slug_gamma(slug)
        if cid:
            print(f"[{symbol.upper()}] Gamma condition_id = {cid}")
            return cid

        # 2) Fallback: CLOB
        cid = condition_id_from_slug_clob(slug)
        if cid:
            print(f"[{symbol.upper()}] CLOB condition_id = {cid}")
            return cid

        print(f"[{symbol.upper()}] Not available yet. Retrying in {poll_every} sec...")
        time.sleep(poll_every)

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "config.py")

def update_config_condition_ids(cid_map: dict):
    """
    cid_map = {
        "BTC": "0x123...",
        "ETH": "0x456...",
        "SOL": "0x789...",
        "XRP": "0xabc..."
    }

    Rewrites ONLY the condition_id lines in config.py.
    """
    if not os.path.exists(CONFIG_PATH):
        print("[CONFIG-UPDATE] config.py not found!")
        return False

    with open(CONFIG_PATH, "r") as f:
        config_text = f.read()

    for symbol, cid in cid_map.items():
        pattern = rf'("{symbol}".*?"condition_id":\s*")[^"]*(")'

        def repl(match):
            return match.group(1) + cid + match.group(2)

        new_text, count = re.subn(pattern, repl, config_text, flags=re.DOTALL)


        if count == 0:
            print(f"[CONFIG-UPDATE] WARNING: Did not find condition_id for {symbol}")
        else:
            print(f"[CONFIG-UPDATE] Updated {symbol}: {cid}")

        config_text = new_text

    # Write back the updated file
    with open(CONFIG_PATH, "w") as f:
        f.write(config_text)

    print("[CONFIG-UPDATE] config.py successfully updated.")
    return True

# -----------------------------------------
# CLI test
# -----------------------------------------

if __name__ == "__main__":
    print("Polymarket 15-minute condition_id resolver")
    print("Updating config.py for: BTC, ETH, SOL, XRP\n")

    symbols = ["btc", "eth", "sol", "xrp"]
    resolved = {}

    try:
        for sym in symbols:
            cid = resolve_current_condition_id(sym)
            resolved[sym.upper()] = cid

    except KeyboardInterrupt:
        print("\nAborted.")
        exit(0)

    print("\n=== CONDITION IDs FOUND ===")
    for sym, cid in resolved.items():
        print(f"{sym}: {cid}")

    # 🔥 write to config.py
    update_config_condition_ids(resolved)
