"""Pure Radiance option-list presets and transformations."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

RADIANCE_QUALITY_NAMES = ("direct", "standard", "quality", "rigorous")

_RADIANCE_OPTION_PRESETS: dict[str, tuple[str, ...]] = {
    "direct": (
        "-ab", "0", "-aa", "0", "-u+", "-dc", "1.0", "-dj", "0.60",
        "-ds", "0.08", "-dt", "0", "-dr", "0", "-lr", "0", "-lw", "1e-5",
    ),
    "standard": (
        "-ab", "3", "-ad", "512", "-as", "128", "-aa", "0.22", "-ar", "48",
        "-dj", "0.35", "-ds", "0.40", "-dt", "0.08", "-dc", "0.50", "-dr", "1",
        "-lr", "6", "-lw", "2e-4",
    ),
    "quality": (
        "-ab", "5", "-ad", "2048", "-as", "512", "-aa", "0.12", "-ar", "96",
        "-dj", "0.65", "-ds", "0.20", "-dt", "0.03", "-dc", "0.85", "-dr", "3",
        "-lr", "12", "-lw", "5e-5",
    ),
    "rigorous": (
        "-ab", "6", "-ad", "4096", "-as", "1024", "-aa", "0.08", "-ar", "128",
        "-dj", "0.70", "-ds", "0.15", "-dt", "0.02", "-dc", "0.90", "-dr", "4",
        "-lr", "16", "-lw", "2e-5",
    ),
}


def radiance_options(
    mode: str,
    *,
    ambient_cache: str | Path | None = None,
) -> list[str]:
    """Return a fresh option list; fast and instant retain the standard preset."""

    normalized = str(mode).strip().lower()
    preset_name = "standard" if normalized in {"fast", "instant"} else normalized
    options = list(
        _RADIANCE_OPTION_PRESETS.get(
            preset_name,
            _RADIANCE_OPTION_PRESETS["standard"],
        )
    )
    if ambient_cache is not None:
        options.extend(["-af", str(ambient_cache)])
    return options


def radiance_option_value(options: Sequence[str], name: str) -> str | None:
    for index, token in enumerate(options):
        if token == name and index + 1 < len(options):
            return str(options[index + 1])
    return None


def replace_radiance_option_value(
    options: Sequence[str],
    name: str,
    value: str,
) -> list[str]:
    """Replace the first option value or append a new name/value pair."""

    if not name or not value:
        raise ValueError("Radiance option name and value must be non-empty.")
    adjusted = [str(option) for option in options]
    for index, token in enumerate(adjusted):
        if token == name and index + 1 < len(adjusted):
            adjusted[index + 1] = value
            return adjusted
    adjusted.extend([name, value])
    return adjusted
