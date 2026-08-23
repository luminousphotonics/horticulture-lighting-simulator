"""Public Proposed module-ring arrangement identity."""

from __future__ import annotations

from enum import StrEnum


class ProposedRingMode(StrEnum):
    """Discrete Proposed module topology selected by the public request."""

    FULL = "full"
    REDUCED_ONE_RING = "reduced_one_ring"


DEFAULT_PROPOSED_RING_MODE = ProposedRingMode.FULL
PROPOSED_MODULE_PATTERN_IDS = {
    ProposedRingMode.FULL: "centered_square_full_v1",
    ProposedRingMode.REDUCED_ONE_RING: "centered_square_reduced_one_ring_v1",
}


def resolve_proposed_ring_mode(value: object = None) -> ProposedRingMode:
    """Resolve an exact public ring value without permissive normalization."""

    if value is None:
        return DEFAULT_PROPOSED_RING_MODE
    if isinstance(value, ProposedRingMode):
        return value
    if isinstance(value, str):
        try:
            return ProposedRingMode(value)
        except ValueError:
            pass
    raise ValueError(
        "proposed_ring_mode must be exactly full or reduced_one_ring."
    )


def proposed_module_pattern_id(mode: ProposedRingMode | str) -> str:
    """Return the cache-safe module-pattern identity for one public mode."""

    return PROPOSED_MODULE_PATTERN_IDS[resolve_proposed_ring_mode(mode)]


def effective_topology_order(
    nominal_base_n: int,
    mode: ProposedRingMode | str,
) -> int:
    """Derive and validate the topology order used during construction."""

    if (
        isinstance(nominal_base_n, bool)
        or not isinstance(nominal_base_n, int)
        or nominal_base_n <= 0
    ):
        raise ValueError("nominal topology order must be a positive integer.")
    resolved = resolve_proposed_ring_mode(mode)
    effective = (
        nominal_base_n - 1
        if resolved is ProposedRingMode.REDUCED_ONE_RING
        else nominal_base_n
    )
    if effective <= 0:
        raise ValueError(
            "reduced_one_ring requires a positive effective topology order."
        )
    return effective


__all__ = [
    "DEFAULT_PROPOSED_RING_MODE",
    "PROPOSED_MODULE_PATTERN_IDS",
    "ProposedRingMode",
    "effective_topology_order",
    "proposed_module_pattern_id",
    "resolve_proposed_ring_mode",
]
