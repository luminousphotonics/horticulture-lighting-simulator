# Authenticated fixture-asset pipelines

## Proposed standalone LED module

The production asset is generated from `proposed_led_module.py` with
Build123d/OpenCascade, exported as a named STEP assembly, tessellated through
OpenCascade, authored as an embedded GLB, and packed with pinned `gltfpack
0.20`.

The fixed tessellation settings are 0.10 mm linear deflection and 0.25 rad
angular deflection. CAD `(X,Y,Z)` is authored into glTF as `(X,Z,-Y)`, so CAD
`+Z` is glTF `+Y`. Units remain millimetres and the registered instance
transform performs the sole `0.001` mm-to-m conversion. The mesh writer emits
indexed triangles with explicit flat per-face normals. It does not auto-center,
auto-fit, simplify, scale, shear, use Draco, or add instancing.

Run from the repository root:

```sh
/home/austin/Desktop/build123d-modeling/.venv/bin/python \
  scripts/fixture_assets/build_proposed_led_module.py \
  --gltfpack /path/to/pinned/gltfpack-0.20
```

The pack command is:

```sh
gltfpack -i led_module.raw.glb -o led_module.glb -kn -km -ke -noq
```

`-noq` prevents the packer from moving the authenticated 27.4 mm cover plane
onto a global quantization grid; the final GLB therefore declares no extension.

## HPS physical housing

`build_hps_fixture.py` authenticates the corrected, user-validated
`hps_1000w_fixture.py` source, exports and reimports its named STEP assembly,
and applies the same OpenCascade tessellation and embedded GLB policy. It keeps
all eight leaf solids as one named mesh/primitive each and records the complete
classification-ready primitive inventory in deterministic provenance.
The active `hps-housing-v3` geometry has an unperforated, sealed top box
and one exhaust flange/bore on the sloped rear wall at the negative-X end.
Mechanical and decoded-mesh ray checks distinguish the rear bore opening from
the sealed roof and record internal socket/base/bracket interception along the
luminous-axis-to-rear direction.

Run from the repository root with the pinned upstream release binary:

```sh
/home/austin/Desktop/build123d-modeling/.venv/bin/python \
  scripts/fixture_assets/build_hps_fixture.py \
  --gltfpack /path/to/pinned/gltfpack-0.20
```

The HPS pack command is `gltfpack -i hps_1000w_fixture.raw.glb -o
hps.glb -kn -km -ke -noq`. The full-scale physical housing remains in
millimetres; publication alone converts it using `0.001 m/mm` and translates
its exact local `Y=-248.92 mm` placement plane by `+248.92 mm`. No fitted scale,
X/Z correction, auto-centering, or relationship to the separate authenticated
`0.798576 x 0.603504 m` scientific IES aperture is introduced.
