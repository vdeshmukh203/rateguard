---
title: 'rateguard: Thread-safe local rate limiting for LLM API clients in Python'
tags:
  - Python
  - rate limiting
  - large language models
  - API
  - concurrency
  - token bucket
  - sliding window
authors:
  - name: Vaibhav Deshmukh
    affiliation: 1
affiliations:
  - name: Independent Researcher
    index: 1
date: 07 May 2026
bibliography: paper.bib
---

# Summary

`rateguard` is a Python library that provides two production-ready, thread-safe
rate-limiting algorithms designed for applications that call large language
model (LLM) APIs. The **token-bucket** algorithm [@rfc2697] supports burst
traffic while enforcing a configurable steady-state throughput ceiling. The
**sliding-window** counter enforces a strict maximum call count within a
rolling time interval. A **composite** limiter combines both algorithms,
mirroring the dual rate-limit structure (requests-per-minute *and*
tokens-per-minute) enforced by most commercial LLM providers. All three
primitives are implemented using only the Python standard library (zero
third-party dependencies), are thread-safe, and are compatible with both
synchronous and `asyncio`-based code bases. An optional interactive Tkinter
dashboard (`rateguard-dashboard`) provides real-time visualisation of limiter
state for researchers and educators.

# Statement of Need

Commercial LLM API providers (OpenAI, Anthropic, Google, Mistral, and others)
enforce rate limits along at least two independent axes: *requests per minute*
(RPM) and *tokens per minute* (TPM). Exceeding either threshold results in
HTTP 429 responses, wasted network round-trips, and exponential back-off
delays that degrade application throughput. Client-side *pre-admission
control*—computing the required wait time before issuing the request—eliminates
429 errors without sacrificing throughput and without requiring application-
level retry logic wired to HTTP error codes.

Existing Python rate-limiting libraries have shortcomings in this context:

- `ratelimit` [@pypi-ratelimit] and `slowapi` [@pypi-slowapi] are built around
  function decorators and WSGI/ASGI web frameworks respectively, adding
  unnecessary coupling and overhead for LLM client code.
- `aiolimiter` [@pypi-aiolimiter] requires an `asyncio` event loop, excluding
  the large body of synchronous LLM client code (OpenAI SDK, LangChain in
  synchronous mode, etc.).
- None of the above libraries expose a *wait-time* API: they either block the
  calling thread directly or raise an exception, making them ill-suited to
  batched or multi-threaded LLM workloads where the caller may want to queue,
  log, or reorder requests before sleeping.

`rateguard` fills this gap with a minimal, principled design:

- **No external dependencies.** The library ships as two modules (the core and
  the optional GUI) and installs cleanly into any Python ≥ 3.9 environment.
- **Wait-time semantics.** Every `acquire()` call returns a `float` (seconds
  to sleep). Callers retain full control over sleeping, queuing, or logging,
  enabling integration with `asyncio`, `concurrent.futures`, and plain
  `threading` worker pools without modification.
- **Thread safety.** Each limiter uses a single `threading.Lock`, making the
  primitives safe for concurrent LLM worker pools without external
  synchronisation.
- **Dual-axis composability.** `CompositeRateLimiter` lets callers enforce both
  RPM and TPM limits through a single `acquire()` call, returning the most
  restrictive wait across all constituent limiters.

The primary audience is developers building production LLM API clients, batch
inference pipelines, and research tooling. The interactive dashboard also makes
`rateguard` useful as a teaching tool for courses on distributed systems and
API design.

# Implementation

## TokenBucket

The token-bucket algorithm [@rfc2697] maintains a counter of available tokens.
Tokens accumulate at a constant `rate` (tokens per second) up to a configurable
maximum `burst` capacity. An `acquire(tokens)` call atomically deducts
`tokens` from the counter and returns `0.0` when the tokens were immediately
present, or the number of seconds until they will have been replenished. Tokens
are reserved in both cases (a deferred reservation model), so concurrent callers
each receive their own fair share of the future capacity without additional
queuing primitives.

A `peek(tokens)` method returns the wait estimate without modifying state,
enabling pre-checks and the no-side-effect inspection needed by
`CompositeRateLimiter`. A `reset()` method refills the bucket to `burst`
capacity. The `tokens` property exposes the current fill level (updated
lazily on each read) for monitoring purposes.

## SlidingWindow

The sliding-window counter [@kleppmann2017] records the monotonic timestamp of
each admitted call in a `collections.deque`. On each `acquire()` call,
timestamps older than `window_seconds` are evicted from the left end of the
deque (O(k) where k is the number of evicted entries, amortised O(1) per
call). When the deque length is below `max_calls`, the new timestamp is
appended and `0.0` is returned. When the limit is reached the call is *not*
recorded—the caller receives the number of seconds until the oldest timestamp
will leave the window and must retry after sleeping.

Companion `peek()`, `reset()`, and `current_calls` members mirror the
`TokenBucket` interface for consistency.

## CompositeRateLimiter

`CompositeRateLimiter` accepts any mix of `TokenBucket` and `SlidingWindow`
instances. Its `acquire()` method first calls `peek()` on every constituent
limiter; if any returns a positive wait, no state is modified and the maximum
wait is returned. Only when all peeks report `0.0` does `acquire()` dispatch
individual `acquire()` calls on each limiter. This peek-before-acquire
protocol prevents consuming `SlidingWindow` slots when a `TokenBucket` would
block, and vice versa. Because each limiter uses its own lock, the sequence is
not globally atomic; rare races under very high concurrency yield a small
spurious positive wait (conservative behaviour) rather than an admission error.

## Interactive Dashboard

The optional Tkinter dashboard (`rateguard-dashboard`) provides three tabbed
panels—one for each limiter type—each containing:

- a configuration form that creates a new limiter on *Apply*;
- a real-time fill-level progress bar and numeric readout (refreshed every
  100 ms via `Widget.after()`);
- an *Acquire* button that simulates an API call and displays the resulting
  wait time;
- a colour-coded scrollable call history (green for admitted, red for waited).

The dashboard targets educators, researchers, and developers who wish to
observe rate-limiter behaviour interactively without writing code.

# Acknowledgements

The author thanks the open-source community for foundational discussions on
client-side rate limiting strategies for LLM APIs.

# References
