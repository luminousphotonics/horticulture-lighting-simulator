from __future__ import annotations

import csv
from io import StringIO
import json
import math
from pathlib import Path
from typing import Any, Mapping

from rad_rebuild.radiance.assembly.fspm_panel import build_fspm_panel_metrics
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD

FSPM_CSV_SCALAR_HEADERS = (
    "run_id",
    "mode",
    "system_label",
    "method",
    "artifact_schema",
    "baseline_ppfd_transport_basis",
    "baseline_ppfd_rgb_decode_method",
    "baseline_source_channel_policy",
    "ppfd_conversion_basis",
    "photopic_luminance_weighting_avoided",
    "uses_179_luminous_efficacy_factor",
    "uses_falsecolor_or_illuminance_conversion",
    "target_ppfd_umol_m2_s",
    "target_tolerance_umol_m2_s",
    "target_lower_threshold_umol_m2_s",
    "target_upper_threshold_umol_m2_s",
    "coverage_basis_label",
    "coverage_source_label",
    "plant_count",
    "leaf_count",
    "receiver_sample_count",
    "receiver_granularity",
    "receiver_samples_per_leaf",
    "receiver_side_policy",
    "receiver_rows_per_mesh_surface_row",
    "receiver_area_basis",
    "receiver_surface_count",
    "one_sided_leaf_area_m2",
    "under_lit_leaves",
    "target_range_leaves",
    "over_lit_leaves",
    "under_lit_leaf_percent",
    "target_range_leaf_percent",
    "over_lit_leaf_percent",
    "under_lit_receiver_surfaces",
    "target_range_receiver_surfaces",
    "over_lit_receiver_surfaces",
    "under_lit_receiver_surface_percent",
    "target_range_receiver_surface_percent",
    "over_lit_receiver_surface_percent",
    "plant_location_under_lit_leaves",
    "plant_location_target_range_leaves",
    "plant_location_over_lit_leaves",
    "mean_plant_location_reference_ppfd_umol_m2_s",
    "lower_tail_plant_location_reference_ppfd_umol_m2_s",
    "target_capped_plant_location_flux_total_umol_s",
    "plant_location_target_capacity_flux_umol_s",
    "raw_incident_flux_total_umol_s",
    "raw_leaf_surface_incident_flux_total_umol_s",
    "excess_above_target_plant_location_flux_umol_s",
    "excess_above_target_fraction_of_raw_percent",
    "deficit_to_target_plant_location_flux_umol_s",
    "deficit_to_target_capacity_percent",
    "target_capped_plant_location_fraction_of_raw_percent",
    "target_capped_plant_location_fraction_of_capacity_percent",
    "target_capped_plant_location_mean_ppfd_umol_m2_s",
    "raw_leaf_surface_flux_mean_ppfd_umol_m2_s",
    "raw_leaf_surface_flux_min_ppfd_umol_m2_s",
    "raw_leaf_surface_flux_p05_ppfd_umol_m2_s",
    "raw_leaf_surface_flux_median_ppfd_umol_m2_s",
    "raw_leaf_surface_flux_p95_ppfd_umol_m2_s",
    "raw_leaf_surface_flux_max_ppfd_umol_m2_s",
    "raw_leaf_surface_flux_mean_percent_of_target",
    "raw_leaf_surface_flux_min_percent_of_target",
    "raw_leaf_surface_flux_p05_percent_of_target",
    "raw_leaf_surface_flux_median_percent_of_target",
    "raw_leaf_surface_flux_p95_percent_of_target",
    "raw_leaf_surface_flux_max_percent_of_target",
    "raw_bucket_0_25_leaf_count",
    "raw_bucket_25_45_leaf_count",
    "raw_bucket_45_70_leaf_count",
    "raw_bucket_70_90_leaf_count",
    "raw_bucket_90_115_leaf_count",
    "raw_bucket_115_150_leaf_count",
    "raw_bucket_150_plus_leaf_count",
    "raw_bucket_0_25_leaf_percent",
    "raw_bucket_25_45_leaf_percent",
    "raw_bucket_45_70_leaf_percent",
    "raw_bucket_70_90_leaf_percent",
    "raw_bucket_90_115_leaf_percent",
    "raw_bucket_115_150_leaf_percent",
    "raw_bucket_150_plus_leaf_percent",
    "target_capped_plant_location_plant_to_plant_cv_percent",
    "note",
)

FSPM_CSV_SPECTRAL_HEADERS = (
    "fspm_spectral_transport_mode",
    "leaf_radiance_material_mode",
    "receiver_trace_count",
    "banded_transport_band_count",
    "banded_transport_active_trace_count",
    "band_scaling_basis",
    "scalar_flux_basis",
    "source_spectrum_basis",
    "leaf_material_profile_id",
    "leaf_material_profile_version",
    "leaf_material_weighting_basis",
    "leaf_material_source_spectrum_id",
    "leaf_material_source_spectrum_source",
    "spectral_absorption_optical_profile_id",
    "spectral_absorption_source_spectrum_basis",
    "spectral_absorption_scalar_flux_basis",
    "modeled_incident_par_ppfd_umol_m2_s",
    "modeled_incident_epar_ppfd_umol_m2_s",
    "target_capped_absorbed_par_ppfd",
    "target_capped_absorbed_epar_ppfd",
    "target_capped_absorbed_blue_ppfd",
    "target_capped_absorbed_green_ppfd",
    "target_capped_absorbed_orange_ppfd",
    "target_capped_absorbed_red_ppfd",
    "target_capped_absorbed_far_red_ppfd",
    "excess_absorbed_par_ppfd_above_target_cap",
    "excess_absorbed_epar_ppfd_above_target_cap",
    "target_capped_absorbed_par_fraction_of_raw",
    "target_capped_absorbed_epar_fraction_of_raw",
    "target_effective_absorbed_fraction",
    "over_target_absorbed_par_fraction_of_raw",
    "under_target_leaf_fraction",
    "in_target_leaf_fraction",
    "over_target_leaf_fraction",
    "modeled_absorbed_par_ppfd_umol_m2_s",
    "modeled_absorbed_epar_ppfd_umol_m2_s",
    "modeled_absorbed_blue_ppfd_umol_m2_s",
    "modeled_absorbed_green_ppfd_umol_m2_s",
    "modeled_absorbed_orange_ppfd_umol_m2_s",
    "modeled_absorbed_red_ppfd_umol_m2_s",
    "modeled_absorbed_far_red_ppfd_umol_m2_s",
    "modeled_absorbed_fraction_percent",
    "modeled_reflected_fraction_percent",
    "modeled_transmitted_fraction_percent",
    "banded_transport_band_summaries_json",
    "blue_pfd_umol_m2_s",
    "green_pfd_umol_m2_s",
    "red_pfd_umol_m2_s",
    "far_red_pfd_umol_m2_s",
    "blue_fraction_of_par_percent",
    "red_to_far_red_diagnostic",
    "phytochrome_pss_proxy",
    "blue_dose_mol_m2",
    "photosynthetic_light_response_mean",
    "photosynthetic_light_response_lower_tail",
    "photosynthetic_light_response_cv_percent",
    "nonuniformity_response_retention",
    "calibration_status",
)

FSPM_CSV_HEADERS = FSPM_CSV_SCALAR_HEADERS[:-1] + FSPM_CSV_SPECTRAL_HEADERS + ("note",)


def fspm_system_label(mode: str) -> str:
    if mode == MODE_SMD:
        return "Proposed LED System"
    if mode == MODE_COMPETITOR:
        return "Conventional LED System"
    if mode == MODE_HPS:
        return "1000W HPS System"
    return mode


def fspm_csv_filename(mode: str, run_id: str) -> str:
    safe_mode = _safe_filename_token(mode.lower().replace(" ", "_"))
    safe_run_id = _safe_filename_token(run_id)
    return f"fspm_summary_{safe_mode}_{safe_run_id}.csv"


def build_fspm_metrics_csv(
    workspace_root: Path,
    *,
    run_id: str,
    mode: str,
    system_label: str | None = None,
) -> str:
    panel = build_fspm_panel_metrics(workspace_root)
    if not panel:
        raise ValueError("FSPM artifact data is unavailable.")

    spectral_active = _panel_spectral_active(panel)
    row = _summary_row(
        panel,
        run_id=run_id,
        mode=mode,
        system_label=system_label or fspm_system_label(mode),
        spectral_active=spectral_active,
    )
    headers = _headers_for_export(spectral_active)
    handle = StringIO()
    writer = csv.DictWriter(
        handle,
        fieldnames=headers,
        extrasaction="ignore",
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerow(row)
    return handle.getvalue()


def _summary_row(
    panel: Mapping[str, Any],
    *,
    run_id: str,
    mode: str,
    system_label: str,
    spectral_active: bool,
) -> dict[str, str]:
    counts = _object(panel.get("counts"))
    absorption = _object(
        panel.get("incident_leaf_surface_flux") or panel.get("plant_surface_absorption")
    )
    spectral_absorption = _object(panel.get("modeled_spectral_absorption"))
    spectral = _object(panel.get("spectral_exposure"))
    response = _object(panel.get("photosynthetic_light_response_potential"))
    exposure = _object(panel.get("photoreceptor_exposure"))
    if not spectral_active:
        spectral_absorption = {}
        spectral = {}
        response = {}
        exposure = {}
    source = _primary_source(absorption, spectral, response, exposure)
    transport_source = _primary_source(spectral_absorption, absorption)

    pss = _object(exposure.get("phytochrome_pss_proxy"))
    dose = _object(exposure.get("blue_photon_dose"))
    raw_summary = _object(absorption.get("raw_leaf_surface_flux_summary"))
    raw_buckets = _raw_bucket_counts(absorption, raw_summary)
    note = _text(panel.get("limitations_note")) or (
        "Lighting-analysis input only; unvalidated response-potential scaffold, "
        "not biological-output prediction."
    )
    note = (
        f"{note} Raw receiver incident flux is physical receiver accounting, "
        "not target-equivalent PPFD classification. Target-capped spectral "
        "absorption columns are response-effective reporting metrics only; raw "
        "modeled spectral absorption columns are populated when "
        "plant_spectral_absorption.json is available."
    )
    target_ppfd = absorption.get("target_ppfd_umol_m2_s")
    leaf_area = counts.get("one_sided_leaf_area_m2")
    target_capacity = _product(target_ppfd, leaf_area)
    target_capped_incident = _first(
        absorption,
        "target_capped_incident_flux_total_umol_s",
        "target_capped_incident_total_flux_umol_s",
        "target_capped_flux_total_umol_s",
        "target_capped_total_flux_umol_s",
    )
    raw_incident = absorption.get("total_incident_photon_flux_umol_s")
    excess_incident = _first(
        absorption,
        "excess_incident_flux_above_target_umol_s",
        "excess_flux_above_target_umol_s",
    )
    deficit_to_target = _first(
        absorption,
        "deficit_to_target_incident_flux_umol_s",
        "under_target_deficit_umol_s",
    )

    leaf_count = counts.get("leaf_count")

    return _format_row(
        {
            "run_id": run_id,
            "mode": mode,
            "system_label": system_label,
            "method": source.get("method"),
            "artifact_schema": source.get("schema"),
            "fspm_spectral_transport_mode": transport_source.get(
                "fspm_spectral_transport_mode"
            ),
            "leaf_radiance_material_mode": transport_source.get(
                "leaf_radiance_material_mode"
            ),
            "receiver_trace_count": transport_source.get("receiver_trace_count"),
            "banded_transport_band_count": transport_source.get(
                "banded_transport_band_count"
            ),
            "banded_transport_active_trace_count": transport_source.get(
                "banded_transport_active_trace_count"
            ),
            "band_scaling_basis": transport_source.get("band_scaling_basis"),
            "scalar_flux_basis": transport_source.get("scalar_flux_basis"),
            "source_spectrum_basis": _first(
                transport_source,
                "source_spectrum_basis",
                "source_spectral_basis",
            ),
            "leaf_material_profile_id": transport_source.get(
                "leaf_material_profile_id"
            ),
            "leaf_material_profile_version": transport_source.get(
                "leaf_material_profile_version"
            ),
            "leaf_material_weighting_basis": transport_source.get(
                "leaf_material_weighting_basis"
            ),
            "leaf_material_source_spectrum_id": transport_source.get(
                "leaf_material_source_spectrum_id"
            ),
            "leaf_material_source_spectrum_source": transport_source.get(
                "leaf_material_source_spectrum_source"
            ),
            "baseline_ppfd_transport_basis": absorption.get(
                "baseline_ppfd_transport_basis"
            ),
            "baseline_ppfd_rgb_decode_method": absorption.get(
                "baseline_ppfd_rgb_decode_method"
            ),
            "baseline_source_channel_policy": absorption.get(
                "baseline_source_channel_policy"
            ),
            "ppfd_conversion_basis": absorption.get("ppfd_conversion_basis"),
            "photopic_luminance_weighting_avoided": absorption.get(
                "photopic_luminance_weighting_avoided"
            ),
            "uses_179_luminous_efficacy_factor": absorption.get(
                "uses_179_luminous_efficacy_factor"
            ),
            "uses_falsecolor_or_illuminance_conversion": absorption.get(
                "uses_falsecolor_or_illuminance_conversion"
            ),
            "target_ppfd_umol_m2_s": target_ppfd,
            "target_tolerance_umol_m2_s": absorption.get("target_tolerance_umol_m2_s"),
            "target_lower_threshold_umol_m2_s": absorption.get(
                "target_lower_threshold_umol_m2_s"
            ),
            "target_upper_threshold_umol_m2_s": absorption.get(
                "target_upper_threshold_umol_m2_s"
            ),
            "target_classification_basis": absorption.get("target_classification_basis"),
            "target_classification_source": absorption.get("target_classification_source"),
            "coverage_basis_label": _coverage_basis_label(absorption),
            "coverage_source_label": _coverage_source_label(
                absorption.get("target_classification_source")
            ),
            "plant_count": counts.get("plant_count"),
            "leaf_count": counts.get("leaf_count"),
            "receiver_sample_count": counts.get("receiver_sample_count"),
            "receiver_granularity": counts.get("receiver_granularity"),
            "receiver_samples_per_leaf": counts.get("receiver_samples_per_leaf"),
            "receiver_side_policy": counts.get("receiver_side_policy"),
            "receiver_rows_per_mesh_surface_row": counts.get(
                "receiver_rows_per_mesh_surface_row"
            ),
            "receiver_area_basis": counts.get("receiver_area_basis"),
            "receiver_surface_count": counts.get("surface_count"),
            "one_sided_leaf_area_m2": counts.get("one_sided_leaf_area_m2"),
            "under_lit_leaves": absorption.get("under_lit_leaf_count"),
            "target_range_leaves": absorption.get("target_range_leaf_count"),
            "over_lit_leaves": absorption.get("over_lit_leaf_count"),
            "under_lit_leaf_percent": _percent(absorption.get("under_lit_leaf_fraction")),
            "target_range_leaf_percent": _percent(
                absorption.get("target_range_leaf_fraction")
            ),
            "over_lit_leaf_percent": _percent(absorption.get("over_lit_leaf_fraction")),
            "under_lit_receiver_surfaces": absorption.get("under_lit_surface_count"),
            "target_range_receiver_surfaces": absorption.get(
                "target_range_surface_count"
            ),
            "over_lit_receiver_surfaces": absorption.get("over_lit_surface_count"),
            "under_lit_receiver_surface_percent": _percent(
                absorption.get("under_lit_surface_fraction")
            ),
            "target_range_receiver_surface_percent": _percent(
                absorption.get("target_range_surface_fraction")
            ),
            "over_lit_receiver_surface_percent": _percent(
                absorption.get("over_lit_surface_fraction")
            ),
            "plant_location_under_lit_leaves": absorption.get("under_lit_leaf_count"),
            "plant_location_target_range_leaves": absorption.get("target_range_leaf_count"),
            "plant_location_over_lit_leaves": absorption.get("over_lit_leaf_count"),
            "mean_plant_location_reference_ppfd_umol_m2_s": absorption.get(
                "target_classification_mean_ppfd_umol_m2_s"
            ),
            "lower_tail_plant_location_reference_ppfd_umol_m2_s": absorption.get(
                "lower_tail_target_classification_ppfd_umol_m2_s"
            ),
            "target_capped_plant_location_flux_total_umol_s": target_capped_incident,
            "plant_location_target_capacity_flux_umol_s": target_capacity,
            "raw_incident_flux_total_umol_s": raw_incident,
            "raw_leaf_surface_incident_flux_total_umol_s": raw_incident,
            "excess_above_target_plant_location_flux_umol_s": excess_incident,
            "excess_above_target_fraction_of_raw_percent": _ratio_percent(
                excess_incident,
                raw_incident,
            ),
            "deficit_to_target_plant_location_flux_umol_s": deficit_to_target,
            "deficit_to_target_capacity_percent": _ratio_percent(
                deficit_to_target,
                target_capacity,
            ),
            "target_capped_plant_location_fraction_of_raw_percent": _ratio_percent(
                target_capped_incident,
                raw_incident,
            ),
            "target_capped_plant_location_fraction_of_capacity_percent": _ratio_percent(
                target_capped_incident,
                target_capacity,
            ),
            "target_capped_plant_location_mean_ppfd_umol_m2_s": _first(
                absorption,
                "target_capped_incident_mean_flux_density_umol_m2_s",
                "target_capped_mean_flux_density_umol_m2_s",
            ),
            "raw_leaf_surface_flux_mean_ppfd_umol_m2_s": raw_summary.get("mean"),
            "raw_leaf_surface_flux_min_ppfd_umol_m2_s": raw_summary.get("min"),
            "raw_leaf_surface_flux_p05_ppfd_umol_m2_s": raw_summary.get("p05"),
            "raw_leaf_surface_flux_median_ppfd_umol_m2_s": raw_summary.get("median"),
            "raw_leaf_surface_flux_p95_ppfd_umol_m2_s": raw_summary.get("p95"),
            "raw_leaf_surface_flux_max_ppfd_umol_m2_s": raw_summary.get("max"),
            "raw_leaf_surface_flux_mean_percent_of_target": raw_summary.get(
                "mean_percent_of_target"
            ),
            "raw_leaf_surface_flux_min_percent_of_target": raw_summary.get(
                "min_percent_of_target"
            ),
            "raw_leaf_surface_flux_p05_percent_of_target": raw_summary.get(
                "p05_percent_of_target"
            ),
            "raw_leaf_surface_flux_median_percent_of_target": raw_summary.get(
                "median_percent_of_target"
            ),
            "raw_leaf_surface_flux_p95_percent_of_target": raw_summary.get(
                "p95_percent_of_target"
            ),
            "raw_leaf_surface_flux_max_percent_of_target": raw_summary.get(
                "max_percent_of_target"
            ),
            "raw_bucket_0_25_leaf_count": raw_buckets.get("0-25%"),
            "raw_bucket_25_45_leaf_count": raw_buckets.get("25-45%"),
            "raw_bucket_45_70_leaf_count": raw_buckets.get("45-70%"),
            "raw_bucket_70_90_leaf_count": raw_buckets.get("70-90%"),
            "raw_bucket_90_115_leaf_count": raw_buckets.get("90-115%"),
            "raw_bucket_115_150_leaf_count": raw_buckets.get("115-150%"),
            "raw_bucket_150_plus_leaf_count": raw_buckets.get("150%+"),
            "raw_bucket_0_25_leaf_percent": _ratio_percent(
                raw_buckets.get("0-25%"),
                leaf_count,
            ),
            "raw_bucket_25_45_leaf_percent": _ratio_percent(
                raw_buckets.get("25-45%"),
                leaf_count,
            ),
            "raw_bucket_45_70_leaf_percent": _ratio_percent(
                raw_buckets.get("45-70%"),
                leaf_count,
            ),
            "raw_bucket_70_90_leaf_percent": _ratio_percent(
                raw_buckets.get("70-90%"),
                leaf_count,
            ),
            "raw_bucket_90_115_leaf_percent": _ratio_percent(
                raw_buckets.get("90-115%"),
                leaf_count,
            ),
            "raw_bucket_115_150_leaf_percent": _ratio_percent(
                raw_buckets.get("115-150%"),
                leaf_count,
            ),
            "raw_bucket_150_plus_leaf_percent": _ratio_percent(
                raw_buckets.get("150%+"),
                leaf_count,
            ),
            "target_capped_plant_location_plant_to_plant_cv_percent": _percent(
                _first(
                    absorption,
                    "plant_to_plant_target_capped_incident_flux_cv",
                    "plant_to_plant_target_capped_flux_cv",
                )
            ),
            "spectral_absorption_optical_profile_id": spectral_absorption.get(
                "optical_profile_id"
            ),
            "spectral_absorption_source_spectrum_basis": spectral_absorption.get(
                "source_spectral_basis"
            ),
            "spectral_absorption_scalar_flux_basis": spectral_absorption.get(
                "scalar_flux_basis"
            ),
            "modeled_incident_par_ppfd_umol_m2_s": _first(
                spectral_absorption,
                "incident_par_ppfd_umol_m2_s",
                "scalar_incident_par_ppfd_umol_m2_s",
            ),
            "modeled_incident_epar_ppfd_umol_m2_s": spectral_absorption.get(
                "incident_epar_ppfd_umol_m2_s"
            ),
            "target_capped_absorbed_par_ppfd": spectral_absorption.get(
                "target_capped_absorbed_par_ppfd"
            ),
            "target_capped_absorbed_epar_ppfd": spectral_absorption.get(
                "target_capped_absorbed_epar_ppfd"
            ),
            "target_capped_absorbed_blue_ppfd": spectral_absorption.get(
                "target_capped_absorbed_blue_ppfd"
            ),
            "target_capped_absorbed_green_ppfd": spectral_absorption.get(
                "target_capped_absorbed_green_ppfd"
            ),
            "target_capped_absorbed_orange_ppfd": spectral_absorption.get(
                "target_capped_absorbed_orange_ppfd"
            ),
            "target_capped_absorbed_red_ppfd": spectral_absorption.get(
                "target_capped_absorbed_red_ppfd"
            ),
            "target_capped_absorbed_far_red_ppfd": spectral_absorption.get(
                "target_capped_absorbed_far_red_ppfd"
            ),
            "excess_absorbed_par_ppfd_above_target_cap": spectral_absorption.get(
                "excess_absorbed_par_ppfd_above_target_cap"
            ),
            "excess_absorbed_epar_ppfd_above_target_cap": spectral_absorption.get(
                "excess_absorbed_epar_ppfd_above_target_cap"
            ),
            "target_capped_absorbed_par_fraction_of_raw": spectral_absorption.get(
                "target_capped_absorbed_par_fraction_of_raw"
            ),
            "target_capped_absorbed_epar_fraction_of_raw": spectral_absorption.get(
                "target_capped_absorbed_epar_fraction_of_raw"
            ),
            "target_effective_absorbed_fraction": spectral_absorption.get(
                "target_effective_absorbed_fraction"
            ),
            "over_target_absorbed_par_fraction_of_raw": spectral_absorption.get(
                "over_target_absorbed_par_fraction_of_raw"
            ),
            "under_target_leaf_fraction": spectral_absorption.get(
                "under_target_leaf_fraction"
            ),
            "in_target_leaf_fraction": spectral_absorption.get(
                "in_target_leaf_fraction"
            ),
            "over_target_leaf_fraction": spectral_absorption.get(
                "over_target_leaf_fraction"
            ),
            "modeled_absorbed_par_ppfd_umol_m2_s": spectral_absorption.get(
                "absorbed_par_ppfd_umol_m2_s"
            ),
            "modeled_absorbed_epar_ppfd_umol_m2_s": spectral_absorption.get(
                "absorbed_epar_ppfd_umol_m2_s"
            ),
            "modeled_absorbed_blue_ppfd_umol_m2_s": spectral_absorption.get(
                "absorbed_blue_ppfd_umol_m2_s"
            ),
            "modeled_absorbed_green_ppfd_umol_m2_s": spectral_absorption.get(
                "absorbed_green_ppfd_umol_m2_s"
            ),
            "modeled_absorbed_orange_ppfd_umol_m2_s": spectral_absorption.get(
                "absorbed_orange_ppfd_umol_m2_s"
            ),
            "modeled_absorbed_red_ppfd_umol_m2_s": spectral_absorption.get(
                "absorbed_red_ppfd_umol_m2_s"
            ),
            "modeled_absorbed_far_red_ppfd_umol_m2_s": spectral_absorption.get(
                "absorbed_far_red_ppfd_umol_m2_s"
            ),
            "modeled_absorbed_fraction_percent": _percent(
                spectral_absorption.get("absorbed_fraction")
            ),
            "modeled_reflected_fraction_percent": _percent(
                spectral_absorption.get("reflected_fraction")
            ),
            "modeled_transmitted_fraction_percent": _percent(
                spectral_absorption.get("transmitted_fraction")
            ),
            "banded_transport_band_summaries_json": _banded_transport_bands_json(
                spectral_absorption
            ),
            "blue_pfd_umol_m2_s": exposure.get("mean_absorbed_blue_pfd_umol_m2_s"),
            "green_pfd_umol_m2_s": exposure.get("mean_absorbed_green_pfd_umol_m2_s"),
            "red_pfd_umol_m2_s": exposure.get("mean_absorbed_red_pfd_umol_m2_s"),
            "far_red_pfd_umol_m2_s": exposure.get(
                "mean_absorbed_far_red_pfd_umol_m2_s"
            ),
            "blue_fraction_of_par_percent": _percent(
                exposure.get("mean_absorbed_blue_fraction_of_par")
            ),
            "red_to_far_red_diagnostic": exposure.get(
                "mean_absorbed_red_to_far_red_ratio_diagnostic"
            ),
            "phytochrome_pss_proxy": pss.get("value"),
            "blue_dose_mol_m2": _umol_to_mol(dose.get("value_umol_m2")),
            "photosynthetic_light_response_mean": response.get(
                "area_weighted_mean_local_response_0_1"
            ),
            "photosynthetic_light_response_lower_tail": _first(
                response,
                "local_response_p10_0_1",
                "bottom_decile_area_weighted_response_0_1",
            ),
            "photosynthetic_light_response_cv_percent": _percent(
                response.get("plant_to_plant_photosynthetic_response_cv")
            ),
            "nonuniformity_response_retention": response.get(
                "nonuniformity_response_retention_0_1"
            ),
            "calibration_status": response.get("calibration_status"),
            "note": note,
        },
        headers=_headers_for_export(spectral_active),
    )


def _banded_transport_bands_json(spectral_absorption: Mapping[str, Any]) -> str | None:
    band_metadata = _band_map(spectral_absorption.get("banded_transport_bands"))
    band_summaries = _band_map(spectral_absorption.get("band_summaries"))
    band_ids = list(dict.fromkeys([*band_summaries, *band_metadata]))
    seen: set[str] = set()
    rows: list[dict[str, object]] = []
    for band_id in band_ids:
        if band_id in seen:
            continue
        seen.add(band_id)
        summary = band_summaries.get(band_id, {})
        metadata = band_metadata.get(band_id, {})
        rows.append(
            {
                "band_id": band_id,
                "wavelength_min_nm": _first_value(
                    summary.get("wavelength_min_nm"),
                    metadata.get("wavelength_min_nm"),
                ),
                "wavelength_max_nm": _first_value(
                    summary.get("wavelength_max_nm"),
                    metadata.get("wavelength_max_nm"),
                ),
                "source_photon_fraction_relative_to_par": _first_value(
                    summary.get("source_photon_fraction_relative_to_par"),
                    metadata.get("source_photon_fraction_relative_to_par"),
                ),
                "receiver_trace_required": _first_value(
                    summary.get("receiver_trace_required"),
                    metadata.get("receiver_trace_required"),
                ),
                "effective_reflectance": _first_value(
                    summary.get("effective_reflectance"),
                    metadata.get("effective_reflectance"),
                ),
                "effective_transmittance": _first_value(
                    summary.get("effective_transmittance"),
                    metadata.get("effective_transmittance"),
                ),
                "effective_absorptance": _first_value(
                    summary.get("effective_absorptance"),
                    metadata.get("effective_absorptance"),
                ),
                "incident_pfd_umol_m2_s": summary.get("incident_pfd_umol_m2_s"),
                "absorbed_pfd_umol_m2_s": summary.get("absorbed_pfd_umol_m2_s"),
                "reflected_pfd_umol_m2_s": summary.get("reflected_pfd_umol_m2_s"),
                "transmitted_pfd_umol_m2_s": summary.get(
                    "transmitted_pfd_umol_m2_s"
                ),
                "radiance_primitive": metadata.get("radiance_primitive"),
                "radiance_red": metadata.get("radiance_red"),
                "radiance_green": metadata.get("radiance_green"),
                "radiance_blue": metadata.get("radiance_blue"),
                "radiance_trans": metadata.get("radiance_trans"),
                "radiance_tspec": metadata.get("radiance_tspec"),
            }
        )
    if not rows:
        return None
    return json.dumps(rows, sort_keys=True, separators=(",", ":"))


def _band_map(value: object) -> dict[str, Mapping[str, Any]]:
    if not isinstance(value, list):
        return {}
    result: dict[str, Mapping[str, Any]] = {}
    for item in value:
        if not isinstance(item, Mapping):
            continue
        band_id = item.get("band_id")
        if band_id is not None:
            result[str(band_id)] = item
    return result


def _coverage_basis_label(absorption: Mapping[str, Any]) -> object:
    basis = _first(
        absorption,
        "target_classification_basis_label",
        "target_classification_basis",
        "target_basis_label",
        "target_basis",
    )
    if basis in {
        "canopy_plane_equivalent_incident_ppfd",
        "canopy-plane equivalent incident PPFD",
    }:
        return "plant_location_canopy_reference_ppfd"
    return basis


def _coverage_source_label(source: object) -> str:
    if source == "interpolated_runtime_ppfd_map":
        return "baseline PPFD map sampled at leaf XY positions"
    return str(source).replace("_", " ") if source is not None else ""


def _raw_bucket_counts(
    absorption: Mapping[str, Any],
    raw_summary: Mapping[str, Any],
) -> dict[str, object]:
    buckets = absorption.get("raw_leaf_surface_flux_bucket_counts")
    if not isinstance(buckets, list):
        buckets = raw_summary.get("bucket_counts")
    if not isinstance(buckets, list):
        return {}
    counts: dict[str, object] = {}
    for bucket in buckets:
        if not isinstance(bucket, Mapping):
            continue
        label = bucket.get("label")
        if label is not None:
            counts[str(label)] = bucket.get("leaf_count")
    return counts


def _spectral_active(*payloads: Mapping[str, Any]) -> bool:
    for payload in payloads:
        mode = str(payload.get("fspm_spectral_transport_mode") or "")
        if mode.startswith("banded"):
            return True
        if _finite(payload.get("banded_transport_band_count")) is not None:
            return True
        if _finite(payload.get("banded_transport_active_trace_count")) is not None:
            return True
        if isinstance(payload.get("band_summaries"), list) and payload.get(
            "band_summaries"
        ):
            return True
        if isinstance(payload.get("banded_transport_bands"), list) and payload.get(
            "banded_transport_bands"
        ):
            return True
        if isinstance(payload.get("band_totals"), Mapping) and payload.get(
            "band_totals"
        ):
            return True
    return False


def _panel_spectral_active(panel: Mapping[str, Any]) -> bool:
    return _spectral_active(
        _object(panel.get("modeled_spectral_absorption")),
        _object(panel.get("spectral_exposure")),
        _object(panel.get("photosynthetic_light_response_potential")),
        _object(panel.get("photoreceptor_exposure")),
    )


def _headers_for_export(spectral_active: bool) -> tuple[str, ...]:
    if spectral_active:
        return FSPM_CSV_HEADERS
    return FSPM_CSV_SCALAR_HEADERS


def _first_value(*values: object) -> object:
    for value in values:
        if value is not None:
            return value
    return None


def _format_row(
    values: Mapping[str, object],
    *,
    headers: tuple[str, ...],
) -> dict[str, str]:
    row: dict[str, str] = {}
    for header in headers:
        value = values.get(header)
        row[header] = _csv_number(value) if _finite(value) is not None else _text(value)
    return row


def _primary_source(*payloads: Mapping[str, Any]) -> Mapping[str, Any]:
    for payload in payloads:
        if payload.get("schema") or payload.get("method"):
            return payload
    return {}


def _safe_filename_token(value: object) -> str:
    text = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "-"
        for char in str(value or "run")
    ).strip("._-")
    return text[:80] or "run"


def _finite(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _csv_number(value: object) -> str:
    number = _finite(value)
    if number is None:
        return ""
    return f"{number:.12g}"


def _object(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _percent(fraction: object) -> float | None:
    number = _finite(fraction)
    return number * 100.0 if number is not None else None


def _product(left: object, right: object) -> float | None:
    left_number = _finite(left)
    right_number = _finite(right)
    if left_number is None or right_number is None:
        return None
    return left_number * right_number


def _ratio_percent(numerator: object, denominator: object) -> float | None:
    numerator_number = _finite(numerator)
    denominator_number = _finite(denominator)
    if numerator_number is None or denominator_number is None or denominator_number == 0.0:
        return None
    return numerator_number / denominator_number * 100.0


def _umol_to_mol(value: object) -> float | None:
    number = _finite(value)
    return number / 1_000_000.0 if number is not None else None


def _first(payload: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None
