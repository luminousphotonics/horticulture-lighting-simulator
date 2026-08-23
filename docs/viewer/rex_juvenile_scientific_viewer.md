# Run-specific juvenile Rex scientific viewer

Phase 27F-A publishes one immutable display derivative of
`rex_juvenile_preheading_12leaf_v1`. Python `PlantMesh` geometry remains the
scientific authority. The GLB and receiver binary are float32 browser-display
derivatives only.

The Phase 27G-D4 v3 profile, v2 identity-map, and run-scene bodies authenticate a separate
surface-sampling identity while retaining the established stable publication
routes. New scenes use
`rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1`; explicit legacy
replay uses `rex_juvenile_surface_sampling_uv_quarter_4x4_v1`. `_PATCH_INDEX`,
receiver markers, and normal overlays all derive from the selected profile.
The renderer still uses one canonical GLB, one instanced plant surface, and one
receiver buffer.

Phase 27G-D4 changes only that browser display derivative. For each
`LeafSurface`, the GLB publisher accumulates authoritative triangle
unnormalized cross products at local source vertex indices, normalizes once per
source vertex, and copies the resulting leaf-local smooth normal to each
nonindexed triangle corner. Leaves are never smoothed together. Positions,
triangles, winding, face order, and compact leaf/face/patch identities are
unchanged; scientific face normals remain authoritative outside the GLB.

The same structured local indices provide exact `TEXCOORD_0` provenance:
`u` is the generator's longitudinal parameter and the stored transverse value
is `(v + 1) / 2`. The base is exactly `(0, 0.5)`, the tip is exactly
`(1, 0.5)`, and intermediate rows come directly from the declared segment
indices. No world-space matching, UV unwrapping, or tolerance reconstruction
is used. D4 scenes declare
`phase27g-d4-viewer-leaf-materials-v1`; the browser keeps explicit, separate
dispatch for authenticated D2 and D3 publications and requires the D4 VEC2
Float32 accessor only for profile schema 3.

## Display-only leaf material policy

The single instanced plant `MeshStandardMaterial` retains metalness 0, no
emissive contribution, no shadows, and roughness 0.82 on the front. Its neutral
front starts at muted botanical `#356B42`; the underside stays in the same
family at `#3B6848`, 0.94 saturation, and roughness 0.84. A fixed leaf-index sequence supplies at
most plus or minus 3% neutral lightness variation. It does not read plant
instance, position, run, system, quality, or runtime randomness.

Exact leaf UVs drive one derivative-filtered midrib at `v=0.5` and six ordered,
curved, tapered secondary-vein pairs. Centralized policy constants cap neutral
midrib luminance at 6%, secondary-vein luminance at 3%, and add restrained
roughness detail. There is no image texture, high-frequency noise,
microtexture, grid, displacement, subdivision, remeshing, or physical vein
geometry. Cosmetic analytic normal detail is capped at 2 degrees and cannot
move geometry.

On a surface-flux-selected side, the authenticated palette becomes the base
color directly: it is never multiplied by neutral green and never receives
leaf-rank color variation. Only grayscale vein luminance up to 1%, restrained
roughness detail, and normal detail capped at 0.5 degrees remain. An unselected
side in Front-only or Back-only mode retains the neutral policy. When receiver
samples or scientific normal arrows are visible, cosmetic normal detail is set
to zero while smooth GLB normals, vein color/roughness, receiver anchors, and
scientific arrows remain unchanged.

These choices are a visual inspection policy, not a botanical measurement or
a change to the optical model. The optimized scientific topology and receiver
identities remain, respectively,
`d59c27614c9b4fde3d60f8ebf090b56bbc6692d77baeed67753e8081ff70138f`
and
`e2f6606ba78642ffe6f9de0601d87c8f4e4197b66cb3ef9d284f8324e1ee305f`.
Counts remain 12 leaves, 1,920 faces, 192 patches, and 384 receivers per
canonical plant.

The viewer has no runtime backend, CDN, framework, build system, mature-profile
input, browser-side layout engine, or metric data. Each completed
application run receives exactly one scene for its authorized request and
Natural-fit plan. A published directory can be served by any static HTTP server.

Phase 27G-D2 validation and rendering code can bind authenticated,
system-neutral surface-flux display artifacts to that same scene for retained
scientific regression coverage. The current public UI does not expose those
Incident/Absorbed PAR controls: Target Coverage is the sole leaf-coloring
selector, and compact playback omits the per-patch/per-leaf display arrays.
This does not remove Phase 27G-C transport or aggregate absorption metrics. The
historical scientific, acceptance, validation, and GPU contract is documented
in `docs/viewer/system_neutral_surface_flux_coloring.md`.

D2 surface-flux calibration is scoped only to the explicit legacy sampling
profile. Optimized scenes publish neutral/unavailable coloring and retain raw
Stage C outputs; they never reuse legacy gamma or beta by fallback.

Phase 27F-C consumes the integrity-checked fixture catalog and complete
server-resolved fixture matrices published by Phase 27F-B. The browser verifies
the catalog identity and every declared GLB and matrix buffer before parsing or
use, then renders the required Proposed, Conventional, or HPS assemblies.
The immutable registry contains nine approved assets. Proposed assemblies
retain their scientific grouping type while an explicit server-authored
display type selects the matching immutable geometry from authoritative member
and connector topology. This distinguishes straight and corner three-module
groups. Four-member display geometry follows ordered segment topology rather
than the scientific grouping label: straight traversal selects `linear4`, a
turn after segment 0 selects `L`, and a turn after segment 1 selects
`reverse_L`. Browser-side classification is prohibited.

## Prepare the pinned renderer

From the repository root, install the exact dependency without creating a lock
file and run the reproducible vendor task:

```text
npm install --no-package-lock
npm run vendor:three
```

The task verifies `three@0.184.0`, then copies only the official Three.js core
ES module, OrbitControls, RoomEnvironment, GLTFLoader, the loader's
BufferGeometryUtils and SkeletonUtils dependencies, and the upstream MIT
license into the static viewer vendor directory. The modules resolve locally
through the viewer import map; there is no CDN, external environment texture,
or runtime network dependency. Commit the resulting vendor files when preparing
the viewer release.

## Publish an external bundle

Choose a new absolute directory outside the repository:

```text
.venv/bin/python -m fspm_optics.viewer \
  --output /absolute/external/viewer \
  --run-id 0123456789abcdef0123456789abcdef \
  --system-id proposed \
  --room-length-ft 12.5 \
  --room-width-ft 7.75 \
  --natural-fit-artifact /absolute/run/natural_fit_layout.json \
  --layout-artifact /absolute/run/fixture_layout.json
```

The output path must not already exist. Publication builds in a deterministic
sibling staging directory, validates profile and artifact hashes, and promotes
the directory only after successful validation. It never derives paths from the
current working directory and never embeds timestamps or absolute machine paths
in artifacts.

The result contains the static application and:

```text
scene.v1.json
instances.f32le.bin
fixtures/catalog.v1.json
fixtures/assets/<display-asset-id>-<hash>.glb
fixtures/transforms/<display-asset-id>-<plan-hash>.f32le.bin
profiles/rex_juvenile_preheading_12leaf_v1/profile.v1.json
profiles/rex_juvenile_preheading_12leaf_v1/geometry.glb
profiles/rex_juvenile_preheading_12leaf_v1/identity-map.v1.json
profiles/rex_juvenile_preheading_12leaf_v1/receivers.f32le.bin
```

Runs with validated FSPM transport also contain:

```text
surface-flux/metadata.v2.json
surface-flux/patch-values.v1.f32le.bin
```

The metadata version changes because front scientific display semantics
changed materially. Existing authenticated bundles may retain
`surface-flux/metadata.v1.json` and their scalar behavior. A v1 artifact is
never interpreted as a v2 local-patch-normalized artifact. The version-1
Float32 patch-value filename remains because its contents and channel semantics
remain raw q, not `u` or `z`.

`scene.v1.json` identifies the run, system, decimal requested room, canonical
profile, authoritative Natural-fit plan and artifact hash, ordered plant IDs,
and exact display bounds. Its fixture reference also declares the catalog byte
length, SHA-256, authoritative layout SHA-256, fixture-plan SHA-256, and exact
counts. It is a single-scene contract: no predefined room catalog or selector
is published. The browser verifies every plant and fixture artifact it consumes
before constructing the scene. The plant surface uses one canonical nonindexed
geometry and one `THREE.InstancedMesh` populated from the sole compact buffer.
Receiver bytes are integrity-checked up front, while receiver typed arrays,
points, and normal lines are created lazily and reused without rebuilding the
plant surface.

Fixture transform buffers contain little-endian Float32 4x4 column-major
matrices with a 64-byte stride. They map packaged GLB scene coordinates directly
into the viewer's right-handed metre space. Proposed matrices are validated
against ordered authoritative module positions with a 0.25 mm maximum anchor
residual; Conventional matrices place the Conventional LED model on its emitting aperture;
HPS matrices apply the exact declared
248.92 mm Y-only housing correction so the authenticated `-248.92 mm` local
placement plane aligns with the separate
0.4572 m scientific luminous-aperture plane.

The official loader parses each validated in-memory GLB once. For each authored
mesh/material primitive, the viewer clones the geometry and bakes its authored
post-root world matrix `A` into asset-root coordinates exactly once. Server
matrices are partitioned by determinant parity, so draw calls scale with unique
source primitive/material/parity groups rather than fixture instances. Authored
materials are retained without brightness, emissive, metalness, roughness,
mirror, side, or height mutations; this includes the HPS transparent
bulb.

The scientific-to-viewer handedness conversion legitimately gives most
fixture server matrices `S` negative parity, which is unsafe for Three.js
InstancedMesh normal handling. Positive-parity matrices submit `S` against the
asset-root geometry `A v`. For negative parity, one asset-root local-X
reflection `R` is baked after `A`, triangle winding is reversed, tangent
handedness is flipped when present, and the submitted matrix is `S R`. Because
`R² = I`, both paths preserve the exact authoritative result:
`S (A v) = (S R) (R A v) = S A v`. Every submitted instance matrix therefore
has positive determinant. Bounds are calculated from the rendered primitive
vertices using the same geometry variant and submitted matrix. Source geometry,
approved GLB bytes, server matrices, and authored materials are unchanged.

Neutral inspection lighting uses one locally generated PMREM from the official
Three 0.184 RoomEnvironment at environment intensity 1.0. This supplies the
uniform image-based lighting required by authored metallic PBR materials while
leaving the viewer's dark background unchanged. Modest white ambient (0.18) and
hemisphere (0.28) fill remain, plus a shadow-free white directional key (0.85)
and target attached to the active camera for shape definition. This lighting
exists only to keep CAD form and material detail readable while orbiting; it
does not model fixture emission, horticultural illumination, Radiance, or PPFD.
The PMREM is generated once, its temporary scene and generator are immediately
disposed, and its retained render target is disposed with the event-driven
viewer session. Renderer color space is unchanged.

The exact decimal room reference surface and perimeter are built from
`scene.v1.json`. Perspective, top, side, and reset framing use the union of room,
plant, reference-plane, and transformed fixture bounds. Fixtures render on a
dedicated layer while plant picking remains on the plant layer. The renderer is
invalidated by controls, resize, and layer changes and remains idle between
events; fixture and plant draw-call reporting is separate.

Surface coloring preserves this path. The browser authenticates and retains
the raw Float32 q array, then allocates one bounded same-sized display array.
For each plant and canonical local patch, the front channels become
`q / (gamma[k] * R)` and the back channels retain `q / (beta * R)`. The display
array is uploaded as one RGBA32F data texture indexed by the existing Float32
local patch attribute and plant instance ID. A `MeshStandardMaterial` shader
hook uses `gl_FrontFacing` to choose the correct variable and side-specific
palette in Both, Front only, and Back only modes.

No per-plant mesh, per-plant material, CPU face duplication, second plant draw,
or alternate `ShaderMaterial` path is created. Rerun or page teardown clears
the raw and derived CPU arrays, disposes the texture, removes the shader hook
and references, and aborts registered UI event handlers. Camera controls,
fixtures, lighting, picking, receiver overlays, and the one-draw
`THREE.InstancedMesh` remain unchanged.

Plant inspection selection persists across empty-surface clicks and click
events produced after orbit or pan gestures. Clicking another plant replaces
the selection. No deselect control is added.

User-run Python and JavaScript validation commands and the manual Standard and
Quality browser procedure are listed in
`docs/viewer/system_neutral_surface_flux_coloring.md` under “User-run
validation” and “Manual browser checks.”
