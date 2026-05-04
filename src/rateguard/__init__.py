"""rateguard — local rate limiter for LLM API calls.

Provides two thread-safe rate-limiting primitives:

* :class:`TokenBucket` — classic token-bucket algorithm with burst support.
  Tokens are reserved at acquire time, so concurrent callers each receive a
  fair, non-overlapping wait estimate.

* :class:`SlidingWindow` — sliding-window counter that tracks call timestamps
  within a fixed interval.  When the window is full the caller must sleep and
  retry; no slot is reserved on a blocked call.

Both classes are pure-stdlib and safe to use from multiple threads.

Example::

    from rateguard import TokenBucket, SlidingWindow

    # Allow 10 requests/s, bursting up to 20.
    bucket = TokenBucket(rate=10.0, burst=20)
    wait = bucket.acquire(1)
    if wait:
        import time; time.sleep(wait)

    # At most 60 calls per 60-second window.
    window = SlidingWindow(max_calls=60, window_seconds=60.0)
    wait = window.acquire()
    if wait:
        import time; time.sleep(wait)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque

__version__ = "0.1.0"
__all__ = ["TokenBucket", "SlidingWindow", "__version__"]


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket starts full and refills at a constant *rate* up to a maximum
    *burst* capacity.  Each :meth:`acquire` call consumes tokens and returns
    the number of seconds the caller must sleep before the requested tokens
    are actually available.  A return value of ``0.0`` means immediate
    admission.

    Tokens are *reserved* at acquire time even when a wait is required, so
    concurrent callers each receive their own fair share of the wait without
    double-booking the same tokens.

    Args:
        rate: Tokens added per second.  Must be > 0.
        burst: Maximum token capacity and the largest single acquire allowed.
            Must be ≥ 1.

    Attributes:
        rate (float): Tokens added per second.
        burst (int): Maximum token capacity.

    Raises:
        ValueError: If *rate* ≤ 0 or *burst* < 1.

    Example::

        bucket = TokenBucket(rate=5.0, burst=10)
        wait = bucket.acquire(3)
        if wait:
            time.sleep(wait)
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
        """Consume *tokens* from the bucket.

        If sufficient tokens are available the call returns ``0.0`` and the
        tokens are deducted immediately.  Otherwise the deficit is reserved
        and the call returns the time in seconds the caller should sleep
        before the tokens will be available.

        Args:
            tokens: Number of tokens to consume.  Must satisfy
                ``1 ≤ tokens ≤ burst``.

        Returns:
            Seconds to sleep before the tokens are available.  ``0.0`` means
            the tokens were available immediately.

        Raises:
            ValueError: If *tokens* < 1 or *tokens* > :attr:`burst`.
        """
        if tokens < 1:
            raise ValueError(f"tokens must be at least 1, got {tokens!r}")
        if tokens > self.burst:
            raise ValueError(
                f"tokens ({tokens}) cannot exceed burst capacity ({self.burst})"
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

    @property
    def available_tokens(self) -> float:
        """Current token level, accounting for elapsed refill time.

        The value is clamped to ``[0.0, burst]``; it never reflects
        un-filled reservations as a negative number.

        Returns:
            Estimated number of tokens currently available.
        """
        with self._lock:
            self._refill(time.monotonic())
            return max(0.0, min(float(self.burst), self._tokens))

    def reset(self) -> None:
        """Refill the bucket to :attr:`burst` capacity immediately.

        Discards any pending reservations (negative internal balance).
        Thread-safe.
        """
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"TokenBucket(rate={self.rate!r}, burst={self.burst!r}, "
            f"available_tokens={self.available_tokens:.2f})"
        )


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks timestamps of recent calls within a rolling interval.  When the
    number of recorded calls reaches *max_calls* the next caller receives a
    positive wait time and **no slot is reserved**; the caller must sleep and
    call :meth:`acquire` again.

    Args:
        max_calls: Maximum number of calls allowed within *window_seconds*.
            Must be ≥ 1.
        window_seconds: Length of the observation window in seconds.
            Must be > 0.

    Attributes:
        max_calls (int): Maximum calls per window.
        window_seconds (float): Window length in seconds.

    Raises:
        ValueError: If *max_calls* < 1 or *window_seconds* ≤ 0.

    Example::

        window = SlidingWindow(max_calls=60, window_seconds=60.0)
        wait = window.acquire()
        if wait:
            time.sleep(wait)
            window.acquire()  # retry after sleeping
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
        """Attempt to record a call in the current window.

        If fewer than :attr:`max_calls` calls are present in the window the
        call is recorded and ``0.0`` is returned.  Otherwise the call is
        *not* recorded and the number of seconds until the oldest call
        expires is returned; the caller should sleep for that duration and
        retry.

        Returns:
            ``0.0`` if the call was admitted; otherwise the seconds to wait
            before retrying.
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                return 0.0
            oldest = self._calls[0]
            wait = (oldest + self.window_seconds) - now
            return wait if wait > 0.0 else 0.0

    @property
    def available_calls(self) -> int:
        """Number of calls that can be admitted right now.

        Returns:
            Remaining call slots in the current window.  ``0`` means the
            window is full and the next :meth:`acquire` will return a
            positive wait time.
        """
        with self._lock:
            self._evict(time.monotonic())
            return max(0, self.max_calls - len(self._calls))

    @property
    def used_calls(self) -> int:
        """Number of calls recorded in the current window.

        Returns:
            Count of calls that are still within the observation window.
        """
        with self._lock:
            self._evict(time.monotonic())
            return len(self._calls)

    def reset(self) -> None:
        """Clear all recorded calls, opening the full window immediately.

        Thread-safe.
        """
        with self._lock:
            self._calls.clear()

    def __repr__(self) -> str:  # pragma: no cover
        used = self.used_calls
        return (
            f"SlidingWindow(max_calls={self.max_calls!r}, "
            f"window_seconds={self.window_seconds!r}, "
            f"used={used}/{self.max_calls})"
        )
