"""Room-fit helpers for deterministic FSPM plant grids."""

from __future__ import annotations

from dataclasses import dataclass
import math

FEET_TO_METERS = 0.3048
DEFAULT_PLANT_TARGET_SPACING_M = 0.40
DEFAULT_PLANT_CLEARANCE_M = 0.005
PLANT_GRID_CALIBRATION_ROOM_FT = 10.0
PLANT_GRID_CALIBRATION_COUNT = 8
CALIBRATED_PLANT_EDGE_CENTER_MARGIN_M = (
    PLANT_GRID_CALIBRATION_ROOM_FT * FEET_TO_METERS
    - (PLANT_GRID_CALIBRATION_COUNT - 1) * DEFAULT_PLANT_TARGET_SPACING_M
) / 2.0


@dataclass(frozen=True)
class PlantGridAxisLayout:
    """Fitted plant-center positions for one room axis."""

    axis_m: float
    count: int
    spacing_m: float
    edge_center_margin_m: float


@dataclass(frozen=True)
class PlantGridLayout:
    """Fitted row/column plant-grid layout for a room footprint."""

    length: PlantGridAxisLayout
    width: PlantGridAxisLayout


def canonical_room_dimensions_ft(
    length_ft: float | int,
    width_ft: float | int,
) -> tuple[float, float]:
    """Return canonical room dimensions as length >= width."""

    length = _positive_finite("length_ft", length_ft)
    width = _positive_finite("width_ft", width_ft)
    return (max(length, width), min(length, width))


def fit_plant_grid_axis(
    axis_ft: float | int,
    *,
    target_spacing_m: float = DEFAULT_PLANT_TARGET_SPACING_M,
    edge_center_margin_m: float = CALIBRATED_PLANT_EDGE_CENTER_MARGIN_M,
    local_half_extent_m: float = 0.0,
    clearance_m: float = DEFAULT_PLANT_CLEARANCE_M,
    count: int | None = None,
) -> PlantGridAxisLayout:
    """Return a deterministic center grid that fits one room axis."""

    axis_m = _positive_finite("axis_ft", axis_ft) * FEET_TO_METERS
    target_spacing = _positive_finite("target_spacing_m", target_spacing_m)
    calibrated_margin = _non_negative_finite(
        "edge_center_margin_m",
        edge_center_margin_m,
    )
    local_half_extent = _non_negative_finite("local_half_extent_m", local_half_extent_m)
    clearance = _non_negative_finite("clearance_m", clearance_m)
    margin = max(calibrated_margin, local_half_extent + clearance)
    if local_half_extent + clearance >= axis_m / 2.0:
        raise ValueError("Plant local footprint does not fit inside room axis.")
    if 2.0 * margin >= axis_m:
        margin = max(0.0, axis_m / 2.0 - local_half_extent - clearance)
    start_count = (
        max(1, int(round(axis_m / target_spacing)))
        if count is None
        else _positive_int("count", count)
    )
    resolved_count = _count_with_spacing_closest_to_target(
        axis_m=axis_m,
        margin_m=margin,
        target_spacing_m=target_spacing,
        max_count=start_count,
    )
    spacing = (
        (axis_m - 2.0 * margin) / (resolved_count - 1)
        if resolved_count > 1
        else 0.0
    )
    return PlantGridAxisLayout(
        axis_m=axis_m,
        count=resolved_count,
        spacing_m=spacing,
        edge_center_margin_m=margin,
    )


def fit_plant_grid(
    length_ft: float | int,
    width_ft: float | int,
    *,
    target_spacing_m: float = DEFAULT_PLANT_TARGET_SPACING_M,
    edge_center_margin_m: float = CALIBRATED_PLANT_EDGE_CENTER_MARGIN_M,
    local_half_extent_length_m: float = 0.0,
    local_half_extent_width_m: float = 0.0,
    clearance_m: float = DEFAULT_PLANT_CLEARANCE_M,
    rows: int | None = None,
    columns: int | None = None,
) -> PlantGridLayout:
    """Fit rows to length_ft and columns to width_ft without sorting axes."""

    return PlantGridLayout(
        length=fit_plant_grid_axis(
            length_ft,
            target_spacing_m=target_spacing_m,
            edge_center_margin_m=edge_center_margin_m,
            local_half_extent_m=local_half_extent_length_m,
            clearance_m=clearance_m,
            count=rows,
        ),
        width=fit_plant_grid_axis(
            width_ft,
            target_spacing_m=target_spacing_m,
            edge_center_margin_m=edge_center_margin_m,
            local_half_extent_m=local_half_extent_width_m,
            clearance_m=clearance_m,
            count=columns,
        ),
    )


def _count_with_spacing_closest_to_target(
    *,
    axis_m: float,
    margin_m: float,
    target_spacing_m: float,
    max_count: int,
) -> int:
    if max_count <= 1:
        return 1
    usable_span_m = axis_m - 2.0 * margin_m
    if usable_span_m <= 0.0:
        return 1
    candidates = range(2, max_count + 1)
    return min(
        candidates,
        key=lambda candidate: (
            abs((usable_span_m / (candidate - 1)) - target_spacing_m),
            -candidate,
        ),
    )


def _positive_finite(name: str, value: float | int) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be a positive finite value.")
    return number


def _non_negative_finite(name: str, value: float | int) -> float:
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{name} must be a non-negative finite value.")
    return number


def _positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer.")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero.")
    return value
