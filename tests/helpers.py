from __future__ import annotations

from fspm_optics.optics.profiles import LeafOpticalProfile, LeafOpticalTreatmentProfile
from fspm_optics.optics.spectral_absorption import WavelengthPhotonDistribution


def optical_profile() -> LeafOpticalProfile:
    wavelengths = (450, 550, 650, 725)
    reflectance = (0.10, 0.20, 0.15, 0.30)
    transmittance = (0.05, 0.10, 0.05, 0.20)
    absorptance = tuple(1.0 - r - t for r, t in zip(reflectance, transmittance, strict=True))
    basis = ("fixture",) * len(wavelengths)
    treatment = LeafOpticalTreatmentProfile(
        treatment_id="mean",
        treatment_label="Mean",
        wavelength_nm=wavelengths,
        reflectance=reflectance,
        transmittance=transmittance,
        absorptance=absorptance,
        raw_reflectance=reflectance,
        raw_transmittance=transmittance,
        raw_absorptance=absorptance,
        implied_absorptance=absorptance,
        absorptance_basis=basis,
    )
    return LeafOpticalProfile(
        profile_id="test_leaf_v1",
        species="Lactuca sativa",
        cultivar="Test",
        growth_stage="mature",
        leaf_side_basis="two-sided mean",
        wavelength_nm=wavelengths,
        reflectance=reflectance,
        transmittance=transmittance,
        absorptance=absorptance,
        source="unit fixture",
        data_provenance="synthetic",
        validation_status="unit-test-only",
        raw_reflectance=reflectance,
        raw_transmittance=transmittance,
        raw_absorptance=absorptance,
        implied_absorptance=absorptance,
        absorptance_basis=basis,
        treatment_id="mean",
        treatment_label="Mean",
        profile_version="1",
        treatments=(treatment,),
    )


def photon_distribution() -> WavelengthPhotonDistribution:
    return WavelengthPhotonDistribution(
        distribution_id="test_spd",
        wavelength_nm=(450, 550, 650, 725),
        photon_fraction_per_nm=(0.2, 0.3, 0.5, 0.1),
        source_spectral_basis="unit_fixture",
        source="unit fixture",
    )
