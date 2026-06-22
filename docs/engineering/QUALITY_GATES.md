# Quality Gates

The durable local gate runner is:

```bash
npm ci
PYTHONPATH=src python scripts/dev/baseline.py
```

Use the repository virtualenv when present:

```bash
npm ci
PYTHONPATH=src ./.venv/bin/python scripts/dev/baseline.py
```

## Gate Policy

Hard failures:

- Python compile fails.
- Pytest fails.
- Repository branch coverage drops below the ratcheted floor in
  `audit/debt_ratchet.json`. The current floor is 65%.
- First-party pytest warnings are introduced.
- JavaScript parse fails.
- Bash parse fails.
- Ruff, Mypy, or ShellCheck introduces a new finding fingerprint.
- Bandit exceeds the configured high, medium, or low limits.
- ESLint reports errors or warnings.
- HTML validation exceeds the configured error limit.

Optional tools:

- Missing optional tools are reported as `missing`.
- Missing optional tools are not counted as passes.
- A missing optional tool fails only when the task requires that tool.

## Standard Commands

```bash
python -m pip install -e ".[dev]"
PYTHONPATH=src python -m pytest -q tests
npm ci
PYTHONPATH=src python scripts/dev/baseline.py
PYTHONPATH=src python -m ruff check .
git diff --check
```

Dependency lock and generated export checks:

```bash
UV_CACHE_DIR=/tmp/rad_rebuild_uv_cache ./.venv/bin/uv lock --check
UV_CACHE_DIR=/tmp/rad_rebuild_uv_cache ./.venv/bin/uv export --frozen --format requirements.txt --extra web --extra api --extra scientific --no-dev --no-emit-project --output-file requirements.txt
UV_CACHE_DIR=/tmp/rad_rebuild_uv_cache ./.venv/bin/uv export --frozen --format requirements.txt --extra scientific --extra live-radiance --no-dev --no-emit-project --output-file docker/radiance/requirements.runtime.txt
git diff --exit-code -- requirements.txt docker/radiance/requirements.runtime.txt
```

## Coverage And Benchmarks

- Coverage is collected with branch tracking through pytest.
- Hypothesis uses the deterministic local profile configured in
  `tests/conftest.py` unless `HYPOTHESIS_PROFILE` is set.
- Test markers are defined for `unit`, `integration`, `radiance`, `docker`,
  `slow`, and `benchmark`.
- Benchmark JSON should be written outside the repository unless a task
  explicitly authorizes committing benchmark data.

## API Contracts

- OpenAPI operation IDs must be present and unique.
- OpenAPI generation must not emit first-party warnings.
