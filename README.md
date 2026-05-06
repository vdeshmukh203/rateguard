# rateguard

[![CI](https://github.com/vdeshmukh203/rateguard/actions/workflows/ci.yml/badge.svg)](https://github.com/vdeshmukh203/rateguard/actions)
[![PyPI version](https://img.shields.io/pypi/v/rateguard)](https://pypi.org/project/rateguard/)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Local rate limiter for LLM API calls.  Provides two thread-safe primitives —
**token-bucket** and **sliding-window** — with zero dependencies (pure standard
library).  A Textual terminal dashboard is available as an optional extra.

## Statement of need

LLM APIs (OpenAI, Anthropic, Google) impose both per-second and per-minute rate
limits.  Client-side enforcement avoids wasted round-trips, unnecessary HTTP 429
responses, and the back-off/retry boilerplate that every application otherwise
has to reimplement.  `rateguard` packages the two most widely used algorithms
in a single, minimal module that fits any Python project without adding
transitive dependencies.

## Install

```bash
pip install rateguard            # core library only
pip install rateguard[gui]       # + Textual terminal dashboard
```

## Quick start

```python
from rateguard import TokenBucket, SlidingWindow
import time

# Token bucket — 10 tokens/s, burst up to 20.
bucket = TokenBucket(rate=10.0, burst=20)
wait = bucket.acquire()          # consume 1 token
if wait:
    time.sleep(wait)
# proceed with the API call

# Sliding window — at most 60 calls per 60 s.
window = SlidingWindow(max_calls=60, window_seconds=60.0)
while True:
    wait = window.acquire()
    if wait == 0.0:
        break
    time.sleep(wait)
# proceed with the API call
```

## API reference

### `TokenBucket(rate, burst)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `rate`    | float | Tokens added per second (must be > 0) |
| `burst`   | int   | Maximum tokens the bucket can hold (must be ≥ 1) |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire(tokens=1)` | `float` | Consume *tokens*; return seconds to wait (0 = immediate). Tokens are reserved even when waiting. |
| `stats()` | `dict` | Non-mutating snapshot: `tokens`, `rate`, `burst`, `fill_ratio`. |
| `reset()` | `None` | Refill bucket to full capacity. |

### `SlidingWindow(max_calls, window_seconds)`

| Parameter | Type | Description |
|-----------|------|-------------|
| `max_calls`      | int   | Maximum calls allowed in the window (must be ≥ 1) |
| `window_seconds` | float | Rolling window length in seconds (must be > 0) |

| Method | Returns | Description |
|--------|---------|-------------|
| `acquire()` | `float` | Record a call; return seconds to wait (0 = admitted). No slot is reserved when blocked — retry after sleeping. |
| `stats()` | `dict` | Non-mutating snapshot: `calls_in_window`, `max_calls`, `window_seconds`, `available`. |
| `reset()` | `None` | Clear all recorded calls from the window. |

Both classes are thread-safe.

## Terminal dashboard (GUI)

```bash
rateguard-gui
```

The Textual dashboard opens two side-by-side panels — one for each algorithm.
Each panel shows a live fill gauge, config inputs (editable in real time),
acquire/reset buttons, an auto-fire toggle, and a timestamped event log.

| Key | Action |
|-----|--------|
| `q` | Quit |
| `Ctrl+L` | Clear event log |

## Choosing an algorithm

| Scenario | Recommended primitive |
|----------|-----------------------|
| Smoothing a continuous request stream | `TokenBucket` |
| Enforcing a hard call quota per minute | `SlidingWindow` |
| Burst allowed, then steady trickle | `TokenBucket` with a generous `burst` |

## Concurrency notes

`TokenBucket.acquire` **reserves** tokens immediately; callers receive
non-overlapping wait times and may sleep in parallel.

`SlidingWindow.acquire` does **not** reserve a slot when blocked; each caller
must retry after sleeping.  This prevents phantom slot exhaustion under heavy
concurrency.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).

## Citation

If you use `rateguard` in academic work, please cite the associated JOSS paper
(see `paper.md`) or reference the GitHub repository.
