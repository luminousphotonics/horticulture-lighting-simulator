from __future__ import annotations

import math

import pytest

from fspm_optics.diagnostics.proposed_source_response import (
    DETERMINISTIC_DIRECT_OPTIONS,
    analytic_empty_aperture_flux,
    analytic_flux,
    analytic_intensity,
    integrate_midpoint_intensity,
    midpoint_angles,
    source_cases,
    unity_dat_text,
)


def test_preregistered_source_cases_close_analytically() -> None:
    cases = source_cases()
    assert [case.case_id for case in cases[:5]] == [
        "native-square-lambertian",
        "equal-area-ring-lambertian",
        "equal-area-ring-unity-flatcorr",
        "equal-area-ring-citizen-flatcorr",
        "equal-area-square-citizen-flatcorr",
    ]
    assert all(
        math.isclose(analytic_flux(case), 1.0, rel_tol=0.0, abs_tol=2e-14)
        for case in cases
    )


@pytest.mark.parametrize("case_index", range(6))
def test_midpoint_analytic_grid_converges(case_index: int) -> None:
    case = source_cases()[case_index]
    values = tuple(
        analytic_intensity(case, theta, phi)
        for theta, phi in midpoint_angles(0.5, 5.0)
    )
    observed = integrate_midpoint_intensity(
        values, theta_step_deg=0.5, phi_step_deg=5.0
    )
    assert observed == pytest.approx(1.0, rel=3e-5, abs=3e-5)


def test_required_deterministic_options_are_frozen() -> None:
    assert DETERMINISTIC_DIRECT_OPTIONS == (
        "-ab",
        "0",
        "-u-",
        "-dj",
        "0",
        "-ds",
        "0",
        "-dt",
        "0",
        "-dc",
        "1",
    )


def test_empty_aperture_acceptance_is_physical() -> None:
    values = [analytic_empty_aperture_flux(case) for case in source_cases()]
    assert all(0.0 < value <= 1.0 for value in values)
    assert values[-1] > values[0]


def test_unity_dat_is_constant_two_dimensional_grid() -> None:
    assert unity_dat_text() == "2\n0 90 2\n0 90 2\n\n1 1 1 1\n"
