"""Stable server-selected Proposed fixture-composition modes."""

from __future__ import annotations

from enum import StrEnum

PROPOSED_LAYOUT_MODE_ENV_VAR = "FSPM_PROPOSED_LAYOUT_MODE"
PROPOSED_LINEAR_LAYOUT_ENV_VAR = "FSPM_ENABLE_PROPOSED_LINEAR_LAYOUT"


class ProposedLayoutMode(StrEnum):
    """Display/assembly composition over invariant authoritative modules."""

    STANDALONE_MODULES = "standalone_modules"
    LINEAR = "linear"
    LEGACY = "legacy"


DEFAULT_PROPOSED_LAYOUT_MODE = ProposedLayoutMode.STANDALONE_MODULES


def resolve_proposed_layout_mode(value: object = None) -> ProposedLayoutMode:
    """Normalize one startup value without reading process environment state."""

    if value is None or value == "":
        return DEFAULT_PROPOSED_LAYOUT_MODE
    if isinstance(value, ProposedLayoutMode):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        try:
            return ProposedLayoutMode(normalized)
        except ValueError:
            pass
    raise ValueError(
        f"{PROPOSED_LAYOUT_MODE_ENV_VAR} must be exactly standalone_modules, "
        "linear, or legacy."
    )


def resolve_proposed_layout_startup_environment(
    layout_selector: object = None,
    linear_flag: object = None,
) -> ProposedLayoutMode:
    """Resolve the fail-closed server startup migration contract."""

    selector = _startup_text(layout_selector, PROPOSED_LAYOUT_MODE_ENV_VAR)
    flag = _startup_text(linear_flag, PROPOSED_LINEAR_LAYOUT_ENV_VAR)
    if flag not in {"", "0", "1"}:
        raise ValueError(
            f"{PROPOSED_LINEAR_LAYOUT_ENV_VAR} must be unset, empty, 0, or 1."
        )
    if selector == "linear":
        raise ValueError(
            f"{PROPOSED_LAYOUT_MODE_ENV_VAR}=linear is no longer accepted at "
            "startup; use FSPM_ENABLE_PROPOSED_LINEAR_LAYOUT=1."
        )
    if selector not in {"", "legacy"}:
        raise ValueError(
            f"{PROPOSED_LAYOUT_MODE_ENV_VAR} must be unset, empty, or legacy "
            "at startup."
        )
    if selector == "legacy":
        if flag == "1":
            raise ValueError(
                f"{PROPOSED_LAYOUT_MODE_ENV_VAR}=legacy conflicts with "
                f"{PROPOSED_LINEAR_LAYOUT_ENV_VAR}=1."
            )
        return ProposedLayoutMode.LEGACY
    if flag == "1":
        return ProposedLayoutMode.LINEAR
    return DEFAULT_PROPOSED_LAYOUT_MODE


def _startup_text(value: object, name: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string when supplied.")
    return value.strip().lower()


__all__ = [
    "DEFAULT_PROPOSED_LAYOUT_MODE",
    "PROPOSED_LINEAR_LAYOUT_ENV_VAR",
    "PROPOSED_LAYOUT_MODE_ENV_VAR",
    "ProposedLayoutMode",
    "resolve_proposed_layout_mode",
    "resolve_proposed_layout_startup_environment",
]
