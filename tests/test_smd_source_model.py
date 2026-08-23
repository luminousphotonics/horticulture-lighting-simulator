from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

import pytest

from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_optimized_module_schedule
from fspm_optics.sources.smd import profile as smd_profile
from fspm_optics.sources.smd.profile import (
    DEFAULT_SMD_COMPONENTS,
    SMD_NORMALIZATION_POLICY,
    build_nominal_smd_source_model,
    read_smd_source_model_json,
    write_smd_source_model_json,
)
from fspm_optics.sources.smd.spd import load_smd_spd
from fspm_optics.spectral.distribution import (
    combine_photon_distributions,
    normalize_relative_radiant_spd_to_par_photons,
)
from fspm_optics.transport.five_band import build_five_band_source_plans


def test_explicit_component_manifest_and_nominal_par_ppf_match_policy() -> None:
    assert [
        (
            component.component_id,
            component.count,
            component.nominal_package_watts,
            component.nominal_ppe_umol_per_j,
            component.spd_resource,
        )
        for component in DEFAULT_SMD_COMPONENTS
    ] == [
        ("warm_white_3000k", 52, 0.68, 2.73, "smd_3000k_spd.csv"),
        ("cool_white_5000k", 52, 0.68, 2.81, "smd_5000k_spd.csv"),
        ("deep_red_660nm", 41, 0.44, 4.13, "smd_660nm_spd.csv"),
    ]

    model = build_nominal_smd_source_model()
    nominal = {
        item.component.component_id: item.nominal_par_ppf_umol_s
        for item in model.components
    }
    assert nominal == pytest.approx(
        {
            "warm_white_3000k": 96.5328,
            "cool_white_5000k": 99.3616,
            "deep_red_660nm": 74.5052,
        }
    )
    assert model.total_nominal_par_ppf_umol_s == pytest.approx(270.3996)


def test_component_par_fractions_match_policy() -> None:
    model = build_nominal_smd_source_model()
    fractions = {
        item.component.component_id: item.fraction_of_nominal_par_ppf
        for item in model.components
    }

    assert fractions == pytest.approx(
        {
            "warm_white_3000k": 0.3570005281,
            "cool_white_5000k": 0.3674620820,
            "deep_red_660nm": 0.2755373898,
        },
        abs=1e-10,
    )
    assert sum(fractions.values()) == pytest.approx(1.0)


def test_source_band_fractions_are_derived_from_packaged_spds() -> None:
    model = build_nominal_smd_source_model()

    assert model.band_photon_fractions_relative_to_par == pytest.approx(
        {
            "blue": 0.125693,
            "green": 0.336604,
            "orange": 0.113724,
            "red": 0.423979,
            "far_red": 0.014267,
        },
        abs=5e-5,
    )
    assert sum(
        model.band_photon_fractions_relative_to_par[band_id]
        for band_id in ("blue", "green", "orange", "red")
    ) == pytest.approx(1.0)
    assert model.far_red_relative_to_par == pytest.approx(
        model.band_photon_fractions_relative_to_par["far_red"]
    )


def test_spd_amplitude_cannot_change_proposed_absolute_source_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    base = build_nominal_smd_source_model()
    original_loader = smd_profile.load_smd_spd
    amplitude_by_resource = {
        "smd_3000k_spd.csv": 7.0,
        "smd_5000k_spd.csv": 11.0,
        "smd_660nm_spd.csv": 13.0,
    }

    def scaled_loader(resource_name: str, *, data_root: str | Path | None = None):
        source = original_loader(resource_name, data_root=data_root)
        factor = amplitude_by_resource[resource_name]
        return replace(
            source,
            relative_spd=tuple(value * factor for value in source.relative_spd),
        )

    monkeypatch.setattr(smd_profile, "load_smd_spd", scaled_loader)
    scaled = smd_profile.build_nominal_smd_source_model()

    assert scaled.band_photon_fractions_relative_to_par == pytest.approx(
        base.band_photon_fractions_relative_to_par,
        abs=1e-15,
    )
    assert scaled.total_nominal_par_ppf_umol_s == base.total_nominal_par_ppf_umol_s

    layout = generate_proposed_led_layout(10.0, 10.0)
    schedule = build_optimized_module_schedule(layout, (20, 25, 30, 35, 40))
    base_plans = build_five_band_source_plans(base, layout, schedule)
    scaled_plans = build_five_band_source_plans(scaled, layout, schedule)

    assert tuple(
        item.internal_band_photon_yield_umol_per_j for item in scaled_plans
    ) == pytest.approx(
        tuple(item.internal_band_photon_yield_umol_per_j for item in base_plans),
        abs=1e-15,
    )
    scaled_par_yield = math.fsum(
        item.internal_band_photon_yield_umol_per_j
        for item in scaled_plans
        if item.band_id != "far_red"
    )
    assert scaled_par_yield == pytest.approx(
        INTERNAL_SOURCE_PPE_UMOL_PER_J,
        abs=1e-12,
    )
    assert scaled_par_yield * ACCEPTED_FIXTURE_TRANSMISSION == pytest.approx(
        COMPLETED_APERTURE_PPE_UMOL_PER_J,
        abs=1e-12,
    )
    assert scaled_plans[-1].band_id == "far_red"


def test_default_model_does_not_equal_weight_the_three_spd_curves() -> None:
    model = build_nominal_smd_source_model()
    component_par_amounts = [
        item.photon_distribution.par_amount for item in model.components
    ]
    assert component_par_amounts == pytest.approx([96.5328, 99.3616, 74.5052])
    assert len({round(value, 6) for value in component_par_amounts}) == 3

    equal_weighted = combine_photon_distributions(
        normalize_relative_radiant_spd_to_par_photons(
            spd.wavelength_nm,
            spd.relative_spd,
        )
        for spd in (
            load_smd_spd("smd_3000k_spd.csv"),
            load_smd_spd("smd_5000k_spd.csv"),
            load_smd_spd("smd_660nm_spd.csv"),
        )
    )
    equal_red_fraction = equal_weighted.amount_between(625, 700) / (
        equal_weighted.par_amount
    )
    assert model.band_photon_fractions_relative_to_par["red"] != pytest.approx(
        equal_red_fraction,
        abs=1e-4,
    )


def test_source_model_payload_contains_computed_hashes_policy_and_limitations() -> None:
    model = build_nominal_smd_source_model()
    payload = model.to_payload()

    assert payload["normalization_policy"] == SMD_NORMALIZATION_POLICY
    assert payload["component_nominal_PAR_PPF"]["deep_red_660nm"] == pytest.approx(
        74.5052
    )
    assert payload["band_photon_fractions_relative_to_PAR"]["orange"] == pytest.approx(
        model.band_photon_fractions_relative_to_par["orange"]
    )
    assert len(payload["resource_hashes"]) == 3
    assert len(payload["limitations"]) == 9
    assert "relative_spd * wavelength_nm" in payload["normalization_details"]
    assert payload["raw_component_values_are_absolute_ppe_authority"] is False
    assert "solely to preserve" in payload["normalization_details"]


def test_source_model_json_payload_round_trips(tmp_path: Path) -> None:
    model = build_nominal_smd_source_model()
    output = write_smd_source_model_json(tmp_path / "smd_source_model.json", model)

    assert read_smd_source_model_json(output) == model.to_payload()
