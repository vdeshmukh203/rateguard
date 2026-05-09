"""rateguard - local rate limiter for LLM API calls.

Provides two thread-safe primitives, :class:`TokenBucket` and
:class:`SlidingWindow`.  Each ``acquire`` call returns the number of
seconds the caller should sleep before proceeding.  A return value of
zero means the caller may proceed immediately.

Example::

    from rateguard import TokenBucket, SlidingWindow
    import time

    bucket = TokenBucket(rate=10.0, burst=20)
    wait = bucket.acquire(1)
    if wait > 0:
        time.sleep(wait)
    # make the API call

    window = SlidingWindow(max_calls=60, window_seconds=60.0)
    wait = window.acquire()
    if wait > 0:
        time.sleep(wait)
    # make the API call
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any

__all__ = ["TokenBucket", "SlidingWindow"]
__version__ = "0.1.0"


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant *rate* up to a maximum *burst*
    capacity.  Callers acquire tokens before performing work.  If the
    bucket does not have enough tokens, :meth:`acquire` returns the
    number of seconds the caller should sleep before retrying.  The
    requested tokens are reserved immediately so concurrent callers
    each receive a fair, non-overlapping wait.

    Attributes:
        rate (float): Tokens added per second.
        burst (int): Maximum tokens the bucket can hold.

    Args:
        rate: Tokens added per second.  Must be greater than zero.
        burst: Maximum tokens the bucket can hold.  Must be at least one.

    Raises:
        ValueError: If *rate* is not positive or *burst* is less than one.
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

    def acquire(self, tokens: int = 1) -> float:
        """Try to consume *tokens* from the bucket.

        Returns ``0.0`` if the tokens were available immediately.
        Otherwise returns the wait time in seconds; the tokens are
        reserved so that back-to-back calls produce non-overlapping
        wait windows.

        Args:
            tokens: Number of tokens to consume.  Must be between one
                and *burst* (inclusive).

        Returns:
            Seconds to sleep before the requested tokens are available.
            Zero means available now.

        Raises:
            ValueError: If *tokens* is less than one or exceeds *burst*.
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

    def reset(self) -> None:
        """Restore the bucket to its initial, full state.

        Useful in tests and after reconfiguration.
        """
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current bucket state.

        Returns:
            A dictionary with keys ``tokens_available``, ``rate``, and
            ``burst``.  ``tokens_available`` may be fractional and is
            clamped to ``[0, burst]`` for readability (negative values
            represent already-reserved future tokens and are reported as
            zero here).
        """
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            return {
                "tokens_available": max(0.0, min(float(self.burst), self._tokens)),
                "rate": self.rate,
                "burst": self.burst,
            }

    def __repr__(self) -> str:  # pragma: no cover
        s = self.status()
        return (
            f"TokenBucket(rate={self.rate}, burst={self.burst}, "
            f"tokens_available={s['tokens_available']:.2f})"
        )


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks the timestamps of recent calls within a fixed-length window.
    Callers acquire a slot before performing work.  If the maximum
    number of calls has already been recorded in the window,
    :meth:`acquire` returns the number of seconds the caller should
    sleep before retrying.  No slot is reserved when the limit is
    exceeded, so the caller must call :meth:`acquire` again after
    sleeping.

    Attributes:
        max_calls (int): Maximum calls allowed within the window.
        window_seconds (float): Length of the window in seconds.

    Args:
        max_calls: Maximum calls allowed within the window.  Must be at
            least one.
        window_seconds: Length of the window in seconds.  Must be
            greater than zero.

    Raises:
        ValueError: If *max_calls* is less than one or *window_seconds*
            is not positive.
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
        returns the wait time in seconds until the oldest call exits the
        window; no slot is reserved and the caller must retry after
        sleeping.

        Returns:
            Seconds to sleep before retrying.  Zero means the call was
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

    def reset(self) -> None:
        """Clear all recorded calls from the window.

        Useful in tests and after reconfiguration.
        """
        with self._lock:
            self._calls.clear()

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current window state.

        Returns:
            A dictionary with keys ``calls_in_window``, ``max_calls``,
            and ``window_seconds``.
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            return {
                "calls_in_window": len(self._calls),
                "max_calls": self.max_calls,
                "window_seconds": self.window_seconds,
            }

    def __repr__(self) -> str:  # pragma: no cover
        s = self.status()
        return (
            f"SlidingWindow(max_calls={self.max_calls}, "
            f"window_seconds={self.window_seconds}, "
            f"calls_in_window={s['calls_in_window']})"
        )
