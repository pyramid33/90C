"""
Historical Data Collection and Storage
Collects, stores, and analyzes historical market data

Supports both:
1. Local SQLite database for caching collected data
2. Polymarket /prices-history API for fetching historical data
"""
import logging
import os
import json
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import requests

if TYPE_CHECKING:
    from polymarket_client import PolymarketClient

logger = logging.getLogger(__name__)

# Polymarket prices-history API endpoint
PRICES_HISTORY_URL = "https://clob.polymarket.com/prices-history"


class HistoricalDataManager:
    """Manages historical data collection and storage"""
    
    def __init__(self, db_path: str = "historical_data.db"):
        self.db_path = self._resolve_db_path(db_path)
        self._init_database()
    
    def _resolve_db_path(self, db_path: str) -> Path:
        """Resolve the database path to an absolute, writable location."""
        env_path = os.getenv("HISTORICAL_DB_PATH")
        path_str = env_path or db_path
        path = Path(path_str)
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            logger.warning("Unable to create directory for DB %s: %s", path.parent, exc)
        return path
    
    def _handle_sqlite_exception(self, exc: Exception) -> bool:
        """Handle sqlite exceptions, attempting to relocate DB if needed."""
        if isinstance(exc, sqlite3.OperationalError) and "readonly" in str(exc).lower():
            return self._relocate_database()
        return False
    
    def _relocate_database(self) -> bool:
        """Move the database to a guaranteed writable location (LocalAppData/Home)."""
        fallback_root = Path(os.getenv("LOCALAPPDATA") or Path.home())
        target_dir = fallback_root / "PolymarketBot"
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
            target_path = target_dir / self.db_path.name
            if target_path == self.db_path:
                return False
            if self.db_path.exists():
                shutil.copy2(self.db_path, target_path)
            else:
                target_path.touch()
            self.db_path = target_path
            logger.warning("Historical DB relocated to writable path: %s", self.db_path)
            self._init_database()
            return True
        except Exception as move_exc:
            logger.error("Failed to relocate database: %s", move_exc)
            return False
        
    def _init_database(self):
        """Initialize SQLite database"""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Price data table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS price_data (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                condition_id TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                price REAL NOT NULL,
                volume REAL,
                high REAL,
                low REAL,
                open_price REAL,
                close_price REAL,
                UNIQUE(condition_id, timestamp)
            )
        ''')
        
        # Order book snapshots
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS orderbook_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                condition_id TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                bid_volume REAL,
                ask_volume REAL,
                spread REAL,
                imbalance REAL,
                data_json TEXT
            )
        ''')
        
        # Technical indicators (pre-calculated)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS indicators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                condition_id TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                rsi REAL,
                ma_short REAL,
                ma_long REAL,
                bollinger_upper REAL,
                bollinger_middle REAL,
                bollinger_lower REAL,
                momentum REAL,
                volatility REAL
            )
        ''')
        
        # Create indexes
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_condition_timestamp ON price_data(condition_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ob_condition_timestamp ON orderbook_snapshots(condition_id, timestamp)')
        cursor.execute('CREATE INDEX IF NOT EXISTS idx_ind_condition_timestamp ON indicators(condition_id, timestamp)')
        
        conn.commit()
        conn.close()
        logger.info(f"Database initialized: {self.db_path}")
    
    def _execute_write(self, query: str, params: Tuple, error_message: str) -> bool:
        """Execute a write query with automatic readonly recovery."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
            return True
        except Exception as exc:
            try:
                conn.rollback()
            except sqlite3.ProgrammingError:
                pass
            logger.error(f"{error_message}: {exc}")
            if self._handle_sqlite_exception(exc):
                return self._execute_write(query, params, error_message)
            return False
        finally:
            conn.close()
    
    def save_price_data(self, condition_id: str, price: float, volume: float = 0, 
                       high: float = None, low: float = None, 
                       open_price: float = None, close_price: float = None):
        """Save price data point"""
        timestamp = datetime.now()
        self._execute_write(
            '''
            INSERT OR REPLACE INTO price_data 
            (condition_id, timestamp, price, volume, high, low, open_price, close_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (condition_id, timestamp, price, volume, high, low, open_price, close_price),
            "Error saving price data",
        )
    
    def save_orderbook_snapshot(self, condition_id: str, orderbook_data: Dict):
        """Save order book snapshot"""
        timestamp = datetime.now()
        bid_volume = sum(float(o.get("size", 0)) for o in orderbook_data.get("bids", []))
        ask_volume = sum(float(o.get("size", 0)) for o in orderbook_data.get("asks", []))
        
        # Calculate spread
        bids = orderbook_data.get("bids", [])
        asks = orderbook_data.get("asks", [])
        spread = None
        if bids and asks:
            best_bid = float(bids[0].get("price", 0))
            best_ask = float(asks[0].get("price", 0))
            if best_bid > 0:
                spread = (best_ask - best_bid) / best_bid
        
        imbalance = None
        total_volume = bid_volume + ask_volume
        if total_volume > 0:
            imbalance = (bid_volume - ask_volume) / total_volume
        
        data_json = json.dumps(orderbook_data)
        
        self._execute_write(
            '''
            INSERT INTO orderbook_snapshots 
            (condition_id, timestamp, bid_volume, ask_volume, spread, imbalance, data_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ''',
            (condition_id, timestamp, bid_volume, ask_volume, spread, imbalance, data_json),
            "Error saving orderbook snapshot",
        )
    
    def get_price_history(self, condition_id: str, hours: int = 24, 
                         timeframe: str = "1h") -> Optional[pd.DataFrame]:
        """
        Get historical price data
        timeframe: "15m", "1h", "1d"
        """
        conn = sqlite3.connect(self.db_path)
        
        # Calculate time range
        end_time = datetime.now()
        start_time = end_time - timedelta(hours=hours)
        
        query = '''
            SELECT timestamp, price, volume, high, low, open_price, close_price
            FROM price_data
            WHERE condition_id = ? AND timestamp >= ? AND timestamp <= ?
            ORDER BY timestamp ASC
        '''
        
        try:
            df = pd.read_sql_query(query, conn, params=(condition_id, start_time, end_time))
            conn.close()
            
            if df.empty:
                return None
            
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df.set_index('timestamp', inplace=True)
            
            # Resample to desired timeframe
            if timeframe == "15m":
                df = df.resample('15T').agg({
                    'price': 'last',
                    'volume': 'sum',
                    'high': 'max',
                    'low': 'min',
                    'open_price': 'first',
                    'close_price': 'last'
                })
            elif timeframe == "1h":
                df = df.resample('1h').agg({
                    'price': 'last',
                    'volume': 'sum',
                    'high': 'max',
                    'low': 'min',
                    'open_price': 'first',
                    'close_price': 'last'
                })
            
            return df
        except Exception as e:
            logger.error(f"Error getting price history: {e}")
            conn.close()
            return None
    
    def calculate_historical_indicators(self, condition_id: str, 
                                       lookback_hours: int = 168) -> Dict:
        """
        Calculate historical indicators for context
        Returns dictionary with historical ranges, averages, etc.
        """
        df = self.get_price_history(condition_id, hours=lookback_hours)
        
        if df is None or df.empty:
            return {}
        
        prices = df['price'].values
        
        # Historical statistics
        stats = {
            "mean_price": float(np.mean(prices)),
            "std_price": float(np.std(prices)),
            "min_price": float(np.min(prices)),
            "max_price": float(np.max(prices)),
            "current_price": float(prices[-1]),
            "price_percentile": float(np.percentile(prices, prices[-1])),
            "volatility": float(np.std(prices) / np.mean(prices)) if np.mean(prices) > 0 else 0
        }
        
        # RSI historical range
        if len(prices) >= 14:
            deltas = np.diff(prices)
            gains = np.where(deltas > 0, deltas, 0)
            losses = np.where(deltas < 0, -deltas, 0)
            
            avg_gain = np.mean(gains[-14:])
            avg_loss = np.mean(losses[-14:])
            
            if avg_loss > 0:
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                stats["current_rsi"] = float(rsi)
        
        # Volume statistics
        if 'volume' in df.columns:
            volumes = df['volume'].values
            stats["avg_volume"] = float(np.mean(volumes))
            stats["current_volume"] = float(volumes[-1]) if len(volumes) > 0 else 0
            stats["volume_ratio"] = stats["current_volume"] / stats["avg_volume"] if stats["avg_volume"] > 0 else 1
        
        return stats
    
    def get_optimal_thresholds(self, condition_id: str) -> Dict:
        """
        Analyze historical data to find optimal indicator thresholds
        """
        df = self.get_price_history(condition_id, hours=720)  # 30 days
        
        if df is None or df.empty or len(df) < 50:
            return {}
        
        prices = df['price'].values
        
        # Calculate RSI over history
        rsi_values = []
        if len(prices) >= 14:
            for i in range(14, len(prices)):
                period_prices = prices[i-14:i+1]
                deltas = np.diff(period_prices)
                gains = np.where(deltas > 0, deltas, 0)
                losses = np.where(deltas < 0, -deltas, 0)
                
                avg_gain = np.mean(gains)
                avg_loss = np.mean(losses)
                
                if avg_loss > 0:
                    rs = avg_gain / avg_loss
                    rsi = 100 - (100 / (1 + rs))
                    rsi_values.append(rsi)
        
        optimal = {}
        
        if rsi_values:
            optimal["rsi_oversold"] = float(np.percentile(rsi_values, 10))  # Bottom 10%
            optimal["rsi_overbought"] = float(np.percentile(rsi_values, 90))  # Top 10%
            optimal["rsi_mean"] = float(np.mean(rsi_values))
        
        # Momentum thresholds
        if len(prices) >= 20:
            momentum_values = []
            for i in range(5, len(prices)):
                momentum = (prices[i] - prices[i-5]) / prices[i-5]
                momentum_values.append(momentum)
            
            if momentum_values:
                optimal["momentum_threshold"] = float(np.percentile(np.abs(momentum_values), 75))
        
        return optimal
    
    def save_indicators(self, condition_id: str, indicators: Dict):
        """Save calculated indicators"""
        timestamp = datetime.now()
        self._execute_write(
            '''
            INSERT INTO indicators 
            (condition_id, timestamp, rsi, ma_short, ma_long, 
             bollinger_upper, bollinger_middle, bollinger_lower, 
             momentum, volatility)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                condition_id,
                timestamp,
                indicators.get("rsi"),
                indicators.get("ma_short"),
                indicators.get("ma_long"),
                indicators.get("bollinger_upper"),
                indicators.get("bollinger_middle"),
                indicators.get("bollinger_lower"),
                indicators.get("momentum"),
                indicators.get("volatility"),
            ),
            "Error saving indicators",
        )
    
    # =========================================================================
    # Polymarket /prices-history API Integration
    # =========================================================================
    
    def fetch_prices_history_api(
        self,
        token_id: str,
        interval: str = None,
        start_ts: int = None,
        end_ts: int = None,
        fidelity: int = None
    ) -> Optional[List[Dict]]:
        """
        Fetch historical price data directly from Polymarket /prices-history API.
        
        Args:
            token_id: The CLOB token ID (not condition_id)
            interval: Time interval - "1m", "1w", "1d", "6h", "1h", "max"
            start_ts: Start Unix timestamp (UTC)
            end_ts: End Unix timestamp (UTC)
            fidelity: Data resolution in minutes (1, 5, 15, 60, etc.)
        
        Returns:
            List of [{"t": timestamp, "p": price}, ...] or None
        """
        params = {"market": token_id}
        
        if interval:
            params["interval"] = interval
        elif start_ts and end_ts:
            params["startTs"] = start_ts
            params["endTs"] = end_ts
        
        if fidelity:
            params["fidelity"] = fidelity
        
        try:
            response = requests.get(PRICES_HISTORY_URL, params=params, timeout=15)
            response.raise_for_status()
            data = response.json()
            history = data.get("history", [])
            logger.debug("API: Fetched %d price points for token %s", len(history), token_id)
            return history
        except requests.exceptions.RequestException as e:
            logger.error("API Error fetching prices-history for %s: %s", token_id, e)
            return None
        except Exception as e:
            logger.error("Unexpected error in fetch_prices_history_api: %s", e)
            return None
    
    def get_price_history_from_api(
        self,
        client: "PolymarketClient",
        condition_id: str,
        side: str = "YES",
        hours: int = 24,
        fidelity: int = 15,
        cache_to_db: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        Get historical price data from the Polymarket API.
        
        This is the preferred method for getting reliable historical data,
        as it fetches directly from Polymarket's servers rather than relying
        on locally collected data.
        
        Args:
            client: PolymarketClient instance (needed to resolve token_id)
            condition_id: Market condition ID
            side: "YES" or "NO"
            hours: How many hours of history to fetch
            fidelity: Data resolution in minutes (default 15)
            cache_to_db: Whether to save fetched data to local SQLite DB
        
        Returns:
            DataFrame with timestamp index and price column, or None
        """
        # Calculate timestamps
        end_ts = int(time.time())
        start_ts = end_ts - (hours * 3600)
        
        # Use the client's method which handles token_id resolution
        df = client.get_prices_history_df(
            condition_id=condition_id,
            side=side,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=fidelity
        )
        
        if df is None or df.empty:
            logger.warning("No data from API for %s (%s)", condition_id, side)
            return None
        
        # Optionally cache to local DB
        if cache_to_db:
            self._cache_api_data_to_db(condition_id, df)
        
        return df
    
    def _cache_api_data_to_db(self, condition_id: str, df: pd.DataFrame):
        """Cache API-fetched data to local SQLite database."""
        if df is None or df.empty:
            return
        
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        try:
            for timestamp, row in df.iterrows():
                price = row.get("price", 0)
                # Convert timezone-aware timestamp to naive UTC
                if hasattr(timestamp, 'tz_localize'):
                    ts = timestamp.tz_localize(None) if timestamp.tzinfo else timestamp
                else:
                    ts = timestamp
                
                cursor.execute(
                    '''
                    INSERT OR REPLACE INTO price_data 
                    (condition_id, timestamp, price, volume, high, low, open_price, close_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ''',
                    (condition_id, ts, price, 0, price, price, price, price)
                )
            
            conn.commit()
            logger.debug("Cached %d API data points to DB for %s", len(df), condition_id)
        except Exception as e:
            logger.error("Error caching API data to DB: %s", e)
            conn.rollback()
        finally:
            conn.close()
    
    def get_combined_price_history(
        self,
        client: "PolymarketClient",
        condition_id: str,
        side: str = "YES",
        hours: int = 24,
        fidelity: int = 15,
        prefer_api: bool = True
    ) -> Optional[pd.DataFrame]:
        """
        Get historical price data, preferring API but falling back to local DB.
        
        Args:
            client: PolymarketClient instance
            condition_id: Market condition ID
            side: "YES" or "NO"
            hours: Hours of history to fetch
            fidelity: Data resolution in minutes
            prefer_api: If True, try API first; if False, try local DB first
        
        Returns:
            DataFrame with price history or None
        """
        if prefer_api:
            # Try API first
            df = self.get_price_history_from_api(
                client=client,
                condition_id=condition_id,
                side=side,
                hours=hours,
                fidelity=fidelity,
                cache_to_db=True
            )
            if df is not None and not df.empty:
                return df
            
            # Fall back to local DB
            logger.info("API returned no data, falling back to local DB for %s", condition_id)
            return self.get_price_history(condition_id, hours=hours)
        else:
            # Try local DB first
            df = self.get_price_history(condition_id, hours=hours)
            if df is not None and not df.empty:
                return df
            
            # Fall back to API
            logger.info("Local DB has no data, fetching from API for %s", condition_id)
            return self.get_price_history_from_api(
                client=client,
                condition_id=condition_id,
                side=side,
                hours=hours,
                fidelity=fidelity,
                cache_to_db=True
            )
    
    def backfill_from_api(
        self,
        client: "PolymarketClient",
        condition_id: str,
        days: int = 7,
        fidelity: int = 60
    ) -> int:
        """
        Backfill local database with historical data from API.
        
        Useful for initializing historical data when starting the bot
        on a new market or after clearing the database.
        
        Args:
            client: PolymarketClient instance
            condition_id: Market condition ID
            days: Number of days of history to fetch
            fidelity: Data resolution in minutes
        
        Returns:
            Number of data points saved
        """
        total_saved = 0
        hours = days * 24
        
        for side in ["YES", "NO"]:
            df = self.get_price_history_from_api(
                client=client,
                condition_id=condition_id,
                side=side,
                hours=hours,
                fidelity=fidelity,
                cache_to_db=True
            )
            
            if df is not None:
                total_saved += len(df)
                logger.info(
                    "Backfilled %d data points for %s (%s) from API",
                    len(df), condition_id, side
                )
        
        return total_saved

