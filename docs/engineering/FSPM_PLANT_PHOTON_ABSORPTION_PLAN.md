# Functional Structural Plant Modeling Plant Photon Absorption Plan

## Status Header

- Feature: Functional Structural Plant Modeling for leafy-green / lettuce-style plant geometry and plant photon absorption groundwork.
- Current phase: Phase 08 in progress; Phase 06 is intentionally skipped/deferred.
- Last updated: 2026-06-24.
- Branch: `feat/fspm-plant-modeling`.
- Worktree: `/home/austin/Desktop/hls-fspm`.
- Public main worktree: `/home/austin/Desktop/horticulture-lighting-simulator` is out of scope and must stay untouched.

## Scientific Goal

Add conservative Functional Structural Plant Modeling support so leafy-green / lettuce-style plant geometry can be represented as deterministic scene geometry, exported into runtime artifacts, and later used as the foundation for plant photon absorption analysis.

The MVP is geometry first:

- Generate deterministic lettuce-style canopy geometry from explicit parameters.
- Export plant geometry in formats that can be consumed by Radiance and the 3D viewer in later phases.
- Keep photon absorption work scoped to physical light interception and absorption groundwork.
- Avoid yield prediction, biomass prediction, production forecasting, or crop output claims.

## Non-Goals

- No yield, biomass, harvest weight, production, or crop output prediction.
- No expansion of public precomputed bundles in early phases.
- No large generated GLB, STEP, octree, PPFD, benchmark, or runtime artifact commits.
- No active simulation behavior changes in Phase 01.
- No replacement of existing emitter, photometry, optimization, or visualization behavior for current modes.
- No public request schema or fingerprint change until an explicit backend integration phase with tests.

## Current Architecture Map

### Existing Boundaries

- Request/domain models live in `src/rad_rebuild/radiance/domain.py`.
- Backend compatibility model exports live in `src/rad_rebuild/radiance/backend/models.py`.
- Backend route handling lives in `src/rad_rebuild/radiance/backend/routes/`.
- Runtime mode selection and precomputed fallback live in `src/rad_rebuild/radiance/backend/runtime.py`.
- Request fingerprinting, workspace leases, committed workspaces, artifact grants, and cleanup live in `src/rad_rebuild/radiance/backend/workspace.py`.
- Runtime environment construction lives in `src/rad_rebuild/radiance/backend/env.py`.
- Artifact sync and visualization manifests live in `src/rad_rebuild/radiance/backend/artifacts.py`.
- Scientific engine code lives under `src/rad_rebuild/radiance/engine/`.
- Existing room and sensor geometry live in `src/rad_rebuild/radiance/engine/geometry/`.
- Existing emitter geometry and Radiance writer code live under `src/rad_rebuild/radiance/engine/emitters/`.
- Precomputed playback and bundle validation live in `src/rad_rebuild/radiance/engine/simulation/precomputed_playback.py`, `precomputed_dataset.py`, and `precomputed_integrity.py`.
- Static 3D assembly scene construction lives in `src/rad_rebuild/radiance/assembly/scene.py`.
- Browser 3D assembly viewer code lives in `src/rad_rebuild/web/static/js/assembly-viewer/`.
- Simulator modal wiring for the assembly viewer lives in `src/rad_rebuild/web/static/js/radiance-simulator/assembly.js`.
- Public viewer assets live under `src/rad_rebuild/web/static/viewer/`.
- Protected source and public demo bundles live under `data/radiance/`.

### Plant Modeling Home

Plant modeling code should live in a new pure engine package:

`src/rad_rebuild/radiance/engine/plants/`

The plant core should not import web or backend modules. It should expose deterministic data structures and exporters that backend, CLI, Radiance scene assembly, and viewer assembly can consume later.

### Later Radiance Export Integration

Radiance export should integrate only after the deterministic core exists and is tested. Candidate integration points:

- New plant Radiance writer module under `src/rad_rebuild/radiance/engine/plants/`.
- Later copy/sync into runtime workspaces through `src/rad_rebuild/radiance/backend/artifacts.py`.
- Later live scene inclusion in existing shell or Python orchestration that builds room, emitters, sensor grid, and octree inputs.
- Later manifest inclusion in `RADIANCE_MANIFEST_SCHEMA_VERSION` only if schema changes are explicitly authorized and tested.

Any Radiance scene inclusion must be behind an explicit gate and must leave default no-plant behavior byte-for-byte stable where practical.

### Later Viewer Export Integration

Viewer export should build on the existing assembly scene pattern:

- Add plant scene payload data to or alongside `src/rad_rebuild/radiance/assembly/scene.py` only in the viewer phase.
- Add browser rendering modules under `src/rad_rebuild/web/static/js/assembly-viewer/` only when the viewer toggle is implemented.
- Prefer procedural geometry or small JSON fixtures over committed GLB/STEP assets.
- Validate alignment with the existing `CANOPY_Z_M`, room dimensions, and x/y/z to Three.js x/z/y transform conventions.

### Later Backend Request Controls

Backend request controls should be added only in Phase 04 or later:

- Extend `RadianceRunRequest` in `src/rad_rebuild/radiance/domain.py` with explicit plant fields only after the compatibility story is decided.
- Update `REQUEST_FINGERPRINT_FIELDS` and `canonical_request_fingerprint_payload()` in `workspace.py` only with tests proving old no-plant requests remain compatible or intentionally migrate.
- Keep public precomputed-first defaults stable.
- Reject unknown JSON fields and enforce strict finite numeric limits consistent with existing boundary security controls.

### Later Artifact Sync And Manifest Handling

Runtime plant artifacts should be generated under workspace/runtime output directories, not committed source data directories:

- Candidate runtime paths: `outputs/radiance/runtime_state/`, staged workspace `runtime_state/`, and committed workspace `runtime_state/`.
- Candidate plant files: `plant_geometry.json`, `plant_geometry.rad`, and `plant_scene.json` once their schemas are defined.
- Artifact sync should extend `_live_workspace_sync_shell()` only when runtime export exists.
- Manifest inclusion should occur only when plant artifacts are part of a run output contract, with schema/version tests.

### Boundary Tests

Use existing tests as guardrails:

- Import boundaries: `tests/radiance/test_import_boundaries.py`.
- Config and active mode contracts: `tests/radiance/test_config_contracts.py`.
- Request, public route, and API contracts: `tests/radiance/test_api_contracts.py`, `tests/radiance/test_route_query_contracts.py`, `tests/radiance/test_public_web_contract.py`.
- Workspace and artifact grants: `tests/radiance/test_workspace_keys.py`, `tests/radiance/test_phase06_workspace_store.py`.
- Precomputed behavior: `tests/radiance/test_precomputed_boundary.py`, `tests/radiance/test_precomputed_integrity.py`, `tests/radiance/test_precomputed_run_route.py`.
- Assembly scene and viewer: `tests/radiance/test_assembly_scene.py`, `tests/radiance/test_assembly_viewer_transforms.py`, `tests/radiance/test_assembly_viewer_heatmap.py`, `tests/browser/frontend-smoke.spec.js`, `tests/browser/assembly-viewer-bounds.spec.js`.
- Scientific contracts: `tests/radiance/test_phase10_scientific_contracts.py`, `tests/radiance/test_emitter_characterization_lock.py`, and focused new plant tests.

## Proposed Module Structure

Create a pure deterministic plant package:

`src/rad_rebuild/radiance/engine/plants/`

Expected files and responsibilities:

- `__init__.py`: stable public exports for the plant core.
- `domain.py`: frozen dataclasses or Pydantic-free value objects for plant parameters, organs, meshes, and metadata.
- `lettuce.py`: deterministic leafy-green / lettuce-style parameter presets and geometry builders.
- `geometry.py`: mesh primitives, transforms, normals, bounds, and triangle validation helpers.
- `materials.py`: conservative leaf material parameter containers, without claiming final optical science.
- `radiance.py`: Radiance text export for leaf geometry and placeholder material definitions, added only when Phase 01 needs export tests or Phase 02 needs runtime files.
- `viewer.py`: JSON-friendly scene export for browser rendering, added when viewer integration approaches.
- `validation.py`: deterministic checks for finite coordinates, positive areas, orientation, bounds, and reproducible IDs.
- `serialization.py`: canonical JSON serialization helpers for test fixtures and runtime handoff.

Rules for this package:

- Pure deterministic core first.
- No imports from `rad_rebuild.radiance.backend` or `rad_rebuild.web`.
- No process environment reads.
- No writes during import.
- No dependency on runtime workspace paths.
- No numerical behavior changes to existing lighting modes.
- All random-looking geometry must come from explicit seeds or deterministic formulas.

## Phase Roadmap

### Phase 00 - Planning Doc

Status: complete.

Checklist:

- Read repository guidance and scoped `AGENTS.md` files.
- Read engineering docs for architecture, API contracts, security, quality gates, scientific policy, and service topology.
- Inspect request models, backend runtime/workspace handling, artifact manifests, precomputed boundaries, assembly scene generation, browser viewer modules, tests, and gitignore protections.
- Create this live plan.
- Run lightweight validation for the documentation-only change.

Validation results:

- `git diff --check`: passed.
- `git diff --stat`: no tracked diff output because the new plan file was still untracked when checked.
- `git status --short`: `?? docs/engineering/FSPM_PLANT_PHOTON_ABSORPTION_PLAN.md`.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`: passed with 15 tests, 39 subtests, and existing third-party matplotlib/pyparsing deprecation warnings.

Next-phase handoff:

- Phase 01 should start by reading this plan, then implement only deterministic plant geometry/export core and focused tests.

### Phase 01 - Deterministic Plant Geometry And Export Core

Goal:

- Add pure plant core modules and deterministic lettuce-style geometry generation.
- Add focused unit tests for deterministic output, finite geometry, bounds, serialization, and import boundaries.
- No active runtime, backend, viewer, or simulation integration.

Expected outputs:

- Source files under `src/rad_rebuild/radiance/engine/plants/`.
- Tests such as `tests/radiance/test_plant_geometry.py` and, if useful, `tests/radiance/test_plant_export_contracts.py`.
- Tiny inline fixtures only when necessary.

Status: complete.

Completed checklist:

- Added a pure engine plant package under `src/rad_rebuild/radiance/engine/plants/`.
- Implemented validated meters-first plant geometry configuration.
- Implemented deterministic leafy-green / lettuce-style rosette generation from an explicit seed.
- Added deterministic plant IDs, leaf IDs, and Radiance polygon surface IDs.
- Added low-density curved leaf triangle meshes with finite coordinate validation coverage.
- Added a Radiance text exporter that returns deterministic text and does not write files.
- Added a JSON-serializable viewer exporter that does not generate GLB files.
- Added focused tests in `tests/radiance/test_fspm_plants.py`.
- Left backend routes, request schemas, viewer UI, public precomputed behavior, and existing lighting modes untouched.

Files added:

- `src/rad_rebuild/radiance/engine/plants/__init__.py`
- `src/rad_rebuild/radiance/engine/plants/config.py`
- `src/rad_rebuild/radiance/engine/plants/models.py`
- `src/rad_rebuild/radiance/engine/plants/generator.py`
- `src/rad_rebuild/radiance/engine/plants/mesh.py`
- `src/rad_rebuild/radiance/engine/plants/radiance_export.py`
- `src/rad_rebuild/radiance/engine/plants/viewer_export.py`
- `tests/radiance/test_fspm_plants.py`

Validation results:

- `PYTHONPATH=src python -m pytest -q tests/radiance/test_fspm_plants.py`: not runnable in this shell because `python` is not on `PATH`.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`: passed, 36 tests.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py`: passed, 6 tests, 33 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py`: passed, 9 tests, 6 subtests.
- `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants tests/radiance/test_fspm_plants.py`: passed.
- `git diff --check`: passed.
- `git diff --stat`: reported the tracked plan update; new Phase 01 source and test files remained untracked.
- `git status --short`: showed `M docs/engineering/FSPM_PLANT_PHOTON_ABSORPTION_PLAN.md`, `?? src/rad_rebuild/radiance/engine/plants/`, and `?? tests/radiance/test_fspm_plants.py`.

Scientific assumptions made:

- Leaf optical coefficients are treated as a conservative Phase 01 energy partition and must sum to 1.0: reflectance + transmittance + absorptance.
- The Phase 01 Radiance material is a deterministic placeholder `plastic` material using reflectance only; transmittance and absorptance are retained as metadata and comments until the leaf material model is reviewed.
- Lettuce-style geometry is an approximate rosette only: each leaf is a low-density tessellated curved surface, not a cultivar-specific botanical model.
- Growth stage scales generated leaf length, width, and curvature with a nonzero seedling floor; it does not imply biomass, yield, or production.
- All geometry lengths and coordinates are meters; tilt is stored in radians in generated metadata and accepted as degrees in config.

Risks/open questions:

- The Radiance leaf material remains a placeholder and must not be used for absorption conclusions before scientific review.
- Leaf density and tessellation are intentionally small for Phase 01; later simulation cost must be characterized before live scene inclusion.
- Viewer/Radiance coordinate alignment still needs explicit validation when the plant payload is connected to the assembly viewer.
- Backend request fingerprinting and public API compatibility remain deferred to Phase 04.

Recommended Phase 02 handoff:

- Add explicit runtime artifact helpers that write plant JSON/Radiance outputs only under workspace/runtime output directories.
- Keep artifact generation developer/internal only; do not include plants in Radiance simulations yet.
- Add path-safety and determinism tests for any file-writing helper.
- Keep default no-plant behavior and public precomputed playback unchanged.
- Decide the canonical plant artifact filenames and whether they belong in manifests before changing sync code.

Validation:

- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py tests/radiance/test_config_contracts.py`
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_plant_geometry.py`
- `git diff --check`

Exit criteria:

- Plant core is deterministic and pure.
- Existing active modes remain unchanged.
- No runtime artifacts are generated or committed.

### Phase 02 - Runtime Plant Artifact Export Without Active Simulation Integration

Goal:

- Add an explicit developer-only or internal export path for plant geometry artifacts in runtime workspaces.
- Do not include plants in Radiance simulations yet.
- Do not alter default public API behavior.

Expected outputs:

- Runtime export helpers that write under workspace/runtime output directories only.
- Tests proving generated files are deterministic, path-safe, and ignored unless intentionally tiny fixtures.
- No public precomputed bundle updates.

Status: complete.

Completed checklist:

- Added an engine-only plant artifact writer under `src/rad_rebuild/radiance/engine/plants/`.
- Wrote artifacts only into a caller-provided directory, with tests using `tmp_path`.
- Kept output filenames fixed and deterministic.
- Included config, seed, schema version, file records, counts, and provenance in manifest/config JSON.
- Kept manifest paths relative and free of timestamps, absolute paths, local runtime paths, or public URLs.
- Added tests for deterministic file contents, parseable JSON, manifest metadata, file records, and no writes outside the target directory.
- Left active Radiance scene/octree assembly, backend request schemas, viewer UI, precomputed bundles, and public-mode behavior untouched.

Artifact filenames:

- `plants.rad`: deterministic Radiance text export for plant geometry.
- `plants_viewer.json`: JSON-serializable viewer payload for later browser integration.
- `plants_manifest.json`: deterministic plant artifact manifest.
- `plant_config.json`: deterministic full plant geometry/material configuration.

Manifest schema notes:

- Plant artifact manifest schema: `rad_rebuild.fspm.plants.artifacts.v1`.
- Plant artifact manifest `schema_version`: `1`.
- `active_simulation_integration` is `false` in Phase 02.
- `files` records include only relative `path`, `bytes`, and `sha256` for `plants.rad`, `plants_viewer.json`, and `plant_config.json`.
- Manifest provenance is deterministic and contains phase, generator name, source module, and units; it intentionally omits timestamps and host paths.
- This manifest is plant-specific and does not change the existing Radiance visualization manifest schema.

Validation results:

- `PYTHONPATH=src python -m pytest -q tests/radiance/test_fspm_plants.py`: not runnable in this shell because `python` is not on `PATH`.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`: passed, 42 tests.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py`: passed, 6 tests, 33 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py`: passed, 9 tests, 6 subtests.
- `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants tests/radiance/test_fspm_plants.py`: passed.
- `git diff --check`: passed.
- `git diff --stat`: reported 3 tracked files changed, 181 insertions, 1 deletion; new `artifacts.py` remained untracked.
- `git status --short`: showed modified plan, plant package `__init__.py`, plant tests, and untracked `src/rad_rebuild/radiance/engine/plants/artifacts.py`.

Risks/open questions:

- The plant manifest is internal and plant-specific; decide in Phase 03 or later whether and how it should relate to runtime visualization manifests.
- `plants.rad` is still a standalone export and must not be included in scene/octree assembly until an explicit gate and no-plant stability tests exist.
- Artifact sync through backend workspace code remains deferred; Phase 02 only writes to caller-provided directories.
- Placeholder leaf material assumptions still require scientific review before absorption or comparison metrics.

Recommended Phase 03 handoff:

- Add an explicit opt-in gate for including `plants.rad` in live Radiance scene assembly.
- Characterize no-plant output before and after the gate, proving default behavior stays stable.
- Candidate integration points are the live scene/orchestration code that collects room, emitter, material, and sensor inputs, not public request schemas yet.
- Keep plant artifact generation under runtime/workspace output paths and add tests proving disabled default behavior.
- Do not alter request fingerprint fields, public API schemas, viewer UI, or precomputed bundle manifests in Phase 03.

Validation:

- Phase 01 tests.
- Targeted export tests.
- Workspace/artifact tests if sync code changes.
- `git status --short` to verify no generated runtime files are staged.
- `git diff --check`.

### Phase 03 - Optional Radiance Scene Inclusion Behind Explicit Gate

Goal:

- Include plant geometry in Radiance scene assembly only when explicitly enabled.
- Preserve default no-plant behavior for `SMD`, `Competitor`, and `1000W HPS`.
- Keep scientific claims limited to geometry participation and conservative light interception groundwork.

Expected outputs:

- Explicit gate, likely environment/internal config first.
- Radiance material assumptions documented and tested.
- Tests proving no-plant scene command/artifact behavior remains stable.

Status: complete.

Completed checklist:

- Added an environment-only plant scene gate for live Radiance orchestration.
- Reused Phase 02 artifact generation to write plant files under `RADIANCE_RUNTIME_STATE_ROOT`.
- Included `runtime_state/plants.rad` in `oconv` inputs only when the explicit gate is enabled.
- Left disabled/default `oconv` argument shape unchanged for no-plant runs.
- Added optional workspace sync copy commands for plant artifacts only when those files are present.
- Kept backend request schemas, request fingerprints, public API contracts, viewer UI, precomputed bundles, and public precomputed behavior unchanged.
- Added targeted tests for disabled no-plant assembly, enabled plant inclusion, runtime artifact location, static-room-octree argument shape, and optional plant artifact sync.

Gate names:

- `FSPM_PLANTS_ENABLED`: explicit boolean gate. Plants are included only when this parses true (`1`, `true`, `yes`, or `on`).
- `FSPM_PLANT_SEED`
- `FSPM_PLANT_ROWS`
- `FSPM_PLANT_COLUMNS`
- `FSPM_PLANT_SPACING_M`
- `FSPM_PLANT_HEIGHT_M`
- `FSPM_PLANT_CANOPY_RADIUS_M`
- `FSPM_PLANT_LEAF_COUNT`
- `FSPM_PLANT_LEAF_LENGTH_MIN_M`
- `FSPM_PLANT_LEAF_LENGTH_MAX_M`
- `FSPM_PLANT_LEAF_WIDTH_MIN_M`
- `FSPM_PLANT_LEAF_WIDTH_MAX_M`
- `FSPM_PLANT_LEAF_TILT_MIN_DEG`
- `FSPM_PLANT_LEAF_TILT_MAX_DEG`
- `FSPM_PLANT_CURVATURE_M`
- `FSPM_PLANT_GROWTH_STAGE`
- `FSPM_PLANT_REFLECTANCE`
- `FSPM_PLANT_TRANSMITTANCE`
- `FSPM_PLANT_ABSORPTANCE`

Exact scene integration behavior:

- Disabled/default live runs call `oconv` with the same room/emitter or static-room-octree/emitter inputs as before.
- Enabled live runs write `plants.rad`, `plants_viewer.json`, `plants_manifest.json`, and `plant_config.json` into `RADIANCE_RUNTIME_STATE_ROOT`.
- Enabled live runs append `plants.rad` after the emitter file in the `oconv` input list.
- When a frozen static room octree is used, enabled live runs call `oconv -f -i <static_room_oct> <emitter_file> <plants.rad>`.
- When no frozen static room octree is used, enabled live runs call `oconv -f <room.rad> <emitter_file> <plants.rad>`.
- Plant artifact manifests generated by the live gate set `active_simulation_integration: true` and deterministic provenance phase `Phase 03`.
- Workspace sync shells copy plant artifacts only through existing `if [ -f ... ]` guards.

Validation results:

- `PYTHONPATH=src python -m pytest -q tests/radiance/test_fspm_plants.py`: not runnable in this shell because `python` is not on `PATH`.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py`: passed, 42 tests.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase125b_cli_orchestration.py`: passed, 27 tests, with existing third-party matplotlib/pyparsing deprecation warnings.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py`: passed, 6 tests, 33 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py`: passed, 9 tests, 6 subtests.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance`: passed, 475 tests, 1 skipped, 98 subtests, with existing third-party matplotlib/pyparsing deprecation warnings.
- `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance/engine/plants src/rad_rebuild/radiance/cli tests/radiance`: passed.
- `git diff --check`: passed.
- `git diff --stat`: 5 files changed, 498 insertions, 9 deletions.
- `git status --short`: showed modified Phase 03 plan, backend artifact sync, CLI scripts, plant artifact writer, and CLI orchestration tests.

Scientific assumptions:

- Plant geometry remains the Phase 01 approximate lettuce-style rosette.
- The Radiance leaf material remains the Phase 01 placeholder `plastic` material using reflectance only.
- Enabling the gate changes only explicitly opted-in live Radiance scene geometry; default no-plant runs remain the comparison baseline.
- No plant photon absorption, yield, biomass, or production conclusions are introduced in Phase 03.

Risks/open questions:

- Placeholder leaf material still needs scientific review before plant-enabled results are interpreted beyond geometry participation.
- Optional workspace sync copies plant artifacts when files are present; later backend integration should decide whether per-run plant state needs explicit request fingerprinting or a separate artifact namespace.
- Plant-enabled basis extraction can include plants through the same live SMD gate; this needs characterization before public/user-facing controls.
- Viewer alignment and plant payload rendering remain deferred.

Recommended Phase 04 handoff:

- Add backend request/config controls only after deciding compatibility and fingerprint semantics.
- Decide whether plant-enabled requests alter `REQUEST_FINGERPRINT_FIELDS`, artifact keys, workspace records, and public cache reuse.
- Add strict public/request validation for all plant fields before exposing controls beyond environment variables.
- Preserve no-plant defaults for every existing public request and precomputed playback path.
- Add API, route, workspace, boundary-security, and OpenAPI checks if request schemas change.

Validation:

- Baseline tests before and after the change.
- Focused Radiance scene export tests.
- Targeted scientific characterization for enabled plant geometry.
- No numerical/golden changes without reviewed evidence.

### Phase 04 - Backend Request, Config, And Workspace Integration

Goal:

- Add explicit backend controls for plant geometry only after Phase 03 behavior is characterized.
- Decide whether plant controls affect request fingerprints, artifact keys, and workspace records.

Expected outputs:

- Strict request fields in `domain.py` if public/API controls are introduced.
- Fingerprint updates in `workspace.py` only with compatibility tests.
- Route/query tests for defaults and validation errors.
- OpenAPI refresh/check only if public API schema changes.

Validation:

- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_api_contracts.py tests/radiance/test_route_query_contracts.py tests/radiance/test_workspace_keys.py tests/radiance/test_boundary_security.py`
- `PYTHONPATH=src ./.venv/bin/python scripts/dev/baseline.py` for broad contract changes.
- `PYTHONPATH=src ./.venv/bin/python scripts/dev/export_openapi.py --check` if OpenAPI is affected.
- `git diff --check`.

Completed checklist:

- Added additive optional plant controls to the backend request contract with default no-plant behavior.
- Reused the Phase 01 `PlantGeometryConfig` validator when `plants_enabled` is true.
- Mapped enabled backend request controls into the Phase 03 `FSPM_PLANTS_*` environment gate.
- Cleared inherited plant env vars for disabled requests so server process env cannot accidentally enable plants.
- Kept `REQUEST_FINGERPRINT_SCHEMA_VERSION = 1` and preserved no-plant/default fingerprint payloads.
- Added plant fingerprint fields only when `plants_enabled` is true.
- Regenerated deterministic OpenAPI after request schema changes.

Exact request fields added:

- `plants_enabled: bool = False`
- `plant_seed: int | None = None`
- `plant_rows: int | None = None`
- `plant_columns: int | None = None`
- `plant_spacing_m: float | None = None`
- `plant_height_m: float | None = None`
- `plant_canopy_radius_m: float | None = None`
- `plant_leaf_count: int | None = None`
- `plant_growth_stage: float | None = None`

Validation behavior:

- Optional plant integer and float fields reject non-JSON-number values, NaN, infinity, and negative zero at the boundary.
- When `plants_enabled` is false, valid plant fields are accepted but ignored for runtime env and fingerprinting.
- When `plants_enabled` is true, fields are resolved through Phase 01 defaults and validated by `PlantGeometryConfig`.
- Invalid enabled plant geometry, including nonpositive rows/spacing and growth stages outside `0..1`, is rejected during request validation.

Runtime env behavior:

- Enabled requests set `FSPM_PLANTS_ENABLED=1`.
- Enabled requests set `FSPM_PLANT_SEED`, `FSPM_PLANT_ROWS`, `FSPM_PLANT_COLUMNS`, `FSPM_PLANT_SPACING_M`, `FSPM_PLANT_HEIGHT_M`, `FSPM_PLANT_CANOPY_RADIUS_M`, `FSPM_PLANT_LEAF_COUNT`, `FSPM_PLANT_GROWTH_STAGE`, and default Phase 01 leaf/optical env values.
- Disabled requests remove all known `FSPM_PLANTS_*` / `FSPM_PLANT_*` keys from the child env.

Fingerprint behavior:

- No-plant/default requests keep the existing schema version and field set.
- Supplying plant fields while `plants_enabled=false` does not alter the request fingerprint or artifact key.
- Plant-enabled requests add `plants_enabled`, `plant_seed`, `plant_rows`, `plant_columns`, `plant_spacing_m`, `plant_height_m`, `plant_canopy_radius_m`, `plant_leaf_count`, and `plant_growth_stage` to the canonical fingerprint payload.
- Changing any enabled plant fingerprint field changes the workspace fingerprint.

Public-mode safety notes:

- Existing public defaults remain precomputed and no-plant.
- Plant controls do not force live execution or bypass precomputed-first routing.
- No plant geometry was added to public precomputed bundles.
- Existing SMD, Conventional LED, and 1000W HPS request fields and route response models remain compatible.

Validation results:

- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_fspm_plants.py` -> 42 passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_config_contracts.py` -> 9 passed, 6 subtests passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_workspace_keys.py` -> 12 passed, 38 subtests passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py` -> 6 passed, 33 subtests passed, existing matplotlib/pyparsing warnings.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_phase05_contracts.py` -> 13 passed, 12 subtests passed.
- `PYTHONPATH=src ./.venv/bin/python scripts/dev/export_openapi.py --check` initially reported stale schema after adding request fields; regenerated with `PYTHONPATH=src ./.venv/bin/python scripts/dev/export_openapi.py`; final check passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance` -> 482 passed, 1 skipped, 119 subtests passed, existing matplotlib/pyparsing warnings.
- `PYTHONPATH=src ./.venv/bin/python -m mypy --show-error-codes app.py src tests scripts` -> success, no issues.
- `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance tests/radiance` -> passed.
- `PYTHONPATH=src ./.venv/bin/python scripts/dev/baseline.py` -> pass; TypeScript, ESLint, and HTML validation reported missing local Node binaries as optional tools.
- `git diff --check` -> passed.
- `git diff --stat` -> 8 files changed, 600 insertions, 2 deletions.
- `git status --short` -> expected modified source, tests, OpenAPI, and plan files only.

Risks/open questions:

- Plant controls are visible in inherited request schemas such as electrical cost estimates, but they do not affect non-Radiance execution paths.
- Plant-enabled precomputed requests remain precomputed-first and do not include plant geometry because no plant precomputed bundles exist.
- Phase 05 should decide whether public UI controls are shown only for live execution modes or are labeled as live-only.

Recommended Phase 05 handoff:

- Add a viewer-only plant toggle that is off by default and consumes `plants_viewer.json` only when present in the authorized workspace.
- Preserve existing fixture, heatmap, camera, and manifest behavior when no plant artifact exists.
- Include plant controls in viewer requests only after deciding whether controls are live-only or allowed as inert precomputed metadata.
- Add browser and route tests that prove hidden/off plant UI preserves current public precomputed behavior.

### Phase 05 - Viewer Plant Rendering And Toggle

Goal:

- Render plant geometry in the 3D assembly viewer behind an explicit toggle.
- Keep fixture rendering, heatmap behavior, and camera bounds stable.

Completed checklist:

- Added optional `plants` data to `AssemblySceneResponse` and omitted it from no-plant scene payloads.
- Loaded `runtime_state/plants_viewer.json` from authorized workspaces only when the artifact exists.
- Added plant query fields to the assembly scene and assembly photometric layer routes so plant-enabled viewer and heatmap requests resolve the same workspace fingerprint.
- Added `src/rad_rebuild/web/static/js/assembly-viewer/plants.js` for JSON mesh parsing, Radiance-to-Three coordinate mapping, material creation, and visibility control.
- Added a compact `Show plants` viewer toggle that is enabled only when plant geometry rendered.
- Kept fixture visibility, fixture height controls, PPFD heatmap controls, and no-plant Conventional/HPS viewer behavior intact.
- Regenerated deterministic OpenAPI and frontend API typedefs.

Scene/schema changes:

- `AssemblySceneResponse.plants` is optional and contains the Phase 01/02 viewer export payload when present.
- No-plant scene dictionaries still omit the `plants` key.
- The assembly route uses response-model unset exclusion so no-plant HTTP responses do not gain `plants: null`.
- Plant artifact lookup is fixed to `runtime_state/plants_viewer.json`; no caller-supplied path or public file URL is accepted.

Viewer toggle behavior:

- Plant controls are hidden when no plant payload exists or when the payload has no renderable leaves.
- Plant controls are visible, enabled, and checked by default when plant geometry renders.
- Toggling plants changes only the plant group visibility and does not touch fixture LOD, fixture height, heatmap loading, or camera controls.

Coordinate alignment notes:

- Plant exporter coordinates remain in meters.
- Viewer plant vertices use the same mapping as fixture points: Radiance/layout `[x, y, z]` maps to Three `[x, z, y]`.
- Plant meshes are inserted directly from the deterministic viewer JSON payload; no generated GLB assets are used.

Validation results:

- `npm ci` -> passed, with existing upstream package deprecation warnings.
- `npm run test:browser` -> initial sandbox run failed to bind the local Flask server; rerun with local-server escalation passed, 44 passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_scene.py` -> 18 passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_import_boundaries.py` -> 6 passed, 33 subtests passed, existing matplotlib/pyparsing warnings.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_viewer_plants.py` -> 2 passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance/test_assembly_photometric_layer.py::test_route_query_canonicalization_matches_assembly_scene_for_smd` -> 1 passed.
- `PYTHONPATH=src ./.venv/bin/python -m pytest -q tests/radiance` -> 487 passed, 1 skipped, 119 subtests passed, existing matplotlib/pyparsing warnings.
- `PYTHONPATH=src ./.venv/bin/python -m ruff check src/rad_rebuild/radiance tests/radiance` -> passed.
- `PYTHONPATH=src ./.venv/bin/python scripts/dev/export_openapi.py --check` -> passed.
- `PYTHONPATH=src ./.venv/bin/python scripts/dev/generate_frontend_types.py --check` -> passed.
- `npm run typecheck:js` -> passed.
- `npm run lint:js` -> passed.
- `PYTHONPATH=src ./.venv/bin/python scripts/dev/baseline.py` -> passed.

Risks/open questions:

- Plant request controls are still not exposed in the public simulator form, so plant viewer rendering is currently available through plant-enabled backend/runtime workspaces rather than end-user UI fields.
- Plant meshes participate in viewer scene bounds when present; current plant defaults stay inside the room/canopy footprint, but Phase 06 should test larger configured canopies before relying on camera comparisons.
- The viewer uses a simple green `MeshStandardMaterial`; Radiance leaf material science remains the Phase 07 review item.

Recommended Phase 06 handoff:

- Add no-plant versus plant-geometry comparison metrics that stay framed as geometric/photometric differences, not yield or biomass.
- Compare workspace artifacts and scene payloads for identical lighting requests with only `plants_enabled` and plant config changed.
- Add tests proving default no-plant metrics and public precomputed playback remain unchanged.
- Decide whether comparison metrics consume plant mesh geometry, Radiance surface IDs, or both.
- Document any camera/bounds or PPFD-layer differences caused by plant geometry as viewer/runtime effects, not crop-response claims.

### Phase 06 - No-Plant Vs Plant-Geometry Comparison Metrics

Status: intentionally deferred/skipped for the current implementation sequence. Do not use this as the next implementation prompt.

Goal:

- Add comparison scaffolding that can report differences between no-plant and plant-geometry scenes without implying crop output.
- Keep metrics physically framed and conservative.

Expected outputs:

- Explicit metric names and documentation avoiding yield/biomass language.
- Tests for disabled default behavior and enabled comparison outputs.
- Review-ready characterization of any PPFD or renderer differences.

Validation:

- Targeted metrics tests.
- Scientific contract tests.
- Baseline before and after if any simulation outputs change.
- No golden update without scientific review.

### Phase 07 - Plant Photon Absorption Metrics Design And Scaffold

Prerequisite:

- Verify that plant-enabled live `/radiance/run` jobs generate and sync `plants.rad`, `plants_viewer.json`, `plants_manifest.json`, and `plant_config.json` automatically before building absorption metrics on top of runtime workspaces.

Goal:

- Design and scaffold plant photon absorption metrics based on validated plant geometry and explicit optical assumptions.
- Keep this as absorption/interception analysis groundwork, not plant growth prediction.

Expected outputs:

- Metrics design notes in this plan or a reviewed engineering/science doc.
- Deterministic plant/leaf/surface ID registry for future photon-flux mapping.
- Scaffolding code and tests only after assumptions are accepted.
- Traceable optical assumptions, units, and limitations.
- Explicit notes that the scaffold does not estimate yield, biomass, growth, or crop output.

Validation:

- Focused metric unit tests.
- Scientific review evidence for material and absorption assumptions.
- Baseline if integrated into simulation outputs.

Implementation notes:

- Add live-only simulator controls for SMD FSPM plant geometry. The controls remain hidden for precomputed playback and non-SMD modes, and send plant request fields through live run payloads and artifact/metrics query params.
- Format the scaffold-only `plant_photon_absorption` block in the browser metrics panel so users see plant/leaf/surface counts, one-sided leaf area, optical assumptions, and a clear `absorbed_flux: not computed` note instead of a raw object dump.


### Phase 08 - Private Live Mode Capability Gate

Goal:

- Keep public live execution limited to the Proposed LED System by default.
- Allow private local Conventional LED and 1000W HPS live runs only when an explicit private capability flag is set and required private IES source files exist locally.
- Support internal FSPM precompute generation without committing protected IES assets or exposing public live capability for private photometry systems.

Implementation notes:

- Public default remains `PUBLIC_LIVE_SUPPORTED_MODES = ("SMD",)`.
- Private live mode opt-in uses `RAD_REBUILD_ENABLE_PRIVATE_LIVE_MODES=1`.
- Private IES paths are provided through `RAD_REBUILD_PRIVATE_CONVENTIONAL_IES` and `RAD_REBUILD_PRIVATE_HPS_IES`.
- Production deployment ignores the private override and remains precomputed-only.
- Runtime status and backend live-run enforcement use the same effective supported-mode calculation.

Validation:

- Runtime status tests for public default, missing private assets, private assets present, and production override.
- Backend live-run gate test proving private assets can authorize Conventional/HPS only under the private gate.
- No protected IES files, absolute private paths, or generated runtime outputs committed.

\nImplementation notes:\n\n- Update simulator FSPM control visibility to follow the runtime `live_supported_modes` payload. Public defaults still expose FSPM controls only for live SMD, while private live mode can expose the same controls for Conventional LED and 1000W HPS when private photometry is available.\n
## Validation Matrix

| Phase | Primary checks | Required commands |
| --- | --- | --- |
| 00 | Documentation only, no generated artifacts | `git diff --check`; `git diff --stat`; `git status --short`; optional import/config tests |
| 01 | Pure plant core and deterministic exports | Plant unit tests; import boundaries; config contracts; `git diff --check` |
| 02 | Runtime artifact export only | Plant export tests; workspace/artifact tests if touched; generated artifact check; `git diff --check` |
| 03 | Explicitly gated Radiance inclusion | Targeted Radiance scene tests; scientific characterization; baseline before behavior change |
| 04 | Request/config/workspace controls | API, route, boundary, workspace, OpenAPI checks as applicable; baseline for broad changes |
| 05 | Viewer rendering and toggle | Assembly viewer tests; browser smoke; Node parse checks; `npm run test:browser` |
| 06 | Deferred comparison metrics | Not part of the current implementation sequence |
| 07 | Photon absorption scaffold | Metric tests; documented assumptions; scientific review evidence |
| 08 | Private live-mode capability gate | Runtime status tests; backend gate tests; production/public default checks |

General command sequence:

1. Start with `git status --short`.
2. Run focused tests for the touched boundary.
3. Run `git diff --check`.
4. Run broader baseline before any scientific behavior change or before work expected to go through GitHub CI.
5. For frontend/viewer work, run browser smoke as a separate required check.
6. Check `git status --short` again for accidental generated outputs.

## Protected Behavior And Invariants

- Active modes remain stable:
  - `SMD`
  - `Competitor` / `competitor_practical`
  - `1000W HPS` / `hps_karma_4x4`
- `ACTIVE_RADIANCE_MODES` and public mode labels stay stable unless an explicit reviewed config change is made.
- Public simulator mode remains precomputed-first.
- Production remains precomputed-only.
- Live Docker and live local execution stay disabled unless explicitly configured in trusted environments.
- Existing public request defaults remain no-plant.
- No accidental request fingerprint changes without explicit tests.
- No schema version changes without a reviewed contract reason.
- No numerical golden updates without scientific review.
- No source curve, IES, or committed public bundle changes in early plant phases.
- No existing PPFD, power, layout, basis, manifest, or visualization semantics are silently reinterpreted.

## Artifact Policy

Allowed source files:

- Plant source code under `src/rad_rebuild/radiance/engine/plants/`.
- Focused tests under `tests/radiance/`.
- Viewer code under `src/rad_rebuild/web/static/js/assembly-viewer/` only in viewer phases.
- Backend/domain code only in explicit backend integration phases.
- This live engineering plan.

Disallowed generated outputs:

- Runtime `outputs/` content.
- Generated `.rad`, `.oct`, PPFD maps, visualization PNGs, cache files, benchmark JSON, expanded precomputed sweeps, large GLB/STEP files, and private datasets.
- Public precomputed bundle expansion outside a reviewed data phase.

Allowed tiny fixtures:

- Minimal deterministic JSON fixtures when required by tests.
- Minimal text fixtures for export contract tests when inline assertions are not practical.
- No binary fixtures unless a later phase explicitly justifies and reviews them.

Runtime output directories for generated plant artifacts:

- `outputs/radiance/runtime_state/`
- `outputs/radiance/artifacts/`
- Staged or committed workspace `runtime_state/` directories under the artifact store

These runtime outputs must remain ignored unless a later phase explicitly authorizes tiny committed fixtures.

## Live Plan Update Procedure

Each later phase must:

1. Read this file before changing code.
2. Update the current phase status/checklist after completion.
3. Add validation commands and results.
4. Add open issues discovered during the phase.
5. Add a short next-phase handoff.
6. Keep this document durable and concise. Do not add transient inventories, benchmark dumps, or generated phase reports.

## Decision Log

### Phase 00 Initial Decisions

- Plant modeling begins as a pure deterministic engine package under `src/rad_rebuild/radiance/engine/plants/`.
- Phase 01 must not change active runtime, backend, public API, viewer, or simulation behavior.
- Plant geometry targets leafy-green / lettuce-style MVP scope.
- Plant photon absorption remains groundwork only until material assumptions and metrics are reviewed.
- Runtime/generated plant artifacts belong under ignored runtime output or workspace directories.
- Public precomputed bundles are not expanded in early phases.
- Existing assembly scene, manifest, request fingerprint, and public mode contracts remain protected.

### Phase 01 Implementation Decisions

- The Phase 01 plant package uses frozen dataclasses and standard-library deterministic random generation only.
- The default rosette uses 2 x 2 plants, 12 leaves per plant, and a fixed low-density mesh of 15 vertices and 16 triangle faces per leaf.
- Optical assumptions are validated as an energy partition that must sum to 1.0.
- Radiance export returns text only and uses a placeholder `plastic` material until a reviewed leaf material model is selected.
- Viewer export returns JSON-serializable mesh payloads and metadata only; no GLB generation or browser integration was added.

### Phase 02 Artifact Export Decisions

- Plant runtime artifact export lives in the pure engine plant package and writes only to caller-provided directories.
- Phase 02 uses fixed filenames: `plants.rad`, `plants_viewer.json`, `plants_manifest.json`, and `plant_config.json`.
- Plant artifact JSON is canonicalized with sorted keys and stable indentation.
- The plant-specific manifest records relative file paths, bytes, SHA-256 hashes, deterministic provenance, and `active_simulation_integration: false`.
- No backend workspace sync, public artifact grants, request fingerprints, scene assembly, or viewer UI integration was added.

### Phase 03 Live Inclusion Decisions

- Plant scene inclusion is gated only by explicit environment variables in this phase.
- `FSPM_PLANTS_ENABLED` is the sole inclusion switch; disabled runs keep the prior `oconv` input list.
- Enabled runs generate plant artifacts into `RADIANCE_RUNTIME_STATE_ROOT` and append `plants.rad` to live `oconv` inputs.
- Plant artifacts are optionally copied by workspace sync when files are present, but existing Radiance visualization manifest schema and public artifact contracts are unchanged.
- Request schemas, request fingerprints, public API controls, viewer UI, and precomputed bundles remain deferred.

### Unresolved Decisions For Later Phases

- Exact lettuce geometry parameterization and acceptable default density.
- Leaf material model for Radiance.
- Whether plant controls become public API fields, internal developer flags, or both.
- Whether plant-enabled requests require a new fingerprint field or a separate artifact namespace.
- Whether viewer plant geometry should be procedural browser geometry, JSON mesh payloads, or static assets.
- How plant absorption metrics should represent units, surfaces, transmittance, reflectance, and uncertainty.


### Phase 08 Private Live Mode Gate Decisions

- The private live gate is an explicit local capability gate, not a secret security boundary.
- Conventional LED and 1000W HPS remain unavailable to public live execution by default.
- Private live unlock requires both `RAD_REBUILD_ENABLE_PRIVATE_LIVE_MODES=1` and local IES path env vars.
- The source code must not commit private IES assets or Austin-local absolute paths.
- Public precomputed bundles may later include derived FSPM artifacts, but not protected photometry inputs.


## Open Questions And Risk Register

- Radiance material model for leaves: decide whether early phases use a conservative placeholder material or defer optical properties until scientific review.
- Transmittance and reflectance assumptions: document source, units, and limitations before using them in absorption metrics.
- Plant geometry density versus runtime cost: lettuce meshes can become expensive quickly; Phase 01 should include deterministic density limits and tests.
- Viewer/Radiance coordinate alignment: reconcile plant coordinates with existing room coordinates and viewer x/z/y mapping.
- Request fingerprint compatibility: adding plant controls could invalidate workspace reuse and public artifact keys; defer until Phase 04 and test explicitly.
- Public precomputed behavior: plant-enabled behavior must not accidentally bypass or mutate precomputed-first public playback.
- Scene defaults: no-plant must remain the default for every existing request.
- Artifact leakage: generated plant files must not expose local paths, secrets, or runtime-only state in public JSON.
