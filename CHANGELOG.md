# Changelog

All notable changes to rateguard are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [0.1.0] — 2026-05-01

### Added

- `TokenBucket` — token-bucket rate limiter with configurable `rate` and
  `burst`; thread-safe; reserves tokens on blocked calls for fair
  concurrency.
- `SlidingWindow` — sliding-window rate limiter with configurable
  `max_calls` and `window_seconds`; thread-safe; does not reserve on block.
- `TokenBucket.tokens_available` property — current available tokens (≥ 0).
- `TokenBucket.reset()` — restore bucket to full capacity.
- `TokenBucket.__repr__`.
- `SlidingWindow.calls_in_window` property — live count of in-window calls.
- `SlidingWindow.slots_remaining` property — remaining capacity right now.
- `SlidingWindow.call_ages()` — ages (seconds) of all in-window calls.
- `SlidingWindow.reset()` — clear all recorded calls.
- `SlidingWindow.__repr__`.
- `RateLimiter` — `typing.Protocol` satisfied by both classes; enables
  type-safe polymorphic usage.
- `__version__` module attribute.
- `rateguard-gui` CLI entry point — Tkinter visualizer with animated
  Token Bucket fill gauge and Sliding Window timeline.
- Full test suite with 39 tests and 100 % line coverage.
- Strict mypy and ruff CI checks.
