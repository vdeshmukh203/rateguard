# rateguard

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/rateguard)](https://pypi.org/project/rateguard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Local, thread-safe rate limiter for LLM API calls.  Provides two
complementary primitives—a **token bucket** and a **sliding window**—both
implemented in pure Python with no external dependencies.

## Installation

```bash
pip install rateguard
```

## Quick start

```python
import time
from rateguard import TokenBucket, SlidingWindow

# --- Token bucket: 10 requests/sec, burst up to 20 ---
bucket = TokenBucket(rate=10.0, burst=20)

wait = bucket.acquire(1)
if wait:
    time.sleep(wait)
# proceed with the API call

# --- Sliding window: at most 60 calls per minute ---
window = SlidingWindow(max_calls=60, window_seconds=60.0)

while True:
    wait = window.acquire()
    if wait == 0.0:
        break          # slot admitted
    time.sleep(wait)   # window full; retry after oldest call expires
```

## Algorithms

### `TokenBucket(rate, burst)`

The bucket starts full (at `burst` tokens) and refills at `rate` tokens per
second up to the burst capacity.  Each `acquire(tokens)` call:

- returns `0.0` immediately if sufficient tokens are available, or
- returns the **wait time in seconds** after pre-reserving the tokens.

Pre-reservation ensures that concurrent callers queue fairly rather than
racing on the same wake-up moment.

| Parameter | Type    | Description                           |
|-----------|---------|---------------------------------------|
| `rate`    | `float` | Tokens added per second (`> 0`)       |
| `burst`   | `int`   | Maximum bucket capacity (`>= 1`)      |

```python
bucket = TokenBucket(rate=5.0, burst=10)
wait = bucket.acquire(2)   # consume 2 tokens
```

**Observable properties**

| Property           | Description                            |
|--------------------|----------------------------------------|
| `tokens_available` | Estimated tokens in the bucket (float) |

### `SlidingWindow(max_calls, window_seconds)`

Timestamps of recent calls are kept in a deque.  Expired entries are evicted
before each decision.  `acquire()`:

- returns `0.0` and records the call if `calls_in_window < max_calls`, or
- returns the **seconds until the oldest call exits the window**.

Unlike `TokenBucket`, no slot is reserved on a rejected call—the caller must
retry after sleeping.

| Parameter        | Type    | Description                                  |
|------------------|---------|----------------------------------------------|
| `max_calls`      | `int`   | Max calls allowed per window (`>= 1`)        |
| `window_seconds` | `float` | Window length in seconds (`> 0`)             |

```python
window = SlidingWindow(max_calls=10, window_seconds=1.0)
wait = window.acquire()
```

**Observable properties**

| Property          | Description                                     |
|-------------------|-------------------------------------------------|
| `calls_in_window` | Calls currently recorded in the active window   |

## Interactive GUI

An optional dashboard lets you explore and compare both algorithms in real
time.

```bash
# after installing the package:
python -m rateguard
# or
rateguard-gui
```

The window provides:

- **Configuration tab** — sliders for all parameters, one-click
  apply/reset, manual call buttons (1 / 5 / 20 / 50 at a time), and an
  auto-fire stream at a configurable calls-per-second rate.
- **Monitor tab** — colour-coded state gauge, rolling call timeline (filled
  dot = admitted, hollow = throttled), session statistics, and a
  timestamped activity log.

## Thread safety

Both primitives use `threading.Lock` internally.  They are safe to share
across threads without external synchronization.

## Development

```bash
git clone https://github.com/vdeshmukh203/rateguard.git
cd rateguard
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
