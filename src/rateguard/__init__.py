"""rateguard - local rate limiter for LLM API calls.

Provides two thread-safe primitives, :class:`TokenBucket` and
:class:`SlidingWindow`.  Each ``acquire`` call returns the number of seconds
the caller should sleep before proceeding; zero means the caller may proceed
immediately.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, NamedTuple

__version__ = "0.1.0"
__all__ = ["TokenBucket", "TokenBucketStatus", "SlidingWindow", "SlidingWindowStatus"]


# ---------------------------------------------------------------------------
# Status snapshots
# ---------------------------------------------------------------------------


class TokenBucketStatus(NamedTuple):
    """Immutable snapshot of a :class:`TokenBucket`'s state."""

    tokens: float
    """Current token count (may be fractional and temporarily negative)."""
    rate: float
    """Configured replenishment rate in tokens per second."""
    burst: int
    """Maximum token capacity."""


class SlidingWindowStatus(NamedTuple):
    """Immutable snapshot of a :class:`SlidingWindow`'s state."""

    calls_in_window: int
    """Number of admitted calls currently within the window."""
    max_calls: int
    """Configured maximum calls allowed per window."""
    window_seconds: float
    """Configured window length in seconds."""


# ---------------------------------------------------------------------------
# TokenBucket
# ---------------------------------------------------------------------------


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket refills at a constant *rate* up to a maximum *burst* capacity.
    Callers call :meth:`acquire` before performing work.  If the bucket does
    not have enough tokens, ``acquire`` returns the number of seconds the
    caller should sleep before proceeding.  The requested tokens are reserved
    immediately so concurrent callers each see a fair share of the wait time
    (reservation-on-block prevents thundering-herd retries).

    Args:
        rate: Tokens added per second.  Must be greater than zero.
        burst: Maximum tokens the bucket can hold.  Must be at least one.

    Raises:
        ValueError: If *rate* is not positive or *burst* is less than one.

    Example:
        >>> import time
        >>> from rateguard import TokenBucket
        >>> bucket = TokenBucket(rate=10.0, burst=20)
        >>> wait = bucket.acquire(1)
        >>> if wait > 0:
        ...     time.sleep(wait)
        >>> # proceed with the API call
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

        Returns ``0.0`` if the tokens were available immediately.  Otherwise
        returns the positive wait time in seconds; the caller should sleep for
        that duration before proceeding.  The tokens are reserved either way
        so that concurrent callers each get a fair share of the budget.

        Args:
            tokens: Number of tokens to consume.  Must be at least ``1`` and
                no greater than :attr:`burst`.

        Returns:
            Seconds to wait before the acquired tokens are available.
            ``0.0`` means proceed immediately.

        Raises:
            ValueError: If *tokens* is less than ``1`` or exceeds
                :attr:`burst`.
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

    def status(self) -> TokenBucketStatus:
        """Return an immutable snapshot of the current limiter state.

        The snapshot is taken under the internal lock so the values are
        mutually consistent.  Reading :attr:`~TokenBucketStatus.tokens` does
        **not** consume any tokens.

        Returns:
            A :class:`TokenBucketStatus` named tuple.
        """
        with self._lock:
            now = time.monotonic()
            self._refill(now)
            return TokenBucketStatus(
                tokens=self._tokens,
                rate=self.rate,
                burst=self.burst,
            )

    def reset(self) -> None:
        """Refill the bucket to full burst capacity.

        Useful in tests and for reconfiguration without re-instantiation.
        """
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        s = self.status()
        return (
            f"TokenBucket(rate={self.rate}, burst={self.burst},"
            f" tokens={s.tokens:.2f})"
        )

    def __enter__(self) -> "TokenBucket":
        return self

    def __exit__(self, *_: object) -> None:
        pass


# ---------------------------------------------------------------------------
# SlidingWindow
# ---------------------------------------------------------------------------


class SlidingWindow:
    """Sliding-window rate limiter.

    Tracks timestamps of recent calls within a fixed-length window.  Callers
    call :meth:`acquire` before performing work.  If the maximum number of
    calls has already been recorded in the window, ``acquire`` returns the
    number of seconds until the oldest call exits the window, after which the
    caller should retry.  *No slot is reserved when blocked*, so the caller
    must call ``acquire`` again after sleeping.

    Args:
        max_calls: Maximum calls allowed within the window.  Must be at
            least one.
        window_seconds: Length of the window in seconds.  Must be greater
            than zero.

    Raises:
        ValueError: If *max_calls* is less than ``1`` or *window_seconds*
            is not positive.

    Example:
        >>> import time
        >>> from rateguard import SlidingWindow
        >>> window = SlidingWindow(max_calls=60, window_seconds=60.0)
        >>> wait = window.acquire()
        >>> if wait > 0:
        ...     time.sleep(wait)
        >>> # proceed with the API call
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
        """Record a call if within the limit.

        Returns ``0.0`` if the call was admitted immediately.  Otherwise
        returns the positive number of seconds until the oldest admitted call
        exits the window; the caller should sleep for that duration and then
        retry.  No slot is reserved when blocked.

        Returns:
            Seconds to wait before retrying.  ``0.0`` means the call was
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

    def status(self) -> SlidingWindowStatus:
        """Return an immutable snapshot of the current limiter state.

        Expired timestamps are evicted before the snapshot is taken so the
        reported count reflects the live window.

        Returns:
            A :class:`SlidingWindowStatus` named tuple.
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            return SlidingWindowStatus(
                calls_in_window=len(self._calls),
                max_calls=self.max_calls,
                window_seconds=self.window_seconds,
            )

    def reset(self) -> None:
        """Clear all recorded calls from the window.

        Useful in tests and for reconfiguration without re-instantiation.
        """
        with self._lock:
            self._calls.clear()

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        s = self.status()
        return (
            f"SlidingWindow(max_calls={self.max_calls},"
            f" window_seconds={self.window_seconds},"
            f" calls_in_window={s.calls_in_window})"
        )

    def __enter__(self) -> "SlidingWindow":
        return self

    def __exit__(self, *_: object) -> None:
        pass
