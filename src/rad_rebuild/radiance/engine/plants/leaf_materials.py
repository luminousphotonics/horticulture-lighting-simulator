"""Radiance leaf material helpers for FSPM receiver transport."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from rad_rebuild.radiance.engine.plants.optical_profiles import LeafOpticalProfile
from rad_rebuild.radiance.engine.plants.spectral_absorption import (
    WavelengthPhotonDistribution,
)

FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV = "FSPM_LEAF_RADIANCE_MATERIAL_MODE"
LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER = "opaque_occluder"
LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS = "rex_source_weighted_trans"
DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE = LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER
FSPM_LEAF_RADIANCE_MATERIAL_MODES = frozenset(
    {
        LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER,
        LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS,
    }
)
LEAF_MATERIAL_WEIGHTING_BASIS_PAR_400_700 = "par_400_700_nm"
LEAF_MATERIAL_TRANSMISSION_DIFFUSE_ONLY = "diffuse_only"
LEAF_MATERIAL_TRANSMISSION_OPAQUE_OCCLUDER = "opaque_occluder"
LEAF_MATERIAL_METADATA_KEYS: tuple[str, ...] = (
    "leaf_radiance_material_mode",
    "leaf_material_weighting_basis",
    "leaf_material_profile_id",
    "leaf_material_profile_version",
    "leaf_material_source_spectrum_id",
    "leaf_material_source_spectrum_source",
    "leaf_material_effective_reflectance",
    "leaf_material_effective_transmittance",
    "leaf_material_effective_absorptance",
    "leaf_material_radiance_primitive",
    "leaf_material_transmission_assumption",
    "leaf_material_specular_reflectance",
    "leaf_material_specular_transmittance_fraction",
    "leaf_material_radiance_red",
    "leaf_material_radiance_green",
    "leaf_material_radiance_blue",
    "leaf_material_radiance_trans",
    "leaf_material_radiance_tspec",
)


@dataclass(frozen=True)
class LeafMaterialEffectiveCoefficients:
    reflectance: float
    transmittance: float
    absorptance: float
    weighting_basis: str = LEAF_MATERIAL_WEIGHTING_BASIS_PAR_400_700


@dataclass(frozen=True)
class RadianceTransMaterialParameters:
    red: float
    green: float
    blue: float
    spec: float
    rough: float
    trans: float
    tspec: float

    def to_payload(self) -> dict[str, float]:
        return {
            "red": self.red,
            "green": self.green,
            "blue": self.blue,
            "spec": self.spec,
            "rough": self.rough,
            "trans": self.trans,
            "tspec": self.tspec,
        }


def normalize_leaf_radiance_material_mode(value: object) -> str:
    """Return a supported FSPM leaf Radiance material mode."""

    if value is None:
        return DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE
    text = str(value).strip().lower()
    if not text:
        return DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE
    if text not in FSPM_LEAF_RADIANCE_MATERIAL_MODES:
        allowed = ", ".join(sorted(FSPM_LEAF_RADIANCE_MATERIAL_MODES))
        raise ValueError(
            f"Unknown {FSPM_LEAF_RADIANCE_MATERIAL_MODE_ENV}: {value!r}. "
            f"Expected one of: {allowed}."
        )
    return text


def par_source_weighted_leaf_coefficients(
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
) -> LeafMaterialEffectiveCoefficients:
    """Compute PAR-normalized source-weighted leaf optical coefficients."""

    if profile.wavelength_nm != distribution.wavelength_nm:
        raise ValueError("Leaf optical profile and source spectrum wavelength grids must align.")

    indices = [
        index
        for index, wavelength in enumerate(profile.wavelength_nm)
        if 400 <= int(wavelength) < 700
        and float(distribution.photon_fraction_per_nm[index]) > 0.0
    ]
    total_weight = sum(float(distribution.photon_fraction_per_nm[index]) for index in indices)
    if total_weight <= 0.0:
        raise ValueError("Source spectrum has no positive PAR photons on the optical profile grid.")

    reflectance = sum(
        float(distribution.photon_fraction_per_nm[index])
        / total_weight
        * float(profile.reflectance[index])
        for index in indices
    )
    transmittance = sum(
        float(distribution.photon_fraction_per_nm[index])
        / total_weight
        * float(profile.transmittance[index])
        for index in indices
    )
    absorptance = sum(
        float(distribution.photon_fraction_per_nm[index])
        / total_weight
        * float(profile.absorptance[index])
        for index in indices
    )
    return LeafMaterialEffectiveCoefficients(
        reflectance=reflectance,
        transmittance=transmittance,
        absorptance=absorptance,
    )


def fit_diffuse_trans_material(
    coefficients: LeafMaterialEffectiveCoefficients,
    *,
    tolerance: float = 1e-6,
) -> RadianceTransMaterialParameters:
    """Fit diffuse-only Radiance `trans` parameters from desired R/T/A."""

    reflectance = _finite_fraction("R_eff", coefficients.reflectance)
    transmittance = _finite_fraction("T_eff", coefficients.transmittance)
    absorptance = _finite_fraction("A_eff", coefficients.absorptance)
    total = reflectance + transmittance + absorptance
    if not math.isclose(total, 1.0, abs_tol=tolerance):
        raise ValueError(
            "Effective leaf material coefficients must sum to 1 within "
            f"{tolerance:g}: got {total:.12g}."
        )
    reflected_plus_transmitted = reflectance + transmittance
    if reflected_plus_transmitted > 1.0 + tolerance:
        raise ValueError("Effective reflectance + transmittance may not exceed 1.")
    if reflected_plus_transmitted <= 0.0:
        raise ValueError("Effective reflectance + transmittance must be greater than 0.")

    color = reflected_plus_transmitted
    transmitted_fraction = transmittance / reflected_plus_transmitted
    return RadianceTransMaterialParameters(
        red=color,
        green=color,
        blue=color,
        spec=0.0,
        rough=0.0,
        trans=transmitted_fraction,
        tspec=0.0,
    )


def radiance_trans_material_definition(
    material_id: str,
    parameters: RadianceTransMaterialParameters,
) -> str:
    """Render a Radiance `trans` material definition."""

    if not material_id:
        raise ValueError("material_id must be non-empty.")
    return (
        f"void trans {material_id}\n"
        "0\n"
        "0\n"
        f"7 {parameters.red:.6f} {parameters.green:.6f} {parameters.blue:.6f} "
        f"{parameters.spec:.6f} {parameters.rough:.6f} "
        f"{parameters.trans:.6f} {parameters.tspec:.6f}\n"
    )


def opaque_leaf_material_metadata() -> dict[str, Any]:
    return {
        "leaf_radiance_material_mode": LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER,
        "leaf_material_weighting_basis": None,
        "leaf_material_profile_id": None,
        "leaf_material_profile_version": None,
        "leaf_material_source_spectrum_id": None,
        "leaf_material_source_spectrum_source": None,
        "leaf_material_effective_reflectance": None,
        "leaf_material_effective_transmittance": None,
        "leaf_material_effective_absorptance": None,
        "leaf_material_radiance_primitive": "plastic",
        "leaf_material_transmission_assumption": LEAF_MATERIAL_TRANSMISSION_OPAQUE_OCCLUDER,
        "leaf_material_specular_reflectance": 0.0,
        "leaf_material_specular_transmittance_fraction": 0.0,
        "leaf_material_radiance_red": None,
        "leaf_material_radiance_green": None,
        "leaf_material_radiance_blue": None,
        "leaf_material_radiance_trans": None,
        "leaf_material_radiance_tspec": None,
    }


def rex_source_weighted_leaf_material_metadata(
    *,
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
    coefficients: LeafMaterialEffectiveCoefficients,
    parameters: RadianceTransMaterialParameters,
) -> dict[str, Any]:
    return {
        "leaf_radiance_material_mode": LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS,
        "leaf_material_weighting_basis": coefficients.weighting_basis,
        "leaf_material_profile_id": profile.profile_id,
        "leaf_material_profile_version": profile.profile_version,
        "leaf_material_source_spectrum_id": distribution.distribution_id,
        "leaf_material_source_spectrum_source": distribution.source,
        "leaf_material_effective_reflectance": coefficients.reflectance,
        "leaf_material_effective_transmittance": coefficients.transmittance,
        "leaf_material_effective_absorptance": coefficients.absorptance,
        "leaf_material_radiance_primitive": "trans",
        "leaf_material_transmission_assumption": LEAF_MATERIAL_TRANSMISSION_DIFFUSE_ONLY,
        "leaf_material_specular_reflectance": parameters.spec,
        "leaf_material_specular_transmittance_fraction": parameters.tspec,
        "leaf_material_radiance_red": parameters.red,
        "leaf_material_radiance_green": parameters.green,
        "leaf_material_radiance_blue": parameters.blue,
        "leaf_material_radiance_trans": parameters.trans,
        "leaf_material_radiance_tspec": parameters.tspec,
    }


def _finite_fraction(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be in [0, 1].")
    return number
