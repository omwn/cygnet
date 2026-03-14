# Cygnet — Agent Guidelines

## Testing

**Always add tests for new functionality.**

- Every new feature or behaviour change must be accompanied by at least one test.
- UI features go in `tests/test_ui.py` as Playwright tests against the test DB.
- Python logic goes in the appropriate `tests/test_*.py` unit-test file.
- Run the full suite (`pytest tests/`) before committing to confirm nothing regresses.
