"""Authenticated controlled COB source-shape surrogate for Proposed fixtures."""

from .source import (
    COB_ANGULAR_DAT_FILENAME,
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    CobAngularLaw,
    ProposedSourceAuthority,
    load_cob_angular_law,
    resolve_proposed_source_authority,
)

__all__ = (
    "COB_ANGULAR_DAT_FILENAME",
    "COB_SOURCE_MODE",
    "NATIVE_SOURCE_MODE",
    "CobAngularLaw",
    "ProposedSourceAuthority",
    "load_cob_angular_law",
    "resolve_proposed_source_authority",
)
