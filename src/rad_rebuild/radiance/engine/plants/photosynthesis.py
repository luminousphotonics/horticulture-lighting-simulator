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
from typing import Any, Mapping

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


def _spectral_leaf_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if payload.get("schema") != PLANT_SPECTRAL_RESPONSE_SCHEMA:
        raise ValueError("Unsupported plant spectral-response schema.")
    rows = payload.get("leaf_summaries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plant spectral-response payload must include leaf_summaries.")
    return rows


def _leaf_photosynthesis_summary(
    row: Mapping[str, Any],
    parameters: PhotosynthesisResponseParameters,
) -> dict[str, Any]:
    leaf_id = row.get("leaf_id")
    plant_id = row.get("plant_id")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise ValueError("Each spectral leaf summary must include leaf_id.")
    if not isinstance(plant_id, str) or not plant_id:
        raise ValueError("Each spectral leaf summary must include plant_id.")

    area = _finite_positive(f"area_m2[{leaf_id}]", row.get("area_m2"))
    absorbed_par_density = _finite_non_negative(
        f"absorbed_par_photon_flux_density_umol_m2_s[{leaf_id}]",
        row.get("absorbed_par_photon_flux_density_umol_m2_s"),
    )
    absorbed_par_flux = _finite_non_negative(
        f"absorbed_par_photon_flux_umol_s[{leaf_id}]",
        row.get("absorbed_par_photon_flux_umol_s"),
    )

    gross_rate = photosynthetic_gross_rate_umol_co2_m2_s(absorbed_par_density, parameters)
    net_rate = photosynthetic_net_rate_umol_co2_m2_s(absorbed_par_density, parameters)
    clipped_net_rate = max(0.0, net_rate)
    max_net_rate = max(
        0.0,
        parameters.max_gross_assimilation_umol_co2_m2_s
        - parameters.dark_respiration_umol_co2_m2_s,
    )
    response_index = _ratio(clipped_net_rate, max_net_rate) if max_net_rate > 0.0 else 0.0
    response_index = min(1.0, max(0.0, float(response_index or 0.0)))
    daily_seconds = parameters.photoperiod_hours * 3600.0

    return {
        "leaf_id": leaf_id,
        "plant_id": plant_id,
        "leaf_index": row.get("leaf_index"),
        "surface_count": row.get("surface_count"),
        "area_m2": area,
        "absorbed_par_photon_flux_umol_s": absorbed_par_flux,
        "absorbed_par_photon_flux_density_umol_m2_s": absorbed_par_density,
        "gross_photosynthetic_potential_umol_co2_m2_s": gross_rate,
        "net_photosynthetic_potential_umol_co2_m2_s": net_rate,
        "clipped_net_photosynthetic_potential_umol_co2_m2_s": clipped_net_rate,
        "gross_photosynthetic_potential_umol_co2_s": gross_rate * area,
        "clipped_net_photosynthetic_potential_umol_co2_s": clipped_net_rate * area,
        "daily_clipped_net_photosynthetic_potential_mol_co2": (
            clipped_net_rate * area * daily_seconds / 1_000_000.0
        ),
        "photosynthetic_response_index_0_1": response_index,
        "photosynthetic_region": _response_region(response_index),
        "absorbed_blue_to_par_fraction": row.get("absorbed_blue_to_par_fraction"),
        "absorbed_red_to_far_red_ratio": row.get("absorbed_red_to_far_red_ratio"),
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
                "absorbed_par_photon_flux_umol_s": 0.0,
                "gross_photosynthetic_potential_umol_co2_s": 0.0,
                "clipped_net_photosynthetic_potential_umol_co2_s": 0.0,
                "daily_clipped_net_photosynthetic_potential_mol_co2": 0.0,
                "response_index_sum": 0.0,
            },
        )
        item["leaf_count"] += 1
        item["area_m2"] += float(row["area_m2"])
        item["absorbed_par_photon_flux_umol_s"] += float(row["absorbed_par_photon_flux_umol_s"])
        item["gross_photosynthetic_potential_umol_co2_s"] += float(
            row["gross_photosynthetic_potential_umol_co2_s"]
        )
        item["clipped_net_photosynthetic_potential_umol_co2_s"] += float(
            row["clipped_net_photosynthetic_potential_umol_co2_s"]
        )
        item["daily_clipped_net_photosynthetic_potential_mol_co2"] += float(
            row["daily_clipped_net_photosynthetic_potential_mol_co2"]
        )
        item["response_index_sum"] += float(row["photosynthetic_response_index_0_1"])

    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        leaf_count = int(item["leaf_count"])
        area = float(item["area_m2"])
        response_index = float(item.pop("response_index_sum")) / leaf_count if leaf_count else 0.0
        item["absorbed_par_photon_flux_density_umol_m2_s"] = (
            float(item["absorbed_par_photon_flux_umol_s"]) / area if area > 0.0 else 0.0
        )
        item["photosynthetic_response_index_0_1"] = response_index
        item["photosynthetic_region"] = _response_region(response_index)
        rows.append(item)

    return sorted(rows, key=lambda item: item["plant_id"])


def _visualization_payload(leaf_rows: list[dict[str, Any]]) -> dict[str, Any]:
    key = "photosynthetic_response_index_0_1"
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
            "driver": "absorbed_par_photon_flux_density_umol_m2_s",
            "basis": "absorbed_PAR_from_leaf_spectral_response",
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
    source_leaf_rows = _spectral_leaf_rows(spectral_response_payload)
    leaf_rows = [
        _leaf_photosynthesis_summary(row, params)
        for row in source_leaf_rows
    ]
    plant_rows = _aggregate_plant_rows(leaf_rows)

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
            "driver": "absorbed_par_photon_flux_density_umol_m2_s",
            "basis": "absorbed_PAR_from_leaf_spectral_response",
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
        },
        "plant_count": spectral_response_payload.get("plant_count"),
        "leaf_count": spectral_response_payload.get("leaf_count"),
        "surface_count": spectral_response_payload.get("surface_count"),
        "total_absorbed_par_photon_flux_umol_s": sum(
            float(row["absorbed_par_photon_flux_umol_s"])
            for row in leaf_rows
        ),
        "total_gross_photosynthetic_potential_umol_co2_s": sum(
            float(row["gross_photosynthetic_potential_umol_co2_s"])
            for row in leaf_rows
        ),
        "total_clipped_net_photosynthetic_potential_umol_co2_s": sum(
            float(row["clipped_net_photosynthetic_potential_umol_co2_s"])
            for row in leaf_rows
        ),
        "daily_clipped_net_photosynthetic_potential_mol_co2": sum(
            float(row["daily_clipped_net_photosynthetic_potential_mol_co2"])
            for row in leaf_rows
        ),
        "mean_leaf_photosynthetic_response_index_0_1": (
            sum(leaf_response_values) / len(leaf_response_values)
            if leaf_response_values
            else 0.0
        ),
        "plant_to_plant_photosynthetic_response_cv": _coefficient_of_variation(plant_net_values),
        "light_limited_leaf_count": sum(
            1 for row in leaf_rows if row["photosynthetic_region"] == "light_limited"
        ),
        "near_saturation_leaf_count": sum(
            1 for row in leaf_rows if row["photosynthetic_region"] == "near_saturation"
        ),
        "plant_summaries": plant_rows,
        "leaf_summaries": leaf_rows,
        "visualization": _visualization_payload(leaf_rows),
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
) -> Path:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_plant_photosynthesis_response_payload(
        spectral_response_payload,
        parameters,
    )
    path = output_dir / PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
