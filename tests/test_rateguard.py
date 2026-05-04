"""Tests for rateguard."""

import threading
import time

import pytest

from rateguard import SlidingWindow, TokenBucket, __version__


# ── version ──────────────────────────────────────────────────────────────────

def test_version_string():
    assert isinstance(__version__, str)
    assert __version__  # non-empty


# ── TokenBucket ──────────────────────────────────────────────────────────────

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


def test_token_bucket_available_tokens_full():
    bucket = TokenBucket(rate=1.0, burst=5)
    assert bucket.available_tokens == pytest.approx(5.0, abs=0.01)


def test_token_bucket_available_tokens_after_drain():
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    # All tokens consumed; available should be 0 (no time elapsed).
    assert bucket.available_tokens == pytest.approx(0.0, abs=0.05)


def test_token_bucket_available_tokens_never_negative():
    bucket = TokenBucket(rate=1.0, burst=3)
    bucket.acquire(3)
    # Reserve more tokens (deficit); available must still be >= 0.
    bucket.acquire(3)
    assert bucket.available_tokens >= 0.0


def test_token_bucket_available_tokens_never_exceeds_burst():
    bucket = TokenBucket(rate=100.0, burst=4)
    time.sleep(0.2)  # let it over-refill time-wise; clamp must hold
    assert bucket.available_tokens <= 4.0


def test_token_bucket_reset():
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    assert bucket.available_tokens == pytest.approx(0.0, abs=0.05)
    bucket.reset()
    assert bucket.available_tokens == pytest.approx(5.0, abs=0.01)


def test_token_bucket_reset_clears_deficit():
    bucket = TokenBucket(rate=0.1, burst=2)
    # Drain then over-reserve to create a large debt.
    bucket.acquire(2)
    bucket.acquire(2)
    # Even with a large debt, reset should fully restore the bucket.
    bucket.reset()
    assert bucket.available_tokens == pytest.approx(2.0, abs=0.01)
    assert bucket.acquire(2) == 0.0


def test_token_bucket_error_message_contains_values():
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=-3.0, burst=1)
    with pytest.raises(ValueError, match="burst"):
        TokenBucket(rate=1.0, burst=0)


# ── SlidingWindow ─────────────────────────────────────────────────────────────

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


def test_sliding_window_available_calls_full():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    assert sw.available_calls == 5


def test_sliding_window_available_calls_decrements():
    sw = SlidingWindow(max_calls=3, window_seconds=10.0)
    sw.acquire()
    assert sw.available_calls == 2
    sw.acquire()
    assert sw.available_calls == 1
    sw.acquire()
    assert sw.available_calls == 0


def test_sliding_window_available_calls_never_negative():
    sw = SlidingWindow(max_calls=2, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    sw.acquire()  # blocked, not admitted
    assert sw.available_calls >= 0


def test_sliding_window_used_calls():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    assert sw.used_calls == 0
    sw.acquire()
    sw.acquire()
    assert sw.used_calls == 2


def test_sliding_window_used_calls_expires():
    sw = SlidingWindow(max_calls=5, window_seconds=0.05)
    sw.acquire()
    assert sw.used_calls == 1
    time.sleep(0.06)
    assert sw.used_calls == 0


def test_sliding_window_reset():
    sw = SlidingWindow(max_calls=2, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    assert sw.available_calls == 0
    sw.reset()
    assert sw.available_calls == 2
    assert sw.used_calls == 0


def test_sliding_window_reset_allows_immediate_reuse():
    sw = SlidingWindow(max_calls=1, window_seconds=60.0)
    assert sw.acquire() == 0.0
    sw.reset()
    assert sw.acquire() == 0.0


def test_sliding_window_error_message_contains_values():
    with pytest.raises(ValueError, match="max_calls"):
        SlidingWindow(max_calls=0, window_seconds=1.0)
    with pytest.raises(ValueError, match="window_seconds"):
        SlidingWindow(max_calls=1, window_seconds=-5.0)
