#!/usr/bin/env python3
"""Run the isolated Proposed source-response closure diagnostic."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fspm_optics.diagnostics.proposed_source_response import (  # noqa: E402
    ProposedSourceResponseError,
    execute,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--theta-step-deg", type=float, default=0.5)
    parser.add_argument("--phi-step-deg", type=float, default=5.0)
    parser.add_argument("--distance-m", type=float, default=20.0)
    parser.add_argument("--rflux-samples", type=int, default=50_000)
    arguments = parser.parse_args()
    try:
        report = execute(
            arguments.output_dir,
            theta_step_deg=arguments.theta_step_deg,
            phi_step_deg=arguments.phi_step_deg,
            distance_m=arguments.distance_m,
            rflux_samples=arguments.rflux_samples,
        )
    except (OSError, RuntimeError, ValueError, ProposedSourceResponseError) as exc:
        print(f"Proposed source-response diagnostic failed: {exc}", file=sys.stderr)
        return 1
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
