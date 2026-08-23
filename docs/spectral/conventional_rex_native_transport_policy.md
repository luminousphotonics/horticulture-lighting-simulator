# Phase 23B Conventional Rex native incident transport policy

Phase 23B Part 2 executes the committed Part 1 Conventional source, Rex optics,
material, geometry, and receiver plan. It produces incident photon-flux-density
artifacts only. Leaf absorption and transmitted/reflected partitions remain
deferred.

## Shared source conversion

One downward-normalized derived IES is converted once with `ies2rad`. The raw
converted RAD is retained only as provenance and its full-box geometry never
enters a final scene. All six isolated source definitions use `flatcorr`, the
canonical Radiance `source.cal` resource, and the same validated DAT through
the run-root-relative reference:

```text
source/conventional_led_unit_downward_flux.dat
```

The DAT is not copied or regenerated per run. Its existence, nonempty content,
hash, and source reference are checked before every native trace.

## Six isolated native runs

Execution is local, sequential, and fixed in this order: scalar PAR, blue,
green, orange, red, and far-red. Each run has its own source and amplitude,
Conventional-weighted Rex `trans` material, plant RAD, scene identity, octree,
ambient cache, raw RGB output, and decoded receiver array. Bands are not packed
into Radiance RGB; all channels are equal scalar carriers.

Scalar PAR uses 1,716 µmol/s per fixture. Each band uses its committed Part 1
per-fixture photon flux. Source carrier amplitude is `PPF × 179`, and
`flatcorr` divides that carrier by the typed fixture aperture area. No 179
conversion, spectral fraction, dimming, target matching, or reconciliation
scale is applied after tracing.

## Rex materials, geometry, and receivers

Every run writes the committed source-weighted Rex material and validates its
A/T/R reconstruction before `oconv`. The mature plant remains one infinitely
thin surface with 32 leaves, 5,248 polygons, and 512 physical patches. No
coincident front/back leaf polygons are created.

One shared receiver artifact contains 1,024 stable records: one front and one
back incident hemisphere per physical patch. Receiver rays and metadata are
hashed and checked before each trace. A patch's physical area is applied once
to its combined front-plus-back incident field; it is not duplicated as two
physical leaf areas.

## Incident artifacts and validation

The deterministic incident NPZ contains these receiver-order arrays:

```text
scalar_par_umol_m2_s
blue_umol_m2_s
green_umol_m2_s
orange_umol_m2_s
red_umol_m2_s
far_red_umol_m2_s
```

The summary records front, back, and combined area-weighted incident means,
whole-plant incident photon flux, and extrema for every run. Four-band PAR is
constructed from blue + green + orange + red. Scalar PAR is compared against
that sum through absolute and relative mean differences plus receiver-level
RMSE; scalar is never added to the bands. Far-red remains separate.

No scientific agreement threshold is invented. Differences are reported for
manual evaluation. Structural failures—including receiver, DAT, source,
material, ordering, nonfinite, or command failures—stop execution and produce
a failure summary without a successful incident summary or final NPZ.

The workflow records exactly thirteen shell-free commands: one `ies2rad`, six
`oconv`, and six `rtrace`. Absorbed-photon calculations, system comparisons,
visualization, plant growth, optimization, sweeps, servers, and HPC execution
are outside this phase.
