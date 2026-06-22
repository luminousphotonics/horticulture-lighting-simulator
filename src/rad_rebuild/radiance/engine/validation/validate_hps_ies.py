#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

from rad_rebuild.radiance.engine.validation.results import (
    ValidationResult,
    emit_validation_result,
)
from rad_rebuild.radiance.paths import RADIANCE_OUTPUT_ROOT, RADIANCE_SCRIPTS_ROOT

ROOT = RADIANCE_OUTPUT_ROOT
OUT_DIR = ROOT / "runtime_state"
HPS_RAD = OUT_DIR / "GLH-KARMA-8-HPS1000.rad"

# Bandit B404/B603 waiver: this validator invokes fixed repository scripts with
# argv lists and no shell. Owner: Phase 12.5C.2. Review before 2026-09-30.

FloatArray: TypeAlias = npt.NDArray[np.float64]
CasePayload: TypeAlias = dict[str, object]
AuditPayload: TypeAlias = dict[str, object]
RunCase: TypeAlias = Callable[[str], CasePayload]
EmissionAudit: TypeAlias = Callable[[], AuditPayload]
LoggingAudit: TypeAlias = Callable[[], AuditPayload]


def _bash_path() -> str:
    bash = shutil.which("bash")
    if bash is None:
        raise FileNotFoundError("bash executable was not found")
    return bash


def _parse_kv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or "=" not in text:
            continue
        key, value = text.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def _nearest_value(
    x: FloatArray, y: FloatArray, ppfd: FloatArray, target_x: float, target_y: float
) -> float:
    index = int(np.argmin((x - target_x) ** 2 + (y - target_y) ** 2))
    return float(ppfd[index])


def _load_ppfd_columns(path: Path) -> tuple[FloatArray, FloatArray, FloatArray]:
    data = np.loadtxt(path, dtype=np.float64)
    if data.ndim == 1:
        data = data.reshape(1, -1)
    return data[:, 0], data[:, 1], data[:, 3]


def _layout_summary(path: Path) -> tuple[int, object]:
    layout = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(layout, dict):
        return 0, None
    fixtures = layout.get("fixtures", [])
    fixture_count = len(fixtures) if isinstance(fixtures, list) else 0
    return fixture_count, layout.get("profile")


def _ratio_or_nan(numerator: float, denominator: float) -> float:
    return numerator / denominator if denominator > 0 else float("nan")


def _run_case(
    name: str, *, length_ft: float, width_ft: float, coverage_ft: float, hps_z_m: float
) -> CasePayload:
    env = os.environ.copy()
    env.setdefault("RADIANCE_USE_DOCKER", "0")
    env.update(
        {
            "MODE": "instant",
            "AUTO_DIM": "0",
            "HPS_IES_VARIANT": "karma",
            "EFF_SCALE": "1.0",
            "LENGTH_FT": f"{length_ft:g}",
            "WIDTH_FT": f"{width_ft:g}",
            "ALIGN_LONG_AXIS_X": "1",
            "HPS_COVERAGE_FT": f"{coverage_ft:g}",
            "HPS_Z_M": f"{hps_z_m:g}",
            "MARGIN_IN": "1",
        }
    )
    subprocess.check_call(  # nosec B603
        [_bash_path(), str(RADIANCE_SCRIPTS_ROOT / "run_simulation_hps.sh")],
        cwd=ROOT,
        env=env,
    )

    x, y, ppfd = _load_ppfd_columns(ROOT / "ppfd_map.txt")
    x_min, x_max = float(np.min(x)), float(np.max(x))
    y_min, y_max = float(np.min(y)), float(np.max(y))
    center = _nearest_value(x, y, ppfd, 0.0, 0.0)
    corners = [
        _nearest_value(x, y, ppfd, x_min, y_min),
        _nearest_value(x, y, ppfd, x_min, y_max),
        _nearest_value(x, y, ppfd, x_max, y_min),
        _nearest_value(x, y, ppfd, x_max, y_max),
    ]
    edge_mids = [
        _nearest_value(x, y, ppfd, 0.0, y_min),
        _nearest_value(x, y, ppfd, 0.0, y_max),
        _nearest_value(x, y, ppfd, x_min, 0.0),
        _nearest_value(x, y, ppfd, x_max, 0.0),
    ]
    fixture_count, layout_profile = _layout_summary(OUT_DIR / "hps_layout.json")
    power = _parse_kv(OUT_DIR / "hps_power.txt")
    mean = float(np.mean(ppfd))
    corner_mean = float(np.mean(np.asarray(corners, dtype=np.float64)))
    return {
        "name": name,
        "length_ft": length_ft,
        "width_ft": width_ft,
        "coverage_ft": coverage_ft,
        "hps_z_m": hps_z_m,
        "fixture_count": fixture_count,
        "mean_ppfd": mean,
        "min_ppfd": float(np.min(ppfd)),
        "max_ppfd": float(np.max(ppfd)),
        "cv_percent": _ratio_or_nan(float(np.std(ppfd) * 100.0), mean),
        "center_ppfd": center,
        "corner_mean_ppfd": corner_mean,
        "edge_mid_mean_ppfd": float(np.mean(np.asarray(edge_mids, dtype=np.float64))),
        "peak_over_mean": _ratio_or_nan(float(np.max(ppfd)), mean),
        "corner_over_center": _ratio_or_nan(corner_mean, center),
        "layout_profile": layout_profile,
        "model_label": power.get("model_label"),
        "pre_normalization_fixture_ppf_umol_s": float(
            power.get("pre_normalization_fixture_ppf_umol_s", "0") or 0.0
        ),
        "default_fixture_anchor_umol_s": float(
            power.get("default_fixture_anchor_umol_s", "0") or 0.0
        ),
        "effective_aperture_length_m": float(
            power.get("effective_aperture_length_m", "0") or 0.0
        ),
        "effective_aperture_width_m": float(
            power.get("effective_aperture_width_m", "0") or 0.0
        ),
    }


def _emission_audit() -> AuditPayload:
    rad_text = HPS_RAD.read_text(encoding="utf-8")
    lines = rad_text.splitlines()
    polygon_headers = [line for line in lines if " polygon " in line]
    sphere_headers = [line for line in lines if " sphere " in line]
    flatcorr = any("flatcorr" in line for line in lines)
    return {
        "rad_file": str(HPS_RAD),
        "polygon_count": len(polygon_headers),
        "sphere_count": len(sphere_headers),
        "uses_flatcorr": flatcorr,
        "single_polygon_emitter": len(polygon_headers) == 1,
        "no_sphere_source": len(sphere_headers) == 0,
        "no_top_or_side_emitters": len(polygon_headers) == 1
        and len(sphere_headers) == 0,
    }


def _logging_audit() -> AuditPayload:
    summary_text = (OUT_DIR / "hps_summary.txt").read_text(encoding="utf-8")
    power = _parse_kv(OUT_DIR / "hps_power.txt")
    required_summary_tokens = [
        "ies_lumens_per_lamp_lm=",
        "ies_input_watts=",
        "spd_umol_per_lumen=",
        "pre_normalization_fixture_ppf_umol_s=",
        "default_fixture_anchor_umol_s=",
        "final_scale_multiplier=",
        "effective_aperture_length_m=",
        "effective_aperture_width_m=",
    ]
    required_power_keys = [
        "ies_lumens_per_lamp_lm",
        "ies_total_luminaire_lumens_lm",
        "ies_input_watts",
        "spd_umol_per_lumen",
        "pre_normalization_fixture_ppf_umol_s",
        "default_fixture_anchor_umol_s",
        "fixture_anchor_authority",
        "final_scale_multiplier",
        "effective_aperture_length_m",
        "effective_aperture_width_m",
    ]
    return {
        "required_summary_tokens_present": all(
            token in summary_text for token in required_summary_tokens
        ),
        "required_power_keys_present": all(key in power for key in required_power_keys),
        "summary_excerpt": summary_text.splitlines()[:14],
    }


def _float_metric(case: Mapping[str, object], key: str) -> float:
    value = case[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{key} must be numeric")
    return float(value)


def _bool_metric(payload: Mapping[str, object], key: str) -> bool:
    return bool(payload.get(key))


def _default_cases(run_case: Callable[..., CasePayload]) -> list[CasePayload]:
    return [
        run_case(
            "single_low", length_ft=8.0, width_ft=8.0, coverage_ft=5.0, hps_z_m=0.76
        ),
        run_case(
            "single_high", length_ft=8.0, width_ft=8.0, coverage_ft=5.0, hps_z_m=1.22
        ),
        run_case(
            "multi_overlap",
            length_ft=16.0,
            width_ft=16.0,
            coverage_ft=4.0,
            hps_z_m=1.07,
        ),
    ]


def run_hps_ies_validation(
    *,
    run_case: Callable[..., CasePayload] = _run_case,
    emission_audit: EmissionAudit = _emission_audit,
    logging_audit: LoggingAudit = _logging_audit,
) -> ValidationResult:
    cases = _default_cases(run_case)
    by_name = {str(case["name"]): case for case in cases}
    single_low = by_name["single_low"]
    single_high = by_name["single_high"]
    multi = by_name["multi_overlap"]

    emission = emission_audit()
    logging_payload = logging_audit()

    checks = {
        "no_pathological_point_source_hotspot": _float_metric(
            single_low, "peak_over_mean"
        )
        < 3.1
        and _float_metric(single_low, "corner_over_center") > 0.14,
        "broad_single_fixture_spread": _float_metric(single_high, "corner_over_center")
        > 0.24,
        "sensible_multi_fixture_overlap": _float_metric(multi, "cv_percent")
        < _float_metric(single_low, "cv_percent")
        and _float_metric(multi, "corner_over_center") > 0.45,
        "height_sensitivity_is_sensible": _float_metric(single_high, "peak_over_mean")
        < _float_metric(single_low, "peak_over_mean")
        and _float_metric(single_high, "corner_over_center")
        > _float_metric(single_low, "corner_over_center"),
        "emission_audit_passed": _bool_metric(emission, "single_polygon_emitter")
        and _bool_metric(emission, "no_sphere_source")
        and _bool_metric(emission, "uses_flatcorr"),
        "logging_audit_passed": _bool_metric(
            logging_payload, "required_summary_tokens_present"
        )
        and _bool_metric(logging_payload, "required_power_keys_present"),
    }
    metrics: dict[str, object] = {
        "profile": "glh_karma_8_hps1000_research_primary_v1",
        "cases": {str(case["name"]): case for case in cases},
        "emission_audit": emission,
        "logging_audit": logging_payload,
    }
    return ValidationResult(
        validator="hps_ies",
        passed=all(checks.values()),
        metrics=metrics,
        checks=checks,
    )


def main() -> int:
    try:
        result = run_hps_ies_validation()
    except (OSError, subprocess.CalledProcessError, ValueError, TypeError) as exc:
        result = ValidationResult(
            validator="hps_ies",
            passed=False,
            metrics={"error": str(exc)},
            checks={"validator_completed": False},
        )
    emit_validation_result(result, stream=sys.stdout)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
