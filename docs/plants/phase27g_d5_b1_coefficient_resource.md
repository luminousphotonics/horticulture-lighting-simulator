# Phase 27G-D5-B1 coefficient-resource promotion

Phase 27G-D5-B1 is a deterministic, non-Radiance promotion step for a completed
and promotion-eligible D5-A2 analysis. It reads the complete A2 directory without
modifying it and atomically publishes a separate, previously absent candidate
directory.

```bash
fspm-optics-promote-surface-flux-d5 \
  --input-dir /persistent/path/phase27g-d5-a2-coefficient-analysis-v1 \
  --output-dir /persistent/path/phase27g-d5-b1-coefficient-candidate-v1
```

Before creating output, the command authenticates the A2 completion, report,
coefficient manifest, normalized-evidence manifest, ordered inventory, and every
referenced binary. It requires `promotion_eligible: true`, validates all fixed
D5-A1 v3 and optimized scientific identities, and verifies that the twelve
ordered `[192]` coefficient arrays concatenate byte-for-byte into the
authenticated 18,432-byte `[3,4,192]` combined Float64 payload.

The output contains exactly:

```text
optimized-surface-flux-local-patch-calibration.v1.json
optimized-surface-flux-local-patch-coefficients.v1.f64le.bin
```

The manifest schema is
`fspm-optics.optimized-surface-flux-local-patch-calibration-resource` version 1.
The payload remains little-endian IEEE-754 Float64 in family-major order:
Standard, Quality, Rigorous; within each family, front incident, front absorbed,
back incident, back absorbed; within each array, local patch 0 through 191.
Each slice has an authenticated offset, length, and SHA-256.

The manifest links the D5-A1 v3 completion/configuration hashes; the D5-A2
completion, report, coefficient-manifest, normalized-evidence-manifest, and
combined-payload authorities; the optimized sampling/topology/receiver, scene,
source, material, and quality-option identities; and all aggregate and per-slice
binary counts and hashes. Its status is always `candidate`, `packaged_resource`
and `production_enabled` are false, and the combined source/candidate SHA-256
values must be identical.

The candidate loader exposes `coefficient_for_family(...)` for exact calibrated
families and the explicitly display-only `coefficient_for_display_quality(...)`
mapping. Standard, Quality, and Rigorous use their exact arrays. Direct is absent
from calibration and maps to Standard only through that future display
normalization entry point; no raw-transport proxy API exists. Unknown identities
and malformed, corrupt, negative, or non-finite coefficients are rejected. No
near-zero threshold or substitution is introduced.

The output is deliberately marked candidate, non-packaged, and not production
enabled. Palette selection, near-zero policy, metadata v3, publication/viewer
activation, held-out Proposed/Conventional acceptance, and production packaging
remain deferred. No calibration binary is added to package data in D5-B1.
