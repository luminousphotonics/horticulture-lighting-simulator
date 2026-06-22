from __future__ import annotations

from pathlib import Path

import numpy as np
from numpy.typing import NDArray
import pandas as pd  # type: ignore[import-untyped]  # Third-party stubs absent; Phase 12.5C.2 owner, review before 2026-09-30.
import plotly.graph_objects as go  # type: ignore[import-untyped]  # Plotly lacks bundled typing; Phase 12.5C.2 owner, review before 2026-09-30.

from .overlays import OverlayContext
from .style import plotly_font

FloatArray = NDArray[np.float64]


def build_scatter_figure(
    df: pd.DataFrame,
    u_vals: FloatArray,
    v_vals: FloatArray,
    u_label: str,
    v_label: str,
    mean_ppfd_for_scale: float,
    vmin: float,
    vmax: float,
    cmap: str,
    overlay_context: OverlayContext,
) -> go.Figure:
    scatter_vmin = vmin
    scatter_vmax = vmax
    if np.isfinite(mean_ppfd_for_scale):
        scatter_vmin = max(0.0, float(mean_ppfd_for_scale) - 200.0)
        scatter_vmax = float(mean_ppfd_for_scale) + 200.0

    fig = go.Figure(
        data=[
            go.Scatter3d(
                x=u_vals,
                y=v_vals,
                z=df.ppfd,
                name="PPFD samples",
                mode="markers",
                showlegend=False,
                hovertemplate=(
                    f"{u_label}: %{{x:.2f}}<br>"
                    f"{v_label}: %{{y:.2f}}<br>"
                    "PPFD: %{z:.1f} µmol/m²/s<extra></extra>"
                ),
                marker=dict(
                    size=4,
                    color=df.ppfd,
                    colorscale=cmap,
                    cmin=scatter_vmin,
                    cmax=scatter_vmax,
                    showscale=True,
                    colorbar=dict(
                        title=dict(text=""),
                        thickness=18,
                        len=0.72,
                        y=0.48,
                        x=1.02,
                    ),
                ),
            )
        ]
    )
    for name, pts, _ in overlay_context.points:
        projected = [overlay_context.project_point(point) for point in pts]
        xs = [point[0] for point in projected if point is not None]
        ys = [point[1] for point in projected if point is not None]
        zs = [0] * len(xs)
        fig.add_trace(
            go.Scatter3d(
                x=xs,
                y=ys,
                z=zs,
                mode="markers",
                marker=dict(size=6, color="black", symbol="square-open"),
                name=name,
                showlegend=False,
                hovertemplate=(
                    f"{name}<br>"
                    f"{u_label}: %{{x:.2f}}<br>"
                    f"{v_label}: %{{y:.2f}}<extra></extra>"
                ),
            )
        )
    if overlay_context.room_bounds and overlay_context.plane == "xy":
        room_x, room_y = overlay_context.room_bounds
        x_span = float(room_x)
        y_span = float(room_y)
    else:
        x_span = float(np.nanmax(u_vals) - np.nanmin(u_vals)) if len(u_vals) else 1.0
        y_span = float(np.nanmax(v_vals) - np.nanmin(v_vals)) if len(v_vals) else 1.0
    z_span = float(np.nanmax(df.ppfd) - np.nanmin(df.ppfd)) if len(df.ppfd) else 1.0
    horiz = max(x_span, y_span, 1e-9)
    aspectratio = dict(
        x=max(x_span / horiz, 0.05),
        y=max(y_span / horiz, 0.05),
        z=max(0.22, min(0.7, z_span / horiz)),
    )
    fig.update_layout(
        title=f"Interactive 3D PPFD Scatter (µmol/m²/s, clamped {scatter_vmin:.0f}–{scatter_vmax:.0f})",
        font=plotly_font(),
        showlegend=False,
        scene=dict(
            xaxis_title=u_label,
            yaxis_title=v_label,
            zaxis_title="PPFD (µmol/m²/s)",
            aspectmode="manual",
            aspectratio=aspectratio,
        ),
        margin=dict(l=20, r=95, t=70, b=20),
        width=900,
        height=650,
    )
    return fig


def write_scatter_html(
    df: pd.DataFrame,
    u_vals: FloatArray,
    v_vals: FloatArray,
    u_label: str,
    v_label: str,
    mean_ppfd_for_scale: float,
    outdir: Path,
    vmin: float,
    vmax: float,
    cmap: str,
    overlay_context: OverlayContext,
) -> None:
    fig = build_scatter_figure(
        df,
        u_vals,
        v_vals,
        u_label,
        v_label,
        mean_ppfd_for_scale,
        vmin,
        vmax,
        cmap,
        overlay_context,
    )
    fig.write_html(outdir / "ppfd_scatter_3d.html")
    print(" -> Saved ppfd_scatter_3d.html")
