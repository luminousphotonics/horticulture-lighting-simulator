from __future__ import annotations

import math

import pytest

from fspm_optics.fixtures.smd.photons import (
    SCALAR_PAR_CONVERSION_POLICY,
    SCALAR_PAR_RGB_POLICY,
    lambertian_radiance_to_photon_flux,
    photon_flux_to_lambertian_radiance,
    photon_flux_umol_s_to_watts,
    watts_to_lambertian_radiance,
    watts_to_photon_flux_umol_s,
)


def test_watt_and_photon_flux_conversions_are_deterministic_and_invertible() -> None:
    photon_flux = watts_to_photon_flux_umol_s(100.0, 2.8)
    assert photon_flux == pytest.approx(280.0)
    assert photon_flux_umol_s_to_watts(photon_flux, 2.8) == pytest.approx(100.0)


def test_lambertian_radiance_conversion_preserves_total_photon_flux() -> None:
    area = 0.12**2
    radiance = photon_flux_to_lambertian_radiance(280.0, area)
    assert radiance == pytest.approx(280.0 / (math.pi * area))
    assert lambertian_radiance_to_photon_flux(radiance, area) == pytest.approx(280.0)
    assert watts_to_lambertian_radiance(100.0, 2.8, area) == pytest.approx(
        radiance
    )


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda: watts_to_photon_flux_umol_s(-1.0, 2.8), "watts"),
        (lambda: watts_to_photon_flux_umol_s(1.0, 0.0), "ppe_umol_per_j"),
        (lambda: photon_flux_umol_s_to_watts(float("nan"), 2.8), "photon_flux"),
        (lambda: photon_flux_to_lambertian_radiance(1.0, 0.0), "emitting_area"),
    ],
)
def test_invalid_power_ppe_flux_and_area_fail_clearly(call: object, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        call()  # type: ignore[operator]


def test_scalar_par_carrier_contract_is_explicit() -> None:
    assert SCALAR_PAR_RGB_POLICY == "R=G=B scalar PAR photon carrier"
    assert "no photopic 179" in SCALAR_PAR_CONVERSION_POLICY
