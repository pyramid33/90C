"""
Real-time Order Book Analysis
Analyzes order book depth, imbalances, and large orders
"""
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class OrderBookAnalyzer:
    """Analyzes order book data for trading signals"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.orderbook_history: Dict[str, List[Dict]] = {}
        self.max_history = 100
        
    def update_orderbook(self, condition_id: str, orderbook: Dict):
        """Update order book data"""
        if condition_id not in self.orderbook_history:
            self.orderbook_history[condition_id] = []
        
        orderbook_data = {
            "timestamp": datetime.now(),
            "bids": orderbook.get("bids", []),
            "asks": orderbook.get("asks", []),
            "last_price": orderbook.get("last_price", 0)
        }
        
        self.orderbook_history[condition_id].append(orderbook_data)
        
        # Keep only recent history
        if len(self.orderbook_history[condition_id]) > self.max_history:
            self.orderbook_history[condition_id] = self.orderbook_history[condition_id][-self.max_history:]
    
    def calculate_order_imbalance(self, condition_id: str) -> Optional[float]:
        """
        Calculate order book imbalance
        Returns: -1 to 1, where 1 = strong buy pressure, -1 = strong sell pressure
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        bids = latest.get("bids", [])
        asks = latest.get("asks", [])
        
        if not bids or not asks:
            return None
        
        # Calculate total bid and ask volume
        bid_volume = sum(float(order.get("size", 0)) * float(order.get("price", 0)) 
                        for order in bids[:10])  # Top 10 levels
        ask_volume = sum(float(order.get("size", 0)) * float(order.get("price", 0)) 
                        for order in asks[:10])
        
        total_volume = bid_volume + ask_volume
        if total_volume == 0:
            return None
        
        imbalance = (bid_volume - ask_volume) / total_volume
        return imbalance
    
    def calculate_depth_imbalance(self, condition_id: str, depth_levels: int = 5) -> Optional[float]:
        """
        Calculate depth imbalance at specific price levels
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        bids = latest.get("bids", [])[:depth_levels]
        asks = latest.get("asks", [])[:depth_levels]
        
        if not bids or not asks:
            return None
        
        bid_count = len(bids)
        ask_count = len(asks)
        total_count = bid_count + ask_count
        
        if total_count == 0:
            return None
        
        return (bid_count - ask_count) / total_count
    
    def detect_large_orders(self, condition_id: str, threshold_multiplier: float = 2.0) -> Dict[str, List]:
        """
        Detect unusually large orders (whale orders)
        Returns: {"bids": [...], "asks": [...]}
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return {"bids": [], "asks": []}
        
        latest = self.orderbook_history[condition_id][-1]
        bids = latest.get("bids", [])
        asks = latest.get("asks", [])
        
        # Calculate average order size
        all_sizes = []
        for order in bids + asks:
            all_sizes.append(float(order.get("size", 0)))
        
        if not all_sizes:
            return {"bids": [], "asks": []}
        
        avg_size = np.mean(all_sizes)
        threshold = avg_size * threshold_multiplier
        
        large_bids = [o for o in bids if float(o.get("size", 0)) > threshold]
        large_asks = [o for o in asks if float(o.get("size", 0)) > threshold]
        
        return {"bids": large_bids, "asks": large_asks}
    
    def calculate_spread(self, condition_id: str) -> Optional[float]:
        """Calculate bid-ask spread"""
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        bids = latest.get("bids", [])
        asks = latest.get("asks", [])
        
        if not bids or not asks:
            return None
        
        best_bid = float(bids[0].get("price", 0))
        best_ask = float(asks[0].get("price", 0))
        
        if best_bid == 0 or best_ask == 0:
            return None
        
        spread = (best_ask - best_bid) / best_bid
        return spread
    
    def calculate_vwap(self, condition_id: str, side: str, target_size: float) -> Optional[Dict]:
        """
        Calculate Volume-Weighted Average Price (VWAP) for a given order size.
        Walks the orderbook to determine realistic execution price.
        
        Args:
            condition_id: Market condition ID
            side: "BUY" or "SELL" (BUY = taking asks, SELL = taking bids)
            target_size: Desired order size in shares
        
        Returns:
            Dict with vwap, total_volume, levels_used, or None if insufficient liquidity
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        
        # BUY = take liquidity from asks, SELL = take liquidity from bids
        orders = latest.get("asks", []) if side.upper() == "BUY" else latest.get("bids", [])
        
        if not orders:
            return None
        
        total_volume = 0.0
        total_cost = 0.0
        levels_used = 0
        
        for order in orders:
            price = float(order.get("price", 0))
            size = float(order.get("size", 0))
            
            if price <= 0 or size <= 0:
                continue
            
            # How much of this level do we need?
            remaining = target_size - total_volume
            volume_from_level = min(size, remaining)
            
            total_volume += volume_from_level
            total_cost += volume_from_level * price
            levels_used += 1
            
            if total_volume >= target_size:
                break
        
        if total_volume == 0:
            return None
        
        vwap = total_cost / total_volume
        
        return {
            "vwap": vwap,
            "total_volume": total_volume,
            "levels_used": levels_used,
            "sufficient_liquidity": total_volume >= target_size,
            "liquidity_shortfall": max(0, target_size - total_volume)
        }
    
    def analyze_liquidity_levels(self, condition_id: str, num_levels: int = 10) -> Optional[Dict]:
        """
        Analyze liquidity distribution across multiple price levels.
        
        Args:
            condition_id: Market condition ID
            num_levels: Number of levels to analyze
        
        Returns:
            Dict with bid/ask level data, gaps, and distribution metrics
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        bids = latest.get("bids", [])[:num_levels]
        asks = latest.get("asks", [])[:num_levels]
        
        if not bids or not asks:
            return None
        
        def analyze_side(orders):
            levels = []
            total_volume = 0.0
            prev_price = None
            gaps = []
            
            for order in orders:
                price = float(order.get("price", 0))
                size = float(order.get("size", 0))
                
                if price <= 0 or size <= 0:
                    continue
                
                total_volume += size
                levels.append({"price": price, "size": size})
                
                # Detect gaps (price jumps larger than tick size)
                if prev_price is not None:
                    gap = abs(price - prev_price)
                    if gap > 0.01:  # 1% gap threshold
                        gaps.append({"from": prev_price, "to": price, "gap": gap})
                
                prev_price = price
            
            # Calculate cumulative volume
            cumulative = []
            cum_vol = 0.0
            for level in levels:
                cum_vol += level["size"]
                cumulative.append(cum_vol)
            
            # Calculate percentage distribution
            if total_volume > 0:
                for i, level in enumerate(levels):
                    level["percentage"] = (level["size"] / total_volume) * 100
                    level["cumulative_percentage"] = (cumulative[i] / total_volume) * 100
            
            return {
                "levels": levels,
                "total_volume": total_volume,
                "gaps": gaps,
                "avg_level_size": total_volume / len(levels) if levels else 0
            }
        
        bid_analysis = analyze_side(bids)
        ask_analysis = analyze_side(asks)
        
        return {
            "bids": bid_analysis,
            "asks": ask_analysis,
            "bid_ask_volume_ratio": (bid_analysis["total_volume"] / ask_analysis["total_volume"] 
                                     if ask_analysis["total_volume"] > 0 else 0)
        }
    
    def estimate_slippage(self, condition_id: str, side: str, order_size: float) -> Optional[Dict]:
        """
        Estimate price impact and slippage for a given order size.
        
        Args:
            condition_id: Market condition ID
            side: "BUY" or "SELL"
            order_size: Order size in shares
        
        Returns:
            Dict with slippage metrics or None
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        orders = latest.get("asks", []) if side.upper() == "BUY" else latest.get("bids", [])
        
        if not orders:
            return None
        
        best_price = float(orders[0].get("price", 0))
        if best_price <= 0:
            return None
        
        # Calculate VWAP for the order
        vwap_data = self.calculate_vwap(condition_id, side, order_size)
        
        if not vwap_data or not vwap_data["sufficient_liquidity"]:
            return {
                "sufficient_liquidity": False,
                "available_volume": vwap_data["total_volume"] if vwap_data else 0,
                "requested_volume": order_size
            }
        
        vwap = vwap_data["vwap"]
        
        # Calculate slippage as difference between VWAP and best price
        slippage_absolute = vwap - best_price if side.upper() == "BUY" else best_price - vwap
        slippage_percentage = (slippage_absolute / best_price) if best_price > 0 else 0
        
        # Find worst execution price (last level used)
        worst_price = best_price
        total_volume = 0.0
        for order in orders:
            price = float(order.get("price", 0))
            size = float(order.get("size", 0))
            total_volume += size
            if total_volume >= order_size:
                worst_price = price
                break
            worst_price = price
        
        return {
            "sufficient_liquidity": True,
            "best_price": best_price,
            "vwap": vwap,
            "worst_price": worst_price,
            "slippage_absolute": slippage_absolute,
            "slippage_percentage": slippage_percentage,
            "levels_used": vwap_data["levels_used"],
            "price_impact_bps": slippage_percentage * 10000  # Basis points
        }
    
    def detect_support_resistance(self, condition_id: str, threshold_multiplier: float = 1.5) -> Optional[Dict]:
        """
        Detect support and resistance levels based on orderbook concentration.
        Identifies "walls" or significant liquidity clusters.
        
        Args:
            condition_id: Market condition ID
            threshold_multiplier: Multiplier for average size to detect significant levels
        
        Returns:
            Dict with support (bid) and resistance (ask) levels
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        bids = latest.get("bids", [])
        asks = latest.get("asks", [])
        
        if not bids or not asks:
            return None
        
        def find_significant_levels(orders, is_bid=True):
            if not orders:
                return []
            
            # Calculate average order size
            sizes = [float(o.get("size", 0)) for o in orders if float(o.get("size", 0)) > 0]
            if not sizes:
                return []
            
            avg_size = np.mean(sizes)
            threshold = avg_size * threshold_multiplier
            
            significant_levels = []
            
            # Find orders above threshold
            for order in orders:
                price = float(order.get("price", 0))
                size = float(order.get("size", 0))
                
                if size >= threshold and price > 0:
                    significant_levels.append({
                        "price": price,
                        "size": size,
                        "size_vs_avg": size / avg_size if avg_size > 0 else 0,
                        "type": "support" if is_bid else "resistance"
                    })
            
            # Sort by size (largest first)
            significant_levels.sort(key=lambda x: x["size"], reverse=True)
            
            return significant_levels
        
        support_levels = find_significant_levels(bids, is_bid=True)
        resistance_levels = find_significant_levels(asks, is_bid=False)
        
        return {
            "support": support_levels[:5],  # Top 5 support levels
            "resistance": resistance_levels[:5],  # Top 5 resistance levels
            "total_support_volume": sum(s["size"] for s in support_levels),
            "total_resistance_volume": sum(r["size"] for r in resistance_levels)
        }
    
    def get_cumulative_depth(self, condition_id: str, max_levels: int = 20) -> Optional[Dict]:
        """
        Build cumulative depth profile for orderbook visualization.
        
        Args:
            condition_id: Market condition ID
            max_levels: Maximum number of levels to include
        
        Returns:
            Dict with cumulative bid/ask depth arrays
        """
        if condition_id not in self.orderbook_history or not self.orderbook_history[condition_id]:
            return None
        
        latest = self.orderbook_history[condition_id][-1]
        bids = latest.get("bids", [])[:max_levels]
        asks = latest.get("asks", [])[:max_levels]
        
        if not bids or not asks:
            return None
        
        def build_cumulative(orders):
            cumulative_depth = []
            total_volume = 0.0
            
            for order in orders:
                price = float(order.get("price", 0))
                size = float(order.get("size", 0))
                
                if price <= 0 or size <= 0:
                    continue
                
                total_volume += size
                cumulative_depth.append({
                    "price": price,
                    "size": size,
                    "cumulative_volume": total_volume
                })
            
            return cumulative_depth
        
        bid_depth = build_cumulative(bids)
        ask_depth = build_cumulative(asks)
        
        # Calculate mid price
        best_bid = float(bids[0].get("price", 0)) if bids else 0
        best_ask = float(asks[0].get("price", 0)) if asks else 0
        mid_price = (best_bid + best_ask) / 2 if (best_bid > 0 and best_ask > 0) else 0
        
        return {
            "bid_depth": bid_depth,
            "ask_depth": ask_depth,
            "mid_price": mid_price,
            "total_bid_volume": bid_depth[-1]["cumulative_volume"] if bid_depth else 0,
            "total_ask_volume": ask_depth[-1]["cumulative_volume"] if ask_depth else 0
        }
    
    def get_orderbook_metrics(self, condition_id: str) -> Dict:
        """Get comprehensive order book metrics"""
        return {
            "imbalance": self.calculate_order_imbalance(condition_id),
            "depth_imbalance": self.calculate_depth_imbalance(condition_id),
            "spread": self.calculate_spread(condition_id),
            "large_orders": self.detect_large_orders(condition_id),
            "market_depth": self.calculate_market_depth(condition_id),
            "liquidity_levels": self.analyze_liquidity_levels(condition_id),
            "support_resistance": self.detect_support_resistance(condition_id),
            "cumulative_depth": self.get_cumulative_depth(condition_id)
        }







