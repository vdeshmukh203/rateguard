---
title: 'rateguard: A Thread-Safe Rate Limiter for LLM API Calls in Python'
tags:
  - Python
  - rate limiting
  - LLM
  - API throttling
  - token bucket
  - sliding window
authors:
  - name: Vaibhav A. Deshmukh
    orcid: 0000-0000-0000-0000
    affiliation: 1
affiliations:
  - name: Independent Researcher
    index: 1
date: 09 May 2026
bibliography: paper.bib
---

# Summary

`rateguard` is a pure-Python, zero-dependency library that provides
thread-safe rate-limiting primitives for applications that call large
language model (LLM) APIs such as OpenAI, Anthropic, Google Gemini, and
similar services.  It exposes two complementary algorithms—a
*token-bucket* limiter and a *sliding-window* limiter—through a minimal,
consistent interface: every `acquire()` call returns the number of
seconds the caller should sleep before proceeding, and zero means
*proceed immediately*.

# Statement of Need

Commercial LLM APIs enforce per-minute and per-second rate limits at
both the request and token levels.  Violating these limits results in
HTTP 429 responses that break automated pipelines, add latency from
exponential back-off, and—depending on the provider—may incur overage
fees.  While retry libraries such as `tenacity` [@tenacity] and
`backoff` [@backoff] handle retries *after* a limit is hit,
`rateguard` prevents the violation from occurring in the first place by
tracking and enforcing limits *locally* before any network call is made.

Existing alternatives either require a running server (Redis-based
limiters), impose framework-specific abstractions (FastAPI middleware,
Django throttling), or lack thread safety for concurrent Python
applications.  `rateguard` fills the gap: a lightweight, in-process,
framework-agnostic library that works with `asyncio`, `threading`, and
`multiprocessing`-spawned threads alike.

# Algorithm Overview

## Token Bucket

The token-bucket algorithm [@tanenbaum2011] models a bucket that fills
with tokens at a constant *rate* (tokens per second) up to a *burst*
capacity.  Each API call consumes one or more tokens.  When the bucket
is sufficiently full the call proceeds immediately; when it is not,
`rateguard` computes the exact wait and *reserves* the tokens
optimistically so that concurrent threads receive non-overlapping,
fair-share wait windows with no thundering-herd behaviour.

The token count after a time interval $\Delta t$ is

$$
T_{t+\Delta t} = \min\!\bigl(B,\; T_t + r\,\Delta t\bigr),
$$

where $r$ is the fill rate (tokens s⁻¹) and $B$ is the burst capacity.

## Sliding Window

The sliding-window algorithm [@leaky2018] maintains a timestamp deque
of all calls admitted in the past $W$ seconds.  When a new call arrives,
expired timestamps are evicted and, if fewer than $N_{\max}$ calls
remain, the call is admitted and its timestamp is appended.  Otherwise
the caller receives the exact wait until the oldest timestamp leaves the
window.  Unlike the token-bucket, no reservation is made when the limit
is hit, so the caller retries after sleeping.

# Implementation

`rateguard` targets Python ≥ 3.9 and is implemented entirely with the
standard library (`threading`, `collections.deque`, `time.monotonic`).
All shared state is protected by a `threading.Lock`, making each
`acquire()` call atomic with respect to concurrent threads.
`time.monotonic()` is used throughout to avoid errors from system-clock
adjustments.

An optional Tkinter dashboard (`rateguard-dashboard`) provides a live
visualisation of both limiters simultaneously, allowing users to explore
algorithm behaviour interactively.

# Usage

```python
from rateguard import TokenBucket, SlidingWindow
import time

# Allow 10 tokens/s with a burst of up to 20
bucket = TokenBucket(rate=10.0, burst=20)
wait = bucket.acquire(1)          # consume 1 token
if wait > 0:
    time.sleep(wait)
# safe to call the LLM API here

# Allow at most 60 requests per minute
window = SlidingWindow(max_calls=60, window_seconds=60.0)
while True:
    wait = window.acquire()
    if wait == 0:
        break
    time.sleep(wait)
# safe to call the LLM API here
```

# Testing

The library ships with a pytest suite that covers both happy-path and
error-path scenarios including thread-safety regression tests that
exercise both primitives concurrently.  Continuous integration runs the
suite against Python 3.9, 3.10, 3.11, and 3.12 via GitHub Actions with
coverage reporting.

# References
