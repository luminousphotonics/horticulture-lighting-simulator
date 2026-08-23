from __future__ import annotations

import math

import numpy as np
import pytest

from fspm_optics.diagnostics.stage_a_validator import reconstruct_proposed_field
from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_FIXTURE_POWER_W,
    CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
    CONVENTIONAL_FIXTURE_PPF_UMOL_S,
    build_conventional_radiance_source_plan,
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.smd.optical_stack import (
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import (
    build_optimized_module_schedule,
)
from fspm_optics.fixtures.smd.radiance_writer import (
    build_smd_radiance_document,
)


def test_large_proposed_topology_expands_one_watt_per_zone_only_by_modules() -> None:
    layout = generate_proposed_led_layout(
        30.0,
        50.0,
        proposed_layout_mode="legacy",
    )
    schedule = build_optimized_module_schedule(
        layout,
        tuple(1.0 for _zone in layout.control_zone_indices),
    )
    document = build_smd_radiance_document(layout, schedule)

    assert len(layout.fixtures) == 202
    assert len(layout.modules) == 791
    assert layout.control_zone_count == 16
    assert schedule.total_watts == 791.0
    assert document.metadata.emitter_primitive_count == 791
    assert document.radiance_text.count("_internal_emitter\n") == 791
    assert document.metadata.modeled_completed_aperture_par_ppf_umol_s == (
        pytest.approx(791.0 * COMPLETED_APERTURE_PPE_UMOL_PER_J)
    )


def test_large_proposed_unequal_zone_schedule_and_basis_have_one_multiplicity() -> None:
    layout = generate_proposed_led_layout(30.0, 50.0)
    zone_watts = tuple(float(zone + 1) for zone in layout.control_zone_indices)
    schedule = build_optimized_module_schedule(layout, zone_watts)
    expected_power = math.fsum(
        zone_watts[module.control_zone_index] for module in layout.modules
    )
    basis = np.asarray(
        [
            [float(zone + 1) for zone in layout.control_zone_indices],
            [float((zone + 1) ** 2) for zone in layout.control_zone_indices],
        ],
        dtype=float,
    )

    field = reconstruct_proposed_field(
        basis_matrix=basis,
        reference_watts_per_module=1.0,
        watts_by_control_zone=zone_watts,
        dimming_factor=1.0,
    )

    assert schedule.total_watts == expected_power
    assert field == pytest.approx(basis @ np.asarray(zone_watts))


def test_large_conventional_topology_has_eight_bar_apertures_per_fixture() -> None:
    layout = plan_conventional_layout_from_feet(
        30.0,
        50.0,
        policy="rolling_bench",
    )
    source = build_conventional_radiance_source_plan(
        layout,
        workspace="synthetic-stage-a-audit",
    )

    assert len(layout.fixtures) == 84
    assert len(source.apertures) == 84 * 8
    assert source.aperture_radiance_text().count(" polygon ") == 84 * 8
    assert source.whole_layout_transported_downward_ppf_umol_s == pytest.approx(
        84 * CONVENTIONAL_FIXTURE_PPF_UMOL_S
    )
    assert 84 * CONVENTIONAL_FIXTURE_POWER_W * CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J == (
        pytest.approx(source.whole_layout_transported_downward_ppf_umol_s)
    )
