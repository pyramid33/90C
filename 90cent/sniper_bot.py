"""
97c Sniper Bot
Strategy: "Grinding the Favorites"
Target: Live Sports Markets
Trigger: Buy YES or NO shares when price is between 0.97 and 0.99.
"""

import time
import logging
import json
import os
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Optional

from polymarket_client import PolymarketClient
from config import (
    POLYMARKET_API_KEY,
    POLYMARKET_API_SECRET,
    POLYMARKET_API_PASSPHRASE,
    POLYMARKET_PRIVATE_KEY,
    POLYMARKET_WALLET_ADDRESS,
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("sniper_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("SniperBot")

class SniperBot:
    def __init__(self, dry_run: bool = True):
        self.dry_run = dry_run
        self.client = PolymarketClient(
            api_key=POLYMARKET_API_KEY,
            api_secret=POLYMARKET_API_SECRET,
            api_passphrase=POLYMARKET_API_PASSPHRASE,
            private_key=POLYMARKET_PRIVATE_KEY,
            wallet_address=POLYMARKET_WALLET_ADDRESS
        )
        self.min_price = 0.97
        self.max_price = 0.99
        self.check_interval = 5  # Seconds between scans
        self.sports_keywords = [
            "NBA", "NFL", "MLB", "NHL", "SOCCER", "TENNIS", "BASKETBALL",
            "ESPORTS", "COUNTER STRIKE", "CSGO", "CS2", "DOTA", "LEAGUE OF LEGENDS", "VALORANT"
        ]
        
        logger.info(f"Sniper Bot Initialized (Dry Run: {self.dry_run})")
        logger.info(f"Targeting prices between {self.min_price} and {self.max_price}")

    def is_live_sport(self, market: Dict, force_sport: bool = False) -> bool:
        """Check if market is a live sports event."""
        # 1. Check Category/Tags
        is_sport = force_sport
        if not is_sport:
            tags = market.get('tags', [])
            category = market.get('category', '').upper()
            
            if any(k in category for k in self.sports_keywords):
                is_sport = True
            elif any(any(k in t.upper() for k in self.sports_keywords) for t in tags):
                is_sport = True
            
        if not is_sport:
            logger.debug(f"Market {market.get('question')} filtered: Not a sport")
            return False

        # 2. Check Live Status
        # A game is live if it started in the past but hasn't closed yet
        try:
            # Check for various start time keys
            start_time_str = (
                market.get('game_start_time') or 
                market.get('gameStartTime') or 
                market.get('startDate')
            )
            
            if not start_time_str:
                logger.debug(f"Market {market.get('question')} filtered: No start time found")
                return False
                
            start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
            now = datetime.now(timezone.utc)
            
            # Must have started
            if now < start_time:
                logger.debug(f"Market {market.get('question')} filtered: Not started yet (Starts: {start_time_str})")
                return False
                
            # Must be active
            active = market.get('active')
            if active is not None and not active:
                logger.debug(f"Market {market.get('question')} filtered: Not active")
                return False

            # Must not be resolved/closed
            if market.get('closed') or market.get('resolved'):
                logger.debug(f"Market {market.get('question')} filtered: Closed or Resolved")
                return False
                
            # Optional: Check if it's been running "too long" (e.g. > 5 hours) to avoid stale markets
            if (now - start_time) > timedelta(hours=6):
                logger.debug(f"Market {market.get('question')} filtered: Too old (> 6 hours)")
                return False
                
            logger.info(f"✅ LIVE MARKET FOUND: {market.get('question')}")
            return True
            
        except Exception as e:
            logger.debug(f"Error checking live status for {market.get('question')}: {e}")
            return False

    def scan_markets(self):
        """Fetch and scan active markets using events and sports endpoints."""
        logger.info("Scanning for live sports/esports markets...")
        
        try:
            tags = ["Sports", "Esports"]
            events = self.client.get_events(tags=tags, limit=50, active=True)
            
            logger.info(f"Fetched {len(events)} active events from API.")
            for e in events:
                logger.debug(f"Event: {e.get('title')} | Tags: {e.get('tags')}")
            
            live_markets = []
            for event in events:
                # Events contain a list of markets
                event_markets = event.get('markets', [])
                for m in event_markets:
                    # Enrich market with event-level data if needed
                    # Since we fetched these via sports/esports tags, we force_sport=True
                    if self.is_live_sport(m, force_sport=True):
                        live_markets.append(m)
            
            # 2. Fallback or Supplement: Fetch markets directly with tags
            # Some markets might not be grouped under the main sports events
            direct_markets = self.client.search_markets_gamma(
                limit=50,
                tags=tags,
                active=True,
                closed=False,
                resolved=False
            )
            for m in direct_markets:
                if m.get('id') not in [lm.get('id') for lm in live_markets]:
                    if self.is_live_sport(m):
                        live_markets.append(m)
            
            logger.info(f"Found {len(live_markets)} live sports/esports markets.")
            
            for market in live_markets:
                self.check_market_opportunities(market)
                
        except Exception as e:
            logger.error(f"Error during scan: {e}")

    def check_market_opportunities(self, market: Dict):
        """Check if market has outcomes in the target price range."""
        question = market.get('question')
        condition_id = market.get('condition_id')
        tokens = market.get('tokens', [])
        
        if not tokens or not condition_id:
            return

        # We need to get the latest price. 
        # The market object might have stale 'yes_price' or 'outcomes' data.
        # Ideally, we fetch the Orderbook, but for speed we can check the 'best_ask' if available,
        # or rely on the cached market data if it's fresh enough.
        # Gamma markets endpoint usually returns 'outcomePrices' or similar.
        
        # Let's look at the outcome prices provided in the market object
        # Note: Gamma format varies. Sometimes it's in 'outcomePrices' (list of strings)
        outcome_prices = market.get('outcomePrices', [])
        
        if not outcome_prices:
            # Fallback: try to infer from 'yes_price' if binary
            yp = market.get('yes_price')
            if yp is not None:
                outcome_prices = [str(1-yp), str(yp)] # [NO, YES] usually
            else:
                return

        outcomes = market.get('outcomes', ['NO', 'YES']) # Usually [NO, YES] order for binary
        
        for i, price_str in enumerate(outcome_prices):
            try:
                price = float(price_str)
            except:
                continue
                
            # Check if price is in target range
            if self.min_price <= price < self.max_price:
                outcome_label = outcomes[i] if i < len(outcomes) else f"Outcome {i}"
                token_id = tokens[i].get('token_id') if i < len(tokens) else None
                
                self.found_opportunity(market, outcome_label, price, token_id)

    def found_opportunity(self, market: Dict, outcome: str, price: float, token_id: str):
        """Log and execute trade for a found opportunity."""
        condition_id = market.get('conditionId') or market.get('condition_id')
        log_msg = (
            f"🎯 SNIPE OPPORTUNITY FOUND!\n"
            f"Event: {market.get('question')}\n"
            f"Outcome: {outcome}\n"
            f"Price: {price:.3f} (Target: {self.min_price}-{self.max_price})\n"
            f"ID: {token_id}"
        )
        logger.info(log_msg)
        
        if self.dry_run:
            logger.info(f"[DRY RUN] Would buy 10 shares of {outcome} at {price}")
        else:
            if condition_id and token_id:
                self.execute_trade(condition_id, outcome, price)
            else:
                logger.error("Missing condition_id or token_id for execution.")

    def execute_trade(self, condition_id: str, outcome: str, price: float):
        """Execute the trade using a Fill-Or-Kill limit order."""
        logger.info(f"EXECUTING TRADE: Buy 10 shares of {outcome} at {price} (Condition: {condition_id})")
        
        try:
            # We use FOK (Fill-Or-Kill) to ensure we either get the whole order at our price or nothing.
            # This is safer for sniping 97c-99c where liquidity might vanish.
            res = self.client.place_limit_order(
                condition_id=condition_id,
                side=outcome.upper(), # YES or NO
                price=price,
                size=10.0,
                time_in_force="FOK"
            )
            if res:
                logger.info(f"✅ Order placed successfully: {res.get('order_id')}")
            else:
                logger.warning("❌ Order failed or was not filled (FOK).")
        except Exception as e:
            logger.error(f"Trade execution failed: {e}")

    def run(self):
        """Main loop."""
        logger.info("Starting Sniper Bot loop...")
        try:
            while True:
                self.scan_markets()
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            logger.info("Sniper Bot stopped by user.")

if __name__ == "__main__":
    # Default to Dry Run = True for safety, now switching to False as requested
    bot = SniperBot(dry_run=False)
    bot.run()
