# HPS Phase 25B layout, spectral, Rex, and transport-planning policy

Phase 25B adds pure planning around the Phase 25A measured-shape source. It
does not run Radiance, calculate absorbed metrics, or introduce another runner.

## Layout and mount

`coverage_4ft_center_pitch_v1` aligns the longer requested room axis to X;
square rooms remain unswapped. Counts are selected independently as
`max(1, floor(aligned_axis_ft / 4))` before wall clearance is considered. The
grid uses exact 1.2192 m X/Y pitch, is centered, and is emitted Y-major then
X-minor. The 0.798576 m fixture axis remains along aligned X and the 0.603504 m
axis along Y.

After counts are fixed, every wall must retain at least 0.0254 m clearance.
Illegal plans fail without reducing counts, clipping, scaling, overlap,
clamping, or rotation. For 10 x 10 ft, the policy produces a 2 x 2 grid at
`(+/-0.6096, +/-0.6096) m`, with 20.28 in X wall gaps and 24.12 in Y wall
gaps.

Mount height means aperture-plane-to-reference-plane distance. The default is
0.4572 m, so `aperture_z = reference_z + 0.4572`. Because the IES opening has
zero height, fixture-center Z equals aperture Z.

## Spectral budgets

The sole packaged `hps_1000w_spd.csv` relative SPD is converted through the shared
`relative_spd * wavelength_nm` photon rule and normalized over 400-699 nm.
Blue, green, orange, and red close to unit PAR; far-red is retained relative to
PAR and remains outside it. Band-effective yields derive from the computed
system PPE `1750 / 1045 = 1.6746411483253588 umol/J`, and
per-fixture/full-layout PAR budgets derive from 1,750 umol/s and fixture count.
SPD amplitude cannot change fractions, electrical power, PPE, or PPF.

## HPS-weighted Rex optics

The HPS adapter supplies its absolute photon distribution to the existing
source-neutral Rex weighting API and maps the resulting scalar PAR plus five
band A/T/R intervals through the shared diffuse symmetric `trans` material
builder. All coefficients and materials carry HPS-specific identities.

The source is positive from 381-779 nm while the Rex grid begins at 404 nm.
Positive 400-403 nm PAR/blue source mass is reported explicitly as unsupported;
it is not filled, extrapolated, renormalized, or redistributed. SMD and
Conventional source identities are rejected at HPS adapter boundaries.

## Isolated transport plans

Run order is `scalar_par`, `blue`, `green`, `orange`, `red`, `far_red`. Every
run shares one Phase 25A angular DAT identity and one authenticated HPS
fixture-body plan, but has distinct source, HPS-weighted leaf material,
scene, octree, and ambient-cache identities. Baseline scene composition is
exactly room, HPS emitters, then fixture bodies. FSPM scene composition is
exactly room, HPS emitters, fixture bodies, then the mature Rex plant.
Body/classification identity is part of every containing scene and cache
identity without changing the Phase 25A source identity.

The established 32-leaf, 5,248-polygon, 512-patch, 1,024 two-sided-receiver
contract and physical-patch-area policy remain unchanged. Each interval's
absolute PPF and `PPF * 179` carrier are applied before trace. Plans prohibit
post-trace 179 conversion, spectral scaling, target matching, dimming, and
spatial symmetrization. Only path-normalized `CommandSpec` descriptions are
serialized; execution remains out of scope.
