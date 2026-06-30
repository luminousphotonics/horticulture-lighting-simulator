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
* Initially set the runtime default to `leaf_centroid`; this was superseded by Step 4.14 after manual live validation.
* Preserved the previous high-resolution behavior as `mesh_patch`.
* Added receiver metadata to `plant_surface_flux.json` and propagated it to `plant_spectral_absorption.json`: `leaf_count`, `receiver_sample_count`, `receiver_granularity`, `receiver_samples_per_leaf`, and `receiver_generation_basis`.
* Updated summaries, CSV/report fields, and viewer wording so traced receiver load is reported as receiver samples, distinct from mesh surface rows.

Actual receiver generation behavior:

* The previous 12,288 receiver-row count for 64 plants and 768 modeled leaves was the deterministic mesh surface row count: 768 leaves x 16 mesh patches per leaf = 12,288 mesh surface rows.
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
* For 768 leaves, `mesh_patch` traces 24,576 receiver samples: 12,288 mesh surface rows x 2 sides.

Recommended production/demo setting:

* Historical note: Step 4.12 used `leaf_centroid` as the initial defensive default before the representative receiver behavior was live-validated.
* Step 4.14 changes the practical default to `leaf_quadrature_4`.

Implications:

* Source-weighted transmissive leaves and five-band spectral transport multiply transport cost; defaulting to leaf centroids prevents the old mesh-patch sampling load from becoming the baseline before those phases.
* `plant_spectral_absorption.json` continues to reuse the single `plant_surface_flux.json` receiver result and does not introduce duplicate receiver tracing.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py`

Validation results:

* Receiver, spectral absorption, and CLI orchestration focused tests: passed, 56 tests, with existing third-party matplotlib/pyparsing deprecation warnings.

### Step 4.13 — Receiver Granularity Regression Fix

Status: complete.

Root cause:

* `leaf_centroid` and `leaf_quadrature_4` placed representative receiver points at area-weighted aggregate centroids. In a plant-inclusive receiver octree, those aggregate points can lie inside or behind the opaque leaf mesh, so the representative samples self-occluded and collapsed to very low incident PPFD.
* `leaf_quadrature_4` also grouped faces by modulo index, which made each representative point cover a non-contiguous patch set instead of a coherent leaf area.
* `mesh_patch` reported 24,576 receiver samples for the 64-plant / 768-leaf case because it intentionally traces front and back receiver rows for each of 12,288 mesh surface rows.
* Plant surface color used legacy broadband absorbed PPFD, which is a scalar absorptance multiple of incident flux. That made the color source too easy to confuse with an absorbed-fraction diagnostic.

Corrected receiver generation behavior:

* Representative receiver areas remain area weighted: `leaf_centroid` sample areas sum to one-sided leaf area, and `leaf_quadrature_4` sample areas sum to one-sided leaf area.
* Representative receiver points now use the local mesh-patch centroid nearest the area-weighted leaf or area-partition centroid, so traced points stay on real leaf surface geometry.
* Representative normals use the selected local mesh-patch normal oriented toward the light-facing upward hemisphere, avoiding top/bottom cancellation and avoiding a one-sided sample on the dark side.
* `leaf_quadrature_4` uses contiguous area partitions rather than modulo-indexed face buckets.

Corrected receiver metadata:

* Added `receiver_represented_area_m2`, `receiver_sample_area_sum_m2`, `receiver_area_basis`, `receiver_side_policy`, `receiver_rows_per_mesh_surface_row`, and `normal_generation_basis`.
* `mesh_patch` explicitly reports `receiver_side_policy: front_and_back_per_mesh_surface_row`, `receiver_rows_per_mesh_surface_row: 2`, and one-sided represented area with doubled sample-area sum.
* `plant_spectral_absorption.json` propagates the corrected receiver metadata from `plant_surface_flux.json` and still does not trigger receiver tracing.

Corrected surface-flux color source:

* Plant surface-flux visualization now defaults to `incident_photon_flux_density_umol_m2_s` with `color_quantity: incident_leaf_surface_ppfd`.
* Baseline canopy-plane heatmap colors are unchanged.

Corrected receiver counts for the current deterministic leaf mesh:

* For 768 leaves, `leaf_centroid` traces 768 receiver samples.
* For 768 leaves, `leaf_quadrature_4` traces 3,072 receiver samples.
* For 768 leaves, `mesh_patch` traces 24,576 receiver samples over 12,288 mesh surface rows.

Recommended production/demo setting:

* Use `leaf_quadrature_4` for development and demo iteration.
* Use `leaf_centroid` for fast smoke/debug iteration where the lower sample count is more important than spatial detail.
* Keep `mesh_patch` for high-resolution convergence, debugging, and final/reference outputs.

Transmissive leaf material deferral:

* Rex-derived transmissive Radiance leaf materials remain deferred until receiver sampling and surface-flux visualization remain stable under live Proposed LED, Conventional LED, and HPS checks.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_assembly_viewer_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py tests/radiance/test_fspm_baseline_octree.py tests/radiance/test_assembly_scene.py tests/radiance/test_route_query_contracts.py tests/radiance/test_public_web_contract.py tests/radiance/test_assembly_viewer_plants.py tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/engine/plants/__init__.py src/rad_rebuild/radiance/backend/metrics.py src/rad_rebuild/radiance/assembly/fspm_panel.py src/rad_rebuild/radiance/assembly/fspm_csv.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_assembly_viewer_plants.py`
* `npm run lint:js`
* `npm run typecheck:js`
* `npm run test:browser`
* `npx playwright test tests/browser/frontend-smoke.spec.js -g "about modal and GitHub actions are accessible" --project=desktop`

Validation results:

* Focused receiver, spectral absorption, and viewer color-source tests: passed, 32 tests.
* Broad FSPM/artifact/report/assembly/route/public/import/config tests: passed, 162 tests, 42 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Focused Ruff check: passed.
* ESLint and TypeScript checks: passed.
* Browser smoke: 53 passed, 1 desktop About-modal test timed out because the demo-guide prompt intercepted the close button; isolated rerun of that exact test passed.

Remaining Phase 4B work:

* Rex-derived diffuse-transmissive Radiance leaf material, now implemented as
  `FSPM_LEAF_RADIANCE_MATERIAL_MODE=rex_source_weighted_trans` in Step 4.16.
* Possible band-specific transport if needed.
* Material fitting assumptions for reflectance/transmittance.
* Optional Level 3 `FSPM_SPECTRAL_TRANSPORT_MODE=scalar_source_weighted | banded_5`
  hook after Level 2 is stable.

Next recommended implementation step:

* Phase 4B — reviewed leaf material transport extension point.

### Step 4.14 — Receiver Granularity Practical Protocol

Status: complete.

Manual validation summary:

* For 768 modeled leaves, `leaf_centroid` produced 768 receiver samples.
* For 768 modeled leaves, `leaf_quadrature_4` produced 3,072 receiver samples.
* For 768 modeled leaves, `mesh_patch` produced 24,576 receiver samples.
* `leaf_centroid` remained useful for fast smoke/debug checks, but underestimated receiver-derived incident PAR relative to `mesh_patch`.
* `leaf_quadrature_4` was much closer to `mesh_patch` while keeping receiver count practical for development and demo iteration.
* `mesh_patch` remains the high-resolution reference mode and is recommended for final/reference workshop outputs when runtime budget permits.

Implemented protocol:

* Changed the default `FSPM_RECEIVER_GRANULARITY` behavior to `leaf_quadrature_4`.
* Retained `leaf_centroid` as an explicit smoke/debug mode.
* Retained `mesh_patch` as an explicit high-resolution final/reference mode.
* Added `receiver_granularity_role` metadata so artifacts and UI summaries can report `smoke_debug`, `development_demo_default`, or `high_resolution_reference`.
* Preserved the existing single-pass FSPM receiver trace: `plant_spectral_absorption.json` reuses `plant_surface_flux.json` and does not add a second receiver trace.

Recommended usage before transmissive leaf materials:

* `leaf_centroid`: smoke/debug only; not recommended for presentation metrics because live comparison showed it underestimates receiver-derived incident PAR versus mesh-patch reference.
* `leaf_quadrature_4`: development/demo default before Level 2 source-weighted transmissive leaves and Level 3 five-band spectral transport.
* `mesh_patch`: high-resolution final/reference output mode, especially for workshop presentation data or convergence checks.

Implications:

* Source-weighted transmissive leaves and five-band spectral transport will multiply transport cost, so the default needs to balance fidelity and iteration speed before those phases.
* `leaf_quadrature_4` is the practical default because it captures more leaf-surface variation than a single centroid without making the old mesh-patch receiver load the baseline.
* `mesh_patch` remains important as the reference basis for final comparison, but should not be the default for routine runs.

Next planned step:

* Phase 4B design spike for Rex-derived source-weighted diffuse-transmissive Radiance leaf materials.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py tests/radiance/test_assembly_viewer_plants.py tests/radiance/test_assembly_scene.py tests/radiance/test_route_query_contracts.py tests/radiance/test_public_web_contract.py tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/engine/plants/__init__.py src/rad_rebuild/radiance/backend/metrics.py src/rad_rebuild/radiance/assembly/fspm_panel.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py`
* `npm run lint:js`
* `npm run typecheck:js`
* `git diff --check`

Validation results:

* Receiver, spectral absorption, and CLI orchestration focused tests: passed, 61 tests, with existing third-party matplotlib/pyparsing deprecation warnings.
* FSPM, artifact/report, assembly, route, public web, import, and config tests: passed, 103 tests, 42 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
* Focused Python Ruff check: passed.
* ESLint and TypeScript checks: passed.
* `git diff --check`: passed.

### Step 4.15 — Transmissive Leaf Material Design Spike

Status: complete.

Design artifact:

* `docs/engineering/FSPM_TRANSMISSIVE_LEAF_MATERIAL_DESIGN.md`

Recommendations:

* Implement Level 2 before Level 3.
* Add `FSPM_LEAF_RADIANCE_MATERIAL_MODE=opaque_occluder | rex_source_weighted_trans`, defaulting to `opaque_occluder`.
* Keep Level 2 scoped to the FSPM receiver scene only; baseline PPFD/uniformity scene inputs must remain room plus emitters only.
* Use PAR 400-700 nm source-weighted Rex reflectance/transmittance/absorptance for Level 2 because the current scalar receiver trace is PAR PPFD.
* Treat first-pass Radiance leaf transmission as diffuse-only unless reviewed data support specular transmission.
* Keep `plant_spectral_absorption.json` as the detailed wavelength/band absorbed/reflected/transmitted photon-flux artifact.
* Add `FSPM_SPECTRAL_TRANSPORT_MODE=scalar_source_weighted | banded_5` for Level 3 after Level 2 is stable.

Next recommended implementation step:

* Validate Level 2 `rex_source_weighted_trans` in live Proposed LED,
  Conventional LED, and HPS smoke runs, then decide whether Level 3 banded
  transport is necessary before the workshop.

Validation:

* `git diff --check`

Validation results:

* `git diff --check`: passed.
* No Markdown-specific lint script is defined in `package.json`.

### Step 4.16 — Level 2 Source-Weighted Transmissive Leaf Material

Status: complete.

Implemented behavior:

* Added `FSPM_LEAF_RADIANCE_MATERIAL_MODE=opaque_occluder | rex_source_weighted_trans`.
* Kept `opaque_occluder` as the default; it preserves the existing
  `plastic plant_leaf_material` output in `runtime_state/plants.rad`.
* Added a Rex source-weighted diffuse `trans` material for the FSPM receiver
  scene only when `rex_source_weighted_trans` is selected.
* Wrote the opt-in receiver material scene to
  `runtime_state/plants_fspm_receiver_material.rad` and passed that file only
  into `_build_fspm_receiver_octree()`.
* Preserved viewer JSON, plant manifest IDs, stable leaf IDs, polygon IDs, and
  baseline PPFD/uniformity scene inputs.
* Preserved single-pass Level 2 receiver tracing: `plant_surface_flux.json` is
  generated from one FSPM receiver trace, and `plant_spectral_absorption.json`
  reuses that payload.

Corrected Radiance material formula:

* Level 2 uses PAR 400-700 nm source-weighted `R_eff`, `T_eff`, and `A_eff`.
* The diffuse-only neutral `trans` fit uses
  `red = green = blue = R_eff + T_eff`,
  `spec = 0`, `rough = 0`,
  `trans = T_eff / (R_eff + T_eff)`, and `tspec = 0`.
* The helper rejects non-finite values, out-of-range fractions, coefficient
  sums not close to 1, `R_eff + T_eff > 1`, and `R_eff + T_eff <= 0`.
* The older candidate formula `color = R_eff / (1 - T_eff)` and
  `transmitted_diffuse = T_eff / (1 - T_eff)` is not used.

Artifact metadata:

* `plant_surface_flux.json` and `ppfd_field_summary` now include the selected
  leaf material mode, PAR weighting basis, Rex profile/source identifiers,
  effective R/T/A coefficients, Radiance primitive, diffuse/opaque assumption,
  specular assumptions, and fitted `red`, `green`, `blue`, `trans`, and
  `tspec` parameters.
* `plant_spectral_absorption.json` propagates the same leaf-material metadata
  from `plant_surface_flux.json` and remains the detailed wavelength/band
  absorbed, reflected, and transmitted photon-flux artifact.

Runtime impact:

* Baseline PPFD, DOU, CV, heatmaps, fixture overlays, 3D scatter, and
  assembly-viewer heatmap overlays remain room plus emitters only.
* `rex_source_weighted_trans` still performs one FSPM receiver trace. It may
  make individual Radiance rays more expensive, but it does not multiply
  receiver traces.
* Level 3 `banded_5` execution is implemented later in Step 4.18.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_leaf_materials.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py tests/radiance/test_plant_leaf_materials.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_optical_profiles.py tests/radiance/test_plant_spectral_optics.py tests/radiance/test_fspm_baseline_octree.py tests/radiance/test_assembly_scene.py tests/radiance/test_assembly_viewer_plants.py tests/radiance/test_route_query_contracts.py tests/radiance/test_phase07_metrics_scaffold.py tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/leaf_materials.py src/rad_rebuild/radiance/engine/plants/radiance_export.py src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/engine/plants/__init__.py src/rad_rebuild/radiance/cli/scripts.py tests/radiance/test_plant_leaf_materials.py tests/radiance/test_fspm_plants.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py`
* `git diff --check`

Validation results:

* Material helper tests: passed, 8 tests.
* Focused FSPM/export/surface-flux/spectral-absorption/orchestration tests:
  passed, 116 tests, with existing third-party matplotlib/pyparsing
  deprecation warnings.
* Optical profile, spectral optics, baseline-octree isolation, assembly,
  route, metrics, import, and config tests: passed, 77 tests and 39 subtests,
  with existing third-party matplotlib/pyparsing deprecation warnings.
* Focused Python Ruff check: passed.
* `git diff --check`: passed.

### Step 4.17 — Level 3A Five-Band Spectral Transport Scaffold

Status: complete.

Implemented scaffold:

* Added `FSPM_SPECTRAL_TRANSPORT_MODE=scalar_source_weighted | banded_5`.
* Kept the default spectral transport mode as `scalar_source_weighted`.
* Added exact Level 3 band definitions:
  * blue: 400-499 nm
  * green: 500-599 nm
  * orange: 600-624 nm
  * red: 625-699 nm
  * far-red: 700-750 nm
* Added PAR band ids `blue`, `green`, `orange`, and `red`.
* Added ePAR band ids `blue`, `green`, `orange`, `red`, and `far_red`.
* Added pure helpers to compute per-band source photon fraction relative to
  PAR, band-specific effective R/T/A coefficients, and corrected diffuse-only
  Radiance `trans` material parameters.
* Added a banded metadata planning payload with `band_scaling_basis:
  source_band_photon_fraction_relative_to_par`,
  `scalar_flux_basis: par_ppfd_umol_m2_s`, profile/source identifiers, and
  one planned metadata block per band.

Scientific scaling rule:

* Current scalar receiver flux remains PAR PPFD.
* Level 3 band scaling is planned as
  `band_pfd = scalar_PAR_PPFD * (band_photon_integral / PAR_photon_integral)`.
* Far-red is included in ePAR planning but is not labeled as part of PAR.

Zero-source band policy:

* Bands with zero source photons skip material fitting.
* Zero-source bands emit zero coefficient/material metadata.
* Zero-source bands set `band_has_source_photons: false` and
  `receiver_trace_required: false`.
* Zero-source bands do not fail the whole banded plan.

Runtime impact:

* Level 3A did not implement full five-band live Radiance execution.
* Step 4.18 implements the live `banded_5` receiver execution path.
* No band-specific receiver `.rad` files are written in Level 3A.
* No baseline PPFD/uniformity octree inputs are changed.
* No duplicate receiver tracing is introduced.

Remaining Level 3B work at the end of Step 4.17, later completed in Step 4.18:

* Write band-specific receiver `.rad` files.
* Scale receiver transport per band.
* Run one FSPM receiver trace per active band.
* Aggregate traced band fields into `plant_spectral_absorption.json`.
* Preserve baseline PPFD/uniformity isolation.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_leaf_materials.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_leaf_materials.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_optical_profiles.py tests/radiance/test_fspm_baseline_octree.py tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants/leaf_materials.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/engine/plants/__init__.py src/rad_rebuild/radiance/cli/scripts.py tests/radiance/test_plant_leaf_materials.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_surface_flux_artifact.py`
* `git diff --check`

Validation results:

* Level 2 and Level 3A leaf-material scaffold tests: passed, 12 tests.
* Receiver granularity, spectral absorption, surface-flux, optical profile,
  baseline-octree, import, and config tests: passed, 69 tests and 39 subtests,
  with existing third-party matplotlib/pyparsing deprecation warnings.
* CLI orchestration tests: passed, 28 tests, with existing third-party
  matplotlib/pyparsing deprecation warnings.
* Focused Python Ruff check: passed.
* `git diff --check`: passed.

### Step 4.18 — Level 3B Five-Band Receiver Execution

Status: complete.

Implemented behavior:

* `FSPM_SPECTRAL_TRANSPORT_MODE=scalar_source_weighted` keeps the existing
  Level 2 single-trace behavior.
* `FSPM_SPECTRAL_TRANSPORT_MODE=banded_5` now runs one FSPM receiver trace per
  active source band.
* `banded_5` requires
  `FSPM_LEAF_RADIANCE_MATERIAL_MODE=rex_source_weighted_trans` and
  `FSPM_LEAF_OPTICAL_PROFILE_ID=rex_green_butterhead_mature_leaf_optics_v1`.
* Each active band writes a receiver-only plant Radiance file with that band's
  Rex `trans` material and builds a band-specific plant-inclusive FSPM receiver
  octree.
* The same receiver sample set is reused across active bands.
* The scalar receiver trace is not run in addition to the band traces.
* Zero-source bands skip material fitting and receiver tracing, remain present
  in metadata, and set `receiver_trace_required: false`.

Aggregation behavior:

* Source scaling is applied exactly once to each band trace result using
  `source_photon_fraction_relative_to_par`.
* `plant_surface_flux.json` in `banded_5` derives PAR aggregate incident fields
  from blue + green + orange + red band receiver rows.
* Far-red is included in ePAR summaries but not PAR surface flux.
* `plant_spectral_absorption.json` aggregates from the banded receiver rows and
  reports band-level incident, absorbed, reflected, and transmitted PFD
  summaries, absorbed PAR, absorbed ePAR, and band effective R/T/A metadata.
* Banded PAR summaries use blue + green + orange + red. Banded ePAR summaries
  use PAR + far-red. Generic absorbed/reflected/transmitted fraction keys use
  PAR as their documented basis and retain explicit ePAR fraction keys.
* `plant_spectral_absorption.json` exposes the panel/report-compatible aliases
  `scalar_incident_par_ppfd_umol_m2_s`, `absorbed_fraction`,
  `reflected_fraction`, `transmitted_fraction`, nested `optical_profile`, and
  nested `source_spectrum`.

Manual validation bug found after the first Level 3B implementation:

* `plant_surface_flux.json` was correct for `banded_5`, including receiver
  sample count, active trace count, and leaf material mode.
* `plant_spectral_absorption.json` contained correct band incident and absorbed
  values, but omitted scalar-compatible panel/report aliases and nested
  optical/source metadata.
* The FSPM panel therefore read `scalar_incident_par_ppfd_umol_m2_s` and
  generic fraction fields as missing, displayed incident PAR and fractions as
  zero, and displayed optical/source metadata as unavailable.
* The fix is metadata and aggregation/reporting compatibility only. The banded
  receiver execution loop remains one FSPM receiver trace per active band, and
  the scalar receiver trace is not run in addition to those band traces.

Baseline isolation:

* `_octree_scene_inputs()` remains plant-free.
* Baseline PPFD/uniformity octree inputs, heatmaps, fixture overlays, 3D
  scatter, and assembly-viewer heatmap overlays are not multiplied by band
  count.
* Only the FSPM receiver transport loop multiplies by active spectral band.

Manual live test instructions:

```bash
FSPM_LEAF_OPTICAL_PROFILE_ID=rex_green_butterhead_mature_leaf_optics_v1
FSPM_LEAF_RADIANCE_MATERIAL_MODE=rex_source_weighted_trans
FSPM_SPECTRAL_TRANSPORT_MODE=banded_5
FSPM_RECEIVER_GRANULARITY=leaf_quadrature_4
```

Remaining work:

* Live comparison of scalar Level 2 versus banded Level 3.
* Optional `mesh_patch` reference runs.
* Live retest of the FSPM panel and CSV/report outputs after the aggregation
  alias fix.
* Full 1 nm hyperspectral Radiance transport remains out of scope.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py::test_smd_banded_transport_traces_active_bands_without_scalar_receiver tests/radiance/test_plant_spectral_absorption.py::test_banded_spectral_absorption_aggregates_par_and_epar`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_spectral_absorption.py::test_banded_spectral_absorption_aggregates_par_and_epar tests/radiance/test_phase125b_cli_orchestration.py::test_smd_banded_transport_traces_active_bands_without_scalar_receiver tests/radiance/test_assembly_scene.py::test_fspm_panel_reads_banded_spectral_absorption_aliases`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py tests/radiance/test_plant_spectral_absorption.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_scene.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_leaf_materials.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_optical_profiles.py tests/radiance/test_fspm_baseline_octree.py tests/radiance/test_phase125b_cli_orchestration.py tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/cli/scripts.py src/rad_rebuild/radiance/engine/plants/leaf_materials.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py src/rad_rebuild/radiance/engine/plants/surface_flux.py src/rad_rebuild/radiance/engine/plants/__init__.py tests/radiance/test_phase125b_cli_orchestration.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_leaf_materials.py tests/radiance/test_plant_surface_flux_artifact.py`
* `git diff --check`

Validation results:

* Targeted Level 3B orchestration and aggregation tests: passed, 2 tests.
* Targeted Level 3B aggregation and FSPM panel alias regression tests: passed,
  3 tests.
* CLI orchestration and spectral absorption tests: passed, 38 tests, with
  existing third-party matplotlib/pyparsing deprecation warnings.
* Assembly/FSPM panel, backend metrics, and CSV/report tests: passed, 78 tests,
  with existing third-party matplotlib/pyparsing deprecation warnings.
* Requested material, spectral absorption, receiver, optical profile,
  baseline-octree, orchestration, import, and config tests: passed, 100 tests
  and 39 subtests, with existing third-party matplotlib/pyparsing deprecation
  warnings.
* Focused Python Ruff check: passed.
* `git diff --check`: passed.

### Step 4.19 — Level 3 Receiver Trace Runtime Profiling

Status: complete.

Implemented behavior:

* Added opt-in `FSPM_RTRACE_PROFILE=0|1` for Level 3 `banded_5` receiver-trace
  runtime metadata.
* Added optional `FSPM_RTRACE_NPROC`; when unset, the existing receiver `rtrace`
  command arguments and threading behavior are preserved.
* Added optional `FSPM_RTRACE_AMBIENT_MODE=default|per_band_af`. The default
  keeps the existing ambient-cache behavior unchanged. `per_band_af` only
  substitutes an explicit per-band `-af` path when ambient bounces are active
  and `FSPM_RTRACE_NPROC` is greater than 1.
* Added shared receiver `rtrace` argument construction so the recorded metadata
  matches the subprocess invocation.
* Added `banded_5` metadata for receiver sample count, active trace count,
  per-band `rtrace` args, configured nproc, ambient mode/file, one-stream-per
  active-band process policy, and per-active-band-not-per-sample subprocess
  granularity.
* When profiling is enabled, each band records octree build wall time and
  receiver `rtrace` wall time. Zero-source bands keep trace count at zero and
  report zero timing values.
* `plant_spectral_absorption.json` propagates the new Level 3 trace-control
  metadata from `plant_surface_flux.json`.

Confirmed behavior:

* `banded_5` still performs one receiver trace per active band.
* The scalar receiver trace is not added in `banded_5`.
* Receiver samples are still streamed through one stdin file per active band;
  there are no per-sample subprocess calls.
* Baseline PPFD/uniformity artifacts and default Radiance parameters remain
  unchanged by these controls.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py::test_fspm_rtrace_controls_default_preserves_existing_receiver_args tests/radiance/test_phase125b_cli_orchestration.py::test_fspm_rtrace_controls_validate_env_values tests/radiance/test_phase125b_cli_orchestration.py::test_smd_banded_transport_traces_active_bands_without_scalar_receiver`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_fspm_plants.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/cli/scripts.py src/rad_rebuild/radiance/engine/plants/spectral_absorption.py tests/radiance/test_phase125b_cli_orchestration.py`
* `git diff --check`

Validation results:

* Focused trace-control orchestration tests: passed, 3 tests.
* CLI orchestration tests: passed, 35 tests, with existing third-party
  matplotlib/pyparsing deprecation warnings.
* Receiver artifact, spectral absorption, and FSPM plant tests: passed, 81
  tests.
* Focused Python Ruff check: passed.
* `git diff --check`: passed.

### Step 4.20 — Radiance Quality And Runtime Settings Audit

Status: complete.

Scope:

* Audited current Radiance quality/runtime settings only.
* No simulation physics, default Radiance parameters, Rex optics data, receiver
  generation math, or baseline PPFD/uniformity artifacts were changed.
* No new quality presets were implemented in this pass.

Current effective trace command shape:

* Baseline PPFD/uniformity uses ASCII `rtrace -h -I+ -n <nthreads>
  <preset-options> -af <ambient-file> <octree>`.
* Scalar FSPM receiver traces use ASCII `rtrace -h -I+ -n <nthreads>
  <preset-options> -af <ambient-file> <plant-inclusive-receiver-octree>`.
* Level 3 `banded_5` receiver traces use the same ASCII `rtrace -h -I+`
  command shape once per active source band. All active bands receive the same
  preset options and `-n` value; only the band material, band octree path,
  receiver output file, and normal default ambient-file path differ.
* If `FSPM_RTRACE_NPROC` is unset, FSPM receiver traces keep the same `-n`
  policy as the calling baseline pass. If set, `banded_5` receiver traces use
  that value for `-n`.
* `FSPM_RTRACE_AMBIENT_MODE=default` preserves current ambient-file behavior.
  `per_band_af` only substitutes a per-band `-af` path when ambient bounces are
  active and configured FSPM receiver `nproc` is greater than 1.
* Input format is plain text receiver/ray rows from stdin. Output format is
  plain text Radiance RGB rows to stdout; no `-f` binary input/output format is
  selected.
* Octrees are built with `oconv` and captured to `.oct` files. Baseline octrees
  use room plus emitters only. FSPM receiver octrees add plant geometry or the
  band-specific receiver plant material geometry. Frozen-room paths may pass
  `oconv -f -i <static-room-oct> <emitters> <plants>`.

Existing quality preset map:

| Mode token | Effective preset | `-ab` | `-ad` | `-as` | `-aa` | `-ar` | `-lw` | `-lr` | `-st` | `-sj` | `-dt` | `-dc` | `-dr` | `-dp` |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- | ---: | ---: | ---: | --- |
| `direct` | `direct` | 0 | omitted | omitted | 0 | omitted | `1e-5` | 0 | omitted | omitted | 0 | 1.0 | 0 | omitted |
| `standard`, `instant`, `fast`, empty public quality alias | `standard` | 3 | 512 | 128 | 0.22 | 48 | `2e-4` | 6 | omitted | omitted | 0.08 | 0.50 | 1 | omitted |
| `quality` | `quality` | 5 | 2048 | 512 | 0.12 | 96 | `5e-5` | 12 | omitted | omitted | 0.03 | 0.85 | 3 | omitted |
| `rigorous` | `rigorous` | 6 | 4096 | 1024 | 0.08 | 128 | `2e-5` | 16 | omitted | omitted | 0.02 | 0.90 | 4 | omitted |

Additional preset flags:

* `direct`: `-u+ -dj 0.60 -ds 0.08`.
* `standard` / `instant` / `fast`: `-dj 0.35 -ds 0.40`.
* `quality`: `-dj 0.65 -ds 0.20`.
* `rigorous`: `-dj 0.70 -ds 0.15`.
* `-n` is not part of the preset table. SMD reads `NTHREADS` when supplied and
  otherwise uses CPU count, except `direct` uses 1. Conventional LED and HPS use
  CPU count except `direct` uses 1. `OS` defaults to 4 for all three live
  simulation modes.
* `-af` is appended outside the preset table. SMD baseline uses `RAD_TMP/amb`;
  Conventional LED and HPS use per-pass cache-root ambient files; FSPM scalar
  receiver traces use `amb_plant_receivers_*`; `banded_5` uses one receiver
  ambient filename per active band under the default policy.

Backend quality modes:

* Public request quality values canonicalize to `direct`, `standard`,
  `quality`, or `rigorous`.
* `instant` and `fast` are accepted aliases but resolve to `standard`.
* SMD rtrace basis extraction reuses the same quality preset map.
* Optional SMD `rcontrib_legacy` basis extraction derives its settings from the
  same rtrace preset values.
* Optional SMD `rcontrib_mcpt` basis extraction uses MCPT guidance instead of
  the full rtrace option table: default `-ad 2048`, `-ab` from the selected
  quality mode, `-lr` defaulting to `-(ab + 3)`, `-lw` defaulting to
  `1 / (2 * ad)`, and `-u+`, with env overrides for
  `SMD_RCONTRIB_MCPT_AD`, `SMD_RCONTRIB_MCPT_AB`,
  `SMD_RCONTRIB_MCPT_LR`, and `SMD_RCONTRIB_MCPT_LW`.

Baseline versus FSPM:

* Baseline PPFD/uniformity and scalar FSPM receiver traces use the same
  selected quality preset and effective Radiance options for a given run.
* They differ in scene composition and ambient filename, not in default
  Radiance quality settings: baseline is room plus emitters, while FSPM receiver
  transport uses room plus emitters plus plant geometry/materials.
* `banded_5` uses the same quality settings for all active bands in a
  simulation. It does not run multiple quality modes or a scalar receiver trace
  in addition to the active-band traces.

Doc-only recommended names:

* `dev`: map to the existing `standard` mode with
  `FSPM_RECEIVER_GRANULARITY=leaf_quadrature_4`. This is the current practical
  default for iteration because it includes ambient bounces without the runtime
  cost of `quality`/`rigorous`.
* `representative`: map to the existing `quality` mode with
  `FSPM_RECEIVER_GRANULARITY=mesh_patch` for final/reference workshop figures
  when runtime permits. `rigorous` remains a higher-cost spot-check option, not
  the recommended default for figure production yet.

Risks and notes for plant-inclusive transmissive FSPM scenes:

* Current `standard` defaults are acceptable for fast/dev FSPM runs and
  interactive workshop iteration, especially with `leaf_quadrature_4`.
* Current `standard` defaults are not the preferred final/reference setting for
  plant-inclusive transmissive scenes. Source-weighted `trans` materials and
  five-band transport increase sensitivity to indirect/direct sampling,
  ambient interpolation, and receiver granularity.
* `direct` is useful only for smoke/debug cases. It disables ambient bounces and
  should not be used for presentation metrics involving transmissive leaves or
  plant-inclusive transport.
* `quality` is the better current candidate for representative final workshop
  outputs. Live comparison against `rigorous` should be done before freezing
  final figures if runtime allows.
* Shared/default ambient-cache behavior is preserved. For parallel banded
  receiver traces with ambient bounces, `FSPM_RTRACE_AMBIENT_MODE=per_band_af`
  is available to isolate active bands without changing the default.
* A future implementation can add first-class `dev` and `representative` mode
  names, but that should be a separate behavior-changing request because it
  affects route contracts, run keys, and reproducibility metadata.

### Step 4.21 — HPS Scalar Grey-Channel PPFD Carrier Fix

Status: complete.

Issue:

* The baseline PPFD guardrail correctly rejects non-grey Radiance RGB output for
  canopy-plane scalar PAR PPFD transport.
* The HPS IES comparator path used `ies2rad` light RGB values as generated and
  scaled. Those values can be spectrally colored, for example high red, lower
  green, and very low blue.
* Before the guardrail, baseline PPFD decoding averaged those non-grey channels.
  After the guardrail, HPS failed because the source no longer satisfied the
  declared `R=G=B` scalar carrier policy.

Fix:

* The HPS IES companion `.rad` light/illum RGB values are now normalized to a
  grey scalar carrier after `ies2rad` scaling and path normalization.
* The grey value is the arithmetic mean of the generated RGB triplet, preserving
  the previous decoded scalar value while making the carrier explicit.
* The fix is scoped to HPS source encoding for baseline scalar PAR PPFD
  transport. It does not change Radiance quality settings, receiver generation,
  Rex optics data, or FSPM banded transport math.

Validation:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_hps_fixture_profile.py`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py::test_hps_simulation_orchestration_succeeds_with_stubbed_tools tests/radiance/test_phase125b_cli_orchestration.py::test_aggregate_rgb_to_ppfd_accepts_only_grey_scalar_channels tests/radiance/test_phase125b_cli_orchestration.py::test_trace_ppfd_reports_non_grey_rgb_as_validation_error`
* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125c_scientific_contracts.py`
* `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/emitters/hps_generation/rad_writer.py src/rad_rebuild/radiance/engine/emitters/hps_generation/service.py tests/radiance/test_hps_fixture_profile.py`
* `git diff --check`

Validation results:

* HPS fixture/profile tests: passed, 3 tests and 3 subtests.
* Targeted HPS orchestration and PPFD decode guardrail tests: passed, 4 tests.
* HPS/scientific contract tests: passed, 22 tests.
* Focused Python Ruff check: passed.
* `git diff --check`: passed.

### Step 5 — Workshop Demo Hardening

Status: in progress.

Goal:

* Make the FSPM output and viewer behavior presentation-ready.
* Prioritize clean artifacts, stable route behavior, clear labels, and visual diagnostics.

Acceptance criteria:

* Proposed LED, Conventional LED, and HPS comparisons can be shown without artifact authorization drift.
* Plant-surface absorption visualization loads reliably.
* Metrics panel uses clear labels and does not dump raw JSON.
* Response-potential fields are clearly separated from baseline PPFD metrics.
* Screenshots/demo outputs can be regenerated locally without committing runtime artifacts.

Completed in this pass:

* Confirmed the FSPM export button uses the existing `/radiance/fspm-csv`
  CSV workflow.
* Extended the compact CSV export with Level 3/banded metadata:
  `fspm_spectral_transport_mode`, `leaf_radiance_material_mode`,
  `receiver_granularity`, `receiver_sample_count`, `receiver_trace_count`,
  `banded_transport_band_count`, `banded_transport_active_trace_count`,
  `band_scaling_basis`, `scalar_flux_basis`, `source_spectrum_basis`,
  `leaf_material_profile_id`, `leaf_material_profile_version`,
  `leaf_material_weighting_basis`, `leaf_material_source_spectrum_id`, and
  `leaf_material_source_spectrum_source`.
* Extended the compact CSV export with baseline PPFD decode metadata:
  `baseline_ppfd_transport_basis`, `baseline_ppfd_rgb_decode_method`,
  `baseline_source_channel_policy`, `ppfd_conversion_basis`,
  `photopic_luminance_weighting_avoided`,
  `uses_179_luminous_efficacy_factor`, and
  `uses_falsecolor_or_illuminance_conversion`.
* Added crop-level incident PAR/ePAR PPFD export fields and retained existing
  modeled absorbed PAR/ePAR, per-color absorbed PFD, and fraction fields.
* Added a compact `banded_transport_band_summaries_json` CSV column that merges
  already-generated per-band metadata and summaries without exporting receiver
  rows.
* Fixed the assembly viewer title under `3D Assembly` so it is populated from
  the loaded scene `display_name`/`mode_label` instead of the static shell
  default. The scene metadata remains the source of truth for Proposed LED,
  Conventional LED, and HPS labels.
* Added response-effective target-capped modeled spectral absorption metrics to
  `plant_spectral_absorption.json` while preserving raw modeled optical
  accounting fields. The cap is `min(1, target upper PPFD / incident PAR PPFD)`
  per receiver-derived summary row, with zero incident PAR producing zero
  response-effective absorption.
* Added explicit target-capped absorption metadata:
  `target_capped_absorption_basis`,
  `target_saturation_cap_ppfd_umol_m2_s`,
  `target_range_lower_ppfd_umol_m2_s`,
  `target_range_upper_ppfd_umol_m2_s`, `target_cap_scale_basis`,
  `raw_absorption_preserved`, and `not_biological_prediction`.
* Extended compact FSPM CSV output with target-capped absorbed PAR/ePAR,
  per-color absorbed PFD, excess absorbed PAR/ePAR above cap, capped fractions
  of raw, target-effective absorbed fraction, and under/in/over-target leaf
  fractions. Raw modeled absorbed PAR/ePAR remain after the target-capped
  fields.
* Removed visible legacy broadband absorbed flux/fraction/CV diagnostics from
  the default compact CSV and from the simulator/assembly-viewer panels.
* Reordered the FSPM panels so target fit, target-capped modeled absorbed
  PAR/ePAR, capped fraction of raw, leaf fractions, receiver metadata, and raw
  modeled optical accounting are clearly separated for workshop reporting.
* Corrected the target-capped spectral absorption basis so cap scale uses
  `target_classification_ppfd_umol_m2_s` from `plant_surface_flux.json`, not raw
  receiver incident PAR PPFD. The target range now follows target PPFD plus or
  minus tolerance, and spectral under/in/over leaf fractions reuse the
  surface-flux target-classification fractions.

Validation for this pass:

* `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_route_query_contracts.py::RadianceRouteQueryContractTests::test_fspm_csv_exports_compact_authorized_summary tests/radiance/test_route_query_contracts.py::RadianceRouteQueryContractTests::test_fspm_csv_missing_optional_artifacts_uses_blank_cells tests/radiance/test_public_web_contract.py::PublicRadianceWebContractTests::test_assembly_viewer_shell_route_is_available tests/radiance/test_assembly_scene.py::test_builder_returns_schema3_geometry_aware_fixture_instances tests/radiance/test_assembly_scene.py::test_builder_returns_conventional_single_fixture_instances tests/radiance/test_assembly_scene.py::test_builder_returns_hps_single_fixture_instances`

Validation results for this pass:

* Targeted FSPM CSV export and assembly title tests: passed, 6 tests.
* Target-capped spectral absorption, CSV, assembly-panel, and metrics tests:
  passed, 34 tests.
* Focused Ruff for touched Python files and tests: passed.
* JavaScript lint and typecheck: passed.
* Browser smoke: passed, 54 tests, after rerunning with local-server escalation
  because the sandbox blocked the Flask socket.
* Corrected target-classification basis validation:
  `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_spectral_absorption.py tests/radiance/test_plant_surface_flux_artifact.py tests/radiance/test_assembly_scene.py tests/radiance/test_phase07_metrics_scaffold.py tests/radiance/test_route_query_contracts.py`
  passed, 82 tests.
* `npm run typecheck:js`, `npm run lint:js`, and
  `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance tests/radiance`
  passed after the target-classification basis correction.

Remaining presentation-output tasks:

* Live browser retest of CSV download from the assembly viewer.
* Optional screenshot/demo-output regeneration for workshop materials.

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
