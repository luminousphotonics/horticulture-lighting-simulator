from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Mapping

from rad_rebuild.radiance.engine.plants.artifacts import PLANTS_VIEWER_FILENAME
from rad_rebuild.radiance.engine.plants.photomorphogenesis import (
    PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME,
    PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.photoreceptor import (
    PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME,
    PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.photosynthesis import (
    SUPPORTED_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMAS,
    PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME,
)
from rad_rebuild.radiance.engine.plants.spectral import (
    PLANT_SPECTRAL_RESPONSE_FILENAME,
    PLANT_SPECTRAL_RESPONSE_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.surface_flux import (
    PLANT_SURFACE_FLUX_FILENAME,
    PLANT_SURFACE_FLUX_SCHEMA,
)

FSPM_PANEL_SCHEMA = "rad_rebuild.fspm.viewer_panel.v1"


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _runtime_artifact(workspace_root: Path, filename: str) -> Path:
    return workspace_root / "runtime_state" / filename


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _count_viewer_plants(payload: Mapping[str, Any] | None) -> dict[str, int]:
    plants = payload.get("plants") if isinstance(payload, Mapping) else None
    if not isinstance(plants, list):
        return {}
    leaf_count = 0
    for plant in plants:
        if not isinstance(plant, Mapping):
            continue
        leaves = plant.get("leaves")
        if isinstance(leaves, list):
            leaf_count += len(leaves)
    return {"plant_count": len(plants), "leaf_count": leaf_count}


def _first_present(*values: object) -> object:
    for value in values:
        if value is not None:
            return value
    return None


def _lower_tail_leaf_density(payload: Mapping[str, Any]) -> float | None:
    rows = payload.get("leaf_summaries")
    if not isinstance(rows, list):
        return None
    values = sorted(
        value
        for row in rows
        if isinstance(row, Mapping)
        for value in [_finite(row.get("absorbed_photon_flux_density_umol_m2_s"))]
        if value is not None
    )
    if not values:
        return None
    index = max(0, min(len(values) - 1, math.floor((len(values) - 1) * 0.10)))
    return values[index]


def _safe_artifact_meta(payload: Mapping[str, Any]) -> dict[str, object]:
    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "method": payload.get("method"),
    }


def _surface_absorption(payload: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not payload or payload.get("schema") != PLANT_SURFACE_FLUX_SCHEMA:
        return None
    area = _finite(payload.get("one_sided_leaf_area_m2"))
    absorbed = _finite(payload.get("total_absorbed_photon_flux_umol_s"))
    mean_density = absorbed / area if absorbed is not None and area and area > 0.0 else None
    return {
        **_safe_artifact_meta(payload),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "one_sided_leaf_area_m2": payload.get("one_sided_leaf_area_m2"),
        "target": payload.get("target"),
        "target_ppfd_umol_m2_s": payload.get("target_ppfd_umol_m2_s"),
        "target_tolerance_umol_m2_s": payload.get("target_tolerance_umol_m2_s"),
        "target_classification_basis": payload.get("target_classification_basis"),
        "target_classification_basis_label": payload.get(
            "target_classification_basis_label"
        ),
        "target_classification_source": payload.get("target_classification_source"),
        "target_classification_note": payload.get("target_classification_note"),
        "target_basis": payload.get("target_basis"),
        "target_basis_label": payload.get("target_basis_label"),
        "target_lower_threshold_umol_m2_s": payload.get("target_lower_threshold_umol_m2_s"),
        "target_upper_threshold_umol_m2_s": payload.get("target_upper_threshold_umol_m2_s"),
        "target_capping_enabled": payload.get("target_capping_enabled"),
        "under_lit_leaf_count": payload.get("under_lit_leaf_count"),
        "target_range_leaf_count": payload.get("target_range_leaf_count"),
        "over_lit_leaf_count": payload.get("over_lit_leaf_count"),
        "under_lit_leaf_fraction": payload.get("under_lit_leaf_fraction"),
        "target_range_leaf_fraction": payload.get("target_range_leaf_fraction"),
        "over_lit_leaf_fraction": payload.get("over_lit_leaf_fraction"),
        "under_lit_surface_count": payload.get("under_lit_surface_count"),
        "target_range_surface_count": payload.get("target_range_surface_count"),
        "over_lit_surface_count": payload.get("over_lit_surface_count"),
        "under_lit_surface_fraction": payload.get("under_lit_surface_fraction"),
        "target_range_surface_fraction": payload.get("target_range_surface_fraction"),
        "over_lit_surface_fraction": payload.get("over_lit_surface_fraction"),
        "under_lit_plant_count": payload.get("under_lit_plant_count"),
        "target_range_plant_count": payload.get("target_range_plant_count"),
        "over_lit_plant_count": payload.get("over_lit_plant_count"),
        "raw_mean_flux_density_umol_m2_s": payload.get("raw_mean_flux_density_umol_m2_s"),
        "target_classification_mean_ppfd_umol_m2_s": payload.get(
            "target_classification_mean_ppfd_umol_m2_s"
        ),
        "target_classification_total_incident_flux_umol_s": payload.get(
            "target_classification_total_incident_flux_umol_s"
        ),
        "target_capped_incident_mean_flux_density_umol_m2_s": payload.get(
            "target_capped_incident_mean_flux_density_umol_m2_s"
        ),
        "target_capped_incident_flux_total_umol_s": payload.get(
            "target_capped_incident_flux_total_umol_s"
        ),
        "target_capped_incident_total_flux_umol_s": payload.get(
            "target_capped_incident_total_flux_umol_s"
        ),
        "excess_incident_flux_above_target_umol_s": payload.get(
            "excess_incident_flux_above_target_umol_s"
        ),
        "excess_incident_flux_fraction": payload.get("excess_incident_flux_fraction"),
        "deficit_to_target_incident_flux_umol_s": payload.get(
            "deficit_to_target_incident_flux_umol_s"
        ),
        "deficit_to_target_incident_flux_fraction": payload.get(
            "deficit_to_target_incident_flux_fraction"
        ),
        "target_capped_mean_flux_density_umol_m2_s": payload.get(
            "target_capped_mean_flux_density_umol_m2_s"
        ),
        "raw_total_flux_umol_s": payload.get("raw_total_flux_umol_s"),
        "target_capped_flux_total_umol_s": payload.get("target_capped_flux_total_umol_s"),
        "excess_flux_above_target_umol_s": payload.get("excess_flux_above_target_umol_s"),
        "excess_flux_fraction": payload.get("excess_flux_fraction"),
        "under_target_deficit_umol_s": payload.get("under_target_deficit_umol_s"),
        "under_target_deficit_fraction": payload.get("under_target_deficit_fraction"),
        "lower_tail_raw_flux_density_umol_m2_s": payload.get(
            "lower_tail_raw_flux_density_umol_m2_s"
        ),
        "lower_tail_target_classification_ppfd_umol_m2_s": payload.get(
            "lower_tail_target_classification_ppfd_umol_m2_s"
        ),
        "lower_tail_target_capped_incident_flux_density_umol_m2_s": payload.get(
            "lower_tail_target_capped_incident_flux_density_umol_m2_s"
        ),
        "lower_tail_target_capped_flux_density_umol_m2_s": payload.get(
            "lower_tail_target_capped_flux_density_umol_m2_s"
        ),
        "plant_to_plant_target_capped_incident_flux_cv": payload.get(
            "plant_to_plant_target_capped_incident_flux_cv"
        ),
        "plant_to_plant_target_capped_flux_cv": payload.get(
            "plant_to_plant_target_capped_flux_cv"
        ),
        "total_absorbed_photon_flux_umol_s": absorbed,
        "total_incident_photon_flux_umol_s": payload.get("total_incident_photon_flux_umol_s"),
        "mean_absorbed_fraction_of_incident": payload.get("mean_absorbed_fraction_of_incident"),
        "mean_absorbed_photon_flux_density_umol_m2_s": mean_density,
        "plant_to_plant_absorbed_photon_flux_cv": payload.get(
            "plant_to_plant_absorbed_photon_flux_cv"
        ),
        "lower_tail_absorbed_photon_flux_density_umol_m2_s": _lower_tail_leaf_density(payload),
    }


def _spectral_exposure(payload: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not payload or payload.get("schema") != PLANT_SPECTRAL_RESPONSE_SCHEMA:
        return None
    return {
        **_safe_artifact_meta(payload),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "total_absorbed_photon_flux_umol_s": payload.get("total_absorbed_photon_flux_umol_s"),
        "total_absorbed_par_photon_flux_umol_s": payload.get(
            "total_absorbed_par_photon_flux_umol_s"
        ),
        "band_totals": payload.get("band_totals"),
        "spectral_distribution": payload.get("spectral_distribution"),
    }


def _photosynthetic_response(payload: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not payload or payload.get("schema") not in SUPPORTED_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMAS:
        return None
    return {
        **_safe_artifact_meta(payload),
        "method_version": payload.get("method_version"),
        "calibration_status": payload.get("calibration_status"),
        "default_parameter_status": payload.get("default_parameter_status"),
        "target_model": payload.get("target_model"),
        "input_basis": payload.get("input_basis"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "total_absorbed_par_photon_flux_umol_s": payload.get(
            "total_absorbed_par_photon_flux_umol_s"
        ),
        "absorbed_par_area_weighted_mean_umol_m2_s": payload.get(
            "absorbed_par_area_weighted_mean_umol_m2_s"
        ),
        "area_weighted_mean_local_response_0_1": payload.get(
            "area_weighted_mean_local_response_0_1"
        ),
        "equal_plant_mean_normalized_response_0_1": payload.get(
            "equal_plant_mean_normalized_response_0_1"
        ),
        "local_response_p10_0_1": payload.get("local_response_p10_0_1"),
        "bottom_decile_area_weighted_response_0_1": payload.get(
            "bottom_decile_area_weighted_response_0_1"
        ),
        "nonuniformity_response_retention_0_1": payload.get(
            "nonuniformity_response_retention_0_1"
        ),
        "plant_to_plant_photosynthetic_response_cv": payload.get(
            "plant_to_plant_photosynthetic_response_cv"
        ),
    }


def _mean_field(rows: object, field_name: str) -> float | None:
    if not isinstance(rows, list):
        return None
    values = [
        value
        for row in rows
        if isinstance(row, Mapping)
        for value in [_finite(row.get(field_name))]
        if value is not None
    ]
    return sum(values) / len(values) if values else None


def _photoreceptor_exposure(payload: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not payload or payload.get("schema") != PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA:
        return None
    plant_summaries = payload.get("plant_summaries")
    return {
        **_safe_artifact_meta(payload),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "mean_absorbed_blue_pfd_umol_m2_s": _mean_field(
            plant_summaries,
            "absorbed_blue_pfd_umol_m2_s",
        ),
        "mean_absorbed_green_pfd_umol_m2_s": _mean_field(
            plant_summaries,
            "absorbed_green_pfd_umol_m2_s",
        ),
        "mean_absorbed_red_pfd_umol_m2_s": _mean_field(
            plant_summaries,
            "absorbed_red_pfd_umol_m2_s",
        ),
        "mean_absorbed_far_red_pfd_umol_m2_s": _mean_field(
            plant_summaries,
            "absorbed_far_red_pfd_umol_m2_s",
        ),
        "mean_absorbed_blue_fraction_of_par": _mean_field(
            plant_summaries,
            "absorbed_blue_fraction_of_par",
        ),
        "mean_absorbed_red_to_far_red_ratio_diagnostic": _mean_field(
            plant_summaries,
            "absorbed_red_to_far_red_ratio_diagnostic",
        ),
        "phytochrome_pss_proxy": payload.get("phytochrome_pss_proxy"),
        "blue_photon_dose": payload.get("blue_photon_dose"),
        "exposure_consistency": payload.get("exposure_consistency"),
        "optional_hypotheses": payload.get("optional_hypotheses"),
    }


def _legacy_morphology_scaffold(payload: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not payload or payload.get("schema") != PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA:
        return None
    return {
        **_safe_artifact_meta(payload),
        "legacy_output": payload.get("legacy_output"),
        "morphology_hypothesis_status": payload.get("morphology_hypothesis_status"),
        "core_photoreceptor_exposure_artifact": payload.get("core_photoreceptor_exposure_artifact"),
    }


def build_fspm_panel_metrics(workspace_root: Path) -> dict[str, object] | None:
    runtime = workspace_root / "runtime_state"
    plants = _load_json(runtime / PLANTS_VIEWER_FILENAME)
    surface = _load_json(_runtime_artifact(workspace_root, PLANT_SURFACE_FLUX_FILENAME))
    spectral = _load_json(_runtime_artifact(workspace_root, PLANT_SPECTRAL_RESPONSE_FILENAME))
    photosynthesis = _load_json(_runtime_artifact(workspace_root, PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME))
    photoreceptor = _load_json(_runtime_artifact(workspace_root, PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME))
    morphology = _load_json(_runtime_artifact(workspace_root, PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME))

    viewer_counts = _count_viewer_plants(plants)
    surface_summary = _surface_absorption(surface)
    spectral_summary = _spectral_exposure(spectral)
    photosynthesis_summary = _photosynthetic_response(photosynthesis)
    photoreceptor_summary = _photoreceptor_exposure(photoreceptor)
    morphology_summary = _legacy_morphology_scaffold(morphology)

    if not any(
        (
            viewer_counts,
            surface_summary,
            spectral_summary,
            photosynthesis_summary,
            photoreceptor_summary,
            morphology_summary,
        )
    ):
        return None

    counts = {
        "plant_count": _first_present(
            photoreceptor_summary.get("plant_count") if photoreceptor_summary else None,
            photosynthesis_summary.get("plant_count") if photosynthesis_summary else None,
            spectral_summary.get("plant_count") if spectral_summary else None,
            surface_summary.get("plant_count") if surface_summary else None,
            viewer_counts.get("plant_count"),
        ),
        "leaf_count": _first_present(
            photoreceptor_summary.get("leaf_count") if photoreceptor_summary else None,
            photosynthesis_summary.get("leaf_count") if photosynthesis_summary else None,
            spectral_summary.get("leaf_count") if spectral_summary else None,
            surface_summary.get("leaf_count") if surface_summary else None,
            viewer_counts.get("leaf_count"),
        ),
        "surface_count": _first_present(
            photoreceptor_summary.get("surface_count") if photoreceptor_summary else None,
            photosynthesis_summary.get("surface_count") if photosynthesis_summary else None,
            spectral_summary.get("surface_count") if spectral_summary else None,
            surface_summary.get("surface_count") if surface_summary else None,
        ),
        "one_sided_leaf_area_m2": surface_summary.get("one_sided_leaf_area_m2") if surface_summary else None,
    }

    payload: dict[str, object] = {
        "schema": FSPM_PANEL_SCHEMA,
        "status": "available",
        "counts": counts,
        "limitations_note": (
            "Lighting-analysis input only; response potentials are unvalidated "
            "and are not biological production forecasts."
        ),
    }
    if surface_summary is not None:
        payload["plant_surface_absorption"] = surface_summary
    if spectral_summary is not None:
        payload["spectral_exposure"] = spectral_summary
    if photosynthesis_summary is not None:
        payload["photosynthetic_light_response_potential"] = photosynthesis_summary
    if photoreceptor_summary is not None:
        payload["photoreceptor_exposure"] = photoreceptor_summary
    if morphology_summary is not None:
        payload["legacy_morphology_response_scaffold"] = morphology_summary
    return payload
