"""Immutable server-startup application modes."""

from __future__ import annotations

from enum import StrEnum


class ApplicationMode(StrEnum):
    """Trust boundary selected only by the process CLI."""

    PUBLIC_PRECOMPUTED = "public_precomputed"
    TRUSTED_LOCAL_LIVE = "trusted_local_live"


__all__ = ["ApplicationMode"]
