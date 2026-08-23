"""Pure source-neutral natural-fit plant placement planning."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import re
from typing import Final, Literal

from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.geometry.coordinate_frame import RoomCoordinateFrame
from fspm_optics.plants.rex_juvenile import (
    REX_JUVENILE_PREHEADING_LOCAL_X_BOUNDS_M,
    REX_JUVENILE_PREHEADING_LOCAL_Y_BOUNDS_M,
    REX_JUVENILE_PREHEADING_PROFILE_ID,
)

NATURAL_FIT_POLICY_ID: Final = "natural_fit_0p40m_sampling_v1"
DEFAULT_TARGET_CENTER_PITCH_M: Final = 0.40
DEFAULT_BOUNDARY_CLEARANCE_M: Final = 0.005
DEFAULT_INTERPLANT_GEOMETRY_CLEARANCE_M: Final = 0.010
METERS_PER_FOOT: Final = 0.3048
_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


def _finite(name: str, value: float | int) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise NaturalFitLayoutError(f"{name} must be a finite number.")
    number = float(value)
    if not math.isfinite(number):
        raise NaturalFitLayoutError(f"{name} must be a finite number.")
    return number


def _positive(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise NaturalFitLayoutError(f"{name} must be positive.")
    return number


def _non_negative(name: str, value: float | int) -> float:
    number = _finite(name, value)
    if number < 0.0:
        raise NaturalFitLayoutError(f"{name} must be non-negative.")
    return number
class NaturalFitLayoutError(ValueError):
    """Raised when a natural-fit plan cannot be constructed exactly."""


@dataclass(frozen=True, slots=True)
class PlantLocalXYBoundsM:
    """Exact asymmetric profile-local XY geometry bounds."""

    min_x_m: float
    max_x_m: float
    min_y_m: float
    max_y_m: float

    def __post_init__(self) -> None:
        values = {
            name: _finite(name, getattr(self, name))
            for name in ("min_x_m", "max_x_m", "min_y_m", "max_y_m")
        }
        if values["min_x_m"] >= values["max_x_m"]:
            raise NaturalFitLayoutError("local x bounds must have positive span.")
        if values["min_y_m"] >= values["max_y_m"]:
            raise NaturalFitLayoutError("local y bounds must have positive span.")
        for name, value in values.items():
            object.__setattr__(self, name, value)

    @property
    def span_x_m(self) -> float:
        return self.max_x_m - self.min_x_m

    @property
    def span_y_m(self) -> float:
        return self.max_y_m - self.min_y_m

    def to_payload(self) -> dict[str, float]:
        return {
            "min_x_m": self.min_x_m,
            "max_x_m": self.max_x_m,
            "min_y_m": self.min_y_m,
            "max_y_m": self.max_y_m,
        }


REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M: Final = PlantLocalXYBoundsM(
    min_x_m=REX_JUVENILE_PREHEADING_LOCAL_X_BOUNDS_M[0],
    max_x_m=REX_JUVENILE_PREHEADING_LOCAL_X_BOUNDS_M[1],
    min_y_m=REX_JUVENILE_PREHEADING_LOCAL_Y_BOUNDS_M[0],
    max_y_m=REX_JUVENILE_PREHEADING_LOCAL_Y_BOUNDS_M[1],
)


@dataclass(frozen=True, slots=True)
class NaturalFitPolicy:
    """Named natural-fit sampling policy with explicit physical clearances."""

    policy_id: str = NATURAL_FIT_POLICY_ID
    target_center_pitch_m: float = DEFAULT_TARGET_CENTER_PITCH_M
    boundary_clearance_m: float = DEFAULT_BOUNDARY_CLEARANCE_M
    interplant_geometry_clearance_m: float = (
        DEFAULT_INTERPLANT_GEOMETRY_CLEARANCE_M
    )

    def __post_init__(self) -> None:
        if self.policy_id != NATURAL_FIT_POLICY_ID:
            raise NaturalFitLayoutError(
                f"policy_id is fixed to {NATURAL_FIT_POLICY_ID!r}."
            )
        object.__setattr__(
            self,
            "target_center_pitch_m",
            _positive("target_center_pitch_m", self.target_center_pitch_m),
        )
        object.__setattr__(
            self,
            "boundary_clearance_m",
            _non_negative("boundary_clearance_m", self.boundary_clearance_m),
        )
        object.__setattr__(
            self,
            "interplant_geometry_clearance_m",
            _non_negative(
                "interplant_geometry_clearance_m",
                self.interplant_geometry_clearance_m,
            ),
        )

    def to_payload(self) -> dict[str, str | float]:
        return {
            "policy_id": self.policy_id,
            "target_center_pitch_m": self.target_center_pitch_m,
            "boundary_clearance_m": self.boundary_clearance_m,
            "interplant_geometry_clearance_m": (
                self.interplant_geometry_clearance_m
            ),
        }


@dataclass(frozen=True, slots=True)
class RequestedRoomFootprintM:
    """Requested room orientation, centered at the scientific XY origin."""

    length_m: float
    width_m: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "length_m", _positive("length_m", self.length_m))
        object.__setattr__(self, "width_m", _positive("width_m", self.width_m))

    def to_payload(self) -> dict[str, float]:
        return {"length_m": self.length_m, "width_m": self.width_m}


@dataclass(frozen=True, slots=True)
class RequestedToAlignedAxisMapping:
    """Metadata for the authoritative proper requested-to-simulation rotation."""

    requested_x_axis: Literal["length"] = "length"
    requested_y_axis: Literal["width"] = "width"
    aligned_x_from_requested_axis: Literal["length", "width"] = "length"
    aligned_y_from_requested_axis: Literal["length", "width"] = "width"
    axes_swapped: bool = False
    rotation_degrees_about_z: int = 0

    def __post_init__(self) -> None:
        if (
            self.requested_x_axis != "length"
            or self.requested_y_axis != "width"
            or not isinstance(self.axes_swapped, bool)
            or self.aligned_x_from_requested_axis
            != ("width" if self.axes_swapped else "length")
            or self.aligned_y_from_requested_axis
            != ("length" if self.axes_swapped else "width")
            or self.rotation_degrees_about_z
            != (-90 if self.axes_swapped else 0)
        ):
            raise NaturalFitLayoutError(
                "natural-fit axes must use the authoritative proper room rotation."
            )

    def to_payload(self) -> dict[str, str | bool | int]:
        payload: dict[str, str | bool | int] = {
            "requested_x_axis": self.requested_x_axis,
            "requested_y_axis": self.requested_y_axis,
            "aligned_x_from_requested_axis": self.aligned_x_from_requested_axis,
            "aligned_y_from_requested_axis": self.aligned_y_from_requested_axis,
            "axes_swapped": self.axes_swapped,
        }
        if self.axes_swapped:
            payload["rotation_degrees_about_z"] = self.rotation_degrees_about_z
            payload["determinant"] = 1
        return payload


@dataclass(frozen=True, slots=True)
class NaturalFitAxisPlan:
    """Resolved origins and feasibility quantities for one requested axis."""

    requested_axis: Literal["length", "width"]
    footprint_min_m: float
    footprint_max_m: float
    plant_min_m: float
    plant_max_m: float
    origin_min_m: float
    origin_max_m: float
    usable_span_m: float
    plant_span_m: float
    minimum_pitch_m: float
    count: int
    actual_pitch_m: float | None
    origins_m: tuple[float, ...]

    def __post_init__(self) -> None:
        if self.requested_axis not in ("length", "width"):
            raise NaturalFitLayoutError("requested axis must be length or width.")
        numeric_names = (
            "footprint_min_m",
            "footprint_max_m",
            "plant_min_m",
            "plant_max_m",
            "origin_min_m",
            "origin_max_m",
            "usable_span_m",
            "plant_span_m",
            "minimum_pitch_m",
        )
        if any(
            not math.isfinite(getattr(self, name)) for name in numeric_names
        ):
            raise NaturalFitLayoutError("axis plan values must be finite.")
        if (
            self.footprint_min_m >= self.footprint_max_m
            or self.plant_min_m >= self.plant_max_m
            or self.origin_min_m > self.origin_max_m
            or self.usable_span_m < 0.0
            or self.plant_span_m <= 0.0
            or self.minimum_pitch_m <= 0.0
        ):
            raise NaturalFitLayoutError("axis plan bounds or spans are invalid.")
        if (
            isinstance(self.count, bool)
            or not isinstance(self.count, int)
            or self.count < 1
        ):
            raise NaturalFitLayoutError("axis count must be a positive integer.")
        if len(self.origins_m) != self.count or any(
            not math.isfinite(value) for value in self.origins_m
        ):
            raise NaturalFitLayoutError("axis origins do not match the resolved count.")
        if self.count == 1:
            if self.actual_pitch_m is not None or self.origins_m != (
                (self.origin_min_m + self.origin_max_m) / 2.0,
            ):
                raise NaturalFitLayoutError(
                    "one-plant axes must use the allowable-interval midpoint."
                )
        elif (
            self.actual_pitch_m is None
            or not math.isfinite(self.actual_pitch_m)
            or self.actual_pitch_m < self.minimum_pitch_m
            or self.origins_m[0] != self.origin_min_m
            or self.origins_m[-1] != self.origin_max_m
        ):
            raise NaturalFitLayoutError("multi-plant axis pitch or origins are invalid.")

    def to_payload(self) -> dict[str, object]:
        return {
            "requested_axis": self.requested_axis,
            "footprint_min_m": self.footprint_min_m,
            "footprint_max_m": self.footprint_max_m,
            "plant_min_m": self.plant_min_m,
            "plant_max_m": self.plant_max_m,
            "origin_min_m": self.origin_min_m,
            "origin_max_m": self.origin_max_m,
            "usable_span_m": self.usable_span_m,
            "plant_span_m": self.plant_span_m,
            "minimum_pitch_m": self.minimum_pitch_m,
            "count": self.count,
            "actual_pitch_m": self.actual_pitch_m,
            "origins_m": list(self.origins_m),
        }


@dataclass(frozen=True, order=True, slots=True)
class PlantGridIndex:
    row_y: int
    column_x: int

    def __post_init__(self) -> None:
        for name in ("row_y", "column_x"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise NaturalFitLayoutError(
                    f"{name} must be a non-negative integer."
                )


@dataclass(frozen=True, slots=True)
class NaturalFitPlantPlacement:
    """One immutable profile origin in aligned and requested coordinates."""

    plant_id: str
    profile_id: str
    grid_index: PlantGridIndex
    aligned_x_m: float
    aligned_y_m: float
    requested_x_m: float
    requested_y_m: float

    def __post_init__(self) -> None:
        if (
            not _SAFE_ID.fullmatch(self.plant_id)
            or not _SAFE_ID.fullmatch(self.profile_id)
        ):
            raise NaturalFitLayoutError("plant and profile IDs must be safe identifiers.")
        positions = (
            self.aligned_x_m,
            self.aligned_y_m,
            self.requested_x_m,
            self.requested_y_m,
        )
        if not all(math.isfinite(value) for value in positions):
            raise NaturalFitLayoutError("plant origins must be finite.")

    def to_payload(self) -> dict[str, object]:
        return {
            "plant_id": self.plant_id,
            "profile_id": self.profile_id,
            "grid": {
                "row_y": self.grid_index.row_y,
                "column_x": self.grid_index.column_x,
            },
            "origin_m": {
                "aligned_x_m": self.aligned_x_m,
                "aligned_y_m": self.aligned_y_m,
                "requested_x_m": self.requested_x_m,
                "requested_y_m": self.requested_y_m,
            },
        }


@dataclass(frozen=True, slots=True)
class NaturalFitLayoutPlan:
    """Complete deterministic Y-major/X-minor natural-fit placement plan."""

    profile_id: str
    policy: NaturalFitPolicy
    requested_room_m: RequestedRoomFootprintM
    axis_mapping: RequestedToAlignedAxisMapping
    plant_local_bounds_m: PlantLocalXYBoundsM
    x_axis: NaturalFitAxisPlan
    y_axis: NaturalFitAxisPlan
    plants: tuple[NaturalFitPlantPlacement, ...]
    active_domain: ActiveRoomDomain | None = None
    plan_hash: str = field(init=False)

    def __post_init__(self) -> None:
        if not _SAFE_ID.fullmatch(self.profile_id):
            raise NaturalFitLayoutError("profile_id must be a safe identifier.")
        frame = self.coordinate_frame
        expected_mapping = RequestedToAlignedAxisMapping(
            aligned_x_from_requested_axis=(
                "width" if frame.axes_swapped else "length"
            ),
            aligned_y_from_requested_axis=(
                "length" if frame.axes_swapped else "width"
            ),
            axes_swapped=frame.axes_swapped,
            rotation_degrees_about_z=frame.rotation_degrees_about_z,
        )
        if self.axis_mapping != expected_mapping:
            raise NaturalFitLayoutError(
                "natural-fit mapping does not match the requested room frame."
            )
        if self.active_domain is not None and (
            not isinstance(self.active_domain, ActiveRoomDomain)
            or not math.isclose(
                self.active_domain.outer_requested_length_m,
                self.requested_room_m.length_m,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
            or not math.isclose(
                self.active_domain.outer_requested_width_m,
                self.requested_room_m.width_m,
                rel_tol=0.0,
                abs_tol=1.0e-12,
            )
        ):
            raise NaturalFitLayoutError(
                "active domain does not match the requested outer room."
            )
        footprint_min_x, footprint_max_x, footprint_min_y, footprint_max_y = (
            self._active_bounds_aligned_m
        )
        expected_x = _plan_axis(
            requested_axis=("width" if frame.axes_swapped else "length"),
            footprint_min_m=footprint_min_x,
            footprint_max_m=footprint_max_x,
            plant_min_m=self.plant_local_bounds_m.min_x_m,
            plant_max_m=self.plant_local_bounds_m.max_x_m,
            policy=self.policy,
        )
        expected_y = _plan_axis(
            requested_axis=("length" if frame.axes_swapped else "width"),
            footprint_min_m=footprint_min_y,
            footprint_max_m=footprint_max_y,
            plant_min_m=self.plant_local_bounds_m.min_y_m,
            plant_max_m=self.plant_local_bounds_m.max_y_m,
            policy=self.policy,
        )
        if self.x_axis != expected_x or self.y_axis != expected_y:
            raise NaturalFitLayoutError("axis plans do not match policy inputs.")
        expected_indices = tuple(
            PlantGridIndex(row_y, column_x)
            for row_y in range(self.y_axis.count)
            for column_x in range(self.x_axis.count)
        )
        if tuple(item.grid_index for item in self.plants) != expected_indices:
            raise NaturalFitLayoutError("plants must use Y-major/X-minor order.")
        if len({item.plant_id for item in self.plants}) != len(self.plants):
            raise NaturalFitLayoutError("plant IDs must be unique.")
        for item in self.plants:
            expected_id = (
                f"{self.profile_id}_layout_r{item.grid_index.row_y:03d}"
                f"_c{item.grid_index.column_x:03d}"
            )
            requested_origin = frame.simulation_to_requested_position(
                (item.aligned_x_m, item.aligned_y_m, 0.0)
            )
            if (
                item.profile_id != self.profile_id
                or item.plant_id != expected_id
                or item.aligned_x_m
                != self.x_axis.origins_m[item.grid_index.column_x]
                or item.aligned_y_m
                != self.y_axis.origins_m[item.grid_index.row_y]
                or item.requested_x_m != requested_origin[0]
                or item.requested_y_m != requested_origin[1]
            ):
                raise NaturalFitLayoutError(
                    "plant identity or origin does not match the resolved grid."
                )
        object.__setattr__(self, "plan_hash", _hash_payload(self.identity_payload()))

    @property
    def count_x(self) -> int:
        return self.x_axis.count

    @property
    def count_y(self) -> int:
        return self.y_axis.count

    @property
    def total_count(self) -> int:
        return len(self.plants)

    @property
    def coordinate_frame(self) -> RoomCoordinateFrame:
        return RoomCoordinateFrame(
            self.requested_room_m.length_m,
            self.requested_room_m.width_m,
        )

    @property
    def aligned_room_m(self) -> RequestedRoomFootprintM:
        frame = self.coordinate_frame
        return RequestedRoomFootprintM(
            frame.simulation_length_m,
            frame.simulation_width_m,
        )

    @property
    def _active_bounds_aligned_m(
        self,
    ) -> tuple[float, float, float, float]:
        if self.active_domain is not None:
            return self.active_domain.active_bounds_aligned_m
        frame = self.coordinate_frame
        return (
            -frame.simulation_length_m / 2.0,
            frame.simulation_length_m / 2.0,
            -frame.simulation_width_m / 2.0,
            frame.simulation_width_m / 2.0,
        )

    def identity_payload(self) -> dict[str, object]:
        payload = {
            "schema_id": "fspm-optics.natural-fit-plant-layout",
            "schema_version": 1,
            "profile_id": self.profile_id,
            "policy": self.policy.to_payload(),
            "requested_room_m": self.requested_room_m.to_payload(),
            "axis_mapping": self.axis_mapping.to_payload(),
            "plant_local_bounds_m": self.plant_local_bounds_m.to_payload(),
            "x_axis": self.x_axis.to_payload(),
            "y_axis": self.y_axis.to_payload(),
            "ordering": "Y-major/X-minor",
            "plants": [plant.to_payload() for plant in self.plants],
        }
        if self.coordinate_frame.axes_swapped:
            payload["aligned_simulation_room_m"] = self.aligned_room_m.to_payload()
            payload["coordinate_frame"] = self.coordinate_frame.to_payload()
        if self.active_domain is not None:
            payload["active_domain"] = self.active_domain.to_payload()
        return payload

    def to_payload(self) -> dict[str, object]:
        return self.identity_payload() | {"plan_hash": self.plan_hash}

    def to_json(self) -> str:
        return json.dumps(
            self.to_payload(), indent=2, sort_keys=True, ensure_ascii=False
        ) + "\n"


def plan_natural_fit_layout(
    room_length_m: float | int,
    room_width_m: float | int,
    *,
    profile_id: str = REX_JUVENILE_PREHEADING_PROFILE_ID,
    plant_local_bounds_m: PlantLocalXYBoundsM = (
        REX_JUVENILE_PREHEADING_LOCAL_XY_BOUNDS_M
    ),
    policy: NaturalFitPolicy = NaturalFitPolicy(),
    active_domain: ActiveRoomDomain | None = None,
) -> NaturalFitLayoutPlan:
    """Plan exact plant bounds in the authoritative long-axis-X simulation frame."""

    room = RequestedRoomFootprintM(room_length_m, room_width_m)
    if not isinstance(profile_id, str) or not _SAFE_ID.fullmatch(profile_id):
        raise NaturalFitLayoutError("profile_id must be a non-empty safe identifier.")
    if not isinstance(plant_local_bounds_m, PlantLocalXYBoundsM):
        raise NaturalFitLayoutError(
            "plant_local_bounds_m must be PlantLocalXYBoundsM."
        )
    if not isinstance(policy, NaturalFitPolicy):
        raise NaturalFitLayoutError("policy must be NaturalFitPolicy.")
    if active_domain is not None and not isinstance(
        active_domain, ActiveRoomDomain
    ):
        raise NaturalFitLayoutError(
            "active_domain must be ActiveRoomDomain."
        )
    if active_domain is not None and (
        not math.isclose(
            active_domain.outer_requested_length_m,
            room.length_m,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        or not math.isclose(
            active_domain.outer_requested_width_m,
            room.width_m,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
    ):
        raise NaturalFitLayoutError(
            "active domain does not match the requested outer room."
        )
    frame = RoomCoordinateFrame(room.length_m, room.width_m)
    if active_domain is None:
        active_bounds = (
            -frame.simulation_length_m / 2.0,
            frame.simulation_length_m / 2.0,
            -frame.simulation_width_m / 2.0,
            frame.simulation_width_m / 2.0,
        )
    else:
        active_bounds = active_domain.active_bounds_aligned_m
    mapping = RequestedToAlignedAxisMapping(
        aligned_x_from_requested_axis=("width" if frame.axes_swapped else "length"),
        aligned_y_from_requested_axis=("length" if frame.axes_swapped else "width"),
        axes_swapped=frame.axes_swapped,
        rotation_degrees_about_z=frame.rotation_degrees_about_z,
    )
    x_axis = _plan_axis(
        requested_axis=("width" if frame.axes_swapped else "length"),
        footprint_min_m=active_bounds[0],
        footprint_max_m=active_bounds[1],
        plant_min_m=plant_local_bounds_m.min_x_m,
        plant_max_m=plant_local_bounds_m.max_x_m,
        policy=policy,
    )
    y_axis = _plan_axis(
        requested_axis=("length" if frame.axes_swapped else "width"),
        footprint_min_m=active_bounds[2],
        footprint_max_m=active_bounds[3],
        plant_min_m=plant_local_bounds_m.min_y_m,
        plant_max_m=plant_local_bounds_m.max_y_m,
        policy=policy,
    )
    plants_list: list[NaturalFitPlantPlacement] = []
    for row_y, y_m in enumerate(y_axis.origins_m):
        for column_x, x_m in enumerate(x_axis.origins_m):
            requested_x, requested_y, _ = frame.simulation_to_requested_position(
                (x_m, y_m, 0.0)
            )
            plants_list.append(
                NaturalFitPlantPlacement(
                    plant_id=f"{profile_id}_layout_r{row_y:03d}_c{column_x:03d}",
                    profile_id=profile_id,
                    grid_index=PlantGridIndex(row_y, column_x),
                    aligned_x_m=x_m,
                    aligned_y_m=y_m,
                    requested_x_m=requested_x,
                    requested_y_m=requested_y,
                )
            )
    plants = tuple(plants_list)
    return NaturalFitLayoutPlan(
        profile_id=profile_id,
        policy=policy,
        requested_room_m=room,
        axis_mapping=mapping,
        plant_local_bounds_m=plant_local_bounds_m,
        x_axis=x_axis,
        y_axis=y_axis,
        plants=plants,
        active_domain=active_domain,
    )


def plan_natural_fit_layout_from_feet(
    room_length_ft: float | int,
    room_width_ft: float | int,
    **kwargs: object,
) -> NaturalFitLayoutPlan:
    """Convert explicit foot-valued room dimensions at the API boundary."""

    length_ft = _positive("room_length_ft", room_length_ft)
    width_ft = _positive("room_width_ft", room_width_ft)
    return plan_natural_fit_layout(
        length_ft * METERS_PER_FOOT,
        width_ft * METERS_PER_FOOT,
        **kwargs,
    )


def format_natural_fit_layout_json(plan: NaturalFitLayoutPlan) -> str:
    """Return canonical, path-free JSON for a completed placement plan."""

    if not isinstance(plan, NaturalFitLayoutPlan):
        raise NaturalFitLayoutError("plan must be NaturalFitLayoutPlan.")
    return plan.to_json()


def _plan_axis(
    *,
    requested_axis: Literal["length", "width"],
    footprint_min_m: float,
    footprint_max_m: float,
    plant_min_m: float,
    plant_max_m: float,
    policy: NaturalFitPolicy,
) -> NaturalFitAxisPlan:
    origin_min = footprint_min_m + policy.boundary_clearance_m - plant_min_m
    origin_max = footprint_max_m - policy.boundary_clearance_m - plant_max_m
    usable_span = origin_max - origin_min
    plant_span = plant_max_m - plant_min_m
    minimum_pitch = plant_span + policy.interplant_geometry_clearance_m
    if not all(
        math.isfinite(value)
        for value in (origin_min, origin_max, usable_span, plant_span, minimum_pitch)
    ):
        raise NaturalFitLayoutError("derived natural-fit axis values must be finite.")
    if usable_span < 0.0:
        raise NaturalFitLayoutError(
            f"plant geometry cannot be contained on requested {requested_axis} axis."
        )

    count = _closest_feasible_count(
        usable_span_m=usable_span,
        minimum_pitch_m=minimum_pitch,
        target_pitch_m=policy.target_center_pitch_m,
    )
    if count == 1:
        actual_pitch = None
        origins = ((origin_min + origin_max) / 2.0,)
    else:
        actual_pitch = usable_span / (count - 1)
        if actual_pitch < minimum_pitch:
            raise NaturalFitLayoutError("resolved axis pitch violates minimum clearance.")
        origins = tuple(origin_min + index * actual_pitch for index in range(count))
        origins = origins[:-1] + (origin_max,)
    return NaturalFitAxisPlan(
        requested_axis=requested_axis,
        footprint_min_m=footprint_min_m,
        footprint_max_m=footprint_max_m,
        plant_min_m=plant_min_m,
        plant_max_m=plant_max_m,
        origin_min_m=origin_min,
        origin_max_m=origin_max,
        usable_span_m=usable_span,
        plant_span_m=plant_span,
        minimum_pitch_m=minimum_pitch,
        count=count,
        actual_pitch_m=actual_pitch,
        origins_m=origins,
    )


def _closest_feasible_count(
    *, usable_span_m: float, minimum_pitch_m: float, target_pitch_m: float
) -> int:
    maximum_count = math.floor(usable_span_m / minimum_pitch_m) + 1
    if maximum_count < 2:
        return 1
    ideal_count = usable_span_m / target_pitch_m + 1.0
    lower = max(2, min(maximum_count, math.floor(ideal_count)))
    upper = max(2, min(maximum_count, math.ceil(ideal_count)))
    candidates = {lower, upper, 2, maximum_count}
    best_count = min(candidates)
    best_error = abs(
        usable_span_m / (best_count - 1) - target_pitch_m
    )
    for count in sorted(candidates):
        error = abs(usable_span_m / (count - 1) - target_pitch_m)
        tied = abs(error - best_error) <= 4.0 * max(
            math.ulp(error), math.ulp(best_error), math.ulp(target_pitch_m)
        )
        if (error < best_error and not tied) or (tied and count > best_count):
            best_count = count
            best_error = error
    return best_count


def _hash_payload(payload: object) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
