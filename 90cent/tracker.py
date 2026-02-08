import csv
import time
import threading
from datetime import datetime
from typing import Optional, Tuple

import requests
import config

from slug_resolver import resolve_current_condition_id


BASE_URL = config.POLYMARKET_API_URL or "https://clob.polymarket.com"
POLL_INTERVAL = 0.5   # seconds
CSV_PATH = "multi_market_midpoints.csv"


# ============================================
# TOKEN RESOLUTION
# ============================================

def resolve_token_ids(condition_id: str) -> Tuple[Optional[str], Optional[str]]:
    """Fetch market tokens and extract Up/Down token IDs."""
    try:
        resp = requests.get(f"{BASE_URL}/markets/{condition_id}", timeout=10)
        resp.raise_for_status()
        market = resp.json()
    except Exception as e:
        print(f"[ERROR] Market lookup failed {condition_id}: {e}")
        return None, None

    tokens = market.get("tokens") or []
    up_id = None
    down_id = None

    for tok in tokens:
        outcome = str(tok.get("outcome", "")).lower()
        tid = tok.get("token_id") or tok.get("tokenId")

        if not tid:
            continue
        if "up" in outcome:
            up_id = tid
        if "down" in outcome:
            down_id = tid

    # Fallback: two-token market
    if (not up_id or not down_id) and len(tokens) == 2:
        up_id = up_id or tokens[0]["token_id"]
        down_id = down_id or tokens[1]["token_id"]

    if not up_id or not down_id:
        print(f"[ERROR] Could not resolve Up/Down tokens for {condition_id}")

    return up_id, down_id


# ============================================
# MIDPOINT FETCHING (FRONTEND ACCURATE)
# ============================================

def midpoint(token_id: str) -> Optional[float]:
    """Fetch midpoint from /midpoint endpoint."""
    try:
        resp = requests.get(
            f"{BASE_URL}/midpoint",
            params={"token_id": token_id},
            timeout=5
        )
        resp.raise_for_status()
    except Exception as e:
        print(f"[MID] Error for token {token_id}: {e}")
        return None

    mid_str = resp.json().get("mid")
    if mid_str is None:
        return None

    try:
        mid = float(mid_str)
        if 0 <= mid <= 1:
            return mid
    except:
        return None

    return None


# ============================================
# TRACKING THREAD
# ============================================

def track_market(market_name: str, condition_id: str, csv_writer):
    """Thread worker for one market."""
    up_id, down_id = resolve_token_ids(condition_id)
    if not up_id or not down_id:
        print(f"[{market_name}] Aborted — token resolution failed.")
        return

    print(f"[TRACKING] {market_name}: Up={up_id} Down={down_id}")

    while True:
        ts = datetime.utcnow().isoformat()

        up_mid = midpoint(up_id)
        down_mid = midpoint(down_id)

        if up_mid is not None and down_mid is not None:
            summed = up_mid + down_mid
        else:
            summed = None

        # Write to CSV
        csv_writer.writerow([
            ts, market_name,
            f"{up_mid:.5f}" if up_mid is not None else "",
            f"{down_mid:.5f}" if down_mid is not None else "",
            f"{summed:.5f}" if summed is not None else "",
        ])

        # Console live print
        up_str = f"{up_mid:.3f}" if up_mid is not None else "NA"
        dn_str = f"{down_mid:.3f}" if down_mid is not None else "NA"
        sm_str = f"{summed:.3f}" if summed is not None else "NA"

        flag = ""
        if summed is not None:
            if summed < 1.0:
                flag = "LONG ARB"
            elif summed > 1.0:
                flag = "SHORT ARB"

        print(f"{ts} | {market_name} | Up={up_str} Down={dn_str} Sum={sm_str} {flag}")

        time.sleep(POLL_INTERVAL)


# ============================================
# MAIN
# ============================================

def main():
    print("[INIT] Starting multi-market tracker...")
    print(f"CSV Output → {CSV_PATH}")

    f = open(CSV_PATH, "a", newline="")
    writer = csv.writer(f)

    if f.tell() == 0:
        writer.writerow([
            "timestamp_utc", "market",
            "up_mid", "down_mid", "sum_mid"
        ])

    
    threads = []

    # Only track these three, skip XRP and anything else
    ACTIVE_SYMBOLS = {"BTC", "ETH", "SOL"}

    for market_name, details in config.MARKETS.items():
        if market_name not in ACTIVE_SYMBOLS:
            print(f"[SKIP] {market_name} — not in ACTIVE_SYMBOLS")
            continue

        symbol = market_name.lower()

        print(f"[RESOLVER] Resolving current condition ID for {market_name}...")
        cid = resolve_current_condition_id(symbol)

        if not cid:
            print(f"[SKIP] {market_name} — could not resolve condition ID")
            continue

        print(f"[RESOLVER] {market_name} -> condition_id = {cid}")

        t = threading.Thread(
            target=track_market,
            args=(market_name, cid, writer),
            daemon=True
        )
        t.start()
        threads.append(t)


    print(f"[INIT] Tracking {len(threads)} markets...\nPress Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[STOP] Shutting down tracker.")
        f.close()


if __name__ == "__main__":
    main()
