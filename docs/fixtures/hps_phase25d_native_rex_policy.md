# HPS native Rex incident-transport policy

Phase 25D executes the Phase 25B HPS isolated-transport bundle without
changing its spectral budgets, HPS-weighted Rex coefficients, materials,
layout, plant, receiver, or source identities. The default request is a 10 x
10 ft room, four fixtures under `coverage_4ft_center_pitch_v1`, a 0.4572 m
aperture-to-reference mount, the mature 32-leaf Rex plant, standard quality,
and six threads.

The active Phase 3 authority is fixed at 1,750 umol/s initial lamp PAR PPF,
1,045 W tested system input, and `1750 / 1045 = 1.6746411483253588 umol/J`
system PPE. `hps_1000w_spd.csv` supplies relative shape only. The nominal
1,000 W class remains provenance and cannot replace tested system input.

## Native sequence and sources

One Phase 25A unit-downward derived IES is converted once and validated through
the Phase 25C zero-height `flatcorr` contract. The shared DAT is referenced by
one run-root-relative path and retains canonical `source.cal`. Raw converted
geometry is provenance only and never enters a final scene.

The fixed run order is `scalar_par`, `blue`, `green`, `orange`, `red`, then
`far_red`. Scientific run provenance remains exactly 13 `CommandSpec` objects:
one shared `ies2rad`, followed by one complete-scene `oconv` and one `rtrace`
for each run. The shared occlusion pipeline first compiles one authenticated
body shape outside that scientific run sequence. Compilation and tracing use
`hps_rex_transport/` as cwd. Every run has a distinct source, HPS Rex
material, scene, octree, ambient cache, RGB output, and log, while all runs
share the validated DAT, fixture-body instances, and ordered receiver rays.

Each run uses its exact Phase 25B per-fixture PPF and applies `PPF x 179` as an
equal-grey carrier before tracing. There is no post-trace 179 conversion,
spectral scaling, unsupported-mass redistribution, target matching, dimming,
reconciliation, or spatial symmetrization. Four-band PAR is blue + green +
orange + red only; scalar PAR is diagnostic and far-red remains separate.

## Geometry, validation, and metrics

Every compiled scene contains the neutral room, HPS apertures, authenticated
fixture-body instances, and the appropriate HPS-material Rex plant, in that
order. The fixed contract is 32 leaves, 5,248
plant polygons, 512 physical patches, and 1,024 front/back receivers in stable
patch-then-side order. One physical patch area multiplies the sum of its front
and back incident densities once.

The DAT, run source, fixture-body source/shape/octree/identity marker,
plant/material file, receiver rays, and receiver metadata are hash-checked
before every compilation and again before every trace.
Material A/T/R reconstruction is checked before materialization. RGB decoding
requires exactly 1,024 finite, nonnegative, equal-grey rows per run.

After all six runs succeed, the incident summary closes each run with ordered
SHA-256 fields for its source RAD, plant/material RAD, scene manifest, octree,
ambient cache, raw RGB, and decoded NPY. Every one of these final artifacts
must exist and be nonempty before the successful summary is written. Missing
or partial artifacts enter the normal failure path and cannot claim successful
native artifact closure.

Reported diagnostics include front, back, and combined area-weighted means,
extrema, whole-plant incident flux, signed/absolute/relative scalar-versus-four-
band mean differences, and receiver-level RMSE/relative RMSE. No pass threshold
or reconciliation factor is applied.

The receiver decoder, front/back pairing, physical-area calculation,
scalar/four-band diagnostics, and deterministic NPZ writer are source-neutral
and are shared with the established Conventional compatibility APIs. Fixture
science, identities, source plans, and artifact roots remain isolated.
The fixture-occlusion identity is propagated through the bundle, native scene,
octree, ambient cache, execution, incident summary, and absorbed-result
compatibility boundaries. Pre-v4 HPS workspaces therefore fail strict loading.

## Workspace and artifacts

The workspace must be absolute and outside the repository, and the runtime
root must be absent or empty. The CLI is:

```text
fspm-optics hps-rex-transport \
  --workspace /absolute/workspace \
  --room-ft 10 10 \
  --threads 6
```

Artifacts include input identity, Phase 25B bundle and Phase 25D execution-plan
summaries, source/optical/material payloads, shared IES/DAT, room and plant
geometry, receiver rays and metadata, six isolated run directories, command
provenance, logs, `incident_transport_summary.json`, and
`incident_receiver_values.npz`. The NPZ contains six ordered `(1024,)` arrays
for scalar PAR, blue, green, orange, red, and far-red. Failed runs preserve raw
diagnostics and failure provenance but remove aggregate incident summaries,
decoded run arrays, and the incident NPZ. Absorption and plant response remain
out of scope.
