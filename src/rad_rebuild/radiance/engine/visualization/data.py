from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray
import pandas as pd  # type: ignore[import-untyped]  # Third-party stubs absent; Phase 12.5C.2 owner, review before 2026-09-30.
from scipy.interpolate import griddata  # type: ignore[import-untyped]  # Third-party stubs absent; Phase 12.5C.2 owner, review before 2026-09-30.

FloatArray = NDArray[np.float64]


def load_ppfd_map(input_file: str | Path) -> pd.DataFrame:
    df = pd.read_csv(input_file, sep=r"\s+", header=None, names=["x", "y", "z", "ppfd"])
    print(f"Loaded {len(df)} total PPFD data points from '{input_file}'")
    return df


def export_ppfd_csv(df: pd.DataFrame, outdir: Path) -> None:
    try:
        csv_path = outdir / "ppfd_map.csv"
        df.to_csv(csv_path, index=False)
        print(f" -> Saved {csv_path.name}")
    except Exception as e:
        print(f"  ! CSV export failed: {e}")


def values_for_plane(
    df: pd.DataFrame, plane: str
) -> tuple[FloatArray, FloatArray, str, str]:
    if plane == "xy":
        return (
            np.asarray(df.x.values, dtype=np.float64),
            np.asarray(df.y.values, dtype=np.float64),
            "X (m)",
            "Y (m)",
        )
    if plane == "xz":
        return (
            np.asarray(df.x.values, dtype=np.float64),
            np.asarray(df.z.values, dtype=np.float64),
            "X (m)",
            "Z (m)",
        )
    return (
        np.asarray(df.y.values, dtype=np.float64),
        np.asarray(df.z.values, dtype=np.float64),
        "Y (m)",
        "Z (m)",
    )


def build_ppfd_grid(
    df: pd.DataFrame,
    u_vals: FloatArray,
    v_vals: FloatArray,
    zvals: FloatArray,
    grid_size: int,
) -> tuple[FloatArray, FloatArray, FloatArray]:
    df_r = df.copy()
    df_r["ur"] = np.round(u_vals, 6)
    df_r["vr"] = np.round(v_vals, 6)
    ptab = df_r.pivot_table(index="vr", columns="ur", values="ppfd", aggfunc="mean")
    if (ptab.shape[0] * ptab.shape[1] == len(df)) and not ptab.isna().any().any():
        print("Data forms a regular grid (via pivot)")
        x_centers = np.array(ptab.columns, dtype=float)
        y_centers = np.array(ptab.index, dtype=float)
        x_grid, y_grid = np.meshgrid(x_centers, y_centers)
        z_grid = np.asarray(ptab.values, dtype=np.float64)
    else:
        print(
            f"Data is irregular ({len(df)} pts); interpolating to {grid_size}×{grid_size}"
        )
        x_grid, y_grid = np.meshgrid(
            np.linspace(u_vals.min(), u_vals.max(), grid_size),
            np.linspace(v_vals.min(), v_vals.max(), grid_size),
        )
        interpolated_grid: FloatArray | None = None
        for method in ("cubic", "linear", "nearest"):
            try:
                interpolated_grid = np.asarray(
                    griddata((u_vals, v_vals), zvals, (x_grid, y_grid), method=method),
                    dtype=np.float64,
                )
                break
            except (RuntimeError, TypeError, ValueError):
                continue
        if interpolated_grid is None:
            z_grid = np.full_like(x_grid, np.nan, dtype=float)
        else:
            z_grid = interpolated_grid

    if np.all(np.isnan(z_grid)):
        print("Warning: grid is all NaN after interpolation; plots may be empty.")
    return x_grid, y_grid, z_grid


def print_grid_metrics(z_grid: FloatArray) -> tuple[float, float]:
    mean_ppfd = np.nanmean(z_grid)
    std_ppfd = np.nanstd(z_grid)
    if np.isfinite(mean_ppfd) and mean_ppfd != 0:
        dou = (1 - std_ppfd / mean_ppfd) * 100
        cv = (std_ppfd / mean_ppfd) * 100
        print(
            f"PPFD (Grid Z) → mean {mean_ppfd:.1f}, std {std_ppfd:.1f}, DOU {dou:.1f}%, CV {cv:.1f}%"
        )
    else:
        print("PPFD (Grid Z) → mean NaN")
    return float(mean_ppfd), float(std_ppfd)


def color_scale_for_mean(
    mean_ppfd: float, vmin: float, vmax: float
) -> tuple[float, float]:
    if np.isfinite(mean_ppfd):
        return max(0.0, mean_ppfd - 200.0), mean_ppfd + 200.0
    return vmin, vmax
