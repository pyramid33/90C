"""
AI-based price prediction module
Supports LSTM, Transformer, and ensemble models
"""
import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
import logging
from sklearn.preprocessing import MinMaxScaler
import pickle
import os

logger = logging.getLogger(__name__)


class AIPredictor:
    """AI-based price prediction using machine learning models"""
    
    def __init__(self, config: Dict):
        self.config = config
        self.model_type = config.get("model_type", "lstm")
        self.prediction_horizon = config.get("prediction_horizon", 1)
        self.confidence_threshold = config.get("confidence_threshold", 0.65)
        self.scaler = MinMaxScaler()
        self.model = None
        self.price_history: Dict[str, List[float]] = {}
        self.model_path = "models"
        
        # Initialize model directory
        os.makedirs(self.model_path, exist_ok=True)
        
        # Try to load existing model
        self._load_model()
    
    def _load_model(self):
        """Load pre-trained model if available"""
        model_file = os.path.join(self.model_path, f"{self.model_type}_model.pkl")
        if os.path.exists(model_file):
            try:
                with open(model_file, 'rb') as f:
                    self.model = pickle.load(f)
                logger.info(f"Loaded {self.model_type} model from {model_file}")
            except Exception as e:
                logger.warning(f"Could not load model: {e}")
    
    def update_price(self, condition_id: str, price: float):
        """Update price history"""
        if condition_id not in self.price_history:
            self.price_history[condition_id] = []
        
        self.price_history[condition_id].append(price)
        
        # Keep history for training/prediction
        max_history = 100
        if len(self.price_history[condition_id]) > max_history:
            self.price_history[condition_id] = self.price_history[condition_id][-max_history:]
    
    def prepare_features(self, condition_id: str, lookback: int = 20) -> Optional[np.ndarray]:
        """Prepare features for prediction"""
        if condition_id not in self.price_history:
            return None
        
        prices = np.array(self.price_history[condition_id])
        
        if len(prices) < lookback:
            return None
        
        # Use recent prices
        recent_prices = prices[-lookback:]
        
        # Create features: price, returns, volatility
        features = []
        for i in range(len(recent_prices) - 1):
            price = recent_prices[i]
            returns = (recent_prices[i+1] - recent_prices[i]) / recent_prices[i] if recent_prices[i] > 0 else 0
            features.append([price, returns])
        
        # Add volatility
        if len(recent_prices) > 5:
            volatility = np.std(recent_prices[-5:]) / np.mean(recent_prices[-5:]) if np.mean(recent_prices[-5:]) > 0 else 0
            features[-1].append(volatility)
        else:
            features[-1].append(0)
        
        return np.array(features)
    
    def predict_price(self, condition_id: str) -> Optional[Tuple[float, float]]:
        """
        Predict future price movement
        Returns: (predicted_change, confidence)
        """
        features = self.prepare_features(condition_id)
        
        if features is None or self.model is None:
            # Fallback to simple momentum-based prediction
            return self._simple_momentum_prediction(condition_id)
        
        try:
            # Normalize features
            features_scaled = self.scaler.transform(features.reshape(1, -1))
            
            # Predict (this is a placeholder - actual implementation would use trained model)
            # For now, use a simple heuristic
            prediction = self._simple_momentum_prediction(condition_id)
            
            return prediction
        except Exception as e:
            logger.error(f"Error in AI prediction: {e}")
            return self._simple_momentum_prediction(condition_id)
    
    def _simple_momentum_prediction(self, condition_id: str) -> Optional[Tuple[float, float]]:
        """Simple momentum-based prediction as fallback"""
        if condition_id not in self.price_history:
            return None
        
        prices = np.array(self.price_history[condition_id])
        
        if len(prices) < 5:
            return None
        
        # Calculate momentum
        recent_change = (prices[-1] - prices[-5]) / prices[-5] if prices[-5] > 0 else 0
        
        # Confidence based on consistency
        changes = np.diff(prices[-5:]) / prices[-5:-1]
        consistency = 1.0 - np.std(changes) if len(changes) > 0 else 0.5
        
        return (recent_change, consistency)
    
    def detect_signal(self, condition_id: str) -> Optional[Tuple[str, float]]:
        """
        Detect trading signal based on AI prediction
        Returns: (side, confidence)
        """
        prediction = self.predict_price(condition_id)
        
        if prediction is None:
            return None
        
        predicted_change, confidence = prediction
        
        if confidence < self.confidence_threshold:
            return None
        
        # Predict upward movement -> buy YES
        if predicted_change > 0.01:  # 1% upward prediction
            return ("YES", confidence)
        
        # Predict downward movement -> buy NO
        elif predicted_change < -0.01:  # 1% downward prediction
            return ("NO", confidence)
        
        return None
    
    def get_optimal_entry_price(self, condition_id: str, side: str, 
                                current_price: float, spread: float) -> float:
        """Calculate optimal entry price"""
        if side == "YES":
            return current_price * (1 - spread)
        else:
            return current_price * (1 + spread)
    
    def train_model(self, historical_data: pd.DataFrame):
        """
        Train the AI model on historical data
        This is a placeholder - implement actual training logic
        """
        logger.info("Training AI model...")
        # Placeholder for actual model training
        # In production, you would:
        # 1. Prepare training data
        # 2. Train LSTM/Transformer model
        # 3. Save the model
        logger.info("Model training not implemented - using fallback predictions")

