from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


DEFAULT_RING_POWER_SCHEDULE_ID = "smd_default_ring_power_schedule_v1"

# Default per-module electrical schedule used when no validated optimizer output
# or basis-mode override is active. Ring 8 is the connector-column schedule entry
# used by modular rectangular layouts. Additional outer rings intentionally clamp
# to the last configured value for compatibility with historical generator runs.
DEFAULT_RING_POWER_W_BY_RING: Mapping[int, float] = MappingProxyType(
    {
        0: 31.827,
        1: 28.284,
        2: 33.154,
        3: 30.241,
        4: 25.900,
        5: 44.723,
        6: 3.596,
        7: 70.760,
        8: 70.760,
    }
)


@dataclass(frozen=True)
class RingPowerSchedule:
    """Per-module electrical watts indexed by SMD control ring."""

    schedule_id: str
    watts_by_ring: Mapping[int, float]


def default_ring_power_schedule() -> RingPowerSchedule:
    """Return the built-in SMD ring-power schedule."""
    return RingPowerSchedule(
        schedule_id=DEFAULT_RING_POWER_SCHEDULE_ID,
        watts_by_ring=DEFAULT_RING_POWER_W_BY_RING,
    )


def expand_ring_power_schedule(
    schedule: RingPowerSchedule | Mapping[int, float],
    required_rings: int,
) -> dict[int, float]:
    """Return a mutable schedule with entries for rings ``0..required_rings-1``.

    Missing outer rings inherit the last configured wattage. This preserves the
    legacy generator behavior for room sizes whose control-ring count exceeds
    the built-in schedule table.
    """
    target_rings = max(0, int(required_rings))
    source = dict(
        schedule.watts_by_ring if isinstance(schedule, RingPowerSchedule) else schedule
    )
    if target_rings <= 0 or not source:
        return source

    last_known_key = max(source.keys())
    last_known_power = float(source[last_known_key])
    expanded = {int(ring): float(power) for ring, power in source.items()}
    for ring in range(target_rings):
        expanded.setdefault(ring, last_known_power)
    return expanded


def ring_power_for(
    schedule: RingPowerSchedule | Mapping[int, float], ring: int
) -> float:
    """Return per-module watts for a ring, clamping past the last known entry."""
    ring_idx = int(ring)
    expanded = expand_ring_power_schedule(schedule, ring_idx + 1)
    return float(expanded.get(ring_idx, 0.0))


def basis_ring_power_schedule(
    required_rings: int, basis_ring: int, basis_unit_w: float
) -> dict[int, float]:
    """Return a schedule with one active basis ring and all other rings at zero."""
    target_rings = max(0, int(required_rings))
    active_ring = int(basis_ring)
    unit_w = float(basis_unit_w)
    return {
        ring: (unit_w if ring == active_ring else 0.0) for ring in range(target_rings)
    }


def module_basis_ring_power_schedule(required_rings: int) -> dict[int, float]:
    """Return a ring schedule with all ring powers disabled for module basis runs."""
    return {ring: 0.0 for ring in range(max(0, int(required_rings)))}


def uniform_ring_power_schedule(required_rings: int, unit_w: float) -> dict[int, float]:
    """Return a schedule with the same per-module watts on every active ring."""
    return {ring: float(unit_w) for ring in range(max(0, int(required_rings)))}
