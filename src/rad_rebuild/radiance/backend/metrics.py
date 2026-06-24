from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import HTTPException

from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_SMD
from rad_rebuild.radiance.engine.plants.absorption import PHOTON_ABSORPTION_SCAFFOLD_SCHEMA
from rad_rebuild.radiance.engine.plants.artifacts import PLANT_ABSORPTION_SURFACES_FILENAME
from rad_rebuild.radiance.engine.plants.surface_flux import (
    PLANT_SURFACE_FLUX_FILENAME,
    PLANT_SURFACE_FLUX_SCHEMA,
    RADIANCE_RECEIVER_METHOD,
)
from rad_rebuild.radiance.engine.plants.spectral import (
    PLANT_SPECTRAL_RESPONSE_FILENAME,
    PLANT_SPECTRAL_RESPONSE_SCHEMA,
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
        work_root / "runtime_state" / PLANT_SPECTRAL_RESPONSE_FILENAME,
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
        "centroids/normals against the unblocked baseline lighting field."
        if status == "computed" and method == RADIANCE_RECEIVER_METHOD
        else (
            "Surface flux artifact present. Current values are proxy values until "
            "the Radiance per-surface receiver method is reviewed."
        )
    )

    return {
        "schema": payload.get("schema"),
        "schema_version": payload.get("schema_version"),
        "status": status,
        "method": method,
        "source_artifact": f"runtime_state/{PLANT_SURFACE_FLUX_FILENAME}",
        "source_ppfd_map": payload.get("source_ppfd_map"),
        "plant_count": payload.get("plant_count"),
        "leaf_count": payload.get("leaf_count"),
        "surface_count": payload.get("surface_count"),
        "one_sided_leaf_area_m2": payload.get("one_sided_leaf_area_m2"),
        "total_incident_photon_flux_umol_s": payload.get("total_incident_photon_flux_umol_s"),
        "total_absorbed_photon_flux_umol_s": payload.get("total_absorbed_photon_flux_umol_s"),
        "mean_absorbed_fraction_of_incident": payload.get("mean_absorbed_fraction_of_incident"),
        "plant_to_plant_absorbed_photon_flux_cv": payload.get("plant_to_plant_absorbed_photon_flux_cv"),
        "under_lit_leaf_count": payload.get("under_lit_leaf_count"),
        "over_lit_leaf_count": payload.get("over_lit_leaf_count"),
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
            "Surface registry only. Absorbed photon flux values are not computed "
            "until a Radiance per-surface flux mapping method is reviewed."
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
        metrics["plant_photon_absorption"] = plant_photon_absorption

    plant_spectral_response = _load_plant_spectral_response_summary(workspace_root)
    if plant_spectral_response is not None:
        metrics["plant_spectral_response"] = plant_spectral_response

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
