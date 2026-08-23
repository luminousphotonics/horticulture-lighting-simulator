"""Scalar RGB parsing and column stacking for isolated SMD bases."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
from numpy.typing import NDArray

from fspm_optics.transport.scalar_ppfd import parse_rtrace_rgb_rows

FloatArray = NDArray[np.float64]


def parse_basis_column(
    rgb_text: str,
    *,
    expected_sensor_count: int | None = None,
) -> FloatArray:
    """Parse one isolated scalar trace output into a sensor-length column."""

    values = parse_rtrace_rgb_rows(rgb_text)
    if expected_sensor_count is not None:
        if (
            isinstance(expected_sensor_count, bool)
            or not isinstance(expected_sensor_count, int)
            or expected_sensor_count <= 0
        ):
            raise ValueError("expected_sensor_count must be a positive integer.")
        if len(values) != expected_sensor_count:
            raise ValueError(
                "basis column sensor count mismatch: "
                f"expected {expected_sensor_count}, got {len(values)}."
            )
    return np.asarray(values, dtype=float)


def stack_basis_columns(
    columns: Sequence[Sequence[float] | FloatArray],
    *,
    expected_sensor_count: int | None = None,
    expected_control_zone_count: int | None = None,
) -> FloatArray:
    """Stack sensor-length columns into ``A[sensors, control_zones]``."""

    if not columns:
        raise ValueError("at least one basis column is required.")
    arrays: list[FloatArray] = []
    for index, column in enumerate(columns):
        array = np.asarray(column, dtype=float)
        if array.ndim != 1 or array.size == 0:
            raise ValueError(f"basis column {index} must be a non-empty vector.")
        if not np.all(np.isfinite(array)):
            raise ValueError(f"basis column {index} contains non-finite values.")
        if np.any(array < 0.0):
            raise ValueError(f"basis column {index} contains negative PPFD values.")
        arrays.append(array)
    sensor_count = len(arrays[0])
    if any(len(array) != sensor_count for array in arrays[1:]):
        raise ValueError("all basis columns must have the same sensor count.")
    if expected_sensor_count is not None and sensor_count != expected_sensor_count:
        raise ValueError(
            "basis sensor count mismatch: "
            f"expected {expected_sensor_count}, got {sensor_count}."
        )
    if (
        expected_control_zone_count is not None
        and len(arrays) != expected_control_zone_count
    ):
        raise ValueError(
            "basis control-zone count mismatch: "
            f"expected {expected_control_zone_count}, got {len(arrays)}."
        )
    return np.asarray(np.column_stack(arrays), dtype=float)


def parse_and_stack_basis_columns(
    rgb_outputs: Sequence[str],
    *,
    expected_sensor_count: int,
    expected_control_zone_count: int,
) -> FloatArray:
    """Parse ordered isolated outputs and assemble the complete basis matrix."""

    columns = tuple(
        parse_basis_column(text, expected_sensor_count=expected_sensor_count)
        for text in rgb_outputs
    )
    return stack_basis_columns(
        columns,
        expected_sensor_count=expected_sensor_count,
        expected_control_zone_count=expected_control_zone_count,
    )
