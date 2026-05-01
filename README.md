# rateguard

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml)
[![PyPI version](https://img.shields.io/pypi/v/rateguard)](https://pypi.org/project/rateguard/)
[![Python versions](https://img.shields.io/pypi/pyversions/rateguard)](https://pypi.org/project/rateguard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**rateguard** is a zero-dependency, thread-safe rate-limiting library for
Python applications that call large-language-model (LLM) APIs or any other
service with per-second or per-minute quotas.

---

## Statement of Need

Every major LLM API provider (OpenAI, Anthropic, Google, Cohere, …) enforces
rate limits expressed as *requests per minute* (RPM), *tokens per minute*
(TPM), or both.  Exceeding these limits returns `HTTP 429` errors that must be
retried, wasting latency and potentially leaving the model mid-generation.

Existing HTTP client middlewares (e.g. `tenacity`, `backoff`) retry *after*
the fact.  rateguard instead acts *proactively*: it measures quota consumption
locally and delays the caller just enough to stay within the budget, with no
network round-trips.

Two algorithms are provided:

| Algorithm | Best for |
|-----------|---------|
| **TokenBucket** | Smooth average throughput with controlled bursting |
| **SlidingWindow** | Hard per-period call counts (e.g. "60 RPM") |

Both primitives are pure Python standard-library with no external dependencies,
making them embeddable in any project without version conflicts.

---

## Installation

```bash
pip install rateguard
```

Requires Python 3.9 or later.

### Development install

```bash
git clone https://github.com/vdeshmukh203/rateguard
cd rateguard
pip install -e ".[dev]"
```

---

## Quick Start

```python
import time
from rateguard import TokenBucket, SlidingWindow

# ── Token Bucket: 10 requests/s, allow bursts up to 20 ──────────────────
bucket = TokenBucket(rate=10.0, burst=20)

for query in queries:
    wait = bucket.acquire(1)
    if wait:
        time.sleep(wait)
    response = llm_client.complete(query)

# ── Sliding Window: at most 60 calls per 60-second window ───────────────
window = SlidingWindow(max_calls=60, window_seconds=60.0)

for query in queries:
    while True:
        wait = window.acquire()
        if not wait:
            break
        time.sleep(wait)
    response = llm_client.complete(query)
```

---

## Algorithm Overview

### Token Bucket

The bucket holds up to `burst` tokens and refills at `rate` tokens per second.
Each `acquire(n)` call consumes `n` tokens:

- **Tokens available** → returns `0.0`, caller may proceed immediately.
- **Tokens insufficient** → reserves the tokens (allowing future callers to
  be scheduled fairly), returns the number of seconds to wait before the
  reserved tokens are ready.

Because tokens are reserved even on a blocked call, concurrent callers each
receive a non-overlapping, fair wait estimate without any external
coordination.

```
  burst ──► ████████████  ◄── refills at `rate` tok/s
             ▼ acquire(3)
             ████████░░░   (3 tokens consumed)
```

### Sliding Window

The window records the wall-clock timestamp of every admitted call.
`acquire()` evicts timestamps older than `window_seconds` and then:

- **Slots available** → records the timestamp, returns `0.0`.
- **Window full** → returns the wait until the *oldest* recorded call ages
  out of the window, freeing a slot.  No slot is reserved; the caller must
  retry after sleeping.

```
  window_seconds ──► [──────────────────── 60 s ────────────────────]
  recorded calls ──► ●   ●  ●●   ●    ●●●      ●   ●  (10 of max 15)
```

### Choosing an Algorithm

| Concern | Use |
|---------|-----|
| Need burst headroom (e.g. batch-fill a cache) | `TokenBucket` |
| Strict "N calls per T seconds" budget | `SlidingWindow` |
| Concurrent threads sharing one limiter | Both are safe |
| Token consumption varies per call | `TokenBucket(acquire(n))` |

---

## API Reference

### `TokenBucket(rate, burst)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `rate` | `float` | Tokens added per second (must be > 0) |
| `burst` | `int` | Maximum bucket capacity (must be ≥ 1) |

| Method / Property | Returns | Description |
|-------------------|---------|-------------|
| `acquire(tokens=1)` | `float` | Wait seconds (0 = immediate). Reserves tokens. |
| `tokens_available` | `float` | Current available tokens (≥ 0, triggers refill). |
| `reset()` | `None` | Restore bucket to full capacity. |
| `repr(bucket)` | `str` | `TokenBucket(rate=…, burst=…)` |

### `SlidingWindow(max_calls, window_seconds)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_calls` | `int` | Maximum admissions per window (must be ≥ 1) |
| `window_seconds` | `float` | Window length in seconds (must be > 0) |

| Method / Property | Returns | Description |
|-------------------|---------|-------------|
| `acquire()` | `float` | Wait seconds (0 = admitted). Does **not** reserve. |
| `calls_in_window` | `int` | Number of calls currently in the window. |
| `slots_remaining` | `int` | `max_calls − calls_in_window` right now. |
| `call_ages()` | `list[float]` | Seconds-since-admission for each in-window call. |
| `reset()` | `None` | Clear all recorded calls. |
| `repr(window)` | `str` | `SlidingWindow(max_calls=…, window_seconds=…)` |

### `RateLimiter` (Protocol)

A `typing.Protocol` satisfied at runtime by both `TokenBucket` and
`SlidingWindow`.  Use it for type annotations when the specific algorithm
does not matter:

```python
from rateguard import RateLimiter

def call_with_limit(limiter: RateLimiter, fn) -> None:
    wait = limiter.acquire()
    if wait:
        time.sleep(wait)
    return fn()
```

---

## Thread Safety

All state mutations in both classes are protected by a `threading.Lock`.
A single instance can safely be shared across any number of threads.
Each `acquire()` call is atomic.

```python
import threading
from rateguard import SlidingWindow

shared_window = SlidingWindow(max_calls=100, window_seconds=60.0)

def worker(queries):
    for q in queries:
        while True:
            wait = shared_window.acquire()
            if not wait:
                break
            time.sleep(wait)
        # make the API call

threads = [threading.Thread(target=worker, args=(batch,)) for batch in batches]
for t in threads:
    t.start()
```

---

## GUI Visualizer

rateguard ships a built-in Tkinter visualizer for exploring rate-limiting
behaviour interactively.  Launch it with:

```bash
rateguard-gui
```

or from Python:

```python
from rateguard._gui import main
main()
```

The visualizer provides:

- **Token Bucket tab** — animated fill-level gauge, manual/auto acquire
  controls, and a live activity log.
- **Sliding Window tab** — real-time timeline of in-window calls, capacity
  fill bar, and a live activity log.

---

## Running the Tests

```bash
pytest                        # run all tests
pytest --cov=rateguard        # with coverage report
```

---

## Contributing

Contributions are welcome!  See [CONTRIBUTING.md](CONTRIBUTING.md) for setup
instructions, coding standards, and the pull-request process.

---

## License

MIT — see [LICENSE](LICENSE).
