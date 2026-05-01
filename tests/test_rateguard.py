"""Tests for rateguard."""

import threading
import time

import pytest

from rateguard import RateLimiter, SlidingWindow, TokenBucket

# ---------------------------------------------------------------------------
# TokenBucket — basic behaviour
# ---------------------------------------------------------------------------


def test_token_bucket_acquire_returns_zero_when_tokens_available() -> None:
    bucket = TokenBucket(rate=10.0, burst=5)
    assert bucket.acquire(1) == 0.0
    assert bucket.acquire(1) == 0.0
    assert bucket.acquire(3) == 0.0


def test_token_bucket_returns_positive_wait_when_drained() -> None:
    bucket = TokenBucket(rate=10.0, burst=2)
    assert bucket.acquire(2) == 0.0
    wait = bucket.acquire(2)
    assert wait > 0.0
    # Refilling 2 tokens at 10/s takes about 0.2 seconds.
    assert wait < 0.5


def test_token_bucket_refills_over_time() -> None:
    bucket = TokenBucket(rate=200.0, burst=1)
    assert bucket.acquire(1) == 0.0
    time.sleep(0.05)
    # After 50 ms at 200 tokens/s the bucket has refilled.
    assert bucket.acquire(1) == 0.0


def test_token_bucket_invalid_rate_raises() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0.0, burst=1)
    with pytest.raises(ValueError):
        TokenBucket(rate=-1.0, burst=5)


def test_token_bucket_invalid_burst_raises() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=1.0, burst=0)


def test_token_bucket_acquire_more_than_burst_raises() -> None:
    bucket = TokenBucket(rate=1.0, burst=2)
    with pytest.raises(ValueError):
        bucket.acquire(3)


def test_token_bucket_acquire_zero_or_negative_raises() -> None:
    bucket = TokenBucket(rate=1.0, burst=2)
    with pytest.raises(ValueError):
        bucket.acquire(0)
    with pytest.raises(ValueError):
        bucket.acquire(-1)


def test_token_bucket_thread_safety_no_crash() -> None:
    bucket = TokenBucket(rate=10000.0, burst=1000)
    results: list[float] = []
    lock = threading.Lock()

    def worker() -> None:
        local: list[float] = []
        for _ in range(20):
            local.append(bucket.acquire(1))
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 100
    assert all(r >= 0.0 for r in results)


# ---------------------------------------------------------------------------
# TokenBucket — tokens_available property
# ---------------------------------------------------------------------------


def test_token_bucket_tokens_available_initial_equals_burst() -> None:
    bucket = TokenBucket(rate=1.0, burst=5)
    assert bucket.tokens_available == 5.0


def test_token_bucket_tokens_available_decreases_on_acquire() -> None:
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(3)
    # At 1 tok/s the bucket barely refills between acquire() and the property
    # read; allow a small tolerance.
    assert bucket.tokens_available == pytest.approx(2.0, abs=0.01)


def test_token_bucket_tokens_available_never_negative() -> None:
    bucket = TokenBucket(rate=1.0, burst=2)
    bucket.acquire(2)
    # Reserve more tokens (internal counter goes below zero).
    wait = bucket.acquire(2)
    assert wait > 0.0
    # Public property must clamp to zero.
    assert bucket.tokens_available == 0.0


def test_token_bucket_tokens_available_refills_over_time() -> None:
    bucket = TokenBucket(rate=100.0, burst=5)
    bucket.acquire(5)
    time.sleep(0.05)
    # At 100 tok/s, 50 ms ≈ 5 tokens; capped at burst.
    assert bucket.tokens_available > 0.0


# ---------------------------------------------------------------------------
# TokenBucket — reset()
# ---------------------------------------------------------------------------


def test_token_bucket_reset_restores_full_capacity() -> None:
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    bucket.reset()
    assert bucket.tokens_available == 5.0


def test_token_bucket_reset_clears_reservations() -> None:
    bucket = TokenBucket(rate=1.0, burst=2)
    bucket.acquire(2)
    bucket.acquire(2)  # drives internal counter negative
    bucket.reset()
    assert bucket.acquire(1) == 0.0


# ---------------------------------------------------------------------------
# TokenBucket — __repr__
# ---------------------------------------------------------------------------


def test_token_bucket_repr() -> None:
    bucket = TokenBucket(rate=5.0, burst=10)
    assert repr(bucket) == "TokenBucket(rate=5.0, burst=10)"


# ---------------------------------------------------------------------------
# SlidingWindow — basic behaviour
# ---------------------------------------------------------------------------


def test_sliding_window_admits_within_limit() -> None:
    sw = SlidingWindow(max_calls=3, window_seconds=1.0)
    assert sw.acquire() == 0.0
    assert sw.acquire() == 0.0
    assert sw.acquire() == 0.0


def test_sliding_window_blocks_when_limit_exceeded() -> None:
    sw = SlidingWindow(max_calls=2, window_seconds=1.0)
    assert sw.acquire() == 0.0
    assert sw.acquire() == 0.0
    wait = sw.acquire()
    assert wait > 0.0
    assert wait <= 1.0


def test_sliding_window_admits_again_after_window() -> None:
    sw = SlidingWindow(max_calls=1, window_seconds=0.05)
    assert sw.acquire() == 0.0
    time.sleep(0.06)
    assert sw.acquire() == 0.0


def test_sliding_window_invalid_max_calls_raises() -> None:
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=0, window_seconds=1.0)
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=-1, window_seconds=1.0)


def test_sliding_window_invalid_window_seconds_raises() -> None:
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=1, window_seconds=0.0)
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=1, window_seconds=-1.0)


def test_sliding_window_does_not_reserve_when_blocked() -> None:
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    assert sw.acquire() == 0.0
    first_wait = sw.acquire()
    second_wait = sw.acquire()
    assert first_wait > 0.0
    assert second_wait > 0.0
    # Neither blocked call consumes a slot, so both report similar waits.
    assert abs(first_wait - second_wait) < 1.0


def test_sliding_window_thread_safety_no_crash() -> None:
    sw = SlidingWindow(max_calls=1000, window_seconds=10.0)

    def worker() -> None:
        for _ in range(20):
            sw.acquire()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ---------------------------------------------------------------------------
# SlidingWindow — calls_in_window property
# ---------------------------------------------------------------------------


def test_sliding_window_calls_in_window_initial_zero() -> None:
    sw = SlidingWindow(max_calls=5, window_seconds=1.0)
    assert sw.calls_in_window == 0


def test_sliding_window_calls_in_window_counts_admitted() -> None:
    sw = SlidingWindow(max_calls=5, window_seconds=1.0)
    sw.acquire()
    sw.acquire()
    assert sw.calls_in_window == 2


def test_sliding_window_calls_in_window_decreases_after_expiry() -> None:
    sw = SlidingWindow(max_calls=5, window_seconds=0.05)
    sw.acquire()
    sw.acquire()
    time.sleep(0.06)
    assert sw.calls_in_window == 0


def test_sliding_window_calls_in_window_unaffected_by_blocked() -> None:
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    sw.acquire()
    sw.acquire()  # blocked — must not increment the count
    assert sw.calls_in_window == 1


# ---------------------------------------------------------------------------
# SlidingWindow — slots_remaining property
# ---------------------------------------------------------------------------


def test_sliding_window_slots_remaining_initial_equals_max_calls() -> None:
    sw = SlidingWindow(max_calls=3, window_seconds=1.0)
    assert sw.slots_remaining == 3


def test_sliding_window_slots_remaining_decreases_on_admit() -> None:
    sw = SlidingWindow(max_calls=3, window_seconds=1.0)
    sw.acquire()
    assert sw.slots_remaining == 2
    sw.acquire()
    sw.acquire()
    assert sw.slots_remaining == 0


def test_sliding_window_slots_remaining_zero_when_full() -> None:
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    sw.acquire()
    assert sw.slots_remaining == 0


# ---------------------------------------------------------------------------
# SlidingWindow — call_ages()
# ---------------------------------------------------------------------------


def test_sliding_window_call_ages_empty_initially() -> None:
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    assert sw.call_ages() == []


def test_sliding_window_call_ages_length_matches_calls_in_window() -> None:
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    sw.acquire()
    ages = sw.call_ages()
    assert len(ages) == 3


def test_sliding_window_call_ages_values_are_non_negative() -> None:
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    ages = sw.call_ages()
    assert all(a >= 0.0 for a in ages)


def test_sliding_window_call_ages_within_window() -> None:
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    sw.acquire()
    ages = sw.call_ages()
    assert ages[0] < 10.0


# ---------------------------------------------------------------------------
# SlidingWindow — reset()
# ---------------------------------------------------------------------------


def test_sliding_window_reset_clears_calls() -> None:
    sw = SlidingWindow(max_calls=2, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    sw.reset()
    assert sw.calls_in_window == 0


def test_sliding_window_reset_allows_immediate_admit() -> None:
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    sw.acquire()
    assert sw.acquire() > 0.0  # blocked
    sw.reset()
    assert sw.acquire() == 0.0  # admitted after reset


# ---------------------------------------------------------------------------
# SlidingWindow — __repr__
# ---------------------------------------------------------------------------


def test_sliding_window_repr() -> None:
    sw = SlidingWindow(max_calls=60, window_seconds=60.0)
    assert repr(sw) == "SlidingWindow(max_calls=60, window_seconds=60.0)"


# ---------------------------------------------------------------------------
# RateLimiter protocol
# ---------------------------------------------------------------------------


def test_rate_limiter_protocol_satisfied_by_token_bucket() -> None:
    bucket = TokenBucket(rate=1.0, burst=1)
    assert isinstance(bucket, RateLimiter)


def test_rate_limiter_protocol_satisfied_by_sliding_window() -> None:
    sw = SlidingWindow(max_calls=1, window_seconds=1.0)
    assert isinstance(sw, RateLimiter)


def test_rate_limiter_protocol_not_satisfied_by_plain_object() -> None:
    assert not isinstance(object(), RateLimiter)
