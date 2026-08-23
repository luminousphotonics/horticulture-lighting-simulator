from __future__ import annotations

import pytest

from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import (
    LEGACY_DEFAULT_CONTROL_ZONE_POWER_W,
    build_legacy_default_module_schedule,
    build_optimized_module_schedule,
    legacy_default_control_zone_power_schedule,
)


def test_explicit_five_zone_vector_maps_to_every_ten_by_ten_module() -> None:
    layout = generate_proposed_led_layout(10, 10)
    coefficients = (10.0, 20.0, 30.0, 40.0, 50.0)
    schedule = build_optimized_module_schedule(layout, coefficients)

    assert schedule.control_zone_count == 5
    assert schedule.module_count == 61
    assert len(schedule.watts_by_module) == 61
    assert schedule.watts_by_control_zone == coefficients
    assert schedule.min_watts == 10.0
    assert schedule.max_watts == 50.0
    assert schedule.total_watts == pytest.approx(
        5 * 10.0 + 8 * 20.0 + 12 * 30.0 + 16 * 40.0 + 20 * 50.0
    )


def test_same_zone_modules_receive_the_same_wattage() -> None:
    layout = generate_proposed_led_layout(12, 10)
    coefficients = tuple(10.0 + zone for zone in layout.control_zone_indices)
    schedule = build_optimized_module_schedule(layout, coefficients)

    for module, watts in zip(
        layout.modules, schedule.watts_by_module, strict=True
    ):
        assert watts == coefficients[module.control_zone_index]
    for zone in layout.control_zone_indices:
        assert {
            watts
            for module, watts in zip(
                layout.modules, schedule.watts_by_module, strict=True
            )
            if module.control_zone_index == zone
        } == {coefficients[zone]}


def test_missing_control_zone_coefficient_fails_clearly() -> None:
    layout = generate_proposed_led_layout(10, 10)
    with pytest.raises(
        ValueError, match=r"missing control-zone coefficients: expected 5, received 4"
    ):
        build_optimized_module_schedule(layout, [1.0, 2.0, 3.0, 4.0])


def test_extra_control_zone_coefficient_fails_by_default() -> None:
    layout = generate_proposed_led_layout(10, 10)
    with pytest.raises(
        ValueError, match=r"extra control-zone coefficients: expected 5, received 6"
    ):
        build_optimized_module_schedule(layout, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0])


def test_extra_coefficients_require_explicit_named_option() -> None:
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(
        layout,
        [1.0, 2.0, 3.0, 4.0, 5.0, 999.0],
        allow_extra_coefficients=True,
    )
    assert schedule.watts_by_control_zone == (1.0, 2.0, 3.0, 4.0, 5.0)
    assert 999.0 not in schedule.watts_by_module


def test_negative_wattage_fails_clearly() -> None:
    layout = generate_proposed_led_layout(10, 10)
    with pytest.raises(ValueError, match=r"coefficient 2 must not be negative"):
        build_optimized_module_schedule(layout, [1.0, 2.0, -3.0, 4.0, 5.0])


def test_legacy_default_schedule_remains_available_but_separate() -> None:
    layout = generate_proposed_led_layout(10, 10)
    legacy = legacy_default_control_zone_power_schedule()
    module_schedule = build_legacy_default_module_schedule(layout)

    assert legacy.watts_by_control_zone == LEGACY_DEFAULT_CONTROL_ZONE_POWER_W
    assert module_schedule.schedule_source.startswith("smd_legacy_default")
    assert module_schedule.watts_by_control_zone == (
        LEGACY_DEFAULT_CONTROL_ZONE_POWER_W[: layout.control_zone_count]
    )


def test_optimized_schedule_never_inherits_legacy_final_fallback() -> None:
    layout = generate_proposed_led_layout(20, 20)
    assert layout.control_zone_count == 10
    explicit = tuple(float(zone + 1) for zone in layout.control_zone_indices)
    schedule = build_optimized_module_schedule(layout, explicit)

    assert schedule.watts_by_control_zone == explicit
    assert schedule.watts_by_control_zone[-1] == 10.0
    assert schedule.watts_by_control_zone[-1] != LEGACY_DEFAULT_CONTROL_ZONE_POWER_W[-1]
