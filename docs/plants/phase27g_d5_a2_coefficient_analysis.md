# Phase 27G-D5-A2 coefficient analysis

Phase 27G-D5-A2 is a deterministic, non-Radiance analyzer for the completed
`phase27g-d5-a1-optimized-surface-flux-sweep-v3` experiment. It reads the D5-A1
directory without modifying it and atomically publishes evidence to a separate,
previously absent output directory.

Run it only after the D5-A1 v3 completion manifest has been published:

```bash
fspm-optics-analyze-surface-flux-d5 \
  --input-dir /persistent/path/phase27g-d5-a1-optimized-surface-flux-sweep-v3 \
  --output-dir /persistent/path/phase27g-d5-a2-coefficient-analysis-v1
```

The command has no Radiance executable, quality, level, topology, band, system,
or palette options and never invokes Radiance. The input must contain exactly
the authenticated v3 configuration, six quality-major/level-minor jobs, twelve
Stage A traces, twenty-four Stage B Float64 artifacts, and the completed top-level
manifest. Configuration identity, scene and receiver hashes, exact quality
options, every nested job inventory, all artifact sizes and SHA-256 values, and
the reconstructed completion document are validated before receiver values are
decoded. A structural or authentication disagreement aborts without publishing
an output directory.

For each Standard, Quality, and Rigorous family, the analyzer sends the four
ordered blue/green/orange/red Stage B files through
`stream_juvenile_par_surface_light`, the existing authoritative Phase 27G-C
kernel. That kernel reconstructs incident and absorbed PAR using the bound leaf
material authorities. The analyzer does not duplicate absorption constants or
optical equations.

For local patch `k` and achieved Stage A reference `R_l`, it computes

```text
mean_q_l[k] = math.fsum(q[l,p,k] for p in 0..63) / 64
ratio_l[k]  = mean_q_l[k] / R_l
gamma[k]    = sum_l(R_l * mean_q_l[k]) / sum_l(R_l^2)
e_l[k]      = mean_q_l[k] - gamma[k] * R_l
```

The fit reuses the project's established centered-residual definition
`R² = 1 - Σe² / Σ(mean_q - mean(mean_q))²`, including its exact constant-data
behavior. Per-level ratios, relative ratio drift, signed residuals, R², and
promotion reasons are retained for all 192 local patches in every channel.

## Output contracts

The coefficient manifest schema is
`fspm-optics.surface-flux-coefficient-payload-manifest` version 1. It binds
twelve headerless little-endian IEEE-754 Float64 arrays of shape `[192]`, in
this exact order:

1. Standard: front incident, front absorbed, back incident, back absorbed;
2. Quality: front incident, front absorbed, back incident, back absorbed;
3. Rigorous: front incident, front absorbed, back incident, back absorbed.

Each array is in canonical `local_patch_index 0..191` order with units
dimensionless `q/R`. A combined `[3,4,192]` payload concatenates the same bytes
in the same order. Every individual binary and the combined binary records its
schema, component type, byte order, units, shape, length, ordering, byte length,
and SHA-256.

The normalized-evidence schema is
`fspm-optics.surface-flux-normalized-neutral-evidence` version 1. It publishes
24 separate `[64,192]` Float64 arrays using
`u[p,k] = q[p,k] / (gamma[k] * R)`, ordered by family, level, channel, plant,
and local patch. Front and back never share a distribution. Each artifact binds
its family, requested and achieved level, side, metric, source job, all four raw
receiver hashes, and coefficient hash. No palette or color anchor is selected.

Exact-zero coefficients are retained without clamping, substitution, a
near-zero threshold, or an availability policy. Because `u` is mathematically
undefined when `gamma == 0`, those binary entries use the single documented
canonical quiet-NaN bit pattern `0x7ff8000000000000`; their exact flat indices
are authenticated in the neutral-evidence manifest. They are never treated as
finite samples. Any exact zero or failed linearity criterion sets
`promotion_eligible: false` while preserving the valid analysis evidence.

The analysis report schema is
`fspm-optics.surface-flux-coefficient-analysis` version 1, and the atomic output
completion schema is
`fspm-optics.surface-flux-coefficient-analysis-completion` version 1. Production
calibration-resource generation, near-zero policy, palette selection, metadata
v3, backend/viewer coloring, and Proposed/Conventional evaluation remain
explicitly deferred. Direct is absent; only the future display-normalization
declaration `Direct -> Standard` is retained, and it never changes raw Direct
transport.
