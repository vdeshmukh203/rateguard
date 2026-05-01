"""rateguard — local rate limiter for LLM API calls.

Provides two thread-safe rate-limiting primitives:

* :class:`TokenBucket` — accumulates tokens at a fixed rate; admits burst
  traffic up to a configurable capacity.
* :class:`SlidingWindow` — admits at most *max_calls* requests in any
  rolling interval of *window_seconds* seconds.

Both classes follow the same convention: :meth:`acquire` returns the number
of seconds the caller should sleep before proceeding.  A return value of
zero means "proceed immediately".

Thread-safety model
-------------------
All state mutations are protected by a :class:`threading.Lock`.  A single
instance may be shared freely across threads; each call to :meth:`acquire`
is atomic.

Examples
--------
Token-bucket — 10 requests/sec, burst up to 20::

    import time
    from rateguard import TokenBucket

    bucket = TokenBucket(rate=10.0, burst=20)
    for _ in range(30):
        wait = bucket.acquire(1)
        if wait:
            time.sleep(wait)
        # … make the API call …

Sliding-window — at most 60 calls per minute::

    import time
    from rateguard import SlidingWindow

    window = SlidingWindow(max_calls=60, window_seconds=60.0)
    while True:
        wait = window.acquire()
        if wait:
            time.sleep(wait)
        else:
            break  # call admitted
    # … make the API call …
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Protocol, runtime_checkable

__all__ = ["RateLimiter", "TokenBucket", "SlidingWindow"]
__version__ = "0.1.0"


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class RateLimiter(Protocol):
    """Structural type satisfied by all rate limiters in this package.

    Any object that exposes a no-argument :meth:`acquire` returning ``float``
    and a no-argument :meth:`reset` returning ``None`` satisfies this protocol
    and can be used wherever a :class:`RateLimiter` is expected.
    """

    def acquire(self) -> float:
        """Request permission to proceed.

        Returns:
            Seconds the caller should sleep before proceeding.
            Zero means proceed immediately.
        """
        ...

    def reset(self) -> None:
        """Reset the limiter to its initial (fully available) state."""
        ...


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant *rate* (tokens per second) up to a
    maximum *burst* capacity.  Callers call :meth:`acquire` before
    performing work.  If enough tokens are available the call returns
    ``0.0`` immediately; otherwise it reserves the tokens and returns the
    number of seconds the caller should sleep before proceeding.  Reserving
    tokens on a blocked call ensures that concurrent callers each receive a
    fair, non-overlapping wait slice.

    Attributes:
        rate: Tokens added per second.
        burst: Maximum number of tokens the bucket can hold.

    Args:
        rate: Tokens added per second.  Must be greater than zero.
        burst: Maximum tokens the bucket can hold.  Must be at least one.

    Raises:
        ValueError: If *rate* ≤ 0 or *burst* < 1.

    Example::

        bucket = TokenBucket(rate=5.0, burst=10)
        wait = bucket.acquire(1)  # 0.0 when tokens are available
        if wait:
            time.sleep(wait)
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

    def acquire(self, tokens: int = 1) -> float:
        """Try to consume *tokens* from the bucket.

        Returns ``0.0`` if the tokens were available immediately.  Otherwise
        reserves the tokens and returns the number of seconds the caller
        should sleep before proceeding.

        Args:
            tokens: Number of tokens to consume.  Must be ≥ 1 and ≤ burst.

        Returns:
            Seconds to wait before the requested tokens are ready.
            Zero means the tokens are available now.

        Raises:
            ValueError: If *tokens* < 1 or *tokens* > burst.

        Example::

            bucket = TokenBucket(rate=10.0, burst=5)
            wait = bucket.acquire(1)
            if wait:
                time.sleep(wait)
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

    @property
    def tokens_available(self) -> float:
        """Current number of tokens available for immediate use (≥ 0).

        Triggers a refill based on elapsed time before returning.  Never
        returns a negative value even when reservations have pushed the
        internal counter below zero.

        Example::

            bucket = TokenBucket(rate=1.0, burst=5)
            bucket.acquire(5)
            print(bucket.tokens_available)  # close to 0.0
        """
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            return max(0.0, self._tokens)

    def reset(self) -> None:
        """Reset the bucket to full capacity, discarding any reservations.

        Example::

            bucket = TokenBucket(rate=1.0, burst=5)
            bucket.acquire(5)
            bucket.reset()
            assert bucket.tokens_available == 5.0
        """
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    def __repr__(self) -> str:
        return f"TokenBucket(rate={self.rate!r}, burst={self.burst!r})"


# ---------------------------------------------------------------------------
# SlidingWindow
# ---------------------------------------------------------------------------


class SlidingWindow:
    """Sliding-window rate limiter.

    Records the timestamps of admitted calls and rejects calls that would
    exceed *max_calls* within any rolling interval of *window_seconds*
    seconds.  When the window is full :meth:`acquire` returns the number
    of seconds until the oldest recorded call expires, after which the
    caller should retry.

    Unlike :class:`TokenBucket`, no slot is reserved on a blocked call;
    the caller must call :meth:`acquire` again after sleeping.

    Attributes:
        max_calls: Maximum calls allowed within the window.
        window_seconds: Length of the sliding window in seconds.

    Args:
        max_calls: Maximum calls allowed within the window.  Must be ≥ 1.
        window_seconds: Length of the window in seconds.  Must be > 0.

    Raises:
        ValueError: If *max_calls* < 1 or *window_seconds* ≤ 0.

    Example::

        window = SlidingWindow(max_calls=60, window_seconds=60.0)
        while True:
            wait = window.acquire()
            if not wait:
                break  # admitted
            time.sleep(wait)
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

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    # ------------------------------------------------------------------
    # Public API

    def acquire(self) -> float:
        """Try to record a call in the window.

        Returns ``0.0`` if the call was admitted immediately.  Otherwise
        returns the number of seconds until the oldest in-window call
        expires, after which the caller should call :meth:`acquire` again.
        No slot is reserved when the window is full.

        Returns:
            Seconds to wait before retrying.  Zero means the call was
            admitted now.

        Example::

            window = SlidingWindow(max_calls=3, window_seconds=1.0)
            for _ in range(3):
                assert window.acquire() == 0.0
            wait = window.acquire()
            assert wait > 0.0  # window is full
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

    @property
    def calls_in_window(self) -> int:
        """Number of calls currently recorded within the active window.

        Evicts stale timestamps before counting.

        Example::

            window = SlidingWindow(max_calls=5, window_seconds=1.0)
            window.acquire()
            window.acquire()
            assert window.calls_in_window == 2
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            return len(self._calls)

    @property
    def slots_remaining(self) -> int:
        """Number of additional calls that can be admitted right now.

        Evicts stale timestamps before computing the count.

        Example::

            window = SlidingWindow(max_calls=3, window_seconds=1.0)
            window.acquire()
            assert window.slots_remaining == 2
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            return max(0, self.max_calls - len(self._calls))

    def call_ages(self) -> list[float]:
        """Return the elapsed age (seconds) of each call currently in the window.

        Ages are ordered chronologically from oldest (largest value) to
        newest (smallest value), matching the internal recording order.
        Evicts stale timestamps before computing ages.

        Returns:
            A list of floats representing how many seconds ago each call
            was admitted.  Empty when no calls are in the window.

        Example::

            window = SlidingWindow(max_calls=5, window_seconds=10.0)
            window.acquire()
            ages = window.call_ages()
            assert len(ages) == 1
            assert 0.0 <= ages[0] < 10.0
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            return [now - t for t in self._calls]

    def reset(self) -> None:
        """Clear all recorded calls from the window.

        Example::

            window = SlidingWindow(max_calls=2, window_seconds=10.0)
            window.acquire()
            window.reset()
            assert window.calls_in_window == 0
        """
        with self._lock:
            self._calls.clear()

    def __repr__(self) -> str:
        return (
            f"SlidingWindow(max_calls={self.max_calls!r}, "
            f"window_seconds={self.window_seconds!r})"
        )
