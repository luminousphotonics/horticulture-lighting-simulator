#!/usr/bin/env python3
"""Compatibility facade for SPYDR3 comparator emitter generation."""

from __future__ import annotations

import importlib
import subprocess  # nosec B404 - compatibility hook for ies2rad tests.
from pathlib import Path
from types import ModuleType
from typing import Any, TextIO, TypeAlias

from rad_rebuild.radiance.engine.emitters.deterministic_ids import radiance_identifier
from rad_rebuild.radiance.engine.emitters.spydr3_generation import (
    service as _service,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.geometry import (
    SpydrFixtureGeometry,
)
from rad_rebuild.radiance.engine.emitters.spydr3_generation.ies_stage import (
    IesBrightdataBlock as IesBrightdataBlock,
    IesCandelaDistribution as IesCandelaDistribution,
    IesLightBlock as IesLightBlock,
    IesPolygonBlock as IesPolygonBlock,
    RelativeSPDPhotonMetrics as RelativeSPDPhotonMetrics,
    SpydrIesStage,
    run_ies2rad,
    scale_ies_rad,
)
from rad_rebuild.radiance.executables import resolve_executable

importlib.reload(_service)

Point2D: TypeAlias = tuple[float, float]
FixtureCenter: TypeAlias = tuple[float, float]
JsonObject: TypeAlias = dict[str, Any]

OUT_DIR = _service.OUT_DIR
PROXY_MODE = _service.PROXY_MODE

_SYNC_GLOBALS = ("OUT_DIR", "PROXY_MODE", "IES_PATH", "IES_BASENAME", "IES_SPD_PATH")


def _sync_service_globals() -> ModuleType:
    for name in _SYNC_GLOBALS:
        if name in globals():
            setattr(_service, name, globals()[name])
    setattr(_service, "radiance_identifier", radiance_identifier)
    setattr(_service, "resolve_executable", resolve_executable)
    setattr(_service, "subprocess", subprocess)
    return _service


def __getattr__(name: str) -> Any:
    return getattr(_service, name)


def rect_corners(
    ccx: float, ccy: float, lx: float, ly: float, rot_rad: float
) -> list[Point2D]:
    return _service.rect_corners(ccx, ccy, lx, ly, rot_rad)


def write_bar(
    fh: TextIO,
    mat: str,
    cx: float,
    cy: float,
    z: float,
    length_m: float,
    width_m: float,
    rot_rad: float,
    sub: int,
    layout_bars: list[JsonObject],
) -> None:
    _sync_service_globals()
    _service.write_bar(
        fh, mat, cx, cy, z, length_m, width_m, rot_rad, sub, layout_bars
    )


def get_fixture_positions() -> list[dict[str, float]]:
    _sync_service_globals()
    return _service.get_fixture_positions()


def _spydr_fixture_geometry() -> SpydrFixtureGeometry:
    _sync_service_globals()
    return _service._spydr_fixture_geometry()


def _base_spydr_layout(
    geometry: SpydrFixtureGeometry,
    centers: list[FixtureCenter],
    nx_eff: int,
    ny_eff: int,
) -> JsonObject:
    _sync_service_globals()
    return _service._base_spydr_layout(geometry, centers, nx_eff, ny_eff)


def _build_spydr_ies_stage(fixture_ppf_active: float) -> SpydrIesStage:
    _sync_service_globals()
    return _service._build_spydr_ies_stage(fixture_ppf_active)


def _scale_ies_rad(rad_path: Path, scale: float) -> None:
    scale_ies_rad(rad_path, scale)


def _run_ies2rad(ies_path: Path, out_base: str, scale: float) -> Path:
    _sync_service_globals()
    return run_ies2rad(
        ies_path=ies_path,
        out_base=out_base,
        scale=scale,
        out_dir=OUT_DIR,
        resolve_executable_fn=resolve_executable,
        subprocess_module=subprocess,
        scale_ies_rad_fn=_scale_ies_rad,
    )


def _write_spydr_fixture(
    fh: TextIO,
    cx: float,
    cy: float,
    geometry: SpydrFixtureGeometry,
    stage: SpydrIesStage,
) -> None:
    _sync_service_globals()
    _service._write_spydr_fixture(fh, cx, cy, geometry, stage)


def main() -> None:
    _sync_service_globals()
    geometry = _spydr_fixture_geometry()
    out_name = "emitters_smd_ALL_umol.rad" if _service.COMPAT else "emitters_spydr3_ALL_umol.rad"
    out = OUT_DIR / out_name
    centers, nx_eff, ny_eff = _service._fixture_centers(
        geometry.fixture_length_m, geometry.fixture_width_m
    )
    layout = _base_spydr_layout(geometry, centers, nx_eff, ny_eff)
    stage = _build_spydr_ies_stage(geometry.fixture_ppf_active)
    _service._apply_spydr_ies_layout_metadata(layout, stage)
    _service._write_spydr_rad(out, centers, geometry, stage, layout)
    _service._write_spydr_summary(layout, geometry, stage)
    _service._print_spydr_completion(out, layout, geometry, stage)


if __name__ == "__main__":
    main()
