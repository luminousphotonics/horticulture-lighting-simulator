from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from fspm_optics.fixtures.smd.band_writer import (
    COMBINED_MODULE_EMITTING_WINDOW,
    NATIVE_LAMBERTIAN_ANGULAR_MODEL,
    build_smd_band_radiance_document,
)
from fspm_optics.fixtures.smd.module_profile import DEFAULT_SMD_MODULE_PROFILE
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_optimized_module_schedule
from fspm_optics.fixtures.smd.radiance_writer import build_smd_radiance_document
from fspm_optics.geometry.room import (
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
)
from fspm_optics.geometry.sensor_grid import SensorGridSpec
from fspm_optics.optics.rex_material_plan import (
    build_rex_radiance_trans_material_plan,
)
from fspm_optics.sources.smd.profile import build_nominal_smd_source_model
from fspm_optics.spectral.bands import FIXED_TRANSPORT_BANDS
from fspm_optics.transport.basis.artifacts import (
    save_basis_execution_summary,
    save_basis_matrix,
)
from fspm_optics.transport.basis.solve import solve_basis_workspace
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)
from fspm_optics.transport.five_band import (
    EXPECTED_FAR_RED_RESULT_UNITS,
    EXPECTED_PAR_BAND_RESULT_UNITS,
    FIVE_BAND_ORDER,
    FIVE_BAND_SCIENTIFIC_CLAIM,
    PHYSICAL_PATCH_AREA_POLICY,
    RECEIVER_HEMISPHERE_POLICY,
    RexFiveBandTransportPlan,
    build_five_band_source_plans,
    format_rex_five_band_transport_plan_json,
    plan_rex_five_band_transport,
    read_rex_five_band_transport_plan_json,
    validate_five_band_material_mapping,
    _layout_sha256,
)


EXPECTED_BUDGETS = {
    "blue": (33.994966308106633, 0.12572121522408553),
    "green": (91.012909086192252, 0.33658670015115505),
    "orange": (30.749357961150295, 0.11371820801935469),
    "red": (114.6423666445508, 0.42397387660540481),
    "far_red": (3.857526060250744, 0.014266019847110515),
}


def layout_and_schedule():
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(layout, (20, 25, 30, 35, 40))
    return layout, schedule


def solved_workspace(tmp_path: Path) -> Path:
    root = tmp_path / "basis"
    layout = generate_proposed_led_layout(10, 10)
    room = RoomDimensions(layout.room_length_m, layout.room_width_m, 3.048)
    grid = SensorGridSpec(room, 3, 1, 0.005)
    materialized = materialize_basis_workspace(
        plan_basis_workspace(
            layout=layout,
            output_directory=root,
            sensor_grid=grid,
        )
    )
    matrix_path = save_basis_matrix(
        root / "basis_matrix.npy",
        np.ones(materialized.manifest.matrix_shape),
        materialized.manifest,
    )
    save_basis_execution_summary(
        root / "basis_matrix.execution.json",
        {
            "schema_version": 1,
            "sensor_count": materialized.manifest.sensor_count,
            "control_zone_count": materialized.manifest.control_zone_count,
            "matrix_shape": list(materialized.manifest.matrix_shape),
            "per_column": [
                {"control_zone_index": index}
                for index in range(materialized.manifest.control_zone_count)
            ],
            "hashes": {
                "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest()
            },
        },
    )
    solve_basis_workspace(root, target_ppfd=10.0)
    return root


def test_five_band_layout_identity_authenticates_fixture_composition_mode() -> None:
    standalone = generate_proposed_led_layout(10, 10)
    linear = generate_proposed_led_layout(
        10,
        10,
        proposed_layout_mode="linear",
    )
    legacy = generate_proposed_led_layout(
        10,
        10,
        proposed_layout_mode="legacy",
    )
    assert linear.modules == legacy.modules
    assert _layout_sha256(linear) == _layout_sha256(
        generate_proposed_led_layout(
            10,
            10,
            proposed_layout_mode="linear",
        )
    )
    assert _layout_sha256(linear) != _layout_sha256(legacy)
    assert _layout_sha256(standalone) not in {
        _layout_sha256(linear),
        _layout_sha256(legacy),
    }


def test_fixed_band_order_relative_budgets_and_internal_yields() -> None:
    layout, schedule = layout_and_schedule()
    plans = build_five_band_source_plans(
        build_nominal_smd_source_model(),
        layout,
        schedule,
    )

    assert FIVE_BAND_ORDER == ("blue", "green", "orange", "red", "far_red")
    assert tuple(item.band_id for item in plans) == FIVE_BAND_ORDER
    for plan in plans:
        nominal, fraction = EXPECTED_BUDGETS[plan.band_id]
        assert plan.total_nominal_par_ppf_umol_s == pytest.approx(270.3996)
        assert plan.nominal_band_ppf_umol_s == pytest.approx(nominal, abs=1e-12)
        assert plan.fraction_relative_to_par == pytest.approx(fraction, abs=1e-15)
        assert plan.internal_band_photon_yield_umol_per_j == pytest.approx(
            INTERNAL_SOURCE_PPE_UMOL_PER_J * fraction,
            abs=1e-15,
        )
        assert plan.internal_source_ppe_umol_per_j == INTERNAL_SOURCE_PPE_UMOL_PER_J
        assert plan.accepted_fixture_transmission == ACCEPTED_FIXTURE_TRANSMISSION
        assert plan.completed_aperture_fixture_ppe_umol_per_j == 2.6
        assert plan.spatial_model_id == COMBINED_MODULE_EMITTING_WINDOW
        assert plan.angular_model_id == NATIVE_LAMBERTIAN_ANGULAR_MODEL


def test_par_closure_far_red_ratio_and_pretransport_normalization() -> None:
    layout, schedule = layout_and_schedule()
    plans = build_five_band_source_plans(
        build_nominal_smd_source_model(), layout, schedule
    )

    assert math.fsum(
        item.fraction_relative_to_par
        for item in plans
        if item.band_id != "far_red"
    ) == pytest.approx(1.0, abs=1e-15)
    assert math.fsum(
        item.internal_band_photon_yield_umol_per_j
        for item in plans
        if item.band_id != "far_red"
    ) == pytest.approx(INTERNAL_SOURCE_PPE_UMOL_PER_J, abs=1e-15)
    assert plans[-1].fraction_relative_to_par == pytest.approx(
        0.014266019847110515,
        abs=1e-15,
    )
    for plan in plans:
        assert plan.raw_relative_mix_aggregate_ppe_umol_per_j == pytest.approx(
            3.0464127985579088
        )
        assert plan.pretransport_relative_mix_normalization_factor == pytest.approx(
            INTERNAL_SOURCE_PPE_UMOL_PER_J / 3.0464127985579088
        )
        assert (
            plan.internal_source_ppe_umol_per_j
            * plan.accepted_fixture_transmission
        ) == pytest.approx(COMPLETED_APERTURE_PPE_UMOL_PER_J)


def test_raw_nominal_aggregate_ppe_and_invalid_band_or_area_are_rejected() -> None:
    layout, schedule = layout_and_schedule()
    source = build_nominal_smd_source_model()

    with pytest.raises(ValueError, match="absolute component-PPE authority"):
        build_five_band_source_plans(
            source,
            layout,
            schedule,
            internal_source_ppe_umol_per_j=3.0464127985579088,
        )
    with pytest.raises(ValueError, match="emitter_area_m2"):
        build_five_band_source_plans(
            source,
            layout,
            schedule,
            emitter_area_m2=0.0,
        )
    with pytest.raises(ValueError, match="spectral bands must be exactly"):
        build_five_band_source_plans(
            source,
            layout,
            schedule,
            bands=tuple(reversed(FIXED_TRANSPORT_BANDS)),
        )


def test_malformed_source_fraction_and_negative_schedule_fail() -> None:
    layout, schedule = layout_and_schedule()
    source = build_nominal_smd_source_model()
    malformed_fractions = dict(source.band_photon_fractions_relative_to_par)
    malformed_fractions["blue"] = 0.2

    with pytest.raises(ValueError, match="blue fraction is inconsistent"):
        build_five_band_source_plans(
            replace(source, band_photon_fractions_relative_to_par=malformed_fractions),
            layout,
            schedule,
        )

    bad_zone_watts = (-1.0, *schedule.watts_by_control_zone[1:])
    bad_module_watts = tuple(
        bad_zone_watts[module.control_zone_index] for module in layout.modules
    )
    with pytest.raises(ValueError, match="wattage"):
        build_five_band_source_plans(
            source,
            layout,
            replace(
                schedule,
                watts_by_control_zone=bad_zone_watts,
                watts_by_module=bad_module_watts,
            ),
        )


@pytest.mark.parametrize("invalid", (0.0, -0.1, float("nan"), float("inf")))
def test_nonpositive_or_nonfinite_channel_budgets_fail_closed(invalid: float) -> None:
    layout, schedule = layout_and_schedule()
    source = build_nominal_smd_source_model()
    malformed = dict(source.band_photon_fractions_relative_to_par)
    malformed["blue"] = invalid
    with pytest.raises(ValueError):
        build_five_band_source_plans(
            replace(source, band_photon_fractions_relative_to_par=malformed),
            layout,
            schedule,
        )


def test_per_zone_and_module_scaling_is_grayscale_and_explicit() -> None:
    layout, schedule = layout_and_schedule()
    plans = build_five_band_source_plans(
        build_nominal_smd_source_model(), layout, schedule
    )
    blue = plans[0]

    assert len(blue.zone_amplitudes) == layout.control_zone_count
    for zone in blue.zone_amplitudes:
        assert zone.module_wattage == schedule.watts_by_control_zone[
            zone.control_zone_index
        ]
        assert zone.module_band_ppf_umol_s == pytest.approx(
            zone.module_wattage * blue.internal_band_photon_yield_umol_per_j
        )
        assert zone.grayscale_radiance_rgb[0] == zone.grayscale_radiance_rgb[1]
        assert zone.grayscale_radiance_rgb[1] == zone.grayscale_radiance_rgb[2]
        assert zone.module_count == sum(
            module.control_zone_index == zone.control_zone_index
            for module in layout.modules
        )


def test_band_writer_is_explicit_and_preserves_scalar_writer_behavior() -> None:
    layout, schedule = layout_and_schedule()
    scalar_before = build_smd_radiance_document(layout, schedule)
    band = build_smd_band_radiance_document(
        layout,
        schedule,
        band_id="blue",
        internal_band_photon_yield_umol_per_j=(
            INTERNAL_SOURCE_PPE_UMOL_PER_J * EXPECTED_BUDGETS["blue"][1]
        ),
    )
    scalar_after = build_smd_radiance_document(layout, schedule)

    assert scalar_before == scalar_after
    assert "scalar PAR carrier" in scalar_after.radiance_text
    assert "isolated band carrier: band=blue" in band.radiance_text
    assert "scalar PAR" not in band.radiance_text
    assert "# ppe_umol_per_j=" not in band.radiance_text
    assert band.metadata.spatial_model_id == COMBINED_MODULE_EMITTING_WINDOW
    assert band.metadata.emitter_area_per_module_m2 == pytest.approx(
        DEFAULT_SMD_MODULE_PROFILE.window_side_m**2
    )
    assert band.metadata.emitter_primitive_count == len(layout.modules)
    assert band.metadata.accepted_fixture_transmission == ACCEPTED_FIXTURE_TRANSMISSION
    assert band.metadata.modeled_completed_aperture_band_yield_umol_per_j == pytest.approx(
        band.metadata.internal_band_photon_yield_umol_per_j
        * ACCEPTED_FIXTURE_TRANSMISSION
    )


def test_phase17_material_mapping_is_exact_and_rejects_order_errors() -> None:
    material_plan = build_rex_radiance_trans_material_plan()
    entries = validate_five_band_material_mapping(material_plan)

    assert tuple(item.interval_id for item in entries) == FIVE_BAND_ORDER
    assert tuple(item.material_identifier for item in entries) == tuple(
        f"rex_leaf_trans_{band_id}" for band_id in FIVE_BAND_ORDER
    )
    with pytest.raises(ValueError, match="exactly once and in fixed order"):
        validate_five_band_material_mapping(
            material_plan,
            (entries[1], entries[0], *entries[2:]),
        )
    with pytest.raises(ValueError, match="exactly once and in fixed order"):
        validate_five_band_material_mapping(material_plan, entries[:-1])
    with pytest.raises(ValueError, match="exactly once and in fixed order"):
        validate_five_band_material_mapping(
            material_plan,
            (entries[0], entries[0], *entries[2:]),
        )


def test_workspace_plan_materializes_deterministic_inputs_not_outputs(
    tmp_path: Path,
) -> None:
    root = solved_workspace(tmp_path)
    plan = plan_rex_five_band_transport(root)
    first_json = format_rex_five_band_transport_plan_json(plan)
    repeated = plan_rex_five_band_transport(root)

    assert repeated == plan
    assert format_rex_five_band_transport_plan_json(repeated) == first_json
    assert plan.output_root == root / "rex_five_band"
    assert plan.scientific_claim == FIVE_BAND_SCIENTIFIC_CLAIM
    assert plan.leaf_count == 32
    assert plan.leaf_patch_grid == (4, 4)
    assert plan.patch_count == 512
    assert plan.receiver_count == 1024
    assert plan.receiver_hemisphere_policy == RECEIVER_HEMISPHERE_POLICY
    assert plan.physical_patch_area_policy == PHYSICAL_PATCH_AREA_POLICY
    assert plan.receiver_path.is_file()
    assert len(plan.receiver_path.read_text(encoding="utf-8").splitlines()) == 1024
    assert plan.source_model_path.is_file()
    assert plan.material_plan_path.is_file()
    assert plan.manifest_path.is_file()

    for band in plan.band_plans:
        assert band.paths.emitter_path.is_file()
        assert band.paths.plant_path.is_file()
        assert not band.paths.octree_path.exists()
        assert not band.paths.rgb_output_path.exists()
        assert not band.paths.decoded_pfd_path.exists()
        assert not band.paths.ambient_cache_path.exists()
        assert band.material.material_identifier == f"rex_leaf_trans_{band.band_id}"
        assert band.paths.plant_path.read_text(encoding="utf-8").count(
            " polygon "
        ) == 5248


def test_workspace_commands_are_safe_deterministic_and_band_isolated(
    tmp_path: Path,
) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path))
    octrees = {item.paths.octree_path for item in plan.band_plans}
    caches = {item.paths.ambient_cache_path for item in plan.band_plans}

    assert len(octrees) == len(caches) == 5
    assert all(
        PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16] in path.name
        for path in caches
    )
    for band in plan.band_plans:
        assert band.oconv_command.argv == (
            "oconv",
            "-f",
                str(plan.room_path),
                str(band.paths.emitter_path),
                str(plan.fixture_body_path),
                str(band.paths.plant_path),
            )
        assert band.oconv_command.stdout_path == band.paths.octree_path
        assert band.rtrace_command.argv[0] == "rtrace"
        assert "-h" in band.rtrace_command.argv
        assert "-I+" in band.rtrace_command.argv
        assert ("-n", "6") == tuple(
            band.rtrace_command.argv[
                band.rtrace_command.argv.index("-n") :
                band.rtrace_command.argv.index("-n") + 2
            ]
        )
        assert str(band.paths.ambient_cache_path) in band.rtrace_command.argv
        assert band.rtrace_command.stdin_path == plan.receiver_path
        assert band.rtrace_command.stdout_path == band.paths.rgb_output_path
        assert band.expected_result_units == (
            EXPECTED_FAR_RED_RESULT_UNITS
            if band.band_id == "far_red"
            else EXPECTED_PAR_BAND_RESULT_UNITS
        )
    payload = plan.to_payload()
    for band in payload["bands"]:
        assert band["commands"]["oconv"]["shell"] is False
        assert band["commands"]["rtrace"]["shell"] is False


def test_typed_json_round_trip_and_receiver_front_back_order(tmp_path: Path) -> None:
    plan = plan_rex_five_band_transport(solved_workspace(tmp_path))
    from_payload = RexFiveBandTransportPlan.from_payload(plan.to_payload())
    from_file = read_rex_five_band_transport_plan_json(plan.manifest_path)

    assert from_payload == plan
    assert from_file == plan
    assert json.loads(plan.manifest_path.read_text(encoding="utf-8")) == (
        plan.to_payload()
    )
    stale = json.loads(json.dumps(plan.to_payload()))
    stale["schema_version"] = 2
    with pytest.raises(ValueError, match="schema_version"):
        RexFiveBandTransportPlan.from_payload(stale)
    stale = json.loads(json.dumps(plan.to_payload()))
    stale["shared_artifacts"]["hashes"][
        "room_model_identity_sha256"
    ] = "0" * 64
    with pytest.raises(ValueError, match="room model"):
        RexFiveBandTransportPlan.from_payload(stale)
    for index in range(0, plan.receiver_count, 2):
        front = plan.ordered_receiver_ids[index]
        back = plan.ordered_receiver_ids[index + 1]
        assert front.endswith("_front")
        assert back.endswith("_back")
        assert front[:-6] == back[:-5]


def test_plan_rejects_incompatible_existing_artifact(tmp_path: Path) -> None:
    root = solved_workspace(tmp_path)
    output = root / "rex_five_band"
    output.mkdir()
    (output / "source_model.json").write_text("incompatible\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="existing five-band artifact is incompatible"):
        plan_rex_five_band_transport(root)
