"""Source-neutral authoritative primitives for PPFD layout overlays."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Literal, Mapping

from fspm_optics.geometry.active_domain import ActiveRoomDomain

OverlayStrokeColorRole = Literal["color_group", "plot_border"]


class OverlayPlanError(ValueError):
    """A layout engine emitted an invalid visualization overlay plan."""


@dataclass(frozen=True, slots=True)
class OverlayRectangle:
    """One explicitly sized and oriented layout primitive in scientific XY."""

    primitive_id: str
    fixture_id: str
    primitive_kind: str
    center_x_m: float
    center_y_m: float
    width_x_m: float
    height_y_m: float
    orientation_degrees: float
    color_group_index: int = 0
    stroke_color_role: OverlayStrokeColorRole = "color_group"
    stroke_width_px: int = 1

    def __post_init__(self) -> None:
        if not self.primitive_id or not self.fixture_id or not self.primitive_kind:
            raise OverlayPlanError("overlay primitive identities must be non-empty.")
        values = (
            self.center_x_m,
            self.center_y_m,
            self.width_x_m,
            self.height_y_m,
            self.orientation_degrees,
        )
        if any(not math.isfinite(float(value)) for value in values):
            raise OverlayPlanError("overlay rectangle values must be finite.")
        if self.width_x_m <= 0.0 or self.height_y_m <= 0.0:
            raise OverlayPlanError("overlay rectangle dimensions must be positive.")
        if (
            isinstance(self.color_group_index, bool)
            or not isinstance(self.color_group_index, int)
            or self.color_group_index < 0
        ):
            raise OverlayPlanError("overlay color group must be a non-negative integer.")
        _validate_stroke(self.stroke_color_role, self.stroke_width_px)

    def to_dict(self) -> dict[str, object]:
        return {
            "primitive_id": self.primitive_id,
            "fixture_id": self.fixture_id,
            "primitive_kind": self.primitive_kind,
            "center_m": {"x": self.center_x_m, "y": self.center_y_m},
            "size_m": {"x": self.width_x_m, "y": self.height_y_m},
            "orientation_degrees": self.orientation_degrees,
            "color_group_index": self.color_group_index,
            "stroke": {
                "color_role": self.stroke_color_role,
                "width_px": self.stroke_width_px,
            },
        }


@dataclass(frozen=True, slots=True)
class OverlayLine:
    """One authoritative connector or centerline in scientific XY."""

    primitive_id: str
    fixture_id: str
    primitive_kind: str
    start_x_m: float
    start_y_m: float
    end_x_m: float
    end_y_m: float
    color_group_index: int = 0
    stroke_color_role: OverlayStrokeColorRole = "color_group"
    stroke_width_px: int = 1

    def __post_init__(self) -> None:
        if not self.primitive_id or not self.fixture_id or not self.primitive_kind:
            raise OverlayPlanError("overlay line identities must be non-empty.")
        values = (self.start_x_m, self.start_y_m, self.end_x_m, self.end_y_m)
        if any(not math.isfinite(float(value)) for value in values):
            raise OverlayPlanError("overlay line values must be finite.")
        if (
            isinstance(self.color_group_index, bool)
            or not isinstance(self.color_group_index, int)
            or self.color_group_index < 0
        ):
            raise OverlayPlanError("overlay color group must be a non-negative integer.")
        _validate_stroke(self.stroke_color_role, self.stroke_width_px)

    def to_dict(self) -> dict[str, object]:
        return {
            "primitive_id": self.primitive_id,
            "fixture_id": self.fixture_id,
            "primitive_kind": self.primitive_kind,
            "start_m": {"x": self.start_x_m, "y": self.start_y_m},
            "end_m": {"x": self.end_x_m, "y": self.end_y_m},
            "color_group_index": self.color_group_index,
            "stroke": {
                "color_role": self.stroke_color_role,
                "width_px": self.stroke_width_px,
            },
        }


def _validate_stroke(
    color_role: OverlayStrokeColorRole | str,
    width_px: int,
) -> None:
    if color_role not in ("color_group", "plot_border"):
        raise OverlayPlanError("overlay stroke color role is unsupported.")
    if (
        isinstance(width_px, bool)
        or not isinstance(width_px, int)
        or width_px <= 0
    ):
        raise OverlayPlanError("overlay stroke width must be a positive integer.")


@dataclass(frozen=True, slots=True)
class AuthoritativeOverlayPlan:
    """Complete geometry emitted by a layout engine for the shared renderer."""

    system_id: str
    policy_id: str
    coordinate_source: str
    room_length_m: float
    room_width_m: float
    axes_swapped: bool
    rectangles: tuple[OverlayRectangle, ...]
    lines: tuple[OverlayLine, ...] = ()
    fixture_metadata: tuple[Mapping[str, object], ...] = ()
    metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if not self.system_id or not self.policy_id or not self.coordinate_source:
            raise OverlayPlanError("overlay plan identities must be non-empty.")
        if (
            not math.isfinite(float(self.room_length_m))
            or not math.isfinite(float(self.room_width_m))
            or self.room_length_m <= 0.0
            or self.room_width_m <= 0.0
        ):
            raise OverlayPlanError("overlay room dimensions must be finite and positive.")
        if not isinstance(self.axes_swapped, bool):
            raise OverlayPlanError("overlay axes_swapped must be boolean.")
        if not self.rectangles:
            raise OverlayPlanError("overlay plan requires authoritative rectangles.")
        primitive_ids = tuple(
            item.primitive_id for item in (*self.rectangles, *self.lines)
        )
        if len(set(primitive_ids)) != len(primitive_ids):
            raise OverlayPlanError("overlay primitive identifiers must be unique.")
        object.__setattr__(
            self,
            "fixture_metadata",
            tuple(dict(item) for item in self.fixture_metadata),
        )
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict[str, object]:
        return {
            "system_id": self.system_id,
            "policy_id": self.policy_id,
            "coordinate_source": self.coordinate_source,
            "coordinate_system": "right-handed scientific XY, meters, Z-up",
            "room": {
                "length_x_m": self.room_length_m,
                "width_y_m": self.room_width_m,
                "axes_swapped_from_request": self.axes_swapped,
            },
            "rectangles": [item.to_dict() for item in self.rectangles],
            "lines": [item.to_dict() for item in self.lines],
            "fixtures": [dict(item) for item in self.fixture_metadata],
            "metadata": dict(self.metadata or {}),
            "image_coordinate_inference": False,
            "geometry_inference": False,
        }


def bind_overlay_to_active_domain(
    plan: AuthoritativeOverlayPlan,
    domain: ActiveRoomDomain,
) -> AuthoritativeOverlayPlan:
    """Publish active coordinates over the unchanged complete outer room."""

    if not isinstance(plan, AuthoritativeOverlayPlan) or not isinstance(
        domain, ActiveRoomDomain
    ):
        raise OverlayPlanError(
            "overlay/domain binding requires authoritative immutable inputs."
        )
    if (
        not math.isclose(
            plan.room_length_m,
            domain.active_aligned_length_m,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
        or not math.isclose(
            plan.room_width_m,
            domain.active_aligned_width_m,
            rel_tol=0.0,
            abs_tol=1.0e-9,
        )
    ):
        raise OverlayPlanError(
            "overlay layout footprint disagrees with the active domain."
        )
    return replace(
        plan,
        room_length_m=domain.outer_aligned_length_m,
        room_width_m=domain.outer_aligned_width_m,
        metadata={
            **dict(plan.metadata or {}),
            "active_domain": domain.to_payload(),
            "layout_coordinates_use_active_domain": True,
            "physical_room_uses_outer_domain": True,
        },
    )


__all__ = [
    "AuthoritativeOverlayPlan",
    "OverlayLine",
    "OverlayPlanError",
    "OverlayRectangle",
    "OverlayStrokeColorRole",
    "bind_overlay_to_active_domain",
]
