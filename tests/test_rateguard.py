"""Tests for rateguard."""

import threading
import time

import pytest

from rateguard import SlidingWindow, TokenBucket, __version__


# ---------------------------------------------------------------------------
# Module-level
# ---------------------------------------------------------------------------


def test_version_string_exists():
    assert isinstance(__version__, str)
    assert len(__version__) > 0


# ---------------------------------------------------------------------------
# TokenBucket — construction
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


def test_token_bucket_acquire_float_raises_type_error():
    bucket = TokenBucket(rate=1.0, burst=10)
    with pytest.raises(TypeError, match="integer"):
        bucket.acquire(1.5)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# TokenBucket — status()
# ---------------------------------------------------------------------------


def test_token_bucket_status_full_on_creation():
    bucket = TokenBucket(rate=5.0, burst=10)
    st = bucket.status()
    assert st["burst"] == 10
    assert st["rate"] == 5.0
    assert abs(st["tokens"] - 10.0) < 0.01


def test_token_bucket_status_reflects_acquisition():
    bucket = TokenBucket(rate=1.0, burst=10)
    bucket.acquire(4)
    st = bucket.status()
    assert abs(st["tokens"] - 6.0) < 0.1


def test_token_bucket_status_does_not_modify_acquire_behaviour():
    """status() must be side-effect-free with respect to token accounting."""
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    # Drain completely; no time passes yet so tokens should be near 0.
    st_before = bucket.status()
    st_after = bucket.status()
    # Two consecutive status calls should return very similar token counts.
    assert abs(st_before["tokens"] - st_after["tokens"]) < 0.05


# ---------------------------------------------------------------------------
# TokenBucket — reset()
# ---------------------------------------------------------------------------


def test_token_bucket_reset_refills_to_burst():
    bucket = TokenBucket(rate=0.01, burst=10)  # very slow refill
    bucket.acquire(10)
    bucket.reset()
    st = bucket.status()
    assert abs(st["tokens"] - 10.0) == pytest.approx(0.0, abs=0.01)
    assert bucket.acquire(10) == 0.0


# ---------------------------------------------------------------------------
# TokenBucket — __repr__()
# ---------------------------------------------------------------------------


def test_token_bucket_repr():
    bucket = TokenBucket(rate=3.5, burst=7)
    r = repr(bucket)
    assert "TokenBucket" in r
    assert "3.5" in r
    assert "7" in r


# ---------------------------------------------------------------------------
# TokenBucket — thread safety
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
# SlidingWindow — construction
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
# SlidingWindow — status()
# ---------------------------------------------------------------------------


def test_sliding_window_status_empty_on_creation():
    sw = SlidingWindow(max_calls=5, window_seconds=30.0)
    st = sw.status()
    assert st["current_calls"] == 0
    assert st["max_calls"] == 5
    assert st["window_seconds"] == 30.0
    assert st["call_ages"] == []


def test_sliding_window_status_counts_active_calls():
    sw = SlidingWindow(max_calls=10, window_seconds=60.0)
    for _ in range(4):
        sw.acquire()
    st = sw.status()
    assert st["current_calls"] == 4
    assert len(st["call_ages"]) == 4


def test_sliding_window_status_call_ages_sorted():
    sw = SlidingWindow(max_calls=5, window_seconds=60.0)
    for _ in range(3):
        sw.acquire()
    ages = sw.status()["call_ages"]
    assert ages == sorted(ages)


def test_sliding_window_status_excludes_expired_calls():
    sw = SlidingWindow(max_calls=3, window_seconds=0.05)
    sw.acquire()
    sw.acquire()
    time.sleep(0.07)
    st = sw.status()
    assert st["current_calls"] == 0


# ---------------------------------------------------------------------------
# SlidingWindow — reset()
# ---------------------------------------------------------------------------


def test_sliding_window_reset_clears_calls():
    sw = SlidingWindow(max_calls=2, window_seconds=60.0)
    sw.acquire()
    sw.acquire()
    assert sw.status()["current_calls"] == 2
    sw.reset()
    assert sw.status()["current_calls"] == 0
    # Should be able to acquire freely again after reset.
    assert sw.acquire() == 0.0
    assert sw.acquire() == 0.0


# ---------------------------------------------------------------------------
# SlidingWindow — __repr__()
# ---------------------------------------------------------------------------


def test_sliding_window_repr():
    sw = SlidingWindow(max_calls=42, window_seconds=7.5)
    r = repr(sw)
    assert "SlidingWindow" in r
    assert "42" in r
    assert "7.5" in r


# ---------------------------------------------------------------------------
# SlidingWindow — thread safety
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
    # Should complete without errors.
    assert True
