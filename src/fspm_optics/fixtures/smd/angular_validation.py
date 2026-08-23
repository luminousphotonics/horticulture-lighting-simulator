"""Shared optical-axis validation for completed-aperture angular profiles."""

from __future__ import annotations

import math
from typing import Final, Sequence

AXIS_PLATEAU_RELATIVE_TOLERANCE: Final = 0.005
AXIS_PLATEAU_BIN_WIDTH_DEG: Final = 2.0
AXIS_PLATEAU_BIN_CENTERS_DEG: Final = tuple(
    float(value) for value in range(0, 91, 2)
)


class AngularProfileValidationError(ValueError):
    """A completed-aperture profile failed its physical-axis contract."""


def evaluate_axis_plateau(
    angles_deg: Sequence[float],
    radiant_intensity: Sequence[float],
    *,
    context: str = "completed-aperture profile",
) -> dict[str, object]:
    """Validate the optical-axis plateau after the fixed 2° bin mean.

    Input arrays are copied, never normalized or mutated. Both raw and
    filtered peak observations remain available for authentication and audit.
    """

    angles = tuple(float(value) for value in angles_deg)
    values = tuple(float(value) for value in radiant_intensity)
    if (
        len(angles) != len(values)
        or len(angles) < 2
        or angles[0] != 0.0
        or angles[-1] != 90.0
        or any(
            not math.isfinite(value) or value < 0.0 for value in values
        )
        or any(
            second <= first
            for first, second in zip(angles, angles[1:])
        )
    ):
        raise AngularProfileValidationError(
            f"{context} has malformed raw angular arrays."
        )
    raw_observation = _peak_observation(angles, values)
    filtered = tuple(
        _centered_bin_mean(
            angles,
            values,
            center=center,
            width=AXIS_PLATEAU_BIN_WIDTH_DEG,
        )
        for center in AXIS_PLATEAU_BIN_CENTERS_DEG
    )
    filtered_observation = _peak_observation(
        AXIS_PLATEAU_BIN_CENTERS_DEG, filtered
    )
    excess = float(filtered_observation["relative_excess_above_optical_axis"])
    if excess > AXIS_PLATEAU_RELATIVE_TOLERANCE:
        raise AngularProfileValidationError(
            f"{context} filtered peak exceeds the optical-axis plateau at "
            f"{float(filtered_observation['maximum_angle_deg']):.6g}°: "
            f"relative excess {excess:.9g} exceeds "
            f"{AXIS_PLATEAU_RELATIVE_TOLERANCE:.9g}."
        )
    filtered_axis = filtered[0]
    filtered_normalized = tuple(value / filtered_axis for value in filtered)
    return {
        "tolerance_relative_excess": AXIS_PLATEAU_RELATIVE_TOLERANCE,
        "filter": "2_degree_centered_angular_bin_mean",
        "normalization_reference": "filtered_optical_axis_intensity",
        "raw_observation": raw_observation,
        "filtered_observation": filtered_observation,
        "accepted": True,
        "filtered_angle_deg": list(AXIS_PLATEAU_BIN_CENTERS_DEG),
        "filtered_optical_axis_normalized_radiant_intensity": list(
            filtered_normalized
        ),
    }


def _centered_bin_mean(
    angles: Sequence[float],
    values: Sequence[float],
    *,
    center: float,
    width: float,
) -> float:
    lower = max(float(angles[0]), center - width / 2.0)
    upper = min(float(angles[-1]), center + width / 2.0)
    if upper <= lower:
        raise AngularProfileValidationError(
            "presentation angular bin has no support."
        )
    knots = [lower]
    knots.extend(angle for angle in angles if lower < angle < upper)
    knots.append(upper)
    area = math.fsum(
        0.5
        * (
            _interpolate(angles, values, start)
            + _interpolate(angles, values, end)
        )
        * (end - start)
        for start, end in zip(knots, knots[1:])
    )
    return area / (upper - lower)


def _peak_observation(
    angles: Sequence[float],
    values: Sequence[float],
) -> dict[str, float]:
    optical_axis = float(values[0])
    if not math.isfinite(optical_axis) or optical_axis <= 0.0:
        raise AngularProfileValidationError(
            "completed-aperture optical-axis intensity must be positive."
        )
    maximum_index = max(
        range(len(values)), key=lambda index: float(values[index])
    )
    maximum = float(values[maximum_index])
    return {
        "optical_axis_intensity": optical_axis,
        "global_maximum_intensity": maximum,
        "maximum_angle_deg": float(angles[maximum_index]),
        "relative_excess_above_optical_axis": max(
            0.0, (maximum - optical_axis) / optical_axis
        ),
    }


def _interpolate(
    angles: Sequence[float],
    values: Sequence[float],
    target: float,
) -> float:
    if target <= angles[0]:
        return float(values[0])
    for left, right, value_left, value_right in zip(
        angles[:-1], angles[1:], values[:-1], values[1:], strict=True
    ):
        if target <= right:
            fraction = (target - left) / (right - left)
            return float(value_left) + fraction * (
                float(value_right) - float(value_left)
            )
    return float(values[-1])


__all__ = [
    "AXIS_PLATEAU_BIN_CENTERS_DEG",
    "AXIS_PLATEAU_BIN_WIDTH_DEG",
    "AXIS_PLATEAU_RELATIVE_TOLERANCE",
    "AngularProfileValidationError",
    "evaluate_axis_plateau",
]
