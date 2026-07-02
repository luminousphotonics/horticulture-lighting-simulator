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
from rad_rebuild.radiance.engine.plants.spectral_absorption import (
    PLANT_SPECTRAL_ABSORPTION_FILENAME,
    PLANT_SPECTRAL_ABSORPTION_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.surface_flux import (
    PLANT_SURFACE_FLUX_FILENAME,
    PLANT_SURFACE_FLUX_SCHEMA,
)

FSPM_PANEL_SCHEMA = "rad_rebuild.fspm.viewer_panel.v1"
FSPM_PANEL_METRICS_FILENAME = "fspm_panel_metrics.json"
FSPM_TRANSPORT_METADATA_KEYS: tuple[str, ...] = (
    "fspm_spectral_transport_mode",
    "leaf_radiance_material_mode",
    "receiver_trace_count",
    "banded_transport_band_count",
    "banded_transport_active_trace_count",
    "band_scaling_basis",
    "scalar_flux_basis",
    "source_spectrum_basis",
    "source_spectral_basis",
    "leaf_material_profile_id",
    "leaf_material_profile_version",
    "leaf_material_weighting_basis",
    "leaf_material_source_spectrum_id",
    "leaf_material_source_spectrum_source",
)
BASELINE_PPFD_METADATA_KEYS: tuple[str, ...] = (
    "baseline_ppfd_transport_basis",
    "baseline_ppfd_rgb_decode_method",
    "baseline_source_channel_policy",
    "ppfd_conversion_basis",
    "photopic_luminance_weighting_avoided",
    "uses_179_luminous_efficacy_factor",
    "uses_falsecolor_or_illuminance_conversion",
)
TARGET_CAPPED_SPECTRAL_METADATA_KEYS: tuple[str, ...] = (
    "target_capped_absorption_basis",
    "target_classification_basis",
    "target_classification_source",
    "target_saturation_cap_ppfd_umol_m2_s",
    "target_range_lower_ppfd_umol_m2_s",
    "target_range_upper_ppfd_umol_m2_s",
    "target_cap_scale_basis",
    "raw_absorption_preserved",
    "not_biological_prediction",
)
TARGET_CAPPED_SPECTRAL_SUMMARY_KEYS: tuple[str, ...] = (
    "target_capped_absorbed_par_ppfd",
    "target_capped_absorbed_epar_ppfd",
    "target_capped_absorbed_blue_ppfd",
    "target_capped_absorbed_green_ppfd",
    "target_capped_absorbed_orange_ppfd",
    "target_capped_absorbed_red_ppfd",
    "target_capped_absorbed_far_red_ppfd",
    "target_capped_absorbed_par_ppfd_umol_m2_s",
    "target_capped_absorbed_epar_ppfd_umol_m2_s",
    "target_capped_absorbed_blue_ppfd_umol_m2_s",
    "target_capped_absorbed_green_ppfd_umol_m2_s",
    "target_capped_absorbed_orange_ppfd_umol_m2_s",
    "target_capped_absorbed_red_ppfd_umol_m2_s",
    "target_capped_absorbed_far_red_ppfd_umol_m2_s",
    "excess_absorbed_par_ppfd_above_target_cap",
    "excess_absorbed_epar_ppfd_above_target_cap",
    "target_capped_absorbed_par_fraction_of_raw",
    "target_capped_absorbed_epar_fraction_of_raw",
    "target_effective_absorbed_fraction",
    "over_target_absorbed_par_fraction_of_raw",
    "under_target_leaf_fraction",
    "in_target_leaf_fraction",
    "over_target_leaf_fraction",
)


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


def _compact_panel_artifact(workspace_root: Path) -> Path:
    return _runtime_artifact(workspace_root, FSPM_PANEL_METRICS_FILENAME)


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
    ppfd_field = payload.get("ppfd_field_summary")
    ppfd_field_summary = ppfd_field if isinstance(ppfd_field, Mapping) else {}
    area = _finite(payload.get("one_sided_leaf_area_m2"))
    absorbed = _finite(payload.get("total_absorbed_photon_flux_umol_s"))
    mean_density = absorbed / area if absorbed is not None and area and area > 0.0 else None
    return {
        **_safe_artifact_meta(payload),
        **{
            key: payload.get(key)
            for key in FSPM_TRANSPORT_METADATA_KEYS
            if key in payload
        },
        **{
            key: ppfd_field_summary.get(key)
            for key in BASELINE_PPFD_METADATA_KEYS
            if key in ppfd_field_summary
        },
        "artifact_role": payload.get("artifact_role", "incident_leaf_surface_flux"),
        "display_label": "Incident leaf-surface PPFD",
        "modeled_spectral_absorption_artifact": payload.get(
            "modeled_spectral_absorption_artifact",
            f"runtime_state/{PLANT_SPECTRAL_ABSORPTION_FILENAME}",
        ),
        "broadband_absorption_note": (
            "Legacy absorbed fields are scalar optical-assumption diagnostics, "
            "not wavelength-resolved modeled leaf absorption."
        ),
        "baseline_transport_scene": payload.get("baseline_transport_scene"),
        "fspm_receiver_transport_scene": payload.get("fspm_receiver_transport_scene"),
        "receiver_trace_count": payload.get("receiver_trace_count"),
        "receiver_sample_count": payload.get("receiver_sample_count"),
        "receiver_granularity": payload.get("receiver_granularity"),
        "receiver_samples_per_leaf": payload.get("receiver_samples_per_leaf"),
        "receiver_generation_basis": payload.get("receiver_generation_basis"),
        "receiver_represented_area_m2": payload.get("receiver_represented_area_m2"),
        "receiver_sample_area_sum_m2": payload.get("receiver_sample_area_sum_m2"),
        "receiver_area_basis": payload.get("receiver_area_basis"),
        "receiver_side_policy": payload.get("receiver_side_policy"),
        "receiver_rows_per_mesh_surface_row": payload.get(
            "receiver_rows_per_mesh_surface_row"
        ),
        "normal_generation_basis": payload.get("normal_generation_basis"),
        "receiver_granularity_role": payload.get("receiver_granularity_role"),
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


def _spectral_absorption(payload: Mapping[str, Any] | None) -> dict[str, object] | None:
    if not payload or payload.get("schema") != PLANT_SPECTRAL_ABSORPTION_SCHEMA:
        return None
    crop = payload.get("crop_summary")
    crop_summary = crop if isinstance(crop, Mapping) else {}
    optical = payload.get("optical_profile")
    optical_profile = optical if isinstance(optical, Mapping) else {}
    source_spectrum = payload.get("source_spectrum")
    source = source_spectrum if isinstance(source_spectrum, Mapping) else {}
    return {
        **_safe_artifact_meta(payload),
        **{
            key: payload.get(key)
            for key in TARGET_CAPPED_SPECTRAL_METADATA_KEYS
            if key in payload
        },
        "artifact_role": payload.get(
            "artifact_role",
            "modeled_spectral_leaf_photon_absorption",
        ),
        "display_label": "Modeled spectral leaf absorption",
        "source_artifact": f"runtime_state/{PLANT_SPECTRAL_ABSORPTION_FILENAME}",
        "optical_profile_id": optical_profile.get("profile_id"),
        "optical_profile_version": optical_profile.get("profile_version"),
        "source_spectral_basis": payload.get("source_spectral_basis"),
        "scalar_flux_basis": payload.get("scalar_flux_basis"),
        "baseline_transport_scene": payload.get("baseline_transport_scene"),
        "fspm_receiver_transport_scene": payload.get("fspm_receiver_transport_scene"),
        "receiver_trace_count": payload.get("receiver_trace_count"),
        "receiver_sample_count": payload.get("receiver_sample_count"),
        "receiver_granularity": payload.get("receiver_granularity"),
        "receiver_samples_per_leaf": payload.get("receiver_samples_per_leaf"),
        "receiver_generation_basis": payload.get("receiver_generation_basis"),
        "receiver_represented_area_m2": payload.get("receiver_represented_area_m2"),
        "receiver_sample_area_sum_m2": payload.get("receiver_sample_area_sum_m2"),
        "receiver_area_basis": payload.get("receiver_area_basis"),
        "receiver_side_policy": payload.get("receiver_side_policy"),
        "receiver_rows_per_mesh_surface_row": payload.get(
            "receiver_rows_per_mesh_surface_row"
        ),
        "normal_generation_basis": payload.get("normal_generation_basis"),
        "receiver_granularity_role": payload.get("receiver_granularity_role"),
        "source_spectrum_id": source.get("distribution_id"),
        "fspm_spectral_transport_mode": payload.get("fspm_spectral_transport_mode"),
        "leaf_radiance_material_mode": payload.get("leaf_radiance_material_mode"),
        "leaf_material_profile_id": payload.get("leaf_material_profile_id"),
        "leaf_material_profile_version": payload.get("leaf_material_profile_version"),
        "leaf_material_weighting_basis": payload.get("leaf_material_weighting_basis"),
        "leaf_material_source_spectrum_id": payload.get(
            "leaf_material_source_spectrum_id"
        ),
        "leaf_material_source_spectrum_source": payload.get(
            "leaf_material_source_spectrum_source"
        ),
        "banded_transport_band_count": payload.get("banded_transport_band_count"),
        "banded_transport_active_trace_count": payload.get(
            "banded_transport_active_trace_count"
        ),
        "band_scaling_basis": payload.get("band_scaling_basis"),
        "source_spectrum_basis": payload.get("source_spectrum_basis"),
        "banded_transport_bands": payload.get("banded_transport_bands"),
        "band_summaries": payload.get("band_summaries"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "area_m2": crop_summary.get("area_m2"),
        "scalar_incident_par_ppfd_umol_m2_s": crop_summary.get(
            "scalar_incident_par_ppfd_umol_m2_s"
        ),
        "incident_par_ppfd_umol_m2_s": crop_summary.get(
            "incident_par_ppfd_umol_m2_s"
        ),
        "incident_epar_ppfd_umol_m2_s": crop_summary.get(
            "incident_epar_ppfd_umol_m2_s"
        ),
        "absorbed_par_ppfd_umol_m2_s": crop_summary.get(
            "absorbed_par_ppfd_umol_m2_s"
        ),
        "absorbed_epar_ppfd_umol_m2_s": crop_summary.get(
            "absorbed_epar_ppfd_umol_m2_s"
        ),
        "absorbed_blue_ppfd_umol_m2_s": crop_summary.get(
            "absorbed_blue_ppfd_umol_m2_s"
        ),
        "absorbed_green_ppfd_umol_m2_s": crop_summary.get(
            "absorbed_green_ppfd_umol_m2_s"
        ),
        "absorbed_orange_ppfd_umol_m2_s": crop_summary.get(
            "absorbed_orange_ppfd_umol_m2_s"
        ),
        "absorbed_red_ppfd_umol_m2_s": crop_summary.get(
            "absorbed_red_ppfd_umol_m2_s"
        ),
        "absorbed_far_red_ppfd_umol_m2_s": crop_summary.get(
            "absorbed_far_red_ppfd_umol_m2_s"
        ),
        **{
            key: crop_summary.get(key)
            for key in TARGET_CAPPED_SPECTRAL_SUMMARY_KEYS
            if key in crop_summary
        },
        "absorbed_fraction": crop_summary.get("absorbed_fraction"),
        "reflected_fraction": crop_summary.get("reflected_fraction"),
        "transmitted_fraction": crop_summary.get("transmitted_fraction"),
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
        "mean_absorbed_blue_pfd_umol_m2_s": _first_present(
            payload.get("mean_absorbed_blue_pfd_umol_m2_s"),
            _mean_field(plant_summaries, "absorbed_blue_pfd_umol_m2_s"),
        ),
        "mean_absorbed_green_pfd_umol_m2_s": _first_present(
            payload.get("mean_absorbed_green_pfd_umol_m2_s"),
            _mean_field(plant_summaries, "absorbed_green_pfd_umol_m2_s"),
        ),
        "mean_absorbed_orange_pfd_umol_m2_s": _first_present(
            payload.get("mean_absorbed_orange_pfd_umol_m2_s"),
            _mean_field(plant_summaries, "absorbed_orange_pfd_umol_m2_s"),
        ),
        "mean_absorbed_red_pfd_umol_m2_s": _first_present(
            payload.get("mean_absorbed_red_pfd_umol_m2_s"),
            _mean_field(plant_summaries, "absorbed_red_pfd_umol_m2_s"),
        ),
        "mean_absorbed_far_red_pfd_umol_m2_s": _first_present(
            payload.get("mean_absorbed_far_red_pfd_umol_m2_s"),
            _mean_field(plant_summaries, "absorbed_far_red_pfd_umol_m2_s"),
        ),
        "mean_absorbed_blue_fraction_of_par": _first_present(
            payload.get("mean_absorbed_blue_fraction_of_par"),
            _mean_field(plant_summaries, "absorbed_blue_fraction_of_par"),
        ),
        "mean_absorbed_red_to_far_red_ratio_diagnostic": _first_present(
            payload.get("mean_absorbed_red_to_far_red_ratio_diagnostic"),
            _mean_field(plant_summaries, "absorbed_red_to_far_red_ratio_diagnostic"),
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


def build_fspm_panel_metrics_from_payloads(
    *,
    plants: Mapping[str, Any] | None = None,
    surface: Mapping[str, Any] | None = None,
    spectral: Mapping[str, Any] | None = None,
    spectral_absorption: Mapping[str, Any] | None = None,
    photosynthesis: Mapping[str, Any] | None = None,
    photoreceptor: Mapping[str, Any] | None = None,
    morphology: Mapping[str, Any] | None = None,
) -> dict[str, object] | None:
    viewer_counts = _count_viewer_plants(plants)
    surface_summary = _surface_absorption(surface)
    spectral_summary = _spectral_exposure(spectral)
    spectral_absorption_summary = _spectral_absorption(spectral_absorption)
    photosynthesis_summary = _photosynthetic_response(photosynthesis)
    photoreceptor_summary = _photoreceptor_exposure(photoreceptor)
    morphology_summary = _legacy_morphology_scaffold(morphology)

    if not any(
        (
            viewer_counts,
            surface_summary,
            spectral_summary,
            spectral_absorption_summary,
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
            spectral_absorption_summary.get("plant_count")
            if spectral_absorption_summary
            else None,
            spectral_summary.get("plant_count") if spectral_summary else None,
            surface_summary.get("plant_count") if surface_summary else None,
            viewer_counts.get("plant_count"),
        ),
        "leaf_count": _first_present(
            photoreceptor_summary.get("leaf_count") if photoreceptor_summary else None,
            photosynthesis_summary.get("leaf_count") if photosynthesis_summary else None,
            spectral_absorption_summary.get("leaf_count")
            if spectral_absorption_summary
            else None,
            spectral_summary.get("leaf_count") if spectral_summary else None,
            surface_summary.get("leaf_count") if surface_summary else None,
            viewer_counts.get("leaf_count"),
        ),
        "surface_count": _first_present(
            photoreceptor_summary.get("surface_count") if photoreceptor_summary else None,
            photosynthesis_summary.get("surface_count") if photosynthesis_summary else None,
            spectral_absorption_summary.get("surface_count")
            if spectral_absorption_summary
            else None,
            spectral_summary.get("surface_count") if spectral_summary else None,
            surface_summary.get("surface_count") if surface_summary else None,
        ),
        "receiver_sample_count": _first_present(
            spectral_absorption_summary.get("receiver_sample_count")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_sample_count") if surface_summary else None,
        ),
        "receiver_granularity": _first_present(
            spectral_absorption_summary.get("receiver_granularity")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_granularity") if surface_summary else None,
        ),
        "receiver_granularity_role": _first_present(
            spectral_absorption_summary.get("receiver_granularity_role")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_granularity_role") if surface_summary else None,
        ),
        "receiver_samples_per_leaf": _first_present(
            spectral_absorption_summary.get("receiver_samples_per_leaf")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_samples_per_leaf") if surface_summary else None,
        ),
        "receiver_generation_basis": _first_present(
            spectral_absorption_summary.get("receiver_generation_basis")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_generation_basis") if surface_summary else None,
        ),
        "receiver_represented_area_m2": _first_present(
            spectral_absorption_summary.get("receiver_represented_area_m2")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_represented_area_m2")
            if surface_summary
            else None,
        ),
        "receiver_area_basis": _first_present(
            spectral_absorption_summary.get("receiver_area_basis")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_area_basis") if surface_summary else None,
        ),
        "receiver_side_policy": _first_present(
            spectral_absorption_summary.get("receiver_side_policy")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_side_policy") if surface_summary else None,
        ),
        "receiver_rows_per_mesh_surface_row": _first_present(
            spectral_absorption_summary.get("receiver_rows_per_mesh_surface_row")
            if spectral_absorption_summary
            else None,
            surface_summary.get("receiver_rows_per_mesh_surface_row")
            if surface_summary
            else None,
        ),
        "normal_generation_basis": _first_present(
            spectral_absorption_summary.get("normal_generation_basis")
            if spectral_absorption_summary
            else None,
            surface_summary.get("normal_generation_basis") if surface_summary else None,
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
        payload["incident_leaf_surface_flux"] = surface_summary
        payload["plant_surface_absorption"] = surface_summary
    if spectral_absorption_summary is not None:
        payload["modeled_spectral_absorption"] = spectral_absorption_summary
    if spectral_summary is not None:
        payload["spectral_exposure"] = spectral_summary
    if photosynthesis_summary is not None:
        payload["photosynthetic_light_response_potential"] = photosynthesis_summary
    if photoreceptor_summary is not None:
        payload["photoreceptor_exposure"] = photoreceptor_summary
    if morphology_summary is not None:
        payload["legacy_morphology_response_scaffold"] = morphology_summary
    return payload


def write_fspm_panel_metrics_artifact(
    workspace_root: Path,
    *,
    plants: Mapping[str, Any] | None = None,
    surface: Mapping[str, Any] | None = None,
    spectral: Mapping[str, Any] | None = None,
    spectral_absorption: Mapping[str, Any] | None = None,
    photosynthesis: Mapping[str, Any] | None = None,
    photoreceptor: Mapping[str, Any] | None = None,
    morphology: Mapping[str, Any] | None = None,
) -> Path | None:
    payload = build_fspm_panel_metrics_from_payloads(
        plants=plants,
        surface=surface,
        spectral=spectral,
        spectral_absorption=spectral_absorption,
        photosynthesis=photosynthesis,
        photoreceptor=photoreceptor,
        morphology=morphology,
    )
    if payload is None:
        return None
    path = _compact_panel_artifact(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def build_fspm_panel_metrics(workspace_root: Path) -> dict[str, object] | None:
    compact = _load_json(_compact_panel_artifact(workspace_root))
    if compact is not None and compact.get("schema") == FSPM_PANEL_SCHEMA:
        return dict(compact)

    runtime = workspace_root / "runtime_state"
    plants = _load_json(runtime / PLANTS_VIEWER_FILENAME)
    surface = _load_json(_runtime_artifact(workspace_root, PLANT_SURFACE_FLUX_FILENAME))
    spectral = _load_json(_runtime_artifact(workspace_root, PLANT_SPECTRAL_RESPONSE_FILENAME))
    spectral_absorption = _load_json(
        _runtime_artifact(workspace_root, PLANT_SPECTRAL_ABSORPTION_FILENAME)
    )
    photosynthesis = _load_json(_runtime_artifact(workspace_root, PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME))
    photoreceptor = _load_json(_runtime_artifact(workspace_root, PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME))
    morphology = _load_json(_runtime_artifact(workspace_root, PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME))

    return build_fspm_panel_metrics_from_payloads(
        plants=plants,
        surface=surface,
        spectral=spectral,
        spectral_absorption=spectral_absorption,
        photosynthesis=photosynthesis,
        photoreceptor=photoreceptor,
        morphology=morphology,
    )
