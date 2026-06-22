# Contributing

Thanks for helping improve `rad_rebuild`. This repository favors small,
reviewable changes with clear validation.

## Start Here

1. Read `AGENTS.md` and the nearest scoped `AGENTS.md` before editing.
2. Create a virtual environment and install the app/development profile:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

3. Run focused tests while working, then run the relevant gates before opening a
   pull request:

```bash
PYTHONPATH=src python -m pytest -q tests
npm ci
PYTHONPATH=src python scripts/dev/baseline.py
git diff --check
```

## Scope And Review

- Keep changes tightly scoped to the issue or pull request.
- Do not refactor production code as part of documentation-only changes.
- Add focused tests for behavior changes.
- Do not commit secrets, runtime state, expanded private datasets, caches, or
  generated benchmark artifacts.
- Use argument-list subprocess calls in Python automation.

## Scientific Artifacts

Source curves, IES files, committed precomputed bundles, numerical goldens, and
simulation formulas are protected. Any intentional scientific change needs the
review material described in `docs/engineering/SCIENTIFIC_CHANGE_POLICY.md`.
Stop and ask before changing protected scientific data unexpectedly.

## Licensing

By submitting a contribution, you agree that your contribution is licensed under
the Apache License 2.0.
