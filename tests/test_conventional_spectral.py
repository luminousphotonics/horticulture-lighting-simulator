from __future__ import annotations

from dataclasses import replace

import pytest

from fspm_optics.fixtures.conventional_led.profile import (
    build_conventional_comparison_profile,
)
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_SPD_SHA256,
    load_conventional_spd,
)
from fspm_optics.fixtures.conventional_led.spectral import (
    CONVENTIONAL_SPECTRAL_SOURCE_ID,
    build_conventional_spectral_distribution,
    format_conventional_spectral_distribution_json,
)
from fspm_optics.sources.smd.profile import SMD_SOURCE_MODEL_ID
from fspm_optics.spectral.spd import RelativeRadiantSpd


EXPECTED_PAR_FRACTIONS = {
    "blue": 0.17540617270400982,
    "green": 0.4358935618794981,
    "orange": 0.13668100935568855,
    "red": 0.25201925606080355,
}
EXPECTED_FAR_RED_RELATIVE_TO_PAR = 0.009237180707785467


def test_approved_spd_recomputes_fixed_photon_bands_with_orange_separate() -> None:
    distribution = build_conventional_spectral_distribution()

    assert distribution.source_id == CONVENTIONAL_SPECTRAL_SOURCE_ID
    assert distribution.source_id != SMD_SOURCE_MODEL_ID
    assert distribution.resource_sha256 == CONVENTIONAL_SPD_SHA256
    assert dict(distribution.par_band_photon_fractions) == pytest.approx(
        EXPECTED_PAR_FRACTIONS,
        abs=1e-14,
    )
    assert sum(dict(distribution.par_band_photon_fractions).values()) == pytest.approx(
        1.0,
        abs=1e-12,
    )
    assert distribution.far_red_relative_to_par == pytest.approx(
        EXPECTED_FAR_RED_RELATIVE_TO_PAR,
        abs=1e-14,
    )
    assert distribution.fraction("orange") != distribution.fraction("red")


def test_uniform_spd_amplitude_scaling_cannot_change_photon_fractions() -> None:
    source = load_conventional_spd()
    scaled_source = replace(
        source,
        relative_spd=tuple(value * 37.0 for value in source.relative_spd),
        sha256="a" * 64,
    )
    base = build_conventional_spectral_distribution(source)
    scaled = build_conventional_spectral_distribution(scaled_source)

    assert dict(scaled.par_band_photon_fractions) == pytest.approx(
        dict(base.par_band_photon_fractions),
        abs=1e-15,
    )
    assert scaled.far_red_relative_to_par == pytest.approx(
        base.far_red_relative_to_par,
        abs=1e-15,
    )
    assert scaled.photon_distribution.photon_amount == pytest.approx(
        base.photon_distribution.photon_amount,
        abs=1e-15,
    )


def test_spd_amplitude_has_no_path_to_declared_absolute_ppf() -> None:
    profile = build_conventional_comparison_profile()
    source = load_conventional_spd()
    scaled = replace(
        source,
        relative_spd=tuple(value * 10.0 for value in source.relative_spd),
        sha256="b" * 64,
    )

    build_conventional_spectral_distribution(scaled)
    assert profile.modeled_operating_point.electrical_power_w_per_fixture == 660.0
    assert profile.modeled_operating_point.par_ppe_umol_per_j == 2.6
    assert profile.modeled_operating_point.par_ppf_umol_s_per_fixture == 1716.0


def test_spectral_serialization_and_distribution_identity_are_deterministic() -> None:
    first = build_conventional_spectral_distribution()
    second = build_conventional_spectral_distribution()

    assert first == second
    assert first.distribution_id == second.distribution_id
    assert format_conventional_spectral_distribution_json(first) == (
        format_conventional_spectral_distribution_json(second)
    )
    payload = first.to_payload()
    assert payload["resource"]["defines_absolute_PAR_PPF"] is False
    assert payload["absolute_PAR_PPF_umol_s"] is None


@pytest.mark.parametrize(
    ("wavelengths", "values", "message"),
    [
        ((400.0, 399.0), (1.0, 1.0), "strictly increasing"),
        ((400.0, 401.0), (1.0, -1.0), "non-negative"),
        ((400.0, 401.0), (1.0, float("nan")), "non-negative"),
    ],
)
def test_relative_spd_domain_rejects_invalid_samples(
    wavelengths: tuple[float, ...],
    values: tuple[float, ...],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RelativeRadiantSpd(
            resource_name="synthetic.csv",
            wavelength_nm=wavelengths,
            relative_spd=values,
            sha256="c" * 64,
        )


def test_relative_spd_requires_positive_par_photon_coverage() -> None:
    zero_par = RelativeRadiantSpd(
        resource_name="synthetic.csv",
        wavelength_nm=(400.0, 500.0, 699.0, 700.0),
        relative_spd=(0.0, 0.0, 0.0, 1.0),
        sha256="d" * 64,
    )
    with pytest.raises(ValueError, match="positive photon weight"):
        build_conventional_spectral_distribution(zero_par)
