"""rateguard - local rate limiter for LLM API calls.

Provides two thread-safe primitives, ``TokenBucket`` and ``SlidingWindow``,
both built on the Python standard library with no third-party dependencies.
Each ``acquire`` call returns the number of seconds the caller should sleep
before proceeding. A return value of zero means the caller may proceed
immediately.

Typical usage::

    from rateguard import TokenBucket, SlidingWindow
    import time

    # 10 requests/sec, burst up to 20
    bucket = TokenBucket(rate=10.0, burst=20)
    wait = bucket.acquire()
    if wait:
        time.sleep(wait)
    # ... make the API call ...

    # At most 60 calls per 60-second window
    window = SlidingWindow(max_calls=60, window_seconds=60.0)
    wait = window.acquire()
    if wait:
        time.sleep(wait)
        window.acquire()  # retry after sleeping
    # ... make the API call ...
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque

__all__ = ["TokenBucket", "SlidingWindow", "__version__"]

__version__ = "0.2.0"


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant rate up to a maximum burst capacity.
    Callers acquire tokens before performing work. If the bucket does not
    have enough tokens, ``acquire`` returns the number of seconds the caller
    should sleep before retrying. The requested tokens are still reserved
    when the wait is positive so concurrent callers each receive their own
    fair share of the wait time.

    Example::

        bucket = TokenBucket(rate=10.0, burst=20)
        wait = bucket.acquire()
        if wait:
            time.sleep(wait)

    Attributes:
        rate: Tokens added per second.
        burst: Maximum tokens the bucket can hold.

    Args:
        rate: Tokens added per second. Must be greater than zero.
        burst: Maximum tokens the bucket can hold. Must be at least one.

    Raises:
        ValueError: If *rate* is not positive or *burst* is less than one.
    """

    def __init__(self, rate: float, burst: int) -> None:
        if rate <= 0:
            raise ValueError(f"rate must be positive, got {rate!r}")
        if burst < 1:
            raise ValueError(f"burst must be at least 1, got {burst!r}")
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

        Returns ``0.0`` if the tokens were available immediately. Otherwise
        returns the wait time in seconds the caller should sleep before
        retrying. The tokens are reserved either way so that concurrent
        callers each see a distinct, fair wait.

        Args:
            tokens: Integer number of tokens to consume. Must be at least
                one and no greater than *burst*.

        Returns:
            Seconds to wait before the requested tokens are available.
            Zero means available now.

        Raises:
            TypeError: If *tokens* is not an integer.
            ValueError: If *tokens* is less than one or exceeds *burst*.
        """
        if not isinstance(tokens, int):
            raise TypeError(
                f"tokens must be an integer, got {type(tokens).__name__!r}"
            )
        if tokens < 1:
            raise ValueError(f"tokens must be at least 1, got {tokens!r}")
        if tokens > self.burst:
            raise ValueError(
                f"tokens ({tokens!r}) cannot exceed burst capacity ({self.burst!r})"
            )
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

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current bucket state.

        The token count is computed using the elapsed time since the last
        operation without modifying internal state.

        Returns:
            A dict with keys ``"tokens"`` (current fill level),
            ``"burst"`` (capacity), and ``"rate"`` (tokens/sec).
        """
        with self._lock:
            elapsed = time.monotonic() - self._last
            current = min(float(self.burst), self._tokens + max(0.0, elapsed) * self.rate)
            return {
                "tokens": round(current, 6),
                "burst": self.burst,
                "rate": self.rate,
            }

    def reset(self) -> None:
        """Refill the bucket to full burst capacity."""
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    def __repr__(self) -> str:
        return f"TokenBucket(rate={self.rate}, burst={self.burst})"


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks the timestamps of recent calls within a fixed-length window.
    Callers acquire a slot before performing work. If the maximum number
    of calls has already been recorded in the window, ``acquire`` returns
    the number of seconds the caller should sleep before retrying.

    Unlike :class:`TokenBucket`, a blocked call does **not** reserve a slot,
    so the caller must call ``acquire`` again after sleeping. This avoids
    quota-locking when many threads are waiting on the same window.

    Example::

        window = SlidingWindow(max_calls=60, window_seconds=60.0)
        while True:
            wait = window.acquire()
            if not wait:
                break
            time.sleep(wait)

    Attributes:
        max_calls: Maximum calls allowed within the window.
        window_seconds: Length of the window in seconds.

    Args:
        max_calls: Maximum calls allowed within the window. Must be at
            least one.
        window_seconds: Length of the window in seconds. Must be greater
            than zero.

    Raises:
        ValueError: If *max_calls* is less than one or *window_seconds* is
            not positive.
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        if max_calls < 1:
            raise ValueError(f"max_calls must be at least 1, got {max_calls!r}")
        if window_seconds <= 0:
            raise ValueError(
                f"window_seconds must be positive, got {window_seconds!r}"
            )
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

        Returns ``0.0`` if the call was admitted immediately. Otherwise
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

    def status(self) -> dict[str, Any]:
        """Return a snapshot of the current window state.

        Expired calls are excluded from the count without being evicted
        from internal storage.

        Returns:
            A dict with keys:

            - ``"current_calls"``: active calls within the window
            - ``"max_calls"``: configured limit
            - ``"window_seconds"``: configured window length
            - ``"call_ages"``: sorted list of ages (seconds since each
              admitted call), youngest first
        """
        with self._lock:
            now = time.monotonic()
            cutoff = now - self.window_seconds
            active = [t for t in self._calls if t > cutoff]
            ages = sorted(now - t for t in active)
            return {
                "current_calls": len(active),
                "max_calls": self.max_calls,
                "window_seconds": self.window_seconds,
                "call_ages": ages,
            }

    def reset(self) -> None:
        """Clear all recorded calls from the window."""
        with self._lock:
            self._calls.clear()

    def __repr__(self) -> str:
        return (
            f"SlidingWindow(max_calls={self.max_calls}, "
            f"window_seconds={self.window_seconds})"
        )
