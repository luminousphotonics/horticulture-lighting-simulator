"""CLI for deterministic Phase 27G-D5-A2 coefficient analysis."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.application.surface_flux_coefficient_analysis import (
    analyze_surface_flux_coefficients,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-analyze-surface-flux-d5",
        description=(
            "Authenticate and analyze a completed Phase 27G-D5-A1 v3 sweep "
            "without invoking Radiance."
        ),
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="completed, immutable D5-A1 v3 sweep directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="distinct absent directory for deterministic D5-A2 evidence",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        publication = analyze_surface_flux_coefficients(
            input_directory=arguments.input_dir,
            output_directory=arguments.output_dir,
        )
    except KeyboardInterrupt:
        print("D5-A2 analysis interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"D5-A2 analysis failed: {exc}", file=sys.stderr)
        return 1
    print(publication.completion_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
