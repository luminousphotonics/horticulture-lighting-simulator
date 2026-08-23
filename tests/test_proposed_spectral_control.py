from __future__ import annotations

from dataclasses import replace
import math

import pytest

from fspm_optics.application.domain import ProposedSpectralBasis
from fspm_optics.application.multispectral import build_proposed_juvenile_source_adapter
from fspm_optics.application.source_state import PhysicalSourceState
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_SPD_RESOURCE_NAME,
    CONVENTIONAL_SPD_SHA256,
)
from fspm_optics.fixtures.conventional_led.spectral import (
    build_conventional_spectral_distribution,
)
from fspm_optics.fixtures.occlusion import (
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PROPOSED_MODELED_BAND_TRANSMISSION,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_optimized_module_schedule
from fspm_optics.geometry.room import RoomDimensions, room_radiance_text
from fspm_optics.sources.smd.profile import build_nominal_smd_source_model
from fspm_optics.sources.smd.spectral_control import (
    PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID,
    build_controlled_conventional_led_proposed_source_model,
    proposed_spectral_basis_payload,
)
from fspm_optics.transport.five_band import (
    SmdBandSourcePlan,
    build_five_band_source_plans,
)


def _layout_schedule_state():
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(layout, (20, 25, 30, 35, 40))
    state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity={"layout_id": "same-proposed-layout"},
        full_output_schedule={"watts_by_control_zone": list(schedule.watts_by_control_zone)},
        operating_point={"effective_w": schedule.total_watts * 0.5},
        source_operation={
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope": {
                "id": layout.mechanical_envelope_id,
                "width_x_m": layout.module_footprint_x_m,
                "height_y_m": layout.module_footprint_y_m,
            },
            "fixture_asset_set_id": layout.fixture_asset_set_id,
            "global_linear_dimming_factor": 0.5,
            "effective_watts_by_control_zone": [
                value * 0.5 for value in schedule.watts_by_control_zone
            ],
        },
    )
    return layout, schedule, state


def test_control_reuses_exact_conventional_led_authority_and_photon_fractions() -> None:
    controlled = build_controlled_conventional_led_proposed_source_model()
    conventional = build_conventional_spectral_distribution()

    assert controlled.source_model_id == PROPOSED_CONVENTIONAL_LED_CONTROL_SOURCE_MODEL_ID
    assert controlled.spectral_distribution_id == conventional.distribution_id
    assert controlled.resource_hashes == {
        CONVENTIONAL_SPD_RESOURCE_NAME: CONVENTIONAL_SPD_SHA256
    }
    assert dict(controlled.band_photon_fractions_relative_to_par) == (
        dict(conventional.par_band_photon_fractions)
        | {"far_red": conventional.far_red_relative_to_par}
    )
    assert controlled.far_red_relative_to_par == conventional.far_red_relative_to_par
    identity = proposed_spectral_basis_payload(controlled)
    assert identity["counterfactual_spectral_control"] is True
    assert identity["physical_proposed_spectral_configuration"] is False
    assert identity["control_provenance"]["authority_boundary"] == {
        "id": "proposed_completed_fixture_aperture",
        "description": "Proposed completed fixture aperture",
        "completed_aperture_par_ppe_umol_per_j": 2.6,
        "accepted_fixture_transmission": ACCEPTED_FIXTURE_TRANSMISSION,
        "internal_par_ppe_umol_per_j": INTERNAL_SOURCE_PPE_UMOL_PER_J,
        "far_red_outside_par_anchor": True,
    }


def test_controlled_band_budgets_close_at_internal_and_completed_apertures() -> None:
    layout, schedule, _state = _layout_schedule_state()
    controlled = build_controlled_conventional_led_proposed_source_model()
    plans = build_five_band_source_plans(controlled, layout, schedule)
    par = plans[:4]

    assert math.fsum(item.fraction_relative_to_par for item in par) == 1.0
    assert math.fsum(
        item.internal_band_photon_yield_umol_per_j for item in par
    ) == pytest.approx(INTERNAL_SOURCE_PPE_UMOL_PER_J, abs=1e-15)
    assert math.fsum(
        item.internal_band_photon_yield_umol_per_j
        * ACCEPTED_FIXTURE_TRANSMISSION
        for item in par
    ) == pytest.approx(COMPLETED_APERTURE_PPE_UMOL_PER_J, abs=1e-15)
    assert plans[-1].band_id == "far_red"
    assert plans[-1].fraction_relative_to_par == controlled.far_red_relative_to_par
    assert plans[-1].fraction_relative_to_par not in {
        item.fraction_relative_to_par for item in par
    }


@pytest.mark.parametrize(
    "transmissions",
    (
        {},
        {**PROPOSED_MODELED_BAND_TRANSMISSION, "blue": 0.8},
        {**PROPOSED_MODELED_BAND_TRANSMISSION, "extra": ACCEPTED_FIXTURE_TRANSMISSION},
    ),
)
def test_control_fails_closed_for_missing_incompatible_or_ambiguous_stack(
    transmissions: dict[str, float],
) -> None:
    with pytest.raises(ValueError, match="controlled spectrum"):
        build_controlled_conventional_led_proposed_source_model(
            transmission_by_band=transmissions
        )


def test_control_fails_closed_for_stale_or_missing_spectral_authority() -> None:
    controlled = build_controlled_conventional_led_proposed_source_model()
    with pytest.raises(ValueError, match="authority"):
        replace(controlled, resource_hashes={})
    with pytest.raises(ValueError, match="authority"):
        replace(controlled, control_provenance={})

    layout, schedule, _state = _layout_schedule_state()
    plan = build_five_band_source_plans(controlled, layout, schedule)[0]
    with pytest.raises(ValueError, match="approved Conventional LED SPD"):
        SmdBandSourcePlan.from_dict(
            plan.to_dict()
            | {"resource_hashes": {CONVENTIONAL_SPD_RESOURCE_NAME: "0" * 64}}
        )


def test_native_and_controlled_adapters_reuse_one_stage_a_state_and_power(
    tmp_path,
) -> None:
    layout, schedule, state = _layout_schedule_state()
    fixture_occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=tmp_path / "fixture_occlusion",
    )
    materialize_fixture_occlusion(fixture_occlusion)
    common = {
        "layout": layout,
        "full_output_schedule": schedule,
        "dimming_factor": 0.5,
        "room_text": room_radiance_text(RoomDimensions(3.048, 3.048, 3.048)),
        "source_state": state,
        "quality_profile": "direct",
        "threads": 1,
        "oconv_bin": "oconv",
        "rtrace_bin": "rtrace",
        "fixture_occlusion": fixture_occlusion,
    }
    native = build_proposed_juvenile_source_adapter(
        **common,
        spectral_basis=ProposedSpectralBasis.NATIVE_PROPOSED,
    )
    controlled = build_proposed_juvenile_source_adapter(
        **common,
        spectral_basis=ProposedSpectralBasis.CONVENTIONAL_LED_CONTROL,
    )

    assert native.source_state_id == controlled.source_state_id == state.source_state_id
    for key in (
        "global_dimming_factor",
        "full_output_schedule_total_w",
        "effective_schedule_total_w",
        "maximum_declared_module_w",
        "target_control_recomputed",
        "final_composite_trace",
    ):
        assert native.source_policy[key] == controlled.source_policy[key]
    assert [
        [zone["module_wattage"] for zone in band.source_provenance["zone_amplitudes"]]
        for band in native.bands
    ] == [
        [zone["module_wattage"] for zone in band.source_provenance["zone_amplitudes"]]
        for band in controlled.bands
    ]
    native_par_yield = math.fsum(
        band.source_provenance["internal_band_photon_yield_umol_per_j"]
        for band in native.bands[:4]
    )
    control_par_yield = math.fsum(
        band.source_provenance["internal_band_photon_yield_umol_per_j"]
        for band in controlled.bands[:4]
    )
    assert native_par_yield == pytest.approx(INTERNAL_SOURCE_PPE_UMOL_PER_J)
    assert control_par_yield == pytest.approx(INTERNAL_SOURCE_PPE_UMOL_PER_J)
    assert native.spectral_basis["source_model_id"] != (
        controlled.spectral_basis["source_model_id"]
    )
    controlled_source = build_controlled_conventional_led_proposed_source_model()
    assert (
        build_nominal_smd_source_model().band_photon_fractions_relative_to_par
        != controlled_source.band_photon_fractions_relative_to_par
    )
