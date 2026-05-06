---
title: 'rateguard: a lightweight local rate limiter for LLM API calls'
tags:
  - Python
  - rate limiting
  - token bucket
  - sliding window
  - LLM
  - API
authors:
  - name: Vaibhav Deshmukh
    orcid: 0000-0000-0000-0000
    affiliation: 1
affiliations:
  - name: Independent researcher
    index: 1
date: 2026-05-06
bibliography: paper.bib
---

# Summary

`rateguard` is a pure-Python library that provides two thread-safe rate-limiting
primitives: a **token-bucket** and a **sliding-window** counter.  Each
primitive exposes a single `acquire()` method that returns the number of
seconds the caller should wait before proceeding; a return value of zero means
the call is admitted immediately.  The library ships with no runtime
dependencies (standard library only) and includes an optional interactive
terminal dashboard built with Textual.

# Statement of need

Large language model (LLM) inference APIs — such as those offered by Anthropic,
OpenAI, and Google — enforce both per-second burst limits and per-minute quota
limits.  Violating these limits results in HTTP 429 responses, wasted latency,
and, in production systems, cascading failures.  Client-side rate limiting
reduces 429s before they occur and eliminates the per-project boilerplate of
implementing exponential back-off and retry logic.

Existing general-purpose rate-limiting libraries for Python
[@ratelimit; @limits] are either tied to external storage backends (Redis,
Memcached) or designed for web-framework middleware rather than direct library
use.  `rateguard` fills the gap with a self-contained, importable primitive
that requires no infrastructure and introduces no transitive dependencies into
user projects.

# Design

## Token bucket

The token-bucket algorithm [@turner1986new] maintains a virtual bucket that
accumulates tokens at a constant rate up to a configurable burst capacity.
Each call consumes one or more tokens.  When the bucket is empty, `acquire`
returns the time until enough tokens will have refilled, and **reserves** those
tokens atomically.  Concurrent callers therefore receive non-overlapping wait
windows and may sleep in parallel, which makes the algorithm fair and easy to
reason about in multi-threaded code.

## Sliding window

The sliding-window counter tracks the timestamps of recent calls in a
`collections.deque` and evicts entries older than the window length on every
call.  When the count of in-window calls reaches the maximum, `acquire` returns
the time until the oldest call expires.  Unlike the token bucket, **no slot is
reserved** when blocked: the caller must retry after sleeping.  This prevents
phantom slot exhaustion when many threads are blocked simultaneously.

## Thread safety

Both classes protect all shared state with `threading.Lock` and use
`time.monotonic()` to remain unaffected by system-clock adjustments.

# Example

```python
from rateguard import TokenBucket, SlidingWindow
import time

# Smooth a stream to 10 req/s with a burst of 20.
bucket = TokenBucket(rate=10.0, burst=20)
for request in batch:
    wait = bucket.acquire()
    if wait:
        time.sleep(wait)
    send(request)

# Hard quota: no more than 60 calls per minute.
window = SlidingWindow(max_calls=60, window_seconds=60.0)
while True:
    wait = window.acquire()
    if wait == 0.0:
        break
    time.sleep(wait)
send(request)
```

# Terminal dashboard

`rateguard` ships with an optional Textual [@textual] terminal user interface
that can be launched with `rateguard-gui`.  The dashboard shows both limiters
side by side with live fill gauges, editable configuration, acquire/reset
controls, an auto-fire toggle, and a timestamped event log.  This makes it easy
to observe algorithm behaviour interactively without writing any code.

# Testing

The test suite (pytest) covers:

- correct admission and blocking under both algorithms;
- token refill timing for `TokenBucket`;
- window eviction timing for `SlidingWindow`;
- input validation (negative rates, zero burst, etc.);
- the `stats()` and `reset()` helpers;
- thread-safety stress tests with five concurrent threads.

Continuous integration runs the suite on Python 3.9 through 3.12 via GitHub
Actions.

# References
