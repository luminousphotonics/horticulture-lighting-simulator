# System-neutral surface-flux calibration experiment

Phase 27G-D1 is experimental infrastructure. It does not color plant surfaces,
change production visualization, or modify physical receiver or aggregation
values.

## Canonical reference field

The field is `neutral-uniform-upper-hemisphere-v1`. Radiance receives a
constant grayscale `glow` modified by a distant `source` directed along
`(0, 0, 1)` with a 180 degree angular diameter. This is the standard complete
upper-hemisphere construction: radiance is constant in polar angle and
azimuth, so it is a diffuse downward field rather than a product-derived
emission pattern.

The calibration scene uses an open boundary and no room surface geometry.
Requested room length and width define only the horizontal reference grid and
the existing natural-fit juvenile placement. This narrow experimental
boundary condition prevents a ceiling or walls from occluding the hemisphere
or adding room-reflection structure. Stage A contains only the open-boundary
document and neutral source. Stage B adds the frozen juvenile geometry and the
existing authoritative Rex leaf material for the current band. The source,
boundary, juvenile topology, receivers, materials, and their hashes are
published in the report.

Blue, green, orange, and red each receive exactly 0.25 of total source photon
amplitude. Every band uses the same spatial and angular document. Far-red is
not executed or aggregated.

The claim of spatial uniformity is empirical, not assumed from construction.
Every level publishes plant-free horizontal mean, minimum, maximum, population
standard deviation, CV, DOU (`100 - CV percent`), minimum/mean, and
minimum/maximum. A final Stage A CV greater than 0.5% stops the level and
reports the observed failure; the threshold is not weakened.

## Execution and equations

For each requested reference level, a unit-amplitude plant-free pilot trace is
followed by an independent resolved-amplitude plant-free trace. Four
independent plant transports then run, one per PAR band. No level is created by
scaling receiver results from another level.

The four raw fixed-stride receiver binaries are passed into the Phase 27G-C
derivation kernel. That kernel validates canonical front/back order, raw
counts, hashes, finite non-negative values, material reconstruction and
provenance, optical closure, and patch-to-leaf-to-plant-to-room conservation.
The calibration consumes one validated patch at a time.

For density `q[i,s,m]`, one-sided patch area `A[i]`, and achieved Stage A mean
`R`:

```
qbar[s,m]       = sum(A[i] * q[i,s,m]) / sum(A[i])
beta_level[s,m] = qbar[s,m] / R
beta[s,m]       = sum(R[level] * qbar[level,s,m]) / sum(R[level]^2)
x[i,s,m]        = q[i,s,m] / R
```

The original scalar candidate display transform was
`z[i,s,m] = q[i,s,m] / (beta[s,m] * R)`. No additive correction was used.
That equation remains historical schema-v1 behavior and remains operational
for v2 back coloring. V2 front coloring no longer uses front beta.

Each normalized distribution uses 400 fixed bins over `[0, 2)`, plus explicit
underflow and overflow counts and areas. Bounds are part of configuration
identity and are fixed before any receiver extrema are read. Pairwise
distribution drift is area-weighted total-variation distance including the two
overflow buckets.

Default acceptance requires, separately for front/back incident and absorbed
PAR:

- through-origin R-squared at least 0.999;
- relative beta range divided by mean beta no greater than 1%;
- all numerical, identity, closure, and conservation checks to pass; and
- every final neutral reference-plane CV no greater than 0.5%.

An optional higher-quality repeat compares its beta values with the matching
primary level and uses a default 1% relative-difference gate.

## Front local-patch neutral response

The v2 front revision derives one response coefficient for every canonical
local patch `k`, separately for incident and absorbed PAR. For calibration
plant index `p`, achieved reference `R`, metric `m`, and Standard level `l`:

```text
gamma_standard[m,k]
  = sum_l(R_l * mean_p(q[l,p,k,m])) / sum_l(R_l^2)

gamma_quality[m,k]
  = mean_p(q[quality500,p,k,m]) / R_quality500
```

Each family/metric array contains exactly 192 positive finite binary64 values
in canonical `local_patch_index` order. The coefficient is an average over all
64 calibration plants for the same local patch, not a coefficient for one
global patch or room position. The same `gamma[k]` is therefore reused on
every evaluated plant. This removes the fixed morphology response that caused
repeated scalar-normalized colors while preserving plant-position, occlusion,
and perimeter/interior lighting differences.

The packaged authoritative input is
`surface-flux-front-local-patch-calibration.v1.json`. Its SHA-256 is
`6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3`.
It is not derived at runtime from `.surface-flux-calibration/`, held-out runs,
or any uploaded archive. The Standard and Quality coefficient arrays, their
individual Float64 little-endian hashes, and their combined profile hash are
validated before use.

## CLI and artifacts

Default sweep:

```text
fspm-optics-calibrate-surface-flux
```

Explicit output and room:

```text
fspm-optics-calibrate-surface-flux \
  --output-dir /tmp/rex-surface-calibration \
  --room-ft 10 10 \
  --quality standard \
  --reference-levels 250 375 500 625 750
```

Higher-quality verification and exact resume:

```text
fspm-optics-calibrate-surface-flux \
  --output-dir /tmp/rex-surface-calibration \
  --verification-level 500 \
  --verification-quality rigorous

fspm-optics-calibrate-surface-flux \
  --output-dir /tmp/rex-surface-calibration \
  --verification-level 500 \
  --verification-quality rigorous \
  --resume
```

The default output is `.surface-flux-calibration/`, which is Git-ignored and
outside packaged resources. Experimental output is rejected inside packaged
resources or an existing published run.

`calibration-configuration.v1.json` locks the exact scientific inputs,
software revision, Radiance executable/version provenance, neutral source,
scene, and material identities. Each completed level has a hashed
`level-completion.v1.json`; resume validates every retained file and refuses
configuration changes, corruption, missing files, and extra files. Active
work is isolated in a same-filesystem temporary level directory. Interruption
removes that directory, while completion atomically renames it into place.

The final `surface-flux-calibration-report.v1.json` is written atomically. It
contains ordered compact level results, common histograms, regression and
convergence diagnostics, validation evidence, limitations, and a complete
relative-path artifact inventory. Full receiver arrays are retained only as
hash-inventoried binary artifacts and are never embedded in the report.

## D2 interpretation boundary

The completed Standard sweep plus Quality repeat retains the original report
fields and values exactly:

```text
experiment_status = complete_fail
acceptance.pass = false
```

The failure is under the original cross-quality convergence criterion. It is
not rewritten, relabeled, or converted into a pass. D2 retains distinct
Standard and Quality coefficient families; the observed difference remains a
numerical-quality diagnostic, with back surfaces the most sensitive, rather
than production acceptance.

V2 applies local-patch `gamma` only to front incident and absorbed coloring.
Front beta remains provenance but is not used for coloring. Back beta, the
back scalar equation, back anchors, colors, legends, and behavior remain
unchanged. Direct maps to Standard as a declared development-family proxy,
Standard maps to Standard without a proxy, Quality maps to Quality without a
proxy, and Rigorous maps to Quality as a declared high-fidelity proxy. The full
metadata, palette, reference, evidence, validation, and limitation contracts
are documented in `docs/viewer/system_neutral_surface_flux_coloring.md`.
