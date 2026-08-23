from __future__ import annotations

from pathlib import Path

import pytest


_TIERS = frozenset({"fast", "subsystem", "scientific"})

# This is intentionally exhaustive and explicit. New test modules do not inherit a
# tier from their name or location: their contract owner must classify them here.
TEST_MODULE_TIERS = {
    "tests/test_absorption.py": "fast",
    "tests/test_application_modes.py": "subsystem",
    "tests/test_aisle_mode.py": "fast",
    "tests/test_angular_experiment.py": "fast",
    "tests/test_basis_artifacts.py": "fast",
    "tests/test_basis_execution.py": "subsystem",
    "tests/test_basis_manifest.py": "fast",
    "tests/test_basis_parsing.py": "fast",
    "tests/test_basis_planning.py": "fast",
    "tests/test_basis_solve.py": "subsystem",
    "tests/test_basis_workspace.py": "subsystem",
    "tests/test_cli_smoke.py": "subsystem",
    "tests/test_composite_validation.py": "subsystem",
    "tests/test_compact_precomputed_bundle.py": "subsystem",
    "tests/test_committed_playback_contract.py": "subsystem",
    "tests/test_conventional_angular.py": "fast",
    "tests/test_conventional_ies_staging.py": "fast",
    "tests/test_conventional_layout.py": "fast",
    "tests/test_conventional_lm63.py": "fast",
    "tests/test_conventional_profile.py": "fast",
    "tests/test_conventional_radiance_source.py": "fast",
    "tests/test_conventional_resources.py": "fast",
    "tests/test_conventional_rex_absorption.py": "scientific",
    "tests/test_conventional_rex_execution.py": "subsystem",
    "tests/test_conventional_rex_planning.py": "fast",
    "tests/test_conventional_scalar_transport.py": "subsystem",
    "tests/test_conventional_scene_planning.py": "fast",
    "tests/test_conventional_spectral.py": "fast",
    "tests/test_five_band_absorption.py": "scientific",
    "tests/test_five_band_execution.py": "subsystem",
    "tests/test_five_band_transport_plan.py": "subsystem",
    "tests/test_fixture_occlusion.py": "fast",
    "tests/test_fixture_body_optics_audit.py": "fast",
    "tests/test_fixture_viewer_catalog.py": "subsystem",
    "tests/test_fixed_precomputed_plan.py": "fast",
    "tests/test_fixed_precomputed_sweep.py": "subsystem",
    "tests/test_forward_flux_documentation.py": "fast",
    "tests/test_hps_fixture_asset.py": "fast",
    "tests/test_hps_angular_source.py": "fast",
    "tests/test_hps_layout.py": "fast",
    "tests/test_hps_lm63.py": "fast",
    "tests/test_hps_profile.py": "fast",
    "tests/test_hps_resources.py": "fast",
    "tests/test_hps_rex_absorption.py": "scientific",
    "tests/test_hps_rex_execution.py": "subsystem",
    "tests/test_hps_scalar_transport.py": "subsystem",
    "tests/test_hps_spectral_rex.py": "fast",
    "tests/test_hps_transport_planning.py": "fast",
    "tests/test_import_boundaries.py": "fast",
    "tests/test_juvenile_multi_plant_scene.py": "scientific",
    "tests/test_juvenile_radiance_scene_export.py": "scientific",
    "tests/test_juvenile_viewer_artifacts.py": "subsystem",
    "tests/test_leaf_materials.py": "fast",
    "tests/test_local_runner.py": "subsystem",
    "tests/test_natural_fit_plant_layout.py": "fast",
    "tests/test_optical_profiles.py": "fast",
    "tests/test_phase27b_request.py": "fast",
    "tests/test_phase27b_spatial_uniformity.py": "fast",
    "tests/test_phase27b_target_control.py": "fast",
    "tests/test_phase27b_ui_contract.py": "fast",
    "tests/test_phase27b_web.py": "subsystem",
    "tests/test_playback_queue.py": "fast",
    "tests/test_precomputed_web.py": "subsystem",
    "tests/test_precomputed_fspm_tolerance.py": "subsystem",
    "tests/test_public_anonymity_integrity_gate.py": "subsystem",
    "tests/test_phase27c_cli.py": "subsystem",
    "tests/test_phase27d_visualization.py": "subsystem",
    "tests/test_phase27e_systems.py": "subsystem",
    "tests/test_phase27f_c_fixture_rendering.py": "subsystem",
    "tests/test_phase27g_b_analysis_scope.py": "fast",
    "tests/test_phase27g_b_multispectral.py": "scientific",
    "tests/test_phase27g_c_scientific_aggregation.py": "scientific",
    "tests/test_phase27g_d1_surface_flux_calibration.py": "scientific",
    "tests/test_phase27g_d2_surface_flux_display.py": "scientific",
    "tests/test_phase27g_d3_receiver_placement_diagnostic.py": "scientific",
    "tests/test_phase27g_d3b_receiver_sampling_profile.py": "scientific",
    "tests/test_phase27g_d5a1_surface_flux_recalibration.py": "scientific",
    "tests/test_phase27g_d5a2_coefficient_analysis.py": "scientific",
    "tests/test_phase27g_d5b1_optimized_calibration.py": "scientific",
    "tests/test_phase27g_d5c1_endpoint_replication.py": "scientific",
    "tests/test_phase27g_d5c2_repeatability_analysis.py": "scientific",
    "tests/test_phase27g_d5c3_display_calibration.py": "scientific",
    "tests/test_phase27h_b_mounting_height.py": "fast",
    "tests/test_phase27h_e_baseline_leaf_uniformity.py": "scientific",
    "tests/test_phase3b_public_hps_contract.py": "subsystem",
    "tests/test_phase4b_public_conventional_contract.py": "subsystem",
    "tests/test_plant_obj_export.py": "scientific",
    "tests/test_plant_radiance_export.py": "scientific",
    "tests/test_plants_geometry.py": "fast",
    "tests/test_proposed_spectral_control.py": "fast",
    "tests/test_proposed_control_mode.py": "subsystem",
    "tests/test_proposed_cob_mode.py": "subsystem",
    "tests/test_proposed_cob_audit.py": "fast",
    "tests/test_proposed_led_module_asset.py": "fast",
    "tests/test_proposed_ring_mode.py": "subsystem",
    "tests/test_proposed_source_response.py": "fast",
    "tests/test_public_release_ui_cleanup.py": "fast",
    "tests/test_production_room_authority.py": "subsystem",
    "tests/test_aperture_ppe_calibration.py": "fast",
    "tests/test_radiance_commands.py": "fast",
    "tests/test_radiance_materials.py": "fast",
    "tests/test_radiance_options.py": "fast",
    "tests/test_radiance_versioning.py": "fast",
    "tests/test_receivers.py": "fast",
    "tests/test_rect_layout.py": "fast",
    "tests/test_rectangular_room_coordinate_frame.py": "scientific",
    "tests/test_rex_juvenile_preheading_geometry.py": "scientific",
    "tests/test_rex_material_plan.py": "fast",
    "tests/test_rex_plant_geometry.py": "scientific",
    "tests/test_rex_receiver_smoke.py": "subsystem",
    "tests/test_rex_source_weighting.py": "fast",
    "tests/test_room_geometry.py": "fast",
    "tests/test_scalar_ppfd.py": "fast",
    "tests/test_scene_planning.py": "subsystem",
    "tests/test_sensor_grid.py": "fast",
    "tests/test_smd_layout.py": "fast",
    "tests/test_smd_optical_stack.py": "fast",
    "tests/test_smd_photons.py": "fast",
    "tests/test_smd_power_schedule.py": "fast",
    "tests/test_smd_radiance_writer.py": "fast",
    "tests/test_smd_source_model.py": "fast",
    "tests/test_smd_spd.py": "fast",
    "tests/test_spectral_absorption.py": "fast",
    "tests/test_spectral_bands.py": "fast",
    "tests/test_spectral_optics.py": "fast",
    "tests/test_stage_a_audit_invariants.py": "fast",
    "tests/test_stage_a_validator.py": "fast",
    "tests/test_standalone_alignment_lattice.py": "fast",
    "tests/test_targets_and_executables.py": "fast",
    "tests/test_uniformity_solver.py": "fast",
}

# Fully qualified pytest node IDs belong here if a module genuinely spans tiers.
TEST_NODE_TIER_EXCEPTIONS: dict[str, str] = {}


def _relative_test_path(root: Path, item: pytest.Item) -> str:
    try:
        return item.path.relative_to(root).as_posix()
    except ValueError as error:
        raise pytest.UsageError(
            f"collected test is outside the repository root: {item.nodeid}"
        ) from error


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    root = Path(config.rootpath)
    registered_modules = set(TEST_MODULE_TIERS)
    discovered_modules = {
        path.relative_to(root).as_posix()
        for path in (root / "tests").rglob("test_*.py")
        if path.is_file()
    }
    unregistered_files = sorted(discovered_modules - registered_modules)
    stale_registry_entries = sorted(registered_modules - discovered_modules)
    if unregistered_files or stale_registry_entries:
        details = []
        if unregistered_files:
            details.append(
                "unregistered test module(s):\n  - "
                + "\n  - ".join(unregistered_files)
            )
        if stale_registry_entries:
            details.append(
                "registry entries without a test module:\n  - "
                + "\n  - ".join(stale_registry_entries)
            )
        raise pytest.UsageError(
            "every test module must have one explicit tier in tests/conftest.py; "
            + "\n".join(details)
        )

    collected_modules = {_relative_test_path(root, item) for item in items}
    unregistered = sorted(collected_modules - registered_modules)
    if unregistered:
        rendered = "\n  - ".join(unregistered)
        raise pytest.UsageError(
            "every test module must have one explicit tier in tests/conftest.py; "
            f"unregistered module(s):\n  - {rendered}"
        )

    invalid_registry_tiers = {
        path: tier for path, tier in TEST_MODULE_TIERS.items() if tier not in _TIERS
    }
    invalid_exception_tiers = {
        nodeid: tier
        for nodeid, tier in TEST_NODE_TIER_EXCEPTIONS.items()
        if tier not in _TIERS
    }
    if invalid_registry_tiers or invalid_exception_tiers:
        raise pytest.UsageError(
            "test tier registry contains a tier other than fast, subsystem, or "
            f"scientific: {invalid_registry_tiers | invalid_exception_tiers}"
        )

    for item in items:
        module_path = _relative_test_path(root, item)
        tier = TEST_NODE_TIER_EXCEPTIONS.get(
            item.nodeid,
            TEST_MODULE_TIERS[module_path],
        )
        existing_tiers = {
            marker.name for marker in item.iter_markers() if marker.name in _TIERS
        }
        if existing_tiers and existing_tiers != {tier}:
            raise pytest.UsageError(
                f"{item.nodeid} declares conflicting tiers {sorted(existing_tiers)}; "
                f"the registry resolves it to {tier!r}"
            )
        if not existing_tiers:
            item.add_marker(getattr(pytest.mark, tier))

        resolved_tiers = {
            marker.name for marker in item.iter_markers() if marker.name in _TIERS
        }
        if resolved_tiers != {tier}:
            raise pytest.UsageError(
                f"{item.nodeid} must resolve to exactly one tier; got "
                f"{sorted(resolved_tiers)}"
            )
