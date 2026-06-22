# Web Rules

This scope contains the Flask UI, templates, styles, and browser scripts. Read root `AGENTS.md` and `docs/engineering/QUALITY_GATES.md` before editing.

## Local Invariants

- Public mode remains precomputed-first unless the task explicitly changes execution exposure.
- Live local and Docker modes remain gated by environment configuration.
- Route query contracts, artifact URLs, and response shapes must stay compatible with existing tests.
- Do not expose local filesystem paths, secrets, or runtime-only output through templates or JSON responses.
- Keep frontend parsing deterministic and avoid browser-only assumptions in shared JavaScript modules.

## Validation Expectations

- Run web route contract tests for route or template changes.
- Run Node parse checks for JavaScript changes.
- Run HTML validation when installed for template changes.
