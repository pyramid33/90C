"""
Portfolio Risk Calculator for Polymarket

Calculates exposure, P&L, and "Green Up" hedging for Polymarket positions.
Allows traders to see aggregate risk across categories and lock in profits.
"""

import logging
import requests
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Position:
    """A Polymarket position."""
    condition_id: str
    token_id: str
    side: str  # "YES" or "NO"
    size: float  # Number of shares
    cost_basis: float  # Total cost in USDC
    
    @property
    def avg_entry_price(self) -> float:
        """Average entry price per share."""
        return self.cost_basis / self.size if self.size > 0 else 0


@dataclass
class EnrichedPosition:
    """Position with market metadata and P&L."""
    position: Position
    question: str
    category: str
    current_price: float
    market_url: str
    
    @property
    def current_value(self) -> float:
        """Current market value of position."""
        return self.position.size * self.current_price
    
    @property
    def pnl(self) -> float:
        """Unrealized profit/loss."""
        return self.current_value - self.position.cost_basis
    
    @property
    def pnl_percent(self) -> float:
        """P&L as percentage."""
        return (self.pnl / self.position.cost_basis * 100) if self.position.cost_basis > 0 else 0
    
    def to_dict(self) -> Dict:
        return {
            "condition_id": self.position.condition_id,
            "question": self.question,
            "category": self.category,
            "side": self.position.side,
            "size": self.position.size,
            "avg_entry_price": self.position.avg_entry_price,
            "current_price": self.current_price,
            "cost_basis": self.position.cost_basis,
            "current_value": self.current_value,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "market_url": self.market_url
        }


@dataclass
class CategoryExposure:
    """Aggregate exposure in a category."""
    category_name: str
    total_cost: float = 0
    total_current_value: float = 0
    positions_count: int = 0
    positions: List[EnrichedPosition] = field(default_factory=list)
    
    @property
    def pnl(self) -> float:
        return self.total_current_value - self.total_cost
    
    @property
    def pnl_percent(self) -> float:
        return (self.pnl / self.total_cost * 100) if self.total_cost > 0 else 0
    
    def to_dict(self) -> Dict:
        return {
            "category": self.category_name,
            "total_cost": self.total_cost,
            "total_current_value": self.total_current_value,
            "pnl": self.pnl,
            "pnl_percent": self.pnl_percent,
            "positions_count": self.positions_count,
            "top_positions": [p.to_dict() for p in sorted(self.positions, key=lambda x: abs(x.pnl), reverse=True)[:3]]
        }


@dataclass
class GreenUpResult:
    """Result of a Green Up calculation."""
    condition_id: str
    current_side: str
    current_size: float
    current_price: float
    current_value: float
    cost_basis: float
    
    # Hedging recommendation
    hedge_side: str  # Opposite side
    hedge_size: float  # How much to buy/sell
    hedge_cost: float  # Cost of hedge
    
    # Outcome
    guaranteed_profit: float
    locked_in: bool  # True if it's possible to lock in profit
    
    def to_dict(self) -> Dict:
        return {
            "condition_id": self.condition_id,
            "current_position": {
                "side": self.current_side,
                "size": self.current_size,
                "avg_price": self.cost_basis / self.current_size if self.current_size > 0 else 0,
                "current_value": self.current_value
            },
            "hedge_recommendation": {
                "side": self.hedge_side,
                "size": self.hedge_size,
                "cost": self.hedge_cost,
                "at_price": 1 - self.current_price if self.current_side == "YES" else self.current_price
            },
            "result": {
                "guaranteed_profit": self.guaranteed_profit,
                "locked_in": self.locked_in,
                "pnl_before_hedge": self.current_value - self.cost_basis
            }
        }


class PortfolioCalculator:
    """Calculator for Polymarket portfolio risk and hedging."""
    
    def __init__(self):
        self.gamma_api_url = "https://gamma-api.polymarket.com"
    
    def fetch_positions_from_gamma(self, wallet_address: str) -> List[Position]:
        """
        Fetch positions from Polymarket Gamma API.
        
        Args:
            wallet_address: Ethereum wallet address
            
        Returns:
            List of Position objects
        """
        try:
            # Gamma API endpoint for positions
            url = f"https://data-api.polymarket.com/positions"
            params = {"user": wallet_address.lower(), "limit": 500}
            
            logger.info(f"Fetching positions from {url} for wallet {wallet_address[:10]}...")
            response = requests.get(url, params=params, timeout=10)
            response.raise_for_status()
            
            positions_data = response.json()
            logger.info(f"API returned {len(positions_data) if isinstance(positions_data, list) else 0} positions")
            
            if not isinstance(positions_data, list):
                logger.error(f"Unexpected response type: {type(positions_data)}")
                return []
            
            positions = []
            
            for pos_data in positions_data:
                logger.debug(f"Processing position data: {pos_data.keys()}")
                
                # Parse position data - try different field names
                condition_id = pos_data.get("condition_id") or pos_data.get("conditionId") or pos_data.get("market")
                token_id = pos_data.get("token_id") or pos_data.get("tokenId") or pos_data.get("asset_id")
                
                # Size might be in different fields
                size = float(pos_data.get("size", 0) or pos_data.get("amount", 0) or pos_data.get("shares", 0))
                
                # Cost basis calculation
                cost_basis = float(pos_data.get("cost_basis", 0) or pos_data.get("costBasis", 0) or pos_data.get("initial_value", 0) or pos_data.get("initialValue", 0))
                
                # If no cost basis, try to calculate from averageEntryPrice
                if cost_basis == 0:
                    avg_entry = float(pos_data.get("averageEntryPrice", 0) or pos_data.get("avg_entry_price", 0) or 0)
                    if avg_entry > 0:
                        cost_basis = size * avg_entry
                
                # Determine side from outcome
                outcome = pos_data.get("outcome", "") or pos_data.get("side", "")
                side = "YES" if str(outcome).upper() in ["YES", "1", "TRUE"] else "NO"
                
                if condition_id and size > 0:
                    positions.append(Position(
                        condition_id=condition_id,
                        token_id=token_id or "",
                        side=side,
                        size=size,
                        cost_basis=cost_basis if cost_basis > 0 else size * 0.5  # Estimate if missing
                    ))
                    logger.debug(f"Added position: {condition_id}, size={size}, side={side}")
            
            logger.info(f"Successfully parsed {len(positions)} positions for wallet {wallet_address[:10]}...")
            return positions
            
        except requests.exceptions.RequestException as e:
            logger.error(f"HTTP error fetching positions from Gamma API: {e}")
            return []
        except Exception as e:
            logger.error(f"Error fetching positions from Gamma API: {e}", exc_info=True)
            return []
    
    def enrich_positions(self, positions: List[Position], markets_data: List[Dict]) -> List[EnrichedPosition]:
        """
        Add market metadata to positions.
        
        Args:
            positions: List of Position objects
            markets_data: Market data from scanner (with prices, questions, etc.)
            
        Returns:
            List of EnrichedPosition objects
        """
        # Create lookup map
        markets_by_condition = {m.get("id"): m for m in markets_data if m.get("id")}
        
        enriched = []
        for pos in positions:
            market = markets_by_condition.get(pos.condition_id)
            
            if not market:
                # Try to fetch individual market if not in bulk data
                logger.warning(f"Market {pos.condition_id} not found in scanner data")
                continue
            
            # Get current price based on side
            current_price = market.get("yes_price", 0.5) if pos.side == "YES" else market.get("no_price", 0.5)
            
            enriched.append(EnrichedPosition(
                position=pos,
                question=market.get("question", "Unknown Market"),
                category=market.get("category", "Other"),
                current_price=current_price,
                market_url=market.get("url", "#")
            ))
        
        return enriched
    
    def aggregate_by_category(self, positions: List[EnrichedPosition]) -> Dict[str, CategoryExposure]:
        """Group positions by market category."""
        categories = defaultdict(lambda: CategoryExposure(category_name="Unknown"))
        
        for pos in positions:
            cat_name = pos.category
            if cat_name not in categories:
                categories[cat_name] = CategoryExposure(category_name=cat_name)
            
            cat_exp = categories[cat_name]
            cat_exp.total_cost += pos.position.cost_basis
            cat_exp.total_current_value += pos.current_value
            cat_exp.positions_count += 1
            cat_exp.positions.append(pos)
        
        return dict(categories)
    
    def aggregate_by_theme(self, positions: List[EnrichedPosition]) -> Dict[str, CategoryExposure]:
        """
        Group positions by keyword themes (e.g., "Trump", "Crypto", "NBA").
        Uses simple keyword matching on question text.
        """
        themes = defaultdict(lambda: CategoryExposure(category_name="Unknown"))
        
        # Define theme keywords
        theme_keywords = {
            "Trump": ["trump", "donald"],
            "Harris": ["harris", "kamala"],
            "Election": ["election", "vote", "electoral"],
            "Bitcoin": ["bitcoin", "btc"],
            "Ethereum": ["ethereum", "eth"],
            "Crypto": ["crypto", "token"],
            "NFL": ["nfl", "football", "super bowl"],
            "NBA": ["nba", "basketball"],
            "Soccer": ["soccer", "fifa", "premier league"]
        }
        
        for pos in positions:
            question_lower = pos.question.lower()
            matched_theme = None
            
            # Find first matching theme
            for theme_name, keywords in theme_keywords.items():
                if any(kw in question_lower for kw in keywords):
                    matched_theme = theme_name
                    break
            
            if not matched_theme:
                matched_theme = "Other"
            
            if matched_theme not in themes:
                themes[matched_theme] = CategoryExposure(category_name=matched_theme)
            
            theme_exp = themes[matched_theme]
            theme_exp.total_cost += pos.position.cost_basis
            theme_exp.total_current_value += pos.current_value
            theme_exp.positions_count += 1
            theme_exp.positions.append(pos)
        
        return dict(themes)
    
    def calculate_green_up(self, position: EnrichedPosition) -> GreenUpResult:
        """
        Calculate "Green Up" hedge to lock in profit.
        
        The goal: adjust position so you get the same payout regardless of outcome.
        
        Formula:
        - Current position: X shares of YES at avg cost C
        - Current YES price: P
        - To balance: buy Y shares of NO at (1-P)
        - Want: X*P = Y*(1-P) after accounting for costs
        
        Args:
            position: EnrichedPosition to hedge
            
        Returns:
            GreenUpResult with hedge recommendation
        """
        pos = position.position
        current_price = position.current_price
        opposite_price = 1 - current_price
        
        # Determine hedge side
        hedge_side = "NO" if pos.side == "YES" else "YES"
        
        # Current value if position wins
        payout_if_win = pos.size  # Pay $1 per share
        
        # Calculate hedge size needed
        # We want equal payout both ways
        # If YES wins: get pos.size USDC, spend hedge_cost
        # If NO wins: lose pos.cost_basis, get hedge_size USDC
        
        # Simplified: hedge_size * opposite_price should equal current_value
        # This makes it so both outcomes pay roughly the same
        hedge_size = position.current_value / opposite_price
        hedge_cost = hedge_size * opposite_price
        
        # Calculate guaranteed profit
        # Scenario 1: Original side wins
        profit_if_original_wins = payout_if_win - pos.cost_basis - hedge_cost
        
        # Scenario 2: Hedge side wins
        profit_if_hedge_wins = hedge_size - pos.cost_basis - hedge_cost
        
        # Guaranteed is the minimum of both
        guaranteed_profit = min(profit_if_original_wins, profit_if_hedge_wins)
        
        # Can only "green up" if there's positive profit to lock
        locked_in = guaranteed_profit > 0
        
        return GreenUpResult(
            condition_id=pos.condition_id,
            current_side=pos.side,
            current_size=pos.size,
            current_price=current_price,
            current_value=position.current_value,
            cost_basis=pos.cost_basis,
            hedge_side=hedge_side,
            hedge_size=hedge_size,
            hedge_cost=hedge_cost,
            guaranteed_profit=guaranteed_profit,
            locked_in=locked_in
        )
    
    def get_portfolio_summary(self, positions: List[EnrichedPosition]) -> Dict:
        """Get overall portfolio summary."""
        if not positions:
            return {
                "total_positions": 0,
                "total_cost": 0,
                "total_current_value": 0,
                "total_pnl": 0,
                "total_pnl_percent": 0
            }
        
        total_cost = sum(p.position.cost_basis for p in positions)
        total_current = sum(p.current_value for p in positions)
        total_pnl = total_current - total_cost
        
        return {
            "total_positions": len(positions),
            "total_cost": total_cost,
            "total_current_value": total_current,
            "total_pnl": total_pnl,
            "total_pnl_percent": (total_pnl / total_cost * 100) if total_cost > 0 else 0,
            "biggest_winner": max(positions, key=lambda p: p.pnl).to_dict() if positions else None,
            "biggest_loser": min(positions, key=lambda p: p.pnl).to_dict() if positions else None
        }
