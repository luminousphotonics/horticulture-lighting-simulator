from __future__ import annotations

import math

import pytest

from fspm_optics.fixtures.conventional_led.angular import (
    normalize_lm63_angular_distribution,
)
from fspm_optics.fixtures.conventional_led.lm63 import (
    load_approved_lm63,
    parse_lm63,
)
from fspm_optics.fixtures.conventional_led.resources import CONVENTIONAL_IES_SHA256


def _synthetic_lm63(
    *,
    candela_scale: float = 1.0,
    candela_multiplier: float = 1.0,
    ballast_factor: float = 1.0,
    future_use: float = 1.1,
) -> str:
    values = tuple(value * candela_scale for value in (4, 2, 0, 2, 1, 0, 4, 2, 0))
    return (
        "IESNA:LM-63-2019\n"
        "[TEST] SYNTHETIC\n"
        "TILT=NONE\n"
        f"1 -1 {candela_multiplier} 3 3 1 2 1 1 0.1\n"
        f"{ballast_factor} {future_use} 100\n"
        "0 90 180\n"
        "0 180 360\n"
        + " ".join(str(value) for value in values)
        + "\n"
    )


def _normalized(text: str):
    return normalize_lm63_angular_distribution(
        parse_lm63(text),
        asset_sha256="a" * 64,
    )


def _flat(values: tuple[tuple[float, ...], ...]) -> tuple[float, ...]:
    return tuple(value for row in values for value in row)


def test_approved_distribution_covers_periodic_domain_and_normalizes_to_one() -> None:
    distribution = normalize_lm63_angular_distribution(
        load_approved_lm63(),
        asset_sha256=CONVENTIONAL_IES_SHA256,
    )

    assert len(distribution.relative_candela_by_unique_plane) == 16
    assert len(distribution.cell_probabilities) == 16
    assert all(len(row) == 180 for row in distribution.cell_probabilities)
    assert distribution.horizontal_domain_radians == pytest.approx(2.0 * math.pi)
    assert distribution.duplicate_closure_plane_counted == 1
    assert distribution.total_probability == pytest.approx(1.0, abs=1e-12)
    assert distribution.applied_factors.candela_multiplier == 1.0
    assert distribution.applied_factors.ballast_factor == 1.0
    assert distribution.applied_factors.future_use_factor == 1.1
    assert distribution.applied_factors.product == pytest.approx(1.1)
    assert distribution.relative_candela_by_unique_plane[0] != (
        distribution.relative_candela_by_unique_plane[8]
    )


def test_uniform_candela_scaling_cannot_change_normalized_angular_allocation() -> None:
    base = _normalized(_synthetic_lm63(candela_scale=1.0))
    scaled = _normalized(_synthetic_lm63(candela_scale=37.0))

    assert _flat(scaled.relative_candela_by_unique_plane) == pytest.approx(
        _flat(base.relative_candela_by_unique_plane)
    )
    assert _flat(scaled.cell_probabilities) == pytest.approx(
        _flat(base.cell_probabilities)
    )
    assert scaled.scaled_table_integral_cd_sr == pytest.approx(
        37.0 * base.scaled_table_integral_cd_sr
    )


def test_future_use_factor_is_applied_once_then_cancels_from_unit_shape() -> None:
    unity = _normalized(_synthetic_lm63(future_use=1.0))
    recorded = _normalized(_synthetic_lm63(future_use=1.1))

    assert _flat(recorded.cell_probabilities) == pytest.approx(
        _flat(unity.cell_probabilities)
    )
    assert recorded.scaled_table_integral_cd_sr == pytest.approx(
        1.1 * unity.scaled_table_integral_cd_sr
    )
    assert recorded.applied_factors.product == pytest.approx(1.1)


def test_all_lm63_magnitude_fields_are_recorded_and_applied_no_more_than_once() -> None:
    unity = _normalized(
        _synthetic_lm63(
            candela_multiplier=1.0,
            ballast_factor=1.0,
            future_use=1.0,
        )
    )
    factored = _normalized(
        _synthetic_lm63(
            candela_multiplier=2.0,
            ballast_factor=3.0,
            future_use=1.1,
        )
    )

    assert factored.applied_factors.product == pytest.approx(6.6)
    assert factored.scaled_table_integral_cd_sr == pytest.approx(
        6.6 * unity.scaled_table_integral_cd_sr
    )
    assert _flat(factored.cell_probabilities) == pytest.approx(
        _flat(unity.cell_probabilities)
    )


def test_declared_ppf_is_applied_separately_and_exactly_once() -> None:
    unity = _normalized(_synthetic_lm63(future_use=1.0))
    recorded = _normalized(_synthetic_lm63(future_use=1.1))

    for distribution in (unity, recorded):
        allocation = distribution.allocate_ppf_umol_s(1716.0)
        assert math.fsum(value for row in allocation for value in row) == pytest.approx(
            1716.0,
            abs=1e-9,
        )


def test_angular_serialization_is_deterministic() -> None:
    first = _normalized(_synthetic_lm63())
    second = _normalized(_synthetic_lm63())

    assert first.distribution_id == second.distribution_id
    assert first.to_payload() == second.to_payload()
