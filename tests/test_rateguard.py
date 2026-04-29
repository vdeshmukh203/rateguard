"""Tests for rateguard."""

import threading
import time

import pytest

import rateguard
from rateguard import SlidingWindow, TokenBucket


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------

def test_version_is_present():
    assert hasattr(rateguard, "__version__")
    assert isinstance(rateguard.__version__, str)
    assert rateguard.__version__  # non-empty


# ---------------------------------------------------------------------------
# TokenBucket — core behaviour
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
    # Filling 2 tokens at 10/s takes ≈0.2 s; allow generous headroom.
    assert wait < 0.5


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(rate=200.0, burst=1)
    assert bucket.acquire(1) == 0.0
    time.sleep(0.05)
    # After 50 ms at 200 tok/s the bucket has refilled.
    assert bucket.acquire(1) == 0.0


def test_token_bucket_wait_is_proportional_to_deficit():
    bucket = TokenBucket(rate=10.0, burst=5)
    bucket.acquire(5)          # drain
    wait = bucket.acquire(5)   # request full burst again
    # 5 tokens at 10/s = 0.5 s; allow ±0.1 s tolerance
    assert 0.4 <= wait <= 0.6


def test_token_bucket_sequential_waits_do_not_overlap():
    """Pre-reservation ensures two concurrent callers queue, not race."""
    bucket = TokenBucket(rate=10.0, burst=2)
    bucket.acquire(2)
    wait1 = bucket.acquire(1)
    wait2 = bucket.acquire(1)
    # Second reservation must wait strictly longer than the first.
    assert wait2 > wait1


# ---------------------------------------------------------------------------
# TokenBucket — input validation
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
# TokenBucket — new properties
# ---------------------------------------------------------------------------

def test_token_bucket_tokens_available_full_at_start():
    bucket = TokenBucket(rate=1.0, burst=10)
    assert bucket.tokens_available == pytest.approx(10.0, abs=0.01)


def test_token_bucket_tokens_available_decreases_after_acquire():
    bucket = TokenBucket(rate=1.0, burst=10)
    bucket.acquire(4)
    assert bucket.tokens_available == pytest.approx(6.0, abs=0.1)


def test_token_bucket_tokens_available_clamps_to_zero():
    bucket = TokenBucket(rate=0.1, burst=1)
    bucket.acquire(1)
    # Rate is 0.1 tok/s so negligible refill occurs between acquire and read.
    assert bucket.tokens_available < 0.01


def test_token_bucket_tokens_available_refills_over_time():
    bucket = TokenBucket(rate=100.0, burst=10)
    bucket.acquire(10)
    time.sleep(0.05)   # 5 tokens should refill at 100/s
    assert bucket.tokens_available >= 4.0


# ---------------------------------------------------------------------------
# TokenBucket — thread safety
# ---------------------------------------------------------------------------

def test_token_bucket_thread_safety_no_crash():
    bucket = TokenBucket(rate=10000.0, burst=1000)
    results: list[float] = []
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


# ---------------------------------------------------------------------------
# SlidingWindow — core behaviour
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


def test_sliding_window_wait_does_not_exceed_window():
    sw = SlidingWindow(max_calls=1, window_seconds=2.0)
    sw.acquire()
    wait = sw.acquire()
    assert 0.0 < wait <= 2.0


# ---------------------------------------------------------------------------
# SlidingWindow — input validation
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
    wait1 = sw.acquire()
    wait2 = sw.acquire()
    assert wait1 > 0.0
    assert wait2 > 0.0
    # Neither blocked call reserved a slot; both report approximately the same
    # remaining wait.
    assert abs(wait1 - wait2) < 0.05


# ---------------------------------------------------------------------------
# SlidingWindow — new properties
# ---------------------------------------------------------------------------

def test_sliding_window_calls_in_window_starts_at_zero():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    assert sw.calls_in_window == 0


def test_sliding_window_calls_in_window_increments():
    sw = SlidingWindow(max_calls=5, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    assert sw.calls_in_window == 2


def test_sliding_window_calls_in_window_evicts_expired():
    sw = SlidingWindow(max_calls=5, window_seconds=0.05)
    sw.acquire()
    assert sw.calls_in_window == 1
    time.sleep(0.06)
    assert sw.calls_in_window == 0


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
