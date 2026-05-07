# rateguard

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/rateguard.svg)](https://pypi.org/project/rateguard/)
[![Python](https://img.shields.io/pypi/pyversions/rateguard.svg)](https://pypi.org/project/rateguard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Thread-safe local rate limiter for LLM API calls.  Pure standard library,
zero dependencies.

## Why rateguard?

Commercial LLM APIs (OpenAI, Anthropic, Google, Mistral …) enforce rate limits
along two independent axes: **requests per minute (RPM)** and **tokens per
minute (TPM)**.  Exceeding either limit returns an HTTP 429, wasting latency
and triggering back-off loops.

`rateguard` solves this with **client-side pre-admission control**: each
`acquire()` call returns the number of seconds to sleep *before* issuing the
request, so 429s never occur in the first place.

| Feature | rateguard | ratelimit | aiolimiter |
|---|---|---|---|
| No external deps | ✓ | ✓ | ✓ |
| Thread-safe | ✓ | partial | asyncio only |
| Wait-time API (not raise/block) | ✓ | ✗ | ✗ |
| Token-bucket + sliding-window | ✓ | ✗ | token-bucket only |
| Composite (RPM + TPM) | ✓ | ✗ | ✗ |
| Interactive dashboard | ✓ | ✗ | ✗ |

## Install

```bash
pip install rateguard
```

## Quick start

### Token bucket — steady throughput with burst support

```python
import time
from rateguard import TokenBucket

# 10 requests/s, burst up to 20
bucket = TokenBucket(rate=10.0, burst=20)

for prompt in prompts:
    wait = bucket.acquire(1)   # returns 0.0 if tokens available immediately
    if wait > 0:
        time.sleep(wait)
    response = client.chat.completions.create(...)
```

### Sliding window — strict per-minute cap

```python
from rateguard import SlidingWindow

# At most 60 requests per minute
window = SlidingWindow(max_calls=60, window_seconds=60.0)

while True:
    wait = window.acquire()
    if wait == 0.0:
        break
    time.sleep(wait)
# proceed with the API call
```

### Composite — enforce RPM *and* TPM simultaneously

```python
from rateguard import CompositeRateLimiter, TokenBucket, SlidingWindow

limiter = CompositeRateLimiter(
    TokenBucket(rate=10.0, burst=10),           # ≤ 10 req/s burst
    SlidingWindow(max_calls=500, window_seconds=60.0),  # ≤ 500 req/min
)

while True:
    wait = limiter.acquire(tokens=1)
    if wait == 0.0:
        break
    time.sleep(wait)
```

## API reference

### `TokenBucket(rate, burst)`

| Parameter | Type | Description |
|---|---|---|
| `rate` | `float` | Tokens replenished per second (must be finite and > 0) |
| `burst` | `int` | Maximum bucket capacity and maximum tokens per acquire (≥ 1) |

| Method / property | Description |
|---|---|
| `acquire(tokens=1) → float` | Consume tokens; return wait seconds (0.0 = immediate) |
| `peek(tokens=1) → float` | Same computation but **no** tokens consumed |
| `reset()` | Refill bucket to `burst` capacity |
| `tokens` | Current fill level (may be negative under heavy concurrency) |

### `SlidingWindow(max_calls, window_seconds)`

| Parameter | Type | Description |
|---|---|---|
| `max_calls` | `int` | Maximum calls admitted per window (≥ 1) |
| `window_seconds` | `float` | Window length in seconds (finite and > 0) |

| Method / property | Description |
|---|---|
| `acquire() → float` | Admit call or return wait seconds; **not** reserved when blocked |
| `peek() → float` | Same computation but call **not** recorded |
| `reset()` | Clear all recorded call timestamps |
| `current_calls` | Number of calls tracked in the current window |

### `CompositeRateLimiter(*limiters)`

Accepts any mix of `TokenBucket` and `SlidingWindow` instances.

| Method | Description |
|---|---|
| `acquire(tokens=1) → float` | Peek all; if any block return max wait; otherwise acquire all |
| `peek(tokens=1) → float` | Maximum peek wait across all limiters (no side effects) |
| `reset()` | Reset all constituent limiters |

## Interactive dashboard

```bash
rateguard-dashboard
# or
python -m rateguard.gui
```

The dashboard opens a three-tab Tkinter window (Token Bucket, Sliding Window,
Composite) with:

- a configuration form (apply new parameters at any time);
- a real-time fill-level progress bar refreshed every 100 ms;
- an *Acquire* button that simulates an API call and logs the result;
- a colour-coded call history (green = admitted, red = waited).

## Concurrency model

`TokenBucket` and `SlidingWindow` each use a single `threading.Lock` and are
safe to share across threads.  `CompositeRateLimiter` relies on each limiter's
own lock; the peek-before-acquire sequence is not globally atomic, so under
very high concurrency a rare spurious positive wait may occur—always
conservative, never over-admitting.

For `asyncio` use, call `acquire()` from a thread pool executor or use
`loop.run_in_executor` so the (potentially sleeping) caller does not block the
event loop.

## Citing rateguard

If you use rateguard in academic work please cite the JOSS paper (see
`paper.md`) or use the metadata in `CITATION.cff`:

```bibtex
@software{rateguard,
  author  = {Deshmukh, Vaibhav},
  title   = {rateguard: Thread-safe local rate limiting for LLM API clients in Python},
  version = {0.2.0},
  year    = {2026},
  url     = {https://github.com/vdeshmukh203/rateguard},
}
```

## License

MIT – see [LICENSE](LICENSE).
