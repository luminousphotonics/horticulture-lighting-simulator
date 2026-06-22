#!/usr/bin/env python3
"""Compatibility facade for HPS comparator emitter generation."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
from types import ModuleType
from typing import Any, TypeAlias

from rad_rebuild.radiance.engine.emitters.hps_generation import service as _service

importlib.reload(_service)

JsonObject: TypeAlias = dict[str, Any]

OUT_DIR = _service.OUT_DIR

_SYNC_GLOBALS = (
    "OUT_DIR",
    "DATA_DIR",
    "IES_VARIANT",
    "ACTIVE_IES_PROFILE",
    "HPS_IES_LABEL",
    "IES_PATH",
    "IES_SPD_PATH",
    "IES_BASENAME",
    "IES_ROT_X_DEG",
    "IES_ROT_Z_DEG",
    "ACTIVE_FIXTURE_LENGTH_M",
    "ACTIVE_FIXTURE_WIDTH_M",
    "ACTIVE_FIXTURE_HEIGHT_M",
)


def _sync_service_globals() -> ModuleType:
    for name in _SYNC_GLOBALS:
        if name in globals():
            setattr(_service, name, globals()[name])
    return _service


def __getattr__(name: str) -> Any:
    return getattr(_service, name)


def compute_fixture_layout() -> JsonObject:
    _sync_service_globals()
    return _service.compute_fixture_layout()


def get_fixture_positions() -> list[dict[str, float]]:
    _sync_service_globals()
    return _service.get_fixture_positions()


def _write_ies_comparator(
    out: Path,
    layout: JsonObject,
    eff_scale: float,
) -> tuple[str, JsonObject, JsonObject]:
    _sync_service_globals()
    return _service._write_ies_comparator(out, layout, eff_scale)


def _write_summary_files(summary_lines: JsonObject, layout: JsonObject) -> None:
    _sync_service_globals()
    _service._write_summary_files(summary_lines, layout)


def main() -> None:
    _sync_service_globals()
    out = OUT_DIR / "emitters_hps_ALL_umol.rad"
    layout = compute_fixture_layout()
    eff_scale = _service._clamp_0_1(float(os.getenv("EFF_SCALE", "1.0")))
    model_label, summary_lines, layout = _write_ies_comparator(out, layout, eff_scale)
    _write_summary_files(summary_lines, layout)
    _service._print_hps_completion(out, model_label, layout)


if __name__ == "__main__":
    main()
