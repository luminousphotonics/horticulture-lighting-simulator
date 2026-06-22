from __future__ import annotations

from collections.abc import Callable


def compute_droop_scale(
    ring_watts_per_module: list[float],
    ring_module_counts: list[int],
    *,
    P_NOM: float = 100.0,
    K: float = 0.12,
) -> float:
    num = 0.0
    den = 0.0
    for P, N in zip(ring_watts_per_module, ring_module_counts):
        if P <= 0 or N <= 0:
            continue
        x = max(0.05, min(1.2, float(P) / max(P_NOM, 1e-9)))
        scale = x ** (-K)
        w = float(P) * float(N)
        num += w * scale
        den += w
    return (num / den) if den > 0 else 1.0


def get_ring_watts_and_counts(
    ring_counts: dict[int, int],
    rings_local: int,
    ring_power_for: Callable[[int], float],
) -> tuple[list[float], list[int]]:
    ring_watts: list[float] = []
    ring_mods: list[int] = []
    for ring in range(rings_local):
        ring_watts.append(ring_power_for(ring))
        ring_mods.append(int(ring_counts.get(ring, 0)))
    return ring_watts, ring_mods
