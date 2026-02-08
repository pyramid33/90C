"""
Volume Profile Analysis
VWAP, volume clusters, volume delta, and acceleration
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import logging

logger = logging.getLogger(__name__)


class VolumeProfileAnalyzer:
    """Analyzes volume patterns for 15-minute predictions"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.price_volume_data: Dict[str, deque] = {}  # Price-volume pairs
        self.trade_history: Dict[str, List[Dict]] = {}  # Trade history
        self.max_history = 1000  # Keep last 1000 data points
        self.vwap_windows = [15, 60, 240]  # 15min, 1h, 4h VWAP
        
    def update_trade(self, condition_id: str, price: float, volume: float, side: str = None):
        """Update with new trade data"""
        if condition_id not in self.price_volume_data:
            self.price_volume_data[condition_id] = deque(maxlen=self.max_history)
            self.trade_history[condition_id] = []
        
        timestamp = datetime.now()
        self.price_volume_data[condition_id].append({
            "timestamp": timestamp,
            "price": price,
            "volume": volume,
            "side": side  # "buy" or "sell" if available
        })
        
        self.trade_history[condition_id].append({
            "timestamp": timestamp,
            "price": price,
            "volume": volume,
            "side": side
        })
        
        # Keep only recent history
        if len(self.trade_history[condition_id]) > self.max_history:
            self.trade_history[condition_id] = self.trade_history[condition_id][-self.max_history:]
    
    def calculate_vwap(self, condition_id: str, window_minutes: int = 15) -> Optional[float]:
        """
        Calculate Volume-Weighted Average Price
        """
        if condition_id not in self.price_volume_data or not self.price_volume_data[condition_id]:
            return None
        
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        recent_data = [d for d in self.price_volume_data[condition_id] 
                      if d["timestamp"] >= cutoff_time]
        
        if not recent_data:
            return None
        
        total_volume = sum(d["volume"] for d in recent_data)
        if total_volume == 0:
            return None
        
        vwap = sum(d["price"] * d["volume"] for d in recent_data) / total_volume
        return vwap
    
    def find_volume_clusters(self, condition_id: str, window_minutes: int = 15, 
                           price_bins: int = 20) -> Optional[Dict]:
        """
        Find price levels with high volume (support/resistance zones)
        """
        if condition_id not in self.price_volume_data or not self.price_volume_data[condition_id]:
            return None
        
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        recent_data = [d for d in self.price_volume_data[condition_id] 
                      if d["timestamp"] >= cutoff_time]
        
        if not recent_data:
            return None
        
        prices = [d["price"] for d in recent_data]
        volumes = [d["volume"] for d in recent_data]
        
        if not prices:
            return None
        
        # Create price bins
        min_price = min(prices)
        max_price = max(prices)
        price_range = max_price - min_price
        
        if price_range == 0:
            return None
        
        bins = np.linspace(min_price, max_price, price_bins + 1)
        bin_volumes = np.zeros(price_bins)
        
        for price, volume in zip(prices, volumes):
            bin_idx = np.digitize(price, bins) - 1
            bin_idx = max(0, min(bin_idx, price_bins - 1))
            bin_volumes[bin_idx] += volume
        
        # Find clusters (bins with above-average volume)
        avg_volume = np.mean(bin_volumes)
        std_volume = np.std(bin_volumes)
        threshold = avg_volume + std_volume
        
        clusters = []
        for i, vol in enumerate(bin_volumes):
            if vol > threshold:
                cluster_price = (bins[i] + bins[i+1]) / 2
                clusters.append({
                    "price": cluster_price,
                    "volume": vol,
                    "strength": vol / avg_volume if avg_volume > 0 else 1
                })
        
        # Sort by volume
        clusters.sort(key=lambda x: x["volume"], reverse=True)
        
        return {
            "clusters": clusters[:5],  # Top 5 clusters
            "current_price": prices[-1] if prices else None,
            "nearest_cluster": self._find_nearest_cluster(clusters, prices[-1] if prices else None)
        }
    
    def _find_nearest_cluster(self, clusters: List[Dict], current_price: float) -> Optional[Dict]:
        """Find nearest volume cluster to current price"""
        if not clusters or current_price is None:
            return None
        
        nearest = min(clusters, key=lambda c: abs(c["price"] - current_price))
        return nearest
    
    def calculate_volume_delta(self, condition_id: str, window_minutes: int = 15) -> Optional[Dict]:
        """
        Calculate buy vs sell volume delta
        """
        if condition_id not in self.trade_history or not self.trade_history[condition_id]:
            return None
        
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        recent_trades = [t for t in self.trade_history[condition_id] 
                        if t["timestamp"] >= cutoff_time]
        
        if not recent_trades:
            return None
        
        buy_volume = sum(t["volume"] for t in recent_trades if t.get("side") == "buy")
        sell_volume = sum(t["volume"] for t in recent_trades if t.get("side") == "sell")
        total_volume = buy_volume + sell_volume
        
        if total_volume == 0:
            # Estimate from price movement if side not available
            prices = [t["price"] for t in recent_trades]
            if len(prices) > 1:
                price_change = prices[-1] - prices[0]
                # Assume positive change = more buying
                if price_change > 0:
                    buy_volume = total_volume * 0.6
                    sell_volume = total_volume * 0.4
                else:
                    buy_volume = total_volume * 0.4
                    sell_volume = total_volume * 0.6
            else:
                return None
        
        delta = buy_volume - sell_volume
        delta_percentage = (delta / total_volume * 100) if total_volume > 0 else 0
        
        return {
            "buy_volume": buy_volume,
            "sell_volume": sell_volume,
            "total_volume": total_volume,
            "delta": delta,
            "delta_percentage": delta_percentage,
            "buy_ratio": buy_volume / total_volume if total_volume > 0 else 0.5
        }
    
    def calculate_volume_acceleration(self, condition_id: str) -> Optional[float]:
        """
        Calculate rate of change in volume (acceleration)
        Positive = volume increasing, Negative = volume decreasing
        """
        if condition_id not in self.price_volume_data or len(self.price_volume_data[condition_id]) < 10:
            return None
        
        recent = list(self.price_volume_data[condition_id])[-10:]
        
        # Calculate volume in first half vs second half
        first_half = sum(d["volume"] for d in recent[:5])
        second_half = sum(d["volume"] for d in recent[5:])
        
        if first_half == 0:
            return None
        
        acceleration = (second_half - first_half) / first_half
        return acceleration
    
    def detect_signal(self, condition_id: str, current_price: float) -> Optional[Tuple[str, float]]:
        """
        Detect trading signal from volume profile
        Returns: (side, confidence)
        """
        vwap_15m = self.calculate_vwap(condition_id, 15)
        volume_delta = self.calculate_volume_delta(condition_id, 15)
        volume_acceleration = self.calculate_volume_acceleration(condition_id)
        clusters = self.find_volume_clusters(condition_id, 15)
        
        signals = []
        confidences = []
        
        # VWAP signal: price above VWAP = bullish, below = bearish
        if vwap_15m:
            if current_price > vwap_15m * 1.001:  # 0.1% above VWAP
                signals.append("YES")
                confidence = min((current_price - vwap_15m) / vwap_15m * 10, 1.0)
                confidences.append(confidence)
            elif current_price < vwap_15m * 0.999:  # 0.1% below VWAP
                signals.append("NO")
                confidence = min((vwap_15m - current_price) / vwap_15m * 10, 1.0)
                confidences.append(confidence)
        
        # Volume delta signal
        if volume_delta:
            if volume_delta["delta_percentage"] > 10:  # 10% more buying
                signals.append("YES")
                confidences.append(min(volume_delta["delta_percentage"] / 50, 1.0))
            elif volume_delta["delta_percentage"] < -10:  # 10% more selling
                signals.append("NO")
                confidences.append(min(abs(volume_delta["delta_percentage"]) / 50, 1.0))
        
        # Volume acceleration signal
        if volume_acceleration:
            if volume_acceleration > 0.2:  # 20% volume increase
                # Combine with price direction
                if current_price > (vwap_15m if vwap_15m else current_price):
                    signals.append("YES")
                    confidences.append(0.6)
            elif volume_acceleration < -0.2:  # 20% volume decrease
                if current_price < (vwap_15m if vwap_15m else current_price):
                    signals.append("NO")
                    confidences.append(0.6)
        
        # Volume cluster support/resistance
        if clusters and clusters.get("nearest_cluster"):
            cluster = clusters["nearest_cluster"]
            price_diff = abs(current_price - cluster["price"]) / cluster["price"]
            if price_diff < 0.002:  # Within 0.2% of cluster
                # Price at support/resistance level
                if current_price < cluster["price"]:
                    signals.append("YES")  # Bounce from support
                    confidences.append(0.5)
                elif current_price > cluster["price"]:
                    signals.append("NO")  # Rejection from resistance
                    confidences.append(0.5)
        
        if not signals:
            return None
        
        # Aggregate signals
        yes_count = signals.count("YES")
        no_count = signals.count("NO")
        
        yes_conf = [c for s, c in zip(signals, confidences) if s == "YES"]
        no_conf = [c for s, c in zip(signals, confidences) if s == "NO"]
        
        if yes_count > no_count and yes_conf:
            avg_confidence = float(np.mean(yes_conf))
            return ("YES", avg_confidence)
        elif no_count > yes_count and no_conf:
            avg_confidence = float(np.mean(no_conf))
            return ("NO", avg_confidence)
        
        return None







