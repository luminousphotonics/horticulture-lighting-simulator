"""Pure Proposed LED modular-layout construction.

This is the default production tile/fill/connect topology.  Its public API uses
``control_zones``; the geometric construction retains the historical notion of
concentric local rings only as an implementation detail.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from .domain import ControlZone, LayoutPoint, LayoutTopology, ModularLayout
from .fixture_plan import (
    build_linear_fixture_groups,
    build_fixture_assemblies,
    build_standalone_fixture_groups,
    partition_linear,
    partition_perimeter,
    partition_square_ring,
)
from .mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    ProposedLayoutMode,
    resolve_proposed_layout_mode,
)
from .ring import (
    DEFAULT_PROPOSED_RING_MODE,
    ProposedRingMode,
    effective_topology_order,
    proposed_module_pattern_id,
    resolve_proposed_ring_mode,
)

MAX_LAYOUT_DIMENSION_FT = 10_000.0


def generate_modular_layout(
    length_ft: float,
    width_ft: float,
    *,
    layout_mode: ProposedLayoutMode | str = DEFAULT_PROPOSED_LAYOUT_MODE,
    ring_mode: ProposedRingMode | str = DEFAULT_PROPOSED_RING_MODE,
) -> ModularLayout:
    """Generate dimensionless module positions and layout-dependent zones."""

    resolved_mode = resolve_proposed_layout_mode(layout_mode)
    resolved_ring_mode = resolve_proposed_ring_mode(ring_mode)
    if (
        resolved_ring_mode is ProposedRingMode.REDUCED_ONE_RING
        and resolved_mode is not ProposedLayoutMode.STANDALONE_MODULES
    ):
        raise ValueError(
            "reduced_one_ring is supported only with the standalone_modules "
            "Proposed physical-composition mode."
        )
    length = _dimension("length_ft", length_ft)
    width = _dimension("width_ft", width_ft)
    short_ft = min(length, width)
    long_ft = max(length, width)
    nominal_base_n = _base_n(short_ft)
    base_n = effective_topology_order(nominal_base_n, resolved_ring_mode)
    square_tiles, has_rectangle, rectangular_long_ft = _partition_long_axis(
        short_ft, long_ft
    )

    zones: list[ControlZone] = []
    fixture_groups: list[tuple[str, tuple[LayoutPoint, ...]]] = []
    build_historical_fixture_groups = (
        resolved_mode is not ProposedLayoutMode.STANDALONE_MODULES
    )
    shift_step = base_n + 1
    for tile_index in range(square_tiles):
        shift = tile_index * shift_step
        _append_square_tile(
            zones,
            fixture_groups,
            base_n,
            shift,
            shift,
            build_fixture_groups=build_historical_fixture_groups,
        )

    for connector_index in range(max(0, square_tiles - 1)):
        shift = connector_index * shift_step
        connector_points = _connector_points(base_n, shift, shift)
        _append_zone(
            zones,
            "connector",
            connector_points,
        )
        if build_historical_fixture_groups:
            fixture_groups.extend(partition_linear(connector_points))

    rectangular_offset = 0
    connector_count = max(0, square_tiles - 1)
    if has_rectangle:
        rectangular_n = effective_topology_order(
            _base_n(rectangular_long_ft),
            resolved_ring_mode,
        )
        rectangular_offset = rectangular_n - base_n
        if rectangular_offset % 2 == 1:
            rectangular_offset -= 1

        if square_tiles > 0:
            shift = (square_tiles - 1) * shift_step
            connector_points = _connector_points(base_n, shift, shift)
            _append_zone(
                zones,
                "connector",
                connector_points,
            )
            if build_historical_fixture_groups:
                fixture_groups.extend(partition_linear(connector_points))
            connector_count += 1
            last_connector_u = (
                base_n + 1 + (square_tiles - 1) * 2 * (base_n + 1)
            )
            shift_u = last_connector_u + 1 + rectangular_offset + base_n
        else:
            shift_u = 0

        center = _transform_rectangular(
            _rectangular_center_points(rectangular_offset),
            shift_u=shift_u,
        )
        if center:
            _append_zone(zones, "rectangular_center", center)
            if build_historical_fixture_groups and rectangular_offset > 0:
                fixture_groups.extend(partition_linear(center))
        for local_ring in range(1, base_n + 1):
            ring_points = _transform_rectangular(
                _rectangular_ring_points(local_ring, rectangular_offset),
                shift_u=shift_u,
            )
            _append_zone(
                zones,
                "rectangular_ring",
                ring_points,
            )
            if not build_historical_fixture_groups:
                continue
            if rectangular_offset == 0 and local_ring == 1:
                fixture_groups.append(("centerpiece", center + ring_points))
            elif rectangular_offset == 0:
                fixture_groups.extend(
                    partition_square_ring(ring_points, local_ring - 1)
                )
            else:
                fixture_groups.extend(partition_perimeter(ring_points))

    ordered_positions = _deduplicate(
        point for zone in zones for point in zone.points
    )
    topology = LayoutTopology(
        base_n=base_n,
        nominal_base_n=nominal_base_n,
        proposed_ring_mode=resolved_ring_mode,
        module_pattern_id=proposed_module_pattern_id(resolved_ring_mode),
        square_tile_count=square_tiles,
        connector_count=connector_count,
        has_rectangular_extension=has_rectangle,
        rectangular_long_ft=rectangular_long_ft if has_rectangle else 0.0,
        rectangular_offset=rectangular_offset,
        control_zone_count=len(zones),
    )
    if resolved_mode is ProposedLayoutMode.STANDALONE_MODULES:
        resolved_fixture_groups = build_standalone_fixture_groups(
            tuple(ordered_positions)
        )
    elif resolved_mode is ProposedLayoutMode.LINEAR:
        resolved_fixture_groups = build_linear_fixture_groups(
            tuple(zones), topology
        )
    else:
        resolved_fixture_groups = fixture_groups
    return ModularLayout(
        positions=tuple(ordered_positions),
        control_zones=tuple(zones),
        fixture_assemblies=build_fixture_assemblies(resolved_fixture_groups),
        topology=topology,
    )


def _base_n(dimension_ft: float) -> int:
    if dimension_ft <= 4.0:
        return 2
    return math.floor((dimension_ft - 4.0) / 2.0) + 2


def _partition_long_axis(
    short_ft: float, long_ft: float
) -> tuple[int, bool, float]:
    connector_ft = 2.0
    unit_ft = short_ft + connector_ft
    minimum_rectangle_ft = short_ft + 4.0
    square_tiles = math.floor((long_ft + connector_ft) / unit_ft)
    while square_tiles >= 0:
        if square_tiles == 0:
            return 0, True, long_ft
        pure_length = short_ft * square_tiles + connector_ft * (square_tiles - 1)
        remainder = long_ft - pure_length
        if abs(remainder) <= 1e-12:
            return square_tiles, False, 0.0
        if remainder > connector_ft and remainder - connector_ft >= minimum_rectangle_ft:
            return square_tiles, True, remainder - connector_ft
        square_tiles -= 1
    raise ValueError("room dimensions could not be partitioned into a modular layout.")


def _append_square_tile(
    zones: list[ControlZone],
    fixture_groups: list[tuple[str, tuple[LayoutPoint, ...]]],
    base_n: int,
    shift_x: float,
    shift_y: float,
    *,
    build_fixture_groups: bool,
) -> None:
    centerpiece = (
        LayoutPoint(shift_x, shift_y),
        LayoutPoint(1.0 + shift_x, shift_y),
        LayoutPoint(shift_x, 1.0 + shift_y),
        LayoutPoint(-1.0 + shift_x, shift_y),
        LayoutPoint(shift_x, -1.0 + shift_y),
    )
    _append_zone(zones, "square_centerpiece", centerpiece)
    if build_fixture_groups:
        fixture_groups.append(("centerpiece", centerpiece))
    for local_ring in range(1, base_n):
        ring_points = tuple(
            LayoutPoint(point.x + shift_x, point.y + shift_y)
            for point in _square_ring_points(local_ring)
        )
        _append_zone(
            zones,
            "square_ring",
            ring_points,
        )
        if build_fixture_groups:
            fixture_groups.extend(partition_square_ring(ring_points, local_ring))


def _square_ring_points(local_ring: int) -> tuple[LayoutPoint, ...]:
    extent = local_ring + 1
    points: list[LayoutPoint] = []
    for index in range(extent + 1):
        points.append(LayoutPoint(float(index), float(extent - index)))
    for index in range(1, extent + 1):
        points.append(LayoutPoint(float(extent - index), float(-index)))
    for index in range(1, extent + 1):
        points.append(LayoutPoint(float(-index), float(-extent + index)))
    for index in range(1, extent + 1):
        points.append(LayoutPoint(float(-extent + index), float(index)))
    return tuple(_deduplicate(points))


def _connector_points(
    base_n: int, shift_x: float, shift_y: float
) -> tuple[LayoutPoint, ...]:
    extension = base_n + 1
    lower = 1
    upper = math.floor((2 * base_n + 1) / 2)
    return tuple(
        LayoutPoint(float(value) + shift_x, float(extension - value) + shift_y)
        for value in range(lower, upper + 1)
    )


def _rectangular_center_points(offset: int) -> tuple[LayoutPoint, ...]:
    return tuple(
        LayoutPoint(float(value), 0.0)
        for value in range(-offset, offset + 1)
        if value % 2 == 0
    )


def _rectangular_ring_points(
    local_ring: int, offset: int
) -> tuple[LayoutPoint, ...]:
    maximum_u = offset + local_ring
    maximum_v = local_ring
    points: list[LayoutPoint] = []
    for value_v in range(-maximum_v, maximum_v + 1):
        if (-maximum_u + value_v) % 2 == 0:
            points.append(LayoutPoint(float(-maximum_u), float(value_v)))
    for value_u in range(-maximum_u + 1, maximum_u + 1):
        candidate = LayoutPoint(float(value_u), float(maximum_v))
        if (value_u + maximum_v) % 2 == 0 and candidate not in points:
            points.append(candidate)
    for value_v in range(maximum_v - 1, -maximum_v - 1, -1):
        candidate = LayoutPoint(float(maximum_u), float(value_v))
        if (maximum_u + value_v) % 2 == 0 and candidate not in points:
            points.append(candidate)
    for value_u in range(maximum_u - 1, -maximum_u - 1, -1):
        candidate = LayoutPoint(float(value_u), float(-maximum_v))
        if (value_u - maximum_v) % 2 == 0 and candidate not in points:
            points.append(candidate)
    return tuple(points)


def _transform_rectangular(
    points: tuple[LayoutPoint, ...], *, shift_u: float
) -> tuple[LayoutPoint, ...]:
    return tuple(
        LayoutPoint(
            (point.x + shift_u + point.y) / 2.0,
            (point.x + shift_u - point.y) / 2.0,
        )
        for point in points
    )


def _append_zone(
    zones: list[ControlZone],
    kind: str,
    points: Iterable[LayoutPoint],
) -> None:
    zone_points = tuple(points)
    zones.append(ControlZone(index=len(zones), kind=kind, points=zone_points))


def _deduplicate(points: Iterable[LayoutPoint]) -> list[LayoutPoint]:
    ordered: list[LayoutPoint] = []
    seen: set[tuple[float, float]] = set()
    for point in points:
        key = (round(point.x, 6), round(point.y, 6))
        if key not in seen:
            seen.add(key)
            ordered.append(point)
    return ordered


def _dimension(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite positive number.")
    dimension = float(value)
    if (
        not math.isfinite(dimension)
        or dimension <= 0.0
        or dimension > MAX_LAYOUT_DIMENSION_FT
    ):
        raise ValueError(
            f"{name} must be finite, positive, and no greater than "
            f"{MAX_LAYOUT_DIMENSION_FT:g}."
        )
    return dimension
