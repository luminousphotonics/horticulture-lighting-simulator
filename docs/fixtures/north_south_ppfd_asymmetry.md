# North–south PPFD asymmetry boundary diagnosis

## Conclusion

The first asymmetric production boundary is positive-bounce Radiance ambient
evaluation. It is not the fixture-body material and it is not present in direct
COB transport.

The existing 10 × 10 ft fixture-body audit establishes:

- Proposed no-body `-ab 0` is exactly symmetric under x mirror, y mirror, and
  180° rotation.
- The first nonzero x/y/180° residual occurs in the no-body `-ab 1` field.
- No-body `-ab 5` changes signed upper-minus-lower bias from `-0.3594 PPFD`
  at coarse sampling to `+22.8649 PPFD` at fine sampling.
- All Proposed material/body variants have the same exact direct field.

A minimal receiver-order probe reused the exact no-body room and COB source.
Each trace used the same octree, emitted PPF, `-ab 1`, `-u-`, `nthreads=1`,
and its own initially absent ambient cache. Reversing receiver order changed
the coordinate-aligned result by `18.6918 PPFD RMS` and changed
upper-minus-lower from `-2.9276` to `+1.7642 PPFD`. Mirroring the y coordinates
while retaining the same sample sequence changed the field by
`19.2567 PPFD RMS`.

This demonstrates a finite-sample, receiver-order-dependent ambient estimate.
Fixture reflection can alter its magnitude, but it does not introduce the
directional sign.

## Existing-field boundary table

The rows below are the Proposed cases needed to locate the first boundary.
The generated CSV/JSON report contains all 44 available Proposed and
Conventional `-ab 0`, `-ab 1`, `-ab 2`, and `-ab 5` fields, including extrema
and their coordinates. Signed residual maps store
`field(x,y) - transformed_field(x,y)` for each exact grid transform.

| Field | U−L | R−L | x RMS | y RMS | 180° RMS | 90° RMS |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| No bodies, `-ab 0`, fine | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 5.5437 |
| No bodies, `-ab 1`, fine | -2.9276 | -0.6094 | 19.6122 | 18.5988 | 18.8721 | 20.3959 |
| No bodies, `-ab 2`, fine | -0.8554 | +0.5233 | 21.4914 | 20.7434 | 24.0606 | 23.3338 |
| No bodies, `-ab 5`, coarse | -0.3594 | -7.4437 | 75.4659 | 77.0947 | 75.3763 | 80.2997 |
| No bodies, `-ab 5`, fine | +22.8649 | +3.6384 | 29.2123 | 39.0915 | 40.8322 | 35.7949 |
| Zero absorber, all bodies, `-ab 0` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 5.5437 |
| Zero absorber, all bodies, `-ab 1` | -0.2183 | -0.1177 | 18.8210 | 17.9620 | 19.1787 | 21.0072 |
| Zero absorber, all bodies, `-ab 2` | -3.0475 | -1.9119 | 32.2084 | 33.2231 | 32.4763 | 35.0752 |
| Zero absorber, all bodies, `-ab 5`, coarse | -21.5404 | -11.1141 | 105.7577 | 98.9168 | 107.4663 | 99.4259 |
| Zero absorber, all bodies, `-ab 5`, fine | +2.1940 | -1.6506 | 46.2216 | 49.3098 | 52.4836 | 52.2244 |
| 70% metal, all bodies, `-ab 0` | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 5.5437 |
| 70% metal, all bodies, `-ab 5`, fine | +11.9568 | +0.5275 | 40.9507 | 45.7118 | 47.3597 | 48.8421 |

The direct-field maximum is the exact room center. Its minimum value occurs at
all four tied corners; `argmin` reports the first, southwest corner. Positive
bounce maxima and minima vary by sampling case. Their exact values and
locations are retained in the complete generated report.

## Exact symmetry contracts

| Boundary | Numerical result | Interpretation |
| --- | --- | --- |
| Module centers | x mirror `0 m`; y mirror `0 m`; 180° `0 m`; 90° maximum nearest-center residual `0.008909545 m` | The intended rectangular lattice is mirror/180° symmetric, not 90° symmetric. |
| Module power | 61 modules, one value `100 W`, total `6100 W` | No module power imbalance. |
| COB LES transforms | 61 rings; maximum center mismatch `0 m`; all normals `(0,0,-1)`; all radii `0.011 m` | Every module receives the same source transform. |
| Aperture stacks | Each of 61 modules has the same six PMMA, four bezel, and four PTFE faces | No per-module aperture orientation branch. |
| Source azimuth | One shared `brightdata`, one DAT, one `source.cal src_phi4 src_theta`, and no source `xform`/`instance` | Every COB uses the same global azimuthal orientation. |
| Fixture grouping | Fixture centroids and orientation axes are exact under 180°; they are not x/y/90° symmetric | Body grouping cannot be the first boundary because no-body positive bounce is already asymmetric. |
| Fixture placements | Linear2 placement origins are exact under 180°; all-fixture maximum origin residual is `15.843 µm` from the centered CAD pivot | This is downstream of the demonstrated no-body boundary. |
| Sensor coordinates | 21 × 21; x/y/180°/90° coordinate residual `0 m`; y-major then x-minor, from negative y to positive y | Coordinates are symmetric, but execution order is directional. |
| Room | Bounds `[-1.524,1.524] m` in x/y and `[0,3.048] m` in z; six inward normals exact; one material on all surfaces | No north/south room distinction. |

The authenticated COB DAT has real C0/C90 anisotropy: its maximum symmetric
relative C0/C90 difference through 80° is `12.7946%`. This explains why the
direct field is not exactly 90° symmetric. It cannot explain north–south bias:
`src_phi4` folds opposing azimuths identically under x mirror, y mirror, and
180° rotation (maximum numerical fold residual below `5.7e-14°`).

## Command and ambient-state boundary

The diagnostic direct command uses no ambient state and produces exact
x/y/180° symmetry. The first positive-bounce audit command adds:

```text
-ab 1 -u- -ad 256 -as 64 -aa 0.15 -ar 64 -af <isolated-cache>
```

The production Quality preset uses `-ab 5 -ad 2048 -as 512 -aa 0.12 -ar 96`,
`-u+`, the configured thread count, and an ambient cache shared by receiver
rays within the run. The scene itself does not depend on receiver order.
Finite-sample ambient evaluation does: ray sequence, thread partitioning, and
cache population can change the numerical realization. The one-thread,
deterministic probe proves receiver-order dependence without thread scheduling
or cross-case cache reuse.

## Reproduction

Analyze every existing field without transport:

```bash
.venv/bin/python scripts/analyze_fixture_body_field_symmetry.py \
  --audit-root /home/austin/Documents/fspm-fixture-body-optics-audit-v1-20260729 \
  --output-directory /tmp/fspm-fixture-body-asymmetry-existing-v1
```

Run the minimal first-bounce receiver-order probe:

```bash
.venv/bin/python scripts/probe_fixture_ppfd_receiver_order.py \
  --audit-root /home/austin/Documents/fspm-fixture-body-optics-audit-v1-20260729 \
  --output-directory /tmp/fspm-fixture-body-receiver-order-probe-v1
```

No 180° source-rotation or azimuthally averaged source trace is needed. The
source assembly is already exactly 180° invariant, direct transport is exactly
180° symmetric, and receiver order changes the positive-bounce result with the
physical scene fixed.

## Recommended correction

No production optical or material correction is justified. For symmetry
diagnostics, the smallest correction is to evaluate complementary receiver
orders with independent ambient state and report their coordinate-aligned
average and spread. A production change to sensor ordering, ambient-cache
policy, source shape, layout, or material should require a separate
convergence/repeatability decision.
