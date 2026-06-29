from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import HTTPException

from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_SMD
from rad_rebuild.radiance.engine.plants.absorption import PHOTON_ABSORPTION_SCAFFOLD_SCHEMA
from rad_rebuild.radiance.engine.plants.artifacts import PLANT_ABSORPTION_SURFACES_FILENAME
from rad_rebuild.radiance.engine.plants.photomorphogenesis import (
    PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME,
    PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.photoreceptor import (
    PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME,
    PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.photosynthesis import (
    PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME,
    SUPPORTED_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMAS,
)
from rad_rebuild.radiance.engine.plants.surface_flux import (
    PLANT_SURFACE_FLUX_FILENAME,
    PLANT_SURFACE_FLUX_SCHEMA,
    RADIANCE_RECEIVER_METHOD,
)
from rad_rebuild.radiance.engine.plants.spectral import (
    PLANT_SPECTRAL_RESPONSE_FILENAME,
    PLANT_SPECTRAL_RESPONSE_SCHEMA,
)
from rad_rebuild.radiance.engine.plants.spectral_absorption import (
    PLANT_SPECTRAL_ABSORPTION_FILENAME,
    PLANT_SPECTRAL_ABSORPTION_SCHEMA,
)

from .artifacts import BACKEND_SERVER_FILE, _cache_fresh, _layout_file_for_mode
from .costs import build_cost_estimate
from .env import HPS_MODE_LABEL, _aligned_dims_ft, _canonicalize_mode_request
from .models import RadianceRunRequest
from .workspace import ENGINE_PACKAGE_ROOT, ROOT


def _parse_kv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        for line in path.read_text().splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            key, val = s.split("=", 1)
            out[key.strip()] = val.strip()
    except Exception:
        return {}
    return out


def _parse_smd_summary(path: Path) -> tuple[float | None, float | None]:
    watts = None
    ppf = None
    try:
        text = path.read_text()
    except Exception:
        return None, None
    num_re = re.compile(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?")
    for line in text.splitlines():
        s = line.strip().lower()
        if watts is None and ("total electrical" in s or "total input" in s) and "w" in s:
            nums = num_re.findall(s)
            if nums:
                try:
                    watts = float(nums[-1])
                except ValueError:
                    watts = None
        if ppf is None and ("total photons" in s or "total ppf" in s) and ("mol/s" in s):
            nums = num_re.findall(s)
            if nums:
                try:
                    ppf = float(nums[-1])
                except ValueError:
                    ppf = None
        if watts is not None and ppf is not None:
            break
    return watts, ppf


def _metrics_path(mode: str, workspace_root: Path | None = None) -> Path:
    slug = mode.lower().replace(" ", "_")
    out = (workspace_root or ROOT) / "artifacts"
    out.mkdir(parents=True, exist_ok=True)
    return out / f"radiance_metrics_{slug}.json"


def _metrics_dependencies(workspace_root: Path | None = None) -> list[Path | None]:
    work_root = workspace_root or ROOT
    return [
        BACKEND_SERVER_FILE,
        ENGINE_PACKAGE_ROOT / "layout" / "layout_engine.py",
        work_root / "ppfd_map.txt",
        _layout_file_for_mode(MODE_COMPETITOR, work_root),
        _layout_file_for_mode(HPS_MODE_LABEL, work_root),
        _layout_file_for_mode(MODE_SMD, work_root),
        work_root / "runtime_state" / "spydr3_power.txt",
        work_root / "runtime_state" / "hps_power.txt",
        work_root / "runtime_state" / "smd_summary.txt",
        work_root / "runtime_state" / "last_run.json",
        work_root / "runtime_state" / PLANT_ABSORPTION_SURFACES_FILENAME,
        work_root / "runtime_state" / PLANT_SURFACE_FLUX_FILENAME,
        work_root / "runtime_state" / PLANT_SPECTRAL_ABSORPTION_FILENAME,
        work_root / "runtime_state" / PLANT_SPECTRAL_RESPONSE_FILENAME,
        work_root / "runtime_state" / PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME,
        work_root / "runtime_state" / PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME,
        work_root / "runtime_state" / PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME,
    ]


def _plant_absorption_unavailable(reason: str) -> dict[str, object]:
    return {
        "schema": PHOTON_ABSORPTION_SCAFFOLD_SCHEMA,
        "status": "unavailable",
        "reason": reason,
        "outputs_do_not_predict": [
            "yield",
            "biomass",
            "growth",
            "crop_output",
        ],
    }


def _load_plant_surface_flux_summary(workspace_root: Path) -> dict[str, object] | None:
    path = workspace_root / "runtime_state" / PLANT_SURFACE_FLUX_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _plant_absorption_unavailable("invalid_surface_flux_artifact")
    if not isinstance(payload, dict):
        return _plant_absorption_unavailable("invalid_surface_flux_artifact")
    if payload.get("schema") != PLANT_SURFACE_FLUX_SCHEMA:
        return _plant_absorption_unavailable("unsupported_surface_flux_schema")

    method = payload.get("method")
    status = payload.get("status", "proxy")
    note = (
        "Radiance receiver sampling present. Values are sampled at leaf surface "
        "centroids/normals against the unblocked baseline lighting field. "
        "Target classification uses target-equivalent PPFD; incident receiver "
        "flux remains separate from modeled spectral absorption."
        if status == "computed" and method == RADIANCE_RECEIVER_METHOD
        else (
            "Incident surface-flux artifact present. Current values are proxy "
            "values until the Radiance per-surface receiver method is reviewed. "
            "Target classification uses target-equivalent PPFD where available; "
            "modeled spectral absorption is reported only when "
            "plant_spectral_absorption.json is available."
        )
    )

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "artifact_role": payload.get("artifact_role", "incident_leaf_surface_flux"),
        "display_label": "Incident leaf-surface PPFD",
        "status": status,
        "method": method,
        "source_artifact": f"runtime_state/{PLANT_SURFACE_FLUX_FILENAME}",
        "source_ppfd_map": payload.get("source_ppfd_map"),
        "baseline_transport_scene": payload.get("baseline_transport_scene"),
        "fspm_receiver_transport_scene": payload.get("fspm_receiver_transport_scene"),
        "receiver_trace_count": payload.get("receiver_trace_count"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "one_sided_leaf_area_m2": payload.get("one_sided_leaf_area_m2"),
        "total_incident_photon_flux_umol_s": payload.get("total_incident_photon_flux_umol_s"),
        "incident_leaf_surface_flux_total_umol_s": payload.get(
            "total_incident_photon_flux_umol_s"
        ),
        "incident_leaf_surface_ppfd_umol_m2_s": payload.get(
            "raw_mean_flux_density_umol_m2_s"
        ),
        "total_absorbed_photon_flux_umol_s": payload.get("total_absorbed_photon_flux_umol_s"),
        "legacy_broadband_absorbed_flux_total_umol_s": payload.get(
            "total_absorbed_photon_flux_umol_s"
        ),
        "broadband_absorption_note": (
            "Legacy absorbed fields are scalar optical-assumption diagnostics, "
            "not wavelength-resolved modeled leaf absorption."
        ),
        "mean_absorbed_fraction_of_incident": payload.get("mean_absorbed_fraction_of_incident"),
        "plant_to_plant_absorbed_photon_flux_cv": payload.get("plant_to_plant_absorbed_photon_flux_cv"),
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
        "under_lit_leaves": payload.get("under_lit_leaves"),
        "target_range_leaves": payload.get("target_range_leaves"),
        "over_lit_leaves": payload.get("over_lit_leaves"),
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
        "under_lit_plant_fraction": payload.get("under_lit_plant_fraction"),
        "target_range_plant_fraction": payload.get("target_range_plant_fraction"),
        "over_lit_plant_fraction": payload.get("over_lit_plant_fraction"),
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
        "target_capped_total_flux_umol_s": payload.get("target_capped_total_flux_umol_s"),
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
        "units": payload.get("units"),
        "plant_summaries": payload.get("plant_summaries", []),
        "leaf_summaries": payload.get("leaf_summaries", []),
        "visualization": payload.get("visualization"),
        "outputs_do_not_predict": payload.get(
            "outputs_do_not_predict",
            ["yield", "biomass", "growth", "crop_output"],
        ),
        "warnings": payload.get("warnings", []),
        "limitations": payload.get("limitations", []),
        "note": note,
    }


def _load_plant_spectral_absorption_summary(workspace_root: Path) -> dict[str, object] | None:
    path = workspace_root / "runtime_state" / PLANT_SPECTRAL_ABSORPTION_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != PLANT_SPECTRAL_ABSORPTION_SCHEMA:
        return None

    crop = payload.get("crop_summary")
    crop_summary = crop if isinstance(crop, dict) else {}
    optical = payload.get("optical_profile")
    optical_profile = optical if isinstance(optical, dict) else {}
    source_spectrum = payload.get("source_spectrum")
    source = source_spectrum if isinstance(source_spectrum, dict) else {}

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "artifact_role": payload.get(
            "artifact_role",
            "modeled_spectral_leaf_photon_absorption",
        ),
        "display_label": "Modeled spectral leaf absorption",
        "status": payload.get("status"),
        "method": payload.get("method"),
        "source_artifact": f"runtime_state/{PLANT_SPECTRAL_ABSORPTION_FILENAME}",
        "source_surface_flux_method": payload.get("source_surface_flux_method"),
        "optical_profile_id": optical_profile.get("profile_id"),
        "optical_profile_version": optical_profile.get("profile_version"),
        "source_spectral_basis": payload.get("source_spectral_basis"),
        "scalar_flux_basis": payload.get("scalar_flux_basis"),
        "baseline_transport_scene": payload.get("baseline_transport_scene"),
        "fspm_receiver_transport_scene": payload.get("fspm_receiver_transport_scene"),
        "receiver_trace_count": payload.get("receiver_trace_count"),
        "source_spectrum_id": source.get("distribution_id"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "scalar_incident_par_ppfd_umol_m2_s": crop_summary.get(
            "scalar_incident_par_ppfd_umol_m2_s"
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
        "absorbed_fraction": crop_summary.get("absorbed_fraction"),
        "reflected_fraction": crop_summary.get("reflected_fraction"),
        "transmitted_fraction": crop_summary.get("transmitted_fraction"),
        "outputs_do_not_predict": payload.get(
            "outputs_do_not_predict",
            ["yield", "biomass", "growth", "crop_output"],
        ),
        "warnings": payload.get("warnings", []),
        "limitations": payload.get("limitations", []),
        "note": (
            "Modeled spectral absorption artifact present. Values summarize "
            "absorbed/reflected/transmitted leaf photon flux from the selected "
            "optical profile and source spectrum basis."
        ),
    }





def _load_plant_photomorphogenesis_response_summary(workspace_root: Path) -> dict[str, object] | None:
    path = workspace_root / "runtime_state" / PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != PLANT_PHOTOMORPHOGENESIS_RESPONSE_SCHEMA:
        return None

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "method": payload.get("method"),
        "source_artifact": f"runtime_state/{PLANT_PHOTOMORPHOGENESIS_RESPONSE_FILENAME}",
        "source_spectral_response_method": payload.get("source_spectral_response_method"),
        "source_photosynthesis_response_method": payload.get("source_photosynthesis_response_method"),
        "source_spectral_distribution": payload.get("source_spectral_distribution"),
        "parameters": payload.get("parameters"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "mean_plant_shade_avoidance_response_index_0_1": payload.get("mean_plant_shade_avoidance_response_index_0_1"),
        "plant_to_plant_shade_avoidance_cv": payload.get("plant_to_plant_shade_avoidance_cv"),
        "mean_plant_morphology_balance_index_0_1": payload.get("mean_plant_morphology_balance_index_0_1"),
        "plant_to_plant_morphology_balance_cv": payload.get("plant_to_plant_morphology_balance_cv"),
        "shade_avoidance_leaf_count": payload.get("shade_avoidance_leaf_count"),
        "compact_response_leaf_count": payload.get("compact_response_leaf_count"),
        "expansion_favorable_leaf_count": payload.get("expansion_favorable_leaf_count"),
        "plant_summaries": payload.get("plant_summaries", []),
        "leaf_summaries": payload.get("leaf_summaries", []),
        "visualization": payload.get("visualization"),
        "outputs_do_not_predict": payload.get(
            "outputs_do_not_predict",
            ["yield", "biomass", "growth", "crop_output"],
        ),
        "warnings": payload.get("warnings", []),
        "limitations": payload.get("limitations", []),
        "note": (
            "Photomorphogenic response artifact present. Values are spectral-ratio "
            "response potentials and do not predict crop output."
        ),
    }


def _load_plant_photoreceptor_exposure_summary(workspace_root: Path) -> dict[str, object] | None:
    path = workspace_root / "runtime_state" / PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA:
        return None

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "method": payload.get("method"),
        "source_artifact": f"runtime_state/{PLANT_PHOTORECEPTOR_EXPOSURE_FILENAME}",
        "source_spectral_response_method": payload.get("source_spectral_response_method"),
        "source_spectral_distribution": payload.get("source_spectral_distribution"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "blue_photon_dose": payload.get("blue_photon_dose"),
        "phytochrome_pss_proxy": payload.get("phytochrome_pss_proxy"),
        "single_leaf_transmission_proxy_note": payload.get(
            "single_leaf_transmission_proxy_note"
        ),
        "red_far_red_diagnostic_note": payload.get("red_far_red_diagnostic_note"),
        "optional_hypotheses": payload.get("optional_hypotheses"),
        "exposure_consistency": payload.get("exposure_consistency"),
        "plant_summaries": payload.get("plant_summaries", []),
        "leaf_summaries": payload.get("leaf_summaries", []),
        "warnings": payload.get("warnings", []),
        "limitations": payload.get("limitations", []),
        "note": (
            "Photoreceptor exposure artifact present. Values are spectral "
            "lighting inputs, not response outcomes."
        ),
    }



def _load_plant_photosynthesis_response_summary(workspace_root: Path) -> dict[str, object] | None:
    path = workspace_root / "runtime_state" / PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") not in SUPPORTED_PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMAS:
        return None

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "method": payload.get("method"),
        "method_version": payload.get("method_version"),
        "contract": payload.get("contract"),
        "calibration_status": payload.get("calibration_status"),
        "default_parameter_status": payload.get("default_parameter_status"),
        "target_model": payload.get("target_model"),
        "input_basis": payload.get("input_basis"),
        "evidence_quality_tier": payload.get("evidence_quality_tier"),
        "uncertainty_notes": payload.get("uncertainty_notes", []),
        "non_prediction_framing": payload.get("non_prediction_framing"),
        "source_artifact": f"runtime_state/{PLANT_PHOTOSYNTHESIS_RESPONSE_FILENAME}",
        "source_spectral_response_method": payload.get("source_spectral_response_method"),
        "source_spectral_distribution": payload.get("source_spectral_distribution"),
        "parameters": payload.get("parameters"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "total_absorbed_par_photon_flux_umol_s": payload.get("total_absorbed_par_photon_flux_umol_s"),
        "total_gross_photosynthetic_potential_umol_co2_s": payload.get("total_gross_photosynthetic_potential_umol_co2_s"),
        "total_clipped_net_photosynthetic_potential_umol_co2_s": payload.get("total_clipped_net_photosynthetic_potential_umol_co2_s"),
        "daily_clipped_net_photosynthetic_potential_mol_co2": payload.get("daily_clipped_net_photosynthetic_potential_mol_co2"),
        "mean_leaf_photosynthetic_response_index_0_1": payload.get("mean_leaf_photosynthetic_response_index_0_1"),
        "plant_to_plant_photosynthetic_response_cv": payload.get("plant_to_plant_photosynthetic_response_cv"),
        "light_limited_leaf_count": payload.get("light_limited_leaf_count"),
        "near_saturation_leaf_count": payload.get("near_saturation_leaf_count"),
        "plant_summaries": payload.get("plant_summaries", []),
        "leaf_summaries": payload.get("leaf_summaries", []),
        "visualization": payload.get("visualization"),
        "outputs_do_not_predict": payload.get(
            "outputs_do_not_predict",
            ["yield", "biomass", "growth", "crop_output"],
        ),
        "warnings": payload.get("warnings", []),
        "limitations": payload.get("limitations", []),
        "note": (
            "Photosynthesis response artifact present. Values are absorbed-PAR "
            "response potentials and do not predict crop output."
        ),
    }



def _load_plant_spectral_response_summary(workspace_root: Path) -> dict[str, object] | None:
    path = workspace_root / "runtime_state" / PLANT_SPECTRAL_RESPONSE_FILENAME
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema") != PLANT_SPECTRAL_RESPONSE_SCHEMA:
        return None

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status"),
        "method": payload.get("method"),
        "source_artifact": f"runtime_state/{PLANT_SPECTRAL_RESPONSE_FILENAME}",
        "source_surface_flux_method": payload.get("source_surface_flux_method"),
        "spectral_distribution": payload.get("spectral_distribution"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "total_absorbed_photon_flux_umol_s": payload.get("total_absorbed_photon_flux_umol_s"),
        "total_absorbed_par_photon_flux_umol_s": payload.get("total_absorbed_par_photon_flux_umol_s"),
        "band_totals": payload.get("band_totals"),
        "plant_summaries": payload.get("plant_summaries", []),
        "leaf_summaries": payload.get("leaf_summaries", []),
        "visualization": payload.get("visualization"),
        "outputs_do_not_predict": payload.get(
            "outputs_do_not_predict",
            ["yield", "biomass", "growth", "crop_output"],
        ),
        "warnings": payload.get("warnings", []),
        "limitations": payload.get("limitations", []),
        "note": (
            "Spectral response artifact present. Values split Radiance receiver "
            "flux into band-level absorbed photon estimates using explicit "
            "spectral photon fractions and leaf optical assumptions."
        ),
    }



def _load_plant_photon_absorption_scaffold(workspace_root: Path) -> dict[str, object] | None:
    path = workspace_root / "runtime_state" / PLANT_ABSORPTION_SURFACES_FILENAME
    if not path.is_file():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _plant_absorption_unavailable("invalid_scaffold_artifact")

    if not isinstance(payload, dict):
        return _plant_absorption_unavailable("invalid_scaffold_artifact")

    if payload.get("schema") != PHOTON_ABSORPTION_SCAFFOLD_SCHEMA:
        return _plant_absorption_unavailable("unsupported_scaffold_schema")

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": payload.get("status", "scaffold_only"),
        "method": payload.get("method"),
        "source_artifact": f"runtime_state/{PLANT_ABSORPTION_SURFACES_FILENAME}",
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "one_sided_leaf_area_m2": payload.get("one_sided_leaf_area_m2"),
        "optical_assumptions": payload.get("optical_assumptions"),
        "units": payload.get("units"),
        "outputs_do_not_predict": payload.get(
            "outputs_do_not_predict",
            ["yield", "biomass", "growth", "crop_output"],
        ),
        "limitations": payload.get("limitations", []),
        "note": (
            "Surface registry only. Incident leaf-surface flux requires "
            "plant_surface_flux.json; modeled spectral absorption requires "
            "plant_spectral_absorption.json."
        ),
    }


def _metrics_payload_for_request(req: RadianceRunRequest, workspace_root: Path) -> dict[str, object]:
    import numpy as np
    from rad_rebuild.radiance.engine.photometry.ppfd_metrics import compute_ppfd_metrics

    req = _canonicalize_mode_request(req)
    ppfd_map = workspace_root / "ppfd_map.txt"
    if not ppfd_map.exists():
        raise HTTPException(status_code=404, detail="ppfd_map.txt not found.")
    try:
        data = np.loadtxt(ppfd_map)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load ppfd_map.txt: {e}")
    if data.ndim == 2 and data.shape[1] >= 4:
        ppfd = data[:, 3]
    else:
        ppfd = data

    area = None
    try:
        length_ft, width_ft = _aligned_dims_ft(req.length_ft, req.width_ft)
        area = (length_ft * width_ft) * 0.09290304
    except Exception:
        area = None

    cap = float(req.target_ppfd) if req.peak_capping_enabled and req.target_ppfd and req.target_ppfd > 0 else None
    if req.mode == HPS_MODE_LABEL:
        cap = None

    total_watts = None
    emitted_ppf = None
    spydr_power = workspace_root / "runtime_state" / "spydr3_power.txt"
    hps_power = workspace_root / "runtime_state" / "hps_power.txt"
    smd_summary = workspace_root / "runtime_state" / "smd_summary.txt"

    def _load_spydr_power(path: Path) -> tuple[float | None, float | None]:
        power = _parse_kv_file(path)
        watts = None
        ppf = None
        try:
            watts = float(power.get("total_w", "") or 0.0) or None
        except ValueError:
            watts = None
        try:
            ppf = float(power.get("total_ppf", "") or 0.0) or None
        except ValueError:
            ppf = None
        return watts, ppf

    if req.mode == MODE_COMPETITOR and spydr_power.exists():
        total_watts, emitted_ppf = _load_spydr_power(spydr_power)
    elif req.mode == HPS_MODE_LABEL and hps_power.exists():
        total_watts, emitted_ppf = _load_spydr_power(hps_power)
    elif req.mode == MODE_SMD and smd_summary.exists():
        total_watts, emitted_ppf = _parse_smd_summary(smd_summary)

    metrics = compute_ppfd_metrics(
        ppfd,
        setpoint_ppfd=cap,
        canopy_area_m2=area,
        total_input_watts=total_watts,
        emitted_ppf_umol_s=emitted_ppf,
        legacy_metrics=True,
    )
    if req.mode == HPS_MODE_LABEL:
        metrics["mode_note"] = "Peak-cap metrics are omitted for 1000W HPS because reliable dimming is not assumed."
    elif not req.peak_capping_enabled:
        metrics["mode_note"] = "Peak-capping is disabled. Dimmable LED systems are evaluated against the requested target PPFD without hotspot-cap post-processing."

    plant_photon_absorption = (
        _load_plant_surface_flux_summary(workspace_root)
        or _load_plant_photon_absorption_scaffold(workspace_root)
    )
    if plant_photon_absorption is not None:
        if plant_photon_absorption.get("artifact_role") == "incident_leaf_surface_flux":
            metrics["plant_incident_surface_flux"] = plant_photon_absorption
        metrics["plant_photon_absorption"] = plant_photon_absorption

    plant_spectral_absorption = _load_plant_spectral_absorption_summary(workspace_root)
    if plant_spectral_absorption is not None:
        metrics["plant_spectral_absorption"] = plant_spectral_absorption

    plant_spectral_response = _load_plant_spectral_response_summary(workspace_root)
    if plant_spectral_response is not None:
        metrics["plant_spectral_response"] = plant_spectral_response

    plant_photosynthesis_response = _load_plant_photosynthesis_response_summary(workspace_root)
    if plant_photosynthesis_response is not None:
        metrics["plant_photosynthesis_response"] = plant_photosynthesis_response

    plant_photoreceptor_exposure = _load_plant_photoreceptor_exposure_summary(workspace_root)
    if plant_photoreceptor_exposure is not None:
        metrics["plant_photoreceptor_exposure"] = plant_photoreceptor_exposure

    plant_photomorphogenesis_response = _load_plant_photomorphogenesis_response_summary(workspace_root)
    if plant_photomorphogenesis_response is not None:
        metrics["plant_photomorphogenesis_response"] = plant_photomorphogenesis_response

    cost_estimate = None
    try:
        cost_estimate = build_cost_estimate(req, workspace_root)
    except Exception:
        cost_estimate = None
    return {"metrics": metrics, "cost_estimate": cost_estimate}


def get_metrics_payload(req: RadianceRunRequest, workspace_root: Path) -> dict[str, object]:
    metrics_path = _metrics_path(req.mode, workspace_root)
    if _cache_fresh(metrics_path, _metrics_dependencies(workspace_root)):
        try:
            cached = json.loads(metrics_path.read_text())
            if isinstance(cached, dict) and isinstance(cached.get("metrics"), dict):
                return cached
        except Exception:
            pass
    payload = _metrics_payload_for_request(req, workspace_root)
    try:
        metrics_path.write_text(json.dumps(payload, indent=2, sort_keys=True))
    except Exception:
        pass
    return payload
