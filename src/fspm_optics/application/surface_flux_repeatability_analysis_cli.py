"""CLI for Phase 27G-D5-C2 endpoint-repeatability analysis."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.application.surface_flux_repeatability_analysis import (
    D5_C2_DEFAULT_A1_INPUT_DIRECTORY,
    D5_C2_DEFAULT_A2_INPUT_DIRECTORY,
    D5_C2_DEFAULT_C1_INPUT_DIRECTORY,
    D5_C2_DEFAULT_OUTPUT_DIRECTORY,
    SurfaceFluxRepeatabilityAnalysisConfig,
    SurfaceFluxRepeatabilityAnalysisError,
    analyze_surface_flux_endpoint_repeatability,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-analyze-surface-flux-d5-c2",
        description=(
            "Authenticate completed D5-A1/A2/C1 evidence and publish the "
            "descriptive Quality/Standard endpoint-repeatability analysis."
        ),
    )
    parser.add_argument(
        "--a1-input-dir",
        type=Path,
        default=D5_C2_DEFAULT_A1_INPUT_DIRECTORY,
        help=f"completed immutable D5-A1 input (default: {D5_C2_DEFAULT_A1_INPUT_DIRECTORY})",
    )
    parser.add_argument(
        "--a2-input-dir",
        type=Path,
        default=D5_C2_DEFAULT_A2_INPUT_DIRECTORY,
        help=f"completed immutable D5-A2 input (default: {D5_C2_DEFAULT_A2_INPUT_DIRECTORY})",
    )
    parser.add_argument(
        "--c1-input-dir",
        type=Path,
        default=D5_C2_DEFAULT_C1_INPUT_DIRECTORY,
        help=f"completed immutable D5-C1 input (default: {D5_C2_DEFAULT_C1_INPUT_DIRECTORY})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=D5_C2_DEFAULT_OUTPUT_DIRECTORY,
        help=f"initially absent D5-C2 output (default: {D5_C2_DEFAULT_OUTPUT_DIRECTORY})",
    )
    return parser


def config_from_namespace(
    arguments: argparse.Namespace,
) -> SurfaceFluxRepeatabilityAnalysisConfig:
    return SurfaceFluxRepeatabilityAnalysisConfig(
        a1_input_directory=arguments.a1_input_dir,
        a2_input_directory=arguments.a2_input_dir,
        c1_input_directory=arguments.c1_input_dir,
        output_directory=arguments.output_dir,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        publication = analyze_surface_flux_endpoint_repeatability(
            config_from_namespace(arguments),
            event_sink=lambda message, _data=None: print(message, flush=True),
        )
    except KeyboardInterrupt:
        print(
            "D5-C2 interrupted before atomic publication completed.",
            file=sys.stderr,
        )
        return 130
    except (OSError, TypeError, ValueError, SurfaceFluxRepeatabilityAnalysisError) as exc:
        print(f"D5-C2 failed: {exc}", file=sys.stderr)
        return 1
    print(publication.report_path)
    print(publication.completion_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "config_from_namespace", "main"]
