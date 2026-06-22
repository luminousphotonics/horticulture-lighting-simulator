# Codex Operating Contract

This repository favors small, scoped changes with clear validation. Read this file,
the nearest scoped `AGENTS.md`, and the relevant engineering docs before editing.

## Repository Map

- `src/rad_rebuild/web/`: Flask entrypoints, templates, and browser assets.
- `src/rad_rebuild/radiance/backend/`: FastAPI models, routes, execution, jobs,
  workspace handling, and artifact responses.
- `src/rad_rebuild/radiance/engine/`: scientific simulation, emitters,
  photometry, geometry, validation, and visualization code.
- `src/rad_rebuild/layout/`: layout generator compatibility modules.
- `scripts/radiance/`: live Radiance helpers, basis extraction, bundle generation,
  and reproducibility scripts.
- `data/radiance/`: source curves, IES files, and committed public demo bundles.
- `tests/`: pytest tests for public behavior, scientific contracts, import
  boundaries, and runtime cleanup.
- `docs/`: architecture, model, and engineering documentation.
- `audit/`: durable quality-gate configuration and security allowlists.

## Working Rules

- Keep edits tightly scoped to the requested behavior.
- Preserve existing module boundaries unless the change clearly requires moving
  them.
- Add or update focused tests for behavior changes.
- Do not create generated phase plans, inventories, reconciliation reports, or
  benchmark artifacts.
- Do not commit secrets, local credentials, runtime state, expanded generated
  bundles, caches, or downloaded private datasets.
- Use argument-list subprocess calls in automation; avoid shell invocation unless
  a shell script is the thing being tested or run.
- Before any commit, push, or merge expected to go through GitHub CI, validate
  the exact state that will be pushed. Start with `git status --short`; if fixes
  are still uncommitted, either commit them before validating the push candidate
  or keep the validation result scoped to the dirty worktree and say so.
- The GitHub "Baseline Python 3.12" job is a multi-step gate. To prevent
  surprises, reproduce its shape locally instead of running only the durable
  Python baseline:
  1. Run `npm ci` when JavaScript dependencies changed or the checkout is fresh.
  2. Run `PYTHONPATH=src ./.venv/bin/python scripts/dev/baseline.py`.
  3. Run `npm run test:browser` as a separate required check.
  4. Run `git diff --check`.
- When GitHub reports a "Baseline Python 3.12" failure, inspect the failed step
  log before fixing, using `gh run view <run-id> --job <job-id> --log` when
  available. Identify whether the failed step was installation, durable baseline,
  or browser smoke; do not infer the failing command from the job name.
- For viewer, modal, frontend asset, or browser interaction changes, the browser
  smoke command must pass locally before push. If local sandboxing blocks the dev
  server or browser socket, rerun the same command with approved local-server
  escalation instead of counting the sandbox-blocked run as validation.
- Keep mobile browser smoke assertions lightweight. Mobile should verify core
  page load, essential controls, and one or two critical state transitions. Put
  detailed diagnostics, repeated control interactions, long fixture inventory
  checks, and expensive WebGL assertions in desktop browser tests or focused
  Node/Python tests so CI mobile runs do not time out.

## Scientific Rules

- Scientific outputs, fixtures, source curves, IES files, and committed
  precomputed demo bundles are protected artifacts.
- Do not change formulas, source models, bundle contents, numerical goldens, or
  protected data unless the task explicitly authorizes a reviewed scientific
  change.
- Stop and report if a numerical golden changes unexpectedly, a binary artifact
  changes outside an authorized data task, or a migration could lose source data
  or committed bundles.

## Supported Commands

Create and activate a local virtual environment:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install the source-checkout app/development profile:

```bash
python -m pip install -e ".[dev]"
```

Run the development supervisor:

```bash
rad-rebuild-dev
```

Run the Flask app only:

```bash
PYTHONPATH=src python -m rad_rebuild.web.app
```

Run tests:

```bash
PYTHONPATH=src python -m pytest -q tests
```

Run the durable local quality gate:

```bash
npm ci
PYTHONPATH=src python scripts/dev/baseline.py
```

Prefer the repository virtualenv when present:

```bash
PYTHONPATH=src ./.venv/bin/python -m pip install -e ".[dev]"
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests
npm ci
PYTHONPATH=src ./.venv/bin/python scripts/dev/baseline.py
```

Check generated dependency exports:

```bash
UV_CACHE_DIR=/tmp/rad_rebuild_uv_cache ./.venv/bin/uv lock --check
UV_CACHE_DIR=/tmp/rad_rebuild_uv_cache ./.venv/bin/uv export --frozen --format requirements.txt --extra web --extra api --extra scientific --no-dev --no-emit-project --output-file requirements.txt
UV_CACHE_DIR=/tmp/rad_rebuild_uv_cache ./.venv/bin/uv export --frozen --format requirements.txt --extra scientific --extra live-radiance --no-dev --no-emit-project --output-file docker/radiance/requirements.runtime.txt
git diff --exit-code -- requirements.txt docker/radiance/requirements.runtime.txt
```

## Definition Of Done

- Requested behavior is implemented and covered by focused tests where useful.
- Relevant pytest tests pass.
- `scripts/dev/baseline.py` passes for broad quality-gate changes.
- Browser smoke checks pass for viewer, modal, frontend asset, and browser
  interaction changes.
- `uv.lock` and generated requirements exports remain consistent when packaging
  metadata or dependencies change.
- Ruff, Mypy, Bandit, ShellCheck, ESLint, and HTML validation are run when they
  apply to the changed surface.
- `git diff --check` passes.
