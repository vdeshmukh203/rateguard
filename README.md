# rateguard

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions)
[![PyPI](https://img.shields.io/pypi/v/rateguard)](https://pypi.org/project/rateguard/)
[![Python](https://img.shields.io/pypi/pyversions/rateguard)](https://pypi.org/project/rateguard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Thread-safe, **pure standard-library** rate limiting primitives for Python
LLM API clients. No external dependencies; works everywhere Python 3.9+ runs.

## Why rateguard?

LLM providers (OpenAI, Anthropic, Google …) enforce per-minute request and
token quotas. Calling them concurrently without local throttling leads to
avoidable HTTP 429 errors and wasted retries. `rateguard` puts the rate
budget *in front of* the API call so that your application never exceeds it
in the first place.

Two well-known algorithms are provided:

| Primitive | Algorithm | Burst handling | Reservation |
|-----------|-----------|----------------|-------------|
| `TokenBucket` | Token bucket | Yes — up to `burst` | Tokens reserved on acquire |
| `SlidingWindow` | Sliding window | No | No — caller must retry |

Both primitives are thread-safe and use `threading.Lock` + `time.monotonic()`
internally.

## Install

```bash
pip install rateguard
```

## Quick start

```python
import time
from rateguard import TokenBucket, SlidingWindow

# ── Token Bucket ──────────────────────────────────────────────────────────
# 10 requests/sec sustained; burst up to 20 requests.
bucket = TokenBucket(rate=10.0, burst=20)

wait = bucket.acquire(1)
if wait > 0:
    time.sleep(wait)
# safe to call the API now

# ── Sliding Window ────────────────────────────────────────────────────────
# At most 60 calls per 60-second window.
window = SlidingWindow(max_calls=60, window_seconds=60.0)

while True:
    wait = window.acquire()
    if wait == 0:
        break
    time.sleep(wait)
# safe to call the API now
```

## API reference

### `TokenBucket(rate, burst)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `rate` | `float` | Tokens added per second. Must be > 0. |
| `burst` | `int` | Maximum tokens the bucket can hold. Must be >= 1. |

**Methods**

```python
bucket.acquire(tokens=1) -> float
```
Consume `tokens` from the bucket. Returns `0.0` if tokens were available
immediately; otherwise returns the number of seconds to sleep before
proceeding. Tokens are *reserved* in both cases, so concurrent callers each
receive their own fair wait slot.

```python
bucket.reset()
```
Refill the bucket to full burst capacity.

**Properties**

```python
bucket.tokens_available  # float — current level, clamped to [0, burst]
bucket.rate              # float
bucket.burst             # int
```

---

### `SlidingWindow(max_calls, window_seconds)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_calls` | `int` | Maximum calls allowed in the window. Must be >= 1. |
| `window_seconds` | `float` | Window length in seconds. Must be > 0. |

**Methods**

```python
window.acquire() -> float
```
Attempt to admit a call. Returns `0.0` if the call was admitted; otherwise
returns the seconds until the next slot opens. No slot is reserved when
blocked — the caller *must* call `acquire()` again after sleeping.

```python
window.reset()
```
Clear all recorded calls, effectively emptying the window.

**Properties**

```python
window.calls_in_window  # int — admitted calls currently in the window
window.slots_remaining  # int — remaining capacity
window.max_calls        # int
window.window_seconds   # float
```

## Interactive GUI dashboard

A live visualisation dashboard ships with the package:

```bash
# via the installed entry point
rateguard-gui

# or directly
python -m rateguard
```

The dashboard lets you configure each limiter, click **Acquire** to simulate
API calls, and watch the level bars update in real time at 10 Hz.  It is
useful for choosing parameters before committing to them in production code.

## Concurrency pattern

Both primitives are safe to share across threads. A typical pattern for a
thread pool that calls an LLM API:

```python
import concurrent.futures
import time
from rateguard import TokenBucket

bucket = TokenBucket(rate=5.0, burst=10)  # 5 req/s, burst of 10

def call_llm(prompt: str) -> str:
    wait = bucket.acquire(1)
    if wait > 0:
        time.sleep(wait)
    return my_llm_client.complete(prompt)

with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
    results = list(pool.map(call_llm, prompts))
```

## Development

```bash
git clone https://github.com/vdeshmukh203/rateguard.git
cd rateguard
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).

## Citation

If you use `rateguard` in academic work, please cite the JOSS paper:

```
Deshmukh, V. (2026). rateguard: Thread-safe rate limiting primitives for
Python LLM API clients. Journal of Open Source Software.
```
