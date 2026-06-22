#!/usr/bin/env python3
"""Run scheduled dependency audits with the expiring pip-audit allowlist."""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback.
    import tomli as tomllib  # type: ignore[no-redef]


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ALLOWLIST = ROOT / "audit" / "pip-audit-allowlist.toml"
DEFAULT_REQUIREMENTS = ROOT / "requirements.txt"


def _load_allowlist(path: Path) -> list[dict[str, Any]]:
    with path.open("rb") as handle:
        data = tomllib.load(handle)
    vulnerabilities = data.get("vulnerability", [])
    if not isinstance(vulnerabilities, list):
        raise ValueError(f"{path} field 'vulnerability' must be a list")
    return vulnerabilities


def _expiry(value: object, *, path: Path, vuln_id: str) -> date:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError(f"{path} vulnerability {vuln_id} has invalid expires date {value!r}") from exc
    raise ValueError(f"{path} vulnerability {vuln_id} must include expires as YYYY-MM-DD")


def pip_audit_ignore_args(path: Path, today: date | None = None) -> list[str]:
    today = today or date.today()
    ignore_args: list[str] = []
    for index, vulnerability in enumerate(_load_allowlist(path), start=1):
        if not isinstance(vulnerability, dict):
            raise ValueError(f"{path} vulnerability entry {index} must be a table")
        vuln_id = str(vulnerability.get("id", "")).strip()
        missing = [
            field
            for field in ("id", "package", "reason", "owner", "expires")
            if not str(vulnerability.get(field, "")).strip()
        ]
        if missing:
            label = vuln_id or f"entry {index}"
            fields = ", ".join(missing)
            raise ValueError(f"{path} vulnerability {label} missing required field(s): {fields}")
        expires = _expiry(vulnerability["expires"], path=path, vuln_id=vuln_id)
        if expires < today:
            raise ValueError(f"{path} vulnerability {vuln_id} expired on {expires.isoformat()}")
        ignore_args.extend(["--ignore-vuln", vuln_id])
    return ignore_args


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--allowlist", type=Path, default=DEFAULT_ALLOWLIST)
    parser.add_argument("--requirements", type=Path, default=DEFAULT_REQUIREMENTS)
    parser.add_argument("--skip-npm", action="store_true")
    args = parser.parse_args(argv)

    pip_cmd = [
        sys.executable,
        "-m",
        "pip_audit",
        "--require-hashes",
        "--disable-pip",
        "-r",
        str(args.requirements),
        *pip_audit_ignore_args(args.allowlist),
    ]
    _run(pip_cmd)
    if not args.skip_npm:
        _run(["npm", "audit", "--audit-level=moderate"])
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
