"""rateguard - local rate limiter for LLM API calls.

Provides two thread-safe primitives, :class:`TokenBucket` and
:class:`SlidingWindow`. Each ``acquire`` call returns the number of seconds
the caller should sleep before proceeding. A return value of zero means the
caller may proceed immediately.

Typical usage::

    from rateguard import TokenBucket, SlidingWindow
    import time

    # Token-bucket: 10 requests/sec, burst up to 20
    bucket = TokenBucket(rate=10.0, burst=20)
    wait = bucket.acquire()
    if wait:
        time.sleep(wait)

    # Sliding-window: at most 60 calls per minute
    window = SlidingWindow(max_calls=60, window_seconds=60.0)
    while True:
        wait = window.acquire()
        if not wait:
            break
        time.sleep(wait)
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Tuple

__version__ = "0.2.0"
__all__ = [
    "TokenBucket",
    "SlidingWindow",
    "BucketStats",
    "WindowStats",
]


@dataclass(frozen=True)
class BucketStats:
    """Immutable snapshot of a :class:`TokenBucket`'s state and counters.

    Attributes:
        current_tokens: Token count at the moment of the snapshot, after
            applying any pending refill. May be negative when tokens have
            been reserved but not yet replenished.
        total_acquired: Total number of :meth:`~TokenBucket.acquire` calls
            that have returned (both immediate and deferred).
        total_waited: Number of :meth:`~TokenBucket.acquire` calls that
            returned a positive wait time (bucket was temporarily drained).
        total_wait_time: Cumulative wait time in seconds across all
            :meth:`~TokenBucket.acquire` calls.
    """

    current_tokens: float
    total_acquired: int
    total_waited: int
    total_wait_time: float


@dataclass(frozen=True)
class WindowStats:
    """Immutable snapshot of a :class:`SlidingWindow`'s state and counters.

    Attributes:
        current_calls: Calls recorded in the window at snapshot time,
            after evicting any expired entries.
        call_times: Monotonic timestamps (seconds) of all calls currently
            in the window, oldest first. Intended for visualisation.
        total_admitted: Number of :meth:`~SlidingWindow.acquire` calls that
            were admitted immediately (returned ``0.0``).
        total_blocked: Number of :meth:`~SlidingWindow.acquire` calls that
            were blocked (returned a positive wait time).
    """

    current_calls: int
    call_times: Tuple[float, ...]
    total_admitted: int
    total_blocked: int


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant rate up to a maximum burst capacity.
    Callers acquire tokens before performing work. If the bucket does not
    have enough tokens, :meth:`acquire` returns the number of seconds the
    caller should sleep before the requested tokens will be available.
    The requested tokens are reserved immediately so concurrent callers each
    receive their own fair share of the wait rather than racing.

    All public methods are thread-safe.

    Attributes:
        rate: Tokens added per second.
        burst: Maximum tokens the bucket can hold.

    Args:
        rate: Tokens added per second. Must be greater than zero.
        burst: Maximum tokens the bucket can hold. Must be at least one.

    Raises:
        ValueError: If *rate* is not positive or *burst* is less than one.

    Example::

        bucket = TokenBucket(rate=10.0, burst=20)
        wait = bucket.acquire()
        if wait:
            time.sleep(wait)
        # proceed with the API call
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
        self._total_acquired: int = 0
        self._total_waited: int = 0
        self._total_wait_time: float = 0.0

    def _refill(self, now: float) -> None:
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
            self._last = now

    def acquire(self, tokens: int = 1) -> float:
        """Try to consume tokens from the bucket.

        Returns zero if the tokens were available immediately. Otherwise
        returns the wait time in seconds the caller should sleep before the
        tokens will be ready. The tokens are reserved in both cases, giving
        concurrent callers a fair, distributed wait.

        Args:
            tokens: Number of tokens to consume. Must be between ``1`` and
                :attr:`burst` inclusive.

        Returns:
            Seconds to wait before the requested tokens are available.
            ``0.0`` means the call was served immediately.

        Raises:
            ValueError: If *tokens* is less than ``1`` or greater than
                :attr:`burst`.
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
                self._total_acquired += 1
                return 0.0
            deficit = tokens - self._tokens
            wait = deficit / self.rate
            self._tokens -= tokens
            self._total_acquired += 1
            self._total_waited += 1
            self._total_wait_time += wait
            return wait

    def stats(self) -> BucketStats:
        """Return a snapshot of the bucket's current state and counters.

        The snapshot reflects the token level after applying any pending
        refill up to the instant of the call.

        Returns:
            A :class:`BucketStats` instance with current readings.
        """
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            return BucketStats(
                current_tokens=self._tokens,
                total_acquired=self._total_acquired,
                total_waited=self._total_waited,
                total_wait_time=self._total_wait_time,
            )

    def reset(self) -> None:
        """Reset the bucket to full capacity and clear all counters.

        Useful for testing or for reinitialising a limiter between workloads
        without constructing a new instance.
        """
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()
            self._total_acquired = 0
            self._total_waited = 0
            self._total_wait_time = 0.0

    def __repr__(self) -> str:
        return f"TokenBucket(rate={self.rate!r}, burst={self.burst!r})"


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks the timestamps of recent calls within a fixed-length window.
    Callers acquire a slot before performing work. If the maximum number of
    calls has already been recorded in the window, :meth:`acquire` returns
    the number of seconds until the oldest call expires, after which the
    caller should retry. No slot is reserved when the call is blocked.

    All public methods are thread-safe.

    Attributes:
        max_calls: Maximum calls allowed within the window.
        window_seconds: Length of the window in seconds.

    Args:
        max_calls: Maximum calls allowed within the window. Must be at
            least one.
        window_seconds: Length of the window in seconds. Must be positive.

    Raises:
        ValueError: If *max_calls* is less than one or *window_seconds* is
            not positive.

    Example::

        window = SlidingWindow(max_calls=60, window_seconds=60.0)
        while True:
            wait = window.acquire()
            if not wait:
                break
            time.sleep(wait)
        # proceed with the API call
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
        self._total_admitted: int = 0
        self._total_blocked: int = 0

    def _evict(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    def acquire(self) -> float:
        """Try to record a call in the window.

        Returns zero if the call was admitted immediately. Otherwise returns
        the number of seconds until the oldest in-window call expires, after
        which the caller should retry. No slot is reserved when blocked.

        Returns:
            Seconds to wait before retrying. ``0.0`` means the call was
            admitted now.
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            if len(self._calls) < self.max_calls:
                self._calls.append(now)
                self._total_admitted += 1
                return 0.0
            oldest = self._calls[0]
            wait = (oldest + self.window_seconds) - now
            self._total_blocked += 1
            return wait if wait > 0 else 0.0

    def stats(self) -> WindowStats:
        """Return a snapshot of the window's current state and counters.

        Expired call records are evicted before the snapshot is taken.

        Returns:
            A :class:`WindowStats` instance with current readings.
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            return WindowStats(
                current_calls=len(self._calls),
                call_times=tuple(self._calls),
                total_admitted=self._total_admitted,
                total_blocked=self._total_blocked,
            )

    def reset(self) -> None:
        """Clear the window and reset all counters.

        Useful for testing or for reinitialising a limiter between workloads
        without constructing a new instance.
        """
        with self._lock:
            self._calls.clear()
            self._total_admitted = 0
            self._total_blocked = 0

    def __repr__(self) -> str:
        return (
            f"SlidingWindow(max_calls={self.max_calls!r}, "
            f"window_seconds={self.window_seconds!r})"
        )
