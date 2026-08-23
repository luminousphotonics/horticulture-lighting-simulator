"""CLI for the fixed Phase 27G-D5-A1 optimized calibration sweep."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.application.surface_flux_calibration import (
    SurfaceFluxCalibrationError,
)
from fspm_optics.application.surface_flux_recalibration import (
    D5_DEFAULT_OUTPUT_DIRECTORY,
    SurfaceFluxRecalibrationConfig,
    build_surface_flux_recalibration_plan,
    format_surface_flux_recalibration_json,
    run_surface_flux_recalibration,
)
from fspm_optics.radiance.commands import LOCAL_DEFAULT_NTHREADS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-calibrate-surface-flux-d5",
        description=(
            "Plan, execute, or exactly resume the fixed Phase 27G-D5-A1 "
            "optimized-profile surface-flux sweep."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=D5_DEFAULT_OUTPUT_DIRECTORY,
        help=f"D5 runtime output directory (default: {D5_DEFAULT_OUTPUT_DIRECTORY})",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=LOCAL_DEFAULT_NTHREADS,
        help=f"Radiance worker count (default: {LOCAL_DEFAULT_NTHREADS})",
    )
    parser.add_argument("--oconv", default="oconv", help="oconv executable")
    parser.add_argument("--rtrace", default="rtrace", help="rtrace executable")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan",
        action="store_true",
        help=(
            "print the deterministic fixed plan without Radiance discovery, "
            "execution, or output writes"
        ),
    )
    mode.add_argument(
        "--resume",
        action="store_true",
        help=(
            "authenticate every existing job artifact and execute only missing "
            "jobs from an exactly matching D5 configuration"
        ),
    )
    return parser


def config_from_namespace(
    arguments: argparse.Namespace,
) -> SurfaceFluxRecalibrationConfig:
    return SurfaceFluxRecalibrationConfig(
        output_directory=arguments.output_dir,
        threads=arguments.threads,
        oconv_command=arguments.oconv,
        rtrace_command=arguments.rtrace,
        resume=arguments.resume,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        config = config_from_namespace(arguments)
    except ValueError as exc:
        parser.error(str(exc))
    if arguments.plan:
        try:
            plan = build_surface_flux_recalibration_plan(config)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"D5 plan failed: {exc}", file=sys.stderr)
            return 1
        print(format_surface_flux_recalibration_json(plan), end="")
        return 0
    try:
        publication = run_surface_flux_recalibration(
            config,
            event_sink=lambda message, _data=None: print(message, flush=True),
        )
    except KeyboardInterrupt:
        print(
            "D5 sweep interrupted; the active temporary job was removed and "
            "completed jobs remain resumable.",
            file=sys.stderr,
        )
        return 130
    except SurfaceFluxCalibrationError as exc:
        print(f"D5 sweep failed: {exc}", file=sys.stderr)
        return 1
    print(publication.completion_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "config_from_namespace", "main"]
