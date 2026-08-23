from __future__ import annotations

from decimal import Decimal

import pytest

from fspm_optics.application.domain import (
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.application.publication import (
    PublicationError,
    _validate_conventional_fixture_power_contract,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.active_domain import (
    AISLE_WIDTH_M,
    ActiveRoomDomain,
)
from fspm_optics.geometry.room import DEFAULT_ROOM_HEIGHT_M, RoomDimensions
from fspm_optics.layout.overlay import bind_overlay_to_active_domain
from fspm_optics.plants.multi_scene import build_juvenile_natural_fit_scene
from fspm_optics.plants.natural_fit import plan_natural_fit_layout_from_feet
from fspm_optics.transport.basis.workspace import plan_basis_workspace
from fspm_optics.transport.conventional_scalar import (
    ConventionalScalarTransportRequest,
    plan_conventional_scalar_transport,
)
from fspm_optics.transport.hps_scalar import (
    HpsScalarTransportRequest,
    plan_hps_scalar_transport,
)


def _payload(system: str, *, aisle_mode: bool | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "system": system,
        "room_length_ft": 10.0,
        "room_width_ft": 20.0,
        "quality": "standard",
    }
    if system != "hps":
        payload["target_ppfd"] = 900.0
    if aisle_mode is not None:
        payload["aisle_mode"] = aisle_mode
    return payload


def _meters(feet: float) -> float:
    return float(Decimal(str(feet)) * Decimal("0.3048"))


@pytest.mark.parametrize("system", ["proposed", "conventional", "hps"])
def test_request_defaults_and_aisle_serialization_round_trip(system: str) -> None:
    legacy = parse_run_request(_payload(system))
    assert legacy.aisle_mode is False
    assert legacy.active_domain.enabled is False
    assert legacy.active_domain.active_requested_length_ft == 10.0

    enabled = parse_run_request(_payload(system, aisle_mode=True))
    serialized = enabled.to_dict()
    assert serialized["aisle_mode"] is True
    assert serialized["active_domain"] == enabled.active_domain.to_payload()
    reparsed = parse_run_request(
        _payload(system, aisle_mode=bool(serialized["aisle_mode"]))
    )
    assert reparsed.active_domain == enabled.active_domain


@pytest.mark.parametrize(
    ("outer_length_ft", "outer_width_ft", "active_length_ft", "active_width_ft"),
    [
        (10.0, 10.0, 6.0, 6.0),
        (10.0, 20.0, 6.0, 16.0),
        (30.0, 50.0, 26.0, 46.0),
    ],
)
def test_exact_centered_active_domain_examples(
    outer_length_ft: float,
    outer_width_ft: float,
    active_length_ft: float,
    active_width_ft: float,
) -> None:
    domain = ActiveRoomDomain.from_feet(
        outer_length_ft,
        outer_width_ft,
        enabled=True,
    )
    assert domain.aisle_width_m == 0.6096
    assert domain.active_requested_length_ft == active_length_ft
    assert domain.active_requested_width_ft == active_width_ft
    assert domain.active_requested_length_m == _meters(active_length_ft)
    assert domain.active_requested_width_m == _meters(active_width_ft)
    min_x, max_x, min_y, max_y = domain.active_bounds_aligned_m
    assert min_x == -max_x
    assert min_y == -max_y


def test_portrait_domain_preserves_outer_coordinate_frame_rotation() -> None:
    domain = ActiveRoomDomain.from_feet(10.0, 20.0, enabled=True)
    assert domain.coordinate_frame.axes_swapped is True
    assert domain.coordinate_frame.rotation_degrees_about_z == -90
    assert domain.outer_aligned_length_m == _meters(20.0)
    assert domain.active_aligned_length_m == _meters(16.0)


def test_proposed_stage_a_keeps_outer_room_and_samples_active_domain(
    tmp_path,
) -> None:
    domain = ActiveRoomDomain.from_feet(10.0, 10.0, enabled=True)
    layout = generate_proposed_led_layout(
        domain.active_requested_length_ft,
        domain.active_requested_width_ft,
    )
    outer = RoomDimensions(
        domain.outer_aligned_length_m,
        domain.outer_aligned_width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    active = RoomDimensions(
        domain.active_aligned_length_m,
        domain.active_aligned_width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    workspace = plan_basis_workspace(
        layout=layout,
        output_directory=tmp_path / "basis",
        physical_room=outer,
        sensor_room=active,
    )
    assert workspace.sensor_grid_spec.room == active
    assert f"{outer.half_length_m:.6f}" in workspace.room_text
    assert layout.topology.base_n < generate_proposed_led_layout(
        10.0, 10.0
    ).topology.base_n
    overlay = bind_overlay_to_active_domain(
        layout.authoritative_overlay_plan(),
        domain,
    )
    assert overlay.room_length_m == domain.outer_aligned_length_m
    assert overlay.metadata["active_domain"] == domain.to_payload()
    for module in layout.modules:
        assert (
            domain.outer_aligned_length_m / 2.0
            - abs(module.x_m)
            - 0.1524 / 2.0
            >= AISLE_WIDTH_M
        )
        assert (
            domain.outer_aligned_width_m / 2.0
            - abs(module.y_m)
            - 0.1650 / 2.0
            >= AISLE_WIDTH_M
        )


@pytest.mark.parametrize("layout_policy", ["practical", "rolling_bench"])
def test_conventional_layout_and_sensors_fit_inside_aisle(
    tmp_path,
    layout_policy: str,
) -> None:
    domain = ActiveRoomDomain.from_feet(10.0, 10.0, enabled=True)
    request = ConventionalScalarTransportRequest.from_feet(
        workspace=tmp_path / layout_policy,
        room_length_ft=10.0,
        room_width_ft=10.0,
        layout_policy=layout_policy,
        active_domain=domain,
    )
    plan = plan_conventional_scalar_transport(request)
    assert plan.room.length_m == _meters(10.0)
    assert plan.sensor_grid_spec.room.length_m == _meters(6.0)
    assert len(plan.layout.fixtures) == 1
    for fixture in plan.layout.fixtures:
        bounds = fixture.footprint_bounds_m
        assert bounds.aligned_min_x_m + plan.room.half_length_m >= AISLE_WIDTH_M
        assert plan.room.half_length_m - bounds.aligned_max_x_m >= AISLE_WIDTH_M
        assert bounds.aligned_min_y_m + plan.room.half_width_m >= AISLE_WIDTH_M
        assert plan.room.half_width_m - bounds.aligned_max_y_m >= AISLE_WIDTH_M
    if layout_policy == "rolling_bench":
        assert plan.layout.placement_provenance_payload()[
            "center_to_center_pitch_m"
        ] == {"x_m": 1.2192, "y_m": 1.2192}
    assert min(
        plan.room.half_length_m - abs(point.x_m)
        for point in plan.sensor_points
    ) >= AISLE_WIDTH_M


def test_ten_by_ten_aisle_conventional_power_uses_the_one_fixture_layout(
    tmp_path,
) -> None:
    domain = ActiveRoomDomain.from_feet(10.0, 10.0, enabled=True)
    request = ConventionalScalarTransportRequest.from_feet(
        workspace=tmp_path / "conventional-power",
        room_length_ft=10.0,
        room_width_ft=10.0,
        global_dimming_factor=1.0,
        active_domain=domain,
    )
    plan = plan_conventional_scalar_transport(request)
    fixture_count = len(plan.layout.fixtures)
    overlay = bind_overlay_to_active_domain(
        plan.layout.authoritative_overlay_plan(),
        domain,
    )
    layout_identity = plan.layout.to_payload() | {
        "active_domain": domain.to_payload()
    }
    target_control = {
        "dimming_factor": 1.0,
        "full_output_power_w": 660.0,
        "effective_power_w": 660.0,
    }
    schedule = {
        "fixture_count": fixture_count,
        "full_output_power_w": 660.0,
    }
    operating_point = {
        "power": {"full_output_w": 660.0, "effective_w": 660.0}
    }

    assert domain.active_requested_length_ft == 6.0
    assert domain.active_requested_width_ft == 6.0
    assert fixture_count == len(overlay.fixture_metadata) == 1
    _validate_conventional_fixture_power_contract(
        layout_identity=layout_identity,
        overlay_plan=overlay,
        counts={"fixtures": fixture_count},
        target_control=target_control,
        full_output_schedule=schedule,
        operating_point=operating_point,
    )

    with pytest.raises(
        PublicationError,
        match=r"effective power disagrees with 1 × 660 W",
    ):
        _validate_conventional_fixture_power_contract(
            layout_identity=layout_identity,
            overlay_plan=overlay,
            counts={"fixtures": fixture_count},
            target_control=target_control | {"effective_power_w": 2640.0},
            full_output_schedule=schedule,
            operating_point=operating_point
            | {"power": {"full_output_w": 660.0, "effective_w": 2640.0}},
        )


def test_hps_reduces_to_active_capacity_without_shrinking_room(tmp_path) -> None:
    domain = ActiveRoomDomain.from_feet(10.0, 10.0, enabled=True)
    plan = plan_hps_scalar_transport(
        HpsScalarTransportRequest.from_feet(
            workspace=tmp_path / "hps",
            room_length_ft=10.0,
            room_width_ft=10.0,
            active_domain=domain,
        )
    )
    assert plan.layout.counts.total == 1
    assert plan.room.length_m == _meters(10.0)
    assert plan.sensor_grid_spec.room.length_m == _meters(6.0)
    fixture = plan.layout.fixtures[0]
    assert (
        fixture.footprint_bounds_m.aligned_min_x_m
        + plan.room.half_length_m
        >= AISLE_WIDTH_M
    )
    assert (
        fixture.footprint_bounds_m.aligned_min_y_m
        + plan.room.half_width_m
        >= AISLE_WIDTH_M
    )


def test_stage_b_geometry_and_receivers_remain_inside_active_domain() -> None:
    domain = ActiveRoomDomain.from_feet(10.0, 20.0, enabled=True)
    natural_fit = plan_natural_fit_layout_from_feet(
        10.0,
        20.0,
        active_domain=domain,
    )
    assert natural_fit.requested_room_m.length_m == _meters(10.0)
    assert natural_fit.aligned_room_m.length_m == _meters(20.0)
    assert natural_fit.x_axis.footprint_min_m == -_meters(8.0)
    scene = build_juvenile_natural_fit_scene(natural_fit)
    min_x, max_x, min_y, max_y = domain.active_bounds_aligned_m
    for leaf in scene.iter_leaves():
        for x_m, y_m, _z_m in leaf.vertices:
            assert min_x <= x_m <= max_x
            assert min_y <= y_m <= max_y
    for receiver in scene.iter_receivers():
        x_m, y_m, _z_m = receiver.point_m
        assert min_x <= x_m <= max_x
        assert min_y <= y_m <= max_y


def test_aisle_mode_changes_geometry_and_cache_authorities(tmp_path) -> None:
    outer = ActiveRoomDomain.from_feet(10.0, 10.0, enabled=False)
    aisle = ActiveRoomDomain.from_feet(10.0, 10.0, enabled=True)
    assert outer.identity_sha256 != aisle.identity_sha256
    outer_room = RoomDimensions(
        outer.outer_aligned_length_m,
        outer.outer_aligned_width_m,
        DEFAULT_ROOM_HEIGHT_M,
    )
    outer_basis = plan_basis_workspace(
        layout=generate_proposed_led_layout(
            outer.active_requested_length_ft,
            outer.active_requested_width_ft,
        ),
        output_directory=tmp_path / "proposed-outer",
        physical_room=outer_room,
        sensor_room=outer_room,
    )
    aisle_basis = plan_basis_workspace(
        layout=generate_proposed_led_layout(
            aisle.active_requested_length_ft,
            aisle.active_requested_width_ft,
        ),
        output_directory=tmp_path / "proposed-aisle",
        physical_room=outer_room,
        sensor_room=RoomDimensions(
            aisle.active_aligned_length_m,
            aisle.active_aligned_width_m,
            DEFAULT_ROOM_HEIGHT_M,
        ),
    )
    assert outer_basis.manifest.sensor_text_sha256 != (
        aisle_basis.manifest.sensor_text_sha256
    )
    assert outer_basis.manifest.emitter_source_sha256 != (
        aisle_basis.manifest.emitter_source_sha256
    )
    assert plan_natural_fit_layout_from_feet(
        10.0, 10.0, active_domain=outer
    ).plan_hash != plan_natural_fit_layout_from_feet(
        10.0, 10.0, active_domain=aisle
    ).plan_hash
    non_aisle = plan_hps_scalar_transport(
        HpsScalarTransportRequest.from_feet(
            workspace=tmp_path / "outer",
            active_domain=outer,
        )
    )
    with_aisle = plan_hps_scalar_transport(
        HpsScalarTransportRequest.from_feet(
            workspace=tmp_path / "aisle",
            active_domain=aisle,
        )
    )
    assert non_aisle.layout.layout_id != with_aisle.layout.layout_id
    assert non_aisle.sensor_identity != with_aisle.sensor_identity
    assert non_aisle.transport_identity != with_aisle.transport_identity
    assert non_aisle.ambient_cache_identity != with_aisle.ambient_cache_identity


def test_aisle_mode_rejects_rooms_without_a_positive_active_domain() -> None:
    with pytest.raises(ValueError, match="exceed 4 ft"):
        ActiveRoomDomain.from_feet(4.0, 10.0, enabled=True)
    with pytest.raises(RequestValidationError, match="exceed 4 ft"):
        parse_run_request(
            _payload("proposed", aisle_mode=True)
            | {"room_length_ft": 4.0}
        )
