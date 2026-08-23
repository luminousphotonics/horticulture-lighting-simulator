"""Authoritative requested-room to long-axis-X simulation coordinates."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Final, TypeAlias


Vector3: TypeAlias = tuple[float, float, float]
ROOM_BOUNDS_TOLERANCE_M: Final = 1.0e-9
ROOM_FRAME_POLICY_ID: Final = "requested_room_to_long_axis_x_rigid_rotation_v1"


def _positive(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a finite positive number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a finite positive number.")
    return number


def _vector(name: str, value: Vector3) -> Vector3:
    if (
        not isinstance(value, tuple)
        or len(value) != 3
        or any(not math.isfinite(float(component)) for component in value)
    ):
        raise ValueError(f"{name} must be a finite XYZ tuple.")
    return tuple(float(component) for component in value)  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class RoomCoordinateFrame:
    """Centered rigid frame with the simulation room's long horizontal axis on X.

    Portrait requests use a -90 degree rotation about +Z:
    ``(requested_x, requested_y, z) -> (requested_y, -requested_x, z)``.
    The matrix has determinant +1; it is never an X/Y reflection.
    """

    requested_length_m: float
    requested_width_m: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "requested_length_m",
            _positive("requested_length_m", self.requested_length_m),
        )
        object.__setattr__(
            self,
            "requested_width_m",
            _positive("requested_width_m", self.requested_width_m),
        )

    @property
    def axes_swapped(self) -> bool:
        return self.requested_width_m > self.requested_length_m

    @property
    def simulation_length_m(self) -> float:
        return max(self.requested_length_m, self.requested_width_m)

    @property
    def simulation_width_m(self) -> float:
        return min(self.requested_length_m, self.requested_width_m)

    @property
    def rotation_degrees_about_z(self) -> int:
        return -90 if self.axes_swapped else 0

    @property
    def rotation_matrix_row_major(self) -> tuple[int, ...]:
        return (
            (0, 1, 0, -1, 0, 0, 0, 0, 1)
            if self.axes_swapped
            else (1, 0, 0, 0, 1, 0, 0, 0, 1)
        )

    def requested_to_simulation_position(self, value: Vector3) -> Vector3:
        """Rotate a position; both centered frames have zero translation."""

        return self.requested_to_simulation_direction(value)

    def simulation_to_requested_position(self, value: Vector3) -> Vector3:
        """Apply the inverse rigid transform to a position."""

        return self.simulation_to_requested_direction(value)

    def requested_to_simulation_direction(self, value: Vector3) -> Vector3:
        """Rotate a direction or normal without applying translation."""

        x, y, z = _vector("requested direction", value)
        return (y, -x, z) if self.axes_swapped else (x, y, z)

    def simulation_to_requested_direction(self, value: Vector3) -> Vector3:
        """Inverse-rotate a direction or normal without applying translation."""

        x, y, z = _vector("simulation direction", value)
        return (-y, x, z) if self.axes_swapped else (x, y, z)

    def contains_simulation_position(
        self,
        value: Vector3,
        *,
        tolerance_m: float = ROOM_BOUNDS_TOLERANCE_M,
    ) -> bool:
        x, y, _ = _vector("simulation position", value)
        tolerance = float(tolerance_m)
        if not math.isfinite(tolerance) or tolerance < 0.0:
            raise ValueError("room bounds tolerance must be finite and non-negative.")
        return (
            -self.simulation_length_m / 2.0 - tolerance
            <= x
            <= self.simulation_length_m / 2.0 + tolerance
            and -self.simulation_width_m / 2.0 - tolerance
            <= y
            <= self.simulation_width_m / 2.0 + tolerance
        )

    def simulation_bounds_to_requested(
        self,
        bounds: tuple[float, float, float, float],
    ) -> tuple[float, float, float, float]:
        """Inverse-transform an aligned XY rectangle via all four corners."""

        if (
            not isinstance(bounds, tuple)
            or len(bounds) != 4
            or any(not math.isfinite(float(value)) for value in bounds)
        ):
            raise ValueError("simulation bounds must be four finite values.")
        min_x, max_x, min_y, max_y = (float(value) for value in bounds)
        if min_x > max_x or min_y > max_y:
            raise ValueError("simulation bounds minima must not exceed maxima.")
        corners = tuple(
            self.simulation_to_requested_position((x, y, 0.0))
            for x in (min_x, max_x)
            for y in (min_y, max_y)
        )
        return (
            min(point[0] for point in corners),
            max(point[0] for point in corners),
            min(point[1] for point in corners),
            max(point[1] for point in corners),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "policy_id": ROOM_FRAME_POLICY_ID,
            "requested_room_m": {
                "length_x_m": self.requested_length_m,
                "width_y_m": self.requested_width_m,
            },
            "aligned_simulation_room_m": {
                "length_x_m": self.simulation_length_m,
                "width_y_m": self.simulation_width_m,
            },
            "axes_swapped": self.axes_swapped,
            "rotation_degrees_about_z": self.rotation_degrees_about_z,
            "rotation_matrix_row_major": list(self.rotation_matrix_row_major),
            "determinant": 1,
            "translation_m": [0.0, 0.0, 0.0],
            "position_rule": "rotation_then_translation",
            "direction_rule": "rotation_only",
            "preserves_z": True,
        }


__all__ = [
    "ROOM_BOUNDS_TOLERANCE_M",
    "ROOM_FRAME_POLICY_ID",
    "RoomCoordinateFrame",
]
