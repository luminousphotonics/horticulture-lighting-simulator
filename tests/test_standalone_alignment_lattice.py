from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path

import pytest

from fspm_optics.fixtures.occlusion import (
    FIXTURE_BODY_MATERIAL_ID,
    FIXTURE_BODY_MATERIAL_NAME,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.alignment_lattice import (
    ALIGNMENT_LATTICE_ATTACHMENT_POLICY_ID,
    ALIGNMENT_LATTICE_DIAMETER_M,
    ALIGNMENT_LATTICE_RADIUS_M,
    AUTHENTICATED_REAR_PLANE_OFFSET_M,
)
from fspm_optics.fixtures.smd.config import SmdLayoutConfig
from fspm_optics.fixtures.smd.positions import (
    generate_proposed_led_layout,
    generate_smd_layout,
)
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.transport.basis.planning import plan_isolated_rtrace_basis
from fspm_optics.transport.proposed_uniform import plan_uniform_proposed_stage_a
from fspm_optics.viewer.fixtures import build_fixture_publication


@pytest.mark.parametrize(
    ("ring_mode", "module_count", "horizontal_count", "diagonal_count"),
    (
        ("reduced_one_ring", 41, 32, 64),
        ("full", 61, 50, 100),
    ),
)
def test_exact_ten_by_ten_topology_counts_order_and_connectivity(
    ring_mode: str,
    module_count: int,
    horizontal_count: int,
    diagonal_count: int,
) -> None:
    first = generate_proposed_led_layout(
        10, 10, proposed_ring_mode=ring_mode
    )
    second = generate_proposed_led_layout(
        10, 10, proposed_ring_mode=ring_mode
    )
    lattice = first.alignment_lattice

    assert lattice is not None
    assert first == second
    assert len(first.modules) == lattice.module_count == module_count
    assert lattice.horizontal_count == horizontal_count
    assert lattice.diagonal_count == diagonal_count
    assert tuple(link.link_index for link in lattice.links) == tuple(
        range(horizontal_count + diagonal_count)
    )
    assert [link.kind for link in lattice.links] == (
        ["horizontal"] * horizontal_count
        + ["diagonal"] * diagonal_count
    )
    assert len(
        {
            tuple(sorted((link.start_module_index, link.end_module_index)))
            for link in lattice.links
        }
    ) == len(lattice.links)
    assert _connected_module_count(first) == module_count
    _assert_no_unrelated_crossings(first)


@pytest.mark.parametrize("dimensions", ((12, 20), (20, 12), (15, 12)))
def test_rectangular_and_axis_swapped_lattices_are_symmetric_and_deterministic(
    dimensions: tuple[int, int],
) -> None:
    layout = generate_proposed_led_layout(
        *dimensions, proposed_ring_mode="reduced_one_ring"
    )
    transposed = generate_proposed_led_layout(
        dimensions[1], dimensions[0], proposed_ring_mode="reduced_one_ring"
    )
    lattice = layout.alignment_lattice

    assert lattice is not None
    assert layout.modules == transposed.modules
    assert lattice == transposed.alignment_lattice
    assert layout.axes_swapped is (dimensions[0] < dimensions[1])
    assert _connected_module_count(layout) == len(layout.modules)
    degrees = defaultdict(int)
    for link in lattice.links:
        degrees[link.start_module_index] += 1
        degrees[link.end_module_index] += 1
    by_reflection: dict[tuple[float, float], int] = {
        (module.x_m, module.y_m): degrees[module.module_index]
        for module in layout.modules
    }
    assert all(
        by_reflection[(module.x_m, module.y_m)]
        == by_reflection[(-module.x_m, module.y_m)]
        == by_reflection[(module.x_m, -module.y_m)]
        for module in layout.modules
    )
    _assert_no_unrelated_crossings(layout)


def test_thirty_by_fifty_reduced_aisle_has_563_connected_modules() -> None:
    domain = ActiveRoomDomain.from_feet(30, 50, enabled=True)
    layout = generate_proposed_led_layout(
        domain.active_requested_length_ft,
        domain.active_requested_width_ft,
        proposed_ring_mode="reduced_one_ring",
    )
    lattice = layout.alignment_lattice

    assert (domain.active_requested_length_ft, domain.active_requested_width_ft) == (
        26.0,
        46.0,
    )
    assert lattice is not None
    assert len(layout.modules) == lattice.module_count == 563
    assert (lattice.horizontal_count, lattice.diagonal_count) == (538, 1056)
    assert _connected_module_count(layout) == 563
    assert len(lattice.links) == 1594


def test_free_spans_use_edge_midpoints_corners_and_never_enter_module_bodies() -> None:
    layout = generate_proposed_led_layout(
        10, 10, proposed_ring_mode="reduced_one_ring"
    )
    lattice = layout.alignment_lattice
    assert lattice is not None
    half = 0.075

    for link in lattice.links:
        start_module = layout.modules[link.start_module_index]
        end_module = layout.modules[link.end_module_index]
        start, end = link.start_xyz_m, link.end_xyz_m
        assert start[2] == end[2] == lattice.attachment_plane_z_m
        if link.kind == "horizontal":
            assert start[1] == start_module.y_m
            assert end[1] == end_module.y_m
            assert abs(start[0] - start_module.x_m) == pytest.approx(half)
            assert abs(end[0] - end_module.x_m) == pytest.approx(half)
        else:
            assert abs(start[0] - start_module.x_m) == pytest.approx(half)
            assert abs(start[1] - start_module.y_m) == pytest.approx(half)
            assert abs(end[0] - end_module.x_m) == pytest.approx(half)
            assert abs(end[1] - end_module.y_m) == pytest.approx(half)
        for fraction in (1.0e-9, 0.25, 0.5, 0.75, 1.0 - 1.0e-9):
            point = (
                start[0] + fraction * (end[0] - start[0]),
                start[1] + fraction * (end[1] - start[1]),
            )
            assert all(
                not (
                    abs(point[0] - module.x_m) < half
                    and abs(point[1] - module.y_m) < half
                )
                for module in layout.modules
            )


def test_topology_is_independent_of_physical_pitch_and_penetration_fails_closed(
    tmp_path: Path,
) -> None:
    default = generate_proposed_led_layout(
        10, 10, proposed_ring_mode="reduced_one_ring"
    )
    compact = generate_smd_layout(
        SmdLayoutConfig(
            room_length_ft=10,
            room_width_ft=10,
            fixed_pitch_m=0.2,
            proposed_ring_mode="reduced_one_ring",
        )
    )
    assert default.alignment_lattice is not None
    assert compact.alignment_lattice is not None
    assert [
        (link.kind, link.start_module_index, link.end_module_index)
        for link in default.alignment_lattice.links
    ] == [
        (link.kind, link.start_module_index, link.end_module_index)
        for link in compact.alignment_lattice.links
    ]

    identity = proposed_layout_transport_payload(default)
    penetrated = deepcopy(identity)
    penetrated["alignment_lattice"]["links"][0]["start_xyz_m"][0] -= 0.001  # type: ignore[index]
    with pytest.raises(ValueError, match="free-span endpoints"):
        plan_fixture_occlusion(
            system_id="proposed",
            layout_identity=penetrated,
            output_directory=tmp_path / "penetrated",
        )


def test_attachment_plane_exact_diameter_material_and_radiance_geometry(
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(10, 10)
    lattice = layout.alignment_lattice
    assert lattice is not None
    plan = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=tmp_path / "occlusion",
    )
    payload = plan.scientific_payload()

    assert lattice.attachment_plane_z_m == (
        layout.modules[0].z_m + AUTHENTICATED_REAR_PLANE_OFFSET_M
    )
    assert plan.shapes[0].authenticated_full_glb_bounds_m[1][2] == (
        AUTHENTICATED_REAR_PLANE_OFFSET_M
    )
    assert payload["alignment_lattice"] == lattice.to_payload()
    alignment = payload["alignment_lattice"]
    assert alignment["attachment_policy"]["id"] == (  # type: ignore[index]
        ALIGNMENT_LATTICE_ATTACHMENT_POLICY_ID
    )
    assert payload["alignment_lattice"]["diameter_m"] == (  # type: ignore[index]
        ALIGNMENT_LATTICE_DIAMETER_M
    )
    assert payload["alignment_lattice"]["material_id"] == (  # type: ignore[index]
        FIXTURE_BODY_MATERIAL_ID
    )
    assert "void metal fixture_body_anodized_aluminum" in plan.instance_source_text
    cylinder_lines = [
        line
        for line in plan.instance_source_text.splitlines()
        if line.startswith(f"{FIXTURE_BODY_MATERIAL_NAME} cylinder alignment_link_")
    ]
    radiance_lines = plan.instance_source_text.splitlines()
    radii = [
        float(radiance_lines[index + 3].split()[-1])
        for index, line in enumerate(radiance_lines)
        if line.startswith(f"{FIXTURE_BODY_MATERIAL_NAME} cylinder alignment_link_")
    ]
    assert len(cylinder_lines) == len(radii) == len(lattice.links)
    assert set(radii) == {ALIGNMENT_LATTICE_RADIUS_M}


def test_viewer_and_radiance_share_identical_link_payload(tmp_path: Path) -> None:
    layout = generate_proposed_led_layout(
        10, 10, proposed_ring_mode="reduced_one_ring"
    )
    identity = proposed_layout_transport_payload(layout)
    occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=identity,
        output_directory=tmp_path / "occlusion",
    )
    publication = build_fixture_publication(
        run_id="a" * 32,
        system_id="proposed",
        requested_length_ft=10,
        requested_width_ft=10,
        layout_identity=identity,
    )
    catalog = json.loads(publication.catalog.data)

    assert catalog["alignment_lattice"] == occlusion.scientific_payload()[
        "alignment_lattice"
    ]
    assert catalog["alignment_lattice_identity_sha256"] == (
        layout.alignment_lattice.identity_sha256  # type: ignore[union-attr]
    )
    assert catalog["fixture_count"] == len(layout.fixtures) == len(layout.modules)


def test_cache_identities_include_lattice_occlusion_and_historical_modes_are_isolated(
    tmp_path: Path,
) -> None:
    standalone = generate_proposed_led_layout(8, 8)
    basis = plan_isolated_rtrace_basis(
        layout=standalone,
        sensor_count=4,
        room_height_m=3.048,
        room_source_path=tmp_path / "room.rad",
        sensor_input_path=tmp_path / "sensors.pts",
        output_directory=tmp_path / "basis",
    )
    uniform = plan_uniform_proposed_stage_a(
        layout=standalone,
        output_directory=tmp_path / "uniform",
        radiance_options=("-ab", "0"),
    )
    occlusion_prefix = basis.fixture_occlusion.identity_sha256[:16]
    assert all(
        column.ambient_cache_path is not None
        and occlusion_prefix in column.ambient_cache_path.name
        for column in basis.columns
    )
    assert uniform.paths.ambient_cache is not None
    assert (
        uniform.fixture_occlusion.identity_sha256[:16]
        in uniform.paths.ambient_cache.name
    )

    for mode in ("linear", "legacy"):
        historical = generate_proposed_led_layout(
            10, 10, proposed_layout_mode=mode
        )
        payload = proposed_layout_transport_payload(historical)
        assert historical.alignment_lattice is None
        assert "alignment_lattice" not in payload
        historical_plan = plan_fixture_occlusion(
            system_id="proposed",
            layout_identity=payload,
            output_directory=tmp_path / mode,
        )
        assert historical_plan.alignment_lattice is None
        assert "alignment_link_" not in historical_plan.instance_source_text


def _connected_module_count(layout: object) -> int:
    lattice = layout.alignment_lattice
    neighbors = defaultdict(set)
    for link in lattice.links:
        neighbors[link.start_module_index].add(link.end_module_index)
        neighbors[link.end_module_index].add(link.start_module_index)
    visited = {0}
    pending = [0]
    while pending:
        for neighbor in neighbors[pending.pop()]:
            if neighbor not in visited:
                visited.add(neighbor)
                pending.append(neighbor)
    return len(visited)


def _assert_no_unrelated_crossings(layout: object) -> None:
    lattice = layout.alignment_lattice
    centers = {
        module.module_index: (module.x_m, module.y_m) for module in layout.modules
    }
    for index, first in enumerate(lattice.links):
        first_members = {first.start_module_index, first.end_module_index}
        a, b = centers[first.start_module_index], centers[first.end_module_index]
        for second in lattice.links[index + 1 :]:
            if first_members & {second.start_module_index, second.end_module_index}:
                continue
            c = centers[second.start_module_index]
            d = centers[second.end_module_index]
            assert not _properly_intersects(a, b, c, d)


def _properly_intersects(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    def side(
        left: tuple[float, float],
        right: tuple[float, float],
        point: tuple[float, float],
    ) -> float:
        return (right[0] - left[0]) * (point[1] - left[1]) - (
            right[1] - left[1]
        ) * (point[0] - left[0])

    return side(a, b, c) * side(a, b, d) < 0.0 and side(c, d, a) * side(
        c, d, b
    ) < 0.0
