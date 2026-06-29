from __future__ import annotations

import math

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants.leaf_materials import (  # noqa: E402
    DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE,
    DEFAULT_FSPM_SPECTRAL_TRANSPORT_MODE,
    EPAR_BAND_IDS,
    FSPM_BANDED_5_TRANSPORT_BANDS,
    LEAF_RADIANCE_MATERIAL_MODE_OPAQUE_OCCLUDER,
    LEAF_RADIANCE_MATERIAL_MODE_REX_SOURCE_WEIGHTED_TRANS,
    PAR_BAND_IDS,
    SPECTRAL_TRANSPORT_MODE_BANDED_5,
    SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED,
    LeafMaterialEffectiveCoefficients,
    build_banded_5_transport_material_plan,
    fit_diffuse_trans_material,
    normalize_fspm_spectral_transport_mode,
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


def _fake_banded_profile() -> LeafOpticalProfile:
    treatment = LeafOpticalTreatmentProfile(
        treatment_id="fake_banded",
        treatment_label="Fake Banded",
        wavelength_nm=(400, 450, 500, 600, 625, 700),
        reflectance=(0.10, 0.20, 0.30, 0.40, 0.50, 0.60),
        transmittance=(0.20, 0.30, 0.20, 0.10, 0.20, 0.10),
        absorptance=(0.70, 0.50, 0.50, 0.50, 0.30, 0.30),
        raw_reflectance=(0.10, 0.20, 0.30, 0.40, 0.50, 0.60),
        raw_transmittance=(0.20, 0.30, 0.20, 0.10, 0.20, 0.10),
        raw_absorptance=(0.70, 0.50, 0.50, 0.50, 0.30, 0.30),
        implied_absorptance=(0.70, 0.50, 0.50, 0.50, 0.30, 0.30),
        absorptance_basis=(
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
            "raw_digitized",
        ),
    )
    return LeafOpticalProfile(
        profile_id="fake_banded_profile",
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


def _fake_banded_distribution(
    photon_fraction_per_nm: tuple[float, ...] = (0.25, 0.25, 0.20, 0.10, 0.20, 0.50),
) -> WavelengthPhotonDistribution:
    return WavelengthPhotonDistribution(
        distribution_id="fake_banded_spd",
        wavelength_nm=(400, 450, 500, 600, 625, 700),
        photon_fraction_per_nm=photon_fraction_per_nm,
        source_spectral_basis=SOURCE_SPECTRAL_BASIS_WAVELENGTH_RESOLVED_SPD,
        source="unit_test_banded_spd",
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


def test_spectral_transport_mode_default_and_overrides() -> None:
    assert DEFAULT_FSPM_SPECTRAL_TRANSPORT_MODE == (
        SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED
    )
    assert normalize_fspm_spectral_transport_mode(None) == (
        SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED
    )
    assert normalize_fspm_spectral_transport_mode("") == (
        SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED
    )
    assert normalize_fspm_spectral_transport_mode("scalar_source_weighted") == (
        SPECTRAL_TRANSPORT_MODE_SCALAR_SOURCE_WEIGHTED
    )
    assert normalize_fspm_spectral_transport_mode(" banded_5 ") == (
        SPECTRAL_TRANSPORT_MODE_BANDED_5
    )

    with pytest.raises(ValueError, match="Unknown FSPM_SPECTRAL_TRANSPORT_MODE"):
        normalize_fspm_spectral_transport_mode("banded_6")


def test_banded_5_definitions_match_level_3a_contract() -> None:
    assert [
        (band.band_id, band.wavelength_min_nm, band.wavelength_max_nm)
        for band in FSPM_BANDED_5_TRANSPORT_BANDS
    ] == [
        ("blue", 400, 499),
        ("green", 500, 599),
        ("orange", 600, 624),
        ("red", 625, 699),
        ("far_red", 700, 750),
    ]
    assert PAR_BAND_IDS == ("blue", "green", "orange", "red")
    assert EPAR_BAND_IDS == ("blue", "green", "orange", "red", "far_red")
    assert [band.band_id for band in FSPM_BANDED_5_TRANSPORT_BANDS if band.included_in_par] == list(
        PAR_BAND_IDS
    )
    assert [
        band.band_id for band in FSPM_BANDED_5_TRANSPORT_BANDS if band.included_in_epar
    ] == list(EPAR_BAND_IDS)


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


def test_banded_5_plan_computes_par_relative_fractions_and_materials() -> None:
    plan = build_banded_5_transport_material_plan(
        _fake_banded_profile(),
        _fake_banded_distribution(),
    )
    payload = plan.to_payload()
    bands = {band["band_id"]: band for band in payload["banded_transport_bands"]}

    assert payload["fspm_spectral_transport_mode"] == "banded_5"
    assert payload["band_scaling_basis"] == (
        "source_band_photon_fraction_relative_to_par"
    )
    assert payload["banded_transport_band_count"] == 5
    assert payload["scalar_flux_basis"] == "par_ppfd_umol_m2_s"
    assert payload["par_band_ids"] == ["blue", "green", "orange", "red"]
    assert payload["epar_band_ids"] == ["blue", "green", "orange", "red", "far_red"]
    assert payload["leaf_material_profile_id"] == "fake_banded_profile"
    assert payload["source_spectrum_id"] == "fake_banded_spd"

    assert bands["blue"]["source_photon_fraction_relative_to_par"] == pytest.approx(0.50)
    assert bands["green"]["source_photon_fraction_relative_to_par"] == pytest.approx(0.20)
    assert bands["orange"]["source_photon_fraction_relative_to_par"] == pytest.approx(0.10)
    assert bands["red"]["source_photon_fraction_relative_to_par"] == pytest.approx(0.20)
    assert bands["far_red"]["source_photon_fraction_relative_to_par"] == pytest.approx(0.50)

    assert bands["blue"]["effective_reflectance"] == pytest.approx(0.15)
    assert bands["blue"]["effective_transmittance"] == pytest.approx(0.25)
    assert bands["blue"]["effective_absorptance"] == pytest.approx(0.60)
    assert bands["blue"]["radiance_red"] == pytest.approx(0.40)
    assert bands["blue"]["radiance_green"] == pytest.approx(0.40)
    assert bands["blue"]["radiance_blue"] == pytest.approx(0.40)
    assert bands["blue"]["radiance_trans"] == pytest.approx(0.25 / 0.40)
    assert bands["blue"]["radiance_tspec"] == 0.0
    assert bands["blue"]["receiver_trace_required"] is True


def test_banded_5_zero_source_band_emits_zero_metadata_without_trace() -> None:
    plan = build_banded_5_transport_material_plan(
        _fake_banded_profile(),
        _fake_banded_distribution(
            photon_fraction_per_nm=(0.25, 0.25, 0.25, 0.0, 0.25, 0.0)
        ),
    )
    bands = {
        band["band_id"]: band
        for band in plan.to_payload()["banded_transport_bands"]
    }
    orange = bands["orange"]
    far_red = bands["far_red"]

    for band in (orange, far_red):
        assert band["source_photon_fraction_relative_to_par"] == 0.0
        assert band["band_has_source_photons"] is False
        assert band["receiver_trace_required"] is False
        assert band["effective_reflectance"] == 0.0
        assert band["effective_transmittance"] == 0.0
        assert band["effective_absorptance"] == 0.0
        assert band["radiance_primitive"] == "none"
        assert band["radiance_red"] == 0.0
        assert band["radiance_trans"] == 0.0


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
