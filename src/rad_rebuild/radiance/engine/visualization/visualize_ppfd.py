#!/usr/bin/env python3
# visualize_ppfd.py • v4.2
# - Overlays: smd / spydr3 / hps / cob / both / none / auto
# - Conventional overlay reads runtime_state/spydr3_layout.json to draw true bar rectangles

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

import numpy as np

from rad_rebuild.radiance.engine.visualization.data import (
    build_ppfd_grid,
    color_scale_for_mean,
    export_ppfd_csv,
    load_ppfd_map,
    print_grid_metrics,
    values_for_plane,
)
from rad_rebuild.radiance.engine.visualization.heatmaps import (
    save_annotated_heatmap,
    save_overlay_heatmap,
    save_secondary_plots,
)
from rad_rebuild.radiance.engine.visualization.overlays import load_overlay_context
from rad_rebuild.radiance.engine.visualization.scatter import write_scatter_html
from rad_rebuild.radiance.engine.visualization.style import configure_visual_style


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="PPFD visualizations with unified color scaling."
    )
    parser.add_argument("--input", default="ppfd_map.txt")
    default_outdir = (
        Path(os.getenv("RADIANCE_VISUALIZATION_OUTPUT_ROOT", "."))
        / "ppfd_visualizations_proposed"
    )
    parser.add_argument("--outdir", default=str(default_outdir))
    parser.add_argument(
        "--grid-size",
        type=int,
        default=15,
        help="Interpolation grid when data not perfectly gridded",
    )
    parser.add_argument(
        "--vmin", type=float, default=0.0, help="Lower color/z limit for plots"
    )
    parser.add_argument(
        "--vmax", type=float, default=1750.0, help="Upper color/z limit for plots"
    )
    parser.add_argument("--cmap", default="viridis")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument(
        "--skip-csv",
        action="store_true",
        help="Skip exporting ppfd_map.csv when only web visuals are needed",
    )
    parser.add_argument(
        "--skip-secondary-plots",
        action="store_true",
        help="Skip non-UI plots like histogram and deviation map",
    )
    parser.add_argument(
        "--skip-scatter",
        action="store_true",
        help="Skip generating the interactive 3D scatter HTML",
    )
    parser.add_argument(
        "--scatter-only",
        action="store_true",
        help="Generate only the interactive 3D scatter HTML",
    )
    parser.add_argument(
        "--annot",
        dest="annot",
        action="store_true",
        help="Annotate seaborn heatmap cells",
    )
    parser.add_argument(
        "--no-annot",
        dest="annot",
        action="store_false",
        help="Disable annotations on heatmap",
    )
    parser.set_defaults(annot=True)
    parser.add_argument(
        "--overlay",
        choices=["auto", "smd", "spydr3", "hps", "cob", "both", "none"],
        default="auto",
        help="Which hardware overlay to draw on heatmaps",
    )
    parser.add_argument(
        "--plane",
        choices=["xy", "xz", "yz"],
        default="xy",
        help="Projection plane for heatmaps (xy, xz, or yz)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    configure_visual_style()

    input_file = args.input
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    vmin = float(args.vmin)
    vmax = float(args.vmax)

    overlay_context = load_overlay_context(args.overlay, args.plane)

    try:
        df = load_ppfd_map(input_file)
    except Exception as e:
        print(f"Error reading '{input_file}': {e}")
        return 1
    if df.empty:
        print("Error: PPFD file is empty.")
        return 1

    if not args.skip_csv and not args.scatter_only:
        export_ppfd_csv(df, outdir)

    zvals = np.asarray(df.ppfd, dtype=np.float64)
    u_vals, v_vals, u_label, v_label = values_for_plane(df, args.plane)
    x_grid, y_grid, z_grid = build_ppfd_grid(df, u_vals, v_vals, zvals, args.grid_size)

    mean_ppfd, _std_ppfd = print_grid_metrics(z_grid)
    vmin, vmax = color_scale_for_mean(mean_ppfd, vmin, vmax)
    print(f"Color scale: vmin={vmin:.1f}, vmax={vmax:.1f}, cmap={args.cmap}")

    if args.scatter_only:
        print("\nGenerating interactive 3D scatter only...")
        try:
            write_scatter_html(
                df,
                u_vals,
                v_vals,
                u_label,
                v_label,
                mean_ppfd,
                outdir,
                vmin,
                vmax,
                args.cmap,
                overlay_context,
            )
        except Exception as e:
            print("  ! Interactive scatter failed:", e)
            return 1
        print(f"\nInteractive scatter saved to '{outdir}'")
        return 0

    print("\nGenerating visualizations...")

    z_clipped = np.clip(z_grid, vmin, vmax)
    save_annotated_heatmap(
        z_grid,
        x_grid,
        y_grid,
        u_label,
        v_label,
        outdir,
        vmin,
        vmax,
        args.cmap,
        args.dpi,
        bool(args.annot),
    )
    save_overlay_heatmap(
        x_grid,
        y_grid,
        z_clipped,
        u_label,
        v_label,
        outdir,
        vmin,
        vmax,
        args.cmap,
        args.dpi,
        overlay_context,
    )

    if not args.skip_secondary_plots:
        save_secondary_plots(
            zvals, z_grid, x_grid, y_grid, mean_ppfd, u_label, v_label, outdir, args.dpi
        )

    if not args.skip_scatter:
        try:
            write_scatter_html(
                df,
                u_vals,
                v_vals,
                u_label,
                v_label,
                mean_ppfd,
                outdir,
                vmin,
                vmax,
                args.cmap,
                overlay_context,
            )
        except Exception as e:
            print("  ! Interactive scatter failed:", e)

    print(f"\nAll visualizations saved to '{outdir.as_posix()}'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
