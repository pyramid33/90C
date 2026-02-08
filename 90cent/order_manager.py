"""
Order management system with spread optimization and risk management
"""
import time
from typing import Dict, List, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)


class OrderManager:
    """Manages order placement, tracking, and optimization"""
    
    def __init__(self, client, config: Dict, risk_config: Dict):
        self.client = client
        self.config = config
        self.risk_config = risk_config
        self.open_orders: Dict[str, Dict] = {}
        self.order_history: List[Dict] = []
        self.daily_pnl = 0.0
        self.last_reset_date = datetime.now().date()
    
    def reset_daily_stats(self):
        """Reset daily statistics"""
        current_date = datetime.now().date()
        if current_date > self.last_reset_date:
            self.daily_pnl = 0.0
            self.last_reset_date = current_date
    
    def calculate_optimal_spread(self, condition_id: str, current_price: float, 
                                volatility: float = 0.01) -> float:
        """Calculate optimal spread based on market conditions"""
        base_spread = self.config.get("spread_percentage", 0.001)
        min_spread = self.config.get("min_spread", 0.0005)
        max_spread = self.config.get("max_spread", 0.005)
        
        # Adjust spread based on volatility
        adjusted_spread = base_spread * (1 + volatility)
        
        # Clamp to min/max
        spread = max(min_spread, min(adjusted_spread, max_spread))
        
        return spread
    
    def calculate_position_size(self, balance: float, confidence: float) -> float:
        """Calculate position size based on risk management rules"""
        base_size = self.config.get("position_size_percentage", 0.1)
        max_size = self.risk_config.get("max_position_size", 0.2)

        # Adjust size based on confidence
        adjusted_size = base_size * confidence

        # Clamp to max percentage
        position_pct = min(adjusted_size, max_size)
        position_value = balance * position_pct

        # Never request more than available balance
        return min(position_value, balance)
    
    def can_place_order(self) -> bool:
        """Check if we can place a new order"""
        self.reset_daily_stats()
        
        # Daily loss limit disabled (set to very negative value)
        max_daily_loss = self.risk_config.get("max_daily_loss", -999999.0)
        if max_daily_loss > -999999.0 and self.daily_pnl < -max_daily_loss:
            logger.warning("Daily loss limit reached (%.2f < %.2f), not placing new orders", 
                          self.daily_pnl, -max_daily_loss)
            return False
        
        return True
        
        return True
    
    def place_limit_order(self, condition_id: str, side: str, price: float, 
                         size: float, strategy: str = "unknown", 
                         time_in_force: str = "GTC", order_side: str = "BUY") -> Optional[Dict]:
        """
        Place a limit order with tracking. Returns dict with order_id and status.
        
        Args:
            condition_id: Market condition ID
            side: Order side ("YES" or "NO")
            price: Order price
            size: Order size
            strategy: Strategy name for tracking
            time_in_force: "GTC" (default), "FOK" (Fill-Or-Kill), "IOC"/"FAK" (Immediate-Or-Cancel)
            order_side: "BUY" (default) or "SELL"
        """
        # Daily loss limit disabled (set to very negative value)
        self.reset_daily_stats()
        max_daily_loss = self.risk_config.get("max_daily_loss", -999999.0)
        if max_daily_loss > -999999.0 and self.daily_pnl < -max_daily_loss:
            logger.warning("Daily loss limit reached (%.2f < %.2f), not placing new orders", 
                          self.daily_pnl, -max_daily_loss)
            return None
        
        try:
            logger.info(f"ORDER_MANAGER: Attempting to place {order_side} order: {side} {size} @ {price} for {condition_id}")
            logger.info(f"ORDER_MANAGER: Current open orders: {len(self.open_orders)}")
            order = self.client.place_limit_order(
                condition_id=condition_id,
                side=side,
                price=price,
                size=size,
                time_in_force=time_in_force,
                order_side=order_side
            )
            
            logger.info(f"ORDER_MANAGER: Order response received: type={type(order)}, value={order}")
            
            if order:
                # Check different possible response formats
                order_id = None
                if isinstance(order, dict):
                    # Try multiple possible keys for order ID
                    order_id = (order.get("id") or order.get("order_id") or order.get("orderId") or 
                               order.get("orderID") or order.get("order-id") or order.get("_id"))
                    # Also check nested structures
                    if not order_id and "data" in order:
                        data = order["data"]
                        if isinstance(data, dict):
                            order_id = (data.get("id") or data.get("order_id") or data.get("orderId"))
                    if not order_id and "result" in order:
                        result = order["result"]
                        if isinstance(result, dict):
                            order_id = (result.get("id") or result.get("order_id") or result.get("orderId"))
                elif isinstance(order, str):
                    order_id = order
                elif hasattr(order, "id"):
                    order_id = order.id
                elif hasattr(order, "order_id"):
                    order_id = order.order_id
                
                if order_id:
                    # Check if order was immediately matched (filled)
                    order_status = "open"
                    if isinstance(order, dict):
                        order_status = order.get("status", "open")
                    
                    self.open_orders[order_id] = {
                        "condition_id": condition_id,
                        "side": side,
                        "price": price,
                        "size": size,
                        "strategy": strategy,
                        "timestamp": datetime.now(),
                        "status": order_status
                    }
                    
                    status_emoji = "✅" if order_status == "matched" else "📝"
                    logger.info(f"{status_emoji} ORDER_MANAGER: Order placed: {order_id} - {side} {size} @ {price} (status={order_status})")
                    
                    # Return dict with order_id and status so caller can track matched orders
                    # FIX: Include size_matched and raw response for partial fill tracking
                    size_matched = 0.0
                    if isinstance(order, dict):
                        size_matched = float(order.get("size_matched") or order.get("matchedSize") or 0.0)
                        if order_status == "matched" and size_matched == 0:
                            size_matched = size

                    return {
                        "order_id": order_id, 
                        "status": order_status, 
                        "size": size, 
                        "price": price,
                        "size_matched": size_matched,
                        "_raw_response": order if isinstance(order, dict) else None
                    }
                else:
                    logger.error(f"❌ ORDER_MANAGER: Failed to place order: No order ID found in response. Response type: {type(order)}, Response: {order}")
                    return None
            else:
                logger.error("Failed to place order: client.place_limit_order returned None")
                return None
                
        except Exception as e:
            logger.error(f"Error placing order: {e}", exc_info=True)
            return None
    
    def place_batch_orders(self, orders: List[Dict], strategy: str = "unknown") -> List[Optional[Dict]]:
        """
        Place multiple orders in a single batch request (up to 15 orders per request).
        
        Args:
            orders: List of order dicts, each with keys: condition_id, side, price, size, time_in_force (optional)
                   Example: [{"condition_id": "...", "side": "YES", "price": 0.45, "size": 10.0, "time_in_force": "FOK"}, ...]
            strategy: Strategy name for tracking
        
        Returns:
            List of order response dicts with order_id and status, None for failed orders
        """
        self.reset_daily_stats()
        max_daily_loss = self.risk_config.get("max_daily_loss", -999999.0)
        if max_daily_loss > -999999.0 and self.daily_pnl < -max_daily_loss:
            logger.warning("Daily loss limit reached (%.2f < %.2f), not placing batch orders", 
                          self.daily_pnl, -max_daily_loss)
            return [None] * len(orders)
        
        # Check max open orders
        max_orders = self.config.get("max_open_orders", 20)
        orders_to_place = len(orders)
        if len(self.open_orders) + orders_to_place > max_orders:
            logger.warning("Batch orders (%d) would exceed max open orders (%d), cannot place batch", 
                          orders_to_place, max_orders)
            return [None] * len(orders)
        
        try:
            logger.info(f"ORDER_MANAGER: Attempting to place batch: {orders_to_place} orders")
            logger.info(f"ORDER_MANAGER: Current open orders: {len(self.open_orders)}/{max_orders}")
            
            # Call client's batch order method
            batch_results = self.client.place_batch_orders(orders)
            
            logger.info(f"ORDER_MANAGER: Batch order response received: {len(batch_results)} results")
            
            # Process each result and track orders
            processed_results = []
            for i, order_result in enumerate(batch_results):
                order_spec = orders[i]
                condition_id = order_spec.get("condition_id")
                side = order_spec.get("side", "").upper()
                price = float(order_spec.get("price", 0))
                size = float(order_spec.get("size", 0))
                
                if not order_result:
                    logger.error(f"ORDER_MANAGER: Batch order {i+1}/{orders_to_place} FAILED: {side} {size} @ {price} for {condition_id}")
                    processed_results.append(None)
                    continue
                
                # Extract order_id from response
                order_id = None
                if isinstance(order_result, dict):
                    order_id = (order_result.get("id") or order_result.get("order_id") or 
                               order_result.get("orderId") or order_result.get("orderID") or 
                               order_result.get("order-id") or order_result.get("_id"))
                    # Check nested structures
                    if not order_id and "data" in order_result:
                        data = order_result["data"]
                        if isinstance(data, dict):
                            order_id = (data.get("id") or data.get("order_id") or data.get("orderId"))
                    if not order_id and "result" in order_result:
                        result = order_result["result"]
                        if isinstance(result, dict):
                            order_id = (result.get("id") or result.get("order_id") or result.get("orderId"))
                elif isinstance(order_result, str):
                    order_id = order_result
                elif hasattr(order_result, "id"):
                    order_id = order_result.id
                elif hasattr(order_result, "order_id"):
                    order_id = order_result.order_id
                
                if order_id:
                    # Check if order was immediately matched (filled)
                    order_status = "open"
                    if isinstance(order_result, dict):
                        order_status = order_result.get("status", "open")
                    
                    self.open_orders[order_id] = {
                        "condition_id": condition_id,
                        "side": side,
                        "price": price,
                        "size": size,
                        "strategy": strategy,
                        "timestamp": datetime.now(),
                        "status": order_status
                    }
                    
                    status_emoji = "✅" if order_status == "matched" else "📝"
                    logger.info(f"{status_emoji} ORDER_MANAGER: Batch order {i+1}/{orders_to_place}: {order_id} - {side} {size} @ {price} (status={order_status})")
                    
                    processed_results.append({
                        "order_id": order_id,
                        "status": order_status,
                        "size": size,
                        "price": price,
                        "condition_id": condition_id,
                        "side": side
                    })
                else:
                    logger.error(f"❌ ORDER_MANAGER: Batch order {i+1}/{orders_to_place}: No order ID found in response. Response: {order_result}")
                    processed_results.append(None)
            
            return processed_results
            
        except Exception as e:
            logger.error(f"Error placing batch orders: {e}", exc_info=True)
            return [None] * len(orders)
    
    def cancel_order(self, order_id: str) -> bool:
        """Cancel an order"""
        try:
            success = self.client.cancel_order(order_id)
            if success and order_id in self.open_orders:
                self.open_orders[order_id]["status"] = "cancelled"
                self.open_orders[order_id]["cancelled_at"] = datetime.now()
            return success
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
            return False
    
    def cancel_stale_orders(self, timeout_seconds: int = 300):
        """Cancel orders that have been open too long"""
        current_time = datetime.now()
        stale_orders = []
        
        for order_id, order_info in self.open_orders.items():
            if order_info["status"] == "open":
                age = (current_time - order_info["timestamp"]).total_seconds()
                if age > timeout_seconds:
                    stale_orders.append(order_id)
        
        for order_id in stale_orders:
            logger.info(f"Cancelling stale order: {order_id}")
            self.cancel_order(order_id)
    
    def cancel_all_orders_for_market(self, condition_id: str) -> int:
        """Cancel all open orders for a specific market to free up collateral.
        
        Returns the number of orders cancelled.
        """
        cancelled_count = 0
        try:
            # Get open orders from exchange
            open_orders = self.client.get_open_orders()
            if not open_orders:
                return 0
            
            for order in open_orders:
                order_condition = order.get("asset_id") or order.get("condition_id") or order.get("market")
                if order_condition and condition_id.lower() in str(order_condition).lower():
                    order_id = order.get("id") or order.get("order_id")
                    if order_id:
                        if self.cancel_order(order_id):
                            cancelled_count += 1
                            logger.info(f"Cancelled order {order_id} for market {condition_id[:10]}...")
        except Exception as e:
            logger.error(f"Error cancelling orders for market {condition_id[:10]}: {e}")
        
        return cancelled_count
    
    def update_order_status(self):
        """Update order statuses from exchange"""
        try:
            open_orders = self.client.get_open_orders()
            exchange_order_ids = {order["id"] for order in open_orders}
            
            # Mark orders as filled if they're no longer in exchange
            for order_id in list(self.open_orders.keys()):
                if order_id not in exchange_order_ids:
                    if self.open_orders[order_id]["status"] == "open":
                        self.open_orders[order_id]["status"] = "filled"
                        self.open_orders[order_id]["filled_at"] = datetime.now()
                        logger.info(f"Order filled: {order_id}")
        except Exception as e:
            logger.error(f"Error updating order status: {e}")
    
    def get_open_orders_count(self) -> int:
        """Get count of open orders"""
        return len([o for o in self.open_orders.values() if o["status"] == "open"])

