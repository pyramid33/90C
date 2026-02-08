"""
Enhanced Error Recovery and Retry Logic

Provides retry decorators, error classification, and resilience patterns
for the Polymarket trading bot.
"""
import functools
import logging
import random
import time
from typing import Callable, Optional, Tuple, Type, Union

import requests
from py_clob_client.exceptions import PolyApiException

logger = logging.getLogger(__name__)


class ErrorClassifier:
    """Classifies errors to determine if they should be retried"""
    
    # HTTP status codes that indicate transient errors (should retry)
    TRANSIENT_HTTP_CODES = {
        408,  # Request Timeout
        429,  # Too Many Requests (rate limit)
        500,  # Internal Server Error
        502,  # Bad Gateway
        503,  # Service Unavailable
        504,  # Gateway Timeout
    }
    
    # HTTP status codes that indicate permanent errors (don't retry)
    PERMANENT_HTTP_CODES = {
        400,  # Bad Request
        401,  # Unauthorized (except for credential refresh)
        403,  # Forbidden
        404,  # Not Found
        405,  # Method Not Allowed
        422,  # Unprocessable Entity
    }
    
    @classmethod
    def is_transient_error(cls, exception: Exception) -> bool:
        """
        Determine if an error is transient and should be retried.
        
        Args:
            exception: The exception to classify
        
        Returns:
            True if error is transient and should be retried
        """
        # Network/connection errors are transient
        if isinstance(exception, (
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout,
        )):
            return True
        
        # Check HTTP status codes
        if isinstance(exception, requests.exceptions.HTTPError):
            if hasattr(exception, 'response') and exception.response is not None:
                status_code = exception.response.status_code
                if status_code in cls.TRANSIENT_HTTP_CODES:
                    return True
                if status_code in cls.PERMANENT_HTTP_CODES:
                    return False
        
        # Polymarket API exceptions
        if isinstance(exception, PolyApiException):
            # Check status code if available
            if hasattr(exception, 'status_code'):
                status_code = exception.status_code
                if status_code in cls.TRANSIENT_HTTP_CODES:
                    return True
                if status_code == 401:  # Unauthorized - might be transient (credential refresh)
                    return True
                if status_code in cls.PERMANENT_HTTP_CODES:
                    return False
        
        # Default: treat unknown errors as non-transient (safer)
        return False
    
    @classmethod
    def should_retry(cls, exception: Exception, attempt: int, max_retries: int) -> bool:
        """
        Determine if a retry should be attempted.
        
        Args:
            exception: The exception that occurred
            attempt: Current attempt number (0-indexed)
            max_retries: Maximum number of retries allowed
        
        Returns:
            True if should retry
        """
        # Check if we've exceeded max retries
        if attempt >= max_retries:
            return False
        
        # Check if error is transient
        return cls.is_transient_error(exception)


def calculate_backoff_delay(
    attempt: int,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True
) -> float:
    """
    Calculate exponential backoff delay with optional jitter.
    
    Args:
        attempt: Current attempt number (0-indexed)
        initial_delay: Initial delay in seconds
        backoff_factor: Exponential backoff multiplier
        max_delay: Maximum delay cap
        jitter: Whether to add random jitter
    
    Returns:
        Delay in seconds
    """
    # Calculate exponential backoff: initial_delay * (backoff_factor ^ attempt)
    delay = initial_delay * (backoff_factor ** attempt)
    
    # Cap at max_delay
    delay = min(delay, max_delay)
    
    # Add jitter (randomize ±25% to prevent thundering herd)
    if jitter:
        jitter_range = delay * 0.25
        delay = delay + random.uniform(-jitter_range, jitter_range)
        # Ensure delay is positive
        delay = max(0.1, delay)
    
    return delay


def retry_with_backoff(
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    max_delay: float = 60.0,
    jitter: bool = True,
    exceptions: Union[Type[Exception], Tuple[Type[Exception], ...]] = Exception,
    retry_on_result: Optional[Callable] = None
):
    """
    Decorator for retrying functions with exponential backoff.
    
    Args:
        max_retries: Maximum number of retry attempts
        initial_delay: Initial delay between retries (seconds)
        backoff_factor: Multiplier for exponential backoff
        max_delay: Maximum delay cap (seconds)
        jitter: Whether to add random jitter to delays
        exceptions: Exception types to catch and retry
        retry_on_result: Optional function to check return value (retry if returns True)
    
    Example:
        @retry_with_backoff(max_retries=3, initial_delay=1.0)
        def unreliable_api_call():
            return requests.get("https://api.example.com/data")
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            for attempt in range(max_retries + 1):  # +1 for initial attempt
                try:
                    result = func(*args, **kwargs)
                    
                    # Check if we should retry based on result
                    if retry_on_result and retry_on_result(result):
                        if attempt < max_retries:
                            delay = calculate_backoff_delay(
                                attempt, initial_delay, backoff_factor, max_delay, jitter
                            )
                            logger.warning(
                                "RETRY: %s returned retry-worthy result, attempt %d/%d, retrying after %.2fs",
                                func.__name__, attempt + 1, max_retries + 1, delay
                            )
                            time.sleep(delay)
                            continue
                    
                    # Success!
                    if attempt > 0:
                        logger.info("RETRY: %s succeeded on attempt %d/%d",
                                   func.__name__, attempt + 1, max_retries + 1)
                    return result
                    
                except exceptions as e:
                    last_exception = e
                    
                    # Check if we should retry this error
                    if not ErrorClassifier.should_retry(e, attempt, max_retries):
                        logger.debug("RETRY: %s - Permanent error detected, not retrying: %s",
                                    func.__name__, str(e))
                        raise
                    
                    # Check if we have retries left
                    if attempt >= max_retries:
                        logger.error("RETRY: %s failed after %d attempts: %s",
                                    func.__name__, attempt + 1, str(e))
                        raise
                    
                    # Calculate delay and retry
                    delay = calculate_backoff_delay(
                        attempt, initial_delay, backoff_factor, max_delay, jitter
                    )
                    
                    logger.warning(
                        "RETRY: %s failed (attempt %d/%d), retrying after %.2fs: %s",
                        func.__name__, attempt + 1, max_retries + 1, delay, str(e)
                    )
                    time.sleep(delay)
            
            # Should not reach here, but just in case
            if last_exception:
                raise last_exception
            
        return wrapper
    return decorator


# Convenience decorators for common scenarios

def retry_on_network_error(max_retries: int = 3):
    """Retry only on network/connection errors"""
    return retry_with_backoff(
        max_retries=max_retries,
        initial_delay=1.0,
        exceptions=(
            requests.exceptions.ConnectionError,
            requests.exceptions.Timeout,
            requests.exceptions.ConnectTimeout,
            requests.exceptions.ReadTimeout,
        )
    )


def retry_on_api_error(max_retries: int = 3):
    """Retry on API errors and network errors"""
    return retry_with_backoff(
        max_retries=max_retries,
        initial_delay=1.0,
        exceptions=(
            requests.exceptions.RequestException,
            PolyApiException,
        )
    )


def retry_on_rate_limit(max_retries: int = 5, initial_delay: float = 2.0):
    """Retry specifically for rate limit errors with longer delays"""
    return retry_with_backoff(
        max_retries=max_retries,
        initial_delay=initial_delay,
        backoff_factor=2.0,
        max_delay=120.0,  # Up to 2 minutes for rate limits
        exceptions=(requests.exceptions.HTTPError, PolyApiException)
    )
