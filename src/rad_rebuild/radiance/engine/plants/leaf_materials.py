"""Radiance leaf material helpers for FSPM receiver transport."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from rad_rebuild.radiance.engine.plants.optical_profiles import LeafOpticalProfile
from rad_rebuild.radiance.engine.plants.spectral_absorption import (
    SCALAR_FLUX_BASIS_PAR_PPFD,
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
FSPM_SPECTRAL_TRANSPORT_MODE_ENV = "FSPM_SPECTRAL_TRANSPORT_MODE"
SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED = "scalar_source_weighted"
SPECTRAL_TRANSPORT_MODE_BANDED_5 = "banded_5"
DEFAULT_FSPM_SPECTRAL_TRANSPORT_MODE = SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED
FSPM_SPECTRAL_TRANSPORT_MODES = frozenset(
    {
        SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED,
        SPECTRAL_TRANSPORT_MODE_BANDED_5,
    }
)
LEAF_MATERIAL_WEIGHTING_BASIS_PAR_400_700 = "par_400_700_nm"
LEAF_MATERIAL_WEIGHTING_BASIS_BAND_SOURCE_WEIGHTED = "band_source_weighted"
LEAF_MATERIAL_TRANSMISSION_DIFFUSE_ONLY = "diffuse_only"
LEAF_MATERIAL_TRANSMISSION_OPAQUE_OCCLUDER = "opaque_occluder"
BAND_SCALING_BASIS_SOURCE_BAND_FRACTION_RELATIVE_TO_PAR = (
    "source_band_photon_fraction_relative_to_par"
)
PAR_BAND_IDS: tuple[str, ...] = ("blue", "green", "orange", "red")
EPAR_BAND_IDS: tuple[str, ...] = (*PAR_BAND_IDS, "far_red")
LEAF_MATERIAL_METADATA_KEYS: tuple[str, ...] = (
    "fspm_spectral_transport_mode",
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
class SpectralTransportBandDefinition:
    band_id: str
    wavelength_min_nm: int
    wavelength_max_nm: int
    included_in_par: bool
    included_in_epar: bool

    def contains_wavelength(self, wavelength_nm: int) -> bool:
        return self.wavelength_min_nm <= int(wavelength_nm) <= self.wavelength_max_nm

    def to_payload(self) -> dict[str, Any]:
        return {
            "band_id": self.band_id,
            "wavelength_min_nm": self.wavelength_min_nm,
            "wavelength_max_nm": self.wavelength_max_nm,
            "included_in_par": self.included_in_par,
            "included_in_epar": self.included_in_epar,
        }


FSPM_BANDED_5_TRANSPORT_BANDS: tuple[SpectralTransportBandDefinition, ...] = (
    SpectralTransportBandDefinition("blue", 400, 499, True, True),
    SpectralTransportBandDefinition("green", 500, 599, True, True),
    SpectralTransportBandDefinition("orange", 600, 624, True, True),
    SpectralTransportBandDefinition("red", 625, 699, True, True),
    SpectralTransportBandDefinition("far_red", 700, 750, False, True),
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


@dataclass(frozen=True)
class BandedTransportBandMaterial:
    band: SpectralTransportBandDefinition
    source_photon_fraction_relative_to_par: float
    band_has_source_photons: bool
    receiver_trace_required: bool
    coefficients: LeafMaterialEffectiveCoefficients | None
    parameters: RadianceTransMaterialParameters | None

    def to_payload(self) -> dict[str, Any]:
        if self.coefficients is None:
            effective_reflectance = 0.0
            effective_transmittance = 0.0
            effective_absorptance = 0.0
        else:
            effective_reflectance = self.coefficients.reflectance
            effective_transmittance = self.coefficients.transmittance
            effective_absorptance = self.coefficients.absorptance

        if self.parameters is None:
            radiance_primitive = "none"
            radiance_red = 0.0
            radiance_green = 0.0
            radiance_blue = 0.0
            radiance_trans = 0.0
            radiance_tspec = 0.0
        else:
            radiance_primitive = "trans"
            radiance_red = self.parameters.red
            radiance_green = self.parameters.green
            radiance_blue = self.parameters.blue
            radiance_trans = self.parameters.trans
            radiance_tspec = self.parameters.tspec

        return {
            **self.band.to_payload(),
            "source_photon_fraction_relative_to_par": (
                self.source_photon_fraction_relative_to_par
            ),
            "band_has_source_photons": self.band_has_source_photons,
            "receiver_trace_required": self.receiver_trace_required,
            "effective_reflectance": effective_reflectance,
            "effective_transmittance": effective_transmittance,
            "effective_absorptance": effective_absorptance,
            "radiance_primitive": radiance_primitive,
            "radiance_red": radiance_red,
            "radiance_green": radiance_green,
            "radiance_blue": radiance_blue,
            "radiance_trans": radiance_trans,
            "radiance_tspec": radiance_tspec,
        }


@dataclass(frozen=True)
class BandedTransportMaterialPlan:
    profile: LeafOpticalProfile
    distribution: WavelengthPhotonDistribution
    bands: tuple[BandedTransportBandMaterial, ...]
    spectral_transport_mode: str = SPECTRAL_TRANSPORT_MODE_BANDED_5
    band_scaling_basis: str = BAND_SCALING_BASIS_SOURCE_BAND_FRACTION_RELATIVE_TO_PAR

    def to_payload(self) -> dict[str, Any]:
        return {
            "fspm_spectral_transport_mode": self.spectral_transport_mode,
            "band_scaling_basis": self.band_scaling_basis,
            "banded_transport_band_count": len(self.bands),
            "banded_transport_bands": [band.to_payload() for band in self.bands],
            "par_band_ids": list(PAR_BAND_IDS),
            "epar_band_ids": list(EPAR_BAND_IDS),
            "scalar_flux_basis": self.distribution.scalar_flux_basis,
            "leaf_material_weighting_basis": LEAF_MATERIAL_WEIGHTING_BASIS_BAND_SOURCE_WEIGHTED,
            "leaf_material_profile_id": self.profile.profile_id,
            "leaf_material_profile_version": self.profile.profile_version,
            "leaf_material_source_spectrum_id": self.distribution.distribution_id,
            "leaf_material_source_spectrum_source": self.distribution.source,
            "source_spectrum_id": self.distribution.distribution_id,
            "source_spectrum_source": self.distribution.source,
            "source_spectral_basis": self.distribution.source_spectral_basis,
            "source_spectrum_basis": self.distribution.source_spectral_basis,
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


def normalize_fspm_spectral_transport_mode(value: object) -> str:
    """Return a supported FSPM spectral transport mode."""

    if value is None:
        return DEFAULT_FSPM_SPECTRAL_TRANSPORT_MODE
    text = str(value).strip().lower()
    if not text:
        return DEFAULT_FSPM_SPECTRAL_TRANSPORT_MODE
    if text not in FSPM_SPECTRAL_TRANSPORT_MODES:
        allowed = ", ".join(sorted(FSPM_SPECTRAL_TRANSPORT_MODES))
        raise ValueError(
            f"Unknown {FSPM_SPECTRAL_TRANSPORT_MODE_ENV}: {value!r}. "
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


def band_source_weighted_leaf_material(
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
    band: SpectralTransportBandDefinition,
) -> BandedTransportBandMaterial:
    """Compute a Level 3A band material scaffold for one spectral band."""

    _validate_profile_distribution_alignment(profile, distribution)
    source_fraction = sum(
        float(distribution.photon_fraction_per_nm[index])
        for index, wavelength in enumerate(distribution.wavelength_nm)
        if band.contains_wavelength(wavelength)
    )
    if source_fraction <= 0.0:
        return BandedTransportBandMaterial(
            band=band,
            source_photon_fraction_relative_to_par=0.0,
            band_has_source_photons=False,
            receiver_trace_required=False,
            coefficients=None,
            parameters=None,
        )

    indices = [
        index
        for index, wavelength in enumerate(profile.wavelength_nm)
        if band.contains_wavelength(wavelength)
        and float(distribution.photon_fraction_per_nm[index]) > 0.0
    ]
    reflectance = sum(
        float(distribution.photon_fraction_per_nm[index])
        / source_fraction
        * float(profile.reflectance[index])
        for index in indices
    )
    transmittance = sum(
        float(distribution.photon_fraction_per_nm[index])
        / source_fraction
        * float(profile.transmittance[index])
        for index in indices
    )
    absorptance = sum(
        float(distribution.photon_fraction_per_nm[index])
        / source_fraction
        * float(profile.absorptance[index])
        for index in indices
    )
    coefficients = LeafMaterialEffectiveCoefficients(
        reflectance=reflectance,
        transmittance=transmittance,
        absorptance=absorptance,
        weighting_basis=(
            f"{LEAF_MATERIAL_WEIGHTING_BASIS_BAND_SOURCE_WEIGHTED}:"
            f"{band.band_id}_{band.wavelength_min_nm}_{band.wavelength_max_nm}_nm"
        ),
    )
    parameters = fit_diffuse_trans_material(coefficients)
    return BandedTransportBandMaterial(
        band=band,
        source_photon_fraction_relative_to_par=source_fraction,
        band_has_source_photons=True,
        receiver_trace_required=True,
        coefficients=coefficients,
        parameters=parameters,
    )


def build_banded_5_transport_material_plan(
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
) -> BandedTransportMaterialPlan:
    """Build the Level 3A five-band material and metadata scaffold."""

    _validate_profile_distribution_alignment(profile, distribution)
    if distribution.scalar_flux_basis != SCALAR_FLUX_BASIS_PAR_PPFD:
        raise ValueError(
            "Level 3A band scaling requires scalar_flux_basis="
            f"{SCALAR_FLUX_BASIS_PAR_PPFD!r}, got "
            f"{distribution.scalar_flux_basis!r}."
        )
    return BandedTransportMaterialPlan(
        profile=profile,
        distribution=distribution,
        bands=tuple(
            band_source_weighted_leaf_material(profile, distribution, band)
            for band in FSPM_BANDED_5_TRANSPORT_BANDS
        ),
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
        "fspm_spectral_transport_mode": SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED,
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
        "fspm_spectral_transport_mode": SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED,
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


def scalar_source_weighted_transport_metadata() -> dict[str, Any]:
    return {
        "fspm_spectral_transport_mode": SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED,
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


def _validate_profile_distribution_alignment(
    profile: LeafOpticalProfile,
    distribution: WavelengthPhotonDistribution,
) -> None:
    if profile.wavelength_nm != distribution.wavelength_nm:
        raise ValueError("Leaf optical profile and source spectrum wavelength grids must align.")
