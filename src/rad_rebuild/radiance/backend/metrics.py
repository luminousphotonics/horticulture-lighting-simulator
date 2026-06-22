from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import HTTPException

from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_SMD

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
    ]


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
