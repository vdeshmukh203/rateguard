# Changelog

All notable changes to rateguard are documented here.  The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and the project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] – 2026-05-07

### Added

- `TokenBucket.peek(tokens)` – inspect wait time without consuming tokens.
- `TokenBucket.reset()` – reset bucket to full capacity.
- `TokenBucket.tokens` property – read current token level.
- `SlidingWindow.peek()` – inspect wait time without recording a call.
- `SlidingWindow.reset()` – clear all recorded calls.
- `SlidingWindow.current_calls` property – read current call count.
- `CompositeRateLimiter` – compose any number of `TokenBucket` and
  `SlidingWindow` instances for dual-axis enforcement (e.g. RPM + TPM).
- Interactive Tkinter dashboard (`rateguard-dashboard` / `python -m
  rateguard.gui`) with real-time fill-level visualisation and simulated calls.
- `py.typed` marker for PEP 561 compliance.
- `CITATION.cff` for citation metadata.
- `paper.md` and `paper.bib` for JOSS submission.
- `CHANGELOG.md` (this file).

### Changed

- Error messages now include the offending value (e.g.
  `"rate must be a finite positive number, got -1.0"`).
- Constructor validation rejects `math.nan` and `math.inf` for `rate` and
  `window_seconds`.
- `__version__ = "0.2.0"` added to the package.
- `pyproject.toml`: added optional extras (`test`, `gui`), `rateguard-
  dashboard` script entry point, `py.typed` package data, and extended
  classifiers.
- CI workflow now installs the package with `.[test]` extras.

## [0.1.0] – 2026-04-01

### Added

- `TokenBucket` – token-bucket rate limiter with configurable rate and burst.
- `SlidingWindow` – sliding-window rate limiter with configurable call count
  and window length.
- Thread-safe implementation using `threading.Lock`.
- Full test suite with 15 tests covering correctness and thread safety.
- CI workflow for Python 3.9–3.12 via GitHub Actions.
