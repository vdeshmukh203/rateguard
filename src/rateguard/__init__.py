"""rateguard - local rate limiter for LLM API calls.

Provides two thread-safe primitives, :class:`TokenBucket` and
:class:`SlidingWindow`.  Each ``acquire`` call returns the number of seconds
the caller should sleep before proceeding; zero means proceed immediately.

Example::

    >>> from rateguard import TokenBucket, SlidingWindow
    >>> bucket = TokenBucket(rate=10.0, burst=5)
    >>> wait = bucket.acquire()
    >>> if wait:
    ...     import time; time.sleep(wait)

    >>> window = SlidingWindow(max_calls=60, window_seconds=60.0)
    >>> wait = window.acquire()
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Dict, Union

__version__ = "0.2.0"
__all__ = ["TokenBucket", "SlidingWindow", "__version__"]


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant ``rate`` up to a maximum ``burst``
    capacity.  Callers acquire tokens before performing work.  If the bucket
    does not hold enough tokens, :meth:`acquire` returns the number of seconds
    the caller should sleep before proceeding.  The requested tokens are
    reserved immediately so concurrent callers each receive a fair share of
    the wait time.

    Attributes:
        rate: Tokens added per second.
        burst: Maximum tokens the bucket can hold.

    Args:
        rate: Tokens added per second.  Must be greater than zero.
        burst: Maximum tokens the bucket can hold.  Must be at least one.

    Raises:
        ValueError: If *rate* ≤ 0 or *burst* < 1.

    Example::

        >>> bucket = TokenBucket(rate=10.0, burst=20)
        >>> wait = bucket.acquire(5)
        >>> import time; time.sleep(wait)  # 0 s if tokens were available
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
    # Public API
    # ------------------------------------------------------------------

    def acquire(self, tokens: Union[int, float] = 1) -> float:
        """Try to consume *tokens* from the bucket.

        Returns ``0.0`` if the tokens were available immediately.  Otherwise
        returns the wait time in seconds; the tokens are reserved either way
        so the caller may sleep and then proceed without re-calling.

        Args:
            tokens: Number of tokens to consume.  Must be positive and no
                greater than :attr:`burst`.

        Returns:
            Seconds to wait before the requested tokens are available.
            ``0.0`` means available now.

        Raises:
            ValueError: If *tokens* ≤ 0 or *tokens* > *burst*.
        """
        if tokens <= 0:
            raise ValueError("tokens must be positive")
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

    def stats(self) -> Dict[str, Any]:
        """Return a non-mutating snapshot of the bucket state.

        Returns:
            Dictionary with keys ``tokens`` (current available, clamped to
            zero), ``rate``, ``burst``, and ``fill_ratio`` (0.0–1.0).
        """
        with self._lock:
            elapsed = time.monotonic() - self._last
            projected = min(float(self.burst), self._tokens + elapsed * self.rate)
            available = max(0.0, projected)
            return {
                "tokens": available,
                "rate": self.rate,
                "burst": self.burst,
                "fill_ratio": available / self.burst,
            }

    def reset(self) -> None:
        """Refill the bucket to full capacity and reset the refill clock."""
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    def __repr__(self) -> str:
        return f"TokenBucket(rate={self.rate!r}, burst={self.burst!r})"


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks the timestamps of recent calls within a rolling window of length
    ``window_seconds``.  Callers acquire a slot before performing work.  If
    the maximum number of calls has already been recorded in the window,
    :meth:`acquire` returns the number of seconds the caller should sleep
    before retrying.  When the limit is hit no slot is reserved, so the
    caller **must** call :meth:`acquire` again after sleeping.

    Attributes:
        max_calls: Maximum calls allowed within the window.
        window_seconds: Length of the window in seconds.

    Args:
        max_calls: Maximum calls allowed within the window.  Must be at
            least one.
        window_seconds: Length of the window in seconds.  Must be greater
            than zero.

    Raises:
        ValueError: If *max_calls* < 1 or *window_seconds* ≤ 0.

    Example::

        >>> window = SlidingWindow(max_calls=60, window_seconds=60.0)
        >>> wait = window.acquire()
        >>> import time; time.sleep(wait)  # 0 s when under the limit
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def acquire(self) -> float:
        """Try to record a call in the window.

        Returns ``0.0`` if the call was admitted immediately.  Otherwise
        returns the number of seconds until the oldest call exits the
        window, after which the caller should retry.

        Returns:
            Seconds to wait before retrying.  ``0.0`` means admitted now.
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

    def stats(self) -> Dict[str, Any]:
        """Return a non-mutating snapshot of the window state.

        Returns:
            Dictionary with keys ``calls_in_window``, ``max_calls``,
            ``window_seconds``, and ``available`` (slots remaining).
        """
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            count = sum(1 for t in self._calls if t > cutoff)
            return {
                "calls_in_window": count,
                "max_calls": self.max_calls,
                "window_seconds": self.window_seconds,
                "available": max(0, self.max_calls - count),
            }

    def reset(self) -> None:
        """Clear all recorded calls from the window."""
        with self._lock:
            self._calls.clear()

    def __repr__(self) -> str:
        return (
            f"SlidingWindow(max_calls={self.max_calls!r}, "
            f"window_seconds={self.window_seconds!r})"
        )
