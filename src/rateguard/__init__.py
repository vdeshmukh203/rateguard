"""rateguard - local rate limiter for LLM API calls.

Provides two thread-safe primitives, ``TokenBucket`` and ``SlidingWindow``.
Each ``acquire`` call returns the number of seconds the caller should sleep
before proceeding.  A return value of zero means the caller may proceed
immediately.

Example::

    from rateguard import TokenBucket, SlidingWindow
    import time

    bucket = TokenBucket(rate=10.0, burst=20)
    wait = bucket.acquire(1)
    if wait:
        time.sleep(wait)

    window = SlidingWindow(max_calls=60, window_seconds=60.0)
    wait = window.acquire()
    if wait:
        time.sleep(wait)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque

__version__ = "0.1.0"
__all__ = ["TokenBucket", "SlidingWindow"]


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant rate up to a maximum burst capacity.
    Callers acquire tokens before performing work.  If the bucket does not
    have enough tokens, :meth:`acquire` returns the number of seconds the
    caller should sleep before retrying.  The requested tokens are reserved
    immediately—even when the wait is positive—so that concurrent callers
    each receive a fair, non-overlapping share of the wait.

    Args:
        rate: Tokens added per second.  Must be greater than zero.
        burst: Maximum tokens the bucket can hold.  Must be at least one.

    Raises:
        ValueError: If *rate* is not positive or *burst* is less than one.

    Example::

        >>> bucket = TokenBucket(rate=5.0, burst=10)
        >>> wait = bucket.acquire(1)
        >>> import time; time.sleep(wait) if wait else None
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
    # Public interface
    # ------------------------------------------------------------------

    @property
    def tokens_available(self) -> float:
        """Estimated tokens currently in the bucket (read-only).

        The value is computed under the lock using the elapsed time since
        the last refill, so it reflects the instantaneous state without
        modifying any internal counters.
        """
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last
            estimated = self._tokens + elapsed * self.rate if elapsed > 0 else self._tokens
            return max(0.0, min(float(self.burst), estimated))

    def acquire(self, tokens: int = 1) -> float:
        """Try to consume *tokens* from the bucket.

        Returns ``0.0`` if the tokens were available immediately.  Otherwise
        returns the wait time in seconds the caller should sleep before
        proceeding.  The tokens are reserved either way so that concurrent
        callers queue fairly.

        Args:
            tokens: Number of tokens to consume.  Must be at least one and
                no greater than *burst*.

        Returns:
            Seconds to wait before the requested tokens are available.
            Zero means the tokens were available immediately.

        Raises:
            ValueError: If *tokens* is less than one or greater than *burst*.
        """
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

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TokenBucket(rate={self.rate!r}, burst={self.burst!r},"
            f" tokens_available={self.tokens_available:.3f})"
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


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks the timestamps of recent calls within a fixed-length window.
    Callers acquire a slot before performing work.  If the maximum number
    of calls has already been recorded in the window, :meth:`acquire`
    returns the number of seconds the caller should sleep before retrying.
    No slot is reserved when the limit is hit, so the caller *must* call
    :meth:`acquire` again after sleeping.

    Args:
        max_calls: Maximum calls allowed within the window.  Must be at
            least one.
        window_seconds: Length of the window in seconds.  Must be greater
            than zero.

    Raises:
        ValueError: If *max_calls* is less than one or *window_seconds*
            is not positive.

    Example::

        >>> window = SlidingWindow(max_calls=10, window_seconds=1.0)
        >>> wait = window.acquire()
        >>> import time; time.sleep(wait) if wait else None
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
    # Public interface
    # ------------------------------------------------------------------

    @property
    def calls_in_window(self) -> int:
        """Number of calls currently recorded in the active window (read-only).

        Expired timestamps are evicted before returning the count, so the
        value reflects the instantaneous state.
        """
        with self._lock:
            self._evict(time.monotonic())
            return len(self._calls)

    def acquire(self) -> float:
        """Try to record a call in the window.

        Returns ``0.0`` if the call was admitted immediately.  Otherwise
        returns the number of seconds until the oldest call exits the
        window, after which the caller should retry.

        Returns:
            Seconds to wait before retrying.  Zero means the call was
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

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"SlidingWindow(max_calls={self.max_calls!r},"
            f" window_seconds={self.window_seconds!r},"
            f" calls_in_window={self.calls_in_window})"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()
