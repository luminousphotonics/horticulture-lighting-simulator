from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TypeGuard

from fastapi import HTTPException

from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_SMD

from .artifacts import _layout_file_for_mode, _load_json_file
from .env import (
    HPS_MODE_LABEL,
    _aligned_dims_ft,
    _canonicalize_mode_request,
    _normalize_mode,
)
from .models import ElectricalCostRequest, RadianceRunRequest

try:
    from rad_rebuild.radiance.engine.simulation.precomputed_dataset import canonical_competitor_layout
except Exception as e:  # pragma: no cover
    raise RuntimeError(f"Failed to import backend cost dependencies: {e}") from e


SPYDR_FIXTURE_COST_USD = 750.0
HPS_FIXTURE_COST_USD = 385.0


def _is_number_like(value: object) -> TypeGuard[str | int | float]:
    return isinstance(value, (str, int, float))


def _int_from_json(value: object) -> int:
    if not _is_number_like(value):
        return 0
    try:
        return int(value or 0)
    except Exception:
        return 0


def _float_from_json(value: object) -> float:
    if not _is_number_like(value):
        return 0.0
    return float(value or 0.0)


def _string_keyed_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    return {str(key): item for key, item in value.items()}


def build_cost_estimate(
    req: RadianceRunRequest, workspace_root: Path
) -> dict[str, object] | None:
    mode = _normalize_mode(req.mode)
    length_ft, width_ft = _aligned_dims_ft(req.length_ft, req.width_ft)
    area_sqft = float(length_ft * width_ft)

    if mode == MODE_SMD:
        total_cost = area_sqft * 50.0
        return {
            "system_label": "Our LED System",
            "pricing_basis": "square_foot_flat_rate",
            "total_cost_usd": total_cost,
            "area_sqft": area_sqft,
            "cost_per_sqft_usd": (total_cost / area_sqft) if area_sqft > 0 else None,
            "control_rate_sqft_usd": 1.5,
            "control_included": True,
            "line_items": [
                {
                    "label": "Installed system cost",
                    "detail": f"{area_sqft:.0f} sq ft at $50.00 / sq ft",
                    "value_usd": total_cost,
                },
                {
                    "label": "Lighting control system",
                    "detail": "Integrated zonal controls remain included within the flat $50 / sq ft system price.",
                    "value_text": "Included",
                }
            ],
            "summary": "Estimated at a flat installed system rate of $50 per square foot, with advanced zonal controls already included.",
        }

    layout_path = _layout_file_for_mode(mode, workspace_root)
    layout = _load_json_file(layout_path) if layout_path else None
    layout_label = None
    fixture_count = 0
    if isinstance(layout, dict):
        layout_label = layout.get("layout_label") or layout.get("label")
        fixtures = layout.get("fixtures")
        if isinstance(fixtures, list):
            fixture_count = len(fixtures)
        else:
            nx = _int_from_json(layout.get("nx", 0))
            ny = _int_from_json(layout.get("ny", 0))
            fixture_count = max(0, nx * ny)

    if mode == MODE_COMPETITOR:
        fixture_cost = fixture_count * SPYDR_FIXTURE_COST_USD
        control_cost = area_sqft * 0.55
        total_cost = fixture_cost + control_cost
        summary = "Estimated at $750 per whole conventional LED fixture plus lighting controls at $0.55 / sq ft."
        if layout_label:
            summary = f"{layout_label}. {summary}"
        return {
            "system_label": "Conventional LED System",
            "pricing_basis": "per_fixture",
            "layout_label": str(layout_label) if layout_label else None,
            "total_cost_usd": total_cost,
            "area_sqft": area_sqft,
            "cost_per_sqft_usd": (total_cost / area_sqft) if area_sqft > 0 else None,
            "fixture_count": fixture_count,
            "control_rate_sqft_usd": 0.55,
            "line_items": [
                {
                    "label": "Conventional fixtures",
                    "detail": f"{fixture_count} fixtures at $750 each",
                    "amount_usd": fixture_cost,
                },
                {
                    "label": "Lighting control system",
                    "detail": f"{area_sqft:.0f} sq ft at $0.55 / sq ft",
                    "amount_usd": control_cost,
                },
            ],
            "summary": summary,
        }

    if mode == HPS_MODE_LABEL:
        fixture_cost = fixture_count * HPS_FIXTURE_COST_USD
        control_cost = area_sqft * 0.30
        total_cost = fixture_cost + control_cost
        summary = "Estimated at $385 per 1000W HPS fixture plus lighting controls at $0.30 / sq ft."
        if layout_label:
            summary = f"{layout_label}. {summary}"
        return {
            "system_label": HPS_MODE_LABEL,
            "pricing_basis": "per_fixture",
            "layout_label": str(layout_label) if layout_label else None,
            "total_cost_usd": total_cost,
            "area_sqft": area_sqft,
            "cost_per_sqft_usd": (total_cost / area_sqft) if area_sqft > 0 else None,
            "fixture_count": fixture_count,
            "control_rate_sqft_usd": 0.30,
            "line_items": [
                {
                    "label": "1000W HPS fixtures",
                    "detail": f"{fixture_count} fixtures at $385 each",
                    "amount_usd": fixture_cost,
                },
                {
                    "label": "Lighting control system",
                    "detail": f"{area_sqft:.0f} sq ft at $0.30 / sq ft",
                    "amount_usd": control_cost,
                },
            ],
            "summary": summary,
        }

    return None


def build_electrical_estimate_payload(
    req: ElectricalCostRequest,
    session_id: str,
    ensure_request_workspace: Callable[[RadianceRunRequest, str], Path],
    metrics_payload_for_request: Callable[[RadianceRunRequest, Path], dict[str, object]],
) -> dict[str, object]:
    mode = _normalize_mode(req.mode)
    competitor_layout = canonical_competitor_layout(req.competitor_layout)
    utility_rate = float(req.utility_rate_kwh or 0.0)
    if utility_rate <= 0:
        raise HTTPException(status_code=400, detail="Utility rate must be greater than zero.")
    if not req.stages:
        raise HTTPException(status_code=400, detail="At least one growth stage is required.")

    baseline_target_ppfd = float(req.target_ppfd or 0.0)
    if baseline_target_ppfd <= 0:
        raise HTTPException(status_code=400, detail="Rendered target PPFD must be greater than zero.")

    baseline_req = _canonicalize_mode_request(
        RadianceRunRequest(
            action="metrics",
            mode=mode,
            execution_mode=req.execution_mode,
            sim_mode=req.sim_mode,
            length_ft=req.length_ft,
            width_ft=req.width_ft,
            target_ppfd=baseline_target_ppfd,
            peak_capping_enabled=req.peak_capping_enabled,
            competitor_layout=competitor_layout,
            hps_coverage_ft=req.hps_coverage_ft,
            hps_ies_variant=req.hps_ies_variant,
            basis_backend=req.basis_backend,
        )
    )
    try:
        baseline_workspace_root = ensure_request_workspace(baseline_req, session_id)
    except RuntimeError as e:
        raise HTTPException(status_code=404, detail=str(e))

    stage_results: list[dict[str, object]] = []
    total_kwh = 0.0
    total_cost = 0.0
    fixed_output_mode = mode == HPS_MODE_LABEL

    for stage in req.stages:
        days = float(stage.days or 0.0)
        hours_per_day = float(stage.hours_per_day or 0.0)
        avg_ppfd = float(stage.avg_ppfd or 0.0)
        if days <= 0 or hours_per_day <= 0 or avg_ppfd <= 0:
            raise HTTPException(status_code=400, detail=f"Stage '{stage.name}' must have positive days, hours, and PPFD.")
        if (not fixed_output_mode) and avg_ppfd > baseline_target_ppfd:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Stage '{stage.name}' requests {avg_ppfd:.0f} PPFD, which exceeds the rendered baseline "
                    f"target of {baseline_target_ppfd:.0f} PPFD. Re-run the simulator at or above your highest "
                    "stage target before estimating cycle cost."
                ),
            )

        stage_req = _canonicalize_mode_request(
            RadianceRunRequest(
                action="metrics",
                mode=mode,
                execution_mode=req.execution_mode,
                sim_mode=req.sim_mode,
                length_ft=req.length_ft,
                width_ft=req.width_ft,
                target_ppfd=avg_ppfd,
                peak_capping_enabled=req.peak_capping_enabled,
                competitor_layout=competitor_layout,
                hps_coverage_ft=req.hps_coverage_ft,
                hps_ies_variant=req.hps_ies_variant,
                basis_backend=req.basis_backend,
            )
        )
        payload = metrics_payload_for_request(stage_req, baseline_workspace_root)
        metrics = _string_keyed_dict(payload.get("metrics"))
        if metrics is None:
            raise HTTPException(
                status_code=500,
                detail=f"Could not determine stage metrics for '{stage.name}'.",
            )

        if fixed_output_mode:
            stage_watts = _float_from_json(metrics.get("watts_in"))
            simulated_mean_ppfd = _float_from_json(metrics.get("mean"))
        elif req.peak_capping_enabled:
            stage_watts = _float_from_json(
                metrics.get("watts_at_cap") or metrics.get("watts_in")
            )
            simulated_mean_ppfd = _float_from_json(
                metrics.get("mean_at_cap") or metrics.get("mean")
            )
        else:
            stage_watts = _float_from_json(metrics.get("watts_in"))
            simulated_mean_ppfd = _float_from_json(metrics.get("mean"))
        if stage_watts <= 0:
            raise HTTPException(status_code=500, detail=f"Could not determine stage watts for '{stage.name}'.")

        stage_hours = days * hours_per_day
        stage_kwh = (stage_watts * stage_hours) / 1000.0
        stage_cost = stage_kwh * utility_rate
        total_kwh += stage_kwh
        total_cost += stage_cost

        stage_results.append(
            {
                "name": stage.name,
                "days": days,
                "hours_per_day": hours_per_day,
                "avg_ppfd": avg_ppfd,
                "simulated_mean_ppfd": simulated_mean_ppfd,
                "stage_watts": stage_watts,
                "stage_hours": stage_hours,
                "stage_kwh": stage_kwh,
                "stage_cost_usd": stage_cost,
                "usable_efficacy_umol_j": (
                    metrics.get("capped_deuc_elec", metrics.get("deuc_elec", metrics.get("deuc")))
                    if req.peak_capping_enabled
                    else metrics.get("full_run_deuc_elec", metrics.get("deuc_elec", metrics.get("deuc")))
                ),
                "watts_basis": (
                    "fixed_output"
                    if fixed_output_mode
                    else ("cap_adjusted" if req.peak_capping_enabled and "watts_at_cap" in metrics else "input")
                ),
                "fixed_output": fixed_output_mode,
            }
        )

    notes = []
    if fixed_output_mode:
        notes.append("1000W HPS is modeled at fixed output, so changing stage PPFD targets does not reduce fixture wattage in this estimate.")
    else:
        if req.peak_capping_enabled:
            notes.append(
                "Dimmable modes reuse the exact rendered layout as the baseline field, then apply each stage PPFD target "
                "as a hotspot-limited dim cap. That yields stage-specific simulated watts without rerunning the layout."
            )
            notes.append(
                "Usable efficacy is shown as capped_deuc_elec when available (ppf_at_cap / watts_at_cap); "
                "full_run_deuc_elec remains available separately for uncapped output."
            )
        else:
            notes.append(
                "Dimmable modes are estimated against the uncapped rendered field by default, so stage wattage and "
                "simulated mean PPFD come from the full-output mean-target solution rather than hotspot-capped metrics."
            )

    return {
        "ok": True,
        "mode": mode,
        "utility_rate_kwh": utility_rate,
        "cycle_kwh": total_kwh,
        "cycle_cost_usd": total_cost,
        "stages": stage_results,
        "notes": notes,
    }
