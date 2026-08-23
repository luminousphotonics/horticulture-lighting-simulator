"""CLI for deterministic Phase 27G-D5-B1 candidate-resource promotion."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.application.surface_flux_optimized_calibration_promotion import (
    promote_optimized_surface_flux_calibration,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-promote-surface-flux-d5",
        description=(
            "Authenticate completed Phase 27G-D5-A2 evidence and atomically "
            "publish a non-packaged optimized coefficient candidate without "
            "invoking Radiance."
        ),
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="completed, immutable D5-A2 coefficient-analysis directory",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="distinct absent directory for the two-file D5-B1 candidate",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        publication = promote_optimized_surface_flux_calibration(
            input_directory=arguments.input_dir,
            output_directory=arguments.output_dir,
        )
    except KeyboardInterrupt:
        print("D5-B1 promotion interrupted.", file=sys.stderr)
        return 130
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"D5-B1 promotion failed: {exc}", file=sys.stderr)
        return 1
    print(publication.manifest_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
