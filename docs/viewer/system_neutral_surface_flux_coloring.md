# System-neutral surface-flux coloring

> Status: retained scientific and regression contract. The current public 3D
> UI exposes only Target Coverage. Incident/Absorbed PAR selectors are not
> public, and compact playback bundles omit their per-patch/per-leaf display
> arrays while preserving native science and aggregate absorption metrics.

Phase 27G-D2 adds a display-only view of the validated Phase 27G-C plant
surface-light artifacts. It does not execute transport, change a source,
rewrite a receiver value, cap a value to a target, or modify the authoritative
little-endian Float64 patch table. Publication continues to emit one
authenticated little-endian Float32 raw-q table with four channels per
canonical patch: front incident PAR, back incident PAR, front absorbed PAR,
and back absorbed PAR. The browser authenticates that table before allocating
one bounded, same-sized Float32 display array.

This calibration is valid only for
`rex_juvenile_surface_sampling_uv_quarter_4x4_v1`, whose frozen topology and
receiver hashes are listed below. The optimized
`rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1` profile has new
dynamically generated hashes and is uncalibrated. Publication fails closed to
neutral/unavailable coloring for that profile: neither front gamma nor scalar
beta is applied, no profile fallback occurs, and authoritative raw Stage C
surface-light outputs remain available.

The Phase 27G-D2 v2 revision changes only front receiver coloring. Front
incident and absorbed PAR use a canonical-local-patch neutral response `gamma`
and display variable `u`. Back incident and absorbed PAR retain the v1 scalar
`beta` equation, anchors, colors, legends, and behavior. This is morphology
compensation for display only; no scientific `q` value is replaced by `u` or
`z`.

## Calibration interpretation and provenance

The original Phase 27G-D1
`fspm-optics.surface-flux-calibration-report` v1 result remains exactly:

```text
experiment_status = complete_fail
acceptance.pass = false
```

The result failed its original cross-quality convergence criterion. D2 does
not relabel, rewrite, or convert that result into a pass. V2 provenance carries
the exact copies `original_d1_experiment_status = "complete_fail"`,
`original_d1_acceptance_pass = false`, and
`original_d1_outcome_must_not_be_rewritten = true`. The retained evidence
nevertheless shows exact Stage A uniformity validation, complete receiver and
conservation validation, Standard five-level through-origin fits above 0.9999
R-squared, Standard beta drift below 0.85%, and a complete Quality repeat at an
achieved reference of 500.0112 micromoles per square metre per second. The
Standard-versus-Quality difference remains a numerical-quality diagnostic,
with the back surface most sensitive; it is not redefined as production
acceptance.

The immutable calibration provenance is:

- repository revision: `3a35cc3a8d143e624a468049344ed15676d791e8`
- configuration SHA-256:
  `d388d6293a94102232f5211dd8bb0b942b401ea30794a63c9f36d8920ae4d9b0`
- neutral source: `neutral-uniform-upper-hemisphere-v1`
- source-definition SHA-256:
  `cfe8b701186ef8c2cd49ae8d8e9783968353cbc9383e47e3674b8909bb3e6530`
- calibration-scene SHA-256:
  `a3bf55f2e2755ee74a28c6285d8b2408ead18ca3c416487988b238debe784d32`
- topology SHA-256:
  `b199a43c98aa899657aae4fbda0b04fee72290d05636aa39d51240598c95fa09`
- receiver SHA-256:
  `e9631120fea1672e2047e0db7fdb8297ca9e4a9ba89cd27b947dd89af99038fc`
- material-plan SHA-256:
  `c5d829ecdbde2b06602468e187fd75c6314f55e671b2d5529289d40adb4ccff6`

The packaged front calibration input is
`src/fspm_optics/resources/calibration/surface-flux-front-local-patch-calibration.v1.json`,
with SHA-256
`6f22972a6ffe41db494f464abafdcad85bc4434bf7c6e326ebabfef6c1008bd3`.
It is copied verbatim; it is not regenerated, refit, rounded, or smoothed at
runtime. Each family and front metric contains exactly 192 binary64
coefficients in canonical `local_patch_index` order `0..191`:

| Family | Front metric | Float64 little-endian SHA-256 |
| --- | --- | --- |
| Standard | Incident PAR | `a0216419e067bedaa20d54f3dbbb4c9ea375aa0ed5198e3e286d036144a925ec` |
| Standard | Absorbed PAR | `f4b79e943efd58e07b847b1c3c51ecc8490c4af483c131918e6b00eb46c3a1fb` |
| Quality | Incident PAR | `725d4b6ae9570fbafc4e01c745e120123325949d9c349230730149904ce34c41` |
| Quality | Absorbed PAR | `98b5c1b07067f407e56c8ddd39015211822625fc8f522be480bcbfd47e86939f` |

The combined order is Standard incident, Standard absorbed, Quality incident,
then Quality absorbed. Its Float64 little-endian SHA-256 is
`b1e3cec31fc4b4e81ce03fd88291edaafd8bb6a717a4d228e57c60d61bd21a07`.

For each local patch, the Standard profile is a five-level through-origin fit
of the plant-mean neutral response against achieved Stage A reference. The
Quality profile is the plant-mean response from the fixed Quality-500 neutral
repeat divided by its achieved Stage A reference. The mean is taken across the
64 plants for the same local patch. It is deliberately not position-specific:
one `gamma[k]` is reused for that local patch on every plant.

Scalar beta values remain calibration provenance:

| Standard surface and metric | Beta |
| --- | ---: |
| Front incident PAR | 0.4510905803216862 |
| Back incident PAR | 0.058044697174435056 |
| Front absorbed PAR | 0.3657821501976058 |
| Back absorbed PAR | 0.04670307023408276 |

| Quality surface and metric | Beta |
| --- | ---: |
| Front incident PAR | 0.448166736538762 |
| Back incident PAR | 0.06098285511211222 |
| Front absorbed PAR | 0.3632938753333456 |
| Back absorbed PAR | 0.04909664459054215 |

V2 does not use front beta for coloring. Back beta remains operational for the
unchanged back normalization. Authenticated v1 publications may retain their
original scalar behavior; a v1 artifact is never interpreted as v2
local-patch-normalized metadata.

The profiles are never averaged. Direct uses the Standard family as an
explicit proxy; Standard uses Standard without a proxy; Quality uses Quality
without a proxy; and Rigorous uses the Quality family as an explicit proxy.
Every published view names the evaluated quality, selected profile, calibration
source quality, family, proxy flag, and proxy rationale. Unknown or missing
quality names have no fallback profile and leave the plant neutral. Lighting
system identity is not an input to profile selection.

Operationally, Direct and Standard are development/iteration modes, Quality is
the normal presentation and scientific-demonstration mode, and Rigorous
remains supported without being recommended for routine execution. The
Rigorous proxy is supported by prior Quality-versus-Rigorous convergence
evidence. No new Rigorous calibration or evaluated simulation is required to
disclose its Quality-family proxy.

## Display variable, Stage A reference, and metrics

Let `p` be `plant_index`, `k` be canonical `local_patch_index` `0..191`, and
`m` be front incident or front absorbed PAR. For authoritative Phase 27G-C
density `q`, selected quality-family coefficient `gamma`, and Stage A reference
`R`, the v2 front display variable is:

```text
u[p,k,m] = q[p,k,m] / (gamma[family,m,k] * R)
```

`u = 1` means expected-equivalent exposure for the same canonical local patch
under the fixed neutral upper-hemisphere calibration. It is not a leaf target
and does not claim raw-q uniformity, target compliance, a transport correction,
or a predicted biological response.

The unchanged back display variable remains:

```text
z[p,k,m] = q[p,k,m] / (beta[family,back,m] * R)
```

The scalar beta previously caused repeated, morphology-driven colors because
it removed only a surface-wide mean response. Fixed leaf orientation,
self-shading, and canonical receiver response remained visible at the same
local patches on every plant. `gamma[k]` removes that fixed local morphology
response. Averaging each coefficient across plants, instead of calibrating a
separate coefficient for every global patch, preserves plant-position,
occlusion, and directional-lighting differences. In particular, it does not
erase the Conventional perimeter/interior distinction.

Reference selection is based only on generic operating-policy metadata:

- target-controlled operation uses the requested Stage A target PPFD;
- fixed-output operation uses achieved plant-free Stage A mean PPFD.

The value, units, requested-or-achieved status, operating policy, source stage,
artifact, and source field are published. Missing, nonfinite, nonpositive,
ambiguous, or contradictory reference metadata is rejected. No lighting-system
name controls reference selection.

The only selectable metrics are Incident PAR and Absorbed PAR. PAR is the
ordered sum of blue, green, orange, and red. Far-red remains a separate Phase
27G-C scientific group and is not read into the display payload.

## Phase 27G-D4 leaf-material interaction

Profile schema 3 adds viewer-only leaf-local smooth normals and exact
structured leaf UVs without changing any Stage C value, patch assignment,
palette anchor, receiver, or calibration resource. The D4 material computes
neutral leaf masks first. On each selected surface-flux side, the existing
`gl_FrontFacing` branch then assigns the authenticated palette result directly
as the diffuse base; neutral green and leaf-rank lightness never multiply that
color. A grayscale vein modulation is capped at 1%, roughness detail is
restrained, and cosmetic normal detail is capped at 0.5 degrees. An unselected
side retains its neutral front or underside treatment.

The implementation remains one canonical `THREE.InstancedMesh`, one
`MeshStandardMaterial`, one plant shader program, one patch texture, and one
plant draw. `_PATCH_INDEX`, `gl_InstanceID`, texture addressing, Both/Front/Back
modes, legends, and fail-closed validation are unchanged. The surface-flux
controller wraps the leaf hook and restores it on cleanup before releasing its
texture and CPU display arrays.

Receiver samples and scientific normal arrows suppress cosmetic leaf-normal
perturbation through a shared uniform. This does not alter the smooth GLB
normal, procedural color or roughness masks, receiver locations, or scientific
arrow directions. These details are display policy only and are not calibrated
botanical structure or transport input.

## Side-specific fixed palettes, legends, and clipping

V2 metadata carries separate authenticated front and back palettes. The front
palette is immutable and shared by both front metrics, all lighting systems,
and all quality families:

| Front anchor `u` | Color |
| ---: | --- |
| 0.00 | `#2563EB` |
| 0.59 | `#06B6D4` |
| 0.81 | `#14B8A6` |
| 0.89 | `#22C55E` |
| 1.00 | `#22C55E` |
| 1.14 | `#22C55E` |
| 1.52 | `#F59E0B` |
| 2.84 | `#DC2626` |

The interval `0.89..1.14` is a fixed green plateau. The calibration-only
envelope uses outward-rounded minimum p1, p5, and p10 values for `0.59`, `0.81`,
and `0.89`, and outward-rounded maximum p95, p99, and p99.9 values for `1.14`,
`1.52`, and `2.84`. Held-out system data, run extrema, targets, means, and
clipping rates did not choose them.

The back palette remains the v1 palette: anchors
`0, 0.025, 0.050, 0.14, 0.42, 1, 2.75, 5.33` with blue `#2563EB`, cyan
`#06B6D4`, teal `#14B8A6`, two green `#22C55E` stops, yellow-green `#A3E635`,
orange `#F59E0B`, and red `#DC2626`. The new front anchors or colors are never
silently applied to the back.

A single physical-q threshold does not exist for a front anchor. For each
local patch it is:

```text
q_anchor[k] = u_anchor * gamma[family,m,k] * R
```

Front metadata publishes this patch-specific rule and may also publish the
minimum and maximum physical threshold across the 192 coefficients. It must
not present one scalar q value as the front threshold. Back legends retain the
scalar `q_anchor = z_anchor * beta * R` threshold behavior.

Each legend publishes metric, side, evaluated quality, selected profile and
proxy provenance, profile hashes, R and its policy, raw-q minimum and maximum,
physical units, threshold semantics, and clipping counts plus physical area
and area fraction. Clipping area uses canonical one-sided patch areas. Counts
and physical areas remain separate quantities. Saturation changes only the
displayed color; it does not clamp or replace raw q or the derived display
array.

## Scientific and GPU artifact boundary

Publication revalidates the run-scoped Phase 27G-C aggregation and Phase 27G-B
transport metadata, metadata hashes, compact receiver index, raw patch-table
hash, fixed Float64 schema, canonical ordering, global counts, topology and
receiver hashes, material authorities, source-state link, Natural-fit layout
identity, quality identity, and front/back availability. It also revalidates
the selected profile, all 192-coefficient arrays, individual and combined
hashes, coefficient order, topology, side-specific palettes, and raw values.
Promotion independently rebuilds the raw Float32 derivative and all four
legends from the authenticated Phase 27G-C Float64 patch table before accepting
the bundle.
Any negative, nonfinite, reordered, truncated, extra, mismatched, or
Float32-unrepresentable value rejects publication.

New publications emit `fspm-optics.surface-flux-display` schema version 2.
The scene manifest authenticates the v2 metadata, and the metadata
authenticates the raw Float32 q buffer and embedded selected gamma arrays.
Existing authenticated schema-v1 artifacts may retain their scalar behavior.
Invalid, incomplete, mismatched, or unauthenticated v2 metadata fails closed:
the browser clears any prior surface texture, preserves neutral plant material,
and displays a diagnostic. Partially trusted coloring is never shown.

The published Float32 artifact retains these raw-q channels and order:

```text
front_incident_par
back_incident_par
front_absorbed_par
back_absorbed_par
```

After authenticating the untouched raw bytes, the browser preserves a raw
array for physical-q inspection and derives one same-sized display array. For
each plant and local patch, its front channels contain `u`; its back channels
contain the unchanged scalar `z`. That derived array alone is uploaded to the
single RGBA32F texture.

The existing canonical plant GLB and `THREE.InstancedMesh` remain unchanged.
The texture has width 192 local patches, height equal to plant count, and one
RGBA texel per global patch, where:

```text
global_patch = 192 * plant_index + local_patch_index
```

The vertex shader uses the existing Float32 `_PATCH_INDEX` binding and
`gl_InstanceID`. The fragment shader uses `gl_FrontFacing` to select the
correct derived variable and side-specific palette. Both, Front only, and Back
only work without duplicating geometry. A bounded shader hook extends the
existing `MeshStandardMaterial`, so the GLB, PMREM, lighting, picking, camera,
fixtures, receiver overlays, and one-draw plant instancing remain intact.
Rerun or teardown clears both raw and derived CPU arrays, disposes the texture,
removes shader hooks and references, aborts UI event handlers, and clears the
active controller.

## Acceptance evidence and limits

The following neutral-calibration green fractions are preserved as acceptance
evidence for the fixed front palette:

| Neutral calibration | Patches in `u=0.89..1.14` |
| --- | ---: |
| Standard incident PAR | approximately 85.53% |
| Standard absorbed PAR | approximately 85.48% |
| Quality incident PAR | approximately 95.21% |
| Quality absorbed PAR | approximately 95.24% |

Proposed and Conventional Standard-500 results were held out from profile and
palette fitting. They are documentation and acceptance evidence only; they are
not packaged run inputs, runtime dependencies, or system-specific selection
branches:

| Held-out incident evidence | Proposed Standard-500 | Conventional Standard-500 |
| --- | ---: | ---: |
| Green `u=0.89..1.14` | approximately 53.65% | approximately 20.04% |
| Below `u=0.81` | approximately 21.47% | approximately 43.89% |
| Above `u=1.14` | approximately 15.14% | approximately 30.85% |
| Plant-mean range | approximately 0.960–1.098 | approximately 0.419–1.508 |
| Plant means in green plateau | 100% | approximately 12.5% |

These percentages are topology-slot or patch-count fractions from the
authoritative calibration input because one-sided area bytes were not part of
the supplied acceptance archive. Published clipping statistics are separately
recomputed with canonical one-sided patch areas. The held-out evidence confirms
that local-patch normalization retains the Conventional pattern: perimeter
plants remain cool while interior hotspots remain warm.

The calibration field is an open-boundary, uniform upper-hemisphere reference,
not a room transport reconstruction. The coefficients describe the frozen
juvenile topology, receiver construction, and stated calibration material
plan. Evaluated runs retain their own hashed band-material authorities; D2
validates and discloses those authorities but does not claim that calibration
removes source-spectrum, material, room-boundary, or numerical-quality effects.
Quality-family proxies are disclosed rather than silently extrapolated.

Standard and Quality are the calibrated families. Direct and Rigorous are
explicit operational proxies. The front model does not claim to remove source
spectrum, material, open-boundary, room-boundary, occlusion, directional, or
numerical-quality effects. Backside local-patch calibration is deferred.

## Historical evidence boundary

The three approved read-only Phase 27G-D snapshot files were consulted only
after their required byte lengths and SHA-256 hashes were verified. Useful
forensic concepts were the blue-to-red palette, explicit front/back surface
distinction, textual legends, and the need to expose unavailable data.

Historical target-normalized anchors, target-capped quantities, fallback
target discovery, per-run scale substitution, CPU face/triangle duplication,
per-leaf/per-plant material construction, disconnected basic materials, and
approximate face matching were rejected. They disagree with the Phase 27G-C
artifact authority, neutral calibration equation, immutable fixed anchors,
canonical patch identity, bounded GPU design, and current PBR viewer contracts.
No snapshot code was copied, imported, packaged, or made a runtime dependency.

## User-run validation

No validation command is implied to have run merely because the documentation
or implementation changed. From the repository root, the user can run:

```text
.venv/bin/python -m pytest \
  tests/test_phase27g_b_analysis_scope.py \
  tests/test_phase27g_b_multispectral.py \
  tests/test_phase27g_c_scientific_aggregation.py \
  tests/test_phase27g_d1_surface_flux_calibration.py \
  tests/test_phase27g_d2_surface_flux_display.py \
  tests/test_import_boundaries.py \
  tests/test_juvenile_viewer_artifacts.py
npm run test:viewer
```

For the complete Python regression suite, run:

```text
.venv/bin/python -m pytest
```

## Manual browser checks

Serve each completed viewer bundle from its parent directory rather than
opening `index.html` through a `file:` URL. For example:

```text
python3 -m http.server 8000 --directory /absolute/external/viewer
```

The following is the retained historical/internal regression procedure; it
requires an instrumented regression page because these selectors are absent
from the public UI. Open `http://127.0.0.1:8000/` and perform the checks on
authenticated Standard and Quality runs:

1. Confirm the panel reports metadata schema v2, raw-q units and range, the R
   value and policy, the selected profile and hashes, proxy status, and the
   retained D1 `complete_fail` status. Standard selects Standard without a
   proxy; Quality selects Quality without a proxy.
2. Select Incident PAR and Absorbed PAR in Both, Front only, and Back only.
   Front legends must use `u`, the `0.89..1.14` green plateau, and the
   patch-specific q-threshold rule. Back legends must retain `z`, the old
   anchors, and scalar physical-q thresholds. In Both mode, the visible face
   must use the correct side palette.
3. Confirm switching metrics or sides does not move plants, duplicate
   geometry, change fixtures, reset the camera, break picking, or alter receiver
   overlays. The performance panel should continue to report one plant draw
   and one bounded RGBA32F surface-flux texture.
4. On comparable Proposed and Conventional Standard-500 bundles, confirm the
   Proposed front is comparatively concentrated around green while the
   Conventional view retains cooler perimeter plants and warmer interior
   hotspots. Do not refit anchors or coefficients to either view.
5. In a disposable copy, corrupt or remove the v2 metadata or raw-q artifact.
   Reloading must show a diagnostic and neutral plant material with no stale
   surface coloring. Restore the authenticated files before further checks.
