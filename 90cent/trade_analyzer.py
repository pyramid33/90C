"""
Trade Analyzer - Analyze actual bot trades and calculate P&L

This analyzes your REAL trades from the trading bot log to show:
- Total trades made
- Position balances per market
- Cost basis vs expected payout
- Actual profitability

Usage:
    python trade_analyzer.py              # Analyze all trades
    python trade_analyzer.py --log        # Parse from trading_bot.log
    python trade_analyzer.py --summary    # Just show summary
"""

import argparse
import re
import os
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class Trade:
    """A single trade"""
    timestamp: datetime
    condition_id: str
    side: str  # YES or NO
    shares: float
    price: float
    cost: float
    order_id: str = ""
    status: str = "placed"  # placed, matched, cancelled


@dataclass 
class Position:
    """Position in a market"""
    condition_id: str
    yes_shares: float = 0.0
    no_shares: float = 0.0
    yes_cost: float = 0.0
    no_cost: float = 0.0
    yes_avg_price: float = 0.0
    no_avg_price: float = 0.0
    trades: List[Trade] = field(default_factory=list)
    
    @property
    def total_cost(self) -> float:
        return self.yes_cost + self.no_cost
    
    @property
    def is_balanced(self) -> bool:
        return abs(self.yes_shares - self.no_shares) < 0.01
    
    @property
    def min_shares(self) -> float:
        return min(self.yes_shares, self.no_shares)
    
    @property
    def guaranteed_payout(self) -> float:
        """If positions are balanced, payout = min_shares * $1.00"""
        return self.min_shares * 1.0
    
    @property
    def expected_profit(self) -> float:
        """Expected profit if balanced (before fees)"""
        if not self.is_balanced:
            return 0.0  # Can't guarantee profit if unbalanced
        return self.guaranteed_payout - self.total_cost
    
    @property
    def roi(self) -> float:
        if self.total_cost == 0:
            return 0.0
        return (self.expected_profit / self.total_cost) * 100


class TradeAnalyzer:
    """Analyzes trading bot activity"""
    
    def __init__(self):
        self.positions: Dict[str, Position] = {}
        self.all_trades: List[Trade] = []
        
    def parse_log_file(self, log_path: str = "trading_bot.log"):
        """Parse trades from the trading bot log file"""
        
        if not os.path.exists(log_path):
            print(f"❌ Log file not found: {log_path}")
            return
        
        print(f"📂 Parsing log file: {log_path}")
        
        # Patterns to match order placements
        # Pattern: "Order placed: YES 5.0 @ 0.99 for 0x..."
        order_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*"
            r"Order placed.*?(YES|NO)\s+([\d.]+)\s+@\s+([\d.]+)\s+for\s+(0x[a-fA-F0-9]+)"
        )
        
        # Pattern: "status': 'matched'"
        matched_pattern = re.compile(
            r"'status':\s*'matched'"
        )
        
        # Alternative pattern for POLYMARKET_CLIENT logs
        client_pattern = re.compile(
            r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*POLYMARKET_CLIENT.*Order placed:\s*(YES|NO)\s+([\d.]+)\s+@\s+([\d.]+)\s+for\s+(0x[a-fA-F0-9]+)"
        )
        
        trades_found = 0
        matched_trades = 0
        
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            lines = f.readlines()
            
        for i, line in enumerate(lines):
            # Try client pattern first
            match = client_pattern.search(line)
            if not match:
                match = order_pattern.search(line)
            
            if match:
                try:
                    timestamp_str = match.group(1)
                    side = match.group(2).upper()
                    shares = float(match.group(3))
                    price = float(match.group(4))
                    condition_id = match.group(5).lower()
                    
                    timestamp = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    cost = shares * price
                    
                    # Check if this trade was matched (look at nearby lines)
                    status = "placed"
                    context = "".join(lines[i:min(i+5, len(lines))])
                    if matched_pattern.search(context):
                        status = "matched"
                        matched_trades += 1
                    
                    trade = Trade(
                        timestamp=timestamp,
                        condition_id=condition_id,
                        side=side,
                        shares=shares,
                        price=price,
                        cost=cost,
                        status=status
                    )
                    
                    self.all_trades.append(trade)
                    self._update_position(trade)
                    trades_found += 1
                    
                except Exception as e:
                    continue
        
        print(f"✅ Found {trades_found} trades ({matched_trades} matched)")
    
    def _update_position(self, trade: Trade):
        """Update position with a new trade"""
        cid = trade.condition_id
        
        if cid not in self.positions:
            self.positions[cid] = Position(condition_id=cid)
        
        pos = self.positions[cid]
        pos.trades.append(trade)
        
        # Only count matched trades for position
        if trade.status != "matched":
            return
            
        if trade.side == "YES":
            # Update average price
            if pos.yes_shares == 0:
                pos.yes_avg_price = trade.price
            else:
                total_cost = pos.yes_cost + trade.cost
                pos.yes_avg_price = total_cost / (pos.yes_shares + trade.shares)
            
            pos.yes_shares += trade.shares
            pos.yes_cost += trade.cost
        else:
            if pos.no_shares == 0:
                pos.no_avg_price = trade.price
            else:
                total_cost = pos.no_cost + trade.cost
                pos.no_avg_price = total_cost / (pos.no_shares + trade.shares)
            
            pos.no_shares += trade.shares
            pos.no_cost += trade.cost
    
    def print_summary(self):
        """Print trading summary"""
        print("\n" + "="*70)
        print("                    TRADE ANALYZER - SUMMARY")
        print("="*70)
        
        total_cost = 0
        total_payout = 0
        total_profit = 0
        balanced_markets = 0
        unbalanced_markets = 0
        
        for cid, pos in self.positions.items():
            if pos.yes_shares == 0 and pos.no_shares == 0:
                continue
                
            total_cost += pos.total_cost
            
            if pos.is_balanced:
                balanced_markets += 1
                total_payout += pos.guaranteed_payout
                total_profit += pos.expected_profit
            else:
                unbalanced_markets += 1
        
        print(f"\n📊 OVERALL STATISTICS")
        print(f"   Total trades:         {len(self.all_trades)}")
        print(f"   Matched trades:       {sum(1 for t in self.all_trades if t.status == 'matched')}")
        print(f"   Markets traded:       {len(self.positions)}")
        print(f"   Balanced markets:     {balanced_markets}")
        print(f"   Unbalanced markets:   {unbalanced_markets}")
        
        print(f"\n💰 PROFITABILITY (Balanced Markets Only)")
        print(f"   Total cost:           ${total_cost:.2f}")
        print(f"   Guaranteed payout:    ${total_payout:.2f}")
        print(f"   Expected profit:      ${total_profit:.2f}")
        if total_cost > 0:
            print(f"   ROI:                  {(total_profit/total_cost)*100:.2f}%")
        
        print("\n" + "="*70)
    
    def print_positions(self):
        """Print detailed position info"""
        print("\n" + "="*70)
        print("                    POSITION DETAILS")
        print("="*70)
        
        for cid, pos in self.positions.items():
            if pos.yes_shares == 0 and pos.no_shares == 0:
                continue
            
            print(f"\n📈 Market: {cid[:20]}...")
            print(f"   YES (Up):   {pos.yes_shares:>8.2f} shares @ {pos.yes_avg_price:.3f} = ${pos.yes_cost:.2f}")
            print(f"   NO (Down):  {pos.no_shares:>8.2f} shares @ {pos.no_avg_price:.3f} = ${pos.no_cost:.2f}")
            print(f"   Total cost: ${pos.total_cost:.2f}")
            
            if pos.is_balanced:
                print(f"   Status:     ✅ BALANCED ({pos.min_shares:.2f} shares each side)")
                print(f"   Payout:     ${pos.guaranteed_payout:.2f} (guaranteed)")
                print(f"   Profit:     ${pos.expected_profit:.2f} ({pos.roi:.2f}% ROI)")
            else:
                diff = abs(pos.yes_shares - pos.no_shares)
                more_side = "YES" if pos.yes_shares > pos.no_shares else "NO"
                print(f"   Status:     ⚠️ UNBALANCED ({more_side} has {diff:.2f} more shares)")
                print(f"   Risk:       Position has directional exposure!")
            
            print(f"   Trades:     {len(pos.trades)}")


def main():
    parser = argparse.ArgumentParser(description="Analyze trading bot trades")
    parser.add_argument("--log", type=str, default="trading_bot.log",
                       help="Path to trading_bot.log")
    parser.add_argument("--summary", action="store_true",
                       help="Show only summary, not position details")
    
    args = parser.parse_args()
    
    analyzer = TradeAnalyzer()
    analyzer.parse_log_file(args.log)
    
    if not args.summary:
        analyzer.print_positions()
    
    analyzer.print_summary()


if __name__ == "__main__":
    main()

