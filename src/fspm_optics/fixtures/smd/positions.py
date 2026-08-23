"""Scale modular Proposed LED topology into deterministic room positions."""

from __future__ import annotations

from dataclasses import dataclass
import math

from fspm_optics.geometry.coordinate_frame import RoomCoordinateFrame
from fspm_optics.geometry.room import FEET_TO_METERS
from fspm_optics.layout.domain import FixtureAssembly, LayoutTopology
from fspm_optics.layout.fixture_plan import fixture_policy_id
from fspm_optics.layout.mode import (
    DEFAULT_PROPOSED_LAYOUT_MODE,
    ProposedLayoutMode,
)
from fspm_optics.layout.ring import (
    DEFAULT_PROPOSED_RING_MODE,
    ProposedRingMode,
    proposed_module_pattern_id,
)
from fspm_optics.layout.modular import generate_modular_layout
from fspm_optics.layout.overlay import (
    AuthoritativeOverlayPlan,
    OverlayLine,
    OverlayRectangle,
)

from .config import DEFAULT_MOUNT_Z_M, SmdLayoutConfig
from .alignment_lattice import AlignmentLattice, build_alignment_lattice
from .module_profile import DEFAULT_SMD_MODULE_PROFILE


@dataclass(frozen=True, slots=True)
class SmdModulePosition:
    """Center point and shared-coefficient assignment for one SMD module."""

    module_index: int
    x_m: float
    y_m: float
    z_m: float
    control_zone_index: int


@dataclass(frozen=True, slots=True)
class SmdFixtureConnector:
    """One physical connector between authoritative member modules."""

    start_module_index: int
    end_module_index: int


@dataclass(frozen=True, slots=True)
class SmdFixtureAssembly:
    """One physical Proposed group with deterministic display topology."""

    fixture_id: str
    fixture_type: str
    display_fixture_type: str
    source_orientation: str
    orientation_degrees: float
    member_module_indices: tuple[int, ...]
    connectors: tuple[SmdFixtureConnector, ...]


@dataclass(frozen=True, slots=True)
class SmdLayout:
    """Physical Proposed LED module positions for one room."""

    modules: tuple[SmdModulePosition, ...]
    fixtures: tuple[SmdFixtureAssembly, ...]
    proposed_layout_mode: ProposedLayoutMode
    proposed_ring_mode: ProposedRingMode
    module_pattern_id: str
    fixture_policy_id: str
    mechanical_envelope_id: str
    module_footprint_x_m: float
    module_footprint_y_m: float
    fixture_asset_set_id: str
    topology: LayoutTopology
    room_length_m: float
    room_width_m: float
    pitch_x_m: float
    pitch_y_m: float
    axes_swapped: bool
    alignment_lattice: AlignmentLattice | None

    @property
    def control_zone_count(self) -> int:
        return self.topology.control_zone_count

    @property
    def coefficient_count(self) -> int:
        """One basis-matrix coefficient is required per control zone."""

        return self.control_zone_count

    @property
    def control_zone_indices(self) -> tuple[int, ...]:
        return tuple(range(self.control_zone_count))

    def authoritative_overlay_plan(self) -> AuthoritativeOverlayPlan:
        """Emit complete module/connector geometry for the shared renderer."""

        rectangles = tuple(
            OverlayRectangle(
                primitive_id=f"proposed-module-{module.module_index:04d}",
                fixture_id=_fixture_id_for_module(self.fixtures, module.module_index),
                primitive_kind="emitter_window",
                center_x_m=module.x_m,
                center_y_m=module.y_m,
                width_x_m=DEFAULT_SMD_MODULE_PROFILE.window_side_m,
                height_y_m=DEFAULT_SMD_MODULE_PROFILE.window_side_m,
                orientation_degrees=0.0,
                color_group_index=module.control_zone_index,
                stroke_color_role="color_group",
                stroke_width_px=2,
            )
            for module in self.modules
        )
        fixture_lines = tuple(
            OverlayLine(
                primitive_id=(
                    f"{fixture.fixture_id}-connector-{connector_index:03d}"
                ),
                fixture_id=fixture.fixture_id,
                primitive_kind="physical_connector",
                start_x_m=self.modules[connector.start_module_index].x_m,
                start_y_m=self.modules[connector.start_module_index].y_m,
                end_x_m=self.modules[connector.end_module_index].x_m,
                end_y_m=self.modules[connector.end_module_index].y_m,
                color_group_index=0,
                stroke_color_role="plot_border",
                stroke_width_px=1,
            )
            for fixture in self.fixtures
            for connector_index, connector in enumerate(fixture.connectors)
        )
        alignment_lines = (
            ()
            if self.alignment_lattice is None
            else tuple(
                OverlayLine(
                    primitive_id=f"alignment-link-{link.link_index:05d}",
                    fixture_id="proposed-standalone-alignment-lattice",
                    primitive_kind="alignment_lattice_link",
                    start_x_m=link.start_xyz_m[0],
                    start_y_m=link.start_xyz_m[1],
                    end_x_m=link.end_xyz_m[0],
                    end_y_m=link.end_xyz_m[1],
                    color_group_index=0,
                    stroke_color_role="plot_border",
                    stroke_width_px=1,
                )
                for link in self.alignment_lattice.links
            )
        )
        fixtures = tuple(
            {
                "fixture_id": fixture.fixture_id,
                "fixture_type": fixture.fixture_type,
                "display_fixture_type": fixture.display_fixture_type,
                "source_orientation": fixture.source_orientation,
                "orientation_degrees": fixture.orientation_degrees,
                "member_module_indices": list(fixture.member_module_indices),
                "connectors": [
                    {
                        "start_module_index": connector.start_module_index,
                        "end_module_index": connector.end_module_index,
                    }
                    for connector in fixture.connectors
                ],
            }
            for fixture in self.fixtures
        )
        return AuthoritativeOverlayPlan(
            system_id="proposed",
            policy_id=self.fixture_policy_id,
            coordinate_source="run manifest layout.modules",
            room_length_m=self.room_length_m,
            room_width_m=self.room_width_m,
            axes_swapped=self.axes_swapped,
            rectangles=rectangles,
            lines=fixture_lines + alignment_lines,
            fixture_metadata=fixtures,
            metadata={
                "proposed_layout_mode": self.proposed_layout_mode.value,
                "proposed_ring_mode": self.proposed_ring_mode.value,
                "module_pattern_id": self.module_pattern_id,
                "fixture_policy_id": self.fixture_policy_id,
                "module_count": len(self.modules),
                "control_zone_count": self.control_zone_count,
                "module_shape": "authoritative emitter window square",
                "module_width_m": DEFAULT_SMD_MODULE_PROFILE.window_side_m,
                "module_height_m": DEFAULT_SMD_MODULE_PROFILE.window_side_m,
                "module_centers": "layout.modules x_m/y_m",
                "mechanical_envelope": {
                    "id": self.mechanical_envelope_id,
                    "width_x_m": self.module_footprint_x_m,
                    "height_y_m": self.module_footprint_y_m,
                },
                "fixture_asset_set_id": self.fixture_asset_set_id,
                **(
                    {}
                    if self.alignment_lattice is None
                    else {"alignment_lattice": self.alignment_lattice.to_payload()}
                ),
                "control_zone_assignment": (
                    "layout.modules control_zone_index"
                ),
                "module_control_zone_coloring": True,
                "fixture_connector_source": (
                    "layout.fixtures connectors and alignment_lattice "
                    "free-span endpoints"
                ),
                "control_zone_membership_generates_connectors": False,
            },
        )


def generate_smd_layout(config: SmdLayoutConfig) -> SmdLayout:
    """Generate the default exact-tiled layout without filesystem access."""

    input_length_m = config.room_length_ft * FEET_TO_METERS
    input_width_m = config.room_width_ft * FEET_TO_METERS
    frame = RoomCoordinateFrame(input_length_m, input_width_m)
    axes_swapped = bool(config.align_long_axis_x and frame.axes_swapped)
    if axes_swapped:
        room_length_m = frame.simulation_length_m
        room_width_m = frame.simulation_width_m
        modular = generate_modular_layout(
            config.room_width_ft,
            config.room_length_ft,
            layout_mode=config.proposed_layout_mode,
            ring_mode=config.proposed_ring_mode,
        )
    else:
        room_length_m, room_width_m = input_length_m, input_width_m
        modular = generate_modular_layout(
            config.room_length_ft,
            config.room_width_ft,
            layout_mode=config.proposed_layout_mode,
            ring_mode=config.proposed_ring_mode,
        )

    axis_points = tuple(
        (point.x + point.y, point.x - point.y) for point in modular.positions
    )
    x_values = tuple(point[0] for point in axis_points)
    y_values = tuple(point[1] for point in axis_points)
    span_x = max(x_values) - min(x_values)
    span_y = max(y_values) - min(y_values)
    if span_x <= 0.0 or span_y <= 0.0:
        raise ValueError("modular layout must have positive spans on both axes.")

    fit_x = room_length_m - 2.0 * (
        config.wall_margin_m
        + config.module_footprint_x_m / 2.0
        + config.fixture_clearance_m
    )
    fit_y = room_width_m - 2.0 * (
        config.wall_margin_m
        + config.module_footprint_y_m / 2.0
        + config.fixture_clearance_m
    )
    if fit_x <= 0.0 or fit_y <= 0.0:
        raise ValueError("room is too small for the configured module envelope.")
    pitch_x_m = fit_x / span_x
    pitch_y_m = fit_y / span_y
    if config.fixed_pitch_m is not None:
        pitch_x_m = min(pitch_x_m, config.fixed_pitch_m)
        pitch_y_m = min(pitch_y_m, config.fixed_pitch_m)
    if (
        config.proposed_layout_mode is ProposedLayoutMode.STANDALONE_MODULES
        and math.isclose(room_length_m, room_width_m, rel_tol=0.0, abs_tol=1.0e-12)
        and not math.isclose(pitch_x_m, pitch_y_m, rel_tol=0.0, abs_tol=1.0e-12)
    ):
        raise ValueError("square standalone layouts must resolve equal X/Y pitch.")

    center_x = 0.5 * (min(x_values) + max(x_values))
    center_y = 0.5 * (min(y_values) + max(y_values))
    zone_by_point = {
        (round(point.x, 6), round(point.y, 6)): zone.index
        for zone in modular.control_zones
        for point in zone.points
    }
    modules = tuple(
        SmdModulePosition(
            module_index=index,
            x_m=round((axis_x - center_x) * pitch_x_m, 6),
            y_m=round((axis_y - center_y) * pitch_y_m, 6),
            z_m=config.mount_z_m,
            control_zone_index=zone_by_point[
                (round(point.x, 6), round(point.y, 6))
            ],
        )
        for index, (point, (axis_x, axis_y)) in enumerate(
            zip(modular.positions, axis_points, strict=True)
        )
    )
    module_index_by_point = {
        (round(point.x, 6), round(point.y, 6)): index
        for index, point in enumerate(modular.positions)
    }
    fixtures = tuple(
        _physical_fixture(
            fixture,
            modules,
            module_index_by_point,
        )
        for fixture in modular.fixture_assemblies
    )
    alignment_lattice = (
        build_alignment_lattice(
            modules,
            modular.positions,
            envelope_x_m=config.module_footprint_x_m,
            envelope_y_m=config.module_footprint_y_m,
        )
        if config.proposed_layout_mode is ProposedLayoutMode.STANDALONE_MODULES
        else None
    )
    return SmdLayout(
        modules=modules,
        fixtures=fixtures,
        proposed_layout_mode=config.proposed_layout_mode,
        proposed_ring_mode=config.proposed_ring_mode,
        module_pattern_id=proposed_module_pattern_id(
            config.proposed_ring_mode
        ),
        fixture_policy_id=fixture_policy_id(config.proposed_layout_mode),
        mechanical_envelope_id=config.mechanical_envelope_id,
        module_footprint_x_m=config.module_footprint_x_m,
        module_footprint_y_m=config.module_footprint_y_m,
        fixture_asset_set_id=(
            "proposed-led-module-v1"
            if config.proposed_layout_mode
            is ProposedLayoutMode.STANDALONE_MODULES
            else "historical-proposed-fixture-assets-v1"
        ),
        topology=modular.topology,
        room_length_m=room_length_m,
        room_width_m=room_width_m,
        pitch_x_m=pitch_x_m,
        pitch_y_m=pitch_y_m,
        axes_swapped=axes_swapped,
        alignment_lattice=alignment_lattice,
    )


def _physical_fixture(
    fixture: FixtureAssembly,
    modules: tuple[SmdModulePosition, ...],
    module_index_by_point: dict[tuple[float, float], int],
) -> SmdFixtureAssembly:
    points = fixture.points
    member_indices = tuple(
        module_index_by_point[(round(point.x, 6), round(point.y, 6))]
        for point in points
    )
    connectors = tuple(
        SmdFixtureConnector(
            module_index_by_point[
                (round(connector.start.x, 6), round(connector.start.y, 6))
            ],
            module_index_by_point[
                (round(connector.end.x, 6), round(connector.end.y, 6))
            ],
        )
        for connector in fixture.connectors
    )
    if connectors:
        start = modules[connectors[0].start_module_index]
        end = modules[connectors[0].end_module_index]
        orientation_degrees = round(
            math.degrees(math.atan2(end.y_m - start.y_m, end.x_m - start.x_m))
            % 360.0,
            6,
        )
    else:
        orientation_degrees = 0.0
    return SmdFixtureAssembly(
        fixture_id=f"proposed-fixture-{fixture.fixture_index:04d}",
        fixture_type=fixture.fixture_type,
        display_fixture_type=fixture.display_fixture_type,
        source_orientation=fixture.orientation,
        orientation_degrees=orientation_degrees,
        member_module_indices=member_indices,
        connectors=connectors,
    )


def _fixture_id_for_module(
    fixtures: tuple[SmdFixtureAssembly, ...], module_index: int
) -> str:
    matches = tuple(
        fixture.fixture_id
        for fixture in fixtures
        if module_index in fixture.member_module_indices
    )
    if len(matches) != 1:
        raise ValueError("each Proposed module must belong to one physical fixture.")
    return matches[0]


def generate_proposed_led_layout(
    length_ft: float,
    width_ft: float,
    *,
    mount_z_m: float = DEFAULT_MOUNT_Z_M,
    proposed_layout_mode: ProposedLayoutMode | str = DEFAULT_PROPOSED_LAYOUT_MODE,
    proposed_ring_mode: ProposedRingMode | str = DEFAULT_PROPOSED_RING_MODE,
) -> SmdLayout:
    """Convenience API for the default local Proposed LED configuration."""

    return generate_smd_layout(
        SmdLayoutConfig(
            room_length_ft=length_ft,
            room_width_ft=width_ft,
            mount_z_m=mount_z_m,
            proposed_layout_mode=proposed_layout_mode,
            proposed_ring_mode=proposed_ring_mode,
        )
    )
