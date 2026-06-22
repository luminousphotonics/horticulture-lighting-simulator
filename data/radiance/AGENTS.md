# Radiance Data Rules

This scope contains source data and minimal public precomputed bundles. Read root `AGENTS.md` and `docs/engineering/SCIENTIFIC_CHANGE_POLICY.md` before editing.

## Local Invariants

- Source curves, IES files, and committed `10x10` precomputed demo bundles are protected scientific artifacts.
- Preserve file checksums unless the task explicitly authorizes a reviewed data change.
- Record provenance, schema, and checksum evidence for any data migration.
- Do not commit expanded generated precomputed sweeps outside the documented public demo bundles.

## Validation Expectations

- Verify checksums after touching protected data.
- Run scientific characterization and route playback tests for any authorized data change.
- Stop on unexplained numerical or binary deltas.
