"""
Momentum-based trading strategy
Detects momentum shifts and places orders accordingly
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class MomentumStrategy:
    """Momentum detection and trading strategy"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.lookback_periods = config.get("lookback_periods", 5)
        self.momentum_threshold = config.get("momentum_threshold", 0.02)
        self.volume_threshold = config.get("volume_threshold", 1.5)
        self.price_history: Dict[str, List[float]] = {}
        self.volume_history: Dict[str, List[float]] = {}
    
    def update_price(self, condition_id: str, price: float, volume: float):
        """Update price and volume history"""
        if condition_id not in self.price_history:
            self.price_history[condition_id] = []
            self.volume_history[condition_id] = []
        
        self.price_history[condition_id].append(price)
        self.volume_history[condition_id].append(volume)
        
        # Keep only recent history
        max_history = self.lookback_periods * 2
        if len(self.price_history[condition_id]) > max_history:
            self.price_history[condition_id] = self.price_history[condition_id][-max_history:]
            self.volume_history[condition_id] = self.volume_history[condition_id][-max_history:]
    
    def calculate_momentum(self, condition_id: str) -> Optional[float]:
        """Calculate momentum indicator"""
        if condition_id not in self.price_history:
            return None
        
        prices = self.price_history[condition_id]
        if len(prices) < self.lookback_periods:
            return None
        
        # Calculate rate of change
        current_price = prices[-1]
        past_price = prices[-self.lookback_periods]
        momentum = (current_price - past_price) / past_price
        
        return momentum
    
    def calculate_volume_momentum(self, condition_id: str) -> Optional[float]:
        """Calculate volume momentum"""
        if condition_id not in self.volume_history:
            return None
        
        volumes = self.volume_history[condition_id]
        if len(volumes) < self.lookback_periods or self.lookback_periods < 2:
            return None
        
        current_volume = volumes[-1]
        prev_window = volumes[-self.lookback_periods:-1]
        if not prev_window:
            return None
        avg_volume = np.mean(prev_window)
        
        if avg_volume == 0:
            return None
        
        volume_ratio = current_volume / avg_volume
        return volume_ratio
    
    def detect_signal(self, condition_id: str) -> Optional[Tuple[str, float]]:
        """
        Detect trading signal based on momentum
        Returns: (side, confidence) where side is "YES" or "NO"
        """
        momentum = self.calculate_momentum(condition_id)
        volume_ratio = self.calculate_volume_momentum(condition_id)
        
        if momentum is None or volume_ratio is None:
            return None
        
        # Check if volume spike confirms momentum
        if volume_ratio < self.volume_threshold:
            return None
        
        # Strong upward momentum
        if momentum > self.momentum_threshold:
            confidence = min(abs(momentum) / self.momentum_threshold, 1.0)
            return ("YES", confidence)
        
        # Strong downward momentum
        elif momentum < -self.momentum_threshold:
            confidence = min(abs(momentum) / self.momentum_threshold, 1.0)
            return ("NO", confidence)
        
        return None
    
    def get_optimal_entry_price(self, condition_id: str, side: str, 
                                current_price: float, spread: float) -> float:
        """Calculate optimal entry price with spread"""
        if side == "YES":
            # For YES, buy slightly below current price
            return current_price * (1 - spread)
        else:
            # For NO, buy slightly above current price
            return current_price * (1 + spread)

