"""Command-line interface for the neutral surface-flux calibration experiment."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.application.surface_flux_calibration import (
    DEFAULT_OUTPUT_DIRECTORY,
    DEFAULT_REFERENCE_LEVELS,
    SurfaceFluxCalibrationConfig,
    SurfaceFluxCalibrationError,
    run_surface_flux_calibration,
)
from fspm_optics.radiance.commands import LOCAL_DEFAULT_NTHREADS
from fspm_optics.radiance.options import RADIANCE_QUALITY_NAMES


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-calibrate-surface-flux",
        description=(
            "Run the system-neutral upper-hemisphere juvenile surface-flux "
            "calibration sweep."
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIRECTORY,
        help=(
            "experimental output directory (default: "
            f"{DEFAULT_OUTPUT_DIRECTORY})"
        ),
    )
    parser.add_argument(
        "--room-ft",
        type=float,
        nargs=2,
        metavar=("LENGTH", "WIDTH"),
        default=(10.0, 10.0),
        help="room footprint used for the reference grid and juvenile placement",
    )
    parser.add_argument(
        "--quality",
        choices=RADIANCE_QUALITY_NAMES,
        default="standard",
        help="existing Radiance quality name (default: standard)",
    )
    parser.add_argument(
        "--reference-levels",
        type=float,
        nargs="+",
        metavar="PPFD",
        default=DEFAULT_REFERENCE_LEVELS,
        help="strictly increasing reference levels (default: 250 375 500 625 750)",
    )
    parser.add_argument(
        "--verification-level",
        type=float,
        help="one primary level to repeat at a higher quality",
    )
    parser.add_argument(
        "--verification-quality",
        choices=RADIANCE_QUALITY_NAMES,
        help="quality for the optional verification (default when used: rigorous)",
    )
    parser.add_argument(
        "--reference-grid",
        type=int,
        nargs=2,
        metavar=("X_POINTS", "Y_POINTS"),
        default=(21, 21),
        help="horizontal reference-plane grid dimensions (default: 21 21)",
    )
    parser.add_argument(
        "--reference-plane-z-m",
        type=float,
        default=0.005,
        help="reference plane elevation in meters (default: 0.005)",
    )
    parser.add_argument(
        "--reference-inset-m",
        type=float,
        default=0.005,
        help="reference grid inset from the requested footprint (default: 0.005)",
    )
    parser.add_argument(
        "--threads",
        type=int,
        default=LOCAL_DEFAULT_NTHREADS,
        help=f"Radiance worker count (default: {LOCAL_DEFAULT_NTHREADS})",
    )
    parser.add_argument("--oconv", default="oconv", help="oconv executable")
    parser.add_argument("--rtrace", default="rtrace", help="rtrace executable")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="validate and resume an exactly matching interrupted experiment",
    )
    return parser


def config_from_namespace(
    arguments: argparse.Namespace,
) -> SurfaceFluxCalibrationConfig:
    return SurfaceFluxCalibrationConfig(
        output_directory=arguments.output_dir,
        room_length_ft=arguments.room_ft[0],
        room_width_ft=arguments.room_ft[1],
        reference_plane_z_m=arguments.reference_plane_z_m,
        reference_grid_x=arguments.reference_grid[0],
        reference_grid_y=arguments.reference_grid[1],
        reference_inset_m=arguments.reference_inset_m,
        quality=arguments.quality,
        reference_levels_umol_m2_s=tuple(arguments.reference_levels),
        verification_level_umol_m2_s=arguments.verification_level,
        verification_quality=arguments.verification_quality,
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
    try:
        publication = run_surface_flux_calibration(
            config,
            event_sink=lambda message, _data=None: print(message, flush=True),
        )
    except KeyboardInterrupt:
        print("Calibration interrupted; active staging artifacts were removed.", file=sys.stderr)
        return 130
    except SurfaceFluxCalibrationError as exc:
        print(f"Calibration failed: {exc}", file=sys.stderr)
        return 1
    print(publication.report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "config_from_namespace", "main"]
