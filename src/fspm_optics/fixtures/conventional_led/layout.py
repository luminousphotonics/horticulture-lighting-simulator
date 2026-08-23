"""Pure deterministic layout and mount planning for the Conventional fixture."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
from typing import Final, Literal

from fspm_optics.geometry.coordinate_frame import RoomCoordinateFrame
from fspm_optics.fixtures.smd.config import SmdLayoutConfig
from fspm_optics.fixtures.smd.positions import generate_smd_layout
from fspm_optics.geometry.mounting import canonical_inches_to_meters
from fspm_optics.geometry.room import FEET_TO_METERS
from fspm_optics.layout.mode import ProposedLayoutMode
from fspm_optics.layout.overlay import (
    AuthoritativeOverlayPlan,
    OverlayRectangle,
)

from .errors import (
    ConventionalLayoutError,
    NoLegalConventionalLayoutError,
    UnsupportedFixtureRotationError,
)
from .profile import CONVENTIONAL_COMPARISON_PROFILE_ID, FixtureDimensionsM

DEFAULT_WALL_MARGIN_M: Final = canonical_inches_to_meters(1.0)
DEFAULT_MOUNT_HEIGHT_M: Final = canonical_inches_to_meters(18.0)
PRACTICAL_LAYOUT_POLICY: Final = "practical"
FULL_FIT_LAYOUT_POLICY: Final = "full_fit"
ROLLING_BENCH_LAYOUT_POLICY: Final = "rolling_bench"
ROLLING_BENCH_PITCH_M: Final = canonical_inches_to_meters(48.0)
ROLLING_BENCH_FIXTURE_ROTATION_DEGREES: Final = 90.0
CONVENTIONAL_OVERLAY_POLICY_ID: Final = (
    "conventional_led_8_bar_equal_width_equal_gap_overlay_v1"
)
CONVENTIONAL_OVERLAY_BAR_COUNT: Final = 8
LayoutPolicyName = Literal["practical", "full_fit", "rolling_bench"]


@dataclass(frozen=True, slots=True)
class LayoutPolicy:
    """Named count policy and its explicit comparison-system dependency."""

    name: LayoutPolicyName = PRACTICAL_LAYOUT_POLICY
    target_spacing_basis: str = "proposed_smd_exact_tiled_pitch_per_aligned_axis"

    def __post_init__(self) -> None:
        if self.name not in (
            PRACTICAL_LAYOUT_POLICY,
            FULL_FIT_LAYOUT_POLICY,
            ROLLING_BENCH_LAYOUT_POLICY,
        ):
            raise ConventionalLayoutError(
                "layout policy must be 'practical', 'full_fit', or 'rolling_bench'."
            )
        expected_basis = {
            PRACTICAL_LAYOUT_POLICY: (
                "proposed_smd_exact_tiled_pitch_per_aligned_axis"
            ),
            FULL_FIT_LAYOUT_POLICY: "footprint_aware_maximum_fit_per_aligned_axis",
            ROLLING_BENCH_LAYOUT_POLICY: (
                "fixed_48_in_center_pitch_per_aligned_axis"
            ),
        }[self.name]
        if self.target_spacing_basis != expected_basis:
            raise ConventionalLayoutError(
                f"{self.name} layout must use target spacing basis {expected_basis!r}."
            )

    @classmethod
    def full_fit(cls) -> "LayoutPolicy":
        return cls(
            name=FULL_FIT_LAYOUT_POLICY,
            target_spacing_basis="footprint_aware_maximum_fit_per_aligned_axis",
        )

    @classmethod
    def rolling_bench(cls) -> "LayoutPolicy":
        return cls(
            name=ROLLING_BENCH_LAYOUT_POLICY,
            target_spacing_basis="fixed_48_in_center_pitch_per_aligned_axis",
        )


@dataclass(frozen=True, slots=True)
class RoomDimensionsM:
    length_m: float
    width_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "length_m", _positive("length_m", self.length_m))
        object.__setattr__(self, "width_m", _positive("width_m", self.width_m))


@dataclass(frozen=True, slots=True)
class RoomAxisPolicy:
    """Requested-to-aligned mapping; square rooms deterministically remain unswapped."""

    requested: RoomDimensionsM
    aligned: RoomDimensionsM
    axes_swapped: bool
    aligned_x_from_requested_axis: Literal["length", "width"]
    aligned_y_from_requested_axis: Literal["length", "width"]
    rotation_degrees_about_z: int

    def __post_init__(self) -> None:
        frame = RoomCoordinateFrame(
            self.requested.length_m,
            self.requested.width_m,
        )
        swapped = frame.axes_swapped
        expected_aligned = (
            RoomDimensionsM(self.requested.width_m, self.requested.length_m)
            if swapped
            else self.requested
        )
        expected_x = "width" if swapped else "length"
        expected_y = "length" if swapped else "width"
        if (
            self.aligned != expected_aligned
            or self.axes_swapped != swapped
            or self.aligned_x_from_requested_axis != expected_x
            or self.aligned_y_from_requested_axis != expected_y
            or self.rotation_degrees_about_z != frame.rotation_degrees_about_z
        ):
            raise ConventionalLayoutError("room axis mapping is inconsistent.")


@dataclass(frozen=True, slots=True)
class UsableFootprintM:
    length_m: float
    width_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "length_m", _positive("length_m", self.length_m))
        object.__setattr__(self, "width_m", _positive("width_m", self.width_m))


@dataclass(frozen=True, slots=True)
class WallMarginM:
    x_m_per_wall: float = DEFAULT_WALL_MARGIN_M
    y_m_per_wall: float = DEFAULT_WALL_MARGIN_M

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "x_m_per_wall", _non_negative("x_m_per_wall", self.x_m_per_wall)
        )
        object.__setattr__(
            self, "y_m_per_wall", _non_negative("y_m_per_wall", self.y_m_per_wall)
        )


@dataclass(frozen=True, slots=True)
class ProposedSmdExactTiledPitchM:
    """Exact-tiled Proposed SMD pitch used only by the practical comparator policy."""

    x_m: float
    y_m: float
    proposed_smd_layout_identity: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_m", _positive("x_m", self.x_m))
        object.__setattr__(self, "y_m", _positive("y_m", self.y_m))
        if len(self.proposed_smd_layout_identity) != 64:
            raise ConventionalLayoutError("Proposed SMD layout identity is malformed.")


@dataclass(frozen=True, slots=True)
class TargetPracticalGapsM:
    x_m: float
    y_m: float
    basis: str = "proposed_smd_exact_tiled_pitch_minus_smd_module_footprint"

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_m", _non_negative("x_m", self.x_m))
        object.__setattr__(self, "y_m", _non_negative("y_m", self.y_m))
        if self.basis != (
            "proposed_smd_exact_tiled_pitch_minus_smd_module_footprint"
        ):
            raise ConventionalLayoutError("practical target-gap basis is fixed.")


@dataclass(frozen=True, slots=True)
class ResolvedFixtureCounts:
    columns_x: int
    rows_y: int

    def __post_init__(self) -> None:
        _positive_integer("columns_x", self.columns_x)
        _positive_integer("rows_y", self.rows_y)

    @property
    def total(self) -> int:
        return self.columns_x * self.rows_y


@dataclass(frozen=True, slots=True)
class ActualCenteredGapsM:
    """Equal free gaps, including the free gap just inside each fixed wall margin."""

    x_m: float
    y_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_m", _non_negative("x_m", self.x_m))
        object.__setattr__(self, "y_m", _non_negative("y_m", self.y_m))


@dataclass(frozen=True, slots=True)
class PerimeterMarginsM:
    """Physical room-edge clearance around the centered fixture array."""

    x_m: float
    y_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x_m", _non_negative("x_m", self.x_m))
        object.__setattr__(self, "y_m", _non_negative("y_m", self.y_m))


@dataclass(frozen=True, order=True, slots=True)
class FixtureGridIndex:
    row_y: int
    column_x: int

    def __post_init__(self) -> None:
        _non_negative_integer("row_y", self.row_y)
        _non_negative_integer("column_x", self.column_x)


@dataclass(frozen=True, slots=True)
class FixtureIdentity:
    fixture_id: str
    conventional_profile_id: str
    grid_index: FixtureGridIndex

    def __post_init__(self) -> None:
        if not self.fixture_id or not self.conventional_profile_id:
            raise ConventionalLayoutError("fixture identities must be non-empty.")


@dataclass(frozen=True, slots=True)
class ApertureCenterPositionM:
    aligned_x_m: float
    aligned_y_m: float
    requested_x_m: float
    requested_y_m: float
    z_m: float


@dataclass(frozen=True, slots=True)
class FixtureCenterPositionM:
    aligned_x_m: float
    aligned_y_m: float
    requested_x_m: float
    requested_y_m: float
    z_m: float


@dataclass(frozen=True, slots=True)
class FootprintBoundsM:
    aligned_min_x_m: float
    aligned_max_x_m: float
    aligned_min_y_m: float
    aligned_max_y_m: float
    requested_min_x_m: float
    requested_max_x_m: float
    requested_min_y_m: float
    requested_max_y_m: float


@dataclass(frozen=True, slots=True)
class MountReferencePlaneSemantics:
    """Mount height is aperture-plane height above the receiver/reference plane."""

    reference_plane_z_m: float = 0.0
    mount_height_m: float = DEFAULT_MOUNT_HEIGHT_M
    mount_height_definition: str = (
        "emitting_aperture_plane_to_receiver_reference_plane"
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reference_plane_z_m",
            _finite("reference_plane_z_m", self.reference_plane_z_m),
        )
        object.__setattr__(
            self, "mount_height_m", _positive("mount_height_m", self.mount_height_m)
        )
        if self.mount_height_definition != (
            "emitting_aperture_plane_to_receiver_reference_plane"
        ):
            raise ConventionalLayoutError("mount height definition is fixed by policy.")

    @property
    def aperture_z_m(self) -> float:
        return self.reference_plane_z_m + self.mount_height_m


@dataclass(frozen=True, slots=True)
class DownwardEmissionTransform:
    local_emission_axis: str = "negative_z"
    world_emission_direction: tuple[float, float, float] = (0.0, 0.0, -1.0)
    fixture_rotation_degrees: float = 0.0

    def __post_init__(self) -> None:
        if (
            self.local_emission_axis != "negative_z"
            or self.world_emission_direction != (0.0, 0.0, -1.0)
            or self.fixture_rotation_degrees
            not in (0.0, ROLLING_BENCH_FIXTURE_ROTATION_DEGREES)
        ):
            raise ConventionalLayoutError(
                "Conventional transform must preserve a supported fixed orientation "
                "and downward emission."
            )


@dataclass(frozen=True, slots=True)
class ConventionalFixtureInstance:
    identity: FixtureIdentity
    aperture_center_m: ApertureCenterPositionM
    fixture_center_m: FixtureCenterPositionM
    footprint_bounds_m: FootprintBoundsM
    transform: DownwardEmissionTransform

    def to_payload(self) -> dict[str, object]:
        grid = self.identity.grid_index
        return {
            "fixture_id": self.identity.fixture_id,
            "conventional_profile_id": self.identity.conventional_profile_id,
            "grid": {"row_y": grid.row_y, "column_x": grid.column_x},
            "aperture_center_m": _position_payload(self.aperture_center_m),
            "fixture_center_m": _position_payload(self.fixture_center_m),
            "footprint_bounds_m": {
                name: getattr(self.footprint_bounds_m, name)
                for name in self.footprint_bounds_m.__dataclass_fields__
            },
            "transform": {
                "local_emission_axis": self.transform.local_emission_axis,
                "world_emission_direction": list(
                    self.transform.world_emission_direction
                ),
                "fixture_rotation_degrees": self.transform.fixture_rotation_degrees,
            },
        }


@dataclass(frozen=True, slots=True)
class ConventionalLayoutPlan:
    conventional_profile_id: str
    policy: LayoutPolicy
    room_axes: RoomAxisPolicy
    wall_margin_m: WallMarginM
    usable_footprint_m: UsableFootprintM
    fixture_dimensions_m: FixtureDimensionsM
    proposed_smd_exact_tiled_pitch_m: ProposedSmdExactTiledPitchM | None
    target_practical_gaps_m: TargetPracticalGapsM | None
    resolved_counts: ResolvedFixtureCounts
    actual_centered_gaps_m: ActualCenteredGapsM
    perimeter_margins_m: PerimeterMarginsM
    mount: MountReferencePlaneSemantics
    fixtures: tuple[ConventionalFixtureInstance, ...]
    layout_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.conventional_profile_id != CONVENTIONAL_COMPARISON_PROFILE_ID:
            raise ConventionalLayoutError("Conventional profile identity is invalid.")
        if self.policy.name == PRACTICAL_LAYOUT_POLICY:
            if (
                self.proposed_smd_exact_tiled_pitch_m is None
                or self.target_practical_gaps_m is None
            ):
                raise ConventionalLayoutError(
                    "practical layout requires explicit Proposed SMD pitch and gaps."
                )
        elif (
            self.proposed_smd_exact_tiled_pitch_m is not None
            or self.target_practical_gaps_m is not None
        ):
            raise ConventionalLayoutError(
                "non-practical layouts must not carry practical target-spacing metadata."
            )
        if len(self.fixtures) != self.resolved_counts.total:
            raise ConventionalLayoutError("fixture count does not match resolved grid.")
        expected_grid = tuple(
            FixtureGridIndex(row, column)
            for row in range(self.resolved_counts.rows_y)
            for column in range(self.resolved_counts.columns_x)
        )
        if tuple(item.identity.grid_index for item in self.fixtures) != expected_grid:
            raise ConventionalLayoutError("fixtures must use deterministic Y-major order.")
        expected_rotation = (
            ROLLING_BENCH_FIXTURE_ROTATION_DEGREES
            if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY
            else 0.0
        )
        if any(
            item.transform.fixture_rotation_degrees != expected_rotation
            for item in self.fixtures
        ):
            raise ConventionalLayoutError(
                "fixture orientation does not match the selected layout policy."
            )
        _validate_planned_geometry(self)
        object.__setattr__(self, "layout_id", _layout_identity(self.identity_payload()))

    def identity_payload(self) -> dict[str, object]:
        requested = self.room_axes.requested
        aligned = self.room_axes.aligned
        pitch = self.proposed_smd_exact_tiled_pitch_m
        target = self.target_practical_gaps_m
        return {
            "schema_version": 1,
            "conventional_profile_id": self.conventional_profile_id,
            "policy": {
                "name": self.policy.name,
                "target_spacing_basis": self.policy.target_spacing_basis,
            },
            "room_axes": {
                "requested_m": _dimensions_payload(requested),
                "aligned_m": _dimensions_payload(aligned),
                "axes_swapped": self.room_axes.axes_swapped,
                "aligned_x_from_requested_axis": (
                    self.room_axes.aligned_x_from_requested_axis
                ),
                "aligned_y_from_requested_axis": (
                    self.room_axes.aligned_y_from_requested_axis
                ),
                **(
                    {
                        "rotation_degrees_about_z": (
                            self.room_axes.rotation_degrees_about_z
                        ),
                        "determinant": 1,
                    }
                    if self.room_axes.axes_swapped
                    else {}
                ),
            },
            "wall_margin_m": {
                "x_per_wall": self.wall_margin_m.x_m_per_wall,
                "y_per_wall": self.wall_margin_m.y_m_per_wall,
            },
            "usable_footprint_m": _dimensions_payload(self.usable_footprint_m),
            "fixture_dimensions_m": self.fixture_dimensions_m.to_dict(),
            "proposed_smd_exact_tiled_pitch_m": None
            if pitch is None
            else {
                "x_m": pitch.x_m,
                "y_m": pitch.y_m,
                "proposed_smd_layout_identity": pitch.proposed_smd_layout_identity,
            },
            "target_practical_gaps_m": None
            if target is None
            else {"x_m": target.x_m, "y_m": target.y_m, "basis": target.basis},
            "resolved_counts": {
                "columns_x": self.resolved_counts.columns_x,
                "rows_y": self.resolved_counts.rows_y,
                "total": self.resolved_counts.total,
            },
            "actual_centered_gaps_m": {
                "x_m": self.actual_centered_gaps_m.x_m,
                "y_m": self.actual_centered_gaps_m.y_m,
            },
            **(
                {"placement_provenance": self.placement_provenance_payload()}
                if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY
                else {}
            ),
            "mount": {
                "reference_plane_z_m": self.mount.reference_plane_z_m,
                "mount_height_m": self.mount.mount_height_m,
                "mount_height_definition": self.mount.mount_height_definition,
                "aperture_z_m": self.mount.aperture_z_m,
                "fixture_center_z_m": (
                    self.mount.aperture_z_m
                    + self.fixture_dimensions_m.height_m / 2.0
                ),
            },
            "fixtures": [fixture.to_payload() for fixture in self.fixtures],
        }

    def placement_provenance_payload(self) -> dict[str, object]:
        extent_x_m, extent_y_m = _oriented_fixture_extents(
            self.policy, self.fixture_dimensions_m
        )
        pitch_x_m = extent_x_m + self.actual_centered_gaps_m.x_m
        pitch_y_m = extent_y_m + self.actual_centered_gaps_m.y_m
        if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY:
            pitch_x_m = ROLLING_BENCH_PITCH_M
            pitch_y_m = ROLLING_BENCH_PITCH_M
        min_x = min(
            fixture.footprint_bounds_m.aligned_min_x_m for fixture in self.fixtures
        )
        max_x = max(
            fixture.footprint_bounds_m.aligned_max_x_m for fixture in self.fixtures
        )
        min_y = min(
            fixture.footprint_bounds_m.aligned_min_y_m for fixture in self.fixtures
        )
        max_y = max(
            fixture.footprint_bounds_m.aligned_max_y_m for fixture in self.fixtures
        )
        return {
            "mode_id": self.policy.name,
            "center_to_center_pitch_m": {
                "x_m": pitch_x_m,
                "y_m": pitch_y_m,
            },
            "orientation": {
                "fixture_rotation_degrees_about_aligned_z": (
                    ROLLING_BENCH_FIXTURE_ROTATION_DEGREES
                    if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY
                    else 0.0
                ),
                "aligned_x_extent_from_fixture_axis": (
                    "width" if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY
                    else "length"
                ),
                "aligned_y_extent_from_fixture_axis": (
                    "length" if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY
                    else "width"
                ),
                "automatic_rotation": False,
            },
            "fixture_footprint_m": {
                "x_extent_m": extent_x_m,
                "y_extent_m": extent_y_m,
            },
            "calculated_fixture_edge_gaps_m": {
                "x_m": pitch_x_m - extent_x_m,
                "y_m": pitch_y_m - extent_y_m,
            },
            "fixture_counts": {
                "columns_x": self.resolved_counts.columns_x,
                "rows_y": self.resolved_counts.rows_y,
                "total": self.resolved_counts.total,
            },
            "array_bounds_m": {
                "aligned_min_x_m": min_x,
                "aligned_max_x_m": max_x,
                "aligned_min_y_m": min_y,
                "aligned_max_y_m": max_y,
            },
            "perimeter_margins_m": {
                "x_m": self.perimeter_margins_m.x_m,
                "y_m": self.perimeter_margins_m.y_m,
            },
        }

    def to_payload(self) -> dict[str, object]:
        return self.identity_payload() | {"layout_id": self.layout_id}

    def to_json(self) -> str:
        return json.dumps(
            self.to_payload(), indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n"

    def authoritative_overlay_plan(self) -> AuthoritativeOverlayPlan:
        """Emit the approved non-emitting Conventional LED eight-bar display geometry."""

        bar_width_m = self.fixture_dimensions_m.width_m / (
            2 * CONVENTIONAL_OVERLAY_BAR_COUNT - 1
        )
        rotation = (
            ROLLING_BENCH_FIXTURE_ROTATION_DEGREES
            if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY
            else 0.0
        )
        rectangles = tuple(
            OverlayRectangle(
                primitive_id=f"{fixture.identity.fixture_id}-bar-{bar_index:02d}",
                fixture_id=fixture.identity.fixture_id,
                primitive_kind="non_emitting_eight_bar_reference",
                center_x_m=fixture.aperture_center_m.aligned_x_m
                + (
                    -_bar_local_y_offset(
                        bar_index, self.fixture_dimensions_m.width_m, bar_width_m
                    )
                    if rotation == ROLLING_BENCH_FIXTURE_ROTATION_DEGREES
                    else 0.0
                ),
                center_y_m=fixture.aperture_center_m.aligned_y_m
                + (
                    0.0
                    if rotation == ROLLING_BENCH_FIXTURE_ROTATION_DEGREES
                    else _bar_local_y_offset(
                        bar_index, self.fixture_dimensions_m.width_m, bar_width_m
                    )
                ),
                width_x_m=self.fixture_dimensions_m.length_m,
                height_y_m=bar_width_m,
                orientation_degrees=rotation,
                color_group_index=fixture_index,
            )
            for fixture_index, fixture in enumerate(self.fixtures)
            for bar_index in range(CONVENTIONAL_OVERLAY_BAR_COUNT)
        )
        fixtures = tuple(
            {
                "fixture_id": fixture.identity.fixture_id,
                "fixture_type": "conventional_led_8_bar",
                "source_orientation": (
                    "aligned_y_downward"
                    if rotation == ROLLING_BENCH_FIXTURE_ROTATION_DEGREES
                    else "aligned_x_downward"
                ),
                "orientation_degrees": rotation,
                "bar_primitive_ids": [
                    f"{fixture.identity.fixture_id}-bar-{index:02d}"
                    for index in range(CONVENTIONAL_OVERLAY_BAR_COUNT)
                ],
            }
            for fixture in self.fixtures
        )
        return AuthoritativeOverlayPlan(
            system_id="conventional",
            policy_id=CONVENTIONAL_OVERLAY_POLICY_ID,
            coordinate_source="ConventionalLayoutPlan.authoritative_overlay_plan",
            room_length_m=self.room_axes.aligned.length_m,
            room_width_m=self.room_axes.aligned.width_m,
            axes_swapped=self.room_axes.axes_swapped,
            rectangles=rectangles,
            fixture_metadata=fixtures,
            metadata={
                "layout_policy": self.policy.name,
                "layout_id": self.layout_id,
                "fixture_count": len(self.fixtures),
                "bar_count_per_fixture": CONVENTIONAL_OVERLAY_BAR_COUNT,
                "bar_length_m": self.fixture_dimensions_m.length_m,
                "bar_width_m": bar_width_m,
                "bar_center_spacing_m": 2.0 * bar_width_m,
                "geometry_role": "overlay_only_non_emitting_reference",
                "transport_geometry_affected": False,
                **(
                    {
                        "placement_provenance": (
                            self.placement_provenance_payload()
                        )
                    }
                    if self.policy.name == ROLLING_BENCH_LAYOUT_POLICY
                    else {}
                ),
            },
        )


def plan_conventional_layout(
    room_length_m: float,
    room_width_m: float,
    *,
    policy: LayoutPolicy | LayoutPolicyName = PRACTICAL_LAYOUT_POLICY,
    wall_margin_m: WallMarginM | None = None,
    mount: MountReferencePlaneSemantics | None = None,
    fixture_rotation_degrees: float = 0.0,
) -> ConventionalLayoutPlan:
    """Plan fixed-orientation Conventional fixtures without filesystem access."""

    requested = RoomDimensionsM(room_length_m, room_width_m)
    room_axes = align_long_room_axis_to_x(requested)
    selected_policy = _coerce_policy(policy)
    if selected_policy.name == ROLLING_BENCH_LAYOUT_POLICY:
        if wall_margin_m is not None and (
            wall_margin_m.x_m_per_wall != 0.0
            or wall_margin_m.y_m_per_wall != 0.0
        ):
            raise ConventionalLayoutError(
                "rolling_bench uses the full room dimensions and does not accept "
                "additional wall margins."
            )
        margins = WallMarginM(0.0, 0.0)
    else:
        margins = wall_margin_m or WallMarginM()
    mount_policy = mount or MountReferencePlaneSemantics()
    rotation = _finite("fixture_rotation_degrees", fixture_rotation_degrees)
    if rotation != 0.0:
        raise UnsupportedFixtureRotationError(
            "only the fixed 0 degree aligned-room fixture orientation is supported."
        )

    fixture_dimensions = FixtureDimensionsM(
        length_m=1.190, width_m=1.087, height_m=0.108
    )
    fixture_extent_x_m, fixture_extent_y_m = _oriented_fixture_extents(
        selected_policy, fixture_dimensions
    )
    usable_length_m = room_axes.aligned.length_m - 2.0 * margins.x_m_per_wall
    usable_width_m = room_axes.aligned.width_m - 2.0 * margins.y_m_per_wall
    if (
        usable_length_m < fixture_extent_x_m
        or usable_width_m < fixture_extent_y_m
    ):
        raise NoLegalConventionalLayoutError(
            "room interior after wall margins is too small for one fixed-orientation "
            "Conventional fixture."
        )
    usable = UsableFootprintM(usable_length_m, usable_width_m)

    pitch: ProposedSmdExactTiledPitchM | None = None
    target: TargetPracticalGapsM | None = None
    if selected_policy.name == PRACTICAL_LAYOUT_POLICY:
        pitch = proposed_smd_exact_tiled_pitch(room_axes.aligned)
        smd_config = SmdLayoutConfig(
            room_length_ft=room_axes.aligned.length_m / FEET_TO_METERS,
            room_width_ft=room_axes.aligned.width_m / FEET_TO_METERS,
            proposed_layout_mode=ProposedLayoutMode.LINEAR,
        )
        target = TargetPracticalGapsM(
            x_m=max(pitch.x_m - smd_config.module_footprint_x_m, 0.0),
            y_m=max(pitch.y_m - smd_config.module_footprint_y_m, 0.0),
        )
        counts = ResolvedFixtureCounts(
            columns_x=_practical_count(
                usable.length_m, fixture_extent_x_m, target.x_m
            ),
            rows_y=_practical_count(
                usable.width_m, fixture_extent_y_m, target.y_m
            ),
        )
        actual_gaps = ActualCenteredGapsM(
            x_m=_actual_centered_gap(
                usable.length_m, fixture_extent_x_m, counts.columns_x
            ),
            y_m=_actual_centered_gap(
                usable.width_m, fixture_extent_y_m, counts.rows_y
            ),
        )
        pitch_x_m = fixture_extent_x_m + actual_gaps.x_m
        pitch_y_m = fixture_extent_y_m + actual_gaps.y_m
    elif selected_policy.name == FULL_FIT_LAYOUT_POLICY:
        counts = ResolvedFixtureCounts(
            columns_x=math.floor(usable.length_m / fixture_extent_x_m),
            rows_y=math.floor(usable.width_m / fixture_extent_y_m),
        )
        actual_gaps = ActualCenteredGapsM(
            x_m=_actual_centered_gap(
                usable.length_m, fixture_extent_x_m, counts.columns_x
            ),
            y_m=_actual_centered_gap(
                usable.width_m, fixture_extent_y_m, counts.rows_y
            ),
        )
        pitch_x_m = fixture_extent_x_m + actual_gaps.x_m
        pitch_y_m = fixture_extent_y_m + actual_gaps.y_m
    else:
        counts = ResolvedFixtureCounts(
            columns_x=_rolling_bench_count(
                room_axes.aligned.length_m, fixture_extent_x_m
            ),
            rows_y=_rolling_bench_count(
                room_axes.aligned.width_m, fixture_extent_y_m
            ),
        )
        pitch_x_m = ROLLING_BENCH_PITCH_M
        pitch_y_m = ROLLING_BENCH_PITCH_M
        actual_gaps = ActualCenteredGapsM(
            x_m=pitch_x_m - fixture_extent_x_m,
            y_m=pitch_y_m - fixture_extent_y_m,
        )
    perimeter_margins = PerimeterMarginsM(
        x_m=(
            room_axes.aligned.length_m
            - (
                fixture_extent_x_m
                + (counts.columns_x - 1) * pitch_x_m
            )
        )
        / 2.0,
        y_m=(
            room_axes.aligned.width_m
            - (
                fixture_extent_y_m
                + (counts.rows_y - 1) * pitch_y_m
            )
        )
        / 2.0,
    )
    fixtures = _fixture_instances(
        room_axes=room_axes,
        dimensions=fixture_dimensions,
        extent_x_m=fixture_extent_x_m,
        extent_y_m=fixture_extent_y_m,
        counts=counts,
        pitch_x_m=pitch_x_m,
        pitch_y_m=pitch_y_m,
        perimeter_margins=perimeter_margins,
        mount=mount_policy,
        policy=selected_policy,
    )
    values = {
        "conventional_profile_id": CONVENTIONAL_COMPARISON_PROFILE_ID,
        "policy": selected_policy,
        "room_axes": room_axes,
        "wall_margin_m": margins,
        "usable_footprint_m": usable,
        "fixture_dimensions_m": fixture_dimensions,
        "proposed_smd_exact_tiled_pitch_m": pitch,
        "target_practical_gaps_m": target,
        "resolved_counts": counts,
        "actual_centered_gaps_m": actual_gaps,
        "perimeter_margins_m": perimeter_margins,
        "mount": mount_policy,
        "fixtures": fixtures,
    }
    return ConventionalLayoutPlan(**values)


def plan_conventional_layout_from_feet(
    room_length_ft: float,
    room_width_ft: float,
    *,
    policy: LayoutPolicy | LayoutPolicyName = PRACTICAL_LAYOUT_POLICY,
    wall_margin_m: WallMarginM | None = None,
    mount: MountReferencePlaneSemantics | None = None,
    fixture_rotation_degrees: float = 0.0,
) -> ConventionalLayoutPlan:
    """Convert explicitly foot-valued room inputs at the API boundary."""

    return plan_conventional_layout(
        _positive("room_length_ft", room_length_ft) * FEET_TO_METERS,
        _positive("room_width_ft", room_width_ft) * FEET_TO_METERS,
        policy=policy,
        wall_margin_m=wall_margin_m,
        mount=mount,
        fixture_rotation_degrees=fixture_rotation_degrees,
    )


def format_conventional_layout_json(plan: ConventionalLayoutPlan) -> str:
    """Return stable, path-free JSON for a completed layout and transform plan."""

    return plan.to_json()


def align_long_room_axis_to_x(requested: RoomDimensionsM) -> RoomAxisPolicy:
    frame = RoomCoordinateFrame(requested.length_m, requested.width_m)
    swapped = frame.axes_swapped
    if swapped:
        aligned = RoomDimensionsM(
            frame.simulation_length_m,
            frame.simulation_width_m,
        )
        return RoomAxisPolicy(requested, aligned, True, "width", "length", -90)
    return RoomAxisPolicy(requested, requested, False, "length", "width", 0)


def proposed_smd_exact_tiled_pitch(
    aligned_room_m: RoomDimensionsM,
) -> ProposedSmdExactTiledPitchM:
    """Derive independent X/Y pitch through the validated Proposed SMD planner."""

    smd_layout = generate_smd_layout(
        SmdLayoutConfig(
            room_length_ft=aligned_room_m.length_m / FEET_TO_METERS,
            room_width_ft=aligned_room_m.width_m / FEET_TO_METERS,
            proposed_layout_mode=ProposedLayoutMode.LINEAR,
        )
    )
    smd_payload = {
        "system": "proposed_smd",
        "room_length_m": smd_layout.room_length_m,
        "room_width_m": smd_layout.room_width_m,
        "pitch_x_m": smd_layout.pitch_x_m,
        "pitch_y_m": smd_layout.pitch_y_m,
        "topology": {
            name: getattr(smd_layout.topology, name)
            for name in (
                "base_n",
                "square_tile_count",
                "connector_count",
                "has_rectangular_extension",
                "rectangular_long_ft",
                "rectangular_offset",
                "control_zone_count",
            )
        },
        "modules": [
            {
                "module_index": module.module_index,
                "x_m": module.x_m,
                "y_m": module.y_m,
                "control_zone_index": module.control_zone_index,
            }
            for module in smd_layout.modules
        ],
    }
    return ProposedSmdExactTiledPitchM(
        x_m=smd_layout.pitch_x_m,
        y_m=smd_layout.pitch_y_m,
        proposed_smd_layout_identity=_hash_payload(smd_payload),
    )


def _fixture_instances(
    *,
    room_axes: RoomAxisPolicy,
    dimensions: FixtureDimensionsM,
    extent_x_m: float,
    extent_y_m: float,
    counts: ResolvedFixtureCounts,
    pitch_x_m: float,
    pitch_y_m: float,
    perimeter_margins: PerimeterMarginsM,
    mount: MountReferencePlaneSemantics,
    policy: LayoutPolicy,
) -> tuple[ConventionalFixtureInstance, ...]:
    start_x = (
        -room_axes.aligned.length_m / 2.0
        + perimeter_margins.x_m
        + extent_x_m / 2.0
    )
    start_y = (
        -room_axes.aligned.width_m / 2.0
        + perimeter_margins.y_m
        + extent_y_m / 2.0
    )
    fixtures: list[ConventionalFixtureInstance] = []
    for row in range(counts.rows_y):
        aligned_y = start_y + row * pitch_y_m
        for column in range(counts.columns_x):
            aligned_x = start_x + column * pitch_x_m
            requested_x, requested_y = _to_requested_axes(
                room_axes, aligned_x, aligned_y
            )
            grid = FixtureGridIndex(row, column)
            fixture_key = {
                "conventional_profile_id": CONVENTIONAL_COMPARISON_PROFILE_ID,
                "layout_policy": policy.name,
                "requested_room_m": _dimensions_payload(room_axes.requested),
                "grid": {"row_y": row, "column_x": column},
                "aligned_aperture_xyz_m": [aligned_x, aligned_y, mount.aperture_z_m],
                "fixture_dimensions_m": dimensions.to_dict(),
            }
            half_x = extent_x_m / 2.0
            half_y = extent_y_m / 2.0
            aligned_bounds = (
                aligned_x - half_x,
                aligned_x + half_x,
                aligned_y - half_y,
                aligned_y + half_y,
            )
            requested_bounds = _bounds_to_requested(room_axes, aligned_bounds)
            aperture_z = mount.aperture_z_m
            fixtures.append(
                ConventionalFixtureInstance(
                    identity=FixtureIdentity(
                        fixture_id=f"conventional-fixture-{_hash_payload(fixture_key)[:16]}",
                        conventional_profile_id=CONVENTIONAL_COMPARISON_PROFILE_ID,
                        grid_index=grid,
                    ),
                    aperture_center_m=ApertureCenterPositionM(
                        aligned_x,
                        aligned_y,
                        requested_x,
                        requested_y,
                        aperture_z,
                    ),
                    fixture_center_m=FixtureCenterPositionM(
                        aligned_x,
                        aligned_y,
                        requested_x,
                        requested_y,
                        aperture_z + dimensions.height_m / 2.0,
                    ),
                    footprint_bounds_m=FootprintBoundsM(
                        *aligned_bounds, *requested_bounds
                    ),
                    transform=DownwardEmissionTransform(
                        fixture_rotation_degrees=(
                            ROLLING_BENCH_FIXTURE_ROTATION_DEGREES
                            if policy.name == ROLLING_BENCH_LAYOUT_POLICY
                            else 0.0
                        )
                    ),
                )
            )
    return tuple(fixtures)


def _coerce_policy(policy: LayoutPolicy | LayoutPolicyName) -> LayoutPolicy:
    if isinstance(policy, LayoutPolicy):
        return policy
    if policy == PRACTICAL_LAYOUT_POLICY:
        return LayoutPolicy()
    if policy == FULL_FIT_LAYOUT_POLICY:
        return LayoutPolicy.full_fit()
    if policy == ROLLING_BENCH_LAYOUT_POLICY:
        return LayoutPolicy.rolling_bench()
    raise ConventionalLayoutError(
        "layout policy must be 'practical', 'full_fit', or 'rolling_bench'."
    )


def _validate_planned_geometry(plan: ConventionalLayoutPlan) -> None:
    half_room_x = plan.room_axes.aligned.length_m / 2.0
    half_room_y = plan.room_axes.aligned.width_m / 2.0
    bounds = tuple(item.footprint_bounds_m for item in plan.fixtures)
    for item in bounds:
        if (
            item.aligned_min_x_m < -half_room_x
            or item.aligned_max_x_m > half_room_x
            or item.aligned_min_y_m < -half_room_y
            or item.aligned_max_y_m > half_room_y
        ):
            raise NoLegalConventionalLayoutError(
                "planned fixture footprint clips a room boundary."
            )
    for index, left in enumerate(bounds):
        for right in bounds[index + 1 :]:
            separated = (
                left.aligned_max_x_m <= right.aligned_min_x_m
                or right.aligned_max_x_m <= left.aligned_min_x_m
                or left.aligned_max_y_m <= right.aligned_min_y_m
                or right.aligned_max_y_m <= left.aligned_min_y_m
            )
            if not separated:
                raise NoLegalConventionalLayoutError(
                    "planned fixture footprints overlap; counts are never reduced."
                )
    if plan.policy.name == ROLLING_BENCH_LAYOUT_POLICY:
        provenance = plan.placement_provenance_payload()
        pitch = provenance["center_to_center_pitch_m"]
        footprint = provenance["fixture_footprint_m"]
        gaps = provenance["calculated_fixture_edge_gaps_m"]
        if (
            plan.wall_margin_m != WallMarginM(0.0, 0.0)
            or pitch != {
                "x_m": ROLLING_BENCH_PITCH_M,
                "y_m": ROLLING_BENCH_PITCH_M,
            }
            or footprint != {"x_extent_m": 1.087, "y_extent_m": 1.190}
            or gaps
            != {
                "x_m": ROLLING_BENCH_PITCH_M - 1.087,
                "y_m": ROLLING_BENCH_PITCH_M - 1.190,
            }
        ):
            raise ConventionalLayoutError(
                "Rolling Bench fixed pitch, orientation, or edge gaps changed."
            )
        x_centers = tuple(
            plan.fixtures[column].aperture_center_m.aligned_x_m
            for column in range(plan.resolved_counts.columns_x)
        )
        y_centers = tuple(
            plan.fixtures[
                row * plan.resolved_counts.columns_x
            ].aperture_center_m.aligned_y_m
            for row in range(plan.resolved_counts.rows_y)
        )
        if any(
            not math.isclose(
                right - left,
                ROLLING_BENCH_PITCH_M,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
            for centers in (x_centers, y_centers)
            for left, right in zip(centers, centers[1:])
        ):
            raise ConventionalLayoutError(
                "Rolling Bench center pitch must remain exactly 48 inches."
            )


def _practical_count(usable_m: float, fixture_span_m: float, gap_m: float) -> int:
    numerator = max(usable_m - gap_m, fixture_span_m)
    return max(1, math.floor(numerator / (fixture_span_m + gap_m)))


def _rolling_bench_count(room_span_m: float, fixture_span_m: float) -> int:
    if room_span_m < fixture_span_m:
        raise NoLegalConventionalLayoutError(
            "room is too small for one fixed-orientation Rolling Bench fixture."
        )
    return math.floor(
        (room_span_m - fixture_span_m) / ROLLING_BENCH_PITCH_M
    ) + 1


def _actual_centered_gap(usable_m: float, fixture_span_m: float, count: int) -> float:
    free = usable_m - count * fixture_span_m
    if free < -1e-12:
        raise NoLegalConventionalLayoutError(
            "resolved fixture count exceeds the legal footprint; counts are never reduced."
        )
    return max(free, 0.0) / (count + 1)


def _oriented_fixture_extents(
    policy: LayoutPolicy, dimensions: FixtureDimensionsM
) -> tuple[float, float]:
    if policy.name == ROLLING_BENCH_LAYOUT_POLICY:
        return dimensions.width_m, dimensions.length_m
    return dimensions.length_m, dimensions.width_m


def _bar_local_y_offset(
    bar_index: int, fixture_width_m: float, bar_width_m: float
) -> float:
    return (
        -fixture_width_m / 2.0
        + bar_width_m / 2.0
        + bar_index * 2.0 * bar_width_m
    )


def _to_requested_axes(
    axes: RoomAxisPolicy, aligned_x: float, aligned_y: float
) -> tuple[float, float]:
    frame = RoomCoordinateFrame(
        axes.requested.length_m,
        axes.requested.width_m,
    )
    requested_x, requested_y, _ = frame.simulation_to_requested_position(
        (aligned_x, aligned_y, 0.0)
    )
    return requested_x, requested_y


def _bounds_to_requested(
    axes: RoomAxisPolicy, bounds: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    frame = RoomCoordinateFrame(
        axes.requested.length_m,
        axes.requested.width_m,
    )
    return frame.simulation_bounds_to_requested(bounds)


def _position_payload(
    position: ApertureCenterPositionM | FixtureCenterPositionM,
) -> dict[str, float]:
    return {
        "aligned_x_m": position.aligned_x_m,
        "aligned_y_m": position.aligned_y_m,
        "requested_x_m": position.requested_x_m,
        "requested_y_m": position.requested_y_m,
        "z_m": position.z_m,
    }


def _dimensions_payload(value: RoomDimensionsM | UsableFootprintM) -> dict[str, float]:
    return {"length_m": value.length_m, "width_m": value.width_m}


def _layout_identity(payload: dict[str, object]) -> str:
    return f"conventional-layout-v1-{_hash_payload(payload)}"


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _positive_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ConventionalLayoutError(f"{name} must be a positive integer.")


def _non_negative_integer(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ConventionalLayoutError(f"{name} must be a non-negative integer.")


def _finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConventionalLayoutError(f"{name} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise ConventionalLayoutError(f"{name} must be a finite number.")
    return number


def _positive(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ConventionalLayoutError(f"{name} must be positive.")
    return number


def _non_negative(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise ConventionalLayoutError(f"{name} must be non-negative.")
    return number
