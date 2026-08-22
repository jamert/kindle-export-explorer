## Development commands

- Always run Python through uv; do not use bare `python`.
- Run tests and type checks with `uv run pytest && uv run pyright`.
- Run ordinary tests with `KINDLE_EXPORT_PATH` unset unless the test explicitly exercises that variable.

## Type safety

- Do not add `Any` or `cast(Any, ...)` merely to make Pyright pass. Use protocols, overloads, type narrowing, or explicit branches.

## Acceptance tests

- Do not claim a layout or behavior is supported unless it has an automated test or an explicitly recorded manual verification.

## Repository inspection

- Exclude `.git`, `.venv`, `.pytest_cache`, `__pycache__`, and generated `scratch/` outputs from broad searches unless they are directly relevant.
