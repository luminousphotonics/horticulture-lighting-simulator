"""Photomorphogenic response potential model for FSPM plant artifacts.

This module consumes spectral and photosynthetic response artifacts and produces
leaf/plant-level photomorphogenic response potential metrics. It does not
predict yield, biomass, harvest weight, or crop output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Literal, Mapping, overload

PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA = "rad_rebuild.fspm.plant_photomorphogenesis_response.v1"
PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA_VERSION = 1
PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME = "plant_photomorphogenesis_response.json"
PLANT_PHOTOMORPHOGENESIS_RESPONSE_METHOD = "spectral_ratio_morphology_response_v1"
PLANT_SPECTRAL_RESPONSE_SCHEMA = "rad_rebuild.fspm.plant_spectral_response.v1"
LEGACY_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA = (
    "rad_rebuild.fspm.plant_photosynthesis_response.v1"
)
PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA = (
    "rad_rebuild.fspm.plant_photosynthetic_light_response.v2"
)
SUPPORTED_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMAS = frozenset(
    {
        LEGACY_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
        PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
    }
)
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]


def _finite_non_negative(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number < 0.0:
        raise ValueError(f"{name} must be greater than or equal to zero.")
    return number


def _finite_positive(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _finite_fraction(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number) or number < 0.0 or number > 1.0:
        raise ValueError(f"{name} must be between 0 and 1.")
    return number


def _clamp01(value: float) -> float:
    return min(1.0, max(0.0, value))


def _safe_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    number = float(value)
    return number if math.isfinite(number) else default


def _scale_between(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp01((value - low) / (high - low))


def _inverse_scale_between(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp01((high - value) / (high - low))


def _coefficient_of_variation(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0.0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def _visual_value(value: float, *, min_value: float, max_value: float) -> float:
    if max_value <= min_value:
        return 0.5
    return _clamp01((value - min_value) / (max_value - min_value))


def _dominant_region(shade: float, compactness: float, expansion: float) -> str:
    if shade >= 0.65:
        return "shade_avoidance"
    if compactness >= 0.65:
        return "compact_response"
    if expansion >= 0.65:
        return "expansion_favorable"
    return "balanced"


@dataclass(frozen=True)
class PhotomorphogenesisResponseParameters:
    """Thresholds for spectral morphology response potential."""

    red_far_red_shade_threshold: float = 1.2
    red_far_red_full_sun_threshold: float = 4.0
    blue_fraction_compact_low: float = 0.10
    blue_fraction_compact_high: float = 0.32
    photosynthetic_expansion_threshold: float = 0.45
    shade_expansion_penalty: float = 0.45

    def __post_init__(self) -> None:
        shade = _finite_positive("red_far_red_shade_threshold", self.red_far_red_shade_threshold)
        full_sun = _finite_positive("red_far_red_full_sun_threshold", self.red_far_red_full_sun_threshold)
        if full_sun <= shade:
            raise ValueError("red_far_red_full_sun_threshold must be greater than red_far_red_shade_threshold.")
        blue_low = _finite_fraction("blue_fraction_compact_low", self.blue_fraction_compact_low)
        blue_high = _finite_fraction("blue_fraction_compact_high", self.blue_fraction_compact_high)
        if blue_high <= blue_low:
            raise ValueError("blue_fraction_compact_high must be greater than blue_fraction_compact_low.")
        _finite_fraction("photosynthetic_expansion_threshold", self.photosynthetic_expansion_threshold)
        _finite_fraction("shade_expansion_penalty", self.shade_expansion_penalty)

    def to_payload(self) -> dict[str, float]:
        return asdict(self)


def default_photomorphogenesis_response_parameters() -> PhotomorphogenesisResponseParameters:
    return PhotomorphogenesisResponseParameters()


def shade_avoidance_response_index(
    absorbed_red_to_far_red_ratio: float | None,
    parameters: PhotomorphogenesisResponseParameters,
) -> float:
    if absorbed_red_to_far_red_ratio is None:
        return 0.0
    ratio = _finite_non_negative("absorbed_red_to_far_red_ratio", absorbed_red_to_far_red_ratio)
    return _inverse_scale_between(
        ratio,
        parameters.red_far_red_shade_threshold,
        parameters.red_far_red_full_sun_threshold,
    )


def blue_compactness_response_index(
    absorbed_blue_to_par_fraction: float | None,
    parameters: PhotomorphogenesisResponseParameters,
) -> float:
    if absorbed_blue_to_par_fraction is None:
        return 0.0
    fraction = _finite_fraction("absorbed_blue_to_par_fraction", absorbed_blue_to_par_fraction)
    return _scale_between(
        fraction,
        parameters.blue_fraction_compact_low,
        parameters.blue_fraction_compact_high,
    )


def _spectral_leaf_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if payload.get("schema") != PLANT_SPECTRAL_RESPONSE_SCHEMA:
        raise ValueError("Unsupported plant spectral-response schema.")
    rows = payload.get("leaf_summaries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plant spectral-response payload must include leaf_summaries.")
    return rows


def _photosynthesis_leaf_rows(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    if payload.get("schema") not in SUPPORTED_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMAS:
        raise ValueError("Unsupported plant photosynthesis-response schema.")
    rows = payload.get("leaf_summaries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plant photosynthesis-response payload must include leaf_summaries.")
    by_leaf: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        leaf_id = row.get("leaf_id")
        if isinstance(leaf_id, str) and leaf_id:
            by_leaf[leaf_id] = row
    return by_leaf


def _leaf_photomorphogenesis_summary(
    spectral_row: Mapping[str, Any],
    photosynthesis_by_leaf: Mapping[str, Mapping[str, Any]],
    parameters: PhotomorphogenesisResponseParameters,
) -> dict[str, Any]:
    leaf_id = spectral_row.get("leaf_id")
    plant_id = spectral_row.get("plant_id")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise ValueError("Each spectral leaf summary must include leaf_id.")
    if not isinstance(plant_id, str) or not plant_id:
        raise ValueError("Each spectral leaf summary must include plant_id.")

    photosynthesis_row = photosynthesis_by_leaf.get(leaf_id, {})
    area = _finite_positive(f"area_m2[{leaf_id}]", spectral_row.get("area_m2"))
    absorbed_blue_to_par = spectral_row.get("absorbed_blue_to_par_fraction")
    absorbed_red_to_far_red = spectral_row.get("absorbed_red_to_far_red_ratio")

    shade_index = shade_avoidance_response_index(
        _safe_float(absorbed_red_to_far_red) if absorbed_red_to_far_red is not None else None,
        parameters,
    )
    compactness_index = blue_compactness_response_index(
        _safe_float(absorbed_blue_to_par) if absorbed_blue_to_par is not None else None,
        parameters,
    )
    photosynthetic_index = _finite_fraction(
        f"photosynthetic_response_index_0_1[{leaf_id}]",
        photosynthesis_row.get("photosynthetic_response_index_0_1", 0.0),
    )

    expansion_from_photosynthesis = _scale_between(
        photosynthetic_index,
        parameters.photosynthetic_expansion_threshold,
        1.0,
    )
    expansion_response = _clamp01(
        expansion_from_photosynthesis
        * (1.0 - parameters.shade_expansion_penalty * shade_index)
    )
    elongation_response = _clamp01(shade_index * (1.0 - 0.35 * compactness_index))
    morphology_balance = _clamp01((compactness_index + expansion_response + (1.0 - shade_index)) / 3.0)

    return {
        "leaf_id": leaf_id,
        "plant_id": plant_id,
        "leaf_index": spectral_row.get("leaf_index"),
        "surface_count": spectral_row.get("surface_count"),
        "area_m2": area,
        "absorbed_blue_to_par_fraction": absorbed_blue_to_par,
        "absorbed_red_to_far_red_ratio": absorbed_red_to_far_red,
        "absorbed_par_photon_flux_density_umol_m2_s": spectral_row.get(
            "absorbed_par_photon_flux_density_umol_m2_s"
        ),
        "far_red_transmitted_photon_flux_umol_s": spectral_row.get(
            "transmitted_far_red_photon_flux_umol_s"
        ),
        "photosynthetic_response_index_0_1": photosynthetic_index,
        "shade_avoidance_response_index_0_1": shade_index,
        "blue_compactness_response_index_0_1": compactness_index,
        "canopy_expansion_response_index_0_1": expansion_response,
        "elongation_response_index_0_1": elongation_response,
        "morphology_balance_index_0_1": morphology_balance,
        "photomorphogenic_region": _dominant_region(
            shade_index,
            compactness_index,
            expansion_response,
        ),
    }


def _aggregate_plant_rows(leaf_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in leaf_rows:
        plant_id = row["plant_id"]
        item = grouped.setdefault(
            plant_id,
            {
                "plant_id": plant_id,
                "leaf_count": 0,
                "area_m2": 0.0,
                "shade_sum": 0.0,
                "compact_sum": 0.0,
                "expansion_sum": 0.0,
                "elongation_sum": 0.0,
                "balance_sum": 0.0,
            },
        )
        area = float(row["area_m2"])
        item["leaf_count"] += 1
        item["area_m2"] += area
        item["shade_sum"] += float(row["shade_avoidance_response_index_0_1"])
        item["compact_sum"] += float(row["blue_compactness_response_index_0_1"])
        item["expansion_sum"] += float(row["canopy_expansion_response_index_0_1"])
        item["elongation_sum"] += float(row["elongation_response_index_0_1"])
        item["balance_sum"] += float(row["morphology_balance_index_0_1"])

    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        leaf_count = int(item["leaf_count"])
        shade = float(item.pop("shade_sum")) / leaf_count if leaf_count else 0.0
        compact = float(item.pop("compact_sum")) / leaf_count if leaf_count else 0.0
        expansion = float(item.pop("expansion_sum")) / leaf_count if leaf_count else 0.0
        elongation = float(item.pop("elongation_sum")) / leaf_count if leaf_count else 0.0
        balance = float(item.pop("balance_sum")) / leaf_count if leaf_count else 0.0
        item["shade_avoidance_response_index_0_1"] = shade
        item["blue_compactness_response_index_0_1"] = compact
        item["canopy_expansion_response_index_0_1"] = expansion
        item["elongation_response_index_0_1"] = elongation
        item["morphology_balance_index_0_1"] = balance
        item["photomorphogenic_region"] = _dominant_region(shade, compact, expansion)
        rows.append(item)

    return sorted(rows, key=lambda item: item["plant_id"])


def _visualization_payload(leaf_rows: list[dict[str, Any]]) -> dict[str, Any]:
    key = "morphology_balance_index_0_1"
    values = [float(row[key]) for row in leaf_rows]
    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 0.0

    return {
        "color_metric": key,
        "normalization": "linear_0_1",
        "leaf_scale": {
            "min": min_value,
            "max": max_value,
        },
        "leaf_values": [
            {
                "leaf_id": row["leaf_id"],
                "plant_id": row["plant_id"],
                "photomorphogenic_region": row["photomorphogenic_region"],
                key: row[key],
                "shade_avoidance_response_index_0_1": row[
                    "shade_avoidance_response_index_0_1"
                ],
                "blue_compactness_response_index_0_1": row[
                    "blue_compactness_response_index_0_1"
                ],
                "canopy_expansion_response_index_0_1": row[
                    "canopy_expansion_response_index_0_1"
                ],
                "elongation_response_index_0_1": row[
                    "elongation_response_index_0_1"
                ],
                "visual_intensity_0_1": _visual_value(
                    float(row[key]),
                    min_value=min_value,
                    max_value=max_value,
                ),
            }
            for row in leaf_rows
        ],
    }


def build_plant_photomorphogenesis_response_payload(
    spectral_response_payload: Mapping[str, Any],
    photosynthesis_response_payload: Mapping[str, Any],
    parameters: PhotomorphogenesisResponseParameters | None = None,
    *,
    method: str = PLANT_PHOTOMORPHOGENESIS_RESPONSE_METHOD,
) -> dict[str, Any]:
    params = parameters or default_photomorphogenesis_response_parameters()
    spectral_rows = _spectral_leaf_rows(spectral_response_payload)
    photosynthesis_by_leaf = _photosynthesis_leaf_rows(photosynthesis_response_payload)

    leaf_rows = [
        _leaf_photomorphogenesis_summary(row, photosynthesis_by_leaf, params)
        for row in spectral_rows
    ]
    plant_rows = _aggregate_plant_rows(leaf_rows)

    shade_values = [float(row["shade_avoidance_response_index_0_1"]) for row in plant_rows]
    balance_values = [float(row["morphology_balance_index_0_1"]) for row in plant_rows]

    return {
        "schema": PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA,
        "schema_version": PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA_VERSION,
        "status": "computed",
        "method": method,
        "legacy_output": True,
        "morphology_hypothesis_status": "legacy_heuristic_response_potential",
        "core_photoreceptor_exposure_artifact": (
            "runtime_state/plant_photoreceptor_exposure.json"
        ),
        "source_spectral_response_schema": spectral_response_payload.get("schema"),
        "source_spectral_response_method": spectral_response_payload.get("method"),
        "source_photosynthesis_response_schema": photosynthesis_response_payload.get("schema"),
        "source_photosynthesis_response_method": photosynthesis_response_payload.get("method"),
        "source_spectral_distribution": spectral_response_payload.get("spectral_distribution"),
        "source_artifacts": {
            "spectral_response": "runtime_state/plant_spectral_response.json",
            "photosynthesis_response": "runtime_state/plant_photosynthesis_response.json",
        },
        "parameters": params.to_payload(),
        "units": {
            "response_indices": "0..1",
            "area": "m2",
        },
        "plant_count": spectral_response_payload.get("plant_count"),
        "leaf_count": spectral_response_payload.get("leaf_count"),
        "surface_count": spectral_response_payload.get("surface_count"),
        "mean_plant_shade_avoidance_response_index_0_1": (
            sum(shade_values) / len(shade_values) if shade_values else 0.0
        ),
        "plant_to_plant_shade_avoidance_cv": _coefficient_of_variation(shade_values),
        "mean_plant_morphology_balance_index_0_1": (
            sum(balance_values) / len(balance_values) if balance_values else 0.0
        ),
        "plant_to_plant_morphology_balance_cv": _coefficient_of_variation(balance_values),
        "shade_avoidance_leaf_count": sum(
            1 for row in leaf_rows if row["photomorphogenic_region"] == "shade_avoidance"
        ),
        "compact_response_leaf_count": sum(
            1 for row in leaf_rows if row["photomorphogenic_region"] == "compact_response"
        ),
        "expansion_favorable_leaf_count": sum(
            1 for row in leaf_rows if row["photomorphogenic_region"] == "expansion_favorable"
        ),
        "plant_summaries": plant_rows,
        "leaf_summaries": leaf_rows,
        "visualization": _visualization_payload(leaf_rows),
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": [
            "This legacy artifact is retained for compatibility; core photoreceptor exposure inputs are reported separately.",
            "Photomorphogenic response values are spectral response potentials from explicit threshold parameters.",
            "This artifact is intended for relative lighting-analysis comparisons, not crop-output prediction.",
        ],
        "limitations": [
            "The model does not include cultivar calibration, hormone signaling, temperature, water stress, nutrient stress, or measured morphology outcomes.",
            "The model does not predict yield, biomass, harvest weight, growth rate, or crop output.",
        ],
    }


@overload
def write_plant_photomorphogenesis_response_artifact(
    target_dir: str | Path,
    spectral_response_payload: Mapping[str, Any],
    photosynthesis_response_payload: Mapping[str, Any],
    parameters: PhotomorphogenesisResponseParameters | None = ...,
    *,
    return_payload: Literal[True],
) -> tuple[Path, dict[str, Any]]: ...


@overload
def write_plant_photomorphogenesis_response_artifact(
    target_dir: str | Path,
    spectral_response_payload: Mapping[str, Any],
    photosynthesis_response_payload: Mapping[str, Any],
    parameters: PhotomorphogenesisResponseParameters | None = ...,
    *,
    return_payload: Literal[False] = ...,
) -> Path: ...


def write_plant_photomorphogenesis_response_artifact(
    target_dir: str | Path,
    spectral_response_payload: Mapping[str, Any],
    photosynthesis_response_payload: Mapping[str, Any],
    parameters: PhotomorphogenesisResponseParameters | None = None,
    *,
    return_payload: bool = False,
) -> Path | tuple[Path, dict[str, Any]]:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_plant_photomorphogenesis_response_payload(
        spectral_response_payload,
        photosynthesis_response_payload,
        parameters,
    )
    path = output_dir / PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME
    path.write_text(
        json.dumps(compact_plant_photomorphogenesis_response_payload(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return (path, payload) if return_payload else path


def compact_plant_photomorphogenesis_response_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(payload).items()
        if key not in {"plant_summaries", "leaf_summaries", "visualization"}
    }
