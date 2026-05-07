"""rateguard - local rate limiter for LLM API calls.

Provides three thread-safe primitives:

- :class:`TokenBucket`: Token-bucket algorithm with configurable burst capacity.
  Each ``acquire`` call reserves tokens immediately and returns the wait time.
- :class:`SlidingWindow`: Sliding-window algorithm for strict per-window limits.
  A call slot is only recorded when the limit is not exceeded.
- :class:`CompositeRateLimiter`: Chains any number of the above limiters and
  returns the largest wait time across all of them.

All primitives are thread-safe, depend only on the Python standard library, and
are compatible with both synchronous and async code bases (the caller handles
sleeping).

Example::

    import time
    from rateguard import TokenBucket, SlidingWindow, CompositeRateLimiter

    # Honour both 10 req/s burst and 60 req/min caps simultaneously.
    limiter = CompositeRateLimiter(
        TokenBucket(rate=10.0, burst=10),
        SlidingWindow(max_calls=60, window_seconds=60.0),
    )

    while True:
        wait = limiter.acquire()
        if wait == 0.0:
            break
        time.sleep(wait)
    # ... make the API call here
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Deque, Union

__version__ = "0.2.0"
__all__ = ["TokenBucket", "SlidingWindow", "CompositeRateLimiter"]


class TokenBucket:
    """Token-bucket rate limiter.

    The bucket starts full and refills at a constant *rate* up to *burst*
    capacity.  Calling :meth:`acquire` deducts *tokens* from the bucket and
    returns ``0.0`` when the tokens are available immediately, or the number of
    seconds the caller should sleep before proceeding.  The tokens are reserved
    (deducted) regardless of whether a wait is required, so concurrent callers
    each receive their own fair portion of the wait time without additional
    queuing logic.

    Parameters
    ----------
    rate:
        Tokens replenished per second.  Must be a finite positive number.
    burst:
        Maximum number of tokens the bucket may hold and the maximum number
        that can be requested in a single :meth:`acquire` call.  Must be at
        least 1.

    Attributes
    ----------
    rate:
        Tokens replenished per second.
    burst:
        Maximum bucket capacity.
    """

    def __init__(self, rate: float, burst: int) -> None:
        if not math.isfinite(rate) or rate <= 0:
            raise ValueError(f"rate must be a finite positive number, got {rate!r}")
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

    def _available(self, now: float) -> float:
        """Return token level after refilling to *now* (does not mutate)."""
        elapsed = now - self._last
        if elapsed > 0:
            return min(float(self.burst), self._tokens + elapsed * self.rate)
        return self._tokens

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def tokens(self) -> float:
        """Current token count after accounting for elapsed time.

        May be negative when tokens have been reserved by concurrent callers.
        Thread-safe; does not modify internal state.
        """
        with self._lock:
            return self._available(time.monotonic())

    def acquire(self, tokens: int = 1) -> float:
        """Consume *tokens* from the bucket and return the wait time.

        Parameters
        ----------
        tokens:
            Number of tokens to consume.  Must satisfy
            ``1 <= tokens <= burst``.

        Returns
        -------
        float
            Seconds to sleep before the tokens are available.  ``0.0`` means
            the tokens were immediately present.

        Raises
        ------
        ValueError
            If *tokens* is outside ``[1, burst]``.
        """
        if tokens < 1:
            raise ValueError(f"tokens must be at least 1, got {tokens!r}")
        if tokens > self.burst:
            raise ValueError(
                f"tokens ({tokens!r}) cannot exceed burst ({self.burst!r})"
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

    def peek(self, tokens: int = 1) -> float:
        """Return the wait time for *tokens* without consuming them.

        Useful for pre-checking whether a call would be admitted without
        modifying limiter state.

        Parameters
        ----------
        tokens:
            Number of tokens to query.  Must satisfy ``1 <= tokens <= burst``.

        Returns
        -------
        float
            Seconds to wait, or ``0.0`` if the tokens are immediately
            available.
        """
        if tokens < 1:
            raise ValueError(f"tokens must be at least 1, got {tokens!r}")
        if tokens > self.burst:
            raise ValueError(
                f"tokens ({tokens!r}) cannot exceed burst ({self.burst!r})"
            )
        with self._lock:
            current = self._available(time.monotonic())
            if current >= tokens:
                return 0.0
            return (tokens - current) / self.rate

    def reset(self) -> None:
        """Reset the bucket to full capacity."""
        with self._lock:
            self._tokens = float(self.burst)
            self._last = time.monotonic()

    def __repr__(self) -> str:
        return f"TokenBucket(rate={self.rate!r}, burst={self.burst!r})"


class SlidingWindow:
    """Sliding-window rate limiter.

    Keeps timestamps of admitted calls within a rolling *window_seconds*
    interval.  A call is admitted (returns ``0.0``) when fewer than
    *max_calls* timestamps are present in the window.  When the limit is
    reached the call is **not** recorded and the caller receives the number of
    seconds until the oldest timestamp leaves the window; the caller must then
    sleep and retry :meth:`acquire`.

    Parameters
    ----------
    max_calls:
        Maximum calls admitted within the window.  Must be at least 1.
    window_seconds:
        Length of the sliding window in seconds.  Must be a finite positive
        number.

    Attributes
    ----------
    max_calls:
        Maximum calls within the window.
    window_seconds:
        Window length in seconds.
    """

    def __init__(self, max_calls: int, window_seconds: float) -> None:
        if max_calls < 1:
            raise ValueError(f"max_calls must be at least 1, got {max_calls!r}")
        if not math.isfinite(window_seconds) or window_seconds <= 0:
            raise ValueError(
                f"window_seconds must be a finite positive number,"
                f" got {window_seconds!r}"
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

    @property
    def current_calls(self) -> int:
        """Number of admitted calls tracked in the current window.

        Thread-safe snapshot; evicts stale entries before reporting.
        """
        with self._lock:
            self._evict(time.monotonic())
            return len(self._calls)

    def acquire(self) -> float:
        """Admit one call or return the wait time.

        Returns
        -------
        float
            ``0.0`` when the call was admitted.  A positive value is the
            number of seconds until the oldest call exits the window; the
            caller should sleep that long and then retry (the slot is **not**
            reserved on a blocked call).
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

    def peek(self) -> float:
        """Return the wait time without recording a call.

        Returns
        -------
        float
            ``0.0`` if a call would be admitted right now; otherwise the
            seconds to wait before retrying.
        """
        with self._lock:
            now = time.monotonic()
            self._evict(now)
            if len(self._calls) < self.max_calls:
                return 0.0
            oldest = self._calls[0]
            wait = (oldest + self.window_seconds) - now
            return wait if wait > 0 else 0.0

    def reset(self) -> None:
        """Clear all recorded calls."""
        with self._lock:
            self._calls.clear()

    def __repr__(self) -> str:
        return (
            f"SlidingWindow(max_calls={self.max_calls!r},"
            f" window_seconds={self.window_seconds!r})"
        )


#: Type alias for any limiter accepted by :class:`CompositeRateLimiter`.
_AnyLimiter = Union[TokenBucket, SlidingWindow]


class CompositeRateLimiter:
    """Compose multiple rate limiters into a single interface.

    A call is admitted only when *all* constituent limiters would admit it.
    :meth:`acquire` first peeks at every limiter; if any would block, no
    limiter state is modified and the maximum peek wait is returned.  Only
    when all peeks report ``0.0`` are the individual ``acquire`` calls
    dispatched.

    This mirrors the dual-rate-limit structure (requests-per-minute *and*
    tokens-per-minute) enforced by most commercial LLM providers::

        limiter = CompositeRateLimiter(
            TokenBucket(rate=10.0, burst=10),        # ≤ 10 req/s burst
            SlidingWindow(max_calls=60, window_seconds=60.0),  # ≤ 60 req/min
        )

    Parameters
    ----------
    *limiters:
        One or more :class:`TokenBucket` or :class:`SlidingWindow` instances.

    Notes
    -----
    Because each constituent limiter uses its own lock, the peek-then-acquire
    sequence across multiple limiters is not globally atomic.  In high-
    concurrency scenarios a limiter that peeked as ready may become
    unavailable by the time ``acquire`` is called, yielding a small spurious
    positive wait.  This is acceptable for client-side LLM rate limiting.

    Callers should use a retry loop::

        while True:
            wait = limiter.acquire(tokens)
            if wait == 0.0:
                break
            time.sleep(wait)
    """

    def __init__(self, *limiters: _AnyLimiter) -> None:
        if not limiters:
            raise ValueError("at least one limiter is required")
        self._limiters: tuple[_AnyLimiter, ...] = limiters

    def peek(self, tokens: int = 1) -> float:
        """Return the maximum wait across all limiters without side effects.

        Parameters
        ----------
        tokens:
            Forwarded to :class:`TokenBucket` limiters only.

        Returns
        -------
        float
            Maximum peek wait in seconds.  ``0.0`` means all limiters would
            admit immediately.
        """
        max_wait = 0.0
        for lim in self._limiters:
            if isinstance(lim, TokenBucket):
                max_wait = max(max_wait, lim.peek(tokens))
            else:
                max_wait = max(max_wait, lim.peek())
        return max_wait

    def acquire(self, tokens: int = 1) -> float:
        """Acquire from all limiters and return the maximum wait.

        If any limiter's peek reports a positive wait, no limiter state is
        modified and the maximum wait is returned.  When all peeks are zero,
        each limiter is acquired in sequence.

        Parameters
        ----------
        tokens:
            Forwarded to :class:`TokenBucket` limiters only.

        Returns
        -------
        float
            Seconds to sleep.  ``0.0`` means all limiters admitted the call.
        """
        wait = self.peek(tokens)
        if wait > 0:
            return wait
        max_wait = 0.0
        for lim in self._limiters:
            if isinstance(lim, TokenBucket):
                max_wait = max(max_wait, lim.acquire(tokens))
            else:
                max_wait = max(max_wait, lim.acquire())
        return max_wait

    def reset(self) -> None:
        """Reset all constituent limiters."""
        for lim in self._limiters:
            lim.reset()

    def __repr__(self) -> str:
        parts = ", ".join(repr(lim) for lim in self._limiters)
        return f"CompositeRateLimiter({parts})"
