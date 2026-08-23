# Phase 25E HPS Rex absorbed-photon policy

Phase 25E is a pure post-processor for one already successful Phase 25D
`hps_rex_transport` workspace. It reconstructs the Phase 25B scientific
bundle and Phase 25D execution plan from the persisted request, then requires
their exact identities and serialized authorities. It also validates the HPS
source, SPD-derived budgets, Rex optics and materials, room, plant, two-sided
receivers, shared angular DAT, isolated scenes and caches, native command
provenance, final incident archive, and each decoded run array. A failure
summary or any partial, changed, substituted, malformed, nonfinite, or negative
authority prevents output.

The reconstructed HPS source is the Phase 3 v2 authority: fixed 1,750 umol/s
initial lamp PAR PPF, 1,045 W tested system input, computed system PPE, and the
hash-locked `hps_1000w_spd.csv` relative photon shape. Absorption never
changes or reconciles that source authority.

Legacy Phase 25D summaries without complete ordered per-run artifact hashes are
not accepted. Before calculation, Phase 25E recomputes the recorded SHA-256 for
every source RAD, plant/material RAD, scene manifest, octree, ambient cache,
raw RGB, and decoded NPY. This makes opaque octree and ambient-cache byte
substitution fail closed, including missing or empty artifacts.

For each blue, green, orange, red, and far-red receiver value, the Phase 25B
HPS-weighted coefficients are applied directly:

`absorbed = incident × A`, `transmitted = incident × T`, and
`reflected = incident × R`.

Front and back PFD remain separate and are combined before one physical patch
area is applied exactly once. Local A/T/R closure is checked at both PFD and
photon-flux levels. Patch values aggregate into the fixed 512 patches, 32
leaves, and whole plant. Four-band PAR is blue + green + orange + red; far-red
is separate. The positive 400–403 nm source mass unsupported by the Rex grid is
reported from Phase 25B and is never corrected, extrapolated, or redistributed.

Scalar absorbed PAR is only `scalar incident PFD × HPS scalar absorptance`.
Its whole-plant, area-weighted, sided, receiver-RMSE, and patch-RMSE differences
from four-band absorbed PAR are diagnostics. Scalar is never added to the
four-band result, and no reconciliation scale or pass threshold is applied.

The only outputs are
`hps_rex_transport/absorbed/absorbed_photon_summary.json` and
`absorbed_patch_metrics.npz`. The NPZ uses fixed member order, uncompressed NPY
members, and fixed ZIP metadata. Exact existing outputs are accepted
idempotently; partial, unknown, or unequal outputs are refused without silent
overwrite. The CLI command `hps-rex-absorbed-metrics --workspace ...`
performs no Radiance discovery, probing, or execution.
