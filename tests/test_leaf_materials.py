from __future__ import annotations

import pytest

from fspm_optics.optics.leaf_materials import (
    DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE,
    FSPM_BANDED_5_TRANSPORT_BANDS,
    LeafMaterialEffectiveCoefficients,
    build_banded_5_transport_material_plan,
    fit_diffuse_trans_material,
    normalize_leaf_radiance_material_mode,
    par_source_weighted_leaf_coefficients,
    radiance_trans_material_definition,
)
from tests.helpers import optical_profile, photon_distribution


def test_leaf_material_mode_default_and_validation() -> None:
    assert normalize_leaf_radiance_material_mode(None) == DEFAULT_FSPM_LEAF_RADIANCE_MATERIAL_MODE
    with pytest.raises(ValueError, match="Unknown"):
        normalize_leaf_radiance_material_mode("invalid")


def test_source_weighted_coefficients_are_par_normalized() -> None:
    coefficients = par_source_weighted_leaf_coefficients(optical_profile(), photon_distribution())
    assert coefficients.reflectance == pytest.approx(0.155)
    assert coefficients.transmittance == pytest.approx(0.065)
    assert coefficients.absorptance == pytest.approx(0.78)
    assert coefficients.reflectance + coefficients.transmittance + coefficients.absorptance == pytest.approx(1.0)


def test_diffuse_trans_fit_preserves_requested_partition() -> None:
    parameters = fit_diffuse_trans_material(
        LeafMaterialEffectiveCoefficients(reflectance=0.2, transmittance=0.1, absorptance=0.7)
    )
    assert parameters.red == pytest.approx(0.3)
    assert parameters.green == pytest.approx(0.3)
    assert parameters.blue == pytest.approx(0.3)
    assert parameters.trans == pytest.approx(1.0 / 3.0)
    definition = radiance_trans_material_definition("leaf", parameters)
    assert "void trans leaf" in definition


def test_banded_plan_covers_five_transport_bands() -> None:
    plan = build_banded_5_transport_material_plan(optical_profile(), photon_distribution())
    assert [item.band.band_id for item in plan.bands] == [
        item.band_id for item in FSPM_BANDED_5_TRANSPORT_BANDS
    ]
    assert sum(item.source_photon_fraction_relative_to_par for item in plan.bands) == pytest.approx(1.1)


@pytest.mark.parametrize(
    "coefficients",
    [
        LeafMaterialEffectiveCoefficients(0.4, 0.4, 0.4),
        LeafMaterialEffectiveCoefficients(0.0, 0.0, 1.0),
    ],
)
def test_diffuse_trans_fit_rejects_unrepresentable_coefficients(
    coefficients: LeafMaterialEffectiveCoefficients,
) -> None:
    with pytest.raises(ValueError):
        fit_diffuse_trans_material(coefficients)
