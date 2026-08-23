"""Generic deterministic rectangular fixture-center layouts."""

from __future__ import annotations

from dataclasses import dataclass
import math

from fspm_optics.geometry.room import RoomDimensions


@dataclass(frozen=True, slots=True)
class RectLayoutSpec:
    room: RoomDimensions
    rows: int
    columns: int
    mount_height_m: float
    edge_margin_x_m: float = 0.0
    edge_margin_y_m: float = 0.0

    def __post_init__(self) -> None:
        for name in ("rows", "columns"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer.")
        mount = _finite("mount_height_m", self.mount_height_m)
        margin_x = _non_negative("edge_margin_x_m", self.edge_margin_x_m)
        margin_y = _non_negative("edge_margin_y_m", self.edge_margin_y_m)
        if not 0.0 <= mount <= self.room.height_m:
            raise ValueError("mount_height_m must lie within the room height.")
        if 2.0 * margin_x >= self.room.length_m:
            raise ValueError("edge_margin_x_m leaves no usable room length.")
        if 2.0 * margin_y >= self.room.width_m:
            raise ValueError("edge_margin_y_m leaves no usable room width.")
        object.__setattr__(self, "mount_height_m", mount)
        object.__setattr__(self, "edge_margin_x_m", margin_x)
        object.__setattr__(self, "edge_margin_y_m", margin_y)


@dataclass(frozen=True, slots=True)
class RectLayoutPosition:
    row: int
    column: int
    x_m: float
    y_m: float
    z_m: float


@dataclass(frozen=True, slots=True)
class RectLayout:
    spec: RectLayoutSpec
    positions: tuple[RectLayoutPosition, ...]
    pitch_x_m: float
    pitch_y_m: float
    footprint_length_m: float
    footprint_width_m: float

    @property
    def count(self) -> int:
        return len(self.positions)


def axis_positions(span_m: float, count: int, edge_margin_m: float = 0.0) -> tuple[float, ...]:
    """Return evenly spaced center positions across one centered room axis."""

    span = _positive("span_m", span_m)
    margin = _non_negative("edge_margin_m", edge_margin_m)
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise ValueError("count must be a positive integer.")
    usable = span - 2.0 * margin
    if usable <= 0.0:
        raise ValueError("edge_margin_m leaves no usable axis span.")
    if count == 1:
        return (0.0,)
    start = -span / 2.0 + margin
    step = usable / (count - 1)
    return tuple(start + index * step for index in range(count))


def generate_rect_layout(spec: RectLayoutSpec) -> RectLayout:
    x_coords = axis_positions(
        spec.room.length_m,
        spec.columns,
        spec.edge_margin_x_m,
    )
    y_coords = axis_positions(
        spec.room.width_m,
        spec.rows,
        spec.edge_margin_y_m,
    )
    positions = tuple(
        RectLayoutPosition(row, column, x, y, spec.mount_height_m)
        for row, y in enumerate(y_coords)
        for column, x in enumerate(x_coords)
    )
    pitch_x = x_coords[1] - x_coords[0] if len(x_coords) > 1 else 0.0
    pitch_y = y_coords[1] - y_coords[0] if len(y_coords) > 1 else 0.0
    return RectLayout(
        spec=spec,
        positions=positions,
        pitch_x_m=pitch_x,
        pitch_y_m=pitch_y,
        footprint_length_m=max(x_coords) - min(x_coords),
        footprint_width_m=max(y_coords) - min(y_coords),
    )


def _finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def _positive(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be positive.")
    return number


def _non_negative(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be non-negative.")
    return number
