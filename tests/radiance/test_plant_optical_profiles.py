from __future__ import annotations

import pytest

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.engine.plants.optical_profiles import (  # noqa: E402
    REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
    REX_LEAF_OPTICS_TREATMENT_ID,
    list_leaf_optical_profiles,
    load_leaf_optical_profile,
    load_rex_green_butterhead_mature_leaf_optics_v1,
)


def test_rex_leaf_optical_profile_loads_v0_2_source_data() -> None:
    profile = load_leaf_optical_profile(REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1)

    assert profile.profile_id == REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1
    assert profile.species == "Lactuca sativa"
    assert profile.cultivar == "Rex"
    assert profile.growth_stage == "mature_leaf_uppermost_fully_expanded"
    assert profile.leaf_side_basis == "mean_of_digitized_treatment_models"
    assert profile.treatment_id == REX_LEAF_OPTICS_TREATMENT_ID
    assert profile.profile_version == "0.2"
    assert profile.data_provenance == "digitized_from_published_figure"
    assert (
        profile.validation_status
        == "research_derived_digitized_unvalidated_in_simulator"
    )
    assert "Figure 7" in profile.source


def test_rex_leaf_optical_profile_grid_is_sorted_and_preserves_rt_tail() -> None:
    profile = load_rex_green_butterhead_mature_leaf_optics_v1()

    assert profile.wavelength_nm == tuple(sorted(profile.wavelength_nm))
    assert profile.wavelength_nm[0] == 404
    assert profile.wavelength_nm[-1] == 797
    assert len(profile.wavelength_nm) == 394
    assert max(
        wavelength
        for wavelength, raw_absorptance in zip(
            profile.wavelength_nm,
            profile.raw_absorptance,
            strict=True,
        )
        if raw_absorptance is not None
    ) < profile.wavelength_nm[-1]
    assert profile.raw_reflectance[-1] is not None
    assert profile.raw_transmittance[-1] is not None
    assert profile.reflectance[-1] > 0.0
    assert profile.transmittance[-1] > 0.0
    assert profile.reflectance[-1] + profile.transmittance[-1] == pytest.approx(1.0)


def test_rex_leaf_optical_profile_arrays_are_fractional_and_aligned() -> None:
    profile = load_leaf_optical_profile(REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1)
    arrays = (
        profile.wavelength_nm,
        profile.absorptance,
        profile.transmittance,
        profile.reflectance,
        profile.absorptance_basis,
        profile.raw_absorptance,
        profile.raw_transmittance,
        profile.raw_reflectance,
        profile.implied_absorptance,
    )

    assert len({len(array) for array in arrays}) == 1
    for values in (profile.absorptance, profile.transmittance, profile.reflectance):
        assert all(isinstance(value, float) for value in values)
        assert all(0.0 <= value <= 1.0 for value in values)


def test_rex_treatment_curves_preserve_raw_and_implied_absorptance_basis() -> None:
    profile = load_leaf_optical_profile(REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1)
    orange = profile.treatment("orange")

    assert "raw_digitized" in orange.absorptance_basis
    assert "implied_from_reflectance_transmittance" in orange.absorptance_basis

    raw_index = orange.index_for_wavelength(737)
    assert orange.absorptance_basis[raw_index] == "raw_digitized"
    assert orange.raw_absorptance[raw_index] is not None
    assert orange.absorptance[raw_index] == pytest.approx(
        orange.raw_absorptance[raw_index]
    )

    implied_index = orange.index_for_wavelength(738)
    assert (
        orange.absorptance_basis[implied_index]
        == "implied_from_reflectance_transmittance"
    )
    assert orange.raw_absorptance[implied_index] is None
    assert orange.implied_absorptance[implied_index] is not None
    assert orange.absorptance[implied_index] == pytest.approx(
        orange.implied_absorptance[implied_index]
    )


def test_rex_is_listed_but_not_loaded_as_an_implicit_default() -> None:
    assert list_leaf_optical_profiles() == (REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,)

    with pytest.raises(KeyError, match="Unknown leaf optical profile"):
        load_leaf_optical_profile("default")
