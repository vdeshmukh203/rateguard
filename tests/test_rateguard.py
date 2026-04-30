"""Tests for rateguard."""

import threading
import time

import pytest

from rateguard import SlidingWindow, TokenBucket


# ---------------------------------------------------------------------------
# TokenBucket – basic behaviour
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
    # Refilling 2 tokens at 10/s takes about 0.2 seconds.
    assert wait < 0.5


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(rate=200.0, burst=1)
    assert bucket.acquire(1) == 0.0
    time.sleep(0.05)
    # After 50 ms at 200 tokens/sec the bucket has refilled.
    assert bucket.acquire(1) == 0.0


# ---------------------------------------------------------------------------
# TokenBucket – validation
# ---------------------------------------------------------------------------

def test_token_bucket_invalid_rate_raises():
    with pytest.raises(ValueError):
        TokenBucket(rate=0.0, burst=1)
    with pytest.raises(ValueError):
        TokenBucket(rate=-1.0, burst=5)


def test_token_bucket_invalid_burst_raises():
    with pytest.raises(ValueError):
        TokenBucket(rate=1.0, burst=0)


def test_token_bucket_acquire_more_than_burst_raises():
    bucket = TokenBucket(rate=1.0, burst=2)
    with pytest.raises(ValueError):
        bucket.acquire(3)


def test_token_bucket_acquire_zero_or_negative_raises():
    bucket = TokenBucket(rate=1.0, burst=2)
    with pytest.raises(ValueError):
        bucket.acquire(0)
    with pytest.raises(ValueError):
        bucket.acquire(-1)


# ---------------------------------------------------------------------------
# TokenBucket – inspection API
# ---------------------------------------------------------------------------

def test_token_bucket_tokens_available_full():
    bucket = TokenBucket(rate=1.0, burst=10)
    assert bucket.tokens_available == 10.0


def test_token_bucket_tokens_available_decreases_after_acquire():
    bucket = TokenBucket(rate=1.0, burst=10)
    bucket.acquire(3)
    # A tiny refill can occur between acquire and the property read.
    assert bucket.tokens_available == pytest.approx(7.0, abs=0.01)


def test_token_bucket_tokens_available_never_negative():
    bucket = TokenBucket(rate=1.0, burst=2)
    bucket.acquire(2)
    bucket.acquire(2)  # drives internal counter below zero
    assert bucket.tokens_available == pytest.approx(0.0, abs=0.01)


def test_token_bucket_reset_refills_bucket():
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    assert bucket.tokens_available == pytest.approx(0.0, abs=0.01)
    bucket.reset()
    assert bucket.tokens_available == 5.0


def test_token_bucket_repr():
    bucket = TokenBucket(rate=5.0, burst=10)
    r = repr(bucket)
    assert "TokenBucket" in r
    assert "rate=5.0" in r
    assert "burst=10" in r
    assert "tokens_available" in r


# ---------------------------------------------------------------------------
# TokenBucket – thread safety
# ---------------------------------------------------------------------------

def test_token_bucket_thread_safety_no_crash():
    bucket = TokenBucket(rate=10000.0, burst=1000)
    results = []
    lock = threading.Lock()

    def worker():
        local = []
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
# SlidingWindow – basic behaviour
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
# SlidingWindow – validation
# ---------------------------------------------------------------------------

def test_sliding_window_invalid_max_calls_raises():
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=0, window_seconds=1.0)
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=-1, window_seconds=1.0)


def test_sliding_window_invalid_window_seconds_raises():
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=1, window_seconds=0.0)
    with pytest.raises(ValueError):
        SlidingWindow(max_calls=1, window_seconds=-1.0)


def test_sliding_window_does_not_reserve_when_blocked():
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    assert sw.acquire() == 0.0
    first_wait = sw.acquire()
    second_wait = sw.acquire()
    assert first_wait > 0.0
    assert second_wait > 0.0
    # Both blocked calls should report similar wait times since neither
    # was admitted.
    assert abs(first_wait - second_wait) < 1.0


# ---------------------------------------------------------------------------
# SlidingWindow – inspection API
# ---------------------------------------------------------------------------

def test_sliding_window_calls_in_window_increases():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    assert sw.calls_in_window == 0
    sw.acquire()
    assert sw.calls_in_window == 1
    sw.acquire()
    assert sw.calls_in_window == 2


def test_sliding_window_calls_in_window_decays_over_time():
    sw = SlidingWindow(max_calls=5, window_seconds=0.05)
    sw.acquire()
    assert sw.calls_in_window == 1
    time.sleep(0.06)
    assert sw.calls_in_window == 0


def test_sliding_window_slots_remaining():
    sw = SlidingWindow(max_calls=3, window_seconds=10.0)
    assert sw.slots_remaining == 3
    sw.acquire()
    assert sw.slots_remaining == 2
    sw.acquire()
    sw.acquire()
    assert sw.slots_remaining == 0


def test_sliding_window_reset_clears_window():
    sw = SlidingWindow(max_calls=2, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    assert sw.calls_in_window == 2
    sw.reset()
    assert sw.calls_in_window == 0
    # Should admit again immediately after reset.
    assert sw.acquire() == 0.0


def test_sliding_window_repr():
    sw = SlidingWindow(max_calls=10, window_seconds=5.0)
    r = repr(sw)
    assert "SlidingWindow" in r
    assert "max_calls=10" in r
    assert "window_seconds=5.0" in r
    assert "calls_in_window" in r


# ---------------------------------------------------------------------------
# SlidingWindow – thread safety
# ---------------------------------------------------------------------------

def test_sliding_window_thread_safety_no_crash():
    sw = SlidingWindow(max_calls=1000, window_seconds=10.0)

    def worker():
        for _ in range(20):
            sw.acquire()

    threads = [threading.Thread(target=worker) for _ in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert True
