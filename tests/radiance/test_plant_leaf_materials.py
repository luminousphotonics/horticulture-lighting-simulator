from __future__ import annotations

import math

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants.leaf_materials import (  # noqa: E402
    DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE,
    LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER,
    LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS,
    LeafMaterialEffectiveCoefficients,
    fit_diffuse_trans_material,
    normalize_leaf_radiance_material_mode,
    par_source_weighted_leaf_coefficients,
    radiance_trans_material_definition,
)
from rad_rebuild.radiance.engine.plants.optical_profiles import (  # noqa: E402
    LeafOpticalProfile,
    LeafOpticalTreatmentProfile,
)
from rad_rebuild.radiance.engine.plants.spectral_absorption import (  # noqa: E402
    SOURCE_SPECTRAL_BASIS_WAVELENGTH_RESOLVED_SPD,
    WavelengthPhotonDistribution,
)


def _fake_profile() -> LeafOpticalProfile:
    treatment = LeafOpticalTreatmentProfile(
        treatment_id="fake",
        treatment_label="Fake",
        wavelength_nm=(400, 500, 600, 700),
        reflectance=(0.10, 0.20, 0.30, 0.90),
        transmittance=(0.50, 0.30, 0.10, 0.05),
        absorptance=(0.40, 0.50, 0.60, 0.05),
        raw_reflectance=(0.10, 0.20, 0.30, 0.90),
        raw_transmittance=(0.50, 0.30, 0.10, 0.05),
        raw_absorptance=(0.40, 0.50, 0.60, 0.05),
        implied_absorptance=(0.40, 0.50, 0.60, 0.05),
        absorptance_basis=(
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
        ),
    )
    return LeafOpticalProfile(
        profile_id="fake_profile",
        species="Lactuca sativa",
        cultivar="test",
        growth_stage="test",
        leaf_side_basis="test",
        wavelength_nm=treatment.wavelength_nm,
        reflectance=treatment.reflectance,
        transmittance=treatment.transmittance,
        absorptance=treatment.absorptance,
        source="unit_test",
        data_provenance="unit_test",
        validation_status="unit_test",
        raw_reflectance=treatment.raw_reflectance,
        raw_transmittance=treatment.raw_transmittance,
        raw_absorptance=treatment.raw_absorptance,
        implied_absorptance=treatment.implied_absorptance,
        absorptance_basis=treatment.absorptance_basis,
        treatment_id=treatment.treatment_id,
        treatment_label=treatment.treatment_label,
        profile_version="test",
        treatments=(treatment,),
    )


def _fake_distribution() -> WavelengthPhotonDistribution:
    return WavelengthPhotonDistribution(
        distribution_id="fake_spd",
        wavelength_nm=(400, 500, 600, 700),
        photon_fraction_per_nm=(0.2, 0.3, 0.5, 100.0),
        source_spectral_basis=SOURCE_SPECTRAL_BASIS_WAVELENGTH_RESOLVED_SPD,
        source="unit_test_spd",
    )


def test_leaf_radiance_material_mode_default_and_overrides() -> None:
    assert DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE == "opaque_occluder"
    assert normalize_leaf_radiance_material_mode(None) == (
        LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER
    )
    assert normalize_leaf_radiance_material_mode("") == (
        LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER
    )
    assert normalize_leaf_radiance_material_mode(" rex_source_weighted_trans ") == (
        LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS
    )

    with pytest.raises(ValueError, match="Unknown FSPM_LEAF_RADIANCE_MATERIAL_MODE"):
        normalize_leaf_radiance_material_mode("banded_5")


def test_rex_source_weighted_leaf_coefficients_are_par_normalized() -> None:
    coefficients = par_source_weighted_leaf_coefficients(
        _fake_profile(),
        _fake_distribution(),
    )

    assert coefficients.reflectance == pytest.approx(0.23)
    assert coefficients.transmittance == pytest.approx(0.24)
    assert coefficients.absorptance == pytest.approx(0.53)
    assert (
        coefficients.reflectance
        + coefficients.transmittance
        + coefficients.absorptance
    ) == pytest.approx(1.0)


def test_rex_source_weighted_leaf_coefficients_require_aligned_grids() -> None:
    with pytest.raises(ValueError, match="wavelength grids must align"):
        par_source_weighted_leaf_coefficients(
            _fake_profile(),
            WavelengthPhotonDistribution(
                distribution_id="misaligned",
                wavelength_nm=(400, 500, 601, 700),
                photon_fraction_per_nm=(0.25, 0.25, 0.25, 0.25),
                source_spectral_basis=SOURCE_SPECTRAL_BASIS_WAVELENGTH_RESOLVED_SPD,
                source="unit_test_spd",
            ),
        )


def test_diffuse_trans_material_uses_corrected_radiance_formula() -> None:
    parameters = fit_diffuse_trans_material(
        LeafMaterialEffectiveCoefficients(
            reflectance=0.23,
            transmittance=0.24,
            absorptance=0.53,
        )
    )

    assert parameters.red == pytest.approx(0.47)
    assert parameters.green == pytest.approx(0.47)
    assert parameters.blue == pytest.approx(0.47)
    assert parameters.spec == 0.0
    assert parameters.rough == 0.0
    assert parameters.trans == pytest.approx(0.24 / 0.47)
    assert parameters.tspec == 0.0

    definition = radiance_trans_material_definition("plant_leaf_material", parameters)
    assert "void trans plant_leaf_material" in definition
    assert "7 0.470000 0.470000 0.470000 0.000000 0.000000" in definition
    assert "0.510638 0.000000" in definition


@pytest.mark.parametrize(
    "coefficients, error",
    (
        (
            LeafMaterialEffectiveCoefficients(0.2, 0.3, 0.4),
            "must sum to 1",
        ),
        (
            LeafMaterialEffectiveCoefficients(0.6, 0.6, -0.2),
            r"A_eff must be in \[0, 1\]",
        ),
        (
            LeafMaterialEffectiveCoefficients(0.0, 0.0, 1.0),
            "must be greater than 0",
        ),
        (
            LeafMaterialEffectiveCoefficients(math.nan, 0.2, 0.8),
            "R_eff must be finite",
        ),
    ),
)
def test_diffuse_trans_material_rejects_invalid_coefficients(
    coefficients: LeafMaterialEffectiveCoefficients,
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        fit_diffuse_trans_material(coefficients)
