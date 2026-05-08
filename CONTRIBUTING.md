# Contributing to rateguard

Thank you for your interest in contributing!

## Getting started

```bash
git clone https://github.com/vdeshmukh203/rateguard
cd rateguard
pip install -e ".[dev]"
```

## Running the tests

```bash
python -m pytest
```

All 30 tests must pass before a pull request can be merged.

## Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Use full type annotations on all public functions and methods.
- Keep runtime dependencies at zero — the library must remain pure standard library.
- Write tests for any new behaviour in `tests/test_rateguard.py`.
- Document new public APIs with NumPy-style docstrings.

## Submitting changes

1. Fork the repository and create a branch from `main`.
2. Make your changes and ensure tests pass.
3. Open a pull request with a clear description of what was changed and why.

## Reporting bugs

Please open an issue at <https://github.com/vdeshmukh203/rateguard/issues>
and include a minimal reproducible example.

## Code of Conduct

Be respectful and constructive. Harassment of any kind will not be tolerated.
