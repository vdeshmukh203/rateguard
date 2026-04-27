# rateguard

**Local rate limiter for LLM API calls.**

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/downloads/)

---

## Statement of Need

Large-language-model (LLM) providers enforce rate limits on their APIs—typically expressed as a maximum number of requests per second or per minute. Applications that generate bursts of concurrent calls routinely trigger `429 Too Many Requests` errors, which must then be handled with retries, back-off logic, and token-counting scattered across the codebase.

**rateguard** centralises that concern into two composable, thread-safe primitives that a caller consults *before* issuing a request. Each primitive answers one question: "how long, if at all, do I need to wait?" The library has **zero runtime dependencies**, uses only the Python standard library, and is safe for use in multi-threaded applications.

---

## Algorithms

### Token Bucket

The token bucket maintains a reservoir that refills at a constant rate `r` (tokens per second) up to a maximum burst capacity `b`. Each API call consumes one or more tokens.

- When the bucket has enough tokens, `acquire` returns `0.0` and the call may proceed immediately.
- When the bucket is depleted, the deficit `d = tokens_requested − tokens_available` determines the wait: `wait = d / r`. The requested tokens are **reserved immediately**, so concurrent callers share the wait fairly rather than competing for the same tokens after a sleep.

This gives smooth throughput at rate `r` while tolerating short bursts up to `b`.

### Sliding Window

The sliding window records a timestamp for each admitted call and evicts timestamps older than `window_seconds`. Before admitting a new call the window checks whether the count of recent calls is below `max_calls`.

- When below the limit, the call is recorded and `acquire` returns `0.0`.
- When at the limit, `acquire` returns the time until the oldest call will expire. **No slot is reserved**, so the caller must retry after sleeping.

This enforces a hard ceiling of `max_calls` in any contiguous `window_seconds`-long interval.

---

## Installation

```bash
pip install rateguard
```

No additional dependencies are required. Python 3.9 or later is supported.

---

## Quick Start

```python
import time
from rateguard import TokenBucket, SlidingWindow

# ── Token bucket: 10 requests/sec, burst up to 20 ────────────────────────────
bucket = TokenBucket(rate=10.0, burst=20)

wait = bucket.acquire()        # consume 1 token
if wait:
    time.sleep(wait)
# ... issue your API call ...

wait = bucket.acquire(tokens=3)  # consume 3 tokens at once
if wait:
    time.sleep(wait)

# ── Sliding window: at most 60 calls per minute ───────────────────────────────
window = SlidingWindow(max_calls=60, window_seconds=60.0)

while True:
    wait = window.acquire()
    if not wait:
        break
    time.sleep(wait)
# ... issue your API call ...
```

---

## API Reference

### `TokenBucket(rate, burst)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `rate` | `float` | Tokens added per second. Must be positive. |
| `burst` | `int` | Maximum tokens the bucket can hold. Must be ≥ 1. |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire(tokens=1)` | `float` | Wait time in seconds; `0.0` = immediate. Tokens are reserved either way. |
| `stats()` | `BucketStats` | Snapshot of current token level and counters. |
| `reset()` | `None` | Refill to capacity and zero all counters. |

### `SlidingWindow(max_calls, window_seconds)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_calls` | `int` | Maximum calls allowed in the window. Must be ≥ 1. |
| `window_seconds` | `float` | Window length in seconds. Must be positive. |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire()` | `float` | Wait time in seconds; `0.0` = admitted. No slot reserved when blocked. |
| `stats()` | `WindowStats` | Snapshot of current window state and counters. |
| `reset()` | `None` | Clear all recorded calls and zero all counters. |

### Statistics objects

```python
from rateguard import BucketStats, WindowStats

s: BucketStats = bucket.stats()
s.current_tokens   # float  – tokens available right now (may be negative)
s.total_acquired   # int    – total acquire() calls returned
s.total_waited     # int    – calls that returned a positive wait
s.total_wait_time  # float  – cumulative wait time in seconds

w: WindowStats = window.stats()
w.current_calls    # int             – calls in the window right now
w.call_times       # tuple[float]    – monotonic timestamps of those calls
w.total_admitted   # int             – acquire() calls that were admitted
w.total_blocked    # int             – acquire() calls that were blocked
```

---

## Interactive Dashboard (GUI)

rateguard ships with a built-in Tkinter dashboard for interactive exploration
and demonstration of both rate limiters.

```bash
# After installation:
rateguard-gui

# Or without installation:
python -m rateguard
```

The dashboard provides:

- **Token Bucket tab** — configure `rate` and `burst`; watch the animated token
  reservoir fill and drain in real time; issue acquires with optional auto-sleep.
- **Sliding Window tab** — configure `max_calls` and `window_seconds`; observe
  call dots slide across the timeline as the window advances; see capacity fill
  level update live.
- Both tabs display running statistics and a timestamped log.

The GUI depends only on `tkinter`, which is included with the CPython standard
library on all major platforms (Linux, macOS, Windows). No additional packages
are required.

---

## Thread Safety

Both primitives use a `threading.Lock` to serialise all state mutations. They
are safe for use from any number of threads concurrently. The lock is held only
for the brief duration of the acquire calculation — no I/O or sleeping occurs
inside the lock.

---

## Testing

```bash
pip install pytest
pytest
```

The test suite covers construction validation, immediate and deferred
acquisition, refill-over-time behaviour, statistics correctness, reset
semantics, boundary conditions, and thread safety. All tests use only the
standard library timer (`time.monotonic`) and avoid unnecessary sleeps.

---

## Contributing

Contributions are welcome. Please open an issue to discuss significant changes
before submitting a pull request. All pull requests must pass the existing test
suite and include tests for any new behaviour.

---

## License

MIT — see [LICENSE](LICENSE).
