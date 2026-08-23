"""Authoritative Proposed fixture assemblies for modular layout points."""

from __future__ import annotations

import math

from .domain import (
    ControlZone,
    FixtureAssembly,
    FixtureConnector,
    LayoutTopology,
    LayoutPoint,
)

from .mode import ProposedLayoutMode, resolve_proposed_layout_mode

LEGACY_FIXTURE_POLICY_ID = "proposed_tile_fill_connect_fixture_assemblies_v1"
LINEAR_FIXTURE_POLICY_ID = "proposed_linear_fixture_assemblies_v1"
STANDALONE_FIXTURE_POLICY_ID = (
    "proposed_standalone_module_instances_with_alignment_lattice_v2"
)
# Historical import retained for callers that explicitly mean the legacy policy.
FIXTURE_POLICY_ID = LEGACY_FIXTURE_POLICY_ID

# Declared square-ring fixture types in topology order. These are fixture
# assemblies, not solver/control zones.
_SQUARE_RING_TYPES = {
    1: ("reverse_L", "reverse_L"),
    2: ("linear3", "linear3", "linear3", "linear3"),
    3: ("L", "L", "L", "L"),
    4: ("linear3", "linear3", "L", "reverse_L", "linear3", "linear3"),
    5: ("L", "linear3", "linear3", "linear3", "linear4", "reverse_L", "linear3"),
    6: ("L", "linear4", "linear4", "linear3", "linear4", "linear3", "linear3", "linear3"),
    7: ("linear4",) * 8,
    8: ("linear4", "linear4", "L", "linear4", "reverse_L", "linear4", "linear4", "linear4", "linear4"),
}
_FIXTURE_SIZE = {
    "centerpiece": 5,
    "linear2": 2,
    "linear3": 3,
    "linear4": 4,
    "L": 4,
    "reverse_L": 4,
}
_DISPLAY_TOPOLOGY_REL_TOL = 1.0e-5


def build_fixture_assemblies(
    grouped: list[tuple[str, tuple[LayoutPoint, ...]]],
) -> tuple[FixtureAssembly, ...]:
    """Finalize fixture groups authored directly by the layout construction."""

    return tuple(
        FixtureAssembly(
            fixture_index=index,
            fixture_type=fixture_type,
            display_fixture_type=_display_fixture_type(fixture_type, points),
            orientation=_orientation_label(fixture_type, points),
            points=points,
            connectors=_connector_primitives(fixture_type, points),
        )
        for index, (fixture_type, points) in enumerate(grouped)
    )


def fixture_policy_id(mode: ProposedLayoutMode | str) -> str:
    """Return the stable fixture-policy identity for one resolved mode."""

    resolved = resolve_proposed_layout_mode(mode)
    if resolved is ProposedLayoutMode.STANDALONE_MODULES:
        return STANDALONE_FIXTURE_POLICY_ID
    if resolved is ProposedLayoutMode.LINEAR:
        return LINEAR_FIXTURE_POLICY_ID
    return LEGACY_FIXTURE_POLICY_ID


def build_standalone_fixture_groups(
    points: tuple[LayoutPoint, ...],
) -> list[tuple[str, tuple[LayoutPoint, ...]]]:
    """Create one rigid standalone fixture per authoritative module."""

    return [("standalone_module", (point,)) for point in points]


def build_linear_fixture_groups(
    control_zones: tuple[ControlZone, ...],
    topology: LayoutTopology,
) -> list[tuple[str, tuple[LayoutPoint, ...]]]:
    """Compose invariant topology using centerpiece, linear2, and linear3 only."""

    grouped: list[tuple[str, tuple[LayoutPoint, ...]]] = []
    consumed_zone_indices: set[int] = set()
    for zone in control_zones:
        if zone.index in consumed_zone_indices:
            continue
        if zone.kind == "square_centerpiece":
            grouped.append(("centerpiece", zone.points))
            continue
        if (
            topology.has_rectangular_extension
            and topology.rectangular_offset == 0
            and zone.kind == "rectangular_center"
        ):
            first_ring = _following_zone(control_zones, zone.index)
            if (
                len(zone.points) != 1
                or first_ring.kind != "rectangular_ring"
                or len(first_ring.points) != 4
            ):
                raise ValueError(
                    "offset-zero rectangular topology is missing its five-module cross."
                )
            grouped.append(("centerpiece", zone.points + first_ring.points))
            consumed_zone_indices.add(first_ring.index)
            continue
        grouped.extend(partition_linear_prefer_pairs(zone.points))
    return grouped


def partition_linear_prefer_pairs(
    points: tuple[LayoutPoint, ...],
) -> list[tuple[str, tuple[LayoutPoint, ...]]]:
    """Partition an ordered row/ring into adjacent pairs and at most one triple."""

    count = len(points)
    if count == 0:
        return []
    if count == 1:
        raise ValueError("a fixture assembly cannot contain one isolated module.")
    if count % 2 == 0:
        return [
            ("linear2", points[index : index + 2])
            for index in range(0, count, 2)
        ]
    if count < 3:
        raise ValueError("an odd linear fixture group requires at least three modules.")
    for triple_start in range(0, count - 2, 2):
        triple = points[triple_start : triple_start + 3]
        if _points_are_collinear(triple):
            grouped = [
                ("linear2", points[index : index + 2])
                for index in range(0, triple_start, 2)
            ]
            grouped.append(("linear3", triple))
            grouped.extend(
                ("linear2", points[index : index + 2])
                for index in range(triple_start + 3, count, 2)
            )
            return grouped
    raise ValueError(
        "odd fixture topology has no contiguous three-module collinear segment."
    )


def _following_zone(
    control_zones: tuple[ControlZone, ...],
    zone_index: int,
) -> ControlZone:
    following_index = zone_index + 1
    if following_index >= len(control_zones):
        raise ValueError("rectangular centerpiece ring is missing.")
    return control_zones[following_index]


def _points_are_collinear(points: tuple[LayoutPoint, ...]) -> bool:
    if len(points) != 3:
        return False
    first, middle, last = points
    incoming = (middle.x - first.x, middle.y - first.y)
    outgoing = (last.x - middle.x, last.y - middle.y)
    return _vectors_are_collinear(incoming, outgoing)


def _display_fixture_type(
    fixture_type: str,
    points: tuple[LayoutPoint, ...],
) -> str:
    """Resolve display geometry from authoritative member topology."""

    return resolve_ordered_display_fixture_type(
        fixture_type,
        tuple((point.x, point.y) for point in points),
    )


def resolve_ordered_display_fixture_type(
    fixture_type: str,
    ordered_points: tuple[tuple[float, float], ...],
) -> str:
    """Select immutable display geometry from ordered member topology."""

    expected_count = {
        "standalone_module": 1,
        "centerpiece": 5,
        "linear2": 2,
        "linear3": 3,
        "linear4": 4,
        "L": 4,
        "reverse_L": 4,
    }.get(fixture_type)
    if expected_count is None:
        raise ValueError("Proposed scientific fixture type is unsupported.")
    if len(ordered_points) != expected_count:
        raise ValueError(
            f"{fixture_type} fixture assemblies must contain {expected_count} modules."
        )
    if fixture_type in {"standalone_module", "centerpiece", "linear2"}:
        return fixture_type
    segments = tuple(
        (right[0] - left[0], right[1] - left[1])
        for left, right in zip(ordered_points, ordered_points[1:])
    )
    if any(_vector_length(segment) <= 1.0e-12 for segment in segments):
        raise ValueError("Proposed display fixture contains a zero-length member edge.")
    if expected_count == 3:
        if _vectors_are_collinear(segments[0], segments[1]):
            return "linear3"
        if _vectors_are_perpendicular(segments[0], segments[1]):
            return "corner3"
    else:
        if all(
            _vectors_are_collinear(segments[0], segment)
            for segment in segments[1:]
        ):
            return "linear4"
        if (
            _vectors_are_perpendicular(segments[0], segments[1])
            and _vectors_are_collinear(segments[1], segments[2])
        ):
            return "L"
        if (
            _vectors_are_collinear(segments[0], segments[1])
            and _vectors_are_perpendicular(segments[1], segments[2])
        ):
            return "reverse_L"
    raise ValueError(
        "Proposed display assembly must match its collinear or right-angle "
        "member topology."
    )


def _vector_length(vector: tuple[float, float]) -> float:
    return math.hypot(*vector)


def _vectors_are_collinear(
    first: tuple[float, float], second: tuple[float, float]
) -> bool:
    return abs(first[0] * second[1] - first[1] * second[0]) <= (
        _DISPLAY_TOPOLOGY_REL_TOL
        * _vector_length(first)
        * _vector_length(second)
    )


def _vectors_are_perpendicular(
    first: tuple[float, float], second: tuple[float, float]
) -> bool:
    return abs(first[0] * second[0] + first[1] * second[1]) <= (
        _DISPLAY_TOPOLOGY_REL_TOL
        * _vector_length(first)
        * _vector_length(second)
    )


def partition_square_ring(
    points: tuple[LayoutPoint, ...], local_ring: int
) -> list[tuple[str, tuple[LayoutPoint, ...]]]:
    fixture_types = _SQUARE_RING_TYPES.get(local_ring)
    if fixture_types is None:
        return partition_perimeter(points)
    expected = sum(_FIXTURE_SIZE[fixture_type] for fixture_type in fixture_types)
    if expected != len(points):
        raise ValueError("square-ring fixture policy does not cover its points.")
    grouped: list[tuple[str, tuple[LayoutPoint, ...]]] = []
    cursor = 0
    for fixture_type in fixture_types:
        size = _FIXTURE_SIZE[fixture_type]
        grouped.append((fixture_type, points[cursor : cursor + size]))
        cursor += size
    return grouped


def partition_linear(
    points: tuple[LayoutPoint, ...]
) -> list[tuple[str, tuple[LayoutPoint, ...]]]:
    sizes = _linear_sizes(len(points))
    grouped: list[tuple[str, tuple[LayoutPoint, ...]]] = []
    cursor = 0
    for size in sizes:
        grouped.append((f"linear{size}", points[cursor : cursor + size]))
        cursor += size
    return grouped


def _linear_sizes(count: int) -> tuple[int, ...]:
    if count == 0:
        return ()
    if count == 1:
        raise ValueError("a fixture assembly cannot contain one isolated module.")
    fours, remainder = divmod(count, 4)
    if remainder == 0:
        return (4,) * fours
    if remainder == 1:
        if fours == 0:
            raise ValueError("five or more points are required for remainder-one fill.")
        return (4,) * (fours - 1) + (3, 2)
    if remainder == 2:
        return (4,) * fours + (2,)
    return (4,) * fours + (3,)


def partition_perimeter(
    points: tuple[LayoutPoint, ...]
) -> list[tuple[str, tuple[LayoutPoint, ...]]]:
    count = len(points)
    corners = _corner_indices(points)
    best_count: list[int | None] = [None] * (count + 1)
    best_type: list[str | None] = [None] * count
    best_count[count] = 0
    for position in range(count - 1, -1, -1):
        candidates: list[tuple[int, str]] = []
        for size in (4, 3, 2):
            end = position + size
            if end > count or best_count[end] is None:
                continue
            middle = set(range(position + 1, end))
            if not middle.intersection(corners):
                candidates.append((1 + best_count[end], f"linear{size}"))
        end = position + 4
        if end <= count and best_count[end] is not None:
            first_corner = position + 1 in corners
            second_corner = position + 2 in corners
            if first_corner and not second_corner:
                candidates.append((1 + best_count[end], "L"))
            if second_corner and not first_corner:
                candidates.append((1 + best_count[end], "reverse_L"))
        if candidates:
            selected_count, selected_type = min(candidates)
            best_count[position] = selected_count
            best_type[position] = selected_type
    if best_count[0] is None:
        raise ValueError("perimeter cannot be partitioned into Proposed fixtures.")
    grouped: list[tuple[str, tuple[LayoutPoint, ...]]] = []
    cursor = 0
    while cursor < count:
        fixture_type = best_type[cursor]
        if fixture_type is None:
            raise ValueError("perimeter fixture reconstruction is incomplete.")
        size = _FIXTURE_SIZE[fixture_type]
        grouped.append((fixture_type, points[cursor : cursor + size]))
        cursor += size
    return grouped


def _corner_indices(points: tuple[LayoutPoint, ...]) -> set[int]:
    corners: set[int] = set()
    count = len(points)
    for index in range(count):
        previous = points[(index - 1) % count]
        current = points[index]
        following = points[(index + 1) % count]
        incoming = (current.x - previous.x, current.y - previous.y)
        outgoing = (following.x - current.x, following.y - current.y)
        if incoming[0] * outgoing[1] != incoming[1] * outgoing[0]:
            corners.add(index)
    return corners


def _connector_primitives(
    fixture_type: str,
    points: tuple[LayoutPoint, ...],
) -> tuple[FixtureConnector, ...]:
    pairs: list[tuple[LayoutPoint, LayoutPoint]] = []
    if fixture_type == "standalone_module":
        return ()
    if fixture_type == "centerpiece":
        pairs.extend((points[0], point) for point in points[1:])
    elif fixture_type.startswith("linear"):
        pairs.extend(zip(points, points[1:]))
    elif fixture_type == "L":
        pairs.extend(zip(points[1:4], points[2:4]))
        pairs.append((points[0], points[1]))
    elif fixture_type == "reverse_L":
        pairs.extend(zip(points[0:3], points[1:3]))
        pairs.append((points[2], points[3]))
    else:
        raise ValueError(f"unsupported fixture type: {fixture_type}")
    return tuple(FixtureConnector(start, end) for start, end in pairs)


def _orientation_label(
    fixture_type: str,
    points: tuple[LayoutPoint, ...],
) -> str:
    connectors = _connector_primitives(fixture_type, points)
    if not connectors:
        return "fixed"
    first = connectors[0]
    dx = first.end.x - first.start.x
    dy = first.end.y - first.start.y
    return f"lattice_dx_{dx:g}_dy_{dy:g}"
