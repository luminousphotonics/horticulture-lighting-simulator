# Production Radiance room model

All active lighting systems use one permanent, versioned room authority:
`closed_six_surface_neutral_diffuse_v1`. There is no selectable room profile,
environment override, compatibility material, or UI control.

The generated room is a closed rectangular enclosure with six polygons. The
four walls and ceiling use `room_wall_ceiling_neutral_diffuse_v1`, a Radiance
`plastic` with RGB reflectance `0.9000 0.9000 0.9000`, specularity `0.0000`, and
roughness `0.0000`. The floor alone uses
`room_floor_neutral_diffuse_v1`, with RGB reflectance
`0.1000 0.1000 0.1000`, specularity `0.0000`, and roughness `0.0000`.

The authority and its canonical SHA-256 identity are defined in
`fspm_optics.geometry.room`. Proposed uniform, Proposed basis, Conventional,
HPS, multispectral, and Rex Stage B manifests and ambient-cache names carry
that identity. Basis manifests predating the authority are rejected during
resume loading, and existing workspace text must match the current generated
room exactly.

This room change does not alter the horizontal sensor plane at `z = 0.005 m`,
the emitting-aperture-to-sensor mounting-height definition, source power or
angular models, sampling grids, fixture geometry, or plant optics. Stage A
continues to exclude plants and benches while retaining authenticated fixture,
module, and Proposed alignment-lattice occlusion.
