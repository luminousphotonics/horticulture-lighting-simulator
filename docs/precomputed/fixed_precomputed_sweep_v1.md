# Fixed precomputed sweep v1

The historical committed-playback authority is one deterministic plan
containing exactly 24 canonical scientific cases. Its immutable identity is:

`114d6171d46a12284c9c7c3de8fae776851dbd7cb806be55b64aa1053d693416`

Current generation resolves live source/resource authorities separately and
may therefore produce a different plan identity. That does not alter this
closed committed-playback contract or any historical case identity.

Every case explicitly authenticates one fixed plan target:
`target_ppfd_umol_m2_s = 250.0`. Target is not a sweep dimension, no target
variants exist, and every case ID and output path contains `ppfd-250`.

For Proposed and Conventional, 250 is the existing mean-target lighting-control
request. HPS still rejects a lighting target and runs only at its
authentic fixed full output. Its case binds 250 as the comparison-plan target
with `fixed_output_comparison_reference_no_dimming` semantics; this does not
invent dimming or imply that HPS achieves 250. All metrics, Final Baseline PPFD
CSV rows, scatter points, and FSPM results retain actual achieved values.

## Canonical rooms and case matrix

The established
`requested_room_to_long_axis_x_rigid_rotation_v1` policy aligns the longer
horizontal dimension to simulation X. The physical/user labels and canonical
domains are:

| Physical label | Exact metric dimensions | Canonical aligned X × Y | Supported request orders |
| --- | --- | --- | --- |
| 10 × 10 ft | 3.048 × 3.048 m | 10 × 10 ft | 10 × 10 |
| 15 × 30 ft | 4.572 × 9.144 m | 30 × 15 ft | 15 × 30, 30 × 15 |
| 30 × 50 ft | 9.144 × 15.24 m | 50 × 30 ft | 30 × 50, 50 × 30 |

The requested dimension order selects a canonical room domain but does not
rotate playback presentation data. Both rectangular orders display the same
canonical landscape X × Y frame, select the same case ID, path, configuration
identity, and compact-bundle identity, and publish identical coordinate-bearing
artifacts. Requested presentation metadata retains the UI dimension order as
provenance and exposes the canonical display dimensions separately. Version 2
of `fspm-optics.requested-orientation-playback` authenticates this no-rotation
presentation contract; it does not change the compact bundle schema or bytes.

Each room has aisle off and aisle on. Aisle state is passed to the current
layout authority; room dimensions are never manually reduced. The groups are:

| Group | Rooms | Aisle states | Count |
| --- | ---: | ---: | ---: |
| Proposed reduced-one-ring | 3 | off/on | 6 |
| Conventional Practical Coverage | 3 | off/on | 6 |
| Conventional Rolling Bench | 3 | off/on | 6 |
| HPS fixed full output | 3 | off/on | 6 |
| **Total** |  |  | **24** |

## Resolved settings

All cases explicitly use Standard, Baseline + Multispectral FSPM, optional
far-red enabled, automatic FSPM target behavior, and the authoritative
75 µmol/m²/s target tolerance. Proposed and Conventional use 18 in (0.4572 m);
HPS uses 24 in (0.6096 m). Proposed uses the native SMD source shape,
standalone modules, uniform-module mean-target dimming (basis-matrix solver
disabled), Conventional LED controlled A/B spectral basis, and Reduced by one
ring. Conventional Practical and Rolling Bench are alternative cases. Existing
fixture, source, transport, room/material, receiver, plant/natural-fit,
occlusion, scene, and normalization authorities are hashed into each case
compatibility identity. A Quality request and its Quality Radiance option tuple
produce different configuration, compatibility, case, binding, and plan
identities, so a Quality-generated bundle cannot validate as any Standard case.

The public playback cases are listed below. Their immutable archived case IDs
remain authenticated by SHA-256 inside the committed-playback contract and are
not exposed through current application responses.

| # | Case ID | Output path |
| ---: | --- | --- |
| 1 | `proposed-10x10-ppfd-250-aisle-off-4d885c0fe70afcb6` | `proposed/proposed_reduced_one_ring/10x10/ppfd-250/aisle-off.fspm-compact` |
| 2 | `proposed-10x10-ppfd-250-aisle-on-de29baaa20bd3be7` | `proposed/proposed_reduced_one_ring/10x10/ppfd-250/aisle-on.fspm-compact` |
| 3 | `proposed-30x15-ppfd-250-aisle-off-02cc1ffde5f6a41b` | `proposed/proposed_reduced_one_ring/30x15/ppfd-250/aisle-off.fspm-compact` |
| 4 | `proposed-30x15-ppfd-250-aisle-on-72fbab3639afc123` | `proposed/proposed_reduced_one_ring/30x15/ppfd-250/aisle-on.fspm-compact` |
| 5 | `proposed-50x30-ppfd-250-aisle-off-2bc67e47980e2497` | `proposed/proposed_reduced_one_ring/50x30/ppfd-250/aisle-off.fspm-compact` |
| 6 | `proposed-50x30-ppfd-250-aisle-on-1fcce32339e280db` | `proposed/proposed_reduced_one_ring/50x30/ppfd-250/aisle-on.fspm-compact` |
| 7 | `conventional-practical-10x10-ppfd-250-aisle-off-5e38082b5b697173` | `conventional/practical/10x10/ppfd-250/aisle-off.fspm-compact` |
| 8 | `conventional-practical-10x10-ppfd-250-aisle-on-933aea01f5ae8bbe` | `conventional/practical/10x10/ppfd-250/aisle-on.fspm-compact` |
| 9 | `conventional-practical-30x15-ppfd-250-aisle-off-d6ef9a1fba3c8d27` | `conventional/practical/30x15/ppfd-250/aisle-off.fspm-compact` |
| 10 | `conventional-practical-30x15-ppfd-250-aisle-on-c54ee9ed58943cf7` | `conventional/practical/30x15/ppfd-250/aisle-on.fspm-compact` |
| 11 | `conventional-practical-50x30-ppfd-250-aisle-off-a1b6a9eae5882ec3` | `conventional/practical/50x30/ppfd-250/aisle-off.fspm-compact` |
| 12 | `conventional-practical-50x30-ppfd-250-aisle-on-b2ec67bf15a6f449` | `conventional/practical/50x30/ppfd-250/aisle-on.fspm-compact` |
| 13 | `conventional-rolling-bench-10x10-ppfd-250-aisle-off-4919a551599735bf` | `conventional/rolling_bench/10x10/ppfd-250/aisle-off.fspm-compact` |
| 14 | `conventional-rolling-bench-10x10-ppfd-250-aisle-on-e8be63b1cb0c4171` | `conventional/rolling_bench/10x10/ppfd-250/aisle-on.fspm-compact` |
| 15 | `conventional-rolling-bench-30x15-ppfd-250-aisle-off-f508d7b26129e200` | `conventional/rolling_bench/30x15/ppfd-250/aisle-off.fspm-compact` |
| 16 | `conventional-rolling-bench-30x15-ppfd-250-aisle-on-2d452ce182fb5e79` | `conventional/rolling_bench/30x15/ppfd-250/aisle-on.fspm-compact` |
| 17 | `conventional-rolling-bench-50x30-ppfd-250-aisle-off-e9776a389eafc647` | `conventional/rolling_bench/50x30/ppfd-250/aisle-off.fspm-compact` |
| 18 | `conventional-rolling-bench-50x30-ppfd-250-aisle-on-7c59902a1a8b9ce8` | `conventional/rolling_bench/50x30/ppfd-250/aisle-on.fspm-compact` |
| 19 | `hps-10x10-ppfd-250-aisle-off-f6ca65661e3d53ed` | `hps/fixed_full_output/10x10/ppfd-250/aisle-off.fspm-compact` |
| 20 | `hps-10x10-ppfd-250-aisle-on-89dd72b4c790890b` | `hps/fixed_full_output/10x10/ppfd-250/aisle-on.fspm-compact` |
| 21 | `hps-30x15-ppfd-250-aisle-off-9d00bca23343813b` | `hps/fixed_full_output/30x15/ppfd-250/aisle-off.fspm-compact` |
| 22 | `hps-30x15-ppfd-250-aisle-on-630a48333dec5ff5` | `hps/fixed_full_output/30x15/ppfd-250/aisle-on.fspm-compact` |
| 23 | `hps-50x30-ppfd-250-aisle-off-636194cd87973a8f` | `hps/fixed_full_output/50x30/ppfd-250/aisle-off.fspm-compact` |
| 24 | `hps-50x30-ppfd-250-aisle-on-a7008a97befee70d` | `hps/fixed_full_output/50x30/ppfd-250/aisle-on.fspm-compact` |

## Safe inspection, status, and verification

Plan-only inspection resolves the complete configuration and prints target 250
for every row. It does not import or initialize the native worker, create a
runtime, create an output directory, export a bundle, or alter existing data:

```bash
PYTHONPATH=src ./.venv/bin/python -m fspm_optics.precomputed_cli plan
PYTHONPATH=src ./.venv/bin/python -m fspm_optics.precomputed_cli plan --json
PYTHONPATH=src ./.venv/bin/python -m fspm_optics.precomputed_cli plan \
  --output-root /absolute/path/to/precomputed
```

Fresh status and final verification are:

```bash
PYTHONPATH=src ./.venv/bin/python -m fspm_optics.precomputed_cli status \
  --output-root /absolute/path/to/precomputed
PYTHONPATH=src ./.venv/bin/python -m fspm_optics.precomputed_cli verify \
  --output-root /absolute/path/to/precomputed
```

`verify` exits nonzero unless all 24 bundles freshly pass schema, exact
canonical configuration, fixed-plan/case identities, compatibility identities,
payload inventory, byte sizes, payload hashes, and overall bundle identity.
Missing means pending. Partial, corrupt, incompatible, configuration-mismatched,
and identity-mismatched bundles are invalid and are never skipped.

## Execution and resume

The following is the production command for the user to run after review. It
was documented but was not executed while implementing this plan:

```bash
PYTHONPATH=src ./.venv/bin/python -m fspm_optics.precomputed_cli execute \
  --output-root /absolute/path/to/precomputed \
  --runtime-root /absolute/path/to/managed-runtime \
  --authorize-native-execution
```

Execution is impossible without the explicit `execute` subcommand and
`--authorize-native-execution`. Preflight verifies the exact plan, safe output
root, native executables, packaged fixture assets, and sequential concurrency
of one before lazily initializing the existing bounded worker. The managed
runtime cleanup policy is unchanged.

After interruption or power loss, run the identical command. Every destination
is freshly revalidated; a bundle atomically published just before interruption
is discovered and skipped even if the progress file is absent or stale. The
progress file is informational only. Missing/invalid cases are retried and only
exact valid bundles are skipped. The compact exporter continues using a unique
case-specific sibling partial file and atomic replacement.

Durable logs do not participate in any authenticated identity. For example,
with Bash pipeline failure propagation enabled:

```bash
set -o pipefail
PYTHONPATH=src ./.venv/bin/python -m fspm_optics.precomputed_cli execute \
  --output-root /absolute/path/to/precomputed \
  --runtime-root /absolute/path/to/managed-runtime \
  --authorize-native-execution 2>&1 | tee -a /absolute/path/to/fixed-sweep.log
```

## Playback proof and capabilities

The catalog resolver requires the full typed configuration and canonicalizes
only ordered room dimensions. Proposed and Conventional may vary only their
playback target; every such request still selects the exact matching 250 bundle
and does not create a target case, path, configuration identity, or plan
variant. A near-match system, aisle, Conventional variant, Proposed mode,
height, quality, scope, far-red state, or compatibility identity is unavailable
rather than approximated. HPS exposes no target-adjustment capability and
rejects every target override. Reversed dimension order does not transform
sensor/grid samples, CSV or scatter coordinates/order, grid axes/extents,
room/camera bounds, fixture matrices (including the asymmetric HPS housing),
Proposed alignment geometry, Conventional layouts, plants, leaf
positions/normals, Target Coverage, or viewer framing. Linear target adjustment
is applied to the canonical LED payload, so both request orders share the base
bundle identity and target-derived playback identity while retaining distinct
request-order provenance.

The versioned `fspm-optics.precomputed-linear-target-adjustment` v1 contract
uses authenticated base values:

```text
desired_scale = requested_target / base_achieved_mean
maximum_scale = 1.0 / base_output_fraction
applied_scale = min(desired_scale, maximum_scale)
achieved_mean = base_achieved_mean * applied_scale
```

The exact 250 request reuses every base artifact without rescaling. A finite,
non-negative request below capacity produces a linearly adjusted view. A
request above capacity succeeds at output fraction 1.0, retains the higher
requested target, reports the lower achieved mean, sets `output_limited`, and
uses the stable reason `maximum_output`. Zero succeeds with zero effective
PPFD, PPF, electrical power, and absorbed-light quantities. Undefined
zero-output spatial ratios deterministically use zero; leaf-position CV uses
the existing unavailable/null `leaf_position_ppfd_mean_zero` convention.

Stage A Float64 values are the derived numeric authority for CSV, scatter,
heatmaps, and Target Coverage. Intensity, variance (by scale squared), aggregate
PAR/far-red, absorbed light, emitted PPF, and effective power are adjusted;
geometry, transforms, CV/uniformity ratios, spectral fractions, capture
efficiency, applicable per-watt metrics, tolerance 75, and full-output capacity
remain invariant. Target Coverage counts and target-relative summaries are
recomputed against the requested lighting target. Automatic FSPM policy is
re-resolved against actual achieved mean. Each target has a deterministic
derived identity referencing the unchanged base bundle identity.

Loading any final bundle revalidates the compact capability contract for both
heatmaps, Final Baseline PPFD CSV, Validated PPFD Scatter, Target Coverage,
metrics-panel values, aggregate absorbed-light metrics, and optional far-red
aggregates. A missing bundle is reported unavailable/pending and cannot produce
a successful playback response.
