"""
Procedural 1000W HPS enclosed reflector grow light fixture with rear exhaust.

Coordinate convention:
- X = fixture length
- Y = fixture width
- Z = vertical height
- Top of fixture is near Z = 0
- Reflector opens downward toward negative Z

Run:
    cd /home/austin/Desktop/build123d-modeling
    source .venv/bin/activate
    python models/hps_1000w_fixture.py
"""

import math
from pathlib import Path

from build123d import (
    Box,
    BuildPart,
    BuildSketch,
    Color,
    Compound,
    Cylinder,
    Locations,
    Plane,
    Rectangle,
    Sphere,
    export_step,
    loft,
)

try:
    from ocp_vscode import show
except ImportError:
    show = None


# -----------------------------
# Dimensions, mm
# -----------------------------

INCH = 25.4

TOTAL_LENGTH = 33.3 * INCH      # 845.82
TOTAL_WIDTH = 25.3 * INCH       # 642.62
TOTAL_HEIGHT = 9.8 * INCH       # 248.92

# Interpreting the 28.0 inch top box dimension as the long dimension.
TOP_BOX_LENGTH = 28.0 * INCH    # 711.20
TOP_BOX_WIDTH = 330.0
TOP_BOX_HEIGHT = 78.0

REFLECTOR_SHEET_THICKNESS = 2.0
REFLECTOR_BOTTOM_Z = -TOTAL_HEIGHT
REFLECTOR_TOP_Z = -TOP_BOX_HEIGHT

EXHAUST_OD = 6.0 * INCH         # 152.4
EXHAUST_WALL = 4.0
EXHAUST_HEIGHT = 58.0
EXHAUST_CENTER_Z = (REFLECTOR_BOTTOM_Z + REFLECTOR_TOP_Z) / 2

BULB_AXIS_Z = -138.0
BULB_AXIS_Y = 0.0
BULB_GLASS_LENGTH = 245.0
BULB_GLASS_RADIUS = 25.0
BULB_BASE_LENGTH = 62.0
BULB_BASE_RADIUS = 20.0

SOCKET_X = -178.0


# -----------------------------
# Utility helpers
# -----------------------------

def label_shape(shape, label, color=None):
    shape.label = label
    if color is not None:
        shape.color = color
    return shape


def union_all(shapes):
    result = shapes[0]
    for shape in shapes[1:]:
        result = result + shape
    return result


def cyl_x(radius, length, center):
    """Cylinder whose long axis runs along X."""
    return Cylinder(radius=radius, height=length).transformed(
        rotate=(0, 90, 0),
        offset=center,
    )


def cyl_axis_xz(radius, length, center, axis_x, axis_z):
    """Cylinder centered at ``center`` with its long axis in the XZ plane."""
    axis_length = math.hypot(axis_x, axis_z)
    if axis_length == 0:
        raise ValueError("cylinder axis must be nonzero")

    axis_x /= axis_length
    axis_z /= axis_length
    rotation_y = math.degrees(math.atan2(axis_x, axis_z))

    return Cylinder(radius=radius, height=length).transformed(
        rotate=(0, rotation_y, 0),
        offset=center,
    )


def sphere_at(radius, center):
    return Sphere(radius=radius).transformed(offset=center)


def centered_box(length, width, height, center):
    return Box(length, width, height).transformed(offset=center)


def lofted_rect_solid(bottom_length, bottom_width, bottom_z, top_length, top_width, top_z):
    """Loft between two centered rectangular profiles."""
    with BuildPart() as part:
        with BuildSketch(Plane.XY.offset(bottom_z)):
            Rectangle(bottom_length, bottom_width)

        with BuildSketch(Plane.XY.offset(top_z)):
            Rectangle(top_length, top_width)

        loft(ruled=True)

    return part.part


def inset_profile_dimension_at_z(
    bottom_dimension,
    top_dimension,
    bottom_z,
    top_z,
    query_z,
    inset,
):
    """Return a linearly extrapolated profile dimension with a per-side inset."""
    profile_height = top_z - bottom_z
    if profile_height <= 0:
        raise ValueError("top_z must be greater than bottom_z")

    fraction = (query_z - bottom_z) / profile_height
    outer_dimension = bottom_dimension + fraction * (top_dimension - bottom_dimension)
    inner_dimension = outer_dimension - 2 * inset

    if inner_dimension <= 0:
        raise ValueError("inset collapses the inner reflector profile")

    return inner_dimension


def profile_dimension_at_z(
    bottom_dimension,
    top_dimension,
    bottom_z,
    top_z,
    query_z,
):
    """Return the linearly interpolated outer profile dimension at ``query_z``."""
    profile_height = top_z - bottom_z
    if profile_height <= 0:
        raise ValueError("top_z must be greater than bottom_z")

    fraction = (query_z - bottom_z) / profile_height
    return bottom_dimension + fraction * (top_dimension - bottom_dimension)


def rear_exhaust_geometry():
    """Return the -X end-wall center and its outward unit normal."""
    center_length = profile_dimension_at_z(
        TOTAL_LENGTH,
        TOP_BOX_LENGTH,
        REFLECTOR_BOTTOM_Z,
        REFLECTOR_TOP_Z,
        EXHAUST_CENTER_Z,
    )
    wall_center = (-center_length / 2, 0.0, EXHAUST_CENTER_Z)

    half_length_run = (TOTAL_LENGTH - TOP_BOX_LENGTH) / 2
    wall_rise = REFLECTOR_TOP_Z - REFLECTOR_BOTTOM_Z
    normal_length = math.hypot(wall_rise, half_length_run)
    outward_normal = (
        -wall_rise / normal_length,
        0.0,
        half_length_run / normal_length,
    )

    return wall_center, outward_normal


# -----------------------------
# Reflector hood
# -----------------------------

def make_top_housing():
    """Central rectangular top box with a completely sealed roof."""
    with BuildPart() as part:
        with Locations((0, 0, -TOP_BOX_HEIGHT / 2)):
            Box(TOP_BOX_LENGTH, TOP_BOX_WIDTH, TOP_BOX_HEIGHT)

    return label_shape(
        part.part,
        "reflector_hood_top_box",
        Color(0.72, 0.72, 0.72, 1.0),
    )


def make_reflector_skirt():
    """
    Hollow flared reflector shell.

    Built as an outer loft minus an extended inner loft. The inner cutter is
    derived from the outer taper at its extended Z planes so it remains inset
    from every wall at the reflector's real top and bottom edges.
    """
    outer = lofted_rect_solid(
        bottom_length=TOTAL_LENGTH,
        bottom_width=TOTAL_WIDTH,
        bottom_z=REFLECTOR_BOTTOM_Z,
        top_length=TOP_BOX_LENGTH,
        top_width=TOP_BOX_WIDTH,
        top_z=REFLECTOR_TOP_Z,
    )

    cut_extension = 4.0
    inner_bottom_z = REFLECTOR_BOTTOM_Z - cut_extension
    inner_top_z = REFLECTOR_TOP_Z + cut_extension

    inner_bottom_length = inset_profile_dimension_at_z(
        TOTAL_LENGTH,
        TOP_BOX_LENGTH,
        REFLECTOR_BOTTOM_Z,
        REFLECTOR_TOP_Z,
        inner_bottom_z,
        REFLECTOR_SHEET_THICKNESS,
    )
    inner_bottom_width = inset_profile_dimension_at_z(
        TOTAL_WIDTH,
        TOP_BOX_WIDTH,
        REFLECTOR_BOTTOM_Z,
        REFLECTOR_TOP_Z,
        inner_bottom_z,
        REFLECTOR_SHEET_THICKNESS,
    )
    inner_top_length = inset_profile_dimension_at_z(
        TOTAL_LENGTH,
        TOP_BOX_LENGTH,
        REFLECTOR_BOTTOM_Z,
        REFLECTOR_TOP_Z,
        inner_top_z,
        REFLECTOR_SHEET_THICKNESS,
    )
    inner_top_width = inset_profile_dimension_at_z(
        TOTAL_WIDTH,
        TOP_BOX_WIDTH,
        REFLECTOR_BOTTOM_Z,
        REFLECTOR_TOP_Z,
        inner_top_z,
        REFLECTOR_SHEET_THICKNESS,
    )

    inner = lofted_rect_solid(
        bottom_length=inner_bottom_length,
        bottom_width=inner_bottom_width,
        bottom_z=inner_bottom_z,
        top_length=inner_top_length,
        top_width=inner_top_width,
        top_z=inner_top_z,
    )

    skirt = outer - inner

    # Put the only cooling opening on the -X end wall behind the lamp socket.
    # Its axis follows the outward normal of the flared panel so the collar
    # mounts flush, and its center is low enough to remain below the roof seam.
    wall_center, outward_normal = rear_exhaust_geometry()
    bore = cyl_axis_xz(
        radius=(EXHAUST_OD / 2) - EXHAUST_WALL,
        length=24.0,
        center=wall_center,
        axis_x=outward_normal[0],
        axis_z=outward_normal[2],
    )
    skirt = skirt - bore

    return label_shape(
        skirt,
        "reflector_hood_flared_skirt",
        Color(0.88, 0.88, 0.82, 1.0),
    )


def make_reflector_hood():
    top_box = make_top_housing()
    skirt = make_reflector_skirt()

    return Compound(
        label="reflector_hood",
        children=[top_box, skirt],
    )


# -----------------------------
# Exhaust port
# -----------------------------

def make_exhaust_flange():
    """6-inch hollow exhaust collar on the -X end wall behind the bulb."""
    wall_center, outward_normal = rear_exhaust_geometry()

    def offset_from_wall(distance):
        return tuple(
            wall_center[index] + outward_normal[index] * distance
            for index in range(3)
        )

    body_center = offset_from_wall(EXHAUST_HEIGHT / 2 - 8.0)
    body_outer = cyl_axis_xz(
        radius=EXHAUST_OD / 2,
        length=EXHAUST_HEIGHT,
        center=body_center,
        axis_x=outward_normal[0],
        axis_z=outward_normal[2],
    )
    body_inner = cyl_axis_xz(
        radius=(EXHAUST_OD / 2) - EXHAUST_WALL,
        length=EXHAUST_HEIGHT + 8.0,
        center=body_center,
        axis_x=outward_normal[0],
        axis_z=outward_normal[2],
    )

    rim_center = offset_from_wall(EXHAUST_HEIGHT - 5.0)
    rim_outer = cyl_axis_xz(
        radius=(EXHAUST_OD / 2) + 5.0,
        length=8.0,
        center=rim_center,
        axis_x=outward_normal[0],
        axis_z=outward_normal[2],
    )
    rim_inner = cyl_axis_xz(
        radius=(EXHAUST_OD / 2) - EXHAUST_WALL,
        length=12.0,
        center=rim_center,
        axis_x=outward_normal[0],
        axis_z=outward_normal[2],
    )

    flange = (body_outer - body_inner) + (rim_outer - rim_inner)

    return label_shape(
        flange,
        "exhaust_flange",
        Color(0.58, 0.58, 0.58, 1.0),
    )


# -----------------------------
# HPS bulb
# -----------------------------

def make_hps_bulb_glass():
    """Outer elongated glass envelope."""
    glass_center_x = -30.0

    tube = cyl_x(
        radius=BULB_GLASS_RADIUS,
        length=BULB_GLASS_LENGTH,
        center=(glass_center_x, BULB_AXIS_Y, BULB_AXIS_Z),
    )

    left_cap = sphere_at(
        radius=BULB_GLASS_RADIUS,
        center=(glass_center_x - BULB_GLASS_LENGTH / 2, BULB_AXIS_Y, BULB_AXIS_Z),
    )

    right_cap = sphere_at(
        radius=BULB_GLASS_RADIUS,
        center=(glass_center_x + BULB_GLASS_LENGTH / 2, BULB_AXIS_Y, BULB_AXIS_Z),
    )

    glass = union_all([tube, left_cap, right_cap])

    return label_shape(
        glass,
        "hps_bulb_glass",
        Color(0.75, 0.90, 1.0, 0.35),
    )


def make_hps_bulb_base():
    """Simplified E39/mogul screw base with raised ring ridges."""
    base_center_x = -30.0 - BULB_GLASS_LENGTH / 2 - BULB_BASE_LENGTH / 2 + 6.0

    shapes = [
        cyl_x(
            radius=BULB_BASE_RADIUS,
            length=BULB_BASE_LENGTH,
            center=(base_center_x, BULB_AXIS_Y, BULB_AXIS_Z),
        )
    ]

    ridge_count = 6
    ridge_spacing = 7.0
    start_x = base_center_x - 18.0

    for i in range(ridge_count):
        shapes.append(
            cyl_x(
                radius=BULB_BASE_RADIUS + 1.8,
                length=2.4,
                center=(start_x + i * ridge_spacing, BULB_AXIS_Y, BULB_AXIS_Z),
            )
        )

    shapes.append(
        cyl_x(
            radius=10.0,
            length=10.0,
            center=(base_center_x - BULB_BASE_LENGTH / 2 - 4.0, BULB_AXIS_Y, BULB_AXIS_Z),
        )
    )

    base = union_all(shapes)

    return label_shape(
        base,
        "hps_bulb_base",
        Color(0.72, 0.64, 0.45, 1.0),
    )


def make_hps_arc_tube():
    """Small inner arc tube inside the glass envelope."""
    arc_tube = cyl_x(
        radius=6.0,
        length=105.0,
        center=(-8.0, BULB_AXIS_Y, BULB_AXIS_Z),
    )

    left_electrode = cyl_x(
        radius=2.0,
        length=32.0,
        center=(-76.0, BULB_AXIS_Y, BULB_AXIS_Z),
    )

    right_electrode = cyl_x(
        radius=2.0,
        length=32.0,
        center=(60.0, BULB_AXIS_Y, BULB_AXIS_Z),
    )

    arc = union_all([arc_tube, left_electrode, right_electrode])

    return label_shape(
        arc,
        "hps_inner_arc_tube",
        Color(1.0, 0.78, 0.35, 1.0),
    )


# -----------------------------
# Socket bracket
# -----------------------------

def make_socket_bracket():
    """Simple internal bracket and socket block holding the HPS bulb."""
    roof_mount = centered_box(
        length=70.0,
        width=70.0,
        height=10.0,
        center=(SOCKET_X, 0, REFLECTOR_TOP_Z - 12.0),
    )

    drop_plate = centered_box(
        length=12.0,
        width=58.0,
        height=62.0,
        center=(SOCKET_X, 0, REFLECTOR_TOP_Z - 45.0),
    )

    socket_cup = cyl_x(
        radius=27.0,
        length=38.0,
        center=(SOCKET_X + 18.0, 0, BULB_AXIS_Z),
    )

    bracket = label_shape(
        union_all([roof_mount, drop_plate]),
        "socket_bracket",
        Color(0.35, 0.35, 0.35, 1.0),
    )

    socket = label_shape(
        socket_cup,
        "ceramic_socket",
        Color(0.85, 0.82, 0.72, 1.0),
    )

    return Compound(
        label="socket_bracket_assembly",
        children=[bracket, socket],
    )


# -----------------------------
# Assembly
# -----------------------------

def make_hps_assembly():
    return Compound(
        label="competitor_hps_1000w",
        children=[
            make_reflector_hood(),
            make_exhaust_flange(),
            make_socket_bracket(),
            make_hps_bulb_base(),
            make_hps_bulb_glass(),
            make_hps_arc_tube(),
        ],
    )


if __name__ == "__main__":
    assembly = make_hps_assembly()

    output_dir = Path(__file__).resolve().parents[1] / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    step_path = output_dir / "hps_1000w_fixture.step"
    export_step(assembly, step_path)

    print(f"Exported: {step_path}")

    if show is not None:
        show(assembly)
    else:
        print("ocp_vscode is not installed, so skipping live viewer preview.")