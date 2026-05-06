"""Tests for rateguard."""

import threading
import time

import pytest

from rateguard import SlidingWindow, TokenBucket, __version__


# ── version ───────────────────────────────────────────────────────────────────

def test_version_string():
    assert isinstance(__version__, str)
    parts = __version__.split(".")
    assert len(parts) == 3
    assert all(p.isdigit() for p in parts)


# ── TokenBucket ───────────────────────────────────────────────────────────────

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
    # After 50 ms at 200 tok/s the bucket has refilled.
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


def test_token_bucket_acquire_float_tokens():
    bucket = TokenBucket(rate=100.0, burst=10)
    # Full bucket: fractional acquire should return 0.
    assert bucket.acquire(0.5) == 0.0
    assert bucket.acquire(2.5) == 0.0


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


def test_token_bucket_repr():
    bucket = TokenBucket(rate=5.0, burst=10)
    r = repr(bucket)
    assert "TokenBucket" in r
    assert "5.0" in r
    assert "10" in r


def test_token_bucket_stats_full():
    bucket = TokenBucket(rate=1.0, burst=10)
    s = bucket.stats()
    assert s["burst"] == 10
    assert s["rate"] == 1.0
    assert abs(s["tokens"] - 10.0) < 0.01
    assert abs(s["fill_ratio"] - 1.0) < 0.01


def test_token_bucket_stats_after_drain():
    bucket = TokenBucket(rate=1.0, burst=4)
    bucket.acquire(4)
    s = bucket.stats()
    assert s["tokens"] < 0.001   # near-zero; tiny refill may have occurred
    assert s["fill_ratio"] < 0.001


def test_token_bucket_stats_is_nonmutating():
    # Calling stats() repeatedly should not change the bucket's effective state.
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    t0 = time.monotonic()
    s1 = bucket.stats()
    time.sleep(0.01)
    s2 = bucket.stats()
    # tokens should be slightly higher in s2 due to refill, not due to stats side-effect.
    assert s2["tokens"] >= s1["tokens"]
    _ = t0  # suppress unused warning


def test_token_bucket_reset():
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    assert bucket.stats()["tokens"] < 0.001   # near-zero after drain
    bucket.reset()
    assert abs(bucket.stats()["tokens"] - 5.0) < 0.01


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
    # Neither blocked call was admitted, so wait times should be nearly equal.
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


def test_sliding_window_repr():
    sw = SlidingWindow(max_calls=60, window_seconds=60.0)
    r = repr(sw)
    assert "SlidingWindow" in r
    assert "60" in r
    assert "60.0" in r


def test_sliding_window_stats_empty():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    s = sw.stats()
    assert s["calls_in_window"] == 0
    assert s["max_calls"] == 5
    assert s["window_seconds"] == 10.0
    assert s["available"] == 5


def test_sliding_window_stats_after_calls():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    sw.acquire()
    s = sw.stats()
    assert s["calls_in_window"] == 3
    assert s["available"] == 2


def test_sliding_window_stats_is_nonmutating():
    sw = SlidingWindow(max_calls=3, window_seconds=10.0)
    sw.acquire()
    # Calling stats() should not add a call.
    sw.stats()
    sw.stats()
    assert sw.stats()["calls_in_window"] == 1


def test_sliding_window_reset():
    sw = SlidingWindow(max_calls=3, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    assert sw.stats()["calls_in_window"] == 2
    sw.reset()
    assert sw.stats()["calls_in_window"] == 0
    # After reset the window accepts calls again.
    assert sw.acquire() == 0.0
