from __future__ import annotations

import numpy as np
import pytest

from fspm_optics.application.target_control import (
    apply_target_control,
    derive_full_output_schedule,
    derive_uniform_full_output_schedule,
    resolve_global_source_dimming,
    resolve_fspm_target_policy,
    scale_global_output_field_float64,
)
from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER,
    derive_conventional_carrier_scale,
)
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout


def _basis() -> np.ndarray:
    return np.asarray(
        [
            [1.0, 0.25, 0.10, 0.05, 0.20],
            [0.2, 1.10, 0.20, 0.10, 0.10],
            [0.1, 0.15, 1.20, 0.20, 0.05],
            [0.3, 0.10, 0.20, 0.90, 0.30],
            [0.1, 0.20, 0.10, 0.25, 1.05],
        ],
        dtype=float,
    )


def test_relative_cv_schedule_is_target_independent_and_normalized() -> None:
    layout = generate_proposed_led_layout(10, 10)
    full = derive_full_output_schedule(_basis(), layout)
    repeated = derive_full_output_schedule(_basis(), layout)
    low = apply_target_control(full, full.full_output_mean_ppfd * 0.25)
    high = apply_target_control(full, full.full_output_mean_ppfd * 0.75)

    assert max(full.relative_coefficients) == pytest.approx(1.0)
    assert max(full.schedule.watts_by_control_zone) == pytest.approx(100.0)
    assert full.relative_coefficients == repeated.relative_coefficients
    assert full.schedule.watts_by_control_zone == repeated.schedule.watts_by_control_zone
    assert low.full_output_mean_ppfd == high.full_output_mean_ppfd
    assert low.full_output_power_w == high.full_output_power_w


def test_global_factor_reaches_feasible_target_without_field_correction() -> None:
    layout = generate_proposed_led_layout(10, 10)
    full = derive_full_output_schedule(_basis(), layout)
    requested = full.full_output_mean_ppfd * 0.4
    controlled = apply_target_control(full, requested)

    assert controlled.feasible is True
    assert controlled.dimming_factor == pytest.approx(0.4)
    assert controlled.achieved_mean_ppfd == pytest.approx(requested)
    assert controlled.achieved_field == pytest.approx(
        np.asarray(full.full_output_field) * 0.4
    )
    assert controlled.effective_power_w == pytest.approx(
        controlled.full_output_power_w * 0.4
    )
    assert controlled.effective_internal_par_ppf_umol_s == pytest.approx(
        controlled.full_output_internal_par_ppf_umol_s * 0.4
    )
    assert controlled.full_output_internal_par_ppf_umol_s == pytest.approx(
        controlled.full_output_power_w * INTERNAL_SOURCE_PPE_UMOL_PER_J
    )
    assert (
        controlled.full_output_modeled_completed_aperture_par_ppf_umol_s
        == pytest.approx(
            controlled.full_output_power_w * COMPLETED_APERTURE_PPE_UMOL_PER_J
        )
    )
    assert controlled.effective_modeled_completed_aperture_par_ppf_umol_s == pytest.approx(
        controlled.full_output_modeled_completed_aperture_par_ppf_umol_s * 0.4
    )
    emitted_ppf = (
        controlled.effective_modeled_completed_aperture_par_ppf_umol_s
    )
    assert emitted_ppf == pytest.approx(
        controlled.effective_power_w * COMPLETED_APERTURE_PPE_UMOL_PER_J
    )
    assert emitted_ppf != pytest.approx(
        controlled.effective_power_w * INTERNAL_SOURCE_PPE_UMOL_PER_J
    )
    assert controlled.effective_internal_par_ppf_umol_s == pytest.approx(
        controlled.effective_power_w * INTERNAL_SOURCE_PPE_UMOL_PER_J
    )
    assert (
        controlled.full_output_internal_par_ppf_umol_s
        * ACCEPTED_FIXTURE_TRANSMISSION
        == pytest.approx(
            controlled.full_output_modeled_completed_aperture_par_ppf_umol_s
        )
    )


def test_unreachable_target_returns_full_field_and_structured_infeasibility() -> None:
    layout = generate_proposed_led_layout(10, 10)
    full = derive_full_output_schedule(_basis(), layout)
    controlled = apply_target_control(full, full.full_output_mean_ppfd * 1.5)

    assert controlled.feasible is False
    assert controlled.dimming_factor == 1.0
    assert controlled.achieved_field == full.full_output_field
    assert controlled.achieved_mean_ppfd == pytest.approx(full.full_output_mean_ppfd)
    assert controlled.infeasibility is not None
    assert (
        controlled.infeasibility["code"]
        == "requested_target_exceeds_full_output_mean"
    )


def test_sampled_cap_uses_global_full_output_maximum_factor() -> None:
    layout = generate_proposed_led_layout(10, 10)
    full = derive_full_output_schedule(_basis(), layout)
    full_maximum = max(full.full_output_field)
    requested_cap = full_maximum * 0.4

    controlled = apply_target_control(
        full,
        requested_cap,
        "target_capped",
    )

    assert controlled.lighting_target_mode == "target_capped"
    assert controlled.dimming_factor == pytest.approx(0.4)
    assert controlled.achieved_field == pytest.approx(
        np.asarray(full.full_output_field) * 0.4
    )
    assert controlled.achieved_maximum_ppfd == pytest.approx(requested_cap)
    assert controlled.achieved_mean_ppfd < requested_cap
    assert controlled.cap_binding is True
    assert controlled.cap_compliant is True
    assert controlled.feasible is True
    assert controlled.infeasibility is None
    assert controlled.limiting_sample_index == int(
        np.argmax(full.full_output_field)
    )


def test_sampled_cap_above_full_output_is_nonbinding_and_feasible() -> None:
    layout = generate_proposed_led_layout(10, 10)
    full = derive_full_output_schedule(_basis(), layout)
    requested_cap = max(full.full_output_field) * 1.25

    controlled = apply_target_control(
        full,
        requested_cap,
        "target_capped",
    )

    assert controlled.dimming_factor == 1.0
    assert controlled.achieved_field == full.full_output_field
    assert controlled.cap_binding is False
    assert controlled.cap_compliant is True
    assert controlled.feasible is True
    assert controlled.infeasibility is None


def test_comparator_sampled_cap_resolves_against_full_output_maximum() -> None:
    controlled = resolve_global_source_dimming(
        requested_target_ppfd=600.0,
        full_output_mean_ppfd=800.0,
        full_output_maximum_ppfd=1200.0,
        lighting_target_mode="target_capped",
        system_id="conventional",
    )
    nonbinding = resolve_global_source_dimming(
        requested_target_ppfd=1300.0,
        full_output_mean_ppfd=800.0,
        full_output_maximum_ppfd=1200.0,
        lighting_target_mode="target_capped",
        system_id="conventional",
    )

    assert controlled.dimming_factor == 0.5
    assert controlled.cap_binding is True
    assert controlled.feasible is True
    assert nonbinding.dimming_factor == 1.0
    assert nonbinding.cap_binding is False
    assert nonbinding.feasible is True
    assert nonbinding.infeasibility is None


def test_sampled_cap_controller_accepts_exact_zero_for_playback_reuse() -> None:
    controlled = resolve_global_source_dimming(
        requested_target_ppfd=0.0,
        full_output_mean_ppfd=800.0,
        full_output_maximum_ppfd=1200.0,
        lighting_target_mode="target_capped",
        system_id="conventional",
    )

    assert controlled.raw_factor == 0.0
    assert controlled.dimming_factor == 0.0
    assert controlled.cap_binding is True
    assert controlled.feasible is True
    assert controlled.infeasibility is None


def test_conventional_float64_scaling_equals_former_global_carrier_scaling() -> None:
    full_output = np.asarray(
        [0.0, 1.0 / 3.0, 517.125, 1024.0000000000002],
        dtype=np.float64,
    )
    requested_factor = 0.8123456789
    former_carrier = derive_conventional_carrier_scale(
        1716.0 * requested_factor
    )
    authoritative_factor = (
        former_carrier.ies2rad_multiplier
        / CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER
    )

    scaled = np.asarray(
        scale_global_output_field_float64(
            full_output,
            authoritative_factor,
        ),
        dtype=np.float64,
    )
    former_linear_transport = np.multiply(
        full_output,
        np.float64(
            former_carrier.ies2rad_multiplier
            / CONVENTIONAL_FULL_OUTPUT_CARRIER_MULTIPLIER
        ),
        dtype=np.float64,
    )

    assert authoritative_factor != requested_factor
    assert np.array_equal(scaled, former_linear_transport)


def test_uniform_proposed_schedule_uses_the_same_sampled_cap_policy() -> None:
    layout = generate_proposed_led_layout(10, 10)
    full = derive_uniform_full_output_schedule(
        np.asarray([500.0, 750.0, 1000.0]),
        layout,
    )
    controlled = apply_target_control(full, 600.0, "target_capped")

    assert controlled.dimming_factor == 0.6
    assert controlled.achieved_field == pytest.approx((300.0, 450.0, 600.0))
    assert controlled.achieved_mean_ppfd == pytest.approx(450.0)
    assert controlled.achieved_maximum_ppfd == pytest.approx(600.0)


def test_fspm_automatic_override_and_inclusive_clamped_bounds() -> None:
    automatic = resolve_fspm_target_policy(
        mode="automatic",
        achieved_baseline_mean_ppfd=12.0,
        tolerance_umol_m2_s=20.0,
    )
    override = resolve_fspm_target_policy(
        mode="override",
        achieved_baseline_mean_ppfd=900.0,
        tolerance_umol_m2_s=20.0,
        override_umol_m2_s=750.0,
    )

    assert automatic.resolved_target_umol_m2_s == 12.0
    assert automatic.inclusive_lower_umol_m2_s == 0.0
    assert automatic.inclusive_upper_umol_m2_s == 32.0
    assert automatic.to_dict()["classification_range"]["bounds"] == "inclusive"
    assert automatic.to_dict()["schema_id"] == "fspm-optics.fspm-reference-policy"
    assert automatic.to_dict()["schema_version"] == 2
    assert automatic.to_dict()["reference_resolution_executes_transport"] is False
    assert "juvenile_fspm_transport_executed" not in automatic.to_dict()
    assert override.resolved_target_umol_m2_s == 750.0
    assert override.inclusive_lower_umol_m2_s == 730.0
    assert override.inclusive_upper_umol_m2_s == 770.0


def test_fspm_tolerance_does_not_cap_baseline_metrics() -> None:
    layout = generate_proposed_led_layout(10, 10)
    full = derive_full_output_schedule(_basis(), layout)
    controlled = apply_target_control(full, full.full_output_mean_ppfd * 0.6)
    before = controlled.achieved_field

    policy = resolve_fspm_target_policy(
        mode="override",
        achieved_baseline_mean_ppfd=controlled.achieved_mean_ppfd,
        tolerance_umol_m2_s=0.1,
        override_umol_m2_s=1.0,
    )

    assert controlled.achieved_field == before
    assert policy.affects_baseline_transport is False
