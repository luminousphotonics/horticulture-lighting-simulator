from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from fspm_optics.application.spatial_uniformity import (
    compute_spatial_uniformity,
)
from fspm_optics.application.target_control import (
    apply_target_control,
    derive_full_output_schedule,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout


def test_known_vector_has_exact_population_uniformity_metrics() -> None:
    metrics = compute_spatial_uniformity([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])

    assert metrics.minimum_ppfd == 2.0
    assert metrics.maximum_ppfd == 9.0
    assert metrics.mean_ppfd == 5.0
    assert metrics.population_standard_deviation_ppfd == 2.0
    assert metrics.coefficient_of_variation == 0.4
    assert metrics.coefficient_of_variation_percent == 40.0
    assert metrics.degree_of_uniformity_percent == 60.0
    assert metrics.minimum_to_mean_uniformity == 0.4
    assert metrics.minimum_to_maximum_ppfd_ratio == 2.0 / 9.0
    assert metrics.sample_count == 8


def test_uniform_field_has_zero_cv_and_unit_minimum_to_mean() -> None:
    metrics = compute_spatial_uniformity([17.5, 17.5, 17.5, 17.5])

    assert metrics.population_standard_deviation_ppfd == 0.0
    assert metrics.coefficient_of_variation == 0.0
    assert metrics.coefficient_of_variation_percent == 0.0
    assert metrics.degree_of_uniformity_percent == 100.0
    assert metrics.minimum_to_mean_uniformity == 1.0
    assert metrics.minimum_to_maximum_ppfd_ratio == 1.0


def test_global_scaling_preserves_ratios_and_scales_dimensional_metrics() -> None:
    values = np.asarray([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    full = compute_spatial_uniformity(values)
    scaled = compute_spatial_uniformity(values * 0.25)

    assert scaled.coefficient_of_variation == full.coefficient_of_variation
    assert (
        scaled.minimum_to_mean_uniformity
        == full.minimum_to_mean_uniformity
    )
    assert (
        scaled.minimum_to_maximum_ppfd_ratio
        == full.minimum_to_maximum_ppfd_ratio
    )
    assert scaled.minimum_ppfd == full.minimum_ppfd * 0.25
    assert scaled.maximum_ppfd == full.maximum_ppfd * 0.25
    assert scaled.mean_ppfd == full.mean_ppfd * 0.25
    assert scaled.population_standard_deviation_ppfd == (
        full.population_standard_deviation_ppfd * 0.25
    )


def test_unreachable_target_reports_maximum_output_field_uniformity() -> None:
    layout = generate_proposed_led_layout(10, 10)
    basis = np.asarray(
        [
            [1.0, 0.2, 0.1, 0.1, 0.2],
            [0.2, 1.0, 0.2, 0.1, 0.1],
            [0.1, 0.2, 1.0, 0.2, 0.1],
            [0.2, 0.1, 0.2, 1.0, 0.2],
        ],
        dtype=np.float64,
    )
    full = derive_full_output_schedule(basis, layout)
    controlled = apply_target_control(full, full.full_output_mean_ppfd * 2.0)
    full_uniformity = compute_spatial_uniformity(full.full_output_field)
    reported_uniformity = compute_spatial_uniformity(controlled.achieved_field)

    assert controlled.feasible is False
    assert controlled.dimming_factor == 1.0
    assert reported_uniformity == full_uniformity


def test_degree_of_uniformity_uses_exact_unclamped_formula() -> None:
    metrics = compute_spatial_uniformity([0.0, 0.0, 0.0, 100.0])

    assert metrics.degree_of_uniformity_percent == (
        100.0 - metrics.coefficient_of_variation_percent
    )
    assert metrics.degree_of_uniformity_percent < 0.0


@pytest.mark.parametrize(
    "samples",
    (
        [],
        [1.0, float("nan")],
        [1.0, float("inf")],
        [0.0, 0.0],
        [-1.0, 1.0],
        [-2.0, -1.0],
    ),
)
def test_invalid_sample_vectors_fail_closed(samples: list[float]) -> None:
    with pytest.raises(ValueError):
        compute_spatial_uniformity(samples)


def test_nonpositive_maximum_fails_closed_explicitly() -> None:
    with pytest.raises(ValueError, match="Maximum PPFD"):
        compute_spatial_uniformity([-1.0, 0.0])


def test_report_contains_values_definitions_and_complete_sample_provenance() -> None:
    report = compute_spatial_uniformity([2.0, 4.0, 6.0]).report_dict()
    values = report["values"]
    definitions = report["definitions"]
    provenance = report["provenance"]

    assert values["minimum_ppfd"] == 2.0
    assert values["maximum_ppfd"] == 6.0
    assert values["mean_ppfd"] == 4.0
    assert "coefficient_of_variation" in values
    assert "minimum_to_mean_uniformity" in values
    assert values["degree_of_uniformity_percent"] == (
        100.0 - values["coefficient_of_variation_percent"]
    )
    assert values["minimum_to_maximum_ppfd_ratio"] == 1.0 / 3.0
    assert "population denominator N" in definitions[
        "population_standard_deviation_ppfd"
    ]
    assert provenance["sample_basis"] == "complete_raw_reference_plane_sample_set"
    assert provenance["reported_field"].startswith("final_achieved")
    for operation in (
        "interpolation",
        "smoothing",
        "symmetrization",
        "clipping",
        "spatial_normalization_or_correction",
        "target_tolerance_filtering",
        "sample_exclusion",
        "boundary_sample_exclusion",
    ):
        assert provenance[operation] is False


def test_metrics_json_and_manifest_receive_the_same_uniformity_report() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "fspm_optics"
        / "application"
        / "publication.py"
    ).read_text(encoding="utf-8")

    assert source.count('"spatial_uniformity": spatial_report') == 2
