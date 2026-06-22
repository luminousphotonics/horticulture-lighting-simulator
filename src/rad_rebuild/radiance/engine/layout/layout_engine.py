#!/usr/bin/env python3
from __future__ import annotations

import io
from collections import Counter
from html import escape
from math import sqrt
from typing import cast

from PIL import Image, ImageDraw, ImageFont

from rad_rebuild.radiance.engine.layout.domain import ModuleTuple, PointTuple
from rad_rebuild.radiance.engine.layout.layout_generator import generate_layout

PRICES = {
    "L": 500,
    "reverse_L": 500,
    "linear4": 350,
    "linear3": 250,
    "linear2": 150,
    "centerpiece": 550,
}

PNG_EXPORT_DPI = 600
PNG_EXPORT_MAX_EDGE_PX = 7200
TRACE_COLOR = "#1f2937"
NODE_FILL = "#ffffff"
NODE_STROKE = "#111827"
TITLE_COLOR = "#111827"
SUBTITLE_COLOR = "#475569"

MODULE_LABELS = {
    "centerpiece": "Centerpiece",
    "L": "L Module",
    "reverse_L": "Reverse-L Module",
    "linear4": "Linear 4",
    "linear3": "Linear 3",
    "linear2": "Linear 2",
}

MODULE_STYLES = {
    "centerpiece": {"stroke": "#111827", "stroke_width": 5.8, "dasharray": ""},
    "L": {"stroke": "#1f2937", "stroke_width": 4.8, "dasharray": ""},
    "reverse_L": {"stroke": "#374151", "stroke_width": 4.8, "dasharray": "13 8"},
    "linear4": {"stroke": "#4b5563", "stroke_width": 4.0, "dasharray": ""},
    "linear3": {"stroke": "#6b7280", "stroke_width": 4.0, "dasharray": "11 7"},
    "linear2": {"stroke": "#6b7280", "stroke_width": 3.6, "dasharray": "2.5 6.5"},
}


def _sorted_positions(positions: list[tuple[float, float]]) -> list[tuple[float, float]]:
    return sorted(positions, key=lambda p: (p[1], p[0]))


def _format_number(value: float) -> str:
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _format_feet(value: float) -> str:
    return f"{value:g}"


def _project_point(x: float, y: float, scale: float) -> tuple[float, float]:
    return (scale * (x + y), scale * (x - y))


def _layout_positions(layout: dict[str, object]) -> list[PointTuple]:
    return cast(list[PointTuple], layout["all_positions"])


def _layout_module_groups(layout: dict[str, object]) -> list[ModuleTuple]:
    return cast(list[ModuleTuple], layout["module_groups"])


def _layout_figure_geometry(all_positions: list[PointTuple]) -> dict[str, object]:
    scale = 34 / sqrt(2)
    plot_points = [_project_point(x, y, scale) for x, y in all_positions]
    min_plot_x = min(point[0] for point in plot_points)
    min_plot_y = min(point[1] for point in plot_points)
    max_plot_x = max(point[0] for point in plot_points)
    max_plot_y = max(point[1] for point in plot_points)

    min_x = min_plot_x - 88
    min_y = min_plot_y - 134
    max_x = max_plot_x + 88
    max_y = max_plot_y + 84
    view_width = max_x - min_x
    view_height = max_y - min_y
    return {
        "scale": scale,
        "plot_points": plot_points,
        "min_x": min_x,
        "min_y": min_y,
        "view_width": view_width,
        "view_height": view_height,
    }


def _load_font(size_px: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ("Arial Bold.ttf", "Arial.ttf", "DejaVuSans-Bold.ttf", "LiberationSans-Bold.ttf")
        if bold
        else ("Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf")
    )
    for name in candidates:
        try:
            return ImageFont.truetype(name, size_px)
        except OSError:
            continue
    return ImageFont.load_default()


def _module_style_attrs(module_type: str) -> str:
    style = MODULE_STYLES.get(module_type, MODULE_STYLES["linear2"])
    stroke_width = cast(float, style["stroke_width"])
    dasharray = str(style["dasharray"])
    attrs = [
        f'stroke="{style["stroke"]}"',
        f'stroke-width="{_format_number(stroke_width)}"',
        'fill="none"',
        'stroke-linecap="round"',
        'stroke-linejoin="round"',
        'vector-effect="non-scaling-stroke"',
    ]
    if dasharray:
        attrs.append(f'stroke-dasharray="{dasharray}"')
    return " ".join(attrs)


def _module_markup(module_type: str, points: list[tuple[float, float]]) -> list[str]:
    attrs = _module_style_attrs(module_type)
    if module_type == "centerpiece":
        cx, cy = points[0]
        return [
            f'<line x1="{_format_number(cx)}" y1="{_format_number(cy)}" '
            f'x2="{_format_number(px)}" y2="{_format_number(py)}" {attrs} />'
            for px, py in points[1:]
        ]

    if "linear" in module_type:
        if len(points) < 2:
            return []
        path = " ".join(f"{_format_number(x)},{_format_number(y)}" for x, y in points)
        return [f'<polyline points="{path}" {attrs} />']

    if module_type == "L":
        long_leg = points[1:4]
        short_leg = points[0:2]
    else:
        long_leg = points[0:3]
        short_leg = points[2:4]

    long_path = " ".join(f"{_format_number(x)},{_format_number(y)}" for x, y in long_leg)
    short_path = " ".join(f"{_format_number(x)},{_format_number(y)}" for x, y in short_leg)
    return [
        f'<polyline points="{long_path}" {attrs} />',
        f'<polyline points="{short_path}" {attrs} />',
    ]


def _legend_sample(module_type: str, x: float, y: float) -> list[str]:
    if module_type == "centerpiece":
        sample_points = [(x, y), (x + 22, y - 12), (x + 22, y + 12), (x + 46, y)]
    elif module_type == "L":
        sample_points = [(x, y + 10), (x + 22, y + 10), (x + 44, y + 10), (x + 44, y - 12)]
    elif module_type == "reverse_L":
        sample_points = [(x, y - 12), (x, y + 10), (x + 22, y + 10), (x + 44, y + 10)]
    elif module_type == "linear4":
        sample_points = [(x, y), (x + 14, y), (x + 28, y), (x + 42, y)]
    elif module_type == "linear3":
        sample_points = [(x, y), (x + 18, y), (x + 36, y)]
    else:
        sample_points = [(x, y), (x + 28, y)]
    return _module_markup(module_type, sample_points)


def _build_download_name(length_ft: float, width_ft: float) -> str:
    def clean(value: float) -> str:
        return _format_feet(value).replace(".", "p")

    return f"horticultural-layout-{clean(length_ft)}ft-x-{clean(width_ft)}ft.svg"


def build_layout_svg(
    all_positions: list[tuple[float, float]],
    module_groups: list[tuple[str, str, list[tuple[float, float]]]],
    *,
    length_ft: float,
    width_ft: float,
    cv_enabled: bool = False,
) -> str:
    if not all_positions:
        return '<svg id="layout-svg" xmlns="http://www.w3.org/2000/svg"></svg>'

    scale = 32 / sqrt(2)
    plot_points = [_project_point(x, y, scale) for x, y in all_positions]
    room_corners = [
        _project_point(-length_ft / 2, -width_ft / 2, scale),
        _project_point(length_ft / 2, -width_ft / 2, scale),
        _project_point(length_ft / 2, width_ft / 2, scale),
        _project_point(-length_ft / 2, width_ft / 2, scale),
    ]

    all_geometry = plot_points + room_corners
    geom_min_x = min(point[0] for point in all_geometry)
    geom_min_y = min(point[1] for point in all_geometry)
    geom_max_x = max(point[0] for point in all_geometry)
    geom_max_y = max(point[1] for point in all_geometry)

    legend_width = 320
    min_x = geom_min_x - 96
    min_y = geom_min_y - 146
    max_x = geom_max_x + legend_width
    max_y = geom_max_y + 118
    view_width = max_x - min_x
    view_height = max_y - min_y

    figure_title = escape(
        f"Horticultural Fixture Layout ({_format_feet(length_ft)} ft × {_format_feet(width_ft)} ft)"
    )
    figure_desc = escape(
        "Publication-style SVG export showing the room boundary, emitter positions, "
        "and grouped fixture traces for the generated horticultural lighting layout."
    )
    room_polygon = " ".join(
        f"{_format_number(x)},{_format_number(y)}" for x, y in room_corners
    )
    length_axis = [
        _project_point(-length_ft / 2, 0, scale),
        _project_point(length_ft / 2, 0, scale),
    ]
    width_axis = [
        _project_point(0, -width_ft / 2, scale),
        _project_point(0, width_ft / 2, scale),
    ]
    module_counts = Counter(module_type for module_type, _orient, _cobs in module_groups)

    svg = [
        f'<svg id="layout-svg" viewBox="{_format_number(min_x)} {_format_number(min_y)} '
        f'{_format_number(view_width)} {_format_number(view_height)}" '
        'xmlns="http://www.w3.org/2000/svg" role="img" aria-labelledby="layout-title layout-desc" '
        'preserveAspectRatio="xMidYMid meet">',
        f"<title id=\"layout-title\">{figure_title}</title>",
        f"<desc id=\"layout-desc\">{figure_desc}</desc>",
        "<style>",
        "#layout-svg { background: #ffffff; color: #111827; font-family: 'Helvetica Neue', Arial, sans-serif; }",
        ".sheet { fill: #ffffff; }",
        ".frame { fill: none; stroke: #d7dde5; stroke-width: 1.5; }",
        ".room-panel { fill: #f8fafc; stroke: #d7dde5; stroke-width: 1.5; }",
        ".room-boundary { fill: #fbfdff; stroke: #0f172a; stroke-width: 3; vector-effect: non-scaling-stroke; }",
        ".room-axis { stroke: #cbd5e1; stroke-width: 1.75; stroke-dasharray: 8 8; vector-effect: non-scaling-stroke; }",
        ".layout-node { fill: #ffffff; stroke: #111827; stroke-width: 1.8; vector-effect: non-scaling-stroke; }",
        ".cv-path { fill: none; stroke: #be123c; stroke-width: 2.2; stroke-dasharray: 6 6; vector-effect: non-scaling-stroke; }",
        ".cv-marker { fill: #be123c; stroke: #ffffff; stroke-width: 1.5; vector-effect: non-scaling-stroke; }",
        ".figure-title { font-size: 28px; font-weight: 700; fill: #111827; letter-spacing: 0.02em; }",
        ".figure-subtitle { font-size: 13px; fill: #475569; }",
        ".legend-title { font-size: 13px; font-weight: 700; fill: #111827; letter-spacing: 0.04em; text-transform: uppercase; }",
        ".legend-label { font-size: 13px; fill: #111827; }",
        ".legend-value { font-size: 13px; fill: #475569; }",
        ".meta-label { font-size: 12px; fill: #64748b; letter-spacing: 0.05em; text-transform: uppercase; }",
        ".meta-value { font-size: 15px; font-weight: 600; fill: #111827; }",
        ".footer-note { font-size: 11px; fill: #64748b; }",
        "</style>",
        f'<rect class="sheet" x="{_format_number(min_x)}" y="{_format_number(min_y)}" '
        f'width="{_format_number(view_width)}" height="{_format_number(view_height)}" />',
        f'<rect class="frame" x="{_format_number(min_x + 18)}" y="{_format_number(min_y + 18)}" '
        f'width="{_format_number(view_width - 36)}" height="{_format_number(view_height - 36)}" rx="18" />',
        f'<text class="figure-title" x="{_format_number(min_x + 42)}" y="{_format_number(min_y + 54)}">'
        "Horticultural Fixture Layout</text>",
        f'<text class="figure-subtitle" x="{_format_number(min_x + 42)}" y="{_format_number(min_y + 82)}">'
        f'Room footprint {_format_feet(length_ft)} ft × {_format_feet(width_ft)} ft · '
        f'{len(all_positions)} emitter positions · {len(module_groups)} grouped fixtures</text>',
        f'<rect class="room-panel" x="{_format_number(geom_min_x - 34)}" y="{_format_number(geom_min_y - 34)}" '
        f'width="{_format_number((geom_max_x - geom_min_x) + 68)}" '
        f'height="{_format_number((geom_max_y - geom_min_y) + 68)}" rx="26" />',
        f'<polygon class="room-boundary" points="{room_polygon}" />',
        f'<line class="room-axis" x1="{_format_number(length_axis[0][0])}" y1="{_format_number(length_axis[0][1])}" '
        f'x2="{_format_number(length_axis[1][0])}" y2="{_format_number(length_axis[1][1])}" />',
        f'<line class="room-axis" x1="{_format_number(width_axis[0][0])}" y1="{_format_number(width_axis[0][1])}" '
        f'x2="{_format_number(width_axis[1][0])}" y2="{_format_number(width_axis[1][1])}" />',
    ]

    for mtype, _orient, cobs in module_groups:
        rotated = [_project_point(x, y, scale) for x, y in cobs]
        svg.extend(_module_markup(mtype, rotated))

    for px, py in plot_points:
        svg.append(
            f'<circle class="layout-node" cx="{_format_number(px)}" cy="{_format_number(py)}" r="4.7" />'
        )

    if cv_enabled:
        unique_py = sorted(set(py for _, py in plot_points), reverse=True)
        gaps = []
        for i in range(len(unique_py) - 1):
            py1 = unique_py[i]
            py2 = unique_py[i + 1]
            mid_py = (py1 + py2) / 2
            row1_px = [px for px, py in plot_points if py == py1]
            row2_px = [px for px, py in plot_points if py == py2]
            all_px = sorted(set(row1_px + row2_px))
            mid_pxs = [(all_px[j] + all_px[j + 1]) / 2 for j in range(len(all_px) - 1)]
            row_gaps = [(mx, mid_py) for mx in (mid_pxs if i % 2 == 0 else reversed(mid_pxs))]
            gaps.extend(row_gaps)
        if gaps:
            path_points = gaps + gaps[::-1][1:]
            path = " ".join(f"{_format_number(x)},{_format_number(y)}" for x, y in path_points)
            animate_values = [path_points[0]] + [p for p in path_points[1:] for _ in (p, p)]
            values_str = ";".join(
                f"{_format_number(x)},{_format_number(y)}" for x, y in animate_values
            )
            num_moves = len(path_points) - 1
            move_dur = 0.5
            pause_dur = 1.0
            total_dur = num_moves * move_dur + num_moves * pause_dur
            key_times = [0.0]
            cum_time = 0.0
            for _ in range(num_moves):
                cum_time += move_dur
                key_times.append(cum_time / total_dur)
                cum_time += pause_dur
                key_times.append(cum_time / total_dur)
            key_times_str = ";".join(f"{t:.4f}" for t in key_times)
            svg.append(f'<polyline class="cv-path" points="{path}" />')
            svg.append('<circle class="cv-marker" r="5.5">')
            svg.append(
                f'<animateMotion dur="{total_dur}s" repeatCount="indefinite" '
                f'calcMode="linear" keyTimes="{key_times_str}" values="{values_str}" />'
            )
            svg.append("</circle>")

    legend_x = geom_max_x + 42
    legend_y = geom_min_y + 12
    svg.extend(
        [
            f'<text class="legend-title" x="{_format_number(legend_x)}" y="{_format_number(legend_y)}">'
            "Module legend</text>",
            f'<text class="meta-label" x="{_format_number(legend_x)}" y="{_format_number(legend_y + 34)}">'
            "Output</text>",
            f'<text class="meta-value" x="{_format_number(legend_x)}" y="{_format_number(legend_y + 56)}">'
            f'{len(all_positions)} emitters</text>',
            f'<text class="meta-value" x="{_format_number(legend_x)}" y="{_format_number(legend_y + 80)}">'
            f'{len(module_groups)} fixture groups</text>',
        ]
    )

    legend_start_y = legend_y + 126
    for index, module_type in enumerate(
        ["centerpiece", "L", "reverse_L", "linear4", "linear3", "linear2"]
    ):
        row_y = legend_start_y + index * 34
        svg.extend(_legend_sample(module_type, legend_x, row_y - 6))
        svg.append(
            f'<text class="legend-label" x="{_format_number(legend_x + 64)}" y="{_format_number(row_y)}">'
            f'{escape(MODULE_LABELS[module_type])}</text>'
        )
        svg.append(
            f'<text class="legend-value" x="{_format_number(legend_x + 212)}" y="{_format_number(row_y)}">'
            f'× {module_counts.get(module_type, 0)}</text>'
        )

    footer_y = max_y - 44
    svg.append(
        f'<text class="footer-note" x="{_format_number(min_x + 42)}" y="{_format_number(footer_y)}">'
        "Monochrome vector export tuned for manuscript figures and technical reports.</text>"
    )
    if cv_enabled:
        svg.append(
            f'<text class="footer-note" x="{_format_number(min_x + 42)}" y="{_format_number(footer_y + 20)}">'
            "Animated CV traversal overlay retained for service-side diagnostics.</text>"
        )
    else:
        svg.append(
            f'<text class="footer-note" x="{_format_number(min_x + 42)}" y="{_format_number(footer_y + 20)}">'
            "Emitter nodes are shown as circles; grouped traces indicate module topology.</text>"
        )

    svg.append("</svg>")
    return "\n".join(svg)


def build_layout_png(
    all_positions: list[PointTuple],
    module_groups: list[ModuleTuple],
    *,
    length_ft: float,
    width_ft: float,
) -> bytes:
    if not all_positions:
        image = Image.new("RGB", (1200, 900), "white")
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", dpi=(PNG_EXPORT_DPI, PNG_EXPORT_DPI))
        return buffer.getvalue()

    geometry = _layout_figure_geometry(all_positions)
    scale = cast(float, geometry["scale"])
    plot_points = cast(list[PointTuple], geometry["plot_points"])
    min_x = cast(float, geometry["min_x"])
    min_y = cast(float, geometry["min_y"])
    view_width = cast(float, geometry["view_width"])
    view_height = cast(float, geometry["view_height"])

    raster_scale = max(1.0, PNG_EXPORT_MAX_EDGE_PX / max(view_width, view_height))
    image_w = max(1, int(round(view_width * raster_scale)))
    image_h = max(1, int(round(view_height * raster_scale)))

    image = Image.new("RGB", (image_w, image_h), NODE_FILL)
    draw = ImageDraw.Draw(image)

    def px(point: PointTuple) -> tuple[int, int]:
        x, y = point
        return (
            int(round((x - min_x) * raster_scale)),
            int(round((y - min_y) * raster_scale)),
        )

    trace_width = max(3, int(round(4.2 * raster_scale)))
    node_stroke_width = max(2, int(round(1.8 * raster_scale)))
    node_half = 4.8 * raster_scale

    title_font = _load_font(max(24, int(round(24 * raster_scale))), bold=True)
    subtitle_font = _load_font(max(14, int(round(12.5 * raster_scale))), bold=False)

    title_text = "Horticultural Fixture Layout"
    subtitle_text = (
        f"Room footprint {_format_feet(length_ft)} ft × {_format_feet(width_ft)} ft · "
        f"{len(all_positions)} emitter positions · {len(module_groups)} grouped fixtures"
    )

    title_pos = px((min_x + 28, min_y + 44))
    subtitle_pos = px((min_x + 28, min_y + 68))
    draw.text(title_pos, title_text, fill=TITLE_COLOR, font=title_font, anchor="la")
    draw.text(subtitle_pos, subtitle_text, fill=SUBTITLE_COLOR, font=subtitle_font, anchor="la")

    for mtype, _orient, cobs in module_groups:
        rotated = [_project_point(x, y, scale) for x, y in cobs]
        if mtype == "centerpiece":
            base = px(rotated[0])
            for point in rotated[1:]:
                draw.line([base, px(point)], fill=TRACE_COLOR, width=trace_width)
            continue

        if "linear" in mtype:
            if len(rotated) >= 2:
                draw.line([px(point) for point in rotated], fill=TRACE_COLOR, width=trace_width, joint="curve")
            continue

        if mtype == "L":
            long_leg = rotated[1:4]
            short_leg = rotated[0:2]
        else:
            long_leg = rotated[0:3]
            short_leg = rotated[2:4]
        if len(long_leg) >= 2:
            draw.line([px(point) for point in long_leg], fill=TRACE_COLOR, width=trace_width, joint="curve")
        if len(short_leg) >= 2:
            draw.line([px(point) for point in short_leg], fill=TRACE_COLOR, width=trace_width, joint="curve")

    for plot_point in plot_points:
        cx, cy = px(plot_point)
        draw.rectangle(
            [(cx - node_half, cy - node_half), (cx + node_half, cy + node_half)],
            fill=NODE_FILL,
            outline=NODE_STROKE,
            width=node_stroke_width,
        )

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", dpi=(PNG_EXPORT_DPI, PNG_EXPORT_DPI), optimize=True)
    return buffer.getvalue()


def build_layout_payload_from_layout(
    layout: dict[str, object],
    *,
    length_ft: float,
    width_ft: float,
    cv_enabled: bool = False,
    include_svg: bool = True,
) -> dict[str, object]:
    all_positions = _sorted_positions(_layout_positions(layout))
    module_groups = _layout_module_groups(layout)
    module_counts: Counter[str] = Counter()
    for mtype, _orient, _cobs in module_groups:
        module_counts[mtype] += 1
    total_cost = sum(module_counts[key] * PRICES.get(key, 0) for key in module_counts)
    svg = (
        build_layout_svg(
            all_positions,
            module_groups,
            length_ft=length_ft,
            width_ft=width_ft,
            cv_enabled=cv_enabled,
        )
        if include_svg
        else ""
    )
    svg_download_name = _build_download_name(length_ft, width_ft)
    return {
        "length_ft": length_ft,
        "width_ft": width_ft,
        "module_count": len(all_positions),
        "module_counts": dict(module_counts),
        "total_cost": total_cost,
        "all_positions": all_positions,
        "svg": svg,
        "svg_download_name": svg_download_name,
        "png_download_name": svg_download_name.replace(".svg", ".png"),
    }


def build_layout_payload(
    length_ft: float,
    width_ft: float,
    *,
    cv_enabled: bool = False,
    include_svg: bool = True,
) -> dict[str, object]:
    layout = generate_layout(length_ft, width_ft)
    return build_layout_payload_from_layout(
        layout,
        length_ft=length_ft,
        width_ft=width_ft,
        cv_enabled=cv_enabled,
        include_svg=include_svg,
    )
