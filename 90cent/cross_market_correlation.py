"""
Cross-Market Momentum Correlation and Lead-Lag Analysis
"""
import numpy as np
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
from collections import deque
import logging

logger = logging.getLogger(__name__)


class CrossMarketCorrelation:
    """Analyzes correlation between Polymarket and spot exchanges"""
    
    def __init__(self, config: Dict = None):
        self.config = config or {}
        self.polymarket_prices: Dict[str, deque] = {}
        self.spot_prices: Dict[str, Dict[str, deque]] = {}  # {market: {exchange: prices}}
        self.max_history = 100
        
    def update_polymarket_price(self, market: str, price: float):
        """Update Polymarket price"""
        if market not in self.polymarket_prices:
            self.polymarket_prices[market] = deque(maxlen=self.max_history)
        
        self.polymarket_prices[market].append({
            "timestamp": datetime.now(),
            "price": price
        })
    
    def update_spot_price(self, market: str, exchange: str, price: float):
        """Update spot exchange price"""
        if market not in self.spot_prices:
            self.spot_prices[market] = {}
        
        if exchange not in self.spot_prices[market]:
            self.spot_prices[market][exchange] = deque(maxlen=self.max_history)
        
        self.spot_prices[market][exchange].append({
            "timestamp": datetime.now(),
            "price": price
        })
    
    def calculate_correlation(self, market: str, window_minutes: int = 15) -> Optional[Dict]:
        """
        Calculate correlation between Polymarket and spot prices
        """
        if market not in self.polymarket_prices or not self.polymarket_prices[market]:
            return None
        
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        
        poly_prices = [p["price"] for p in self.polymarket_prices[market] 
                      if p["timestamp"] >= cutoff_time]
        
        if not poly_prices:
            return None
        
        correlations = {}
        
        # Calculate correlation with each exchange
        if market in self.spot_prices:
            for exchange, prices in self.spot_prices[market].items():
                spot_prices = [p["price"] for p in prices 
                             if p["timestamp"] >= cutoff_time]
                
                if len(spot_prices) < 5 or len(poly_prices) < 5:
                    continue
                
                # Align prices (take minimum length)
                min_len = min(len(poly_prices), len(spot_prices))
                poly_aligned = poly_prices[-min_len:]
                spot_aligned = spot_prices[-min_len:]
                
                if min_len < 5:
                    continue
                
                # Calculate correlation
                correlation = np.corrcoef(poly_aligned, spot_aligned)[0, 1]
                
                if not np.isnan(correlation):
                    correlations[exchange] = correlation
        
        if not correlations:
            return None
        
        avg_correlation = np.mean(list(correlations.values()))
        
        return {
            "correlations": correlations,
            "average_correlation": avg_correlation,
            "strong_correlation": avg_correlation > 0.7
        }
    
    def analyze_lead_lag(self, market: str, window_minutes: int = 15) -> Optional[Dict]:
        """
        Analyze which market leads (moves first)
        Returns: which market leads and by how much
        """
        if market not in self.polymarket_prices or not self.polymarket_prices[market]:
            return None
        
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        
        poly_prices = [(p["timestamp"], p["price"]) for p in self.polymarket_prices[market] 
                      if p["timestamp"] >= cutoff_time]
        
        if not poly_prices:
            return None
        
        # Calculate returns
        poly_returns = []
        for i in range(1, len(poly_prices)):
            if poly_prices[i-1][1] > 0:
                ret = (poly_prices[i][1] - poly_prices[i-1][1]) / poly_prices[i-1][1]
                poly_returns.append((poly_prices[i][0], ret))
        
        lead_lag_analysis = {}
        
        if market in self.spot_prices:
            for exchange, prices in self.spot_prices[market].items():
                spot_prices = [(p["timestamp"], p["price"]) for p in prices 
                             if p["timestamp"] >= cutoff_time]
                
                if len(spot_prices) < 5:
                    continue
                
                spot_returns = []
                for i in range(1, len(spot_prices)):
                    if spot_prices[i-1][1] > 0:
                        ret = (spot_prices[i][1] - spot_prices[i-1][1]) / spot_prices[i-1][1]
                        spot_returns.append((spot_prices[i][0], ret))
                
                # Simple lead-lag: compare return timing
                if len(poly_returns) > 0 and len(spot_returns) > 0:
                    # Check if spot moves before poly (lead) or after (lag)
                    # Simplified: compare first significant move
                    poly_first_move = poly_returns[0] if poly_returns else None
                    spot_first_move = spot_returns[0] if spot_returns else None
                    
                    if poly_first_move and spot_first_move:
                        time_diff = (poly_first_move[0] - spot_first_move[0]).total_seconds()
                        
                        if abs(time_diff) < 300:  # Within 5 minutes
                            if time_diff > 0:
                                lead_lag_analysis[exchange] = {
                                    "leader": "spot",
                                    "lag_seconds": time_diff
                                }
                            else:
                                lead_lag_analysis[exchange] = {
                                    "leader": "polymarket",
                                    "lag_seconds": abs(time_diff)
                                }
        
        return lead_lag_analysis if lead_lag_analysis else None
    
    def calculate_momentum_correlation(self, market: str, window_minutes: int = 5) -> Optional[Dict]:
        """
        Calculate momentum correlation (short-term price changes)
        """
        if market not in self.polymarket_prices or not self.polymarket_prices[market]:
            return None
        
        cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
        
        poly_prices = [p["price"] for p in self.polymarket_prices[market] 
                      if p["timestamp"] >= cutoff_time]
        
        if len(poly_prices) < 2:
            return None
        
        poly_momentum = (poly_prices[-1] - poly_prices[0]) / poly_prices[0] if poly_prices[0] > 0 else 0
        
        spot_momentums = {}
        
        if market in self.spot_prices:
            for exchange, prices in self.spot_prices[market].items():
                spot_prices = [p["price"] for p in prices 
                             if p["timestamp"] >= cutoff_time]
                
                if len(spot_prices) < 2:
                    continue
                
                spot_momentum = (spot_prices[-1] - spot_prices[0]) / spot_prices[0] if spot_prices[0] > 0 else 0
                spot_momentums[exchange] = spot_momentum
        
        if not spot_momentums:
            return None
        
        avg_spot_momentum = np.mean(list(spot_momentums.values()))
        
        # Calculate momentum correlation
        momentum_corr = 1.0 if (poly_momentum > 0 and avg_spot_momentum > 0) or \
                           (poly_momentum < 0 and avg_spot_momentum < 0) else -1.0
        
        return {
            "polymarket_momentum": poly_momentum,
            "spot_momentums": spot_momentums,
            "average_spot_momentum": avg_spot_momentum,
            "momentum_correlation": momentum_corr,
            "aligned": abs(poly_momentum - avg_spot_momentum) < 0.01  # Within 1%
        }
    
    def detect_divergence(self, market: str) -> Optional[Dict]:
        """
        Detect divergence between Polymarket and spot prices
        """
        if market not in self.polymarket_prices or not self.polymarket_prices[market]:
            return None
        
        if market not in self.spot_prices:
            return None
        
        poly_price = self.polymarket_prices[market][-1]["price"] if self.polymarket_prices[market] else None
        
        if poly_price is None:
            return None
        
        spot_prices = {}
        for exchange, prices in self.spot_prices[market].items():
            if prices:
                spot_prices[exchange] = prices[-1]["price"]
        
        if not spot_prices:
            return None
        
        avg_spot = np.mean(list(spot_prices.values()))
        
        # Calculate divergence
        divergence_pct = ((poly_price - avg_spot) / avg_spot * 100) if avg_spot > 0 else 0
        
        return {
            "polymarket_price": poly_price,
            "average_spot_price": avg_spot,
            "divergence_percentage": divergence_pct,
            "is_diverged": abs(divergence_pct) > 2.0,  # More than 2% divergence
            "direction": "polymarket_high" if divergence_pct > 0 else "polymarket_low"
        }
    
    def detect_signal(self, market: str) -> Optional[Tuple[str, float]]:
        """
        Detect trading signal from cross-market analysis
        Returns: (side, confidence)
        """
        momentum = self.calculate_momentum_correlation(market, 5)
        divergence = self.detect_divergence(market)
        lead_lag = self.analyze_lead_lag(market, 15)
        
        signals = []
        confidences = []
        
        # Momentum alignment signal
        if momentum and momentum.get("aligned"):
            if momentum["polymarket_momentum"] > 0.005:  # 0.5% upward
                signals.append("YES")
                confidences.append(0.6)
            elif momentum["polymarket_momentum"] < -0.005:  # 0.5% downward
                signals.append("NO")
                confidences.append(0.6)
        
        # Divergence mean reversion
        if divergence and divergence.get("is_diverged"):
            if divergence["direction"] == "polymarket_high":
                # Polymarket overpriced, expect reversion down
                signals.append("NO")
                confidences.append(0.5)
            elif divergence["direction"] == "polymarket_low":
                # Polymarket underpriced, expect reversion up
                signals.append("YES")
                confidences.append(0.5)
        
        # Lead-lag following
        if lead_lag:
            for exchange, analysis in lead_lag.items():
                if analysis.get("leader") == "spot":
                    # Spot leads, follow spot momentum
                    if market in self.spot_prices and exchange in self.spot_prices[market]:
                        spot_prices = list(self.spot_prices[market][exchange])
                        if len(spot_prices) >= 2:
                            spot_momentum = (spot_prices[-1]["price"] - spot_prices[-2]["price"]) / spot_prices[-2]["price"]
                            if spot_momentum > 0.003:
                                signals.append("YES")
                                confidences.append(0.5)
                            elif spot_momentum < -0.003:
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
















