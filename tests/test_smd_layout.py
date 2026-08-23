from __future__ import annotations

from collections import Counter
import math

import pytest

from fspm_optics.fixtures import smd
from fspm_optics.fixtures.smd.config import SmdLayoutConfig
from fspm_optics.fixtures.smd.positions import (
    generate_proposed_led_layout,
    generate_smd_layout,
)
from fspm_optics.fixtures.smd.power_schedule import build_uniform_module_schedule
from fspm_optics.fixtures.smd.radiance_writer import build_smd_radiance_document
from fspm_optics.layout.domain import LayoutPoint
from fspm_optics.layout.fixture_plan import (
    LEGACY_FIXTURE_POLICY_ID,
    LINEAR_FIXTURE_POLICY_ID,
    STANDALONE_FIXTURE_POLICY_ID,
    build_fixture_assemblies,
    partition_linear_prefer_pairs,
    resolve_ordered_display_fixture_type,
)
from fspm_optics.layout.mode import ProposedLayoutMode
from fspm_optics.layout.modular import generate_modular_layout


FAILING_REVERSE_L_POINTS = (
    (-5.0, -1.0),
    (-6.0, 0.0),
    (-5.0, 1.0),
    (-4.0, 2.0),
)


def test_ordered_topology_overrides_l_family_scientific_label() -> None:
    for scientific_type in ("L", "reverse_L"):
        assert resolve_ordered_display_fixture_type(
            scientific_type, FAILING_REVERSE_L_POINTS
        ) == "L"
        assert resolve_ordered_display_fixture_type(
            scientific_type, tuple(reversed(FAILING_REVERSE_L_POINTS))
        ) == "reverse_L"

    assembly = build_fixture_assemblies(
        [
            (
                "reverse_L",
                tuple(LayoutPoint(*point) for point in FAILING_REVERSE_L_POINTS),
            )
        ]
    )[0]
    assert assembly.fixture_type == "reverse_L"
    assert assembly.display_fixture_type == "L"
    assert tuple((point.x, point.y) for point in assembly.points) == (
        FAILING_REVERSE_L_POINTS
    )


@pytest.mark.parametrize("scientific_type", ("linear4", "L", "reverse_L"))
def test_four_collinear_members_always_select_linear4(
    scientific_type: str,
) -> None:
    points = ((-3.0, 2.0), (-1.0, 2.0), (1.0, 2.0), (3.0, 2.0))
    assert resolve_ordered_display_fixture_type(scientific_type, points) == "linear4"


def test_three_member_ordered_topology_selects_straight_or_corner() -> None:
    assert resolve_ordered_display_fixture_type(
        "linear3", ((0.0, 0.0), (1.0, 0.0), (2.0, 0.0))
    ) == "linear3"
    assert resolve_ordered_display_fixture_type(
        "linear3", ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0))
    ) == "corner3"


@pytest.mark.parametrize(
    ("fixture_type", "points"),
    (
        ("linear3", ((0.0, 0.0), (1.0, 0.0), (2.0, 0.5))),
        ("linear3", ((0.0, 0.0), (0.0, 0.0), (1.0, 0.0))),
        ("L", ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (2.0, 1.0))),
        ("reverse_L", ((0.0, 0.0), (1.0, 0.0), (2.0, 1.0), (3.0, 2.0))),
        ("linear4", ((0.0, 0.0), (1.0, 0.0), (1.0, 0.0), (2.0, 0.0))),
    ),
)
def test_ordered_topology_rejects_unsupported_paths(
    fixture_type: str,
    points: tuple[tuple[float, float], ...],
) -> None:
    with pytest.raises(ValueError, match="topology|zero-length"):
        resolve_ordered_display_fixture_type(fixture_type, points)


def test_ten_by_ten_proposed_led_layout_has_five_control_zones() -> None:
    modular = generate_modular_layout(10, 10)
    physical = generate_proposed_led_layout(10, 10)
    legacy = generate_proposed_led_layout(
        10, 10, proposed_layout_mode=ProposedLayoutMode.LEGACY
    )
    linear = generate_proposed_led_layout(
        10, 10, proposed_layout_mode=ProposedLayoutMode.LINEAR
    )

    assert modular.control_zone_count == 5
    assert modular.coefficient_count == 5
    assert [len(zone.points) for zone in modular.control_zones] == [5, 8, 12, 16, 20]
    assert len(modular.positions) == 61
    assert physical.control_zone_count == 5
    assert len(physical.modules) == 61
    assert tuple(
        (module.x_m, module.y_m, module.z_m, module.control_zone_index)
        for module in physical.modules[:5]
    ) == (
        (0.0, 0.0, 0.4622, 0),
        (0.28345, 0.28345, 0.4622, 0),
        (0.28345, -0.28345, 0.4622, 0),
        (-0.28345, -0.28345, 0.4622, 0),
        (-0.28345, 0.28345, 0.4622, 0),
    )
    assert physical.proposed_layout_mode is ProposedLayoutMode.STANDALONE_MODULES
    assert physical.fixture_policy_id == STANDALONE_FIXTURE_POLICY_ID
    assert physical.mechanical_envelope_id == "standalone_module_150x150mm_v1"
    assert physical.module_footprint_x_m == physical.module_footprint_y_m == 0.15
    assert physical.pitch_x_m == physical.pitch_y_m
    assert physical.fixture_asset_set_id == "proposed-led-module-v1"
    assert len(physical.fixtures) == 61
    assert Counter(item.fixture_type for item in physical.fixtures) == {
        "standalone_module": 61,
    }
    assert all(
        fixture.member_module_indices == (fixture_index,)
        and fixture.connectors == ()
        and fixture.orientation_degrees == 0.0
        and fixture.source_orientation == "fixed"
        for fixture_index, fixture in enumerate(physical.fixtures)
    )
    assert tuple(
        module_index
        for fixture in physical.fixtures
        for module_index in fixture.member_module_indices
    ) == tuple(range(61))
    assert linear.fixture_policy_id == LINEAR_FIXTURE_POLICY_ID
    assert len(linear.fixtures) == 29
    assert Counter(item.fixture_type for item in linear.fixtures) == {
        "centerpiece": 1,
        "linear2": 28,
    }
    assert legacy.fixture_policy_id == LEGACY_FIXTURE_POLICY_ID
    assert len(legacy.fixtures) == 17
    assert (
        legacy.fixtures[0].fixture_type,
        legacy.fixtures[0].member_module_indices,
        tuple(
            (connector.start_module_index, connector.end_module_index)
            for connector in legacy.fixtures[0].connectors
        ),
    ) == (
        "centerpiece",
        (0, 1, 2, 3, 4),
        ((0, 1), (0, 2), (0, 3), (0, 4)),
    )
    assert (
        legacy.fixtures[1].fixture_type,
        legacy.fixtures[1].member_module_indices,
        tuple(
            (connector.start_module_index, connector.end_module_index)
            for connector in legacy.fixtures[1].connectors
        ),
    ) == (
        "reverse_L",
        (5, 6, 7, 8),
        ((5, 6), (6, 7), (7, 8)),
    )


def test_mechanical_envelope_is_immutable_and_owned_by_layout_mode() -> None:
    standalone = SmdLayoutConfig(room_length_ft=10, room_width_ft=10)
    linear = SmdLayoutConfig(
        room_length_ft=10,
        room_width_ft=10,
        proposed_layout_mode="linear",
    )
    legacy = SmdLayoutConfig(
        room_length_ft=10,
        room_width_ft=10,
        proposed_layout_mode="legacy",
    )
    assert (
        standalone.mechanical_envelope_id,
        standalone.module_footprint_x_m,
        standalone.module_footprint_y_m,
    ) == ("standalone_module_150x150mm_v1", 0.15, 0.15)
    assert {
        (
            config.mechanical_envelope_id,
            config.module_footprint_x_m,
            config.module_footprint_y_m,
        )
        for config in (linear, legacy)
    } == {("proposed_fixture_module_152p4x165mm_v1", 0.1524, 0.165)}
    with pytest.raises(TypeError, match="module_footprint_x_m"):
        SmdLayoutConfig(
            room_length_ft=10,
            room_width_ft=10,
            module_footprint_x_m=0.2,  # type: ignore[call-arg]
        )


def test_control_zone_count_is_the_basis_coefficient_count() -> None:
    layout = generate_proposed_led_layout(15, 12)
    assert layout.coefficient_count == layout.control_zone_count
    assert layout.control_zone_indices == tuple(range(layout.coefficient_count))


def test_ten_by_ten_distinguishes_corner3_and_straight_linear3_display_types() -> None:
    layout = generate_proposed_led_layout(
        10, 10, proposed_layout_mode=ProposedLayoutMode.LEGACY
    )
    corner = [
        fixture
        for fixture in layout.fixtures
        if fixture.fixture_type == "linear3"
        and fixture.display_fixture_type == "corner3"
    ]
    straight = [
        fixture
        for fixture in layout.fixtures
        if fixture.fixture_type == "linear3"
        and fixture.display_fixture_type == "linear3"
    ]

    assert corner
    assert straight
    assert all(len(fixture.member_module_indices) == 3 for fixture in corner + straight)
    assert [fixture.fixture_id for fixture in layout.fixtures] == [
        f"proposed-fixture-{index:04d}" for index in range(len(layout.fixtures))
    ]


def test_ten_by_ten_reclassifies_only_the_six_collinear_four_module_groups() -> None:
    layout = generate_proposed_led_layout(
        10, 10, proposed_layout_mode=ProposedLayoutMode.LEGACY
    )
    corrected = {
        fixture.fixture_id: (fixture.fixture_type, fixture.display_fixture_type)
        for fixture in layout.fixtures
        if fixture.fixture_id in {
            "proposed-fixture-0007",
            "proposed-fixture-0008",
            "proposed-fixture-0009",
            "proposed-fixture-0010",
            "proposed-fixture-0013",
            "proposed-fixture-0014",
        }
    }
    assert corrected == {
        "proposed-fixture-0007": ("L", "linear4"),
        "proposed-fixture-0008": ("L", "linear4"),
        "proposed-fixture-0009": ("L", "linear4"),
        "proposed-fixture-0010": ("L", "linear4"),
        "proposed-fixture-0013": ("L", "linear4"),
        "proposed-fixture-0014": ("reverse_L", "linear4"),
    }
    for fixture in layout.fixtures:
        if fixture.fixture_id not in corrected:
            continue
        points = [layout.modules[index] for index in fixture.member_module_indices]
        segments = [
            (right.x_m - left.x_m, right.y_m - left.y_m)
            for left, right in zip(points, points[1:])
        ]
        assert all(
            abs(first[0] * second[1] - first[1] * second[0])
            <= 1.0e-5 * math.hypot(*first) * math.hypot(*second)
            for first, second in zip(segments, segments[1:])
        )
    assert layout.fixtures[1].display_fixture_type == "reverse_L"
    assert layout.fixtures[2].display_fixture_type == "reverse_L"


@pytest.mark.parametrize(("length_ft", "width_ft"), [(12, 10), (15, 12)])
def test_rectangular_layouts_emit_corner3_from_member_topology(
    length_ft: int, width_ft: int
) -> None:
    layout = generate_proposed_led_layout(
        length_ft,
        width_ft,
        proposed_layout_mode=ProposedLayoutMode.LEGACY,
    )
    assert any(
        fixture.fixture_type == "linear3"
        and fixture.display_fixture_type == "corner3"
        for fixture in layout.fixtures
    )


def test_every_module_has_a_valid_control_zone_index() -> None:
    layout = generate_proposed_led_layout(20, 10)
    assert layout.modules
    assert {module.control_zone_index for module in layout.modules} == set(
        layout.control_zone_indices
    )
    assert all(
        0 <= module.control_zone_index < layout.control_zone_count
        for module in layout.modules
    )


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "expected_control_zones"),
    [
        (10, 10, 5),
        (12, 10, 6),
        (15, 12, 7),
        (20, 10, 6),
        (20, 20, 10),
    ],
)
def test_integer_room_layouts_have_geometry_dependent_control_zone_counts(
    length_ft: int, width_ft: int, expected_control_zones: int
) -> None:
    layout = generate_proposed_led_layout(length_ft, width_ft)
    assert layout.control_zone_count == expected_control_zones
    assert 5 <= layout.control_zone_count <= 10


def test_rectangular_positions_and_zone_ids_are_deterministic() -> None:
    config = SmdLayoutConfig(room_length_ft=16, room_width_ft=10)
    first = generate_smd_layout(config)
    second = generate_smd_layout(config)
    transposed = generate_proposed_led_layout(10, 16)

    assert first == second
    assert first.modules == transposed.modules
    assert first.fixtures == transposed.fixtures
    assert first.control_zone_count == transposed.control_zone_count
    assert transposed.axes_swapped is True
    assert len({(module.x_m, module.y_m) for module in first.modules}) == len(
        first.modules
    )
    assert all(
        fixture.fixture_id == f"proposed-fixture-{index:04d}"
        for index, fixture in enumerate(first.fixtures)
    )
    assert all(
        math.isfinite(fixture.orientation_degrees) for fixture in first.fixtures
    )
    assert all(
        connector.start_module_index in fixture.member_module_indices
        and connector.end_module_index in fixture.member_module_indices
        for fixture in first.fixtures
        for connector in fixture.connectors
    )
    for fixture in first.fixtures:
        if not fixture.connectors:
            assert fixture.fixture_type == "standalone_module"
            assert fixture.orientation_degrees == 0.0
            continue
        connector = fixture.connectors[0]
        start = first.modules[connector.start_module_index]
        end = first.modules[connector.end_module_index]
        expected_orientation = round(
            math.degrees(math.atan2(end.y_m - start.y_m, end.x_m - start.x_m))
            % 360.0,
            6,
        )
        assert fixture.orientation_degrees == expected_orientation


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "expected"),
    (
        (10, 10, {"centerpiece": 1, "linear2": 28}),
        (10, 12, {"centerpiece": 1, "linear2": 28}),
        (10, 14, {"linear3": 1, "linear2": 40}),
        (10, 20, {"linear3": 1, "linear2": 51}),
        (10, 22, {"centerpiece": 2, "linear3": 1, "linear2": 57}),
        (10, 34, {"centerpiece": 3, "linear3": 2, "linear2": 86}),
    ),
)
def test_linear_mode_representative_fixture_inventories(
    length_ft: int,
    width_ft: int,
    expected: dict[str, int],
) -> None:
    layout = generate_proposed_led_layout(
        length_ft,
        width_ft,
        proposed_layout_mode=ProposedLayoutMode.LINEAR,
    )
    assert Counter(item.fixture_type for item in layout.fixtures) == expected
    assert {
        item.display_fixture_type for item in layout.fixtures
    } <= {"centerpiece", "linear2", "linear3"}
    owned = [
        module_index
        for fixture in layout.fixtures
        for module_index in fixture.member_module_indices
    ]
    assert sorted(owned) == list(range(len(layout.modules)))
    assert len(owned) == len(set(owned))


@pytest.mark.parametrize(
    ("length_ft", "width_ft", "expected"),
    (
        (
            10,
            10,
            {"centerpiece": 1, "linear3": 8, "L": 5, "reverse_L": 3},
        ),
        (
            10,
            14,
            {
                "linear2": 2,
                "linear3": 1,
                "linear4": 8,
                "L": 6,
                "reverse_L": 5,
            },
        ),
    ),
)
def test_legacy_mode_retains_fixture_inventory(
    length_ft: int,
    width_ft: int,
    expected: dict[str, int],
) -> None:
    layout = generate_proposed_led_layout(
        length_ft,
        width_ft,
        proposed_layout_mode=ProposedLayoutMode.LEGACY,
    )
    assert Counter(item.fixture_type for item in layout.fixtures) == expected
    assert layout.fixture_policy_id == LEGACY_FIXTURE_POLICY_ID


def test_offset_zero_rectangle_keeps_one_five_module_cross() -> None:
    layout = generate_proposed_led_layout(
        10, 12, proposed_layout_mode=ProposedLayoutMode.LINEAR
    )
    centerpieces = [
        fixture for fixture in layout.fixtures if fixture.fixture_type == "centerpiece"
    ]
    assert len(centerpieces) == 1
    fixture = centerpieces[0]
    modules = [layout.modules[index] for index in fixture.member_module_indices]
    center = modules[0]
    assert len(modules) == 5
    offsets = {
        (
            round(item.x_m - center.x_m, 6),
            round(item.y_m - center.y_m, 6),
        )
        for item in modules[1:]
    }
    assert len(offsets) == 4
    assert all((-x, -y) in offsets for x, y in offsets)
    assert {item.control_zone_index for item in modules} == {0, 1}


def test_pair_preferred_partition_handles_even_and_odd_collinear_rows() -> None:
    even = tuple(LayoutPoint(float(index), 0.0) for index in range(6))
    odd = tuple(LayoutPoint(float(index), 0.0) for index in range(7))
    assert [kind for kind, _points in partition_linear_prefer_pairs(even)] == [
        "linear2",
        "linear2",
        "linear2",
    ]
    assert [kind for kind, _points in partition_linear_prefer_pairs(odd)] == [
        "linear3",
        "linear2",
        "linear2",
    ]

    connector_layout = generate_modular_layout(
        10, 22, layout_mode=ProposedLayoutMode.LINEAR
    )
    connector = next(
        zone for zone in connector_layout.control_zones if zone.kind == "connector"
    )
    connector_fixtures = [
        fixture
        for fixture in connector_layout.fixture_assemblies
        if set(fixture.points) <= set(connector.points)
    ]
    assert Counter(item.fixture_type for item in connector_fixtures) == {
        "linear3": 1,
        "linear2": 1,
    }


@pytest.mark.parametrize(
    ("length_ft", "width_ft"),
    ((10, 10), (10, 12), (10, 14), (10, 22), (12.5, 7.75), (9.25, 30.0)),
)
def test_modes_preserve_authoritative_modules_topology_and_source_text(
    length_ft: float,
    width_ft: float,
) -> None:
    linear_modular = generate_modular_layout(
        length_ft, width_ft, layout_mode=ProposedLayoutMode.LINEAR
    )
    legacy_modular = generate_modular_layout(
        length_ft,
        width_ft,
        layout_mode=ProposedLayoutMode.LEGACY,
    )
    assert linear_modular.positions == legacy_modular.positions
    assert linear_modular.control_zones == legacy_modular.control_zones
    assert linear_modular.topology == legacy_modular.topology

    linear = generate_proposed_led_layout(
        length_ft,
        width_ft,
        proposed_layout_mode=ProposedLayoutMode.LINEAR,
    )
    legacy = generate_proposed_led_layout(
        length_ft,
        width_ft,
        proposed_layout_mode=ProposedLayoutMode.LEGACY,
    )
    assert linear.modules == legacy.modules
    assert linear.topology == legacy.topology
    assert linear.room_length_m == legacy.room_length_m
    assert linear.room_width_m == legacy.room_width_m
    assert linear.pitch_x_m == legacy.pitch_x_m
    assert linear.pitch_y_m == legacy.pitch_y_m
    assert linear.axes_swapped == legacy.axes_swapped
    assert linear.fixture_policy_id != legacy.fixture_policy_id
    linear_schedule = build_uniform_module_schedule(linear, 1.0)
    legacy_schedule = build_uniform_module_schedule(legacy, 1.0)
    assert linear_schedule == legacy_schedule
    assert (
        build_smd_radiance_document(linear, linear_schedule).radiance_text
        == build_smd_radiance_document(legacy, legacy_schedule).radiance_text
    )


def test_unsupported_optional_control_modes_are_not_public() -> None:
    assert "basis_ring_power_schedule" not in smd.__all__
    assert "module_basis_ring_power_schedule" not in smd.__all__
    assert "outer_module_overrides" not in smd.__all__
    assert not hasattr(smd, "basis_ring_power_schedule")
    assert not hasattr(smd, "module_basis_ring_power_schedule")
