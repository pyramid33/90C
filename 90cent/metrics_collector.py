"""
Advanced Monitoring and Metrics Collection

Tracks bot performance, trading activity, and system health.
Provides periodic summary reports.
"""
import logging
import time
from collections import deque
from datetime import datetime
from typing import Dict, List, Optional, Deque

logger = logging.getLogger(__name__)


class MetricsCollector:
    """
    Collects and aggregates trading bot metrics.
    
    Tracks:
    - Trading performance (orders, fills, profit)
    - API performance (latency, errors)
    - System health (balance, uptime)
    - Per-market statistics
    """
    
    def __init__(self, history_size: int = 1000):
        """
        Initialize metrics collector.
        
        Args:
            history_size: Maximum number of events to keep in history
        """
        self.start_time = time.time()
        self.history_size = history_size
        
        # Trading metrics
        self.trades_placed = 0
        self.trades_filled = 0
        self.trades_rejected = 0
        self.trades_failed = 0
        self.total_profit_usd = 0.0
        self.trade_history: Deque[Dict] = deque(maxlen=history_size)
        
        # API metrics
        self.api_calls_total = 0
        self.api_calls_success = 0
        self.api_calls_error = 0
        self.api_latency_history: Deque[float] = deque(maxlen=history_size)
        
        # System metrics
        self.last_balance = 0.0
        self.peak_balance = 0.0
        self.open_orders_count = 0
        self.active_markets_count = 0
        
        # Per-market metrics
        self.market_stats: Dict[str, Dict] = {}
        
        # Reset interval tracking
        self.last_reset_time = time.time()
    
    def record_trade(
        self,
        market: str,
        side: str,
        size: float,
        price: float,
        status: str,
        profit: Optional[float] = None
    ) -> None:
        """
        Record a trade execution.
        
        Args:
            market: Market identifier
            side: "YES" or "NO"
            size: Order size in shares
            price: Execution price
            status: "filled", "rejected", "failed"
            profit: Profit/loss if available
        """
        self.trades_placed += 1
        
        if status == "filled":
            self.trades_filled += 1
            if profit:
                self.total_profit_usd += profit
        elif status == "rejected":
            self.trades_rejected += 1
        elif status == "failed":
            self.trades_failed += 1
        
        # Record trade
        self.trade_history.append({
            "timestamp": time.time(),
            "market": market,
            "side": side,
            "size": size,
            "price": price,
            "status": status,
            "profit": profit or 0.0
        })
        
        # Update market-specific stats
        if market not in self.market_stats:
            self.market_stats[market] = {
                "trades": 0,
                "filled": 0,
                "rejected": 0,
                "profit": 0.0
            }
        
        self.market_stats[market]["trades"] += 1
        if status == "filled":
            self.market_stats[market]["filled"] += 1
            if profit:
                self.market_stats[market]["profit"] += profit
        elif status == "rejected":
            self.market_stats[market]["rejected"] += 1
    
    def record_api_call(
        self,
        endpoint: str,
        latency_ms: float,
        success: bool = True
    ) -> None:
        """
        Record API call performance.
        
        Args:
            endpoint: API endpoint name
            latency_ms: Latency in milliseconds
            success: Whether call succeeded
        """
        self.api_calls_total += 1
        
        if success:
            self.api_calls_success += 1
        else:
            self.api_calls_error += 1
        
        self.api_latency_history.append(latency_ms)
    
    def update_system_metrics(
        self,
        balance: Optional[float] = None,
        open_orders: Optional[int] = None,
        active_markets: Optional[int] = None
    ) -> None:
        """
        Update system health metrics.
        
        Args:
            balance: Current balance
            open_orders: Number of open orders
            active_markets: Number of active markets
        """
        if balance is not None:
            self.last_balance = balance
            if balance > self.peak_balance:
                self.peak_balance = balance
        
        if open_orders is not None:
            self.open_orders_count = open_orders
        
        if active_markets is not None:
            self.active_markets_count = active_markets
    
    def get_summary(self) -> Dict:
        """
        Get aggregated metrics summary.
        
        Returns:
            Dictionary with all metrics
        """
        uptime = time.time() - self.start_time
        interval = time.time() - self.last_reset_time
        
        # Calculate rates
        fill_rate = (self.trades_filled / self.trades_placed * 100) if self.trades_placed > 0 else 0
        reject_rate = (self.trades_rejected / self.trades_placed * 100) if self.trades_placed > 0 else 0
        error_rate = (self.api_calls_error / self.api_calls_total * 100) if self.api_calls_total > 0 else 0
        
        # Calculate averages
        avg_profit = (self.total_profit_usd / self.trades_filled) if self.trades_filled > 0 else 0
        
        avg_latency = (sum(self.api_latency_history) / len(self.api_latency_history)
                      if self.api_latency_history else 0)
        max_latency = max(self.api_latency_history) if self.api_latency_history else 0
        min_latency = min(self.api_latency_history) if self.api_latency_history else 0
        
        return {
            "uptime": {
                "total_seconds": uptime,
                "interval_seconds": interval,
                "formatted": self._format_duration(uptime)
            },
            "trading": {
                "trades_placed": self.trades_placed,
                "trades_filled": self.trades_filled,
                "trades_rejected": self.trades_rejected,
                "trades_failed": self.trades_failed,
                "fill_rate_pct": fill_rate,
                "reject_rate_pct": reject_rate,
                "total_profit_usd": self.total_profit_usd,
                "avg_profit_usd": avg_profit
            },
            "api": {
                "calls_total": self.api_calls_total,
                "calls_success": self.api_calls_success,
                "calls_error": self.api_calls_error,
                "error_rate_pct": error_rate,
                "avg_latency_ms": avg_latency,
                "max_latency_ms": max_latency,
                "min_latency_ms": min_latency
            },
            "system": {
                "balance_usd": self.last_balance,
                "peak_balance_usd": self.peak_balance,
                "open_orders": self.open_orders_count,
                "active_markets": self.active_markets_count
            },
            "markets": self.market_stats.copy()
        }
    
    def get_report(self) -> str:
        """
        Get formatted text report.
        
        Returns:
            Multi-line string report
        """
        summary = self.get_summary()
        
        lines = []
        lines.append("=" * 60)
        lines.append("TRADING BOT METRICS REPORT")
        lines.append(f"Uptime: {summary['uptime']['formatted']}")
        lines.append("=" * 60)
        
        # Trading Performance
        trading = summary["trading"]
        lines.append("\nTRADING PERFORMANCE:")
        lines.append(f"  Total Orders:        {trading['trades_placed']}")
        lines.append(f"  Filled Orders:       {trading['trades_filled']} ({trading['fill_rate_pct']:.1f}%)")
        lines.append(f"  Rejected Orders:     {trading['trades_rejected']} ({trading['reject_rate_pct']:.1f}%)")
        lines.append(f"  Failed Orders:       {trading['trades_failed']}")
        lines.append(f"  Total Profit:        ${trading['total_profit_usd']:.4f}")
        lines.append(f"  Avg Profit/Trade:    ${trading['avg_profit_usd']:.4f}")
        
        # API Performance
        api = summary["api"]
        lines.append("\nAPI PERFORMANCE:")
        lines.append(f"  Total Calls:         {api['calls_total']}")
        lines.append(f"  Successful:          {api['calls_success']}")
        lines.append(f"  Errors:              {api['calls_error']} ({api['error_rate_pct']:.1f}%)")
        lines.append(f"  Avg Latency:         {api['avg_latency_ms']:.1f}ms")
        lines.append(f"  Max Latency:         {api['max_latency_ms']:.1f}ms")
        
        # System Health
        system = summary["system"]
        lines.append("\nSYSTEM HEALTH:")
        lines.append(f"  Current Balance:     ${system['balance_usd']:.2f}")
        lines.append(f"  Peak Balance:        ${system['peak_balance_usd']:.2f}")
        lines.append(f"  Open Orders:         {system['open_orders']}")
        lines.append(f"  Active Markets:      {system['active_markets']}")
        
        # Market Activity
        markets = summary["markets"]
        if markets:
            lines.append("\nMARKET ACTIVITY:")
            for market_id, stats in list(markets.items())[:5]:  # Top 5 markets
                fill_rate = (stats["filled"] / stats["trades"] * 100) if stats["trades"] > 0 else 0
                market_short = market_id[:12] + "..." if len(market_id) > 12 else market_id
                lines.append(f"  {market_short:15} {stats['trades']:3} trades, "
                           f"{fill_rate:5.1f}% filled, ${stats['profit']:+.3f}")
        
        lines.append("=" * 60)
        
        return "\n".join(lines)
    
    def reset_interval_stats(self) -> None:
        """Reset statistics for new interval (keeps cumulative totals)"""
        self.last_reset_time = time.time()
        logger.info("Metrics interval reset")
    
    @staticmethod
    def _format_duration(seconds: float) -> str:
        """Format duration in human-readable form"""
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            return f"{int(seconds // 60)}m {int(seconds % 60)}s"
        else:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            return f"{hours}h {minutes}m"
