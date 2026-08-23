# Phase 23B Part 1 Conventional spectral and Rex planning policy

Phase 23B Part 1 is a pure scientific planning layer. It recomputes the
Conventional photon source, weights the packaged mature Rex optical profile,
maps those coefficients to the approved diffuse symmetric thin-leaf material,
and plans one scalar-PAR plus five isolated future native runs. It does not
write a runtime workspace, execute Radiance, expose a CLI, or calculate
absorbed metrics.

## Approved SPD and absolute output

`conventional_led_spd.csv` is the sole production Conventional spectral shape. Its
relative radiant samples are converted to photon weights with
`relative_spd × wavelength_nm` and normalized over discrete PAR samples. SPD
amplitude cannot change the normalized fractions. The IES supplies angular
shape and fixture footprint only; its magnitude does not affect spectral
budgets.

The sole rated operating point supplies absolute output:

- 660 W per fixture;
- 2.6 µmol/J PAR PPE;
- 1,716 µmol/s PAR PPF per fixture.

Blue, green, orange, and red remain separate and close to the scalar PAR
anchor. Far-red is expressed relative to PAR but remains outside PAR. Every
isolated source carries its absolute band photon flux before tracing; no
spectral fraction is applied after tracing.

## Conventional-weighted Rex optics

The source-neutral weighting boundary joins the Conventional photon
distribution to the packaged Rex `mean_of_treatments` model on the exact
404–797 nm grid. It uses the modeled absorptance, transmittance, and reflectance
columns without interpolation, extrapolation, missing-wavelength filling, or
renormalization. The implementation records the actual positive source
coverage and unsupported source mass for scalar PAR and each fixed interval.

The approved Conventional SPD has SHA-256
`1820512df69f93d7325b08e27887ab2e8155efa9d536bcd06b9f1c739bf57e44`,
has positive values from 416 through 739 nm, and preserves the missing
740–780 nm digitized tail as explicit zeros rather than extrapolation.
Consequently, no 400–403 nm photon mass is invented when the Rex grid begins at
404 nm. All computed A/T/R triples must be finite, bounded, and close to one
within the existing strict tolerance. Conventional and SMD weighted identities
remain distinct.

## Rex Radiance material model

Every scalar or band interval uses
`diffuse_only_symmetric_thin_leaf_energy_partition`. For source-weighted
`A, T, R`, with `S = R + T`, the seven grayscale `trans` arguments are:

```text
A1 = A2 = A3 = S
A4 = 0
A5 = 0
A6 = T / S
A7 = 0
```

The forward reconstruction must recover the original A/T/R. These parameters
are a diffuse, symmetric, infinitely thin energy-partition approximation. They
are not measured BRDF, BTDF, or BSDF data. The scientific plant uses one leaf
surface only; front and back receivers represent incident hemispheres and are
not coincident front/back polygons.

## Scalar and five isolated plans

The deterministic future run order is:

1. Scalar PAR
2. Blue
3. Green
4. Orange
5. Red
6. Far-red

Scalar PAR exists only to cross-check the sum of the four PAR-band native
results. It must never be added to them. Far-red is separate and must never be
labeled PPFD. Bands are not packed into Radiance RGB; each future run uses equal
grayscale channels.

All six runs share the validated downward Type-C angular DAT identity and the
same ordered 1,024 Rex receivers. Each run has its own absolute source
amplitude, source identity, Rex material, scene, octree identity, ambient-cache
identity, and future output paths. Per fixture, a band uses:

```text
carrier multiplier = band PPF × 179
flat-source correction = carrier multiplier / (1.190 × 1.087)
```

The shared DAT is planned once. No plan requests a separate `ies2rad`
conversion per band. The raw full-box conversion remains provenance only and
is excluded from every final scene.

## Geometry, room, and claim boundaries

The plan reuses one centered mature Rex plant: 32 leaves, 5,248 polygons, a
4×4 patch grid per leaf, 512 physical patches, and 1,024 stable front/back
receivers. One physical patch area applies once to the combined front/back
incident field. Geometry is not clipped, scaled, oversampled, or mutated.

The room uses the production closed six-surface authority: neutral diffuse
0.90 walls and ceiling, and a distinct neutral diffuse 0.10 floor. Both
Radiance plastics have zero specularity and zero roughness. This remains a
wavelength-neutral planning limitation, not a spectral room model. The
Conventional source is one-sided and excludes the original small upward IES
leakage.

The bundle exposes a source-neutral future absorption input tying source,
Conventional-weighted A/T/R, material, band order, and receiver identity
together without pretending to be SMD. Phase 20 execution and artifact writing
remain unchanged and are deferred for this Conventional consumer.

Phase 23B Part 2 owns native `ies2rad`, `oconv`, and `rtrace` execution,
sequential run orchestration, decoded arrays, runtime summaries, and any
absorbed-metrics connection. Part 1 makes no photosynthesis, DLI, growth,
biomass, yield, optimization, or system-comparison claim.
