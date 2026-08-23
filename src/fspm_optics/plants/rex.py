"""Typed configuration for the mature Rex butterhead scientific archetype."""

from __future__ import annotations

from dataclasses import dataclass
import math
import re

GOLDEN_ANGLE_DEG = 137.507764
MATURE_REX_GROWTH_STAGE = "mature_greenhouse_head"
REX_LEAF_LAYERS = ("outer", "mid", "inner")

_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class RexPlantConfig:
    """Deterministic mature greenhouse Rex butterhead geometry settings."""

    plant_id: str = "rex_plant_000"
    seed: int = 1
    leaf_count: int = 32
    projected_diameter_m: float = 0.29
    plant_height_m: float = 0.16
    nominal_leaf_thickness_m: float = 0.0008
    growth_stage: str = MATURE_REX_GROWTH_STAGE
    base_angle_deg: float = 0.0
    golden_angle_deg: float = GOLDEN_ANGLE_DEG
    angular_jitter_deg: float = 4.0
    size_variation_fraction: float = 0.06
    leaf_patch_u: int = 4
    leaf_patch_v: int = 4
    radiance_material_id: str = "rex_leaf_surface"

    def __post_init__(self) -> None:
        for name in ("plant_id", "radiance_material_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                raise ValueError(
                    f"{name} must be a non-empty Radiance-safe identifier."
                )
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("seed must be an integer.")
        if (
            isinstance(self.leaf_count, bool)
            or not isinstance(self.leaf_count, int)
            or not 24 <= self.leaf_count <= 40
        ):
            raise ValueError("leaf_count must be an integer in 24..40.")
        _bounded(
            "projected_diameter_m",
            self.projected_diameter_m,
            0.25,
            0.32,
        )
        _bounded("plant_height_m", self.plant_height_m, 0.12, 0.20)
        _bounded(
            "nominal_leaf_thickness_m",
            self.nominal_leaf_thickness_m,
            0.0005,
            0.0015,
        )
        if self.growth_stage != MATURE_REX_GROWTH_STAGE:
            raise ValueError(
                f"growth_stage is fixed to {MATURE_REX_GROWTH_STAGE!r}."
            )
        for name in ("base_angle_deg", "golden_angle_deg"):
            _finite(name, getattr(self, name))
        _bounded("angular_jitter_deg", self.angular_jitter_deg, 0.0, 15.0)
        _bounded(
            "size_variation_fraction",
            self.size_variation_fraction,
            0.0,
            0.20,
        )
        for name in ("leaf_patch_u", "leaf_patch_v"):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= 32
            ):
                raise ValueError(f"{name} must be an integer in 1..32.")

    @property
    def leaf_patch_grid(self) -> tuple[int, int]:
        return (self.leaf_patch_u, self.leaf_patch_v)

    def mesh_segments_for_layer(self, layer: str) -> tuple[int, int]:
        """Return fixed curvature tessellation derived from the patch grid."""

        if layer in {"outer", "mid"}:
            return (self.leaf_patch_u * 3, self.leaf_patch_v * 2)
        if layer == "inner":
            u_factor = 3 if self.leaf_patch_u == 1 else 2
            return (self.leaf_patch_u * u_factor, self.leaf_patch_v * 2)
        raise ValueError(f"Unknown Rex leaf layer: {layer!r}.")


def rex_layer_counts(leaf_count: int) -> tuple[int, int, int]:
    """Allocate approximately 40/40/20 percent outer/mid/inner leaves."""

    if isinstance(leaf_count, bool) or not isinstance(leaf_count, int):
        raise ValueError("leaf_count must be an integer.")
    outer = round(leaf_count * 0.40)
    mid = round(leaf_count * 0.40)
    inner = leaf_count - outer - mid
    return outer, mid, inner


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be finite.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    return number


def _bounded(name: str, value: float, lower: float, upper: float) -> float:
    number = _finite(name, value)
    if not lower <= number <= upper:
        raise ValueError(f"{name} must be in {lower:g}..{upper:g}.")
    return number
