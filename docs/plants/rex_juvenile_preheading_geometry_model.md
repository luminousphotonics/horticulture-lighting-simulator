# Rex juvenile pre-heading static geometry model

## Purpose and identity

`rex_juvenile_preheading_12leaf_v1` is a deterministic procedural reference
plant for development and comparative lighting-system analysis. It represents
Rex butterhead lettuce at an approximate age of 9 days after transplant, after
transplant establishment and before heading. Its phenological interpretation is
BBCH 19/pre-41.

This profile is a literature-constrained procedural reference geometry. It is
not a measured reconstruction of an individual plant or a measured average Rex
morphology. It is also not a growth-response model: geometry does not respond to
light, absorbed photons, time, environment, or prior state.

## Static architecture

The immutable `RexJuvenilePreheadingConfig` fixes twelve modeled leaves in
oldest-to-youngest rank order:

- ranks 1-5: five large, low, horizontally extended outer leaves;
- ranks 6-9: four smaller, moderately elevated middle leaves; and
- ranks 10-12: three smallest, upright, centrally concentrated inner leaves.

All leaves are positive-area Rex-shaped surfaces generated independently from
stage- and rank-specific parameters. No mature leaf is copied or deleted, and
the inner leaves are not dormant or zero-scale placeholders. Successive ranks
use the established 137.507764-degree golden-angle phyllotactic convention.

Mean length and width decrease from outer to middle to inner cohorts. Mean
elevation increases in the same order. Rank-specific length, width, elevation,
base position, arch, cup, edge curl, tip sag, and margin phase keep every leaf
geometrically distinct while preserving stable rank order.

## Visual QA corrections and shared crown attachment

The first viewer inspection found narrow, near-vertical inner leaves forming a
pointed crown. An initial anti-crown adjustment then produced hanging panels by
forcing short tips far below strong arches. The next parameter correction
flattened those leaves, but used inner `base_height_m` values of 0.027-0.032 m
to recover plant height. Because `base_height_m` is the z component of the
origin added to every generated surface point, that choice translated each
complete inner mesh several centimeters above the outer and middle foliage.
Numerical envelope and inclination checks therefore passed while the leaves
were visibly floating.

The final model treats attachment as a structural contract. Every leaf starts
at vertex zero, which is exactly its world-transform origin, inside one compact
profile-local crown:

- outer attachment radii are 0.0005-0.0007 m and z is 0.0004-0.0010 m;
- middle attachment radii are 0.0008-0.0012 m and z is 0.0015-0.0027 m; and
- inner attachment radii are 0.0002-0.0007 m and z is 0.0034-0.0046 m.

Thus every anchor is within the 0.002 m crown radius and 0-0.005 m crown z
range, and the total anchor-height span is 0.0042 m. These offsets only prevent
exact coplanarity; they do not create separate foliage layers. The proximal fan
triangles of every leaf share their crown anchor, allowing natural overlap
without stems, connector geometry, or filler surfaces.

Base attachment is distinct from intrinsic elevation. The existing elevation,
arch, cup, and tip terms act along normalized midrib position `u`. A progressive
distal-lift term uses `D * u^2 * (3 - 2u)`: it is zero with zero derivative at
the crown and increases smoothly along the attached midrib. Middle ranks use
0.0025-0.0050 m distal lift and inner ranks use 0.009-0.012 m. This supplies a
gradual central height progression while retaining middle elevations of 28-40
degrees, inner elevations of 44-52 degrees, broad inner laminas, low cup, modest
arch, and minor tip sag. No completed leaf is translated or scaled afterward.

Generated-geometry regression protections enforce that:

- all twelve vertex-zero anchors match their world-transform origins and lie
  within the declared crown radius and z range;
- anchor vertical span is at most 0.005 m and every leaf contains geometry at
  or below 0.007 m;
- the projected bounding area of all anchors is at most 0.000016 m2;
- the first two structured midrib segments have bounded lengths and vertical
  rises, comparable lengths, and a tangent cosine of at least 0.75;
- every proximal tip fan remains incident on its crown anchor;
- every leaf triangle graph is one connected, positive-area surface;
- inner crest-to-tip drop is at most 0.008 m and the distal midrib retains its
  outward, non-hooking progression;
- central inner-base concentration and moderate deterministic tip-height
  variation remain protected; and
- cohort mean elevation remains monotonically increasing without large jumps.

These limits are visual and procedural safeguards for this fixed reference
geometry. They are not measured biological thresholds or population-level
claims about Rex morphology.

## Procedural envelope

The declared projected canopy diameter target is 0.18 m, with a deterministic
acceptable envelope of 0.17-0.19 m. The final procedural plant-height target is
approximately 0.065 m, with an acceptable envelope of 0.055-0.075 m. Geometry
determines this height through attached leaf curvature; the model does not
translate crown anchors upward to force the upper limit. These are design
targets for a procedural reference, not claims of measured mean Rex morphology.

The envelope emerges directly from the juvenile rank parameters. The generator
does not post-scale, clamp, cap, or normalize the plant or any leaf. Each leaf's
world-transform scale is exactly one and its vertical offset is zero.

## Scientific mesh, sampling profiles, and ordering

Morphology identity and surface-sampling identity are separate. The immutable
vertices, triangle indices, face IDs, winding, and face order remain under
`rex_juvenile_preheading_12leaf_v1`. New construction defaults to
`rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1`; exact D2 replay
uses `rex_juvenile_surface_sampling_uv_quarter_4x4_v1` explicitly.

Every leaf retains 16 physical patches in `4 * u_band + v_band` order. Outer and middle leaves use a
12 by 8 parametric tessellation and produce 176 triangles each. Inner leaves use
an 8 by 8 tessellation and produce 112 triangles each. The resulting fixed
contract is:

- 12 leaves;
- 1,920 triangular faces (`9 * 176 + 3 * 112`);
- 192 physical patches (`12 * 16`); and
- 384 ordered front/back receivers (`192 * 2`).

Canonical ordering is leaf rank, face index within leaf, patch `u` then patch
`v`, and front receiver then back receiver. The profile ID, plant ID, and all
leaf, face, patch, and receiver IDs are profile-specific and deterministic.
Identical configurations therefore produce identical geometry, identities, and
ordering.

The optimized profile uses declared, cell-aligned boundaries; no optimizer runs
during generation. All leaves use lateral `v` cuts `(2, 4, 6)`. Longitudinal
`u` cuts are `(4, 6, 8)` for ranks 1-5, `(3, 5, 8)` for ranks 6-8,
`(3, 5, 7)` for rank 9, `(2, 3, 5)` for ranks 10 and 12, and `(2, 3, 4)`
for rank 11. Cells are indivisible, every face belongs to exactly one patch,
and regrouping preserves each leaf's complete one-sided triangle area.

Patch area, area-weighted centroid, and area-weighted aggregate normal remain
separate aggregate properties. An optimized patch additionally retains the
closest point from its aggregate centroid to its member-triangle union, the
selected carrier face ID, barycentric coordinates, and that triangle's
winding-defined unit normal. The carrier triangle is the authoritative local
piecewise-planar surface. With epsilon `5e-5 m`, optimized origins and
directions are:

```text
front_origin    = surface_anchor + epsilon * carrier_normal
back_origin     = surface_anchor - epsilon * carrier_normal
front_direction = carrier_normal
back_direction  = -carrier_normal
```

Front remains immediately before back. The aggregate patch normal is retained
for analysis but is not used to place optimized receivers. Legacy replay keeps
the original centroid plus/minus area-weighted patch-normal construction.

The optimized topology and receiver hashes are generated from serialized
scientific content and are not golden constants. The legacy replay retains the
frozen topology and receiver hashes required by D2 calibration. Optimized
sampling is deliberately uncalibrated for D2 surface-flux coloring; there is no
fallback from optimized identity to legacy gamma or beta. Raw Stage C density
and physical-area aggregation remain available and unchanged.

## Calibration and optical boundary

No juvenile-specific reflectance, transmittance, or absorptance is introduced.
The geometry's material identifier is deliberately juvenile-specific and has no
embedded optical meaning. Existing mature Rex leaf optics may be applied later
only through an explicit, documented proxy decision; they are not silently
assigned by this profile.

The juvenile generator is separate from the mature generator and is not routed
through mature-only transport planning. Mature defaults, morphology, envelope
normalization, 32-leaf allocation, mesh and receiver counts, geometry ordering,
optics, and transport validation remain unchanged.
