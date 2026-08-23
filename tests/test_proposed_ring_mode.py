from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import pytest

import fspm_optics.layout.modular as modular_module
from fspm_optics.application.domain import (
    ProposedRingMode,
    ProposedRunRequest,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.application.proposed import _layout_identity
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import (
    build_uniform_module_schedule,
)
from fspm_optics.layout.mode import ProposedLayoutMode
from fspm_optics.layout.modular import generate_modular_layout
from fspm_optics.fixtures.smd.radiance_writer import SmdEmitterAssumptions
from fspm_optics.transport.basis.planning import plan_isolated_rtrace_basis
from fspm_optics.transport.proposed_uniform import (
    plan_uniform_proposed_stage_a,
)
from fspm_optics.viewer.fixtures import build_fixture_publication


def _payload(**changes: object) -> dict[str, object]:
    return {
        "system": "proposed",
        "target_ppfd": 500.0,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    } | changes


def _identity(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _zone_sizes(length_ft: float, width_ft: float, mode: str) -> list[int]:
    return [
        len(zone.points)
        for zone in generate_modular_layout(
            length_ft,
            width_ft,
            ring_mode=mode,
        ).control_zones
    ]


def _extents(layout: object) -> tuple[float, float, float, float]:
    modules = layout.modules  # type: ignore[attr-defined]
    return (
        min(module.x_m for module in modules),
        max(module.x_m for module in modules),
        min(module.y_m for module in modules),
        max(module.y_m for module in modules),
    )


def test_public_ring_enum_defaults_serializes_and_changes_canonical_identity() -> None:
    default = ProposedRunRequest.from_payload(_payload())
    explicit_full = ProposedRunRequest.from_payload(
        _payload(proposed_ring_mode="full")
    )
    reduced = ProposedRunRequest.from_payload(
        _payload(proposed_ring_mode="reduced_one_ring")
    )

    assert default.proposed_ring_mode is ProposedRingMode.FULL
    assert explicit_full == default
    assert reduced.proposed_ring_mode is ProposedRingMode.REDUCED_ONE_RING
    assert default.to_dict()["proposed_ring_mode"] == "full"
    assert reduced.to_dict()["proposed_ring_mode"] == "reduced_one_ring"
    assert _identity(default.to_dict()) != _identity(reduced.to_dict())


@pytest.mark.parametrize(
    "value",
    ("", "FULL", " reduced_one_ring", "reduced_one_ring ", "reduced", 1, True, []),
)
def test_public_ring_mode_rejects_unknown_malformed_and_ambiguous_values(
    value: object,
) -> None:
    with pytest.raises(RequestValidationError) as caught:
        ProposedRunRequest.from_payload(_payload(proposed_ring_mode=value))
    assert caught.value.field == "proposed_ring_mode"


@pytest.mark.parametrize("system", ("conventional", "hps"))
def test_ring_mode_is_proposed_only(system: str) -> None:
    payload = _payload(
        system=system,
        proposed_ring_mode="reduced_one_ring",
    )
    if system == "hps":
        payload.pop("target_ppfd")
    with pytest.raises(RequestValidationError, match="unsupported request fields"):
        parse_run_request(payload)


@pytest.mark.parametrize(
    "physical_mode",
    (ProposedLayoutMode.LINEAR, ProposedLayoutMode.LEGACY),
)
def test_reduced_ring_fails_closed_for_historical_physical_modes(
    physical_mode: ProposedLayoutMode,
) -> None:
    with pytest.raises(RequestValidationError, match="standalone_modules") as caught:
        parse_run_request(
            _payload(proposed_ring_mode="reduced_one_ring"),
            proposed_layout_mode=physical_mode,
        )
    assert caught.value.field == "proposed_ring_mode"

    full = parse_run_request(
        _payload(proposed_ring_mode="full"),
        proposed_layout_mode=physical_mode,
    )
    assert full.proposed_layout_mode is physical_mode
    assert full.proposed_ring_mode is ProposedRingMode.FULL


def test_exact_square_counts_zone_sizes_and_topology_identity() -> None:
    full = generate_modular_layout(10, 10)
    reduced = generate_modular_layout(
        10, 10, ring_mode=ProposedRingMode.REDUCED_ONE_RING
    )

    assert len(full.positions) == 61
    assert _zone_sizes(10, 10, "full") == [5, 8, 12, 16, 20]
    assert full.topology.base_n == full.topology.nominal_base_n == 5
    assert full.topology.module_pattern_id == "centered_square_full_v1"

    assert len(reduced.positions) == 41
    assert _zone_sizes(10, 10, "reduced_one_ring") == [5, 8, 12, 16]
    assert reduced.topology.nominal_base_n == 5
    assert reduced.topology.base_n == 4
    assert (
        reduced.topology.module_pattern_id
        == "centered_square_reduced_one_ring_v1"
    )


def test_exact_pure_rectangular_counts_and_outer_ring_removal() -> None:
    full = generate_modular_layout(30, 50)
    reduced = generate_modular_layout(
        30, 50, ring_mode=ProposedRingMode.REDUCED_ONE_RING
    )

    assert full.topology.square_tile_count == 0
    assert full.topology.has_rectangular_extension is True
    assert len(full.positions) == 791
    assert [len(zone.points) for zone in full.control_zones][-1] == 80
    assert len(reduced.positions) == 711
    assert [len(zone.points) for zone in reduced.control_zones] == [
        11, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60, 64, 68, 72, 76,
    ]
    assert len(full.positions) - len(reduced.positions) == 80
    assert reduced.topology.rectangular_offset == full.topology.rectangular_offset


def test_reduced_topologies_are_constructed_without_generating_outer_rings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    square_orders: list[int] = []
    rectangular_orders: list[int] = []
    original_square = modular_module._square_ring_points
    original_rectangular = modular_module._rectangular_ring_points

    def observed_square(order: int):
        square_orders.append(order)
        return original_square(order)

    def observed_rectangular(order: int, offset: int):
        rectangular_orders.append(order)
        return original_rectangular(order, offset)

    monkeypatch.setattr(modular_module, "_square_ring_points", observed_square)
    monkeypatch.setattr(
        modular_module,
        "_rectangular_ring_points",
        observed_rectangular,
    )
    generate_modular_layout(10, 10, ring_mode="reduced_one_ring")
    generate_modular_layout(30, 50, ring_mode="reduced_one_ring")

    assert square_orders == [1, 2, 3]
    assert rectangular_orders == list(range(1, 15))


@pytest.mark.parametrize("dimensions", ((10, 10), (30, 50)))
def test_reduced_pitch_increases_and_natural_fit_extents_are_unchanged(
    dimensions: tuple[int, int],
) -> None:
    full = generate_proposed_led_layout(*dimensions)
    reduced = generate_proposed_led_layout(
        *dimensions,
        proposed_ring_mode="reduced_one_ring",
    )

    assert reduced.pitch_x_m > full.pitch_x_m
    assert reduced.pitch_y_m > full.pitch_y_m
    assert _extents(reduced) == _extents(full)
    for layout in (full, reduced):
        minimum_x, maximum_x, minimum_y, maximum_y = _extents(layout)
        assert math.isclose(minimum_x + maximum_x, 0.0, abs_tol=1e-12)
        assert math.isclose(minimum_y + maximum_y, 0.0, abs_tol=1e-12)


def test_reduced_standalone_fixture_zone_schedule_and_power_dimensions() -> None:
    layout = generate_proposed_led_layout(
        10, 10, proposed_ring_mode="reduced_one_ring"
    )
    schedule = build_uniform_module_schedule(layout, 100.0)

    assert len(layout.modules) == len(layout.fixtures) == 41
    assert layout.control_zone_count == layout.coefficient_count == 4
    assert schedule.module_count == 41
    assert schedule.control_zone_count == 4
    assert schedule.total_watts == 4100.0
    assert all(
        fixture.member_module_indices == (index,)
        and fixture.connectors == ()
        and fixture.fixture_type == "standalone_module"
        for index, fixture in enumerate(layout.fixtures)
    )
    assert {
        module.module_index for module in layout.modules
    } == {
        member
        for fixture in layout.fixtures
        for member in fixture.member_module_indices
    }
    assert {
        module.control_zone_index for module in layout.modules
    } == set(range(4))


def test_reduced_basis_dimensions_and_cache_identity_follow_topology(
    tmp_path: Path,
) -> None:
    reduced = generate_proposed_led_layout(
        10, 10, proposed_ring_mode="reduced_one_ring"
    )
    plan = plan_isolated_rtrace_basis(
        layout=reduced,
        sensor_count=3,
        room_height_m=3.048,
        room_source_path=tmp_path / "room.rad",
        sensor_input_path=tmp_path / "sensors.pts",
        output_directory=tmp_path / "basis",
        reference_watts=1.0,
    )

    assert len(plan.columns) == 4
    assert plan.manifest.matrix_shape == (3, 4)
    assert plan.manifest.layout_module_count == 41
    assert plan.manifest.proposed_ring_mode is ProposedRingMode.REDUCED_ONE_RING
    assert (
        plan.manifest.module_pattern_id
        == "centered_square_reduced_one_ring_v1"
    )
    assert all(
        ".reduced_one_ring." in column.ambient_cache_path.name
        for column in plan.columns
        if column.ambient_cache_path is not None
    )


@pytest.mark.parametrize(
    "source_mode",
    ("native_smd", "cob_source_shape_surrogate"),
)
def test_reduced_native_and_cob_sources_plan_in_both_control_modes(
    source_mode: str,
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(
        10, 10, proposed_ring_mode="reduced_one_ring"
    )
    assumptions = SmdEmitterAssumptions(source_mode=source_mode)
    basis = plan_isolated_rtrace_basis(
        layout=layout,
        sensor_count=3,
        room_height_m=3.048,
        room_source_path=tmp_path / source_mode / "room.rad",
        sensor_input_path=tmp_path / source_mode / "sensors.pts",
        output_directory=tmp_path / source_mode / "basis",
        reference_watts=1.0,
        emitter_assumptions=assumptions,
    )
    uniform = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=tmp_path / source_mode / "uniform",
        radiance_options=("-ab", "0"),
        emitter_assumptions=assumptions,
    )

    assert len(basis.columns) == 4
    assert basis.manifest.proposed_source_mode == source_mode
    assert basis.manifest.proposed_ring_mode is ProposedRingMode.REDUCED_ONE_RING
    assert uniform.emitter_document.metadata.source_mode == source_mode
    assert uniform.layout.proposed_ring_mode is ProposedRingMode.REDUCED_ONE_RING
    assert uniform.schedule.module_count == 41
    assert ".reduced_one_ring." in uniform.paths.ambient_cache.name


def test_reduced_viewer_catalog_authenticates_ring_and_singleton_placements() -> None:
    layout = generate_proposed_led_layout(
        10, 10, proposed_ring_mode="reduced_one_ring"
    )
    publication = build_fixture_publication(
        run_id="a" * 32,
        system_id="proposed",
        requested_length_ft=10.0,
        requested_width_ft=10.0,
        layout_identity=_layout_identity(layout),
    )
    catalog = json.loads(publication.catalog.data)

    assert catalog["proposed_ring_mode"] == "reduced_one_ring"
    assert (
        catalog["module_pattern_id"]
        == "centered_square_reduced_one_ring_v1"
    )
    assert catalog["fixture_plan"]["proposed_ring_mode"] == "reduced_one_ring"
    assert catalog["fixture_count"] == 41
    assert catalog["asset_group_count"] == 1
    assert catalog["asset_groups"][0]["display_asset_id"] == (
        "proposed-led-module-v1"
    )
    assert catalog["asset_groups"][0]["instance_matrices"]["count"] == 41


def test_full_default_is_exact_and_rectangular_axis_swap_is_preserved() -> None:
    assert generate_proposed_led_layout(10, 10) == generate_proposed_led_layout(
        10, 10, proposed_ring_mode="full"
    )
    length_major = generate_proposed_led_layout(
        50, 30, proposed_ring_mode="reduced_one_ring"
    )
    width_major = generate_proposed_led_layout(
        30, 50, proposed_ring_mode="reduced_one_ring"
    )
    assert length_major.modules == width_major.modules
    assert length_major.fixtures == width_major.fixtures
    assert length_major.axes_swapped is False
    assert width_major.axes_swapped is True


def test_small_reduced_room_has_positive_two_axis_span_or_precise_error() -> None:
    valid = generate_proposed_led_layout(
        1, 1, proposed_ring_mode="reduced_one_ring"
    )
    assert len(valid.modules) == 5
    assert valid.pitch_x_m > 0.0
    assert valid.pitch_y_m > 0.0
    assert len({(module.x_m, module.y_m) for module in valid.modules}) == 5

    with pytest.raises(ValueError, match="too small.*module envelope"):
        generate_proposed_led_layout(
            0.5, 0.5, proposed_ring_mode="reduced_one_ring"
        )
