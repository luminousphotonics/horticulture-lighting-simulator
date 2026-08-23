"""Default mechanical envelope for the 145-site Proposed LED module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from .optical_stack import (
    APERTURE_SIDE_M,
    BEZEL_OUTER_SIDE_M,
    EMITTER_SIDE_M,
    INTERNAL_CAVITY_HEIGHT_M,
    PMMA_SIDE_M,
    PMMA_THICKNESS_M,
    PTFE_PHYSICAL_THICKNESS_M,
)

MODULE_PROFILE_VERSION: Final = "145led_clear_lid_ptfe_stack_v2"


@dataclass(frozen=True, slots=True)
class SmdModuleProfile:
    """Mechanical facts needed by layout code; no emitter writer behavior."""

    profile_id: str
    pcb_side_m: float
    window_side_m: float
    site_pitch_m: float
    lid_side_m: float
    lid_thickness_m: float
    stack_height_m: float
    bezel_pocket_m: float
    gasket_aperture_m: float
    ptfe_liner_thickness_m: float


DEFAULT_SMD_MODULE_PROFILE: Final = SmdModuleProfile(
    profile_id=MODULE_PROFILE_VERSION,
    pcb_side_m=0.150,
    window_side_m=EMITTER_SIDE_M,
    site_pitch_m=0.007,
    lid_side_m=PMMA_SIDE_M,
    lid_thickness_m=PMMA_THICKNESS_M,
    stack_height_m=INTERNAL_CAVITY_HEIGHT_M,
    bezel_pocket_m=BEZEL_OUTER_SIDE_M,
    gasket_aperture_m=APERTURE_SIDE_M,
    ptfe_liner_thickness_m=PTFE_PHYSICAL_THICKNESS_M,
)
