"""Shared exact-finding fingerprint gate helpers."""

from __future__ import annotations

from typing import Any


def source_classification(path: str) -> str:
    if path.startswith("tests/"):
        return "test-only"
    if path.startswith(("audit/", "data/radiance/precomputed/")):
        return "generated"
    if path.startswith(("src/rad_rebuild/", "scripts/", "app.py")):
        return "first-party"
    return "third-party"


def compare_fingerprints(current: set[str], allowed: set[str]) -> dict[str, list[str]]:
    return {
        "new": sorted(current - allowed),
        "resolved": sorted(allowed - current),
    }


def fingerprint_set(
    findings: list[dict[str, Any]], *, tool: str, first_party_only: bool = False
) -> set[str]:
    out: set[str] = set()
    for finding in findings:
        if finding.get("tool") != tool:
            continue
        if first_party_only and finding.get("source_classification") != "first-party":
            continue
        out.add(str(finding["fingerprint"]))
    return out
