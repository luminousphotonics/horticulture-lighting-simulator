#!/usr/bin/env python3
"""Compare old and corrected matched Direct source-response cases."""

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
    compare_corrected_direct_runs,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--old-report", type=Path, required=True)
    parser.add_argument("--corrected-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        output = compare_corrected_direct_runs(
            arguments.old_report,
            arguments.corrected_report,
            arguments.output,
        )
    except (OSError, ValueError, ProposedSourceResponseError) as exc:
        print(f"Direct source-response comparison failed: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
