"""Spatial-uniformity metrics for validated reference-plane PPFD samples."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np


@dataclass(frozen=True, slots=True)
class SpatialUniformityMetrics:
    """Float64 population statistics for one complete achieved PPFD field."""

    minimum_ppfd: float
    maximum_ppfd: float
    mean_ppfd: float
    population_standard_deviation_ppfd: float
    coefficient_of_variation: float
    coefficient_of_variation_percent: float
    degree_of_uniformity_percent: float
    minimum_to_mean_uniformity: float
    minimum_to_maximum_ppfd_ratio: float
    sample_count: int

    def values_dict(self) -> dict[str, float | int]:
        return {
            "minimum_ppfd": self.minimum_ppfd,
            "maximum_ppfd": self.maximum_ppfd,
            "mean_ppfd": self.mean_ppfd,
            "population_standard_deviation_ppfd": (
                self.population_standard_deviation_ppfd
            ),
            "coefficient_of_variation": self.coefficient_of_variation,
            "coefficient_of_variation_percent": (
                self.coefficient_of_variation_percent
            ),
            "degree_of_uniformity_percent": self.degree_of_uniformity_percent,
            "minimum_to_mean_uniformity": self.minimum_to_mean_uniformity,
            "minimum_to_maximum_ppfd_ratio": (
                self.minimum_to_maximum_ppfd_ratio
            ),
            "sample_count": self.sample_count,
        }

    def report_dict(
        self,
        *,
        reported_field: str = (
            "final_achieved_target_controlled_horizontal_reference_plane_ppfd"
        ),
    ) -> dict[str, object]:
        return {
            "schema_id": "fspm-optics.spatial-uniformity",
            "schema_version": 1,
            "values": self.values_dict(),
            "definitions": spatial_uniformity_definitions(),
            "provenance": spatial_uniformity_provenance(
                reported_field=reported_field
            ),
        }


def compute_spatial_uniformity(
    ppfd_samples: Sequence[float] | np.ndarray,
) -> SpatialUniformityMetrics:
    """Compute the declared metrics from every supplied sample in Float64."""

    try:
        values = np.asarray(ppfd_samples, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ValueError("PPFD samples must be a one-dimensional numeric vector.") from exc
    if values.ndim != 1 or values.size == 0:
        raise ValueError("PPFD samples must be a non-empty one-dimensional vector.")
    if not np.all(np.isfinite(values)):
        raise ValueError("PPFD samples must contain only finite values.")
    maximum = float(values.max())
    if not math.isfinite(maximum) or maximum <= 0.0:
        raise ValueError("Maximum PPFD sample must be finite and positive.")
    mean = float(values.mean(dtype=np.float64))
    if not math.isfinite(mean) or mean <= 0.0:
        raise ValueError("PPFD sample mean must be finite and positive.")
    minimum = float(values.min())
    standard_deviation = float(values.std(ddof=0, dtype=np.float64))
    coefficient_of_variation = standard_deviation / mean
    coefficient_of_variation_percent = coefficient_of_variation * 100.0
    degree_of_uniformity_percent = 100.0 - coefficient_of_variation_percent
    minimum_to_mean = minimum / mean
    minimum_to_maximum = minimum / maximum
    results = (
        minimum,
        maximum,
        mean,
        standard_deviation,
        coefficient_of_variation,
        coefficient_of_variation_percent,
        degree_of_uniformity_percent,
        minimum_to_mean,
        minimum_to_maximum,
    )
    if any(not math.isfinite(value) for value in results):
        raise ValueError("Spatial-uniformity calculation produced a non-finite value.")
    return SpatialUniformityMetrics(
        minimum_ppfd=minimum,
        maximum_ppfd=maximum,
        mean_ppfd=mean,
        population_standard_deviation_ppfd=standard_deviation,
        coefficient_of_variation=coefficient_of_variation,
        coefficient_of_variation_percent=coefficient_of_variation_percent,
        degree_of_uniformity_percent=degree_of_uniformity_percent,
        minimum_to_mean_uniformity=minimum_to_mean,
        minimum_to_maximum_ppfd_ratio=minimum_to_maximum,
        sample_count=int(values.size),
    )


def spatial_uniformity_definitions() -> dict[str, str]:
    """Return stable, machine-readable definitions recorded in run artifacts."""

    return {
        "minimum_ppfd": "minimum(samples)",
        "maximum_ppfd": "maximum(samples)",
        "mean_ppfd": "sum(samples) / N",
        "population_standard_deviation_ppfd": (
            "sqrt(sum((sample - mean_ppfd)^2) / N); population denominator N"
        ),
        "coefficient_of_variation": (
            "population_standard_deviation_ppfd / mean_ppfd"
        ),
        "coefficient_of_variation_percent": (
            "coefficient_of_variation * 100"
        ),
        "degree_of_uniformity_percent": (
            "100.0 - coefficient_of_variation_percent; not clamped"
        ),
        "minimum_to_mean_uniformity": "minimum_ppfd / mean_ppfd",
        "minimum_to_maximum_ppfd_ratio": "minimum_ppfd / maximum_ppfd",
    }


def spatial_uniformity_provenance(
    *,
    reported_field: str = (
        "final_achieved_target_controlled_horizontal_reference_plane_ppfd"
    ),
) -> dict[str, object]:
    """Declare the exact sample basis and absence of spatial transformations."""

    if not isinstance(reported_field, str) or not reported_field:
        raise ValueError("reported_field provenance must be non-empty.")
    return {
        "reported_field": reported_field,
        "sample_basis": "complete_raw_reference_plane_sample_set",
        "calculation_input": (
            "in_memory_float64_vector_before_ppfd_csv_serialization"
        ),
        "published_sample_artifact": "ppfd.csv",
        "numeric_precision": "Float64",
        "population_standard_deviation": True,
        "population_standard_deviation_denominator": "N",
        "interpolation": False,
        "smoothing": False,
        "symmetrization": False,
        "clipping": False,
        "spatial_normalization_or_correction": False,
        "target_tolerance_filtering": False,
        "sample_exclusion": False,
        "boundary_sample_exclusion": False,
    }
