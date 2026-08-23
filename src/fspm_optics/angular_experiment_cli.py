"""Command-line entry point for the Stage A completed-aperture experiment."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.application.angular_experiment import (
    AngularExperimentConfig,
    AngularExperimentError,
    execute_angular_experiment,
    plan_angular_experiment,
)
from fspm_optics.application.angular_republish import (
    AngularRepublishError,
    load_completed_experiment_config,
    republish_characterization_only,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fspm-optics-angular-experiment",
        description=(
            "Plan or execute the six-case Stage A-only completed-aperture "
            "angular-sensitivity experiment using one complete equal-module "
            "scene trace and uniform module dimming per case."
        ),
    )
    subparsers = parser.add_subparsers(dest="action", required=True)
    for action in ("plan", "execute"):
        command = subparsers.add_parser(
            action,
            help=(
                "materialize the deterministic experiment plan"
                if action == "plan"
                else (
                    "characterize sources and execute all six complete-scene "
                    "uniform Stage A cases"
                )
            ),
        )
        command.add_argument(
            "--output-dir",
            type=Path,
            required=True,
            help="Dedicated experiment output directory (required).",
        )
        command.add_argument(
            "--target-ppfd",
            type=float,
            required=True,
            help="Common target mean PPFD in umol m^-2 s^-1 (required).",
        )
        command.add_argument(
            "--quality",
            choices=("direct", "standard", "quality", "rigorous"),
            default="standard",
            help=(
                "Radiance quality preset shared by characterization and all "
                "six cases (default: standard)."
            ),
        )
        command.add_argument(
            "--threads",
            type=int,
            default=1,
            help="Radiance worker count shared by characterization and all cases.",
        )
        if action == "execute":
            command.add_argument(
                "--resume",
                action="store_true",
                help=(
                    "Reuse only completed, identity- and hash-validated artifacts; "
                    "partial artifacts fail closed."
                ),
            )
    republish = subparsers.add_parser(
        "republish-characterization",
        help=(
            "authenticate completed cases, rerun fixed-source far-field "
            "characterization only, and republish the polar artifacts"
        ),
    )
    republish.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help=(
            "Existing completed experiment directory; target, quality, and "
            "threads are authenticated from its plan."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    arguments = parser.parse_args(argv)
    try:
        if arguments.action == "republish-characterization":
            config = load_completed_experiment_config(arguments.output_dir)
            result = republish_characterization_only(config)
        else:
            config = AngularExperimentConfig(
                output_directory=arguments.output_dir,
                target_ppfd_umol_m2_s=arguments.target_ppfd,
                quality=arguments.quality,
                nthreads=arguments.threads,
            )
            if arguments.action == "plan":
                result = plan_angular_experiment(config)
            else:
                result = execute_angular_experiment(
                    config,
                    resume=bool(arguments.resume),
                )
    except (
        AngularExperimentError,
        AngularRepublishError,
        OSError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"Angular experiment failed: {exc}", file=sys.stderr)
        return 1
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
