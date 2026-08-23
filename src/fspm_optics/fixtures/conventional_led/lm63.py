"""Compatibility adapter for the approved Conventional LM-63 subset."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from fspm_optics.photometry.lm63 import (
    METRIC_UNITS,
    Lm63Keyword,
    Lm63Photometry,
    Lm63ParseError as SharedLm63ParseError,
    parse_lm63 as parse_shared_lm63,
)

from .errors import Lm63ParseError

SUPPORTED_LM63_VERSION: Final = "IESNA:LM-63-2019"


def parse_lm63(text: str, *, source: str = "<memory>") -> Lm63Photometry:
    """Preserve the original strict LM-63-2019 Conventional API."""

    try:
        photometry = parse_shared_lm63(text, source=source)
    except SharedLm63ParseError as exc:
        raise Lm63ParseError(str(exc)) from exc
    if photometry.version != SUPPORTED_LM63_VERSION:
        raise Lm63ParseError(
            f"unsupported LM-63 version in {source}: {photometry.version!r}; "
            f"only {SUPPORTED_LM63_VERSION!r} is supported."
        )
    if photometry.units_type != METRIC_UNITS:
        raise Lm63ParseError("unsupported LM-63 units; metric units (2) are required.")
    if photometry.fixture_height_m <= 0.0:
        raise Lm63ParseError("LM-63 fixture dimensions must be positive.")
    if photometry.vertical_angles_deg[-1] != 180.0:
        raise Lm63ParseError(
            "supported Type-C LM-63 vertical angles must close from 0 to 180 degrees."
        )
    if photometry.horizontal_angles_deg[-1] != 360.0:
        raise Lm63ParseError(
            "supported Type-C LM-63 horizontal angles must close from 0 to 360 degrees."
        )
    return photometry


def load_approved_lm63(
    *,
    data_root: str | Path | None = None,
) -> Lm63Photometry:
    """Load and parse the byte-verified packaged Conventional LED LM-63 resource."""

    from .resources import CONVENTIONAL_IES_RESOURCE_NAME, conventional_resource_bytes

    raw = conventional_resource_bytes(
        CONVENTIONAL_IES_RESOURCE_NAME,
        data_root=data_root,
    )
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise Lm63ParseError("approved LM-63 resource must be ASCII text.") from exc
    return parse_lm63(text, source=f"packaged:{CONVENTIONAL_IES_RESOURCE_NAME}")


__all__ = [
    "Lm63Keyword",
    "Lm63Photometry",
    "Lm63ParseError",
    "SUPPORTED_LM63_VERSION",
    "load_approved_lm63",
    "parse_lm63",
]
