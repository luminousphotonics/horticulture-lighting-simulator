# Receiver-placement measurement diagnostic

Phase 27G-D3-B retains the read-only diagnostic for the
`rex_juvenile_preheading_12leaf_v1` mesh. It measures the current 192 physical
patches and 384 front/back receivers, reports the explicit legacy quarter-grid
comparison, and retains both cell-aligned optimization measurements. It does
not install an optimizer candidate or run transport.

## Measurements

The diagnostic consumes the authoritative generated `PlantMesh` and the
production receiver builder. It does not duplicate the Rex leaf equation. The
public mesh does not expose cell IDs, so the diagnostic reconstructs
cells from the generator's canonical `u`-cell/`v`-cell face emission order. It
then verifies that the installed sampling profile's declared cuts exactly
reproduce every authoritative patch face-ID tuple before reporting results.
The production and diagnostic paths share the same triangle closest-point
utility; the diagnostic no longer owns a second implementation.

For every current patch, the output records physical and leaf-normalized area,
the area-weighted Euclidean centroid, and the closest point on the union of its
triangles. Closest-point selection evaluates every member triangle and resolves
tolerance-equivalent distances with the lowest canonical face ID. The selected
face supplies barycentric coordinates and the local triangle normal used for
the patch-average/local-normal angle.

The same selected triangle defines the signed carrier plane. If `a` is the
closest surface anchor, `n` is that triangle's winding-defined unit normal,
`c` is the current centroid, and `o_front` and `o_back` are the unchanged
receiver origins, the reported signed distances are:

```text
centroid_signed_distance = dot(c       - a, n)
front_signed_distance    = dot(o_front - a, n)
back_signed_distance     = dot(o_back  - a, n)
```

Direction alignment is `dot(direction, n)` for each side. The binary64
side-classification tolerance is an absolute `1e-12 m`: values greater than
`+1e-12 m` are `front_side`, values less than `-1e-12 m` are `back_side`, and
the closed interval between them is `on_plane_within_tolerance`. Correct
bracketing requires the labeled front origin to be `front_side` and the
labeled back origin to be `back_side`. Front direction alignment must be
strictly positive and back alignment strictly negative.

The carrier plane represents the authoritative piecewise-planar carrier
triangle, not an analytic tangent plane for the procedural leaf equation.
Minimum correct-side separation is the smallest correctly classified origin's
positive distance margin from that plane. Maximum wrong-side penetration is
the largest distance by which a strictly wrong-side origin crosses it.

For each current front/back receiver, the output records its unchanged origin
and direction, closest point on the full canonical plant mesh, whether that
face belongs to its own patch, and minimum point clearance to a non-own face.
A segment is also tested from the surface-constrained patch anchor to the
current origin. Contact at the anchor is expected and is separated from
unintended intersections. A nontrivial coplanar segment/triangle case is
reported as unavailable rather than assigned an approximate result.
Bracketing and intersection safety are separate measurements. A segment can
avoid every unintended triangle intersection while both receiver origins are
still on the wrong carrier-plane side; absence of intersections is never used
to infer signed-side correctness.

Per-leaf and plant-wide summaries include patch-area range, min/max ratio, CV,
maximum and RMS normalized-area deviation, centroid-to-surface distances,
normal angles, closest-face ownership counts, intersection counts when all
those measurements are available, measured non-own-face clearance, bracketing
and wrong-side counts, on-plane origin count, origin-level direction failures,
minimum correct-side separation, and maximum wrong-side penetration.

## Cell-aligned candidates

Every existing mesh cell is indivisible. No triangle is split, retriangulated,
or reassigned in production. All candidate regions are nonempty contiguous
cell rectangles in stable `4 * u_band + v_band` order.

The current authoritative layout uses its declared cell cuts. The legacy
comparison separately reports exact quarter-parameter cuts. The global candidate
chooses three `u` cuts and three `v` cuts, with the same lateral cuts in all
four longitudinal bands. The band-adaptive candidate chooses three global `u`
cuts and independently chooses three `v` cuts inside each longitudinal band;
it additionally reports lateral boundary steps between adjacent bands.

Candidates are compared lexicographically with a documented binary64
tolerance of `1e-12`:

1. minimum maximum absolute normalized-area deviation;
2. minimum RMS normalized-area deviation;
3. minimum total absolute displacement from nominal quarter cuts;
4. for the adaptive layout, minimum summed lateral-boundary step change; and
5. lexicographically smallest complete cut-index tuple.

Cut displacement and boundary-step terms use cell-index positions normalized
by the relevant segment count. The JSON also reports raw cut indices, actual
leaf parameters (`u` in `[0, 1]`, `v` in `[-1, 1]`), and `[0, 1]` lateral cell
fractions so those terms are independently reproducible.

For `U` by `V` cells, let `Cu = choose(U - 1, 3)` and
`Cv = choose(V - 1, 3)`. The global search is exhaustive and costs
`O(Cu * Cv * U * V)` with the direct cell sums used here. For fixed global
`u` cuts, the first three adaptive objective terms separate by longitudinal
band. The diagnostic filters exact lateral optima and uses a four-stage dynamic
program only for the coupled step-change term. This remains exact for the
ordered objective and costs `O(Cu * (Cv * U * V + Cv^2))`, instead of
enumerating `Cu * Cv^4` complete adaptive layouts. Current closest-point and
segment measurements cost `O(R * F)` for `R` receivers and `F` canonical
faces. These bounds are small for the fixed 12 by 8 and 8 by 8 lattices.

## Interpretation boundary

Unequal patch areas do not make Stage C aggregation incorrect: Stage C uses
each patch's actual physical area, so its weighted sums retain the represented
one-sided leaf area. Area imbalance can still matter to quadrature. One
receiver represents a larger and potentially more variable surface when its
patch is larger, so a single origin and normal may sample that region less
well even while the later area weighting is correct.

Receiver-only movement would keep face-to-patch topology and change
only where an existing patch is sampled. The candidates measured here are
topology-changing regroupings: adopting one would change face membership,
centroids, normals, receiver identities, and hashes, and would require a
separate implementation and scientific validation phase. D3-B installs only
the validated declared regrouping and carrier-constrained receiver profile;
the diagnostic's global and band-adaptive optimizers remain measurement-only.
Lower imbalance alone does not establish transport or quadrature accuracy;
receiver-count convergence and transport comparisons are not run.

## JSON and CLI

The reusable entry point is
`fspm_optics.diagnostics.receiver_placement.build_receiver_placement_diagnostic`.
Its JSON-compatible result contains:

- schema version 3, morphology and sampling profiles, canonical
  topology/receiver hashes, counts, offset
  epsilon, tolerances, carrier-plane contract, and optimization contract;
- ordered per-leaf current patch/receiver measurements and summaries;
- current authoritative, legacy quarter-grid, best global, and best
  band-adaptive regions, cuts, parameter
  boundaries, areas, improvements, coverage, uniqueness, and conservation;
- plant-wide current and candidate summaries; and
- explicit limitations, unperformed measurements, and a false production
  change flag.

Version 3 is required because “current” now means the installed optimized
sampling profile and the legacy quarter grid is a separate named comparison.
All signed carrier-plane fields introduced in version 2 remain required. The
schema ID remains `fspm-optics.receiver-placement-diagnostic`; earlier results
remain distinguishable and are not silently reinterpreted.

The installed command is:

```text
fspm-optics-diagnose-receiver-placement [--compact] [--output PATH]
```

The default is deterministic, key-sorted, finite JSON on stdout and performs
no write. `--output PATH` is the only output-file option. `--compact` removes
indentation without changing the result body. No timestamp, repository path,
temporary path, or generated result file is added to the comparable payload.
