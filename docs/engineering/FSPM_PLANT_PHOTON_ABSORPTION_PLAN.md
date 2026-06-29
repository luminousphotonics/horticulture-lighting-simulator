# FSPM Plant Photon Absorption Live Implementation Plan

## Status Header

* Feature: Functional-Structural Plant Modeling for lettuce-style plant geometry, plant-surface photon absorption, spectral response inputs, and bounded response-potential metrics.
* Branch: `feat/fspm-plant-modeling`.
* Worktree: `/home/austin/Desktop/hls-fspm`.
* Public/main worktree: `/home/austin/Desktop/horticulture-lighting-simulator` is out of scope and must stay untouched.
* Current implementation stage: Phase 15/16 reset and hardening before Phase 17 state updates.
* Target crop model: generic lettuce / leafy-green rosette archetype, not a cultivar-specific model.
* Target environment: indoor controlled-environment growth chamber under sole-source electric lighting.
* Presentation goal: prepare a defensible Radiance Workshop demonstration focused on plant-surface receiver sampling, absorbed photon flux, spectral response inputs, and plant-to-plant lighting-response consistency.
* Current science status: unvalidated but structured model scaffold. Outputs must be framed as lighting-analysis inputs, response potentials, or deterministic scenario states, not biological predictions.

## Operating Rules

Codex must read this plan before starting each implementation prompt and update the relevant status/checklist after completing work.

Keep this file concise. Do not turn it into a research archive, benchmark dump, or full phase history.

Do not add research appendices, source lists, or study summaries to this live plan.

Implementation rules:

* Do not touch `main`.
* Do not push or commit to `main`.
* Keep work on `feat/fspm-plant-modeling`.
* Do not commit generated runtime outputs, precomputed bundles, private IES/SPD inputs, local absolute paths, `.oct`, `.rad`, PPFD maps, PNG outputs, GLB/STEP exports, or benchmark dumps.
* Prefer small targeted patches over full-file rewrites.
* Preserve public precomputed-first behavior.
* Preserve existing baseline PPFD, DOU, CV, mean PPFD, heatmap, fixture-output, and artifact-route behavior unless a prompt explicitly changes it.
* Any fingerprint-affecting request control must be threaded through every route that reconstructs `RadianceRunRequest`.

## Scientific Guardrails

This project may model:

* Deterministic lettuce-style plant structure.
* Leaf-surface receiver points and normals.
* Incident photon flux at plant surfaces.
* Absorbed photon flux from explicit optical assumptions.
* Spectral-band exposure inputs.
* Relative photosynthetic light-response potential.
* Relative photoreceptor or morphology-response potential.
* Deterministic illustrative state updates when clearly labeled.

This project must not claim:

* Yield prediction.
* Biomass prediction.
* Harvest-weight prediction.
* Crop-output forecasting.
* Cultivar-validated growth modeling.
* Biological validation without measured calibration data.
* Canopy energy conservation when plant geometry is excluded from lighting transport.
* Canopy penetration when only single-leaf transmission proxies are calculated.

Preferred language:

* `response potential`
* `relative response`
* `lighting-analysis input`
* `deterministic scenario state`
* `model scaffold`
* `unvalidated default`
* `reviewed profile`
* `controlled-environment lettuce rosette archetype`

Avoid:

* `yield`
* `biomass`
* `harvest weight`
* `crop production`
* `validated cultivar model`
* `growth prediction`
* `canopy photosynthesis` unless explicitly supported by a validated canopy model.

## Current Architecture Summary

Plant model code lives under:

`src/rad_rebuild/radiance/engine/plants/`

Current plant artifacts:

* `plants.rad`
* `plants_viewer.json`
* `plants_manifest.json`
* `plant_config.json`
* `plant_surface_flux.json`
* `plant_spectral_response.json`
* `plant_spectral_absorption.json`
* `plant_photosynthesis_response.json`
* `plant_photomorphogenesis_response.json`

Current runtime behavior:

* Plant geometry is optional and request-controlled.
* Plant artifacts are runtime/workspace outputs.
* Baseline PPFD and fixture comparison metrics remain room-plus-emitters only.
* Plant geometry is not included in the baseline PPFD/uniformity octree.
* Plant receiver sampling is a separate FSPM analysis layer.
* Current receiver sampling treats thin leaves as two-sided by sampling both normal directions.
* Current plant-surface flux is an unblocked-field receiver method, not self-shaded canopy transport.

Important recent implementation lesson:

* `match_system_ppe` is part of the Proposed LED live run identity.
* It must be preserved in run keys, artifact query params, backend request reconstruction, image routes, metrics routes, assembly scene routes, scatter routes, CSV routes, and photometric-layer routes.
* Future fingerprint-affecting controls should use shared helpers where practical.

## Target Model Direction

The model should become a defensible lettuce-style FSPM scaffold for Radiance-based lighting analysis.

The core model should prioritize:

1. Plant structure.
2. Surface-local photon capture.
3. Spectral exposure.
4. Uniformity-aware response potential.
5. Viewer-ready visual diagnostics.
6. Optional deterministic state snapshots.

The model should not attempt calibrated biological growth prediction before physical and physiological validation exists.

The default target should be:

`lettuce_rosette_archetype_v1`

This is a generic controlled-environment lettuce-style rosette model. Named cultivars may be added later only as explicitly reviewed calibrated profiles.

## Phase 15/16 Reset Goals

The current Phase 15/16 artifacts exist, but they need hardening before they are used by Phase 17.

Primary fixes:

* Add provenance and calibration-status metadata.
* Make input basis explicit: incident PPFD, absorbed PAR, APAR, or mixed.
* Evaluate nonlinear light-response functions locally before plant-level aggregation.
* Add area-weighted aggregation.
* Add uniformity-aware and lower-tail metrics.
* Keep signed reference rates separate from bounded visualization indices.
* Remove or de-emphasize daily clipped CO2-style outputs that imply physiological precision.
* Split core photoreceptor exposure from optional morphology hypotheses.
* Keep morphology-style outputs disabled or clearly heuristic unless an explicitly reviewed calibrated profile is selected.

## Phase 15 Hardening: Photosynthetic Light-Response Potential

Target artifact name remains unless a prompt explicitly changes it:

`plant_photosynthesis_response.json`

Preferred future schema direction:

`rad_rebuild.fspm.plant_photosynthetic_light_response.v2`

The Phase 15 v2 model should:

* Consume `plant_spectral_response.json`.
* Use absorbed PAR or explicitly declared photon basis.
* Calculate response per receiver surface before aggregation.
* Multiply by physical one-sided surface area only when totals are needed.
* Aggregate to leaf, plant, and system summaries.
* Report area-weighted and equal-plant summaries separately.
* Report lower-tail and uniformity-aware metrics.
* Preserve signed reference net exchange fields if included.
* Use bounded response fractions for visualization.
* Clearly label parameters as unvalidated defaults unless an explicitly reviewed calibrated profile is selected.

Required output concepts:

* Surface APAR.
* Leaf APAR.
* Plant APAR.
* Area-weighted response fraction.
* Plant-normalized response fraction.
* Plant-to-plant CV using normalized response, not total plant area.
* Lower-tail response metrics.
* Area fraction below compensation reference if the selected profile supports it.
* Area fraction above profile valid range or near-saturation range if available.
* Nonuniformity response retention.

Do not make Phase 15 output look like measured daily crop carbon gain.

## Phase 16 Hardening: Photoreceptor And Morphology Inputs

Current artifact:

`plant_photomorphogenesis_response.json`

Preferred direction is to split this into:

* `plant_photoreceptor_exposure.json`
* optional `plant_morphology_hypothesis.json`

The core Phase 16 model should report exposure inputs, not assert morphology outcomes.

Core exposure fields should include:

* Blue PFD.
* Blue fraction of PAR.
* Blue photon dose when recipe timing exists.
* Green PFD.
* Red PFD.
* Far-red PFD.
* Far-red fraction or explicitly defined R:FR diagnostic.
* Phytochrome proxy if wavelength-dependent method data exists.
* Single-leaf transmission proxies clearly labeled as single-leaf proxies.
* Plant-to-plant exposure consistency.

Morphology hypotheses should be optional and disabled by default unless a prompt explicitly implements a reviewed profile system.

Remove or demote generic fields that imply actual morphology prediction, including broad default compactness, elongation, expansion, or morphology-balance scores.

## Phase 17 Gate

Phase 17 may begin before physical validation only if it is explicitly limited to deterministic, illustrative state updates.

Allowed Phase 17 framing:

* deterministic state progression
* scenario visualization
* geometry-state scaffold
* relative lighting-response driver
* non-calibrated FSPM state update

Disallowed Phase 17 framing:

* growth prediction
* yield prediction
* biomass prediction
* harvest prediction
* cultivar-validated growth cycle
* production forecast

Before Phase 17 consumes Phase 15/16 outputs, the v2 artifacts should include:

* provenance metadata
* calibration status
* input basis
* evidence-quality tier or equivalent concise field
* uncertainty/limitation notes
* normalized plant-level metrics
* hotspot-sensitive metrics
* explicit non-prediction language in schema/tests/docs

## Immediate Implementation Queue

### Step 1 — Clean Live Plan And Remove Cultivar-Specific Direction

Status: complete.

Completed checklist:

* Verified this compact live implementation plan is present.
* Removed stale live-plan references to removed cultivar-specific basis documents and required research/source lookup.
* Confirmed the target model remains the generic lettuce / leafy-green rosette archetype.
* Left runtime formulas, schemas, tests, source data, generated artifacts, bundles, and numerical goldens unchanged.

Goal:

* Replace the prior long live plan with this compact implementation plan.
* Remove any deleted or obsolete cultivar-specific basis-document references from docs/tests/code comments if present.
* Keep the repo focused on generic lettuce-style FSPM.

Acceptance criteria:

* No required reference to deleted cultivar-specific basis documents.
* No cultivar-specific default target.
* No required source lookup or research appendix in this live plan.
* Plan remains concise enough for Codex to reread each prompt.

Validation run:

* `git status --short`
* Targeted stale-reference search across `docs`, `src`, `tests`, `scripts`, and `AGENTS.md`.
* `git diff --check`

Validation results:

* `git status --short`: only this live plan was modified.
* Targeted stale-reference search: no matches after cleanup.
* `git diff --check`: passed.

Unresolved issues:

* None for Step 1.

Next recommended implementation step:

* Step 2 — Phase 15 V2 Provenance And Contract Hardening.

Suggested validation:

* `git status --short`
* Targeted stale-reference search across `docs`, `src`, `tests`, `scripts`, and `AGENTS.md`.
* `git diff --check`

### Step 2 — Phase 15 V2 Provenance And Contract Hardening

Status: complete.

Completed checklist:

* Added a v2 schema/method identity to `plant_photosynthesis_response.json`.
* Added additive contract metadata for calibration status, input basis, unvalidated default-parameter status, target model identity, evidence-quality tier, uncertainty notes, and non-prediction framing.
* Kept existing leaf, plant, total, visualization, warning, limitation, and summary fields intact for current consumers.
* Allowed Phase 16 and backend metrics loading to read both legacy v1 and new v2 photosynthesis-response artifacts.
* Added focused tests for v2 metadata presence and prohibited crop-claim wording absence in the new contract metadata.
* Left request schemas, routes, OpenAPI, baseline PPFD, DOU, CV, mean PPFD, heatmaps, fixture-output metrics, source data, generated artifacts, bundles, and numerical goldens unchanged.

Goal:

* Add v2 metadata/contract fields to photosynthetic response potential without changing public baseline PPFD behavior.
* Preserve or migrate v1 behavior deliberately.
* Do not introduce new biological claims.

Acceptance criteria:

* Artifact declares schema/method version.
* Artifact declares calibration status.
* Artifact declares input basis.
* Artifact declares that defaults are unvalidated.
* Existing runtime tests pass.
* New tests verify prohibited wording is absent.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_photosynthesis_response.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_photomorphogenesis_response.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase07_metrics_scaffold.py tests/radiance/test_route_query_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/photosynthesis.py src/rad_rebuild/radiance/engine/plants/photomorphogenesis.py src/rad_rebuild/radiance/backend/metrics.py tests/radiance/test_plant_photosynthesis_response.py tests/radiance/test_plant_photomorphogenesis_response.py`
* `git diff --check`

Validation results:

* Photosynthesis artifact tests: passed, 7 tests.
* Photomorphogenesis artifact tests: passed, 5 tests.
* Metrics/route-focused checks: passed, 15 tests.
* Plant/FSPM tests: passed, 44 tests.
* Import-boundary tests: passed, 6 tests, 33 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Config-contract tests: passed, 9 tests, 6 subtests.
* Focused Ruff check: passed.
* `git diff --check`: passed.

Unresolved issues:

* None for Step 2.

Next recommended implementation step:

* Step 3 — Surface-Local Phase 15 Aggregation.

### Step 3 — Surface-Local Phase 15 Aggregation

Status: complete.

Completed checklist:

* Refactored Phase 15 photosynthetic response potential to evaluate the nonlinear response curve per spectral surface/receiver row before aggregation.
* Preserved compatibility with legacy leaf-only spectral summaries by treating each leaf summary as an aggregate receiver.
* Added surface-level photosynthesis summaries while keeping existing leaf, plant, total, and visualization fields.
* Changed plant-to-plant photosynthetic response CV to use normalized plant response rather than total plant size, with a separate total-potential CV retained for comparison.
* Added area-weighted mean local response, equal-plant mean normalized response, lower-tail metrics, compensation-reference area fraction, near-saturation area fraction, and nonuniformity response retention.
* Preserved signed net response-potential fields alongside clipped/bounded visualization fractions.
* Added hotspot, uniform surface-subdivision, plant-size normalization, and legacy leaf-only compatibility tests.
* Left request schemas, routes, fingerprints, OpenAPI, baseline PPFD, DOU, CV, mean PPFD, heatmaps, fixture-output metrics, source data, generated artifacts, bundles, and numerical goldens unchanged.

Goal:

* Calculate nonlinear response at the surface level before leaf/plant/system aggregation.
* Add uniformity-aware response metrics that do not reward hotspots.

Acceptance criteria:

* Same-mean hotspot case scores lower than or equal to uniform case.
* Surface subdivision under uniform exposure does not change aggregate result materially.
* Plant consistency metrics use normalized response, not total plant size.
* Area-weighted and equal-plant summaries are both available.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_photosynthesis_response.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_photomorphogenesis_response.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_spectral_optics.py tests/radiance/test_plant_photomorphogenesis_response.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/photosynthesis.py tests/radiance/test_plant_photosynthesis_response.py`
* `git diff --check`

Validation results:

* Photosynthesis artifact tests: passed, 11 tests.
* Photomorphogenesis artifact tests: passed, 5 tests.
* Spectral plus photomorphogenesis artifact tests: passed, 19 tests.
* Plant/FSPM tests: passed, 44 tests.
* Import-boundary tests: passed, 6 tests, 33 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Config-contract tests: passed, 9 tests, 6 subtests.
* Focused Ruff check: passed.
* `git diff --check`: passed.

Unresolved issues:

* None for Step 3.

Next recommended implementation step:

* Step 4 — Phase 16 Exposure Split.

### Step 4 — Phase 16 Exposure Split

Status: complete.

Completed checklist:

* Added `plant_photoreceptor_exposure.json` as the core Phase 16 exposure-input artifact.
* Wired the new artifact into live artifact generation, stale-runtime cleanup, workspace sync, and backend metrics loading.
* Preserved `plant_photomorphogenesis_response.json` as a legacy compatibility artifact and marked it as heuristic response-potential output.
* Reported blue, green, red, far-red, blue fraction of PAR, far-red fraction, and explicitly diagnostic absorbed R:FR exposure inputs.
* Reported recipe-timing blue dose and phytochrome/PSS proxy as null-with-reason when required timing or wavelength-dependent method inputs are not present.
* Labeled transmission diagnostics as single-leaf proxies, not canopy penetration.
* Added plant-to-plant exposure consistency metrics.
* Added tests for core exposure artifact presence, null-with-reason PSS behavior, prohibited wording absence in the core artifact, deterministic writes, metrics loading, and legacy compatibility fields.
* Left request schemas, route fingerprints, OpenAPI, frontend types, public baseline PPFD, DOU, CV, mean PPFD, heatmaps, fixture-output behavior, formulas, source data, generated artifacts, bundles, and numerical goldens unchanged.

Goal:

* Split core photoreceptor exposure inputs from optional morphology hypotheses.
* Keep morphology outputs disabled, absent, or explicitly heuristic by default.

Acceptance criteria:

* Core output reports spectral exposure inputs.
* R:FR is labeled as diagnostic if retained.
* PSS/phytochrome proxy is only emitted when method inputs exist.
* Single-leaf transmission is not called canopy penetration.
* No default artifact claims actual compactness, elongation, expansion, biomass, yield, or growth.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_photoreceptor_exposure.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_photomorphogenesis_response.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_workspace_keys.py tests/radiance/test_api_contracts.py tests/radiance/test_route_query_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase07_metrics_scaffold.py tests/radiance/test_fspm_basis_skip.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_spectral_optics.py tests/radiance/test_plant_photosynthesis_response.py tests/radiance/test_plant_photoreceptor_exposure.py tests/radiance/test_plant_photomorphogenesis_response.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/photoreceptor.py src/rad_rebuild/radiance/engine/plants/photomorphogenesis.py src/rad_rebuild/radiance/engine/plants/__init__.py src/rad_rebuild/radiance/cli/scripts.py src/rad_rebuild/radiance/backend/artifacts.py src/rad_rebuild/radiance/backend/metrics.py tests/radiance/test_plant_photoreceptor_exposure.py tests/radiance/test_plant_photomorphogenesis_response.py`

Validation results:

* Photoreceptor exposure tests: passed, 5 tests.
* Photomorphogenesis artifact tests: passed, 5 tests.
* Plant/FSPM tests: passed, 44 tests.
* Import-boundary tests: passed, 6 tests, 33 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Config-contract tests: passed, 9 tests, 6 subtests.
* Workspace/API/route contract tests: passed, 35 tests, 38 subtests.
* Metrics scaffold and basis-skip tests: passed, 11 tests.
* Spectral, photosynthesis, photoreceptor, and photomorphogenesis artifact tests: passed, 35 tests.
* Focused Ruff check: passed.

Unresolved issues:

* Optional broad CLI orchestration check `tests/radiance/test_phase125b_cli_orchestration.py` still has two stale failures unrelated to the Phase 16 exposure split: one fake-octree live `rtrace` path and one expectation that plant geometry is appended to the baseline PPFD octree.

Next recommended implementation step:

* Step 5 — Workshop Demo Hardening.

### Step 4.5 — Assembly Viewer FSPM Panel

Status: complete.

Completed checklist:

* Replaced the visible 3D Assembly viewer Diagnostics Panel with `FSPM Panel`.
* Added a sanitized assembly-scene `fspm_metrics` block only when plant/FSPM data exists, preserving no-plant scene shape.
* Displayed summarized plant-model counts, incident leaf-surface flux, spectral exposure, photosynthetic light-response potential, and photoreceptor exposure without raw artifact JSON.
* Kept the panel hidden when no plant/FSPM scene data exists.
* Preserved fixture rendering, plant rendering, PPFD heatmap controls, surface-flux coloring, camera controls, and assembly viewer controls.
* Kept visible UI labels framed as lighting-analysis inputs and unvalidated response potential, not biological production forecasts.
* Refreshed OpenAPI and frontend API typedef contracts for the additive scene field.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_scene.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_workspace_keys.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_api_contracts.py tests/radiance/test_route_query_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_viewer_plants.py tests/radiance/test_assembly_viewer_heatmap.py tests/radiance/test_assembly_viewer_fixture_controls.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_public_web_contract.py`
* `PYTHONPATH=src ./.venv/bin/python scripts/dev/export_openapi.py --check`
* `PYTHONPATH=src ./.venv/bin/python scripts/dev/generate_frontend_types.py --check`
* `npm run typecheck:js`
* `npm run lint:js`
* `npm run test:browser`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance tests/radiance`
* `git diff --check`

Validation results:

* Assembly scene tests: passed, 19 tests.
* Plant/FSPM tests: passed, 44 tests.
* Workspace-key tests: passed, 12 tests, 38 subtests.
* API and route contract tests: passed, 23 tests.
* Assembly viewer helper tests: passed, 6 tests.
* Import-boundary and config-contract tests: passed, 15 tests, 39 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Public web contract tests: passed, 9 tests, 3 subtests.
* OpenAPI and frontend type checks: passed after refreshing generated contract files.
* TypeScript and ESLint checks: passed.
* Browser smoke: passed, 50 tests. Initial sandboxed run could not start the local Flask server; rerun with local-server escalation passed.

Unresolved issues:

* None for Step 4.5.

Next recommended implementation step:

* Step 5 — Workshop Demo Hardening.

### Step 4.6 — FSPM Target PPFD and Target-Capped Absorption Metrics

Status: complete.

Completed checklist:

* Added FSPM target PPFD and tolerance controls inside the FSPM Plant Geometry panel, with target defaulting from the run target until edited.
* Added optional `fspm_target_ppfd_umol_m2_s` and `fspm_target_tolerance_umol_m2_s` request fields with finite-positive validation.
* Included target/tolerance in plant-enabled request fingerprints while keeping disabled-plant/no-plant fingerprints stable.
* Threaded target/tolerance through metrics, artifact, image, CSV/scatter, assembly-scene, and photometric-layer request reconstruction paths.
* Corrected under-lit/over-lit target classification to use per-surface `target_classification_ppfd_umol_m2_s`, area-weighted into leaf/plant summaries.
* Preferred interpolated runtime `ppfd_map.txt` values at plant-surface XY positions for target classification, with a labeled plant-surface receiver fallback when no PPFD map is available.
* Added target-range leaf/surface/plant counts and target metadata to `plant_surface_flux.json`.
* Added target-capped incident flux, excess incident above target, deficit-to-target incident flux, lower-tail target-classification PPFD, and plant-to-plant target-capped incident CV metrics.
* Updated the external FSPM metrics block and 3D Assembly viewer `FSPM Panel` to label target classification basis/source separately from raw receiver incident flux and legacy broadband absorbed diagnostics.
* Preserved baseline PPFD, DOU, CV, mean PPFD, heatmap, fixture-output, and no-plant public behavior.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_absorption_phase07.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_scene.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase07_metrics_scaffold.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_workspace_keys.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_route_query_contracts.py`
* `npm run typecheck:js`
* `npm run lint:js`
* `npm run test:browser`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/fspm_targets.py src/rad_rebuild/radiance/engine/plants/absorption.py src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/backend/metrics.py src/rad_rebuild/radiance/assembly/fspm_panel.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_assembly_scene.py tests/radiance/test_phase07_metrics_scaffold.py`
* `git diff --check`

Validation results:

* Surface-flux artifact tests: passed, 18 tests.
* FSPM absorption helper tests: passed, 5 tests.
* Workspace-key tests: passed, 13 tests, 42 subtests.
* Route query contract tests: passed, 11 tests.
* Assembly scene tests: passed, 19 tests.
* Metrics scaffold tests: passed, 5 tests.
* Plant/FSPM tests: passed, 44 tests.
* Import-boundary and config-contract tests: passed, 15 tests, 39 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* TypeScript and ESLint checks: passed.
* Browser smoke: passed, 52 tests. Initial sandboxed run could not start the local Flask server; rerun with local-server escalation passed.
* Focused Ruff check: passed.
* Diff whitespace check: passed.
* OpenAPI/type export checks were not rerun; this correction did not change request schemas, routes, or OpenAPI contracts.

Unresolved issues:

* None for Step 4.6.

Next recommended implementation step:

* Step 5 — Workshop Demo Hardening.

### Step 4.7 — FSPM Panel Compact CSV Export

Status: complete.

Completed checklist:

* Added an `Export Data` button at the top of the 3D Assembly viewer `FSPM Panel`.
* Added dynamic `/radiance/fspm-csv` export route using the same completed-workspace authorization pattern as PPFD CSV.
* Preserved plant fields, FSPM target PPFD/tolerance fields, `match_system_ppe`, mode/comparison fields, session ID, and artifact token through the export URL/query identity.
* Corrected the default export to a compact wide CSV with exactly one data row per current run/system.
* Kept stable snake-case headers, numeric values unit-free, and units in header names where practical.
* Filled leaf and receiver-surface target-classification percentages from the FSPM artifact summary.
* Kept target-capped incident metrics separate from raw incident and raw absorbed metrics.
* Added chart-ready derived target-capacity and incident-flux fraction columns, with blank cells for missing or zero denominators.
* Removed the default raw incident-vs-target-capacity percent field so chart workflows keep raw receiver accounting separate from target-fit metrics.
* Added CSV note wording that raw receiver incident flux is physical receiver accounting, not target-equivalent PPFD classification.
* Preserved target classification basis/source in the summary row.
* Avoided per-plant, per-leaf, per-surface receiver, per-bucket, and per-metric rows by default.
* Added concise unavailable/error status handling in the panel without displaying raw CSV content.
* Allowed iframe-initiated downloads from the 3D Assembly modal so `Export Data` behaves like the PPFD CSV download.
* Regenerated the OpenAPI contract for the new export route.

Validation run:

* `git diff --check`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_workspace_keys.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_api_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_route_query_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_scene.py`
* `npm run typecheck:js`
* `npm run lint:js`
* `PYTHONPATH=src ./.venv/bin/python scripts/dev/export_openapi.py --check`
* `PYTHONPATH=src ./.venv/bin/python scripts/dev/generate_frontend_types.py --check`
* `npx playwright test tests/browser/assembly-viewer-bounds.spec.js --project=desktop`
* `npm run test:browser`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/assembly/fspm_csv.py src/rad_rebuild/radiance/assembly/fspm_panel.py src/rad_rebuild/radiance/backend/routes/artifacts.py tests/radiance/test_route_query_contracts.py`

Validation results:

* Plant/FSPM tests: passed, 44 tests.
* Workspace-key tests: passed, 13 tests, 42 subtests.
* API contract tests: passed, 13 tests.
* Route query contract tests: passed, 14 tests after the compact wide-row, derived-column, and raw-receiver-accounting cleanup corrections.
* Assembly scene tests: passed, 19 tests.
* TypeScript and ESLint checks: passed.
* OpenAPI and frontend type checks: passed after regenerating `docs/api/radiance-openapi.json`.
* Assembly viewer focused browser tests: passed, 7 tests. Initial sandboxed run could not start the local Flask server; rerun with local-server escalation passed.
* Browser smoke: passed, 54 tests before the compact wide-row correction. After the iframe download fix, one full-suite desktop navigation timeout reproduced as isolated load flakiness and passed when rerun by itself.
* Focused Ruff check: passed.
* Diff whitespace check: passed.

Unresolved issues:

* None for Step 4.7.

Next recommended implementation step:

* Step 5 — Workshop Demo Hardening.

### Step 4.8 — Source-Bound Leaf Optical Profiles

Status: complete.

Completed checklist:

* Added `src/rad_rebuild/radiance/engine/plants/optical_profiles.py` as an opt-in leaf optical profile loader.
* Registered `rex_green_butterhead_mature_leaf_optics_v1` without wiring it into the simulator default path.
* Loaded the Rex profile from `data/radiance/plant_optics/lettuce_rex/rex_leaf_optical_properties_digitized_v0_2.csv` with metadata from the v0.2 manifest.
* Exposed typed profile and treatment curves with wavelength, model absorptance, model transmittance, model reflectance, raw digitized values, implied absorptance, absorptance basis flags, provenance, and validation metadata.
* Kept the main profile curve on the manifest-backed `mean_of_treatments` model while retaining per-treatment curves for raw-versus-implied absorptance auditability.
* Preserved v0.2 behavior that reflectance/transmittance are not clipped to the direct raw absorptance range because wavelengths beyond direct absorptance coverage still have R/T support and use implied absorptance rather than treating missing raw absorptance as zero.
* Added focused loader tests in `tests/radiance/test_plant_optical_profiles.py`.
* Left baseline PPFD, uniformity, fixture-output metrics, browser assets, routes, numerical goldens, and protected data unchanged.

Limitations and assumptions:

* The Rex optical profile is research-derived digitized data and remains unvalidated in this simulator.
* The loader trusts the v0.2 model columns as source of truth and validates source-choice consistency; it does not implement new wavelength-binned absorption math.
* This profile supports modeled absorbed photon flux only, not photosynthesis, morphology, biomass, or yield prediction.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_optical_profiles.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py tests/radiance/test_plant_spectral_optics.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/optical_profiles.py src/rad_rebuild/radiance/engine/plants/__init__.py tests/radiance/test_plant_optical_profiles.py`
* `git diff --check`

Validation results:

* Optical-profile loader tests: passed, 5 tests.
* Plant/FSPM and spectral optics tests: passed, 58 tests.
* Import-boundary and config-contract tests: passed, 15 tests, 39 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Focused Ruff check: passed.
* Diff whitespace check: passed.

Unresolved issues:

* None for Step 4.8.

Next recommended implementation step:

* Phase 2 — wavelength-binned spectral absorption artifacts using explicit incident spectral photon flux and selected optical profile curves.

### Step 4.9 — Wavelength-Binned Spectral Absorption Artifact

Status: complete.

Completed checklist:

* Added `src/rad_rebuild/radiance/engine/plants/spectral_absorption.py` for opt-in wavelength-binned modeled leaf absorption.
* Added `plant_spectral_absorption.json` with schema `rad_rebuild.fspm.plant_spectral_absorption.v1`.
* Wired runtime generation to write the new artifact only when `FSPM_LEAF_OPTICAL_PROFILE_ID` is set, with `rex_green_butterhead_mature_leaf_optics_v1` supported through the Phase 1 loader.
* Preserved existing behavior when no optical profile is selected; stale `plant_spectral_absorption.json` is removed and legacy `plant_spectral_response.json` generation remains unchanged.
* Reused existing curve-data SPD discovery/parsing style, interpolated source spectra onto the selected leaf optical profile wavelength grid, and normalized source photon fractions so the PAR integral maps to the scalar plant receiver PPFD.
* Added an explicitly labeled `band_fraction_legacy` fallback for cases where only coarse spectral photon fractions are available.
* Computed crop, plant, leaf, and surface summaries for incident PAR PPFD, absorbed PAR/ePAR and blue/green/orange/red/far-red PPFD, absorbed/reflected/transmitted fractions, and absorptance-basis coverage.
* Included spectral-grid arrays for wavelength, source photon fraction, model A/R/T coefficients, original absorptance basis, and derived raw-vs-implied absorptance source basis.
* Updated the plant artifact inventory and package exports for the new artifact helpers.
* Added focused tests in `tests/radiance/test_plant_spectral_absorption.py`.
* Left baseline PPFD, uniformity, fixture-output metrics, UI labels, CSV export, route schemas, Rex optics data, numerical goldens, and plant-shaded octree behavior unchanged.

Artifact schema summary:

* Metadata: schema/version, method, source surface-flux artifact, selected optical profile, source spectrum basis, scalar flux basis, wavelength range, band definitions, units, warnings, and limitations.
* Data: compact spectral grid plus crop, plant, leaf, and surface summary totals. Per-surface per-wavelength rows are not emitted to keep file size reasonable.
* Scaling: scalar receiver flux is treated as PAR PPFD; wavelength-resolved source spectra are converted from relative spectral power to relative photon weight and normalized so 400-700 nm photons sum to the scalar receiver PPFD.

Limitations and assumptions:

* Current plant receiver scalar flux is treated as PAR PPFD for spectral scaling.
* ePAR/far-red fields are computed from the same normalized source spectrum where grid/source coverage contributes photons beyond 700 nm.
* The legacy band-fraction fallback is audit-labeled and should not be presented as measured 1 nm SPD data.
* Downstream UI/CSV and legacy spectral-response consumers still use their existing artifacts pending Phase 3 relabeling.
* Plant geometry still does not occlude/shade receiver sampling; plant-geometry occlusion remains Phase 4 work.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_optical_profiles.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_spectral_optics.py tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_basis_skip.py tests/radiance/test_phase125b_cli_orchestration.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/engine/plants/__init__.py src/rad_rebuild/radiance/engine/plants/artifacts.py src/rad_rebuild/radiance/cli/scripts.py tests/radiance/test_plant_spectral_absorption.py`
* `git diff --check`

Validation results:

* Spectral-absorption and optical-profile tests: passed, 12 tests.
* Plant/FSPM and spectral optics tests: passed, 58 tests.
* Import-boundary and config-contract tests: passed, 15 tests, 39 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Focused Ruff check: passed.
* Diff whitespace check: passed.
* FSPM basis-skip plus optional CLI orchestration check: 31 passed and 2 known stale CLI orchestration failures remained. The failures are the previously documented fake-octree live `rtrace` path and the stale expectation that plant geometry is appended to the baseline PPFD octree.

Unresolved issues:

* None for the Phase 2 spectral-absorption artifact itself.
* Existing optional CLI orchestration test staleness remains unrelated to this task.

Next recommended implementation step:

* Phase 3 — UI/CSV/artifact relabeling so user-facing surfaces distinguish scalar incident receiver flux, wavelength-binned modeled absorption, and legacy band-response artifacts.
* Phase 4 — optional plant-geometry occlusion mode for plant-shaded receiver sampling.

### Step 4.10 — FSPM UI/CSV/Artifact Relabeling

Status: complete.

Completed checklist:

* Added artifact role/description metadata to `plant_surface_flux.json` so it is identified as incident leaf-surface receiver flux and target-fit metrics.
* Added artifact role/description metadata to `plant_spectral_absorption.json` so it is identified as modeled spectral absorbed/reflected/transmitted leaf photon flux.
* Added manifest artifact descriptions for surface-flux and spectral-absorption outputs.
* Added `incident_leaf_surface_flux` panel metrics while preserving the legacy `plant_surface_absorption` alias for compatibility.
* Added compact `modeled_spectral_absorption` panel and API summaries only when `plant_spectral_absorption.json` is present.
* Added backend `plant_incident_surface_flux` and `plant_spectral_absorption` metric blocks while preserving legacy `plant_photon_absorption` behavior.
* Added CSV columns for incident leaf-surface PPFD/flux, legacy broadband absorbed diagnostics, optical profile ID, source spectrum basis, scalar flux basis, modeled absorbed PAR/ePAR/band PPFD, and modeled absorbed/reflected/transmitted fractions.
* Left spectral absorption CSV cells blank when no optical-profile artifact exists.
* Updated simulator and assembly-viewer labels to distinguish incident leaf-surface flux, legacy broadband diagnostics, target-fit metrics, and modeled spectral absorption.
* Renamed the viewer coloring label/status from absorption color to surface-flux color.
* Preserved baseline canopy-plane PPFD/uniformity calculations, Rex opt-in behavior, Rex source data, and existing JSON compatibility fields.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_optical_profiles.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_assembly_scene.py tests/radiance/test_phase07_metrics_scaffold.py tests/radiance/test_route_query_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_public_web_contract.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/engine/plants/artifacts.py src/rad_rebuild/radiance/assembly/fspm_panel.py src/rad_rebuild/radiance/assembly/fspm_csv.py src/rad_rebuild/radiance/backend/metrics.py tests/radiance/test_assembly_scene.py tests/radiance/test_phase07_metrics_scaffold.py tests/radiance/test_route_query_contracts.py`
* `npm run lint:js`
* `npm run typecheck:js`
* `npx playwright test tests/browser/frontend-smoke.spec.js tests/browser/assembly-viewer-bounds.spec.js --project=desktop --grep "metrics formatter|plant scaffold|assembly viewer exposes|FSPM panel"`
* `npx playwright test tests/browser/frontend-smoke.spec.js --project=desktop --grep "metrics panel formats plant absorption scaffold"`
* `git diff --check`

Validation results:

* Spectral-absorption and optical-profile tests: passed, 12 tests.
* Surface-flux artifact, assembly-scene, metrics scaffold, and route-query contract tests: passed, 57 tests.
* Public web contract tests: passed, 9 tests, 3 subtests.
* Focused Ruff check: passed.
* ESLint and TypeScript checks: passed.
* Focused Playwright tests: passed, 3 desktop tests total. Initial sandboxed run could not start the local Flask server; reruns with local-server escalation passed.
* Diff whitespace check: passed.

Next recommended implementation step:

* Phase 4 — optional plant-geometry occlusion mode for plant-shaded receiver sampling.

### Step 4.11 — Phase 4A Tandem Transport Architecture

Status: complete.

Completed checklist:

* Kept baseline canopy-plane PPFD transport on the existing room-plus-emitters scene.
* Added dedicated plant-inclusive FSPM receiver scene inputs via `_fspm_receiver_scene_inputs`.
* Added `_build_fspm_receiver_octree` to build `smd_fspm_receiver.oct`, `hps_fspm_receiver.oct`, or `spydr_fspm_receiver.oct` only when FSPM plants are enabled.
* Routed plant receiver sampling to the dedicated FSPM receiver octree while leaving baseline PPFD/uniformity octrees plant-free.
* Kept baseline heatmap, scatter, photometric layer, annotated heatmap, overlay heatmap, and baseline metrics tied to `ppfd_map.txt` from the baseline transport path.
* Preserved disabled-FSPM behavior; no FSPM receiver octree is built and stale plant artifacts are cleared as before.
* Preserved single-pass plant receiver tracing: `plant_surface_flux.json` is produced from one FSPM receiver trace, and `plant_spectral_absorption.json` consumes that surface-flux payload.
* Added FSPM transport metadata to `plant_surface_flux.json` and propagated it to `plant_spectral_absorption.json`: `baseline_transport_scene`, `fspm_receiver_transport_scene`, and `receiver_trace_count`.
* Updated summary payloads so panel/API consumers can see the transport-scene metadata without opening raw artifacts.
* Left Rex optical profiles opt-in only and did not add a user-facing unblocked/plant-shaded mode.

Files changed:

* `src/rad_rebuild/radiance/cli/scripts.py`
* `src/rad_rebuild/radiance/engine/plants/surface_flux.py`
* `src/rad_rebuild/radiance/engine/plants/spectral_absorption.py`
* `src/rad_rebuild/radiance/assembly/fspm_panel.py`
* `src/rad_rebuild/radiance/backend/metrics.py`
* `tests/radiance/test_phase125b_cli_orchestration.py`
* `tests/radiance/test_plant_surface_flux_artifact.py`
* `tests/radiance/test_plant_spectral_absorption.py`

Confirmation:

* Baseline PPFD/uniformity artifacts remain isolated from plant receiver tracing.
* Baseline octree inputs still exclude `plants.rad`, including frozen-room octree cases.
* FSPM receiver transport uses a plant-inclusive scene containing room, emitters, and plant geometry.
* Plant receiver tracing is single-pass per simulation run.
* Modeled spectral absorption reuses `plant_surface_flux.json` and does not invoke receiver tracing.

Manual live test instructions:

* Run a live plant-enabled SMD simulation with `FSPM_PLANTS_ENABLED=1` and confirm `ppfd_map.txt` plus baseline visual artifacts are produced normally.
* Confirm the cache contains a baseline `smd_scene.oct` and a separate `smd_fspm_receiver.oct` for that run.
* Confirm `runtime_state/plant_surface_flux.json` has `baseline_transport_scene: room_emitters_only`, `fspm_receiver_transport_scene: room_emitters_plants`, and `receiver_trace_count: 1`.
* With `FSPM_LEAF_OPTICAL_PROFILE_ID=rex_green_butterhead_mature_leaf_optics_v1`, confirm `runtime_state/plant_spectral_absorption.json` carries the same transport metadata and `source_artifact: runtime_state/plant_surface_flux.json`.

Validation run:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py tests/radiance/test_fspm_baseline_octree.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_optical_profiles.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py tests/radiance/test_assembly_scene.py tests/radiance/test_route_query_contracts.py tests/radiance/test_public_web_contract.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/cli/scripts.py src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/assembly/fspm_panel.py src/rad_rebuild/radiance/backend/metrics.py tests/radiance/test_phase125b_cli_orchestration.py tests/radiance/test_fspm_baseline_octree.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py`
* `npm run lint:js`
* `npm run typecheck:js`

Validation results:

* CLI orchestration and baseline-octree tests: passed, 29 tests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Surface-flux, spectral-absorption, and optical-profile tests: passed, 30 tests.
* Plant/FSPM, assembly-scene, route-query, and public web contract tests: passed, 86 tests, 3 subtests.
* Import-boundary and config-contract tests: passed, 15 tests, 39 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Focused Ruff check: passed.
* ESLint and TypeScript checks: passed.

### Step 4.12 — Explicit Receiver Sampling Granularity

Status: complete.

Completed:

* Added `FSPM_RECEIVER_GRANULARITY=leaf_centroid | leaf_quadrature_4 | mesh_patch`.
* Set the runtime default to `leaf_centroid`.
* Preserved the previous high-resolution behavior as `mesh_patch`.
* Added receiver metadata to `plant_surface_flux.json` and propagated it to `plant_spectral_absorption.json`: `leaf_count`, `receiver_sample_count`, `receiver_granularity`, `receiver_samples_per_leaf`, and `receiver_generation_basis`.
* Updated summaries, CSV/report fields, and viewer wording so traced receiver load is reported as receiver samples, distinct from mesh surface rows.

Actual receiver generation behavior:

* The previous 12,288 receiver count for 64 plants and 768 modeled leaves was expected for the old high-resolution mesh-patch sampling path: 768 leaves x 8 mesh patches per leaf x 2 sides = 12,288 receiver samples.
* `leaf_count` is the modeled botanical leaf count.
* `surface_count` remains the deterministic mesh-face registry used by absorption summaries and visualization.
* `receiver_sample_count` is the actual number of `rtrace` receiver rows generated for the selected granularity.

Granularity behavior:

* `leaf_centroid`: one centroid/normal receiver sample per modeled leaf. Each sample represents the full one-sided leaf area and is expanded area-weightedly onto the mesh-face registry for existing artifact summaries.
* `leaf_quadrature_4`: four representative centroid/normal receiver samples per modeled leaf when the current mesh supports four non-empty face groups. Each sample represents its face-group area.
* `mesh_patch`: current high-resolution behavior, using leaf-surface patch centroids/normals with front/back samples for each patch.

Receiver counts for the current deterministic leaf mesh:

* For 768 leaves, `leaf_centroid` traces 768 receiver samples.
* For 768 leaves, `leaf_quadrature_4` traces 3,072 receiver samples.
* For 768 leaves, `mesh_patch` traces 12,288 receiver samples.

Recommended production/demo setting:

* Use `FSPM_RECEIVER_GRANULARITY=leaf_centroid` before Level 2 source-weighted transmissive leaves and Level 3 five-band transport.
* Use `leaf_quadrature_4` for focused convergence checks when a moderate receiver-count increase is acceptable.
* Use `mesh_patch` only when the high-resolution leaf-surface patch basis is explicitly needed.

Implications:

* Source-weighted transmissive leaves and five-band spectral transport multiply transport cost; defaulting to leaf centroids prevents the old mesh-patch sampling load from becoming the baseline before those phases.
* `plant_spectral_absorption.json` continues to reuse the single `plant_surface_flux.json` receiver result and does not introduce duplicate receiver tracing.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py`

Validation results:

* Receiver, spectral absorption, and CLI orchestration focused tests: passed, 56 tests, with existing third-party matplotlib/pyparsing deprecation warnings.

Remaining Phase 4B work:

* Rex-derived diffuse-transmissive Radiance leaf material.
* Possible band-specific transport if needed.
* Material fitting assumptions for reflectance/transmittance.
* Optional `FSPM_LEAF_RADIANCE_MATERIAL_MODE=opaque_occluder | rex_diffuse_transmissive` hook.

Next recommended implementation step:

* Phase 4B — reviewed leaf material transport extension point.

### Step 5 — Workshop Demo Hardening

Status: pending.

Goal:

* Make the FSPM output and viewer behavior presentation-ready.
* Prioritize clean artifacts, stable route behavior, clear labels, and visual diagnostics.

Acceptance criteria:

* Proposed LED, Conventional LED, and HPS comparisons can be shown without artifact authorization drift.
* Plant-surface absorption visualization loads reliably.
* Metrics panel uses clear labels and does not dump raw JSON.
* Response-potential fields are clearly separated from baseline PPFD metrics.
* Screenshots/demo outputs can be regenerated locally without committing runtime artifacts.

## Validation Matrix

Run focused tests for touched areas first. Run broad tests before pushing.

Common checks:

```bash
git status --short
git diff --check
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py
```

Plant/FSPM checks:

```bash
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py
```

Backend/artifact checks when request fields, fingerprints, or routes change:

```bash
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_workspace_keys.py
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_api_contracts.py
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_route_query_contracts.py
```

Viewer checks when assembly viewer code changes:

```bash
npm run typecheck:js
npm run lint:js
npm run test:browser
```

Broad checks before push:

```bash
PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance
PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance tests/radiance
PYTHONPATH=src ./.venv/bin/python -m mypy --show-error-codes app.py src tests scripts
```

OpenAPI/frontend type checks when schemas change:

```bash
PYTHONPATH=src ./.venv/bin/python scripts/dev/export_openapi.py --check
PYTHONPATH=src ./.venv/bin/python scripts/dev/generate_frontend_types.py --check
```

## Live Plan Update Procedure

After each implementation prompt, update only the relevant section of this file.

Each update should include:

* Status changed from `pending` to `complete` when done.
* Short completed checklist.
* Validation commands run.
* Validation results.
* Any unresolved issues.
* Next recommended implementation step.

Do not add long research summaries, source tables, benchmark dumps, generated output inventories, or transient notes.
