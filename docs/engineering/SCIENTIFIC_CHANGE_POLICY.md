# Scientific Change Policy

Scientific behavior is protected because small changes can alter comparative conclusions.

## Protected Artifacts

- Source curves in `data/radiance/curve_data/`
- IES files in `data/radiance/ies_sources/`
- Minimal committed precomputed bundles in `data/radiance/precomputed/*/10x10/`
- Layout JSON, power JSON, manifests, basis matrices, PPFD maps, and visualization outputs
- Engine code that affects geometry, photometry, emitter generation, optimization, simulation, validation, or visualization

## Required Review For Scientific Changes

A change that intentionally changes scientific behavior must include:

- Reason for the change and expected impact.
- Before/after numerical metrics.
- Golden, fixture, or bundle deltas with provenance.
- Independent validation command or script.
- Rollback plan that preserves old artifacts until review is complete.

## Stop Conditions

Stop and report when:

- A numerical golden changes unexpectedly.
- A binary scientific artifact changes outside an authorized data phase.
- A migration would delete or overwrite source data or precomputed bundles.
- A validation result cannot be reproduced.

## Waivers

Waivers must be narrow, file-specific, and documented next to the validation result. Waivers do not permit unreviewed scientific changes.
