"""Unit-total angular allocation from validated Type-C LM-63 photometry."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Final

from .errors import AngularNormalizationError
from .lm63 import Lm63Photometry

ANGULAR_NORMALIZATION_POLICY: Final = (
    "type_c_periodic_cell_trapezoid_solid_angle_unit_total_v1"
)
ANGULAR_NORMALIZATION_ABS_TOLERANCE: Final = 1e-12


@dataclass(frozen=True, slots=True)
class Lm63AppliedFactors:
    """Applicable LM-63 magnitude fields, represented exactly once."""

    candela_multiplier: float
    ballast_factor: float
    future_use_factor: float

    def __post_init__(self) -> None:
        for name in ("candela_multiplier", "ballast_factor", "future_use_factor"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                or float(value) <= 0.0
            ):
                raise AngularNormalizationError(
                    f"LM-63 {name} must be finite and positive."
                )
            object.__setattr__(self, name, float(value))

    @property
    def product(self) -> float:
        return self.candela_multiplier * self.ballast_factor * self.future_use_factor

    def to_dict(self) -> dict[str, float]:
        return {
            "candela_multiplier": self.candela_multiplier,
            "ballast_factor": self.ballast_factor,
            "future_use_factor": self.future_use_factor,
            "applied_product": self.product,
        }


@dataclass(frozen=True, slots=True)
class NormalizedAngularDistribution:
    """Magnitude-invariant directional probabilities over Type-C angular cells."""

    distribution_id: str
    asset_sha256: str
    normalization_policy: str
    vertical_angles_deg: tuple[float, ...]
    horizontal_angles_deg_with_closure: tuple[float, ...]
    relative_candela_by_unique_plane: tuple[tuple[float, ...], ...]
    cell_probabilities: tuple[tuple[float, ...], ...]
    applied_factors: Lm63AppliedFactors
    scaled_table_integral_cd_sr: float
    horizontal_domain_radians: float
    duplicate_closure_plane_counted: int
    integration_assumptions: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.distribution_id or not self.normalization_policy:
            raise AngularNormalizationError(
                "angular distribution identity and policy must be non-empty."
            )
        if self.normalization_policy != ANGULAR_NORMALIZATION_POLICY:
            raise AngularNormalizationError("angular normalization policy is invalid.")
        if len(self.asset_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.asset_sha256
        ):
            raise AngularNormalizationError("angular asset SHA-256 is malformed.")
        if self.distribution_id != angular_distribution_id(self.asset_sha256):
            raise AngularNormalizationError("angular distribution identity is stale.")
        horizontal_segment_count = len(self.horizontal_angles_deg_with_closure) - 1
        vertical_segment_count = len(self.vertical_angles_deg) - 1
        if horizontal_segment_count <= 0 or vertical_segment_count <= 0:
            raise AngularNormalizationError("angular distribution requires complete cells.")
        if len(self.relative_candela_by_unique_plane) != horizontal_segment_count:
            raise AngularNormalizationError(
                "relative candela planes must omit exactly the duplicate closure plane."
            )
        if any(
            len(plane) != len(self.vertical_angles_deg)
            for plane in self.relative_candela_by_unique_plane
        ):
            raise AngularNormalizationError("relative candela plane dimensions are invalid.")
        relative_values = tuple(
            value for plane in self.relative_candela_by_unique_plane for value in plane
        )
        if any(
            not math.isfinite(value) or not 0.0 <= value <= 1.0
            for value in relative_values
        ):
            raise AngularNormalizationError(
                "relative candela values must be finite fractions in [0, 1]."
            )
        if max(relative_values) != 1.0:
            raise AngularNormalizationError("relative candela shape must be max-normalized.")
        if len(self.cell_probabilities) != horizontal_segment_count or any(
            len(row) != vertical_segment_count for row in self.cell_probabilities
        ):
            raise AngularNormalizationError("angular probability cell dimensions are invalid.")
        probabilities = tuple(value for row in self.cell_probabilities for value in row)
        if any(not math.isfinite(value) or value < 0.0 for value in probabilities):
            raise AngularNormalizationError(
                "angular cell probabilities must be finite and non-negative."
            )
        if not math.isclose(
            math.fsum(probabilities),
            1.0,
            rel_tol=0.0,
            abs_tol=ANGULAR_NORMALIZATION_ABS_TOLERANCE,
        ):
            raise AngularNormalizationError("angular cell probabilities must sum to one.")
        if not math.isclose(
            self.horizontal_domain_radians,
            2.0 * math.pi,
            rel_tol=0.0,
            abs_tol=1e-14,
        ):
            raise AngularNormalizationError(
                "angular horizontal segments must cover the periodic domain exactly once."
            )
        if self.duplicate_closure_plane_counted != 1:
            raise AngularNormalizationError(
                "the duplicate 0/360 closure must be represented exactly once."
            )
        if (
            not math.isfinite(self.scaled_table_integral_cd_sr)
            or self.scaled_table_integral_cd_sr <= 0.0
        ):
            raise AngularNormalizationError("angular table integral must be positive.")

    @property
    def total_probability(self) -> float:
        return math.fsum(value for row in self.cell_probabilities for value in row)

    def allocate_ppf_umol_s(
        self,
        declared_fixture_par_ppf_umol_s: float,
    ) -> tuple[tuple[float, ...], ...]:
        """Apply a separately declared fixture PPF to unit probabilities once."""

        if (
            isinstance(declared_fixture_par_ppf_umol_s, bool)
            or not isinstance(declared_fixture_par_ppf_umol_s, int | float)
            or not math.isfinite(float(declared_fixture_par_ppf_umol_s))
            or float(declared_fixture_par_ppf_umol_s) <= 0.0
        ):
            raise AngularNormalizationError("declared fixture PAR PPF must be positive.")
        ppf = float(declared_fixture_par_ppf_umol_s)
        return tuple(
            tuple(probability * ppf for probability in row)
            for row in self.cell_probabilities
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "distribution_id": self.distribution_id,
            "asset_sha256": self.asset_sha256,
            "normalization_policy": self.normalization_policy,
            "integration_assumptions": list(self.integration_assumptions),
            "vertical_angles_deg": list(self.vertical_angles_deg),
            "horizontal_angles_deg_with_closure": list(
                self.horizontal_angles_deg_with_closure
            ),
            "unique_horizontal_plane_count": len(
                self.relative_candela_by_unique_plane
            ),
            "cell_dimensions": {
                "horizontal": len(self.cell_probabilities),
                "vertical": len(self.cell_probabilities[0]),
            },
            "relative_candela_by_unique_plane": [
                list(row) for row in self.relative_candela_by_unique_plane
            ],
            "cell_probabilities": [list(row) for row in self.cell_probabilities],
            "total_probability": self.total_probability,
            "applied_lm63_factors": self.applied_factors.to_dict(),
            "scaled_table_integral_cd_sr": self.scaled_table_integral_cd_sr,
            "horizontal_domain_radians": self.horizontal_domain_radians,
            "duplicate_closure_plane_counted": self.duplicate_closure_plane_counted,
            "absolute_par_calibration_included": False,
        }


def angular_distribution_id(asset_sha256: str) -> str:
    canonical = json.dumps(
        {
            "asset_sha256": asset_sha256,
            "normalization_policy": ANGULAR_NORMALIZATION_POLICY,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "conventional_led_angular_v1_" + hashlib.sha256(canonical).hexdigest()[:20]


def normalize_lm63_angular_distribution(
    photometry: Lm63Photometry,
    *,
    asset_sha256: str,
) -> NormalizedAngularDistribution:
    """Integrate the periodic table once and normalize it to unit total.

    Every adjacent C/gamma cell uses the mean of its four measured corner
    intensities multiplied by its exact solid angle ``delta_C *
    (cos(gamma_0) - cos(gamma_1))``. This is a cell trapezoid over C and the
    solid-angle coordinate. The terminal 360-degree row closes the final cell
    but is not treated as an additional plane.
    """

    if not photometry.has_duplicate_horizontal_closure:
        raise AngularNormalizationError(
            "angular normalization requires an exact duplicate 0/360 closure."
        )
    factors = Lm63AppliedFactors(
        candela_multiplier=photometry.candela_multiplier,
        ballast_factor=photometry.ballast_factor,
        future_use_factor=photometry.future_use_factor,
    )
    factor_product = factors.product
    if not math.isfinite(factor_product) or factor_product <= 0.0:
        raise AngularNormalizationError("combined LM-63 magnitude factor is invalid.")

    max_candela = max(
        value
        for plane in photometry.candela_by_horizontal_plane[:-1]
        for value in plane
    )
    if max_candela <= 0.0:
        raise AngularNormalizationError("LM-63 angular table has no positive intensity.")
    relative_planes = tuple(
        tuple(value / max_candela for value in plane)
        for plane in photometry.candela_by_horizontal_plane[:-1]
    )

    raw_cells: list[tuple[float, ...]] = []
    horizontal_widths: list[float] = []
    vertical = tuple(math.radians(value) for value in photometry.vertical_angles_deg)
    horizontal = tuple(math.radians(value) for value in photometry.horizontal_angles_deg)
    for plane_index in range(len(horizontal) - 1):
        delta_horizontal = horizontal[plane_index + 1] - horizontal[plane_index]
        horizontal_widths.append(delta_horizontal)
        first_plane = photometry.candela_by_horizontal_plane[plane_index]
        second_plane = photometry.candela_by_horizontal_plane[plane_index + 1]
        row: list[float] = []
        for vertical_index in range(len(vertical) - 1):
            solid_angle = delta_horizontal * (
                math.cos(vertical[vertical_index])
                - math.cos(vertical[vertical_index + 1])
            )
            corner_mean = 0.25 * factor_product * (
                first_plane[vertical_index]
                + first_plane[vertical_index + 1]
                + second_plane[vertical_index]
                + second_plane[vertical_index + 1]
            )
            row.append(corner_mean * solid_angle)
        raw_cells.append(tuple(row))
    horizontal_domain = math.fsum(horizontal_widths)
    total = math.fsum(value for row in raw_cells for value in row)
    if not math.isfinite(total) or total <= 0.0:
        raise AngularNormalizationError("LM-63 solid-angle integral must be positive.")
    probabilities = tuple(
        tuple(value / total for value in row)
        for row in raw_cells
    )
    return NormalizedAngularDistribution(
        distribution_id=angular_distribution_id(asset_sha256),
        asset_sha256=asset_sha256,
        normalization_policy=ANGULAR_NORMALIZATION_POLICY,
        vertical_angles_deg=photometry.vertical_angles_deg,
        horizontal_angles_deg_with_closure=photometry.horizontal_angles_deg,
        relative_candela_by_unique_plane=relative_planes,
        cell_probabilities=probabilities,
        applied_factors=factors,
        scaled_table_integral_cd_sr=total,
        horizontal_domain_radians=horizontal_domain,
        duplicate_closure_plane_counted=1,
        integration_assumptions=(
            "Type-C C-planes are periodic over 0 <= C < 360 degrees.",
            "The 360-degree duplicate closes the final cell and contributes no extra plane.",
            "Each angular cell uses its four-corner trapezoidal mean and exact solid angle.",
            "Candela multiplier, ballast factor, and future-use factor are applied once before normalization.",
            "The resulting distribution is relative only; declared PAR PPF is external.",
        ),
    )


def format_angular_distribution_json(
    distribution: NormalizedAngularDistribution,
) -> str:
    return json.dumps(distribution.to_payload(), indent=2, sort_keys=True) + "\n"
