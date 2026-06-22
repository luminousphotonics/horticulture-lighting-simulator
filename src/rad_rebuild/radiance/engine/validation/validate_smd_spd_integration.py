#!/usr/bin/env python3
from __future__ import annotations

import math
import os
import re
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Callable, Mapping
from typing import Any, TypeAlias

import numpy as np

from rad_rebuild.radiance.engine.photometry.ppfd_metrics import (
    compute_ppfd_metrics,
    metric_float,
)
from rad_rebuild.radiance.engine.photometry.smd_curve_model import validate_curve_model
from rad_rebuild.radiance.engine.validation.results import (
    ValidationResult,
    emit_validation_result,
)
from rad_rebuild.radiance.paths import RADIANCE_OUTPUT_ROOT, RADIANCE_SCRIPTS_ROOT

ROOT = RADIANCE_OUTPUT_ROOT
SUMMARY_PATH = ROOT / "runtime_state" / "smd_summary.txt"
PPFD_PATH = ROOT / "ppfd_map.txt"

# Bandit B404/B603 waiver: this validator invokes fixed repository scripts with
# argv lists and no shell. Owner: Phase 12.5C.2. Review before 2026-09-30.

MetricPayload: TypeAlias = dict[str, float]
SceneRunner: TypeAlias = Callable[[str, dict[str, str]], MetricPayload]
CurveValidator: TypeAlias = Callable[[], Mapping[str, Any]]

BASELINE: dict[str, dict[str, object]] = {
    "nominal": {
        "nominal_source_umol_s": 266.34360599999997,
        "nominal_output_umol_s": 245.03611751999998,
    },
    "square_12x12_direct": {
        "env": {"LENGTH_FT": "12", "WIDTH_FT": "12"},
        "metrics": {
            "mean": 718.9125826530612,
            "min": 528.83375,
            "max": 800.00695,
            "p05": 618.132725,
            "p95": 794.973175,
            "ppf_out": 9617.639676871775,
            "cv_percent": 8.710250129308092,
        },
    },
    "rect_16x12_direct": {
        "env": {"LENGTH_FT": "16", "WIDTH_FT": "12"},
        "metrics": {
            "mean": 605.081433734694,
            "min": 98.06816,
            "max": 801.30845,
            "p05": 135.7346,
            "p95": 794.271075,
            "ppf_out": 10793.069691170233,
            "cv_percent": 35.450122268143005,
        },
    },
}

TOLERANCES = {
    "nominal_source_umol_s": 1e-9,
    "nominal_output_umol_s": 1e-9,
    "mean": 1e-1,
    "min": 1.5,
    "max": 1.5,
    "p05": 1.5,
    "p95": 1.5,
    "ppf_out": 2.5e-1,
    "cv_percent": 1e-2,
}


def _bash_path() -> str:
    bash = shutil.which("bash")
    if bash is None:
        raise FileNotFoundError("bash executable was not found")
    return bash


def _parse_summary_power() -> tuple[float | None, float | None]:
    watts = None
    emitted = None
    for line in SUMMARY_PATH.read_text(encoding="utf-8").splitlines():
        text = line.strip().lower()
        if "total electrical input" in text:
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*w", text)
            if match:
                watts = float(match.group(1))
        if "total emitted photons" in text:
            match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*[µu]mol/s", text)
            if match:
                emitted = float(match.group(1))
    return watts, emitted


def _load_ppfd_values() -> np.ndarray[Any, np.dtype[np.float64]]:
    values: list[float] = []
    for line in PPFD_PATH.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            values.append(float(parts[3]))
    if not values:
        raise ValueError(f"No PPFD samples found in {PPFD_PATH}")
    return np.asarray(values, dtype=np.float64)


def _run_scene(name: str, extra_env: dict[str, str]) -> MetricPayload:
    env = os.environ.copy()
    env.update(
        {
            "MODE": "direct",
            "SYM": "0",
            "NTHREADS": "1",
            "LOG_CAP_METRICS": "0",
        }
    )
    env.update(extra_env)
    subprocess.run(  # nosec B603
        [_bash_path(), str(RADIANCE_SCRIPTS_ROOT / "run_simulation_smd.sh")],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    watts, emitted = _parse_summary_power()
    area_m2 = float(extra_env["LENGTH_FT"]) * float(extra_env["WIDTH_FT"]) * 0.09290304
    metrics = compute_ppfd_metrics(
        _load_ppfd_values(),
        canopy_area_m2=area_m2,
        total_input_watts=watts,
        emitted_ppf_umol_s=emitted,
        legacy_metrics=True,
    )
    legacy = metrics.get("legacy") or {}
    if not isinstance(legacy, dict):
        raise TypeError("legacy metrics payload must be a dictionary")
    return {
        "mean": metric_float(metrics, "mean"),
        "min": metric_float(metrics, "min"),
        "max": metric_float(metrics, "max"),
        "p05": metric_float(metrics, "p05"),
        "p95": metric_float(metrics, "p95"),
        "ppf_out": metric_float(metrics, "ppf_out"),
        "cv_percent": float(legacy["cv_percent"]),
    }


def _float_from_mapping(payload: Mapping[str, Any], key: str) -> float:
    value = payload[key]
    if not isinstance(value, int | float):
        raise TypeError(f"{key} must be numeric")
    return float(value)


def _baseline_metrics(name: str) -> Mapping[str, float]:
    metrics = BASELINE[name]["metrics"]
    if not isinstance(metrics, dict):
        raise TypeError(f"{name} metrics baseline must be a dictionary")
    return {str(key): float(value) for key, value in metrics.items()}


def _baseline_env(name: str) -> dict[str, str]:
    env = BASELINE[name]["env"]
    if not isinstance(env, dict):
        raise TypeError(f"{name} env baseline must be a dictionary")
    return {str(key): str(value) for key, value in env.items()}


def _metric_check(value: float, baseline: float, tolerance: float) -> bool:
    return math.isfinite(value) and abs(value - baseline) <= tolerance


def run_smd_spd_integration_validation(
    *,
    curve_model: CurveValidator = validate_curve_model,
    run_scene: SceneRunner = _run_scene,
) -> ValidationResult:
    nominal = curve_model()
    nominal_metrics = {
        "nominal_source_umol_s": _float_from_mapping(nominal, "nominal_source_umol_s"),
        "nominal_output_umol_s": _float_from_mapping(nominal, "nominal_output_umol_s"),
    }
    checks: dict[str, bool] = {}
    deltas: dict[str, float] = {}
    for key, value in nominal_metrics.items():
        baseline = _float_from_mapping(BASELINE["nominal"], key)
        deltas[f"nominal.{key}"] = value - baseline
        checks[f"nominal.{key}"] = _metric_check(value, baseline, TOLERANCES[key])

    scene_results: dict[str, MetricPayload] = {}
    for name in BASELINE:
        if name == "nominal":
            continue
        result = run_scene(name, _baseline_env(name))
        scene_results[name] = result
        for key, value in result.items():
            baseline = _baseline_metrics(name)[key]
            deltas[f"{name}.{key}"] = value - baseline
            checks[f"{name}.{key}"] = _metric_check(value, baseline, TOLERANCES[key])

    metrics: dict[str, object] = {
        "nominal": nominal_metrics,
        "scenes": scene_results,
        "deltas": deltas,
        "tolerances": dict(TOLERANCES),
    }
    return ValidationResult(
        validator="smd_spd_integration",
        passed=all(checks.values()),
        metrics=metrics,
        checks=checks,
    )


def main() -> int:
    try:
        result = run_smd_spd_integration_validation()
    except (OSError, subprocess.CalledProcessError, ValueError, TypeError) as exc:
        result = ValidationResult(
            validator="smd_spd_integration",
            passed=False,
            metrics={"error": str(exc)},
            checks={"validator_completed": False},
        )
    emit_validation_result(result, stream=sys.stdout)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
