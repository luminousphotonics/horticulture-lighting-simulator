# Rex Butterhead Lettuce Geometry Model

Status: draft implementation specification  
Target file: `docs/plants/rex_butterhead_geometry_model.md`  
Scope: deterministic plant geometry, receiver mesh, Radiance export, and future render export  
Non-goal: growth dynamics, multispectral transport, or browser visualization

---

## 1. Purpose

This document defines the geometry model for a deterministic Rex butterhead lettuce plant used by `fspm-optics-engine`.

The model must satisfy two requirements at the same time:

1. **Scientific receiver fidelity**  
   The plant mesh must support leaf-level `rtrace -I+` receiver transport with stable identifiers, patch areas, normals, centroids, and front/back receiver samples.

2. **Visual plausibility**  
   The plant should visually read as a mature greenhouse Rex butterhead lettuce head, not a generic pile of leaves, romaine plant, kale plant, spinach cluster, or decorative low-poly asset.

The core rule is:

```text
Python-generated scientific geometry is the source of truth.
```

Any future OBJ, GLB, WebGL, Three.js, or rendered representation must consume geometry derived from the scientific mesh. Rendering must never define the plant geometry used for optical transport.

---

## 2. Biological target

The target plant is a mature greenhouse/hydroponic Rex butterhead lettuce archetype.

Important traits to preserve:

- Compact greenhouse/hydroponic butterhead habit.
- Broad, rounded, soft leaves.
- Loose but coherent head structure.
- A tighter, cupped inner heart.
- Overlapping rosette architecture.
- Well-closed base.
- Thick-looking leaves relative to generic looseleaf lettuce.
- Smooth-to-lightly-ruffled leaf margins.
- Medium-to-dark green outer leaves with lighter inner leaves as a future render/material option.

Rex is treated here as a cultivar-specific archetype, not as a statistically validated digital clone. The first implementation should be deterministic, configurable, and visually/physically credible. It does not need to model genetic variability yet.

---

## 3. Coordinate system

Use a simple right-handed coordinate system:

```text
x = room width axis
y = room length axis
z = vertical axis
```

Plant origin:

```text
(0, 0, 0) = center of plant base / crown
```

Default mature plant envelope:

```text
projected diameter: 0.25–0.32 m
height:             0.12–0.20 m
leaf count:          32 default, configurable 24–40
```

These values are defaults, not hard biological constants. The model must expose them through typed configuration.

---

## 4. Plant architecture

The plant should be built as a layered rosette with three leaf classes.

### 4.1 Outer leaves

Purpose:

- Define the broad butterhead footprint.
- Create realistic self-shading and edge exposure.
- Make the plant visually read as lettuce.

Traits:

```text
count fraction:       35–45% of leaves
relative length:      largest
relative width:       broad, width/length often near or above 0.65
elevation angle:      low to moderate
radial reach:         high
cupping:              mild to moderate
margin waviness:      moderate
edge curl:            mild
```

Outer leaves should spread outward at a low angle, rise gently through the
mid-blade, and show deterministic distal tip sag with only mild edge curl.
Their crown attachment heights should overlap rather than forming a visible
horizontal tier. They should not look like vertical romaine leaves.

### 4.2 Mid leaves

Purpose:

- Fill the body of the head.
- Provide overlapping surfaces and realistic canopy density.
- Bridge the outer rosette and inner heart.

Traits:

```text
count fraction:       35–45% of leaves
relative length:      medium
relative width:       broad
elevation angle:      moderate
radial reach:         medium
cupping:              moderate
margin waviness:      mild to moderate
edge curl:            mild
```

Mid leaves should be more upright and more cupped than outer leaves. Their
elevation and crown height should include enough deterministic variation to
avoid a shelf-like stacked side profile while preserving coherent overlap.

### 4.3 Inner heart leaves

Purpose:

- Create the butterhead center.
- Produce the closed head visual.
- Provide dense self-shadowing in the plant core.

Traits:

```text
count fraction:       15–25% of leaves
relative length:      small
relative width:       broad but compact
elevation angle:      moderate to high
radial reach:         low
cupping:              high
margin waviness:      low
edge curl:            moderate inward curl
```

Inner leaves should spiral tightly around the crown, use stronger cupping and
edge curl, and remain substantially more upright than mid leaves. Together
they should form a visually closed heart without a discrete elevated platform.

---

## 5. Leaf placement model

Use deterministic spiral phyllotaxis with small deterministic variation.

Default angular increment:

```text
golden-angle step ≈ 137.507764 degrees
```

For each leaf index:

```text
theta_i = base_angle + i * golden_angle + deterministic_jitter(seed, i)
```

Radial and vertical placement should depend on layer:

```text
outer leaves: low z, high radial reach
mid leaves:   medium z, medium radial reach
inner leaves: higher z, low radial reach
```

The model should avoid perfect visual symmetry, but all variation must be deterministic from the seed.

Required stable identifiers:

```text
plant_id
leaf_id
leaf_rank
leaf_layer
```

---

## 6. Leaf surface parameterization

Each leaf should be a curved parametric surface, not a flat ellipse.

Use a local leaf coordinate system:

```text
u ∈ [0, 1] along the midrib from base to tip
v ∈ [-1, 1] across the blade from left edge to right edge
```

A reasonable first model:

```text
centerline(u):
  x_local = length * u
  z_local = midrib_arch(u)
  y_local = 0

half_width(u):
  broad near lower/mid leaf
  zero at base
  tapers smoothly toward rounded tip

surface(v, u):
  y_local = v * half_width(u)
  z_local += transverse_cupping(u, v)
  z_local += edge_curl(u, v)
  z_local += margin_waviness(u, v)
```

### 6.1 Width profile

Use a smooth width function, not a sharp triangle.

The width profile should support broad butterhead leaves:

```text
half_width(u) = max_width * sin(pi * u)^shape * asymmetry_adjustment
```

The maximum width should occur before or near mid-length, not at the very tip.

### 6.2 Tip roundness

The leaf tip must be rounded.

Avoid pointed, lanceolate tips unless explicitly modeling a non-butterhead cultivar.

### 6.3 Cupping

Cupping is important visually and optically.

Use transverse curvature:

```text
z_cup ∝ cup_strength * (v^2) * cup_profile(u)
```

Outer leaves: mild cupping.  
Mid leaves: moderate cupping.  
Inner leaves: stronger cupping.

### 6.4 Midrib arch

Use a gentle longitudinal curve:

```text
z_arch ∝ arch_strength * sin(pi * u)
```

Outer leaves may sag near the tip. Inner leaves should generally remain more upright.

### 6.5 Margin waviness

Use low-amplitude sinusoidal edge perturbation concentrated near the margins.

Requirement:

```text
waviness changes outline and normals without creating self-intersecting leaves.
```

Do not use aggressive kale-like frills.

### 6.6 Thickness

The scientific mesh can remain a two-sided surface for transport, but metadata should include an optional nominal thickness.

Default:

```text
nominal_leaf_thickness_m: 0.0005–0.0015
```

Thickness is for future rendering/material work. It should not create a separate solid mesh unless needed later.

---

## 7. Mesh and receiver-patch resolution

The mesh must be fine enough for curvature and receiver sampling, but light enough for local workstation use.

The receiver sampling unit is a **leaf patch**, not an individual triangle.
Each leaf is partitioned in its parametric coordinates into a stable patch
grid:

```text
leaf_patch_grid = (leaf_patch_u, leaf_patch_v)
default leaf_patch_grid = (4, 4)
```

The default therefore produces 16 receiver patches per leaf. The curved mesh
inside each patch may contain multiple deterministic triangles. Those
triangles support curvature and exact polygon export, but they do not create
additional receiver samples. Patch centroid, normal, and area are derived by
area-weighted aggregation of the patch's exact scientific triangles.

For the default mature plant:

```text
32 leaves × 16 patches/leaf = 512 patches
512 patches × front/back    = 1,024 receivers
```

There are no named receiver-density presets. Convergence studies may override
`leaf_patch_u` and `leaf_patch_v` explicitly. Higher-resolution studies must
record that patch grid with their geometry artifacts.

Each patch's underlying cells should be triangulated deterministically.

Requirements:

- No zero-area faces.
- Avoid extremely skinny triangles where practical.
- Face normals must be unit length.
- Face winding must be consistent.
- Leaf-local to world transform must be deterministic.

The public config exposes only `leaf_patch_u` and `leaf_patch_v`. Curvature
tessellation is derived internally from that grid; it is not a second receiver
density control.

---

## 8. Scientific mesh data model

The scientific mesh must include enough information for optical transport, aggregation, validation, and future visualization.

Required records:

### Plant

```text
plant_id
config
seed
leaves
```

### Leaf

```text
plant_id
leaf_id
leaf_rank
leaf_layer
world_transform
vertices
faces
metadata
```

### Triangle face

```text
plant_id
leaf_id
face_id
vertices
centroid
unit_normal
area_m2
leaf_layer
```

Face IDs must be deterministic and stable for a given config and seed.

### Receiver patch

```text
plant_id
leaf_id
patch_id
face_id alias for receiver/aggregation compatibility
triangle_face_ids
centroid
unit_normal
area_m2
leaf_layer
```

Receiver patch IDs must likewise be deterministic. Triangle face IDs and
receiver patch IDs are separate: a patch may contain multiple triangle faces,
but produces exactly one front/back receiver pair.

---

## 9. Receiver sampling model

Receiver samples are derived directly from area-weighted mesh patches.

For each receiver patch:

```text
front receiver:
  point  = centroid + normal * offset
  normal = normal

back receiver:
  point  = centroid - normal * offset
  normal = -normal
```

Default offset:

```text
receiver_normal_offset_m = 1e-5 to 1e-4
```

The offset is to avoid self-intersection. It should be configurable and recorded in artifact metadata.

Required receiver fields:

```text
receiver_id
plant_id
leaf_id
patch_id
face_id
side: front | back
point: x y z
normal: dx dy dz
area_m2
leaf_layer
```

Default policy:

```text
leaf_patch_grid:       4 × 4
patches per leaf:      16
receivers per leaf:    32
receivers per plant:   1,024 for the default 32-leaf plant
```

Required invariants:

- Receiver count equals `2 × patch_count`.
- Front/back receiver normals are exact opposites.
- Front/back receiver areas match.
- Receiver IDs are unique and stable.
- Receiver order is deterministic.

---

## 10. Radiance export

Radiance plant geometry must be exported from the scientific mesh.

Rules:

- Use the exact scientific mesh faces.
- Do not simplify, smooth, or resample geometry during Radiance export.
- Preserve deterministic face ordering.
- Use material names that can be swapped per transport mode.

Initial scalar placeholder material is acceptable for geometry validation, but the final multispectral model must use band-specific diffuse-transmissive leaf material definitions derived from Rex optical properties.

Radiance export should support:

```text
plant polygons only
plant material definition optional
metadata comments optional
```

---

## 11. OBJ debug export

An OBJ exporter is allowed for debugging.

Rules:

- OBJ export must consume the scientific mesh.
- OBJ export must not create or modify geometry.
- OBJ export should preserve face count.
- OBJ export should include group/object names for plant/leaf IDs where practical.
- OBJ export should reference a neighboring MTL file containing one default
  lettuce-green material for visual inspection.
- The OBJ material is not a scientific optical material and must never affect
  receiver geometry or transport properties.
- OBJ export is not a rendering pipeline.

No GLB, Three.js, or browser viewer work belongs in the first implementation phase.

---

## 12. Visual constraints

The plant should visually read as Rex butterhead lettuce.

Required visual characteristics:

- Compact rosette.
- Broad rounded leaves.
- Soft, cupped, overlapping leaves.
- Dense but not chaotic center.
- Closed inner heart.
- Clean base.
- Slight asymmetry.
- Mild margin waviness.
- No aggressive serration.
- No oakleaf lobing.
- No romaine-like vertical spear leaves.
- No flat plate leaves.
- No random salad-pile geometry.

The first implementation should include deterministic defaults tuned for a visually pleasing mature Rex head.

---

## 13. Numerical constraints

The model must satisfy these geometry validation rules:

```text
all vertices finite
all face areas > 0
all normals finite and unit length
all receiver points finite
all receiver normals finite and unit length
receiver count = 2 × patch count
stable deterministic output for same config/seed
```

Recommended additional checks:

```text
projected diameter within configured bounds
height within configured bounds
total leaf area within plausible configured bounds
face aspect ratio warning threshold
self-intersection warning hook for future work
```

Do not make self-intersection validation a hard blocker in the first implementation unless it is cheap and reliable.

---

## 14. Growth-stage handling

Do not implement dynamic growth yet.

The first implementation should support a single mature archetype:

```text
growth_stage = "mature_greenhouse_head"
```

Future stages can be added later:

```text
seedling
juvenile_rosette
pre_head
mature_greenhouse_head
senescent_or_stressed
```

---

## 15. Optical transport roadmap

This geometry phase should not implement multispectral transport.

The intended future sequence is:

1. Generate deterministic Rex butterhead scientific mesh.
2. Export plant Radiance polygons.
3. Generate two-sided leaf receiver samples.
4. Run scalar geometry smoke tests.
5. Derive band-level Rex leaf R/T/A from packaged optical profile.
6. Generate band-specific leaf material definitions.
7. Run 5-band receiver `rtrace -I+` transport.
8. Compute absorbed photons using receiver flux and area-weighted aggregation.
9. Feed verified optical metrics into photosynthesis/transpiration models.

---

## 16. Leaf optical material policy for future work

The Rex optical profile provides wavelength-dependent leaf optical behavior. Future banding should reduce the profile into the project’s selected bands:

```text
blue
green
orange
red
far_red
```

For each band:

```text
reflectance_band
transmittance_band
absorptance_band
```

Required invariant:

```text
reflectance + transmittance + absorptance ≈ 1
```

The geometry layer should not bake in optical coefficients. It should only provide material hooks and receiver geometry.

---

## 17. Implementation phases

### Phase 13A: geometry specification

Create this document.

### Phase 13B: parametric scientific plant mesh

Implement:

```text
RexPlantConfig
generate_rex_butterhead_plant(config, seed)
LeafSurface
PlantMesh
face area/normal/centroid utilities
```

### Phase 13C: receiver sampling and export validation

Implement or strengthen:

```text
two-sided receiver generation
Radiance polygon export
OBJ debug export
geometry validation reports
```

### Phase 14: scalar plant receiver smoke

Run native Radiance with plant geometry included in the receiver scene and verify receiver rows parse correctly.

### Phase 15: Rex leaf optical material mapping

Implement band-specific R/T/A mapping and material generation.

### Phase 16: 5-band multispectral transport

Run per-band receiver transport and absorption metrics.

---

## 18. Non-goals

Do not include in the first geometry implementation:

- Three.js geometry generation.
- GLB export.
- Browser viewer.
- Growth response model.
- Dynamic organogenesis.
- Multispectral transport.
- HVAC, transpiration, fertigation, or Farquhar coupling.
- HPC/distributed execution.
- Photorealistic rendering materials.
- Manual sculpted mesh assets.

---

## 19. Acceptance criteria for first implementation

The first implementation is acceptable when:

1. A mature Rex butterhead plant can be generated from config and seed.
2. The same config and seed produce byte-stable geometry metadata.
3. The plant visually reads as a compact butterhead rosette when exported to OBJ.
4. The scientific mesh has stable leaf and face IDs.
5. Every mesh face has positive area and unit normal.
6. Two-sided receiver samples are generated from every receiver patch.
7. The default 32-leaf, 4 × 4 patch-grid plant has 512 patches and 1,024 receivers.
8. Radiance export uses the same scientific mesh faces.
9. OBJ export, if included, uses the same scientific mesh faces.
10. Tests prove determinism, geometry validity, receiver validity, and export consistency.

---

## 20. Source grounding

This model is based on:

- Rex butterhead lettuce being a greenhouse/hydroponic standard with slow bolting and tipburn tolerance.
- Rex being described by suppliers as a compact/thick-leaved butterhead type with a clean, well-closed base.
- Butterhead lettuce having broad, soft, rounded, loose-head morphology rather than romaine-like or oakleaf-like morphology.
- Head lettuce morphology literature indicating head formation is associated with leaf length-to-width ratio dropping below 1.
- General lettuce size guidance placing mature lettuce around 6–12 inches tall/wide, supporting a configurable mature envelope rather than a single fixed size.

The document is an implementation specification, not a peer-reviewed cultivar morphology model. Defaults should be treated as first-pass engineering assumptions and refined as measured Rex morphology data becomes available.
