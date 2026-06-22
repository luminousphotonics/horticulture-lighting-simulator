from __future__ import annotations

import math
import statistics
import struct
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.backend.models import RadianceRunRequest
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD, RADIANCE_MODE_LABELS

SCHEMA_VERSION = 1
SOURCE_ARTIFACT = "ppfd_map.txt"
PPFD_UNITS = "µmol/m²/s"
ENCODING = "float32-le"
SUPPORTED_MODES = frozenset({MODE_SMD, MODE_COMPETITOR, MODE_HPS})
Z_EXACT_TOLERANCE_M = 1e-9
Z_SLIGHT_TOLERANCE_M = 1e-4
GRID_SPACING_TOLERANCE_M = 5e-6


class PhotometricLayerError(ValueError):
    """Public-safe photometric layer construction failure."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 422,
        error: str = "invalid_photometric_layer",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def _parse_ppfd_map(ppfd_map: Path) -> list[tuple[float, float, float, float]]:
    if not ppfd_map.is_file():
        raise PhotometricLayerError(
            "ppfd_map.txt was not found.",
            status_code=404,
            error="photometric_layer_not_found",
        )

    rows: list[tuple[float, float, float, float]] = []
    try:
        lines = ppfd_map.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise PhotometricLayerError(
            "ppfd_map.txt could not be read.",
            status_code=404,
            error="photometric_layer_not_found",
        ) from exc

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 4:
            raise PhotometricLayerError(
                f"Malformed ppfd_map.txt line {line_number}.",
                status_code=422,
                error="photometric_layer_malformed",
            )
        try:
            x_m, y_m, z_m, ppfd = (float(part) for part in parts)
        except ValueError as exc:
            raise PhotometricLayerError(
                f"Malformed ppfd_map.txt line {line_number}.",
                status_code=422,
                error="photometric_layer_malformed",
            ) from exc
        if not all(math.isfinite(value) for value in (x_m, y_m, z_m, ppfd)):
            raise PhotometricLayerError(
                f"Non-finite ppfd_map.txt value on line {line_number}.",
                status_code=422,
                error="photometric_layer_malformed",
            )
        rows.append((x_m, y_m, z_m, ppfd))

    if not rows:
        raise PhotometricLayerError(
            "ppfd_map.txt is empty.",
            status_code=422,
            error="photometric_layer_malformed",
        )
    return rows


def _ensure_regular_spacing(axis_values: list[float], axis_name: str) -> None:
    if len(axis_values) < 3:
        return
    expected = axis_values[1] - axis_values[0]
    if expected <= 0:
        raise PhotometricLayerError(
            f"Photometric layer {axis_name} axis is irregular.",
            status_code=409,
            error="photometric_layer_irregular_grid",
        )
    for before, after in zip(axis_values[1:], axis_values[2:]):
        spacing = after - before
        if not math.isclose(
            spacing,
            expected,
            rel_tol=0.0,
            abs_tol=GRID_SPACING_TOLERANCE_M,
        ):
            raise PhotometricLayerError(
                f"Photometric layer {axis_name} axis spacing is irregular.",
                status_code=409,
                error="photometric_layer_irregular_grid",
            )


def _grid_values(rows: list[tuple[float, float, float, float]]) -> tuple[list[float], list[float], list[float]]:
    xs = sorted({row[0] for row in rows})
    ys = sorted({row[1] for row in rows})
    _ensure_regular_spacing(xs, "x")
    _ensure_regular_spacing(ys, "y")

    cells: dict[tuple[float, float], float] = {}
    for x_m, y_m, _z_m, ppfd in rows:
        key = (x_m, y_m)
        if key in cells:
            raise PhotometricLayerError(
                "ppfd_map.txt contains duplicate x/y grid cells.",
                status_code=409,
                error="photometric_layer_duplicate_cell",
            )
        cells[key] = ppfd

    expected_count = len(xs) * len(ys)
    if len(cells) != expected_count:
        raise PhotometricLayerError(
            "ppfd_map.txt does not contain a complete rectangular grid.",
            status_code=409,
            error="photometric_layer_incomplete_grid",
        )

    values: list[float] = []
    for y_m in ys:
        for x_m in xs:
            try:
                values.append(cells[(x_m, y_m)])
            except KeyError as exc:
                raise PhotometricLayerError(
                    "ppfd_map.txt does not contain a complete rectangular grid.",
                    status_code=409,
                    error="photometric_layer_incomplete_grid",
                ) from exc
    return xs, ys, values


def _z_m_and_warnings(rows: list[tuple[float, float, float, float]]) -> tuple[float, list[str]]:
    zs = [row[2] for row in rows]
    z_min = min(zs)
    z_max = max(zs)
    spread = z_max - z_min
    if spread <= Z_EXACT_TOLERANCE_M:
        return zs[0], []
    if spread <= Z_SLIGHT_TOLERANCE_M:
        return statistics.median(zs), [
            "ppfd_map.txt z values vary slightly; bounds_m.z_m uses the median measurement height."
        ]
    raise PhotometricLayerError(
        "ppfd_map.txt z values vary too much for a single measurement plane.",
        status_code=409,
        error="photometric_layer_inconsistent_z",
    )


def _color_scale_for_mean(mean_ppfd: float) -> tuple[float, float]:
    if math.isfinite(mean_ppfd):
        return max(0.0, mean_ppfd - 200.0), mean_ppfd + 200.0
    return 0.0, 1750.0


def _metadata(req: RadianceRunRequest, xs: list[float], ys: list[float], z_m: float, values: list[float], warnings: list[str]) -> dict[str, Any]:
    mean_ppfd = math.fsum(values) / len(values)
    vmin, vmax = _color_scale_for_mean(mean_ppfd)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": req.mode,
        "mode_label": RADIANCE_MODE_LABELS.get(req.mode, req.mode),
        "units": PPFD_UNITS,
        "grid_width": len(xs),
        "grid_height": len(ys),
        "value_count": len(values),
        "bounds_m": {
            "x_min": xs[0],
            "x_max": xs[-1],
            "y_min": ys[0],
            "y_max": ys[-1],
            "z_m": z_m,
        },
        "min_ppfd": min(values),
        "max_ppfd": max(values),
        "mean_ppfd": mean_ppfd,
        "target_ppfd": float(req.target_ppfd),
        "encoding": ENCODING,
        "colormap": {
            "name": "viridis",
            "source": "matplotlib",
            "normalization": "linear-clamped",
        },
        "color_scale": {
            "vmin": vmin,
            "vmax": vmax,
            "source": "simulation-visualization-mean-ppfd",
            "clamp": True,
        },
        "orientation": {
            "plane": "xy",
            "column_axis": "x",
            "row_axis": "y",
            "column_order": "ascending",
            "row_order": "ascending",
            "storage_order": "row-major",
            "value_index": "row * grid_width + column",
        },
        "warnings": warnings,
    }


def build_assembly_photometric_layer(workspace_root: Path, req: RadianceRunRequest) -> tuple[dict[str, Any], bytes]:
    if req.mode not in SUPPORTED_MODES:
        raise PhotometricLayerError(
            f"3D assembly photometric layer is not available for mode {req.mode!r}.",
            status_code=422,
            error="assembly_photometric_layer_unsupported",
        )
    rows = _parse_ppfd_map(workspace_root / SOURCE_ARTIFACT)
    xs, ys, values = _grid_values(rows)
    z_m, warnings = _z_m_and_warnings(rows)
    metadata = _metadata(req, xs, ys, z_m, values, warnings)
    binary = struct.pack(f"<{len(values)}f", *values)
    return metadata, binary
