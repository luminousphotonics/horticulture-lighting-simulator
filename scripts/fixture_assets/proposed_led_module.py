"""Standalone 150 x 150 mm Proposed LED module authored in Build123d.

This repository-owned source is derived from the supplied ``led_module.py``
whose authenticated SHA-256 is recorded by ``build_proposed_led_module.py``.
Units are millimetres, the module/optical center is X=Y=0, and CAD +Z is the
light-facing direction.
"""

from __future__ import annotations

from build123d import Box, Color, Compound, Cylinder

ALUMINUM = Color(0.72, 0.72, 0.68, 1.0)
OPAQUE_DIFFUSER_WHITE = Color(0.90, 0.90, 0.84, 1.0)
SCREW_DARK = Color(0.04, 0.04, 0.04, 1.0)

MODULE_X = 150.0
MODULE_Y = 150.0
MODULE_CENTER_X = 0.0
MODULE_CENTER_Y = 0.0
COMPLETED_APERTURE_SIDE = 126.0
OPTICAL_CENTER_X = 0.0
OPTICAL_CENTER_Y = 0.0

HEATSINK_BASE_Z = 6.0
HEATSINK_BASE_CENTER_Z = -3.0
FIN_COUNT = 32
FIN_X = 2.0
FIN_Y = 144.0
FIN_Z = 28.8
FIN_CENTER_Z = -20.4
FIN_SPAN_X = 136.0
FIN_OFFSETS_X = tuple(
    -FIN_SPAN_X / 2.0 + index * FIN_SPAN_X / (FIN_COUNT - 1)
    for index in range(FIN_COUNT)
)

MODULE_FRAME_Z = 23.6
MODULE_FRAME_CENTER_Z = 13.6
MODULE_FRAME_THICK = 7.0
MODULE_FRAME_CORNER_BLOCK = 20.0
OPAQUE_COVER_X = 144.0
OPAQUE_COVER_Y = 144.0
OPAQUE_COVER_Z = 3.0
OPAQUE_COVER_CENTER_Z = 25.9
OPAQUE_COVER_LIGHT_SIDE_Z = 27.4
FASTENER_RADIUS = 3.0
FASTENER_Z = 1.2
FASTENER_CENTER_Z = 27.6
FASTENER_OFFSET_X = 65.0
FASTENER_OFFSET_Y = 65.0


def _label(shape, name: str, color: Color):
    shape.label = name
    shape.color = color
    return shape


def _box(
    x: float,
    y: float,
    z: float,
    center: tuple[float, float, float],
    name: str,
    color: Color,
):
    return _label(Box(x, y, z).transformed(offset=center), name, color)


def _bounds_box(
    name: str,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
):
    return _box(
        xmax - xmin,
        ymax - ymin,
        zmax - zmin,
        ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0),
        name,
        ALUMINUM,
    )


def make_heatsink(label: str = "module") -> Compound:
    base = _box(
        MODULE_X,
        MODULE_Y,
        HEATSINK_BASE_Z,
        (0.0, 0.0, HEATSINK_BASE_CENTER_Z),
        f"{label}_heatsink_base_plate",
        ALUMINUM,
    )
    fins = [
        _box(
            FIN_X,
            FIN_Y,
            FIN_Z,
            (offset, 0.0, FIN_CENTER_Z),
            f"{label}_heatsink_fin_{index:02d}",
            ALUMINUM,
        )
        for index, offset in enumerate(FIN_OFFSETS_X, start=1)
    ]
    return Compound(
        label=f"{label}_aluminum_heatsink",
        children=[base, *fins],
    )


def make_module_frame(label: str = "module") -> Compound:
    xmin, xmax = -MODULE_X / 2.0, MODULE_X / 2.0
    ymin, ymax = -MODULE_Y / 2.0, MODULE_Y / 2.0
    parts = [
        _bounds_box(
            f"{label}_module_frame_west",
            xmin,
            xmin + MODULE_FRAME_THICK,
            ymin,
            ymax,
            1.8,
            25.4,
        ),
        _bounds_box(
            f"{label}_module_frame_east",
            xmax - MODULE_FRAME_THICK,
            xmax,
            ymin,
            ymax,
            1.8,
            25.4,
        ),
        _bounds_box(
            f"{label}_module_frame_south",
            xmin,
            xmax,
            ymin,
            ymin + MODULE_FRAME_THICK,
            1.8,
            25.4,
        ),
        _bounds_box(
            f"{label}_module_frame_north",
            xmin,
            xmax,
            ymax - MODULE_FRAME_THICK,
            ymax,
            1.8,
            25.4,
        ),
    ]
    corners = (
        (-65.0, -65.0),
        (-65.0, 65.0),
        (65.0, -65.0),
        (65.0, 65.0),
    )
    parts.extend(
        _box(
            MODULE_FRAME_CORNER_BLOCK,
            MODULE_FRAME_CORNER_BLOCK,
            MODULE_FRAME_Z,
            (x, y, MODULE_FRAME_CENTER_Z),
            f"{label}_corner_mount_block_{index:02d}",
            ALUMINUM,
        )
        for index, (x, y) in enumerate(corners, start=1)
    )
    return Compound(label=f"{label}_module_frame", children=parts)


def make_opaque_cover(label: str = "module") -> Compound:
    cover = _box(
        OPAQUE_COVER_X,
        OPAQUE_COVER_Y,
        OPAQUE_COVER_Z,
        (0.0, 0.0, OPAQUE_COVER_CENTER_Z),
        f"{label}_opaque_bottom_cover",
        OPAQUE_DIFFUSER_WHITE,
    )
    return Compound(
        label=f"{label}_opaque_bottom_cover_assembly",
        children=[cover],
    )


def make_fasteners(label: str = "module") -> Compound:
    children = []
    for index, (x, y) in enumerate(
        (
            (-FASTENER_OFFSET_X, -FASTENER_OFFSET_Y),
            (-FASTENER_OFFSET_X, FASTENER_OFFSET_Y),
            (FASTENER_OFFSET_X, -FASTENER_OFFSET_Y),
            (FASTENER_OFFSET_X, FASTENER_OFFSET_Y),
        ),
        start=1,
    ):
        shape = Cylinder(radius=FASTENER_RADIUS, height=FASTENER_Z).transformed(
            offset=(x, y, FASTENER_CENTER_Z)
        )
        children.append(
            _label(shape, f"{label}_module_screw_{index:02d}", SCREW_DARK)
        )
    return Compound(label=f"{label}_module_fasteners", children=children)


def make_led_module(label: str = "module") -> Compound:
    return Compound(
        label=f"{label}_led_module",
        children=[
            make_heatsink(label),
            make_module_frame(label),
            make_opaque_cover(label),
            make_fasteners(label),
        ],
    )
