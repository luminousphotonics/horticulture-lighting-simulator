#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from typing import Any, Mapping

from rad_rebuild.radiance.engine.photometry.smd_curve_model import (
    curve_model_manifest,
    validate_curve_model,
)
from rad_rebuild.radiance.engine.validation.results import (
    ValidationResult,
    emit_validation_result,
)


def _metric_float(metrics: Mapping[str, Any], key: str) -> float:
    return float(metrics[key])


def run_curve_model_validation() -> ValidationResult:
    manifest = curve_model_manifest()
    metrics = validate_curve_model()
    checks = {
        "positive_nominal_input_w": _metric_float(metrics, "nominal_input_w") > 0.0,
        "positive_nominal_output_umol_s": _metric_float(
            metrics, "nominal_output_umol_s"
        )
        > 0.0,
        "positive_wall_plug_ppe": _metric_float(metrics, "nominal_wall_plug_ppe") > 0.0,
        "positive_source_ppe": _metric_float(metrics, "nominal_source_ppe") > 0.0,
        "thermal_multiplier_in_unit_interval": 0.0
        < _metric_float(metrics, "thermal_multiplier")
        <= 1.0,
    }
    return ValidationResult(
        validator="smd_curve_model",
        passed=all(checks.values()),
        metrics={
            "manifest": manifest,
            "metrics": metrics,
            "white_nominal_current_ma": metrics["white_nominal_current_ma"],
            "red_nominal_current_ma": metrics["red_nominal_current_ma"],
            "nominal_input_w": metrics["nominal_input_w"],
            "nominal_led_supply_w": metrics["nominal_led_supply_w"],
            "nominal_source_umol_s": metrics["nominal_source_umol_s"],
            "nominal_output_umol_s": metrics["nominal_output_umol_s"],
            "nominal_source_ppe": metrics["nominal_source_ppe"],
            "nominal_wall_plug_ppe": metrics["nominal_wall_plug_ppe"],
            "thermal_multiplier": metrics["thermal_multiplier"],
        },
        checks=checks,
    )


def _emit_text(result: ValidationResult) -> None:
    manifest = result.metrics["manifest"]
    metrics = result.metrics["metrics"]
    print("SMD curve-model validation")
    print(f"  model              : {manifest['curve_model_version']}")
    print(
        f"  white nominal I    : {metrics['white_nominal_current_ma']:.2f} mA/package"
    )
    print(f"  red nominal I      : {metrics['red_nominal_current_ma']:.2f} mA/package")
    print(f"  nominal input      : {metrics['nominal_input_w']:.2f} W/module")
    print(f"  nominal LED power  : {metrics['nominal_led_supply_w']:.2f} W/module")
    print(
        f"  source photons     : {metrics['nominal_source_umol_s']:.2f} umol/s/module"
    )
    print(
        f"  output photons     : {metrics['nominal_output_umol_s']:.2f} umol/s/module"
    )
    print(f"  source PPE         : {metrics['nominal_source_ppe']:.4f} umol/J")
    print(f"  wall-plug PPE      : {metrics['nominal_wall_plug_ppe']:.4f} umol/J")
    print(f"  thermal multiplier : {metrics['thermal_multiplier']:.4f}")
    print(
        f"  white SPD efficacy : {metrics['white_spectral_par_umol_per_radiant_w']:.4f} umol/J_radiant"
    )
    print(
        f"  red SPD efficacy   : {metrics['red_spectral_par_umol_per_radiant_w']:.4f} umol/J_radiant"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the SMD curve model.")
    parser.add_argument("--format", choices=("json", "text"), default="json")
    args = parser.parse_args(argv)

    result = run_curve_model_validation()
    if args.format == "text":
        _emit_text(result)
    else:
        emit_validation_result(result, stream=sys.stdout)
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
