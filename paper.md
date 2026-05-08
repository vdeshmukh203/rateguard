---
title: 'rateguard: A Thread-Safe Local Rate Limiter for LLM API Calls'
tags:
  - Python
  - rate limiting
  - LLM
  - API
  - token bucket
  - sliding window
  - concurrency
authors:
  - name: Vaibhav Deshmukh
    affiliation: 1
affiliations:
  - name: Independent Researcher
    index: 1
date: 08 May 2026
bibliography: paper.bib
---

# Summary

`rateguard` is a pure-Python library that provides two thread-safe
rate-limiting primitives — a *token bucket* and a *sliding-window counter* —
for controlling the pace of calls to large-language-model (LLM) API providers.
Both primitives expose a single `acquire()` method that returns `0` when the
caller may proceed immediately, and a positive float (seconds to sleep) when
the caller must wait.  The library has no runtime dependencies beyond the
Python standard library and therefore carries zero installation overhead.

# Statement of Need

LLM API providers (e.g., OpenAI, Anthropic, Google, Cohere) enforce per-user
rate limits in two dimensions: *tokens per minute* (TPM) and *requests per
minute* (RPM).  Exceeding these limits results in HTTP 429 responses that
interrupt generation pipelines, waste retry budget, and can trigger
provider-level suspensions.  Retry logic with exponential back-off handles
isolated failures but does not prevent the upstream burst that causes them in
the first place [@openai-rate-limits; @anthropic-rate-limits].

Existing Python rate-limiting libraries are designed for web-framework
middleware rather than local script or agent use.  `ratelimiter`
[@ratelimiter] provides a decorator-based token bucket but lacks sliding-window
support and a monitoring API.  `limits` [@limits] offers multiple algorithms
and supports Redis or Memcached as backends, introducing runtime dependencies
that are unnecessary for single-process workloads.  `slowapi` [@slowapi] wraps
`limits` for ASGI/WSGI applications and is not intended for direct use in
scripts or multi-threaded agents.

`rateguard` fills this gap by offering:

1. **TokenBucket** — a token-bucket algorithm [@tanenbaum2011] with a
   configurable replenishment rate and burst capacity.  This maps directly onto
   the TPM limits published by every major LLM provider.
2. **SlidingWindow** — a sliding-window counter that enforces a maximum number
   of calls within a rolling interval.  This maps directly onto RPM limits.

Both primitives are safe for use from multiple threads without additional
user-side locking, and both expose a `status()` monitoring method and a
`reset()` convenience method.  An optional Tkinter-based graphical interface
ships with the package for interactive exploration.

# Implementation

## TokenBucket

`TokenBucket` tracks the current fill level as a floating-point value, lazily
refilling on each `acquire()` call by multiplying elapsed wall-clock time
(measured with `time.monotonic()` to avoid clock-step artefacts) by the
configured rate:

```python
self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
```

When fewer tokens are available than requested, the method computes the
deficit, *reserves* the tokens immediately, and returns the wait duration:

```python
deficit = tokens - self._tokens
wait = deficit / self.rate
self._tokens -= tokens   # reservation
return wait
```

Reservation-on-block ensures that concurrent callers each see a fair,
non-overlapping share of the wait time, preventing the thundering-herd
re-synchronisation seen in non-reserving implementations [@nichols1999].

## SlidingWindow

`SlidingWindow` maintains a `collections.deque` of admitted-call timestamps.
On each `acquire()` call it evicts timestamps that have aged beyond the window
before deciding whether to admit the new call:

```python
cutoff = now - self.window_seconds
while self._calls and self._calls[0] <= cutoff:
    self._calls.popleft()
```

When the window is full, the method returns the time until the oldest admitted
call exits the window, allowing the caller to sleep the minimum necessary
duration before retrying.  No slot is reserved when blocked, so the caller
must call `acquire()` again after sleeping.

## Thread Safety

Both classes hold a single `threading.Lock` that is acquired for the duration
of each `acquire()` and `status()` call.  Because the critical sections are
O(1) for `TokenBucket` and amortised O(k) for `SlidingWindow` (where *k* is
the number of evictions), lock contention is minimal even under high
concurrency.

## Graphical Interface

The optional `rateguard-gui` entry point launches a Tkinter application with
two tabs — one per primitive.  Each tab provides parameter controls, a
live-updating fill gauge coloured by traffic-light logic (green → yellow → red
as the limiter approaches capacity), an acquire button, an auto-fire mode, and
a timestamped log of all acquire results.  The interface updates at 80 ms
intervals using Tkinter's `after` scheduler, keeping the display responsive
without blocking the main event loop.

# Comparison with Related Software

| Library | Algorithm | Thread-safe | External deps | Monitoring API |
|---------|-----------|:-----------:|:-------------:|:--------------:|
| `ratelimiter` [@ratelimiter] | Token bucket | Yes | None | No |
| `limits` [@limits] | Multiple | Yes | Redis/Memcached | No |
| `slowapi` [@slowapi] | Via `limits` | Yes | Redis/Starlette | No |
| **rateguard** | Token bucket + Sliding window | Yes | None | Yes |

`rateguard` is unique in providing both algorithms with zero external
dependencies and a built-in monitoring and visualisation layer.

# Testing

The test suite (`tests/test_rateguard.py`) contains 30 unit tests exercising
correct acquisition behaviour, parameter validation, refill-over-time timing,
the `status()` and `reset()` APIs, context-manager usage, and thread-safety
for both primitives.  Tests run under Python 3.9–3.12 via GitHub Actions CI.

# Acknowledgements

The author thanks the open-source Python community for the comprehensive
standard library that makes a zero-dependency rate limiter possible.

# References
