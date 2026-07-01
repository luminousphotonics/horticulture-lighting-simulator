"""Photosynthetic response potential model for FSPM plant artifacts.

This module consumes band-level absorbed photon flux from
`plant_spectral_response.json` and computes a leaf/plant photosynthetic
light-response potential. It does not predict yield, biomass, harvest weight,
or crop output.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

LEGACY_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA = (
    "rad_rebuild.fspm.plant_photosynthesis_response.v1"
)
PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA = (
    "rad_rebuild.fspm.plant_photosynthetic_light_response.v2"
)
PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA_VERSION = 2
PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME = "plant_photosynthesis_response.json"
PLANT_PHOTOSYNTHESIS_RESPONSE_METHOD = "absorbed_par_non_rectangular_hyperbola_v2"
PLANT_SPECTRAL_RESPONSE_SCHEMA = "rad_rebuild.fspm.plant_spectral_response.v1"
NO_CROP_OUTPUT_TERMS = ["yield", "biomass", "growth", "crop_output"]
PHOTOSYNTHESIS_TARGET_MODEL_ID = "lettuce_rosette_archetype_v1"
PHOTOSYNTHESIS_TARGET_MODEL_ARCHETYPE = (
    "generic lettuce / leafy-green rosette archetype"
)
PHOTOSYNTHESIS_CALIBRATION_STATUS = "uncalibrated_model_scaffold"
PHOTOSYNTHESIS_DEFAULT_PARAMETER_STATUS = "unvalidated_default_parameters"
SUPPORTED_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMAS = frozenset(
    {
        LEGACY_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
        PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
    }
)


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


def _finite_fraction_open(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number) or number <= 0.0 or number > 1.0:
        raise ValueError(f"{name} must be greater than zero and less than or equal to one.")
    return number


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return numerator / denominator


def _coefficient_of_variation(values: list[float]) -> float:
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    if mean <= 0.0:
        return 0.0
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance) / mean


def _weighted_mean(values: Iterable[tuple[float, float]]) -> float:
    total_weight = 0.0
    weighted_sum = 0.0
    for value, weight in values:
        if weight <= 0.0:
            continue
        total_weight += weight
        weighted_sum += value * weight
    return weighted_sum / total_weight if total_weight > 0.0 else 0.0


def _weighted_percentile(values: Iterable[tuple[float, float]], percentile: float) -> float:
    weighted_values = sorted(
        (value, weight)
        for value, weight in values
        if weight > 0.0
    )
    if not weighted_values:
        return 0.0
    fraction = min(1.0, max(0.0, percentile))
    total_weight = sum(weight for _, weight in weighted_values)
    threshold = total_weight * fraction
    cumulative = 0.0
    for value, weight in weighted_values:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return weighted_values[-1][0]


def _weighted_lower_tail_mean(
    values: Iterable[tuple[float, float]],
    tail_fraction: float,
) -> float:
    weighted_values = sorted(
        (value, weight)
        for value, weight in values
        if weight > 0.0
    )
    if not weighted_values:
        return 0.0
    total_weight = sum(weight for _, weight in weighted_values)
    target_weight = total_weight * min(1.0, max(0.0, tail_fraction))
    if target_weight <= 0.0:
        return weighted_values[0][0]

    used_weight = 0.0
    weighted_sum = 0.0
    for value, weight in weighted_values:
        remaining = target_weight - used_weight
        if remaining <= 0.0:
            break
        take = min(weight, remaining)
        weighted_sum += value * take
        used_weight += take
    return weighted_sum / used_weight if used_weight > 0.0 else 0.0


def _visual_value(value: float, *, min_value: float, max_value: float) -> float:
    if max_value <= min_value:
        return 0.5
    return min(1.0, max(0.0, (value - min_value) / (max_value - min_value)))


def _response_region(index: float) -> str:
    if index < 0.35:
        return "light_limited"
    if index > 0.85:
        return "near_saturation"
    return "responsive"


@dataclass(frozen=True)
class PhotosynthesisResponseParameters:
    """Parameters for a simple absorbed-light response curve.

    The model uses absorbed PAR photon flux density as the input driver and a
    non-rectangular hyperbola to estimate photosynthetic response potential.
    """

    initial_quantum_yield_mol_co2_per_mol_photons: float = 0.055
    max_gross_assimilation_umol_co2_m2_s: float = 24.0
    dark_respiration_umol_co2_m2_s: float = 1.2
    curvature_factor: float = 0.70
    photoperiod_hours: float = 16.0

    def __post_init__(self) -> None:
        _finite_positive(
            "initial_quantum_yield_mol_co2_per_mol_photons",
            self.initial_quantum_yield_mol_co2_per_mol_photons,
        )
        _finite_positive(
            "max_gross_assimilation_umol_co2_m2_s",
            self.max_gross_assimilation_umol_co2_m2_s,
        )
        _finite_non_negative(
            "dark_respiration_umol_co2_m2_s",
            self.dark_respiration_umol_co2_m2_s,
        )
        _finite_fraction_open("curvature_factor", self.curvature_factor)
        _finite_positive("photoperiod_hours", self.photoperiod_hours)

    def to_payload(self) -> dict[str, float]:
        return asdict(self)


def default_photosynthesis_response_parameters() -> PhotosynthesisResponseParameters:
    return PhotosynthesisResponseParameters()


def photosynthetic_gross_rate_umol_co2_m2_s(
    absorbed_par_umol_m2_s: float,
    parameters: PhotosynthesisResponseParameters,
) -> float:
    """Compute gross photosynthetic response potential from absorbed PAR.

    This is a non-rectangular hyperbola response curve. It is useful for
    relative light-response comparisons, not cultivar-specific crop prediction.
    """

    absorbed_par = _finite_non_negative("absorbed_par_umol_m2_s", absorbed_par_umol_m2_s)
    alpha_i = parameters.initial_quantum_yield_mol_co2_per_mol_photons * absorbed_par
    amax = parameters.max_gross_assimilation_umol_co2_m2_s
    theta = parameters.curvature_factor
    discriminant = (alpha_i + amax) ** 2 - (4.0 * theta * alpha_i * amax)
    if discriminant < 0.0 and discriminant > -1e-9:
        discriminant = 0.0
    if discriminant < 0.0:
        raise ValueError("Photosynthetic response discriminant became negative.")
    return ((alpha_i + amax) - math.sqrt(discriminant)) / (2.0 * theta)


def photosynthetic_net_rate_umol_co2_m2_s(
    absorbed_par_umol_m2_s: float,
    parameters: PhotosynthesisResponseParameters,
) -> float:
    gross = photosynthetic_gross_rate_umol_co2_m2_s(absorbed_par_umol_m2_s, parameters)
    return gross - parameters.dark_respiration_umol_co2_m2_s


def _max_net_rate_umol_co2_m2_s(parameters: PhotosynthesisResponseParameters) -> float:
    return max(
        0.0,
        parameters.max_gross_assimilation_umol_co2_m2_s
        - parameters.dark_respiration_umol_co2_m2_s,
    )


def _local_response_values(
    absorbed_par_density: float,
    parameters: PhotosynthesisResponseParameters,
) -> dict[str, float]:
    gross_rate = photosynthetic_gross_rate_umol_co2_m2_s(absorbed_par_density, parameters)
    net_rate = photosynthetic_net_rate_umol_co2_m2_s(absorbed_par_density, parameters)
    clipped_net_rate = max(0.0, net_rate)
    max_net_rate = _max_net_rate_umol_co2_m2_s(parameters)
    response_index = _ratio(clipped_net_rate, max_net_rate) if max_net_rate > 0.0 else 0.0
    response_index = min(1.0, max(0.0, float(response_index or 0.0)))
    return {
        "gross_rate": gross_rate,
        "net_rate": net_rate,
        "clipped_net_rate": clipped_net_rate,
        "response_index": response_index,
    }


def _compensation_absorbed_par_density(
    parameters: PhotosynthesisResponseParameters,
) -> float | None:
    if parameters.dark_respiration_umol_co2_m2_s <= 0.0:
        return 0.0
    if _max_net_rate_umol_co2_m2_s(parameters) <= 0.0:
        return None

    low = 0.0
    high = 1.0
    while photosynthetic_net_rate_umol_co2_m2_s(high, parameters) < 0.0:
        high *= 2.0
        if high > 1_000_000.0:
            return None

    for _ in range(80):
        mid = (low + high) / 2.0
        if photosynthetic_net_rate_umol_co2_m2_s(mid, parameters) < 0.0:
            low = mid
        else:
            high = mid
    return high


def _spectral_receiver_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if payload.get("schema") != PLANT_SPECTRAL_RESPONSE_SCHEMA:
        raise ValueError("Unsupported plant spectral-response schema.")

    rows = payload.get("surface_summaries")
    if isinstance(rows, list) and rows:
        return rows

    rows = payload.get("leaf_summaries")
    if isinstance(rows, list) and rows:
        return rows

    raise ValueError(
        "Plant spectral-response payload must include surface_summaries or leaf_summaries."
    )


def _surface_photosynthesis_summary(
    row: Mapping[str, Any],
    parameters: PhotosynthesisResponseParameters,
) -> dict[str, Any]:
    leaf_id = row.get("leaf_id")
    plant_id = row.get("plant_id")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise ValueError("Each spectral leaf summary must include leaf_id.")
    if not isinstance(plant_id, str) or not plant_id:
        raise ValueError("Each spectral leaf summary must include plant_id.")

    raw_surface_id = row.get("surface_id")
    surface_id = (
        raw_surface_id
        if isinstance(raw_surface_id, str) and raw_surface_id
        else f"{leaf_id}__aggregate_receiver"
    )
    receiver_scope = "surface" if surface_id == raw_surface_id else "leaf_summary"

    area = _finite_positive(f"area_m2[{surface_id}]", row.get("area_m2"))
    absorbed_par_density = _finite_non_negative(
        f"absorbed_par_photon_flux_density_umol_m2_s[{surface_id}]",
        row.get("absorbed_par_photon_flux_density_umol_m2_s"),
    )
    raw_absorbed_par_flux = row.get("absorbed_par_photon_flux_umol_s")
    absorbed_par_flux = (
        absorbed_par_density * area
        if raw_absorbed_par_flux is None
        else _finite_non_negative(
            f"absorbed_par_photon_flux_umol_s[{surface_id}]",
            raw_absorbed_par_flux,
        )
    )

    values = _local_response_values(absorbed_par_density, parameters)
    gross_rate = values["gross_rate"]
    net_rate = values["net_rate"]
    clipped_net_rate = values["clipped_net_rate"]
    response_index = values["response_index"]
    daily_seconds = parameters.photoperiod_hours * 3600.0

    return {
        "surface_id": surface_id,
        "receiver_scope": receiver_scope,
        "leaf_id": leaf_id,
        "plant_id": plant_id,
        "leaf_index": row.get("leaf_index"),
        "face_index": row.get("face_index"),
        "area_m2": area,
        "absorbed_par_photon_flux_umol_s": absorbed_par_flux,
        "absorbed_par_photon_flux_density_umol_m2_s": absorbed_par_density,
        "gross_photosynthetic_potential_umol_co2_m2_s": gross_rate,
        "net_photosynthetic_potential_umol_co2_m2_s": net_rate,
        "clipped_net_photosynthetic_potential_umol_co2_m2_s": clipped_net_rate,
        "gross_photosynthetic_potential_umol_co2_s": gross_rate * area,
        "net_photosynthetic_potential_umol_co2_s": net_rate * area,
        "clipped_net_photosynthetic_potential_umol_co2_s": clipped_net_rate * area,
        "daily_clipped_net_photosynthetic_potential_mol_co2": (
            clipped_net_rate * area * daily_seconds / 1_000_000.0
        ),
        "photosynthetic_response_index_0_1": response_index,
        "area_weighted_response_fraction_0_1": response_index,
        "photosynthetic_region": _response_region(response_index),
        "absorbed_blue_to_par_fraction": row.get("absorbed_blue_to_par_fraction"),
        "absorbed_red_to_far_red_ratio": row.get("absorbed_red_to_far_red_ratio"),
    }


def _aggregate_response_rows(
    surface_rows: list[dict[str, Any]],
    *,
    key_name: str,
) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}

    for row in surface_rows:
        key = row.get(key_name)
        if not isinstance(key, str) or not key:
            raise ValueError(f"Photosynthesis surface row missing {key_name}.")
        area = float(row["area_m2"])
        item = grouped.setdefault(
            key,
            {
                key_name: key,
                "plant_id": row.get("plant_id"),
                "leaf_id": row.get("leaf_id") if key_name == "leaf_id" else None,
                "leaf_ids": set(),
                "area_m2": 0.0,
                "surface_count": 0,
                "absorbed_par_photon_flux_umol_s": 0.0,
                "gross_photosynthetic_potential_umol_co2_s": 0.0,
                "net_photosynthetic_potential_umol_co2_s": 0.0,
                "clipped_net_photosynthetic_potential_umol_co2_s": 0.0,
                "daily_clipped_net_photosynthetic_potential_mol_co2": 0.0,
                "response_area_sum": 0.0,
            },
        )
        item["surface_count"] += 1
        item["area_m2"] += area
        leaf_id = row.get("leaf_id")
        if isinstance(leaf_id, str) and leaf_id:
            item["leaf_ids"].add(leaf_id)
        item["absorbed_par_photon_flux_umol_s"] += float(row["absorbed_par_photon_flux_umol_s"])
        item["gross_photosynthetic_potential_umol_co2_s"] += float(
            row["gross_photosynthetic_potential_umol_co2_s"]
        )
        item["net_photosynthetic_potential_umol_co2_s"] += float(
            row["net_photosynthetic_potential_umol_co2_s"]
        )
        item["clipped_net_photosynthetic_potential_umol_co2_s"] += float(
            row["clipped_net_photosynthetic_potential_umol_co2_s"]
        )
        item["daily_clipped_net_photosynthetic_potential_mol_co2"] += float(
            row["daily_clipped_net_photosynthetic_potential_mol_co2"]
        )
        item["response_area_sum"] += float(row["photosynthetic_response_index_0_1"]) * area

    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        area = float(item["area_m2"])
        response_index = float(item.pop("response_area_sum")) / area if area > 0.0 else 0.0
        leaf_ids = item.pop("leaf_ids")
        if key_name == "plant_id":
            item["leaf_count"] = len(leaf_ids)
        item["absorbed_par_photon_flux_density_umol_m2_s"] = (
            float(item["absorbed_par_photon_flux_umol_s"]) / area if area > 0.0 else 0.0
        )
        item["gross_photosynthetic_potential_umol_co2_m2_s"] = (
            float(item["gross_photosynthetic_potential_umol_co2_s"]) / area
            if area > 0.0
            else 0.0
        )
        item["net_photosynthetic_potential_umol_co2_m2_s"] = (
            float(item["net_photosynthetic_potential_umol_co2_s"]) / area
            if area > 0.0
            else 0.0
        )
        item["clipped_net_photosynthetic_potential_umol_co2_m2_s"] = (
            float(item["clipped_net_photosynthetic_potential_umol_co2_s"]) / area
            if area > 0.0
            else 0.0
        )
        item["photosynthetic_response_index_0_1"] = response_index
        item["area_weighted_response_fraction_0_1"] = response_index
        item["normalized_response_fraction_0_1"] = response_index
        item["photosynthetic_region"] = _response_region(response_index)
        rows.append(item)

    return sorted(rows, key=lambda item: str(item[key_name]))


def _response_system_metrics(
    surface_rows: list[dict[str, Any]],
    plant_rows: list[dict[str, Any]],
    parameters: PhotosynthesisResponseParameters,
) -> dict[str, Any]:
    total_area = sum(float(row["area_m2"]) for row in surface_rows)
    total_absorbed_par = sum(
        float(row["absorbed_par_photon_flux_umol_s"])
        for row in surface_rows
    )
    response_weighted_values = [
        (float(row["photosynthetic_response_index_0_1"]), float(row["area_m2"]))
        for row in surface_rows
    ]
    area_weighted_mean_response = _weighted_mean(response_weighted_values)
    mean_absorbed_par_density = total_absorbed_par / total_area if total_area > 0.0 else 0.0
    mean_light_response = _local_response_values(
        mean_absorbed_par_density,
        parameters,
    )["response_index"]
    retention = (
        min(1.0, area_weighted_mean_response / mean_light_response)
        if mean_light_response > 0.0
        else None
    )
    plant_response_values = [
        float(row["normalized_response_fraction_0_1"])
        for row in plant_rows
    ]
    compensation_reference = _compensation_absorbed_par_density(parameters)
    below_compensation_area = (
        sum(
            float(row["area_m2"])
            for row in surface_rows
            if float(row["absorbed_par_photon_flux_density_umol_m2_s"])
            < compensation_reference
        )
        if compensation_reference is not None
        else None
    )
    near_saturation_area = sum(
        float(row["area_m2"])
        for row in surface_rows
        if row["photosynthetic_region"] == "near_saturation"
    )

    return {
        "absorbed_par_area_weighted_mean_umol_m2_s": mean_absorbed_par_density,
        "area_weighted_mean_local_response_0_1": area_weighted_mean_response,
        "uniform_mean_light_response_0_1": mean_light_response,
        "nonuniformity_response_retention_0_1": retention,
        "equal_plant_mean_normalized_response_0_1": (
            sum(plant_response_values) / len(plant_response_values)
            if plant_response_values
            else 0.0
        ),
        "plant_to_plant_normalized_response_cv": _coefficient_of_variation(
            plant_response_values
        ),
        "local_response_p10_0_1": _weighted_percentile(response_weighted_values, 0.10),
        "bottom_decile_area_weighted_response_0_1": _weighted_lower_tail_mean(
            response_weighted_values,
            0.10,
        ),
        "compensation_reference_absorbed_par_umol_m2_s": compensation_reference,
        "area_fraction_below_compensation_reference": (
            below_compensation_area / total_area
            if below_compensation_area is not None and total_area > 0.0
            else None
        ),
        "area_fraction_near_saturation_range": (
            near_saturation_area / total_area if total_area > 0.0 else 0.0
        ),
        "area_fraction_above_profile_valid_range": None,
    }


def _visualization_payload(
    leaf_rows: list[dict[str, Any]],
    surface_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    key = "photosynthetic_response_index_0_1"
    values = [float(row[key]) for row in leaf_rows]
    min_value = min(values) if values else 0.0
    max_value = max(values) if values else 0.0
    surface_values = [float(row[key]) for row in surface_rows]
    surface_min_value = min(surface_values) if surface_values else 0.0
    surface_max_value = max(surface_values) if surface_values else 0.0

    return {
        "color_metric": key,
        "normalization": "linear_0_1",
        "leaf_scale": {
            "min": min_value,
            "max": max_value,
        },
        "surface_scale": {
            "min": surface_min_value,
            "max": surface_max_value,
        },
        "leaf_values": [
            {
                "leaf_id": row["leaf_id"],
                "plant_id": row["plant_id"],
                "photosynthetic_region": row["photosynthetic_region"],
                key: row[key],
                "absorbed_par_photon_flux_density_umol_m2_s": row[
                    "absorbed_par_photon_flux_density_umol_m2_s"
                ],
                "visual_intensity_0_1": _visual_value(
                    float(row[key]),
                    min_value=min_value,
                    max_value=max_value,
                ),
            }
            for row in leaf_rows
        ],
        "surface_values": [
            {
                "surface_id": row["surface_id"],
                "leaf_id": row["leaf_id"],
                "plant_id": row["plant_id"],
                "photosynthetic_region": row["photosynthetic_region"],
                key: row[key],
                "absorbed_par_photon_flux_density_umol_m2_s": row[
                    "absorbed_par_photon_flux_density_umol_m2_s"
                ],
                "visual_intensity_0_1": _visual_value(
                    float(row[key]),
                    min_value=surface_min_value,
                    max_value=surface_max_value,
                ),
            }
            for row in surface_rows
        ],
    }


def _contract_payload(method: str) -> dict[str, Any]:
    return {
        "schema": PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
        "schema_version": PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA_VERSION,
        "legacy_schema": LEGACY_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
        "method": method,
        "method_version": 2,
        "calibration_status": PHOTOSYNTHESIS_CALIBRATION_STATUS,
        "default_parameter_status": PHOTOSYNTHESIS_DEFAULT_PARAMETER_STATUS,
        "target_model": {
            "id": PHOTOSYNTHESIS_TARGET_MODEL_ID,
            "archetype": PHOTOSYNTHESIS_TARGET_MODEL_ARCHETYPE,
            "cultivar_specific": False,
        },
        "input_basis": {
            "source_artifact": "runtime_state/plant_spectral_response.json",
            "driver": "surface absorbed_par_photon_flux_density_umol_m2_s",
            "basis": "surface_absorbed_PAR_from_leaf_spectral_response",
        },
        "evidence_quality_tier": "unvalidated_default",
        "uncertainty_note": (
            "Response-potential values are deterministic lighting-analysis "
            "outputs from unvalidated default parameters."
        ),
        "non_prediction_framing": (
            "This artifact is a lighting-analysis response-potential scaffold, "
            "not a biological prediction."
        ),
    }


def build_plant_photosynthesis_response_payload(
    spectral_response_payload: Mapping[str, Any],
    parameters: PhotosynthesisResponseParameters | None = None,
    *,
    method: str = PLANT_PHOTOSYNTHESIS_RESPONSE_METHOD,
) -> dict[str, Any]:
    params = parameters or default_photosynthesis_response_parameters()
    source_receiver_rows = _spectral_receiver_rows(spectral_response_payload)
    surface_rows = [
        _surface_photosynthesis_summary(row, params)
        for row in source_receiver_rows
    ]
    leaf_rows = _aggregate_response_rows(
        surface_rows,
        key_name="leaf_id",
    )
    plant_rows = _aggregate_response_rows(
        surface_rows,
        key_name="plant_id",
    )
    system_response_metrics = _response_system_metrics(surface_rows, plant_rows, params)

    plant_response_values = [
        float(row["normalized_response_fraction_0_1"])
        for row in plant_rows
    ]
    plant_net_values = [
        float(row["clipped_net_photosynthetic_potential_umol_co2_s"])
        for row in plant_rows
    ]
    leaf_response_values = [
        float(row["photosynthetic_response_index_0_1"])
        for row in leaf_rows
    ]

    return {
        "schema": PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
        "schema_version": PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA_VERSION,
        "status": "computed",
        "method": method,
        "method_version": 2,
        "contract": _contract_payload(method),
        "calibration_status": PHOTOSYNTHESIS_CALIBRATION_STATUS,
        "default_parameter_status": PHOTOSYNTHESIS_DEFAULT_PARAMETER_STATUS,
        "target_model": {
            "id": PHOTOSYNTHESIS_TARGET_MODEL_ID,
            "archetype": PHOTOSYNTHESIS_TARGET_MODEL_ARCHETYPE,
            "cultivar_specific": False,
        },
        "input_basis": {
            "source_artifact": "runtime_state/plant_spectral_response.json",
            "driver": "surface absorbed_par_photon_flux_density_umol_m2_s",
            "basis": "surface_absorbed_PAR_from_leaf_spectral_response",
        },
        "evidence_quality_tier": "unvalidated_default",
        "uncertainty_notes": [
            (
                "Response-potential values are deterministic lighting-analysis "
                "outputs from unvalidated default parameters."
            ),
            (
                "The current model omits environmental and physiological "
                "calibration factors."
            ),
        ],
        "non_prediction_framing": (
            "This artifact is a lighting-analysis response-potential scaffold, "
            "not a biological prediction."
        ),
        "source_spectral_response_schema": spectral_response_payload.get("schema"),
        "source_spectral_response_method": spectral_response_payload.get("method"),
        "source_spectral_distribution": spectral_response_payload.get("spectral_distribution"),
        "source_artifact": "runtime_state/plant_spectral_response.json",
        "parameters": params.to_payload(),
        "units": {
            "photon_flux": "umol/s",
            "photon_flux_density": "umol/m2/s",
            "photosynthetic_rate_density": "umol_CO2/m2/s",
            "photosynthetic_rate": "umol_CO2/s",
            "daily_potential": "mol_CO2/photoperiod",
            "area": "m2",
            "response_fraction": "0..1",
            "area_fraction": "0..1",
        },
        "plant_count": spectral_response_payload.get("plant_count"),
        "leaf_count": spectral_response_payload.get("leaf_count"),
        "surface_count": spectral_response_payload.get("surface_count"),
        "total_absorbed_par_photon_flux_umol_s": sum(
            float(row["absorbed_par_photon_flux_umol_s"])
            for row in surface_rows
        ),
        "total_gross_photosynthetic_potential_umol_co2_s": sum(
            float(row["gross_photosynthetic_potential_umol_co2_s"])
            for row in surface_rows
        ),
        "total_net_photosynthetic_potential_umol_co2_s": sum(
            float(row["net_photosynthetic_potential_umol_co2_s"])
            for row in surface_rows
        ),
        "total_clipped_net_photosynthetic_potential_umol_co2_s": sum(
            float(row["clipped_net_photosynthetic_potential_umol_co2_s"])
            for row in surface_rows
        ),
        "daily_clipped_net_photosynthetic_potential_mol_co2": sum(
            float(row["daily_clipped_net_photosynthetic_potential_mol_co2"])
            for row in surface_rows
        ),
        **system_response_metrics,
        "mean_leaf_photosynthetic_response_index_0_1": (
            sum(leaf_response_values) / len(leaf_response_values)
            if leaf_response_values
            else 0.0
        ),
        "plant_to_plant_photosynthetic_response_cv": _coefficient_of_variation(
            plant_response_values
        ),
        "plant_to_plant_total_clipped_net_potential_cv": _coefficient_of_variation(
            plant_net_values
        ),
        "light_limited_leaf_count": sum(
            1 for row in leaf_rows if row["photosynthetic_region"] == "light_limited"
        ),
        "near_saturation_leaf_count": sum(
            1 for row in leaf_rows if row["photosynthetic_region"] == "near_saturation"
        ),
        "plant_summaries": plant_rows,
        "leaf_summaries": leaf_rows,
        "surface_summaries": surface_rows,
        "visualization": _visualization_payload(leaf_rows, surface_rows),
        "outputs_do_not_predict": list(NO_CROP_OUTPUT_TERMS),
        "warnings": [
            "Photosynthetic response potential is computed from absorbed PAR using explicit model parameters.",
            "This artifact is intended for relative lighting-analysis comparisons, not crop-output prediction.",
        ],
        "limitations": [
            "The model does not include stomatal conductance, CO2 concentration, temperature, water stress, nutrient stress, or cultivar-specific calibration.",
            "The model does not predict yield, biomass, harvest weight, growth rate, or crop output.",
        ],
    }


def write_plant_photosynthesis_response_artifact(
    target_dir: str | Path,
    spectral_response_payload: Mapping[str, Any],
    parameters: PhotosynthesisResponseParameters | None = None,
    *,
    return_payload: bool = False,
) -> Path | tuple[Path, dict[str, Any]]:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_plant_photosynthesis_response_payload(
        spectral_response_payload,
        parameters,
    )
    path = output_dir / PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME
    path.write_text(
        json.dumps(compact_plant_photosynthesis_response_payload(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return (path, payload) if return_payload else path


def compact_plant_photosynthesis_response_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(payload).items()
        if key not in {
            "plant_summaries",
            "leaf_summaries",
            "surface_summaries",
            "visualization",
        }
    }
