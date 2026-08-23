"""Approved HPS LM-63 loading through the shared source-neutral parser."""

from __future__ import annotations

from pathlib import Path

from fspm_optics.photometry.lm63 import (
    IMPERIAL_UNITS,
    TYPE_C_PHOTOMETRY,
    Lm63ParseError,
    Lm63Photometry,
    parse_lm63,
)

from .errors import HpsPhotometryError
from .resources import HPS_IES_RESOURCE_NAME, hps_resource_bytes


def load_hps_lm63(
    *,
    data_root: str | Path | None = None,
) -> Lm63Photometry:
    """Load, byte-check, parse, and validate the approved measured asset."""

    raw = hps_resource_bytes(HPS_IES_RESOURCE_NAME, data_root=data_root)
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise HpsPhotometryError("approved HPS LM-63 resource must be ASCII.") from exc
    try:
        photometry = parse_lm63(
            text,
            source=f"packaged:{HPS_IES_RESOURCE_NAME}",
        )
    except Lm63ParseError as exc:
        raise HpsPhotometryError(str(exc)) from exc
    expected = (
        photometry.version == "IESNA:LM-63-2002",
        photometry.photometric_type == TYPE_C_PHOTOMETRY,
        photometry.units_type == IMPERIAL_UNITS,
        photometry.vertical_angles_deg[0] == 0.0,
        photometry.vertical_angles_deg[-1] == 90.0,
        photometry.horizontal_angles_deg[0] == 0.0,
        photometry.horizontal_angles_deg[-1] == 90.0,
        photometry.fixture_height_m == 0.0,
    )
    if not all(expected):
        raise HpsPhotometryError(
            "approved HPS photometry must remain LM-63-2002 imperial, Type C, "
            "0-90 vertical/quadrant, with zero emitting height."
        )
    if any(plane[-1] != 0.0 for plane in photometry.candela_by_horizontal_plane):
        raise HpsPhotometryError("approved HPS 90-degree intensities must remain zero.")
    return photometry


__all__ = ["Lm63Photometry", "load_hps_lm63"]
