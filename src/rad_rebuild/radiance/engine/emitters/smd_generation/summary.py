from __future__ import annotations

from collections.abc import Callable
from typing import Any

PositionRecord = dict[str, Any]


def ring_power_summary(
    *,
    rings_local: int,
    ring_counts: dict[int, int],
    per_module_mode: bool,
    positions: list[PositionRecord],
    per_mod_w: list[float] | None,
    ring_power_for: Callable[[int], float],
    module_effective_watts_for_w: Callable[[float], float],
) -> tuple[float, float, list[str]]:
    if per_module_mode:
        return _per_module_ring_power_summary(
            rings_local=rings_local,
            positions=positions,
            per_mod_w=per_mod_w,
            module_effective_watts_for_w=module_effective_watts_for_w,
        )

    total_w_in = 0.0
    total_w_eff = 0.0
    lines: list[str] = []
    for ring in range(rings_local):
        count = ring_counts.get(ring, 0)
        w_in = ring_power_for(ring)
        w_eff = module_effective_watts_for_w(w_in)
        total_w_in += count * w_in
        total_w_eff += count * w_eff
        lines.append(
            f"    ring {ring}: {w_in:5.2f} W in ({w_eff:5.2f} W eff) × {count} mods"
        )
    return total_w_in, total_w_eff, lines


def photon_totals(
    *,
    rings_local: int,
    ring_counts: dict[int, int],
    per_module_mode: bool,
    per_mod_w: list[float] | None,
    module_photon_flux_umol_s: Callable[[int], float],
    module_output_photon_umol_s: Callable[[int], float],
    module_photon_flux_umol_s_for_w: Callable[[float], float],
    module_output_photon_umol_s_for_w: Callable[[float], float],
) -> tuple[float, float]:
    if per_module_mode:
        if per_mod_w is None:
            raise RuntimeError("per-module mode selected without per-module watt values")
        return (
            sum(module_photon_flux_umol_s_for_w(w) for w in per_mod_w),
            sum(module_output_photon_umol_s_for_w(w) for w in per_mod_w),
        )
    return (
        sum(
            module_photon_flux_umol_s(ring) * ring_counts.get(ring, 0)
            for ring in range(rings_local)
        ),
        sum(
            module_output_photon_umol_s(ring) * ring_counts.get(ring, 0)
            for ring in range(rings_local)
        ),
    )


def curve_debug_text(
    *,
    positions: list[PositionRecord],
    per_module_mode: bool,
    per_mod_w: list[float] | None,
    enabled: bool,
    debug_module_idx: int,
    debug_ring: int,
    ring_power_for: Callable[[int], float],
    curve_debug_report: Callable[[int, int, float], str],
) -> str:
    if not enabled:
        return ""
    selected_idx = _curve_debug_index(
        positions=positions,
        debug_module_idx=debug_module_idx,
        debug_ring=debug_ring,
    )
    selected_ring = int(positions[selected_idx].get("ring", 0)) if positions else 0
    selected_w = _curve_debug_watts(
        ring=selected_ring,
        index=selected_idx,
        per_module_mode=per_module_mode,
        per_mod_w=per_mod_w,
        ring_power_for=ring_power_for,
    )
    return curve_debug_report(selected_idx, selected_ring, selected_w) + "\n"


def _per_module_ring_power_summary(
    *,
    rings_local: int,
    positions: list[PositionRecord],
    per_mod_w: list[float] | None,
    module_effective_watts_for_w: Callable[[float], float],
) -> tuple[float, float, list[str]]:
    if per_mod_w is None:
        raise RuntimeError("per-module mode selected without per-module watt values")
    ring_w: dict[int, list[float]] = {ring: [] for ring in range(rings_local)}
    for idx, position in enumerate(positions):
        ring = int(position.get("ring", 0))
        if 0 <= ring < rings_local:
            ring_w[ring].append(float(per_mod_w[idx]))
    total_w_in = 0.0
    total_w_eff = 0.0
    lines: list[str] = []
    for ring in range(rings_local):
        ws = ring_w.get(ring, [])
        w_eff_list = [module_effective_watts_for_w(w) for w in ws]
        total_w_in += float(sum(ws))
        total_w_eff += float(sum(w_eff_list))
        lines.append(_per_module_ring_line(ring, ws, w_eff_list))
    return total_w_in, total_w_eff, lines


def _per_module_ring_line(ring: int, ws: list[float], w_eff_list: list[float]) -> str:
    count = len(ws)
    w_avg = float(sum(ws) / count) if count > 0 else 0.0
    w_min = float(min(ws)) if count > 0 else 0.0
    w_max = float(max(ws)) if count > 0 else 0.0
    w_eff = float(sum(w_eff_list) / count) if count > 0 else 0.0
    return (
        f"    ring {ring}: {w_avg:5.2f} W avg ({w_eff:5.2f} W eff) "
        f"[min {w_min:5.2f}, max {w_max:5.2f}] × {count} mods"
    )


def _curve_debug_index(
    *,
    positions: list[PositionRecord],
    debug_module_idx: int,
    debug_ring: int,
) -> int:
    if 0 <= debug_module_idx < len(positions):
        return debug_module_idx
    return next(
        (
            index
            for index, position in enumerate(positions)
            if int(position.get("ring", 0)) == debug_ring
        ),
        0,
    )


def _curve_debug_watts(
    *,
    ring: int,
    index: int,
    per_module_mode: bool,
    per_mod_w: list[float] | None,
    ring_power_for: Callable[[int], float],
) -> float:
    if per_module_mode and per_mod_w:
        return float(per_mod_w[index])
    return ring_power_for(ring)
