"""Immutable outer-room and centered active-grow-domain authority."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
import hashlib
import json
import math
from typing import Final

from .coordinate_frame import RoomCoordinateFrame


FEET_TO_METERS_EXACT: Final = Decimal("0.3048")
AISLE_WIDTH_FT: Final = 2.0
AISLE_WIDTH_M: Final = 0.6096
_TOTAL_AISLE_SPAN_M: Final = Decimal("1.2192")
ACTIVE_DOMAIN_POLICY_ID: Final = "centered_fixed_2ft_perimeter_aisle_v1"


def _positive(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite positive number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a finite positive number.")
    return number


def _feet_to_meters(value: float) -> float:
    return float(Decimal(str(value)) * FEET_TO_METERS_EXACT)


@dataclass(frozen=True, slots=True)
class ActiveRoomDomain:
    """One centered coordinate authority for room, layout, and receivers."""

    outer_requested_length_m: float
    outer_requested_width_m: float
    enabled: bool = False
    identity_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        length = _positive(
            "outer_requested_length_m", self.outer_requested_length_m
        )
        width = _positive(
            "outer_requested_width_m", self.outer_requested_width_m
        )
        if not isinstance(self.enabled, bool):
            raise ValueError("Aisle Mode must be a Boolean.")
        object.__setattr__(self, "outer_requested_length_m", length)
        object.__setattr__(self, "outer_requested_width_m", width)
        if (
            self.active_requested_length_m <= 0.0
            or self.active_requested_width_m <= 0.0
        ):
            raise ValueError(
                "Aisle Mode requires both outer room dimensions to exceed 4 ft."
            )
        object.__setattr__(
            self,
            "identity_sha256",
            hashlib.sha256(
                json.dumps(
                    self.identity_payload(),
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest(),
        )

    @classmethod
    def from_feet(
        cls,
        length_ft: float | int,
        width_ft: float | int,
        *,
        enabled: bool = False,
    ) -> "ActiveRoomDomain":
        length = _positive("outer_requested_length_ft", length_ft)
        width = _positive("outer_requested_width_ft", width_ft)
        return cls(
            _feet_to_meters(length),
            _feet_to_meters(width),
            enabled=enabled,
        )

    @property
    def aisle_width_ft(self) -> float:
        return AISLE_WIDTH_FT if self.enabled else 0.0

    @property
    def aisle_width_m(self) -> float:
        return AISLE_WIDTH_M if self.enabled else 0.0

    @property
    def coordinate_frame(self) -> RoomCoordinateFrame:
        return RoomCoordinateFrame(
            self.outer_requested_length_m,
            self.outer_requested_width_m,
        )

    @property
    def outer_aligned_length_m(self) -> float:
        return self.coordinate_frame.simulation_length_m

    @property
    def outer_aligned_width_m(self) -> float:
        return self.coordinate_frame.simulation_width_m

    @property
    def active_requested_length_m(self) -> float:
        if not self.enabled:
            return self.outer_requested_length_m
        return float(
            Decimal(str(self.outer_requested_length_m))
            - _TOTAL_AISLE_SPAN_M
        )

    @property
    def active_requested_width_m(self) -> float:
        if not self.enabled:
            return self.outer_requested_width_m
        return float(
            Decimal(str(self.outer_requested_width_m))
            - _TOTAL_AISLE_SPAN_M
        )

    @property
    def active_aligned_length_m(self) -> float:
        return max(
            self.active_requested_length_m,
            self.active_requested_width_m,
        )

    @property
    def active_aligned_width_m(self) -> float:
        return min(
            self.active_requested_length_m,
            self.active_requested_width_m,
        )

    @property
    def active_requested_length_ft(self) -> float:
        return round(
            self.active_requested_length_m / float(FEET_TO_METERS_EXACT),
            12,
        )

    @property
    def active_requested_width_ft(self) -> float:
        return round(
            self.active_requested_width_m / float(FEET_TO_METERS_EXACT),
            12,
        )

    @property
    def outer_area_m2(self) -> float:
        return self.outer_requested_length_m * self.outer_requested_width_m

    @property
    def active_area_m2(self) -> float:
        return self.active_requested_length_m * self.active_requested_width_m

    @property
    def active_bounds_aligned_m(self) -> tuple[float, float, float, float]:
        return (
            -self.active_aligned_length_m / 2.0,
            self.active_aligned_length_m / 2.0,
            -self.active_aligned_width_m / 2.0,
            self.active_aligned_width_m / 2.0,
        )

    def identity_payload(self) -> dict[str, object]:
        min_x, max_x, min_y, max_y = self.active_bounds_aligned_m
        return {
            "schema_id": "fspm-optics.active-room-domain",
            "schema_version": 1,
            "policy_id": ACTIVE_DOMAIN_POLICY_ID,
            "enabled": self.enabled,
            "outer_requested_m": {
                "length": self.outer_requested_length_m,
                "width": self.outer_requested_width_m,
            },
            "outer_aligned_m": {
                "length_x": self.outer_aligned_length_m,
                "width_y": self.outer_aligned_width_m,
            },
            "aisle": {
                "width_ft_per_wall": self.aisle_width_ft,
                "width_m_per_wall": self.aisle_width_m,
            },
            "active_requested_m": {
                "length": self.active_requested_length_m,
                "width": self.active_requested_width_m,
            },
            "active_aligned_m": {
                "length_x": self.active_aligned_length_m,
                "width_y": self.active_aligned_width_m,
            },
            "active_bounds_aligned_m": {
                "min_x": min_x,
                "max_x": max_x,
                "min_y": min_y,
                "max_y": max_y,
            },
            "areas_m2": {
                "outer_room": self.outer_area_m2,
                "active_grow": self.active_area_m2,
            },
            "centered": True,
            "coordinate_frame": self.coordinate_frame.to_payload(),
        }

    def to_payload(self) -> dict[str, object]:
        return self.identity_payload() | {
            "identity_sha256": self.identity_sha256
        }


__all__ = [
    "ACTIVE_DOMAIN_POLICY_ID",
    "AISLE_WIDTH_FT",
    "AISLE_WIDTH_M",
    "ActiveRoomDomain",
]
