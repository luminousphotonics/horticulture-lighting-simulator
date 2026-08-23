"""Command-line entry point for external juvenile viewer publication."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Sequence

from fspm_optics.plants import plan_natural_fit_layout_from_feet
from fspm_optics.viewer.publish import publish_run_viewer


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m fspm_optics.viewer",
        description="Publish one run-specific juvenile Rex scientific viewer.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Absolute external path for the new self-contained viewer bundle.",
    )
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--system-id",
        required=True,
        choices=("proposed", "conventional", "hps"),
    )
    parser.add_argument("--room-length-ft", required=True, type=float)
    parser.add_argument("--room-width-ft", required=True, type=float)
    parser.add_argument(
        "--natural-fit-artifact",
        required=True,
        type=Path,
        help="Authoritative natural_fit_layout.json for this run.",
    )
    parser.add_argument(
        "--layout-artifact",
        required=True,
        type=Path,
        help="Authoritative system fixture-layout JSON object for this run.",
    )
    arguments = parser.parse_args(argv)
    natural_fit = plan_natural_fit_layout_from_feet(
        arguments.room_length_ft, arguments.room_width_ft
    )
    natural_fit_bytes = arguments.natural_fit_artifact.read_bytes()
    if json.loads(natural_fit_bytes) != natural_fit.to_payload():
        parser.error("Natural-fit artifact does not match the requested room.")
    layout_identity = json.loads(arguments.layout_artifact.read_bytes())
    if not isinstance(layout_identity, dict):
        parser.error("Fixture layout artifact must be a JSON object.")
    output = publish_run_viewer(
        arguments.output,
        run_id=arguments.run_id,
        system_id=arguments.system_id,
        requested_length_ft=arguments.room_length_ft,
        requested_width_ft=arguments.room_width_ft,
        natural_fit=natural_fit,
        natural_fit_artifact_sha256=hashlib.sha256(natural_fit_bytes).hexdigest(),
        layout_identity=layout_identity,
    )
    print(
        json.dumps(
            {
                "output": str(output),
                "profile_id": "rex_juvenile_preheading_12leaf_v1",
                "run_id": arguments.run_id,
                "status": "published",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
