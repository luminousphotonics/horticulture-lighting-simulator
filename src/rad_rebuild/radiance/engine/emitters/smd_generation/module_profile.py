from __future__ import annotations

from typing import Any


# Shared profile tag used to invalidate stale SMD precomputed bundles when the
# module electrical baseline changes in a meaningful way.
MODULE_PROFILE_VERSION = "145led_clear_lid_ptfe_stack_v2"

# Optical / mechanical envelope taken from the optical-stack package.
PCB_SIDE_M = 0.150
WINDOW_SIDE_M = 0.120
SITE_PITCH_M = 0.007
LID_SIDE_M = 0.128
LID_THK_M = 0.003
STACK_HEIGHT_M = 0.008
BEZEL_POCKET_M = 0.129
GASKET_APERTURE_M = 0.126
PTFE_LINER_THK_M = 0.000508
PTFE_REFLECTANCE = 0.97

# Real 145-site channel composition for the module.
CHANNEL_COUNTS = {
    "WW": 52,
    "CW": 52,
    "R": 41,
    "B": 0,
    "FR": 0,
    "C": 0,
    "UV": 0,
}

# Nominal per-package electrical power assumptions near the intended operating
# current (~233 mA per package string).
PER_LED_W = {
    "WW": 0.68,
    "CW": 0.68,
    "R": 0.44,
    "B": 0.0,
    "FR": 0.0,
    "C": 0.0,
    "UV": 0.0,
}

# Package-level nominal PPE assumptions. These are not measured finished-module
# values; PMMA, wiring, driver, and thermal losses are still applied downstream.
PPE_UMOL_PER_J = {
    "WW": 2.73,
    "CW": 2.81,
    "R": 4.13,
    "B": 0.0,
    "FR": 0.0,
    "C": 0.0,
    "UV": 0.0,
}


def module_profile_meta() -> dict[str, Any]:
    return {
        "module_profile": MODULE_PROFILE_VERSION,
        "pcb_side_m": PCB_SIDE_M,
        "window_side_m": WINDOW_SIDE_M,
        "site_pitch_m": SITE_PITCH_M,
        "lid_side_m": LID_SIDE_M,
        "lid_thk_m": LID_THK_M,
        "stack_height_m": STACK_HEIGHT_M,
        "bezel_pocket_m": BEZEL_POCKET_M,
        "gasket_aperture_m": GASKET_APERTURE_M,
        "ptfe_liner_thk_m": PTFE_LINER_THK_M,
        "ptfe_reflectance": PTFE_REFLECTANCE,
        "channel_counts": dict(CHANNEL_COUNTS),
        "per_led_w": dict(PER_LED_W),
        "ppe_umol_per_j": dict(PPE_UMOL_PER_J),
    }
