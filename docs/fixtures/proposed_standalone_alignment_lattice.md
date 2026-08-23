# Proposed standalone alignment lattice policy

The production `standalone_modules` composition permanently includes the
versioned `standalone_module_integer_staggered_triangular_graph_v1` alignment
lattice. Historical Linear and Legacy fixture compositions do not include it,
and there is no request or UI switch for it.

Topology is constructed from exact pre-scale integer lattice coordinates.
Consecutive modules in each physical row receive horizontal edges; module
pairs offset by one integer coordinate across adjacent staggered rows receive
diagonal edges. Physical distance does not select edges. Horizontal edges are
emitted first in physical-row order, followed by diagonal edges in adjacent-row
order. This yields one deterministic, connected, crossing-free triangulation.

Each edge becomes a straight, taut cylinder with diameter `0.0015875 m`.
Free-span endpoints are calculated after room scaling from the final module
transforms and the immutable `0.150 x 0.150 m` mechanical envelope:

- horizontal links use facing edge midpoints;
- diagonal links use nearest-facing corners.

All endpoints lie on
`proposed_led_module_authenticated_rear_plane_v1`. This plane is the
rear-most full-GLB bound of authenticated asset `proposed-led-module-v1`,
`0.034800000889971816 m` behind its calibrated light-side aperture plane after
the fixed scientific transform. Planning fails if the authenticated geometry
no longer produces that offset. The module asset is never modified or scaled.

The cylinders use `fixture_body_anodized_aluminum_v2` and are non-emitting
occluding geometry in every Stage A and Stage B scene. Radiance consumes the
ordered link endpoints directly. The viewer consumes the same records and
renders one exact-diameter instanced cylinder mesh. Topology, geometry,
attachment, material, counts, endpoints, and their SHA-256 identity are carried
by the run layout, fixture catalog, and fixture-occlusion metadata; ambient
cache filenames also include the fixture-occlusion identity.

Terminal fittings, perimeter or building restraints, lifting hardware, sag,
and cable coating are intentionally outside this model.
