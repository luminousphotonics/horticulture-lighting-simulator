# SMD Source and Rex Leaf Weighting Policy

Status: draft implementation specification  
Target file: `docs/spectral/smd_source_and_rex_leaf_weighting_policy.md`  
Scope: Proposed LED/SMD source SPD model, five-band photon distribution, Rex leaf source-weighted A/T/R coefficients, strict diffuse-only Radiance `trans` material planning, sequential native receiver-smoke orchestration, and validated absorbed-photon post-processing
Non-goal: Photosynthesis equations, DLI, growth dynamics, or physiological response modeling

---

## 1. Purpose

This document defines the spectral source and leaf-optics weighting policy for `fspm-optics-engine`.

The immediate goal is to prevent a known scientific failure mode from the legacy project:

```text
Do not equal-weight peak-normalized SMD SPD files.
```

The new engine must use an explicit component manifest and a documented physical normalization policy before computing five-band source photon fractions or Rex leaf optical coefficients.

---

## 2. Critical audit conclusion

The Proposed LED/SMD module has three nonzero spectral emitter components:

```text
warm_white_3000k: 52 packages
cool_white_5000k: 52 packages
deep_red_660nm:   41 packages
```

There is no discrete blue emitter. Blue photons come from the warm-white and cool-white SPD curves.

The legacy plant-optics path combined the three actual SPD files with weight `1.0` each. That produced an equal-peak-amplitude mixture. This is not acceptable as the default source model because the three components do not contribute equal nominal PAR photon output.

---

## 3. Required SMD source data files

The source model packages and checksums only the three manuscript SMD SPDs.
The legacy current/PPE and forward-voltage curves were audited and removed:
they had no active caller that affected relative channel balance and cannot be
an absolute efficacy authority.

Expected resource location in the new repo:

```text
src/fspm_optics/resources/data/smd/
```

Required files:

```text
smd_3000k_spd.csv
smd_5000k_spd.csv
smd_660nm_spd.csv
```

### 3.1 SPD CSV schema

The three SPD files have headers:

```text
wavelength_nm
relative_spd
```

Meaning:

```text
wavelength_nm = wavelength in nanometres
relative_spd  = nonnegative relative radiant spectral-power shape
```

These SPDs are relative curves. They peak near `1.0`. They are not absolute spectroradiometric measurements.

---

## 4. Explicit SMD component manifest

Do not infer component identity from filenames.

Define explicit component records:

```text
component_id
label
count
nominal_package_watts
nominal_ppe_umol_per_j
spd_resource
spectral_role
```

Default SMD component manifest:

| component_id | label | count | nominal_package_watts | nominal_ppe_umol_per_j | spd_resource |
|---|---:|---:|---:|---:|---|
| warm_white_3000k | Warm white 3000 K | 52 | 0.68 | 2.73 | smd_3000k_spd.csv |
| cool_white_5000k | Cool white 5000 K | 52 | 0.68 | 2.81 | smd_5000k_spd.csv |
| deep_red_660nm | Deep red 660 nm | 41 | 0.44 | 4.13 | smd_660nm_spd.csv |

No source model code should silently discover SPDs from a directory.

---

## 5. Default normalization policy

The component-curve policy is:

```text
relative_component_and_spd_mix_only_then_internal_fixture_ppe_anchor
```

For each component:

```text
Q_nom,j = package_count_j × nominal_package_watts_j × nominal_ppe_j
```

Default nominal values:

| component_id | Q_nom_umol_s | fraction_of_nominal_PAR_PPF |
|---|---:|---:|
| warm_white_3000k | 96.5328 | 0.3570005281 |
| cool_white_5000k | 99.3616 | 0.3674620820 |
| deep_red_660nm | 74.5052 | 0.2755373898 |
| total | 270.3996 | 1.0 |

These raw values determine relative component and channel fractions only. They
are not an absolute fixture-PPE authority. After the relative mixture is
formed, all PAR channels are normalized together to the calibrated internal
source PPE. Far-red remains a ratio outside that PAR anchor.

---

## 6. Radiant SPD to photon distribution

The SPD files are relative radiant spectral-power curves.

For photon weighting:

```text
photon_weight(lambda) proportional to relative_radiant_power(lambda) × lambda
```

Physical constants cancel when computing normalized photon fractions, so the implementation may use `relative_spd × wavelength_nm` for relative photon weighting.

Do not use photopic weighting.

Do not use equal wavelength weighting for the SMD source model.

---

## 7. Component SPD normalization

For each component:

1. Load strict SPD samples.
2. Validate nonnegative finite wavelengths and values.
3. Convert radiant shape to photon-weighted shape:

```text
component_photon_shape(lambda) = relative_spd(lambda) × lambda
```

4. Normalize the component over PAR:

```text
400 <= lambda < 700
```

5. Scale the normalized component by `Q_nom,j`.

6. Sum all scaled components to produce the total source photon distribution.

Far-red is not part of the PAR normalization. It should be reported relative to total PAR photon output.

---

## 8. Five fixed transport bands

The simulator uses exactly five transport bands.

Band IDs and ranges are fixed:

| band_id | label | start_nm | end_nm | interval |
|---|---|---:|---:|---|
| blue | Blue | 400 | 499 | inclusive integer bin |
| green | Green | 500 | 599 | inclusive integer bin |
| orange | Orange | 600 | 624 | inclusive integer bin |
| red | Red | 625 | 699 | inclusive integer bin |
| far_red | Far-red | 700 | 750 | inclusive integer bin |

Implementation may represent these as closed integer bin ranges or equivalent continuous half-open intervals:

```text
blue:    400 <= lambda < 500
green:   500 <= lambda < 600
orange:  600 <= lambda < 625
red:     625 <= lambda < 700
far_red: 700 <= lambda <= 750 or 700 <= lambda < 751 for integer-grid data
```

The red and orange bands must remain separate.

Do not use any legacy helper that combines 600–699 nm into one red band.

Do not use inspection-only Rex band files whose orange/red boundaries differ from this policy.

---

## 9. PAR and far-red conventions

PAR normalization:

```text
400 <= lambda < 700
```

Because the Rex optical profile starts at 404 nm, Rex-weighted integrations practically operate over:

```text
404 <= lambda < 700
```

when interpolated onto the Rex grid.

Far-red:

```text
700–750 nm
```

Far-red should be reported as:

```text
FR photon amount relative to PAR
```

not as a fraction of PAR+FR unless explicitly labeled.

---

## 10. Source photon fraction payload

The SMD source model should produce a serializable payload:

```text
schema_version
source_model_id
normalization_policy
components
component_nominal_PAR_PPF
component_PAR_fractions
band_photon_fractions_relative_to_PAR
far_red_relative_to_PAR
par_normalization_wavelength_range
resource_hashes
limitations
```

The expected diagnostic approximate band fractions under nominal component PAR photon output are:

| band_id | photon fraction relative to PAR |
|---|---:|
| blue | approximately 0.125693 |
| green | approximately 0.336604 |
| orange | approximately 0.113724 |
| red | approximately 0.423979 |
| far_red | approximately 0.014267 |

These values should be used as rough checks, not blindly copied as hard goldens until the new implementation reproduces them from packaged data.

---

## 11. Rex leaf optical source weighting

Rex leaf optical coefficients must be weighted by source photon distribution.

The Rex runtime profile is the `mean_of_treatments` model on a 404–797 nm grid.

Use model columns:

```text
absorptance_fraction_model
transmittance_fraction_model
reflectance_fraction_model
```

Do not use raw audit columns for simulator coefficients.

At each wavelength:

```text
A_lambda + T_lambda + R_lambda = 1
```

within tolerance.

### 11.1 Scalar PAR-weighted coefficients

For scalar PAR coefficients:

```text
R_eff = sum(p_lambda R_lambda) / sum(p_lambda)
T_eff = sum(p_lambda T_lambda) / sum(p_lambda)
A_eff = sum(p_lambda A_lambda) / sum(p_lambda)
```

where `p_lambda` is positive source photon weight and `400 <= lambda < 700`.

### 11.2 Band-specific coefficients

For each band:

```text
R_band = sum(p_lambda R_lambda) / sum(p_lambda within band)
T_band = sum(p_lambda T_lambda) / sum(p_lambda within band)
A_band = sum(p_lambda A_lambda) / sum(p_lambda within band)
```

A zero-source band should produce no material coefficients and no receiver requirement.

---

## 12. Closure and validation

For every scalar or band coefficient set:

```text
0 <= A,T,R <= 1
A + T + R approximately 1
```

Record diagnostics:

```text
raw_absorptance
raw_transmittance
raw_reflectance
raw_sum
closure_error
normalization_applied
```

If `raw_sum` is not close to `1.0`, fail clearly rather than silently normalizing a bad profile.

Default closure tolerance:

```text
±0.05 for raw data
floating-point tolerance for normalized simulator coefficients
```

---

## 13. Prohibited legacy behavior

Do not port:

```text
_candidate_spd_files
_curve_file_weight
fixture_spectral_distribution_from_curve_data unchanged
default_fixture_spectral_distribution
default_leafy_green_spectral_bands
filename-token weighting
permissive SPD parsing
silent fallback to invented fractions
equal-weight peak-normalized SPD mixing
```

Do not silently fall back if a required source file is missing.

Do not infer component identity from filename.

Do not use photopic weighting.

---

## 14. Known limitations

The first implementation is a nominal source model.

Known limitations:

- Source SPDs are relative digitized curves, not absolute spectroradiometry.
- Datasheet/source provenance for the SPD curves is incomplete.
- Spectral shape is fixed with current and temperature.
- Nominal component watt/PPE inputs affect relative channel balance only; they
  do not set absolute source or completed-fixture efficacy.
- PMMA transmission is treated as spectrally flat for now.
- No wavelength-dependent PMMA/PTFE/optic transmission yet.
- No thermal spectral shift.
- No drive-current spectral shift.
- Rex A/T/R data are digitized and remain unvalidated against this simulator’s physical crop environment.

These limitations must be recorded in output payloads.

---

## 15. Implementation phases

### Phase 15A: source policy document

Create this document.

### Phase 15B: package SMD curve resources

Package all seven SMD CSV files under explicit package resources.

### Phase 15C: strict SMD source model

Implement:

```text
sources/smd/profile.py
sources/smd/spd.py
spectral/bands.py
spectral/distribution.py
```

### Phase 15D: Rex source-weighted A/T/R

Implement:

```text
leaves/rex/profile.py or use existing optics/profiles.py
leaves/weighting.py or optics/rex_leaf_bands.py
```

### Phase 16: material planning

Create band material plans for Radiance transport.

### Phase 17: banded receiver transport

Run 5-band receiver transport.

---

## 16. Acceptance criteria for source model

The source model is acceptable when:

1. All seven SMD CSV resources are packaged and checksum-tested.
2. Component identity is explicit and not inferred from filenames.
3. The three source components are count-weighted and nominal-PAR-photon weighted.
4. The equal-peak legacy mixture is not used as default.
5. B/G/O/R/FR bands exactly match the simulator policy.
6. Source photon fractions are computed from data, not hard-coded.
7. Band fractions are deterministic and serialized with provenance.
8. The source model exposes limitations and normalization policy.
9. Tests prove the actual three SMD curves are not equal-weighted.
10. No Radiance execution is required for the source model.

---

## 17. Approved diffuse-only Radiance `trans` mapping

The approved material policy identifier is:

```text
diffuse_only_symmetric_thin_leaf_energy_partition
```

The available Rex profile provides total source-weighted absorptance,
transmittance, and reflectance, but it does not provide a defensible
specular/diffuse partition, BRDF, BTDF, surface roughness, separate abaxial
reflectance, or reverse-incidence measurements. The baseline therefore uses:

```text
Rs = 0
Ts = 0
roughness = 0
Rd = R
Td = T
```

For `S = R + T`, the standard Radiance `trans` arguments reduce to:

```text
A1 = A2 = A3 = S
A4 = 0
A5 = 0
A6 = T / S
A7 = 0
```

Forward reconstruction is:

```text
Rd = (1 - A4) * A1 * (1 - A6)
Td = (1 - A4) * A1 * A6 * (1 - A7)
Rs = A4
Ts = (1 - A4) * A1 * A6 * A7
R = Rd + Rs
T = Td + Ts
A = 1 - R - T
```

The mapper must reject `R + T == 0`; no implicit perfect-absorber `trans`
policy is defined. `T == 0, R > 0` is canonicalized to `A6 = A7 = 0`.
`R == 0, T > 0` is valid with `A1 = A2 = A3 = T`, `A6 = 1`, and
`A7 = 0`.

### 17.1 Grayscale isolated-run policy

Each scalar PAR or spectral-band transport run uses one grayscale material:

```text
A1 = A2 = A3
```

The five transport bands are not packed into Radiance RGB channels. Each band
is a separate scalar carrier and future production transport remains repeated
isolated tracing. This preserves the strict equal-channel scalar decode policy
and avoids assigning physical wavelength bands to Radiance display-primary
channels.

### 17.2 Geometry and sidedness assumptions

The model is symmetric front-to-back and applies to one infinitely thin leaf
surface. Coincident front/back leaf polygons are prohibited because a ray could
receive the material interaction twice. The existing scientific leaf mesh must
continue to export each triangle exactly once.

The Rex measurements do not validate angular scattering, specularity,
roughness, or separate adaxial/abaxial behavior. Accordingly, this model must
not be described as a measured BSDF, BRDF, BTDF, or angular leaf model. The
accurate claim language is:

```text
source-weighted, diffuse, symmetric, thin-leaf energy-partition model
```

The planned material preserves the Phase 16 source-weighted A/T/R energy
partition. It does not claim to reproduce the leaf's directional scattering
distribution.

---

## 18. Five-band absolute calibration and dry-run transport policy

Five-band transport preserves the current deterministic forward
completed-aperture authority while applying absolute amplitude before Radiance
transport:

```text
T_forward = 0.833143250
completed_aperture_fixture_PPE = 2.6 umol/J
internal_source_PPE = 2.6 / T_forward = 3.120711833 umol/J
```

The 2.6 µmol/J value is the completed-aperture forward-transport boundary, not
the photon yield captured by a receiver plane. Deterministic forward
`rtrace -I+` integration supplies the production transmission. The retired
backward near-field `rfluxmtx` estimate is diagnostic only and cannot change
five-band source amplitude, identities, or cached artifacts.

The nominal component model determines spectral composition only. Its nominal
aggregate PPE, approximately `3.0464128 umol/J`, is retained only as provenance
for the relative mix and must not become an absolute source or fixture value.
The pre-transport relative-mix normalization factor is:

```text
pretransport_normalization = internal_source_PPE / raw_mix_aggregate_PPE
```

For scheduled module wattage `w` and strict combined-source band fraction
`F_b` relative to PAR:

```text
Q_internal,module,b = w * internal_source_PPE * F_b
L_module,b = Q_internal,module,b / (pi * emitter_area)
```

Far-red uses `Q_FR / Q_PAR` in the same expression and remains outside PAR.
The blue, green, orange, and red internal yields must sum to approximately
`3.120711833 umol/J`; calibrated stack transport makes their modeled
completed-aperture sum `2.6 umol/J`.
Scalar PAR is a separate optional comparator and is never added to band
results.

### 18.1 Combined source geometry

The spatial model identifier is:

```text
combined_module_emitting_window
```

Each module retains one `0.120 m * 0.120 m` emitting window at its existing
layout position and control-zone wattage. Warm-white, cool-white, and deep-red
component contributions are combined before Radiance text generation because
the validated geometry does not assign component-specific package positions.
No package-level emitters or component-specific controls are implied.

Every band uses the existing native Lambertian angular assumption and an equal
Radiance channel amplitude:

```text
R = G = B
```

Band identity comes from the isolated run's source photon budget and matching
Rex material, not from Radiance RGB channels.

### 18.2 Isolated scene and receiver policy

Blue, green, orange, red, and far-red each require an independent full-source
octree and ambient cache because both source amplitude and Rex leaf material
differ. The receiver file is shared and retains one ordered front/back pair for
each of 512 physical patches, or 1,024 receiver rows for the default plant.

Front and back samples represent distinct incident hemispheres. For a future
absorbed-photon calculation, one physical patch area multiplies the sum of its
front and back incident flux; the duplicated receiver metadata does not create
two physical leaf areas.

Blue, green, orange, and red outputs are band photon flux density within PAR.
Far-red output is photon flux density and must not be labeled PPFD.

### 18.3 Claim boundary

The accurate five-band claim is:

```text
Band-resolved SMD source composition and Rex leaf optics within a wavelength-neutral room and fixture model.
```

The production room uses neutral diffuse 0.90 walls and ceiling plus a distinct
neutral diffuse 0.10 floor; both plastics have zero specularity and roughness.
Room plastic, PTFE, and PMMA remain wavelength-neutral. The model also retains
combined module-window geometry, fixed source spectra versus current and
temperature, diffuse symmetric leaf optics, fixed source-weighted coefficients
within each band, no within-band spectral reshaping, and the documented lack of
Rex optical data from 400 through 403 nm.

---

## 19. Native five-band Rex receiver smoke

Phase 19 executes the Phase 18 plan without changing its source, material,
layout, schedule, or receiver identities. The CLI command is:

```bash
.venv/bin/python -m fspm_optics.cli rex-five-band-receiver-smoke \
  --workspace /absolute/path/to/solved-basis-workspace \
  --threads 6 \
  --execute
```

Omitting `--execute` materializes and prints the dry-run plan without locating
or running Radiance. Native execution always runs ten commands sequentially:
`oconv` then `rtrace` for blue, green, orange, red, and far-red. Each `oconv`
routes binary stdout directly to its planned octree; each `rtrace` reads the
shared receiver PTS on stdin and routes ASCII RGB stdout directly to its
planned receiver file. Bands never share ambient caches.

Each band must decode exactly 1,024 rows with exactly three finite,
nonnegative, equal grayscale channels. The decoded artifact is a float64
vector with shape `(1024,)`. Receiver interpretation follows explicit side
metadata: every physical patch has one ordered front record followed by one
back record. Transmitted light leaving the opposite side is not itself an
opposite-side incident measurement, while light returned through a later
reflection can become incident.

The native artifacts are:

```text
rex_five_band/
  five_band_transport_plan.json
  rex_plant_receivers.pts
  source_model.json
  rex_material_plan.json
  blue/
    emitters.rad
    rex_plant.rad
    scene.oct
    receiver.rgb
    receiver_band_pfd.npy
    scene.amb
  green/ ...
  orange/ ...
  red/ ...
  far_red/ ...
  five_band_receiver_summary.json
```

The summary contains incident-light diagnostics only: per-band minimum,
maximum, mean, separate front and back means, and an area-weighted receiver
mean. Front and back means are not added or described as a conventional
horizontal PPFD measurement. Blue, green, orange, and red are PAR-band photon
flux densities. Far-red is photon flux density outside PAR, not PPFD. Scalar
PAR remains separate and is not included in or added to these five results.
No absorbed-photon quantity is calculated in Phase 19.

---

## 20. Absorbed photon metrics

Phase 20 is pure post-processing of a successful Phase 19 workspace. It does
not locate or execute Radiance. Run:

```bash
.venv/bin/python -m fspm_optics.cli rex-five-band-absorbed-metrics \
  --workspace /absolute/path/to/successful-phase19-workspace
```

For each physical patch `p` and band `b`, explicit receiver side metadata is
used to identify the front and back rows:

```text
E_incident,p,b = E_front,p,b + E_back,p,b
E_absorbed,p,b = A_b * E_incident,p,b
Q_absorbed,p,b = E_absorbed,p,b * area_p
```

The physical patch area is applied once after combining the two incident
hemispheres. Front and back absorbed contributions are retained separately and
close to the combined value. Local A/T/R diagnostics require absorbed,
transmitted, and reflected partition terms to close to incident flux; the
transmitted and reflected terms are not net escaped or newly intercepted
photon totals.

Blue, green, orange, and red alone form absorbed PAR totals. Far-red remains a
separate photon-flux quantity outside PAR and is never labeled PPFD. Scalar PAR
is not combined with the five band results.

Phase 20 writes:

```text
rex_five_band/
  absorbed_photon_summary.json
  absorbed_patch_metrics.npz
```

The NPZ contains deterministic numeric arrays in stable patch and band order.
The JSON records source hashes, formulas, units, patch ordering, leaf summaries,
whole-plant summaries, conservation diagnostics, and the existing scientific
limitations. These optical absorption metrics are not photosynthesis, DLI,
growth, biomass, or yield predictions.
