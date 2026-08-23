# Conventional Radiance source-adapter policy

Phase 22C connects the approved Conventional profile and deterministic fixture
layout to pure Radiance source and scene plans. It does not run `ies2rad`, build
an octree, trace receivers, or establish native validation results.

## Directional staging rather than absolute photometry

The packaged Conventional LED LM-63 file remains unchanged and retains its original hash and
test provenance. A runtime-derived LM-63 document is planned solely so
`ies2rad` can convert the measured directional distribution.

The derived document preserves every vertical angle, every measured C plane,
the asymmetric candela shape, and the exact duplicate 0/360 closure. The
periodic Type-C table is integrated once with the same four-corner cell
trapezoid and exact solid-angle measure used by Phase 22A. Diagnostics retain
the full-sphere, downward 0-90 degree, and upward 90-180 degree integrals and
fractions. A cell crossing 90 degrees is split by linear interpolation in the
same solid-angle coordinate used by the trapezoid.

Every candela value is divided uniformly by the original downward-hemisphere
integral. Deterministic 17-significant-digit formatting is then parsed again by
the strict LM-63 parser, and its downward integral must equal one within
`1e-12`. The full-sphere integral may exceed one. Angles above 90 degrees and
their measured values remain in the derived document unchanged except for the
same uniform scale; they are not folded, mirrored, zeroed, or redistributed.

The derived candela multiplier, ballast factor, 2019 future-use field, and input
watts field are all `1`. Derivation headers explicitly identify it as staging,
record the original resource SHA-256, and state that absolute PAR output is
external. Consequently, the original candela scale, the recorded `1.10000`
field, IES lumens, and the 663.20 W test input cannot calibrate modeled output.

## Exactly-once carrier scaling

Radiance source code defines `WHTEFFICACY = 179 lm/W` for uniform white light.
The canonical `ies2rad` implementation writes candela data with a factor of
`1/WHTEFFICACY`; its `-m` option participates once in the generated source
multiplier. The official manual also defines `-m` as the output-quantity
multiplier. References:

- Radiance `ies2rad.c`, revision 2.38:
  <https://www.radiance-online.org/cgi-bin/viewcvs.cgi/ray/src/cv/ies2rad.c?revision=2.38&view=markup>
- <https://floyd.lbl.gov/radiance/man_html/ies2rad.1.html>

The downward-normalized derived IES represents one transported downward
photometric-flux unit. Radiance therefore converts that domain to `1/179` white
carrier units before the explicit multiplier. To make the equal RGB channels
carry the declared downward fixture scalar PPF directly:

```text
ies2rad_multiplier
  = fixture_ppf_umol_s × WHTEFFICACY
  = 1716 × 179
  = 307164

integrated_equal_channel_carrier
  = 1 × 307164 / 179
  = 1716 umol/s
```

The eventual scalar `rtrace` decoder asserts equal channels and returns their
mean directly. It performs no multiplication or division by 179, no photopic
weighting, no dimming, and no post-trace PPF rescaling.

## `ies2rad` planning and orientation contract

The command plan uses a `CommandSpec` with explicit `cwd`, no redirected stdin
or stdout, and individual argv elements equivalent to:

```text
ies2rad -dm -t default -c 1 1 1 -m 307164 \
  -o conventional_led_unit_downward_flux \
  conventional_led_unit_downward_flux.ies
```

`-t default` makes the explicit neutral `-c 1 1 1` color authoritative. `-o`
fixes the RAD/DAT root. The command consumes only the downward-normalized IES;
the executable token remains injectable through the existing resolution
architecture.

The official `ies2rad` contract centers geometry at the origin, aims it toward
negative Z, aligns the Type-C zero-degree plane and fixture length with X, and
aligns fixture width with Y. The limited converted-output validator accepts only
the expected brightdata modifier, neutral light modifier, `source.cal`, one
deterministic DAT reference, and the centered 1.190 × 1.087 × 0.108 m box. Extra
or unknown emitting primitives fail.

## One-aperture transport approximation

The complete converted box is a temporary validation boundary, not final scene
geometry. Its upward and lateral polygons are never added to a ScenePlan. After
validation, the temporary box `boxcorr` definition is adapted to a clean
`flatcorr` lower-aperture definition. Its single real argument is
`307164 / (1.190 × 1.087)`, matching the canonical flat-source area correction
without retaining projected side-face area.

The clean source representation shares this immutable angular/light definition
and writes exactly one horizontal polygon for each Phase 22B fixture. Each
polygon is 1.190 m along aligned X and 1.087 m along aligned Y, uses the Phase
22B aperture Z directly, and is wound for normal `(0, 0, -1)`. Coordinates are
written explicitly in Python; neither `xform` nor inline Radiance commands are
used.

There are no individual bar sources, housing faces, upward apertures, lateral
emitters, or frame occlusion. Visual eight-bar metadata cannot affect transport.
Every full-output aperture transports exactly 1,716 µmol/s, and an N-fixture
layout transports exactly `N × 1,716 µmol/s`.

The approved table contains small nonzero values above 90 degrees. The
comparison model intentionally excludes that measured upward leakage because a
one-sided downward polygon cannot transport it. Its original flux and fraction
remain deterministic provenance diagnostics and cannot reduce the declared
downward output. This is not a full-sphere reproduction of the tested Conventional LED
fixture and does not permit angles to be altered during staging.

## Scene and claim boundaries

The baseline ScenePlan contains room, shared angular definition, and fixture
apertures, with a separate horizontal sensor-grid receiver input. The FSPM
ScenePlan adds Rex plant geometry and uses Rex plant receivers. Scientific scene
identities include source, room, receiver, and plant identities and derive
distinct future octree and ambient-cache identities without constructing those
artifacts.

The Conventional LED asset supplies measured angular shape, footprint, orientation, and
test provenance. The sole rated authority is 660 W, 2.6 µmol/J, and 1,716
µmol/s; the IES-tested 663.20 W is provenance only. Phase 23 must run native conversion and scalar/five-
band transport, validate native converted output against the contract, generate
Conventional-weighted Rex materials, and validate absorbed metrics. Phase 22C
does not claim native numerical validation.
