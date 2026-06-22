from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "docs" / "api" / "radiance-openapi.json"


def _schema_text() -> str:
    from rad_rebuild.radiance.backend.server import create_app

    schema = create_app().openapi()
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Export or check the deterministic Radiance FastAPI OpenAPI schema."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that the committed OpenAPI schema matches the current app.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Path to write or check. Defaults to docs/api/radiance-openapi.json.",
    )
    args = parser.parse_args(argv)

    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    current = _schema_text()

    if args.check:
        try:
            expected = output.read_text(encoding="utf-8")
        except FileNotFoundError:
            print(f"OpenAPI schema is missing: {output}", file=sys.stderr)
            return 1
        if expected != current:
            print(
                f"OpenAPI schema is stale. Regenerate with: {sys.executable} {Path(__file__)}",
                file=sys.stderr,
            )
            return 1
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(current, encoding="utf-8")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
