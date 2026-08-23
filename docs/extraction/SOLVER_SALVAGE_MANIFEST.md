# Solver Salvage Manifest

## Executive Summary

The extractable core is the scientific Radiance/FSPM optics layer: plant geometry, mesh-patch receiver sampling, receiver rtrace parsing and aggregation, leaf optical material fitting, Rex A/T/R spectral absorption, fixture photometry, SMD layout and power schedules, basis transport, and uniformity optimization. These should move into a new headless package with narrow, typed configuration and explicit workspace inputs.

The largest architectural risk is that the most valuable transport behavior is currently concentrated in `src/rad_rebuild/radiance/cli/scripts.py`. That file mixes solver policy, Radiance command construction, environment variables, subprocess execution, artifact writing, precomputed playback assumptions, and public app runtime state. The extraction should split this file before moving any high-value routines.

Do not migrate browser, backend route, assembly viewer, public-demo precomputed playback, deployment, or static presentation code into the new compute engine. Keep precomputed bundle generation and playback as historical reference only unless a separate dataset tool is later created.

## Proposed New Repo Module Map

| Old Area | Proposed New Path | Notes |
|---|---|---|
| `radiance/executables.py` | `src/fspm_optics/radiance/executables.py` | Executable lookup only; no subprocess execution. |
| `radiance/fspm_targets.py` | `src/fspm_optics/fspm/targets.py` | Target coverage thresholds and classification metadata. |
| `radiance/engine/plants/{config,models,mesh,layout,generator,radiance_export}.py` | `src/fspm_optics/plants/` | Deterministic plant geometry and Radiance polygon export. |
| `radiance/engine/plants/surface_flux.py` | `src/fspm_optics/receivers/{samples.py,aggregation.py,artifacts.py}` | Split sample generation, rtrace parsing, aggregation, and legacy artifact payloads. |
| `radiance/engine/plants/{leaf_materials,optical_profiles,spectral,spectral_absorption}.py` | `src/fspm_optics/optics/` | Leaf A/T/R, Radiance trans material fitting, SPD parsing, and banded absorption. |
| `radiance/engine/plants/{photosynthesis,photoreceptor,photomorphogenesis}.py` | `src/fspm_optics/biology/` | Optional biological-response layer, downstream of optical transport. |
| `radiance/engine/emitters/smd_generation/` | `src/fspm_optics/fixtures/smd/` | Ring layout, module profiles, power schedules, source variants, and Radiance writers. |
| `radiance/engine/emitters/hps_generation/` | `src/fspm_optics/fixtures/hps/` | HPS fixture profile, geometry, and Radiance writer. |
| `radiance/engine/emitters/conventional_led_generation/` | `src/fspm_optics/fixtures/conventional_led/` | Conventional LED geometry, SPD/IES staging, and Radiance writer. |
| `radiance/engine/photometry/` | `src/fspm_optics/photometry/` | PPFD metrics, IES photon toolkit, SMD curve model, and grid symmetrization. |
| `radiance/engine/simulation/basis_{backends,rcontrib}.py` | `src/fspm_optics/transport/basis/` | Headless Radiance basis execution with injected runner/workspace. |
| `radiance/engine/optimization/solve_uniformity.py` | `src/fspm_optics/optimization/uniformity.py` | Basis-matrix optimizer without CLI/env coupling. |
| Extracted `cli/scripts.py` helpers | `src/fspm_optics/runner/` and `src/fspm_optics/transport/` | See the extraction plan below. |
| `tests/radiance/` scientific tests | `tests/` in new repo | Port high-value unit tests first; rewrite CLI/backend/browser tests. |

## File Classification Table

Touch flags in the Reason column use `Cmd`, `FSPM`, `PPFD`, and `Spectral` for whether the file touches Radiance command execution, plant/FSPM receiver transport, baseline PPFD transport, and spectral/leaf optical logic.

| Current Path | Classification | New Path | Reason | Key Dependencies | Relevant Tests | Risk |
|---|---|---|---|---|---|---|
| `src/rad_rebuild/radiance/config.py` | REFERENCE ONLY | Selected constants to `src/fspm_optics/config/modes.py` if needed | Cmd no; FSPM no; PPFD no; Spectral no. Public app mode labels, demo defaults, and precomputed/public UI configuration are not a solver boundary. | `os`, package config | `test_config_contracts.py`, `test_phase05_contracts.py` | high |
| `src/rad_rebuild/radiance/domain.py` | REFERENCE ONLY | Selected request DTOs to `src/fspm_optics/config/requests.py` | Cmd no; FSPM indirect; PPFD indirect; Spectral no. Pydantic API/domain schemas include route, viewer, and public-demo response shape; useful only as validation history. | `pydantic`, `rad_rebuild.radiance.config`, plant config | `test_api_contracts.py`, `test_route_query_contracts.py` | high |
| `src/rad_rebuild/radiance/executables.py` | KEEP EXACTLY | `src/fspm_optics/radiance/executables.py` | Cmd resolves only; FSPM no; PPFD no; Spectral no. Clean executable resolution helper. | `os`, `pathlib` | `test_executable_resolution.py` | low |
| `src/rad_rebuild/radiance/fspm_targets.py` | KEEP EXACTLY | `src/fspm_optics/fspm/targets.py` | Cmd no; FSPM yes; PPFD yes; Spectral no. Pure target threshold classification used by receiver and target-coverage artifacts. | `dataclasses` | `test_plant_surface_flux_artifact.py`, `test_phase125b_cli_orchestration.py` | low |
| `src/rad_rebuild/radiance/paths.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Old repository path constants should be replaced by package resources and injected workspaces. | `pathlib` | `test_config_contracts.py` | medium |
| `src/rad_rebuild/radiance/settings.py` | REFERENCE ONLY | Selected settings model to `src/fspm_optics/config/settings.py` | Cmd no; FSPM indirect; PPFD indirect; Spectral no. Contains web/backend ports, public precomputed roots, and runtime mode decisions. | `os`, `pathlib`, `dataclasses` | `test_runtime_status.py`, `test_config_contracts.py` | high |
| `src/rad_rebuild/radiance/cli/scripts.py` | KEEP BUT REFACTOR | `src/fspm_optics/runner/`, `src/fspm_optics/transport/`, `src/fspm_optics/artifacts/` | Cmd yes; FSPM yes; PPFD yes; Spectral yes. Critical solver orchestration is mixed with env vars, subprocesses, runtime state, old paths, precomputed output, and CLI exit behavior. | `subprocess`, `numpy`, engine modules, runtime state, env vars | `test_phase125b_cli_orchestration.py`, `test_run_uniformity_argv.py`, many integration tests | high |
| `src/rad_rebuild/radiance/assembly/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Package marker for presentation assembly. | None | N/A | low |
| `src/rad_rebuild/radiance/assembly/classification.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Viewer fixture grouping and display classification may inform metadata but is not solver logic. | `dataclasses`, fixture metadata | `test_assembly_viewer_payload.py` | low |
| `src/rad_rebuild/radiance/assembly/transforms.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Small coordinate transforms for assembly presentation; solver should define its own geometry transforms near transport. | `math` | `test_assembly_viewer_payload.py` | low |
| `src/rad_rebuild/radiance/assembly/fspm_csv.py` | REFERENCE ONLY | N/A | Cmd no; FSPM yes; PPFD yes; Spectral yes. Useful report aggregation examples, but this is CSV/panel export rather than core transport. | `csv`, JSON artifact schemas | `test_fspm_csv_exports.py` if retained | medium |
| `src/rad_rebuild/radiance/assembly/fspm_panel.py` | REFERENCE ONLY | N/A | Cmd no; FSPM yes; PPFD yes; Spectral yes. Useful historical FSPM summary calculations, coupled to viewer panel semantics. | JSON artifact schemas | Viewer/panel tests | medium |
| `src/rad_rebuild/radiance/assembly/scene.py` | DROP | N/A | Cmd no; FSPM display only; PPFD display only; Spectral display only. Imports backend models and web viewer/static paths; should not enter headless solver. | `rad_rebuild.radiance.backend.models`, web static constants, assembly files | `test_assembly_viewer_*` | high |
| `src/rad_rebuild/radiance/engine/emitters/deterministic_ids.py` | KEEP EXACTLY | `src/fspm_optics/radiance/identifiers.py` | Cmd no; FSPM no; PPFD no; Spectral no. Deterministic emitter IDs are a clean artifact-contract helper. | `hashlib` | `test_deterministic_emitter_ids.py` | low |
| `src/rad_rebuild/radiance/engine/emitters/generate_emitters_smd.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD fixture input; Spectral indirect. Compatibility wrapper around old SMD service. | SMD service | SMD generation tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/generate_emitters_hps.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD fixture input; Spectral indirect. Compatibility wrapper around old HPS service. | HPS service | HPS tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/generate_emitters_conventional_led.py` | REFERENCE ONLY | N/A | Cmd indirect; FSPM no; PPFD fixture input; Spectral yes. Wrapper around conventional LED service and IES staging. | conventional_led service | conventional_led/IES tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/config.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/config.py` | Cmd no; FSPM no; PPFD yes; Spectral indirect. Valuable SMD config dataclasses mixed with env/global defaults. | `dataclasses`, env, geometry constants | `test_smd_optics_contract.py` | medium |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/fixture_overlay.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/overlay.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Fixture overlay metadata is useful but currently presentation-adjacent. | SMD positions/config | Viewer and SMD contract tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/module_profile.py` | KEEP EXACTLY | `src/fspm_optics/fixtures/smd/module_profile.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Clean module-level wattage/photon profile definitions. | `dataclasses` | `test_smd_optics_contract.py`, `test_smd_power_schedule.py` | low |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/optical_stack.py` | KEEP EXACTLY | `src/fspm_optics/fixtures/smd/optical_stack.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Compact optical stack math and metadata. | SMD module/profile helpers | `test_smd_optics_contract.py` | low |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/outputs.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/artifacts.py` | Cmd no; FSPM no; PPFD yes; Spectral indirect. Useful JSON output contracts but old path and report concerns should be isolated. | JSON, pathlib | SMD output tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/photons.py` | KEEP EXACTLY | `src/fspm_optics/fixtures/smd/photons.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Photon/watt/PPE conversion helpers are pure. | math/constants | `test_smd_ppe_matching.py` | low |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/positions.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/positions.py` | Cmd no; FSPM no; PPFD yes; Spectral no. SMD ring/layout placement is central but tied to env, old layout modules, and `SystemExit`. | layout generator, env, geometry | `test_phase125c2_geometry_contracts.py`, `test_smd_optics_contract.py` | high |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/power_overrides.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/power_overrides.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Useful override parsing should become typed config without environment coupling. | env, parsing | `test_smd_power_schedule.py` | medium |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/power_schedule.py` | KEEP EXACTLY | `src/fspm_optics/fixtures/smd/power_schedule.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Core SMD ring/basis/power schedule logic is clean and heavily worth porting. | `dataclasses`, math | `test_smd_power_schedule.py`, `test_smd_optics_contract.py` | low |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/rad_writer.py` | KEEP EXACTLY | `src/fspm_optics/fixtures/smd/radiance_writer.py` | Cmd no; FSPM no; PPFD yes; Spectral indirect. Radiance text writer for SMD emitters; minimal path coupling. | pathlib, SMD config | SMD generation tests | low |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/service.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/build.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. High-value fixture generation but import-time globals, env, file outputs, and old path assumptions must be removed. | SMD submodules, photometry, paths, env | `test_phase125d_scientific_services.py`, `test_smd_optics_contract.py` | high |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/solution_metadata.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/solution_metadata.py` | Cmd no; FSPM no; PPFD yes; Spectral indirect. Useful solver metadata, currently artifact/report shaped. | JSON, SMD config | SMD contract tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/source_variants.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/smd/source_variants.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Angular/source variant math is valuable; CAL/file-writing concerns need isolation. | math, pathlib | `test_smd_optics_contract.py` | medium |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/summary.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD yes; Spectral indirect. Human/report summary layer, not solver core. | JSON/report helpers | SMD summary tests | low |
| `src/rad_rebuild/radiance/engine/emitters/hps_generation/profile.py` | KEEP EXACTLY | `src/fspm_optics/fixtures/hps/profile.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Clean HPS fixture profile constants and dataclasses. | `dataclasses` | `test_hps_fixture_profile.py` | low |
| `src/rad_rebuild/radiance/engine/emitters/hps_generation/geometry.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/hps/geometry.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Fixture geometry is useful but should lose old service assumptions. | HPS profile | `test_hps_fixture_profile.py` | medium |
| `src/rad_rebuild/radiance/engine/emitters/hps_generation/rad_writer.py` | KEEP EXACTLY | `src/fspm_optics/fixtures/hps/radiance_writer.py` | Cmd no; FSPM no; PPFD yes; Spectral indirect. Radiance writer is close to pure text generation. | HPS geometry/profile | HPS fixture tests | low |
| `src/rad_rebuild/radiance/engine/emitters/hps_generation/service.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/hps/build.py` | Cmd indirect; FSPM no; PPFD yes; Spectral yes. Fixture build service is useful but coupled to old paths, IES assets, and outputs. | IES toolkit, paths, HPS modules | `test_hps_fixture_profile.py`, `test_emitter_characterization_lock.py` | high |
| `src/rad_rebuild/radiance/engine/emitters/hps_generation/summary.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD yes; Spectral indirect. Reporting summary only. | JSON/report helpers | HPS tests | low |
| `src/rad_rebuild/radiance/engine/emitters/conventional_led_generation/config.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/conventional_led/config.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Conventional LED dimensions and SPD/IES configuration need typed, path-injected settings. | env, paths | conventional_led tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/conventional_led_generation/geometry.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/conventional_led/geometry.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Useful fixture geometry but contains CLI/print/exit behavior. | config, math | conventional_led geometry tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/conventional_led_generation/ies_stage.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/conventional_led/ies_stage.py` | Cmd yes; FSPM no; PPFD yes; Spectral yes. Important SPD/IES staging and `ies2rad` integration; inject command runner. | IES toolkit, subprocess/executables | IES/SPD tests | high |
| `src/rad_rebuild/radiance/engine/emitters/conventional_led_generation/rad_writer.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/conventional_led/radiance_writer.py` | Cmd no; FSPM no; PPFD yes; Spectral indirect. Radiance writer is useful but has conventional LED service assumptions. | geometry/config | conventional_led tests | medium |
| `src/rad_rebuild/radiance/engine/emitters/conventional_led_generation/service.py` | KEEP BUT REFACTOR | `src/fspm_optics/fixtures/conventional_led/build.py` | Cmd yes; FSPM no; PPFD yes; Spectral yes. Valuable build service, but command execution and old asset paths must be injected. | IES stage, paths, subprocess | conventional_led service tests | high |
| `src/rad_rebuild/radiance/engine/emitters/conventional_led_generation/summary.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD yes; Spectral indirect. Human-facing summary layer. | JSON/report helpers | conventional_led tests | low |
| `src/rad_rebuild/radiance/engine/geometry/generate_grid.py` | KEEP BUT REFACTOR | `src/fspm_optics/geometry/sensor_grid.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Sensor grid math matters; CLI/env/printing should be removed. | math, pathlib, env | `test_phase125c2_geometry_contracts.py` | medium |
| `src/rad_rebuild/radiance/engine/geometry/generate_room.py` | KEEP BUT REFACTOR | `src/fspm_optics/radiance/room.py` | Cmd no; FSPM no; PPFD scene input; Spectral no. Room Radiance generation is valuable but old file output and env behavior need cleanup. | pathlib, env | room/geometry tests | medium |
| `src/rad_rebuild/radiance/engine/geometry/rect_layout.py` | KEEP BUT REFACTOR | `src/fspm_optics/geometry/rect_layout.py` | Cmd no; FSPM no; PPFD fixture layout; Spectral no. Rectangular layout math is useful; split env helpers from pure layout. | math, env | geometry contract tests | medium |
| `src/rad_rebuild/radiance/engine/layout/domain.py` | KEEP EXACTLY | `src/fspm_optics/layout/domain.py` | Cmd no; FSPM no; PPFD fixture layout; Spectral no. Pure layout domain records. | `dataclasses` | layout/geometry tests | low |
| `src/rad_rebuild/radiance/engine/layout/layout_generator.py` | KEEP BUT REFACTOR | `src/fspm_optics/layout/modular.py` | Cmd no; FSPM no; PPFD fixture layout; Spectral no. Important modular layout logic, with legacy/global compatibility behavior. | math, layout domain | `test_phase125c2_geometry_contracts.py` | medium |
| `src/rad_rebuild/radiance/engine/layout/layout_engine.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD display/layout; Spectral no. Prototype/layout visualization layer with PIL; not core solver. | PIL, drawing | visual layout tests | low |
| `src/rad_rebuild/radiance/engine/photometry/ies_photon_toolkit.py` | KEEP BUT REFACTOR | `src/fspm_optics/photometry/ies.py` | Cmd yes; FSPM no; PPFD yes; Spectral yes. Important IES/SPD photon math and `ies2rad` handling; command execution must be injected. | subprocess, numpy, SPD/IES assets | `test_emitter_characterization_lock.py`, IES tests | high |
| `src/rad_rebuild/radiance/engine/photometry/ppfd_metrics.py` | KEEP EXACTLY | `src/fspm_optics/photometry/ppfd_metrics.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Pure PPFD metrics and uniformity calculations. | `numpy` | `test_phase03_properties.py`, metrics tests | low |
| `src/rad_rebuild/radiance/engine/photometry/smd_curve_model.py` | KEEP BUT REFACTOR | `src/fspm_optics/photometry/smd_curve_model.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Important curve model tied to old data paths and validation outputs. | numpy, path/data files | `test_smd_optics_contract.py`, `test_phase03_characterization.py` | medium |
| `src/rad_rebuild/radiance/engine/photometry/symmetrize_ppfd.py` | KEEP BUT REFACTOR | `src/fspm_optics/photometry/symmetrize.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Useful grid normalization with CLI/file behavior to isolate. | numpy, CSV | PPFD metrics tests | medium |
| `src/rad_rebuild/radiance/engine/optimization/solve_uniformity.py` | KEEP BUT REFACTOR | `src/fspm_optics/optimization/uniformity.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Core basis optimizer should move, minus CLI/env/JSON coupling. | numpy, scipy, JSON, pathlib | `test_run_uniformity_argv.py`, `test_phase125b_cli_orchestration.py` | high |
| `src/rad_rebuild/radiance/engine/plants/__init__.py` | KEEP BUT REFACTOR | `src/fspm_optics/plants/__init__.py` | Cmd no; FSPM yes; PPFD no; Spectral yes. Rebuild exports around the new package split. | plant modules | plant tests | low |
| `src/rad_rebuild/radiance/engine/plants/absorption.py` | KEEP EXACTLY | `src/fspm_optics/plants/absorption.py` | Cmd no; FSPM yes; PPFD no; Spectral yes. Pure absorption aggregation and registry contracts. | plant models | `test_fspm_absorption_phase07.py` | low |
| `src/rad_rebuild/radiance/engine/plants/artifacts.py` | KEEP BUT REFACTOR | `src/fspm_optics/artifacts/plant_absorption.py` | Cmd no; FSPM yes; PPFD indirect; Spectral yes. Useful artifact contract but file output and old schema naming should be separated from core math. | JSON, pathlib, plant absorption | `test_fspm_absorption_phase07.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/config.py` | KEEP EXACTLY | `src/fspm_optics/plants/config.py` | Cmd no; FSPM yes; PPFD no; Spectral no. Plant configuration dataclasses are clean. | `dataclasses` | `test_fspm_plants.py` | low |
| `src/rad_rebuild/radiance/engine/plants/generator.py` | KEEP EXACTLY | `src/fspm_optics/plants/generator.py` | Cmd no; FSPM yes; PPFD no; Spectral no. Deterministic plant generation and natural-fit layout are central. | plant models/layout/mesh | `test_fspm_plants.py`, `test_rex_growth_geometry.py` | low |
| `src/rad_rebuild/radiance/engine/plants/layout.py` | KEEP EXACTLY | `src/fspm_optics/plants/layout.py` | Cmd no; FSPM yes; PPFD no; Spectral no. Pure plant placement helpers. | math, config/models | `test_fspm_plants.py` | low |
| `src/rad_rebuild/radiance/engine/plants/leaf_materials.py` | KEEP EXACTLY | `src/fspm_optics/optics/leaf_materials.py` | Cmd no; FSPM yes; PPFD no; Spectral yes. Rex leaf A/T/R weighted coefficients and Radiance trans material fitting are clean. | optical profiles, math | `test_plant_leaf_materials.py`, `test_plant_spectral_optics.py` | low |
| `src/rad_rebuild/radiance/engine/plants/mesh.py` | KEEP EXACTLY | `src/fspm_optics/plants/mesh.py` | Cmd no; FSPM yes; PPFD no; Spectral no. Pure mesh geometry and patch area/normal data. | math, plant models | `test_fspm_plants.py`, `test_plant_surface_flux_artifact.py` | low |
| `src/rad_rebuild/radiance/engine/plants/models.py` | KEEP EXACTLY | `src/fspm_optics/plants/models.py` | Cmd no; FSPM yes; PPFD no; Spectral no. Core plant/leaf/mesh data models. | `dataclasses` | `test_fspm_plants.py` | low |
| `src/rad_rebuild/radiance/engine/plants/optical_profiles.py` | KEEP BUT REFACTOR | `src/fspm_optics/optics/profiles.py` | Cmd no; FSPM yes; PPFD no; Spectral yes. Rex optical profile loading is essential but data paths must become package resources. | CSV, JSON, pathlib | `test_plant_optical_profiles.py`, `test_plant_leaf_materials.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/photomorphogenesis.py` | KEEP BUT REFACTOR | `src/fspm_optics/biology/photomorphogenesis.py` | Cmd no; FSPM receiver summaries; PPFD indirect; Spectral yes. Optional biology response logic, downstream from optics. | spectral exposure models | `test_plant_photomorphogenesis_response.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/photoreceptor.py` | KEEP BUT REFACTOR | `src/fspm_optics/biology/photoreceptor.py` | Cmd no; FSPM receiver summaries; PPFD indirect; Spectral yes. Useful receptor exposure calculations, but keep optional. | spectral data | `test_plant_photoreceptor_exposure.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/photosynthesis.py` | KEEP BUT REFACTOR | `src/fspm_optics/biology/photosynthesis.py` | Cmd no; FSPM receiver summaries; PPFD yes; Spectral yes. Useful response model downstream from receiver PPFD and absorption. | spectral absorption, response models | `test_plant_photosynthesis_response.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/radiance_export.py` | KEEP EXACTLY | `src/fspm_optics/plants/radiance_export.py` | Cmd no; FSPM yes; PPFD scene input; Spectral indirect. Radiance polygon export for plant meshes is core solver input. | plant models/mesh | `test_fspm_plants.py`, `test_phase125b_cli_orchestration.py` | low |
| `src/rad_rebuild/radiance/engine/plants/spectral.py` | KEEP BUT REFACTOR | `src/fspm_optics/optics/spectral.py` | Cmd no; FSPM receiver summaries; PPFD indirect; Spectral yes. SPD parsing and spectral payload helpers are valuable; placeholder defaults should be separated. | CSV, JSON, pathlib | `test_plant_spectral_optics.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/spectral_absorption.py` | KEEP BUT REFACTOR | `src/fspm_optics/optics/spectral_absorption.py` | Cmd no; FSPM yes; PPFD indirect; Spectral yes. Core Rex leaf A/T/R and banded absorption logic; split pure optics from artifact writing/env defaults. | numpy, optical profiles, leaf materials | `test_plant_spectral_absorption.py`, `test_plant_spectral_optics.py` | high |
| `src/rad_rebuild/radiance/engine/plants/surface_flux.py` | KEEP BUT REFACTOR | `src/fspm_optics/receivers/{samples.py,aggregation.py,artifacts.py}` | Cmd parses output only; FSPM yes; PPFD yes; Spectral indirect. Critical mesh-patch receiver generation, rtrace parsing, raw flux aggregation, and target coverage logic; viewer payloads should be split off. | numpy, plant mesh/models, fspm targets | `test_plant_surface_flux_artifact.py`, `test_phase125b_cli_orchestration.py` | high |
| `src/rad_rebuild/radiance/engine/plants/viewer_export.py` | DROP | N/A | Cmd no; FSPM display only; PPFD display only; Spectral display only. Browser/3D viewer payload generation. | viewer artifact schemas | viewer tests | low |
| `src/rad_rebuild/radiance/engine/plants/growth/adapter.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/adapter.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Useful bridge from optical summaries to growth models; remove runtime artifact assumptions. | growth response/profile modules | `test_rex_growth_adapter.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/canopy.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/canopy.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Optional canopy response logic; keep downstream from optics. | growth state/profile | growth tests | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/daily.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/daily.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Optional daily integration logic; isolate from app runtime. | growth state/profile | growth tests | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/evolution.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/evolution.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Optional deterministic growth evolution; keep after optical core. | growth state/response | `test_rex_growth_evolution.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/exposure.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/exposure.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Multispectral exposure summaries are useful but secondary to solver extraction. | spectral absorption, surface flux | receptor/photosynthesis tests | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/profile.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/profile.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Growth profile constants/config should become package resources. | dataclasses | growth response tests | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/response.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/response.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Scientific response model is useful, but optional. | growth profile/state | `test_rex_growth_response.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/runtime_artifact.py` | REFERENCE ONLY | N/A | Cmd no; FSPM yes; PPFD yes; Spectral yes. Runtime artifact writer coupled to current app outputs. | JSON, pathlib | `test_rex_growth_runtime_scaffold.py` | medium |
| `src/rad_rebuild/radiance/engine/plants/growth/state.py` | KEEP BUT REFACTOR | `src/fspm_optics/growth/state.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Growth state dataclasses are portable after package rename. | dataclasses | growth tests | low |
| `src/rad_rebuild/radiance/engine/plants/growth/static_render.py` | REFERENCE ONLY | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Static rendering/report artifact is not core. | image/render helpers | growth render tests | low |
| `src/rad_rebuild/radiance/engine/plants/growth/viewer_timeline.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Viewer timeline payload belongs to browser app, not solver. | viewer schemas | viewer/growth UI tests | low |
| `src/rad_rebuild/radiance/engine/simulation/basis_backends.py` | KEEP BUT REFACTOR | `src/fspm_optics/transport/basis/backends.py` | Cmd option construction; FSPM no; PPFD yes; Spectral no. Basis backend configuration is important but imports old domain/config. | domain enums, dataclasses | `test_basis_rcontrib.py`, `test_phase125b_cli_orchestration.py` | medium |
| `src/rad_rebuild/radiance/engine/simulation/basis_rcontrib.py` | KEEP BUT REFACTOR | `src/fspm_optics/transport/basis/rcontrib.py` | Cmd yes; FSPM no; PPFD yes; Spectral no. Core rcontrib/oconv basis builder; needs injected runner, workspace, and command audit. | subprocess, numpy, Radiance executables | `test_basis_rcontrib.py` | high |
| `src/rad_rebuild/radiance/engine/simulation/precompute_sweep.py` | DROP | N/A | Cmd yes; FSPM yes; PPFD yes; Spectral yes. Public-demo/precomputed bundle generator imports backend models and output packaging; out of scope. | backend models, bundle writer, CLI/env | precomputed tests | high |
| `src/rad_rebuild/radiance/engine/simulation/precomputed_dataset.py` | DROP | N/A | Cmd no; FSPM artifact playback; PPFD playback; Spectral playback. Public dataset lookup/playback support, not clean-room solver logic. | data paths, manifests | `test_precomputed_*` | high |
| `src/rad_rebuild/radiance/engine/simulation/precomputed_integrity.py` | REFERENCE ONLY | N/A | Cmd no; FSPM artifact validation; PPFD playback; Spectral playback. Useful archive safety patterns, but tied to public bundle contracts. | hashes, manifests, pathlib | `test_precomputed_integrity.py` | medium |
| `src/rad_rebuild/radiance/engine/simulation/precomputed_playback.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Imports assembly/backend/public-demo playback concepts; not a headless solver. | backend models, assembly scene, public data | `test_precomputed_playback.py`, route tests | high |
| `src/rad_rebuild/radiance/engine/validation/results.py` | KEEP EXACTLY | `src/fspm_optics/validation/results.py` | Cmd no; FSPM no; PPFD no; Spectral no. Clean validation result dataclass. | dataclasses | validation tests | low |
| `src/rad_rebuild/radiance/engine/validation/validate_hps_ies.py` | REFERENCE ONLY | `tools/validate_hps_ies.py` if needed | Cmd yes; FSPM no; PPFD yes; Spectral yes. Validation recipe useful historically but should not be core library code. | IES toolkit, HPS modules | HPS/IES tests | medium |
| `src/rad_rebuild/radiance/engine/validation/validate_smd_curve_model.py` | KEEP BUT REFACTOR | `src/fspm_optics/validation/smd_curve_model.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Useful scientific validation, but separate from production library API. | SMD curve model | `test_smd_optics_contract.py` | medium |
| `src/rad_rebuild/radiance/engine/validation/validate_smd_power_solution.py` | REFERENCE ONLY | `tools/validate_smd_power_solution.py` if needed | Cmd no; FSPM no; PPFD yes; Spectral no. Validation script/report, not core. | SMD power modules | `test_smd_power_schedule.py` | low |
| `src/rad_rebuild/radiance/engine/validation/validate_smd_spd_integration.py` | REFERENCE ONLY | `tools/validate_smd_spd_integration.py` if needed | Cmd no; FSPM no; PPFD yes; Spectral yes. Validation recipe for SPD integration; keep as reference. | SMD SPD/photometry | spectral tests | medium |
| `src/rad_rebuild/radiance/engine/visualization/data.py` | REFERENCE ONLY | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Some grid helpers are useful examples, but this is plotting support. | pandas/numpy | visualization tests | low |
| `src/rad_rebuild/radiance/engine/visualization/heatmaps.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Presentation heatmap generation. | matplotlib/pandas | `test_visualization_heatmaps.py` | low |
| `src/rad_rebuild/radiance/engine/visualization/overlays.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Presentation overlay generation. | matplotlib/PIL | visualization tests | low |
| `src/rad_rebuild/radiance/engine/visualization/scatter.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Presentation scatter plots. | matplotlib/pandas | `test_visualization_scatter.py` | low |
| `src/rad_rebuild/radiance/engine/visualization/style.py` | DROP | N/A | Cmd no; FSPM no; PPFD display; Spectral no. Plot styling only. | matplotlib | visualization tests | low |
| `src/rad_rebuild/radiance/engine/visualization/visualize_ppfd.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Visualization CLI/presentation layer. | visualization modules | visualization tests | low |
| `scripts/radiance/run_uniformity.sh` | REFERENCE ONLY | `tools/run_uniformity` after API split | Cmd yes via Python CLI; FSPM no; PPFD yes; Spectral no. Thin shell wrapper around old CLI. | shell, `python -m rad_rebuild...` | `test_run_uniformity_argv.py` | low |
| `scripts/radiance/run_basis_extraction.sh` | REFERENCE ONLY | N/A | Cmd yes; FSPM optional; PPFD yes; Spectral no. Old reproducibility wrapper; replace with new CLI after runner extraction. | shell, CLI scripts | basis tests | low |
| `scripts/radiance/generate_sensor_grid.sh` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD yes; Spectral no. Thin wrapper over old CLI sensor grid. | shell, CLI scripts | geometry tests | low |
| `scripts/radiance/run_simulation_smd.sh` | REFERENCE ONLY | N/A | Cmd yes through CLI; FSPM optional; PPFD yes; Spectral yes. Old all-in simulation wrapper. | shell, CLI scripts | integration tests | medium |
| `scripts/radiance/run_simulation_hps.sh` | REFERENCE ONLY | N/A | Cmd yes through CLI; FSPM optional; PPFD yes; Spectral yes. Old all-in HPS wrapper. | shell, CLI scripts | integration tests | medium |
| `scripts/radiance/run_simulation_conventional_led.sh` | REFERENCE ONLY | N/A | Cmd yes through CLI; FSPM optional; PPFD yes; Spectral yes. Old all-in conventional LED wrapper. | shell, CLI scripts | integration tests | medium |
| `scripts/radiance/reproduce.sh` | REFERENCE ONLY | N/A | Cmd yes through CLI; FSPM optional; PPFD yes; Spectral yes. Historical reproduction shell wrapper. | shell, CLI scripts | integration tests | medium |
| `scripts/radiance/generate_precomputed_bundles.sh` | DROP | N/A | Cmd yes; FSPM yes; PPFD yes; Spectral yes. Public-demo precomputed bundle generation is explicitly out of scope. | shell, precompute sweep | precomputed tests | high |
| `scripts/radiance/download_precomputed.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Public dataset download/verification tool, not solver core. | requests/urllib, archive safety | `test_download_precomputed_security.py` | medium |
| `src/rad_rebuild/radiance/engine/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface; create a fresh package boundary in the new repo. | package imports | import-boundary tests | low |
| `src/rad_rebuild/radiance/engine/emitters/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | import-boundary tests | low |
| `src/rad_rebuild/radiance/engine/emitters/smd_generation/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | SMD tests | low |
| `src/rad_rebuild/radiance/engine/emitters/hps_generation/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | HPS tests | low |
| `src/rad_rebuild/radiance/engine/emitters/conventional_led_generation/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | conventional_led tests | low |
| `src/rad_rebuild/radiance/engine/geometry/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | geometry tests | low |
| `src/rad_rebuild/radiance/engine/layout/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | layout tests | low |
| `src/rad_rebuild/radiance/engine/optimization/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | optimizer tests | low |
| `src/rad_rebuild/radiance/engine/photometry/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | photometry tests | low |
| `src/rad_rebuild/radiance/engine/plants/growth/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | growth tests | low |
| `src/rad_rebuild/radiance/engine/simulation/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | simulation tests | low |
| `src/rad_rebuild/radiance/engine/validation/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current package marker/export surface. | package imports | validation tests | low |
| `src/rad_rebuild/radiance/engine/visualization/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Visualization package marker; presentation layer does not move. | package imports | visualization tests | low |
| `src/rad_rebuild/radiance/engine/AGENTS.md` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Repository scientific-change instructions, not source to migrate. | documentation | N/A | low |
| `scripts/radiance/AGENTS.md` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Reproducibility instructions for current helper scripts, not source to migrate. | documentation | N/A | low |

### Test Files

| Current Path | Classification | New Path | Reason | Key Dependencies | Relevant Tests | Risk |
|---|---|---|---|---|---|---|
| `tests/radiance/test_plant_surface_flux_artifact.py` | KEEP BUT REFACTOR | `tests/test_receivers_surface_flux.py` | Cmd no; FSPM yes; PPFD yes; Spectral indirect. Port first for mesh-patch receivers, parsing, aggregation, and target coverage. | plant receiver modules | self | medium |
| `tests/radiance/test_plant_spectral_absorption.py` | KEEP BUT REFACTOR | `tests/test_spectral_absorption.py` | Cmd no; FSPM yes; PPFD indirect; Spectral yes. Port first for Rex A/T/R and banded absorption contracts. | spectral absorption modules | self | medium |
| `tests/radiance/test_plant_spectral_optics.py` | KEEP BUT REFACTOR | `tests/test_spectral_optics.py` | Cmd no; FSPM yes; PPFD indirect; Spectral yes. Port first for optical profile and spectral transport contracts. | optics modules | self | medium |
| `tests/radiance/test_plant_leaf_materials.py` | KEEP BUT REFACTOR | `tests/test_leaf_materials.py` | Cmd no; FSPM yes; PPFD no; Spectral yes. Port for Radiance trans material fitting and leaf coefficient contracts. | leaf material modules | self | low |
| `tests/radiance/test_plant_optical_profiles.py` | KEEP BUT REFACTOR | `tests/test_optical_profiles.py` | Cmd no; FSPM yes; PPFD no; Spectral yes. Port with package-resource fixtures replacing old data paths. | optical profile assets | self | medium |
| `tests/radiance/test_fspm_plants.py` | KEEP BUT REFACTOR | `tests/test_plants_geometry.py` | Cmd no; FSPM yes; PPFD no; Spectral no. Port for deterministic plant generation and mesh geometry. | plant generator/models | self | low |
| `tests/radiance/test_fspm_absorption_phase07.py` | KEEP BUT REFACTOR | `tests/test_absorption.py` | Cmd no; FSPM yes; PPFD no; Spectral yes. Port pure absorption aggregation; rewrite artifact assertions. | absorption/artifact modules | self | medium |
| `tests/radiance/test_phase125b_cli_orchestration.py` | KEEP BUT REFACTOR | Split across `tests/test_runner_transport.py`, `tests/test_receivers_rtrace.py`, `tests/test_scalar_ppfd.py` | Cmd yes with stubs; FSPM yes; PPFD yes; Spectral yes. Do not port whole CLI file; extract high-value stubbed contracts. | CLI scripts internals, monkeypatch | self | high |
| `tests/radiance/test_basis_rcontrib.py` | KEEP BUT REFACTOR | `tests/test_basis_rcontrib.py` | Cmd yes with stubs; FSPM no; PPFD yes; Spectral no. Port command construction with injected runner. | basis modules | self | high |
| `tests/radiance/test_fspm_baseline_octree.py` | KEEP BUT REFACTOR | `tests/test_octree_scene_inputs.py` | Cmd yes with stubs; FSPM yes; PPFD yes; Spectral no. Preserve contract that baseline octree excludes plants and FSPM octree includes plants. | CLI scene helpers | self | high |
| `tests/radiance/test_fspm_basis_skip.py` | KEEP BUT REFACTOR | `tests/test_basis_controls.py` | Cmd yes with stubs; FSPM yes; PPFD yes; Spectral no. Port only basis skip/control policy if retained. | CLI basis helpers | self | medium |
| `tests/radiance/test_run_uniformity_argv.py` | KEEP BUT REFACTOR | `tests/test_uniformity_cli.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Port builder tests after separating CLI from optimizer. | CLI scripts, optimizer | self | medium |
| `tests/radiance/test_smd_power_schedule.py` | KEEP BUT REFACTOR | `tests/test_smd_power_schedule.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Port with minimal import path changes. | SMD power schedule | self | low |
| `tests/radiance/test_smd_optics_contract.py` | KEEP BUT REFACTOR | `tests/test_smd_optics_contract.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Port fixture optical contracts. | SMD optics modules | self | medium |
| `tests/radiance/test_smd_ppe_matching.py` | KEEP BUT REFACTOR | `tests/test_smd_ppe_matching.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Port photon/PPE matching contracts. | SMD photons | self | low |
| `tests/radiance/test_hps_fixture_profile.py` | KEEP BUT REFACTOR | `tests/test_hps_fixture_profile.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Port HPS profile tests. | HPS profile | self | low |
| `tests/radiance/test_deterministic_emitter_ids.py` | KEEP BUT REFACTOR | `tests/test_identifiers.py` | Cmd no; FSPM no; PPFD no; Spectral no. Port deterministic ID contract. | deterministic IDs | self | low |
| `tests/radiance/test_emitter_characterization_lock.py` | KEEP BUT REFACTOR | `tests/test_fixture_characterization.py` | Cmd possible; FSPM no; PPFD yes; Spectral yes. Port scientific characterization checks, not old file layout. | fixture modules, IES/SPD assets | self | medium |
| `tests/radiance/test_phase03_characterization.py` | KEEP BUT REFACTOR | `tests/test_characterization_contracts.py` | Cmd no; FSPM no; PPFD yes; Spectral yes. Port scientific characterization assertions. | photometry/fixture modules | self | medium |
| `tests/radiance/test_phase03_properties.py` | KEEP BUT REFACTOR | `tests/test_ppfd_properties.py` | Cmd no; FSPM no; PPFD yes; Spectral no. Port metrics/property checks. | PPFD metrics | self | low |
| `tests/radiance/test_phase03_benchmarks.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD yes; Spectral no. Benchmarks are useful for historical thresholds but should be redesigned for new repo. | benchmark fixtures | self | medium |
| `tests/radiance/test_phase10_scientific_contracts.py` | KEEP BUT REFACTOR | `tests/test_scientific_contracts.py` | Cmd mixed; FSPM mixed; PPFD yes; Spectral mixed. Port only core scientific invariants. | many modules | self | high |
| `tests/radiance/test_phase11_decomposition.py` | KEEP BUT REFACTOR | `tests/test_module_boundaries.py` | Cmd no; FSPM mixed; PPFD mixed; Spectral mixed. Rewrite import-boundary tests for new package. | import graph | self | medium |
| `tests/radiance/test_phase125c_scientific_contracts.py` | KEEP BUT REFACTOR | `tests/test_scientific_contracts.py` | Cmd mixed; FSPM yes; PPFD yes; Spectral mixed. Port scientific parts, drop app contracts. | many modules | self | high |
| `tests/radiance/test_phase125c2_geometry_contracts.py` | KEEP BUT REFACTOR | `tests/test_geometry_contracts.py` | Cmd no; FSPM yes; PPFD yes; Spectral no. Port geometry/layout contracts. | geometry/layout modules | self | medium |
| `tests/radiance/test_phase125d_scientific_services.py` | KEEP BUT REFACTOR | `tests/test_fixture_build_services.py` | Cmd mixed; FSPM no; PPFD yes; Spectral yes. Port service behavior after runner/path injection. | fixture services | self | high |
| `tests/radiance/test_phase125d1_scientific_completion.py` | KEEP BUT REFACTOR | `tests/test_scientific_completion.py` | Cmd mixed; FSPM yes; PPFD yes; Spectral yes. Port only stable scientific completion checks. | many modules | self | high |
| `tests/radiance/test_phase09_layout_consolidation.py` | KEEP BUT REFACTOR | `tests/test_layout_consolidation.py` | Cmd no; FSPM no; PPFD fixture layout; Spectral no. Port layout consolidation assertions that apply to new fixture layout APIs. | layout modules | self | medium |
| `tests/radiance/test_phase07_metrics_scaffold.py` | REFERENCE ONLY | N/A | Cmd no; FSPM mixed; PPFD yes; Spectral no. Historical metrics scaffold; replace with focused metric and artifact tests. | metric/artifact modules | self | medium |
| `tests/radiance/test_phase05_contracts.py` | REFERENCE ONLY | N/A | Cmd no; FSPM indirect; PPFD indirect; Spectral no. Current app/domain contract history; rewrite only selected request validation. | config/domain | self | medium |
| `tests/radiance/test_phase12_shell_cli.py` | REFERENCE ONLY | N/A | Cmd yes; FSPM mixed; PPFD mixed; Spectral mixed. Old shell wrapper behavior; new CLI should get fresh tests after API split. | shell scripts, CLI scripts | self | medium |
| `tests/radiance/test_plant_photosynthesis_response.py` | KEEP BUT REFACTOR | `tests/test_biology_photosynthesis.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Port if biology layer moves. | photosynthesis modules | self | medium |
| `tests/radiance/test_plant_photoreceptor_exposure.py` | KEEP BUT REFACTOR | `tests/test_biology_photoreceptor.py` | Cmd no; FSPM yes; PPFD indirect; Spectral yes. Port if biology layer moves. | photoreceptor modules | self | medium |
| `tests/radiance/test_plant_photomorphogenesis_response.py` | KEEP BUT REFACTOR | `tests/test_biology_photomorphogenesis.py` | Cmd no; FSPM yes; PPFD indirect; Spectral yes. Port if biology layer moves. | photomorphogenesis modules | self | medium |
| `tests/radiance/test_rex_growth_adapter.py` | KEEP BUT REFACTOR | `tests/test_growth_adapter.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Port after optical core, if growth remains in scope. | growth modules | self | medium |
| `tests/radiance/test_rex_growth_evolution.py` | KEEP BUT REFACTOR | `tests/test_growth_evolution.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Optional growth port. | growth modules | self | medium |
| `tests/radiance/test_rex_growth_geometry.py` | KEEP BUT REFACTOR | `tests/test_growth_geometry.py` | Cmd no; FSPM yes; PPFD no; Spectral no. Port geometry parts if growth remains in scope. | plant/growth modules | self | low |
| `tests/radiance/test_rex_growth_response.py` | KEEP BUT REFACTOR | `tests/test_growth_response.py` | Cmd no; FSPM yes; PPFD yes; Spectral yes. Optional growth-response port. | growth modules | self | medium |
| `tests/radiance/test_rex_growth_runtime_scaffold.py` | REFERENCE ONLY | N/A | Cmd no; FSPM yes; PPFD yes; Spectral yes. Runtime artifact scaffolding is app-coupled; rewrite only if new growth artifacts are needed. | runtime artifacts | self | medium |
| `tests/radiance/test_rex_growth_viewer_timeline.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Viewer timeline behavior. | growth viewer timeline | self | low |
| `tests/radiance/test_executable_resolution.py` | KEEP BUT REFACTOR | `tests/test_executables.py` | Cmd resolves only; FSPM no; PPFD no; Spectral no. Port with import path updates. | executable resolver | self | low |
| `tests/radiance/test_api_contracts.py` | DROP | N/A | Cmd no; FSPM API only; PPFD API only; Spectral API only. Backend/API route contract. | backend/domain | self | high |
| `tests/radiance/test_boundary_security.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Backend/file boundary security for current app. | backend/workspace | self | medium |
| `tests/radiance/test_config_contracts.py` | REFERENCE ONLY | N/A | Cmd no; FSPM indirect; PPFD indirect; Spectral no. Useful current-app config history, not directly portable. | config/settings | self | medium |
| `tests/radiance/test_route_query_contracts.py` | DROP | N/A | Cmd no; FSPM API only; PPFD API only; Spectral API only. Route query behavior. | backend routes/domain | self | high |
| `tests/radiance/test_run_route_plant_query.py` | DROP | N/A | Cmd no; FSPM API only; PPFD API only; Spectral API only. Web/backend route test. | backend routes | self | high |
| `tests/radiance/test_runtime_status.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. App runtime status endpoint/state. | backend runtime | self | medium |
| `tests/radiance/test_workspace_keys.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current backend workspace-key behavior. | backend workspace | self | medium |
| `tests/radiance/test_phase06_workspace_store.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Backend workspace store behavior. | backend workspace | self | medium |
| `tests/radiance/test_phase07_jobs.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Backend job lifecycle. | backend jobs | self | high |
| `tests/radiance/test_phase08_lifecycle.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. App lifecycle/runtime cleanup. | backend jobs/workspace | self | high |
| `tests/radiance/test_fspm_job_env_allowlist.py` | DROP | N/A | Cmd no; FSPM backend env; PPFD backend env; Spectral no. Backend job environment allowlist. | backend jobs/env | self | medium |
| `tests/radiance/test_private_live_manual_plumbing.py` | DROP | N/A | Cmd yes; FSPM no; PPFD yes; Spectral no. Current private live app plumbing, not clean core. | backend settings/docker | self | high |
| `tests/radiance/test_dev_launcher.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Development launcher behavior. | launcher | self | low |
| `tests/radiance/test_dev_dependency_audit.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current repository dependency audit, not solver science. | packaging/audit config | self | low |
| `tests/radiance/test_quality_gate_contracts.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current repository quality-gate behavior. | scripts/dev, audit config | self | low |
| `tests/radiance/test_docker_runner_contract.py` | DROP | N/A | Cmd yes; FSPM no; PPFD yes; Spectral no. Current Docker runner/backend integration. | backend docker runner | self | high |
| `tests/radiance/test_dev_radiance_docker_smoke.py` | DROP | N/A | Cmd yes; FSPM no; PPFD yes; Spectral no. Live Docker smoke, out of extraction scope. | Docker/Radiance | self | high |
| `tests/radiance/test_public_web_contract.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Public web behavior. | web app | self | high |
| `tests/radiance/test_dev_growth_ui_contract.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. UI contract. | browser/UI | self | high |
| `tests/radiance/test_assembly_viewer_payload.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Assembly viewer payload. | assembly scene/viewer | self | medium |
| `tests/radiance/test_assembly_viewer_browser.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Browser test. | browser/viewer | self | high |
| `tests/radiance/test_assembly_viewer_headless_smoke.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Browser smoke. | browser/viewer | self | high |
| `tests/radiance/test_assembly_viewer_fixture_controls.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Viewer fixture controls. | browser/viewer | self | high |
| `tests/radiance/test_assembly_viewer_heatmap.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Viewer heatmap layer. | browser/viewer | self | high |
| `tests/radiance/test_assembly_viewer_plants.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Viewer plant controls. | browser/viewer | self | high |
| `tests/radiance/test_assembly_viewer_transforms.py` | REFERENCE ONLY | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Viewer transform examples only. | assembly transforms | self | low |
| `tests/radiance/test_assembly_scene.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Assembly scene imports backend/web static behavior. | assembly scene | self | high |
| `tests/radiance/test_assembly_classification.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Viewer classification behavior may inform metadata only. | assembly classification | self | low |
| `tests/radiance/test_assembly_photometric_layer.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Viewer photometric layer. | assembly/viewer | self | medium |
| `tests/radiance/test_viewer_asset_validation.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral display. Viewer/static asset validation. | web static assets | self | medium |
| `tests/radiance/test_convert_step_to_glb.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. 3D asset conversion for viewer. | asset pipeline | self | medium |
| `tests/radiance/test_matched_ppe_artifact_routes.py` | DROP | N/A | Cmd no; FSPM API/artifact; PPFD API/artifact; Spectral yes. Backend route/artifact behavior for matched PPE. | backend routes/artifacts | self | high |
| `tests/radiance/test_precomputed_boundary.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Public precomputed boundary behavior. | precomputed dataset | self | high |
| `tests/radiance/test_precomputed_dataset.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Public precomputed dataset behavior. | precomputed dataset | self | high |
| `tests/radiance/test_precomputed_integrity.py` | REFERENCE ONLY | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Archive integrity ideas only. | precomputed integrity | self | medium |
| `tests/radiance/test_precomputed_playback.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Playback engine is not clean solver. | precomputed playback | self | high |
| `tests/radiance/test_precomputed_run_route.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Backend route playback. | backend/precomputed | self | high |
| `tests/radiance/test_precomputed_plant_generation.py` | REFERENCE ONLY | N/A | Cmd no; FSPM yes; PPFD playback; Spectral playback. Plant-generation ideas are useful, but public precomputed generation is out of scope. | precomputed generation, plants | self | medium |
| `tests/radiance/test_precomputed_plant_playback.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Public precomputed plant playback. | precomputed playback | self | high |
| `tests/radiance/test_precomputed_promotion.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Public precomputed promotion workflow. | precomputed assets | self | high |
| `tests/radiance/test_precomputed_growth_contract.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Precomputed growth playback contract. | precomputed/growth playback | self | high |
| `tests/radiance/test_download_precomputed_security.py` | DROP | N/A | Cmd no; FSPM playback; PPFD playback; Spectral playback. Public download tool security. | download script | self | medium |
| `tests/radiance/test_visualization_heatmaps.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Presentation plotting. | visualization | self | low |
| `tests/radiance/test_visualization_scatter.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Presentation plotting. | visualization | self | low |
| `tests/radiance/test_visualization_overlays.py` | DROP | N/A | Cmd no; FSPM display; PPFD display; Spectral no. Presentation plotting. | visualization | self | low |
| `tests/radiance/test_import_boundaries.py` | KEEP BUT REFACTOR | `tests/test_import_boundaries.py` | Cmd no; FSPM mixed; PPFD mixed; Spectral mixed. Rewrite for the new package import graph and no web/backend imports. | import graph | self | medium |
| `tests/radiance/runtime_env.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current test environment helper, not a production module. | pytest/env | tests | low |
| `tests/radiance/private_assets.py` | REFERENCE ONLY | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current private asset test helper. | paths | tests | low |
| `tests/radiance/__init__.py` | DROP | N/A | Cmd no; FSPM no; PPFD no; Spectral no. Current test package marker. | package imports | N/A | low |

## scripts.py Extraction Plan

Extract `src/rad_rebuild/radiance/cli/scripts.py` by first making its implicit runtime state explicit. The goal is not to migrate the file; it is to mine the scientific functions and leave the old CLI behind.

| scripts.py item | New home | Coupling to remove |
|---|---|---|
| `RuntimeConfig`, `_runtime_config`, `_bool_env`, `_float_env` | `src/fspm_optics/config/runtime.py` | Replace environment reads with typed config construction at CLI boundary. |
| `RadianceScriptExit`, `ScriptEvent` | `src/fspm_optics/runner/events.py` | Remove direct process-exit semantics from library code. |
| `UniformityConfig`, `_build_uniformity_config`, `build_uniformity_solver_argv`, `run_uniformity` | `src/fspm_optics/optimization/uniformity_cli.py` and `src/fspm_optics/optimization/uniformity.py` | Separate argv construction from optimizer input models; avoid old repo paths and stdout status events. |
| `_detect_smd_layout`, `_basis_fingerprint_requires_rebuild`, `_validate_basis_sampling_grid` | `src/fspm_optics/transport/basis/validation.py` | Replace filesystem probing with manifest objects and explicit sampling-grid inputs. |
| `_octree_scene_inputs` | `src/fspm_optics/transport/scenes.py` | Preserve the scientific rule that baseline PPFD octrees exclude plant geometry; inject room/emitter paths. |
| `_fspm_receiver_scene_inputs`, `_build_fspm_receiver_octree` | `src/fspm_optics/transport/receivers/octree.py` | Preserve the rule that FSPM receiver octrees rebuild room + emitters + plants to avoid stale octree bounds; inject command runner. |
| `_radiance_options`, `_radiance_option_value`, `_replace_radiance_option_value`, `_radiance_ambient_bounce_count`, `_fresh_ambient_cache` | `src/fspm_optics/radiance/options.py` | Remove env/global ambient cache assumptions; return explicit option lists. |
| `_read_points`, `_write_snake_and_dirs` | `src/fspm_optics/geometry/sensors.py` | Replace temp-file side effects with pure parse/format helpers plus a separate writer. |
| `BASELINE_PPFD_TRANSPORT_BASIS`, `BASELINE_PPFD_RGB_DECODE_METHOD`, `BASELINE_SOURCE_CHANNEL_POLICY`, `PPFD_CONVERSION_BASIS`, `BASELINE_PPFD_PHOTOPIC_LUMINANCE_WEIGHTING_AVOIDED`, scalar tolerances | `src/fspm_optics/transport/scalar_ppfd.py` | Keep as documented scientific constants with tests; avoid duplicating them in receiver artifact code. |
| `_grey_channel_ppfd_from_rgb`, `_aggregate_rgb_to_ppfd`, `_load_ppfd`, `_ppfd_mean`, `_ppfd_max`, `_scale_ppfd_map`, `_trace_ppfd` | `src/fspm_optics/transport/scalar_ppfd.py` | Split pure RGB-to-PPFD math from `rtrace` subprocess execution; inject command runner and decoder policy. |
| `_receiver_material_photon_distribution`, `_prepare_fspm_receiver_plant_material` | `src/fspm_optics/optics/leaf_materials.py` and `src/fspm_optics/transport/receivers/materials.py` | Remove env-controlled material mode; require explicit leaf optical profile/material mode. |
| `_plant_receiver_rtrace_argv`, `_plant_receiver_rtrace_args_metadata`, `_fspm_rtrace_profile_enabled`, `_fspm_rtrace_nproc`, `_fspm_rtrace_ambient_mode`, `_fspm_receiver_trace_options`, `_fspm_receiver_trace_metadata`, `_trace_plant_surface_receivers` | `src/fspm_optics/transport/receivers/rtrace.py` | Inject runner, nproc, ambient mode, and temp paths; no env reads or global workspace. |
| `_infer_fixture_spectral_mode`, `_write_optional_spectral_absorption_artifact`, `_write_band_receiver_plant_rad`, `_banded_octree_label`, `_prepare_banded_transport_plan`, `_write_banded_plant_surface_flux_artifact`, `_write_optional_plant_surface_flux_artifact` | `src/fspm_optics/optics/banded_transport.py`, `src/fspm_optics/artifacts/receivers.py` | Separate banded transport planning from artifact writing; eliminate optional app-output switches. |
| `_smd_plant_receiver_basis_enabled`, `_move_smd_plant_receiver_basis_column`, `_smd_basis_column_order_metadata`, `_write_smd_plant_receiver_basis_outputs` | `src/fspm_optics/transport/basis/smd_receiver_basis.py` | Replace env feature flags and file renames with explicit basis-output plans. |
| `_prepare_static_scene` | `src/fspm_optics/runner/scene_build.py` | Isolate room/grid/emitter preparation from mode-specific simulation commands. |
| `run_simulation_smd`, `_run_hps_pass`, `run_simulation_hps`, `_run_conventional_led_pass`, `run_simulation_conventional_led` | `src/fspm_optics/runner/pipeline.py` | Rebuild as composable pipeline functions with injected fixture builders, transport runners, and artifact sinks. |
| `generate_sensor_grid`, `run_basis_extraction` | `src/fspm_optics_cli/commands.py` | Thin CLI wrappers over library APIs. |
| `generate_precomputed_bundles`, `reproduce`, `main` | Do not migrate | Public-demo/precomputed and legacy CLI entrypoints are out of scope. |

## Test Port Plan

Port first:

1. Receiver transport and aggregation: `test_plant_surface_flux_artifact.py`, selected `test_phase125b_cli_orchestration.py`, `test_fspm_baseline_octree.py`.
2. Rex leaf optics and spectral absorption: `test_plant_leaf_materials.py`, `test_plant_optical_profiles.py`, `test_plant_spectral_optics.py`, `test_plant_spectral_absorption.py`.
3. Plant geometry: `test_fspm_plants.py`, geometry portions of `test_phase125c2_geometry_contracts.py`.
4. SMD scientific contracts: `test_smd_power_schedule.py`, `test_smd_optics_contract.py`, `test_smd_ppe_matching.py`.
5. Baseline PPFD and basis execution: `test_basis_rcontrib.py`, scalar PPFD tests extracted from `test_phase125b_cli_orchestration.py`, `test_run_uniformity_argv.py`.

Rewrite:

1. CLI orchestration tests should become runner/unit tests with injected command runners and workspaces.
2. Fixture service tests should use typed config and package resources rather than old env variables and repository paths.
3. Growth tests should move only if the new repo includes optional biology/growth modules.
4. Scientific contract tests from phase files should be split into small module-level tests with explicit fixtures.

Drop:

1. API, route, backend job, workspace, runtime status, Docker runner, and public web tests.
2. Assembly viewer, browser smoke, static viewer asset, and Three.js/GLB tests.
3. Precomputed playback, public dataset download, and run-route playback tests.
4. Visualization heatmap/scatter/overlay tests unless a separate reporting package is created.

## Do-Not-Migrate List

Do not migrate these into the new headless solver repo:

| Path or pattern | Reason |
|---|---|
| `src/rad_rebuild/radiance/backend/**` | FastAPI/backend routes, jobs, runtime state, workspace behavior. |
| `src/rad_rebuild/web/**` | Flask app, browser UI, static assets, viewer behavior. |
| `src/rad_rebuild/radiance/assembly/scene.py` | Imports backend models and web viewer/static paths. |
| `src/rad_rebuild/radiance/assembly/fspm_panel.py` | Viewer/report panel layer; reference only. |
| `src/rad_rebuild/radiance/assembly/fspm_csv.py` | CSV/report export layer; reference only. |
| `src/rad_rebuild/radiance/engine/plants/viewer_export.py` | Browser 3D viewer payloads. |
| `src/rad_rebuild/radiance/engine/plants/growth/viewer_timeline.py` | Viewer timeline payloads. |
| `src/rad_rebuild/radiance/engine/visualization/**` | Presentation plots and reporting images. |
| `src/rad_rebuild/radiance/engine/simulation/precomputed_playback.py` | Public-demo playback path, not live solver transport. |
| `src/rad_rebuild/radiance/engine/simulation/precomputed_dataset.py` | Public dataset lookup and manifest behavior. |
| `src/rad_rebuild/radiance/engine/simulation/precompute_sweep.py` | Bundle generation/public-demo orchestration. |
| `data/radiance/precomputed/**` | Protected generated public demo bundles. |
| `scripts/radiance/generate_precomputed_bundles.sh` | Public-demo bundle generation wrapper. |
| `scripts/radiance/download_precomputed.py` | Public dataset download tool. |
| `tests/radiance/test_assembly_viewer_*` | Browser/viewer behavior. |
| `tests/radiance/test_precomputed_*` | Public-demo playback behavior. |
| `tests/radiance/test_api_contracts.py`, `test_route_query_contracts.py`, `test_run_route_plant_query.py` | Backend/API behavior. |
| `tests/radiance/test_dev_launcher.py`, `test_dev_radiance_docker_smoke.py`, `test_docker_runner_contract.py` | Current app/development infrastructure. |
| `tests/radiance/test_public_web_contract.py`, `test_dev_growth_ui_contract.py`, `test_viewer_asset_validation.py`, `test_convert_step_to_glb.py` | Web UI/static asset behavior. |

## First Extraction Order

1. Create the new package skeleton and copy only pure dataclasses/constants first: `executables.py`, `fspm_targets.py`, plant `config.py`, `models.py`, `mesh.py`, `layout.py`, `generator.py`, and `radiance_export.py`.
2. Move leaf optical primitives: optical profiles as package resources, leaf materials, spectral band definitions, and Rex A/T/R absorption tests.
3. Split receiver transport: mesh-patch sample generation, rtrace input formatting, rtrace output parsing, and receiver aggregation from `surface_flux.py`.
4. Extract baseline PPFD scalar transport from `scripts.py`, including RGB decode constants, PPFD map scaling, and grey-channel validation.
5. Extract Radiance command construction behind an injected runner: rtrace receiver transport, baseline rtrace, oconv, rcontrib, and ies2rad.
6. Move fixture science in this order: SMD module/profile/power schedule, SMD ring/position logic, SMD Radiance writers, HPS profile/writer, conventional LED IES/SPD staging.
7. Move uniformity optimization after basis matrix transport is stable, then port selected scientific contract tests.
8. Add only a thin new CLI after the library API is stable; do not carry over public-demo precomputed or browser/server entrypoints.
