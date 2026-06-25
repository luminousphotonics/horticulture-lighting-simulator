from __future__ import annotations

import csv
from io import StringIO
import math
from pathlib import Path
from typing import Any, Mapping

from rad_rebuild.radiance.assembly.fspm_panel import build_fspm_panel_metrics
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD

FSPM_CSV_HEADERS = (
    "run_id",
    "mode",
    "system_label",
    "method",
    "artifact_schema",
    "target_ppfd_umol_m2_s",
    "target_tolerance_umol_m2_s",
    "target_lower_threshold_umol_m2_s",
    "target_upper_threshold_umol_m2_s",
    "target_classification_basis",
    "target_classification_source",
    "plant_count",
    "leaf_count",
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
    "target_capped_incident_flux_total_umol_s",
    "raw_incident_flux_total_umol_s",
    "raw_absorbed_flux_total_umol_s",
    "absorbed_fraction_percent",
    "excess_incident_flux_above_target_umol_s",
    "deficit_to_target_incident_flux_umol_s",
    "target_capped_incident_mean_flux_density_umol_m2_s",
    "raw_mean_flux_density_umol_m2_s",
    "lower_tail_target_classification_ppfd_umol_m2_s",
    "lower_tail_raw_flux_density_umol_m2_s",
    "raw_plant_to_plant_absorption_cv_percent",
    "target_capped_incident_plant_to_plant_cv_percent",
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
    "note",
)


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

    row = _summary_row(
        panel,
        run_id=run_id,
        mode=mode,
        system_label=system_label or fspm_system_label(mode),
    )
    handle = StringIO()
    writer = csv.DictWriter(handle, fieldnames=FSPM_CSV_HEADERS, lineterminator="\n")
    writer.writeheader()
    writer.writerow(row)
    return handle.getvalue()


def _summary_row(
    panel: Mapping[str, Any],
    *,
    run_id: str,
    mode: str,
    system_label: str,
) -> dict[str, str]:
    counts = _object(panel.get("counts"))
    absorption = _object(panel.get("plant_surface_absorption"))
    spectral = _object(panel.get("spectral_exposure"))
    response = _object(panel.get("photosynthetic_light_response_potential"))
    exposure = _object(panel.get("photoreceptor_exposure"))
    source = _primary_source(absorption, spectral, response, exposure)

    pss = _object(exposure.get("phytochrome_pss_proxy"))
    dose = _object(exposure.get("blue_photon_dose"))
    note = _text(panel.get("limitations_note")) or (
        "Lighting-analysis input only; unvalidated response-potential scaffold, "
        "not biological-output prediction."
    )

    return _format_row(
        {
            "run_id": run_id,
            "mode": mode,
            "system_label": system_label,
            "method": source.get("method"),
            "artifact_schema": source.get("schema"),
            "target_ppfd_umol_m2_s": absorption.get("target_ppfd_umol_m2_s"),
            "target_tolerance_umol_m2_s": absorption.get("target_tolerance_umol_m2_s"),
            "target_lower_threshold_umol_m2_s": absorption.get(
                "target_lower_threshold_umol_m2_s"
            ),
            "target_upper_threshold_umol_m2_s": absorption.get(
                "target_upper_threshold_umol_m2_s"
            ),
            "target_classification_basis": absorption.get("target_classification_basis"),
            "target_classification_source": absorption.get("target_classification_source"),
            "plant_count": counts.get("plant_count"),
            "leaf_count": counts.get("leaf_count"),
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
            "target_capped_incident_flux_total_umol_s": _first(
                absorption,
                "target_capped_incident_flux_total_umol_s",
                "target_capped_incident_total_flux_umol_s",
                "target_capped_flux_total_umol_s",
                "target_capped_total_flux_umol_s",
            ),
            "raw_incident_flux_total_umol_s": absorption.get(
                "total_incident_photon_flux_umol_s"
            ),
            "raw_absorbed_flux_total_umol_s": absorption.get(
                "total_absorbed_photon_flux_umol_s"
            ),
            "absorbed_fraction_percent": _percent(
                absorption.get("mean_absorbed_fraction_of_incident")
            ),
            "excess_incident_flux_above_target_umol_s": _first(
                absorption,
                "excess_incident_flux_above_target_umol_s",
                "excess_flux_above_target_umol_s",
            ),
            "deficit_to_target_incident_flux_umol_s": _first(
                absorption,
                "deficit_to_target_incident_flux_umol_s",
                "under_target_deficit_umol_s",
            ),
            "target_capped_incident_mean_flux_density_umol_m2_s": _first(
                absorption,
                "target_capped_incident_mean_flux_density_umol_m2_s",
                "target_capped_mean_flux_density_umol_m2_s",
            ),
            "raw_mean_flux_density_umol_m2_s": absorption.get(
                "raw_mean_flux_density_umol_m2_s"
            ),
            "lower_tail_target_classification_ppfd_umol_m2_s": absorption.get(
                "lower_tail_target_classification_ppfd_umol_m2_s"
            ),
            "lower_tail_raw_flux_density_umol_m2_s": absorption.get(
                "lower_tail_raw_flux_density_umol_m2_s"
            ),
            "raw_plant_to_plant_absorption_cv_percent": _percent(
                absorption.get("plant_to_plant_absorbed_photon_flux_cv")
            ),
            "target_capped_incident_plant_to_plant_cv_percent": _percent(
                _first(
                    absorption,
                    "plant_to_plant_target_capped_incident_flux_cv",
                    "plant_to_plant_target_capped_flux_cv",
                )
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
        }
    )


def _format_row(values: Mapping[str, object]) -> dict[str, str]:
    row: dict[str, str] = {}
    for header in FSPM_CSV_HEADERS:
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


def _umol_to_mol(value: object) -> float | None:
    number = _finite(value)
    return number / 1_000_000.0 if number is not None else None


def _first(payload: Mapping[str, Any], *keys: str) -> object:
    for key in keys:
        value = payload.get(key)
        if value is not None:
            return value
    return None
