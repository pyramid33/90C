"""
Micro-Order Flow Analysis
Tracks order book velocity, cancellations, hidden orders, and momentum
"""
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import logging

logger = logging.getLogger(__name__)


class OrderFlowAnalyzer:
    """Analyzes micro-order flow patterns for 15-minute predictions"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.orderbook_snapshots: Dict[str, deque] = {}  # Recent snapshots
        self.order_changes: Dict[str, List[Dict]] = {}  # Track order changes
        self.max_snapshots = 60  # Keep last 60 seconds
        self.max_history = 300  # 5 minutes of history
        
    def update_orderbook(self, condition_id: str, orderbook: Dict):
        """Update order book and track changes"""
        if condition_id not in self.orderbook_snapshots:
            self.orderbook_snapshots[condition_id] = deque(maxlen=self.max_snapshots)
            self.order_changes[condition_id] = []
        
        current_time = datetime.now()
        snapshot = {
            "timestamp": current_time,
            "bids": {order.get("id", i): order for i, order in enumerate(orderbook.get("bids", []))},
            "asks": {order.get("id", i): order for i, order in enumerate(orderbook.get("asks", []))},
            "bid_volume": sum(float(o.get("size", 0)) for o in orderbook.get("bids", [])),
            "ask_volume": sum(float(o.get("size", 0)) for o in orderbook.get("asks", [])),
            "best_bid": float(orderbook.get("bids", [{}])[0].get("price", 0)) if orderbook.get("bids") else 0,
            "best_ask": float(orderbook.get("asks", [{}])[0].get("price", 0)) if orderbook.get("asks") else 0,
        }
        
        # Detect changes if we have previous snapshot
        if len(self.orderbook_snapshots[condition_id]) > 0:
            prev_snapshot = self.orderbook_snapshots[condition_id][-1]
            changes = self._detect_changes(prev_snapshot, snapshot)
            if changes:
                self.order_changes[condition_id].append({
                    "timestamp": current_time,
                    "changes": changes
                })
                # Keep only recent history
                if len(self.order_changes[condition_id]) > self.max_history:
                    self.order_changes[condition_id] = self.order_changes[condition_id][-self.max_history:]
        
        self.orderbook_snapshots[condition_id].append(snapshot)
    
    def _detect_changes(self, prev: Dict, curr: Dict) -> Dict:
        """Detect changes between snapshots"""
        changes = {
            "bids_added": 0,
            "bids_removed": 0,
            "asks_added": 0,
            "asks_removed": 0,
            "bids_modified": 0,
            "asks_modified": 0,
            "volume_change_bid": curr["bid_volume"] - prev["bid_volume"],
            "volume_change_ask": curr["ask_volume"] - prev["ask_volume"]
        }
        
        # Detect new/removed orders
        prev_bid_ids = set(prev["bids"].keys())
        curr_bid_ids = set(curr["bids"].keys())
        changes["bids_added"] = len(curr_bid_ids - prev_bid_ids)
        changes["bids_removed"] = len(prev_bid_ids - curr_bid_ids)
        
        prev_ask_ids = set(prev["asks"].keys())
        curr_ask_ids = set(curr["asks"].keys())
        changes["asks_added"] = len(curr_ask_ids - prev_ask_ids)
        changes["asks_removed"] = len(prev_ask_ids - curr_ask_ids)
        
        # Detect modified orders (same ID, different size/price)
        common_bids = prev_bid_ids & curr_bid_ids
        for bid_id in common_bids:
            if prev["bids"][bid_id] != curr["bids"][bid_id]:
                changes["bids_modified"] += 1
        
        common_asks = prev_ask_ids & curr_ask_ids
        for ask_id in common_asks:
            if prev["asks"][ask_id] != curr["asks"][ask_id]:
                changes["asks_modified"] += 1
        
        return changes
    
    def calculate_order_flow_velocity(self, condition_id: str, window_seconds: int = 30) -> Optional[Dict]:
        """
        Calculate order flow velocity (orders per second)
        Returns: velocity metrics
        """
        if condition_id not in self.order_changes or not self.order_changes[condition_id]:
            return None
        
        cutoff_time = datetime.now() - timedelta(seconds=window_seconds)
        recent_changes = [c for c in self.order_changes[condition_id] 
                         if c["timestamp"] >= cutoff_time]
        
        if not recent_changes:
            return None
        
        total_bid_changes = sum(c["changes"]["bids_added"] + c["changes"]["bids_removed"] 
                               for c in recent_changes)
        total_ask_changes = sum(c["changes"]["asks_added"] + c["changes"]["asks_removed"] 
                               for c in recent_changes)
        
        velocity = {
            "bid_velocity": total_bid_changes / window_seconds,
            "ask_velocity": total_ask_changes / window_seconds,
            "total_velocity": (total_bid_changes + total_ask_changes) / window_seconds,
            "imbalance_velocity": (total_bid_changes - total_ask_changes) / window_seconds
        }
        
        return velocity
    
    def calculate_cancellation_rate(self, condition_id: str, window_seconds: int = 30) -> Optional[float]:
        """
        Calculate order cancellation rate
        High cancellation rate = fake liquidity / market making
        """
        if condition_id not in self.order_changes or not self.order_changes[condition_id]:
            return None
        
        cutoff_time = datetime.now() - timedelta(seconds=window_seconds)
        recent_changes = [c for c in self.order_changes[condition_id] 
                         if c["timestamp"] >= cutoff_time]
        
        if not recent_changes:
            return None
        
        total_removed = sum(c["changes"]["bids_removed"] + c["changes"]["asks_removed"] 
                           for c in recent_changes)
        total_added = sum(c["changes"]["bids_added"] + c["changes"]["asks_added"] 
                         for c in recent_changes)
        
        if total_added == 0:
            return None
        
        cancellation_rate = total_removed / (total_added + total_removed) if (total_added + total_removed) > 0 else 0
        return cancellation_rate
    
    def detect_hidden_orders(self, condition_id: str) -> Optional[Dict]:
        """
        Detect hidden/large orders split into smaller ones
        Looks for patterns of multiple small orders at similar prices
        """
        if condition_id not in self.orderbook_snapshots or not self.orderbook_snapshots[condition_id]:
            return None
        
        latest = self.orderbook_snapshots[condition_id][-1]
        bids = list(latest["bids"].values())
        asks = list(latest["asks"].values())
        
        # Group orders by price (within 0.1% of each other)
        def group_by_price(orders, side):
            groups = {}
            for order in orders:
                price = float(order.get("price", 0))
                if price == 0:
                    continue
                
                # Find group within 0.1%
                found_group = False
                for group_price in groups.keys():
                    if abs(price - group_price) / group_price < 0.001:
                        groups[group_price].append(order)
                        found_group = True
                        break
                
                if not found_group:
                    groups[price] = [order]
            
            # Find suspicious groups (multiple orders, similar sizes)
            suspicious = []
            for price, group_orders in groups.items():
                if len(group_orders) >= 3:  # 3+ orders at same price
                    total_size = sum(float(o.get("size", 0)) for o in group_orders)
                    avg_size = total_size / len(group_orders)
                    # Check if sizes are similar (within 20%)
                    sizes = [float(o.get("size", 0)) for o in group_orders]
                    if all(abs(s - avg_size) / avg_size < 0.2 for s in sizes if avg_size > 0):
                        suspicious.append({
                            "price": price,
                            "count": len(group_orders),
                            "total_size": total_size,
                            "side": side
                        })
            
            return suspicious
        
        hidden_bids = group_by_price(bids, "bid")
        hidden_asks = group_by_price(asks, "ask")
        
        return {
            "hidden_bids": hidden_bids,
            "hidden_asks": hidden_asks,
            "has_hidden_orders": len(hidden_bids) > 0 or len(hidden_asks) > 0
        }
    
    def calculate_order_book_momentum(self, condition_id: str, window_seconds: int = 15) -> Optional[Dict]:
        """
        Calculate order book momentum (rate of change in depth)
        """
        if condition_id not in self.orderbook_snapshots or len(self.orderbook_snapshots[condition_id]) < 2:
            return None
        
        cutoff_time = datetime.now() - timedelta(seconds=window_seconds)
        recent_snapshots = [s for s in self.orderbook_snapshots[condition_id] 
                           if s["timestamp"] >= cutoff_time]
        
        if len(recent_snapshots) < 2:
            return None
        
        first = recent_snapshots[0]
        last = recent_snapshots[-1]
        time_diff = (last["timestamp"] - first["timestamp"]).total_seconds()
        
        if time_diff == 0:
            return None
        
        bid_momentum = (last["bid_volume"] - first["bid_volume"]) / time_diff
        ask_momentum = (last["ask_volume"] - first["ask_volume"]) / time_diff
        
        return {
            "bid_momentum": bid_momentum,
            "ask_momentum": ask_momentum,
            "net_momentum": bid_momentum - ask_momentum,
            "momentum_ratio": bid_momentum / ask_momentum if ask_momentum != 0 else None
        }
    
    def detect_signal(self, condition_id: str) -> Optional[Tuple[str, float]]:
        """
        Detect trading signal from order flow analysis
        Returns: (side, confidence)
        """
        velocity = self.calculate_order_flow_velocity(condition_id)
        cancellation_rate = self.calculate_cancellation_rate(condition_id)
        momentum = self.calculate_order_book_momentum(condition_id)
        hidden_orders = self.detect_hidden_orders(condition_id)
        
        signals = []
        confidences = []
        
        # High bid velocity with low cancellation = strong buying interest
        if velocity and velocity["imbalance_velocity"] > 2:  # 2+ more bid orders per second
            if cancellation_rate and cancellation_rate < 0.5:  # Low cancellation = real interest
                signals.append("YES")
                confidence = min(abs(velocity["imbalance_velocity"]) / 5, 1.0)
                confidences.append(confidence)
        
        # High ask velocity with low cancellation = strong selling interest
        if velocity and velocity["imbalance_velocity"] < -2:
            if cancellation_rate and cancellation_rate < 0.5:
                signals.append("NO")
                confidence = min(abs(velocity["imbalance_velocity"]) / 5, 1.0)
                confidences.append(confidence)
        
        # Order book momentum
        if momentum and momentum["net_momentum"]:
            if momentum["net_momentum"] > 10:  # Significant bid momentum
                signals.append("YES")
                confidences.append(0.6)
            elif momentum["net_momentum"] < -10:  # Significant ask momentum
                signals.append("NO")
                confidences.append(0.6)
        
        # Hidden large orders (whale activity)
        if hidden_orders and hidden_orders["has_hidden_orders"]:
            if len(hidden_orders["hidden_bids"]) > len(hidden_orders["hidden_asks"]):
                signals.append("YES")
                confidences.append(0.5)
            elif len(hidden_orders["hidden_asks"]) > len(hidden_orders["hidden_bids"]):
                signals.append("NO")
                confidences.append(0.5)
        
        if not signals:
            return None
        
        # Aggregate signals
        yes_count = signals.count("YES")
        no_count = signals.count("NO")
        
        if yes_count > no_count:
            avg_confidence = np.mean([c for s, c in zip(signals, confidences) if s == "YES"])
            return ("YES", avg_confidence)
        elif no_count > yes_count:
            avg_confidence = np.mean([c for s, c in zip(signals, confidences) if s == "NO"])
            return ("NO", avg_confidence)
        
        return None
















