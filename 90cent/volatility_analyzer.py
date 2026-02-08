"""
Real-Time Volatility Measurement and Regime Detection
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import logging

logger = logging.getLogger(__name__)


class VolatilityAnalyzer:
    """Analyzes real-time volatility for spread optimization and risk management"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.price_history: Dict[str, deque] = {}
        self.returns_history: Dict[str, deque] = {}
        self.max_history = 500
        
    def update_price(self, condition_id: str, price: float):
        """Update price and calculate returns"""
        if condition_id not in self.price_history:
            self.price_history[condition_id] = deque(maxlen=self.max_history)
            self.returns_history[condition_id] = deque(maxlen=self.max_history)
        
        prev_price = self.price_history[condition_id][-1] if self.price_history[condition_id] else price
        
        self.price_history[condition_id].append(price)
        
        # Calculate return
        if prev_price > 0:
            returns = (price - prev_price) / prev_price
            self.returns_history[condition_id].append(returns)
    
    def calculate_realized_volatility(self, condition_id: str, window_minutes: int = 15) -> Optional[float]:
        """
        Calculate realized volatility (standard deviation of returns)
        """
        if condition_id not in self.returns_history or not self.returns_history[condition_id]:
            return None
        
        # Get recent returns
        recent_returns = list(self.returns_history[condition_id])[-window_minutes:]
        
        if len(recent_returns) < 5:
            return None
        
        # Annualized volatility (assuming 1-minute returns)
        volatility = np.std(recent_returns) * np.sqrt(525600)  # Minutes in a year
        
        return volatility
    
    def calculate_rolling_volatility(self, condition_id: str, window: int = 20) -> Optional[float]:
        """Calculate rolling volatility"""
        if condition_id not in self.returns_history or len(self.returns_history[condition_id]) < window:
            return None
        
        recent_returns = list(self.returns_history[condition_id])[-window:]
        volatility = np.std(recent_returns) * np.sqrt(525600)
        
        return volatility
    
    def detect_volatility_regime(self, condition_id: str) -> Optional[str]:
        """
        Detect volatility regime: low, medium, high
        """
        short_vol = self.calculate_rolling_volatility(condition_id, 10)
        long_vol = self.calculate_rolling_volatility(condition_id, 50)
        
        if short_vol is None or long_vol is None or long_vol == 0:
            return None

        vol_ratio = short_vol / long_vol
        
        if vol_ratio > 1.5:
            return "high"
        elif vol_ratio < 0.7:
            return "low"
        else:
            return "medium"
    
    def calculate_volatility_clustering(self, condition_id: str) -> Optional[float]:
        """
        Measure volatility clustering (high vol tends to follow high vol)
        """
        if condition_id not in self.returns_history or len(self.returns_history[condition_id]) < 20:
            return None
        
        returns = [r for r in self.returns_history[condition_id] if np.isfinite(r)][-20:]
        if len(returns) < 20:
            return None

        abs_returns = [abs(r) for r in returns]
        if len(abs_returns) < 2:
            return None

        mean_abs = np.mean(abs_returns)
        if not np.isfinite(mean_abs) or mean_abs == 0:
            return None

        autocorr = np.corrcoef(abs_returns[:-1], abs_returns[1:])[0, 1]
        
        return autocorr if np.isfinite(autocorr) else None
    
    def forecast_volatility(self, condition_id: str) -> Optional[float]:
        """
        Simple volatility forecast using GARCH-like approach
        """
        if condition_id not in self.returns_history or len(self.returns_history[condition_id]) < 10:
            return None
        
        returns = [r for r in self.returns_history[condition_id] if np.isfinite(r)][-20:]
        if len(returns) < 10:
            return None
        
        alpha = 0.1  # Decay factor
        vol_forecast = np.std(returns[-10:]) if returns[-10:] else 0.0
        
        for ret in returns[-10:]:
            vol_forecast = alpha * abs(ret) + (1 - alpha) * vol_forecast
        
        return vol_forecast * np.sqrt(525600) if vol_forecast > 0 else None
    
    def get_volatility_metrics(self, condition_id: str) -> Dict:
        """Get comprehensive volatility metrics"""
        return {
            "realized_vol_15m": self.calculate_realized_volatility(condition_id, 15),
            "realized_vol_1h": self.calculate_realized_volatility(condition_id, 60),
            "rolling_vol_short": self.calculate_rolling_volatility(condition_id, 10),
            "rolling_vol_long": self.calculate_rolling_volatility(condition_id, 50),
            "regime": self.detect_volatility_regime(condition_id),
            "clustering": self.calculate_volatility_clustering(condition_id),
            "forecast": self.forecast_volatility(condition_id)
        }
    
    def get_optimal_spread_multiplier(self, condition_id: str) -> float:
        """
        Calculate spread multiplier based on volatility
        Higher volatility = wider spreads needed
        """
        vol = self.calculate_realized_volatility(condition_id, 15)
        
        if vol is None:
            return 1.0
        
        # Normalize volatility (assuming typical range 0.5-2.0)
        normalized_vol = min(max(vol / 1.0, 0.5), 2.0)  # Clamp between 0.5 and 2.0
        
        # Spread multiplier: 1.0 for normal vol, up to 2.0 for high vol
        multiplier = 0.5 + (normalized_vol * 0.75)
        
        return multiplier







