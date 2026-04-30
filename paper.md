---
title: 'rateguard: Thread-safe rate limiting primitives for Python LLM API clients'
tags:
  - Python
  - rate limiting
  - token bucket
  - sliding window
  - LLM
  - API throttling
authors:
  - name: Vaibhav Deshmukh
    orcid: 0000-0000-0000-0000
    affiliation: 1
affiliations:
  - name: Independent Researcher
    index: 1
date: 30 April 2026
bibliography: paper.bib
---

# Summary

Large Language Model (LLM) service providers—including OpenAI, Anthropic, and
Google—impose strict rate limits on their public APIs, expressed as requests
per minute (RPM), tokens per minute (TPM), and concurrent request limits
[@openai_rate_limits; @anthropic_rate_limits].  Applications that call these
APIs in concurrent Python code routinely exceed these limits, receive HTTP 429
responses, and must implement exponential back-off or request queuing logic.

`rateguard` provides two thread-safe rate limiting primitives—a **token bucket**
and a **sliding window**—implemented entirely in the Python standard library
with no external dependencies.  Both primitives are designed to be placed
directly in front of API calls to enforce locally a rate budget that mirrors
the server-side limit, eliminating avoidable 429 errors before they occur.

# Statement of Need

Existing Python rate-limiting libraries such as `ratelimit`
[@ratelimit_pypi], `limits` [@limits_pypi], and `throttler`
[@throttler_pypi] provide comparable functionality, but each carries
external dependencies (Redis, `asyncio`-only event loops, or third-party
storage backends) that add installation complexity and reduce portability.
`rateguard` intentionally restricts its implementation to the Python
standard library so that it can be vendored into any project or bundled
with a `zip_import`-based deployment without modification.

A secondary motivation is pedagogical: the two algorithm families—token
bucket and sliding window—behave differently under burst traffic, and a
library that exposes both with consistent APIs allows practitioners and
students to compare their trade-offs empirically.  The optional
`rateguard-gui` dashboard (`python -m rateguard`) provides a live
visualisation of both algorithms under interactive simulation, supporting
this educational use case.

# Design and Implementation

## TokenBucket

The token bucket [@tanenbaum_computer_networks, ch. 6] maintains an internal
float counter `_tokens` that is incremented at `rate` tokens per second up to
a maximum of `burst`.  `acquire(n)` is called before each API request:

- If `_tokens >= n`, the tokens are consumed and the call returns `0.0`
  (proceed immediately).
- Otherwise the deficit `n - _tokens` is computed, a wait time
  `deficit / rate` is returned, and `_tokens` is decremented by `n`
  regardless.  This *reservation* model ensures that concurrent threads
  each receive an equitable, non-overlapping slot in the future token
  stream rather than all computing the same earliest-available time.

The internal lock is held only during the arithmetic on `_tokens`, keeping
contention to a minimum.

## SlidingWindow

The sliding window [@leaky_bucket_1986] records the monotonic timestamp of
each admitted call in a `collections.deque`.  On each `acquire()`:

1. All timestamps older than `window_seconds` are evicted from the left end
   of the deque.
2. If `len(deque) < max_calls`, the current timestamp is appended and `0.0`
   is returned.
3. Otherwise the time until the oldest remaining call exits the window is
   returned, and *no* slot is reserved; the caller must retry after sleeping.

This no-reservation strategy avoids the unbounded token-debt problem that
arises with the token bucket when many threads are blocked simultaneously,
at the cost of requiring callers to loop on `acquire()`.

## Thread Safety

Both primitives use `threading.Lock` to serialise access to shared mutable
state.  `time.monotonic()` is used throughout so that clock adjustments do
not corrupt wait calculations.

## Inspection API

Both classes expose read-only properties (`tokens_available`,
`calls_in_window`, `slots_remaining`) and a `reset()` method to support
testing and monitoring.  A `__repr__` on each class includes the current
state for convenient debugging in interactive sessions and log output.

# Interactive Dashboard

The package includes a Tkinter-based GUI (`src/rateguard/gui.py`) that can be
launched with:

```
python -m rateguard
# or, after installation:
rateguard-gui
```

The dashboard provides two tabs, one per algorithm.  Each tab contains:

- **Configuration panel** — editable fields for algorithm parameters with
  a *Create / Reset* button.
- **Live level bar** — a colour-coded bar that redraws at 10 Hz to show
  the current token level (green above 30 %, red below) or window occupancy
  (green below 80 %, red at or above).
- **Simulate panel** — an *Acquire* button that calls the limiter and
  displays the returned wait time.
- **Activity log** — a scrollable record of all create and acquire events.

The dashboard is useful for teaching and for manually validating that a
given configuration will sustain a target throughput.

# Acknowledgements

The authors thank the open-source community for foundational work on rate
limiting algorithms documented in computer networking textbooks and IETF
standards.

# References
