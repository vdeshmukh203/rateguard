# Contributing to rateguard

Thank you for considering a contribution!  This document covers how to set up
a development environment, run the test suite, and submit a pull request.

---

## Setting up a development environment

```bash
# 1. Fork and clone
git clone https://github.com/<your-fork>/rateguard
cd rateguard

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# 3. Install in editable mode with all dev extras
pip install -e ".[dev]"
```

---

## Running the tests

```bash
pytest                         # run all tests
pytest -v                      # verbose output
pytest --cov=rateguard --cov-report=term-missing   # with coverage
```

All tests must pass and coverage must remain at 100 % before a PR is merged.

---

## Code style

rateguard uses **ruff** for linting/formatting and **mypy** (strict mode) for
type checking.

```bash
ruff check src tests           # lint
ruff format src tests          # auto-format
mypy src/rateguard             # type check
```

CI will run both checks automatically on every pull request.

### Key conventions

- Pure standard library only — no new runtime dependencies.
- Type-annotate every public function, method, and attribute.
- Keep docstrings in Google-style with a short one-line summary, optional
  extended description, Args/Returns/Raises sections, and an `Example::` block.
- Write tests for every new public method or property; maintain 100 % coverage.
- No comments that merely restate the code.  Add a comment only when the *why*
  is non-obvious.

---

## Pull request process

1. Open an issue first for significant changes so the approach can be discussed.
2. Create a feature branch from `main`: `git checkout -b feature/my-change`.
3. Make your changes, write tests, run `pytest` and both linters.
4. Push the branch and open a draft PR.
5. Once CI is green and you are happy with the code, mark the PR as ready.
6. A maintainer will review and merge or request changes.

---

## Reporting bugs

Please open a [GitHub issue](https://github.com/vdeshmukh203/rateguard/issues)
with a minimal reproducible example and the Python version you are using.
