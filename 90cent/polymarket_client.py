"""
Polymarket API Client for real-time price feeds and order placement
"""
import json
import logging
import random
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Set

import requests
import websocket
from py_clob_client.client import ClobClient
from py_clob_client.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    OrderArgs,
    PartialCreateOrderOptions,
    RequestArgs,
    OrderType,
)
from py_clob_client.headers.headers import create_level_2_headers
from py_clob_client.exceptions import PolyApiException

# Import retry logic for error recovery
from error_recovery import retry_with_backoff, retry_on_api_error, ErrorClassifier

# Import caching for performance optimization
from cache_manager import TTLCache

# Try to import py_order_utils for building signed orders (for FOK/FAK support)
try:
    from py_order_utils.builders import OrderBuilder
    from py_order_utils.model.order import OrderData
    from py_order_utils.model.sides import BUY, SELL
    from py_order_utils.model.signatures import EOA, POLY_PROXY
    from py_order_utils.signer import Signer
    PY_ORDER_UTILS_AVAILABLE = True
except ImportError:
    PY_ORDER_UTILS_AVAILABLE = False
    logger.warning("py_order_utils not available - FOK/FAK orders will fall back to GTC")

# Import order builder helpers from py_clob_client
try:
    from py_clob_client.order_builder.helpers import (
        decimal_places,
        round_down,
        round_normal,
        round_up,
        to_token_decimals,
    )
    ORDER_BUILDER_HELPERS_AVAILABLE = True
except ImportError:
    ORDER_BUILDER_HELPERS_AVAILABLE = False
    logger.warning("py_clob_client order_builder helpers not available")

logger = logging.getLogger(__name__)


class RateLimiter:
    """
    Thread-safe rate limiter for Polymarket API.
    
    Enforces:
    - Burst limit: 240 requests/second
    - Sustained limit: 40 requests/second (over 1 second window)
    """
    
    def __init__(self, burst_limit: int = 240, sustained_limit: int = 40, window_seconds: float = 1.0):
        """
        Initialize rate limiter.
        
        Args:
            burst_limit: Maximum requests allowed in a short burst
            sustained_limit: Maximum requests per window_seconds
            window_seconds: Time window for sustained limit (default 1 second)
        """
        self.burst_limit = burst_limit
        self.sustained_limit = sustained_limit
        self.window_seconds = window_seconds
        
        # Thread-safe request tracking
        self.lock = threading.Lock()
        
        # Track request timestamps
        # For burst: track last N requests
        self.burst_timestamps = deque(maxlen=burst_limit)
        
        # For sustained: track requests in sliding window
        self.sustained_timestamps = deque()
        
        # Statistics
        self.total_requests = 0
        self.total_delays = 0
        self.total_delay_time = 0.0
    
    def wait_if_needed(self, request_count: int = 1) -> float:
        """
        Wait if necessary to respect rate limits.
        
        Args:
            request_count: Number of requests being made (for batch orders)
        
        Returns:
            Time waited in seconds
        """
        with self.lock:
            current_time = time.time()
            wait_time = 0.0
            
            # Clean old timestamps outside the window
            cutoff_time = current_time - self.window_seconds
            while self.sustained_timestamps and self.sustained_timestamps[0] < cutoff_time:
                self.sustained_timestamps.popleft()
            
            # Check sustained limit (requests per second)
            if len(self.sustained_timestamps) + request_count > self.sustained_limit:
                # Need to wait until we can make the request
                if self.sustained_timestamps:
                    oldest_time = self.sustained_timestamps[0]
                    wait_until = oldest_time + self.window_seconds
                    wait_time = max(0.0, wait_until - current_time)
                    
                    if wait_time > 0:
                        logger.debug(
                            "RATE_LIMIT: Sustained limit reached (%d/%d), waiting %.3f seconds",
                            len(self.sustained_timestamps), self.sustained_limit, wait_time
                        )
                        time.sleep(wait_time)
                        current_time = time.time()
                        # Clean up old timestamps after waiting
                        cutoff_time = current_time - self.window_seconds
                        while self.sustained_timestamps and self.sustained_timestamps[0] < cutoff_time:
                            self.sustained_timestamps.popleft()
            
            # Check burst limit (very short-term)
            if len(self.burst_timestamps) + request_count > self.burst_limit:
                # For burst, we need to ensure minimum spacing
                if self.burst_timestamps:
                    # Calculate minimum time between requests for burst limit
                    min_interval = 1.0 / self.burst_limit
                    last_request_time = self.burst_timestamps[-1] if self.burst_timestamps else 0
                    next_allowed_time = last_request_time + (min_interval * request_count)
                    burst_wait = max(0.0, next_allowed_time - current_time)
                    
                    if burst_wait > 0:
                        logger.debug(
                            "RATE_LIMIT: Burst limit protection, waiting %.3f seconds",
                            burst_wait
                        )
                        time.sleep(burst_wait)
                        current_time = time.time()
                        wait_time += burst_wait
            
            # Record the requests
            for _ in range(request_count):
                self.burst_timestamps.append(current_time)
                self.sustained_timestamps.append(current_time)
            
            self.total_requests += request_count
            if wait_time > 0:
                self.total_delays += 1
                self.total_delay_time += wait_time
            
            return wait_time
    
    def get_stats(self) -> Dict:
        """Get rate limiter statistics"""
        with self.lock:
            current_time = time.time()
            cutoff_time = current_time - self.window_seconds
            recent_requests = sum(1 for ts in self.sustained_timestamps if ts >= cutoff_time)
            
            return {
                "total_requests": self.total_requests,
                "recent_requests_per_sec": recent_requests,
                "total_delays": self.total_delays,
                "total_delay_time": self.total_delay_time,
                "sustained_limit": self.sustained_limit,
                "burst_limit": self.burst_limit,
            }


class WebSocketReconnectManager:
    """
    Manages WebSocket reconnection with exponential backoff.
    
    Features:
    - Exponential backoff: wait time doubles after each failed attempt
    - Maximum backoff cap: prevents infinite wait times
    - Jitter: randomizes wait times to prevent thundering herd
    - Connection attempt tracking
    - Reset on successful connection
    """
    
    def __init__(self, initial_backoff: float = 1.0, max_backoff: float = 300.0, 
                 backoff_multiplier: float = 2.0, jitter_max: float = 1.0):
        """
        Initialize reconnection manager.
        
        Args:
            initial_backoff: Initial wait time in seconds (default: 1s)
            max_backoff: Maximum wait time in seconds (default: 300s = 5 min)
            backoff_multiplier: Multiplier for exponential backoff (default: 2.0)
            jitter_max: Maximum jitter to add in seconds (default: 1s)
        """
        self.initial_backoff = initial_backoff
        self.max_backoff = max_backoff
        self.backoff_multiplier = backoff_multiplier
        self.jitter_max = jitter_max
        
        self.connection_attempts = 0
        self.last_error_time = None
        self.last_error = None
        self.consecutive_failures = 0
        self.last_success_time = None
        self.lock = threading.Lock()
    
    def get_next_backoff(self) -> float:
        """
        Calculate next backoff delay with exponential backoff and jitter.
        
        Returns:
            Wait time in seconds before next reconnection attempt
        """
        with self.lock:
            if self.consecutive_failures == 0:
                # First failure - use initial backoff
                base_delay = self.initial_backoff
            else:
                # Exponential backoff: initial * (multiplier ^ failures)
                base_delay = self.initial_backoff * (self.backoff_multiplier ** self.consecutive_failures)
            
            # Cap at maximum backoff
            base_delay = min(base_delay, self.max_backoff)
            
            # Add jitter (random value between 0 and jitter_max)
            # This prevents all clients from reconnecting at the same time
            jitter = random.uniform(0, min(self.jitter_max, base_delay * 0.1))
            total_delay = base_delay + jitter
            
            return total_delay
    
    def record_failure(self, error: Exception = None):
        """Record a connection failure."""
        with self.lock:
            self.connection_attempts += 1
            self.consecutive_failures += 1
            self.last_error_time = time.time()
            if error:
                self.last_error = str(error)
    
    def record_success(self):
        """Record a successful connection."""
        with self.lock:
            self.consecutive_failures = 0
            self.last_success_time = time.time()
            self.last_error = None
    
    def reset(self):
        """Reset all counters (e.g., after manual reconnect)."""
        with self.lock:
            self.connection_attempts = 0
            self.consecutive_failures = 0
            self.last_error = None
    
    def get_stats(self) -> Dict:
        """Get reconnection statistics."""
        with self.lock:
            return {
                "connection_attempts": self.connection_attempts,
                "consecutive_failures": self.consecutive_failures,
                "last_error_time": self.last_error_time,
                "last_success_time": self.last_success_time,
                "last_error": self.last_error,
                "next_backoff": self.get_next_backoff() if self.consecutive_failures > 0 else 0.0
            }


class PolymarketClient:
    """Client for interacting with Polymarket API"""
    
    def __init__(
        self,
        api_key: str,
        private_key: str,
        api_url: str = "https://clob.polymarket.com",
        api_secret: str = "",
        api_passphrase: str = "",
        wallet_address: str = "",
        chain_id: int = 137,
        signature_type: Optional[int] = None,
        funder_address: str = "",
        outcome_map: Optional[Dict[str, Dict[str, str]]] = None,
        ws_url: Optional[str] = None,
    ):
        self.api_key = api_key
        self.private_key = self._normalize_private_key(private_key)
        self.api_secret = api_secret
        self.api_passphrase = api_passphrase
        self.wallet_address = wallet_address.lower() if wallet_address else ""
        self.api_url = api_url.rstrip("/")
        self.chain_id = chain_id
        self.signature_type = signature_type
        self.funder_address = funder_address
        self.outcome_map = outcome_map or {}
        self.token_cache: Dict[str, Dict[str, str]] = {}
        self.ws_url = self._resolve_ws_url(ws_url)
        self.ws = None
        self.ws_thread = None
        self.price_callbacks: Dict[str, List[Callable]] = {}
        self.orderbook_callbacks: Dict[str, List[Callable]] = {}
        self.running = False
        self.asset_to_condition: Dict[str, str] = {}
        self.asset_to_side: Dict[str, str] = {}
        self._reconnect_manager = WebSocketReconnectManager(
            initial_backoff=1.0,    # Start with 1 second
            max_backoff=300.0,      # Cap at 5 minutes
            backoff_multiplier=2.0, # Double each time
            jitter_max=2.0          # Add up to 2 seconds of jitter
        )
        self._reconnect_thread = None
        self._should_reconnect = False

        self.clob_client = self._init_clob_client()
        
        # Initialize rate limiter (240 orders/sec burst, 40/sec sustained)
        self.rate_limiter = RateLimiter(
            burst_limit=240,
            sustained_limit=40,
            window_seconds=1.0
        )
        
        # Initialize caches for performance optimization
        self.orderbook_cache = TTLCache(default_ttl=2.0, name="orderbook")
        self.balance_cache = TTLCache(default_ttl=5.0, name="balance")
        self._market_info_cache: Dict[str, Dict] = {}  # condition_id -> {tick_size, neg_risk}
        self._fee_rate_cache: Dict[str, int] = {}  # token_id -> fee_rate_bps
        logger.info("Initialized caches: orderbook (TTL=2.0s), balance (TTL=5.0s)")

        # Proactively ensure API credentials are set up
        self.ensure_api_credentials()

    @staticmethod
    def _normalize_private_key(private_key: str) -> str:
        if not private_key:
            return private_key
        stripped = private_key.strip()
        if stripped.startswith("0x") or stripped.startswith("0X"):
            return stripped
        if len(stripped) == 64:
            return f"0x{stripped}"
        return stripped

    def _resolve_ws_url(self, ws_url: Optional[str]) -> str:
        """Return the websocket endpoint to use."""
        if ws_url:
            return ws_url
        return "wss://ws-subscriptions-clob.polymarket.com"

    def _init_clob_client(self) -> Optional[ClobClient]:
        """Instantiate the official CLOB client for authenticated requests."""
        creds = None
        if self.api_key and self.api_secret and self.api_passphrase:
            creds = ApiCreds(
                api_key=self.api_key,
                api_secret=self.api_secret,
                api_passphrase=self.api_passphrase,
            )

        if not self.private_key:
            logger.warning(
                "POLYMARKET_PRIVATE_KEY missing - authenticated endpoints will fail"
            )
            return None

        try:
            return ClobClient(
                host=self.api_url,
                chain_id=self.chain_id,
                key=self.private_key,
                creds=creds,
                signature_type=self.signature_type,
                funder=self.funder_address or None,
            )
        except Exception as exc:
            logger.error("Failed to initialize ClobClient: %s", exc)
            return None

    def _refresh_api_credentials(self) -> bool:
        if not self.clob_client or not self.private_key:
            return False
        try:
            derived = self.clob_client.derive_api_key()
            if not derived:
                logger.debug("Deriving API credentials failed (keys may not exist yet)")
                return False
            self.clob_client.set_api_creds(derived)
            self.api_key = derived.api_key
            self.api_secret = derived.api_secret
            self.api_passphrase = derived.api_passphrase
            logger.info("Derived fresh API credentials for wallet %s", self.wallet_address or self.clob_client.get_address())
            return True
        except Exception as exc:
            logger.debug("Unable to derive API credentials: %s", exc)
            return False

    def ensure_api_credentials(self) -> bool:
        """
        Ensure the client has valid API credentials.
        If missing or invalid, attempts to derive them from the private key.
        If derivation fails, attempts to create new ones.
        """
        # If we have keys, verify them
        if self.api_key and self.api_secret and self.api_passphrase:
            try:
                # Try a simple authenticated call to verify keys
                self.clob_client.get_orders()
                logger.debug("API credentials verified and working")
                return True
            except Exception as e:
                logger.warning("Existing API credentials failed verification: %s. Attempting to derive fresh ones...", e)
                # Clear them so we force derivation
                self.api_key = ""
                self.api_secret = ""
                self.api_passphrase = ""

        if not self.private_key:
            logger.warning("Cannot ensure API credentials: Private key missing")
            return False

        logger.info("API credentials missing or invalid; attempting proactive setup...")
        
        # 1. Try to derive existing keys
        if self._refresh_api_credentials():
            return True
            
        # 2. If derivation fails, try to create new ones
        return self._create_new_api_key()

    def _create_new_api_key(self) -> bool:
        """Create a new API key for the wallet."""
        if not self.clob_client or not self.private_key:
            return False
            
        try:
            logger.info("Attempting to create new API credentials for wallet %s...", self.wallet_address or "unknown")
            # Note: create_api_key returns the same ApiCreds object as derive_api_key
            new_creds = self.clob_client.create_api_key()
            if not new_creds:
                logger.error("Failed to create new API credentials")
                return False
                
            self.clob_client.set_api_creds(new_creds)
            self.api_key = new_creds.api_key
            self.api_secret = new_creds.api_secret
            self.api_passphrase = new_creds.api_passphrase
            logger.info("Successfully created new API credentials for wallet %s", self.wallet_address or self.clob_client.get_address())
            return True
        except Exception as exc:
            logger.error("Error creating new API credentials: %s", exc)
            return False
        
    def register_market(self, condition_id: str, yes_outcome: str, no_outcome: str):
        """Register a market with its specific outcome labels."""
        if not condition_id:
            return
        self.outcome_map[condition_id.lower()] = {
            "YES": yes_outcome,
            "NO": no_outcome
        }
        logger.info("Registered market %s with outcomes: YES=%s, NO=%s", 
                   condition_id, yes_outcome, no_outcome)
        
    def _get_headers(self) -> Dict[str, str]:
        """Get authentication headers"""
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

    def _get_outcome_labels(self, condition_id: str) -> Dict[str, str]:
        """Return configured outcome labels for YES/NO sides."""
        if not condition_id:
            return {"YES": "Yes", "NO": "No"}
        return self.outcome_map.get(
            condition_id.lower(), {"YES": "Yes", "NO": "No"}
        )

    def _fetch_market_tokens(self, condition_id: str) -> Dict[str, str]:
        """Fetch token IDs for the given condition and cache them."""
        def build_mapping(market_obj: Dict) -> Dict[str, str]:
            tokens = market_obj.get("tokens", []) if isinstance(market_obj, dict) else []
            if not tokens:
                return {}
                
            mapping_local: Dict[str, str] = {}
            labels = self._get_outcome_labels(condition_id)
            
            # Group aliases for bidirectional matching
            YES_ALIASES = {"yes", "up", "long", "true"}
            NO_ALIASES = {"no", "down", "short", "false"}
            
            for side_key, target_label in labels.items():
                if not target_label:
                    continue
                
                target_lower = target_label.lower()
                
                # 1. Try exact match first
                token = next((t for t in tokens if str(t.get("outcome", "")).lower() == target_lower), None)
                
                # 2. If not found, try aliases
                if not token:
                    search_set = set()
                    if target_lower in YES_ALIASES:
                        search_set = YES_ALIASES
                    elif target_lower in NO_ALIASES:
                        search_set = NO_ALIASES
                    
                    if search_set:
                        token = next((t for t in tokens if str(t.get("outcome", "")).lower() in search_set), None)
                
                # 3. Last resort fallback for binary markets: 
                # If we only have 2 tokens and we're looking for YES, and one token is "Yes" or "Up"
                if not token and len(tokens) == 2:
                    if side_key.upper() == "YES":
                        token = next((t for t in tokens if str(t.get("outcome", "")).lower() in YES_ALIASES), None)
                    else:
                        token = next((t for t in tokens if str(t.get("outcome", "")).lower() in NO_ALIASES), None)

                if token and token.get("token_id"):
                    mapping_local[side_key.upper()] = token["token_id"]
                    logger.debug("Mapped %s to token %s (outcome: %s) for market %s", 
                               side_key, token["token_id"], token.get("outcome"), condition_id)
            
            return mapping_local

        # 1) Try via authenticated client
        try:
            if self.clob_client:
                market = self.clob_client.get_market(condition_id)
                if isinstance(market, list):
                    market = market[0] if market else {}
                mapping = build_mapping(market or {})
                if mapping:
                    self.token_cache[condition_id.lower()] = mapping
                    # Update asset_to_side reverse mapping
                    for side, token_id in mapping.items():
                        self.asset_to_side[token_id] = side
                    return mapping
        except Exception as exc:
            logger.warning("ClobClient.get_market failed for %s: %s", condition_id, exc)

        # 2) Try public REST: /markets/{condition_id}
        try:
            url = f"{self.api_url}/markets/{condition_id}"
            resp = requests.get(url, timeout=10)
            if resp.ok:
                market = resp.json() or {}
                mapping = build_mapping(market)
                if mapping:
                    self.token_cache[condition_id.lower()] = mapping
                    # Update asset_to_side reverse mapping
                    for side, token_id in mapping.items():
                        self.asset_to_side[token_id] = side
                    return mapping
        except Exception as exc:
            logger.warning("Public GET /markets/{id} failed for %s: %s", condition_id, exc)

        # 3) Try public REST: /markets and scan
        try:
            url = f"{self.api_url}/markets"
            resp = requests.get(url, timeout=15)
            if resp.ok:
                data = resp.json()
                markets = []
                if isinstance(data, list):
                    markets = data
                elif isinstance(data, dict):
                    markets = data.get("data") or data.get("markets") or data.get("results") or []
                cond_lower = condition_id.lower()
                for m in markets:
                    if not isinstance(m, dict):
                        continue
                    cid = (m.get("condition_id") or m.get("conditionId") or "").lower()
                    if cid == cond_lower:
                        mapping = build_mapping(m)
                        if mapping:
                            self.token_cache[condition_id.lower()] = mapping
                            # Update asset_to_side reverse mapping
                            for side, token_id in mapping.items():
                                self.asset_to_side[token_id] = side
                            return mapping
        except Exception as exc:
            logger.error("Failed to fetch tokens via public /markets for %s: %s", condition_id, exc)

        logger.error("Unable to resolve token mapping for %s from any source", condition_id)
        return {}

    def _get_token_id(self, condition_id: str, side: str) -> Optional[str]:
        """Resolve the token_id for the requested condition + side."""
        if not condition_id:
            return None
        condition_key = condition_id.lower()
        side_key = side.upper()
        cached = self.token_cache.get(condition_key, {})
        if side_key in cached:
            return cached[side_key]
        mapping = self._fetch_market_tokens(condition_id)
        return mapping.get(side_key)

    def _get_token_mapping(self, condition_id: str) -> Dict[str, str]:
        condition_key = condition_id.lower()
        mapping = self.token_cache.get(condition_key)
        if not mapping:
            mapping = self._fetch_market_tokens(condition_id)
        return mapping

    def _get_subscribed_asset_ids(self) -> List[str]:
        assets: Set[str] = set()
        for cond in set(list(self.price_callbacks.keys()) + list(self.orderbook_callbacks.keys())):
            if not cond:
                continue
            mapping = self._get_token_mapping(cond) or {}
            for side, token_id in mapping.items():
                if token_id:
                    assets.add(token_id)
                    self.asset_to_condition[token_id] = cond
                    self.asset_to_side[token_id] = side
        return list(assets)
    
    # ============================================================
    # Gamma API Methods for Market Discovery
    # ============================================================
    
    def get_market_by_slug(self, slug: str) -> Optional[Dict]:
        """
        Get market by slug using Gamma API.
        
        Args:
            slug: Market slug (e.g., 'btc-updown-15m-1764552600')
        
        Returns:
            Market dict with condition_id and other metadata, or None
        """
        url = f"https://gamma-api.polymarket.com/markets/slug/{slug}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 404:
                logger.debug("Market slug %s not found (404)", slug)
                return None
            resp.raise_for_status()
            data = resp.json()
            
            # Handle different response formats
            if isinstance(data, dict):
                # Sometimes wrapped in "data"
                if "data" in data and isinstance(data["data"], dict):
                    return data["data"]
                return data
            return None
        except Exception as e:
            logger.warning("Error fetching market by slug %s from Gamma API: %s", slug, e)
            return None
    
    def search_markets_gamma(self, query: str = None, tags: List[str] = None, 
                            limit: int = 100, offset: int = 0) -> List[Dict]:
        """
        Search markets using Gamma API.
        
        Args:
            query: Search query string
            tags: List of tags to filter by
            limit: Maximum number of results
            offset: Pagination offset
        
        Returns:
            List of market dicts
        """
        url = "https://gamma-api.polymarket.com/markets"
        params = {}
        
        if query:
            params["query"] = query
        if tags:
            params["tags"] = ",".join(tags)
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            
            # Handle different response formats
            if isinstance(data, list):
                return data
            elif isinstance(data, dict):
                return data.get("data") or data.get("markets") or data.get("results") or []
            return []
        except Exception as e:
            logger.warning("Error searching markets from Gamma API: %s", e)
            return []
    
    def get_market_by_condition_id(self, condition_id: str) -> Optional[Dict]:
        """
        Get market by condition ID using Gamma API.
        
        Args:
            condition_id: Market condition ID
        
        Returns:
            Market dict or None
        """
        url = f"https://gamma-api.polymarket.com/markets/{condition_id}"
        try:
            resp = requests.get(url, timeout=10)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            data = resp.json()
            
            if isinstance(data, dict):
                if "data" in data:
                    return data["data"]
                return data
            return None
        except Exception as e:
            logger.warning("Error fetching market by condition_id %s from Gamma API: %s", condition_id, e)
            return None
    
    def get_active_updown_markets(self, symbol: str = None, timeframe: str = "15m", 
                                  limit: int = 100) -> List[Dict]:
        """
        Get active Up/Down markets for a specific symbol and timeframe.
        
        Args:
            symbol: Crypto symbol (btc, eth, sol, etc.) - optional
            timeframe: Market timeframe ('15m' or '1h') - defaults to '15m'
            limit: Maximum results to return
        
        Returns:
            List of active market dicts matching criteria
        """
        # Build search tags
        tags = ["up or down"]
        if timeframe == "15m":
            tags.append("15m")
        elif timeframe == "1h":
            tags.append("1h")
        
        # Search Gamma API
        markets = self.search_markets_gamma(tags=tags, limit=limit)
        
        # Filter for active markets matching symbol
        filtered = []
        symbol_lower = symbol.lower() if symbol else None
        
        for market in markets:
            if not isinstance(market, dict):
                continue
            
            # Check if market is active/open
            active = market.get("active", True)
            resolved = market.get("resolved", False)
            closed = market.get("closed", False)
            
            if resolved or closed or not active:
                continue
            
            # If symbol specified, check if market mentions it
            if symbol_lower:
                question = (market.get("question") or "").lower()
                slug = (market.get("slug") or "").lower()
                
                # Check for symbol mention
                if symbol_lower not in question and symbol_lower not in slug:
                    # Also check for full names
                    symbol_names = {
                        "btc": ["bitcoin"],
                        "eth": ["ethereum"],
                        "sol": ["solana"],
                        "xrp": ["ripple"]
                    }
                    names = symbol_names.get(symbol_lower, [])
                    if not any(name in question or name in slug for name in names):
                        continue
            
            # Must have condition_id
            condition_id = market.get("condition_id") or market.get("conditionId")
            if not condition_id:
                continue
            
            filtered.append(market)
        
        # Sort by most recent (if timestamp available)
        filtered.sort(key=lambda m: m.get("created_at") or m.get("end_date") or 0, reverse=True)
        
        return filtered[:limit]
    
    def resolve_condition_id_from_slug_pattern(self, symbol: str, timeframe: str = "15m") -> Optional[str]:
        """
        Resolve condition ID for current timeframe bucket using slug pattern.
        Uses the pattern: {symbol}-updown-{timeframe}-{timestamp}
        
        Args:
            symbol: Crypto symbol (btc, eth, sol, etc.)
            timeframe: '15m' or '1h'
        
        Returns:
            Condition ID or None if market not yet created
        """
        import time
        
        symbol = symbol.lower()
        interval = 900 if timeframe == "15m" else 3600  # 15 min or 1 hour
        
        # Calculate current bucket timestamp (end of current window)
        ts = int(time.time())
        bucket = ((ts + interval) // interval) * interval - interval
        
        # Build slug
        slug = f"{symbol}-updown-{timeframe}-{bucket}"
        
        # Try Gamma API first
        market = self.get_market_by_slug(slug)
        if market:
            condition_id = market.get("condition_id") or market.get("conditionId")
            if condition_id:
                logger.info("Resolved condition_id %s for slug %s via Gamma API", condition_id, slug)
                return condition_id
        
        # Fallback: search active markets
        markets = self.get_active_updown_markets(symbol=symbol, timeframe=timeframe, limit=10)
        if markets:
            # Get most recent matching market
            market = markets[0]
            condition_id = market.get("condition_id") or market.get("conditionId")
            if condition_id:
                logger.info("Resolved condition_id %s for %s %s via market search (fallback)", 
                          condition_id, symbol, timeframe)
                return condition_id
        
        return None

    def _signed_request(
        self, method: str, path: str, body: Optional[Dict] = None, request_count: int = 1
    ) -> requests.Response:
        """
        Make a signed HTTP request using ClobClient credentials.
        
        Args:
            method: HTTP method (GET, POST, DELETE)
            path: API endpoint path
            body: Request body (for POST/DELETE)
            request_count: Number of requests this represents (for batch operations)
        """
        if not self.clob_client or not self.clob_client.signer or not self.clob_client.creds:
            raise ValueError("Clob client is not configured for authenticated requests")

        # Apply rate limiting
        wait_time = self.rate_limiter.wait_if_needed(request_count)
        if wait_time > 0.1:  # Log if we waited more than 100ms
            logger.debug(
                "RATE_LIMIT: Waited %.3f seconds before %s %s",
                wait_time, method, path
            )

        request_args = RequestArgs(method=method.upper(), request_path=path, body=body)
        headers = create_level_2_headers(
            self.clob_client.signer,
            self.clob_client.creds,
            request_args,
        )
        url = f"{self.api_url}{path}"
        method_upper = method.upper()
        if method_upper == "GET":
            response = requests.get(url, headers=headers, timeout=10)
        elif method_upper == "DELETE":
            response = requests.delete(url, headers=headers, json=body, timeout=10)
        else:
            response = requests.post(url, headers=headers, json=body, timeout=10)
        response.raise_for_status()
        return response
    
    def get_markets(self, token: str = None) -> List[Dict]:
        """Get available markets"""
        try:
            url = f"{self.api_url}/markets"
            if token:
                url += f"?token={token}"
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching markets: {e}")
            return []
    
    def get_market_price(self, condition_id: str) -> Optional[Dict]:
        """Get current market price for a condition"""
        try:
            url = f"{self.api_url}/markets/{condition_id}"
            response = requests.get(url, headers=self._get_headers())
            response.raise_for_status()
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching market price for {condition_id}: {e}")
            return None

    def get_price(self, token_id: str, side: str = "buy") -> Optional[float]:
        """
        Get the current price for a specific token ID using the CLOB API.
        This is a lightweight call compared to fetching the full orderbook.
        
        Args:
            token_id: The token ID
            side: "buy" or "sell" (default: "buy")
        """
        try:
            # Apply rate limiting
            self.rate_limiter.wait_if_needed(1)
            
            url = f"{self.api_url}/price?token_id={token_id}&side={side.lower()}"
            response = requests.get(url, timeout=10)
            if response.status_code == 200:
                data = response.json()
                price = data.get("price")
                return float(price) if price is not None else None
            return None
        except Exception as e:
            logger.error(f"Error fetching price for token {token_id}: {e}")
            return None

    def get_market_price_clob(self, condition_id: str, side: str) -> Optional[float]:
        """
        Get the current price for a market outcome using the CLOB API.
        """
        token_id = self._get_token_id(condition_id, side.upper())
        if not token_id:
            return None
        return self.get_price(token_id, side="buy")
    
    @retry_with_backoff(max_retries=3, initial_delay=0.5, backoff_factor=2.0)
    def get_orderbook(self, condition_id: str, side: Optional[str] = None) -> Optional[Dict]:
        """Fetch order book summary for the given condition via token IDs.
        Includes automatic retry with exponential backoff for transient errors.
        Uses TTL cache to reduce API calls (2 second TTL).
        
        Args:
            condition_id: The condition ID
            side: Optional side ("YES" or "NO") to get specific token orderbook. 
                  If None, returns YES token orderbook (default behavior).
        """
        # Try cache first
        cache_key = f"{condition_id}_{side or 'YES'}"
        cached = self.orderbook_cache.get(cache_key)
        
        if cached is not None:
            return cached
        
        # Cache miss - fetch from API
        if not self.clob_client:
            logger.warning("Clob client not configured; cannot fetch orderbook for %s", condition_id)
            return None
        mapping = self._get_token_mapping(condition_id)
        if not mapping:
            logger.warning("No token mapping available for %s", condition_id)
            return None
        
        # Get token ID for the requested side (default to YES)
        if side:
            token_id = mapping.get(side.upper())
        else:
            # Try YES first, then fallback to first available
            if "YES" in mapping:
                side = "YES"
                token_id = mapping["YES"]
            else:
                # Fallback to first available side
                side = next(iter(mapping.keys()))
                token_id = mapping[side]
        
        if not token_id:
            logger.warning("Token mapping missing IDs for %s (side=%s). Mapping: %s", condition_id, side, mapping)
            return None
        try:
            # Apply rate limiting (1 GET request)
            self.rate_limiter.wait_if_needed(1)
            
            # Log which token is being fetched for debugging
            logger.debug("ORDERBOOK: Fetching %s orderbook for %s using token_id=%s", 
                        side, condition_id, token_id[:20] + "..." if len(token_id) > 20 else token_id)
            
            summary = self.clob_client.get_order_book(token_id)
            if not summary:
                logger.warning("get_order_book returned None for token %s (condition %s)", token_id, condition_id)
                return None
            return {
                "condition_id": condition_id,
                "token_id": token_id,
                "side": side or "YES",
                "bids": [bid.__dict__ for bid in (summary.bids or [])],
                "asks": [ask.__dict__ for ask in (summary.asks or [])],
                "timestamp": summary.timestamp,
                "min_order_size": summary.min_order_size,
                "tick_size": summary.tick_size,
            }
        except Exception as e:
            error_str = str(e)
            if "No orderbook exists" in error_str or "404" in error_str:
                logger.debug("Orderbook not found for %s (token %s). Market might be inactive.", condition_id, token_id)
                return None
            logger.error("Error fetching orderbook for %s (token_id=%s, side=%s): %s", condition_id, token_id, side, e, exc_info=True)
            return None
    
    def getSpread(self, condition_id: str, side: Optional[str] = None, detailed: bool = False) -> Optional[float | Dict]:
        """
        Get the current bid-ask spread for a market.
        
        This method retrieves the spread (difference between best ask and best bid)
        for the specified market. It prioritizes cached orderbook data to minimize
        API calls and can return either a simple spread value or detailed information.
        
        Args:
            condition_id: The condition ID of the market
            side: Optional side ("YES" or "NO") to get spread for specific outcome.
                  If None, returns spread for YES token (default).
            detailed: If True, returns detailed dict with bid, ask, spread, and metadata.
                     If False (default), returns just the spread as a float.
        
        Returns:
            If detailed=False: Float spread value (ask - bid) or None on error
            If detailed=True: Dict with:
                - spread: Float spread value (ask - bid)
                - spread_pct: Spread as percentage of ask price
                - best_bid: Best bid price
                - best_ask: Best ask price  
                - mid_price: Mid-market price ((bid + ask) / 2)
                - side: The side this spread is for ("YES" or "NO")
                - timestamp: Timestamp of the orderbook data
                Or None on error
        
        Example:
            # Simple usage - just get the spread
            spread = client.getSpread("0xabc123")
            print(f"Spread: {spread}")  # Output: Spread: 0.015
            
            # Detailed usage - get full spread info
            spread_info = client.getSpread("0xabc123", detailed=True)
            print(f"Bid: {spread_info['best_bid']}, Ask: {spread_info['best_ask']}")
            print(f"Spread: {spread_info['spread']} ({spread_info['spread_pct']:.2f}%)")
            
            # Get spread for NO token
            no_spread = client.getSpread("0xabc123", side="NO")
        """
        try:
            # Get orderbook for the requested side
            orderbook = self.get_orderbook(condition_id, side=side)
            
            if not orderbook:
                logger.warning("getSpread: Unable to fetch orderbook for %s (side=%s)", 
                             condition_id, side or "YES")
                return None
            
            # Extract bids and asks
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            
            if not bids or not asks:
                logger.warning("getSpread: Orderbook for %s (side=%s) missing bids or asks",
                             condition_id, side or "YES")
                return None
            
            # Extract best bid and best ask prices
            best_bid_obj = bids[0]
            best_ask_obj = asks[0]
            
            # Handle different object formats (dict or object with .price attribute)
            if isinstance(best_bid_obj, dict):
                best_bid = float(best_bid_obj.get("price", 0))
            elif hasattr(best_bid_obj, "price"):
                best_bid = float(best_bid_obj.price)
            else:
                best_bid = float(best_bid_obj)
            
            if isinstance(best_ask_obj, dict):
                best_ask = float(best_ask_obj.get("price", 0))
            elif hasattr(best_ask_obj, "price"):
                best_ask = float(best_ask_obj.price)
            else:
                best_ask = float(best_ask_obj)
            
            # Validate prices
            if best_bid <= 0 or best_ask <= 0 or best_bid >= 1 or best_ask >= 1:
                logger.warning("getSpread: Invalid prices for %s (side=%s): bid=%.4f, ask=%.4f",
                             condition_id, side or "YES", best_bid, best_ask)
                return None
            
            # Calculate spread
            spread = best_ask - best_bid
            
            # Return simple spread if not detailed
            if not detailed:
                return spread
            
            # Calculate additional metrics for detailed response
            mid_price = (best_bid + best_ask) / 2
            spread_pct = (spread / best_ask * 100) if best_ask > 0 else 0
            
            return {
                "spread": spread,
                "spread_pct": spread_pct,
                "best_bid": best_bid,
                "best_ask": best_ask,
                "mid_price": mid_price,
                "side": side or "YES",
                "condition_id": condition_id,
                "timestamp": orderbook.get("timestamp"),
                "tick_size": orderbook.get("tick_size"),
            }
            
        except Exception as e:
            logger.error("getSpread: Error calculating spread for %s (side=%s): %s",
                        condition_id, side, e, exc_info=True)
            return None
    

    
    def _place_batch_orders_http(self, orders: List[Dict]) -> List[Optional[Dict]]:
        """
        Place multiple orders in a batch via HTTP API with orderType support (FOK/FAK/IOC).
        This is the proper batch implementation that submits all orders atomically.
        
        Args:
            orders: List of order dicts with keys: condition_id, side, price, size, time_in_force
        
        Returns:
            List of order responses (same order as input), None for failed orders
        """
        if not PY_ORDER_UTILS_AVAILABLE or not ORDER_BUILDER_HELPERS_AVAILABLE:
            logger.warning("Direct HTTP API not available - py_order_utils or helpers missing")
            return [None] * len(orders)
        
        if not self.clob_client:
            logger.error("Clob client is not configured; cannot place batch orders")
            return [None] * len(orders)
        
        if not orders:
            return []
        
        # Apply rate limiting (count all orders in batch)
        self.rate_limiter.wait_if_needed(len(orders))
        
        try:
            import config
            
            # Build signed orders for each order in the batch
            batch_payload = []
            order_metadata = []  # Track order details for result mapping
            
            signer = Signer(self.private_key)
            builder = OrderBuilder(
                exchange_address=config.POLYMARKET_EXCHANGE_ADDRESS,
                chain_id=self.chain_id,
                signer=signer,
            )
            
            # Get maker address (proxy or wallet)
            maker_address = config.POLYMARKET_PROXY_ADDRESS or self.wallet_address or self.clob_client.get_address()
            if not maker_address:
                logger.error("No maker address available for batch orders")
                return [None] * len(orders)
            
            # Get signature type
            signature_type = EOA
            if self.signature_type is not None:
                signature_type = POLY_PROXY if self.signature_type == 1 else EOA
            
            for order in orders:
                condition_id = order.get("condition_id")
                side = order.get("side", "").upper()
                price = float(order.get("price", 0))
                size = float(order.get("size", 0))
                time_in_force = order.get("time_in_force", "GTC").upper()
                
                if not condition_id or not side or price <= 0 or size <= 0:
                    logger.warning("Invalid order in batch: condition_id=%s, side=%s, price=%s, size=%s",
                                  condition_id, side, price, size)
                    batch_payload.append(None)
                    order_metadata.append({"valid": False})
                    continue
                
                # Get token ID
                token_id = self._get_token_id(condition_id, side)
                if not token_id:
                    logger.error("Unable to resolve token_id for %s (%s) in batch", condition_id, side)
                    batch_payload.append(None)
                    order_metadata.append({"valid": False})
                    continue
                
                try:
                    # Get tick size and neg_risk for correct order construction
                    tick_size_str = "0.01"  # Default
                    neg_risk = False        # Default
                    
                    try:
                        market_info = self.clob_client.get_market(condition_id)
                        if market_info:
                            if isinstance(market_info, list):
                                market_info = market_info[0] if market_info else {}
                            tick_size_str = str(market_info.get("minimum_tick_size") or market_info.get("tickSize") or "0.01")
                            neg_risk = bool(market_info.get("neg_risk") or market_info.get("negRisk"))
                    except Exception:
                        logger.debug("Could not get market info for %s, using defaults", condition_id)
                    
                    # Fetch correct fee rate for this token
                    fee_rate_bps = 0
                    try:
                        fee_rate_bps = self.clob_client.get_fee_rate_bps(token_id)
                    except Exception:
                        pass

                    # Use official clob_client to create and sign the order
                    order_args = OrderArgs(
                        token_id=token_id,
                        price=price,
                        size=size,
                        side=order.get("order_side", "BUY").upper(),
                        nonce=0,
                        expiration=0,
                        fee_rate_bps=fee_rate_bps
                    )
                    
                    order_options = PartialCreateOrderOptions(
                        tick_size=tick_size_str if tick_size_str in ['0.1', '0.01', '0.001', '0.0001'] else '0.01',
                        neg_risk=neg_risk,
                    )
                    
                    signed_order = self.clob_client.create_order(order_args, options=order_options)
                    
                    # Map IOC to FAK (Polymarket uses FAK for Immediate-Or-Cancel)
                    order_type_str = "FAK" if time_in_force == "IOC" else time_in_force
                    
                    # Create PostOrder object for batch payload
                    # Note: We still need to build the payload manually for batch /orders POST
                    # but we use the signed_order.dict() which is now correct
                    post_order = {
                        "order": signed_order.dict(),
                        "owner": self.clob_client.creds.api_key if self.clob_client.creds else self.wallet_address,
                        "orderType": order_type_str.upper()
                    }
                    
                    batch_payload.append(post_order)
                    order_metadata.append({
                        "valid": True,
                        "condition_id": condition_id,
                        "side": side,
                        "price": price,
                        "size": size,
                        "order_type": order_type
                    })
                    
                except Exception as exc:
                    logger.error("Error building signed order in batch for %s %s: %s", 
                               condition_id, side, exc, exc_info=True)
                    batch_payload.append(None)
                    order_metadata.append({"valid": False})
            
            # Filter out None orders (invalid orders)
            valid_orders = [(i, order) for i, order in enumerate(batch_payload) if order is not None]
            
            if not valid_orders:
                logger.error("No valid orders in batch")
                return [None] * len(orders)
            
            # Prepare batch request with list of PostOrder objects
            batch_request = [order for _, order in valid_orders]
            
            # POST to /orders endpoint with batch payload
            logger.info("Batch HTTP API: Submitting %d orders atomically (FOK/FAK batch)", len(batch_request))
            response = self._signed_request("POST", "/orders", body=batch_request, request_count=0)  # Already counted above
            result = response.json()
            
            # Parse batch response
            # The response should be a list of order results
            if isinstance(result, dict) and "data" in result:
                batch_results = result["data"]
            elif isinstance(result, list):
                batch_results = result
            else:
                batch_results = [result]
            
            # Map results back to original order positions
            results = [None] * len(orders)
            
            for (original_idx, _), batch_result in zip(valid_orders, batch_results):
                if batch_result:
                    metadata = order_metadata[original_idx]
                    order_type = metadata.get("order_type", "GTC")
                    
                    # Extract order_id (could be in various fields)
                    order_id = None
                    if isinstance(batch_result, dict):
                        order_id = (batch_result.get("id") or batch_result.get("order_id") or 
                                   batch_result.get("orderId") or batch_result.get("orderID"))
                    
                    # Extract status
                    order_status = "open"
                    if isinstance(batch_result, dict):
                        order_status = batch_result.get("status", "open")
                        
                        # For FOK orders: if status is not "matched", it means rejected
                        if order_type == "FOK" and order_status != "matched":
                            order_status = "rejected"
                        # For FAK/IOC: partial fills are possible
                        elif order_type in ["FAK", "IOC"]:
                            filled_size = batch_result.get("filled_size") or batch_result.get("filledSize") or batch_result.get("filled", 0)
                            if filled_size and float(filled_size) >= metadata.get("size", 0) * 0.99:
                                order_status = "matched"
                            elif filled_size and float(filled_size) > 0:
                                order_status = "partially_filled"
                    
                    # Build normalized response
                    normalized_result = {
                        "order_id": order_id,
                        "status": order_status,
                        "size": metadata.get("size"),
                        "price": metadata.get("price"),
                        "condition_id": metadata.get("condition_id"),
                        "side": metadata.get("side"),
                        "_raw_response": batch_result
                    }
                    
                    # For FOK orders that didn't fill, return None to indicate failure
                    if order_type == "FOK" and order_status != "matched":
                        logger.warning(
                            "Batch FOK order REJECTED (did not fill): %s %s @ %s for %s",
                            metadata.get("side"), metadata.get("size"), 
                            metadata.get("price"), metadata.get("condition_id")
                        )
                        results[original_idx] = None
                    else:
                        results[original_idx] = normalized_result
                        logger.info(
                            "Batch order %d: %s %s @ %s for %s - order_id=%s, status=%s",
                            original_idx + 1, metadata.get("side"), metadata.get("size"),
                            metadata.get("price"), metadata.get("condition_id")[:10] if metadata.get("condition_id") else "unknown",
                            order_id, order_status
                        )
            
            return results
            
        except Exception as exc:
            logger.error("Error placing batch HTTP orders: %s", exc, exc_info=True)
            return [None] * len(orders)
    
    @retry_with_backoff(max_retries=3, initial_delay=1.0)
    def place_limit_order(self, condition_id: str, side: str, price: float, 
                         size: float, order_type: str = "LIMIT", 
                         time_in_force: str = "GTC", order_side: str = "BUY") -> Optional[Dict]:
        """
        Place a limit order via the official CLOB client.
        Includes automatic retry with exponential backoff for transient errors.
        
        Args:
            condition_id: Market condition ID
            side: Order side ("YES" or "NO")
            price: Order price (0-1)
            size: Order size in shares
            order_type: Order type (LIMIT, etc.) - kept for compatibility
            time_in_force: Time in force - "GTC" (Good-Til-Cancelled), "FOK" (Fill-Or-Kill), 
                         "IOC" (Immediate-Or-Cancel, also called FAK), or "GTD" (Good-Til-Date)
        
        Returns:
            Order response dict or None on failure
        """
        if not self.clob_client:
            logger.error("Clob client is not configured; cannot place order")
            return None

        # Apply rate limiting (1 order = 1 request)
        self.rate_limiter.wait_if_needed(1)

        side_key = side.upper()
        token_id = self._get_token_id(condition_id, side_key)
        if not token_id:
            logger.error(
                "Unable to resolve token_id for %s (%s)", condition_id, side_key
            )
            return None

        time_in_force_upper = time_in_force.upper()
        
        # Fetch market data for tick_size and neg_risk (cached to avoid repeated API calls)
        tick_size = "0.01"
        neg_risk = False

        if condition_id in self._market_info_cache:
            cached = self._market_info_cache[condition_id]
            tick_size = cached["tick_size"]
            neg_risk = cached["neg_risk"]
        else:
            try:
                market_info = self.clob_client.get_market(condition_id)
                if market_info:
                    if isinstance(market_info, list):
                        market_info = market_info[0] if market_info else {}
                    tick_size = str(market_info.get("minimum_tick_size") or market_info.get("tickSize") or "0.01")
                    neg_risk = bool(market_info.get("neg_risk") or market_info.get("negRisk"))
                    self._market_info_cache[condition_id] = {"tick_size": tick_size, "neg_risk": neg_risk}
            except Exception as e:
                logger.debug("Could not fetch market info for %s: %s", condition_id, e)

        # Fetch fee rate (cached per token_id)
        if token_id in self._fee_rate_cache:
            fee_rate_bps = self._fee_rate_cache[token_id]
        else:
            fee_rate_bps = 0
            try:
                fee_rate_bps = self.clob_client.get_fee_rate_bps(token_id)
                self._fee_rate_cache[token_id] = fee_rate_bps
            except Exception as e:
                logger.warning("Could not fetch fee rate for %s, using 0: %s", token_id, e)

        # Map string time_in_force to OrderType enum
        enum_order_type = OrderType.GTC
        if time_in_force_upper == "FOK":
            enum_order_type = OrderType.FOK
        elif time_in_force_upper in ["FAK", "IOC"]:
            enum_order_type = OrderType.FAK

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=order_side.upper(),  # BUY or SELL
            nonce=0,                  # Use 0 for automatic nonce management
            expiration=0,
            fee_rate_bps=fee_rate_bps
        )
        
        # Options for correct order construction
        order_options = PartialCreateOrderOptions(
            tick_size=tick_size if tick_size in ['0.1', '0.01', '0.001', '0.0001'] else '0.01',
            neg_risk=neg_risk,
        )

        def _post():
            # Proactively ensure credentials are valid
            self.ensure_api_credentials()
            
            # Create the signed order
            signed_order = self.clob_client.create_order(order_args, options=order_options)
            
            # Post the order with the correct OrderType
            # Note: clob_client.post_order returns the parsed JSON dict on success (200)
            # and raises PolyApiException on failure.
            return self.clob_client.post_order(signed_order, orderType=enum_order_type)

        try:
            result = _post()
            
            # Normalize response format
            normalized_result = {}
            if isinstance(result, dict):
                order_id = (result.get("id") or result.get("order_id") or result.get("orderId") or 
                           result.get("orderID") or result.get("order-id") or result.get("_id"))
                order_status = result.get("status", "open")
                
                # For FOK orders: if status is not "matched", it means rejected
                if time_in_force_upper == "FOK" and order_status != "matched":
                    logger.warning(
                        "POLYMARKET_CLIENT: FOK order REJECTED (did not fill immediately): %s %s @ %s for %s",
                        side_key, size, price, condition_id
                    )
                    return None
                    
                normalized_result = {
                    "order_id": order_id,
                    "status": order_status,
                    "size": size,
                    "price": price,
                    "condition_id": condition_id,
                    "side": side_key,
                    "_raw_response": result
                }

            logger.info(
                "POLYMARKET_CLIENT: Order placed (%s): %s %s @ %s for %s, order_id=%s, status=%s", 
                time_in_force_upper, side_key, size, price, condition_id, 
                normalized_result.get("order_id"), normalized_result.get("status")
            )
            
            # Invalidate caches after successful order
            if normalized_result:
                self.balance_cache.invalidate("balance")
                self.orderbook_cache.invalidate_pattern(f"{condition_id}_*")
                
            return normalized_result

        except PolyApiException as exc:
            logger.error("Error placing order via ClobClient: %s", exc)
            if exc.status_code == 401 and self._refresh_api_credentials():
                try:
                    result = _post()
                    # Return result if it succeeds on retry
                    return result
                except Exception as retry_exc:
                    logger.error("Retry after credential refresh failed: %s", retry_exc)
        except Exception as exc:
            logger.error("Error placing order via ClobClient: %s", exc, exc_info=True)
        return None
    
    @retry_with_backoff(max_retries=2, initial_delay=2.0, backoff_factor=2.0)
    def place_batch_orders(self, orders: List[Dict]) -> List[Optional[Dict]]:
        """
        Place multiple orders in a single batch request (up to 15 orders per request).
        Includes automatic retry with exponential backoff for transient errors.
        
        Args:
            orders: List of order dicts, each with keys: condition_id, side, price, size, order_type (optional)
        
        Returns:
            List of order responses (same order as input), None for failed orders
        """
        if not self.clob_client:
            logger.error("Clob client is not configured; cannot place batch orders")
            return [None] * len(orders)
        
        if not orders:
            return []
        
        # Limit to 15 orders per batch (Polymarket API limit)
        MAX_BATCH_SIZE = 15
        if len(orders) > MAX_BATCH_SIZE:
            logger.warning("Batch order size (%d) exceeds max (%d), splitting into multiple batches", 
                          len(orders), MAX_BATCH_SIZE)
            results = []
            for i in range(0, len(orders), MAX_BATCH_SIZE):
                batch = orders[i:i + MAX_BATCH_SIZE]
                batch_results = self.place_batch_orders(batch)
                results.extend(batch_results)
            return results
        
        # Check if any orders need FOK/FAK (require direct HTTP API)
        has_fok_fak = False
        for order in orders:
            tif = order.get("time_in_force", "GTC").upper()
            if tif in ["FOK", "IOC", "FAK"]:
                has_fok_fak = True
                break
        
        # For all batch orders, use the robust _place_batch_orders_http method if available
        if PY_ORDER_UTILS_AVAILABLE and ORDER_BUILDER_HELPERS_AVAILABLE:
            logger.info("Using robust batch HTTP API for %d orders", len(orders))
            results = self._place_batch_orders_http(orders)
            
            # CRITICAL: For FOK batch orders, if only one side succeeded, we have an imbalance risk
            fok_orders = [i for i, o in enumerate(orders) if o.get("time_in_force", "GTC").upper() == "FOK"]
            if len(fok_orders) == 2:  # Two FOK orders (YES and NO)
                filled_count = sum(1 for i in fok_orders if results[i] and results[i].get("status") == "matched")
                if filled_count == 1:
                    logger.error(
                        "CRITICAL: Only one FOK order filled in batch! This creates imbalance. "
                        "Filled orders cannot be cancelled. Results: %s",
                        [(i, results[i].get("status") if results[i] else "FAILED") for i in fok_orders]
                    )
            
            return results
        
        # Fallback: place orders individually if batch submission is not available
        logger.warning("Batch submission not available, placing %d orders individually", len(orders))
        results = []
        for order in orders:
            res = self.place_limit_order(
                condition_id=order.get("condition_id"),
                side=order.get("side"),
                price=float(order.get("price", 0)),
                size=float(order.get("size", 0)),
                time_in_force=order.get("time_in_force", "GTC"),
                order_side=order.get("order_side", "BUY")
            )
            results.append(res)
        
        return results
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            if not self.clob_client:
                raise ValueError("Clob client not configured")
            
            # Apply rate limiting (1 cancel = 1 request)
            self.rate_limiter.wait_if_needed(1)
            
            self.clob_client.cancel(order_id)
            logger.info(f"Order cancelled: {order_id}")
            return True
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False
    
    def get_open_orders(self, status: str = "open", limit: int = 100) -> List[Dict]:
        """Get open orders for the authenticated wallet"""
        if not self.clob_client:
            logger.warning("Clob client is not configured; cannot fetch open orders")
            return []
        
        try:
            # Apply rate limiting (1 GET request)
            self.rate_limiter.wait_if_needed(1)
            
            orders = self.clob_client.get_orders()
            if not orders:
                return []
            if status:
                orders = [o for o in orders if o.get("status") == status]
            if limit:
                orders = orders[:limit]
            return orders
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            return []
    
    def get_positions(self) -> List[Dict]:
        """Get current positions from the CLOB API.
        
        Returns:
            List of position dicts, or None if the API call failed.
            Empty list [] means no positions (valid response).
            None means API error (should not reset tracker).
        """
        if not self.clob_client:
            logger.warning("Clob client not configured; cannot fetch positions")
            return None
        
        try:
            # Try using the official clob_client method if available
            # The py_clob_client might have get_positions or similar
            if hasattr(self.clob_client, 'get_positions'):
                positions = self.clob_client.get_positions()
                return positions if positions is not None else []
            
            # Fallback: Try the data-api for positions (Gamma API)
            # This is the public API that shows user positions
            gamma_url = "https://data-api.polymarket.com/positions"
            # Use funder_address (proxy) if available, otherwise wallet_address
            user_address = self.funder_address or self.wallet_address
            params = {"user": user_address} if user_address else {}
            
            response = requests.get(gamma_url, params=params, timeout=10)
            if response.status_code == 200:
                return response.json()
            
            logger.warning(f"Gamma positions API returned {response.status_code}")
            return None
            
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            return None  # Return None on error, not [] - to avoid resetting tracker

    def get_balance_allowance(self, asset_type: str = "COLLATERAL", token_id: str = None) -> Optional[Dict]:
        """
        Get balance and allowance for an asset.
        
        Args:
            asset_type: "COLLATERAL" (USDC) or "CONDITIONAL" (Outcome tokens)
            token_id: Required if asset_type is "CONDITIONAL"
            
        Returns:
            Dict with 'balance' and 'allowances' or None on failure
        """
        if not self.clob_client:
            return None
            
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            
            at = AssetType.COLLATERAL if asset_type.upper() == "COLLATERAL" else AssetType.CONDITIONAL

            # Use client's signature_type for proxy wallet support
            sig_type = self.signature_type if self.signature_type is not None else -1
            params = BalanceAllowanceParams(
                asset_type=at,
                token_id=token_id,
                signature_type=sig_type
            )

            return self.clob_client.get_balance_allowance(params)
        except Exception as e:
            logger.error(f"Error fetching balance/allowance: {e}")
            return None

    @retry_with_backoff(max_retries=3, initial_delay=1.0)
    def get_available_balance(self) -> float:
        """Return available USDC balance for the authenticated wallet.
        Includes automatic retry with exponential backoff for transient errors.
        Uses TTL cache to reduce API calls (5 second TTL).
        """
        # Try cache first
        cached = self.balance_cache.get("balance")
        if cached is not None:
            return cached
        
        # Cache miss - fetch from API
        if not self.clob_client:
            logger.warning("Clob client not configured; cannot fetch balances")
            return 0.0

        try:
            # Proactively ensure credentials are valid
            self.ensure_api_credentials()
            
            # Apply rate limiting
            self.rate_limiter.wait_if_needed(1)
            
            result = self.get_balance_allowance("COLLATERAL")
            logger.debug("Balance API response: %s", result)
            
            if result and isinstance(result, dict):
                # Prefer available_balance over balance
                # 'balance' = total balance (including locked in open orders)
                # 'available_balance' = what you can actually spend
                raw_balance = result.get("available_balance") or result.get("available") or result.get("balance")
                
                if raw_balance is not None:
                    # Convert from raw units (6 decimals) to float
                    balance = float(raw_balance) / 1_000_000.0
                    logger.debug("Fetched balance: %.2f USDC", balance)
                    
                    # Update cache
                    self.balance_cache.set("balance", balance)
                    return balance
                    
        except Exception as exc:
            logger.error(f"Error fetching wallet balance: {exc}")
            
        return 0.0
    
    def get_rate_limit_stats(self) -> Dict:
        """
        Get rate limiter statistics.
        
        Returns:
            Dict with rate limiting stats including:
            - total_requests: Total requests made
            - recent_requests_per_sec: Current requests per second
            - total_delays: Number of times rate limiting caused delays
            - total_delay_time: Total time spent waiting
            - sustained_limit: Sustained rate limit (requests/sec)
            - burst_limit: Burst rate limit
        """
        return self.rate_limiter.get_stats()
    
    def get_prices_history(
        self,
        condition_id: str,
        side: str = "YES",
        interval: str = None,
        start_ts: int = None,
        end_ts: int = None,
        fidelity: int = None
    ) -> Optional[List[Dict]]:
        """
        Fetch historical price data from the Polymarket /prices-history API.
        
        Args:
            condition_id: The market condition ID
            side: "YES" or "NO" - which outcome token to get prices for
            interval: Time interval string (mutually exclusive with start_ts/end_ts)
                     Options: "1m" (1 month), "1w" (1 week), "1d" (1 day), 
                              "6h" (6 hours), "1h" (1 hour), "max"
            start_ts: Start Unix timestamp (UTC) - use with end_ts
            end_ts: End Unix timestamp (UTC) - use with start_ts
            fidelity: Data resolution in minutes (e.g., 1, 5, 15, 60)
        
        Returns:
            List of price history entries: [{"t": timestamp, "p": price}, ...]
            or None if request fails
            
        Example:
            # Get last hour of data with 1-minute resolution
            history = client.get_prices_history(condition_id, "YES", interval="1h", fidelity=1)
            
            # Get specific time range with 15-minute resolution
            history = client.get_prices_history(condition_id, "NO", 
                                                start_ts=1697875200, end_ts=1697961600, 
                                                fidelity=15)
        """
        # Get token_id for the side
        token_id = self._get_token_id(condition_id, side.upper())
        if not token_id:
            logger.error("Cannot get prices-history: no token_id for %s (%s)", condition_id, side)
            return None
        
        # Build URL with query parameters
        url = f"{self.api_url}/prices-history"
        params = {"market": token_id}
        
        # Add optional parameters
        if interval:
            params["interval"] = interval
        elif start_ts and end_ts:
            params["startTs"] = start_ts
            params["endTs"] = end_ts
        
        if fidelity:
            params["fidelity"] = fidelity
        
        try:
            # Rate limit: prices-history has 100 req/10s limit
            self.rate_limiter.wait_if_needed(1)
            
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            data = response.json()
            history = data.get("history", [])
            
            logger.debug(
                "PRICES_HISTORY: Fetched %d data points for %s (%s) with params %s",
                len(history), condition_id, side, params
            )
            
            return history
            
        except requests.exceptions.RequestException as e:
            logger.error("Error fetching prices-history for %s (%s): %s", condition_id, side, e)
            return None
        except Exception as e:
            logger.error("Unexpected error in get_prices_history: %s", e, exc_info=True)
            return None
    
    def get_prices_history_df(
        self,
        condition_id: str,
        side: str = "YES",
        interval: str = None,
        start_ts: int = None,
        end_ts: int = None,
        fidelity: int = None
    ):
        """
        Fetch historical price data and return as a pandas DataFrame.
        
        Same parameters as get_prices_history(), but returns a DataFrame with:
        - 'timestamp': datetime index
        - 'price': price values
        
        Returns:
            pandas.DataFrame or None if request fails or pandas is not available.
        """
        try:
            import pandas as pd
        except ImportError:
            logger.warning("pandas not available for get_prices_history_df")
            return None
        
        history = self.get_prices_history(
            condition_id=condition_id,
            side=side,
            interval=interval,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=fidelity
        )
        
        if not history:
            return None
        
        # Convert to DataFrame
        df = pd.DataFrame(history)
        if df.empty:
            return None
        
        # Rename columns
        df = df.rename(columns={"t": "timestamp", "p": "price"})
        
        # Convert timestamp to datetime
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s", utc=True)
        df.set_index("timestamp", inplace=True)
        
        # Add metadata
        df.attrs["condition_id"] = condition_id
        df.attrs["side"] = side
        
        return df
    
    def subscribe_to_price_updates(self, condition_id: str, callback: Callable):
        """Subscribe to real-time price updates"""
        if condition_id not in self.price_callbacks:
            self.price_callbacks[condition_id] = []
        self.price_callbacks[condition_id].append(callback)
        
        mapping = self._get_token_mapping(condition_id)
        
        if not self.running:
            self._start_websocket()
        elif mapping:
            # If already running, send subscription message for new tokens
            asset_ids = [tid for tid in mapping.values() if tid]
            if asset_ids:
                self._send_subscription_message(asset_ids)
    
    def subscribe_to_orderbook_updates(self, condition_id: str, callback: Callable):
        """Subscribe to real-time order book updates"""
        if condition_id not in self.orderbook_callbacks:
            self.orderbook_callbacks[condition_id] = []
        self.orderbook_callbacks[condition_id].append(callback)
        
        mapping = self._get_token_mapping(condition_id)
        
        if not self.running:
            self._start_websocket()
        elif mapping:
            # If already running, send subscription message for new tokens
            asset_ids = [tid for tid in mapping.values() if tid]
            if asset_ids:
                self._send_subscription_message(asset_ids)
    
    def _start_websocket(self, is_reconnect: bool = False):
        """
        Start WebSocket connection for real-time updates with exponential backoff reconnection.
        
        Args:
            is_reconnect: True if this is a reconnection attempt (for logging)
        """
        # Precompute assets; if none, don't start WS to avoid NoneType sock errors
        assets_initial = self._get_subscribed_asset_ids()
        if not assets_initial:
            logger.warning("No assets to subscribe; skipping WebSocket startup")
            self.running = False
            return
        
        if is_reconnect:
            backoff = self._reconnect_manager.get_next_backoff()
            stats = self._reconnect_manager.get_stats()
            logger.info(
                "Attempting WebSocket reconnection (attempt #%d, consecutive failures: %d, backoff: %.1fs)",
                stats["connection_attempts"], stats["consecutive_failures"], backoff
            )
        
        self.running = True
        self.last_message_time = time.time()
        
        def handle_payload(payload):
            if not isinstance(payload, dict):
                return
            condition_id = payload.get("condition_id")
            asset_id = payload.get("asset_id")
            if not condition_id and asset_id:
                condition_id = self.asset_to_condition.get(asset_id)
                if condition_id:
                    payload["condition_id"] = condition_id

            if condition_id:
                outcome_side = self.asset_to_side.get(asset_id)
                if not outcome_side:
                    # Only log if we have an asset_id but no mapping
                    if asset_id:
                        logger.warning("WS: Missing side mapping for asset %s (cond: %s). Defaulting to YES.", 
                                     asset_id, condition_id)
                    outcome_side = "YES"
                if condition_id in self.price_callbacks:
                    for callback in self.price_callbacks[condition_id]:
                        # Pass condition_id, payload, and outcome_side
                        callback(condition_id, payload, outcome_side)
                if condition_id in self.orderbook_callbacks:
                    for callback in self.orderbook_callbacks[condition_id]:
                        # Pass condition_id, payload, and outcome_side
                        callback(condition_id, payload, outcome_side)

        def on_message(ws, message):
            self.last_message_time = time.time()
            if not message:
                return
            
            # Ensure message is a string
            if isinstance(message, bytes):
                try:
                    message = message.decode('utf-8')
                except Exception:
                    return

            # Strip whitespace and check if empty
            message = message.strip()
            if not message:
                return

            if message in ("PING", "PONG"):
                logger.debug("WebSocket heartbeat: %s", message)
                return
                
            try:
                data = json.loads(message)
                if isinstance(data, list):
                    for item in data:
                        handle_payload(item)
                else:
                    handle_payload(data)
            except json.JSONDecodeError:
                # Log as debug to avoid cluttering logs with expected noise
                logger.debug("WebSocket received non-JSON message: %s", message)
            except Exception as e:
                logger.error(f"Error processing WebSocket message: {e}")
        
        def on_error(ws, error):
            """Handle WebSocket errors with appropriate logging and reconnection logic."""
            error_str = str(error) if error else "Unknown error"
            
            # Categorize error types for better handling
            if "Connection refused" in error_str or "Connection reset" in error_str:
                error_type = "connection_error"
                logger.error("WebSocket connection error: %s", error_str)
            elif "timeout" in error_str.lower():
                error_type = "timeout"
                logger.warning("WebSocket timeout: %s", error_str)
            elif "SSL" in error_str or "certificate" in error_str.lower():
                error_type = "ssl_error"
                logger.error("WebSocket SSL error: %s", error_str)
            else:
                error_type = "unknown"
                logger.error("WebSocket error: %s", error_str)
            
            # Record failure for exponential backoff
            self._reconnect_manager.record_failure(error if isinstance(error, Exception) else Exception(error_str))
            
            # Don't close here - let on_close handle reconnection
        
        def on_close(ws, close_status_code, close_msg):
            """Handle WebSocket close events with exponential backoff reconnection."""
            close_reason = f"code={close_status_code}, msg={close_msg}" if close_status_code or close_msg else "unknown"
            logger.warning("WebSocket connection closed (%s)", close_reason)
            
            # Record failure if it wasn't a clean shutdown
            if close_status_code != 1000:  # 1000 = normal closure
                self._reconnect_manager.record_failure()
            
            # Only reconnect if still running and not manually stopped
            if self.running and self._should_reconnect:
                self._schedule_reconnect()
        
        def on_open(ws):
            """Handle WebSocket open events - record success and subscribe to assets."""
            # Record successful connection (resets exponential backoff)
            self._reconnect_manager.record_success()
            stats = self._reconnect_manager.get_stats()
            
            if stats["connection_attempts"] > 1:
                logger.info(
                    "✓ WebSocket reconnected successfully (attempt #%d, was down for %.1fs)",
                    stats["connection_attempts"],
                    time.time() - (stats["last_error_time"] or time.time())
                )
            else:
                logger.info("✓ WebSocket connection opened")
            
            assets = self._get_subscribed_asset_ids()
            if not assets:
                logger.warning("No assets to subscribe after open; closing socket")
                try:
                    ws.close()
                except Exception:
                    pass
                return
            
            subscribe_msg = {
                "assets_ids": assets,
                "type": "market"
            }
            try:
                ws.send(json.dumps(subscribe_msg))
                logger.debug("Subscribed to %d assets via WebSocket", len(assets))
            except Exception as e:
                logger.error("Failed to send subscription message: %s", e)
                self._reconnect_manager.record_failure(e)
                return
            
            threading.Thread(target=self._ping_loop, args=(ws,), daemon=True).start()
            threading.Thread(target=self._watchdog_loop, args=(ws,), daemon=True).start()
        
        def run_ws():
            try:
                self.ws = websocket.WebSocketApp(
                    f"{self.ws_url}/ws/market",
                    on_message=on_message,
                    on_error=on_error,
                    on_close=on_close,
                    on_open=on_open
                )
                # Aggressive keep-alive: Ping every 20s, timeout if no pong in 10s
                # Note: websocket-client requires ping_interval > ping_timeout
                self.ws.run_forever(ping_interval=20, ping_timeout=10)
            except Exception as e:
                logger.error("WebSocket thread exception: %s", e, exc_info=True)
                self._reconnect_manager.record_failure(e)
                if self.running and self._should_reconnect:
                    self._schedule_reconnect()
        
        self.ws_thread = threading.Thread(target=run_ws, daemon=True)
        self.ws_thread.start()
        self.running = True
        self._should_reconnect = True

    def _send_subscription_message(self, asset_ids: List[str]):
        """Send a subscription message for the given asset IDs."""
        if not self.ws or not self.running:
            return
            
        subscribe_msg = {
            "assets_ids": asset_ids,
            "type": "market"
        }
        try:
            self.ws.send(json.dumps(subscribe_msg))
            logger.info("Sent dynamic subscription for %d assets", len(asset_ids))
        except Exception as e:
            logger.error("Failed to send dynamic subscription message: %s", e)
    
    def _schedule_reconnect(self):
        """Schedule a reconnection attempt with exponential backoff."""
        if not self.running or not self._should_reconnect:
            return
        
        # Calculate backoff delay
        backoff = self._reconnect_manager.get_next_backoff()
        stats = self._reconnect_manager.get_stats()
        
        logger.info(
            "Scheduling WebSocket reconnection in %.1f seconds (attempt #%d, consecutive failures: %d)",
            backoff, stats["connection_attempts"] + 1, stats["consecutive_failures"]
        )
        
        def reconnect_after_delay():
            time.sleep(backoff)
            if self.running and self._should_reconnect:
                try:
                    self._start_websocket(is_reconnect=True)
                except Exception as e:
                    logger.error("Error during scheduled reconnection: %s", e, exc_info=True)
                    self._reconnect_manager.record_failure(e)
                    # Schedule another attempt
                    if self.running and self._should_reconnect:
                        self._schedule_reconnect()
        
        # Start reconnection thread
        if self._reconnect_thread and self._reconnect_thread.is_alive():
            # Previous reconnect thread still running - don't start another
            return
        
        self._reconnect_thread = threading.Thread(target=reconnect_after_delay, daemon=True)
        self._reconnect_thread.start()

    def _ping_loop(self, ws):
        """Send periodic heartbeats to keep connection alive."""
        logger.debug("WebSocket ping loop started")
        while self.running:
            try:
                if not ws or not hasattr(ws, 'sock') or not ws.sock:
                    break
                ws.send("PING")
            except Exception as exc:
                logger.debug("WebSocket ping failed: %s", exc)
                break
            
            # Responsive sleep: check self.running frequently
            for _ in range(20): # 10 seconds total (20 * 0.5s)
                if not self.running:
                    break
                time.sleep(0.5)

    def _watchdog_loop(self, ws):
        """Monitor WebSocket health and force reconnect if silent."""
        logger.debug("WebSocket watchdog started")
        while self.running:
            # Responsive sleep: check self.running frequently
            for _ in range(10): # 5 seconds total (10 * 0.5s)
                if not self.running:
                    break
                time.sleep(0.5)
                
            if not self.running:
                break
                
            # Check for silence
            silence_duration = time.time() - getattr(self, "last_message_time", 0)
            if silence_duration > 60:
                logger.warning("WebSocket watchdog: No messages for %.1fs. Forcing reconnect...", silence_duration)
                try:
                    ws.close() # This triggers on_close -> schedule_reconnect
                except Exception as e:
                    logger.error("Watchdog failed to close socket: %s", e)
                break
    
    def stop(self):
        """Stop WebSocket connection and prevent reconnection."""
        if not self.running:
            return
            
        logger.info("Stopping WebSocket connection...")
        self.running = False
        self._should_reconnect = False
        
        # Reset reconnection manager for clean shutdown
        self._reconnect_manager.reset()
        
        # Capture local reference and clear instance variable to avoid race conditions
        ws_to_close = self.ws
        self.ws = None
        
        if ws_to_close:
            # Close in a separate thread to avoid blocking the main shutdown sequence
            # websocket-client's close() can sometimes hang
            def force_close():
                try:
                    logger.debug("Closing WebSocket socket...")
                    # Set a short timeout for the close operation if possible
                    ws_to_close.close()
                    logger.debug("WebSocket socket closed successfully")
                except Exception as e:
                    logger.debug("Error closing WebSocket: %s", e)
            
            close_thread = threading.Thread(target=force_close, daemon=True)
            close_thread.start()
            # Give it a tiny bit of time to start/finish, but don't block
            close_thread.join(timeout=0.2)
        
        logger.info("WebSocket connection stop initiated")
    
    def get_websocket_stats(self) -> Dict:
        """
        Get WebSocket connection statistics.
        
        Returns:
            Dict with WebSocket stats including:
            - is_connected: Whether WebSocket is currently connected
            - is_running: Whether WebSocket manager is running
            - reconnect_stats: Reconnection manager statistics
        """
        is_connected = self.ws is not None and hasattr(self.ws, 'sock') and self.ws.sock
        return {
            "is_connected": bool(is_connected),
            "is_running": self.running,
            "should_reconnect": self._should_reconnect,
            "reconnect_stats": self._reconnect_manager.get_stats(),
            "subscribed_assets": len(self._get_subscribed_asset_ids()),
            "price_callbacks_count": sum(len(callbacks) for callbacks in self.price_callbacks.values()),
            "orderbook_callbacks_count": sum(len(callbacks) for callbacks in self.orderbook_callbacks.values())
        }

