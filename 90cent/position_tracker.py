"""
Position tracking and arbitrage detection for Polymarket trading
"""
import logging
import json
import os
from typing import Dict, Optional, Tuple
from datetime import datetime

import config

logger = logging.getLogger(__name__)


class PositionTracker:
    """Tracks YES/NO positions per condition and detects arbitrage opportunities"""
    
    def __init__(self, persistence_file: str = "positions.json"):
        # Track positions: {condition_id: {"YES": shares, "NO": shares, "avg_price_yes": float, "avg_price_no": float}}
        self.positions: Dict[str, Dict] = {}
        # Track last sync time to avoid excessive API calls
        self._last_sync: Dict[str, datetime] = {}
        self._sync_interval_seconds = 2.0  # Minimum seconds between syncs per condition
        self.persistence_file = persistence_file
        
        # Load existing positions from file
        self._load_from_file()
    
    def _save_to_file(self):
        """Save current positions to a JSON file for persistence"""
        try:
            # Create a serializable copy (convert datetime to string)
            serializable_positions = {}
            for cid, data in self.positions.items():
                serializable_positions[cid] = data.copy()
                if "last_update" in serializable_positions[cid] and isinstance(serializable_positions[cid]["last_update"], datetime):
                    serializable_positions[cid]["last_update"] = serializable_positions[cid]["last_update"].isoformat()
            
            with open(self.persistence_file, "w") as f:
                json.dump(serializable_positions, f, indent=4)
            logger.debug("POS_TRACKER: Saved positions to %s", self.persistence_file)
        except Exception as e:
            logger.error("POS_TRACKER: Error saving positions to file: %s", e)

    def _load_from_file(self):
        """Load positions from a JSON file on initialization"""
        if not os.path.exists(self.persistence_file):
            logger.debug("POS_TRACKER: No persistence file found at %s", self.persistence_file)
            return

        try:
            with open(self.persistence_file, "r") as f:
                data = json.load(f)
            
            # Convert ISO strings back to datetime
            for cid, pos_data in data.items():
                if "last_update" in pos_data and isinstance(pos_data["last_update"], str):
                    try:
                        pos_data["last_update"] = datetime.fromisoformat(pos_data["last_update"])
                    except ValueError:
                        pos_data["last_update"] = datetime.now()
                self.positions[cid] = pos_data
            
            logger.info("POS_TRACKER: Loaded %d positions from %s", len(self.positions), self.persistence_file)
        except Exception as e:
            logger.error("POS_TRACKER: Error loading positions from file: %s", e)
    
    def sync_from_api(self, condition_id: str, api_positions: list, outcome_map: Dict[str, str] = None):
        """
        Sync positions from Polymarket API response.
        
        Args:
            condition_id: The condition ID to sync
            api_positions: List of position dicts from client.get_positions()
            outcome_map: Maps outcome names like "Up"/"Down" to "YES"/"NO"
        """
        # If API returns None, we can't sync (error case)
        if api_positions is None:
            return
        
        # Initialize position if not exists
        if condition_id not in self.positions:
            self.positions[condition_id] = {
                "YES": 0.0,
                "NO": 0.0,
                "avg_price_yes": 0.0,
                "avg_price_no": 0.0,
                "highest_price_yes": 0.0,
                "highest_price_no": 0.0,
                "last_update": datetime.now()
            }
        
        # Default outcome mapping
        if outcome_map is None:
            outcome_map = {"Yes": "YES", "No": "NO", "Up": "YES", "Down": "NO"}
        
        # Reset positions for this condition before syncing
        yes_shares = 0.0
        no_shares = 0.0
        yes_avg_price = 0.0
        no_avg_price = 0.0
        
        condition_id_lower = condition_id.lower()
        
        for pos_data in api_positions:
            # Match by condition_id
            pos_condition = str(pos_data.get("condition_id", "") or pos_data.get("conditionId", "")).lower()
            if pos_condition != condition_id_lower:
                continue
            
            # Extract outcome and normalize to YES/NO
            outcome = str(pos_data.get("outcome", ""))
            normalized_outcome = outcome_map.get(outcome, outcome.upper())
            
            # Extract size and average price
            size = float(pos_data.get("size", 0) or 0)
            avg_price = float(pos_data.get("avgPrice", 0) or pos_data.get("avg_price", 0) or 0)
            
            if normalized_outcome == "YES":
                yes_shares += size
                if size > 0 and avg_price > 0:
                    yes_avg_price = avg_price
            elif normalized_outcome == "NO":
                no_shares += size
                if size > 0 and avg_price > 0:
                    no_avg_price = avg_price
        
        # Update tracker with API data
        old_yes = self.positions[condition_id]["YES"]
        old_no = self.positions[condition_id]["NO"]
        
        self.positions[condition_id]["YES"] = yes_shares
        self.positions[condition_id]["NO"] = no_shares
        self.positions[condition_id]["avg_price_yes"] = yes_avg_price
        self.positions[condition_id]["avg_price_no"] = no_avg_price
        self.positions[condition_id]["last_update"] = datetime.now()
        self._last_sync[condition_id] = datetime.now()
        
        # Log if positions changed
        if abs(old_yes - yes_shares) > 0.0001 or abs(old_no - no_shares) > 0.0001:
            logger.info(
                f"SYNC: {condition_id[:10]}... positions updated from API: "
                f"YES: {old_yes:.4f} -> {yes_shares:.4f}, NO: {old_no:.4f} -> {no_shares:.4f}"
            )
            # Save to file after update
            self._save_to_file()
    
    def should_sync(self, condition_id: str) -> bool:
        """Check if enough time has passed since last sync for this condition"""
        if condition_id not in self._last_sync:
            return True
        elapsed = (datetime.now() - self._last_sync[condition_id]).total_seconds()
        return elapsed >= self._sync_interval_seconds
    
    def update_position(self, condition_id: str, side: str, shares: float, price: float):
        """Update position after a trade"""
        if condition_id not in self.positions:
            self.positions[condition_id] = {
                "YES": 0.0,
                "NO": 0.0,
                "avg_price_yes": 0.0,
                "avg_price_no": 0.0,
                "highest_price_yes": 0.0,
                "highest_price_no": 0.0,
                "last_update": datetime.now()
            }
        
        pos = self.positions[condition_id]
        side_key = side.upper()
        
        # Update shares and average price
        if side_key == "YES":
            if pos["YES"] == 0:
                pos["avg_price_yes"] = price
                pos["YES"] = shares
            else:
                # Weighted average
                total_value = pos["YES"] * pos["avg_price_yes"] + shares * price
                pos["YES"] += shares
                pos["avg_price_yes"] = total_value / pos["YES"] if pos["YES"] > 0 else 0
        elif side_key == "NO":
            if pos["NO"] == 0:
                pos["avg_price_no"] = price
                pos["NO"] = shares
            else:
                total_value = pos["NO"] * pos["avg_price_no"] + shares * price
                pos["NO"] += shares
                pos["avg_price_no"] = total_value / pos["NO"] if pos["NO"] > 0 else 0
        
        pos["last_update"] = datetime.now()
        
        # Initialize highest price with entry price
        if side_key == "YES":
            pos["highest_price_yes"] = max(pos.get("highest_price_yes", 0), price)
        else:
            pos["highest_price_no"] = max(pos.get("highest_price_no", 0), price)
            
        # Save to file after update
        self._save_to_file()
    
    def reduce_position(self, condition_id: str, side: str, shares: float):
        """Reduce position (sell)"""
        if condition_id not in self.positions:
            return
        
        pos = self.positions[condition_id]
        side_key = side.upper()
        
        if side_key == "YES":
            pos["YES"] = max(0, pos["YES"] - shares)
            if pos["YES"] == 0:
                pos["avg_price_yes"] = 0.0
        elif side_key == "NO":
            pos["NO"] = max(0, pos["NO"] - shares)
            if pos["NO"] == 0:
                pos["avg_price_no"] = 0.0
        
        # Save to file after reduction
        self._save_to_file()
    
    def get_position(self, condition_id: str) -> Dict:
        """Get current position for a condition"""
        return self.positions.get(condition_id, {
            "YES": 0.0,
            "NO": 0.0,
            "avg_price_yes": 0.0,
            "avg_price_no": 0.0,
            "highest_price_yes": 0.0,
            "highest_price_no": 0.0
        })

    def update_peak_price(self, condition_id: str, side: str, current_price: float):
        """Update the highest price seen for a position"""
        if condition_id not in self.positions:
            return
        
        pos = self.positions[condition_id]
        side_key = side.upper()
        
        if side_key == "YES" and pos["YES"] > 0:
            if current_price > pos.get("highest_price_yes", 0):
                pos["highest_price_yes"] = current_price
                self._save_to_file()
        elif side_key == "NO" and pos["NO"] > 0:
            if current_price > pos.get("highest_price_no", 0):
                pos["highest_price_no"] = current_price
                self._save_to_file()
    
    def has_position(self, condition_id: str, side: Optional[str] = None) -> bool:
        """Check if we have a position"""
        if condition_id not in self.positions:
            return False
        
        pos = self.positions[condition_id]
        if side is None:
            return pos["YES"] > 0 or pos["NO"] > 0
        return pos[side.upper()] > 0
    
    def detect_arbitrage(self, condition_id: str, yes_price: float, no_price: float,
                         min_profit_threshold: Optional[float] = None) -> Optional[Tuple[str, float]]:
        """
        Detect arbitrage opportunities:
        - If YES + NO < 1.0, buy both for guaranteed profit
        - Returns ("ARBITRAGE", profit_percentage) if opportunity exists
        """
        combined_price = yes_price + no_price
        profit = 1.0 - combined_price

        # If no explicit threshold passed, derive it from ARB_ENTRY_CONFIG (min_edge_bps)
        if min_profit_threshold is None:
            try:
                arb_cfg = getattr(config, "ARB_ENTRY_CONFIG", {})
                min_profit_threshold = float(arb_cfg.get("min_edge_bps", 0)) / 10000.0
            except Exception:
                min_profit_threshold = 0.04  # sensible default 4¢
        
        # Require a decent edge after fees/slippage (use >= to allow exact threshold matches)
        if profit >= min_profit_threshold:
            logger.info(
                f"Arbitrage opportunity detected for {condition_id}: "
                f"YES={yes_price:.4f}, NO={no_price:.4f}, "
                f"Combined={combined_price:.4f}, Profit={profit:.2%}"
            )
            return ("ARBITRAGE", profit)
        
        return None
    
    def should_flip_position(self, condition_id: str, new_side: str, 
                           confidence: float, min_confidence_flip: float = 0.6) -> bool:
        """
        Determine if we should flip from current position to new side
        Returns True if we have opposite position and new signal is strong enough
        """
        if not self.has_position(condition_id):
            return False
        
        pos = self.positions[condition_id]
        new_side_key = new_side.upper()
        
        # Check if we have opposite position
        if new_side_key == "YES" and pos["NO"] > 0:
            return confidence >= min_confidence_flip
        elif new_side_key == "NO" and pos["YES"] > 0:
            return confidence >= min_confidence_flip
        
        return False
    
    def get_flip_instructions(self, condition_id: str, new_side: str) -> Optional[Dict]:
        """
        Get instructions for flipping position:
        Returns {"sell_side": "YES/NO", "sell_shares": float, "buy_side": "YES/NO"}
        """
        if condition_id not in self.positions:
            return None
        
        pos = self.positions[condition_id]
        new_side_key = new_side.upper()
        
        if new_side_key == "YES" and pos["NO"] > 0:
            return {
                "sell_side": "NO",
                "sell_shares": pos["NO"],
                "buy_side": "YES"
            }
        elif new_side_key == "NO" and pos["YES"] > 0:
            return {
                "sell_side": "YES",
                "sell_shares": pos["YES"],
                "buy_side": "NO"
            }
        
        return None

