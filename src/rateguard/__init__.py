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

__all__ = ["TokenBucket", "SlidingWindow"]
__version__ = "0.1.0"


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant rate up to a maximum burst capacity.
    Callers acquire tokens before performing work. If the bucket does not
    have enough tokens, ``acquire`` returns the number of seconds the caller
    should sleep before retrying. The requested tokens are reserved regardless,
    so concurrent callers each receive their own fair share of the wait time
    rather than all waiting for the same future slot.

    Attributes:
        rate: Tokens added per second.
        burst: Maximum tokens the bucket can hold.

    Args:
        rate: Tokens added per second. Must be greater than zero.
        burst: Maximum tokens the bucket can hold. Must be at least one.

    Raises:
        ValueError: If ``rate`` is not positive or ``burst`` is less than one.

    Example:
        >>> bucket = TokenBucket(rate=10.0, burst=20)
        >>> wait = bucket.acquire(1)
        >>> if wait > 0:
        ...     import time; time.sleep(wait)
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

    # ------------------------------------------------------------------
    # Public inspection API
    # ------------------------------------------------------------------

    @property
    def tokens_available(self) -> float:
        """Current token count, clamped to ``[0, burst]``, after a refill.

        This is a snapshot; the value may change immediately after reading
        in a concurrent context.
        """
        with self._lock:
            self._refill(time.monotonic())
            return max(0.0, min(float(self.burst), self._tokens))

    def reset(self) -> None:
        """Reset the bucket to its full burst capacity."""
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    def __repr__(self) -> str:
        return (
            f"TokenBucket(rate={self.rate!r}, burst={self.burst!r}, "
            f"tokens_available={self.tokens_available:.2f})"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _refill(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(
                float(self.burst),
                self._tokens + elapsed * self.rate,
            )
            self._last = now

    # ------------------------------------------------------------------
    # Acquire
    # ------------------------------------------------------------------

    def acquire(self, tokens: int = 1) -> float:
        """Try to consume tokens from the bucket.

        Returns zero if the tokens were available immediately. Otherwise
        returns the wait time in seconds the caller should sleep before
        proceeding. The tokens are reserved in both cases, so back-to-back
        calls from concurrent threads share the future capacity fairly.

        Args:
            tokens: Number of tokens to consume. Must be at least one and
                no greater than ``burst``.

        Returns:
            Seconds to wait before the requested tokens are available.
            Zero means the tokens were available now.

        Raises:
            ValueError: If ``tokens`` is less than one or exceeds ``burst``.
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
    of calls has already been recorded in the window, ``acquire`` returns
    the number of seconds the caller should sleep before retrying.  No slot
    is reserved when the limit is hit, so the caller *must* call ``acquire``
    again after sleeping.

    Attributes:
        max_calls: Maximum calls allowed within the window.
        window_seconds: Length of the window in seconds.

    Args:
        max_calls: Maximum calls allowed within the window. Must be at
            least one.
        window_seconds: Length of the window in seconds. Must be greater
            than zero.

    Raises:
        ValueError: If ``max_calls`` is less than one or ``window_seconds``
            is not positive.

    Example:
        >>> sw = SlidingWindow(max_calls=60, window_seconds=60.0)
        >>> wait = sw.acquire()
        >>> if wait > 0:
        ...     import time; time.sleep(wait)
        ...     wait = sw.acquire()  # retry once
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self.max_calls: int = int(max_calls)
        self.window_seconds: float = float(window_seconds)
        self._calls: deque[float] = deque()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public inspection API
    # ------------------------------------------------------------------

    @property
    def calls_in_window(self) -> int:
        """Number of admitted calls currently inside the sliding window.

        This is a snapshot; the value may change immediately after reading
        in a concurrent context.
        """
        with self._lock:
            self._evict(time.monotonic())
            return len(self._calls)

    @property
    def slots_remaining(self) -> int:
        """Remaining call slots available in the current window.

        This is a snapshot; the value may change immediately after reading
        in a concurrent context.
        """
        return max(0, self.max_calls - self.calls_in_window)

    def reset(self) -> None:
        """Clear all recorded calls, effectively emptying the window."""
        with self._lock:
            self._calls.clear()

    def __repr__(self) -> str:
        in_window = self.calls_in_window
        return (
            f"SlidingWindow(max_calls={self.max_calls!r}, "
            f"window_seconds={self.window_seconds!r}, "
            f"calls_in_window={in_window})"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    # ------------------------------------------------------------------
    # Acquire
    # ------------------------------------------------------------------

    def acquire(self) -> float:
        """Try to record a call in the window.

        Returns zero if the call was admitted immediately. Otherwise
        returns the wait time in seconds until the oldest call exits the
        window, after which the caller should retry.

        Returns:
            Seconds to wait before retrying. Zero means the call was
            admitted now.
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
