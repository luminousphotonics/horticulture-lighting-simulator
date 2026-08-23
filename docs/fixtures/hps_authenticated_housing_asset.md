# Authenticated HPS housing asset

The active viewer housing identity is `hps-housing-v3`. It is generated
from the byte-authenticated Build123d source
`scripts/fixture_assets/hps_1000w_fixture.py` by
`scripts/fixture_assets/build_hps_fixture.py`. The source SHA-256 is
`4aefe69488dba7f916af74877d92eebe512547fd19b8da5970c25e7158561cd9`.

This revision seals the former vertical roof exhaust and moves the exhaust
flange and bore to the sloped rear wall at the negative-X end. Hood-only CAD,
STEP-roundtrip, raw-GLB, and packed-GLB ray contracts prove that straight-up
and upward/outward rays hit the sealed roof, while a deliberate ray through
the rear bore axis clears the hood. A ray from the luminous axis toward that
rear region is intercepted by the socket/base/bracket assembly before it could
escape, matching the complete physical assembly rather than the hood alone.

The production artifacts are:

- `src/fspm_optics/resources/cad/hps/hps_1000w_fixture.step`
- `src/fspm_optics/resources/cad/hps/hps_1000w_fixture.raw.glb`
- `src/fspm_optics/resources/viewer/fixtures/hps/hps.glb`
- `src/fspm_optics/resources/cad/hps/hps_1000w_fixture.provenance.json`

CAD millimetres map to glTF millimetres as `(X,Y,Z)->(X,Z,-Y)`. The asset is
not centered or fitted. Publication uses `meters_per_asset_unit=0.001`, unit
dimension correction, no X/Z placement correction, and an exact `+248.92 mm`
Y translation so the local `Y=-248.92 mm` physical opening plane registers at
the run's luminous-aperture elevation.

This registration does not make the housing authoritative scientific source
geometry. HPS transport continues to use the authenticated zero-height
`0.798576 x 0.603504 m` IES aperture, unchanged IES/SPD data, and unchanged
layout and mounting behavior. Fixture-occlusion v4 now transports six reviewed
external leaves: the top box, flared skirt, exhaust flange, socket bracket,
ceramic socket, and bulb base. They use the shared generic
`fixture_body_anodized_aluminum_v2` reference material; this is not a
component-specific material claim.

The transparent viewer bulb glass is excluded because its alpha has no
authenticated Radiance coefficients. The viewer arc tube is excluded because
the IES source remains authoritative. The active classifications are in
`classification.v4.json`. Historical `classification.v3.json` remains exactly
692,248 bytes with SHA-256
`bdbd6c4ca708bcd446a0535490a642c56a321a8d1f7ef4122fee54a4d15eab7e`;
the v3 provenance `scientific_boundary` records the prior disabled phase and
is intentionally not rewritten.
