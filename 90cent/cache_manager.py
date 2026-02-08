"""
TTL-Based Cache Manager for Performance Optimization

Provides time-based caching with automatic expiration
to reduce redundant API calls.
"""
import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TTLCache:
    """
    Time-To-Live cache with automatic expiration.
    
    Stores key-value pairs with configurable expiration times.
    Automatically invalidates entries when TTL expires.
    """
    
    def __init__(self, default_ttl: float = 5.0, name: str = "cache"):
        """
        Initialize TTL cache.
        
        Args:
            default_ttl: Default time-to-live in seconds
            name: Cache name for logging
        """
        self._cache: Dict[str, Dict[str, Any]] = {}
        self.default_ttl = default_ttl
        self.name = name
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "invalidations": 0,
            "expirations": 0
        }
    
    def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache if not expired.
        
        Args:
            key: Cache key
        
        Returns:
            Cached value if exists and not expired, None otherwise
        """
        if key not in self._cache:
            self._stats["misses"] += 1
            return None
        
        entry = self._cache[key]
        age = time.time() - entry["timestamp"]
        
        # Check if expired
        if age >= entry["ttl"]:
            # Expired - remove and return None
            del self._cache[key]
            self._stats["expirations"] += 1
            self._stats["misses"] += 1
            logger.debug("CACHE [%s]: Expired key '%s' (age=%.2fs, ttl=%.2fs)",
                        self.name, key, age, entry["ttl"])
            return None
        
        # Cache hit!
        self._stats["hits"] += 1
        logger.debug("CACHE [%s]: HIT on key '%s' (age=%.2fs, ttl=%.2fs)",
                    self.name, key, age, entry["ttl"])
        return entry["data"]
    
    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> None:
        """
        Store value in cache with TTL.
        
        Args:
            key: Cache key
            value: Value to cache
            ttl: Time-to-live in seconds (uses default if None)
        """
        self._cache[key] = {
            "data": value,
            "timestamp": time.time(),
            "ttl": ttl if ttl is not None else self.default_ttl
        }
        self._stats["sets"] += 1
        logger.debug("CACHE [%s]: SET key '%s' (ttl=%.2fs)",
                    self.name, key, ttl or self.default_ttl)
    
    def invalidate(self, key: str) -> bool:
        """
        Force invalidation of cache entry.
        
        Args:
            key: Cache key to invalidate
        
        Returns:
            True if key was in cache, False otherwise
        """
        if key in self._cache:
            del self._cache[key]
            self._stats["invalidations"] += 1
            logger.debug("CACHE [%s]: INVALIDATED key '%s'", self.name, key)
            return True
        return False
    
    def invalidate_pattern(self, pattern: str) -> int:
        """
        Invalidate all keys matching pattern.
        
        Args:
            pattern: Pattern to match (supports * wildcard)
        
        Returns:
            Number of keys invalidated
        """
        count = 0
        keys_to_delete = []
        
        # Simple pattern matching with * wildcard
        if "*" in pattern:
            prefix = pattern.split("*")[0]
            for key in self._cache.keys():
                if key.startswith(prefix):
                    keys_to_delete.append(key)
        else:
            # Exact match
            if pattern in self._cache:
                keys_to_delete.append(pattern)
        
        # Delete matched keys
        for key in keys_to_delete:
            del self._cache[key]
            count += 1
        
        if count > 0:
            self._stats["invalidations"] += count
            logger.debug("CACHE [%s]: INVALIDATED %d keys matching '%s'",
                        self.name, count, pattern)
        
        return count
    
    def clear(self) -> int:
        """
        Clear entire cache.
        
        Returns:
            Number of entries cleared
        """
        count = len(self._cache)
        self._cache.clear()
        logger.info("CACHE [%s]: CLEARED %d entries", self.name, count)
        return count
    
    def cleanup_expired(self) -> int:
        """
        Remove all expired entries.
        
        Returns:
            Number of expired entries removed
        """
        current_time = time.time()
        expired_keys = []
        
        for key, entry in self._cache.items():
            age = current_time - entry["timestamp"]
            if age >= entry["ttl"]:
                expired_keys.append(key)
        
        # Remove expired
        for key in expired_keys:
            del self._cache[key]
        
        if expired_keys:
            self._stats["expirations"] += len(expired_keys)
            logger.debug("CACHE [%s]: Cleaned up %d expired entries",
                        self.name, len(expired_keys))
        
        return len(expired_keys)
    
    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.
        
        Returns:
            Dictionary with cache statistics
        """
        total_requests = self._stats["hits"] + self._stats["misses"]
        hit_rate = (self._stats["hits"] / total_requests * 100) if total_requests > 0 else 0
        
        return {
            "name": self.name,
            "size": len(self._cache),
            "hits": self._stats["hits"],
            "misses": self._stats["misses"],
            "sets": self._stats["sets"],
            "invalidations": self._stats["invalidations"],
            "expirations": self._stats["expirations"],
            "hit_rate_pct": hit_rate,
            "total_requests": total_requests
        }
    
    def reset_stats(self) -> None:
        """Reset statistics counters"""
        self._stats = {
            "hits": 0,
            "misses": 0,
            "sets": 0,
            "invalidations": 0,
            "expirations": 0
        }
        logger.info("CACHE [%s]: Statistics reset", self.name)
    
    def peek(self, key: str) -> Optional[Dict[str, Any]]:
        """
        Peek at cache entry without affecting statistics.
        
        Args:
            key: Cache key
        
        Returns:
            Cache entry info or None
        """
        if key in self._cache:
            entry = self._cache[key]
            age = time.time() - entry["timestamp"]
            return {
                "age": age,
                "ttl": entry["ttl"],
                "expired": age >= entry["ttl"],
                "data": entry["data"]
            }
        return None
