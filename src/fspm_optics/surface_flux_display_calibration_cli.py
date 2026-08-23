"""CLI for deterministic Phase 27G-D5-C3 display-calibration packaging."""

from __future__ import annotations

import argparse
from pathlib import Path

from fspm_optics.application.surface_flux_display_calibration import (
    SurfaceFluxDisplayCalibrationConfig,
    generate_surface_flux_display_calibration,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-generate-surface-flux-display-calibration",
        description=(
            "Authenticate pinned D5-C2 evidence and atomically package the "
            "D5-C3 display-only Float64 coefficient resource."
        ),
    )
    parser.add_argument("--c2-input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    publication = generate_surface_flux_display_calibration(
        SurfaceFluxDisplayCalibrationConfig(
            c2_input_directory=args.c2_input_dir,
            output_directory=args.output_dir,
        )
    )
    payload = publication.manifest["coefficient_payload"]
    print(
        f"Published {publication.manifest['resource_id']} to {args.output_dir} "
        f"({payload['value_count']} Float64 values; {payload['byte_length']} bytes)."
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
