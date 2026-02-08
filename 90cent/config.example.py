"""
Configuration file for Polymarket Trading Bot

SETUP:
1. Copy this file to config.py:  cp config.example.py config.py
2. Fill in your API keys and private key in the .env file or directly below
3. Adjust trading parameters as needed
"""
import os
from dotenv import load_dotenv

# Load .env from the same directory as this config file
_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(_env_path)

# Polymarket API Configuration
POLYMARKET_API_URL = "https://clob.polymarket.com"
POLYMARKET_WS_URL = os.getenv("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com")
POLYMARKET_CHAIN_ID = int(os.getenv("POLYMARKET_CHAIN_ID", "137"))
_signature_type_raw = os.getenv("POLYMARKET_SIGNATURE_TYPE", "").strip()
POLYMARKET_SIGNATURE_TYPE = int(_signature_type_raw) if _signature_type_raw else 0
POLYMARKET_PROXY_ADDRESS = os.getenv("POLYMARKET_PROXY_ADDRESS", "")
POLYMARKET_EXCHANGE_ADDRESS = os.getenv(
    "POLYMARKET_EXCHANGE_ADDRESS", "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
)

# API Authentication (set in .env file or replace placeholders below)
POLYMARKET_API_KEY = os.getenv("POLYMARKET_API_KEY", "")
POLYMARKET_PRIVATE_KEY = os.getenv("POLYMARKET_PRIVATE_KEY", "")
POLYMARKET_API_SECRET = os.getenv("POLYMARKET_API_SECRET", "")
POLYMARKET_API_PASSPHRASE = os.getenv("POLYMARKET_API_PASSPHRASE", "")
POLYMARKET_WALLET_ADDRESS = os.getenv("POLYMARKET_WALLET_ADDRESS", "")
AUTO_DISCOVERY_ENABLED = True
MARKET_REFRESH_INTERVAL = 300  # Refresh market condition IDs every 5 minutes

# Auto-Claim Configuration
# Automatically claims winnings from resolved markets
AUTO_CLAIM_ENABLED = True  # Set to False to disable auto-claiming
AUTO_CLAIM_INTERVAL = 900  # Check for redeemable positions every 15 minutes (900 seconds)

# Trading Configuration
MARKETS = {
    "BTC": {
        "condition_id": "",  # Auto-discovered via slug lookup
        "yes_outcome": "Up",
        "no_outcome": "Down",
        "auto_discover": {
            "keywords_any": ["btc", "bitcoin", "up", "down", "15", "15m", "15 minutes"],
            "phrases": ["up or down", "15 minutes"],
            "tags": ["crypto"]
        },
        "timeframes": ["15m"],
        "min_order_size": 1.01,
        "max_order_size": 80.0
    },
    "ETH": {
        "condition_id": "",  # Auto-discovered via slug lookup
        "yes_outcome": "Up",
        "no_outcome": "Down",
        "auto_discover": {
            "keywords_any": ["eth", "ethereum", "up", "down", "15", "15m", "15 minutes"],
            "phrases": ["up or down", "15 minutes"],
            "tags": ["crypto"]
        },
        "timeframes": ["15m"],
        "min_order_size": 1.01,
        "max_order_size": 80.0
    },
    # "SOL": {
    #     "condition_id": "",
    #     "yes_outcome": "Up",
    #     "no_outcome": "Down",
    #     "auto_discover": {
    #         "keywords_any": ["sol", "solana", "up", "down", "15", "15m", "15 minutes"],
    #         "phrases": ["up or down", "15 minutes"],
    #         "tags": ["crypto"]
    #     },
    #     "timeframes": ["15m"],
    #     "min_order_size": 1.01,
    #     "max_order_size": 5.0
    # },
    # "XRP": {
    #     "condition_id": "",
    #     "yes_outcome": "Up",
    #     "no_outcome": "Down",
    #     "auto_discover": {
    #         "keywords_any": ["xrp", "ripple", "up", "down", "15", "15m", "15 minutes"],
    #         "phrases": ["up or down", "15 minutes"],
    #         "tags": ["crypto"]
    #     },
    #     "timeframes": ["15m"],
    #     "min_order_size": 1.01,
    #     "max_order_size": 5.0
    # }
}

# Strategy Configuration
STRATEGY_CONFIG = {
    "momentum": {
        "enabled": False,
        "lookback_periods": 2,
        "momentum_threshold": 0.0005,
        "volume_threshold": 1.02
    },
    "technical_indicators": {
        "enabled": False,
        "rsi_period": 14,
        "rsi_oversold": 40,
        "rsi_overbought": 60,
        "ma_short": 5,
        "ma_long": 13,
        "bollinger_period": 15,
        "bollinger_std": 1.25
    },
    "orderbook": {
        "enabled": False,
        "imbalance_threshold": 0.1,
        "large_order_multiplier": 1.1
    },
    "ai_prediction": {
        "enabled": False,
        "model_type": "lstm",
        "prediction_horizon": 1,
        "confidence_threshold": 0.65
    }
}

# Order Management
ORDER_CONFIG = {
    "spread_percentage": 0.001,
    "min_spread": 0.0001,
    "max_spread": 0.002,
    "order_timeout": 30,
    "position_size_percentage": 0.02,
    "stale_order_threshold": 0.05,
    "cancel_stale_orders": True,
    "cancel_all_before_new_order": True
}

# Risk Management
RISK_CONFIG = {
    "max_daily_loss": -999999.0,
    "max_position_size": 0.50,
    "stop_loss_percentage": 1.0,
    "take_profit_percentage": 1.0,
    "max_leverage": 1.0
}

# Data Collection
DATA_CONFIG = {
    "historical_data_enabled": True,
    "orderbook_snapshots_enabled": True,
    "spot_price_updates": False,
    "spot_price_interval": 10,
    "historical_lookback_hours": 168
}

# Background Tasks
ORDER_STATUS_POLLING_ENABLED = False

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = "trading_bot.log"


# Flipping behavior: disabled for Buy Once strategy
FLIP_CONFIG = {
    "enabled": False,
    "min_conf_gap": 0.20,
    "max_reinforce_pct": 0.05
}

# Arbitrage entry policy: disabled for Buy Once strategy
ARB_ENTRY_CONFIG = {
    "enabled": False,
    "min_edge_bps": 20,
    "use_executable_prices": True
}

# Opportunistic Arbitrage Configuration: disabled for Buy Once strategy
ARB_CONFIG = {
    "enabled": False,
    "min_arb_profit_bps": 20,
    "max_position_size": 999999.0,
    "accumulation_enabled": True,
    "hold_until_resolution": True,
    "check_interval_seconds": 0.5,
    "aggressive_accumulation": True,
    "min_accumulation_size_multiplier": 2.0,
    "directional_fallback_enabled": False,
    "min_expensive_price": 0.49,
    "max_expensive_price": 0.99,
    "directional_premium_pct": 0.01,
    "aggressive_pricing_premium": 0.005,
    "micro_profit_enabled": False,
    "min_micro_profit_bps": 10,
    "micro_profit_check_interval": 1,
    "max_micro_profit_bps": 200,
    "require_equal_shares": True,
    "micro_profit_sell_aggressiveness": 0.998,
    "websocket_arbitrage_enabled": False,
    "websocket_arbitrage_cooldown": 2.0
}

# Buy Once Strategy Configuration
BUY_ONCE_CONFIG = {
    "enabled": True,
    "min_price": 0.98,
    "max_price": 0.99,
    "pre_check_price": 0.90,
    "order_size": 80.0,
    "aggressive_pricing": True,
    "aggressive_premium": 0.005,
    "stop_loss_price": 0.92,
    "trailing_stop_distance": 0.02,
    "trailing_stop_activation_price": 0.999,
    "stability_duration": 1,
    "max_time_before_resolution": 180
}

# Safety Sell Retry Configuration
SAFETY_SELL_CONFIG = {
    "max_retries": 15,
    "price_step": 0.05,
    "min_price": 0.01,
    "retry_delay": 0.2,  # Fast retries - speed is critical for stop-loss exits
}

# Pre-Resolution Exit Configuration
PRE_RESOLUTION_EXIT = {
    "enabled": False,
    "time_before_resolution": 120,
    "min_exit_price": 0.999,
    "price_discount": 0.0,
}

# Leaderboard Configuration
# Stats are reported to the central leaderboard server so users can compare performance
LEADERBOARD_ENABLED = True
LEADERBOARD_URL = "https://nine0cent-leaderboard.onrender.com"  # Central leaderboard server
LEADERBOARD_USERNAME = os.getenv("LEADERBOARD_USERNAME", "Anonymous")  # Set your display name
LEADERBOARD_REPORT_INTERVAL = 300  # Report stats every 5 minutes (seconds)

# Manual Position Initialization
INITIAL_POSITIONS = {
    # Empty = start fresh with 0 shares (bot tracks positions as it trades)
}
