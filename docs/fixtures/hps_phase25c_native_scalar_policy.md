# HPS native scalar transport policy

Phase 25C executes the fixed-output HPS comparison profile as one isolated
horizontal scalar-PAR transport. The default 10 x 10 ft room uses
`coverage_4ft_center_pitch_v1`: four apertures on a 2 x 2 fixed-pitch grid,
with the aperture plane 0.4572 m above the receiver/reference plane. Each
aperture is the Phase 25A zero-height 0.798576 x 0.603504 m downward rectangle.

## Absolute carrier and native conversion

The derived LM-63 table integrates to one over the downward hemisphere. Its
per-fixture absolute carrier is therefore applied once by `ies2rad` as

```text
1750 umol/s x 179 lm/carrier-W = 313250
```

The IES supplies angular shape and aperture footprint only. There is no lumen
or SPD absolute bridge, post-trace 179 conversion, reconciliation scale,
target match, dimming, or spatial symmetrization. Equal-grey `rtrace` values
decode directly as scalar PPFD.

The native conversion gate accepts exactly the zero-height HPS form: one
`flatcorr` brightdata using the planned DAT and canonical `source.cal`, one
neutral equal-grey light, and one centered downward rectangle with the
expected dimensions and native 0.25 mm guard offset. Extra or substituted
modifiers, CAL/DAT references, source geometry, colors, dimensions, or
orientation fail closed. The raw converted polygon is validation evidence and
never enters the compiled scene. The validated angular/light definition is
rewritten with one run-root-relative DAT reference and reused by all modeled
fixture apertures. DAT presence, nonempty content, structure, hash, resolution,
and exact reference count are checked before `oconv` and again before `rtrace`.

## Command and scene boundary

The scientific command provenance remains one `ies2rad`, one complete-scene
`oconv`, and one `rtrace`, in order. Before the complete scene is compiled, the
shared occlusion pipeline materializes and compiles each unique authenticated
body shape once. All invocations use explicit working directories and streams
with `shell=False`. `oconv` and `rtrace` use the `hps_scalar/` run root so
the relative DAT resolves identically. The scalar scene order is neutral room,
validated HPS angular source/apertures, then authenticated fixture-body
instances. It contains no plant or SMD/Conventional emitter. Standard quality
uses the established horizontal sensor grid; the default run retains 441
ordered samples, a dedicated octree, and a dedicated ambient cache.

HPS and Conventional retain their separate scientific identities and public
executors, while sharing the same source-neutral DAT parser, equal-grey decoder,
scalar metrics calculation, and deterministic NPZ formatter.

The native entry point is:

```text
fspm-optics hps-scalar-transport \
  --workspace /absolute/workspace \
  --room-ft 10 10 \
  --threads 6
```

## Artifacts and claim boundary

The absolute external workspace must have an absent or empty
`hps_scalar/` root. A successful run writes deterministic equivalents of
`input_identity.json`, `source_conversion_summary.json`,
`scalar_transport_summary.json`, `command_provenance_summary.json`, and
`ppfd_values.npz`, plus `source/`, `scene/`, `native/`, and `logs/`. NPZ member
order is `x_m`, `y_m`, `z_m`, then `ppfd_umol_m2_s`. Failures retain native
diagnostics and write `failure_summary.json` without a successful scalar
summary or NPZ result.

The v4 classification hash and fixture-body identity participate in the scene,
octree, ambient-cache, transport, result, and publication identities. Existing
pre-occlusion workspaces cannot resume as equivalent. Source-only identities,
IES/SPD hashes, aperture dimensions, normalization, run order, and output
budgets are unchanged.

The default planned closure is four fixtures, 4,180 W tested system input, and
7,000 umol/s initial lamp PAR PPF. Per fixture, reported system PPE is
`1750 / 1045 = 1.6746411483253588 umol/J`; the nominal 1,000 W lamp class is
provenance only. The
reported mean, minimum, maximum, variation, and minimum-to-mean uniformity are
transport diagnostics, not a scientific pass threshold. Phase 25C makes only
a modeled horizontal scalar-PPFD claim; five-band/far-red execution, plants,
Rex optics, receivers, absorbed metrics, and response claims remain out of
scope.
