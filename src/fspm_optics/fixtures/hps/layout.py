"""Pure fixed-pitch layout and aperture-plane mount planning for HPS."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import math
from typing import Final, Literal

from fspm_optics.geometry.coordinate_frame import RoomCoordinateFrame
from fspm_optics.geometry.mounting import canonical_inches_to_meters
from fspm_optics.layout.overlay import (
    AuthoritativeOverlayPlan,
    OverlayRectangle,
)

from .errors import HpsLayoutError, NoLegalHpsLayoutError
from .profile import HPS_COMPARISON_PROFILE_ID

HPS_LAYOUT_POLICY_ID: Final = "coverage_4ft_center_pitch_v1"
HPS_CENTER_PITCH_M: Final = 1.2192
HPS_WALL_CLEARANCE_M: Final = canonical_inches_to_meters(1.0)
HPS_DEFAULT_MOUNT_HEIGHT_M: Final = canonical_inches_to_meters(24.0)
HPS_FIXTURE_LENGTH_X_M: Final = 0.798576
HPS_FIXTURE_WIDTH_Y_M: Final = 0.603504
_METRES_PER_FOOT: Final = Decimal("0.3048")
_CENTER_PITCH_M_DECIMAL: Final = Decimal("1.2192")


@dataclass(frozen=True, slots=True)
class HpsRoomDimensionsM:
    length_m: float
    width_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "length_m", _positive("length_m", self.length_m))
        object.__setattr__(self, "width_m", _positive("width_m", self.width_m))


@dataclass(frozen=True, slots=True)
class HpsRoomAxes:
    requested: HpsRoomDimensionsM
    aligned: HpsRoomDimensionsM
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
        expected = (
            HpsRoomDimensionsM(self.requested.width_m, self.requested.length_m)
            if swapped
            else self.requested
        )
        if (
            self.aligned != expected
            or self.axes_swapped != swapped
            or self.aligned_x_from_requested_axis != ("width" if swapped else "length")
            or self.aligned_y_from_requested_axis != ("length" if swapped else "width")
            or self.rotation_degrees_about_z != frame.rotation_degrees_about_z
        ):
            raise HpsLayoutError("HPS requested/aligned room-axis mapping is inconsistent.")


@dataclass(frozen=True, slots=True)
class HpsFixtureCounts:
    columns_x: int
    rows_y: int

    def __post_init__(self) -> None:
        _positive_integer("columns_x", self.columns_x)
        _positive_integer("rows_y", self.rows_y)

    @property
    def total(self) -> int:
        return self.columns_x * self.rows_y


@dataclass(frozen=True, order=True, slots=True)
class HpsGridIndex:
    row_y: int
    column_x: int

    def __post_init__(self) -> None:
        _non_negative_integer("row_y", self.row_y)
        _non_negative_integer("column_x", self.column_x)


@dataclass(frozen=True, slots=True)
class HpsMountPlanes:
    reference_plane_z_m: float = 0.0
    mount_height_m: float = HPS_DEFAULT_MOUNT_HEIGHT_M
    definition: str = "emitting_aperture_plane_to_receiver_reference_plane"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "reference_plane_z_m",
            _finite("reference_plane_z_m", self.reference_plane_z_m),
        )
        object.__setattr__(
            self,
            "mount_height_m",
            _positive("mount_height_m", self.mount_height_m),
        )
        if self.definition != "emitting_aperture_plane_to_receiver_reference_plane":
            raise HpsLayoutError("HPS mount-height semantics are fixed by policy.")

    @property
    def aperture_plane_z_m(self) -> float:
        return self.reference_plane_z_m + self.mount_height_m

    @property
    def fixture_center_z_m(self) -> float:
        return self.aperture_plane_z_m


@dataclass(frozen=True, slots=True)
class HpsFootprintBoundsM:
    aligned_min_x_m: float
    aligned_max_x_m: float
    aligned_min_y_m: float
    aligned_max_y_m: float
    requested_min_x_m: float
    requested_max_x_m: float
    requested_min_y_m: float
    requested_max_y_m: float


@dataclass(frozen=True, slots=True)
class HpsFixtureLayoutInstance:
    fixture_id: str
    profile_id: str
    grid_index: HpsGridIndex
    aligned_x_m: float
    aligned_y_m: float
    requested_x_m: float
    requested_y_m: float
    aperture_z_m: float
    fixture_center_z_m: float
    footprint_bounds_m: HpsFootprintBoundsM

    def __post_init__(self) -> None:
        if not self.fixture_id or self.profile_id != HPS_COMPARISON_PROFILE_ID:
            raise HpsLayoutError("HPS fixture layout identity is invalid.")
        if self.aperture_z_m != self.fixture_center_z_m:
            raise HpsLayoutError("zero-height HPS fixture center must equal aperture Z.")

    def to_payload(self) -> dict[str, object]:
        return {
            "fixture_id": self.fixture_id,
            "profile_id": self.profile_id,
            "grid": {
                "row_y": self.grid_index.row_y,
                "column_x": self.grid_index.column_x,
            },
            "center_m": {
                "aligned_x": self.aligned_x_m,
                "aligned_y": self.aligned_y_m,
                "requested_x": self.requested_x_m,
                "requested_y": self.requested_y_m,
                "aperture_z": self.aperture_z_m,
                "fixture_center_z": self.fixture_center_z_m,
            },
            "footprint_bounds_m": {
                name: getattr(self.footprint_bounds_m, name)
                for name in self.footprint_bounds_m.__dataclass_fields__
            },
        }


@dataclass(frozen=True, slots=True)
class HpsLayoutPlan:
    profile_id: str
    policy_id: str
    room_axes: HpsRoomAxes
    counts: HpsFixtureCounts
    pitch_x_m: float
    pitch_y_m: float
    fixture_length_x_m: float
    fixture_width_y_m: float
    minimum_wall_clearance_m: float
    actual_wall_gap_x_m: float
    actual_wall_gap_y_m: float
    mount: HpsMountPlanes
    fixtures: tuple[HpsFixtureLayoutInstance, ...]
    layout_id: str = field(init=False)

    def __post_init__(self) -> None:
        if self.profile_id != HPS_COMPARISON_PROFILE_ID:
            raise HpsLayoutError("HPS layout profile identity is invalid.")
        if self.policy_id != HPS_LAYOUT_POLICY_ID:
            raise HpsLayoutError("HPS layout policy identity is invalid.")
        if (self.pitch_x_m, self.pitch_y_m) != (
            HPS_CENTER_PITCH_M,
            HPS_CENTER_PITCH_M,
        ):
            raise HpsLayoutError("HPS center pitch must remain exactly 1.2192 m.")
        if (self.fixture_length_x_m, self.fixture_width_y_m) != (
            HPS_FIXTURE_LENGTH_X_M,
            HPS_FIXTURE_WIDTH_Y_M,
        ):
            raise HpsLayoutError("HPS fixture orientation/dimensions are invalid.")
        if (
            self.pitch_x_m < self.fixture_length_x_m
            or self.pitch_y_m < self.fixture_width_y_m
        ):
            raise HpsLayoutError("HPS fixed-pitch fixture footprints may not overlap.")
        if self.minimum_wall_clearance_m != HPS_WALL_CLEARANCE_M:
            raise HpsLayoutError("HPS minimum wall clearance must remain one inch.")
        if min(self.actual_wall_gap_x_m, self.actual_wall_gap_y_m) < (
            self.minimum_wall_clearance_m
        ):
            raise NoLegalHpsLayoutError("HPS fixed-pitch layout violates wall clearance.")
        if len(self.fixtures) != self.counts.total:
            raise HpsLayoutError("HPS fixture count does not match the grid.")
        expected_order = tuple(
            HpsGridIndex(row, column)
            for row in range(self.counts.rows_y)
            for column in range(self.counts.columns_x)
        )
        if tuple(item.grid_index for item in self.fixtures) != expected_order:
            raise HpsLayoutError("HPS fixtures must be ordered Y-major/X-minor.")
        object.__setattr__(
            self,
            "layout_id",
            "hps-layout-v1-" + _hash_payload(self.scientific_payload()),
        )

    def scientific_payload(self) -> dict[str, object]:
        requested = self.room_axes.requested
        aligned = self.room_axes.aligned
        return {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "policy_id": self.policy_id,
            "count_rule": (
                "max(1, floor(aligned_axis_ft / 4))_then_deterministic_"
                "decrement_until_complete_fixture_bounds_fit"
            ),
            "room_axes": {
                "requested_m": {"length": requested.length_m, "width": requested.width_m},
                "aligned_m": {"length_x": aligned.length_m, "width_y": aligned.width_m},
                "axes_swapped": self.room_axes.axes_swapped,
                "aligned_x_from_requested_axis": self.room_axes.aligned_x_from_requested_axis,
                "aligned_y_from_requested_axis": self.room_axes.aligned_y_from_requested_axis,
                **(
                    {
                        "rotation_degrees_about_z": self.room_axes.rotation_degrees_about_z,
                        "determinant": 1,
                    }
                    if self.room_axes.axes_swapped
                    else {}
                ),
            },
            "counts": {
                "columns_x": self.counts.columns_x,
                "rows_y": self.counts.rows_y,
                "total": self.counts.total,
            },
            "center_pitch_m": {"x": self.pitch_x_m, "y": self.pitch_y_m},
            "fixture_footprint_m": {
                "length_x": self.fixture_length_x_m,
                "width_y": self.fixture_width_y_m,
                "emitting_height": 0.0,
            },
            "wall_clearance_m": {
                "minimum_per_wall": self.minimum_wall_clearance_m,
                "actual_x_per_wall": self.actual_wall_gap_x_m,
                "actual_y_per_wall": self.actual_wall_gap_y_m,
            },
            "mount": {
                "reference_plane_z_m": self.mount.reference_plane_z_m,
                "mount_height_m": self.mount.mount_height_m,
                "definition": self.mount.definition,
                "aperture_plane_z_m": self.mount.aperture_plane_z_m,
                "fixture_center_z_m": self.mount.fixture_center_z_m,
            },
            "fixtures": [item.to_payload() for item in self.fixtures],
            "prohibited_adjustments": [
                "clipping",
                "rescaling",
                "overlap",
                "clamping",
                "rotation",
            ],
        }

    def to_payload(self) -> dict[str, object]:
        return self.scientific_payload() | {"layout_id": self.layout_id}

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2, sort_keys=True) + "\n"

    def authoritative_overlay_plan(self) -> AuthoritativeOverlayPlan:
        """Emit one exact luminous-aperture primitive per HPS fixture."""

        rectangles = tuple(
            OverlayRectangle(
                primitive_id=f"{fixture.fixture_id}-aperture",
                fixture_id=fixture.fixture_id,
                primitive_kind="luminous_aperture",
                center_x_m=fixture.aligned_x_m,
                center_y_m=fixture.aligned_y_m,
                width_x_m=self.fixture_length_x_m,
                height_y_m=self.fixture_width_y_m,
                orientation_degrees=0.0,
                color_group_index=index,
            )
            for index, fixture in enumerate(self.fixtures)
        )
        fixtures = tuple(
            {
                "fixture_id": fixture.fixture_id,
                "fixture_type": "hps_1000w_fixture",
                "source_orientation": "aligned_x_downward",
                "orientation_degrees": 0.0,
                "aperture_primitive_id": f"{fixture.fixture_id}-aperture",
            }
            for fixture in self.fixtures
        )
        return AuthoritativeOverlayPlan(
            system_id="hps",
            policy_id=self.policy_id,
            coordinate_source="HpsLayoutPlan.authoritative_overlay_plan",
            room_length_m=self.room_axes.aligned.length_m,
            room_width_m=self.room_axes.aligned.width_m,
            axes_swapped=self.room_axes.axes_swapped,
            rectangles=rectangles,
            fixture_metadata=fixtures,
            metadata={
                "layout_id": self.layout_id,
                "fixture_count": len(self.fixtures),
                "aperture_length_x_m": self.fixture_length_x_m,
                "aperture_width_y_m": self.fixture_width_y_m,
                "mount_height_m": self.mount.mount_height_m,
                "geometry_role": "authoritative_luminous_aperture",
            },
        )


def plan_hps_layout(
    room_length_m: float,
    room_width_m: float,
    *,
    reference_plane_z_m: float = 0.0,
    mount_height_m: float = HPS_DEFAULT_MOUNT_HEIGHT_M,
) -> HpsLayoutPlan:
    """Plan the fixed 4 ft center-pitch policy from explicit metre inputs."""

    requested = HpsRoomDimensionsM(room_length_m, room_width_m)
    axes = align_hps_room_long_axis_to_x(requested)
    counts = HpsFixtureCounts(
        columns_x=_largest_fitting_count(
            axes.aligned.length_m,
            HPS_FIXTURE_LENGTH_X_M,
        ),
        rows_y=_largest_fitting_count(
            axes.aligned.width_m,
            HPS_FIXTURE_WIDTH_Y_M,
        ),
    )
    gap_x = _wall_gap(
        axes.aligned.length_m,
        counts.columns_x,
        HPS_FIXTURE_LENGTH_X_M,
    )
    gap_y = _wall_gap(
        axes.aligned.width_m,
        counts.rows_y,
        HPS_FIXTURE_WIDTH_Y_M,
    )
    if min(gap_x, gap_y) < HPS_WALL_CLEARANCE_M:
        raise NoLegalHpsLayoutError(
            "selected coverage_4ft_center_pitch_v1 counts cannot preserve the "
            "required 0.0254 m wall clearance."
        )
    mount = HpsMountPlanes(reference_plane_z_m, mount_height_m)
    fixtures = _fixture_instances(axes, counts, mount)
    return HpsLayoutPlan(
        profile_id=HPS_COMPARISON_PROFILE_ID,
        policy_id=HPS_LAYOUT_POLICY_ID,
        room_axes=axes,
        counts=counts,
        pitch_x_m=HPS_CENTER_PITCH_M,
        pitch_y_m=HPS_CENTER_PITCH_M,
        fixture_length_x_m=HPS_FIXTURE_LENGTH_X_M,
        fixture_width_y_m=HPS_FIXTURE_WIDTH_Y_M,
        minimum_wall_clearance_m=HPS_WALL_CLEARANCE_M,
        actual_wall_gap_x_m=gap_x,
        actual_wall_gap_y_m=gap_y,
        mount=mount,
        fixtures=fixtures,
    )


def plan_hps_layout_from_feet(
    room_length_ft: float,
    room_width_ft: float,
    *,
    reference_plane_z_m: float = 0.0,
    mount_height_m: float = HPS_DEFAULT_MOUNT_HEIGHT_M,
) -> HpsLayoutPlan:
    """Plan from explicitly foot-valued inputs using exact decimal conversion."""

    length_ft = _positive("room_length_ft", room_length_ft)
    width_ft = _positive("room_width_ft", room_width_ft)
    return plan_hps_layout(
        float(Decimal(str(length_ft)) * _METRES_PER_FOOT),
        float(Decimal(str(width_ft)) * _METRES_PER_FOOT),
        reference_plane_z_m=reference_plane_z_m,
        mount_height_m=mount_height_m,
    )


def align_hps_room_long_axis_to_x(requested: HpsRoomDimensionsM) -> HpsRoomAxes:
    frame = RoomCoordinateFrame(requested.length_m, requested.width_m)
    swapped = frame.axes_swapped
    return HpsRoomAxes(
        requested=requested,
        aligned=(
            HpsRoomDimensionsM(
                frame.simulation_length_m,
                frame.simulation_width_m,
            )
            if swapped
            else requested
        ),
        axes_swapped=swapped,
        aligned_x_from_requested_axis="width" if swapped else "length",
        aligned_y_from_requested_axis="length" if swapped else "width",
        rotation_degrees_about_z=frame.rotation_degrees_about_z,
    )


def format_hps_layout_json(plan: HpsLayoutPlan) -> str:
    return plan.to_json()


def _fixture_instances(
    axes: HpsRoomAxes,
    counts: HpsFixtureCounts,
    mount: HpsMountPlanes,
) -> tuple[HpsFixtureLayoutInstance, ...]:
    frame = RoomCoordinateFrame(
        axes.requested.length_m,
        axes.requested.width_m,
    )
    x_start = -0.5 * HPS_CENTER_PITCH_M * (counts.columns_x - 1)
    y_start = -0.5 * HPS_CENTER_PITCH_M * (counts.rows_y - 1)
    fixtures: list[HpsFixtureLayoutInstance] = []
    for row in range(counts.rows_y):
        aligned_y = y_start + row * HPS_CENTER_PITCH_M
        for column in range(counts.columns_x):
            aligned_x = x_start + column * HPS_CENTER_PITCH_M
            requested_x, requested_y, _ = frame.simulation_to_requested_position(
                (aligned_x, aligned_y, 0.0)
            )
            aligned_bounds = (
                aligned_x - HPS_FIXTURE_LENGTH_X_M / 2.0,
                aligned_x + HPS_FIXTURE_LENGTH_X_M / 2.0,
                aligned_y - HPS_FIXTURE_WIDTH_Y_M / 2.0,
                aligned_y + HPS_FIXTURE_WIDTH_Y_M / 2.0,
            )
            requested_bounds = frame.simulation_bounds_to_requested(aligned_bounds)
            fixtures.append(
                HpsFixtureLayoutInstance(
                    fixture_id=f"hps_fixture_r{row:03d}_c{column:03d}",
                    profile_id=HPS_COMPARISON_PROFILE_ID,
                    grid_index=HpsGridIndex(row, column),
                    aligned_x_m=aligned_x,
                    aligned_y_m=aligned_y,
                    requested_x_m=requested_x,
                    requested_y_m=requested_y,
                    aperture_z_m=mount.aperture_plane_z_m,
                    fixture_center_z_m=mount.fixture_center_z_m,
                    footprint_bounds_m=HpsFootprintBoundsM(
                        *aligned_bounds,
                        *requested_bounds,
                    ),
                )
            )
    return tuple(fixtures)


def _wall_gap(room_axis_m: float, count: int, fixture_axis_m: float) -> float:
    span = HPS_CENTER_PITCH_M * (count - 1)
    return room_axis_m / 2.0 - span / 2.0 - fixture_axis_m / 2.0


def _coverage_count(aligned_axis_m: float) -> int:
    """Apply floor(axis_ft / 4) in exact decimal metre-equivalent form."""

    quotient = Decimal(str(aligned_axis_m)) / _CENTER_PITCH_M_DECIMAL
    return max(1, int(quotient))


def _largest_fitting_count(
    aligned_axis_m: float,
    fixture_axis_m: float,
) -> int:
    """Reduce the coverage candidate until the complete fixture span fits."""

    count = _coverage_count(aligned_axis_m)
    while (
        count > 1
        and _wall_gap(aligned_axis_m, count, fixture_axis_m)
        < HPS_WALL_CLEARANCE_M
    ):
        count -= 1
    if _wall_gap(
        aligned_axis_m, count, fixture_axis_m
    ) < HPS_WALL_CLEARANCE_M:
        raise NoLegalHpsLayoutError(
            "active domain is too small for one complete HPS fixture."
        )
    return count


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise HpsLayoutError(f"{name} must be positive.")
    return number


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise HpsLayoutError(f"{name} must be numeric.")
    number = float(value)
    if not math.isfinite(number):
        raise HpsLayoutError(f"{name} must be finite.")
    return number


def _positive_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HpsLayoutError(f"{name} must be a positive integer.")
    return value


def _non_negative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise HpsLayoutError(f"{name} must be a non-negative integer.")
    return value
