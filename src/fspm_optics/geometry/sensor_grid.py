"""Deterministic canopy-plane sensor grids and rtrace receiver formatting."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable, Literal

from fspm_optics.geometry.coordinate_frame import RoomCoordinateFrame
from fspm_optics.geometry.room import RoomDimensions

GridLayout = Literal["centered", "edge_aligned"]
BASELINE_REFERENCE_PLANE_Z_M = 0.005


def _finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


@dataclass(frozen=True, slots=True)
class AdaptiveSensorGridPolicy:
    """Physical-spacing policy for reproducible baseline sampling."""

    target_spacing_m: float = 0.25
    min_points_x: int = 5
    min_points_y: int = 5
    min_floor_points_x: int = 21
    min_floor_points_y: int = 21
    max_points_x: int = 0
    max_points_y: int = 0
    wall_margin_m: float = 0.005
    module_side_m: float = 0.0
    align_long_axis_x: bool = True

    def __post_init__(self) -> None:
        target = _finite("target_spacing_m", self.target_spacing_m)
        if target <= 0.0:
            raise ValueError("target_spacing_m must be positive.")
        object.__setattr__(self, "target_spacing_m", target)
        for name in (
            "min_points_x",
            "min_points_y",
            "min_floor_points_x",
            "min_floor_points_y",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        for name in ("max_points_x", "max_points_y"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer.")
        for name in ("wall_margin_m", "module_side_m"):
            value = _finite(name, getattr(self, name))
            if value < 0.0:
                raise ValueError(f"{name} must be non-negative.")
            object.__setattr__(self, name, value)


@dataclass(frozen=True, slots=True)
class AdaptiveSensorGrid:
    """Resolved grid plus the adaptive decisions that produced it."""

    spec: "SensorGridSpec"
    policy: AdaptiveSensorGridPolicy
    base_resolution_x: int
    base_resolution_y: int
    minimum_floor_enforced_x: bool
    minimum_floor_enforced_y: bool
    axes_swapped: bool

    @property
    def point_count(self) -> int:
        return self.spec.point_count


@dataclass(frozen=True, slots=True)
class SensorGridSpec:
    room: RoomDimensions
    resolution_x: int
    resolution_y: int
    canopy_height_m: float
    inset_m: float = 0.0
    layout: GridLayout = "centered"

    def __post_init__(self) -> None:
        for name in ("resolution_x", "resolution_y"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        canopy_height = _finite("canopy_height_m", self.canopy_height_m)
        inset = _finite("inset_m", self.inset_m)
        if not 0.0 <= canopy_height <= self.room.height_m:
            raise ValueError("canopy_height_m must lie within the room height.")
        if inset < 0.0:
            raise ValueError("inset_m must be non-negative.")
        if 2.0 * inset >= min(self.room.length_m, self.room.width_m):
            raise ValueError("inset_m leaves no usable sensor-grid footprint.")
        if self.layout not in {"centered", "edge_aligned"}:
            raise ValueError("layout must be 'centered' or 'edge_aligned'.")
        object.__setattr__(self, "canopy_height_m", canopy_height)
        object.__setattr__(self, "inset_m", inset)

    @property
    def point_count(self) -> int:
        return self.resolution_x * self.resolution_y


@dataclass(frozen=True, slots=True)
class SensorPoint:
    x_m: float
    y_m: float
    z_m: float
    dx: float = 0.0
    dy: float = 0.0
    dz: float = 1.0

    def __post_init__(self) -> None:
        values = (self.x_m, self.y_m, self.z_m, self.dx, self.dy, self.dz)
        if any(not math.isfinite(float(value)) for value in values):
            raise ValueError("Sensor point coordinates and direction must be finite.")
        magnitude = math.sqrt(self.dx * self.dx + self.dy * self.dy + self.dz * self.dz)
        if not math.isclose(magnitude, 1.0, abs_tol=1e-9):
            raise ValueError("Sensor direction must be a unit vector.")


def build_adaptive_sensor_grid(
    room: RoomDimensions,
    *,
    canopy_height_m: float = BASELINE_REFERENCE_PLANE_Z_M,
    policy: AdaptiveSensorGridPolicy = AdaptiveSensorGridPolicy(),
) -> AdaptiveSensorGrid:
    """Resolve the production adaptive cell-centered baseline grid policy."""

    frame = RoomCoordinateFrame(room.length_m, room.width_m)
    axes_swapped = bool(policy.align_long_axis_x and frame.axes_swapped)
    effective_room = (
        RoomDimensions(
            frame.simulation_length_m,
            frame.simulation_width_m,
            room.height_m,
        )
        if axes_swapped
        else room
    )
    inset_m = policy.wall_margin_m + policy.module_side_m / 2.0
    interior_length_m = max(0.1, effective_room.length_m - 2.0 * inset_m)
    interior_width_m = max(0.1, effective_room.width_m - 2.0 * inset_m)
    if interior_length_m > effective_room.length_m or interior_width_m > effective_room.width_m:
        raise ValueError("adaptive grid inset leaves no usable room footprint.")
    base_x = _adaptive_count(
        interior_length_m,
        policy.target_spacing_m,
        policy.min_points_x,
        policy.max_points_x,
    )
    base_y = _adaptive_count(
        interior_width_m,
        policy.target_spacing_m,
        policy.min_points_y,
        policy.max_points_y,
    )
    resolution_x = max(base_x, policy.min_floor_points_x)
    resolution_y = max(base_y, policy.min_floor_points_y)
    if policy.max_points_x > 0:
        resolution_x = min(resolution_x, policy.max_points_x)
    if policy.max_points_y > 0:
        resolution_y = min(resolution_y, policy.max_points_y)
    spec = SensorGridSpec(
        room=effective_room,
        resolution_x=resolution_x,
        resolution_y=resolution_y,
        canopy_height_m=canopy_height_m,
        inset_m=inset_m,
        layout="centered",
    )
    return AdaptiveSensorGrid(
        spec=spec,
        policy=policy,
        base_resolution_x=base_x,
        base_resolution_y=base_y,
        minimum_floor_enforced_x=resolution_x > base_x,
        minimum_floor_enforced_y=resolution_y > base_y,
        axes_swapped=axes_swapped,
    )


def generate_sensor_points(spec: SensorGridSpec) -> tuple[SensorPoint, ...]:
    usable_length = spec.room.length_m - 2.0 * spec.inset_m
    usable_width = spec.room.width_m - 2.0 * spec.inset_m
    x_coords = _axis_coordinates(usable_length, spec.resolution_x, spec.layout)
    y_coords = _axis_coordinates(usable_width, spec.resolution_y, spec.layout)
    return tuple(
        SensorPoint(x, y, spec.canopy_height_m)
        for y in y_coords
        for x in x_coords
    )


def format_rtrace_receivers(points: Iterable[SensorPoint]) -> str:
    """Format x y z dx dy dz rows for irradiance-mode rtrace input."""

    return "".join(
        f"{point.x_m:.6f} {point.y_m:.6f} {point.z_m:.6f} "
        f"{point.dx:.6f} {point.dy:.6f} {point.dz:.6f}\n"
        for point in points
    )


def format_sensor_coordinates(points: Iterable[SensorPoint], *, header: bool = True) -> str:
    prefix = "x y z\n" if header else ""
    return prefix + "".join(
        f"{point.x_m:.6f} {point.y_m:.6f} {point.z_m:.6f}\n"
        for point in points
    )


def write_sensor_grid(
    path: str | Path,
    points: Iterable[SensorPoint],
    *,
    rtrace: bool = True,
) -> Path:
    output = Path(path)
    point_tuple = tuple(points)
    text = (
        format_rtrace_receivers(point_tuple)
        if rtrace
        else format_sensor_coordinates(point_tuple)
    )
    output.write_text(text, encoding="utf-8")
    return output


def _axis_coordinates(span_m: float, count: int, layout: GridLayout) -> tuple[float, ...]:
    half_span = span_m / 2.0
    if count == 1:
        return (0.0,)
    if layout == "edge_aligned":
        step = span_m / (count - 1)
        return tuple(-half_span + index * step for index in range(count))
    cell = span_m / count
    return tuple(-half_span + (index + 0.5) * cell for index in range(count))


def _adaptive_count(
    span_m: float,
    target_spacing_m: float,
    min_points: int,
    max_points: int,
) -> int:
    if span_m <= 0.0:
        return max(1, min_points)
    count = max(min_points, int(round(span_m / max(target_spacing_m, 1e-6))))
    if max_points > 0:
        count = min(count, max_points)
    return max(1, count)
