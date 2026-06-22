# Radiance Script Rules

This scope contains command-line helpers for live Radiance execution, reproducibility, basis extraction, and dataset support. Read root `AGENTS.md` before editing.

## Local Invariants

- Preserve CLI flags, environment variables, output paths, and artifact naming unless the task explicitly migrates them.
- Treat shell scripts as reproducibility contracts; avoid host-specific assumptions.
- Do not overwrite source data or committed precomputed demo bundles without an explicit migration and rollback plan.
- Keep generated output under documented runtime output directories.

## Validation Expectations

- Run Bash parse checks for changed shell scripts.
- Run ShellCheck when installed.
- Run Python compile and targeted tests for changed Python helpers.
