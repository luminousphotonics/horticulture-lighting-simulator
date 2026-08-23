# Phase 27G-D5-A1 optimized surface-flux sweep

Phase 27G-D5-A1 is an execution-only, system-neutral calibration experiment.
It does not derive coefficients, package a calibration resource, or enable
surface coloring. Optimized-profile coloring remains fail-closed until a later
reviewed analysis and promotion phase.

## Fixed scientific contract

The executable has no quality, level, band, sampling-profile, or room-shape
override. It fixes:

- sampling profile
  `rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1`;
- topology SHA-256
  `d59c27614c9b4fde3d60f8ebf090b56bbc6692d77baeed67753e8081ff70138f`;
- receiver SHA-256
  `e2f6606ba78642ffe6f9de0601d87c8f4e4197b66cb3ef9d284f8324e1ee305f`;
- D5 source model
  `neutral-uniform-upper-hemisphere-v1`, reusing the historically validated
  grayscale Radiance `glow` modifier on the distant 180-degree `source`;
- requested total PAR levels 250 and 500 µmol m⁻² s⁻¹;
- equal blue, green, orange, and red photon fractions of 0.25;
- Standard, Quality, and Rigorous as independent Radiance jobs; and
- no far-red, Proposed, Conventional, proxy among calibrated families, option
  fallback, or evaluated-system input.

Job order is quality-major and level-minor:

1. `standard-250`
2. `standard-500`
3. `quality-250`
4. `quality-500`
5. `rigorous-250`
6. `rigorous-500`

Each job performs its own unit-amplitude Stage A pilot, resolved Stage A final
trace, and four Stage B band traces. The completed sweep therefore contains 6
Stage A evidence records and exactly 24 authenticated Float64 Stage B receiver
artifacts. Each Stage B artifact contains 24,576 little-endian Float64 values
in canonical plant-major, local-patch, front/back receiver order.

The evidence count is six because each job owns one independently resolved
Stage A reference; the trace count is twelve because every reference has a
pilot and a final trace. All three executed quality families have ambient
evaluation and use the same historically validated glow source in Stage A and
all four Stage B bands.

Direct is intentionally not a calibration family. A future display phase will
map Direct to Standard coefficients for display normalization only and will
authenticate and report that proxy. Raw Direct transport will continue to use
the real Direct Radiance preset. D5-A1 neither executes Direct nor implements
that display mapping, and no proxy is permitted among Standard, Quality, and
Rigorous.

## Commands

Inspect the deterministic plan without discovering or executing Radiance and
without writing the output directory:

```bash
fspm-optics-calibrate-surface-flux-d5 \
  --plan \
  --output-dir /tmp/phase27g-d5-a1-glow-v3
```

Execute a fresh sweep:

```bash
fspm-optics-calibrate-surface-flux-d5 \
  --output-dir /tmp/phase27g-d5-a1-glow-v3
```

Resume the exact sweep after interruption:

```bash
fspm-optics-calibrate-surface-flux-d5 \
  --output-dir /tmp/phase27g-d5-a1-glow-v3 \
  --resume
```

`--threads`, `--oconv`, and `--rtrace` are execution controls and become part
of the authenticated configuration identity. Changing any of them requires a
new output directory. The revised scope is experiment identity
`phase27g-d5-a1-optimized-surface-flux-sweep-v3`; a directory initialized by
the v1 or v2 implementation cannot be resumed as v3.

## Artifact layout and resume policy

```text
phase27g-d5-a1-glow-v3/
  recalibration-configuration.v1.json
  jobs/
    standard-250/
      job-result.v1.json
      job-completion.v1.json
      reference/...
      bands/00-blue/receiver-values.v1.f64le.bin
      bands/01-green/receiver-values.v1.f64le.bin
      bands/02-orange/receiver-values.v1.f64le.bin
      bands/03-red/receiver-values.v1.f64le.bin
    ... five further jobs ...
  surface-flux-recalibration-completion.v1.json
```

Every job is written to a same-filesystem temporary directory. Its complete
file inventory is size- and SHA-256-authenticated before the directory is
committed atomically. Resume validates the stored configuration, completion
manifest, job identity, exact Radiance option vector, complete file inventory,
artifact sizes, and hashes before reusing a job. Corrupt, extra, duplicated,
substituted, or mismatched artifacts cause a hard failure; they are never
overwritten or silently reused.

The top-level completion schema is
`fspm-optics.surface-flux-recalibration-completion` version 1. It inventories
the independently achieved Stage A references and the ordered 24-artifact
Stage B matrix. It explicitly records that coefficients, production resources,
and optimized coloring have not been produced.

The other D5-only schemas are:

- `fspm-optics.surface-flux-recalibration-plan` version 1;
- `fspm-optics.surface-flux-recalibration-configuration` version 1;
- `fspm-optics.surface-flux-recalibration-job` version 1; and
- `fspm-optics.surface-flux-recalibration-job-completion` version 1.

None reuses or changes a historical D1/D2 schema identifier.

## Deferred work

Local-patch coefficient calculation, front/back palettes, any near-zero policy,
calibration promotion, display metadata changes, and viewer/backend coloring
are intentionally outside D5-A1.

After this v3 sweep is complete, the separate non-Radiance D5-A2 analyzer
described in [phase27g_d5_a2_coefficient_analysis.md](phase27g_d5_a2_coefficient_analysis.md)
may authenticate it and derive candidate coefficient and normalized-neutral
evidence. D5-A2 does not change this execution-only contract or promote a
production calibration resource.
