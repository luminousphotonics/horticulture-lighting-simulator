"""Thin CLI for the measurement-only receiver-placement diagnostic."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.diagnostics.receiver_placement import (
    ReceiverPlacementDiagnosticError,
    build_receiver_placement_diagnostic,
    format_receiver_placement_diagnostic,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-diagnose-receiver-placement",
        description=(
            "Measure the frozen juvenile Rex receiver layout and deterministic "
            "cell-aligned 4x4 repartitioning candidates without changing production."
        ),
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional JSON output path; default is stdout.",
    )
    parser.add_argument(
        "--compact",
        action="store_true",
        help="Emit compact canonical JSON instead of two-space-indented JSON.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        payload = build_receiver_placement_diagnostic()
        text = format_receiver_placement_diagnostic(
            payload,
            indent=None if arguments.compact else 2,
        )
        if arguments.output is None:
            sys.stdout.write(text)
        else:
            arguments.output.write_text(text, encoding="utf-8")
    except (OSError, ReceiverPlacementDiagnosticError, ValueError) as exc:
        print(f"Receiver-placement diagnostic failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]
