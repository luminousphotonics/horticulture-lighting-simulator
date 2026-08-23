from __future__ import annotations

from dataclasses import replace
import math

import pytest

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_SOURCE_ID,
    build_conventional_spectral_source_payload,
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.hps import (
    HPS_BAND_ORDER,
    HPS_BAND_SOURCE_MODEL_ID,
    HPS_SOURCE_ID,
    HPS_SPECTRAL_SOURCE_ID,
    build_hps_comparison_profile,
    build_hps_spectral_distribution,
    build_hps_spectral_source_payload,
    load_hps_spd,
    plan_hps_layout_from_feet,
)
from fspm_optics.optics.conventional_rex import (
    build_conventional_rex_weighted_atr_payload,
)
from fspm_optics.optics.hps import (
    HPS_REX_MATERIAL_PREFIX,
    build_hps_rex_radiance_material_plan,
    build_hps_rex_weighted_atr_payload,
)
from fspm_optics.sources.smd.profile import SMD_SOURCE_MODEL_ID, build_nominal_smd_source_model


EXPECTED_FRACTIONS = {
    "blue": 0.05097805852605509,
    "green": 0.5045791405070055,
    "orange": 0.2432155983108364,
    "red": 0.20122720265610305,
    "far_red": 0.049571170506866734,
}
EXPECTED_ATR = {
    "scalar_par": (0.7603935760629122, 0.13168425379822862, 0.1079221701388591),
    "blue": (0.9003252748468743, 0.031458306469612785, 0.06821641868351289),
    "green": (0.6990161454002677, 0.1675186375290056, 0.13346521707072678),
    "orange": (0.7819421533796015, 0.12193655407870699, 0.09612129254169144),
    "red": (0.8533258025534639, 0.07862753143304617, 0.06804666601348992),
    "far_red": (0.21051647046569288, 0.44786971954979926, 0.3416138099845078),
}


def _source():
    return build_hps_spectral_source_payload(
        plan_hps_layout_from_feet(10.0, 10.0)
    )


def test_hps_spd_fractions_yields_and_absolute_budgets_close() -> None:
    payload = _source()

    assert HPS_BAND_ORDER == ("blue", "green", "orange", "red", "far_red")
    assert payload.fixture_count == 4
    assert (
        payload.scientific_payload()["model_id"]
        == HPS_BAND_SOURCE_MODEL_ID
        == "hps_fixed_output_five_band_source_v2"
    )
    assert (
        payload.spectral_source_id
        == HPS_SPECTRAL_SOURCE_ID
        == "hps_se_relative_photon_shape_v2"
    )
    for budget in payload.bands:
        fraction = EXPECTED_FRACTIONS[budget.band_id]
        assert budget.photon_fraction_relative_to_par == pytest.approx(fraction, abs=5e-13)
        assert budget.effective_yield_umol_per_j == pytest.approx(
            (1750.0 / 1045.0) * fraction, rel=1e-10
        )
        assert budget.per_fixture_ppf_umol_s == pytest.approx(1750.0 * fraction, rel=1e-10)
        assert budget.whole_layout_ppf_umol_s == pytest.approx(
            4.0 * 1750.0 * fraction,
            rel=1e-10,
        )
    par = payload.bands[:4]
    assert math.fsum(item.photon_fraction_relative_to_par for item in par) == 1.0
    assert math.fsum(item.effective_yield_umol_per_j for item in par) == 1750.0 / 1045.0
    assert math.fsum(item.per_fixture_ppf_umol_s for item in par) == 1750.0
    assert math.fsum(item.whole_layout_ppf_umol_s for item in par) == 7000.0
    assert all(item.is_par for item in par)
    assert payload.band("far_red").is_par is False
    assert {item.band_id: item.per_fixture_ppf_umol_s for item in payload.bands} == {
        "blue": 89.2116024205964,
        "green": 883.0134958872596,
        "orange": 425.62729704396367,
        "red": 352.1476046481803,
        "far_red": 86.74954838701679,
    }
    assert payload.band("orange").start_nm == 600
    assert payload.band("red").start_nm == 625


def test_spd_amplitude_cannot_change_hps_fractions_or_operating_point() -> None:
    spd = load_hps_spd()
    scaled = replace(spd, relative_spd=tuple(value * 31.0 for value in spd.relative_spd))
    original = build_hps_spectral_distribution(spd)
    changed = build_hps_spectral_distribution(scaled)

    assert tuple(name for name, _ in changed.par_band_photon_fractions) == tuple(
        name for name, _ in original.par_band_photon_fractions
    )
    assert tuple(value for _, value in changed.par_band_photon_fractions) == pytest.approx(
        tuple(value for _, value in original.par_band_photon_fractions)
    )
    assert changed.far_red_relative_to_par == pytest.approx(original.far_red_relative_to_par)
    assert changed.distribution_id == original.distribution_id
    profile = build_hps_comparison_profile()
    assert profile.modeled_operating_point.par_ppf_umol_s_per_fixture == 1750.0
    assert profile.modeled_operating_point.par_ppe_umol_per_j == 1750.0 / 1045.0
    assert profile.angular_distribution_id == (
        build_hps_comparison_profile().angular_distribution_id
    )


def test_hps_rex_atr_closes_and_retains_actual_source_coverage_and_unsupported_mass() -> None:
    optics = build_hps_rex_weighted_atr_payload(_source())

    assert optics.intervals.positive_source_start_nm == 381
    assert optics.intervals.positive_source_end_nm == 779
    assert optics.unsupported_scalar_par_umol_s == pytest.approx(1.3146065778355478, rel=1e-9)
    assert optics.unsupported_scalar_par_fraction == pytest.approx(
        0.0007512037587631701, rel=1e-9
    )
    unsupported = dict(optics.intervals.unsupported_source_mass_by_interval)
    assert unsupported["scalar_par"] == pytest.approx(
        unsupported["blue"],
        rel=0.0,
        abs=1e-12,
    )
    assert all(unsupported[name] == 0.0 for name in ("green", "orange", "red", "far_red"))
    for interval in (optics.scalar_par, *optics.bands):
        coefficients = interval.coefficients
        actual = (
            coefficients.absorptance,
            coefficients.transmittance,
            coefficients.reflectance,
        )
        assert actual == pytest.approx(EXPECTED_ATR[interval.interval_id], abs=5e-9)
        assert math.fsum(actual) == pytest.approx(1.0, abs=1e-12)
    coverage = optics.scientific_payload()["source_coverage"]
    assert coverage["unsupported_mass_redistributed"] is False


def test_hps_materials_reconstruct_every_weighted_atr_interval() -> None:
    optics = build_hps_rex_weighted_atr_payload(_source())
    materials = build_hps_rex_radiance_material_plan(optics)

    assert tuple(item.interval_id for item in materials.materials) == (
        "scalar_par", *HPS_BAND_ORDER
    )
    assert all(
        item.material_identifier.startswith(HPS_REX_MATERIAL_PREFIX)
        for item in materials.materials
    )
    for material in materials.materials:
        source = material.source_interval.coefficients
        reconstructed = material.reconstruction
        assert reconstructed.absorptance == pytest.approx(source.absorptance, abs=1e-12)
        assert reconstructed.total_transmittance == pytest.approx(
            source.transmittance, abs=1e-12
        )
        assert reconstructed.total_reflectance == pytest.approx(source.reflectance, abs=1e-12)


def test_hps_rex_boundaries_reject_smd_and_conventional_sources() -> None:
    conventional_source = build_conventional_spectral_source_payload(
        plan_conventional_layout_from_feet(10.0, 10.0)
    )
    conventional_optics = build_conventional_rex_weighted_atr_payload(conventional_source)

    with pytest.raises(TypeError, match="HpsSpectralSourcePayload"):
        build_hps_rex_weighted_atr_payload(conventional_source)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="HpsSpectralSourcePayload"):
        build_hps_rex_weighted_atr_payload(build_nominal_smd_source_model())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="HPS Rex optical payload"):
        build_hps_rex_radiance_material_plan(conventional_optics)  # type: ignore[arg-type]


def test_phase25b_does_not_change_source_family_identities() -> None:
    assert build_hps_comparison_profile().source_id == HPS_SOURCE_ID
    assert HPS_SOURCE_ID != CONVENTIONAL_SOURCE_ID
    assert HPS_SOURCE_ID != SMD_SOURCE_MODEL_ID
