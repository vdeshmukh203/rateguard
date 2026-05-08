# rateguard

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions)
[![PyPI](https://img.shields.io/pypi/v/rateguard)](https://pypi.org/project/rateguard/)
[![Python](https://img.shields.io/pypi/pyversions/rateguard)](https://pypi.org/project/rateguard/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Local rate limiter for LLM API calls.  Provides a **token bucket** and a
**sliding-window** primitive, both thread-safe, both pure standard library.

## Install

```bash
pip install rateguard
```

## Quickstart

```python
import time
from rateguard import TokenBucket, SlidingWindow

# Token bucket: 10 requests/sec, burst up to 20.
bucket = TokenBucket(rate=10.0, burst=20)
wait = bucket.acquire(1)
if wait > 0:
    time.sleep(wait)
# proceed with the API call

# Sliding window: at most 60 calls per minute.
window = SlidingWindow(max_calls=60, window_seconds=60.0)
wait = window.acquire()
if wait > 0:
    time.sleep(wait)
# proceed with the API call
```

Both primitives also work as context managers:

```python
with TokenBucket(rate=10.0, burst=20) as bucket:
    wait = bucket.acquire(1)
    ...
```

## API reference

### `TokenBucket(rate, burst)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `rate` | `float` | Tokens added per second (must be > 0) |
| `burst` | `int` | Maximum tokens the bucket can hold (must be ≥ 1) |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire(tokens=1)` | `float` | Seconds to sleep; `0.0` means proceed immediately |
| `status()` | `TokenBucketStatus` | Immutable snapshot of `tokens`, `rate`, `burst` |
| `reset()` | `None` | Refill bucket to full burst capacity |

### `SlidingWindow(max_calls, window_seconds)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_calls` | `int` | Maximum calls allowed per window (must be ≥ 1) |
| `window_seconds` | `float` | Window length in seconds (must be > 0) |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire()` | `float` | Seconds to sleep; `0.0` means admitted immediately |
| `status()` | `SlidingWindowStatus` | Immutable snapshot of `calls_in_window`, `max_calls`, `window_seconds` |
| `reset()` | `None` | Clear all recorded calls from the window |

## Graphical interface

```bash
rateguard-gui
```

Launches an interactive Tkinter explorer with live fill gauges and acquire logs
for both the token bucket and the sliding window.

## How it works

**TokenBucket** refills at a constant `rate` up to `burst` capacity.  When a
caller requests more tokens than are currently available, the deficit is
computed, the tokens are *reserved* (so concurrent callers each see a fair wait
time), and the required sleep duration is returned.

**SlidingWindow** tracks the timestamps of admitted calls in a deque.  Old
timestamps are lazily evicted on each `acquire()` call.  When the window is
full, the time until the oldest call exits is returned; no slot is reserved, so
the caller must retry after sleeping.

## Development

```bash
pip install -e ".[dev]"
python -m pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full guidelines.

## Citation

If you use rateguard in your research, please cite it using the metadata in
[CITATION.cff](CITATION.cff).

## License

MIT — see [LICENSE](LICENSE).
