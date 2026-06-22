from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class RingPowerOverrideUpdates:
    ring_updates: dict[int, float]
    outer_ring_index: int | None
    outer_indices: list[int]
    outer_powers: list[float]
    module_indices: list[int]
    module_powers: list[float]
    applied_parts: tuple[str, ...]
    warnings: tuple[str, ...]


def unique_ints_or_empty(values: object) -> list[int]:
    if not isinstance(values, Iterable):
        return []
    try:
        return sorted({int(value) for value in values})
    except (TypeError, ValueError):
        return []


def ring_power_override_updates(path: Path, data: JsonObject) -> RingPowerOverrideUpdates:
    ring_updates, ring_parts, ring_warnings = _ring_schedule_updates(path, data)
    outer_ring_index, outer_indices, outer_powers, outer_parts = _outer_module_updates(
        data
    )
    module_indices, module_powers, module_parts, module_warnings = (
        _module_power_updates(path, data)
    )
    return RingPowerOverrideUpdates(
        ring_updates=ring_updates,
        outer_ring_index=outer_ring_index,
        outer_indices=outer_indices,
        outer_powers=outer_powers,
        module_indices=module_indices,
        module_powers=module_powers,
        applied_parts=ring_parts + outer_parts + module_parts,
        warnings=ring_warnings + module_warnings,
    )


def _json_list(value: object) -> list[Any]:
    if not isinstance(value, Iterable):
        raise TypeError("JSON value is not iterable")
    return list(value)


def _ring_schedule_updates(
    path: Path, data: JsonObject
) -> tuple[dict[int, float], tuple[str, ...], tuple[str, ...]]:
    values = data.get("ring_powers_W_per_module") or data.get("ring_powers")
    if values is None:
        return {}, (), ()
    powers = _json_list(values)
    indices = data.get("ring_indices")
    index_values = list(range(len(powers))) if indices is None else _json_list(indices)
    if len(index_values) != len(powers):
        return (
            {},
            (),
            (
                f"WARNING: ring_powers length mismatch in {path}; skipping ring override",
            ),
        )
    updates: dict[int, float] = {}
    for index, power in zip(index_values, powers):
        try:
            updates[int(index)] = float(power)
        except (TypeError, ValueError):
            continue
    return updates, ("rings",), ()


def _outer_module_updates(
    data: JsonObject,
) -> tuple[int | None, list[int], list[float], tuple[str, ...]]:
    outer_ring = data.get("outer_ring_index")
    outer_ring_index = int(outer_ring) if isinstance(outer_ring, int) else None
    indices = data.get("outer_ring_indices") or []
    powers = data.get("outer_ring_powers_W_per_module") or data.get("outer_ring_powers")
    if not indices or not powers or len(indices) != len(powers):
        return outer_ring_index, [], [], ()
    try:
        return (
            outer_ring_index,
            [int(value) for value in _json_list(indices)],
            [float(value) for value in _json_list(powers)],
            ("outer-modules",),
        )
    except (TypeError, ValueError):
        return outer_ring_index, [], [], ()


def _module_power_updates(
    path: Path, data: JsonObject
) -> tuple[list[int], list[float], tuple[str, ...], tuple[str, ...]]:
    powers = data.get("module_powers_W_per_module") or data.get("module_powers_W")
    if powers is None:
        return [], [], (), ()
    power_values = _json_list(powers)
    indices = data.get("module_indices")
    index_values = (
        list(range(len(power_values))) if indices is None else _json_list(indices)
    )
    if len(index_values) != len(power_values):
        return (
            [],
            [],
            (),
            (
                f"WARNING: module_powers length mismatch in {path}; skipping module override",
            ),
        )
    try:
        return (
            [int(value) for value in index_values],
            [float(value) for value in power_values],
            ("modules",),
            (),
        )
    except (TypeError, ValueError):
        return [], [], (), ()
