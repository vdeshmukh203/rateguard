"""Tests for rateguard."""

import math
import threading
import time

import pytest

from rateguard import CompositeRateLimiter, SlidingWindow, TokenBucket


# ===========================================================================
# TokenBucket – construction
# ===========================================================================

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
    # Refilling 2 tokens at 10/s takes ~0.2 s.
    assert wait < 0.5


def test_token_bucket_refills_over_time():
    bucket = TokenBucket(rate=200.0, burst=1)
    assert bucket.acquire(1) == 0.0
    time.sleep(0.05)
    assert bucket.acquire(1) == 0.0


def test_token_bucket_invalid_rate_raises():
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=0.0, burst=1)
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=-1.0, burst=5)
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=math.inf, burst=5)
    with pytest.raises(ValueError, match="rate"):
        TokenBucket(rate=math.nan, burst=5)


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


# ===========================================================================
# TokenBucket – peek
# ===========================================================================

def test_token_bucket_peek_returns_zero_when_available():
    bucket = TokenBucket(rate=10.0, burst=5)
    assert bucket.peek(3) == 0.0


def test_token_bucket_peek_positive_when_insufficient():
    bucket = TokenBucket(rate=1.0, burst=2)
    bucket.acquire(2)
    wait = bucket.peek(2)
    assert wait > 0.0
    assert wait < 2.5


def test_token_bucket_peek_does_not_consume_tokens():
    bucket = TokenBucket(rate=10.0, burst=5)
    for _ in range(10):
        bucket.peek(5)
    # tokens still full – acquire should succeed immediately
    assert bucket.acquire(5) == 0.0


def test_token_bucket_peek_invalid_args_raises():
    bucket = TokenBucket(rate=1.0, burst=2)
    with pytest.raises(ValueError):
        bucket.peek(0)
    with pytest.raises(ValueError):
        bucket.peek(3)


# ===========================================================================
# TokenBucket – reset and tokens property
# ===========================================================================

def test_token_bucket_reset_refills_bucket():
    bucket = TokenBucket(rate=1.0, burst=5)
    bucket.acquire(5)
    assert bucket.tokens < 1.0
    bucket.reset()
    assert bucket.tokens == pytest.approx(5.0, abs=0.01)


def test_token_bucket_tokens_property_decreases_on_acquire():
    bucket = TokenBucket(rate=1.0, burst=10)
    before = bucket.tokens
    bucket.acquire(3)
    after = bucket.tokens
    assert after < before


def test_token_bucket_tokens_property_increases_over_time():
    bucket = TokenBucket(rate=100.0, burst=10)
    bucket.acquire(10)
    t0 = bucket.tokens
    time.sleep(0.05)
    t1 = bucket.tokens
    assert t1 > t0


# ===========================================================================
# TokenBucket – repr
# ===========================================================================

def test_token_bucket_repr():
    bucket = TokenBucket(rate=5.0, burst=10)
    r = repr(bucket)
    assert "TokenBucket" in r
    assert "5.0" in r
    assert "10" in r


# ===========================================================================
# TokenBucket – thread safety
# ===========================================================================

def test_token_bucket_thread_safety_no_crash():
    bucket = TokenBucket(rate=10000.0, burst=1000)
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


# ===========================================================================
# SlidingWindow – construction
# ===========================================================================

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
    with pytest.raises(ValueError, match="window_seconds"):
        SlidingWindow(max_calls=1, window_seconds=math.inf)
    with pytest.raises(ValueError, match="window_seconds"):
        SlidingWindow(max_calls=1, window_seconds=math.nan)


def test_sliding_window_does_not_reserve_when_blocked():
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    assert sw.acquire() == 0.0
    first_wait = sw.acquire()
    second_wait = sw.acquire()
    assert first_wait > 0.0
    assert second_wait > 0.0
    assert abs(first_wait - second_wait) < 1.0


# ===========================================================================
# SlidingWindow – peek
# ===========================================================================

def test_sliding_window_peek_zero_when_below_limit():
    sw = SlidingWindow(max_calls=3, window_seconds=1.0)
    assert sw.peek() == 0.0


def test_sliding_window_peek_positive_when_at_limit():
    sw = SlidingWindow(max_calls=2, window_seconds=1.0)
    sw.acquire()
    sw.acquire()
    wait = sw.peek()
    assert wait > 0.0
    assert wait <= 1.0


def test_sliding_window_peek_does_not_record_call():
    sw = SlidingWindow(max_calls=1, window_seconds=10.0)
    for _ in range(5):
        sw.peek()
    # No calls recorded yet – first acquire must succeed.
    assert sw.acquire() == 0.0


# ===========================================================================
# SlidingWindow – reset and current_calls property
# ===========================================================================

def test_sliding_window_reset_clears_calls():
    sw = SlidingWindow(max_calls=2, window_seconds=10.0)
    sw.acquire()
    sw.acquire()
    assert sw.current_calls == 2
    sw.reset()
    assert sw.current_calls == 0
    assert sw.acquire() == 0.0


def test_sliding_window_current_calls_increases():
    sw = SlidingWindow(max_calls=5, window_seconds=60.0)
    assert sw.current_calls == 0
    sw.acquire()
    assert sw.current_calls == 1
    sw.acquire()
    assert sw.current_calls == 2


def test_sliding_window_current_calls_decreases_over_time():
    sw = SlidingWindow(max_calls=5, window_seconds=0.05)
    sw.acquire()
    sw.acquire()
    time.sleep(0.07)
    assert sw.current_calls == 0


# ===========================================================================
# SlidingWindow – repr
# ===========================================================================

def test_sliding_window_repr():
    sw = SlidingWindow(max_calls=60, window_seconds=60.0)
    r = repr(sw)
    assert "SlidingWindow" in r
    assert "60" in r


# ===========================================================================
# SlidingWindow – thread safety
# ===========================================================================

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


# ===========================================================================
# CompositeRateLimiter – construction
# ===========================================================================

def test_composite_requires_at_least_one_limiter():
    with pytest.raises(ValueError, match="at least one"):
        CompositeRateLimiter()


def test_composite_admits_when_all_limiters_ready():
    comp = CompositeRateLimiter(
        TokenBucket(rate=100.0, burst=10),
        SlidingWindow(max_calls=100, window_seconds=60.0),
    )
    assert comp.acquire() == 0.0


def test_composite_blocks_when_bucket_drained():
    comp = CompositeRateLimiter(
        TokenBucket(rate=1.0, burst=1),
        SlidingWindow(max_calls=100, window_seconds=60.0),
    )
    assert comp.acquire() == 0.0
    wait = comp.acquire()
    assert wait > 0.0


def test_composite_blocks_when_window_full():
    comp = CompositeRateLimiter(
        TokenBucket(rate=100.0, burst=10),
        SlidingWindow(max_calls=1, window_seconds=10.0),
    )
    assert comp.acquire() == 0.0
    wait = comp.acquire()
    assert wait > 0.0


# ===========================================================================
# CompositeRateLimiter – peek
# ===========================================================================

def test_composite_peek_zero_when_all_ready():
    comp = CompositeRateLimiter(
        TokenBucket(rate=100.0, burst=10),
        SlidingWindow(max_calls=100, window_seconds=60.0),
    )
    assert comp.peek() == 0.0


def test_composite_peek_positive_when_any_blocked():
    comp = CompositeRateLimiter(
        TokenBucket(rate=100.0, burst=10),
        SlidingWindow(max_calls=1, window_seconds=10.0),
    )
    comp.acquire()  # fill window
    assert comp.peek() > 0.0


def test_composite_peek_does_not_consume():
    tb = TokenBucket(rate=100.0, burst=5)
    sw = SlidingWindow(max_calls=5, window_seconds=60.0)
    comp = CompositeRateLimiter(tb, sw)
    for _ in range(10):
        comp.peek()
    # state must be intact – all acquires should succeed
    for _ in range(5):
        assert comp.acquire() == 0.0


# ===========================================================================
# CompositeRateLimiter – reset and repr
# ===========================================================================

def test_composite_reset_clears_state():
    comp = CompositeRateLimiter(
        TokenBucket(rate=1.0, burst=1),
        SlidingWindow(max_calls=1, window_seconds=60.0),
    )
    comp.acquire()
    assert comp.acquire() > 0.0
    comp.reset()
    assert comp.acquire() == 0.0


def test_composite_repr():
    comp = CompositeRateLimiter(
        TokenBucket(rate=10.0, burst=5),
        SlidingWindow(max_calls=60, window_seconds=60.0),
    )
    r = repr(comp)
    assert "CompositeRateLimiter" in r
    assert "TokenBucket" in r
    assert "SlidingWindow" in r


# ===========================================================================
# Package metadata
# ===========================================================================

def test_version_attribute_exists():
    import rateguard
    assert hasattr(rateguard, "__version__")
    assert isinstance(rateguard.__version__, str)
    parts = rateguard.__version__.split(".")
    assert len(parts) >= 2
