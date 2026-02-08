"""
Multiple Real-Time Data Sources
Integrates data from Polymarket, spot exchanges, and other sources
"""
import requests
import websocket
import json
import threading
import time
from typing import Dict, List, Optional, Callable
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class SpotExchangeClient:
    """Client for fetching spot exchange prices"""
    
    def __init__(self):
        self.binance_url = "https://api.binance.com/api/v3"
        self.coinbase_url = "https://api.coinbase.com/v2"
        self.kraken_url = "https://api.kraken.com/0/public"
        
    def get_binance_price(self, symbol: str) -> Optional[float]:
        """Get price from Binance"""
        try:
            url = f"{self.binance_url}/ticker/price"
            params = {"symbol": symbol}
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            return float(data.get("price", 0))
        except Exception as e:
            logger.debug(f"Binance API error for {symbol}: {e}")
            return None
    
    def get_coinbase_price(self, symbol: str) -> Optional[float]:
        """Get price from Coinbase"""
        try:
            url = f"{self.coinbase_url}/prices/{symbol}/spot"
            response = requests.get(url, timeout=5)
            response.raise_for_status()
            data = response.json()
            return float(data.get("data", {}).get("amount", 0))
        except Exception as e:
            logger.debug(f"Coinbase API error for {symbol}: {e}")
            return None
    
    def get_kraken_price(self, pair: str) -> Optional[float]:
        """Get price from Kraken"""
        try:
            url = f"{self.kraken_url}/Ticker"
            params = {"pair": pair}
            response = requests.get(url, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()
            result = data.get("result", {})
            if result:
                ticker_data = list(result.values())[0]
                price_data = ticker_data.get("c", [])
                if price_data:
                    return float(price_data[0])
            return None
        except Exception as e:
            logger.debug(f"Kraken API error for {pair}: {e}")
            return None


class DataAggregator:
    """Aggregates data from multiple sources"""
    
    def __init__(self):
        self.spot_client = SpotExchangeClient()
        self.symbol_mapping = {
            "BTC": {"binance": "BTCUSDT", "coinbase": "BTC-USD", "kraken": "XBTUSD"},
            "ETH": {"binance": "ETHUSDT", "coinbase": "ETH-USD", "kraken": "ETHUSD"},
            "SOL": {"binance": "SOLUSDT", "coinbase": "SOL-USD", "kraken": "SOLUSD"},
            "XRP": {"binance": "XRPUSDT", "coinbase": "XRP-USD", "kraken": "XRPUSD"}
        }
        self.spot_prices: Dict[str, Dict[str, float]] = {}
        self.update_thread = None
        self.running = False
        
    def start_spot_price_updates(self, interval: int = 10):
        """Start periodic spot price updates"""
        self.running = True
        
        def update_loop():
            while self.running:
                try:
                    for token, symbols in self.symbol_mapping.items():
                        prices = {}
                        
                        # Binance
                        binance_price = self.spot_client.get_binance_price(symbols["binance"])
                        if binance_price:
                            prices["binance"] = binance_price
                        
                        # Coinbase
                        coinbase_price = self.spot_client.get_coinbase_price(symbols["coinbase"])
                        if coinbase_price:
                            prices["coinbase"] = coinbase_price
                        
                        # Kraken
                        kraken_price = self.spot_client.get_kraken_price(symbols["kraken"])
                        if kraken_price:
                            prices["kraken"] = kraken_price
                        
                        if prices:
                            self.spot_prices[token] = {
                                **prices,
                                "timestamp": datetime.now(),
                                "average": sum(prices.values()) / len(prices) if prices else None
                            }
                except Exception as e:
                    logger.error(f"Error updating spot prices: {e}")
                
                time.sleep(interval)
        
        self.update_thread = threading.Thread(target=update_loop, daemon=True)
        self.update_thread.start()
        logger.info("Spot price updates started")
    
    def get_spot_price(self, token: str) -> Optional[float]:
        """Get average spot price for a token"""
        if token in self.spot_prices:
            return self.spot_prices[token].get("average")
        return None
    
    def get_all_spot_prices(self, token: str) -> Dict:
        """Get all spot prices for a token"""
        return self.spot_prices.get(token, {})
    
    def calculate_premium_discount(self, token: str, polymarket_price: float) -> Optional[float]:
        """
        Calculate premium/discount of Polymarket vs spot
        Returns: positive = premium, negative = discount
        """
        spot_price = self.get_spot_price(token)
        if spot_price is None or spot_price == 0:
            return None
        
        # For prediction markets, we need to compare differently
        # This is a simplified version - adjust based on your market structure
        premium = ((polymarket_price - spot_price) / spot_price) * 100
        return premium
    
    def stop(self):
        """Stop spot price updates"""
        self.running = False


class OnChainData:
    """Placeholder for on-chain data integration"""
    
    def __init__(self):
        # Would integrate with Glassnode, CryptoQuant, etc.
        pass
    
    def get_transaction_volume(self, token: str, hours: int = 24) -> Optional[float]:
        """Get transaction volume (placeholder)"""
        # Implement with actual on-chain API
        return None
    
    def get_active_addresses(self, token: str, hours: int = 24) -> Optional[int]:
        """Get active addresses (placeholder)"""
        # Implement with actual on-chain API
        return None


class SentimentData:
    """Placeholder for sentiment data integration"""
    
    def __init__(self):
        # Would integrate with Twitter API, Reddit API, etc.
        pass
    
    def get_sentiment_score(self, token: str) -> Optional[float]:
        """Get sentiment score -1 to 1 (placeholder)"""
        # Implement with actual sentiment API
        return None
















