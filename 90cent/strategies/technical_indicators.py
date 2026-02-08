"""
Technical indicator-based trading strategy
Uses RSI, Moving Averages, Bollinger Bands, etc.
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class TechnicalIndicatorsStrategy:
    """Technical analysis-based trading strategy"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.ma_short = config.get("ma_short", 9)
        self.ma_long = config.get("ma_long", 21)
        self.bollinger_period = config.get("bollinger_period", 20)
        self.bollinger_std = config.get("bollinger_std", 2)
        self.price_history: Dict[str, List[float]] = {}
    
    def update_price(self, condition_id: str, price: float):
        """Update price history"""
        if condition_id not in self.price_history:
            self.price_history[condition_id] = []
        
        self.price_history[condition_id].append(price)
        
        # Keep only recent history
        max_history = max(self.bollinger_period, self.ma_long) * 2
        if len(self.price_history[condition_id]) > max_history:
            self.price_history[condition_id] = self.price_history[condition_id][-max_history:]
    
    def calculate_rsi(self, condition_id: str) -> Optional[float]:
        """Calculate Relative Strength Index"""
        if condition_id not in self.price_history:
            return None
        
        prices = pd.Series(self.price_history[condition_id])
        if len(prices) < self.rsi_period + 1:
            return None
        
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.rsi_period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.rsi_period).mean()
        
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        
        return rsi.iloc[-1]
    
    def calculate_moving_averages(self, condition_id: str) -> Optional[Tuple[float, float]]:
        """Calculate short and long moving averages"""
        if condition_id not in self.price_history:
            return None
        
        prices = pd.Series(self.price_history[condition_id])
        
        if len(prices) < self.ma_long:
            return None
        
        ma_short = prices.rolling(window=self.ma_short).mean().iloc[-1]
        ma_long = prices.rolling(window=self.ma_long).mean().iloc[-1]
        
        return (ma_short, ma_long)
    
    def calculate_bollinger_bands(self, condition_id: str) -> Optional[Tuple[float, float, float]]:
        """Calculate Bollinger Bands"""
        if condition_id not in self.price_history:
            return None
        
        prices = pd.Series(self.price_history[condition_id])
        
        if len(prices) < self.bollinger_period:
            return None
        
        sma = prices.rolling(window=self.bollinger_period).mean().iloc[-1]
        std = prices.rolling(window=self.bollinger_period).std().iloc[-1]
        
        upper_band = sma + (self.bollinger_std * std)
        lower_band = sma - (self.bollinger_std * std)
        
        return (upper_band, sma, lower_band)
    
    def detect_signal(self, condition_id: str, current_price: float) -> Optional[Tuple[str, float]]:
        """
        Detect trading signal based on technical indicators
        Returns: (side, confidence)
        """
        rsi = self.calculate_rsi(condition_id)
        mas = self.calculate_moving_averages(condition_id)
        bb = self.calculate_bollinger_bands(condition_id)
        
        signals = []
        confidences = []
        
        # RSI signals
        if rsi is not None:
            if rsi < self.rsi_oversold:
                signals.append("YES")
                confidence = (self.rsi_oversold - rsi) / self.rsi_oversold
                confidences.append(confidence)
            elif rsi > self.rsi_overbought:
                signals.append("NO")
                confidence = (rsi - self.rsi_overbought) / (100 - self.rsi_overbought)
                confidences.append(confidence)
        
        # Moving average crossover
        if mas is not None:
            ma_short, ma_long = mas
            if ma_short > ma_long:
                signals.append("YES")
                confidence = abs(ma_short - ma_long) / ma_long
                confidences.append(min(confidence, 1.0))
            elif ma_short < ma_long:
                signals.append("NO")
                confidence = abs(ma_short - ma_long) / ma_long
                confidences.append(min(confidence, 1.0))
        
        # Bollinger Bands
        if bb is not None:
            upper, middle, lower = bb
            if current_price < lower:
                signals.append("YES")
                confidence = (lower - current_price) / (middle - lower)
                confidences.append(min(confidence, 1.0))
            elif current_price > upper:
                signals.append("NO")
                confidence = (current_price - upper) / (upper - middle)
                confidences.append(min(confidence, 1.0))
        
        # Aggregate signals
        if not signals:
            return None
        
        # Count signals for each side
        yes_count = signals.count("YES")
        no_count = signals.count("NO")
        
        if yes_count > no_count:
            avg_confidence = np.mean([c for s, c in zip(signals, confidences) if s == "YES"])
            return ("YES", avg_confidence)
        elif no_count > yes_count:
            avg_confidence = np.mean([c for s, c in zip(signals, confidences) if s == "NO"])
            return ("NO", avg_confidence)
        
        return None
    
    def get_optimal_entry_price(self, condition_id: str, side: str, 
                                current_price: float, spread: float) -> float:
        """Calculate optimal entry price"""
        if side == "YES":
            return current_price * (1 - spread)
        else:
            return current_price * (1 + spread)

