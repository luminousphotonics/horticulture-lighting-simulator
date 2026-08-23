from __future__ import annotations

import numpy as np
import pytest

from fspm_optics.optimization.uniformity import solve_uniformity


def test_solves_feasible_basis_with_exact_target_mean() -> None:
    result = solve_uniformity(
        [[1.0, 0.0], [0.0, 1.0]],
        target_ppfd=5.0,
        lower_bounds=[0.0, 0.0],
        upper_bounds=[10.0, 10.0],
    )
    assert result.success
    assert result.failure_reason is None
    assert result.coefficients == pytest.approx((5.0, 5.0))
    assert result.predicted_ppfd == pytest.approx((5.0, 5.0))
    assert result.mean == pytest.approx(5.0, abs=1e-9)
    assert result.rmse == pytest.approx(0.0, abs=1e-9)


def test_minimizes_variance_when_multiple_mean_feasible_solutions_exist() -> None:
    result = solve_uniformity(
        [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]],
        target_ppfd=10.0,
        lower_bounds=[0.0, 0.0],
        upper_bounds=[20.0, 20.0],
    )
    assert result.success
    assert result.coefficients == pytest.approx((7.5, 7.5), abs=1e-7)
    assert result.predicted_ppfd == pytest.approx((7.5, 7.5, 15.0), abs=1e-7)
    assert result.mean == pytest.approx(10.0, abs=1e-9)


def test_rejects_infeasible_target_without_relaxing_bounds() -> None:
    result = solve_uniformity(
        [[1.0], [1.0]],
        target_ppfd=2.0,
        lower_bounds=[0.0],
        upper_bounds=[1.0],
    )
    assert not result.success
    assert result.coefficients == ()
    assert result.predicted_ppfd == ()
    assert result.failure_reason is not None
    assert "infeasible" in result.failure_reason
    assert "[0, 1]" in result.failure_reason


@pytest.mark.parametrize(
    "matrix",
    [
        [1.0, 2.0],
        [],
        [[], []],
        [[1.0, float("nan")]],
    ],
)
def test_rejects_invalid_basis_matrix_shapes_and_values(matrix: object) -> None:
    with pytest.raises(ValueError, match="basis_matrix"):
        solve_uniformity(matrix, 1.0, [0.0], [2.0])  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("lower", "upper"),
    [
        ([0.0], [1.0, 1.0]),
        ([0.0, 0.0], [1.0]),
        ([0.0, 2.0], [1.0, 1.0]),
    ],
)
def test_rejects_mismatched_or_reversed_bounds(
    lower: list[float], upper: list[float]
) -> None:
    with pytest.raises(ValueError, match="bound|control-zone"):
        solve_uniformity([[1.0, 1.0]], 1.0, lower, upper)


@pytest.mark.parametrize("control_zones", [1, 3, 7])
def test_control_zone_count_is_derived_from_layout_basis_columns(
    control_zones: int,
) -> None:
    matrix = np.ones((2, control_zones), dtype=float)
    result = solve_uniformity(
        matrix,
        target_ppfd=3.0,
        lower_bounds=[0.0] * control_zones,
        upper_bounds=[3.0] * control_zones,
    )
    assert result.success
    assert result.n_control_zones == control_zones
    assert len(result.coefficients) == control_zones
    assert result.mean == pytest.approx(3.0, abs=1e-9)


def test_reports_population_cv_percent_and_rmse() -> None:
    result = solve_uniformity(
        [[1.0], [2.0]],
        target_ppfd=3.0,
        lower_bounds=[0.0],
        upper_bounds=[4.0],
    )
    assert result.success
    assert result.predicted_ppfd == pytest.approx((2.0, 4.0))
    assert result.mean == pytest.approx(3.0)
    assert result.standard_deviation == pytest.approx(1.0)
    assert result.cv_percent == pytest.approx(100.0 / 3.0)
    assert result.rmse == pytest.approx(1.0)
