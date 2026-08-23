"""Static configuration for the juvenile pre-heading Rex profile."""

from __future__ import annotations

from dataclasses import dataclass
import re
from types import MappingProxyType

from fspm_optics.plants.rex import GOLDEN_ANGLE_DEG

REX_JUVENILE_PREHEADING_PROFILE_ID = "rex_juvenile_preheading_12leaf_v1"
REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID = (
    "rex_juvenile_surface_sampling_uv_quarter_4x4_v1"
)
REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID = (
    "rex_juvenile_surface_sampling_equal_area_cell_aligned_4x4_v1"
)
REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS = frozenset(
    {
        REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID,
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID,
    }
)
REX_JUVENILE_OPTIMIZED_V_CUTS = (2, 4, 6)
REX_JUVENILE_OPTIMIZED_U_CUTS_BY_LEAF_RANK = MappingProxyType({
    1: (4, 6, 8),
    2: (4, 6, 8),
    3: (4, 6, 8),
    4: (4, 6, 8),
    5: (4, 6, 8),
    6: (3, 5, 8),
    7: (3, 5, 8),
    8: (3, 5, 8),
    9: (3, 5, 7),
    10: (2, 3, 5),
    11: (2, 3, 4),
    12: (2, 3, 5),
})
REX_JUVENILE_PREHEADING_GROWTH_STAGE = "juvenile_post_transplant_pre_heading"
REX_JUVENILE_PREHEADING_PHENOLOGICAL_BOUNDARY = "BBCH_19_pre_41"
REX_JUVENILE_PREHEADING_COHORT_COUNTS = (5, 4, 3)
# Exact scientific-coordinate bounds of the immutable generated profile.  These
# are profile data, not a nominal circular canopy approximation.
REX_JUVENILE_PREHEADING_LOCAL_X_BOUNDS_M = (
    -0.08162080833475895,
    0.08590156495994676,
)
REX_JUVENILE_PREHEADING_LOCAL_Y_BOUNDS_M = (
    -0.08414348531724969,
    0.06819392493850181,
)

_SAFE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class RexJuvenilePreheadingConfig:
    """Immutable identity and mesh contract for the static 9-DAT profile."""

    profile_id: str = REX_JUVENILE_PREHEADING_PROFILE_ID
    sampling_profile_id: str = (
        REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
    )
    plant_id: str = "rex_juvenile_preheading_12leaf_v1_plant_000"
    cultivar: str = "Rex butterhead lettuce"
    approximate_days_after_transplant: int = 9
    phenological_boundary: str = REX_JUVENILE_PREHEADING_PHENOLOGICAL_BOUNDARY
    calibration_status: str = (
        "literature_constrained_procedural_reference_geometry"
    )
    seed: int = 0
    leaf_count: int = 12
    projected_diameter_m: float = 0.18
    plant_height_m: float = 0.065
    nominal_leaf_thickness_m: float = 0.0008
    growth_stage: str = REX_JUVENILE_PREHEADING_GROWTH_STAGE
    base_angle_deg: float = 0.0
    golden_angle_deg: float = GOLDEN_ANGLE_DEG
    leaf_patch_u: int = 4
    leaf_patch_v: int = 4
    radiance_material_id: str = "rex_juvenile_leaf_surface"

    def __post_init__(self) -> None:
        fixed_values = {
            "profile_id": REX_JUVENILE_PREHEADING_PROFILE_ID,
            "plant_id": "rex_juvenile_preheading_12leaf_v1_plant_000",
            "cultivar": "Rex butterhead lettuce",
            "approximate_days_after_transplant": 9,
            "phenological_boundary": REX_JUVENILE_PREHEADING_PHENOLOGICAL_BOUNDARY,
            "calibration_status": (
                "literature_constrained_procedural_reference_geometry"
            ),
            "seed": 0,
            "leaf_count": 12,
            "projected_diameter_m": 0.18,
            "plant_height_m": 0.065,
            "nominal_leaf_thickness_m": 0.0008,
            "growth_stage": REX_JUVENILE_PREHEADING_GROWTH_STAGE,
            "base_angle_deg": 0.0,
            "golden_angle_deg": GOLDEN_ANGLE_DEG,
            "leaf_patch_u": 4,
            "leaf_patch_v": 4,
            "radiance_material_id": "rex_juvenile_leaf_surface",
        }
        for name, expected in fixed_values.items():
            value = getattr(self, name)
            if type(value) is not type(expected) or value != expected:
                raise ValueError(
                    f"{name} is fixed to {expected!r} for this profile."
                )
        if self.sampling_profile_id not in REX_JUVENILE_SURFACE_SAMPLING_PROFILE_IDS:
            raise ValueError(
                "sampling_profile_id must identify a declared juvenile surface "
                "sampling profile."
            )
        for name in ("plant_id", "radiance_material_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
                raise ValueError(
                    f"{name} must be a non-empty Radiance-safe identifier."
                )

    @property
    def leaf_patch_grid(self) -> tuple[int, int]:
        return (self.leaf_patch_u, self.leaf_patch_v)

    @property
    def cohort_counts(self) -> tuple[int, int, int]:
        return REX_JUVENILE_PREHEADING_COHORT_COUNTS

    def mesh_segments_for_layer(self, layer: str) -> tuple[int, int]:
        """Return the fixed tessellation that realizes the face contract."""

        if layer in {"outer", "mid"}:
            return (12, 8)
        if layer == "inner":
            return (8, 8)
        raise ValueError(f"Unknown juvenile Rex leaf layer: {layer!r}.")

    @property
    def uses_surface_constrained_receivers(self) -> bool:
        return (
            self.sampling_profile_id
            == REX_JUVENILE_OPTIMIZED_SURFACE_SAMPLING_PROFILE_ID
        )

    @property
    def sampling_calibration_status(self) -> str:
        if self.uses_surface_constrained_receivers:
            return "uncalibrated_for_phase27g_d2_surface_flux_display"
        return "legacy_phase27g_d2_surface_flux_calibration_compatible"

    def patch_cell_cuts_for_leaf_rank(
        self,
        leaf_rank: int,
        *,
        u_segments: int,
        v_segments: int,
    ) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
        """Return declared longitudinal/lateral cell boundaries for one leaf."""

        if self.uses_surface_constrained_receivers:
            try:
                u_cuts = REX_JUVENILE_OPTIMIZED_U_CUTS_BY_LEAF_RANK[leaf_rank]
            except KeyError as exc:
                raise ValueError(f"Unknown juvenile leaf rank: {leaf_rank!r}.") from exc
            v_cuts = REX_JUVENILE_OPTIMIZED_V_CUTS
        else:
            if u_segments % 4 or v_segments % 4:
                raise ValueError(
                    "Legacy quarter-grid sampling requires segments divisible by four."
                )
            u_cuts = tuple(u_segments * index // 4 for index in range(1, 4))
            v_cuts = tuple(v_segments * index // 4 for index in range(1, 4))
        return (
            (u_cuts[0], u_cuts[1], u_cuts[2]),
            (v_cuts[0], v_cuts[1], v_cuts[2]),
        )


def legacy_rex_juvenile_preheading_config() -> RexJuvenilePreheadingConfig:
    """Return the explicit replay configuration for the frozen D2 sampling."""

    return RexJuvenilePreheadingConfig(
        sampling_profile_id=REX_JUVENILE_LEGACY_SURFACE_SAMPLING_PROFILE_ID
    )
