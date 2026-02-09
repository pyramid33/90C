import os
import re
import json
import time
import logging
import threading
import requests as http_requests
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Flask, render_template, jsonify, request
import config
from polymarket_client import PolymarketClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("Dashboard")

app = Flask(__name__)

# Cache for CLOB token IDs: {condition_id: {"Up": token_id, "Down": token_id}}
_token_id_cache = {}

# Get potential paths for data files
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.dirname(BASE_DIR)

# Time-based claim lock - prevents concurrent dashboard claims, auto-expires after 5 minutes
_claim_lock_time = 0.0
_claim_lock_mutex = threading.Lock()  # protects _claim_lock_time
CLAIM_LOCK_MAX_AGE = 300  # 5 minutes - auto-expire stuck locks
# File-based lock for cross-process claim coordination (dashboard subprocess + bot auto-claim)
CLAIM_LOCK_FILE = os.path.join(ROOT_DIR, ".claim_lock")

def get_file_path(filename):
    """Try to find file in root first, then in 90cent dir."""
    root_path = os.path.join(ROOT_DIR, filename)
    if os.path.exists(root_path):
        return root_path
    sub_path = os.path.join(BASE_DIR, filename)
    return sub_path

LOG_FILE = get_file_path("trading_bot.log")
POSITIONS_FILE = get_file_path("positions.json")
RESET_FILE = get_file_path("pnl_reset.json")
TRADING_STATUS_FILE = get_file_path("trading_status.json")
RESOLUTION_CACHE_FILE = get_file_path("resolution_cache.json")

# All symbols the dashboard tracks (superset of what may be enabled)
ALL_SYMBOLS = ["btc", "eth", "sol", "xrp"]

def get_trading_status():
    """Get which symbols are enabled for trading."""
    if os.path.exists(TRADING_STATUS_FILE):
        try:
            with open(TRADING_STATUS_FILE, "r") as f:
                return json.load(f)
        except:
            pass
    # Default: derive from config.MARKETS (whatever is uncommented = enabled)
    enabled = {s.lower(): True for s in config.MARKETS.keys()}
    for s in ALL_SYMBOLS:
        if s not in enabled:
            enabled[s] = False
    return enabled

def set_trading_status(symbol, enabled):
    """Enable or disable trading for a symbol."""
    status = get_trading_status()
    status[symbol.lower()] = enabled
    with open(TRADING_STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)
    return status

# Initialize Client (minimal initialization for balance checks)
try:
    client = PolymarketClient(
        api_key=config.POLYMARKET_API_KEY,
        private_key=config.POLYMARKET_PRIVATE_KEY,
        api_url=config.POLYMARKET_API_URL,
        api_secret=config.POLYMARKET_API_SECRET,
        api_passphrase=config.POLYMARKET_API_PASSPHRASE,
        wallet_address=config.POLYMARKET_WALLET_ADDRESS,
        chain_id=getattr(config, "POLYMARKET_CHAIN_ID", 137),
        signature_type=getattr(config, "POLYMARKET_SIGNATURE_TYPE", None),
        funder_address=getattr(config, "POLYMARKET_PROXY_ADDRESS", ""),
    )
except Exception as e:
    logger.error(f"Failed to initialize client: {e}")
    client = None

def build_market_name_cache():
    """Build a cache mapping condition_ids to market names from logs."""
    cache = {}
    if not os.path.exists(LOG_FILE):
        return cache

    # Pattern: ✓ Auto-discovery successful for BTC: condition_id=0x..., YES=Up
    pattern = re.compile(r"Auto-discovery successful for (\w+): condition_id=(0x[a-fA-F0-9]+)")

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                match = pattern.search(line)
                if match:
                    market_name, cond_id = match.groups()
                    cache[cond_id] = market_name
                    # Also store truncated version
                    cache[cond_id[:10]] = market_name
    except Exception as e:
        logger.error(f"Error building market name cache: {e}")

    return cache

# Global cache for market names (rebuilt on each request for freshness)
_market_name_cache = None

def get_reset_timestamp():
    """Get the P&L reset timestamp, if set."""
    if not os.path.exists(RESET_FILE):
        return None
    try:
        with open(RESET_FILE, "r") as f:
            data = json.load(f)
            return data.get("reset_timestamp")
    except:
        return None

def set_reset_timestamp():
    """Set the P&L reset timestamp to now."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(RESET_FILE, "w") as f:
        json.dump({"reset_timestamp": now}, f)
    return now

def get_market_name(condition_id):
    """Get market name for a condition_id."""
    global _market_name_cache
    if _market_name_cache is None:
        _market_name_cache = build_market_name_cache()

    # Try full ID first, then truncated
    if condition_id in _market_name_cache:
        return _market_name_cache[condition_id]
    if condition_id[:10] in _market_name_cache:
        return _market_name_cache[condition_id[:10]]

    # Fallback: check config.MARKETS
    for m_name, m_cfg in config.MARKETS.items():
        if m_cfg.get("condition_id") == condition_id:
            return m_name

    # Return shortened ID if nothing found
    return condition_id[:10] + "..."

# --- Market Resolution Cache ---
_resolution_cache = None

def _load_resolution_cache():
    global _resolution_cache
    if _resolution_cache is not None:
        return _resolution_cache
    if os.path.exists(RESOLUTION_CACHE_FILE):
        try:
            with open(RESOLUTION_CACHE_FILE, "r") as f:
                _resolution_cache = json.load(f)
        except Exception:
            _resolution_cache = {}
    else:
        _resolution_cache = {}
    return _resolution_cache

def _save_resolution_cache():
    if _resolution_cache is not None:
        try:
            with open(RESOLUTION_CACHE_FILE, "w") as f:
                json.dump(_resolution_cache, f, indent=2)
        except Exception as e:
            logger.debug(f"Failed to save resolution cache: {e}")

def check_market_resolution(condition_id, buy_side):
    """Check Gamma API whether buy_side won or lost. Returns 'win', 'loss', or 'pending'."""
    cache = _load_resolution_cache()
    cache_key = f"{condition_id}:{buy_side}"
    if cache_key in cache:
        return cache[cache_key]

    try:
        url = f"https://gamma-api.polymarket.com/markets?condition_ids={condition_id}&limit=1"
        logger.info(f"RESOLUTION: Checking {condition_id[:16]}... side={buy_side}")
        resp = http_requests.get(url, timeout=5)
        if resp.status_code != 200:
            logger.warning(f"RESOLUTION: API returned {resp.status_code} for {condition_id[:16]}")
            return "pending"

        markets = resp.json()
        if not markets or not isinstance(markets, list):
            return "pending"

        market = markets[0]
        if not market.get("closed"):
            return "pending"

        outcomes_raw = market.get("outcomes", "[]")
        prices_raw = market.get("outcomePrices", "[]")
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else (outcomes_raw or [])
        prices = json.loads(prices_raw) if isinstance(prices_raw, str) else (prices_raw or [])

        if not outcomes or not prices:
            return "pending"

        # Build outcome -> resolved price map (e.g. {"UP": 1.0, "DOWN": 0.0})
        outcome_map = {}
        for i, name in enumerate(outcomes):
            if i < len(prices):
                try:
                    outcome_map[name.upper()] = float(prices[i])
                except (ValueError, TypeError):
                    pass

        # YES=Up, NO=Down
        side_aliases = {"YES": ["YES", "UP"], "NO": ["NO", "DOWN"]}
        aliases = side_aliases.get(buy_side.upper(), [buy_side.upper()])

        resolved_price = None
        for alias in aliases:
            if alias in outcome_map:
                resolved_price = outcome_map[alias]
                break

        if resolved_price is None:
            return "pending"

        result = "win" if resolved_price > 0.5 else "loss"
        logger.info(f"RESOLUTION: {condition_id[:16]}... side={buy_side} -> {result} (outcome_map={outcome_map})")
        cache[cache_key] = result
        _save_resolution_cache()
        return result

    except Exception as e:
        logger.warning(f"RESOLUTION: Failed for {condition_id[:16]}...: {e}")
        return "pending"

def parse_trades_from_logs():
    """Parse logs to extract trade history."""
    global _market_name_cache
    _market_name_cache = None  # Force cache rebuild

    if not os.path.exists(LOG_FILE):
        return []

    trades = []
    reset_ts = get_reset_timestamp()
    # Track full condition_ids from buys to match with truncated sell IDs
    condition_id_map = {}  # truncated -> full

    # Patterns for different trade types
    # 1. Buy Once fills - capture full condition_id
    buy_once_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?TRADE: (0x[a-fA-F0-9]+) - \[SUCCESS\] 'Buy Once' order filled: ([\d\.]+) (YES|NO) @ ([\d\.]+)")
    # 2. Safety exits with [PARTIAL/FULL] confirmation - actual sale with confirmed amount/price
    # Format: SAFETY EXIT: 0x21270027 - [PARTIAL/FULL] STOP_LOSS sold 19.9996 @ 0.010 on attempt 4
    sell_confirmed_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?SAFETY EXIT: (0x[a-fA-F0-9]+) - \[PARTIAL/FULL\] (?:PRE_RESOLUTION|STOP_LOSS|TRAILING_STOP) sold ([\d\.]+) @ ([\d\.]+)")
    # 3. Safety exits without [PARTIAL/FULL] - need to track start + success separately
    # Format: SAFETY EXIT: 0x5a8385ff - Starting PRE_RESOLUTION sell: 5.0000 shares @ 0.999 (FLOOR)
    sell_start_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?SAFETY EXIT: (0x[a-fA-F0-9]+) - Starting (?:PRE_RESOLUTION|STOP_LOSS|TRAILING_STOP) sell: ([\d\.]+) shares @ ([\d\.]+)")
    # 4. Safety exit success confirmation - "[SUCCESS] Entire position closed"
    sell_success_pattern = re.compile(r"SAFETY EXIT: (0x[a-fA-F0-9]+) - \[SUCCESS\] Entire position closed")
    # 4. Manual / other sells
    manual_sell_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?Sell order placed: (YES|NO) ([\d\.]+) shares @ ([\d\.]+)")
    # 5. Claims - dashboard claims (new format)
    claim_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?CLAIM: (0x[a-fA-F0-9]+) - redeemed ([\d\.]+) shares")
    # 6. Auto-claims from trading bot - "Successfully redeemed 0x... (~$50.00)"
    # Format: 2026-02-05 02:08:06,524 - __main__ - INFO - AUTO-CLAIM: ✓ Successfully redeemed 0x418471a4 (~$40.33)
    auto_claim_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?AUTO-CLAIM:.*?(?:Successfully redeemed|already claimed)\s*(0x[a-fA-F0-9]+).*?\(\~?\$?([\d\.]+)\)")
    # 7. Fallback for simpler logs
    fallback_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*?(BUY|SELL).*?(?:0x[a-fA-F0-9]+).*?([\d\.]+) @ ([\d\.]+)")

    # Pattern for ORDER_MANAGER fill data (has actual takingAmount/makingAmount)
    # Format: ORDER_MANAGER: Order response received: type=<class 'dict'>, value={'order_id': '...', ... 'condition_id': '0x...', 'side': 'NO', '_raw_response': {..., 'takingAmount': '67.5891', 'makingAmount': '80', ...}}
    order_fill_pattern = re.compile(r"ORDER_MANAGER: Order response received:.*?'condition_id': '(0x[a-fA-F0-9]+)'.*?'side': '(\w+)'.*?'takingAmount': '([\d\.]+)'.*?'makingAmount': '([\d\.]+)'")

    # Two-pass approach: first collect all data, then filter successful sells
    pending_sells = {}  # truncated_id -> {ts, size, price} (most recent start)
    successful_sell_ids = set()  # truncated_ids that had [SUCCESS]
    phantom_sell_ids = set()  # sells where a REDEEM/CLAIM happened after (proves sell reverted on-chain)
    # Actual fill data from ORDER_MANAGER responses keyed by truncated condition_id
    # Stores list of fills so we can match sequentially
    sell_fill_data = {}  # truncated_id -> [{"taking": float, "making": float}, ...]
    buy_fill_data = {}   # truncated_id -> [{"taking": float, "making": float}, ...]

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # First pass: collect sell starts, successes, and fill data
        for line in lines:
            # Track sell start attempts
            match = sell_start_pattern.search(line)
            if match:
                ts, truncated_id, size, price = match.groups()
                pending_sells[truncated_id] = {"ts": ts, "size": float(size), "price": float(price)}

            # Track successful exits
            match = sell_success_pattern.search(line)
            if match:
                truncated_id = match.group(1)
                successful_sell_ids.add(truncated_id)

            # Detect phantom sells: if a REDEEM/CLAIM happens for a condition_id
            # that was already "sold", the on-chain sell tx must have reverted
            if successful_sell_ids and ("REDEEM:" in line or "AUTO-CLAIM:" in line or "CLAIM:" in line):
                activity_match = re.search(r"(?:REDEEM|AUTO-CLAIM|CLAIM):?\s*(?:Position \d+/\d+ - )?(0x[a-fA-F0-9]+)", line)
                if activity_match:
                    cid = activity_match.group(1)[:10]
                    if cid in successful_sell_ids:
                        phantom_sell_ids.add(cid)

            # Track actual fill data from ORDER_MANAGER responses
            if "ORDER_MANAGER: Order response received:" in line and "'takingAmount'" in line:
                match = order_fill_pattern.search(line)
                if match:
                    full_cid, side, taking, making = match.groups()
                    truncated = full_cid[:10]
                    price_match = re.search(r"'price': ([\d\.]+)", line)
                    order_price = float(price_match.group(1)) if price_match else 0.5
                    if order_price < 0.50:
                        # Floor-priced sell
                        if truncated not in sell_fill_data:
                            sell_fill_data[truncated] = []
                        sell_fill_data[truncated].append({
                            "taking": float(taking),
                            "making": float(making)
                        })
                    else:
                        # Buy order
                        if truncated not in buy_fill_data:
                            buy_fill_data[truncated] = []
                        buy_fill_data[truncated].append({
                            "taking": float(taking),
                            "making": float(making)
                        })

        # Second pass: parse all trades
        for line in lines:
            # BUYS
            match = buy_once_pattern.search(line)
            if match:
                ts, cond_id, size, side, price = match.groups()
                # Store mapping from truncated ID to full ID
                truncated = cond_id[:10]  # "0x" + first 8 hex chars
                condition_id_map[truncated] = cond_id
                logged_price = float(price)
                logged_size = float(size)

                # Try to get actual fill price from ORDER_MANAGER response
                # For buys: makingAmount = USDC spent, takingAmount = shares received
                actual_price = logged_price
                fills = buy_fill_data.get(truncated, [])
                if fills:
                    fill = fills.pop(0)
                    # making = USDC spent, taking = shares received
                    if fill["taking"] > 0:
                        actual_price = round(fill["making"] / fill["taking"], 4)

                trades.append({
                    "timestamp": ts,
                    "condition_id": cond_id,
                    "market": get_market_name(cond_id),
                    "type": "BUY",
                    "side": side,
                    "size": logged_size,
                    "price": actual_price,
                    "value": logged_size * actual_price,
                    "source": "BuyOnce"
                })
                continue

            # CONFIRMED SELLS - [PARTIAL/FULL] entries have actual sale data
            # NOTE: The logged price is the ORDER price, not the FILL price!
            # Use actual fill data from ORDER_MANAGER response (takingAmount/makingAmount)
            match = sell_confirmed_pattern.search(line)
            if match:
                ts, truncated_id, size, price = match.groups()
                if truncated_id in phantom_sell_ids:
                    continue
                logged_price = float(price)
                logged_size = float(size)

                # Try to get actual fill price from ORDER_MANAGER response
                actual_price = logged_price
                actual_value = logged_size * logged_price
                fills = sell_fill_data.get(truncated_id, [])
                if fills and logged_price < 0.50:
                    # Pop the first matching fill (they appear in order)
                    fill = fills.pop(0)
                    # takingAmount = $ received, makingAmount = shares sold
                    actual_value = fill["taking"]
                    actual_price = fill["taking"] / fill["making"] if fill["making"] > 0 else logged_price

                full_cond_id = condition_id_map.get(truncated_id, truncated_id)
                trades.append({
                    "timestamp": ts,
                    "condition_id": full_cond_id,
                    "market": get_market_name(full_cond_id),
                    "type": "SELL",
                    "size": logged_size,
                    "price": actual_price,
                    "value": actual_value,
                    "source": "SafetyExit"
                })
                # Mark this as already processed so we don't double-count from start+success
                successful_sell_ids.discard(truncated_id)
                continue

            # MANUAL SELLS
            match = manual_sell_pattern.search(line)
            if match:
                ts, side, size, price = match.groups()
                trades.append({
                    "timestamp": ts,
                    "condition_id": "Unknown",
                    "type": "SELL",
                    "side": side,
                    "size": float(size),
                    "price": float(price),
                    "value": float(size) * float(price),
                    "source": "Manual"
                })
                continue

            # CLAIMS - redeemed at $1.00 per share (winning positions)
            match = claim_pattern.search(line)
            if match:
                ts, cond_id, size = match.groups()
                # Map truncated condition_id to full ID so it matches buys
                full_cond_id = condition_id_map.get(cond_id, cond_id)
                trades.append({
                    "timestamp": ts,
                    "condition_id": full_cond_id,
                    "market": get_market_name(full_cond_id),
                    "type": "CLAIM",
                    "size": float(size),
                    "price": 1.0,  # Claims always redeem at $1.00
                    "value": float(size),  # $1.00 per share
                    "source": "Claim"
                })
                continue

            # AUTO-CLAIMS from trading bot (historical)
            match = auto_claim_pattern.search(line)
            if match:
                ts, cond_id, value = match.groups()
                # Map truncated condition_id to full ID so it matches buys
                full_cond_id = condition_id_map.get(cond_id, cond_id)
                # Value is in dollars, and each share = $1.00 when claimed
                size = float(value)
                trades.append({
                    "timestamp": ts,
                    "condition_id": full_cond_id,
                    "market": get_market_name(full_cond_id),
                    "type": "CLAIM",
                    "size": size,
                    "price": 1.0,
                    "value": size,
                    "source": "AutoClaim"
                })
                continue

            # FALLBACK - skip lines already handled by dedicated patterns
            if "ORDER_MANAGER:" in line or "SAFETY EXIT:" in line or "POLYMARKET_CLIENT:" in line:
                continue
            match = fallback_pattern.search(line)
            if match:
                ts, t_type, size, price = match.groups()
                trades.append({
                    "timestamp": ts,
                    "condition_id": "Unknown",
                    "type": t_type,
                    "size": float(size),
                    "price": float(price),
                    "value": float(size) * float(price),
                    "source": "LogAnalysis"
                })

        # Add successful safety exit sells (only those with [SUCCESS] confirmation)
        for truncated_id, sell_data in pending_sells.items():
            if truncated_id in successful_sell_ids and truncated_id not in phantom_sell_ids:
                full_cond_id = condition_id_map.get(truncated_id, truncated_id)
                sell_price = sell_data["price"]
                sell_value = sell_data["size"] * sell_price
                # Use actual fill data if available and price was floor
                fills = sell_fill_data.get(truncated_id, [])
                if fills and sell_price < 0.50:
                    fill = fills.pop(0)
                    sell_value = fill["taking"]
                    sell_price = fill["taking"] / fill["making"] if fill["making"] > 0 else sell_price
                trades.append({
                    "timestamp": sell_data["ts"],
                    "condition_id": full_cond_id,
                    "market": get_market_name(full_cond_id),
                    "type": "SELL",
                    "size": sell_data["size"],
                    "price": sell_price,
                    "value": sell_value,
                    "source": "SafetyExit"
                })

    except Exception as e:
        logger.error(f"Error parsing logs: {e}")

    # Filter trades by reset timestamp if set
    if reset_ts:
        trades = [t for t in trades if t["timestamp"] >= reset_ts]

    return trades

def calculate_stats(trades):
    """Summarize trade stats using only trade logs (no positions.json dependency).

    Win/Loss logic (log-based only):
    - BUY = investment
    - SELL (stop loss) = return at sell price (usually a loss)
    - CLAIM = return at $1.00 per share (winning positions held to resolution)
    - Positions without sells or claims = pending (not counted)
    """
    if not trades:
        return {
            "total_pnl": 0, "total_roi": 0, "total_invested": 0, "total_returned": 0,
            "win_rate": 0, "trade_count": 0, "daily_pnl": [], "wins": 0, "losses": 0
        }

    # Reset in-memory resolution cache so it reloads from disk
    global _resolution_cache
    _resolution_cache = None

    # Track buys, sells, and claims per condition_id
    positions_traded = defaultdict(lambda: {
        "bought": 0, "bought_value": 0,
        "sold": 0, "sold_value": 0,  # Stop-loss sells
        "claimed": 0, "claimed_value": 0,  # Redeemed at $1.00
        "avg_buy_price": 0, "date": None, "timestamp": None,
        "buy_side": None  # YES or NO
    })

    for t in trades:
        cid = t["condition_id"]
        if t["type"] == "BUY":
            positions_traded[cid]["bought"] += t["size"]
            positions_traded[cid]["bought_value"] += t["value"]
            positions_traded[cid]["date"] = t["timestamp"].split(" ")[0]
            positions_traded[cid]["timestamp"] = t["timestamp"]
            if t.get("side"):
                positions_traded[cid]["buy_side"] = t["side"]
        elif t["type"] == "CLAIM":
            # Claims always return $1.00 per share
            positions_traded[cid]["claimed"] += t["size"]
            positions_traded[cid]["claimed_value"] += t["size"]  # $1.00 per share
        else:
            # SELL (stop loss or manual)
            positions_traded[cid]["sold"] += t["size"]
            positions_traded[cid]["sold_value"] += t["value"]

    # Calculate avg buy price for each position
    for cid, data in positions_traded.items():
        if data["bought"] > 0:
            data["avg_buy_price"] = data["bought_value"] / data["bought"]

    total_invested = 0
    total_returned = 0
    wins = 0
    losses = 0

    # Daily aggregation
    daily = defaultdict(lambda: {"invested": 0, "returned": 0})

    # Get current time for checking if positions are old enough to be resolved
    # 15-minute markets resolve within ~16 min; 35 min threshold is safe
    from datetime import datetime, timedelta
    now = datetime.now()
    resolution_cutoff = (now - timedelta(minutes=35)).strftime("%Y-%m-%d %H:%M:%S")

    for cid, data in positions_traded.items():
        date = data["date"] or "unknown"
        total_exited = data["sold"] + data["claimed"]
        total_returned_value = data["sold_value"] + data["claimed_value"]

        if total_exited > 0 and data["bought"] > 0:
            # Has exits (sells and/or claims) - only count REALIZED portion
            exit_ratio = min(total_exited / data["bought"], 1.0)
            invested_for_exited = data["bought_value"] * exit_ratio

            total_invested += invested_for_exited
            total_returned += total_returned_value
            daily[date]["invested"] += invested_for_exited
            daily[date]["returned"] += total_returned_value

            # Win if returned >= invested
            if total_returned_value >= invested_for_exited:
                wins += 1
            else:
                losses += 1

        elif data["bought"] > 0 and total_exited == 0:
            # No sell/claim logged — check Gamma API for actual resolution
            buy_timestamp = data.get("timestamp", "")
            buy_side = data.get("buy_side")
            if buy_timestamp and buy_timestamp < resolution_cutoff and buy_side:
                resolution = check_market_resolution(cid, buy_side)
                logger.info(f"STATS: {cid[:16]}... no exit, side={buy_side}, resolution={resolution}, bought={data['bought']}")
                if resolution == "win":
                    returned = data["bought"]  # $1.00 per share
                    total_invested += data["bought_value"]
                    total_returned += returned
                    daily[date]["invested"] += data["bought_value"]
                    daily[date]["returned"] += returned
                    wins += 1
                elif resolution == "loss":
                    total_invested += data["bought_value"]
                    daily[date]["invested"] += data["bought_value"]
                    losses += 1
            elif not buy_side:
                logger.warning(f"STATS: {cid[:16]}... no exit, buy_side=None — skipping resolution check")

        # Positions with sells = already counted above
        # Positions with low buy price (<98c) without exits = still pending

    total_pnl = total_returned - total_invested
    total_roi = (total_pnl / total_invested * 100) if total_invested > 0 else 0
    win_rate = (wins / (wins + losses) * 100) if (wins + losses) > 0 else 0

    # Calculate 24h and 7d stats
    from datetime import datetime, timedelta
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    week_ago = (now - timedelta(days=7)).strftime("%Y-%m-%d")

    pnl_24h = 0
    pnl_7d = 0
    trades_24h = 0
    trades_7d = 0

    # Use daily dict for P&L (already correctly calculated above)
    for date, ddata in daily.items():
        if date >= yesterday:
            pnl_24h += ddata["returned"] - ddata["invested"]
        if date >= week_ago:
            pnl_7d += ddata["returned"] - ddata["invested"]

    # Count actual trades (closed positions) per time period - separate from P&L
    for cid, data in positions_traded.items():
        date = data["date"] or "unknown"
        total_exited = data["sold"] + data["claimed"]
        if total_exited > 0 and data["bought"] > 0:
            # This position was closed - count it as a trade
            if date >= yesterday:
                trades_24h += 1
            if date >= week_ago:
                trades_7d += 1
        elif data["bought"] > 0 and total_exited == 0:
            buy_timestamp = data.get("timestamp", "")
            buy_side = data.get("buy_side")
            if buy_timestamp and buy_timestamp < resolution_cutoff and buy_side:
                resolution = check_market_resolution(cid, buy_side)
                if resolution in ("win", "loss"):
                    if date >= yesterday:
                        trades_24h += 1
                    if date >= week_ago:
                        trades_7d += 1

    daily_stats = []
    cumulative_pnl = 0
    for date, ddata in sorted(daily.items()):
        pnl = ddata["returned"] - ddata["invested"]
        roi = (pnl / ddata["invested"] * 100) if ddata["invested"] > 0 else 0
        cumulative_pnl += pnl
        daily_stats.append({
            "date": date,
            "pnl": round(pnl, 2),
            "roi": round(roi, 2),
            "invested": round(ddata["invested"], 2),
            "cumulative": round(cumulative_pnl, 2)
        })

    return {
        "total_pnl": round(total_pnl, 2),
        "total_roi": round(total_roi, 2),
        "total_invested": round(total_invested, 2),
        "total_returned": round(total_returned, 2),
        "trade_count": len(positions_traded),
        "daily_pnl": daily_stats,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 1),
        "pnl_24h": round(pnl_24h, 2),
        "pnl_7d": round(pnl_7d, 2),
        "trades_24h": trades_24h,
        "trades_7d": trades_7d
    }

@app.route('/')
def index():
    return render_template('dashboard.html')

@app.route('/api/summary')
def get_summary():
    trades = parse_trades_from_logs()
    stats = calculate_stats(trades)

    # Get balance
    balance = 0
    if client:
        try:
            balance = client.get_available_balance() or 0
        except:
            pass

    stats["balance"] = round(balance, 2)
    stats["reset_timestamp"] = get_reset_timestamp()

    # ROI Calculation
    # Use Estimated Starting Balance = Current Balance - Total PnL
    # This gives "Return on Capital" instead of "Return on Turnover"
    total_pnl = stats.get("total_pnl", 0)
    current_balance = stats.get("balance", 0)

    # Estimate what we started with (for the current PnL session)
    # If PnL is positive, we started with less. If negative, we started with more.
    estimated_starting_balance = current_balance - total_pnl

    if estimated_starting_balance > 0:
        stats["total_roi"] = round((total_pnl / estimated_starting_balance) * 100, 2)
    elif stats.get("total_invested", 0) > 0:
        # Fallback to turnover-based ROI if starting balance calc is weird (e.g. infinite roi)
        stats["total_roi"] = round((total_pnl / stats["total_invested"]) * 100, 2)
    else:
        stats["total_roi"] = 0

    return jsonify(stats)

@app.route('/api/positions')
def get_positions():
    """Get current active positions from Polymarket API (live on-chain data).

    Falls back to positions.json with time-based filtering if API is unavailable.
    This ensures manually claimed positions are correctly excluded.
    """
    global _market_name_cache
    _market_name_cache = None  # Force cache rebuild for fresh data

    from datetime import datetime, timedelta

    # Strategy 1: Try to get live positions from Polymarket API
    # This is the most accurate since it reflects actual on-chain state
    # (catches manual claims, auto-claims, sells - everything)
    if client:
        try:
            api_positions = client.get_positions()
            if api_positions is not None:
                positions = []
                for pos in api_positions:
                    condition_id = pos.get("conditionId") or pos.get("condition_id") or pos.get("market")
                    if not condition_id:
                        continue

                    size = float(pos.get("size", 0) or pos.get("shares", 0) or 0)
                    if size <= 0.01:
                        continue  # Skip empty positions

                    outcome = (pos.get("outcome", "") or "").upper()
                    side = outcome if outcome in ("YES", "NO") else (pos.get("side", "YES") or "YES")
                    avg_price = float(pos.get("avgPrice", 0) or pos.get("avg_price", 0) or 0)
                    market_name = get_market_name(condition_id)

                    positions.append({
                        "condition_id": condition_id,
                        "market": market_name,
                        "side": side,
                        "shares": round(size, 4),
                        "avg_price": round(avg_price, 3),
                        "value": round(size * avg_price, 2),
                        "last_update": datetime.now().isoformat()
                    })
                return jsonify(positions)
        except Exception as e:
            logger.warning(f"Failed to fetch live positions from API: {e}")

    # Strategy 2: Fallback to positions.json with time filter
    if not os.path.exists(POSITIONS_FILE):
        return jsonify([])

    try:
        with open(POSITIONS_FILE, "r") as f:
            data = json.load(f)

        now = datetime.now()
        cutoff = now - timedelta(minutes=20)

        # Build map of claimed/sold shares from trade logs
        trades = parse_trades_from_logs()
        exited_shares = {}
        for t in trades:
            if t["type"] in ("CLAIM", "SELL"):
                cid_key = t["condition_id"]
                exited_shares[cid_key] = exited_shares.get(cid_key, 0) + t["size"]

        positions = []
        for cid, pos in data.items():
            if pos.get("YES", 0) > 0 or pos.get("NO", 0) > 0:
                side = "YES" if pos.get("YES", 0) > 0 else "NO"
                shares = pos.get(side, 0)

                # Subtract logged exits
                exited = exited_shares.get(cid, 0)
                remaining = shares - exited
                if remaining <= 0.01:
                    continue

                # Time filter - positions older than 20 min are likely resolved
                last_update = pos.get("last_update")
                if last_update:
                    try:
                        update_dt = datetime.fromisoformat(last_update)
                        if update_dt < cutoff:
                            continue
                    except (ValueError, TypeError):
                        pass

                avg_price = pos.get(f"avg_price_{side.lower()}", 0)
                market_name = get_market_name(cid)

                positions.append({
                    "condition_id": cid,
                    "market": market_name,
                    "side": side,
                    "shares": round(remaining, 4),
                    "avg_price": round(avg_price, 3),
                    "value": round(remaining * avg_price, 2),
                    "last_update": last_update
                })
        return jsonify(positions)
    except Exception as e:
        logger.error(f"Error reading positions: {e}")
        return jsonify([])

@app.route('/api/trades')
def get_recent_trades():
    trades = parse_trades_from_logs()
    # Return last 50 trades, newest first
    return jsonify(trades[::-1][:50])

@app.route('/api/reset-pnl', methods=['POST'])
def reset_pnl():
    """Reset P&L tracking to start from now."""
    reset_ts = set_reset_timestamp()
    logger.info(f"P&L reset at {reset_ts}")
    return jsonify({"status": "ok", "reset_timestamp": reset_ts})

@app.route('/api/reset-info')
def get_reset_info():
    """Get current reset timestamp."""
    reset_ts = get_reset_timestamp()
    return jsonify({"reset_timestamp": reset_ts})

@app.route('/api/sync-positions', methods=['POST'])
def sync_positions():
    """Sync positions from Polymarket API and update local positions.json."""
    if not client:
        return jsonify({"status": "error", "message": "Client not initialized"}), 500

    try:
        # Fetch positions from Polymarket
        api_positions = client.get_positions()

        if api_positions is None:
            return jsonify({"status": "error", "message": "Failed to fetch positions from API"}), 500

        # Convert API response to our positions.json format
        new_positions = {}
        for pos in api_positions:
            condition_id = pos.get("conditionId") or pos.get("condition_id") or pos.get("market")
            if not condition_id:
                continue

            # Handle different API response formats
            outcome = pos.get("outcome", "").upper()
            size = float(pos.get("size", 0) or pos.get("shares", 0) or 0)
            avg_price = float(pos.get("avgPrice", 0) or pos.get("avg_price", 0) or 0)

            if condition_id not in new_positions:
                new_positions[condition_id] = {
                    "YES": 0.0,
                    "NO": 0.0,
                    "avg_price_yes": 0.0,
                    "avg_price_no": 0.0,
                    "last_update": datetime.now().isoformat()
                }

            if outcome == "YES" or pos.get("side") == "YES":
                new_positions[condition_id]["YES"] = size
                new_positions[condition_id]["avg_price_yes"] = avg_price
            elif outcome == "NO" or pos.get("side") == "NO":
                new_positions[condition_id]["NO"] = size
                new_positions[condition_id]["avg_price_no"] = avg_price

        # Save to positions.json
        with open(POSITIONS_FILE, "w") as f:
            json.dump(new_positions, f, indent=4)

        logger.info(f"Synced {len(new_positions)} positions from Polymarket")
        return jsonify({
            "status": "ok",
            "synced": len(new_positions),
            "positions": len(api_positions)
        })

    except Exception as e:
        logger.error(f"Error syncing positions: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

def get_markets_from_logs():
    """Parse trading bot logs to find recently discovered markets."""
    markets_found = {}
    if not os.path.exists(LOG_FILE):
        return markets_found

    # Pattern: ✓ Auto-discovery successful for BTC: condition_id=0x..., YES=Up
    # Also capture endDate if available from "Resolved BTC 15m via slug: condition_id=..., endDate=..."
    discovery_pattern = re.compile(
        r"Auto-discovery successful for (\w+): condition_id=(0x[a-fA-F0-9]+)"
    )
    slug_pattern = re.compile(
        r"Resolved (\w+) (\d+[mh]) via slug: condition_id=(0x[a-fA-F0-9]+)(?:, endDate=(\S+))?"
    )

    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            for line in f:
                # Check slug resolution first (has more info)
                match = slug_pattern.search(line)
                if match:
                    symbol, timeframe, cond_id, end_date = match.groups()
                    key = f"{symbol.upper()}_{timeframe}"
                    markets_found[key] = {
                        "symbol": symbol.upper(),
                        "timeframe": timeframe,
                        "condition_id": cond_id,
                        "end_date": end_date
                    }
                    continue

                # Check auto-discovery pattern
                match = discovery_pattern.search(line)
                if match:
                    symbol, cond_id = match.groups()
                    key = f"{symbol.upper()}_15m"
                    if key not in markets_found:
                        markets_found[key] = {
                            "symbol": symbol.upper(),
                            "timeframe": "15m",
                            "condition_id": cond_id,
                            "end_date": None
                        }
    except Exception as e:
        logger.error(f"Error parsing logs for markets: {e}")

    return markets_found

def get_clob_realtime_prices(condition_id: str):
    """Fetch real-time prices from CLOB API for a market condition."""
    global _token_id_cache

    # Get token IDs (cached per condition_id since they don't change)
    if condition_id not in _token_id_cache:
        try:
            resp = http_requests.get(
                f"https://clob.polymarket.com/markets/{condition_id}",
                timeout=5
            )
            if resp.status_code == 200:
                data = resp.json()
                mapping = {}
                for token in data.get("tokens", []):
                    outcome = (token.get("outcome") or "").upper()
                    token_id = token.get("token_id")
                    if token_id and outcome:
                        mapping[outcome] = token_id
                if mapping:
                    _token_id_cache[condition_id] = mapping
        except Exception as e:
            logger.debug(f"Failed to fetch CLOB token IDs for {condition_id}: {e}")

    token_map = _token_id_cache.get(condition_id, {})
    if not token_map:
        return None, None

    yes_price = None
    no_price = None

    for outcome, token_id in token_map.items():
        try:
            resp = http_requests.get(
                f"https://clob.polymarket.com/price?token_id={token_id}&side=buy",
                timeout=5
            )
            if resp.status_code == 200:
                price = resp.json().get("price")
                if price is not None:
                    price_float = float(price)
                    if outcome in ("YES", "UP"):
                        yes_price = price_float
                    elif outcome in ("NO", "DOWN"):
                        no_price = price_float
        except Exception:
            pass

    return yes_price, no_price


def get_market_by_slug_direct(symbol: str, timeframe: str = "15m"):
    """Try to get market directly via slug pattern."""
    import time as _time
    interval = 900 if timeframe == "15m" else 3600
    ts = int(_time.time())
    bucket = ((ts + interval) // interval) * interval - interval
    slug = f"{symbol.lower()}-updown-{timeframe}-{bucket}"

    try:
        market_data = client.get_market_by_slug(slug)
        if market_data:
            # Parse outcomePrices and outcomes from Gamma API (they're JSON strings!)
            outcome_prices_raw = market_data.get("outcomePrices", "[]")
            outcomes_raw = market_data.get("outcomes", "[]")

            if isinstance(outcome_prices_raw, str):
                outcome_prices = json.loads(outcome_prices_raw)
            else:
                outcome_prices = outcome_prices_raw or []

            if isinstance(outcomes_raw, str):
                outcomes = json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw or []

            yes_price = None
            no_price = None

            # Match prices to outcomes (Up = YES, Down = NO)
            for i, outcome in enumerate(outcomes):
                if i < len(outcome_prices):
                    try:
                        price = float(outcome_prices[i])
                        if outcome.upper() in ["UP", "YES"]:
                            yes_price = price
                        elif outcome.upper() in ["DOWN", "NO"]:
                            no_price = price
                    except (ValueError, TypeError):
                        pass

            return {
                "condition_id": market_data.get("conditionId") or market_data.get("condition_id"),
                "end_date": market_data.get("endDate") or market_data.get("end_date"),
                "tokens": market_data.get("tokens", []),
                "resolved": market_data.get("resolved", False),
                "question": market_data.get("question", ""),
                "yes_price": yes_price,
                "no_price": no_price,
                "last_trade_price": market_data.get("lastTradePrice")
            }
    except Exception as e:
        logger.debug(f"Slug lookup failed for {slug}: {e}")
    return None

@app.route('/api/markets')
def get_live_markets():
    """Get live prices for active markets using multiple strategies."""
    markets = []

    if not client:
        logger.warning("Client not initialized for /api/markets")
        return jsonify(markets)

    trading_status = get_trading_status()

    try:
        for symbol in ALL_SYMBOLS:
            market_data = None
            condition_id = None
            end_date = None

            # Strategy 1: Try slug-based lookup (most reliable for current markets)
            slug_result = get_market_by_slug_direct(symbol, "15m")
            if slug_result and slug_result.get("condition_id"):
                condition_id = slug_result["condition_id"]
                end_date = slug_result.get("end_date")
                market_data = slug_result
                logger.debug(f"Found {symbol} via slug: {condition_id}")

            # Strategy 2: Check trading bot logs for recently discovered markets
            if not condition_id:
                log_markets = get_markets_from_logs()
                key = f"{symbol.upper()}_15m"
                if key in log_markets:
                    condition_id = log_markets[key]["condition_id"]
                    end_date = log_markets[key].get("end_date")
                    logger.debug(f"Found {symbol} from logs: {condition_id}")

            if not condition_id:
                continue

            # Build market info
            result = {
                "name": f"{symbol.upper()} 15m",
                "symbol": symbol.lower(),
                "condition_id": condition_id,
                "yes_price": None,
                "no_price": None,
                "spread": None,
                "end_date": end_date,
                "resolved": market_data.get("resolved", False) if market_data else False,
                "enabled": trading_status.get(symbol.lower(), False)
            }

            # Get real-time prices from CLOB /price endpoint
            clob_yes, clob_no = get_clob_realtime_prices(condition_id)
            if clob_yes is not None:
                result["yes_price"] = clob_yes
            if clob_no is not None:
                result["no_price"] = clob_no

            # Fallback: Use Gamma API's cached outcomePrices if CLOB failed
            if not result["yes_price"] and market_data and market_data.get("yes_price"):
                result["yes_price"] = market_data["yes_price"]
            if not result["no_price"] and market_data and market_data.get("no_price"):
                result["no_price"] = market_data["no_price"]

            # Get Binance price for context
            if not result["yes_price"] or not result["no_price"]:
                try:
                    import requests as req
                    binance_symbol = f"{symbol.upper()}USDT"
                    binance_resp = req.get(
                        f"https://api.binance.com/api/v3/ticker/price?symbol={binance_symbol}",
                        timeout=5
                    )
                    if binance_resp.status_code == 200:
                        result["binance_price"] = float(binance_resp.json().get("price", 0))
                except:
                    pass

            # Calculate spread
            if result["yes_price"] and result["no_price"]:
                total = result["yes_price"] + result["no_price"]
                result["spread"] = round(abs(1.0 - total) * 100, 2)

            markets.append(result)

    except Exception as e:
        logger.error(f"Error in get_live_markets: {e}")

    return jsonify(markets)

@app.route('/api/toggle-market', methods=['POST'])
def toggle_market():
    """Enable or disable trading for a market symbol."""
    data = request.get_json()
    symbol = data.get("symbol", "").lower()
    enabled = data.get("enabled", False)

    if symbol not in ALL_SYMBOLS:
        return jsonify({"status": "error", "message": f"Unknown symbol: {symbol}"}), 400

    status = set_trading_status(symbol, enabled)
    logger.info(f"Trading {'enabled' if enabled else 'disabled'} for {symbol.upper()}")
    return jsonify({"status": "ok", "trading_status": status})

@app.route('/api/trading-status')
def trading_status_api():
    """Get current trading enabled/disabled status for all symbols."""
    return jsonify(get_trading_status())

# Initialize polymarket-apis clients for claiming (lazy load)
_web3_client = None
_data_client = None

def get_claim_clients():
    """Lazy initialize the polymarket-apis clients for claiming."""
    global _web3_client, _data_client
    if _web3_client is None:
        try:
            from polymarket_apis import PolymarketGaslessWeb3Client, PolymarketDataClient

            # Debug: log config values
            pk = config.POLYMARKET_PRIVATE_KEY
            logger.info(f"Config loaded - PRIVATE_KEY set: {bool(pk)}, length: {len(pk) if pk else 0}")
            logger.info(f"Config loaded - SIGNATURE_TYPE: {config.POLYMARKET_SIGNATURE_TYPE}")
            logger.info(f"Config loaded - CHAIN_ID: {getattr(config, 'POLYMARKET_CHAIN_ID', 137)}")

            # Validate config
            if not pk:
                logger.error("POLYMARKET_PRIVATE_KEY not set in config")
                return None, None

            sig_type = getattr(config, "POLYMARKET_SIGNATURE_TYPE", 2)
            if sig_type not in [1, 2]:
                logger.warning(f"POLYMARKET_SIGNATURE_TYPE should be 1 or 2, got {sig_type}, defaulting to 2")
                sig_type = 2  # Default to EOA wallet

            _web3_client = PolymarketGaslessWeb3Client(
                private_key=pk,
                signature_type=sig_type,
                chain_id=getattr(config, "POLYMARKET_CHAIN_ID", 137)
            )
            _data_client = PolymarketDataClient()
            logger.info(f"Initialized claim clients for {_web3_client.address} with sig_type={sig_type}")
        except ImportError:
            logger.warning("polymarket-apis not installed - claim feature unavailable")
            return None, None
        except Exception as e:
            logger.error(f"Failed to initialize claim clients: {e}")
            return None, None
    return _web3_client, _data_client

@app.route('/api/redeemable')
def get_redeemable():
    """Get all redeemable positions (resolved markets with winnings to claim)."""
    web3_client, data_client = get_claim_clients()

    if not web3_client or not data_client:
        return jsonify({
            "available": False,
            "error": "Claim feature not available - polymarket-apis not installed",
            "positions": [],
            "total_value": 0,
            "count": 0
        })

    try:
        positions = data_client.get_positions(web3_client.address, redeemable=True)

        if not positions:
            return jsonify({
                "available": True,
                "positions": [],
                "total_value": 0,
                "count": 0
            })

        # Format positions for frontend
        formatted = []
        total_value = 0

        for pos in positions:
            size = float(getattr(pos, 'size', 0) or 0)
            title = getattr(pos, 'title', 'Unknown Market')
            outcome = getattr(pos, 'outcome', 'Unknown')

            # Each winning share = $1.00
            value = size
            total_value += value

            formatted.append({
                "condition_id": getattr(pos, 'condition_id', ''),
                "title": title[:50] + "..." if len(title) > 50 else title,
                "outcome": outcome,
                "size": round(size, 4),
                "value": round(value, 2),
                "outcome_index": getattr(pos, 'outcome_index', 0),
                "negative_risk": getattr(pos, 'negative_risk', False) or getattr(pos, 'negRisk', False)
            })

        return jsonify({
            "available": True,
            "positions": formatted,
            "total_value": round(total_value, 2),
            "count": len(formatted)
        })

    except Exception as e:
        logger.error(f"Error fetching redeemable positions: {e}")
        return jsonify({
            "available": True,
            "error": str(e),
            "positions": [],
            "total_value": 0,
            "count": 0
        })

def _get_last_claim_time():
    """Read last claim timestamp from shared lock file (cross-process safe)."""
    try:
        if os.path.exists(CLAIM_LOCK_FILE):
            with open(CLAIM_LOCK_FILE, "r") as f:
                return float(f.read().strip())
    except (ValueError, OSError):
        pass
    return 0

def _wait_for_claim_cooldown():
    """Wait until enough time has passed since last claim to respect rate limits."""
    min_gap = 20  # seconds between any two claim API calls (cross-process)
    last = _get_last_claim_time()
    elapsed = time.time() - last
    if elapsed < min_gap:
        wait = min_gap - elapsed
        logger.info(f"Claim cooldown: waiting {wait:.0f}s before next claim...")
        time.sleep(wait)

def _record_claim_time():
    """Write current timestamp to shared lock file (cross-process safe)."""
    try:
        with open(CLAIM_LOCK_FILE, "w") as f:
            f.write(str(time.time()))
    except OSError as e:
        logger.debug(f"Failed to write claim lock file: {e}")

@app.route('/api/claim', methods=['POST'])
def claim_all():
    """Claim all redeemable positions."""
    web3_client, data_client = get_claim_clients()

    if not web3_client or not data_client:
        return jsonify({
            "status": "error",
            "message": "Claim feature not available - check logs for initialization errors"
        }), 400

    # Try to acquire time-based claim lock (auto-expires after CLAIM_LOCK_MAX_AGE)
    global _claim_lock_time
    with _claim_lock_mutex:
        now = time.time()
        if _claim_lock_time > 0 and (now - _claim_lock_time) < CLAIM_LOCK_MAX_AGE:
            elapsed = int(now - _claim_lock_time)
            return jsonify({
                "status": "error",
                "message": f"Another claim operation is running ({elapsed}s ago). Please wait and try again."
            }), 429
        _claim_lock_time = now

    try:
        logger.info(f"Fetching redeemable positions for {web3_client.address}")
        positions = data_client.get_positions(web3_client.address, redeemable=True)
        logger.info(f"Found {len(positions) if positions else 0} redeemable positions")

        if not positions:
            return jsonify({
                "status": "ok",
                "message": "No positions to claim",
                "claimed": 0,
                "total_value": 0
            })

        # Build batch of positions to redeem
        batch = []
        total_value = 0.0
        for pos in positions:
            condition_id = pos.condition_id
            neg_risk = getattr(pos, 'negative_risk', False) or getattr(pos, 'negRisk', False)
            size = float(pos.size)
            outcome_index = pos.outcome_index

            amounts = [0.0, 0.0]
            amounts[outcome_index] = size

            batch.append({
                "condition_id": condition_id,
                "amounts": amounts,
                "neg_risk": neg_risk,
                "size": size,
            })
            total_value += size

        if not batch:
            return jsonify({
                "status": "ok",
                "message": "No valid positions to claim",
                "claimed": 0,
                "total_value": 0
            })

        # Single attempt - retries just perpetuate relayer rate limits
        from claim_utils import batch_redeem

        try:
            _wait_for_claim_cooldown()
            logger.info("CLAIM: Redeeming %d positions (~$%.2f)", len(batch), total_value)

            batch_redeem(web3_client, batch)
            _record_claim_time()

            for pos_data in batch:
                logger.info("CLAIM: %s - redeemed %.4f shares @ $1.00 = $%.2f",
                           pos_data["condition_id"], pos_data["size"], pos_data["size"])

            logger.info("CLAIM: Complete: %d positions, ~$%.2f", len(batch), total_value)
            return jsonify({
                "status": "ok",
                "message": f"Claimed {len(batch)} positions",
                "claimed": len(batch),
                "total_value": round(total_value, 2),
                "errors": None,
                "skipped": 0
            })

        except Exception as claim_e:
            _record_claim_time()
            error_msg = str(claim_e).lower()
            is_rate_limited = any(x in error_msg for x in ['429', 'too many', 'rate limit', 'throttl'])
            is_already_claimed = 'already' in error_msg

            # Extract 429 response body for debugging
            response_detail = ''
            if hasattr(claim_e, 'response'):
                try:
                    response_detail = claim_e.response.text[:500]
                except Exception:
                    pass

            if is_already_claimed:
                logger.info("CLAIM: Positions already claimed")
                return jsonify({
                    "status": "ok",
                    "message": "Positions already claimed",
                    "claimed": len(batch),
                    "total_value": round(total_value, 2),
                    "errors": None,
                    "skipped": 0
                })
            elif is_rate_limited:
                # Parse "resets in X seconds" from relayer response
                import re
                reset_match = re.search(r'resets in (\d+) seconds', response_detail or error_msg)
                if reset_match:
                    reset_min = int(reset_match.group(1)) // 60
                    human_msg = f"Relayer quota exceeded. Resets in ~{reset_min} minutes. Auto-claim will retry after reset."
                else:
                    human_msg = f"Rate limited by relayer. Try again later. Detail: {response_detail or str(claim_e)[:100]}"
                logger.warning("CLAIM: %s", human_msg)
                return jsonify({
                    "status": "error",
                    "message": human_msg,
                    "claimed": 0,
                    "total_value": 0,
                    "errors": [response_detail or "Rate limited"],
                    "skipped": len(batch)
                })
            else:
                # Non-rate-limit error: try individual claims
                logger.warning("CLAIM: Batch failed (%s), trying individual claims", claim_e)
                time.sleep(15)
                claimed_count = 0
                errors = []
                for pos_data in batch:
                    try:
                        _wait_for_claim_cooldown()
                        web3_client.redeem_position(
                            condition_id=pos_data["condition_id"],
                            amounts=pos_data["amounts"],
                            neg_risk=pos_data["neg_risk"]
                        )
                        _record_claim_time()
                        claimed_count += 1
                        logger.info("CLAIM: %s - redeemed %.4f shares",
                                   pos_data["condition_id"][:16], pos_data["size"])
                    except Exception as ind_e:
                        _record_claim_time()
                        errors.append(f"{pos_data['condition_id'][:16]}: {str(ind_e)[:60]}")

                return jsonify({
                    "status": "ok" if claimed_count > 0 else "error",
                    "message": f"Claimed {claimed_count}/{len(batch)} individually",
                    "claimed": claimed_count,
                    "total_value": round(sum(p["size"] for p in batch[:claimed_count]), 2),
                    "errors": errors if errors else None,
                    "skipped": len(batch) - claimed_count
                })

    except Exception as e:
        logger.error(f"Error in claim_all: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
    finally:
        with _claim_lock_mutex:
            _claim_lock_time = 0.0

@app.route('/api/analytics')
def get_analytics():
    """Get detailed strategy performance analytics using FIFO PnL matching."""
    trades = parse_trades_from_logs()
    
    # FIFO matching structures: market -> side -> queue of (size, price, timestamp)
    inventory = defaultdict(lambda: {"YES": [], "NO": []})
    cid_to_market = {}
    
    market_stats = defaultdict(lambda: {
        "realized_pnl": 0.0, 
        "wins": 0, 
        "losses": 0, 
        "trades": 0,
        "volume": 0.0
    })
    
    hourly_pnl = defaultdict(float)
    hourly_pnl_by_date = defaultdict(lambda: defaultdict(float))
    daily_pnl = defaultdict(float)
    hold_times = {"win": [], "loss": []}
    
    def parse_ts(ts_str):
        try:
            return datetime.strptime(ts_str.split(',')[0], "%Y-%m-%d %H:%M:%S")
        except:
            return None

    # Sort trades by timestamp to ensure FIFO
    sorted_trades = sorted(trades, key=lambda x: x["timestamp"])

    for t in sorted_trades:
        market = t["market"]
        cid = t["condition_id"]
        cid_to_market[cid] = market
        side = t.get("side", "")
        t_type = t["type"]
        size = t["size"]
        price = t["price"]
        val = t["value"]
        ts = parse_ts(t["timestamp"])

        if not ts: continue

        market_stats[market]["volume"] += val

        if t_type == "BUY":
            if not side:
                side = "YES"  # Default for buys
            inventory[cid][side].append({
                "size": size,
                "price": price,
                "ts": ts
            })
        elif t_type in ["SELL", "CLAIM"]:
            # Outcome price: CLAIM is always 1.0, SELL is the trade price
            exit_price = 1.0 if t_type == "CLAIM" else price
            remaining_exit_size = size

            # If side not specified, find which side has inventory for this condition_id
            if not side:
                yes_qty = sum(e["size"] for e in inventory[cid]["YES"])
                no_qty = sum(e["size"] for e in inventory[cid]["NO"])
                side = "NO" if no_qty >= yes_qty else "YES"

            # Match against inventory (FIFO)
            while remaining_exit_size > 0.0001 and inventory[cid][side]:
                entry = inventory[cid][side][0]
                match_size = min(remaining_exit_size, entry["size"])
                
                # Calculate PnL for this matched portion
                entry_cost = match_size * entry["price"]
                exit_return = match_size * exit_price
                trade_pnl = exit_return - entry_cost
                
                # Accrue stats
                market_stats[market]["realized_pnl"] += trade_pnl
                market_stats[market]["trades"] += 1
                hourly_pnl[ts.hour] += trade_pnl
                hourly_pnl_by_date[ts.strftime("%Y-%m-%d")][ts.hour] += trade_pnl
                daily_pnl[ts.strftime("%Y-%m-%d")] += trade_pnl
                
                # Holding time
                duration = (ts - entry["ts"]).total_seconds() / 60
                if trade_pnl >= 0.01: # Small threshold to avoid rounding noise
                    hold_times["win"].append(duration)
                    market_stats[market]["wins"] += 1
                else:
                    hold_times["loss"].append(duration)
                    market_stats[market]["losses"] += 1
                
                # Update remaining sizes
                remaining_exit_size -= match_size
                entry["size"] -= match_size
                
                if entry["size"] < 0.0001:
                    inventory[cid][side].pop(0)

    # Unmatched positions: check Gamma API for actual resolution
    from datetime import timedelta
    ref_ts = datetime.now()
    if sorted_trades:
        last_log_ts = parse_ts(sorted_trades[-1]["timestamp"])
        if last_log_ts:
            ref_ts = last_log_ts

    for cid, sides in inventory.items():
        market = cid_to_market.get(cid, "Unknown")
        for side, entries in sides.items():
            for entry in entries:
                if (ref_ts - entry["ts"]).total_seconds() > (35 * 60):
                    resolution = check_market_resolution(cid, side)
                    if resolution == "win":
                        trade_pnl = (entry["size"] * 1.0) - (entry["size"] * entry["price"])
                        exit_ts = entry["ts"] + timedelta(minutes=16)
                        market_stats[market]["realized_pnl"] += trade_pnl
                        market_stats[market]["trades"] += 1
                        hourly_pnl[exit_ts.hour] += trade_pnl
                        hourly_pnl_by_date[exit_ts.strftime("%Y-%m-%d")][exit_ts.hour] += trade_pnl
                        daily_pnl[exit_ts.strftime("%Y-%m-%d")] += trade_pnl
                        hold_times["win"].append(16.0)
                        market_stats[market]["wins"] += 1
                    elif resolution == "loss":
                        trade_pnl = -(entry["size"] * entry["price"])
                        exit_ts = entry["ts"] + timedelta(minutes=16)
                        market_stats[market]["realized_pnl"] += trade_pnl
                        market_stats[market]["trades"] += 1
                        hourly_pnl[exit_ts.hour] += trade_pnl
                        hourly_pnl_by_date[exit_ts.strftime("%Y-%m-%d")][exit_ts.hour] += trade_pnl
                        daily_pnl[exit_ts.strftime("%Y-%m-%d")] += trade_pnl
                        hold_times["loss"].append(16.0)
                        market_stats[market]["losses"] += 1

    # Convert to frontend format
    analytics_markets = []
    for m, data in market_stats.items():
        win_rate = 0
        total_closed = data["wins"] + data["losses"]
        if total_closed > 0:
            win_rate = (data["wins"] / total_closed) * 100
            
        analytics_markets.append({
            "market": m,
            "pnl": round(data["realized_pnl"], 2),
            "win_rate": round(win_rate, 1),
            "trades": total_closed,
            "volume": round(data["volume"], 2)
        })
    
    analytics_markets.sort(key=lambda x: x["pnl"], reverse=True)
    
    # Build daily series sorted by date
    sorted_days = sorted(daily_pnl.keys())
    daily_series = [{"date": d, "pnl": round(daily_pnl[d], 2)} for d in sorted_days]

    # Weekly aggregation (ISO week)
    weekly_pnl = defaultdict(float)
    for d in sorted_days:
        dt = datetime.strptime(d, "%Y-%m-%d")
        week_label = dt.strftime("%Y-W%W")
        weekly_pnl[week_label] += daily_pnl[d]
    sorted_weeks = sorted(weekly_pnl.keys())
    weekly_series = [{"week": w, "pnl": round(weekly_pnl[w], 2)} for w in sorted_weeks]

    # Monthly aggregation
    monthly_pnl = defaultdict(float)
    for d in sorted_days:
        month_label = d[:7]  # YYYY-MM
        monthly_pnl[month_label] += daily_pnl[d]
    sorted_months = sorted(monthly_pnl.keys())
    monthly_series = [{"month": m, "pnl": round(monthly_pnl[m], 2)} for m in sorted_months]

    # All-time cumulative (daily granularity)
    cumulative = 0
    alltime_series = []
    for d in sorted_days:
        cumulative += daily_pnl[d]
        alltime_series.append({"date": d, "cumulative": round(cumulative, 2), "pnl": round(daily_pnl[d], 2)})

    # Build hourly_by_date: { "2026-02-05": [{"hour": 0, "pnl": ...}, ...], ... }
    hourly_by_date_out = {}
    for date_str in sorted(hourly_pnl_by_date.keys()):
        hourly_by_date_out[date_str] = [
            {"hour": h, "pnl": round(hourly_pnl_by_date[date_str][h], 2)} for h in range(24)
        ]

    # P&L Prediction based on historical performance
    total_closed = sum(m.get("trades", 0) for m in analytics_markets)
    overall_win_rate = (sum(m["win_rate"] * m.get("trades", 0) for m in analytics_markets) / total_closed) if total_closed > 0 else 0

    # Calculate avg daily P&L from actual daily data
    daily_pnl_values = [daily_pnl[d] for d in sorted_days]
    num_days = len(daily_pnl_values)
    avg_daily_pnl = sum(daily_pnl_values) / num_days if num_days > 0 else 0

    # Use recent 3 days as trend if available (weighted more recent)
    if num_days >= 3:
        recent_3d = daily_pnl_values[-3:]
        trend_daily = (recent_3d[0] * 0.2 + recent_3d[1] * 0.3 + recent_3d[2] * 0.5)
    elif num_days >= 1:
        trend_daily = daily_pnl_values[-1]
    else:
        trend_daily = 0

    # Blend: 60% overall average + 40% recent trend
    projected_daily = avg_daily_pnl * 0.6 + trend_daily * 0.4 if num_days > 0 else 0

    # Current cumulative P&L
    current_cumulative = cumulative  # from alltime_series calc above

    # Build 14-day projection series (past actuals + future projected)
    projection_series = []
    # Add last few actual days for context (up to 7)
    actual_days_to_show = min(7, len(alltime_series))
    for entry in alltime_series[-actual_days_to_show:]:
        projection_series.append({
            "label": entry["date"][5:],  # MM-DD
            "value": round(entry["cumulative"], 2),
            "type": "actual"
        })

    # Project 365 days forward (show ~monthly labels for readability)
    proj_cumulative = current_cumulative
    today = datetime.now()
    for i in range(1, 366):
        proj_cumulative += projected_daily
        # Include data points at: day 1-7 daily, then 1st of each month
        future_date = today + timedelta(days=i)
        if i <= 7 or future_date.day == 1 or i in (30, 60, 90, 180, 365):
            projection_series.append({
                "label": future_date.strftime("%b %d") if i <= 30 else future_date.strftime("%b '%y"),
                "value": round(proj_cumulative, 2),
                "type": "projected"
            })

    prediction = {
        "avg_daily_pnl": round(avg_daily_pnl, 2),
        "projected_daily": round(projected_daily, 2),
        "projected_7d": round(projected_daily * 7, 2),
        "projected_30d": round(projected_daily * 30, 2),
        "projected_60d": round(projected_daily * 60, 2),
        "projected_90d": round(projected_daily * 90, 2),
        "projected_180d": round(projected_daily * 180, 2),
        "projected_1y": round(projected_daily * 365, 2),
        "win_rate": round(overall_win_rate, 1),
        "total_trades": total_closed,
        "days_tracked": num_days,
        "projection": projection_series
    }

    return jsonify({
        "markets": analytics_markets,
        "hourly": [{"hour": h, "pnl": round(hourly_pnl[h], 2)} for h in range(24)],
        "hourly_by_date": hourly_by_date_out,
        "daily": daily_series,
        "weekly": weekly_series,
        "monthly": monthly_series,
        "alltime": alltime_series,
        "prediction": prediction
    })


# --- Leaderboard Reporting ---

_leaderboard_cache = {"data": [], "last_fetch": None}

def report_to_leaderboard():
    """Send current stats to the central leaderboard server."""
    if not getattr(config, 'LEADERBOARD_ENABLED', False):
        return

    url = getattr(config, 'LEADERBOARD_URL', '')
    if not url:
        return

    try:
        trades = parse_trades_from_logs()
        stats = calculate_stats(trades)

        wallet_hint = getattr(config, 'POLYMARKET_WALLET_ADDRESS', '')[:10]

        # Calculate capital-based ROI (same as dashboard /api/summary)
        total_pnl = stats.get("total_pnl", 0)
        balance = 0
        if client:
            try:
                balance = client.get_available_balance() or 0
            except:
                pass
        estimated_starting_balance = balance - total_pnl
        if estimated_starting_balance > 0:
            roi = round((total_pnl / estimated_starting_balance) * 100, 2)
        elif stats.get("total_invested", 0) > 0:
            roi = round((total_pnl / stats["total_invested"]) * 100, 2)
        else:
            roi = 0

        payload = {
            "username": getattr(config, 'LEADERBOARD_USERNAME', 'Anonymous'),
            "wallet_hint": wallet_hint,
            "total_pnl": total_pnl,
            "win_rate": stats.get("win_rate", 0),
            "wins": stats.get("wins", 0),
            "losses": stats.get("losses", 0),
            "total_trades": (stats.get("wins", 0) + stats.get("losses", 0)),
            "roi": roi,
            "pnl_24h": stats.get("pnl_24h", 0),
            "pnl_7d": stats.get("pnl_7d", 0),
        }

        http_requests.post(f"{url}/api/report", json=payload, timeout=10)
        logger.debug("Leaderboard stats reported successfully")
    except Exception as e:
        logger.debug(f"Leaderboard report failed: {e}")


def leaderboard_reporter_loop():
    """Background thread that reports stats periodically."""
    import time
    interval = getattr(config, 'LEADERBOARD_REPORT_INTERVAL', 300)
    time.sleep(15)  # Wait for dashboard to initialize
    while True:
        report_to_leaderboard()
        time.sleep(interval)


@app.route('/api/leaderboard')
def get_leaderboard():
    """Proxy leaderboard data from the central server (cached 30s)."""
    global _leaderboard_cache
    now = datetime.now()

    # Return cache if fresh
    if (_leaderboard_cache["last_fetch"] and
            (now - _leaderboard_cache["last_fetch"]).total_seconds() < 30):
        return jsonify(_leaderboard_cache["data"])

    url = getattr(config, 'LEADERBOARD_URL', '')
    if not url:
        return jsonify([])

    try:
        resp = http_requests.get(f"{url}/api/leaderboard", timeout=10)
        data = resp.json()
        _leaderboard_cache = {"data": data, "last_fetch": now}
        return jsonify(data)
    except Exception as e:
        logger.debug(f"Leaderboard fetch failed: {e}")
        return jsonify(_leaderboard_cache["data"])


if __name__ == '__main__':
    # Ensure template dir exists
    os.makedirs("templates", exist_ok=True)
    os.makedirs("static", exist_ok=True)

    # Start leaderboard reporter in background
    if getattr(config, 'LEADERBOARD_ENABLED', False):
        reporter = threading.Thread(target=leaderboard_reporter_loop, daemon=True)
        reporter.start()
        logger.info(f"Leaderboard reporting enabled as '{getattr(config, 'LEADERBOARD_USERNAME', 'Anonymous')}'")

    port = 5052
    print(f"Starting Dashboard on http://localhost:{port}")
    app.run(host='0.0.0.0', port=port, debug=True)
