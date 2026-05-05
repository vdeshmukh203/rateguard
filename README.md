# rateguard

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

**rateguard** provides lightweight, thread-safe rate-limiting primitives for
Python — with no third-party dependencies. It is designed for applications that
call external LLM or REST APIs and need to enforce per-process rate limits
without spinning up a separate broker (Redis, Celery, etc.).

## Statement of need

Managed LLM services (OpenAI, Anthropic, Google Gemini, …) impose strict
per-minute and per-day rate limits. Exceeding them results in `429` errors,
exponential back-off penalties, and degraded user experience. Existing
solutions either require a network-accessible store (Redis-based limiters) or
are tightly coupled to a specific HTTP framework. **rateguard** fills the gap
for single-process applications — scripts, notebooks, and local agents — that
need a drop-in primitive with zero external dependencies.

## Features

- **`TokenBucket`** — refills at a constant rate; supports burst allowances and
  fair queuing across concurrent threads
- **`SlidingWindow`** — enforces a hard call-count limit over a rolling time
  window
- Thread-safe using only `threading.Lock`
- `status()` for real-time introspection, `reset()` for test isolation
- Interactive **Tkinter dashboard** (`rateguard-gui`) for visual exploration
- Pure Python standard library; works on Python 3.9 – 3.12

## Install

```bash
pip install rateguard
```

## Quick start

### Token bucket

```python
import time
from rateguard import TokenBucket

# Allow 10 requests/sec with a burst of up to 20
bucket = TokenBucket(rate=10.0, burst=20)

wait = bucket.acquire()       # consume 1 token
if wait:
    time.sleep(wait)
# ... make the API call ...

# Or acquire multiple tokens at once
wait = bucket.acquire(tokens=5)
if wait:
    time.sleep(wait)
```

The bucket refills continuously; tokens consumed when the bucket is empty are
**reserved immediately** so concurrent callers each get their own fair wait
rather than all sleeping the same duration and then hammering the API together.

### Sliding window

```python
import time
from rateguard import SlidingWindow

# At most 60 calls per 60-second window
window = SlidingWindow(max_calls=60, window_seconds=60.0)

while True:
    wait = window.acquire()
    if not wait:
        break           # slot admitted — proceed
    time.sleep(wait)    # wait for oldest call to expire, then retry

# ... make the API call ...
```

Unlike `TokenBucket`, a blocked `SlidingWindow.acquire()` does **not** reserve
a slot; the caller must retry after sleeping. This avoids quota-locking when
many threads compete on the same window.

### Introspection

```python
bucket = TokenBucket(rate=5.0, burst=10)
bucket.acquire(7)

st = bucket.status()
# {'tokens': 3.0, 'burst': 10, 'rate': 5.0}

window = SlidingWindow(max_calls=3, window_seconds=30.0)
window.acquire()
window.acquire()

st = window.status()
# {'current_calls': 2, 'max_calls': 3, 'window_seconds': 30.0, 'call_ages': [...]}
```

### Reset (useful in tests)

```python
bucket.reset()   # refill to full burst capacity
window.reset()   # clear all recorded calls
```

## Graphical dashboard

rateguard ships with an interactive Tkinter dashboard that lets you visualise
both rate limiters in real time and experiment without writing code.

```bash
rateguard-gui
```

Or run directly:

```bash
python -m rateguard.gui
```

The dashboard provides:

- **Token Bucket tab** — animated bucket fill level, refilling in real time as
  you watch
- **Sliding Window tab** — timeline bar showing active calls sliding out of the
  window and a slot-usage grid
- Configurable parameters, acquire buttons, and a scrollable log

## API reference

### `TokenBucket(rate, burst)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `rate` | `float` | Tokens added per second. Must be > 0. |
| `burst` | `int` | Maximum tokens the bucket can hold. Must be ≥ 1. |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire(tokens=1)` | `float` | Seconds to wait (0 = immediate). Tokens are reserved even if wait > 0. |
| `status()` | `dict` | `{"tokens", "burst", "rate"}` snapshot without modifying state. |
| `reset()` | `None` | Refill to burst capacity. |

### `SlidingWindow(max_calls, window_seconds)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_calls` | `int` | Maximum calls allowed within the window. Must be ≥ 1. |
| `window_seconds` | `float` | Window length in seconds. Must be > 0. |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire()` | `float` | Seconds to wait (0 = admitted). No slot reserved when blocked. |
| `status()` | `dict` | `{"current_calls", "max_calls", "window_seconds", "call_ages"}` snapshot. |
| `reset()` | `None` | Clear all recorded calls. |

## Development

```bash
git clone https://github.com/vdeshmukh203/rateguard.git
cd rateguard
pip install -e ".[dev]"
pytest
```

Tests run on Python 3.9, 3.10, 3.11, and 3.12 via GitHub Actions CI.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on reporting bugs,
requesting features, and submitting pull requests.

## License

MIT — see [LICENSE](LICENSE).
