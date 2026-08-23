"""Parsing helpers for already-captured Radiance receiver output."""

from __future__ import annotations

import math


def parse_rtrace_receiver_output(text: str) -> list[float]:
    """Decode each RGB row as the arithmetic-mean scalar photon density."""

    densities: list[float] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 3:
            raise ValueError(f"Malformed receiver output row {line_number}: {line!r}")
        try:
            channels = [float(parts[-3]), float(parts[-2]), float(parts[-1])]
        except ValueError as exc:
            raise ValueError(f"Malformed receiver output row {line_number}: {line!r}") from exc
        if any(not math.isfinite(channel) or channel < 0.0 for channel in channels):
            raise ValueError(f"Invalid receiver output row {line_number}: {line!r}")
        densities.append(sum(channels) / 3.0)
    return densities
