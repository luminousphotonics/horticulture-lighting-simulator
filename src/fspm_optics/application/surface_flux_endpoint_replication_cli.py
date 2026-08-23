"""CLI for the fixed Phase 27G-D5-C1 endpoint replication."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.application.surface_flux_endpoint_replication import (
    D5_C1_DEFAULT_A1_INPUT_DIRECTORY,
    D5_C1_DEFAULT_A2_INPUT_DIRECTORY,
    D5_C1_DEFAULT_OUTPUT_DIRECTORY,
    SurfaceFluxEndpointReplicationConfig,
    SurfaceFluxEndpointReplicationError,
    build_surface_flux_endpoint_replication_plan,
    format_surface_flux_endpoint_replication_json,
    run_surface_flux_endpoint_replication,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-run-surface-flux-d5-c1",
        description=(
            "Authenticate D5-A1/A2 and plan, execute, or exactly resume the "
            "fixed Quality-first D5-C1 endpoint-only Stage B replication."
        ),
    )
    parser.add_argument(
        "--a1-input-dir",
        type=Path,
        default=D5_C1_DEFAULT_A1_INPUT_DIRECTORY,
        help=(
            "completed immutable D5-A1 v3 input "
            f"(default: {D5_C1_DEFAULT_A1_INPUT_DIRECTORY})"
        ),
    )
    parser.add_argument(
        "--a2-input-dir",
        type=Path,
        default=D5_C1_DEFAULT_A2_INPUT_DIRECTORY,
        help=(
            "completed immutable D5-A2 v1 input "
            f"(default: {D5_C1_DEFAULT_A2_INPUT_DIRECTORY})"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=D5_C1_DEFAULT_OUTPUT_DIRECTORY,
        help=(
            "absent D5-C1 output or exact resumable directory "
            f"(default: {D5_C1_DEFAULT_OUTPUT_DIRECTORY})"
        ),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--plan-only",
        action="store_true",
        help=(
            "authenticate A1/A2 and print strict JSON without executable "
            "discovery, native execution, or output creation"
        ),
    )
    mode.add_argument(
        "--resume",
        action="store_true",
        help=(
            "reauthenticate committed jobs and execute only missing jobs from "
            "an exactly matching partial output"
        ),
    )
    return parser


def config_from_namespace(
    arguments: argparse.Namespace,
) -> SurfaceFluxEndpointReplicationConfig:
    return SurfaceFluxEndpointReplicationConfig(
        a1_input_directory=arguments.a1_input_dir,
        a2_input_directory=arguments.a2_input_dir,
        output_directory=arguments.output_dir,
        resume=arguments.resume,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        config = config_from_namespace(arguments)
        if arguments.plan_only:
            plan = build_surface_flux_endpoint_replication_plan(config)
            print(format_surface_flux_endpoint_replication_json(plan), end="")
            return 0
        publication = run_surface_flux_endpoint_replication(
            config,
            event_sink=lambda message, _data=None: print(message, flush=True),
        )
    except KeyboardInterrupt:
        print(
            "D5-C1 interrupted; the active staged job was removed and committed "
            "jobs remain resumable.",
            file=sys.stderr,
        )
        return 130
    except (OSError, ValueError, SurfaceFluxEndpointReplicationError) as exc:
        print(f"D5-C1 failed: {exc}", file=sys.stderr)
        return 1
    print(publication.outcome_path)
    return 2 if publication.status == "blocked" else 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "config_from_namespace", "main"]
