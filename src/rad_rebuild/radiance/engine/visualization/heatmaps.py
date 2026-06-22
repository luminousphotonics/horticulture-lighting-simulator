from __future__ import annotations

from pathlib import Path
from typing import Any

from matplotlib.axes import Axes
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon
import numpy as np
from numpy.typing import NDArray

from .overlays import OverlayContext

FloatArray = NDArray[np.float64]
BoolArray = NDArray[np.bool_]
Bounds = tuple[float, float, float, float]


def _grid_edges_1d(centers: FloatArray | list[float]) -> FloatArray:
    vals = np.asarray(centers, dtype=float)
    if vals.size <= 1:
        step = 0.1
        c = float(vals[0]) if vals.size == 1 else 0.0
        return np.array([c - step * 0.5, c + step * 0.5], dtype=float)
    mids = 0.5 * (vals[:-1] + vals[1:])
    first = vals[0] - 0.5 * (vals[1] - vals[0])
    last = vals[-1] + 0.5 * (vals[-1] - vals[-2])
    return np.concatenate(([first], mids, [last]))


def _room_aligned_edges_1d(
    centers: FloatArray | list[float], span: float | None
) -> FloatArray:
    edges = _grid_edges_1d(centers)
    if span is None or span <= 0 or edges.size < 2:
        return edges

    room_min = -0.5 * float(span)
    room_max = 0.5 * float(span)
    room_width = room_max - room_min
    edge_width = float(edges[-1] - edges[0])
    if room_width <= 0 or edge_width <= 0:
        return edges

    diffs = np.diff(np.asarray(centers, dtype=float))
    typical_step = float(np.nanmedian(np.abs(diffs))) if diffs.size else edge_width
    tolerance = max(typical_step, room_width * 0.03, 0.02)
    if abs(edges[0] - room_min) <= tolerance and abs(edges[-1] - room_max) <= tolerance:
        aligned = edges.copy()
        aligned[0] = room_min
        aligned[-1] = room_max
        return aligned
    return edges


def _edges_match_span(edges: FloatArray | list[float], span: float | None) -> bool:
    vals = np.asarray(edges, dtype=float)
    if span is None or span <= 0 or vals.size < 2:
        return False
    edge_span = float(vals[-1] - vals[0])
    if edge_span <= 0:
        return False
    return abs(edge_span - float(span)) <= max(edge_span, float(span)) * 0.05


def _annotation_fontsize(values: FloatArray) -> float:
    arr = np.asarray(values, dtype=float)
    finite = arr[np.isfinite(arr)]
    vmax = float(np.nanmax(finite)) if finite.size else 0.0
    digits = len(str(int(round(abs(vmax))))) if vmax > 0 else 1
    rows, cols = arr.shape if arr.ndim == 2 else (1, int(arr.size or 1))
    dense_dim = max(rows, cols)
    size = 5.8
    if digits >= 4:
        size -= 0.9
    if digits >= 5:
        size -= 0.4
    if dense_dim >= 20:
        size -= 0.5
    if dense_dim >= 28:
        size -= 0.4
    if dense_dim >= 36:
        size -= 0.2
    return max(3.8, size)


def _annotation_mask(values: FloatArray, dense_threshold: int = 20) -> BoolArray:
    arr = np.asarray(values)
    if arr.ndim != 2:
        return np.ones(arr.shape, dtype=np.bool_)
    rows, cols = arr.shape
    if max(rows, cols) < dense_threshold:
        return np.ones((rows, cols), dtype=np.bool_)

    cy = rows // 2
    cx = cols // 2
    yy, xx = np.indices((rows, cols))
    mask: BoolArray = ((yy - cy) + (xx - cx)) % 2 == 0
    return mask


def save_annotated_heatmap(
    z_grid: FloatArray,
    x_grid: FloatArray,
    y_grid: FloatArray,
    u_label: str,
    v_label: str,
    outdir: Path,
    vmin: float,
    vmax: float,
    cmap: str,
    dpi: int,
    annotate: bool,
) -> None:
    fig = None
    try:
        z_for_annot = z_grid
        x_annot = np.array(x_grid[0, :], dtype=float) if x_grid is not None else None
        y_annot = np.array(y_grid[:, 0], dtype=float) if y_grid is not None else None

        fig, ax = plt.subplots(figsize=(8, 6))
        z_plot = np.clip(z_for_annot, vmin, vmax)
        if x_annot is None or y_annot is None:
            raise ValueError("Missing coordinate grid for annotated heatmap")
        xu_e = _grid_edges_1d(x_annot)
        yu_e = _grid_edges_1d(y_annot)
        pc = ax.pcolormesh(
            xu_e,
            yu_e,
            z_plot,
            cmap=cmap,
            edgecolors="gray",
            linewidth=0.5,
            shading="auto",
            vmin=vmin,
            vmax=vmax,
        )
        fig.colorbar(pc, ax=ax, label="PPFD (µmol/m²/s)")
        if annotate:
            annot_fontsize = _annotation_fontsize(z_for_annot)
            annot_mask = _annotation_mask(z_for_annot)
            for iy, yv in enumerate(y_annot):
                for ix, xv in enumerate(x_annot):
                    if not annot_mask[iy, ix]:
                        continue
                    try:
                        val = float(z_for_annot[iy, ix])
                    except (IndexError, TypeError, ValueError):
                        continue
                    ax.text(
                        xv,
                        yv,
                        f"{val:.0f}",
                        ha="center",
                        va="center",
                        fontsize=annot_fontsize,
                        color="black",
                        bbox=dict(
                            facecolor="white", alpha=0.4, edgecolor="none", pad=0.45
                        ),
                    )
        ax.set_title("PPFD Heatmap (annotated)" if annotate else "PPFD Heatmap")
        ax.set_xlabel(u_label)
        ax.set_ylabel(v_label)
        ax.set_xlim(xu_e[0], xu_e[-1])
        ax.set_ylim(yu_e[0], yu_e[-1])
        ax.set_aspect("equal", adjustable="box")
        fig.tight_layout()
        fig.savefig(outdir / "ppfd_heatmap_annotated.png", dpi=dpi)
        print(" -> Saved ppfd_heatmap_annotated.png")
    except (OSError, RuntimeError, ValueError) as exc:
        print("  ! Annotated heatmap failed:", exc)
        fallback_fig = None
        try:
            fallback_fig, ax = plt.subplots(figsize=(8, 6))
            ax.imshow(
                np.clip(z_grid, vmin, vmax),
                cmap=cmap,
                vmin=vmin,
                vmax=vmax,
                origin="lower",
                aspect="auto",
            )
            ax.set_title("Annotated heatmap unavailable")
            ax.set_xlabel(u_label)
            ax.set_ylabel(v_label)
            fallback_fig.savefig(outdir / "ppfd_heatmap_annotated.png", dpi=dpi)
            print(" -> Saved ppfd_heatmap_annotated.png (fallback)")
        except (OSError, RuntimeError, ValueError) as fallback_exc:
            print("  ! Annotated heatmap fallback failed:", fallback_exc)
        finally:
            if fallback_fig is not None:
                plt.close(fallback_fig)
    finally:
        if fig is not None:
            plt.close(fig)


def _expand_bounds(bounds: Bounds, xs: list[float], ys: list[float]) -> Bounds:
    if not xs or not ys:
        return bounds
    xmin, xmax, ymin, ymax = bounds
    return (
        min(xmin, min(xs)),
        max(xmax, max(xs)),
        min(ymin, min(ys)),
        max(ymax, max(ys)),
    )


def _draw_polygon_layers(
    ax: Axes, overlay_context: OverlayContext, *, draw_overlay: bool, bounds: Bounds
) -> Bounds:
    if not draw_overlay:
        return bounds
    next_bounds = bounds
    for name, polys, kwargs in overlay_context.polys:
        for poly in polys:
            xy = overlay_context.project_poly(poly)
            if len(xy) < 3:
                continue
            ax.add_patch(Polygon(xy, **kwargs))
            xs_p, ys_p = zip(*xy)
            next_bounds = _expand_bounds(next_bounds, list(xs_p), list(ys_p))
        ax.plot([], [], color="black", label=f"{name} ({len(polys)})")
    return next_bounds


def _draw_line_layers(
    ax: Axes, overlay_context: OverlayContext, *, draw_overlay: bool, bounds: Bounds
) -> Bounds:
    if not draw_overlay:
        return bounds
    xmin, xmax, ymin, ymax = bounds
    for name, lines, kwargs in overlay_context.lines:
        line_kwargs: dict[str, Any] = dict(kwargs)
        legend_count = line_kwargs.pop("legend_count", len(lines))
        for a, b in lines:
            pa = overlay_context.project_point(a)
            pb = overlay_context.project_point(b)
            if pa is None or pb is None:
                continue
            ax.plot([pa[0], pb[0]], [pa[1], pb[1]], **line_kwargs)
            xmin = min(xmin, pa[0], pb[0])
            xmax = max(xmax, pa[0], pb[0])
            ymin = min(ymin, pa[1], pb[1])
            ymax = max(ymax, pa[1], pb[1])
        if name:
            ax.plot(
                [],
                [],
                color=line_kwargs.get("color", "cyan"),
                label=f"{name} ({legend_count})",
            )
    return xmin, xmax, ymin, ymax


def _draw_point_layers(
    ax: Axes, overlay_context: OverlayContext, *, draw_overlay: bool, bounds: Bounds
) -> Bounds:
    if not draw_overlay:
        return bounds
    next_bounds = bounds
    for name, pts, kwargs in overlay_context.points:
        projected = [overlay_context.project_point(point) for point in pts]
        xs = [point[0] for point in projected if point is not None]
        ys = [point[1] for point in projected if point is not None]
        ax.scatter(xs, ys, **kwargs, label=f"{name} ({len(pts)})")
        next_bounds = _expand_bounds(next_bounds, xs, ys)
    return next_bounds


def _room_bounds_or_current(
    overlay_context: OverlayContext, *, room_bounds_match_grid: bool, bounds: Bounds
) -> Bounds:
    if (
        overlay_context.room_bounds
        and overlay_context.plane == "xy"
        and room_bounds_match_grid
    ):
        length, width = overlay_context.room_bounds
        return -length * 0.5, length * 0.5, -width * 0.5, width * 0.5
    return bounds


def _apply_overlay_axes(
    ax: Axes,
    overlay_context: OverlayContext,
    *,
    draw_overlay: bool,
    bounds: Bounds,
    u_label: str,
    v_label: str,
) -> None:
    xmin, xmax, ymin, ymax = bounds
    pad_x = max(0.0, 0.005 * (xmax - xmin))
    pad_y = max(0.0, 0.005 * (ymax - ymin))
    ax.set_xlim(xmin - pad_x, xmax + pad_x)
    ax.set_ylim(ymin - pad_y, ymax + pad_y)
    if draw_overlay and (overlay_context.polys or overlay_context.points):
        ax.legend(
            loc="lower center", bbox_to_anchor=(0.5, 1.20), ncol=2, framealpha=0.9
        )
    ax.set_title("PPFD Heatmap with Overlay", y=1.08)
    ax.set_xlabel(u_label)
    ax.set_ylabel(v_label)
    ax.set_aspect("equal", adjustable="box")


def save_overlay_heatmap(
    x_grid: FloatArray,
    y_grid: FloatArray,
    z_clipped: FloatArray,
    u_label: str,
    v_label: str,
    outdir: Path,
    vmin: float,
    vmax: float,
    cmap: str,
    dpi: int,
    overlay_context: OverlayContext,
) -> None:
    fig = None
    try:
        room_length = None
        room_width = None
        if overlay_context.room_bounds and overlay_context.plane == "xy":
            room_length, room_width = overlay_context.room_bounds
        xu_e = _room_aligned_edges_1d(x_grid[0, :], room_length)
        yu_e = _room_aligned_edges_1d(y_grid[:, 0], room_width)
        room_bounds_match_grid = (
            overlay_context.plane == "xy"
            and _edges_match_span(xu_e, room_length)
            and _edges_match_span(yu_e, room_width)
        )
        draw_overlay = (
            not overlay_context.room_bounds
            or overlay_context.plane != "xy"
            or room_bounds_match_grid
        )
        if (
            overlay_context.room_bounds
            and overlay_context.plane == "xy"
            and not room_bounds_match_grid
        ):
            print(
                "Overlay skipped: layout room bounds do not match the PPFD grid extent."
            )

        fig, ax = plt.subplots(figsize=(8, 6))
        pc = ax.pcolormesh(
            xu_e,
            yu_e,
            z_clipped,
            cmap=cmap,
            edgecolors="gray",
            linewidth=0.5,
            shading="auto",
            vmin=vmin,
            vmax=vmax,
        )
        fig.colorbar(pc, label="PPFD (µmol/m²/s)")
        bounds: Bounds = (
            float(xu_e[0]),
            float(xu_e[-1]),
            float(yu_e[0]),
            float(yu_e[-1]),
        )

        bounds = _draw_polygon_layers(
            ax, overlay_context, draw_overlay=draw_overlay, bounds=bounds
        )
        bounds = _draw_line_layers(
            ax, overlay_context, draw_overlay=draw_overlay, bounds=bounds
        )
        bounds = _draw_point_layers(
            ax, overlay_context, draw_overlay=draw_overlay, bounds=bounds
        )
        bounds = _room_bounds_or_current(
            overlay_context,
            room_bounds_match_grid=room_bounds_match_grid,
            bounds=bounds,
        )
        _apply_overlay_axes(
            ax,
            overlay_context,
            draw_overlay=draw_overlay,
            bounds=bounds,
            u_label=u_label,
            v_label=v_label,
        )
        fig.tight_layout()
        fig.savefig(outdir / "ppfd_heatmap_overlay.png", dpi=dpi)
        print(" -> Saved ppfd_heatmap_overlay.png")
    except (OSError, RuntimeError, ValueError) as exc:
        print("  ! Heatmap with overlay failed:", exc)
    finally:
        if fig is not None:
            plt.close(fig)


def save_secondary_plots(
    zvals: FloatArray,
    z_grid: FloatArray,
    x_grid: FloatArray,
    y_grid: FloatArray,
    mean_ppfd: float,
    u_label: str,
    v_label: str,
    outdir: Path,
    dpi: int,
) -> None:
    fig = None
    try:
        fig = plt.figure(figsize=(8, 6))
        vals = zvals[np.isfinite(zvals)]
        plt.hist(vals, bins=30, edgecolor="black")
        if np.isfinite(mean_ppfd):
            plt.axvline(
                mean_ppfd, color="red", linestyle="--", label=f"Mean {mean_ppfd:.1f}"
            )
        plt.title("PPFD Distribution")
        plt.xlabel("PPFD (µmol/m²/s)")
        plt.ylabel("Frequency")
        plt.legend()
        plt.tight_layout()
        plt.savefig(outdir / "ppfd_histogram.png", dpi=dpi)
        print(" -> Saved ppfd_histogram.png")
    except (OSError, RuntimeError, ValueError) as exc:
        print("  ! Histogram failed:", exc)
    finally:
        if fig is not None:
            plt.close(fig)

    fig = None
    try:
        if np.isfinite(mean_ppfd):
            z_dev = np.abs(z_grid - mean_ppfd)
            fig = plt.figure(figsize=(8, 6))
            levels = 20
            cd = plt.contourf(x_grid, y_grid, z_dev, cmap="RdYlBu", levels=levels)
            plt.colorbar(cd, label="|PPFD – mean|")
            plt.title("PPFD Absolute Deviation from Mean")
            plt.xlabel(u_label)
            plt.ylabel(v_label)
            plt.gca().set_aspect("equal", adjustable="box")
            plt.tight_layout()
            plt.savefig(outdir / "ppfd_deviation.png", dpi=dpi)
            print(" -> Saved ppfd_deviation.png")
    except (OSError, RuntimeError, ValueError) as exc:
        print("  ! Deviation plot failed:", exc)
    finally:
        if fig is not None:
            plt.close(fig)
