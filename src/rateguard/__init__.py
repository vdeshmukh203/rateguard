"""rateguard - local rate limiter for LLM API calls.

Provides two thread-safe primitives, TokenBucket and SlidingWindow.
Each acquire call returns the number of seconds the caller should sleep
before proceeding. A return value of zero means the caller may proceed
immediately.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque

__all__ = ["TokenBucket", "SlidingWindow"]


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant rate up to a maximum burst capacity.
    Callers acquire tokens before performing work. If the bucket does not
    have enough tokens, acquire returns the number of seconds the caller
    should sleep before retrying. The requested tokens are still reserved
    when the wait is positive so concurrent callers each see their own
    fair share of the wait.

    Attributes:
        rate: Tokens added per second.
        burst: Maximum tokens the bucket can hold.

    Args:
        rate: Tokens added per second. Must be greater than zero.
        burst: Maximum tokens the bucket can hold. Must be at least one.
    """

    def __init__(self, rate: float, burst: int) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self.rate: float = float(rate)
        self.burst: int = int(burst)
        self._tokens: float = float(burst)
        self._last: float = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(
                float(self.burst),
                self._tokens + elapsed * self.rate,
            )
            self._last = now

    def acquire(self, tokens: int = 1) -> float:
        """Try to consume tokens from the bucket.

        Returns zero if the tokens were available immediately. Otherwise
        returns the wait time in seconds the caller should sleep before
        retrying. The tokens are reserved either way.

        Args:
            tokens: Number of tokens to consume. Must be at least one and
                no greater than burst.

        Returns:
            The number of seconds to wait before the requested tokens
            are available. Zero means available now.
        """
        if tokens < 1:
            raise ValueError("tokens must be at least 1")
        if tokens > self.burst:
            raise ValueError("tokens cannot exceed burst capacity")
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            deficit = tokens - self._tokens
            wait = deficit / self.rate
            self._tokens -= tokens
            return wait


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks the timestamps of recent calls within a fixed-length window.
    Callers acquire a slot before performing work. If the maximum number
    of calls has already been recorded in the window, acquire returns
    the number of seconds the caller should sleep before retrying. When
    the limit is hit no slot is reserved, so the caller must call
    acquire again after sleeping.

    Attributes:
        max_calls: Maximum calls allowed within the window.
        window_seconds: Length of the window in seconds.

    Args:
        max_calls: Maximum calls allowed within the window. Must be at
            least one.
        window_seconds: Length of the window in seconds. Must be greater
            than zero.
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_calls: int = int(max_calls)
        self.window_seconds: float = float(window_seconds)
        self._calls: Deque[float] = deque()
        self._lock = threading.Lock()

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    def acquire(self) -> float:
        """Try to record a call in the window.

        Returns zero if the call was admitted immediately. Otherwise
        returns the wait time in seconds until the oldest call exits the
        window, after which the caller can retry.

        Returns:
            The number of seconds to wait before retrying. Zero means
            the call was admitted now.
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return 0.0
            oldest = self._calls[0]
            wait = (oldest + self.window_seconds) - now
            return wait if wait > 0 else 0.0
