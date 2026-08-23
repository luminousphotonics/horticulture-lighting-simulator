"""Immutable domain objects for modular control-zone layouts."""

from __future__ import annotations

from dataclasses import dataclass

from .ring import ProposedRingMode


@dataclass(frozen=True, order=True, slots=True)
class LayoutPoint:
    """A point on the dimensionless modular-layout lattice."""

    x: float
    y: float


@dataclass(frozen=True, slots=True)
class ControlZone:
    """A group of module points sharing one solver coefficient."""

    index: int
    kind: str
    points: tuple[LayoutPoint, ...]


@dataclass(frozen=True, slots=True)
class FixtureConnector:
    """One authoritative connector between two fixture member points."""

    start: LayoutPoint
    end: LayoutPoint


@dataclass(frozen=True, slots=True)
class FixtureAssembly:
    """One Proposed group with separate scientific and display types."""

    fixture_index: int
    fixture_type: str
    display_fixture_type: str
    orientation: str
    points: tuple[LayoutPoint, ...]
    connectors: tuple[FixtureConnector, ...]


@dataclass(frozen=True, slots=True)
class LayoutTopology:
    """Discrete construction metadata for a modular layout."""

    base_n: int
    nominal_base_n: int
    proposed_ring_mode: ProposedRingMode
    module_pattern_id: str
    square_tile_count: int
    connector_count: int
    has_rectangular_extension: bool
    rectangular_long_ft: float
    rectangular_offset: int
    control_zone_count: int


@dataclass(frozen=True, slots=True)
class ModularLayout:
    """Dimensionless module points and their control-zone partition."""

    positions: tuple[LayoutPoint, ...]
    control_zones: tuple[ControlZone, ...]
    fixture_assemblies: tuple[FixtureAssembly, ...]
    topology: LayoutTopology

    def __post_init__(self) -> None:
        expected = tuple(range(len(self.control_zones)))
        actual = tuple(zone.index for zone in self.control_zones)
        if actual != expected:
            raise ValueError("control-zone indices must be contiguous and zero-based.")
        if self.topology.control_zone_count != len(self.control_zones):
            raise ValueError("topology control-zone count does not match the layout.")
        if len(self.positions) != len(set(self.positions)):
            raise ValueError("module positions must be unique.")
        fixture_indices = tuple(
            fixture.fixture_index for fixture in self.fixture_assemblies
        )
        if fixture_indices != tuple(range(len(self.fixture_assemblies))):
            raise ValueError("fixture indices must be contiguous and zero-based.")
        fixture_points = tuple(
            point
            for fixture in self.fixture_assemblies
            for point in fixture.points
        )
        if len(fixture_points) != len(set(fixture_points)):
            raise ValueError("each module point must belong to exactly one fixture.")
        if set(fixture_points) != set(self.positions):
            raise ValueError("fixture membership must cover every module position.")
        for fixture in self.fixture_assemblies:
            members = set(fixture.points)
            if not members:
                raise ValueError("fixture assemblies must contain module points.")
            if any(
                connector.start not in members
                or connector.end not in members
                or connector.start == connector.end
                for connector in fixture.connectors
            ):
                raise ValueError(
                    "fixture connectors must join two distinct member points."
                )

    @property
    def control_zone_count(self) -> int:
        return len(self.control_zones)

    @property
    def coefficient_count(self) -> int:
        """Number of basis coefficients required by this layout."""

        return self.control_zone_count
