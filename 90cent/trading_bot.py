"""
Main Polymarket Trading Bot
High-frequency trading bot for crypto prediction markets
"""
import time
import logging
import threading
from datetime import datetime
from typing import Dict, Optional, Tuple

import requests
import json

from polymarket_client import PolymarketClient
from order_manager import OrderManager
from orderbook_analyzer import OrderBookAnalyzer
from historical_data import HistoricalDataManager
from data_sources import DataAggregator
from order_flow_analyzer import OrderFlowAnalyzer
from volume_profile import VolumeProfileAnalyzer
from volatility_analyzer import VolatilityAnalyzer
from spread_optimizer import SpreadOptimizer
from cross_market_correlation import CrossMarketCorrelation
from time_patterns import TimePatternAnalyzer
from strategies.momentum_strategy import MomentumStrategy
from strategies.technical_indicators import TechnicalIndicatorsStrategy
from strategies.ai_predictor import AIPredictor
from position_tracker import PositionTracker
import config

# Configure logging
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(config.LOG_FILE, encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class TradingBot:
    """Main trading bot orchestrator"""
    
    def __init__(self):
        # Initialize API client first (needed for auto-discovery)
        # Create minimal outcome map initially
        initial_outcome_map: Dict[str, Dict[str, str]] = {}
        for market_cfg in config.MARKETS.values():
            condition_id = market_cfg.get("condition_id")
            if condition_id:
                initial_outcome_map[condition_id.lower()] = {
                    "YES": market_cfg.get("yes_outcome", "Yes"),
                    "NO": market_cfg.get("no_outcome", "No"),
                }
        
        self.client = PolymarketClient(
            api_key=config.POLYMARKET_API_KEY,
            private_key=config.POLYMARKET_PRIVATE_KEY,
            api_url=config.POLYMARKET_API_URL,
            api_secret=config.POLYMARKET_API_SECRET,
            api_passphrase=config.POLYMARKET_API_PASSPHRASE,
            wallet_address=config.POLYMARKET_WALLET_ADDRESS,
            chain_id=getattr(config, "POLYMARKET_CHAIN_ID", 137),
            signature_type=getattr(config, "POLYMARKET_SIGNATURE_TYPE", None),
            funder_address=getattr(config, "POLYMARKET_PROXY_ADDRESS", ""),
            outcome_map=initial_outcome_map,
            ws_url=getattr(config, "POLYMARKET_WS_URL", None),
        )
        
        # Resolve latest condition IDs if auto-discovery is enabled
        if getattr(config, "AUTO_DISCOVERY_ENABLED", False):
            self.market_configs = self._resolve_market_configs(config.MARKETS)
        else:
            self.market_configs = config.MARKETS

        # Update outcome labels per market (used to map YES/NO to actual tokens)
        outcome_map: Dict[str, Dict[str, str]] = {}
        for market_cfg in self.market_configs.values():
            condition_id = market_cfg.get("condition_id")
            if condition_id:
                outcome_map[condition_id.lower()] = {
                    "YES": market_cfg.get("yes_outcome", "Yes"),
                    "NO": market_cfg.get("no_outcome", "No"),
                }
        
        # Update client's outcome_map with resolved values
        self.client.outcome_map = outcome_map
        
        # Initialize order manager
        self.order_manager = OrderManager(
            client=self.client,
            config=config.ORDER_CONFIG,
            risk_config=config.RISK_CONFIG
        )
        
        # Initialize order book analyzer
        self.orderbook_analyzer = OrderBookAnalyzer()
        
        # Initialize micro-order flow analyzer
        self.order_flow_analyzer = OrderFlowAnalyzer()
        
        # Initialize volume profile analyzer
        self.volume_profile = VolumeProfileAnalyzer()
        
        # Initialize volatility analyzer
        self.volatility_analyzer = VolatilityAnalyzer()
        
        # Initialize spread optimizer
        self.spread_optimizer = SpreadOptimizer(config.ORDER_CONFIG)
        
        # Initialize cross-market correlation
        self.cross_market = CrossMarketCorrelation()
        
        # Initialize time pattern analyzer
        self.time_patterns = TimePatternAnalyzer()
        
        # Initialize historical data manager
        self.historical_data = HistoricalDataManager()
        
        # Initialize data aggregator (spot prices, etc.)
        self.data_aggregator = DataAggregator()
        self.data_aggregator.start_spot_price_updates(interval=10)  # Update every 10 seconds
        
        # Initialize strategies
        self.strategies = {}
        
        if config.STRATEGY_CONFIG["momentum"]["enabled"]:
            self.strategies["momentum"] = MomentumStrategy(
                config.STRATEGY_CONFIG["momentum"]
            )
        
        if config.STRATEGY_CONFIG["technical_indicators"]["enabled"]:
            self.strategies["technical"] = TechnicalIndicatorsStrategy(
                config.STRATEGY_CONFIG["technical_indicators"]
            )
        
        if config.STRATEGY_CONFIG["ai_prediction"]["enabled"]:
            self.strategies["ai"] = AIPredictor(
                config.STRATEGY_CONFIG["ai_prediction"]
            )
        
        # Market data storage
        self.market_data: Dict[str, Dict] = {}
        self.running = False
        
        # Price stability tracker for anti-fakeout logic
        # {condition_id: {"YES": timestamp, "NO": timestamp}}
        self.price_stability_tracker: Dict[str, Dict[str, float]] = {}
        
        # Position tracking and arbitrage detection
        self.position_tracker = PositionTracker()

        # Load initial positions from config if specified
        self._load_initial_positions()
        
        # Load optimal thresholds from historical data
        self._load_optimal_thresholds()
        self._balance_value: Optional[float] = None
        self._balance_timestamp = datetime.min
        # Markets to hold arb legs to resolution (skip flips/sells)
        self._arb_hold_until_resolution = set()
        # Track invalid markets (404 - market not found) to avoid repeated errors
        self._invalid_markets: set = set()
        
        # Legacy market configs for positions that are still open after market rotation
        self.legacy_market_configs: Dict[str, Dict] = {}
        
        # Track markets where we exited via pre-resolution logic to prevent buy-backs
        self._pre_resolution_exited_markets: set = set()
        
        # WebSocket-based arbitrage detection throttling
        
        # WebSocket-based arbitrage detection throttling
        # Track last time we triggered analyze_and_trade for each condition_id
        self._last_arbitrage_trigger: Dict[str, float] = {}
        arb_cfg = getattr(config, "ARB_CONFIG", {})
        self._arbitrage_trigger_cooldown = float(arb_cfg.get("websocket_arbitrage_cooldown", 2.0))
        
        logger.info("Trading bot initialized")
    
    def _load_initial_positions(self):
        """Load initial positions from config if specified.
        
        This allows manual override of positions when API sync doesn't work
        or when positions were created outside the bot.
        """
        initial_positions = getattr(config, "INITIAL_POSITIONS", {})
        if not initial_positions:
            logger.debug("No initial positions configured")
            return
        
        for condition_id, pos_data in initial_positions.items():
            if not pos_data:
                continue
            
            yes_shares = float(pos_data.get("YES", 0))
            no_shares = float(pos_data.get("NO", 0))
            yes_avg = float(pos_data.get("avg_price_yes", 0))
            no_avg = float(pos_data.get("avg_price_no", 0))
            
            if yes_shares > 0 or no_shares > 0:
                # Initialize the position in the tracker
                self.position_tracker.positions[condition_id] = {
                    "YES": yes_shares,
                    "NO": no_shares,
                    "avg_price_yes": yes_avg,
                    "avg_price_no": no_avg,
                    "last_update": datetime.now()
                }
                logger.info(
                    "INIT: Loaded initial position for %s: YES=%.4f @ %.4f, NO=%.4f @ %.4f",
                    condition_id[:10] + "...", yes_shares, yes_avg, no_shares, no_avg
                )

    def _maybe_confidence_flip(self, market_symbol: str, condition_id: str, orderbook: Dict, yes_confidence: float, no_confidence: float) -> bool:
        """Flip loser to winner when confidence gap is large; reinforce winner with capped size."""
        try:
            # Do not flip if we're intentionally holding equal-legs arbitrage to resolution
            if condition_id in self._arb_hold_until_resolution:
                return False
            flip_cfg = getattr(config, "FLIP_CONFIG", {})
            if not flip_cfg or not flip_cfg.get("enabled", True):
                return False
            min_conf_gap = float(flip_cfg.get("min_conf_gap", 0.2))
            max_reinforce_pct = float(flip_cfg.get("max_reinforce_pct", 0.05))

            # Determine winner/loser by confidence
            winner = None
            loser = None
            gap = 0.0
            if yes_confidence is not None and no_confidence is not None:
                if yes_confidence - no_confidence >= min_conf_gap:
                    winner, loser = "YES", "NO"
                    gap = yes_confidence - no_confidence
                elif no_confidence - yes_confidence >= min_conf_gap:
                    winner, loser = "NO", "YES"
                    gap = no_confidence - yes_confidence
            if not winner:
                return False

            # Positions
            pos = self.position_tracker.get_position(condition_id)
            yes_shares = float((pos.get("YES") or {}).get("shares", 0.0)) if pos else 0.0
            no_shares = float((pos.get("NO") or {}).get("shares", 0.0)) if pos else 0.0
            loser_shares = yes_shares if loser == "YES" else no_shares
            if loser_shares <= 0:
                return False

            bids = (orderbook or {}).get("bids", [])
            asks = (orderbook or {}).get("asks", [])
            best_bid = float(bids[0].get("price", 0.0)) if bids and isinstance(bids[0], dict) else 0.0
            best_ask = float(asks[0].get("price", 0.0)) if asks and isinstance(asks[0], dict) else 0.0
            if best_bid <= 0 or best_ask <= 0:
                return False

            def min_notional_shares(price: float) -> float:
                return (1.01 / price) if price > 0 else 0.0

            # Sell loser: do NOT enforce $1 notional minimum for sells as requested
            sell_shares = min(loser_shares, loser_shares)
            sell_order_id = self._place_sell_order(condition_id, loser, sell_shares, round(max(0.001, min(0.999, best_bid * 1.001)), 3))
            if not sell_order_id:
                return False
            self.position_tracker.reduce_position(condition_id, loser, sell_shares)

            # Reinforce winner with proceeds, capped by balance and per-side $5 cap
            proceeds_value = sell_shares * best_bid
            balance = self._get_available_balance()
            cap_value = max(0.0, balance * max_reinforce_pct)
            buy_value = max(1.01, min(proceeds_value, cap_value))
            buy_shares = max(min_notional_shares(best_ask), buy_value / best_ask)
            buy_price = round(max(0.001, min(0.999, best_ask * 1.001)), 3)
            buy_order = self.order_manager.place_limit_order(
                condition_id=condition_id,
                side=winner,
                price=buy_price,
                size=buy_shares,
                strategy="flip_reinforce"
            )
            if buy_order:
                order_status = buy_order.get("status", "open") if isinstance(buy_order, dict) else "open"
                if order_status == "matched":
                    self.position_tracker.update_position(condition_id, winner, buy_shares, buy_price)
                logger.info(
                    "Confidence flip executed for %s: %s -> %s (sold %.4f @ %.3f, bought %.4f @ %.3f, gap=%.2f, status=%s)",
                    market_symbol, loser, winner, sell_shares, best_bid, buy_shares, buy_price, gap, order_status
                )
                return True
        except Exception as exc:
            logger.error("Flip rule error: %s", exc)
        return False

    def _get_available_balance(self) -> float:
        ttl_seconds = getattr(config, "BALANCE_CACHE_TTL", 30)
        if (datetime.now() - self._balance_timestamp).total_seconds() > ttl_seconds:
            balance = self.client.get_available_balance()
            if balance is not None:
                self._balance_value = max(0.0, balance)
                self._balance_timestamp = datetime.now()

        if self._balance_value is not None:
            return self._balance_value

        # Fallback only if we've never fetched a balance
        return getattr(config, "DEFAULT_BALANCE_FALLBACK", 1000.0)

    def _resolve_market_configs(self, base_configs: Dict[str, Dict]) -> Dict[str, Dict]:
        """Resolve market configuration, optionally auto-discovering condition IDs using Gamma API."""
        resolved = {}
        catalog = None
        
        for market_name, cfg in base_configs.items():
            cfg_copy = dict(cfg)
            auto_cfg = cfg_copy.get("auto_discover")
            
            # For timeframe-based markets (15m, 1h Up/Down), ALWAYS resolve fresh IDs
            # These markets rotate every 15 minutes, so any configured ID is likely stale
            timeframes = cfg_copy.get("timeframes", [])
            is_timeframe_market = bool(timeframes) or auto_cfg is not None
            
            # Force discovery for timeframe markets, skip only if no auto_discover config
            needs_discovery = is_timeframe_market or not bool(cfg_copy.get("condition_id"))
            match = None

            if needs_discovery:
                # Strategy 1: Try slug-based resolution for current timeframe (15m/1h markets)
                # This is the fastest and most reliable for Up/Down markets
                if not timeframes:
                    timeframes = ["15m"]  # Default to 15m
                symbol_lower = market_name.lower()
                
                # Try each timeframe
                for timeframe in timeframes:
                    try:
                        # Get full market data (not just condition_id) for endDate
                        import time as _time
                        interval = 900 if timeframe == "15m" else 3600
                        ts = int(_time.time())
                        bucket = ((ts + interval) // interval) * interval - interval
                        slug = f"{symbol_lower}-updown-{timeframe}-{bucket}"
                        
                        market_data = self.client.get_market_by_slug(slug)
                        if market_data:
                            condition_id = market_data.get("conditionId") or market_data.get("condition_id")
                            if condition_id:
                                cfg_copy["condition_id"] = condition_id
                                # Store endDate for pre-resolution exit
                                end_date = market_data.get("endDate") or market_data.get("end_date")
                                if end_date:
                                    cfg_copy["end_date_iso"] = end_date
                                logger.info(
                                    "Resolved %s %s via slug: condition_id=%s, endDate=%s",
                                    market_name, timeframe, condition_id, end_date
                                )
                                match = market_data  # Use full market data
                                break
                    except Exception as e:
                        logger.debug("Slug pattern resolution failed for %s %s: %s", market_name, timeframe, e)


                
                # Strategy 2: If slug resolution failed, use Gamma API search
                if not match:
                    try:
                        # Get active markets for this symbol
                        markets = []
                        for timeframe in timeframes:
                            found = self.client.get_active_updown_markets(
                                symbol=symbol_lower,
                                timeframe=timeframe,
                                limit=10
                            )
                            markets.extend(found)
                        
                        if markets:
                            # Use most recent active market
                            match = markets[0]
                            cfg_copy["condition_id"] = match.get("condition_id") or match.get("conditionId")
                            logger.info(
                                "Resolved %s via Gamma API search: condition_id=%s (question: %s)",
                                market_name,
                                cfg_copy.get("condition_id"),
                                match.get("question", "unknown")
                            )
                    except Exception as e:
                        logger.debug("Gamma API search failed for %s: %s", market_name, e)
                
                # Strategy 3: Fallback to catalog search (CLOB API)
                if not match:
                    if catalog is None:
                        catalog = self._fetch_polymarket_markets()
                    match = self._find_market_match(catalog, auto_cfg or {}, market_name)
                    if match:
                        cfg_copy["condition_id"] = match.get("condition_id") or match.get("conditionId")
                        logger.info(
                            "Resolved %s via catalog search: condition_id=%s (question: %s)",
                            market_name,
                            cfg_copy.get("condition_id"),
                            match.get("question", "unknown"),
                        )
                
                # Set outcome labels if found
                if match:
                    tokens = match.get("tokens", [])
                    if not cfg_copy.get("yes_outcome"):
                        cfg_copy["yes_outcome"] = self._infer_outcome_label(tokens, "up") or "Up"
                    if not cfg_copy.get("no_outcome"):
                        cfg_copy["no_outcome"] = self._infer_outcome_label(tokens, "down") or "Down"
                    
                    # Store end time for pre-resolution exit feature
                    end_date = match.get("end_date_iso") or match.get("endDate") or match.get("end_date")
                    if end_date:
                        cfg_copy["end_date_iso"] = end_date
                
                # Log final status
                if match and cfg_copy.get("condition_id"):
                    logger.info(
                        "✓ Auto-discovery successful for %s: condition_id=%s, YES=%s, NO=%s",
                        market_name,
                        cfg_copy.get("condition_id"),
                        cfg_copy.get("yes_outcome"),
                        cfg_copy.get("no_outcome")
                    )
                elif cfg_copy.get("condition_id"):
                    logger.warning(
                        "Auto-discovery partial for %s; using configured condition_id %s",
                        market_name,
                        cfg_copy["condition_id"],
                    )
                else:
                    logger.warning(
                        "Auto-discovery failed for %s and no condition_id was provided",
                        market_name,
                    )
            else:
                logger.info(
                    "Using configured condition_id for %s: %s",
                    market_name,
                    cfg_copy.get("condition_id"),
                )

            resolved[market_name] = cfg_copy
        return resolved

    def _fetch_polymarket_markets(self) -> list:
        """Fetch markets from Gamma API (preferred) or CLOB API (fallback)."""
        # Try Gamma API first for better market discovery
        try:
            # Search for Up/Down markets with 15m and 1h timeframes
            markets = []
            
            # Search for 15m markets
            markets_15m = self.client.search_markets_gamma(tags=["up or down", "15m"], limit=200)
            markets.extend(markets_15m)
            
            # Search for 1h markets
            markets_1h = self.client.search_markets_gamma(tags=["up or down", "1h"], limit=200)
            markets.extend(markets_1h)
            
            # Remove duplicates based on condition_id
            seen_ids = set()
            unique_markets = []
            for market in markets:
                condition_id = market.get("condition_id") or market.get("conditionId")
                if condition_id and condition_id not in seen_ids:
                    seen_ids.add(condition_id)
                    unique_markets.append(market)
            
            if unique_markets:
                logger.info("Fetched %d unique markets from Gamma API", len(unique_markets))
                return unique_markets
        except Exception as exc:
            logger.warning("Failed to fetch markets from Gamma API, falling back to CLOB: %s", exc)
        
        # Fallback to CLOB API
        try:
            base_url = getattr(config, "POLYMARKET_API_URL", "https://clob.polymarket.com")
            resp = requests.get(f"{base_url}/markets", timeout=15)
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list):
                return data
            if isinstance(data, dict):
                return data.get("data") or data.get("markets") or data.get("results", [])
        except Exception as exc:
            logger.error("Failed to fetch Polymarket markets from CLOB: %s", exc)
        return []

    def _find_market_match(self, markets: list, criteria: Dict, market_name: str):
        """Find the most recent active market matching the criteria.
        
        Strategy:
        1) Strict: keywords_all + (keywords_any OR market_name) + phrases (if provided)
        2) Relaxed: (keywords_any OR market_name) without phrases
        3) Fallback: slug/tags contain market_name or any keyword
        Picks most recently updated/accepting market.
        """
        if not markets:
            return None

        keywords_any = [
            kw.lower()
            for kw in criteria.get("keywords_any", criteria.get("keywords", []))
        ]
        keywords_all = [kw.lower() for kw in criteria.get("keywords_all", [])]
        phrases = [ph.lower() for ph in criteria.get("phrases", [])]
        tags_required = [tag.lower() for tag in criteria.get("tags", [])]

        def is_active(_mkt: dict) -> bool:
            # Do NOT hard-filter by active/closed; some tradable markets may have varying flags.
            return True

        def matches_strict(mkt: dict) -> bool:
            text = get_text_fields(mkt)
            if keywords_all and not all(k in text for k in keywords_all):
                return False
            any_ok = True
            if keywords_any:
                any_ok = any(k in text for k in keywords_any)
            # allow market_name to serve as a keyword
            if not any_ok and market_name.lower() in text:
                any_ok = True
            if not any_ok:
                return False
            if phrases and not all(p in text for p in phrases):
                return False
            tags = [str(tag).lower() for tag in mkt.get("tags", [])]
            if tags_required and not all(tag in tags for tag in tags_required):
                return False
            return True

        def matches_relaxed(mkt: dict) -> bool:
            text = get_text_fields(mkt)
            if keywords_all and not all(k in text for k in keywords_all):
                return False
            if keywords_any and any(k in text for k in keywords_any):
                return True
            return market_name.lower() in text

        def matches_fallback(mkt: dict) -> bool:
            slug = str(mkt.get("slug") or "").lower()
            tags = [str(tag).lower() for tag in mkt.get("tags", [])]
            if market_name.lower() in slug:
                return True
            if keywords_any and any(k in slug for k in keywords_any):
                return True
            if keywords_any and any(k in tags for k in keywords_any):
                return True
            return False

        def get_condition_id(m: dict) -> str:
            return m.get("condition_id") or m.get("conditionId") or ""

        def get_text_fields(m: dict) -> str:
            parts = []
            for key in ("question", "title", "name", "subtitle"):
                val = m.get(key)
                if isinstance(val, str):
                    parts.append(val)
            # fall back to slug
            slug = m.get("slug")
            if isinstance(slug, str):
                parts.append(slug.replace("-", " "))
            return " ".join(parts).lower()

        def collect(filter_fn):
            out = []
            for m in markets:
                if not isinstance(m, dict):
                    continue
                if not get_condition_id(m):
                    continue
                if filter_fn(m):
                    out.append(m)
            return out

        candidates = collect(matches_strict)
        if not candidates:
            candidates = collect(matches_relaxed)
        if not candidates:
            candidates = collect(matches_fallback)
        if not candidates:
            # Final fallback: any market whose combined text contains any keyword or the market name
            def matches_any(mkt: dict) -> bool:
                text = get_text_fields(mkt)
                if market_name.lower() in text:
                    return True
                if keywords_any and any(k in text for k in keywords_any):
                    return True
                return False
            candidates = collect(matches_any)

        if not candidates:
            logger.warning("No matching market found for %s", market_name)
            return None

        candidates.sort(
            key=lambda m: self._parse_timestamp(
                m.get("accepting_order_timestamp")
                or m.get("updated_at")
                or m.get("end_date")
            ),
            reverse=True,
        )
        top = candidates[0]
        # Normalize expected fields for downstream (tokens/outcomes/etc.)
        if not top.get("condition_id") and top.get("conditionId"):
            top = dict(top)
            top["condition_id"] = top.get("conditionId")
        return top

    @staticmethod
    def _parse_timestamp(value: Optional[str]) -> float:
        if not value:
            return 0.0
        try:
            if value.endswith("Z"):
                value = value.replace("Z", "+00:00")
            return datetime.fromisoformat(value).timestamp()
        except Exception:
            return 0.0

    @staticmethod
    def _infer_outcome_label(tokens: list, preferred_keyword: str) -> str:
        preferred_keyword = preferred_keyword.lower()
        for token in tokens or []:
            outcome = str(token.get("outcome", ""))
            if preferred_keyword in outcome.lower():
                return outcome
        if tokens:
            return tokens[0].get("outcome", preferred_keyword.capitalize())
        return preferred_keyword.capitalize()
    
    def _load_optimal_thresholds(self):
        """Load optimal thresholds from historical data analysis"""
        try:
            for market in self.market_configs.keys():
                condition_id = self.market_configs[market].get("condition_id")
                if condition_id:
                    optimal = self.historical_data.get_optimal_thresholds(condition_id)
                    if optimal:
                        # Update strategy configs with optimal values
                        if "rsi_oversold" in optimal:
                            config.STRATEGY_CONFIG["technical_indicators"]["rsi_oversold"] = optimal["rsi_oversold"]
                        if "rsi_overbought" in optimal:
                            config.STRATEGY_CONFIG["technical_indicators"]["rsi_overbought"] = optimal["rsi_overbought"]
                        if "momentum_threshold" in optimal:
                            config.STRATEGY_CONFIG["momentum"]["momentum_threshold"] = optimal["momentum_threshold"]
                        logger.info(f"Loaded optimal thresholds for {market}: {optimal}")
        except Exception as e:
            logger.warning(f"Could not load optimal thresholds: {e}")
    
    def get_condition_id(self, market: str) -> Optional[str]:
        """Get condition ID for a market"""
        market_config = self.market_configs.get(market)
        if market_config:
            return market_config.get("condition_id")
        return None
    
    def update_market_data(self, condition_id: str, data: Dict, outcome_side: str = "YES"):
        """Update market data from WebSocket"""
        if condition_id not in self.market_data:
            self.market_data[condition_id] = {}
        
        # Extract price and volume
        price_raw = data.get("price", data.get("last_price"))
        volume_raw = data.get("volume", 0)
        try:
            price = float(price_raw) if price_raw is not None else None
        except (TypeError, ValueError):
            price = None
        try:
            volume = float(volume_raw) if volume_raw is not None else 0.0
        except (TypeError, ValueError):
            volume = 0.0
        side = data.get("side")  # "buy" or "sell" if available
        
        if price is not None:
            # Store side-specific price
            self.market_data[condition_id][f"{outcome_side.lower()}_price"] = price
            
            # Only update the main 'price' field if it's the YES side (standard behavior)
            if outcome_side.upper() == "YES":
                self.market_data[condition_id]["price"] = price
                
            self.market_data[condition_id]["volume"] = volume
            self.market_data[condition_id]["timestamp"] = datetime.now()
            self.market_data[condition_id]["last_outcome_side"] = outcome_side
            
            # Save to historical data
            self.historical_data.save_price_data(condition_id, price, volume)
            
            # Update volatility analyzer
            self.volatility_analyzer.update_price(condition_id, price)
            
            # Update volume profile
            self.volume_profile.update_trade(condition_id, price, volume, side)
            
            # Update cross-market correlation
            market = self._get_market_from_condition_id(condition_id)
            if market:
                self.cross_market.update_polymarket_price(market, price)
            
            # Update strategies
            for strategy in self.strategies.values():
                update_fn = getattr(strategy, "update_price", None)
                if callable(update_fn):
                    try:
                        update_fn(condition_id, price, volume)
                    except TypeError:
                        update_fn(condition_id, price)
            
            # Update peak price for safety exits if we have a position
            if self.position_tracker.has_position(condition_id):
                self.position_tracker.update_peak_price(condition_id, outcome_side, price)
    
    def update_orderbook_data(self, condition_id: str, data: Dict, outcome_side: str = "YES"):
        """Update order book data from WebSocket and trigger real-time arbitrage detection"""
        # Check if this is actual orderbook data (has bids/asks structure)
        # WebSocket may send price updates that don't have full orderbook structure
        has_orderbook = (
            (isinstance(data.get("bids"), (list, dict)) and len(data.get("bids", [])) > 0) or
            (isinstance(data.get("asks"), (list, dict)) and len(data.get("asks", [])) > 0)
        )
        
        # Only process if we have actual orderbook data structure
        if has_orderbook:
            # Update order book analyzer
            self.orderbook_analyzer.update_orderbook(condition_id, data)
            
            # Update micro-order flow analyzer
            self.order_flow_analyzer.update_orderbook(condition_id, data)
            
            # Save order book snapshot to historical data
            self.historical_data.save_orderbook_snapshot(condition_id, data)
            
            # REAL-TIME ARBITRAGE DETECTION: Check for arbitrage opportunities from WebSocket update
            # Wrap in try-except to ensure it doesn't break normal order flow
            try:
                self._check_websocket_arbitrage(condition_id, data)
                # Also check for Buy Once strategy
                self._check_websocket_price_update(condition_id, data, outcome_side)
            except Exception as e:
                # Log error but don't let it break order placement
                logger.debug("WS_ARB: Error in WebSocket arbitrage detection (non-critical): %s", e)
    
    def _get_market_from_condition_id(self, condition_id: str) -> Optional[str]:
        """Get market name from condition ID"""
        for market, market_config in self.market_configs.items():
            if market_config.get("condition_id") == condition_id:
                return market
        return None
    
    def _get_yes_no_prices(self, condition_id: str, orderbook: Dict, outcome_side: str = "YES") -> Tuple[Optional[float], Optional[float]]:
        """Extract YES and NO ASK prices for arbitrage detection."""
        yes_price = None
        no_price = None
        
        # Debug: Log what we have
        logger.debug("PURE ARB: %s - orderbook type: %s, keys: %s", 
                    condition_id, type(orderbook), list(orderbook.keys()) if orderbook else None)
        logger.debug("PURE ARB: %s - market_data has condition: %s", 
                    condition_id, condition_id in self.market_data)
        if condition_id in self.market_data:
            logger.debug("PURE ARB: %s - market_data price: %s", 
                        condition_id, self.market_data[condition_id].get("price"))
        
        # Method: Extract from orderbook (orderbook represents YES token)
        # In binary markets: YES_ask + NO_ask should be ~1.0 for arbitrage
        # NO_ask = 1 - YES_bid (since NO bid = 1 - YES ask)
        if orderbook:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            logger.debug("PURE ARB: %s - orderbook has %d bids, %d asks", 
                        condition_id, len(bids) if bids else 0, len(asks) if asks else 0)
            
            if bids and len(bids) > 0 and asks and len(asks) > 0:
                try:
                    # Extract best bid and ask for YES token
                    best_bid = None
                    best_ask = None
                    
                    bid_obj = bids[0]
                    ask_obj = asks[0]
                    
                    logger.debug("PURE ARB: %s - bid_obj type: %s, value: %s", 
                                condition_id, type(bid_obj), bid_obj)
                    logger.debug("PURE ARB: %s - ask_obj type: %s, value: %s", 
                                condition_id, type(ask_obj), ask_obj)
                    
                    if isinstance(bid_obj, dict):
                        best_bid = float(bid_obj.get("price", 0))
                    elif hasattr(bid_obj, "price"):
                        best_bid = float(bid_obj.price)
                    else:
                        best_bid = float(bid_obj)
                    
                    if isinstance(ask_obj, dict):
                        best_ask = float(ask_obj.get("price", 0))
                    elif hasattr(ask_obj, "price"):
                        best_ask = float(ask_obj.price)
                    else:
                        best_ask = float(ask_obj)
                    
                    logger.debug("PURE ARB: %s - extracted: best_bid=%.4f best_ask=%.4f", 
                                condition_id, best_bid, best_ask)
                    
                    if best_bid > 0 and best_ask > 0 and best_bid < 1 and best_ask < 1:
                        if outcome_side.upper() == "YES":
                            # YES ask = best ask from orderbook
                            yes_price = best_ask
                            # NO ask = 1 - YES bid
                            no_price = 1.0 - best_bid
                        else:
                            # NO ask = best ask from orderbook
                            no_price = best_ask
                            # YES ask = 1 - NO bid
                            yes_price = 1.0 - best_bid
                            
                        logger.info("PURE ARB: %s - using orderbook (%s): YES_ask=%.4f NO_ask=%.4f", 
                                   condition_id, outcome_side, yes_price, no_price)
                        return yes_price, no_price
                    else:
                        logger.warning("PURE ARB: %s - invalid prices from orderbook: bid=%.4f ask=%.4f", 
                                      condition_id, best_bid, best_ask)
                except Exception as e:
                    logger.warning("PURE ARB: %s - error extracting prices from orderbook: %s", condition_id, e, exc_info=True)
            else:
                logger.debug("PURE ARB: %s - orderbook missing bids or asks", condition_id)
        else:
            logger.debug("PURE ARB: %s - orderbook is None or empty", condition_id)
        
        # Fallback: Use market_data price (less accurate for arb, but better than nothing)
        if condition_id in self.market_data:
            current_price = self.market_data[condition_id].get("price")
            logger.debug("PURE ARB: %s - market_data price: %s", condition_id, current_price)
            if current_price and 0 < current_price < 1:
                # Approximate: use market price as YES, add small spread for asks
                yes_price = current_price * 1.001  # Add 0.1% for ask
                no_price = (1.0 - current_price) * 1.001  # Add 0.1% for ask
                logger.info("PURE ARB: %s - using market_data fallback: YES_ask≈%.4f NO_ask≈%.4f", 
                           condition_id, yes_price, no_price)
                return yes_price, no_price
            else:
                logger.debug("PURE ARB: %s - market_data price invalid: %s", condition_id, current_price)
        
        # Last resort: Try to fetch market price directly from public API (no auth needed)
        try:
            import requests
            # Try direct market endpoint first (faster)
            url = f"https://clob.polymarket.com/markets/{condition_id}"
            response = requests.get(url, timeout=10)
            if response.ok:
                market = response.json()
                if isinstance(market, dict):
                    # Try to extract price
                    price = None
                    # Check tokens array first (most reliable)
                    tokens = market.get("tokens", [])
                    for token in tokens:
                        outcome = str(token.get("outcome", "")).lower()
                        if outcome in ["yes", "up"]:
                            price = token.get("price") or token.get("lastPrice")
                            if price:
                                break
                    
                    # Fallback: try market-level price fields
                    if not price:
                        price = market.get("price") or market.get("lastPrice") or market.get("last_price")
                    
                    if price and 0 < float(price) < 1:
                        current_price = float(price)
                        yes_price = current_price * 1.001
                        no_price = (1.0 - current_price) * 1.001
                        logger.info("PURE ARB: %s - using public API fallback: YES_ask≈%.4f NO_ask≈%.4f", 
                                   condition_id, yes_price, no_price)
                        return yes_price, no_price
            
            # Fallback: search all markets (slower but more reliable)
            url = f"https://clob.polymarket.com/markets"
            response = requests.get(url, timeout=15)
            if response.ok:
                data = response.json()
                markets = []
                if isinstance(data, list):
                    markets = data
                elif isinstance(data, dict):
                    markets = data.get("data") or data.get("markets") or data.get("results") or []
                
                # Find our market
                cond_lower = condition_id.lower()
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    cid = (market.get("condition_id") or market.get("conditionId") or "").lower()
                    if cid == cond_lower:
                        # Try to extract price
                        price = None
                        tokens = market.get("tokens", [])
                        for token in tokens:
                            outcome = str(token.get("outcome", "")).lower()
                            if outcome in ["yes", "up"]:
                                price = token.get("price") or token.get("lastPrice")
                                if price:
                                    break
                        
                        if not price:
                            price = market.get("price") or market.get("lastPrice") or market.get("last_price")
                        
                        if price and 0 < float(price) < 1:
                            current_price = float(price)
                            yes_price = current_price * 1.001
                            no_price = (1.0 - current_price) * 1.001
                            logger.info("PURE ARB: %s - using public API search fallback: YES_ask≈%.4f NO_ask≈%.4f", 
                                       condition_id, yes_price, no_price)
                            return yes_price, no_price
        except Exception as e:
            logger.debug("PURE ARB: %s - public API fallback failed: %s", condition_id, e)
        
        # Also try the client's get_market_price method
        try:
            market_info = self.client.get_market_price(condition_id)
            if market_info:
                # Try to extract price from market info
                price = None
                if isinstance(market_info, dict):
                    price = market_info.get("price") or market_info.get("lastPrice") or market_info.get("last_price")
                    if not price and "outcomes" in market_info:
                        # Try to get from outcomes
                        outcomes = market_info.get("outcomes", [])
                        for outcome in outcomes:
                            if outcome.get("outcome") == "Yes" or outcome.get("outcome") == "Up":
                                price = outcome.get("price") or outcome.get("lastPrice")
                                break
                    if not price and "tokens" in market_info:
                        # Try tokens array
                        tokens = market_info.get("tokens", [])
                        for token in tokens:
                            outcome = str(token.get("outcome", "")).lower()
                            if outcome in ["yes", "up"]:
                                price = token.get("price") or token.get("lastPrice")
                                if price:
                                    break
                
                if price and 0 < float(price) < 1:
                    current_price = float(price)
                    yes_price = current_price * 1.001
                    no_price = (1.0 - current_price) * 1.001
                    logger.info("PURE ARB: %s - using client API fallback: YES_ask≈%.4f NO_ask≈%.4f", 
                               condition_id, yes_price, no_price)
                    return yes_price, no_price
        except Exception as e:
            logger.debug("PURE ARB: %s - client API fallback failed: %s", condition_id, e)
        
        # Log detailed info about why we failed
        orderbook_info = "None"
        if orderbook:
            bids = orderbook.get("bids", [])
            asks = orderbook.get("asks", [])
            orderbook_info = f"bids={len(bids) if bids else 0}, asks={len(asks) if asks else 0}"
        
        market_data_info = "missing"
        if condition_id in self.market_data:
            price = self.market_data[condition_id].get("price")
            market_data_info = f"price={price}"
        
        logger.warning(
            "PURE ARB: %s - could not determine YES/NO prices (orderbook: %s, market_data: %s)", 
            condition_id, orderbook_info, market_data_info
        )
        return None, None
    
    def _check_websocket_price_update(self, condition_id: str, orderbook_data: Dict, outcome_side: str = "YES"):
        """
        Check for price updates from WebSocket and trigger analyze_and_trade() 
        if the price is within the target range.
        """
        try:
            # Skip invalid markets
            if condition_id in self._invalid_markets:
                return
            
            # Get market name
            market = self._get_market_from_condition_id(condition_id)
            if not market:
                return
            
            # Extract YES and NO prices
            yes_price, no_price = self._get_yes_no_prices(condition_id, orderbook_data, outcome_side)
            
            if yes_price is None or no_price is None:
                return

            # Check if either price is in the target range
            buy_once_cfg = getattr(config, "BUY_ONCE_CONFIG", {"enabled": True, "min_price": 0.97, "max_price": 0.99})
            if not buy_once_cfg.get("enabled", True):
                return

            min_p = buy_once_cfg.get("min_price", 0.97)
            max_p = buy_once_cfg.get("max_price", 0.99)

            if (min_p <= yes_price <= max_p) or (min_p <= no_price <= max_p):
                logger.info("WS_PRICE: %s - Price in range (YES=%.4f, NO=%.4f). Triggering trade check.", 
                            condition_id, yes_price, no_price)
                
                # Trigger analyze_and_trade() in a separate thread
                def trigger_trade():
                    try:
                        time.sleep(0.1)
                        self.analyze_and_trade(condition_id, market, orderbook=orderbook_data, outcome_side=outcome_side)
                    except Exception as e:
                        logger.error("WS_PRICE: %s - Error in analyze_and_trade: %s", condition_id, e)
                
                threading.Thread(target=trigger_trade, daemon=True).start()
                
        except Exception as e:
            logger.error("WS_PRICE: %s - Error: %s", condition_id, e)
    
    def _get_real_polymarket_positions(self, condition_id: str) -> tuple:
        """
        EMERGENCY FIX: Query REAL positions directly from Polymarket API.
        Returns (yes_shares, no_shares) - the ACTUAL positions on Polymarket.
        
        This bypasses the broken internal tracker and gets ground truth from the exchange.
        """
        try:
            # Get token IDs for this market
            token_mapping = self.client._get_token_mapping(condition_id)
            yes_token = token_mapping.get("YES", "")
            no_token = token_mapping.get("NO", "")
            
            if not yes_token and not no_token:
                logger.warning("REAL_POS: %s - Could not get token mapping", condition_id)
                return 0.0, 0.0
            
            # Query real positions from Polymarket
            api_positions = self.client.get_positions()
            
            if api_positions is None:
                logger.warning("REAL_POS: %s - API returned None", condition_id)
                return 0.0, 0.0
            
            # DEBUG: Log what the API actually returns (first 3 positions)
            if api_positions:
                logger.info("REAL_POS: %s - API returned %d positions. Sample: %s", 
                           condition_id, len(api_positions), 
                           str(api_positions[:3])[:500] if len(api_positions) > 0 else "[]")
            else:
                logger.info("REAL_POS: %s - API returned empty list", condition_id)
            
            yes_shares = 0.0
            no_shares = 0.0
            
            # Map outcome names to YES/NO
            outcome_map = {"Yes": "YES", "No": "NO", "Up": "YES", "Down": "NO"}
            for market_key, market_cfg in self.market_configs.items():
                if market_cfg.get("condition_id", "").lower() == condition_id.lower():
                    yes_outcome = market_cfg.get("yes_outcome", "Yes")
                    no_outcome = market_cfg.get("no_outcome", "No")
                    outcome_map[yes_outcome] = "YES"
                    outcome_map[no_outcome] = "NO"
            
            # Search through positions to find matching ones
            for pos in api_positions:
                if not isinstance(pos, dict):
                    continue
                
                # Match by condition_id OR by token_id (asset)
                pos_cond = (pos.get("condition_id") or pos.get("conditionId") or "").lower()
                pos_asset = (pos.get("asset") or pos.get("asset_id") or pos.get("token_id") or pos.get("tokenId") or "").lower()
                
                # Check if matches by condition_id OR by token_id
                matches_condition = pos_cond == condition_id.lower()
                matches_token = pos_asset == yes_token.lower() or pos_asset == no_token.lower()
                
                if not matches_condition and not matches_token:
                    continue
                
                # Get outcome and size
                outcome = pos.get("outcome") or pos.get("side") or ""
                size = float(pos.get("size") or pos.get("shares") or pos.get("amount") or 0)
                
                # Try to determine YES/NO by multiple methods:
                # 1. By outcome name (Up/Down, Yes/No)
                # 2. By token_id matching
                mapped = outcome_map.get(outcome, "")
                if not mapped and outcome:
                    mapped = outcome.upper() if outcome.upper() in ["YES", "NO"] else ""
                
                # If outcome mapping didn't work, try token matching
                if not mapped and pos_asset:
                    if pos_asset == yes_token.lower():
                        mapped = "YES"
                    elif pos_asset == no_token.lower():
                        mapped = "NO"
                
                if mapped == "YES":
                    yes_shares += size
                    logger.debug("REAL_POS: Found YES position: %.2f shares (outcome=%s, asset=%s)", 
                                size, outcome, pos_asset[:20] if pos_asset else "N/A")
                elif mapped == "NO":
                    no_shares += size
                    logger.debug("REAL_POS: Found NO position: %.2f shares (outcome=%s, asset=%s)", 
                                size, outcome, pos_asset[:20] if pos_asset else "N/A")
            
            logger.info("REAL_POS: %s - Fetched REAL positions: YES=%.2f, NO=%.2f", 
                       condition_id, yes_shares, no_shares)
            return yes_shares, no_shares
            
        except Exception as e:
            logger.error("REAL_POS: %s - Error fetching real positions: %s", condition_id, e)
            return 0.0, 0.0
    
    def _sync_positions_from_api(self, condition_id: str, force: bool = False) -> bool:
        """
        Sync positions from Polymarket API to ensure we have accurate position data.
        Returns True if sync was successful, False otherwise.
        
        NOTE: If API sync fails, we keep using local tracker data.
        The local tracker is updated when orders are placed.
        """
        try:
            # Check if we should sync (avoid excessive API calls)
            if not force and not self.position_tracker.should_sync(condition_id):
                return True  # Already synced recently
            
            # Fetch positions from API
            api_positions = self.client.get_positions()
            
            if api_positions is None:
                # API call failed - keep using local tracker data, don't reset
                logger.warning("SYNC: %s - API returned None, keeping local tracker data", condition_id)
                return False
            
            # Get outcome mapping for this market
            outcome_map = {"Yes": "YES", "No": "NO", "Up": "YES", "Down": "NO"}
            
            # Check market config for custom outcome names
            for market_key, market_cfg in self.market_configs.items():
                if market_cfg.get("condition_id", "").lower() == condition_id.lower():
                    yes_outcome = market_cfg.get("yes_outcome", "Yes")
                    no_outcome = market_cfg.get("no_outcome", "No")
                    outcome_map[yes_outcome] = "YES"
                    outcome_map[no_outcome] = "NO"
                    break
            
            # Sync to position tracker
            self.position_tracker.sync_from_api(condition_id, api_positions, outcome_map)
            
            return True
            
        except Exception as e:
            logger.error("SYNC: %s - Error syncing positions: %s", condition_id, e, exc_info=True)
            return False
    
    def _sync_filled_orders(self, condition_id: str) -> None:
        """
        Check for filled orders and update position tracker.
        This is the reliable way to track positions - based on actual fills.
        
        Checks both "matched" (filled) and "open" orders to catch any that filled
        after being placed but before we checked their status.
        """
        try:
            # Get all orders (both open and matched) for this condition
            # We need to check matched orders to find ones that filled since last check
            # Get both open and matched orders separately, then combine
            open_orders = self.client.get_open_orders(status="open", limit=100)
            matched_orders = self.client.get_open_orders(status="matched", limit=100)
            all_orders = (open_orders or []) + (matched_orders or [])
            if not all_orders:
                return
            if not all_orders:
                return
            
            # Get market config for outcome mapping
            outcome_map = {}
            for market_key, market_cfg in self.market_configs.items():
                if market_cfg.get("condition_id", "").lower() == condition_id.lower():
                    outcome_map[market_cfg.get("yes_outcome", "Yes")] = "YES"
                    outcome_map[market_cfg.get("no_outcome", "No")] = "NO"
                    break
            
            # Track which orders we've already processed (by order_id) to avoid double-counting
            processed_order_ids = set()
            
            for order in all_orders:
                order_condition = str(order.get("condition_id", "") or order.get("asset_id", "")).lower()
                if order_condition != condition_id.lower():
                    continue
                
                order_id = order.get("id") or order.get("order_id")
                order_status = order.get("status", "").lower()
                
                # Skip if we've already processed this order
                if order_id and order_id in processed_order_ids:
                    continue
                
                # Only process "matched" (filled) orders that we haven't tracked yet
                if order_status != "matched":
                    continue
                
                # Extract order details
                side = str(order.get("side", "")).upper()
                outcome = str(order.get("outcome", ""))
                
                # Normalize side
                if outcome in outcome_map:
                    side = outcome_map[outcome]
                elif side not in ["YES", "NO"]:
                    continue
                
                # Get filled size - use size_matched if available, otherwise use full size for matched orders
                filled_size = float(order.get("size_matched", 0) or order.get("filled_size", 0) or 0)
                if filled_size == 0:
                    # For matched orders, if size_matched is 0, use the order size
                    filled_size = float(order.get("size", 0) or 0)
                
                price = float(order.get("price", 0) or order.get("average_price", 0) or 0)
                
                if filled_size > 0 and price > 0:
                    # Check if this order is already in our order manager's tracked orders
                    # If it's a new fill, update position tracker
                    if order_id and order_id not in self.order_manager.open_orders:
                        # This is a newly filled order we haven't tracked - update position
                        self.position_tracker.update_position(condition_id, side, filled_size, price)
                        logger.info("SYNC: %s - Newly filled order detected: %s %s %.4f @ %.4f", 
                                   condition_id[:10], order_id[:10] if order_id else "unknown", side, filled_size, price)
                    elif order_id:
                        # Order is tracked but status might have changed - update if needed
                        tracked_order = self.order_manager.open_orders.get(order_id)
                        if tracked_order and tracked_order.get("status") != "matched":
                            # Status changed from open to matched - update position
                            self.position_tracker.update_position(condition_id, side, filled_size, price)
                            tracked_order["status"] = "matched"
                            logger.info("SYNC: %s - Order status changed to matched: %s %s %.4f @ %.4f", 
                                       condition_id[:10], order_id[:10], side, filled_size, price)
                    
                    if order_id:
                        processed_order_ids.add(order_id)

        except Exception as e:
            logger.debug("SYNC: %s - Error checking filled orders: %s", condition_id, e)
    
    def _cancel_stale_orders(self, condition_id: str, yes_price: float, no_price: float) -> int:
        """
        Cancel open orders that are too far from current market price.
        These orders will never fill and waste order slots.
        
        Returns number of cancelled orders.
        """
        # Check if feature is enabled
        if not config.ORDER_CONFIG.get("cancel_stale_orders", True):
            return 0
        
        stale_threshold = config.ORDER_CONFIG.get("stale_order_threshold", 0.10)  # 10% away from market = stale
        cancelled_count = 0
        
        try:
            open_orders = self.client.get_open_orders(status="open", limit=50)
            if not open_orders:
                return 0
            
            for order in open_orders:
                try:
                    order_condition = order.get("asset_id", "") or order.get("condition_id", "")
                    
                    # Skip orders for other markets
                    if order_condition.lower() != condition_id.lower():
                        # Also check if this is a token ID for this market
                        if order_condition not in self.client.token_cache.get(condition_id, {}).values():
                            continue
                    
                    order_id = order.get("id") or order.get("order_id")
                    order_price = float(order.get("price", 0))
                    order_side = order.get("side", "").upper()
                    
                    # Determine which market price to compare against
                    if order_side == "BUY":
                        # For BUY orders, check against ask price
                        # Need to determine if this is YES or NO token
                        outcome = order.get("outcome", "")
                        if "YES" in outcome.upper() or "UP" in outcome.upper():
                            market_price = yes_price
                        elif "NO" in outcome.upper() or "DOWN" in outcome.upper():
                            market_price = no_price
                        else:
                            # Try to determine from asset_id
                            market_price = yes_price  # Default to YES
                    else:
                        continue  # Skip SELL orders for now
                    
                    if market_price <= 0:
                        continue
                    
                    # Calculate how far order is from market
                    price_diff = abs(order_price - market_price) / market_price
                    
                    if price_diff > stale_threshold:
                        logger.info("STALE ORDER: Cancelling %s order @ %.3f (market=%.3f, diff=%.1f%%)",
                                   order_side, order_price, market_price, price_diff * 100)
                        
                        if self.order_manager.cancel_order(order_id):
                            cancelled_count += 1
                            logger.info("STALE ORDER: Successfully cancelled order %s", order_id)
                        else:
                            logger.warning("STALE ORDER: Failed to cancel order %s", order_id)
                            
                except Exception as e:
                    logger.debug("STALE ORDER: Error processing order: %s", e)
                    continue
            
            if cancelled_count > 0:
                logger.info("STALE ORDER: Cancelled %d stale orders for %s", cancelled_count, condition_id[:10])
                
        except Exception as e:
            logger.error("STALE ORDER: Error checking stale orders: %s", e)
        
        return cancelled_count
    
    def _place_sell_order(self, condition_id: str, side: str, shares: float, price: float) -> Optional[str]:
        """Place a sell order (reduce position)"""
        try:
            # For selling, we need to use SELL side in the API
            # But Polymarket uses BUY for both - selling means buying the opposite outcome
            opposite_side = "NO" if side == "YES" else "YES"
            
            order = self.client.place_limit_order(
                condition_id=condition_id,
                side=opposite_side,  # Buy opposite to close position
                price=price,
                size=shares
            )
            
            if order and "id" in order:
                logger.info(f"Sell order placed: {side} {shares} shares @ {price} (via {opposite_side} buy)")
                return order["id"]
        except Exception as e:
            logger.error(f"Error placing sell order: {e}")
        return None
    
    def fetch_gamma_prices(self, condition_id: str) -> Optional[Dict[str, float]]:
        """
        Fetch prices from Gamma API as a fallback when CLOB is empty/blocked.
        Uses a 2-step lookup:
        1. Get market_slug from CLOB API (which works even if orderbook is empty)
        2. Get prices from Gamma API using the slug
        
        Returns a dict with {'yes_ask': float, 'no_ask': float} or None.
        """
        try:
            # Step 1: Get Slug from CLOB API
            # This endpoint returns market metadata including the slug
            clob_url = f"https://clob.polymarket.com/markets/{condition_id}"
            r_clob = requests.get(clob_url, timeout=5)
            
            if r_clob.status_code != 200:
                logger.warning("GAMMA FALLBACK: CLOB API failed to get slug: %s", r_clob.status_code)
                return None
                
            clob_data = r_clob.json()
            market_slug = clob_data.get("market_slug")
            
            if not market_slug:
                logger.warning("GAMMA FALLBACK: No slug found in CLOB response")
                return None
                
            # Step 2: Get Prices from Gamma API using Slug
            gamma_url = "https://gamma-api.polymarket.com/events"
            params = {"slug": market_slug}
            
            r_gamma = requests.get(gamma_url, params=params, timeout=5)
            if r_gamma.status_code != 200:
                logger.warning("GAMMA FALLBACK: Gamma API failed: %s", r_gamma.status_code)
                return None
                
            events = r_gamma.json()
            if not events:
                return None
                
            # Find the specific market in the event
            found_market = None
            for e in events:
                for m in e.get("markets", []):
                    if m.get("conditionId") == condition_id:
                        found_market = m
                        break
                if found_market: break
            
            if not found_market:
                logger.warning("GAMMA FALLBACK: Market not found in Gamma event")
                return None
            
            # Extract prices
            best_ask = found_market.get("bestAsk")
            best_bid = found_market.get("bestBid")
            
            if best_ask is None:
                return None
                
            yes_ask = float(best_ask)
            
            # Derive NO ask
            # If we have a bid, NO ask is roughly 1 - bid
            # Otherwise estimate from spread
            if best_bid is not None:
                no_ask = 1.0 - float(best_bid)
            else:
                no_ask = 1.0 - (yes_ask - 0.01) # Estimate spread
                
            return {
                "yes_ask": yes_ask,
                "no_ask": no_ask,
                "source": "gamma"
            }
            
        except Exception as e:
            logger.error("GAMMA FALLBACK: Error fetching prices: %s", e)
            return None


    def analyze_and_trade(self, condition_id: str, market_name: str = None, orderbook: Dict = None, outcome_side: str = "YES"):
        """
        Analyze market and execute 'Buy Once' strategy.
        """
        try:
            # 1. Check if strategy is enabled
            buy_once_cfg = getattr(config, "BUY_ONCE_CONFIG", {"enabled": True, "min_price": 0.97, "max_price": 0.99, "pre_check_price": 0.95})
            if not buy_once_cfg.get("enabled", True):
                return

            # 2. Check if we already have a position in this market
            if self.position_tracker.has_position(condition_id):
                logger.debug("TRADE: %s - Already have a position. Skipping 'Buy Once'.", condition_id)
                return

            # 3. Check if we exited this market via pre-resolution logic (prevent buy-back loop)
            if condition_id in self._pre_resolution_exited_markets:
                logger.debug("TRADE: %s - Market in pre-resolution exit list. Skipping buy-back.", condition_id)
                return

            # 3.5 Time Window Check: Only buy if close to resolution (e.g. < 5 mins)
            # This prevents "catching a falling knife" by ensuring we only trade when price convergence is imminent.
            max_time_before_res = buy_once_cfg.get("max_time_before_resolution", 0)
            if max_time_before_res > 0 and market_name:
                market_cfg = self.market_configs.get(market_name)
                if market_cfg:
                    end_date_str = market_cfg.get("end_date_iso")
                    if end_date_str:
                        try:
                            from datetime import datetime, timezone
                            end_date_str = end_date_str.replace("Z", "+00:00")
                            end_time = datetime.fromisoformat(end_date_str)
                            now = datetime.now(timezone.utc)
                            seconds_to_res = (end_time - now).total_seconds()
                            
                            if seconds_to_res > max_time_before_res:
                                logger.debug("TRADE: %s - Too early to buy. %.0fs > %.0fs limit.", 
                                            condition_id, seconds_to_res, max_time_before_res)
                                return
                            
                            if seconds_to_res < 0:
                                logger.debug("TRADE: %s - Market already resolved. Skipping.", condition_id)
                                return
                                
                        except Exception as e:
                            logger.warning("TRADE: %s - Error checking time window: %s", condition_id, e)

            # 3. Pre-check price
            pre_check_p = buy_once_cfg.get("pre_check_price", 0.95)
            
            yes_p = None
            no_p = None
            
            # Use WebSocket data for pre-check if available (TRUST WS)
            if orderbook:
                try:
                    asks = orderbook.get("asks", [])
                    if asks:
                        first_ask = asks[0]
                        price_val = float(first_ask.get("price", 0)) if isinstance(first_ask, dict) else float(first_ask)
                        if outcome_side == "YES":
                            yes_p = price_val
                        elif outcome_side == "NO":
                            no_p = price_val
                except:
                    pass
            
            # Fallback to CLOB for pre-check ONLY if WS data missing for BOTH sides
            # This fulfills the "trust WebSocket" requirement
            if yes_p is None and no_p is None:
                yes_p = self.client.get_market_price_clob(condition_id, "YES")
                no_p = self.client.get_market_price_clob(condition_id, "NO")
            
            if yes_p is None and no_p is None:
                logger.debug("TRADE: %s - Could not fetch lightweight prices. Falling back to orderbook.", condition_id)
            else:
                # If both prices are below pre-check, exit early
                # We only need one side to be above pre-check to continue
                if (yes_p is None or yes_p < pre_check_p) and (no_p is None or no_p < pre_check_p):
                    logger.debug("TRADE: %s - Prices below pre-check (%.2f). YES=%.4f, NO=%.4f", 
                                 condition_id, pre_check_p, yes_p or 0, no_p or 0)
                    return

            # 4. Get BOTH orderbooks (YES and NO) for precise execution
            # OPTIMIZATION: For small orders (<$10), skip CLOB fetch and use WebSocket data if available
            yes_orderbook = None
            no_orderbook = None
            
            # Estimate trade value
            estimated_price = yes_p if yes_p else (no_p if no_p else 0.5)
            estimated_value = buy_once_cfg.get("order_size", 10.0) * estimated_price
            
            skip_clob = False
            if estimated_value < 10.0 and orderbook:
                logger.info("TRADE: %s - Small order (~$%.2f), skipping CLOB fetch to reduce latency.", condition_id, estimated_value)
                skip_clob = True
                if outcome_side == "YES":
                    yes_orderbook = orderbook
                elif outcome_side == "NO":
                    no_orderbook = orderbook
            
            # Fetch if not skipped or missing
            if not skip_clob or not yes_orderbook:
                yes_orderbook = self.client.get_orderbook(condition_id, side="YES")
            if not skip_clob or not no_orderbook:
                no_orderbook = self.client.get_orderbook(condition_id, side="NO")
            
            # Extract BEST (lowest) ask from each orderbook
            # The asks array might NOT be sorted lowest-first, so we find the minimum
            yes_price = None
            no_price = None
            
            if yes_orderbook:
                asks = yes_orderbook.get("asks", [])
                if asks:
                    try:
                        # Find the minimum ask price (best price for buyers)
                        ask_prices = [float(a.get("price", 999)) if isinstance(a, dict) else float(a) for a in asks]
                        yes_price = min(ask_prices) if ask_prices else None
                    except (ValueError, TypeError) as e:
                        logger.warning("TRADE: Error parsing YES asks: %s", e)
            
            if no_orderbook:
                asks = no_orderbook.get("asks", [])
                if asks:
                    try:
                        # Find the minimum ask price (best price for buyers)
                        ask_prices = [float(a.get("price", 999)) if isinstance(a, dict) else float(a) for a in asks]
                        no_price = min(ask_prices) if ask_prices else None
                    except (ValueError, TypeError) as e:
                        logger.warning("TRADE: Error parsing NO asks: %s", e)
            
            # Log detailed mapping for debugging
            yes_token = yes_orderbook.get("token_id", "UNKNOWN") if yes_orderbook else "NONE"
            no_token = no_orderbook.get("token_id", "UNKNOWN") if no_orderbook else "NONE"
            logger.info("TRADE: %s - DETAILED: YES_token=%s YES_ask=%.4f, NO_token=%s NO_ask=%.4f", 
                       condition_id, 
                       yes_token[:20] + "..." if len(yes_token) > 20 else yes_token, 
                       yes_price or 0,
                       no_token[:20] + "..." if len(no_token) > 20 else no_token,
                       no_price or 0)
            
            if yes_price is None and no_price is None:
                logger.debug("TRADE: %s - Could not fetch orderbook prices.", condition_id)
                return

            # 5. Check price conditions
            min_price = buy_once_cfg.get("min_price", 0.97)
            max_price = buy_once_cfg.get("max_price", 0.99)
            
            # Use market-specific order size if available, otherwise fall back to BUY_ONCE_CONFIG
            default_order_size = buy_once_cfg.get("order_size", 10.0)
            market_cfg = config.MARKETS.get(market_name, {}) if market_name else {}
            order_size = market_cfg.get("max_order_size", default_order_size)
            
            stability_duration = buy_once_cfg.get("stability_duration", 30)
            
            buy_side = None
            buy_price = None

            # Check which side meets the price criteria
            # Also implement Time Persistence Check (Wait for Stability)
            import time
            current_time = time.time()
            
            # Initialize tracker for this condition if needed
            if condition_id not in self.price_stability_tracker:
                self.price_stability_tracker[condition_id] = {"YES": 0, "NO": 0}
            
            # Check YES side
            if yes_price is not None and min_price <= yes_price <= max_price:
                # Price is in range, check stability
                if self.price_stability_tracker[condition_id]["YES"] == 0:
                    self.price_stability_tracker[condition_id]["YES"] = current_time
                    logger.info("TRADE: %s - YES price %.4f hit target. Starting %ds stability timer.", condition_id, yes_price, stability_duration)
                
                duration = current_time - self.price_stability_tracker[condition_id]["YES"]
                if duration >= stability_duration:
                    buy_side = "YES"
                    buy_price = yes_price
                else:
                    logger.debug("TRADE: %s - YES price %.4f stable for %.1fs/%ds. Waiting.", condition_id, yes_price, duration, stability_duration)
            else:
                # Price dropped or out of range, reset timer
                if self.price_stability_tracker[condition_id]["YES"] > 0:
                    logger.info("TRADE: %s - YES price dropped below target. Resetting stability timer.", condition_id)
                    self.price_stability_tracker[condition_id]["YES"] = 0

            # Check NO side (only if we haven't decided to buy YES)
            if not buy_side:
                if no_price is not None and min_price <= no_price <= max_price:
                    # Price is in range, check stability
                    if self.price_stability_tracker[condition_id]["NO"] == 0:
                        self.price_stability_tracker[condition_id]["NO"] = current_time
                        logger.info("TRADE: %s - NO price %.4f hit target. Starting %ds stability timer.", condition_id, no_price, stability_duration)
                    
                    duration = current_time - self.price_stability_tracker[condition_id]["NO"]
                    if duration >= stability_duration:
                        buy_side = "NO"
                        buy_price = no_price
                    else:
                        logger.debug("TRADE: %s - NO price %.4f stable for %.1fs/%ds. Waiting.", condition_id, no_price, duration, stability_duration)
                else:
                    # Price dropped or out of range, reset timer
                    if self.price_stability_tracker[condition_id]["NO"] > 0:
                        logger.info("TRADE: %s - NO price dropped below target. Resetting stability timer.", condition_id)
                        self.price_stability_tracker[condition_id]["NO"] = 0

            if not buy_side:
                logger.debug("TRADE: %s - Prices not in range (YES=%.4f, NO=%.4f). Skipping.", 
                             condition_id, yes_price or 0, no_price or 0)
                return

            # 6. Safety Check: Verify we are buying the correct token
            # The user reported issues where the bot buys the cheap side (e.g. 5c) instead of expensive (97c).
            # We explicitly check the price of the target token ID before placing the order.
            target_token_id = self.client._get_token_id(condition_id, buy_side)
            if target_token_id:
                # We want to buy, so we check the 'buy' side (best ask) of the CLOB
                # In Polymarket API: side="buy" returns what you'd pay to buy (the ask)
                current_ask = self.client.get_price(target_token_id, side="buy")
                if current_ask is not None:
                    # STRICT SAFETY CHECK:
                    # 1. Wrong Side Protection: If we expect > 90c but price is < 50c, abort.
                    if buy_price > 0.90 and current_ask < 0.50:
                        logger.error(
                            "SAFETY CHECK FAILED: Attempting to buy %s at %.2f, but token %s is trading at %.2f. "
                            "Aborting trade to prevent wrong-side execution.",
                            buy_side, buy_price, target_token_id, current_ask
                        )
                        return
                    
                    # 2. Minimum Price Protection:
                    # User requested NOT to buy if the price is below the minimum threshold (e.g. 97c).
                    # This prevents buying into a crashing market where the price has suddenly dropped.
                    if current_ask < min_price:
                        logger.warning(
                            "SAFETY CHECK FAILED: Market price %.4f is below minimum threshold %.2f. "
                            "Aborting trade to avoid buying cheap shares.",
                            current_ask, min_price
                        )
                        return

                    logger.info("Safety check passed: %s token trading at %.2f (target %.2f, min %.2f)", 
                               buy_side, current_ask, buy_price, min_price)
            
            # 7. Place order
            logger.info("TRADE: %s - Price condition met for %s (%.4f). Placing 'Buy Once' order.", 
                        condition_id, buy_side, buy_price)
            
            # Apply aggressive premium if enabled
            if buy_once_cfg.get("aggressive_pricing", True):
                premium = buy_once_cfg.get("aggressive_premium", 0.005)
                # Tick size handling: > 0.97 uses 0.001
                # We use 3 decimal places for rounding when price > 0.97
                raw_price = buy_price * (1 + premium)
                # Cap at 0.99 as required by Polymarket CLOB API
                buy_price = round(min(0.99, raw_price), 3)

            # Check balance
            balance = self._get_available_balance()
            if balance < buy_price * order_size:
                logger.warning("TRADE: %s - Insufficient balance ($%.2f) for order ($%.2f)", 
                               condition_id, balance, buy_price * order_size)
                return

            order = self.order_manager.place_limit_order(
                condition_id=condition_id,
                side=buy_side,
                price=buy_price,
                size=order_size,
                strategy="buy_once",
                time_in_force="FOK"
            )
            
            if order:
                # If it's a dict, check status. If it's a string, it's the order_id (legacy behavior)
                order_id = order.get("order_id") if isinstance(order, dict) else order
                status = order.get("status") if isinstance(order, dict) else "open"
                
                if status == "matched":
                    # Use actual fill price from raw response if available
                    fill_price = buy_price
                    if isinstance(order, dict):
                        # order["_raw_response"] = polymarket_client normalized dict
                        # order["_raw_response"]["_raw_response"] = actual API response with takingAmount/makingAmount
                        poly_result = order.get("_raw_response") or {}
                        raw = poly_result.get("_raw_response") if isinstance(poly_result, dict) else {}
                        raw = raw or {}
                        taking = float(raw.get("takingAmount", 0))  # shares received
                        making = float(raw.get("makingAmount", 0))  # USDC spent
                        if taking > 0 and making > 0:
                            fill_price = round(making / taking, 4)
                    logger.info("TRADE: %s - [SUCCESS] 'Buy Once' order filled: %s %s @ %.3f",
                                condition_id, order_size, buy_side, fill_price)
                    self.position_tracker.update_position(condition_id, buy_side, order_size, fill_price)
                    # Record buy timestamp for grace period (prevent immediate stop loss)
                    if not hasattr(self, '_buy_timestamps'):
                        self._buy_timestamps = {}
                    self._buy_timestamps[condition_id] = time.time()

                else:
                    logger.info("TRADE: %s - [PENDING] 'Buy Once' order placed but not filled (FOK): %s", 
                                condition_id, order_id)
            else:
                logger.error("TRADE: %s - [FAILED] 'Buy Once' order failed", condition_id)

        except Exception as e:
            logger.error("TRADE: %s - Exception in analyze_and_trade: %s", condition_id, e, exc_info=True)

    def _manage_safety_exits(self, condition_id: str):
        """Manage safety exits (Stop Loss and Trailing Stop) for a specific market"""
        try:
            if not self.position_tracker.has_position(condition_id):
                return

            # Grace period: skip stop loss checks for 2s after buying
            # This prevents the bot's own buy from depleting orderbook liquidity
            # and causing an immediate false stop-loss trigger on the thin bid
            buy_timestamps = getattr(self, '_buy_timestamps', {})
            buy_ts = buy_timestamps.get(condition_id, 0)
            if buy_ts > 0 and (time.time() - buy_ts) < 2:
                return

            pos = self.position_tracker.get_position(condition_id)
            buy_once_cfg = getattr(config, "BUY_ONCE_CONFIG", {})
            
            # Check both sides (though usually we only have one)
            for side in ["YES", "NO"]:
                shares = float(pos.get(side, 0))
                if shares <= 0:
                    continue
                
                # Get current price for this side
                # We use get_price (best ask) as a proxy for what we could sell at (best bid)
                # but for safety exits, we should check the BID price (what we can sell for)
                target_token_id = self.client._get_token_id(condition_id, side)
                if not target_token_id:
                    continue
                
                # side="sell" in Polymarket API returns the best bid (what you get when you sell)
                current_bid = self.client.get_price(target_token_id, side="sell")
                if current_bid is None:
                    continue

                # 1. Stop Loss Check
                stop_loss_price = buy_once_cfg.get("stop_loss_price", 0.94)
                if current_bid <= stop_loss_price:
                    logger.warning(
                        "SAFETY EXIT: %s - Stop Loss triggered for %s. Price %.4f <= %.4f. Selling %.4f shares.",
                        condition_id, side, current_bid, stop_loss_price, shares
                    )
                    self._execute_safety_sell(condition_id, side, shares, current_bid, "stop_loss")
                    continue

                # 2. Trailing Stop Check
                trailing_dist = buy_once_cfg.get("trailing_stop_distance", 0.02)
                activation_price = buy_once_cfg.get("trailing_stop_activation_price", 0.98)
                peak_price = float(pos.get(f"highest_price_{side.lower()}", 0))
                
                if peak_price >= activation_price:
                    trigger_price = peak_price - trailing_dist
                    if current_bid <= trigger_price:
                        logger.warning(
                            "SAFETY EXIT: %s - Trailing Stop triggered for %s. Price %.4f <= %.4f (Peak: %.4f). Selling %.4f shares.",
                            condition_id, side, current_bid, trigger_price, peak_price, shares
                        )
                        self._execute_safety_sell(condition_id, side, shares, current_bid, "trailing_stop")
                        continue

        except Exception as e:
            logger.error("SAFETY EXIT: %s - Error in _manage_safety_exits: %s", condition_id, e)

    def _execute_safety_sell(self, condition_id: str, side: str, shares: float, current_price: float, reason: str) -> bool:
        """Execute a safety sell order with aggressive retry logic.
        
        Keeps trying to sell at floor prices until the entire position is closed.
        Uses FAK orders to ensure immediate execution or rejection.
        """
        try:
            # Get retry config - speed is critical for stop-loss exits
            safety_cfg = getattr(config, "SAFETY_SELL_CONFIG", {})
            max_retries = safety_cfg.get("max_retries", 15)
            retry_delay = safety_cfg.get("retry_delay", 0.1)  # Minimal delay - speed is everything
            
            # AGGRESSIVE FLOOR: Always sell at 0.01 for safety exits to hit any bid
            # unless it's a pre-resolution exit which has its own floor.
            floor_price = 0.01
            if reason == "pre_resolution":
                pre_res_cfg = getattr(config, "PRE_RESOLUTION_EXIT", {})
                floor_price = pre_res_cfg.get("min_exit_price", 0.99)
            
            sell_price = floor_price
            remaining_shares = shares
            
            logger.warning(
                "SAFETY EXIT: %s - Starting %s sell: %.4f shares @ %.3f (FLOOR)",
                condition_id[:10], reason.upper(), shares, sell_price
            )
            
            for attempt in range(max_retries):
                if remaining_shares <= 0.0001:
                    break
                
                # Place FAK order
                order = self.order_manager.place_limit_order(
                    condition_id=condition_id,
                    side=side,
                    price=sell_price,
                    size=remaining_shares,
                    strategy=f"safety_exit_{reason}",
                    time_in_force="FAK",
                    order_side="SELL"
                )
                
                filled_size = 0.0
                if order and isinstance(order, dict):
                    # Use size_matched from order_manager (which we just fixed)
                    filled_size = float(order.get("size_matched", 0.0))
                    
                    if filled_size > 0:
                        logger.info(
                            "SAFETY EXIT: %s - [PARTIAL/FULL] %s sold %.4f @ %.3f on attempt %d",
                            condition_id[:10], reason.upper(), filled_size, sell_price, attempt + 1
                        )
                        self.position_tracker.reduce_position(condition_id, side, filled_size)
                        remaining_shares -= filled_size
                
                if remaining_shares <= 0.0001:
                    remaining_shares = 0
                    break
                
                # If not fully filled, wait and retry
                # We don't immediately trust the API position (ghost share bug)
                # Instead we rely on our remaining_shares counter.
                # We only sync as a secondary check after a delay.
                logger.warning(
                    "SAFETY EXIT: %s - FAK partial/no fill (%.4f remaining), waiting %.1fs for liquidity (attempt %d/%d)",
                    condition_id[:10], remaining_shares, retry_delay, attempt + 1, max_retries
                )
                time.sleep(retry_delay)
                
                # Every 8 attempts, do a forced sync to verify ground truth
                if (attempt + 1) % 8 == 0:
                    logger.info("SAFETY EXIT: %s - Periodic position sync to verify ground truth...", condition_id[:10])
                    self._sync_positions_from_api(condition_id, force=True)
                    pos = self.position_tracker.get_position(condition_id)
                    real_shares = float(pos.get(side, 0))
                    
                    if real_shares < remaining_shares:
                        logger.warning("SAFETY EXIT: %s - Position sync shows fewer shares (%.4f) than tracked (%.4f). Adjusting.", 
                                     condition_id[:10], real_shares, remaining_shares)
                        remaining_shares = real_shares
            
            if remaining_shares > 0:
                logger.error(
                    "SAFETY EXIT: %s - [FAILED] Could not sell %.4f shares after %d retries",
                    condition_id[:10], remaining_shares, max_retries
                )
                return False
            else:
                logger.info("SAFETY EXIT: %s - [SUCCESS] Entire position closed.", condition_id[:10])
                return True
                
        except Exception as e:
            logger.error("SAFETY EXIT: %s - Error executing safety sell: %s", condition_id, e, exc_info=True)
            return False

    def _check_pre_resolution_exit(self, condition_id: str, market_config: Dict):
        """Check if we should exit positions near resolution when price is 99c+.
        
        When price is very high and market is close to resolution, the upside
        is minimal but the risk of a last-minute flip is real. Better to lock in profit.
        """
        try:
            pre_res_cfg = getattr(config, "PRE_RESOLUTION_EXIT", {})
            if not pre_res_cfg.get("enabled", True):
                return
            
            # Check if we have a position
            if not self.position_tracker.has_position(condition_id):
                return
            
            # Get market end time
            end_date_str = market_config.get("end_date_iso")
            if not end_date_str:
                return
            
            # Parse end time and check if we're within the exit window
            try:
                from datetime import datetime, timezone
                # Handle various ISO formats
                end_date_str = end_date_str.replace("Z", "+00:00")
                end_time = datetime.fromisoformat(end_date_str)
                now = datetime.now(timezone.utc)
                seconds_to_resolution = (end_time - now).total_seconds()
            except Exception as e:
                logger.debug("PRE_RES_EXIT: Could not parse end_date_iso: %s", e)
                return
            
            time_threshold = pre_res_cfg.get("time_before_resolution", 300)
            if seconds_to_resolution > time_threshold:
                if seconds_to_resolution < 600: # Only log if getting close (10 mins)
                    logger.debug("PRE_RES_EXIT: %s - Time %.0fs > threshold %.0fs", condition_id[:10], seconds_to_resolution, time_threshold)
                return  # Not within the exit window
            
            if seconds_to_resolution < 0:
                return # Already resolved
            
            min_exit_price = pre_res_cfg.get("min_exit_price", 0.99)
            price_discount = pre_res_cfg.get("price_discount", 0.005)
            
            pos = self.position_tracker.get_position(condition_id)
            
            # Check both sides
            for side in ["YES", "NO"]:
                shares = float(pos.get(side, 0))
                if shares <= 0:
                    continue
                
                # Get current bid price
                target_token_id = self.client._get_token_id(condition_id, side)
                if not target_token_id:
                    continue
                
                current_bid = self.client.get_price(target_token_id, side="sell")
                if current_bid is None:
                    continue
                
                # Check if price meets threshold
                if current_bid >= min_exit_price:
                    sell_price = round(current_bid - price_discount, 2)
                    logger.warning(
                        "PRE-RESOLUTION EXIT: %s - %s price %.4f >= %.2f with %.0fs to resolution. Selling %.4f shares.",
                        condition_id[:10], side, current_bid, min_exit_price, seconds_to_resolution, shares
                    )
                    success = self._execute_safety_sell(condition_id, side, shares, sell_price, "pre_resolution")
                    
                    # Mark as exited to prevent buy-back ONLY if fully closed
                    if success:
                        self._pre_resolution_exited_markets.add(condition_id)
                    else:
                        logger.warning("PRE-RESOLUTION EXIT: %s - Partial fill or failure. Market NOT marked as exited to allow retry.", condition_id[:10])
                    
        except Exception as e:
            logger.error("PRE_RES_EXIT: %s - Error: %s", condition_id, e, exc_info=True)


    
    
    def start(self):
        """Start the trading bot"""
        logger.info("Starting trading bot...")
        self.running = True
        
        # Subscribe to price and order book updates for all markets
        for market, market_config in self.market_configs.items():
            condition_id = market_config.get("condition_id")
            if condition_id:
                # Register market outcomes with client
                self.client.register_market(
                    condition_id,
                    market_config.get("yes_outcome", "Yes"),
                    market_config.get("no_outcome", "No")
                )
                
                # Prime the token mapping cache and asset_to_side map
                # This is CRITICAL to ensure WebSocket updates have the correct side
                self.client._get_token_mapping(condition_id)
                
                logger.info(f"Subscribing to {market} (condition_id: {condition_id})")
                
                # Subscribe to price updates
                self.client.subscribe_to_price_updates(
                    condition_id,
                    lambda cid, data, side: self.update_market_data(cid, data, side)
                )
                
                # Subscribe to order book updates
                self.client.subscribe_to_orderbook_updates(
                    condition_id,
                    lambda cid, data, side: self.update_orderbook_data(cid, data, side)
                )
                
                # Also fetch initial order book
                try:
                    orderbook = self.client.get_orderbook(condition_id)
                    if orderbook:
                        self.update_orderbook_data(condition_id, orderbook)
                except Exception as e:
                    logger.warning(f"Could not fetch initial orderbook for {market}: {e}")
            else:
                logger.warning(f"No condition_id configured for {market}")
        
        # Start main trading loop
        trading_thread = threading.Thread(target=self._trading_loop, daemon=True)
        trading_thread.start()
        
        # Start order management loop if enabled
        if getattr(config, "ORDER_STATUS_POLLING_ENABLED", True):
            order_thread = threading.Thread(target=self._order_management_loop, daemon=True)
            order_thread.start()
            
        # Start market refresh loop if auto-discovery is enabled
        if getattr(config, "AUTO_DISCOVERY_ENABLED", False):
            refresh_thread = threading.Thread(target=self._market_refresh_loop, daemon=True)
            refresh_thread.start()
            logger.info("Market refresh loop started")

        # Start auto-claim loop to claim winnings from resolved markets
        if getattr(config, "AUTO_CLAIM_ENABLED", True):
            claim_thread = threading.Thread(target=self._auto_claim_loop, daemon=True)
            claim_thread.start()
            logger.info("Auto-claim loop started (interval: %ds)", getattr(config, "AUTO_CLAIM_INTERVAL", 900))

        logger.info("Trading bot started")
    
    def _trading_loop(self):
        """Main trading loop"""
        logger.info("Trading loop started")
        iteration = 0
        micro_profit_check_counter = 0
        arb_cfg = getattr(config, "ARB_CONFIG", {})
        micro_profit_check_interval = arb_cfg.get("micro_profit_check_interval", 1)
        
        while self.running:
            try:
                iteration += 1
                if iteration % 10 == 0:  # Log every 10 iterations
                    logger.info("Trading loop iteration %d, markets: %d", iteration, len(self.market_configs))
                
                # Analyze each market
                for market, market_config in self.market_configs.items():
                    condition_id = market_config.get("condition_id")
                    if condition_id:
                        # Skip invalid markets (404 - market not found)
                        if condition_id in self._invalid_markets:
                            continue
                        
                        # Safety Exit Management - CHECK FIRST before any analysis
                        # This ensures positions are protected with minimal delay
                        self._manage_safety_exits(condition_id)

                        logger.debug("Trading loop: calling analyze_and_trade for %s (market: %s)", condition_id, market)
                        self.analyze_and_trade(condition_id, market)

                        # Pre-Resolution Exit (sell at 99c+ when near resolution)
                        self._check_pre_resolution_exit(condition_id, market_config)
                    else:
                        logger.debug("Trading loop: market %s has no condition_id", market)
                
                # Manage legacy markets (orphaned positions)
                # Iterate over copy of items to allow modification during iteration
                for condition_id, legacy_cfg in list(self.legacy_market_configs.items()):
                    if not self.position_tracker.has_position(condition_id):
                        logger.info("Legacy market %s position closed, removing from tracking", condition_id)
                        del self.legacy_market_configs[condition_id]
                        # Cleanup exit tracking
                        self._pre_resolution_exited_markets.discard(condition_id)
                        continue
                    
                    # Only manage exits for legacy markets - do NOT trade/buy more
                    self._manage_safety_exits(condition_id)
                    self._check_pre_resolution_exit(condition_id, legacy_cfg)
                
                micro_profit_check_counter += 1
                time.sleep(1)  # Check every second
            except Exception as e:
                logger.error(f"Error in trading loop: {e}", exc_info=True)
                time.sleep(5)

    def _market_refresh_loop(self):
        """Background loop to periodically refresh market condition IDs."""
        logger.info("Market refresh loop starting...")
        refresh_interval = getattr(config, "MARKET_REFRESH_INTERVAL", 300)  # Default 5 minutes
        
        while self.running:
            try:
                # Wait for next interval
                time.sleep(refresh_interval)
                
                logger.info("Refreshing market configurations...")
                new_configs = self._resolve_market_configs(config.MARKETS)
                
                # Check for changes
                changes_found = False
                for market, new_cfg in new_configs.items():
                    old_cfg = self.market_configs.get(market, {})
                    new_cid = new_cfg.get("condition_id")
                    old_cid = old_cfg.get("condition_id")
                    
                    if new_cid and new_cid != old_cid:
                        logger.info("NEW MARKET DISCOVERED: %s -> %s", market, new_cid)
                        
                        # Register with client
                        self.client.register_market(
                            new_cid,
                            new_cfg.get("yes_outcome", "Yes"),
                            new_cfg.get("no_outcome", "No")
                        )
                        
                        # Prime token mapping
                        self.client._get_token_mapping(new_cid)
                        
                        # Subscribe to updates
                        self.client.subscribe_to_price_updates(
                            new_cid,
                            lambda cid, data, side, m=market: self.update_market_data(cid, data, side)
                        )
                        self.client.subscribe_to_orderbook_updates(
                            new_cid,
                            lambda cid, data, side, m=market: self.update_orderbook_data(cid, data, side)
                        )
                        
                        # Check if we have an open position in the old market
                        if old_cid and self.position_tracker.has_position(old_cid):
                            logger.info("Preserving legacy market config for %s (position open)", old_cid)
                            self.legacy_market_configs[old_cid] = old_cfg
                        
                        # Update local config
                        self.market_configs[market] = new_cfg
                        
                        # Cleanup exit tracking for the old market (no longer active for buying)
                        if old_cid:
                            self._pre_resolution_exited_markets.discard(old_cid)
                            
                        changes_found = True
                
                if not changes_found:
                    logger.debug("No market configuration changes detected")

            except Exception as e:
                logger.error("Error in market refresh loop: %s", e, exc_info=True)
                time.sleep(60)  # Wait a minute before retrying on error

    def _get_claim_lock_time(self):
        """Read last claim timestamp from shared lock file (cross-process safe)."""
        import os
        lock_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claim_lock")
        try:
            if os.path.exists(lock_file):
                with open(lock_file, "r") as f:
                    return float(f.read().strip())
        except (ValueError, OSError):
            pass
        return 0

    def _record_claim_time(self):
        """Write current timestamp to shared lock file (cross-process safe)."""
        import os
        lock_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claim_lock")
        try:
            with open(lock_file, "w") as f:
                f.write(str(time.time()))
        except OSError:
            pass

    def _wait_claim_cooldown(self):
        """Wait until enough time has passed since last claim across all processes."""
        min_gap = 20
        last = self._get_claim_lock_time()
        elapsed = time.time() - last
        if elapsed < min_gap:
            wait = min_gap - elapsed
            logger.info("AUTO-CLAIM: Cooldown - waiting %.0fs before next claim...", wait)
            time.sleep(wait)

    def _auto_claim_loop(self):
        """Background loop to automatically claim winnings from resolved markets."""
        logger.info("Auto-claim loop starting...")
        claim_interval = getattr(config, "AUTO_CLAIM_INTERVAL", 900)  # Default 15 minutes

        # Import the polymarket-apis package for gasless redemption
        try:
            from polymarket_apis import PolymarketGaslessWeb3Client, PolymarketDataClient
            web3_client = PolymarketGaslessWeb3Client(
                private_key=config.POLYMARKET_PRIVATE_KEY,
                signature_type=getattr(config, "POLYMARKET_SIGNATURE_TYPE", 2),
                chain_id=getattr(config, "POLYMARKET_CHAIN_ID", 137)
            )
            data_client = PolymarketDataClient()
            logger.info("AUTO-CLAIM: Initialized gasless web3 client for address %s", web3_client.address)
        except ImportError:
            logger.warning("AUTO-CLAIM: polymarket-apis package not installed. Auto-claim disabled.")
            logger.warning("AUTO-CLAIM: Install with: pip install polymarket-apis")
            return
        except Exception as e:
            logger.error("AUTO-CLAIM: Failed to initialize web3 client: %s", e)
            return

        # Wait a bit before first check to let bot initialize
        time.sleep(60)

        while self.running:
            try:
                # Skip if we're in a rate-limit backoff period
                skip_until = getattr(self, '_claim_skip_until', 0)
                if time.time() < skip_until:
                    remaining = int(skip_until - time.time())
                    logger.info("AUTO-CLAIM: In rate-limit backoff, %d min remaining", remaining // 60)
                    time.sleep(claim_interval)
                    continue

                logger.info("AUTO-CLAIM: Checking for redeemable positions...")

                # Get all positions that can be redeemed (resolved markets)
                try:
                    positions = data_client.get_positions(web3_client.address, redeemable=True)
                except Exception as e:
                    logger.warning("AUTO-CLAIM: Error fetching positions: %s", e)
                    positions = []

                if not positions:
                    logger.info("AUTO-CLAIM: No redeemable positions found")
                else:
                    logger.info("AUTO-CLAIM: Found %d redeemable positions", len(positions))

                    # Build batch of positions to redeem
                    batch = []
                    total_value = 0.0
                    for pos in positions:
                        condition_id = getattr(pos, 'condition_id', None) or getattr(pos, 'conditionId', None)
                        if not condition_id:
                            continue

                        neg_risk = getattr(pos, 'negative_risk', False) or getattr(pos, 'negRisk', False)
                        amounts = [0.0, 0.0]
                        outcome_index = getattr(pos, 'outcome_index', None)
                        size = float(getattr(pos, 'size', 0) or 0)

                        if outcome_index is not None and size > 0:
                            amounts[outcome_index] = size
                        elif hasattr(pos, 'outcome'):
                            outcome = str(getattr(pos, 'outcome', '')).upper()
                            if outcome in ['YES', 'UP', '0']:
                                amounts[0] = size
                            elif outcome in ['NO', 'DOWN', '1']:
                                amounts[1] = size

                        if sum(amounts) == 0:
                            continue

                        batch.append({
                            "condition_id": condition_id,
                            "amounts": amounts,
                            "neg_risk": neg_risk,
                            "size": size,
                        })
                        total_value += size

                    if not batch:
                        logger.info("AUTO-CLAIM: No valid positions to redeem after filtering")
                    else:
                        # Single attempt per cycle - retries just perpetuate rate limits
                        from claim_utils import batch_redeem as _batch_redeem_fn

                        try:
                            self._wait_claim_cooldown()
                            logger.info("AUTO-CLAIM: Redeeming %d positions (~$%.2f)",
                                       len(batch), total_value)

                            _batch_redeem_fn(web3_client, batch)
                            self._record_claim_time()
                            self._consecutive_claim_429s = 0

                            logger.info("AUTO-CLAIM: Successfully redeemed %d positions (~$%.2f)",
                                       len(batch), total_value)

                        except Exception as claim_e:
                            self._record_claim_time()
                            error_msg = str(claim_e).lower()
                            is_rate_limited = any(x in error_msg for x in ['rate limit', '429', 'too many requests', 'throttl'])
                            is_already_claimed = 'already' in error_msg

                            # Log response body for 429s (httpx includes it)
                            response_body = ''
                            if hasattr(claim_e, 'response'):
                                try:
                                    response_body = claim_e.response.text[:500]
                                except Exception:
                                    pass

                            if is_already_claimed:
                                logger.info("AUTO-CLAIM: Positions already claimed")
                            elif is_rate_limited:
                                # Parse "resets in X seconds" from relayer response
                                import re
                                reset_match = re.search(r'resets in (\d+) seconds', response_body or error_msg)
                                if reset_match:
                                    reset_seconds = int(reset_match.group(1)) + 60  # add 1 min buffer
                                    self._claim_skip_until = time.time() + reset_seconds
                                    logger.warning("AUTO-CLAIM: Quota exceeded. Relayer resets in %d min. "
                                                  "Will retry after reset. Response: %s",
                                                  reset_seconds // 60, response_body)
                                else:
                                    self._consecutive_claim_429s = getattr(self, '_consecutive_claim_429s', 0) + 1
                                    skip_cycles = min(2 ** (self._consecutive_claim_429s - 1), 8)
                                    self._claim_skip_until = time.time() + (claim_interval * skip_cycles)
                                    logger.warning("AUTO-CLAIM: Rate limited (consecutive: %d). "
                                                  "Skipping %d cycles (~%d min). Response: %s",
                                                  self._consecutive_claim_429s, skip_cycles,
                                                  (claim_interval * skip_cycles) // 60,
                                                  response_body or error_msg)
                            else:
                                # Non-rate-limit error: try individual claims
                                logger.warning("AUTO-CLAIM: Batch failed (%s), trying individual claims", claim_e)
                                time.sleep(15)
                                for pos_data in batch:
                                    try:
                                        self._wait_claim_cooldown()
                                        web3_client.redeem_position(
                                            condition_id=pos_data["condition_id"],
                                            amounts=pos_data["amounts"],
                                            neg_risk=pos_data["neg_risk"]
                                        )
                                        self._record_claim_time()
                                        logger.info("AUTO-CLAIM: Redeemed %s (~$%.2f)",
                                                   pos_data["condition_id"][:16], pos_data["size"])
                                    except Exception as ind_e:
                                        self._record_claim_time()
                                        logger.warning("AUTO-CLAIM: Failed to redeem %s: %s",
                                                      pos_data["condition_id"][:16], ind_e)

                # Wait for next interval
                time.sleep(claim_interval)

            except Exception as e:
                logger.error("AUTO-CLAIM: Error in auto-claim loop: %s", e, exc_info=True)
                time.sleep(300)  # Wait 5 minutes before retrying on error

    def _order_management_loop(self):
        """Order management loop"""
        while self.running:
            try:
                # Update order statuses
                self.order_manager.update_order_status()
                
                # Sync positions from API for all markets to catch any filled orders
                # This ensures position tracker stays in sync with actual exchange positions
                for market, market_config in self.market_configs.items():
                    condition_id = market_config.get("condition_id")
                    if condition_id and condition_id not in self._invalid_markets:
                        try:
                            # Sync filled orders first
                            self._sync_filled_orders(condition_id)
                            # Then sync positions from API
                            self._sync_positions_from_api(condition_id)
                        except Exception as e:
                            logger.debug("ORDER_MGMT: Error syncing %s positions: %s", condition_id[:10], e)
                
                # Cancel stale orders
                self.order_manager.cancel_stale_orders(
                    timeout_seconds=config.ORDER_CONFIG.get("order_timeout", 300)
                )
                
                time.sleep(30)  # Check every 30 seconds
            except Exception as e:
                logger.error(f"Error in order management loop: {e}")
                time.sleep(60)
    
    def stop(self):
        """Stop the trading bot"""
        logger.info("Stopping trading bot...")
        self.running = False
        self.client.stop()
        self.data_aggregator.stop()
        logger.info("Trading bot stopped")


def main():
    """Main entry point"""
    import subprocess
    import sys
    import os

    # Start the dashboard in a subprocess
    dashboard_process = None
    dashboard_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard.py")

    try:
        logger.info("Starting dashboard on http://localhost:5052")
        dashboard_process = subprocess.Popen(
            [sys.executable, dashboard_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        logger.warning(f"Failed to start dashboard: {e}")

    bot = TradingBot()

    try:
        bot.start()

        # Keep running
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Received interrupt signal")
    finally:
        bot.stop()
        # Stop the dashboard
        if dashboard_process:
            logger.info("Stopping dashboard...")
            dashboard_process.terminate()
            try:
                dashboard_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                dashboard_process.kill()


if __name__ == "__main__":
    main()

