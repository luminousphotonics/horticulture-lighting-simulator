from __future__ import annotations

from typing import Any

from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_COVERAGE_FT,
    DEFAULT_MOUNT_Z_M,
    DEFAULT_OUTER_MARGIN_IN,
    IN2M,
    LAMP_ARC_DIAMETER_M,
    LAMP_ARC_LENGTH_M,
    LAMP_CCT_DESCRIPTION,
    LAMP_RADIAL_FACES,
    PROFILE_LABEL,
    HpsIesComparatorProfile,
    aligned_dims_ft,
    coverage_grid_counts,
    ies_profile_manifest_dict,
    validate_coverage_ft,
)

JsonObject = dict[str, Any]


def maybe_swap_dims(
    length_m: float, width_m: float, *, align_long_axis_x: bool
) -> tuple[float, float]:
    if align_long_axis_x and width_m > length_m:
        return width_m, length_m
    return length_m, width_m


def fixture_body_outline_xy(
    cx: float, cy: float, fixture_length_m: float, fixture_width_m: float
) -> list[tuple[float, float]]:
    hx = 0.5 * fixture_length_m
    hy = 0.5 * fixture_width_m
    return [
        (cx - hx, cy - hy),
        (cx + hx, cy - hy),
        (cx + hx, cy + hy),
        (cx - hx, cy + hy),
    ]


def compute_fixture_layout(
    *,
    length_ft: float,
    width_ft: float,
    margin_in: float = DEFAULT_OUTER_MARGIN_IN,
    coverage_ft: float = DEFAULT_COVERAGE_FT,
    mount_z_m: float = DEFAULT_MOUNT_Z_M,
    align_long_axis_x: bool = True,
    profile: HpsIesComparatorProfile,
) -> JsonObject:
    room_l_ft, room_w_ft = aligned_dims_ft(length_ft, width_ft)
    room_l_m, room_w_m = maybe_swap_dims(
        room_l_ft * 0.3048,
        room_w_ft * 0.3048,
        align_long_axis_x=align_long_axis_x,
    )
    coverage = validate_coverage_ft(coverage_ft)
    nx, ny = coverage_grid_counts(room_l_ft, room_w_ft, coverage, margin_in)
    centers = fixture_centers(room_l_ft, room_w_ft, coverage, margin_in, profile)
    fixtures = fixture_records(centers, mount_z_m, profile)
    return {
        "profile": profile.profile_version,
        "label": PROFILE_LABEL,
        "archetype": profile.archetype,
        "reference_geometry_archetype": profile.reference_geometry_archetype,
        "units": "meters",
        "coverage_ft": coverage,
        "z": mount_z_m,
        "room": {"L": room_l_m, "W": room_w_m},
        "fixture_envelope_m": {
            "length": profile.fixture_length_m,
            "width": profile.fixture_width_m,
            "height": profile.fixture_height_m,
        },
        "lamp_arc_m": {
            "length": LAMP_ARC_LENGTH_M,
            "diameter": LAMP_ARC_DIAMETER_M,
            "faces": LAMP_RADIAL_FACES,
            "description": LAMP_CCT_DESCRIPTION,
        },
        "nx": nx,
        "ny": ny,
        "fixtures": fixtures,
        "ies_profile": ies_profile_manifest_dict(profile.variant),
    }


def fixture_centers(
    length_ft: float,
    width_ft: float,
    coverage_ft: float,
    margin_in: float,
    profile: HpsIesComparatorProfile,
) -> list[tuple[float, float]]:
    nx, ny = coverage_grid_counts(length_ft, width_ft, coverage_ft, margin_in)
    spacing_m = validate_coverage_ft(coverage_ft) * 0.3048
    span_x = spacing_m * max(nx - 1, 0)
    span_y = spacing_m * max(ny - 1, 0)
    usable_half_x = 0.5 * length_ft * 0.3048 - margin_in * IN2M
    usable_half_y = 0.5 * width_ft * 0.3048 - margin_in * IN2M
    if 0.5 * span_x + 0.5 * profile.fixture_length_m > usable_half_x + 1e-9:
        raise ValueError("HPS fixture grid violates the X wall inset clearance.")
    if 0.5 * span_y + 0.5 * profile.fixture_width_m > usable_half_y + 1e-9:
        raise ValueError("HPS fixture grid violates the Y wall inset clearance.")
    xs = [(-0.5 * span_x) + i * spacing_m for i in range(nx)]
    ys = [(-0.5 * span_y) + i * spacing_m for i in range(ny)]
    return [(x, y) for y in ys for x in xs]


def fixture_records(
    centers: list[tuple[float, float]],
    mount_z_m: float,
    profile: HpsIesComparatorProfile,
) -> list[JsonObject]:
    fixtures: list[JsonObject] = []
    for cx, cy in centers:
        lamp_line = [
            [cx - 0.5 * LAMP_ARC_LENGTH_M, cy, mount_z_m],
            [cx + 0.5 * LAMP_ARC_LENGTH_M, cy, mount_z_m],
        ]
        body = fixture_body_outline_xy(cx, cy, profile.fixture_length_m, profile.fixture_width_m)
        fixtures.append(
            {
                "cx": cx,
                "cy": cy,
                "lamp_line": lamp_line,
                "body_corners": [[x, y] for x, y in body],
            }
        )
    return fixtures


def fixture_positions(layout: JsonObject) -> list[dict[str, float]]:
    z = float(layout["z"])
    return [
        {"x": float(fx["cx"]), "y": float(fx["cy"]), "z": z}
        for fx in layout["fixtures"]
    ]
