"""Validated configuration for deterministic plant geometry generation."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import TypeAlias

Range: TypeAlias = tuple[float, float]

_OPTICAL_TOLERANCE = 1e-9


def _require_finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _require_positive(name: str, value: float) -> float:
    value = _require_finite(name, value)
    if value <= 0.0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _require_non_negative(name: str, value: float) -> float:
    value = _require_finite(name, value)
    if value < 0.0:
        raise ValueError(f"{name} must be greater than or equal to zero")
    return value


def _require_fraction(name: str, value: float) -> float:
    value = _require_finite(name, value)
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} must be in 0..1")
    return value


def _require_positive_range(name: str, value: Range) -> Range:
    if len(value) != 2:
        raise ValueError(f"{name} must contain exactly two values")
    lower = _require_positive(f"{name}[0]", value[0])
    upper = _require_positive(f"{name}[1]", value[1])
    if lower > upper:
        raise ValueError(f"{name} lower bound must be <= upper bound")
    return (lower, upper)


def _require_finite_range(name: str, value: Range) -> Range:
    if len(value) != 2:
        raise ValueError(f"{name} must contain exactly two values")
    lower = _require_finite(f"{name}[0]", value[0])
    upper = _require_finite(f"{name}[1]", value[1])
    if lower > upper:
        raise ValueError(f"{name} lower bound must be <= upper bound")
    return (lower, upper)


def _require_positive_int(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class PlantOpticalAssumptions:
    """Leaf optical energy partition used as conservative Phase 01 metadata."""

    reflectance: float = 0.22
    transmittance: float = 0.08
    absorptance: float = 0.70

    def __post_init__(self) -> None:
        reflectance = _require_fraction("reflectance", self.reflectance)
        transmittance = _require_fraction("transmittance", self.transmittance)
        absorptance = _require_fraction("absorptance", self.absorptance)
        total = reflectance + transmittance + absorptance
        if not math.isclose(total, 1.0, abs_tol=_OPTICAL_TOLERANCE):
            raise ValueError(
                "optical reflectance, transmittance, and absorptance must sum to 1"
            )
        object.__setattr__(self, "reflectance", reflectance)
        object.__setattr__(self, "transmittance", transmittance)
        object.__setattr__(self, "absorptance", absorptance)


@dataclass(frozen=True)
class PlantGeometryConfig:
    """Meters-first deterministic leafy-green plant geometry configuration."""

    seed: int = 1
    plant_grid_rows: int = 2
    plant_grid_columns: int = 2
    plant_spacing_m: float = 0.30
    plant_height_m: float = 0.16
    canopy_radius_m: float = 0.18
    leaf_count_per_plant: int = 12
    leaf_length_range_m: Range = (0.10, 0.18)
    leaf_width_range_m: Range = (0.035, 0.075)
    leaf_tilt_range_deg: Range = (18.0, 52.0)
    leaf_curvature_m: float = 0.018
    growth_stage: float = 1.0
    optical: PlantOpticalAssumptions = field(default_factory=PlantOpticalAssumptions)

    def __post_init__(self) -> None:
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer")
        rows = _require_positive_int("plant_grid_rows", self.plant_grid_rows)
        columns = _require_positive_int(
            "plant_grid_columns", self.plant_grid_columns
        )
        leaf_count = _require_positive_int(
            "leaf_count_per_plant", self.leaf_count_per_plant
        )
        growth_stage = _require_fraction("growth_stage", self.growth_stage)

        object.__setattr__(self, "plant_grid_rows", rows)
        object.__setattr__(self, "plant_grid_columns", columns)
        object.__setattr__(self, "leaf_count_per_plant", leaf_count)
        object.__setattr__(
            self, "plant_spacing_m", _require_positive("plant_spacing_m", self.plant_spacing_m)
        )
        object.__setattr__(
            self, "plant_height_m", _require_positive("plant_height_m", self.plant_height_m)
        )
        object.__setattr__(
            self,
            "canopy_radius_m",
            _require_positive("canopy_radius_m", self.canopy_radius_m),
        )
        object.__setattr__(
            self,
            "leaf_length_range_m",
            _require_positive_range("leaf_length_range_m", self.leaf_length_range_m),
        )
        object.__setattr__(
            self,
            "leaf_width_range_m",
            _require_positive_range("leaf_width_range_m", self.leaf_width_range_m),
        )
        object.__setattr__(
            self,
            "leaf_tilt_range_deg",
            _require_finite_range("leaf_tilt_range_deg", self.leaf_tilt_range_deg),
        )
        object.__setattr__(
            self,
            "leaf_curvature_m",
            _require_non_negative("leaf_curvature_m", self.leaf_curvature_m),
        )
        object.__setattr__(self, "growth_stage", growth_stage)
