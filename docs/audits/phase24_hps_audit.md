# Phase 24 HPS 1000 W HPS Audit

> **Superseded historical audit.** Phase 3 replaces this audit's former active
> source authority. Current production uses `hps_1000w_spd.csv`, 1,750
> umol/s documented initial lamp PAR PPF, 1,045 W tested system input,
> `1750 / 1045` system PPE, and a 313,250 source-side Radiance carrier. Values
> and filenames below are retained only as historical evidence of the
> superseded Phase 24 model and must not be used by active code or policy.

Audit date: 2026-07-10

This audit uses these path bases:

- **Legacy repository:** `/home/austin/Desktop/horticulture-lighting-simulator`
- **New repository:** `/home/austin/Desktop/fspm-optics-engine`
- **Approved external IES:** `/home/austin/Desktop/ies_sources/hps_1000w.ies`

The new repository was inspected on branch `phase24-hps-audit` at commit
`e601845837bb2b2460b1e295195d6dd4992a4411`. Its working tree was clean before
this document was created. The legacy repository was inspected on branch
`feat/plant-growth-model` at commit
`a900d361d368dbfea40eadf9836a3fe3947d7ab8`; it already had an unrelated
untracked `docs/salvage/` directory, which was not modified. Static shell
inspection and one in-memory calculation through the new repository's
`.venv/bin/python` were used. No Radiance executable, test, application,
server, copy operation, commit, or push was run.

## 1. Executive verdict

The smallest defensible clean model is a **hybrid measured-shape comparison
profile**:

> A modeled generic HPS hps_1000w_fixture horticultural
> HPS comparator using the tested fixture's Type-C angular distribution and
> 31.44 x 23.76 in luminous-opening footprint, with a separately declared
> fixed operating point of 1,045 W system input, 1.72 umol/J PAR PPE, and
> 1,797.4 umol/s PAR PPF per fixture.

The IES is evidence for a physical tested assembly: one HPS-branded
nominal 1000 W HPS lamp, formed-aluminum housing, hammertone reflector, and one
HPS 1000 electronic switchable ballast. It does **not** measure PAR PPF or
PPE. The 1.72 umol/J value is a legacy shared comparison assumption, not an IES
or manufacturer measurement. The active SPD is a generic digitized relative
`DEhps_1000w` shape and is not proven to be the tested HPS lamp's spectrum.

| Capability | Status | Verdict |
|---|---|---|
| Fixture identity | **Ready with a strict claim boundary** | Use the named measured photometric assembly plus a declared modeled PAR operating point. Keep `1000W HPS`, `hps`, `hps_4x4`, and “double-ended surrogate” as provenance aliases or limitations, not the primary clean identity. |
| Layout | **Ready to reimplement** | Preserve the 4 ft nominal coverage pitch/count policy, independent X/Y counts, centered Cartesian grid, long axis along X, and explicit clearance checks. Do not describe `4x4` as a four-by-four array. |
| Mount policy | **Needs an explicit clean definition** | Legacy defaults conflict at 0.4572 m and 0.9144 m, and `HPS_Z_M` is a world-Z source transform rather than a named plane-to-plane gap. Phase 25 should define 0.4572 m from emitting aperture plane to receiver/reference plane. |
| IES angular model | **Ready after shared-parser generalization** | The approved LM-63-2002 file is complete, non-negative, quadrant-symmetric Type C photometry with a zero-height rectangular opening. Expand declared symmetry once, normalize downward flux once, and retain one downward rectangular aperture per fixture. |
| Scalar absolute calibration | **Legacy result is not defensible; clean policy is ready** | The old `ies2rad` multiplier omits the 179 lm/W white-carrier bridge while decoding Radiance values directly as PPFD. Normalize the angular distribution to unit downward flux, then apply 1,797.4 umol/s exactly once with the existing 179 carrier contract. |
| Relative spectral model | **Ready as a modeled shape** | Package the exact SPD bytes, label them generic/digitized/relative, convert `relative_spd * wavelength_nm`, normalize PAR, and keep far-red separate. The SPD must not set PPF or electrical power. |
| HPS-weighted Rex optics | **Ready to compute through existing source-neutral code** | Recompute scalar PAR and five-band A/T/R from the HPS distribution. Do not copy SMD-, Conventional-, fallback-, or generated legacy coefficients. |
| Native scalar/five-band execution | **Defer from the minimum Phase 25** | Phase 25 can stop at deterministic resources, profiles, layout/source/material plans, and Radiance-free tests. A narrow later subphase can adapt the established Conventional executors without creating another HPS framework. |

## 2. Active HPS execution path

### 2.1 HPS selection and configuration

1. `src/rad_rebuild/radiance/domain.py:SystemMode.HPS` defines the public mode
   label `1000W HPS`. `RadianceRunRequest` supplies application defaults
   `hps_coverage_ft=4.0`, `hps_z_m=0.4572`,
   `hps_fixture_ppf=1797.3999999999999`, `hps_input_watts=1045.0`, and
   `hps_ies_variant="hps"`. Its validator rejects every non-HPS
   variant and disallows peak capping and PPE matching for HPS.
2. `src/rad_rebuild/radiance/backend/env.py:_env_hps` copies those values to
   `HPS_COVERAGE_FT`, `HPS_Z_M`, `HPS_FIXTURE_PPF`, `HPS_INPUT_WATTS`, and
   `HPS_IES_VARIANT`, fixes `EFF_SCALE=1.0`, and disables cap logging. `NX` and
   `NY` are also computed, but the active HPS emitter service recomputes its own
   counts and does not consume them.
3. `src/rad_rebuild/radiance/backend/runtime.py:pipeline_shell` selects
   `scripts/radiance/run_simulation_hps.sh`. That shell file is only a
   compatibility launcher for
   `rad_rebuild.radiance.cli.scripts run-simulation-hps`; it is not scientific
   source code and is unnecessary in the clean engine.
4. `src/rad_rebuild/radiance/cli/scripts.py:run_simulation_hps` selects HPS
   again, but its direct-launch fallback silently changes the mount default to
   `HPS_Z_M=0.9144`. It defaults coverage to 4 ft and profile-derived PPF/power.
   Backend and committed playback generation normally provide 0.4572 m, so the
   legacy behavior depends on entry point.
5. `src/rad_rebuild/radiance/engine/emitters/generate_emitters_hps.py` is a
   mutable-global/import-reload facade. The actual source builder is
   `emitters/hps_generation/service.py`; the facade adds no fixture science.

### 2.2 Room, layout, photometry, and source generation

6. `cli/scripts.py:run_simulation_hps` calls
   `engine.geometry.generate_room` and `generate_sensor_grid`. The room is a
   centered axis-aligned box with a 0.10 diffuse floor and 0.90 diffuse walls
   and ceiling. Sensors default to a plane at world `z=0.005 m`. Those material
   values and the sensor policy are shared legacy scene assumptions, not HPS
   evidence.
7. `hps_generation/profile.py` is the compact fixture record. The active
   profile is `hps_1000w_research_primary_v1`, with a 31.44 x 23.76 x
   0 in envelope, 4 ft default coverage, 1 in minimum wall inset, HPS IES,
   HPS SPD, 1,045 W input, and the declared 1.72 umol/J anchor. It also retains
   an invented 0.160 m x 0.014 m eight-sided arc, reflector cross-section, and
   material constants that the active transport path does not instantiate.
8. `hps_generation/geometry.py:compute_fixture_layout` aligns the longer room
   axis to X, calls `coverage_grid_counts`, creates centered 4 ft-spaced fixture
   centers, and writes JSON-only body corners and a lamp line. The source
   orientation is `horizontal_x` by profile declaration. The body/lamp records
   are visualization/reference geometry, not active emitters.
9. `hps_generation/service.py:_write_ies_comparator` parses the IES through
   `engine/photometry/ies_photon_toolkit.py`, derives a lumen-to-photon bridge
   from the SPD, integrates a baseline lumen value, forms a PPF normalization
   ratio, and calls `ies2rad -m <ratio>`. It then inspects the generated source,
   requires nonzero width/length opening dimensions, and converts the generated
   light's RGB arithmetic mean to equal grey channels.
10. Existing committed runtime metadata records the generated HPS source as
    one native `flatcorr` polygon, 0.798576 x 0.603504 m, with local
    `z=-0.00025 m`. No reflector shell, housing, arc tube, or multiple emitting
    faces enter the active transport model.
11. `hps_generation/rad_writer.py:write_hps_rad` emits one `!xform` per fixture:
    `-rx HPS_IES_ROT_X_DEG -rz HPS_IES_ROT_Z_DEG -t cx cy layout_z`. Both
    rotations default to zero. Layout collision/clearance checks do not account
    for nonzero rotation overrides.

### 2.3 Scene, Radiance commands, decoding, metrics, and artifacts

12. `cli/scripts.py:_run_hps_pass` builds the octree from room, the HPS emitter
    file, and optional plant geometry through `oconv`. In baseline mode this
    means plants can enter what otherwise looks like the canopy-plane scene;
    the new repository's baseline/FSPM scene separation is cleaner.
13. Sensor rays are repeated `OS=4` times without directional jitter and point
    upward. `_trace_ppfd` runs an argument-list command equivalent to
    `rtrace -h -I+ -n <threads> <quality options> <octree>`. Standard mode uses
    `-ab 3 -ad 512 -as 128 -aa 0.22 -ar 48 -dj 0.35 -ds 0.40 -dt 0.08
    -dc 0.50 -dr 1 -lr 6 -lw 2e-4`, plus a fresh ambient cache.
14. `_aggregate_rgb_to_ppfd` requires R=G=B within `1e-6`, averages the three
    values and repeated samples, and declares the result to be PPFD directly.
    It intentionally performs no post-trace 179 conversion.
15. HPS is fixed-output with respect to target PPFD: no mean/peak target
    multiplier or second trace is applied. Direct callers can still set
    `EFF_SCALE` in `[0,1]`; the emitter PPF and reported electrical input both
    scale linearly, despite the UI's fixed-output claim.
16. HPS tracing defaults `SYM=1`. `symmetrize_ppfd.py` averages D4 orbits,
    including X/Y swaps unless `AXES_ONLY` is set. This preserves orbit means
    but can erase real differences between the long and short HPS axes and
    between measured C-planes. It is an inappropriate post-trace shape
    correction for the oriented rectangular fixture and must not be ported.
17. The scalar path writes `ppfd_map.txt`, `hps_layout.json`,
    `hps_summary.txt`, and `hps_power.txt`; application layers later transform
    those into API metrics, manifests, images, and precomputed bundles.
    Reporting computes fixture count from layout JSON, total PPF as
    `fixture_ppf * count * EFF_SCALE`, and total watts as
    `input_watts * count * EFF_SCALE`.
18. When plants and `banded_5` are enabled, the old FSPM path derives the HPS
    photon distribution from the SPD, creates one Rex `trans` material, plant
    scene, octree, ambient cache, and receiver trace per band, and applies the
    band source fraction after each full-source trace. The new engine instead
    requires absolute band flux before trace, which is the preferred policy.

### 2.4 Active behavior versus out-of-scope layers

- **Active HPS science:** the HPS-only profile, 4 ft coverage grid,
  centered placement, 0.4572 m application mount input, approved IES, HPS SPD,
  one native polygon per fixture, fixed declared PPF/power, scalar trace/decode,
  and optional HPS-weighted Rex receiver materials.
- **Other HPS variants:** current code accepts only `hps`. OG, HDE, and a
  handcrafted surrogate appear in `docs/radiance/hps_ies.md` as historical
  research context and must not be copied into the HPS port.
- **Application infrastructure:** domain aliases, backend routes/env/jobs,
  workspace keys, costs, artifact serving, and public/private runtime gates are
  not scientific dependencies.
- **Precomputed playback:** `simulation/precompute_*`, playback logic,
  download helpers, and committed `hps_4x4` bundles are generated
  outputs. They encode the old calibration path and are not port inputs.
- **Visualization:** assembly models, JSON lamp/body outlines, overlays,
  viewer assets, browser code, and web templates do not define transport
  geometry.
- **Deployment and validation execution:** Docker, Render, shell launchers, and
  scripts that run Radiance are not clean-port components. Selected validation
  files are useful only as static salvage references.

## 3. HPS identity and numerical behavior

### 3.1 Physical asset identity and claim boundary

| Identity field | Evidence | Clean interpretation |
|---|---|---|
| Manufacturer | IES `[MANUFAC] generic HPS reference` | Physical photometric asset provenance. |
| Product/catalog | `[LUMCAT] hps_1000w_fixture` | Primary luminaire identifier. Preserve exact punctuation/case in asset metadata. |
| Luminaire | `Formed aluminum housing, hammertone reflector` | Tested reflector/housing description; detailed dimensions/material BRDF are not supplied. |
| Lamp | `generic 1000W HPS Lamp. LUMEN RATING = 150000 LMS.` | One nominal 1000 W HPS lamp. The IES does not say double-ended, provide an exact lamp catalog, or provide SPD/PPF. |
| Ballast | `[OTHER] One HPS 1000 Electronic Switchable Ballast` | Ballast family/type only. Switch setting, voltage, current, PF, losses, and dimming curve are absent. |
| `HPS-8` | Appears only inside the catalog string and legacy names | Treat as an opaque product-series token. Available evidence does not establish that `8` means eight lamps, emitters, bars, coverage feet, or a fixture count. |
| Measured vs scenario | UL test metadata plus complete candela table; no PAR measurement | Angular distribution is measured physical-fixture evidence. PAR/electrical comparison behavior is a declared modeled operating point. |

The clean profile should be named along the lines of
`hps_1000w_declared_par_comparator_v1`. The aliases
`hps`, `1000W HPS`, and `hps_4x4` may be retained in provenance fields.
The legacy phrase `1000W double-ended high-pressure sodium lamp surrogate`
must remain a limitation/provenance note because the approved IES does not
establish double-ended construction.

### 3.2 Electrical and photon quantities

| Quantity | Exact legacy value | Authority and disposition |
|---|---:|---|
| Nominal lamp class | 1000 W | IES lamp text. Physical identity metadata, not modeled system input. |
| IES/test input | 1,045.0 W | LM-63 numeric header. Best available complete-fixture input value; voltage/current/PF are absent. |
| Modeled fixed input | 1,045.0 W/fixture | Legacy profile/request. Preserve as declared comparison input while recording that it numerically adopts IES test input. |
| Declared PAR PPE | 1.72 umol/J | `SHARED_DEFAULT_FIXTURE_PPE_UMOL_PER_J`. Scenario assumption shared historically across HPS variants; not measured HPS efficacy. |
| Declared PAR PPF | `1045 * 1.72 = 1797.4 umol/s` | Final clean absolute anchor, exactly once. |
| IES lamp lumens field | 150,000 lm | Per-lamp photometric metadata; never PPF. |
| Static symmetric-table flux diagnostic | 118,057.256732 lm (cd·sr) | Read-only quadrant expansion with the shared four-corner cell mean and exact solid-angle factor `delta_C * (cos(gamma_0) - cos(gamma_1))`. Diagnostic only, about 78.70% of the lamp lumen field. |
| Legacy integrated flux | 118,214.632529 lm | `ies_photon_toolkit.integrate_ies_lumens`; its horizontal angle weights cover 371.25 degrees rather than 360, so it is not an acceptable normalization integral. |
| Bare-lamp reference | 2,000 umol/s and 144,400 lm | Unproven constants in `profile.py`; neither matches the IES 150,000 lm field nor controls the active anchor. Reject from production calibration. |
| SPD bridge | 0.0124977757525 umol/lm | Legacy approximate photopic/SPD diagnostic. Relative shape plus an approximate V-lambda function; do not use for absolute calibration. |
| Legacy pre-normalization PPF | 1,477.419968 umol/s | Buggy integrated lumens times SPD bridge. Diagnostic only. |
| Legacy `ies_scale` | 1.21658028111 | `1797.4 / 1477.419968`; passed to `ies2rad -m`. |
| Effectiveness | 1.0 default, clamped `[0,1]` | Backend fixes 1.0. Exclude dimming from the minimum fixed-output profile. |

**Phase 25A correction:** the original `117,927.390044` diagnostic used a
theta-space trapezoid over sampled `I(theta) * sin(theta)`. That quadrature is
close, but it is not the solid-angle-coordinate cell contract already approved
for the Conventional source. With the exact expanded HPS plane sequence and
the 360-degree row used only as closure, the shared contract gives
`118,057.25673187636 cd sr`. This correction changes a read-only diagnostic and
the unit-normalization divisor; it does not make IES magnitude an absolute PAR
authority or change the separately declared 1,797.4 umol/s operating point.

### 3.3 Absolute scaling audit

The old intent is documented as:

```text
IES lumens * SPD-derived umol/lm = baseline PPF
fixture anchor / baseline PPF = ies2rad multiplier
rtrace grey value = PPFD
```

That chain mixes two different carrier conventions. Radiance `ies2rad`
converts photometric candela through the uniform-white efficacy constant of
179 lm per carrier watt. The new repository correctly compensates by applying
`fixture_ppf * 179` to a unit-flux derived IES and then decoding without a
second 179 factor. The old HPS code passes only `1.21658028111` and also decodes
without 179.

Static closure gives:

```text
179 * 0.0124977757525 = 2.23710185969
118214.632529 lm * 1.21658028111 / 179 = 803.450228 carrier units
1797.4 / 803.450228 = 2.23710185969
```

Thus the active legacy source is inferred to be about **2.2371 times below its
declared PAR anchor**, before transport, subject to the exact generated
`ies2rad` contract. The defect is not “SPD applied twice.” The SPD bridge is
used once to form the multiplier, and grey normalization changes channels but
does not add another spectral scale. The defect is a lumen/photon versus
Radiance-white-carrier unit mismatch, compounded by the 371.25-degree legacy
flux integral. Because every LM-63 magnitude factor is 1.0 here, there is no
current HPS ballast/future-use double multiplier; the clean generic code must
still prove exactly-once factor handling.

No HPS target-based scale or amplitude correction occurs after tracing.
`SYM=1` is a post-trace spatial correction only, and it should be removed.

The clean operating-point policy is:

1. expand LM-63-declared quadrant symmetry without inventing new data;
2. integrate and normalize the transported downward angular table to unit flux;
3. neutralize IES lumens, watts, candela magnitude, ballast/future-use factors,
   and SPD amplitude as absolute PAR authorities;
4. apply exactly 1,797.4 umol/s per fixture before trace through the explicit
   `179 lm/W` grey-carrier derivation;
5. decode equal RGB directly, with no post-trace 179, SPD, PPF, target, or
   dimming multiplier; and
6. report 1,045 W, 1.72 umol/J, and 1,797.4 umol/s as declared modeled fields,
   separately from nominal lamp watts and IES test metadata.

## 4. Layout and mount policy

| Topic | Active legacy behavior | Clean Phase 25 rule |
|---|---|---|
| Room axes | Requested dimensions are reordered so the longer axis is X. | Preserve only if named explicitly; retain requested and aligned axes in the plan. |
| `4x4` meaning | `coverage_ft=4`; nominal 4 ft X and Y center pitch and `floor(room_axis_ft / 4)` count per aligned axis. | It means a nominal 4 ft x 4 ft coverage footprint/pitch policy, not 4 fixtures by 4 fixtures and not a measured photometric coverage guarantee. |
| Count formula | `nx=max(1,floor(length_ft/4))`, independently `ny=max(1,floor(width_ft/4))`. The 1 in margin does not shrink the count calculation. | Reimplement exactly for the declared comparison policy, then validate physical fit. Do not silently mutate counts. |
| Centers | Span is `4 ft * (count-1)`; centers run from `-span/2` in exact 4 ft increments. Y-major, X-minor order. | Preserve deterministic independent X/Y spacing and centering. |
| Edge behavior | The fixed-pitch grid is centered; it does not distribute remaining room space as equal wall/interfixture gaps and does not fill the nominal room with 4 ft cells. | Record actual wall gaps and usable footprint; do not call them 4 ft edge coverage. |
| Wall margin | Default 1 in. Fit requires center extrema plus half the 31.44 x 23.76 in footprint to remain inside room half-span minus 1 in. | Preserve as a minimum physical clearance validation. Reject too-small rooms rather than clip or rescale. |
| 10 x 10 ft example | 2 x 2 fixtures at `(x,y)=(+/-0.6096,+/-0.6096) m`; room-wall gaps to body edges are 20.28 in on X and 24.12 in on Y. | Useful deterministic test, not a numerical output golden. |
| Fixture footprint | Long X = 31.44 in = 0.798576 m; short Y = 23.76 in = 0.603504 m; height = 0 in. | Use exact IES dimensions. Height zero describes luminous-opening geometry, not necessarily physical housing thickness. |
| Orientation | Profile says horizontal lamp/long axis X. IES rotations default zero. | Fix long aperture axis to aligned X for this policy. Treat lamp construction/orientation as profile declaration unless better mechanical evidence is added. |
| Rotation safety | Nonzero `HPS_IES_ROT_X_DEG`/`HPS_IES_ROT_Z_DEG` rotates transport but not footprint checks or JSON body geometry. | Do not expose arbitrary rotation in Phase 25. A later rotation feature must validate transformed footprint and Type-C orientation. |
| Overlap/clipping | At 4 or 5 ft pitch the default fixture does not overlap. Tiny rooms can fail the wall-clearance check after the formula forces one fixture. | Typed failure; no clipping, overlap, clamp, rescale, or silent count reduction. |
| Visual geometry | JSON contains a 0.160 m lamp line and rectangular body. | Provenance/display only; do not treat as measured lamp or transport geometry. |
| Transport geometry | One IES polygon per fixture; no housing/reflector/arc geometry. | One downward rectangular aperture is appropriate for the supplied zero-height photometry. Defer housing occlusion. |

The mount value is ambiguous in legacy code. `HPS_Z_M` becomes both the JSON
lamp-line Z and the `xform` translation Z. The native generated aperture is
0.00025 m below that local origin, while canopy sensors default to world
`z=0.005 m`. Therefore legacy `0.4572` is approximately an IES-origin/lamp-line
world coordinate, not rigorously a lamp-to-canopy, fixture-center-to-canopy, or
aperture-to-receiver distance. Direct CLI fallback uses 0.9144 m instead.

For compatibility with the source-neutral Conventional policy, Phase 25 should
define:

```text
mount_height_m = vertical distance from emitting-aperture plane
                 to receiver/reference plane
default = 0.4572 m
```

The planner should accept `reference_plane_z_m`, derive
`aperture_plane_z_m = reference_plane_z_m + mount_height_m`, and state both
values in artifacts. With a zero-height IES aperture, fixture-center and
aperture-plane Z can coincide nominally, but the semantic reference must remain
the aperture. The old 0.9144 m direct-launch fallback should be provenance-only.

## 5. Approved IES audit

| Field | Finding |
|---|---|
| Absolute path | `/home/austin/Desktop/ies_sources/hps_1000w.ies` |
| SHA-256 / byte size | `05f449b6d5762fd1ae459a41df2cad510a680d5d9bbb9639ca0a26d3609002a3`; 2,578 bytes |
| Encoding/line endings | ASCII text, CRLF; 47 lines |
| LM-63 version | `IESNA:LM-63-2002` |
| Test / date | `[TEST] ANONYMIZED HPS PHOTOMETRY`; `[ISSUEDATE] ANONYMIZED` |
| Laboratory | `ANONYMIZED` |
| Manufacturer/product | `generic HPS reference`; `hps_1000w_fixture` |
| Luminaire | Formed aluminum housing, hammertone reflector |
| Lamp | generic nominal 1000 W HPS, stated lumen rating 150,000 lm |
| Ballast | `[OTHER] One HPS 1000 Electronic Switchable Ballast`; no dedicated ballast catalog or electrical settings |
| `TILT` | `NONE` |
| Lamp count / lumen field | 1 lamp / 150,000 lumens per lamp |
| Candela multiplier | 1.0 |
| Photometric type | 1 = Type C |
| Units type | 1 = feet |
| Luminous-opening dimensions | width 1.98 ft = 23.76 in = 0.603504 m; length 2.62 ft = 31.44 in = 0.798576 m; height 0.00 ft |
| Ballast / future-use factor | 1.0 / 1.0 |
| Input watts | 1,045.0 W |
| Vertical angles | 37 values, 0-90 degrees inclusive, 2.5-degree spacing |
| Horizontal angles | 5 values, 0-90 degrees inclusive, 22.5-degree spacing |
| Candela table | Exactly `37*5 = 185` values; minimum 0, maximum 55,945.5 cd; 5 zeros; no negatives or non-finite tokens |
| Numeric completeness | Exactly 240 tokens after `TILT`, equal to `13 + 37 + 5 + 185`; no missing or excess values |

The 0-90 C-plane range declares quadrant symmetry under LM-63 Type-C
conventions. It is not an incomplete 0-360 table and must not be processed as a
periodic five-plane ring. Phase 25 should mirror the measured planes to the
remaining quadrants, preserve boundary planes once, and create a duplicate
360-degree closure only in a derived normalized document. The vertical range
0-90 describes only the downward hemisphere; every 90-degree value is zero.

The legacy profile dimensions match the file exactly after feet-to-metre
conversion. Lamp count, nominal lamp wattage, fixture input watts, product,
housing/reflector description, and native one-polygon metadata also match.
Legacy claims of a double-ended lamp, exact 0.160 m arc, reflector materials,
and detailed cross-section are not supported by the IES.

### Source representation recommendation

HPS should use **one downward 0.798576 x 0.603504 m rectangular aperture per
fixture**, driven by the normalized whole-fixture Type-C angular distribution.
This is not merely borrowed from the Conventional approximation:

- the HPS numeric header itself has nonzero width/length and zero height;
- the table contains no upward hemisphere and is zero at 90 degrees;
- existing legacy conversion metadata records exactly one native polygon and
  no removed faces; and
- the photometry represents one complete lamp/reflector/ballast luminaire, not
  multiple independently measured surfaces.

A box or multi-surface luminous source would invent side/top emitting area that
the zero-height header does not declare. Detailed reflector/housing geometry
should be deferred until mechanical dimensions and validated occlusion effects
are available. The clean parser/stager must not execute `ies2rad` during tests;
future native conversion should be a planned `CommandSpec` run through
`LocalRunner`.

The existing new `fixtures/conventional_led/lm63.py` cannot parse this asset
unchanged: it accepts only LM-63-2019, metric units, positive height, complete
0-180 vertical angles, and explicit 0-360 closure. Those are adapter
restrictions, not reasons to reject the HPS IES. Generalize the shared LM-63
model for supported versions, imperial conversion, zero-height rectangular
openings, downward-only vertical domains, and declared symmetry compression;
keep Conventional's stricter approved-asset checks in its adapter.

## 6. SPD and calibration verdict

### 6.1 Active HPS SPD

| Field | Finding |
|---|---|
| Legacy path | `data/radiance/curve_data/hps/DEhps_1000w_spd.csv` |
| SHA-256 / byte size | `b0d55604b4c46c1143f530ff4d58414eb88b873941b62de80dcd60e6a35bcdf0`; 9,650 bytes |
| Structure | Header `wavelength_nm,relative_spd`; 401 data rows |
| Range/spacing | 380-780 nm inclusive at exactly 1 nm |
| Values | Relative radiant-power shape; min 0, max 1.0 at 570 nm; 399 positive rows from 381-779 nm; no negatives |
| PAR coverage | Complete 400-699 nm discrete PAR bins |
| Far-red coverage | Complete 700-750 nm fixed band; file continues to 780 nm |
| Binding | `HpsProfile.default_spd_file` and the sole `hps` comparator profile point to this file; FSPM mode resolution maps HPS to the `curve_data/hps` directory |
| Provenance | Repository documentation calls it digitized and relative. Git history begins at the public-release commit; no lamp catalog, laboratory, instrument, original graph/source, license, or acquisition record is embedded. |

The active absolute legacy service uses a trapezoidal wavelength-aware
photopic/photon bridge. Existing metadata reports total-power PAR fraction
0.9315347038, PAR centroid 593.2961776 nm, 369.6666115 lm/radiant-W,
4.620010414 umol/radiant-J, and 0.01249777575 umol/lm. These are diagnostics of
the relative curve and approximate V-lambda implementation, not measured lamp
efficacy.

The FSPM wavelength-resolved path converts interpolated relative power to
photon weights with `power * wavelength`, aligns to the Rex grid, and
normalizes PAR. A coarse older path merges 600-699 nm as `red` and normalizes
UV/PAR/far-red together; its file-derived fractions are UV 0.00174314, blue
0.04100498, green 0.46742389, red 0.44383918, and far-red 0.04598881. Its
hard-coded fallback HPS fractions (0.04/0.39/0.46/0.11) are not active while
the SPD exists and must be rejected.

### 6.2 Strict diagnostic photon fractions

Using the new repository's discrete policy (`relative_spd * wavelength_nm`,
PAR normalized over 400-699 nm) gives:

| Band | Interval | Photon fraction relative to PAR |
|---|---:|---:|
| Blue | 400-499 nm | 0.043060337780 |
| Green | 500-599 nm | 0.490853270802 |
| Orange | 600-624 nm | 0.253366507575 |
| Red | 625-699 nm | 0.212719883843 |
| Far-red | 700-750 nm | 0.048293971265 relative to PAR |

Blue + green + orange + red closes to 1.0. Orange and red are intentionally
separate. These are static audit diagnostics, not production constants; Phase
25 must recompute them from the packaged SPD hash through the shared strict
loader.

### 6.3 Calibration verdict

The SPD can serve as the modeled **relative spectral-shape input** while
absolute PAR PPF is anchored separately at 1,797.4 umol/s. Its amplitude,
photopic efficacy, and far-red content cannot change electrical input, PAR PPE,
or PAR PPF. Production provenance must say that it is a generic digitized
1000 W double-ended-HPS-labeled curve, not verified HPS spectroradiometry.

Scalar planning and spectral planning are ready. Native scalar results from the
old repository are not a trustworthy calibration reference because of the 179
carrier defect and D4 symmetrization. New native acceptance must be based on
source closure, command/source identities, and controlled analytic/synthetic
checks, not matching the old PPFD map.

## 7. HPS-weighted Rex implications

The new repository already has the required source-neutral core:

- `spectral/spd.py` and `spectral/distribution.py` for strict relative radiant
  SPD loading and PAR-normalized photons;
- `spectral/bands.py` for blue, green, orange, red, and far-red intervals;
- `optics/rex_weighting.py:RexSourceWeightingInput` and
  `compute_rex_source_weighted_interval_set` for scalar PAR and band A/T/R;
- `optics/rex_material_plan.py:build_rex_radiance_trans_intervals` for strict
  diffuse symmetric thin-leaf `trans` materials; and
- source-neutral isolated-run and absorbed-metrics inputs in
  `transport/five_band.py` and `transport/five_band_absorption.py`.

An in-memory audit calculation using the exact HPS SPD and the packaged Rex
`mean_of_treatments` profile produced these diagnostics:

| Interval | Absorptance | Transmittance | Reflectance |
|---|---:|---:|---:|
| Scalar PAR | 0.760239334 | 0.132008636 | 0.107752031 |
| Blue | 0.900230589 | 0.031536987 | 0.068232424 |
| Green | 0.696458801 | 0.169136715 | 0.134404484 |
| Orange | 0.781662632 | 0.122102957 | 0.096234411 |
| Red | 0.853955760 | 0.078186864 | 0.067857375 |
| Far-red | 0.209790768 | 0.448543107 | 0.341666125 |

The Rex profile starts at 404 nm, so 400-403 nm contribute 1.084684676 umol/s
at the declared fixture PPF, or 0.000603474 of PAR, to the explicit unsupported
source-mass diagnostic. No fill or extrapolation is needed or allowed.

These values are audit-only and must not be copied into production constants.
Phase 25 should create a HPS source wrapper that supplies a distinct source
model ID, SPD hash, normalization policy, and absolute 1,797.4 umol/s photon
distribution to the source-neutral Rex functions. It should then build one
scalar material plus five band materials. SMD- and Conventional-weighted
payloads and material IDs must be rejected by type/identity validation.

For native transport, each run must use:

1. the same HPS angular data identity and fixture layout;
2. the interval's absolute per-fixture photon flux before trace;
3. the matching HPS-weighted Rex material;
4. its own octree and ambient cache because source amplitude and leaf material
   change by interval;
5. equal-grey scalar carriers and strict R=G=B decoding; and
6. the existing source-neutral absorbed-photon formulas, where blue + green +
   orange + red form PAR and far-red remains separate.

The old approach of tracing the full-PAR source in every band and multiplying
the decoded result by a fraction is linear but no longer matches the new
pre-trace absolute-source contract and should not be ported.

## 8. New-architecture mapping

| Requirement | Reusable new component | HPS-specific or shared change needed |
|---|---|---|
| Resource/provenance records | Conventional `resources.py`/`profile.py` patterns and deterministic SHA-256 identities | Add approved HPS IES/SPD records, physical-test metadata, declared operating point, and limitations. Do not reuse Conventional IDs or authorization claims. |
| LM-63 parsing | Conventional `Lm63Photometry` typed structure and strict failure model | Generalize into a source-neutral parser supporting LM-63-2002, units type 1, zero-height rectangular openings, 0-90 downward vertical range, and LM-63 symmetry expansion. Retain Conventional strict adapter behavior. |
| Angular normalization | Conventional cell solid-angle integration, factor record, and derived-IES normalization policy | Expand HPS's quadrant symmetry to full closure once; normalize the 0-90 downward domain; create HPS angular IDs. Do not use the legacy `_angle_widths` implementation. |
| Layout planning | New typed room/position/identity conventions and `geometry/rect_layout.py` utilities | Add a HPS 4 ft coverage-grid policy with 1 in clearance, fixed long-X orientation, explicit requested/aligned axes, mount planes, wall gaps, and typed no-fit errors. Conventional's SMD-derived practical-gap policy is not HPS behavior. |
| Source representation | Conventional derived-unit-IES and one-aperture source-planning pattern | Parameterize the shared one-aperture adapter for the zero-height HPS opening, HPS dimensions/IDs, and 1,797.4 umol/s. Do not validate against a six-face box contract. |
| Radiance commands | `radiance.commands.CommandSpec`, `radiance.runner.LocalRunner`, executable resolution, options, explicit stdin/stdout/cwd | Reuse unchanged. Any future `ies2rad`, `oconv`, or `rtrace` call must flow through these boundaries. |
| Scalar scene/decode | Source-neutral room/sensor contracts, `transport/scenes.py`, `transport/scalar_ppfd.py`, and Conventional scalar executor behavior | Add a HPS adapter/plan; keep baseline room+emitters only and FSPM room+emitters+plants. Prohibit symmetrization and target scaling. |
| Spectral payload | `spectral/*`, Conventional `band_source.py` pattern | Add HPS IDs and budgets from 1,797.4 umol/s plus recomputed fractions; far-red is relative to PAR and not part of PAR closure. |
| Rex weighting/materials | `RexSourceWeightingInput`, `compute_rex_source_weighted_interval_set`, `build_rex_radiance_trans_intervals` | Thin HPS wrapper enforcing the HPS SPD/source identity and HPS material prefix. |
| Isolated scalar/five-band plan | `SourceNeutralIsolatedTransportInput`, `IsolatedTransportEmitterGroup`, Conventional six-run bundle pattern | Adapt one HPS fixture-array group. Extract shared planner/executor pieces rather than clone `conventional_rex*.py`. |
| Native execution | `LocalRunner`, Conventional scalar and Rex execution command/provenance/validation mechanics | Defer from minimum Phase 25; later generalize source providers and keep a single execution framework. |
| Absorbed metrics | Source-neutral five-band absorbed-metrics input and formulas; receiver identities/NPZ validation | Add a HPS adapter carrying HPS source/material IDs. Never reuse Conventional or SMD coefficients. |

The genuinely HPS-specific production additions are only: two resources and
their provenance; a typed HPS comparison profile; the 4 ft coverage layout
policy; HPS angular/source IDs and zero-height aperture contract; the fixed
1,045 W/1.72/1,797.4 operating-point record; the HPS SPD wrapper/band budget;
and HPS-specific Rex/material identity wrappers. LM-63 generalization,
one-aperture source generalization, command execution, receiver parsing, and
absorbed metrics should remain shared.

## 9. Preserve/reimplement/reuse/reject/defer table

“Preserve” means a byte-identical file under the future manual salvage root for
auditability, never importable production code.

| Legacy/new component | Classification | Disposition |
|---|---|---|
| Legacy `hps_generation/profile.py` | **Preserve as local salvage reference; reimplement cleanly** | Authoritative compact record of active identity and declared values. Reject detailed reflector/arc/material constants and double-ended claim as physical evidence. |
| Legacy `hps_generation/geometry.py` | **Preserve as local salvage reference; reimplement cleanly** | Preserve coverage count/centering/order and clearance behavior; replace dict/env coupling with typed plans and named mount planes. |
| Legacy `hps_generation/rad_writer.py` | **Preserve as local salvage reference; reimplement cleanly** | Captures one `xform` per fixture and equal-grey policy. Replace shell-like `!xform` text dependency with deterministic source planning and shared command boundaries. |
| Legacy `hps_generation/service.py` | **Preserve as local salvage reference; reject literal port** | End-to-end active builder and calibration evidence. It has import-time directory creation, globals/env, `SystemExit`, direct `ies2rad`, the carrier defect, and runtime report coupling. |
| Legacy `ies_photon_toolkit.py` | **Preserve as local salvage reference; reject numerical implementation** | Useful IES/SPD field inventory and generated-source inspection. Reject 371.25-degree integration and direct subprocess ownership. |
| Legacy `backend/env.py` HPS symbols | **Preserve as local salvage reference; reject mechanism** | Captures actual application defaults and fixed-output inputs. Do not port backend DTO/env plumbing. |
| Legacy `cli/scripts.py` HPS/common symbols | **Preserve as local salvage reference; reject monolith** | Captures commands, decode, power reporting, FSPM path, and invalid D4 symmetrization. Reuse new command/runner/transport layers. |
| Legacy `docs/radiance/hps_ies.md` | **Preserve as local salvage reference** | Useful statement of intended IES/SPD/anchor roles. Other variants and surrogate path are historical/out of scope; do not treat them as current code. |
| Legacy HPS SPD | **Preserve and promote as a production resource** | Exact relative modeled spectral shape. Record generic/digitized provenance limitations; never use amplitude for PPF/power. |
| Approved external HPS IES | **Preserve and promote as a production resource** | Exact measured angular/footprint/test asset. Record visible metadata, hash, byte size, original path, and missing license/acquisition details. |
| Legacy focused HPS tests and validator | **Preserve as local salvage references; reimplement focused tests** | Useful grey-source, HPS-only, command/cache, source-geometry, and fixed-output assertions. Do not run or copy them as new tests. |
| Legacy bare-lamp 2,000 umol/s / 144,400 lm constants | **Reject** | Unproven, inconsistent with the IES lumen field, and inactive as final calibration. |
| Legacy `DEFAULT_MOUNT_Z_M=0.4572` and CLI 0.9144 fallback | **Reimplement cleanly / reject fallback** | Adopt 0.4572 m only as an explicit aperture-to-reference policy. Keep 0.9144 m as provenance. |
| Legacy quadrant integration and `ies_scale` | **Reject** | Incorrect 371.25-degree weights and missing 179 carrier bridge. |
| Legacy D4 PPFD symmetrization | **Reject** | Can erase measured/oriented HPS asymmetry. |
| Legacy JSON lamp arc/body, reflector constants, overlay geometry | **Defer** | Visual/reference reconstruction without sufficient mechanical or optical evidence. |
| Legacy precomputed bundles, maps, octrees, DAT/CAL/RAD files, summaries | **Reject** | Generated by the defective absolute path and outside source evidence. |
| Legacy web/backend/workspace/viewer/deployment layers | **Reject** | Application and presentation infrastructure. |
| New `radiance/{commands,runner,options,executables}.py` | **Reuse from new repository** | Existing safe command boundary; no HPS subprocess layer. |
| New `spectral/{spd,distribution,bands}.py` | **Reuse from new repository** | Strict source-neutral SPD and band pipeline. |
| New `optics/rex_weighting.py` and `rex_material_plan.py` | **Reuse from new repository** | Source-neutral A/T/R and material construction; add only HPS identity wrappers. |
| New `transport/five_band.py` source-neutral inputs and five-band absorption | **Reuse from new repository** | Use generic inputs/formulas; do not use SMD plan types or coefficients. |
| New Conventional LM-63/angular/staging/source modules | **Reuse after source-neutral generalization** | Preserve Conventional adapter contracts while extracting support for HPS's valid LM-63 subset and native one-aperture geometry. |
| New Conventional scalar/Rex native executors | **Defer then generalize** | Minimum Phase 25 remains Radiance-free. A later subphase should extract a source-provider adapter rather than duplicate these modules. |

## 10. Manual salvage inventory

At the start of Phase 25, copy only the following files unchanged under
`.salvage_source/phase25-hps/`. A machine-readable manifest should record
the original absolute path, repository-relative path, recommended destination,
legacy commit `a900d361d368dbfea40eadf9836a3fe3947d7ab8`, byte size, and verified
SHA-256. Any mismatch must stop the salvage operation.

| Original absolute path | Repository-relative path | Recommended salvage-relative destination | Role | Known limitations | Required SHA-256 |
|---|---|---|---|---|---|
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/profile.py` | `src/rad_rebuild/radiance/engine/emitters/hps_generation/profile.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/profile.py` | Active identity, dimensions, operating inputs, layout defaults | Contains unsupported detailed reflector/arc/material and double-ended-surrogate claims | `d5281bae07f5563867e3cf92b3a420bfc7ef1088ec5954d5db316e2411378337` |
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/geometry.py` | `src/rad_rebuild/radiance/engine/emitters/hps_generation/geometry.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/geometry.py` | Active coverage grid, ordering, clearance, JSON reference geometry | Dict-based; aligns axes implicitly; visual/transport mismatch; rotation not checked | `ba874f9b5df474030bb74d6c80d1c2977b09d31d51b895bcfe2645678ca3ddad` |
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/rad_writer.py` | `src/rad_rebuild/radiance/engine/emitters/hps_generation/rad_writer.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/rad_writer.py` | Active grey normalization and one-source transform writer | Mutates generated RAD; inherits broken absolute scale; `!xform` indirection | `fb5af097ad94e8d825cf45109a9fd9e4a3800d22deb29fa54f245ba3666d12bb` |
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/service.py` | `src/rad_rebuild/radiance/engine/emitters/hps_generation/service.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/emitters/hps_generation/service.py` | Active IES/SPD/calibration/source assembly | Import-time writes, env/globals, direct conversion, carrier defect, runtime-output coupling | `568b52e061ab92a70fe51b36624e3b8192e8b186c30ea72ca40af0e834c22811` |
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/photometry/ies_photon_toolkit.py` | `src/rad_rebuild/radiance/engine/photometry/ies_photon_toolkit.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/photometry/ies_photon_toolkit.py` | Parser/converter/SPD bridge and generated-source inspection reference | Incorrect quadrant/periodic integration; approximate photopic bridge; direct subprocess | `c41c5297e5c25bee4b6cadfe002bb690093ff5e24b884a7d237ced4e70bd0fc7` |
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/cli/scripts.py` | `src/rad_rebuild/radiance/cli/scripts.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/cli/scripts.py` | Actual HPS scene, command, decode, reporting, and Rex orchestration trace | Large application monolith; conflicting mount default; invalid D4 symmetrization; plants can enter baseline | `3e9209c07754134d9bda4601992c86957210fb0cd292935ec4124416928df2ff` |
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/backend/env.py` | `src/rad_rebuild/radiance/backend/env.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/backend/env.py` | Application-selected HPS inputs and 0.4572 m behavior | Backend/SMD coupling, env transport, unused HPS NX/NY | `779d4c8ff9c3d32988e131b5e021b438b8c7618e877946b4c060ee514bde3f5c` |
| `/home/austin/Desktop/horticulture-lighting-simulator/docs/radiance/hps_ies.md` | `docs/radiance/hps_ies.md` | `horticulture-lighting-simulator/docs/radiance/hps_ies.md` | Legacy scientific intent and provenance narrative | Mentions out-of-scope variants and obsolete selectable paths; understates carrier problem | `f784891b70f44a53928e3aba769fecd1040418c990f25174476bea9d3b49edb0` |
| `/home/austin/Desktop/horticulture-lighting-simulator/data/radiance/curve_data/hps/DEhps_1000w_spd.csv` | `data/radiance/curve_data/hps/DEhps_1000w_spd.csv` | `horticulture-lighting-simulator/data/radiance/curve_data/hps/DEhps_1000w_spd.csv` | Superseded Phase 24 relative SPD evidence; not an active resource | Generic/digitized; no exact HPS lamp binding or absolute units/source citation | `b0d55604b4c46c1143f530ff4d58414eb88b873941b62de80dcd60e6a35bcdf0` |
| `/home/austin/Desktop/horticulture-lighting-simulator/tests/radiance/test_hps_fixture_profile.py` | `tests/radiance/test_hps_fixture_profile.py` | `horticulture-lighting-simulator/tests/radiance/test_hps_fixture_profile.py` | HPS-only and grey-normalization regression reference | Tests mutation of synthetic `ies2rad` output, not absolute closure or real asset parsing | `16e51e78a97808901410d4274276b5b985ccc98bd7189747e6ca06081ee7e259` |
| `/home/austin/Desktop/horticulture-lighting-simulator/tests/radiance/test_phase125b_cli_orchestration.py` | `tests/radiance/test_phase125b_cli_orchestration.py` | `horticulture-lighting-simulator/tests/radiance/test_phase125b_cli_orchestration.py` | Mocked HPS command/cache/fixed-output orchestration reference | Very broad legacy test file; preserve only for named HPS test context, never collect in new suite | `9aca462c45deaeb20bf1972b175b4edcd25e3ad1a1bb6afbb7c2aec987dd4aa0` |
| `/home/austin/Desktop/horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/validation/validate_hps_ies.py` | `src/rad_rebuild/radiance/engine/validation/validate_hps_ies.py` | `horticulture-lighting-simulator/src/rad_rebuild/radiance/engine/validation/validate_hps_ies.py` | Expected native polygon/flatcorr and mount sensitivity reference | Executes Radiance in normal use; output-based validation inherits old calibration | `58556d707f7bfc2b0b5b98f9805a6772a6c38bfef68089cecd8ebdf99d003eff` |
| `/home/austin/Desktop/ies_sources/hps_1000w.ies` | Not repository-relative (external approved input) | `external/ies_sources/hps_1000w.ies` | Exact approved manufacturer/test photometry and future production resource source | No embedded license/download/acquisition record; no PAR/SPD; no detailed housing geometry | `05f449b6d5762fd1ae459a41df2cad510a680d5d9bbb9639ca0a26d3609002a3` |

Do not salvage `generate_emitters_hps.py`, `summary.py`, shell launchers,
precomputed bundles, generated RAD/DAT/CAL files, octrees, ambient caches,
PPFD maps, runtime JSON/text, viewer assets, web/API files, deployment code, or
other HPS variants.

### Production package resources

The smaller production resource set is exactly:

| Source | Recommended package destination | Production role |
|---|---|---|
| External `hps_1000w.ies` | `src/fspm_optics/resources/data/hps/hps_1000w.ies` | Approved angular distribution, dimensions, sanitized metadata, and nominal lamp/system values |
| Superseded Phase 24 `DEhps_1000w_spd.csv` | Removed former packaged path | Historical relative radiant spectral-shape evidence only; not active |

Phase 25 should verify byte identity against the hashes above and add package
metadata. The user has designated the IES as the approved input for this work,
but the file itself supplies no license or redistribution terms; record that
absence rather than inventing authorization details. The SPD likewise needs an
explicit provenance limitation in its resource record.

## 11. Proposed Phase 25 boundary

The smallest safe Phase 25 is a complete **Radiance-free HPS profile and
planning phase**:

1. **Manual salvage and production resources:** copy only the inventory above,
   create a checksum/provenance manifest, package the two production resources,
   and verify exact bytes.
2. **Shared LM-63 generalization:** extract or extend the new typed parser and
   angular normalization so valid LM-63-2002 imperial quadrant-symmetric,
   downward-only, zero-height rectangular photometry is supported. Keep the
   existing Conventional approved subset and tests intact.
3. **Typed HPS profile:** add immutable physical-test asset records, opaque
   `HPS-8` catalog identity, scenario claim boundary, declared 1,045 W / 1.72
   umol/J / 1,797.4 umol/s operating point, dimensions, source orientation,
   hashes, and limitations. Separate lamp nominal watts, IES test/system watts,
   and modeled inputs.
4. **Layout/mount planner:** implement the named `coverage_4ft_center_pitch_v1`
   policy, 1 in minimum clearance, independent X/Y counts, deterministic
   centers/order, long-X orientation, requested/aligned axes, actual wall gaps,
   explicit reference/aperture planes, and typed failures. Default to 0.4572 m
   aperture-to-reference distance.
5. **IES normalization/source representation:** expand quadrant symmetry,
   normalize downward flux to one, build a deterministic derived IES plan,
   derive the 1,797.4 * 179 carrier multiplier, and plan one downward rectangle
   per fixture. Record that raw lumens/watts/SPD do not set source magnitude.
6. **Spectral payload:** strict-load the packaged SPD, recompute PAR-normalized
   photons and five band budgets, keep far-red relative to PAR, and create
   HPS-specific source/payload identities.
7. **HPS-weighted Rex optics:** call the existing source-neutral weighting
   and material functions to produce scalar and five-band A/T/R/material plans
   with HPS-only identities and unsupported-source-mass diagnostics.
8. **Scalar and five-band planning:** adapt
   `SourceNeutralIsolatedTransportInput` for one HPS fixture-array group and
   produce deterministic future scene/command/cache/artifact plans. Reuse
   `CommandSpec`; do not execute it.
9. **Radiance-free tests:** cover exact resource hashes/metadata, parser failure
   cases, symmetry expansion, angular normalization closure, 179 carrier
   closure, operating-point separation, layout/mount examples and no-fit
   errors, one-aperture source count, SPD fractions, orange/red separation,
   HPS Rex A/T/R closure, identity rejection for SMD/Conventional payloads,
   baseline/FSPM scene separation, deterministic argv, and proof that no runner
   was called.

Suggested production surfaces are a narrow `fixtures/hps/` package plus
shared LM-63/source generalization. Do not add aliases, web schemas,
environment parsing, playback, viewers, or deployment logic.

A later Phase 25B may generalize the existing Conventional scalar and six-run
Rex executors to consume the source-neutral HPS plans, then perform native
scalar/five-band execution and absorbed metrics. It must use isolated octrees
and ambient caches, pre-trace absolute band flux, strict equal-grey decode, and
no symmetrization or target scaling. This split avoids making native execution
a prerequisite for establishing the correct scientific source contract.

## 12. Risks and blockers

### Blocking correctness gates for implementation

1. **Carrier closure:** Phase 25 must prove unit downward angular flux and
   exactly one `1797.4 * 179` carrier application. No legacy result may be used
   as a target golden.
2. **LM-63 symmetry/version support:** parser and normalization must implement
   0-90 quadrant symmetry and downward-only vertical coverage correctly while
   preserving Conventional's full-table behavior.
3. **Mount semantics:** 0.4572 m must be approved in code as
   aperture-to-reference distance, not inherited as an unnamed world Z.
4. **Identity separation:** nominal lamp 1000 W, IES input 1,045 W, declared
   system input 1,045 W, assumed PPE 1.72, and derived PPF 1,797.4 must occupy
   distinct typed fields and artifact roles.
5. **No cross-source optics:** HPS SPD, Rex coefficients, materials, source
   payloads, and IDs must not accept SMD or Conventional identities.

### Material limitations and non-blocking risks

- The 1.72 umol/J anchor is a declared comparison assumption with no
  HPS-specific measurement in the audited files. It is adequate for a
  controlled scenario only.
- The SPD is generic, digitized, and labeled double-ended, while the IES does
  not establish a double-ended lamp or exact lamp catalog. Exact physical HPS
  spectral claims remain unsupported.
- `HPS-8` is not decoded by available evidence. Inferring eight emitters or
  any geometric meaning would be fabrication.
- The IES gives input watts but no voltage, current, PF, ballast setting, or
  uncertainty. A full ballast/electrical model is not available.
- The zero-height luminous opening supports an aperture model but does not
  describe housing depth, reflector occlusion, lamp position, or near-field
  source distribution.
- Fixed long-X orientation is a declared comparison convention. The IES
  metadata itself does not state lamp-axis orientation.
- The relative SPD has no embedded source citation or license. The external IES
  has visible test provenance but no license/acquisition record. Distribution
  metadata must state what is unknown.
- Rex optics are digitized research data and represent a diffuse symmetric
  infinitely thin leaf model, not measured angular BSDF behavior for a HPS
  crop experiment.
- The Rex grid omits 400-403 nm; retain the explicit 0.06035% unsupported PAR
  mass diagnostic rather than filling it.

### Deferred native validation

Native execution is not a blocker for the Phase 25 planning boundary. Before
claiming a native HPS comparison, a later subphase must validate derived IES
structure, one-polygon orientation, DAT binding, analytic source closure,
scalar/four-band incident closure, far-red separation, command provenance, and
absorbed-photon conservation. It must not validate by reproducing the old
symmetrized PPFD bundle.

## 13. Final recommendation

Proceed with Phase 25 as a Radiance-free clean port of the measured-shape,
declared-operating-point HPS comparator.

- **Model identity:** generic HPS
  `hps_1000w_fixture` tested whole-fixture Type-C photometric shape and
  zero-height rectangular opening, combined with a declared fixed comparison
  point of **1,045 W system input, 1.72 umol/J PAR PPE, and 1,797.4 umol/s PAR
  PPF per fixture**. This is not a measured PAR-performance claim.
- **Layout/mount:** `4x4` means a nominal 4 ft x 4 ft center-pitch/coverage
  policy, with independent floored X/Y counts, centered placement, long axis X,
  and 1 in minimum physical clearance. Define **0.4572 m from emitting aperture
  to receiver/reference plane**; reject the 0.9144 m hidden CLI fallback.
- **Source representation:** one downward **0.798576 x 0.603504 m rectangular
  aperture per fixture**, driven by quadrant-expanded, unit-downward-flux
  HPS photometry. Do not create a six-face box or detailed reflector source.
- **Scalar readiness:** profile/layout/source planning is ready, but legacy
  scalar output is not a calibration reference because the old path omitted
  the 179 carrier bridge and then symmetrized the map.
- **Spectral readiness:** the exact SPD is suitable as a relative modeled shape;
  strict five-band fractions, HPS-weighted Rex materials, and source-neutral
  absorbed-metric plans can be recomputed now. Orange and red remain separate.
- **Phase boundary:** implement resources, typed science, deterministic plans,
  and Radiance-free tests in Phase 25. Defer native scalar/five-band execution
  to a small adapter/generalization subphase.

Do not port the legacy lumen/SPD absolute bridge, 371.25-degree integration,
dimensionless `ies2rad` scale, D4 symmetrization, hidden mount default,
generated bundles, fallback spectral fractions, bare-lamp constants, or
visual reflector/arc reconstruction.
