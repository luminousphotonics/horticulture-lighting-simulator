"""Quadrant-preserving unit-downward angular staging for HPS photometry."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import math
from typing import Final, Iterable

from fspm_optics.photometry.lm63 import Lm63Keyword, Lm63Photometry, parse_lm63
from fspm_optics.photometry.type_c import (
    Lm63AppliedFactors,
    TypeCFluxDiagnostics,
    as_metric_dimensions,
    expand_type_c_horizontal_symmetry,
    integrate_type_c_flux,
)

from .errors import HpsPhotometryError
from .profile import HPS_ANGULAR_DISTRIBUTION_ID
from .resources import HPS_IES_RESOURCE_NAME, HPS_IES_SHA256

HPS_ANGULAR_NORMALIZATION_POLICY: Final = (
    "lm63_type_c_quadrant_expansion_exclude_upward_unit_downward_flux_v2"
)
HPS_DERIVED_IES_FILENAME: Final = "hps_unit_downward_flux.ies"


@dataclass(frozen=True, slots=True)
class HpsAngularDistribution:
    distribution_id: str
    normalization_policy: str
    asset_sha256: str
    declared_horizontal_angles_deg: tuple[float, ...]
    expanded_horizontal_angles_deg_with_closure: tuple[float, ...]
    vertical_angles_deg: tuple[float, ...]
    relative_candela_by_expanded_plane: tuple[tuple[float, ...], ...]
    unit_downward_candela_by_expanded_plane: tuple[tuple[float, ...], ...]
    applied_factors: Lm63AppliedFactors
    original_flux: TypeCFluxDiagnostics
    duplicate_closure_plane_counted: int

    def __post_init__(self) -> None:
        if self.distribution_id != HPS_ANGULAR_DISTRIBUTION_ID:
            raise HpsPhotometryError("HPS angular distribution identity is invalid.")
        if self.normalization_policy != HPS_ANGULAR_NORMALIZATION_POLICY:
            raise HpsPhotometryError("HPS angular normalization policy is invalid.")
        if self.asset_sha256 != HPS_IES_SHA256:
            raise HpsPhotometryError("HPS angular asset hash is invalid.")
        if self.expanded_horizontal_angles_deg_with_closure[0] != 0.0 or (
            self.expanded_horizontal_angles_deg_with_closure[-1] != 360.0
        ):
            raise HpsPhotometryError("HPS angular table must close from 0 to 360 degrees.")
        if self.duplicate_closure_plane_counted != 1:
            raise HpsPhotometryError("HPS horizontal closure must be counted once.")
        if not math.isclose(
            self.original_flux.horizontal_domain_degrees,
            360.0,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise HpsPhotometryError("HPS expanded C-plane domain must cover 360 degrees.")
        values = tuple(value for row in self.relative_candela_by_expanded_plane for value in row)
        if not values or max(values) != 1.0 or min(values) < 0.0:
            raise HpsPhotometryError("HPS relative candela shape must be non-negative/unit-peak.")
        normalized_values = tuple(
            value for row in self.unit_downward_candela_by_expanded_plane for value in row
        )
        if len(normalized_values) != len(values) or min(normalized_values) < 0.0:
            raise HpsPhotometryError("HPS unit-downward candela table is invalid.")

    def to_payload(self) -> dict[str, object]:
        total_original_flux = (
            self.original_flux.downward_flux_cd_sr
            + self.original_flux.upward_flux_cd_sr
        )
        return {
            "schema_version": 2,
            "distribution_id": self.distribution_id,
            "normalization_policy": self.normalization_policy,
            "asset_sha256": self.asset_sha256,
            "declared_horizontal_angles_deg": list(self.declared_horizontal_angles_deg),
            "declared_symmetry": "lm63_type_c_0_to_90_quadrant",
            "expanded_horizontal_angles_deg_with_closure": list(
                self.expanded_horizontal_angles_deg_with_closure
            ),
            "vertical_angles_deg": list(self.vertical_angles_deg),
            "relative_candela_by_expanded_plane": [
                list(row) for row in self.relative_candela_by_expanded_plane
            ],
            "unit_downward_candela_by_expanded_plane": [
                list(row) for row in self.unit_downward_candela_by_expanded_plane
            ],
            "applied_lm63_factors": self.applied_factors.to_payload(),
            "original_flux_diagnostics": self.original_flux.to_payload(),
            "excluded_upward_flux_cd_sr": self.original_flux.upward_flux_cd_sr,
            "excluded_upward_fraction": (
                self.original_flux.upward_flux_cd_sr / total_original_flux
            ),
            "duplicate_closure_plane_counted": self.duplicate_closure_plane_counted,
            "absolute_PAR_calibration_included": False,
        }


@dataclass(frozen=True, slots=True)
class HpsDerivedIesDocument:
    filename: str
    text: str
    sha256: str
    normalization_policy: str
    asset_sha256: str
    applied_factors: Lm63AppliedFactors
    original_flux: TypeCFluxDiagnostics
    candela_normalization_scale: float
    derived_flux: TypeCFluxDiagnostics
    horizontal_angle_count_with_closure: int
    duplicate_closure_plane_counted: int

    def __post_init__(self) -> None:
        if self.filename != HPS_DERIVED_IES_FILENAME:
            raise HpsPhotometryError("HPS derived IES filename is fixed by policy.")
        if self.normalization_policy != HPS_ANGULAR_NORMALIZATION_POLICY:
            raise HpsPhotometryError("HPS derived IES normalization policy is invalid.")
        if self.sha256 != hashlib.sha256(self.text.encode("ascii")).hexdigest():
            raise HpsPhotometryError("HPS derived IES hash is stale.")
        if not self.text.endswith("\n") or "\r" in self.text:
            raise HpsPhotometryError("HPS derived IES must have deterministic LF endings.")
        if self.derived_flux.downward_flux_cd_sr != 1.0:
            raise HpsPhotometryError("HPS derived IES must integrate to unit downward flux.")
        if self.derived_flux.upward_flux_cd_sr != 0.0:
            raise HpsPhotometryError("HPS derived IES may not invent upward flux.")
        if self.horizontal_angle_count_with_closure != 17:
            raise HpsPhotometryError("HPS quadrant expansion must produce 17 C-planes.")
        if self.duplicate_closure_plane_counted != 1:
            raise HpsPhotometryError("HPS derived closure must be counted once.")

    def to_payload(self) -> dict[str, object]:
        total_original_flux = (
            self.original_flux.downward_flux_cd_sr
            + self.original_flux.upward_flux_cd_sr
        )
        return {
            "filename": self.filename,
            "sha256": self.sha256,
            "normalization_policy": self.normalization_policy,
            "original_resource": {
                "name": HPS_IES_RESOURCE_NAME,
                "sha256": self.asset_sha256,
                "absolute_calibration_role": "none_shape_and_footprint_only",
            },
            "applied_lm63_factors": self.applied_factors.to_payload(),
            "original_flux_diagnostics": self.original_flux.to_payload(),
            "excluded_upward_flux_cd_sr": self.original_flux.upward_flux_cd_sr,
            "excluded_upward_fraction": (
                self.original_flux.upward_flux_cd_sr / total_original_flux
            ),
            "candela_normalization_scale": self.candela_normalization_scale,
            "derived_flux_diagnostics": self.derived_flux.to_payload(),
            "transport_domain": "type_c_vertical_0_to_90_downward_hemisphere",
            "horizontal_angle_count_with_closure": self.horizontal_angle_count_with_closure,
            "duplicate_closure_plane_counted": self.duplicate_closure_plane_counted,
            "neutralized_lm63_fields": {
                "lumens_per_lamp": -1.0,
                "candela_multiplier": 1.0,
                "ballast_factor": 1.0,
                "future_use_factor": 1.0,
                "input_watts": 1.0,
            },
            "legacy_lumen_or_spd_absolute_bridge_used": False,
        }


def normalize_hps_angular_distribution(
    photometry: Lm63Photometry,
) -> HpsAngularDistribution:
    expanded = expand_type_c_horizontal_symmetry(photometry)
    diagnostics = integrate_type_c_flux(expanded, apply_lm63_factors=True)
    maximum = max(value for row in expanded.candela_by_horizontal_plane for value in row)
    if maximum <= 0.0:
        raise HpsPhotometryError("HPS candela table must contain positive intensity.")
    relative = tuple(
        tuple(value / maximum for value in row)
        for row in expanded.candela_by_horizontal_plane
    )
    scale = photometry.applicable_uniform_factor / diagnostics.downward_flux_cd_sr
    unit_downward = tuple(
        tuple(
            value * scale if angle <= 90.0 else 0.0
            for angle, value in zip(
                expanded.vertical_angles_deg,
                row,
                strict=True,
            )
        )
        for row in expanded.candela_by_horizontal_plane
    )
    return HpsAngularDistribution(
        distribution_id=HPS_ANGULAR_DISTRIBUTION_ID,
        normalization_policy=HPS_ANGULAR_NORMALIZATION_POLICY,
        asset_sha256=HPS_IES_SHA256,
        declared_horizontal_angles_deg=photometry.horizontal_angles_deg,
        expanded_horizontal_angles_deg_with_closure=expanded.horizontal_angles_deg,
        vertical_angles_deg=expanded.vertical_angles_deg,
        relative_candela_by_expanded_plane=relative,
        unit_downward_candela_by_expanded_plane=unit_downward,
        applied_factors=_applied_factors(photometry),
        original_flux=diagnostics,
        duplicate_closure_plane_counted=1,
    )


def build_hps_derived_ies(photometry: Lm63Photometry) -> HpsDerivedIesDocument:
    expanded = expand_type_c_horizontal_symmetry(photometry)
    applied = _applied_factors(photometry)
    original_flux = integrate_type_c_flux(expanded, apply_lm63_factors=True)
    scale = applied.product / original_flux.downward_flux_cd_sr
    normalized = tuple(
        tuple(
            value * scale if angle <= 90.0 else 0.0
            for angle, value in zip(
                expanded.vertical_angles_deg,
                row,
                strict=True,
            )
        )
        for row in expanded.candela_by_horizontal_plane
    )
    staged = replace(
        as_metric_dimensions(expanded),
        lumens_per_lamp=-1.0,
        candela_multiplier=1.0,
        ballast_factor=1.0,
        future_use_factor=1.0,
        input_watts=1.0,
        candela_by_horizontal_plane=normalized,
    )
    text = _format_lm63(staged)
    try:
        parsed = parse_lm63(text, source=f"derived:{HPS_DERIVED_IES_FILENAME}")
    except ValueError as exc:
        raise HpsPhotometryError(str(exc)) from exc
    derived_flux = integrate_type_c_flux(parsed, apply_lm63_factors=True)
    for _ in range(8):
        if derived_flux.downward_flux_cd_sr == 1.0:
            break
        correction_denominator = derived_flux.downward_flux_cd_sr
        staged = replace(
            parsed,
            keywords=photometry.keywords,
            candela_by_horizontal_plane=tuple(
                tuple(
                    value / correction_denominator
                    for value in row
                )
                for row in parsed.candela_by_horizontal_plane
            ),
        )
        scale /= correction_denominator
        text = _format_lm63(staged)
        try:
            parsed = parse_lm63(
                text,
                source=f"derived:{HPS_DERIVED_IES_FILENAME}",
            )
        except ValueError as exc:
            raise HpsPhotometryError(str(exc)) from exc
        derived_flux = integrate_type_c_flux(parsed, apply_lm63_factors=True)
    return HpsDerivedIesDocument(
        filename=HPS_DERIVED_IES_FILENAME,
        text=text,
        sha256=hashlib.sha256(text.encode("ascii")).hexdigest(),
        normalization_policy=HPS_ANGULAR_NORMALIZATION_POLICY,
        asset_sha256=HPS_IES_SHA256,
        applied_factors=applied,
        original_flux=original_flux,
        candela_normalization_scale=scale,
        derived_flux=derived_flux,
        horizontal_angle_count_with_closure=parsed.horizontal_angle_count,
        duplicate_closure_plane_counted=1,
    )


def format_hps_angular_json(distribution: HpsAngularDistribution) -> str:
    return json.dumps(distribution.to_payload(), indent=2, sort_keys=True) + "\n"


def _applied_factors(photometry: Lm63Photometry) -> Lm63AppliedFactors:
    return Lm63AppliedFactors(
        candela_multiplier=photometry.candela_multiplier,
        ballast_factor=photometry.ballast_factor,
        future_use_factor=photometry.future_use_factor,
    )


def _format_lm63(photometry: Lm63Photometry) -> str:
    keywords = (
        *photometry.keywords,
        Lm63Keyword(
            "_FSPM_DERIVATION",
            "quadrant-expanded unit-downward-flux shape; not original measurement",
        ),
        Lm63Keyword("_FSPM_ORIGINAL_SHA256", HPS_IES_SHA256),
        Lm63Keyword("_FSPM_ABSOLUTE_ROLE", "none; declared PAR PPF is external"),
    )
    lines = [photometry.version]
    lines.extend(f"[{item.name}]{item.value}" for item in keywords)
    lines.extend(
        (
            "TILT=NONE",
            " ".join(
                (
                    str(photometry.lamp_count),
                    _number(photometry.lumens_per_lamp),
                    _number(photometry.candela_multiplier),
                    str(photometry.vertical_angle_count),
                    str(photometry.horizontal_angle_count),
                    str(photometry.photometric_type),
                    str(photometry.units_type),
                    _number(photometry.fixture_width_m),
                    _number(photometry.fixture_length_m),
                    _number(photometry.fixture_height_m),
                )
            ),
            "1 1 1",
        )
    )
    lines.extend(_format_numeric_rows(photometry.vertical_angles_deg))
    lines.extend(_format_numeric_rows(photometry.horizontal_angles_deg))
    for row in photometry.candela_by_horizontal_plane:
        lines.extend(_format_numeric_rows(row))
    return "\n".join(lines) + "\n"


def _format_numeric_rows(values: Iterable[float], width: int = 12) -> list[str]:
    tokens = [_number(value) for value in values]
    return [" ".join(tokens[index : index + width]) for index in range(0, len(tokens), width)]


def _number(value: float | int) -> str:
    number = float(value)
    if not math.isfinite(number):
        raise HpsPhotometryError("HPS derived IES values must be finite.")
    return format(number, ".17g")
