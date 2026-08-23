"""Bounded basis-matrix solver for spatial PPFD uniformity.

The basis matrix has one row per sensor and one column per control zone.  The
solver minimizes squared spatial error while enforcing the requested mean as a
hard equality.  It performs no Radiance, filesystem, or command execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np
from numpy.typing import NDArray
from scipy.optimize import minimize, nnls

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class UniformityResult:
    """Outcome and field metrics from a control-zone uniformity solve."""

    predicted_ppfd: tuple[float, ...]
    mean: float | None
    standard_deviation: float | None
    cv_percent: float | None
    rmse: float | None
    coefficients: tuple[float, ...]
    success: bool
    failure_reason: str | None
    n_sensors: int
    n_control_zones: int


@dataclass(frozen=True, slots=True)
class RelativeUniformityResult:
    """Target-independent non-negative control-zone shape.

    Coefficients are normalized so the largest value is exactly one. The
    predicted field therefore has arbitrary relative units until an operating
    power is applied by a caller.
    """

    predicted_relative_field: tuple[float, ...]
    coefficients: tuple[float, ...]
    mean: float | None
    standard_deviation: float | None
    cv_percent: float | None
    success: bool
    failure_reason: str | None
    n_sensors: int
    n_control_zones: int


def solve_relative_uniformity(
    basis_matrix: Sequence[Sequence[float]] | FloatArray,
) -> RelativeUniformityResult:
    """Find the non-negative field shape closest to a spatial constant.

    Non-negative least squares against a unit field chooses a scale and shape
    together. After removing that arbitrary scale, its objective is monotonic
    in population CV, so the returned normalized coefficients are a
    target-independent CV-optimal relative schedule. No spatial transforms,
    symmetry operations, or post-solve field corrections are performed.
    """

    matrix = _basis_matrix(basis_matrix)
    n_sensors, n_control_zones = matrix.shape
    if np.any(matrix < 0.0):
        raise ValueError("basis_matrix must not contain negative values.")
    if not np.any(matrix > 0.0):
        return _relative_failure(
            n_sensors,
            n_control_zones,
            "basis matrix cannot produce a positive field.",
        )
    try:
        raw_coefficients, _residual = nnls(
            matrix,
            np.ones(n_sensors, dtype=float),
            maxiter=max(3 * n_control_zones, 500),
        )
    except (ArithmeticError, RuntimeError, ValueError) as exc:
        return _relative_failure(
            n_sensors,
            n_control_zones,
            f"relative uniformity optimizer failed: {exc}",
        )
    maximum = float(np.max(raw_coefficients))
    if not math.isfinite(maximum) or maximum <= 0.0:
        return _relative_failure(
            n_sensors,
            n_control_zones,
            "relative uniformity optimizer returned no positive coefficient.",
        )
    coefficients = np.asarray(raw_coefficients / maximum, dtype=float)
    predicted = np.asarray(matrix @ coefficients, dtype=float)
    mean_ppfd = float(predicted.mean())
    if not np.all(np.isfinite(predicted)) or mean_ppfd <= 0.0:
        return _relative_failure(
            n_sensors,
            n_control_zones,
            "relative uniformity optimizer returned an invalid field.",
        )
    standard_deviation = float(predicted.std(ddof=0))
    return RelativeUniformityResult(
        predicted_relative_field=tuple(float(value) for value in predicted),
        coefficients=tuple(float(value) for value in coefficients),
        mean=mean_ppfd,
        standard_deviation=standard_deviation,
        cv_percent=100.0 * standard_deviation / mean_ppfd,
        success=True,
        failure_reason=None,
        n_sensors=n_sensors,
        n_control_zones=n_control_zones,
    )


def solve_uniformity(
    basis_matrix: Sequence[Sequence[float]] | FloatArray,
    target_ppfd: float,
    lower_bounds: Sequence[float],
    upper_bounds: Sequence[float],
) -> UniformityResult:
    """Solve the default bounded control-zone PPFD uniformity problem.

    ``basis_matrix`` must have shape ``(sensors, control_zones)``.  Bounds are
    explicit per-control-zone sequences; scalar broadcasting is intentionally
    not performed.  Invalid inputs raise ``ValueError``.  A valid but
    infeasible optimization problem returns an unsuccessful result with a
    descriptive reason.
    """

    matrix = _basis_matrix(basis_matrix)
    n_sensors, n_control_zones = matrix.shape
    target = _target(target_ppfd)
    lower = _bound_vector("lower_bounds", lower_bounds, n_control_zones)
    upper = _bound_vector("upper_bounds", upper_bounds, n_control_zones)
    if np.any(lower > upper):
        first = int(np.flatnonzero(lower > upper)[0])
        raise ValueError(
            f"lower_bounds[{first}] must not exceed upper_bounds[{first}]."
        )

    mean_row = np.asarray(matrix.mean(axis=0), dtype=float)
    minimum_mean, maximum_mean = _achievable_mean_range(mean_row, lower, upper)
    equality_tolerance = _equality_tolerance(target)
    if target < minimum_mean - equality_tolerance or target > maximum_mean + equality_tolerance:
        return _failure(
            n_sensors,
            n_control_zones,
            "target_ppfd is infeasible under the supplied control-zone bounds; "
            f"target={target:.12g}, achievable mean range="
            f"[{minimum_mean:.12g}, {maximum_mean:.12g}].",
        )

    initial = _feasible_mean_point(mean_row, target, lower, upper)
    target_field = np.full(n_sensors, target, dtype=float)

    def objective(coefficients: FloatArray) -> float:
        residual = matrix @ coefficients - target_field
        return 0.5 * float(residual @ residual)

    def gradient(coefficients: FloatArray) -> FloatArray:
        return np.asarray(matrix.T @ (matrix @ coefficients - target_field), dtype=float)

    all_fixed = bool(np.all(lower == upper))
    zero_mean_row = bool(np.all(mean_row == 0.0))
    if all_fixed:
        coefficients = initial
        solver_success = True
        solver_message = "all control-zone coefficients are fixed by their bounds"
    else:
        constraints: tuple[dict[str, object], ...] = ()
        method = "L-BFGS-B"
        if not zero_mean_row:
            constraints = (
                {
                    "type": "eq",
                    "fun": lambda values: float(mean_row @ values - target),
                    "jac": lambda _values: mean_row,
                },
            )
            method = "SLSQP"
        try:
            optimized = minimize(
                objective,
                initial,
                method=method,
                jac=gradient,
                bounds=list(zip(lower, upper, strict=True)),
                constraints=constraints,
                options={"ftol": 1e-12, "maxiter": 5000}
                if method == "SLSQP"
                else {"ftol": 1e-12, "gtol": 1e-10, "maxiter": 5000},
            )
        except (ArithmeticError, ValueError) as exc:
            return _failure(
                n_sensors,
                n_control_zones,
                f"uniformity optimizer failed: {exc}",
            )
        coefficients = np.asarray(optimized.x, dtype=float)
        solver_success = bool(optimized.success)
        solver_message = str(optimized.message)

    if not solver_success:
        return _failure(
            n_sensors,
            n_control_zones,
            f"uniformity optimizer did not converge: {solver_message}",
        )
    if not np.all(np.isfinite(coefficients)):
        return _failure(
            n_sensors,
            n_control_zones,
            "uniformity optimizer returned non-finite coefficients.",
        )

    bound_tolerance = 1e-9
    below = coefficients < lower - bound_tolerance
    above = coefficients > upper + bound_tolerance
    if np.any(below) or np.any(above):
        return _failure(
            n_sensors,
            n_control_zones,
            "uniformity optimizer returned coefficients outside the supplied bounds.",
        )

    predicted = np.asarray(matrix @ coefficients, dtype=float)
    mean_ppfd = float(predicted.mean())
    mean_error = abs(mean_ppfd - target)
    if mean_error > equality_tolerance:
        return _failure(
            n_sensors,
            n_control_zones,
            "hard target mean constraint was not met; "
            f"target={target:.12g}, result={mean_ppfd:.12g}, "
            f"absolute error={mean_error:.12g}.",
        )

    standard_deviation = float(predicted.std(ddof=0))
    if abs(mean_ppfd) <= 1e-15:
        cv_percent = 0.0 if standard_deviation <= 1e-15 else math.inf
    else:
        cv_percent = 100.0 * standard_deviation / abs(mean_ppfd)
    rmse = float(np.sqrt(np.mean(np.square(predicted - target))))
    return UniformityResult(
        predicted_ppfd=tuple(float(value) for value in predicted),
        mean=mean_ppfd,
        standard_deviation=standard_deviation,
        cv_percent=cv_percent,
        rmse=rmse,
        coefficients=tuple(float(value) for value in coefficients),
        success=True,
        failure_reason=None,
        n_sensors=n_sensors,
        n_control_zones=n_control_zones,
    )


def _basis_matrix(values: Sequence[Sequence[float]] | FloatArray) -> FloatArray:
    try:
        matrix = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("basis_matrix must be a rectangular numeric matrix.") from exc
    if matrix.ndim != 2:
        raise ValueError(
            "basis_matrix must be two-dimensional with shape "
            f"sensors x control_zones; got shape {matrix.shape}."
        )
    if matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise ValueError("basis_matrix must contain at least one sensor and control zone.")
    if not np.all(np.isfinite(matrix)):
        raise ValueError("basis_matrix must contain only finite values.")
    return np.asarray(matrix, dtype=float)


def _relative_failure(
    n_sensors: int,
    n_control_zones: int,
    reason: str,
) -> RelativeUniformityResult:
    return RelativeUniformityResult(
        predicted_relative_field=(),
        coefficients=(),
        mean=None,
        standard_deviation=None,
        cv_percent=None,
        success=False,
        failure_reason=reason,
        n_sensors=n_sensors,
        n_control_zones=n_control_zones,
    )


def _target(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("target_ppfd must be a finite non-negative number.")
    target = float(value)
    if not math.isfinite(target) or target < 0.0:
        raise ValueError("target_ppfd must be a finite non-negative number.")
    return target


def _bound_vector(name: str, values: Sequence[float], expected: int) -> FloatArray:
    if isinstance(values, str):
        raise ValueError(f"{name} must contain one value per control zone.")
    try:
        vector = np.asarray(values, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must contain one numeric value per control zone.") from exc
    if vector.ndim != 1 or len(vector) != expected:
        actual = len(vector) if vector.ndim == 1 else 0
        raise ValueError(
            f"{name} length {actual} must match the control-zone count {expected}."
        )
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} must contain only finite values.")
    return np.asarray(vector, dtype=float)


def _achievable_mean_range(
    mean_row: FloatArray,
    lower: FloatArray,
    upper: FloatArray,
) -> tuple[float, float]:
    minimum_point = np.where(mean_row >= 0.0, lower, upper)
    maximum_point = np.where(mean_row >= 0.0, upper, lower)
    return float(mean_row @ minimum_point), float(mean_row @ maximum_point)


def _feasible_mean_point(
    mean_row: FloatArray,
    target: float,
    lower: FloatArray,
    upper: FloatArray,
) -> FloatArray:
    point = np.where(mean_row >= 0.0, lower, upper).astype(float)
    remaining = target - float(mean_row @ point)
    tolerance = _equality_tolerance(target)
    if remaining <= tolerance:
        return np.asarray(point, dtype=float)
    for index, mean_coefficient in enumerate(mean_row):
        capacity = abs(float(mean_coefficient)) * (upper[index] - lower[index])
        if capacity <= 0.0:
            continue
        contribution = min(remaining, capacity)
        if mean_coefficient >= 0.0:
            point[index] += contribution / mean_coefficient
        else:
            point[index] -= contribution / abs(mean_coefficient)
        remaining -= contribution
        if remaining <= tolerance:
            break
    return np.asarray(point, dtype=float)


def _equality_tolerance(target: float) -> float:
    return max(1e-8, abs(target) * 1e-10)


def _failure(
    n_sensors: int,
    n_control_zones: int,
    reason: str,
) -> UniformityResult:
    return UniformityResult(
        predicted_ppfd=(),
        mean=None,
        standard_deviation=None,
        cv_percent=None,
        rmse=None,
        coefficients=(),
        success=False,
        failure_reason=reason,
        n_sensors=n_sensors,
        n_control_zones=n_control_zones,
    )
