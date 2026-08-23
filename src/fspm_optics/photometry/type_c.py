"""Deterministic LM-63 Type-C symmetry expansion and solid-angle integration."""

from __future__ import annotations

from dataclasses import dataclass, replace
import math

from .lm63 import Lm63Photometry, METRIC_UNITS


@dataclass(frozen=True, slots=True)
class Lm63AppliedFactors:
    candela_multiplier: float
    ballast_factor: float
    future_use_factor: float

    @property
    def product(self) -> float:
        return self.candela_multiplier * self.ballast_factor * self.future_use_factor

    def to_payload(self) -> dict[str, float]:
        return {
            "candela_multiplier": self.candela_multiplier,
            "ballast_factor": self.ballast_factor,
            "future_use_factor": self.future_use_factor,
            "applied_product": self.product,
        }


@dataclass(frozen=True, slots=True)
class TypeCFluxDiagnostics:
    full_expanded_table_flux_cd_sr: float
    downward_flux_cd_sr: float
    upward_flux_cd_sr: float
    horizontal_domain_degrees: float
    horizontal_closure_counted_once: bool

    def to_payload(self) -> dict[str, float | bool]:
        return {
            "full_expanded_table_flux_cd_sr": self.full_expanded_table_flux_cd_sr,
            "downward_flux_cd_sr": self.downward_flux_cd_sr,
            "upward_flux_cd_sr": self.upward_flux_cd_sr,
            "horizontal_domain_degrees": self.horizontal_domain_degrees,
            "horizontal_closure_counted_once": self.horizontal_closure_counted_once,
        }


def expand_type_c_horizontal_symmetry(photometry: Lm63Photometry) -> Lm63Photometry:
    """Expand a 0-90 Type-C quadrant to 0-360, with one duplicate closure row."""

    if photometry.has_duplicate_horizontal_closure:
        return photometry
    if not photometry.is_quadrant_symmetric_type_c:
        raise ValueError("Type-C symmetry expansion requires a declared 0-90 C-plane quadrant.")
    declared_angles = photometry.horizontal_angles_deg
    declared_rows = photometry.candela_by_horizontal_plane
    expanded: list[tuple[float, tuple[float, ...]]] = []
    for quadrant in range(4):
        if quadrant % 2 == 0:
            pairs = tuple(zip(declared_angles, declared_rows, strict=True))
        else:
            pairs = tuple(
                (90.0 - angle, row)
                for angle, row in zip(declared_angles, declared_rows, strict=True)
            )[::-1]
        base = 90.0 * quadrant
        for local_angle, row in pairs:
            angle = base + local_angle
            if expanded and angle == expanded[-1][0]:
                if row != expanded[-1][1]:
                    raise ValueError("Type-C quadrant boundary planes are inconsistent.")
                continue
            expanded.append((angle, row))
    if expanded[-1][0] != 360.0 or expanded[-1][1] != expanded[0][1]:
        raise ValueError("Type-C quadrant expansion did not produce exact 0/360 closure.")
    angles = tuple(angle for angle, _ in expanded)
    rows = tuple(row for _, row in expanded)
    return replace(
        photometry,
        horizontal_angle_count=len(angles),
        horizontal_angles_deg=angles,
        candela_by_horizontal_plane=rows,
    )


def integrate_type_c_flux(
    photometry: Lm63Photometry,
    *,
    apply_lm63_factors: bool = True,
) -> TypeCFluxDiagnostics:
    """Integrate adjacent Type-C cells over the represented vertical domain."""

    if not photometry.has_duplicate_horizontal_closure:
        raise ValueError("Type-C flux integration requires exact 0/360 closure.")
    vertical = tuple(math.radians(value) for value in photometry.vertical_angles_deg)
    horizontal = tuple(math.radians(value) for value in photometry.horizontal_angles_deg)
    factor = photometry.applicable_uniform_factor if apply_lm63_factors else 1.0
    downward_cells: list[float] = []
    upward_cells: list[float] = []
    for plane_index in range(len(horizontal) - 1):
        delta_horizontal = horizontal[plane_index + 1] - horizontal[plane_index]
        first = photometry.candela_by_horizontal_plane[plane_index]
        second = photometry.candela_by_horizontal_plane[plane_index + 1]
        for vertical_index in range(len(vertical) - 1):
            start = vertical[vertical_index]
            end = vertical[vertical_index + 1]
            cell = 0.25 * factor * (
                first[vertical_index]
                + first[vertical_index + 1]
                + second[vertical_index]
                + second[vertical_index + 1]
            ) * delta_horizontal * (math.cos(start) - math.cos(end))
            if end <= math.pi / 2.0:
                downward_cells.append(cell)
            elif start >= math.pi / 2.0:
                upward_cells.append(cell)
            else:
                raise ValueError("a Type-C vertical cell may not cross 90 degrees implicitly.")
    downward = math.fsum(downward_cells)
    upward = math.fsum(upward_cells)
    total = math.fsum((downward, upward))
    if not math.isfinite(total) or total <= 0.0 or downward <= 0.0:
        raise ValueError("Type-C flux integrals must be finite with positive downward flux.")
    return TypeCFluxDiagnostics(
        full_expanded_table_flux_cd_sr=total,
        downward_flux_cd_sr=downward,
        upward_flux_cd_sr=upward,
        horizontal_domain_degrees=math.degrees(math.fsum(
            second - first for first, second in zip(horizontal, horizontal[1:])
        )),
        horizontal_closure_counted_once=True,
    )


def as_metric_dimensions(photometry: Lm63Photometry) -> Lm63Photometry:
    """Return an equivalent record whose source-dimension fields are metres."""

    return replace(
        photometry,
        units_type=METRIC_UNITS,
        source_fixture_width=photometry.fixture_width_m,
        source_fixture_length=photometry.fixture_length_m,
        source_fixture_height=photometry.fixture_height_m,
    )
