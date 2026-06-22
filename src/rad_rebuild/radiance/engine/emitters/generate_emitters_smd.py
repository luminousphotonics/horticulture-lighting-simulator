#!/usr/bin/env python3
"""Compatibility facade for SMD emitter generation."""

from __future__ import annotations

import importlib
from collections.abc import Callable
from types import ModuleType
from typing import TYPE_CHECKING, Any, TextIO, TypeAlias

from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier
from rad_rebuild.radiance.engine.emitters.smd_generation import service as _service

importlib.reload(_service)

PositionRecord: TypeAlias = dict[str, Any]
JsonObject: TypeAlias = dict[str, Any]

if TYPE_CHECKING:
    from rad_rebuild.radiance.engine.emitters.smd_generation.service import (
        PerimeterGapEdges as PerimeterGapEdges,
        PerimeterGapGeometry as PerimeterGapGeometry,
        SmdEmitterConfig as SmdEmitterConfig,
        SmdEmitterEvent as SmdEmitterEvent,
        SmdEmitterResult as SmdEmitterResult,
        SmdLayoutStage as SmdLayoutStage,
        SmdLossStage as SmdLossStage,
        SmdPositionContext as SmdPositionContext,
        SmdPowerStage as SmdPowerStage,
        SmdSummaryStage as SmdSummaryStage,
    )
    from rad_rebuild.radiance.engine.emitters.smd_generation.power_overrides import (
        RingPowerOverrideUpdates as RingPowerOverrideUpdates,
    )
else:
    SmdEmitterConfig = _service.SmdEmitterConfig
    SmdEmitterEvent = _service.SmdEmitterEvent
    SmdEmitterResult = _service.SmdEmitterResult
    SmdLayoutStage = _service.SmdLayoutStage
    SmdPowerStage = _service.SmdPowerStage
    SmdLossStage = _service.SmdLossStage
    SmdSummaryStage = _service.SmdSummaryStage
    RingPowerOverrideUpdates = _service.RingPowerOverrideUpdates
    PerimeterGapEdges = _service.PerimeterGapEdges
    PerimeterGapGeometry = _service.PerimeterGapGeometry
    SmdPositionContext = _service.SmdPositionContext

_SYNC_GLOBALS = (
    "OUT_DIR",
    "SMD_JSON",
    "SMD_PERIM_GAP_FILL",
    "RING_POWER_BY_RING",
    "RING_POWERS_SOURCE",
    "RING_POWERS_COMPAT_STATUS",
    "RING_POWERS_COMPAT_MESSAGE",
    "PER_MODULE_OUTER_INDICES",
    "PER_MODULE_OUTER_POWERS",
    "PER_MODULE_INDICES",
    "PER_MODULE_POWERS",
    "OUTER_RING_INDEX_JSON",
    "LOSS_SCALE",
    "EFF_SCALE",
    "DROOP_SCALE",
)


def _sync_service_globals() -> ModuleType:
    for name in _SYNC_GLOBALS:
        if name in globals():
            setattr(_service, name, globals()[name])
    setattr(_service, "radiance_identifier", radiance_identifier)
    return _service


def __getattr__(name: str) -> Any:
    return getattr(_service, name)


def _compute_positions_from_env() -> tuple[list[PositionRecord], float, JsonObject]:
    _sync_service_globals()
    return _service._compute_positions_from_env()


def get_module_positions() -> tuple[list[PositionRecord], float]:
    _sync_service_globals()
    return _service.get_module_positions()


def _apply_perimeter_gap_fill(
    positions: list[PositionRecord],
) -> tuple[list[PositionRecord], JsonObject]:
    return _service._apply_perimeter_gap_fill(positions)


def _build_fixture_overlay(
    positions: list[PositionRecord],
    meta: JsonObject,
    z_m: float,
) -> tuple[list[JsonObject], JsonObject]:
    _sync_service_globals()
    return _service._build_fixture_overlay(positions, meta, z_m)


def _write_area_square(
    fh: TextIO, mat: str, cx: float, cy: float, z: float, side: float
) -> None:
    _sync_service_globals()
    _service._write_area_square(fh, mat, cx, cy, z, side)


def _write_area_rect(
    fh: TextIO, mat: str, cx: float, cy: float, z: float, lx: float, ly: float
) -> None:
    _sync_service_globals()
    _service._write_area_rect(fh, mat, cx, cy, z, lx, ly)


def read_smd_layout_json() -> tuple[list[PositionRecord], float]:
    _sync_service_globals()
    return _service.read_smd_layout_json()


def compute_droop_scale(
    ring_watts_per_module: list[float],
    ring_module_counts: list[int],
    *,
    P_NOM: float = 100.0,
    K: float = 0.12,
) -> float:
    return _service.compute_droop_scale(
        ring_watts_per_module, ring_module_counts, P_NOM=P_NOM, K=K
    )


def get_ring_watts_and_counts(
    ring_counts: dict[int, int], rings_local: int
) -> tuple[list[float], list[int]]:
    _sync_service_globals()
    return _service.get_ring_watts_and_counts(ring_counts, rings_local)


def run_smd_emitter_generation(
    config: SmdEmitterConfig | None = None,
    *,
    emit: Callable[[SmdEmitterEvent], None] | None = None,
) -> SmdEmitterResult:
    _sync_service_globals()
    return _service.run_smd_emitter_generation(config, emit=emit)


def main() -> None:
    run_smd_emitter_generation(emit=lambda event: print(event.message))


if __name__ == "__main__":
    main()
