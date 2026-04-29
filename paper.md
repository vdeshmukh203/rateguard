---
title: 'rateguard: A Lightweight, Thread-Safe Rate Limiter for LLM API Calls in Python'
tags:
  - Python
  - rate limiting
  - LLM
  - API
  - token bucket
  - sliding window
authors:
  - name: Vaibhav Deshmukh
    orcid: 0000-0000-0000-0000
    affiliation: 1
affiliations:
  - name: Independent Researcher
    index: 1
date: 29 April 2026
bibliography: paper.bib
---

# Summary

`rateguard` is a pure-Python library that provides two classical rate-limiting
primitives—a token-bucket algorithm and a sliding-window algorithm—designed
for controlling the rate of calls to large language model (LLM) APIs such as
OpenAI, Anthropic, and others.  Both primitives are thread-safe and rely
exclusively on the Python standard library, requiring no external dependencies.
Each `acquire` call returns the number of seconds the caller should sleep
before proceeding, allowing the primitives to be used inline with any
synchronous or asynchronous workflow.  An optional interactive GUI built on
`tkinter` lets users explore and compare the two algorithms in real time.

# Statement of Need

LLM API providers enforce per-minute and per-second rate limits that, when
exceeded, result in costly retries, degraded throughput, and elevated latency.
Applications that issue concurrent API calls—multi-agent pipelines, batch
annotation tools, retrieval-augmented generation systems—are particularly
susceptible.  Existing general-purpose rate-limiting libraries (e.g.,
`ratelimit` [@ratelimit], `slowapi` [@slowapi]) either rely on decorators that
assume single-entry-point request handling, embed web-framework assumptions,
or introduce non-trivial dependency chains.

`rateguard` fills the gap by offering:

1. **Zero external dependencies** — pure standard library (`threading`,
   `time`, `collections.deque`), making it trivially embeddable without
   virtual-environment conflicts.
2. **Two complementary algorithms** — `TokenBucket` for smooth, burst-tolerant
   rate control, and `SlidingWindow` for hard per-window call caps.
3. **Thread safety** — both primitives use `threading.Lock` to support
   concurrent workers without data races.
4. **Sleep-compatible return values** — `acquire` returns a `float` (seconds),
   which can be passed directly to `time.sleep` or an async equivalent.
5. **Interactive visualization** — a bundled `tkinter` dashboard lets
   practitioners understand the behavioral differences between the two
   algorithms before integrating them into production code.

# Algorithm Design

## Token Bucket

The token-bucket algorithm [@tanenbaum2003] maintains a counter of available
tokens that refills at a constant rate up to a configurable burst capacity.
Each API call consumes one or more tokens.  If the bucket has enough tokens the
call is admitted immediately; otherwise the deficit determines the required
wait.  `rateguard` pre-reserves the requested tokens even when a wait is
returned, so that concurrent threads each receive a fair, non-overlapping
share of the wait rather than all waking and racing at the same instant.

$$
\text{wait} = \frac{\text{deficit}}{\text{rate}}, \quad
\text{deficit} = \max(0,\, \text{tokens\_requested} - \text{tokens\_available})
$$

## Sliding Window

The sliding-window algorithm [@kleppmann2017] stores the monotonic timestamp of
every admitted call and evicts records older than `window_seconds` before
each decision.  If the number of remaining records is below `max_calls` the
new call is recorded and admitted; otherwise the time until the oldest record
expires is returned.  Unlike the token-bucket implementation, no slot is
reserved on a rejected call: the caller must retry after sleeping.

# Interactive GUI

The optional GUI (`python -m rateguard` or the `rateguard-gui` command)
renders a live dashboard with:

- **Configuration panel** — sliders for all algorithm parameters, instant
  re-application without restart.
- **State gauge** — a colour-coded bar showing the current fill level
  (token bucket) or window occupancy (sliding window).
- **Call timeline** — a scrolling dot plot where filled circles represent
  immediately admitted calls and hollow circles represent throttled calls.
- **Session statistics** — total calls, admitted count, throttled count,
  and mean wait time.
- **Activity log** — timestamped, colour-coded record of every `acquire`
  invocation.

# Testing

The test suite (`tests/test_rateguard.py`) contains 28 test functions
exercising correctness of the core algorithms, input validation, boundary
conditions, and concurrent access under a five-thread stress load for each
primitive.  Continuous integration runs the full test suite across Python 3.9,
3.10, 3.11, and 3.12, with coverage reporting and static type checking via
`mypy`.

# Acknowledgements

The author thanks the open-source Python community whose extensive standard
library documentation made it possible to implement this library without
external dependencies.

# References
