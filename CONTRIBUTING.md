# Contributing to rateguard

Thank you for considering a contribution to rateguard! The following guidelines
help keep the process smooth for everyone.

## Reporting bugs

Open an issue at <https://github.com/vdeshmukh203/rateguard/issues> and include:

- Python version and operating system
- A minimal, self-contained code snippet that reproduces the problem
- The full traceback or unexpected output

## Requesting features

Open an issue with the label **enhancement**. Describe the use-case, not just
the proposed API, so we can evaluate it in context.

## Submitting pull requests

1. Fork the repository and create a branch from `main`.
2. Install the package in editable mode with dev extras:
   ```bash
   pip install -e ".[dev]"
   ```
3. Make your changes. Keep commits focused and atomic.
4. Add or update tests so that `pytest` passes with full coverage of the
   changed code.
5. Run the test suite:
   ```bash
   pytest
   ```
6. Open a pull request against `main`. The CI pipeline runs on Python 3.9 –
   3.12 and must be green before merging.

## Code style

- Follow [PEP 8](https://peps.python.org/pep-0008/).
- Use type annotations for all public function signatures.
- Keep the zero-dependency contract: no third-party packages in
  `src/rateguard/` (the GUI's use of `tkinter`, which is part of the standard
  library, is the sole exception).
- Write tests in `tests/test_rateguard.py` using `pytest` conventions.

## Versioning

rateguard follows [Semantic Versioning](https://semver.org/). Breaking changes
to the public API require a major version bump.

## License

By contributing you agree that your work will be released under the
[MIT License](LICENSE) that covers this project.
