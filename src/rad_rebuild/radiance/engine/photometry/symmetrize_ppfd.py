#!/usr/bin/env python3
"""Symmetrize PPFD maps while preserving row order and coordinates."""

from __future__ import annotations

import argparse
import math
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

import numpy as np
import numpy.typing as npt

FloatArray: TypeAlias = npt.NDArray[np.float64]
GridIndex: TypeAlias = tuple[int, int]
PointKey: TypeAlias = tuple[float, float]


@dataclass(frozen=True)
class PpfdRow:
    x_m: float
    y_m: float
    z_m: float
    ppfd_umol_m2_s: float


@dataclass(frozen=True)
class SymmetryStats:
    mean_ppfd: float
    stddev_ppfd: float
    cv: float
    dou_percent: float


@dataclass(frozen=True)
class PpfdGrid:
    x_values_m: tuple[float, ...]
    y_values_m: tuple[float, ...]
    values: FloatArray
    index_by_point: dict[PointKey, GridIndex]
    center_x_m: float
    center_y_m: float


@dataclass(frozen=True)
class SymmetrizeOptions:
    input_path: Path
    output_path: Path | None = None
    lam: float = 1.0
    tol: float = 1e-6
    axes_only: bool = False


@dataclass(frozen=True)
class SymmetrizeResult:
    input_path: Path
    output_path: Path
    before: SymmetryStats
    after: SymmetryStats
    rows: int


def _rounded_key(x_m: float, y_m: float, tol: float) -> PointKey:
    return (round(x_m / tol) * tol, round(y_m / tol) * tol)


def _require_finite(value: float, *, label: str, line_number: int) -> float:
    if not math.isfinite(value):
        raise ValueError(f"non-finite {label} on PPFD row {line_number}")
    return value


def load_ppfd(path: Path) -> list[PpfdRow]:
    rows: list[PpfdRow] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) < 4:
                raise ValueError(f"PPFD row {line_number} has fewer than four columns")
            try:
                x_m = float(parts[0])
                y_m = float(parts[1])
                z_m = float(parts[2])
                ppfd = float(parts[3])
            except ValueError as exc:
                raise ValueError(
                    f"numeric PPFD row parse failed on row {line_number}"
                ) from exc
            rows.append(
                PpfdRow(
                    x_m=_require_finite(x_m, label="x", line_number=line_number),
                    y_m=_require_finite(y_m, label="y", line_number=line_number),
                    z_m=_require_finite(z_m, label="z", line_number=line_number),
                    ppfd_umol_m2_s=_require_finite(
                        ppfd, label="ppfd", line_number=line_number
                    ),
                )
            )
    if not rows:
        raise ValueError(f"no PPFD rows found in {path}")
    return rows


def build_grid(rows: list[PpfdRow], *, tol: float = 1e-6) -> PpfdGrid:
    if tol <= 0 or not math.isfinite(tol):
        raise ValueError("tol must be a positive finite value")
    x_values = tuple(sorted({row.x_m for row in rows}))
    y_values = tuple(sorted({row.y_m for row in rows}))
    center_x = 0.5 * (x_values[0] + x_values[-1])
    center_y = 0.5 * (y_values[0] + y_values[-1])
    x_index = {round(x / tol) * tol: index for index, x in enumerate(x_values)}
    y_index = {round(y / tol) * tol: index for index, y in enumerate(y_values)}
    values = np.full((len(y_values), len(x_values)), np.nan, dtype=np.float64)
    index_by_point: dict[PointKey, GridIndex] = {}
    for row in rows:
        key = _rounded_key(row.x_m, row.y_m, tol)
        i = x_index[key[0]]
        j = y_index[key[1]]
        if math.isfinite(float(values[j, i])):
            raise ValueError(f"duplicate PPFD coordinate at x={row.x_m}, y={row.y_m}")
        values[j, i] = row.ppfd_umol_m2_s
        index_by_point[key] = (i, j)
    return PpfdGrid(
        x_values_m=x_values,
        y_values_m=y_values,
        values=values,
        index_by_point=index_by_point,
        center_x_m=center_x,
        center_y_m=center_y,
    )


def d4_orbit(
    grid: PpfdGrid, index: GridIndex, *, tol: float = 1e-6, axes_only: bool = False
) -> tuple[GridIndex, ...]:
    i, j = index
    x_m = grid.x_values_m[i]
    y_m = grid.y_values_m[j]
    dx = x_m - grid.center_x_m
    dy = y_m - grid.center_y_m
    points: tuple[PointKey, ...] = (
        (grid.center_x_m + dx, grid.center_y_m + dy),
        (grid.center_x_m + dx, grid.center_y_m - dy),
        (grid.center_x_m - dx, grid.center_y_m + dy),
        (grid.center_x_m - dx, grid.center_y_m - dy),
    )
    if not axes_only:
        points = (
            *points,
            (grid.center_x_m + dy, grid.center_y_m + dx),
            (grid.center_x_m + dy, grid.center_y_m - dx),
            (grid.center_x_m - dy, grid.center_y_m + dx),
            (grid.center_x_m - dy, grid.center_y_m - dx),
        )
    seen: set[GridIndex] = set()
    orbit: list[GridIndex] = []
    for point in points:
        key = _rounded_key(point[0], point[1], tol)
        candidate = grid.index_by_point.get(key)
        if candidate is not None and candidate not in seen:
            seen.add(candidate)
            orbit.append(candidate)
    return tuple(orbit)


def compute_symmetry_stats(values: FloatArray) -> SymmetryStats:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        raise ValueError(
            "cannot compute symmetry statistics for an empty finite PPFD field"
        )
    mean = float(np.mean(finite))
    stddev = float(np.std(finite))
    cv = stddev / mean if mean > 0 else math.inf
    return SymmetryStats(
        mean_ppfd=mean, stddev_ppfd=stddev, cv=cv, dou_percent=100.0 * (1.0 - cv)
    )


def symmetrize_grid(
    grid: PpfdGrid, *, lam: float = 1.0, tol: float = 1e-6, axes_only: bool = False
) -> FloatArray:
    if not math.isfinite(lam):
        raise ValueError("lam must be finite")
    symmetrized = np.array(grid.values, copy=True)
    visited: set[GridIndex] = set()
    ny, nx = symmetrized.shape
    for j in range(ny):
        for i in range(nx):
            if (i, j) in visited:
                continue
            orbit = d4_orbit(grid, (i, j), tol=tol, axes_only=axes_only)
            orbit_values = [
                float(grid.values[orbit_j, orbit_i])
                for orbit_i, orbit_j in orbit
                if math.isfinite(float(grid.values[orbit_j, orbit_i]))
            ]
            if not orbit_values:
                continue
            average = float(np.mean(np.asarray(orbit_values, dtype=np.float64)))
            for orbit_i, orbit_j in orbit:
                visited.add((orbit_i, orbit_j))
                if math.isfinite(float(symmetrized[orbit_j, orbit_i])):
                    symmetrized[orbit_j, orbit_i] = (
                        lam * average + (1.0 - lam) * symmetrized[orbit_j, orbit_i]
                    )
    return symmetrized


def serialize_rows(rows: list[PpfdRow], grid: PpfdGrid, values: FloatArray) -> str:
    ppfd_by_point = {
        (grid.x_values_m[i], grid.y_values_m[j]): float(values[j, i])
        for j in range(len(grid.y_values_m))
        for i in range(len(grid.x_values_m))
    }
    return "".join(
        f"{row.x_m:.6f} {row.y_m:.6f} {row.z_m:.6f} {ppfd_by_point[(row.x_m, row.y_m)]:.6f}\n"
        for row in rows
    )


def run_symmetrization(options: SymmetrizeOptions) -> SymmetrizeResult:
    rows = load_ppfd(options.input_path)
    grid = build_grid(rows, tol=options.tol)
    before = compute_symmetry_stats(grid.values)
    symmetrized = symmetrize_grid(
        grid, lam=options.lam, tol=options.tol, axes_only=options.axes_only
    )
    after = compute_symmetry_stats(symmetrized)
    output_path = options.output_path or options.input_path
    output_path.write_text(serialize_rows(rows, grid, symmetrized), encoding="utf-8")
    return SymmetrizeResult(
        input_path=options.input_path,
        output_path=output_path,
        before=before,
        after=after,
        rows=len(rows),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="ppfd_map.txt")
    parser.add_argument("--output", default=None, help="default: overwrite input")
    parser.add_argument(
        "--lam", type=float, default=1.0, help="blend: 1.0=full sym, 0=off"
    )
    parser.add_argument("--tol", type=float, default=1e-6)
    parser.add_argument(
        "--axes-only",
        action="store_true",
        help="only x/y flips, no 90 degree rotations",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        result = run_symmetrization(
            SymmetrizeOptions(
                input_path=Path(args.input),
                output_path=Path(args.output) if args.output else None,
                lam=float(args.lam),
                tol=float(args.tol),
                axes_only=bool(args.axes_only),
            )
        )
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}")
        return 1
    if args.verbose:
        print(
            "Before: "
            f"mean={result.before.mean_ppfd:.2f} std={result.before.stddev_ppfd:.2f} "
            f"CV={result.before.cv * 100.0:.2f}% DOU={result.before.dou_percent:.2f}%"
        )
        print(
            "After : "
            f"mean={result.after.mean_ppfd:.2f} std={result.after.stddev_ppfd:.2f} "
            f"CV={result.after.cv * 100.0:.2f}% DOU={result.after.dou_percent:.2f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
