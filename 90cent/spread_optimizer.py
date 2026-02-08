"""
Dynamic Spread Optimizer
Finds optimal spreads based on order book depth, fill probability, and historical performance
"""
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import logging

logger = logging.getLogger(__name__)


class SpreadOptimizer:
    """Optimizes spread for maximum fill probability while maintaining profitability"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.spread_performance: Dict[str, List[Dict]] = {}  # Track spread performance
        self.orderbook_depth_cache: Dict[str, Dict] = {}
        
    def calculate_fill_probability(self, condition_id: str, spread: float, 
                                  side: str, orderbook: Dict) -> float:
        """
        Estimate fill probability at given spread
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        current_price = orderbook.get("last_price", 0)
        
        if current_price == 0:
            return 0.0
        
        target_price = current_price * (1 - spread) if side == "YES" else current_price * (1 + spread)
        
        if side == "YES":
            # Buying: need to be at or above best bid
            if not bids:
                return 0.0
            best_bid = float(bids[0].get("price", 0))
            if target_price >= best_bid:
                # Calculate depth at target price
                depth = sum(float(o.get("size", 0)) for o in bids 
                          if float(o.get("price", 0)) >= target_price)
                # More depth = higher fill probability
                fill_prob = min(depth / 10.0, 1.0)  # Normalize
                return fill_prob
        else:
            # Selling: need to be at or below best ask
            if not asks:
                return 0.0
            best_ask = float(asks[0].get("price", 0))
            if target_price <= best_ask:
                depth = sum(float(o.get("size", 0)) for o in asks 
                          if float(o.get("price", 0)) <= target_price)
                fill_prob = min(depth / 10.0, 1.0)
                return fill_prob
        
        return 0.0
    
    def calculate_optimal_spread(self, condition_id: str, current_price: float,
                                orderbook: Dict, side: str, 
                                min_spread: float = 0.0005,
                                max_spread: float = 0.005) -> Tuple[float, float]:
        """
        Calculate optimal spread that balances fill probability and profitability
        Returns: (optimal_spread, expected_fill_probability)
        """
        # Test different spreads
        spread_candidates = np.linspace(min_spread, max_spread, 20)
        best_spread = min_spread
        best_score = 0.0
        best_fill_prob = 0.0
        
        for spread in spread_candidates:
            fill_prob = self.calculate_fill_probability(condition_id, spread, side, orderbook)
            
            # Score = fill_probability * (1 - spread_penalty)
            # Prefer higher fill prob but penalize wide spreads
            spread_penalty = spread / max_spread
            score = fill_prob * (1 - spread_penalty * 0.3)  # 30% penalty for wide spreads
            
            if score > best_score:
                best_score = score
                best_spread = spread
                best_fill_prob = fill_prob
        
        return (best_spread, best_fill_prob)
    
    def analyze_orderbook_depth(self, condition_id: str, orderbook: Dict) -> Dict:
        """
        Analyze order book depth at different price levels
        """
        bids = orderbook.get("bids", [])
        asks = orderbook.get("asks", [])
        current_price = orderbook.get("last_price", 0)
        
        if current_price == 0:
            return {}
        
        # Calculate depth at different price levels
        depth_levels = [0.001, 0.002, 0.005, 0.01]  # 0.1%, 0.2%, 0.5%, 1%
        
        depth_analysis = {
            "bid_depth": {},
            "ask_depth": {},
            "total_liquidity": 0,
            "imbalance": 0
        }
        
        for level in depth_levels:
            price_above = current_price * (1 + level)
            price_below = current_price * (1 - level)
            
            # Bid depth (below current price)
            bid_depth = sum(float(o.get("size", 0)) for o in bids 
                          if price_below <= float(o.get("price", 0)) <= current_price)
            
            # Ask depth (above current price)
            ask_depth = sum(float(o.get("size", 0)) for o in asks 
                          if current_price <= float(o.get("price", 0)) <= price_above)
            
            depth_analysis["bid_depth"][level] = bid_depth
            depth_analysis["ask_depth"][level] = ask_depth
        
        total_bid = sum(depth_analysis["bid_depth"].values())
        total_ask = sum(depth_analysis["ask_depth"].values())
        depth_analysis["total_liquidity"] = total_bid + total_ask
        
        if total_bid + total_ask > 0:
            depth_analysis["imbalance"] = (total_bid - total_ask) / (total_bid + total_ask)
        
        # Cache for later use
        self.orderbook_depth_cache[condition_id] = depth_analysis
        
        return depth_analysis
    
    def get_spread_recommendation(self, condition_id: str, current_price: float,
                                 orderbook: Dict, side: str, 
                                 volatility_multiplier: float = 1.0) -> Dict:
        """
        Get comprehensive spread recommendation
        """
        depth_analysis = self.analyze_orderbook_depth(condition_id, orderbook)
        
        # Base spread from config
        base_spread = self.config.get("spread_percentage", 0.001)
        min_spread = self.config.get("min_spread", 0.0005)
        max_spread = self.config.get("max_spread", 0.005)
        
        # Adjust for volatility
        adjusted_base = base_spread * volatility_multiplier
        
        # Calculate optimal spread
        optimal_spread, fill_prob = self.calculate_optimal_spread(
            condition_id, current_price, orderbook, side, min_spread, max_spread
        )
        
        # Adjust based on depth
        if depth_analysis.get("total_liquidity", 0) > 100:
            # High liquidity: can use tighter spread
            optimal_spread = max(optimal_spread * 0.8, min_spread)
        elif depth_analysis.get("total_liquidity", 0) < 10:
            # Low liquidity: need wider spread
            optimal_spread = min(optimal_spread * 1.5, max_spread)
        
        # Use historical performance if available
        historical_optimal = self._get_historical_optimal_spread(condition_id, side)
        if historical_optimal:
            # Blend historical with current optimal
            optimal_spread = (optimal_spread * 0.6 + historical_optimal * 0.4)
        
        return {
            "optimal_spread": optimal_spread,
            "fill_probability": fill_prob,
            "depth_analysis": depth_analysis,
            "recommended_entry_price": current_price * (1 - optimal_spread) if side == "YES" else current_price * (1 + optimal_spread),
            "spread_multiplier": volatility_multiplier
        }
    
    def record_spread_performance(self, condition_id: str, spread: float, 
                                 side: str, filled: bool, profit: float = 0):
        """Record spread performance for learning"""
        if condition_id not in self.spread_performance:
            self.spread_performance[condition_id] = []
        
        self.spread_performance[condition_id].append({
            "timestamp": datetime.now(),
            "spread": spread,
            "side": side,
            "filled": filled,
            "profit": profit
        })
        
        # Keep only recent history
        if len(self.spread_performance[condition_id]) > 1000:
            self.spread_performance[condition_id] = self.spread_performance[condition_id][-1000:]
    
    def _get_historical_optimal_spread(self, condition_id: str, side: str) -> Optional[float]:
        """Get optimal spread from historical performance"""
        if condition_id not in self.spread_performance:
            return None
        
        # Get recent performance for this side
        recent = [p for p in self.spread_performance[condition_id][-100:]
                 if p["side"] == side and p["filled"]]
        
        if not recent:
            return None
        
        # Find spread with best fill rate and profitability
        spread_stats = defaultdict(lambda: {"fills": 0, "total": 0, "profit": 0})
        
        for perf in recent:
            spread = perf["spread"]
            spread_stats[spread]["total"] += 1
            if perf["filled"]:
                spread_stats[spread]["fills"] += 1
                spread_stats[spread]["profit"] += perf["profit"]
        
        # Score: fill_rate * (1 + profit_factor)
        best_spread = None
        best_score = 0
        
        for spread, stats in spread_stats.items():
            fill_rate = stats["fills"] / stats["total"] if stats["total"] > 0 else 0
            avg_profit = stats["profit"] / stats["fills"] if stats["fills"] > 0 else 0
            profit_factor = min(avg_profit / 0.01, 1.0)  # Normalize profit
            score = fill_rate * (1 + profit_factor * 0.2)
            
            if score > best_score:
                best_score = score
                best_spread = spread
        
        return best_spread
















