"""Scalar PAR photon, electrical-power, and Radiance conversions."""

from __future__ import annotations

import math
from typing import Final

SCALAR_PAR_CARRIER_BASIS: Final = "photon_radiance_umol_s_m2_sr"
SCALAR_PAR_RGB_POLICY: Final = "R=G=B scalar PAR photon carrier"
SCALAR_PAR_CONVERSION_POLICY: Final = (
    "direct photon transport; no photopic 179 lm/W conversion"
)


def watts_to_photon_flux_umol_s(watts: float, ppe_umol_per_j: float) -> float:
    """Convert electrical watts and PPE to photon flux in micromoles/second."""

    power = _non_negative_finite("watts", watts)
    ppe = _positive_finite("ppe_umol_per_j", ppe_umol_per_j)
    return power * ppe


def photon_flux_umol_s_to_watts(
    photon_flux_umol_s: float, ppe_umol_per_j: float
) -> float:
    """Convert photon flux back to electrical watts for a stated PPE."""

    photon_flux = _non_negative_finite(
        "photon_flux_umol_s", photon_flux_umol_s
    )
    ppe = _positive_finite("ppe_umol_per_j", ppe_umol_per_j)
    return photon_flux / ppe


def photon_flux_to_lambertian_radiance(
    photon_flux_umol_s: float, emitting_area_m2: float
) -> float:
    """Convert hemispherical photon flux to uniform Lambertian radiance.

    For a Lambertian plane, hemispherical flux is ``pi * area * radiance``.
    The result is the scalar value assigned equally to Radiance R, G, and B.
    """

    photon_flux = _non_negative_finite(
        "photon_flux_umol_s", photon_flux_umol_s
    )
    area = _positive_finite("emitting_area_m2", emitting_area_m2)
    return photon_flux / (math.pi * area)


def lambertian_radiance_to_photon_flux(
    radiance_umol_s_m2_sr: float, emitting_area_m2: float
) -> float:
    """Invert :func:`photon_flux_to_lambertian_radiance`."""

    radiance = _non_negative_finite(
        "radiance_umol_s_m2_sr", radiance_umol_s_m2_sr
    )
    area = _positive_finite("emitting_area_m2", emitting_area_m2)
    return radiance * math.pi * area


def watts_to_lambertian_radiance(
    watts: float,
    ppe_umol_per_j: float,
    emitting_area_m2: float,
) -> float:
    """Convert module watts directly to scalar PAR photon radiance."""

    return photon_flux_to_lambertian_radiance(
        watts_to_photon_flux_umol_s(watts, ppe_umol_per_j),
        emitting_area_m2,
    )


def _positive_finite(name: str, value: float) -> float:
    number = _finite_number(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _non_negative_finite(name: str, value: float) -> float:
    number = _finite_number(name, value)
    if number < 0.0:
        raise ValueError(f"{name} must be finite and non-negative.")
    return number


def _finite_number(name: str, value: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a finite number.") from exc
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number.")
    return number
