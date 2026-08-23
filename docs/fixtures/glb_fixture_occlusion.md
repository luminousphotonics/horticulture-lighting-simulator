# GLB-derived fixture-body occlusion

## Scope and scientific boundary

`glb-fixture-occlusion-v4` makes authenticated Proposed LED, Conventional LED,
and HPS fixture bodies mandatory Radiance geometry in Stage A and Stage
B.

Photometric emission remains separate from external structure:

- Proposed retains its calibrated internal emitter, PMMA, PTFE cavity, bezel,
  and completed-aperture PPF/PPE normalization.
- Conventional retains its authenticated IES angular distribution and
  1716 µmol/s at 660 W (2.6 µmol/J) source boundary, but emits through eight
  GLB-derived bar apertures instead of a full-footprint rectangle.
- HPS retains its authenticated zero-height `0.798576 × 0.603504 m` IES
  aperture, IES angular distribution, SPD, and source placement. The GLB arc
  tube does not replace or derive that source, and the unauthenticated viewer
  glass is not introduced as scientific transport glass.
- External fixture geometry uses the shared
  `fixture_body_anodized_aluminum` Radiance `metal` with RGB reflectance
  `0.70 0.70 0.70`, specularity `0.90`, and roughness `0.10`.
  GLB PBR values never become scientific optical coefficients.

This is the project's deliberate generic reference for anodized extruded
aluminum, not a product-specific claim for any HPS reflector, socket, bracket,
base, or flange. Proposed, Conventional, and HPS use the same material
definition and identity so fixture geometry remains the intended
differentiator. The parameters reuse the completed fixture-body optics audit's
70% metal evidence:

```text
void metal fixture_body_anodized_aluminum
0
0
5 0.70 0.70 0.70 0.90 0.10
```

## Authenticated assets and exact classifications

The active canonical primitive-by-primitive classification is
[`classification.v4.json`](../../src/fspm_optics/resources/data/fixture_occlusion/classification.v4.json).
It records every node path, node and primitive index, accessor and material
index, classification, GLB SHA-256, node-inventory SHA-256, and classification
SHA-256. Any changed GLB byte, registry identity, node inventory, missing
primitive, duplicate primitive key, or changed classification fails closed.

The reviewed inventory counts are:

| Asset | External occluder | Existing-stack duplicate | Decorative exclusion | Emitter |
| --- | ---: | ---: | ---: | ---: |
| `proposed-centerpiece-v1` | 299 | 5 | 4 | 0 |
| `proposed-linear2-v1` | 122 | 2 | 2 | 0 |
| `proposed-linear3-v1` | 173 | 3 | 2 | 0 |
| `proposed-corner3-v1` | 179 | 3 | 2 | 0 |
| `proposed-linear4-v1` | 250 | 4 | 2 | 0 |
| `proposed-l-v1` | 258 | 4 | 4 | 0 |
| `proposed-reverse-l-v1` | 258 | 4 | 4 | 0 |
| `proposed-led-module-v1` | 45 | 1 | 0 | 0 |
| `conventional-led-8-bar-v1` | 55 | 0 | 0 | 8 |
| `hps-housing-v3` | 6 | 1 | 0 | 1 |

For Proposed, the only existing-stack duplicates are the
`opaque_bottom_cover` nodes. The standalone asset has exactly one such node;
its other 45 named components are external occluders. Cover light-side faces
authenticate the physical placement plane, while the separately calibrated
emitter/PMMA/PTFE/bezel stack replaces their optical role. Heat-sink base
plates are physical external occluders. The earlier exclusion of those plates
was a symptom of registering the GLB module-anchor plane, rather than the GLB
light-side plane, to the scientific aperture. The only decorative exclusions
in historical assets are `spec_label` and `logo_plate` surfaces; their
supporting structures remain occluders. The standalone module has no
decorative exclusions. All other Proposed primitives—including heat-sink fins
and bases, backs, frames, drivers, rails, crossmembers, connectors, mounts,
fasteners, and substantial hardware—are external occluders.

For Conventional, the eight exact `led_light_face_1` through
`led_light_face_8` primitives are emitters and are excluded from the body mesh.
All other 55 primitives, including bar bodies, frame, crossmembers, driver,
heat sinks, mounts, and hardware, are external occluders. No GLB primitive is
treated as an internal optical element, and the raw `ies2rad` box is validation
input only.

For HPS, the top box, flared skirt, rear exhaust flange, socket bracket,
ceramic socket, and bulb base are the six external occluders. The
`hps_bulb_glass` primitive is an existing source/optical duplicate because its
viewer alpha of 0.35 has no authenticated Radiance transmission or refraction
coefficients. The `hps_inner_arc_tube` is an emitter exclusion because the IES
source remains authoritative. These eight classifications are exact and
one-to-one; neither excluded primitive contributes a body triangle.

The checked manifest can be verified, or deliberately regenerated after
review, with:

```bash
.venv/bin/python scripts/fixture_occlusion_manifest.py
.venv/bin/python scripts/fixture_occlusion_manifest.py --write
```

The active v4 file is 695,775 bytes with SHA-256
`b63ef558fa612f122992eb155f142cd179cf72aec070e0f4d63d2ef729c5984e`.
Historical
[`classification.v3.json`](../../src/fspm_optics/resources/data/fixture_occlusion/classification.v3.json)
is deliberately retained byte-for-byte at 692,248 bytes and SHA-256
`bdbd6c4ca708bcd446a0535490a642c56a321a8d1f7ef4122fee54a4d15eab7e`.

## Geometry, transforms, and reuse

The strict GLB decoder accepts one embedded glTF 2.0 JSON chunk and one binary
chunk, indexed triangle primitives, non-sparse embedded accessors, and a
strict acyclic scene tree. It resolves node matrices or TRS hierarchy,
millimetre-to-metre scale, handedness, authored axes, registry dimension
corrections, fixture rotation, portrait alignment, and placement.

Transport consumes the exact float32 matrix bytes published to the viewer,
including the viewer matrix SHA-256. It converts those matrices back to the
scientific Z-up frame rather than inferring a second placement. Proposed
horizontal module anchors retain the viewer's 0.25 mm residual gate. Vertical
placement is authenticated independently from the maximum authored local-Y
face of each GLB `opaque_bottom_cover`, which is the outward light-side face
after the shared LED-down reflection. That plane maps to the calibrated
aperture Z with the same viewer matrix, applied exactly once.

The production-default standalone path does not use anchor fitting. Its matrix
contains only the fixed CAD/GLB axis convention, exact `0.001` mm-to-m
conversion, handedness reflection, and translation from the authoritative
module center plus the authenticated cover plane. Validation requires three
equal 0.001 basis lengths, orthogonality, normalized determinant magnitude one,
and the exact absence of fitted scale or shear.

The reviewed local light-side planes and corrections from the former
module-anchor vertical plane are:

| Asset | Light-side local Y (mm) | Corrected vertical registration (mm) |
| --- | ---: | ---: |
| `proposed-centerpiece-v1` | 27.382621983 | 30.929919983 |
| `proposed-linear2-v1` | 27.398212731 | 35.443836731 |
| `proposed-linear3-v1` | 27.388845602 | 30.973621602 |
| `proposed-corner3-v1` | 27.396988109 | 35.435830109 |
| `proposed-linear4-v1` | 27.376385716 | 35.406286716 |
| `proposed-l-v1` | 27.421794448 | 30.979799448 |
| `proposed-reverse-l-v1` | 27.421794448 | 30.979799448 |
| `proposed-led-module-v1` | 27.4 | 27.4 |

The values are GLB-derived interface planes, not fitted offsets. Authentication
fails if the cover count, node classification, common cover plane, registry
plane, or heat-sink-base classification changes.
Conventional full-GLB bounds must agree with the 1.190 × 1.087 × 0.108 m
catalog dimensions within 2 µm; the decoded result is
1.190000052 × 1.087000046 × 0.108000003 m.

HPS uses the catalog matrix without recentering or fitted scale. CAD
millimetres map as `(X,Y,Z)->(X,Z,-Y)`, with exact `0.001` unit conversion,
unit dimension correction, local placement plane `Y=-248.92 mm`, and the
published `[0,+248.92,0] mm` correction. The local plane registers to the
scientific aperture Z while preserving the negative-X rear-flange asymmetry.
Full decoded dimensions are approximately
`0.894897565 × 0.642620026 × 0.248920010 m`.

Each unique `(asset SHA-256, classification SHA-256, linear transform)` body
is compiled once as a Radiance octree. Repeated fixture bodies are placed with
Radiance `instance` primitives using translation only. Emission is never
compiled into these reusable body octrees. A reusable compiled octree is
accepted only with a sidecar identity that binds its byte hash to the derived
mesh-source SHA-256; a missing or mismatched sidecar forces recompilation.

Projected opaque area is calculated from the classified triangle mesh as half
the two-sided horizontal triangle projection. This preserves modeled openings
and is not a bounding-box footprint. It is an additive shell metric, not a
Boolean union: layered or overlapping mesh shells may double-count area, and
open or non-two-sided authoring may make it conservative.

For the default 10 × 10 ft layouts:

| System | Fixture instances | Unique body shapes | Instanced external triangles | Projected opaque area |
| --- | ---: | ---: | ---: | ---: |
| Proposed `standalone_modules` | 61 | 1 | 78,812 | 2.295357666 m² |
| Conventional Practical | 4 | 1 | 14,112 | 2.183304536 m² |
| HPS | 4 | 1 | 16,072 | 2.284843197 m² |

Representative catalog-aligned shape metrics are listed below. Proposed
fixture fitting can produce small transform-specific differences, so each run
publishes its exact full bounds, derived-mesh SHA-256, projected area, and
instance multiplicity.

| Asset | Full GLB dimensions, sorted X/Y × Z | External triangles | Projected opaque area |
| --- | --- | ---: | ---: |
| `proposed-centerpiece-v1` | 0.773119178 × 0.782382632 × 0.087882626 m | 7,973 | 0.466766466 m² |
| `proposed-linear2-v1` | 0.164988160 × 0.744218344 × 0.084265890 m | 4,205 | 0.169030569 m² |
| `proposed-linear3-v1` | 0.222259003 × 1.356657692 × 0.089247248 m | 5,844 | 0.275351240 m² |
| `proposed-corner3-v1` | 0.753486253 × 0.761439127 × 0.089248371 m | 5,970 | 0.306329561 m² |
| `proposed-linear4-v1` | 0.194185926 × 1.879118742 × 0.084262744 m | 9,382 | 0.421377312 m² |
| `proposed-l-v1` | 0.766582756 × 1.358705170 × 0.084281417 m | 9,609 | 0.380093793 m² |
| `proposed-reverse-l-v1` | 0.785930639 × 1.335690025 × 0.084281417 m | 9,620 | 0.383087092 m² |
| `proposed-led-module-v1` | 0.150000007 × 0.150000007 × 0.063000003 m | 1,292 | 0.037628814 m² |
| `conventional-led-8-bar-v1` | 1.087000046 × 1.190000052 × 0.108000003 m | 3,528 | 0.545826134 m² |
| `hps-housing-v3` | 0.894897565 × 0.642620026 × 0.248920010 m | 4,018 | 0.571210799 m² |

The Conventional body has 3,528 external triangles and
0.545826134 m² additive projected opaque area per fixture. Its eight emitter
faces total 0.178222007 m² per fixture. Default Proposed reuses the one
standalone body shape through 61 Radiance instances. Explicit Linear Mode uses
one centerpiece and 28 `linear2` instances at 10 × 10 ft; other Linear and
supported Legacy layouts continue to authenticate `linear3`, `corner3`,
`linear4`, `L`, and `reverse_L` in the same way.
HPS has six body primitives and 4,018 triangles per unique shape. The
default four-fixture layout compiles one shape and places four instances; it
exposes zero GLB-derived emitting boundaries.

## Conventional source reconstruction

The previous 1.190 × 1.087 m whole-footprint `light` polygon is not used.
For each fixture:

1. The lowest coplanar rectangular boundary is extracted from each of the
   eight classified GLB light-face meshes.
2. The authenticated whole-fixture IES angular data are normalized by the
   combined real bar area.
3. Eight parallel `illum` polygons share that normalized `brightdata`
   distribution.
4. Each aperture carries one eighth of the 1716 µmol/s metadata boundary;
   their `math.fsum` closes to 1716 µmol/s per fixture.
5. The emitting area is not part of the reflective body mesh, so self-emission
   is not blocked. Open spaces between bars remain open.

The source-side electrical boundary remains 660 W and 2.6 µmol/J. The
diagnostic aggregate far field is evaluated at 0°, 15°, 30°, 45°, 60°, and
75° from 40 m against the same authenticated IES distribution on a diagnostic
full boundary. The declared maximum relative tolerance is 1%; the current
maximum error is 0.000640058 (0.064006%).

## Pipeline and identity contract

The same `FixtureOcclusionPlan` is used by:

- Proposed uniform Stage A;
- every Proposed basis-column octree, independent of the active source zone;
- Conventional scalar Stage A and legacy Conventional Rex transport;
- HPS scalar Stage A and every isolated spectral baseline;
- Proposed, Conventional, and HPS four/five-band Stage B, including the
  HPS Rex native execution and strict absorption consumer;
- run manifests, source state, transport metadata, cache/workspace identities,
  and viewer-derived provenance.

The identity includes model version, GLB and inventory hashes, classification
hashes, derived Radiance source hashes, material policy, projected area,
primitive and triangle counts, exact viewer matrix hashes, transform-set hash,
and emitting boundaries. Stage B refuses any supported adapter without a body
plan for the matching system. The manifest hash and body identity participate
in scene, octree, ambient-cache, scalar/multispectral run, result, artifact,
and publication provenance. Transport schema/version changes and body identity
participation make pre-v4 HPS workspaces incompatible with current resume
validation while source-only IES/SPD identities remain unchanged.

## Inexpensive validation

The maintained Radiance audit samples all 61 modules in the default 10 × 10
layout at aperture center, four edge midpoints, and four corners. It traces
exact diagnostic fans at 0°, 15°, 30°, 45°, 60°, and 75°, plus complete
15-degree azimuth quadrature over six polar rings spanning the downward
hemisphere. Native Lambertian PPF weights use the exact
`cos(theta) sin(theta)` integral per ring and tensor-product Simpson aperture
weights. Every ray records first-hit distance, Radiance object/modifier, GLB
asset, primitive, and node path.

The standalone CAD places one fastener at each 126 mm aperture corner. The
native area-weighted audit therefore requires an exact blocked fraction of
`1/9` (11.111111%), split equally as `1/36` across the four authenticated
`module_screw_01` through `module_screw_04` node paths. Center and edge
positions remain clear. This is a fail-closed attribution check, not a relaxed
generic blockage threshold; any other first-hit node or changed fraction
fails. Separate support probes also require real body occlusion.

Run the maintained checks without starting the production comparisons:

```bash
.venv/bin/python scripts/fixture_occlusion_manifest.py
.venv/bin/python scripts/audit_fixture_occlusion.py
.venv/bin/python -m pytest -q \
  tests/test_fixture_occlusion.py \
  tests/test_conventional_radiance_source.py \
  tests/test_conventional_scene_planning.py \
  tests/test_conventional_scalar_transport.py \
  tests/test_conventional_rex_planning.py \
  tests/test_conventional_rex_execution.py \
  tests/test_basis_planning.py \
  tests/test_basis_execution.py \
  tests/test_proposed_control_mode.py \
  tests/test_five_band_transport_plan.py \
  tests/test_smd_optical_stack.py \
  tests/test_aperture_ppe_calibration.py
scripts/test-fast
npm run test:viewer
git diff --check
```

The audit compiles real reusable body octrees and verifies:

- Conventional bar and frame rays hit `fixture_body_anodized_aluminum`;
- a real inter-bar opening transmits;
- downward rays on the emitting side transmit;
- all 61 Proposed module direct rays in the default 10 × 10 ft layout transmit;
- a Proposed support ray hits `fixture_body_anodized_aluminum`; and
- the eight-aperture aggregate far field stays within tolerance;
- an HPS luminous-axis-to-rear ray first hits the classified ceramic socket,
  while a ray started in the rear bore passes;
- an HPS straight-up ray hits the sealed top box with
  `fixture_body_anodized_aluminum`, while straight down remains open.

## Exact Quality comparison reruns

These commands reproduce the comparison request contracts without adding a
fixture-occlusion mode. They deliberately use the historical comparison
settings: target 750 µmol/m²/s; 10 × 10 ft with no aisle, Proposed uniform
dimming, and Conventional Practical; 30 × 50 ft with the 2 ft perimeter aisle,
Proposed uniform dimming, and Conventional Rolling Bench. The 30 × 50 settings
match the authenticated archived manifests.

Start the server in terminal 1:

```bash
.venv/bin/fspm-optics-web --keep-runtime
```

In terminal 2, submit each run sequentially and save its terminal response,
manifest, and metrics:

```bash
mkdir -p /tmp/fspm-fixture-occlusion-quality

run_quality () {
  label="$1"
  payload="$2"
  response="$(curl -fsS -X POST http://127.0.0.1:8895/api/runs \
    -H 'Content-Type: application/json' \
    --data "$payload")"
  job_id="$(printf '%s' "$response" | jq -er '.job_id')"
  run_id="$(printf '%s' "$response" | jq -er '.run_id')"
  while :; do
    status="$(curl -fsS "http://127.0.0.1:8895/api/jobs/$job_id")"
    if [ "$(printf '%s' "$status" | jq -r '.terminal')" = true ]; then
      break
    fi
    sleep 5
  done
  printf '%s\n' "$status" |
    tee "/tmp/fspm-fixture-occlusion-quality/$label.status.json"
  test "$(printf '%s' "$status" | jq -r '.state')" = succeeded
  curl -fsS "http://127.0.0.1:8895/api/runs/$run_id/manifest" \
    -o "/tmp/fspm-fixture-occlusion-quality/$label.manifest.json"
  curl -fsS "http://127.0.0.1:8895/api/runs/$run_id/metrics" \
    -o "/tmp/fspm-fixture-occlusion-quality/$label.metrics.json"
}

run_quality proposed-10x10 \
  '{"system":"proposed","target_ppfd":750,"room_length_ft":10,"room_width_ft":10,"quality":"quality","analysis_scope":"baseline_ppfd","fspm_target_mode":"automatic","fspm_target_tolerance":20,"mounting_height_in":18,"aisle_mode":false,"proposed_control_mode":"uniform_module_dimming"}'

run_quality conventional-10x10 \
  '{"system":"conventional","target_ppfd":750,"room_length_ft":10,"room_width_ft":10,"quality":"quality","analysis_scope":"baseline_ppfd","fspm_target_mode":"automatic","fspm_target_tolerance":20,"mounting_height_in":18,"aisle_mode":false,"layout_mode":"practical"}'

run_quality proposed-30x50 \
  '{"system":"proposed","target_ppfd":750,"room_length_ft":30,"room_width_ft":50,"quality":"quality","analysis_scope":"baseline_ppfd","fspm_target_mode":"automatic","fspm_target_tolerance":20,"mounting_height_in":18,"aisle_mode":true,"proposed_control_mode":"uniform_module_dimming"}'

run_quality conventional-30x50 \
  '{"system":"conventional","target_ppfd":750,"room_length_ft":30,"room_width_ft":50,"quality":"quality","analysis_scope":"baseline_ppfd","fspm_target_mode":"automatic","fspm_target_tolerance":20,"mounting_height_in":18,"aisle_mode":true,"layout_mode":"rolling_bench"}'
```

Do not compare new runs with old cached basis or transport artifacts. Compare
only succeeded manifests that declare current fixture-occlusion identities.

## Invalidated results and limitations

The material change advances the scientific model from
`glb-fixture-occlusion-v1` to `glb-fixture-occlusion-v2` and renames the
material identity from `fixture_body_zero_reflectance_absorber_v1` to
`fixture_body_anodized_aluminum_v2`. Prior Proposed and Conventional absorber
results therefore cannot authenticate as reflective-body results. Invalidated
outputs include Stage A PPFD fields,
mean/min/max/uniformity, control schedules and dimming, achieved-target and
modeled-power claims, direct/interreflected decompositions, heatmaps and
viewer overlays derived from those fields, Stage B incident/absorbed
multispectral values and surface aggregation, and cross-system rankings or
percentage comparisons using any affected run.

Intrinsic authenticated source boundaries are not invalidated: Proposed
completed-aperture PPF/PPE, Conventional 1716 µmol/s and 2.6 µmol/J, relative
SPDs, and authenticated IES angular data remain unchanged. HPS transport and
HPS-only results are unchanged, but any cross-system comparison containing an
old Proposed or Conventional result must be rerun.

The shared material is a project reference assumption rather than authenticated
product-specific surface data. The projected-area value remains an auditable
mesh metric, not a measured effective optical cross-section or a unioned
silhouette.
