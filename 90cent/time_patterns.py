"""
Time-of-Day Pattern Recognition
Identifies intraday patterns for better timing
"""
import numpy as np
from typing import Dict, Optional
from datetime import datetime, time
import logging

logger = logging.getLogger(__name__)


class TimePatternAnalyzer:
    """Analyzes time-based patterns for trading"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        
    def get_time_of_day_factor(self) -> Dict:
        """
        Get time-of-day adjustment factor
        Returns factors that adjust confidence based on time
        """
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        day_of_week = now.weekday()  # 0 = Monday
        
        factors = {
            "confidence_multiplier": 1.0,
            "volatility_expected": "medium",
            "pattern": "normal"
        }
        
        # US Market Hours (9:30 AM - 4:00 PM EST = 14:30 - 21:00 UTC)
        # Adjust for your timezone
        if 14 <= current_hour < 21:
            factors["confidence_multiplier"] = 1.1  # Higher confidence during active hours
            factors["volatility_expected"] = "high"
            factors["pattern"] = "active_trading"
        elif 21 <= current_hour or current_hour < 6:
            factors["confidence_multiplier"] = 0.9  # Lower confidence during off-hours
            factors["volatility_expected"] = "low"
            factors["pattern"] = "low_volume"
        elif 6 <= current_hour < 9:
            factors["confidence_multiplier"] = 1.05  # Slightly higher during morning
            factors["volatility_expected"] = "medium"
            factors["pattern"] = "morning_activity"
        
        # Day of week patterns
        if day_of_week == 0:  # Monday
            factors["confidence_multiplier"] *= 1.05  # Monday volatility
        elif day_of_week == 4:  # Friday
            factors["confidence_multiplier"] *= 0.95  # Friday slowdown
        
        return factors
    
    def adjust_confidence_by_time(self, base_confidence: float) -> float:
        """Adjust confidence based on time patterns"""
        time_factors = self.get_time_of_day_factor()
        adjusted = base_confidence * time_factors["confidence_multiplier"]
        return min(adjusted, 1.0)  # Cap at 1.0
















