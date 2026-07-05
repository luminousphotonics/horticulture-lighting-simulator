"""Photoreceptor exposure inputs for FSPM plant artifacts.

This module consumes band-level spectral exposure from
`plant_spectral_response.json` and reports photoreceptor-relevant lighting
inputs. It does not infer plant form or biological outcomes.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Literal, Mapping, overload

PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA = "rad_rebuild.fspm.plant_photoreceptor_exposure.v1"
PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA_VERSION = 1
PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME = "plant_photoreceptor_exposure.json"
PLANT_PHOTORECEPTOR_EXPOSURE_METHOD = "spectral_band_exposure_inputs_v1"
PLANT_SPECTRAL_RESPONSE_SCHEMA = "rad_rebuild.fspm.plant_spectral_response.v1"

PAR_BAND_IDS = frozenset({"blue", "green", "orange", "red"})
EXPOSURE_BAND_IDS = ("blue", "green", "orange", "red", "far_red")


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


def _band_flux(row: Mapping[str, Any], band_id: str, flux_name: str) -> float:
    band_totals = row.get("band_totals")
    if not isinstance(band_totals, Mapping):
        return 0.0
    band = band_totals.get(band_id)
    if not isinstance(band, Mapping):
        return 0.0
    return _finite_non_negative(f"{band_id}.{flux_name}", band.get(flux_name, 0.0))


def _spectral_leaf_rows(payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if payload.get("schema") != PLANT_SPECTRAL_RESPONSE_SCHEMA:
        raise ValueError("Unsupported plant spectral-response schema.")
    rows = payload.get("leaf_summaries")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Plant spectral-response payload must include leaf_summaries.")
    return rows


def _leaf_exposure_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    leaf_id = row.get("leaf_id")
    plant_id = row.get("plant_id")
    if not isinstance(leaf_id, str) or not leaf_id:
        raise ValueError("Each spectral leaf summary must include leaf_id.")
    if not isinstance(plant_id, str) or not plant_id:
        raise ValueError("Each spectral leaf summary must include plant_id.")

    area = _finite_positive(f"area_m2[{leaf_id}]", row.get("area_m2"))
    absorbed_flux = {
        band_id: _band_flux(row, band_id, "absorbed_photon_flux_umol_s")
        for band_id in EXPOSURE_BAND_IDS
    }
    transmitted_flux = {
        band_id: _band_flux(row, band_id, "transmitted_photon_flux_umol_s")
        for band_id in EXPOSURE_BAND_IDS
    }
    incident_flux = {
        band_id: _band_flux(row, band_id, "incident_photon_flux_umol_s")
        for band_id in EXPOSURE_BAND_IDS
    }
    absorbed_par_flux = sum(absorbed_flux[band_id] for band_id in PAR_BAND_IDS)
    absorbed_total_flux = _finite_non_negative(
        f"absorbed_photon_flux_umol_s[{leaf_id}]",
        row.get("absorbed_photon_flux_umol_s", sum(absorbed_flux.values())),
    )
    far_red_transmission_proxy = _ratio(
        transmitted_flux["far_red"],
        incident_flux["far_red"],
    )

    return {
        "leaf_id": leaf_id,
        "plant_id": plant_id,
        "leaf_index": row.get("leaf_index"),
        "surface_count": row.get("surface_count"),
        "area_m2": area,
        "absorbed_blue_pfd_umol_m2_s": absorbed_flux["blue"] / area,
        "absorbed_green_pfd_umol_m2_s": absorbed_flux["green"] / area,
        "absorbed_orange_pfd_umol_m2_s": absorbed_flux["orange"] / area,
        "absorbed_red_pfd_umol_m2_s": absorbed_flux["red"] / area,
        "absorbed_far_red_pfd_umol_m2_s": absorbed_flux["far_red"] / area,
        "absorbed_par_pfd_umol_m2_s": absorbed_par_flux / area,
        "absorbed_blue_fraction_of_par": _ratio(absorbed_flux["blue"], absorbed_par_flux),
        "absorbed_far_red_fraction_of_total": _ratio(
            absorbed_flux["far_red"],
            absorbed_total_flux,
        ),
        "absorbed_red_to_far_red_ratio_diagnostic": _ratio(
            absorbed_flux["red"],
            absorbed_flux["far_red"],
        ),
        "absorbed_red_to_far_red_ratio_definition": (
            "absorbed red PFD divided by absorbed far-red PFD; diagnostic only"
        ),
        "single_leaf_transmitted_far_red_pfd_umol_m2_s": (
            transmitted_flux["far_red"] / area
        ),
        "single_leaf_far_red_transmittance_proxy": far_red_transmission_proxy,
    }


def _aggregate_plant_rows(leaf_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    density_fields = (
        "absorbed_blue_pfd_umol_m2_s",
        "absorbed_green_pfd_umol_m2_s",
        "absorbed_orange_pfd_umol_m2_s",
        "absorbed_red_pfd_umol_m2_s",
        "absorbed_far_red_pfd_umol_m2_s",
        "absorbed_par_pfd_umol_m2_s",
        "single_leaf_transmitted_far_red_pfd_umol_m2_s",
    )
    fraction_fields = (
        "absorbed_blue_fraction_of_par",
        "absorbed_far_red_fraction_of_total",
        "absorbed_red_to_far_red_ratio_diagnostic",
        "single_leaf_far_red_transmittance_proxy",
    )

    for row in leaf_rows:
        plant_id = row["plant_id"]
        item = grouped.setdefault(
            plant_id,
            {
                "plant_id": plant_id,
                "leaf_count": 0,
                "area_m2": 0.0,
                **{f"{field}_area_sum": 0.0 for field in density_fields},
                **{f"{field}_values": [] for field in fraction_fields},
            },
        )
        area = float(row["area_m2"])
        item["leaf_count"] += 1
        item["area_m2"] += area
        for field in density_fields:
            item[f"{field}_area_sum"] += float(row[field]) * area
        for field in fraction_fields:
            value = row[field]
            if isinstance(value, int | float) and math.isfinite(float(value)):
                item[f"{field}_values"].append(float(value))

    rows: list[dict[str, Any]] = []
    for item in grouped.values():
        area = float(item["area_m2"])
        out = {
            "plant_id": item["plant_id"],
            "leaf_count": item["leaf_count"],
            "area_m2": area,
        }
        for field in density_fields:
            out[field] = (
                float(item[f"{field}_area_sum"]) / area if area > 0.0 else 0.0
            )
        for field in fraction_fields:
            values = item[f"{field}_values"]
            out[field] = sum(values) / len(values) if values else None
        rows.append(out)

    return sorted(rows, key=lambda item: item["plant_id"])


def _mean_field(rows: list[dict[str, Any]], field_name: str) -> float | None:
    values = [
        float(row[field_name])
        for row in rows
        if isinstance(row.get(field_name), int | float)
        and math.isfinite(float(row[field_name]))
    ]
    return sum(values) / len(values) if values else None


def _exposure_mean_fields(plant_rows: list[dict[str, Any]]) -> dict[str, float | None]:
    return {
        "mean_absorbed_blue_pfd_umol_m2_s": _mean_field(
            plant_rows,
            "absorbed_blue_pfd_umol_m2_s",
        ),
        "mean_absorbed_green_pfd_umol_m2_s": _mean_field(
            plant_rows,
            "absorbed_green_pfd_umol_m2_s",
        ),
        "mean_absorbed_orange_pfd_umol_m2_s": _mean_field(
            plant_rows,
            "absorbed_orange_pfd_umol_m2_s",
        ),
        "mean_absorbed_red_pfd_umol_m2_s": _mean_field(
            plant_rows,
            "absorbed_red_pfd_umol_m2_s",
        ),
        "mean_absorbed_far_red_pfd_umol_m2_s": _mean_field(
            plant_rows,
            "absorbed_far_red_pfd_umol_m2_s",
        ),
        "mean_absorbed_blue_fraction_of_par": _mean_field(
            plant_rows,
            "absorbed_blue_fraction_of_par",
        ),
        "mean_absorbed_red_to_far_red_ratio_diagnostic": _mean_field(
            plant_rows,
            "absorbed_red_to_far_red_ratio_diagnostic",
        ),
    }


def _exposure_consistency(plant_rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "plant_to_plant_absorbed_blue_pfd_cv": _coefficient_of_variation(
            [float(row["absorbed_blue_pfd_umol_m2_s"]) for row in plant_rows]
        ),
        "plant_to_plant_absorbed_red_pfd_cv": _coefficient_of_variation(
            [float(row["absorbed_red_pfd_umol_m2_s"]) for row in plant_rows]
        ),
        "plant_to_plant_absorbed_orange_pfd_cv": _coefficient_of_variation(
            [float(row["absorbed_orange_pfd_umol_m2_s"]) for row in plant_rows]
        ),
        "plant_to_plant_absorbed_far_red_pfd_cv": _coefficient_of_variation(
            [float(row["absorbed_far_red_pfd_umol_m2_s"]) for row in plant_rows]
        ),
        "plant_to_plant_blue_fraction_cv": _coefficient_of_variation(
            [
                float(row["absorbed_blue_fraction_of_par"])
                for row in plant_rows
                if row["absorbed_blue_fraction_of_par"] is not None
            ]
        ),
        "plant_to_plant_red_far_red_diagnostic_cv": _coefficient_of_variation(
            [
                float(row["absorbed_red_to_far_red_ratio_diagnostic"])
                for row in plant_rows
                if row["absorbed_red_to_far_red_ratio_diagnostic"] is not None
            ]
        ),
    }


def build_plant_photoreceptor_exposure_payload(
    spectral_response_payload: Mapping[str, Any],
    *,
    method: str = PLANT_PHOTORECEPTOR_EXPOSURE_METHOD,
) -> dict[str, Any]:
    spectral_rows = _spectral_leaf_rows(spectral_response_payload)
    leaf_rows = [_leaf_exposure_summary(row) for row in spectral_rows]
    plant_rows = _aggregate_plant_rows(leaf_rows)
    exposure_means = _exposure_mean_fields(plant_rows)

    return {
        "schema": PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA,
        "schema_version": PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA_VERSION,
        "status": "computed",
        "method": method,
        "source_spectral_response_schema": spectral_response_payload.get("schema"),
        "source_spectral_response_method": spectral_response_payload.get("method"),
        "source_spectral_response_data_basis": spectral_response_payload.get(
            "source_data_basis"
        ),
        "source_spectral_distribution": spectral_response_payload.get("spectral_distribution"),
        "source_artifact": "runtime_state/plant_spectral_response.json",
        "units": {
            "photon_flux_density": "umol/m2/s",
            "area": "m2",
            "fraction": "0..1",
            "ratio": "unitless",
        },
        "plant_count": spectral_response_payload.get("plant_count"),
        "leaf_count": spectral_response_payload.get("leaf_count"),
        "surface_count": spectral_response_payload.get("surface_count"),
        **exposure_means,
        "blue_photon_dose": {
            "value_umol_m2": None,
            "status": "not_computed",
            "reason": "recipe timing input not present",
        },
        "phytochrome_pss_proxy": {
            "value": None,
            "status": "not_computed",
            "reason": (
                "requires wavelength-dependent phytochrome action inputs; "
                "current artifact contains broad spectral bands"
            ),
        },
        "single_leaf_transmission_proxy_note": (
            "Far-red transmission fields are single-leaf optical proxies."
        ),
        "red_far_red_diagnostic_note": (
            "R:FR is reported as an absorbed-light diagnostic only; no response "
            "outcome is inferred."
        ),
        "optional_hypotheses": {
            "enabled": False,
            "status": "not_part_of_core_exposure_artifact",
        },
        "exposure_consistency": _exposure_consistency(plant_rows),
        "plant_summaries": plant_rows,
        "leaf_summaries": leaf_rows,
        "warnings": [
            "Photoreceptor exposure artifact reports spectral lighting inputs only.",
            "Broad spectral-band diagnostics are not wavelength-action models.",
        ],
        "limitations": [
            "PSS is not computed without wavelength-dependent phytochrome method inputs.",
            "Dose fields remain null unless recipe timing input is provided.",
        ],
    }


@overload
def write_plant_photoreceptor_exposure_artifact(
    target_dir: str | Path,
    spectral_response_payload: Mapping[str, Any],
    *,
    return_payload: Literal[True],
) -> tuple[Path, dict[str, Any]]: ...


@overload
def write_plant_photoreceptor_exposure_artifact(
    target_dir: str | Path,
    spectral_response_payload: Mapping[str, Any],
    *,
    return_payload: Literal[False] = ...,
) -> Path: ...


def write_plant_photoreceptor_exposure_artifact(
    target_dir: str | Path,
    spectral_response_payload: Mapping[str, Any],
    *,
    return_payload: bool = False,
) -> Path | tuple[Path, dict[str, Any]]:
    output_dir = Path(target_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = build_plant_photoreceptor_exposure_payload(spectral_response_payload)
    path = output_dir / PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME
    path.write_text(
        json.dumps(compact_plant_photoreceptor_exposure_payload(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return (path, payload) if return_payload else path


def compact_plant_photoreceptor_exposure_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in dict(payload).items()
        if key not in {"plant_summaries", "leaf_summaries"}
    }
