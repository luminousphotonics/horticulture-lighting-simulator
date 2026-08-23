"""Frozen physical and forward-flux authority for the Proposed LED fixture.

The completed-aperture transmission is the converged deterministic far-field
``rtrace -I+`` integral of a unit-flux native Lambertian emitter through the
exact PMMA/PTFE/bezel stack declared here.  The former near-field
backward-matrix receiver result is retained only as diagnostic history because it
does not share one source-independent response with directional ``flatcorr``
emitters.
"""

from __future__ import annotations

import math
from types import MappingProxyType
from typing import Final, Mapping, Sequence


PROPOSED_FIXTURE_OPTICAL_STACK_ID: Final = (
    "proposed_120mm_lambertian_128mm_pmma_126mm_aperture_forward_flux_v2"
)
ACCEPTED_FIXTURE_TRANSMISSION: Final = 0.8331432504293806
COMPLETED_APERTURE_PPE_UMOL_PER_J: Final = 2.6
INTERNAL_SOURCE_PPE_UMOL_PER_J: Final = (
    COMPLETED_APERTURE_PPE_UMOL_PER_J / ACCEPTED_FIXTURE_TRANSMISSION
)
PROPOSED_MODELED_BAND_TRANSMISSION: Final[Mapping[str, float]] = MappingProxyType(
    {
        band_id: ACCEPTED_FIXTURE_TRANSMISSION
        for band_id in ("blue", "green", "orange", "red", "far_red")
    }
)

APERTURE_PLANE_Z_M: Final = 0.0
PMMA_BOTTOM_Z_M: Final = 0.0
PMMA_THICKNESS_M: Final = 0.003
PMMA_TOP_Z_M: Final = PMMA_BOTTOM_Z_M + PMMA_THICKNESS_M
PMMA_SIDE_M: Final = 0.128
PMMA_IOR: Final = 1.49
INTERNAL_CAVITY_HEIGHT_M: Final = 0.008
EMITTER_Z_M: Final = PMMA_TOP_Z_M + INTERNAL_CAVITY_HEIGHT_M
EMITTER_SIDE_M: Final = 0.120
EMITTER_AREA_M2: Final = EMITTER_SIDE_M**2
APERTURE_SIDE_M: Final = 0.126
APERTURE_AREA_M2: Final = APERTURE_SIDE_M**2
BEZEL_OUTER_SIDE_M: Final = 0.129
PTFE_REFLECTANCE: Final = 0.90
PTFE_PHYSICAL_THICKNESS_M: Final = 0.000508
CALIBRATION_EPSILON_M: Final = 1.0e-6
OUTWARD_APERTURE_NORMAL: Final = (0.0, 0.0, -1.0)

PROPOSED_PMMA_MATERIAL: Final = "proposed_pmma"
PROPOSED_PTFE_MATERIAL: Final = "proposed_ptfe"
PROPOSED_BLACK_BEZEL_MATERIAL: Final = "proposed_black_bezel"


def validate_common_accepted_band_transmission(
    transmission_by_band: Mapping[str, object],
) -> float:
    """Fail closed unless every modeled band shares the accepted stack value."""

    expected_ids = tuple(PROPOSED_MODELED_BAND_TRANSMISSION)
    if not isinstance(transmission_by_band, Mapping) or set(
        transmission_by_band
    ) != set(expected_ids):
        raise ValueError(
            "controlled spectrum requires one unambiguous fixture transmission "
            "for every modeled band."
        )
    values: list[float] = []
    for band_id in expected_ids:
        value = transmission_by_band[band_id]
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise ValueError(
                "controlled spectrum fixture transmissions must be finite numbers."
            )
        number = float(value)
        if not math.isfinite(number) or number <= 0.0:
            raise ValueError(
                "controlled spectrum fixture transmissions must be finite and positive."
            )
        values.append(number)
    if any(value != values[0] for value in values[1:]) or values[0] != (
        ACCEPTED_FIXTURE_TRANSMISSION
    ):
        raise ValueError(
            "controlled spectrum requires the wavelength-neutral Proposed stack "
            "with one common accepted transmission across all modeled bands."
        )
    return values[0]


def proposed_fixture_materials_radiance_text() -> str:
    """Return the exact accepted, wavelength-neutral material declarations."""

    return """void dielectric proposed_pmma
0
0
5 1 1 1 1.49 0

void plastic proposed_ptfe
0
0
5 0.90 0.90 0.90 0 0

void plastic proposed_black_bezel
0
0
5 0 0 0 0 0
"""


def proposed_fixture_stack_radiance_text(
    module_index: int,
    center_x_m: float,
    center_y_m: float,
    aperture_z_m: float,
    *,
    name_prefix: str = "proposed",
) -> str:
    """Translate the accepted local optical stack to one production module.

    ``aperture_z_m`` is the completed-aperture plane.  The 0.508 mm PTFE
    thickness is deliberately not used to create a volume: the liner is four
    infinitesimally thin, inward-facing optical boundaries.
    """

    index = _non_negative_integer("module_index", module_index)
    center_x = _finite("center_x_m", center_x_m)
    center_y = _finite("center_y_m", center_y_m)
    aperture_z = _finite("aperture_z_m", aperture_z_m)
    prefix = str(name_prefix).strip()
    if not prefix:
        raise ValueError("name_prefix must not be empty.")
    object_prefix = f"{prefix}_m{index:04d}"

    pmma_half = PMMA_SIDE_M / 2.0
    pmma_bottom = aperture_z + PMMA_BOTTOM_Z_M
    pmma_top = aperture_z + PMMA_TOP_Z_M
    sections = [
        box_radiance_text(
            PROPOSED_PMMA_MATERIAL,
            f"{object_prefix}_pmma",
            center_x - pmma_half,
            center_x + pmma_half,
            center_y - pmma_half,
            center_y + pmma_half,
            pmma_bottom,
            pmma_top,
        ).rstrip(),
        _black_bezel_text(object_prefix, center_x, center_y, aperture_z).rstrip(),
        _ptfe_cavity_text(object_prefix, center_x, center_y, aperture_z).rstrip(),
    ]
    return "\n".join(sections) + "\n"


def proposed_fixture_emitter_z_m(aperture_z_m: float) -> float:
    """Return the absolute macro-emitter plane for an aperture-plane height."""

    return _finite("aperture_z_m", aperture_z_m) + EMITTER_Z_M


def downward_square_radiance_text(
    modifier: str,
    name: str,
    center_x_m: float,
    center_y_m: float,
    z_m: float,
    side_m: float,
) -> str:
    """Return one square polygon wound toward the outward ``-Z`` direction."""

    half = _positive("side_m", side_m) / 2.0
    center_x = _finite("center_x_m", center_x_m)
    center_y = _finite("center_y_m", center_y_m)
    z = _finite("z_m", z_m)
    return polygon_radiance_text(
        modifier,
        name,
        (
            (center_x - half, center_y + half, z),
            (center_x + half, center_y + half, z),
            (center_x + half, center_y - half, z),
            (center_x - half, center_y - half, z),
        ),
    )


def downward_circle_radiance_text(
    modifier: str,
    name: str,
    x_m: float,
    y_m: float,
    z_m: float,
    diameter_m: float,
) -> str:
    """Return a centered circular LES with its emitting normal along -Z."""

    radius = float(diameter_m) / 2.0
    if not math.isfinite(radius) or radius <= 0.0:
        raise ValueError("diameter_m must be finite and positive.")
    return (
        f"{modifier} ring {name}\n"
        "0\n"
        "0\n"
        f"8 {x_m:.9f} {y_m:.9f} {z_m:.9f} "
        f"0 0 -1 0 {radius:.9f}\n"
    )


def _black_bezel_text(
    prefix: str,
    center_x: float,
    center_y: float,
    aperture_z: float,
) -> str:
    outer = BEZEL_OUTER_SIDE_M / 2.0
    inner = APERTURE_SIDE_M / 2.0
    z = aperture_z - CALIBRATION_EPSILON_M / 2.0
    strips = (
        ("north", -outer, inner, outer, outer),
        ("south", -outer, -outer, outer, -inner),
        ("west", -outer, -inner, -inner, inner),
        ("east", inner, -inner, outer, inner),
    )
    return "".join(
        polygon_radiance_text(
            PROPOSED_BLACK_BEZEL_MATERIAL,
            f"{prefix}_black_bezel_{side}",
            (
                (center_x + x0, center_y + y0, z),
                (center_x + x1, center_y + y0, z),
                (center_x + x1, center_y + y1, z),
                (center_x + x0, center_y + y1, z),
            ),
        )
        for side, x0, y0, x1, y1 in strips
    )


def _ptfe_cavity_text(
    prefix: str,
    center_x: float,
    center_y: float,
    aperture_z: float,
) -> str:
    half = APERTURE_SIDE_M / 2.0
    bottom = aperture_z + PMMA_TOP_Z_M
    top = aperture_z + EMITTER_Z_M
    x0, x1 = center_x - half, center_x + half
    y0, y1 = center_y - half, center_y + half
    faces = (
        ("north", ((x0, y1, bottom), (x1, y1, bottom), (x1, y1, top), (x0, y1, top))),
        ("south", ((x0, y0, bottom), (x0, y0, top), (x1, y0, top), (x1, y0, bottom))),
        ("west", ((x0, y0, bottom), (x0, y1, bottom), (x0, y1, top), (x0, y0, top))),
        ("east", ((x1, y0, bottom), (x1, y0, top), (x1, y1, top), (x1, y1, bottom))),
    )
    return "".join(
        polygon_radiance_text(
            PROPOSED_PTFE_MATERIAL,
            f"{prefix}_ptfe_{side}",
            vertices,
        )
        for side, vertices in faces
    )


def box_radiance_text(
    modifier: str,
    name_prefix: str,
    x0: float,
    x1: float,
    y0: float,
    y1: float,
    z0: float,
    z1: float,
) -> str:
    """Return six consistently wound polygons for an axis-aligned box."""

    faces = (
        ("top", ((x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1))),
        ("bottom", ((x0, y1, z0), (x1, y1, z0), (x1, y0, z0), (x0, y0, z0))),
        ("north", ((x0, y1, z0), (x0, y1, z1), (x1, y1, z1), (x1, y1, z0))),
        ("south", ((x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1))),
        ("west", ((x0, y0, z0), (x0, y0, z1), (x0, y1, z1), (x0, y1, z0))),
        ("east", ((x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1))),
    )
    return "\n".join(
        polygon_radiance_text(modifier, f"{name_prefix}_{face}", vertices).rstrip()
        for face, vertices in faces
    ) + "\n"


def polygon_radiance_text(
    modifier: str,
    name: str,
    vertices: Sequence[tuple[float, float, float]],
) -> str:
    """Return a deterministic Radiance polygon."""

    if len(vertices) < 3:
        raise ValueError("a polygon requires at least three vertices.")
    lines = [f"{modifier} polygon {name}", "0", "0", str(3 * len(vertices))]
    lines.extend(
        f"  {_number(x)} {_number(y)} {_number(z)}" for x, y, z in vertices
    )
    return "\n".join(lines) + "\n"


def _number(value: int | float) -> str:
    return format(_finite("coordinate", value), ".15g")


def _finite(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def _positive(name: str, value: object) -> float:
    number = _finite(name, value)
    if number <= 0.0:
        raise ValueError(f"{name} must be finite and positive.")
    return number


def _non_negative_integer(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer.")
    return value
