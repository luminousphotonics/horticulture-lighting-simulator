from __future__ import annotations

import hashlib
from pathlib import Path

from fspm_optics.fixtures.conventional_led.profile import (
    CONVENTIONAL_COMPARISON_PROFILE_ID,
)
from fspm_optics.fixtures.hps import (
    HPS_ANGULAR_DISTRIBUTION_ID,
    HPS_COMPARISON_PROFILE_ID,
    HPS_RADIANCE_CARRIER_MULTIPLIER,
    HpsFixturePlacement,
    build_hps_radiance_source_plan,
    plan_hps_layout_from_feet,
)
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.sources.smd.profile import SMD_SOURCE_MODEL_ID
from fspm_optics.transport.hps import (
    HPS_REX_RUN_ORDER,
    format_hps_isolated_run_source_rad,
    format_hps_isolated_transport_bundle_json,
    plan_hps_isolated_transport_bundle,
)


def test_bundle_consumes_layout_and_has_one_phase25a_aperture_per_fixture(
    tmp_path: Path,
) -> None:
    layout = plan_hps_layout_from_feet(10.0, 10.0)
    bundle = plan_hps_isolated_transport_bundle(tmp_path / "future", layout=layout)

    assert bundle.layout is layout
    assert len(bundle.phase25a_source_plan.apertures) == len(layout.fixtures) == 4
    assert tuple(item.fixture_id for item in bundle.phase25a_source_plan.apertures) == tuple(
        item.fixture_id for item in layout.fixtures
    )
    assert tuple(item.interval_id for item in bundle.runs) == HPS_REX_RUN_ORDER
    assert len(bundle.band_runs) == 5
    assert bundle.scalar_par.interval_id == "scalar_par"
    assert bundle.fixture_occlusion.system_id == "hps"
    assert len(bundle.fixture_occlusion.instances) == 4
    assert bundle.fixture_occlusion.emitting_boundaries == ()


def test_explicit_36_inch_stage_b_bundle_shares_authenticated_placement_identity(
    tmp_path: Path,
) -> None:
    layout = plan_hps_layout_from_feet(
        8.0,
        12.0,
        reference_plane_z_m=0.005,
        mount_height_m=0.9144,
    )
    bundle = plan_hps_isolated_transport_bundle(
        tmp_path / "raised-stage-b",
        layout=layout,
    )
    placement_identity = bundle.fixture_occlusion.asset_provenance[0][
        "placement_contract_sha256"
    ]

    assert placement_identity == (
        "d83577f6bb7503ff8865dbe55122bfd37d5ea0e0f2c1d9dc130a7a98f9598293"
    )
    assert bundle.scientific_payload()["fixture_occlusion"][
        "identity_sha256"
    ] == bundle.fixture_occlusion.identity_sha256
    for run in bundle.runs:
        assert run.baseline_scene.scene.source_files[-1] == (
            bundle.fixture_occlusion.instance_source_path
        )
        assert run.fspm_scene.scene.source_files[-2] == (
            bundle.fixture_occlusion.instance_source_path
        )


def test_bundle_shares_dat_and_isolates_source_material_scene_octree_and_cache(
    tmp_path: Path,
) -> None:
    bundle = plan_hps_isolated_transport_bundle(tmp_path / "future")

    assert len({item.source_input.shared_angular_data_identity for item in bundle.runs}) == 1
    assert len({item.source_definition_id for item in bundle.runs}) == 6
    assert len({item.material.material_identifier for item in bundle.runs}) == 6
    assert len({item.baseline_scene.scene_id for item in bundle.runs}) == 6
    assert len({item.fspm_scene.scene_id for item in bundle.runs}) == 6
    assert len({item.baseline_scene.octree_identity for item in bundle.runs}) == 6
    assert len({item.fspm_scene.octree_identity for item in bundle.runs}) == 6
    assert len({item.ambient_cache_identity for item in bundle.runs}) == 6
    for run in bundle.runs:
        source_text = format_hps_isolated_run_source_rad(
            bundle, run.interval_id
        )
        assert hashlib.sha256(source_text.encode("utf-8")).hexdigest() == (
            run.source_text_sha256
        )
        assert run.source_input.absolute_photon_flux_applied_before_trace is True
        assert run.source_input.spectral_fraction_applied_after_trace is False
        assert run.scientific_payload()["post_trace_179_conversion"] is False
        assert run.scientific_payload()["spatial_symmetrization"] is False


def test_baseline_and_fspm_scenes_are_strictly_separated() -> None:
    bundle = plan_hps_isolated_transport_bundle("/tmp/hps_plan_only")

    for run in bundle.runs:
        assert run.baseline_scene.component_roles == (
            "room", "hps_emitters", "fixture_bodies"
        )
        assert run.fspm_scene.component_roles == (
            "room", "hps_emitters", "fixture_bodies", "rex_plant"
        )
        assert len(run.baseline_scene.scene.source_files) == 3
        assert len(run.fspm_scene.scene.source_files) == 4
        assert run.baseline_scene.scene.source_files[-1] == (
            bundle.fixture_occlusion.instance_source_path
        )
        assert run.fspm_scene.scene.source_files[-2] == (
            bundle.fixture_occlusion.instance_source_path
        )
        assert isinstance(run.baseline_scene.oconv_command, CommandSpec)
        assert isinstance(run.fspm_scene.oconv_command, CommandSpec)
        assert isinstance(run.rtrace_command, CommandSpec)
        payload = run.scientific_payload()
        assert payload["baseline_scene"]["scene_id"] == (
            run.baseline_scene.scene_id
        )
        assert payload["baseline_scene"]["octree_identity"] == (
            run.baseline_scene.octree_identity
        )
        assert payload["fspm_scene"]["scene_id"] == run.fspm_scene.scene_id
        assert payload["fspm_scene"]["octree_identity"] == (
            run.fspm_scene.octree_identity
        )
        assert payload["ambient_cache_identity"] == run.ambient_cache_identity
    assert bundle.scientific_payload()["fixture_occlusion"][
        "identity_sha256"
    ] == bundle.fixture_occlusion.identity_sha256


def test_mature_rex_geometry_receiver_and_physical_area_contracts_are_stable(
    tmp_path: Path,
) -> None:
    bundle = plan_hps_isolated_transport_bundle(tmp_path / "future")
    payload = bundle.scientific_payload()

    assert (bundle.leaf_count, bundle.plant_polygon_count, bundle.patch_count) == (
        32, 5248, 512
    )
    assert bundle.leaf_patch_grid == (4, 4)
    assert bundle.receiver_count == 1024
    assert len(bundle.ordered_receiver_ids) == 1024
    assert len(set(bundle.ordered_receiver_ids)) == 1024
    assert payload["receivers"]["physical_patch_area_policy"] == (
        "one_physical_patch_area_multiplies_front_plus_back_incident_flux_once"
    )


def test_bundle_serialization_is_path_free_and_deterministic(tmp_path: Path) -> None:
    first = plan_hps_isolated_transport_bundle(tmp_path / "one")
    repeat = plan_hps_isolated_transport_bundle(tmp_path / "one")
    elsewhere = plan_hps_isolated_transport_bundle(tmp_path / "two")

    assert first.bundle_id == repeat.bundle_id == elsewhere.bundle_id
    assert first.scientific_payload() == repeat.scientific_payload()
    assert first.scientific_payload() == elsewhere.scientific_payload()
    assert format_hps_isolated_transport_bundle_json(first) == (
        format_hps_isolated_transport_bundle_json(elsewhere)
    )
    serialized = format_hps_isolated_transport_bundle_json(first)
    assert str(tmp_path) not in serialized
    assert "$WORKSPACE" in serialized
    assert first.workspace.exists() is False
    assert first.output_root.exists() is False


def test_explicit_phase25a_source_plan_identity_is_validated(tmp_path: Path) -> None:
    layout = plan_hps_layout_from_feet(10.0, 10.0)
    placements = tuple(
        HpsFixturePlacement(
            item.fixture_id,
            item.aligned_x_m,
            item.aligned_y_m,
            item.aperture_z_m,
        )
        for item in layout.fixtures
    )
    source = build_hps_radiance_source_plan(
        workspace=tmp_path / "source",
        placements=placements,
    )
    bundle = plan_hps_isolated_transport_bundle(
        tmp_path / "bundle",
        layout=layout,
        phase25a_source_plan=source,
    )

    assert bundle.phase25a_source_plan.source_plan_id == source.source_plan_id
    assert bundle.phase25a_source_plan.angular_distribution_id == (
        HPS_ANGULAR_DISTRIBUTION_ID
    )
    assert bundle.phase25a_source_plan.carrier_scale.ies2rad_multiplier == (
        HPS_RADIANCE_CARRIER_MULTIPLIER
    )
    assert bundle.layout.profile_id == HPS_COMPARISON_PROFILE_ID
    assert bundle.layout.profile_id != CONVENTIONAL_COMPARISON_PROFILE_ID
    assert bundle.source_payload.spectral_source_id != SMD_SOURCE_MODEL_ID
