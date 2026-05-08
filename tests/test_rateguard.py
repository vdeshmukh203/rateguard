"""Tests for rateguard."""

import threading
import time

import pytest

from rateguard import (
    SlidingWindow,
    SlidingWindowStatus,
    TokenBucket,
    TokenBucketStatus,
    __version__,
)


# ---------------------------------------------------------------------------
# Package metadata
# ---------------------------------------------------------------------------


def test_version_string_exists():
    assert isinstance(__version__, str)
    assert __version__  # non-empty


# ---------------------------------------------------------------------------
# TokenBucket — basic acquisition
# ---------------------------------------------------------------------------


def test_token_bucket_acquire_returns_zero_when_tokens_available():
    bucket = TokenBucket(rate=10.0, burst=5)
    assert bucket.acquire(1) == 0.0
    assert bucket.acquire(1) == 0.0
    assert bucket.acquire(3) == 0.0


def test_token_bucket_returns_positive_wait_when_drained():
    bucket = TokenBucket(rate=10.0, burst=2)
    assert bucket.acquire(2) == 0.0
    wait = bucket.acquire(2)
    assert wait > 0.0
    # Refilling 2 tokens at 10/s takes about 0.2 s.
    assert wait < 0.5


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(rate=200.0, burst=1)
    assert bucket.acquire(1) == 0.0
    time.sleep(0.05)
    # After 50 ms at 200 tokens/sec the bucket has refilled.
    assert bucket.acquire(1) == 0.0


# ---------------------------------------------------------------------------
# TokenBucket — validation
# ---------------------------------------------------------------------------


def test_token_bucket_invalid_rate_raises():
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=0.0, burst=1)
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=-1.0, burst=5)


def test_token_bucket_invalid_burst_raises():
    with pytest.raises(ValueError, match="burst"):
        TokenBucket(rate=1.0, burst=0)


def test_token_bucket_acquire_more_than_burst_raises():
    bucket = TokenBucket(rate=1.0, burst=2)
    with pytest.raises(ValueError, match="burst"):
        bucket.acquire(3)


def test_token_bucket_acquire_zero_or_negative_raises():
    bucket = TokenBucket(rate=1.0, burst=2)
    with pytest.raises(ValueError, match="tokens"):
        bucket.acquire(0)
    with pytest.raises(ValueError, match="tokens"):
        bucket.acquire(-1)


# ---------------------------------------------------------------------------
# TokenBucket — status, reset, repr
# ---------------------------------------------------------------------------


def test_token_bucket_status_returns_named_tuple():
    bucket = TokenBucket(rate=5.0, burst=10)
    s = bucket.status()
    assert isinstance(s, TokenBucketStatus)
    assert s.rate == 5.0
    assert s.burst == 10
    assert 0.0 <= s.tokens <= 10.0


def test_token_bucket_status_reflects_consumption():
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(3)
    s = bucket.status()
    # After consuming 3, roughly 2 tokens remain (may have partially refilled).
    assert s.tokens <= 2.1


def test_token_bucket_status_does_not_consume_tokens():
    bucket = TokenBucket(rate=1.0, burst=5)
    s1 = bucket.status()
    s2 = bucket.status()
    assert s1.tokens <= s2.tokens  # may refill but never decrease from status calls


def test_token_bucket_reset_restores_full_burst():
    bucket = TokenBucket(rate=1.0, burst=8)
    bucket.acquire(8)
    bucket.reset()
    s = bucket.status()
    assert s.tokens == pytest.approx(8.0, abs=0.05)


def test_token_bucket_repr_contains_key_info():
    bucket = TokenBucket(rate=3.0, burst=7)
    r = repr(bucket)
    assert "TokenBucket" in r
    assert "rate=3.0" in r
    assert "burst=7" in r
    assert "tokens=" in r


# ---------------------------------------------------------------------------
# TokenBucket — context manager
# ---------------------------------------------------------------------------


def test_token_bucket_context_manager():
    with TokenBucket(rate=10.0, burst=5) as bucket:
        assert bucket.acquire(1) == 0.0


# ---------------------------------------------------------------------------
# TokenBucket — thread safety
# ---------------------------------------------------------------------------


def test_token_bucket_thread_safety_no_crash():
    bucket = TokenBucket(rate=10000.0, burst=1000)
    results: list = []
    lock = threading.Lock()

    def worker() -> None:
        local = [bucket.acquire(1) for _ in range(20)]
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 100
    assert all(r >= 0.0 for r in results)


def test_token_bucket_concurrent_status_no_crash():
    bucket = TokenBucket(rate=100.0, burst=50)

    def worker() -> None:
        for _ in range(20):
            bucket.status()
            bucket.acquire(1)

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


# ---------------------------------------------------------------------------
# SlidingWindow — basic acquisition
# ---------------------------------------------------------------------------


def test_sliding_window_admits_within_limit():
    sw = SlidingWindow(max_calls=3, window_seconds=1.0)
    assert sw.acquire() == 0.0
    assert sw.acquire() == 0.0
    assert sw.acquire() == 0.0


def test_sliding_window_blocks_when_limit_exceeded():
    sw = SlidingWindow(max_calls=2, window_seconds=1.0)
    assert sw.acquire() == 0.0
    assert sw.acquire() == 0.0
    wait = sw.acquire()
    assert wait > 0.0
    assert wait <= 1.0


def test_sliding_window_admits_again_after_window():
    sw = SlidingWindow(max_calls=1, window_seconds=0.05)
    assert sw.acquire() == 0.0
    time.sleep(0.06)
    assert sw.acquire() == 0.0


# ---------------------------------------------------------------------------
# SlidingWindow — validation
# ---------------------------------------------------------------------------


def test_sliding_window_invalid_max_calls_raises():
    with pytest.raises(ValueError, match="max_calls"):
        SlidingWindow(max_calls=0, window_seconds=1.0)
    with pytest.raises(ValueError, match="max_calls"):
        SlidingWindow(max_calls=-1, window_seconds=1.0)


def test_sliding_window_invalid_window_seconds_raises():
    with pytest.raises(ValueError, match="window_seconds"):
        SlidingWindow(max_calls=1, window_seconds=0.0)
    with pytest.raises(ValueError, match="window_seconds"):
        SlidingWindow(max_calls=1, window_seconds=-1.0)


# ---------------------------------------------------------------------------
# SlidingWindow — no reservation on block
# ---------------------------------------------------------------------------


def test_sliding_window_does_not_reserve_when_blocked():
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    assert sw.acquire() == 0.0
    first_wait = sw.acquire()
    second_wait = sw.acquire()
    assert first_wait > 0.0
    assert second_wait > 0.0
    # Both blocked calls report similar waits since neither was admitted.
    assert abs(first_wait - second_wait) < 1.0


# ---------------------------------------------------------------------------
# SlidingWindow — status, reset, repr
# ---------------------------------------------------------------------------


def test_sliding_window_status_returns_named_tuple():
    sw = SlidingWindow(max_calls=5, window_seconds=2.0)
    s = sw.status()
    assert isinstance(s, SlidingWindowStatus)
    assert s.max_calls == 5
    assert s.window_seconds == 2.0
    assert s.calls_in_window == 0


def test_sliding_window_status_reflects_admitted_calls():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    s = sw.status()
    assert s.calls_in_window == 2


def test_sliding_window_status_evicts_expired():
    sw = SlidingWindow(max_calls=3, window_seconds=0.05)
    sw.acquire()
    sw.acquire()
    time.sleep(0.06)
    s = sw.status()
    assert s.calls_in_window == 0


def test_sliding_window_reset_clears_calls():
    sw = SlidingWindow(max_calls=3, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    sw.reset()
    s = sw.status()
    assert s.calls_in_window == 0


def test_sliding_window_repr_contains_key_info():
    sw = SlidingWindow(max_calls=4, window_seconds=30.0)
    r = repr(sw)
    assert "SlidingWindow" in r
    assert "max_calls=4" in r
    assert "window_seconds=30.0" in r
    assert "calls_in_window=" in r


# ---------------------------------------------------------------------------
# SlidingWindow — context manager
# ---------------------------------------------------------------------------


def test_sliding_window_context_manager():
    with SlidingWindow(max_calls=3, window_seconds=1.0) as sw:
        assert sw.acquire() == 0.0


# ---------------------------------------------------------------------------
# SlidingWindow — thread safety
# ---------------------------------------------------------------------------


def test_sliding_window_thread_safety_no_crash():
    sw = SlidingWindow(max_calls=1000, window_seconds=10.0)

    def worker() -> None:
        for _ in range(20):
            sw.acquire()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()


def test_sliding_window_concurrent_status_no_crash():
    sw = SlidingWindow(max_calls=100, window_seconds=5.0)

    def worker() -> None:
        for _ in range(20):
            sw.status()
            sw.acquire()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
