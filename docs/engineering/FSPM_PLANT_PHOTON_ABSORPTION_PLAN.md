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
* Displayed summarized plant-model counts, plant-surface absorption, spectral exposure, photosynthetic light-response potential, and photoreceptor exposure without raw artifact JSON.
* Kept the panel hidden when no plant/FSPM scene data exists.
* Preserved fixture rendering, plant rendering, PPFD heatmap controls, absorption coloring, camera controls, and assembly viewer controls.
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
