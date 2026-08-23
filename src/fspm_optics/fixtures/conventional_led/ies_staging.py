"""Pure downward-hemisphere LM-63 staging for one-sided transport."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Final, Iterable

from .angular import ANGULAR_NORMALIZATION_ABS_TOLERANCE
from .errors import ConventionalLedError
from .lm63 import Lm63Keyword, Lm63Photometry, parse_lm63

DERIVED_IES_DOWNWARD_NORMALIZATION_POLICY: Final = (
    "periodic_type_c_trapezoid_unit_downward_flux_neutral_fields_v1"
)
DERIVED_IES_DOWNWARD_UNIT_ABS_TOLERANCE: Final = (
    ANGULAR_NORMALIZATION_ABS_TOLERANCE
)
DERIVED_IES_FILENAME: Final = "conventional_led_unit_downward_flux.ies"
TRANSPORT_NORMALIZATION_DOMAIN: Final = (
    "type_c_vertical_0_to_90_degrees_downward_hemisphere"
)
ONE_SIDED_APERTURE_POLICY: Final = "transport_only_downward_lower_face"
EXCLUDED_UPWARD_FLUX_LIMITATION: Final = (
    "Measured Type-C angles above 90 degrees are retained as provenance but "
    "excluded from the one-sided downward comparison transport."
)


class DerivedIesError(ConventionalLedError):
    """A normalized LM-63 staging document cannot satisfy its contract."""


@dataclass(frozen=True, slots=True)
class OriginalIesIdentity:
    resource_name: str
    sha256: str
    product_id: str
    test_id: str
    tested_input_watts: float
    candela_multiplier: float
    ballast_factor: float
    future_use_factor_2019: float

    def __post_init__(self) -> None:
        if not all((self.resource_name, self.product_id, self.test_id)):
            raise DerivedIesError("original IES identity fields must be non-empty.")
        _validate_sha256("original IES", self.sha256)
        for name in (
            "tested_input_watts",
            "candela_multiplier",
            "ballast_factor",
            "future_use_factor_2019",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0.0:
                raise DerivedIesError(f"original IES {name} must be positive.")
            object.__setattr__(self, name, value)

    def to_payload(self) -> dict[str, object]:
        return {
            "resource_name": self.resource_name,
            "sha256": self.sha256,
            "product_id": self.product_id,
            "test_id": self.test_id,
            "tested_input_watts": self.tested_input_watts,
            "candela_multiplier": self.candela_multiplier,
            "ballast_factor": self.ballast_factor,
            "future_use_factor_2019": self.future_use_factor_2019,
            "absolute_calibration_role": "provenance_only",
        }


@dataclass(frozen=True, slots=True)
class TypeCHemisphereFlux:
    full_sphere_flux_cd_sr: float
    downward_hemisphere_flux_cd_sr: float
    upward_hemisphere_flux_cd_sr: float
    downward_fraction_of_full_sphere: float
    upward_fraction_of_full_sphere: float
    horizontal_closure_counted_once: bool = True
    hemisphere_boundary_deg: float = 90.0

    def __post_init__(self) -> None:
        values = (
            self.full_sphere_flux_cd_sr,
            self.downward_hemisphere_flux_cd_sr,
            self.upward_hemisphere_flux_cd_sr,
        )
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise DerivedIesError("hemisphere flux values must be finite and non-negative.")
        if self.full_sphere_flux_cd_sr <= 0.0 or self.downward_hemisphere_flux_cd_sr <= 0.0:
            raise DerivedIesError("full and downward Type-C flux must be positive.")
        if not math.isclose(
            self.full_sphere_flux_cd_sr,
            self.downward_hemisphere_flux_cd_sr + self.upward_hemisphere_flux_cd_sr,
            rel_tol=1e-13,
            abs_tol=1e-12,
        ):
            raise DerivedIesError("full Type-C flux must equal downward plus upward flux.")
        if not math.isclose(
            self.downward_fraction_of_full_sphere
            + self.upward_fraction_of_full_sphere,
            1.0,
            rel_tol=0.0,
            abs_tol=1e-14,
        ):
            raise DerivedIesError("downward and upward fractions must sum to one.")
        if not (
            0.0 <= self.downward_fraction_of_full_sphere <= 1.0
            and 0.0 <= self.upward_fraction_of_full_sphere <= 1.0
        ):
            raise DerivedIesError("hemisphere fractions must lie in [0, 1].")
        if not self.horizontal_closure_counted_once or self.hemisphere_boundary_deg != 90.0:
            raise DerivedIesError("Type-C hemisphere integration policy is invalid.")

    def to_payload(self) -> dict[str, object]:
        return {
            "full_sphere_flux_cd_sr": self.full_sphere_flux_cd_sr,
            "downward_hemisphere_flux_cd_sr": self.downward_hemisphere_flux_cd_sr,
            "upward_hemisphere_flux_cd_sr": self.upward_hemisphere_flux_cd_sr,
            "downward_fraction_of_full_sphere": self.downward_fraction_of_full_sphere,
            "upward_fraction_of_full_sphere": self.upward_fraction_of_full_sphere,
            "horizontal_closure_counted_once": self.horizontal_closure_counted_once,
            "hemisphere_boundary_deg": self.hemisphere_boundary_deg,
        }


@dataclass(frozen=True, slots=True)
class DerivedDownwardNormalizedIesDocument:
    filename: str
    text: str
    sha256: str
    normalization_policy: str
    original_ies: OriginalIesIdentity
    original_flux: TypeCHemisphereFlux
    candela_normalization_scale: float
    derived_flux: TypeCHemisphereFlux
    vertical_angle_count: int
    horizontal_angle_count_with_closure: int
    duplicate_closure_plane_counted: int

    def __post_init__(self) -> None:
        if self.filename != DERIVED_IES_FILENAME:
            raise DerivedIesError("derived IES filename is fixed by policy.")
        if self.normalization_policy != DERIVED_IES_DOWNWARD_NORMALIZATION_POLICY:
            raise DerivedIesError("derived IES normalization policy is invalid.")
        if not self.text.endswith("\n") or "\r" in self.text:
            raise DerivedIesError("derived IES must use deterministic LF termination.")
        actual_hash = hashlib.sha256(self.text.encode("ascii")).hexdigest()
        if self.sha256 != actual_hash:
            raise DerivedIesError("derived IES content hash is stale.")
        if self.candela_normalization_scale <= 0.0:
            raise DerivedIesError("candela normalization scale must be positive.")
        if not math.isclose(
            self.derived_flux.downward_hemisphere_flux_cd_sr,
            1.0,
            rel_tol=0.0,
            abs_tol=DERIVED_IES_DOWNWARD_UNIT_ABS_TOLERANCE,
        ):
            raise DerivedIesError("derived IES must re-integrate to unit downward flux.")
        if self.derived_flux.full_sphere_flux_cd_sr < 1.0:
            raise DerivedIesError("derived full-sphere flux cannot be below downward flux.")
        if self.vertical_angle_count < 2 or self.horizontal_angle_count_with_closure < 2:
            raise DerivedIesError("derived IES must retain complete angle grids.")
        if self.duplicate_closure_plane_counted != 1:
            raise DerivedIesError("duplicate 360-degree closure must be counted once.")

    def to_payload(self) -> dict[str, object]:
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "normalization_policy": self.normalization_policy,
            "original_ies": self.original_ies.to_payload(),
            "original_flux_diagnostics": self.original_flux.to_payload(),
            "candela_normalization_scale": self.candela_normalization_scale,
            "derived_flux_diagnostics": self.derived_flux.to_payload(),
            "downward_unit_abs_tolerance": DERIVED_IES_DOWNWARD_UNIT_ABS_TOLERANCE,
            "transport_normalization_domain": TRANSPORT_NORMALIZATION_DOMAIN,
            "one_sided_aperture_policy": ONE_SIDED_APERTURE_POLICY,
            "excluded_upward_flux_limitation": EXCLUDED_UPWARD_FLUX_LIMITATION,
            "vertical_angle_count": self.vertical_angle_count,
            "horizontal_angle_count_with_closure": (
                self.horizontal_angle_count_with_closure
            ),
            "duplicate_closure_plane_counted": self.duplicate_closure_plane_counted,
            "absolute_output_calibration_included": False,
            "neutralized_lm63_fields": {
                "candela_multiplier": 1.0,
                "ballast_factor": 1.0,
                "future_use_factor": 1.0,
                "input_watts": 1.0,
            },
        }


def integrate_type_c_hemisphere_flux(
    photometry: Lm63Photometry,
) -> TypeCHemisphereFlux:
    """Integrate full, downward, and upward Type-C domains exactly once."""

    if not photometry.has_duplicate_horizontal_closure:
        raise DerivedIesError("downward staging requires exact 0/360 closure.")
    vertical = tuple(math.radians(value) for value in photometry.vertical_angles_deg)
    horizontal = tuple(math.radians(value) for value in photometry.horizontal_angles_deg)
    downward_cells: list[float] = []
    upward_cells: list[float] = []
    boundary = math.pi / 2.0
    for plane_index in range(len(horizontal) - 1):
        delta_horizontal = horizontal[plane_index + 1] - horizontal[plane_index]
        first = photometry.candela_by_horizontal_plane[plane_index]
        second = photometry.candela_by_horizontal_plane[plane_index + 1]
        for vertical_index in range(len(vertical) - 1):
            theta_start = vertical[vertical_index]
            theta_end = vertical[vertical_index + 1]
            corners = (
                first[vertical_index],
                first[vertical_index + 1],
                second[vertical_index],
                second[vertical_index + 1],
            )
            if theta_start < boundary:
                downward_cells.append(
                    _integrate_vertical_subcell(
                        delta_horizontal,
                        theta_start,
                        theta_end,
                        theta_start,
                        min(theta_end, boundary),
                        corners,
                    )
                )
            if theta_end > boundary:
                upward_cells.append(
                    _integrate_vertical_subcell(
                        delta_horizontal,
                        theta_start,
                        theta_end,
                        max(theta_start, boundary),
                        theta_end,
                        corners,
                    )
                )
    downward = math.fsum(downward_cells)
    upward = math.fsum(upward_cells)
    full = math.fsum((downward, upward))
    if not math.isfinite(full) or full <= 0.0 or downward <= 0.0:
        raise DerivedIesError("LM-63 hemisphere integrals must be finite and positive.")
    return TypeCHemisphereFlux(
        full_sphere_flux_cd_sr=full,
        downward_hemisphere_flux_cd_sr=downward,
        upward_hemisphere_flux_cd_sr=upward,
        downward_fraction_of_full_sphere=downward / full,
        upward_fraction_of_full_sphere=upward / full,
    )


def _integrate_vertical_subcell(
    delta_horizontal: float,
    full_theta_start: float,
    full_theta_end: float,
    sub_theta_start: float,
    sub_theta_end: float,
    corners: tuple[float, float, float, float],
) -> float:
    """Split a trapezoid linearly in the solid-angle coordinate cos(theta)."""

    full_u_start = _solid_angle_coordinate(full_theta_start)
    full_u_end = _solid_angle_coordinate(full_theta_end)
    sub_u_start = _solid_angle_coordinate(sub_theta_start)
    sub_u_end = _solid_angle_coordinate(sub_theta_end)
    first_start, first_end, second_start, second_end = corners
    sub_corners = (
        _interpolate_in_solid_angle(
            first_start, first_end, full_u_start, full_u_end, sub_u_start
        ),
        _interpolate_in_solid_angle(
            first_start, first_end, full_u_start, full_u_end, sub_u_end
        ),
        _interpolate_in_solid_angle(
            second_start, second_end, full_u_start, full_u_end, sub_u_start
        ),
        _interpolate_in_solid_angle(
            second_start, second_end, full_u_start, full_u_end, sub_u_end
        ),
    )
    corner_mean = 0.25 * math.fsum(sub_corners)
    return corner_mean * delta_horizontal * (sub_u_start - sub_u_end)


def _interpolate_in_solid_angle(
    start_value: float,
    end_value: float,
    u_start: float,
    u_end: float,
    target_u: float,
) -> float:
    fraction = (u_start - target_u) / (u_start - u_end)
    return start_value + fraction * (end_value - start_value)


def _solid_angle_coordinate(theta_rad: float) -> float:
    if math.isclose(theta_rad, math.pi / 2.0, rel_tol=0.0, abs_tol=1e-15):
        return 0.0
    return math.cos(theta_rad)


def build_downward_normalized_derived_ies(
    photometry: Lm63Photometry,
    *,
    original_ies: OriginalIesIdentity,
) -> DerivedDownwardNormalizedIesDocument:
    """Build a complete LM-63 table normalized over transported 0-90 degrees."""

    original_flux = integrate_type_c_hemisphere_flux(photometry)
    scale = 1.0 / original_flux.downward_hemisphere_flux_cd_sr
    normalized_table = tuple(
        tuple(value * scale for value in plane)
        for plane in photometry.candela_by_horizontal_plane
    )
    keywords = (*photometry.keywords, *_derivation_keywords(original_ies))
    text = _format_lm63(
        photometry,
        keywords=keywords,
        candela_by_horizontal_plane=normalized_table,
    )
    parsed = parse_lm63(text, source=f"derived:{DERIVED_IES_FILENAME}")
    _validate_round_trip_structure(photometry, parsed)
    derived_flux = integrate_type_c_hemisphere_flux(parsed)
    sha256 = hashlib.sha256(text.encode("ascii")).hexdigest()
    return DerivedDownwardNormalizedIesDocument(
        filename=DERIVED_IES_FILENAME,
        text=text,
        sha256=sha256,
        normalization_policy=DERIVED_IES_DOWNWARD_NORMALIZATION_POLICY,
        original_ies=original_ies,
        original_flux=original_flux,
        candela_normalization_scale=scale,
        derived_flux=derived_flux,
        vertical_angle_count=parsed.vertical_angle_count,
        horizontal_angle_count_with_closure=parsed.horizontal_angle_count,
        duplicate_closure_plane_counted=1,
    )


def _format_lm63(
    original: Lm63Photometry,
    *,
    keywords: tuple[Lm63Keyword, ...],
    candela_by_horizontal_plane: tuple[tuple[float, ...], ...],
) -> str:
    lines = [original.version]
    lines.extend(f"[{item.name}]{item.value}" for item in keywords)
    lines.extend(
        (
            "TILT=NONE",
            " ".join(
                (
                    "1",
                    "-1",
                    "1",
                    str(original.vertical_angle_count),
                    str(original.horizontal_angle_count),
                    str(original.photometric_type),
                    str(original.units_type),
                    _number(original.fixture_width_m),
                    _number(original.fixture_length_m),
                    _number(original.fixture_height_m),
                )
            ),
            "1 1 1",
        )
    )
    lines.extend(_format_numeric_rows(original.vertical_angles_deg))
    lines.extend(_format_numeric_rows(original.horizontal_angles_deg))
    for plane in candela_by_horizontal_plane:
        lines.extend(_format_numeric_rows(plane))
    return "\n".join(lines) + "\n"


def _derivation_keywords(
    original_ies: OriginalIesIdentity,
) -> tuple[Lm63Keyword, ...]:
    return (
        Lm63Keyword(
            "_FSPM_DERIVATION",
            "unit-downward-flux directional staging; not original measurement",
        ),
        Lm63Keyword("_FSPM_ORIGINAL_SHA256", original_ies.sha256),
        Lm63Keyword(
            "_FSPM_SCALE_FIELDS",
            "candela_multiplier=1 ballast=1 future_use=1 input_watts=1",
        ),
        Lm63Keyword(
            "_FSPM_ABSOLUTE_ROLE",
            "none; declared PAR PPF is external",
        ),
    )


def _validate_round_trip_structure(
    original: Lm63Photometry,
    derived: Lm63Photometry,
) -> None:
    if derived.vertical_angles_deg != original.vertical_angles_deg:
        raise DerivedIesError("derived IES changed the vertical angle grid.")
    if derived.horizontal_angles_deg != original.horizontal_angles_deg:
        raise DerivedIesError("derived IES changed the horizontal C-plane grid.")
    if (
        derived.vertical_angle_count != original.vertical_angle_count
        or derived.horizontal_angle_count != original.horizontal_angle_count
    ):
        raise DerivedIesError("derived IES changed the angular table dimensions.")
    if not derived.has_duplicate_horizontal_closure:
        raise DerivedIesError("derived IES lost the exact duplicate closure plane.")
    if (
        derived.candela_multiplier,
        derived.ballast_factor,
        derived.future_use_factor,
        derived.input_watts,
    ) != (1.0, 1.0, 1.0, 1.0):
        raise DerivedIesError("derived IES scaling fields were not neutralized.")
    original_max = max(value for row in original.candela_by_horizontal_plane for value in row)
    derived_max = max(value for row in derived.candela_by_horizontal_plane for value in row)
    if original_max <= 0.0 or derived_max <= 0.0:
        raise DerivedIesError("derived IES angular shape must be nonzero.")
    for original_row, derived_row in zip(
        original.candela_by_horizontal_plane,
        derived.candela_by_horizontal_plane,
        strict=True,
    ):
        for original_value, derived_value in zip(original_row, derived_row, strict=True):
            if not math.isclose(
                original_value / original_max,
                derived_value / derived_max,
                rel_tol=1e-13,
                abs_tol=1e-15,
            ):
                raise DerivedIesError("derived IES changed measured C-plane asymmetry.")


def _format_numeric_rows(values: Iterable[float], width: int = 12) -> list[str]:
    tokens = [_number(value) for value in values]
    return [" ".join(tokens[index : index + width]) for index in range(0, len(tokens), width)]


def _number(value: float | int) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise DerivedIesError("derived IES values must be finite.")
    return format(number, ".17g")


def _validate_sha256(label: str, value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise DerivedIesError(f"{label} SHA-256 is malformed.")
