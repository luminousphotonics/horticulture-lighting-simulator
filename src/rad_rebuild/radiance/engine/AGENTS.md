# Radiance Engine Rules

This scope contains scientific behavior. Read root `AGENTS.md` and `docs/engineering/SCIENTIFIC_CHANGE_POLICY.md` before editing.

## Local Invariants

- Preserve numerical behavior unless the task explicitly authorizes a reviewed scientific change.
- Treat IES parsing, SPD integration, PPFD metrics, layout geometry, source generation, optimization, and visualization transforms as scientific surfaces.
- Keep source models and comparator identities stable: `SMD`, `competitor_practical`, and `hps_karma_4x4`.
- Do not replace fixtures, relax tolerances, or update goldens to make tests pass without a documented review.
- Stop and report unexplained changes to PPFD maps, power summaries, layouts, basis matrices, or emitted Radiance files.

## Validation Expectations

- Run targeted engine tests plus the full pytest suite for behavior changes.
- For numerical changes, add characterization output, before/after metrics, and provenance in the change review.
- Prefer deterministic pure functions around scientific transforms.
