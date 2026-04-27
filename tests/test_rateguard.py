"""Tests for rateguard."""

import threading
import time

import pytest

from rateguard import (
    BucketStats,
    SlidingWindow,
    TokenBucket,
    WindowStats,
    __version__,
)


# ── Version ───────────────────────────────────────────────────────────────────

def test_version_string():
    assert isinstance(__version__, str)
    parts = __version__.split(".")
    assert len(parts) >= 2
    assert all(p.isdigit() for p in parts)


# ── TokenBucket: construction and validation ──────────────────────────────────

def test_token_bucket_invalid_rate_raises():
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=0.0, burst=1)
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=-1.0, burst=5)


def test_token_bucket_invalid_burst_raises():
    with pytest.raises(ValueError, match="burst"):
        TokenBucket(rate=1.0, burst=0)


def test_token_bucket_repr():
    b = TokenBucket(rate=5.0, burst=10)
    assert repr(b) == "TokenBucket(rate=5.0, burst=10)"


# ── TokenBucket: acquire ──────────────────────────────────────────────────────

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
    assert wait < 0.5  # 2 tokens at 10/s takes ~0.2s


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(rate=200.0, burst=1)
    assert bucket.acquire(1) == 0.0
    time.sleep(0.05)
    assert bucket.acquire(1) == 0.0


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


def test_token_bucket_wait_is_proportional_to_deficit():
    # Drain 2 tokens from a rate-1 bucket with burst=2; deficit = 2 tokens.
    bucket = TokenBucket(rate=1.0, burst=2)
    assert bucket.acquire(2) == 0.0
    wait = bucket.acquire(2)
    assert 1.8 < wait < 2.2


def test_token_bucket_thread_safety_no_crash():
    bucket = TokenBucket(rate=10_000.0, burst=1000)
    results = []
    lock = threading.Lock()

    def worker():
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


# ── TokenBucket: stats ────────────────────────────────────────────────────────

def test_token_bucket_stats_initial():
    b = TokenBucket(rate=10.0, burst=5)
    s = b.stats()
    assert isinstance(s, BucketStats)
    assert s.current_tokens == pytest.approx(5.0, abs=0.1)
    assert s.total_acquired == 0
    assert s.total_waited == 0
    assert s.total_wait_time == 0.0


def test_token_bucket_stats_after_acquire():
    b = TokenBucket(rate=10.0, burst=5)
    b.acquire(3)
    s = b.stats()
    assert s.total_acquired == 1
    assert s.total_waited == 0
    assert s.current_tokens == pytest.approx(2.0, abs=0.05)


def test_token_bucket_stats_records_wait():
    b = TokenBucket(rate=10.0, burst=2)
    b.acquire(2)          # immediate
    wait = b.acquire(2)   # deferred
    assert wait > 0
    s = b.stats()
    assert s.total_acquired == 2
    assert s.total_waited == 1
    assert s.total_wait_time == pytest.approx(wait, rel=1e-6)


# ── TokenBucket: reset ────────────────────────────────────────────────────────

def test_token_bucket_reset_restores_full():
    b = TokenBucket(rate=1.0, burst=5)
    b.acquire(5)
    # After draining at rate=1, a second acquire immediately returns wait > 0
    assert b.acquire(5) > 0
    b.reset()
    s_after = b.stats()
    assert s_after.current_tokens == pytest.approx(5.0, abs=0.01)
    assert s_after.total_acquired == 0
    assert s_after.total_waited == 0
    assert s_after.total_wait_time == 0.0


def test_token_bucket_reset_clears_counters():
    b = TokenBucket(rate=10.0, burst=3)
    b.acquire(3)
    b.acquire(3)
    b.reset()
    s = b.stats()
    assert s.total_acquired == 0
    assert s.total_waited == 0


# ── SlidingWindow: construction and validation ────────────────────────────────

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


def test_sliding_window_repr():
    sw = SlidingWindow(max_calls=10, window_seconds=5.0)
    assert repr(sw) == "SlidingWindow(max_calls=10, window_seconds=5.0)"


# ── SlidingWindow: acquire ────────────────────────────────────────────────────

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
    assert 0.0 < wait <= 1.0


def test_sliding_window_admits_again_after_window():
    sw = SlidingWindow(max_calls=1, window_seconds=0.05)
    assert sw.acquire() == 0.0
    time.sleep(0.06)
    assert sw.acquire() == 0.0


def test_sliding_window_does_not_reserve_when_blocked():
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    assert sw.acquire() == 0.0
    first_wait = sw.acquire()
    second_wait = sw.acquire()
    assert first_wait > 0.0
    assert second_wait > 0.0
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


# ── SlidingWindow: stats ──────────────────────────────────────────────────────

def test_sliding_window_stats_initial():
    sw = SlidingWindow(max_calls=3, window_seconds=1.0)
    s = sw.stats()
    assert isinstance(s, WindowStats)
    assert s.current_calls == 0
    assert s.call_times == ()
    assert s.total_admitted == 0
    assert s.total_blocked == 0


def test_sliding_window_stats_after_admits():
    sw = SlidingWindow(max_calls=3, window_seconds=60.0)
    sw.acquire()
    sw.acquire()
    s = sw.stats()
    assert s.current_calls == 2
    assert len(s.call_times) == 2
    assert s.total_admitted == 2
    assert s.total_blocked == 0


def test_sliding_window_stats_records_blocked():
    sw = SlidingWindow(max_calls=1, window_seconds=60.0)
    sw.acquire()  # admitted
    sw.acquire()  # blocked
    sw.acquire()  # blocked
    s = sw.stats()
    assert s.total_admitted == 1
    assert s.total_blocked == 2
    assert s.current_calls == 1


def test_sliding_window_stats_call_times_are_monotonic():
    sw = SlidingWindow(max_calls=3, window_seconds=60.0)
    t0 = time.monotonic()
    sw.acquire()
    sw.acquire()
    t1 = time.monotonic()
    s = sw.stats()
    for ts in s.call_times:
        assert t0 <= ts <= t1


def test_sliding_window_stats_evicts_expired():
    sw = SlidingWindow(max_calls=1, window_seconds=0.05)
    sw.acquire()
    time.sleep(0.06)
    s = sw.stats()
    assert s.current_calls == 0
    assert s.call_times == ()


# ── SlidingWindow: reset ──────────────────────────────────────────────────────

def test_sliding_window_reset_clears_calls():
    sw = SlidingWindow(max_calls=3, window_seconds=60.0)
    sw.acquire()
    sw.acquire()
    sw.reset()
    s = sw.stats()
    assert s.current_calls == 0
    assert s.call_times == ()
    assert s.total_admitted == 0
    assert s.total_blocked == 0


def test_sliding_window_reset_allows_immediate_reuse():
    sw = SlidingWindow(max_calls=1, window_seconds=60.0)
    sw.acquire()
    assert sw.acquire() > 0  # would be blocked
    sw.reset()
    assert sw.acquire() == 0.0  # admitted after reset
