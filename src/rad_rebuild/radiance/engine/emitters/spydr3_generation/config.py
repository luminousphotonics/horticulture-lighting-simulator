from __future__ import annotations

import os
from pathlib import Path

from rad_rebuild.radiance.paths import RADIANCE_CURVE_DATA_ROOT, RADIANCE_IES_ROOT

IN2M = 0.0254
FT2M = IN2M * 12.0

DEFAULT_MODEL = "QB-FSG-8B-7T660W"
DEFAULT_FIXTURE_INPUT_W = 800.0
DEFAULT_FIXTURE_PPE_UMOL_PER_J = 2.8
DEFAULT_FIXTURE_PPF_UMOL_S = DEFAULT_FIXTURE_INPUT_W * DEFAULT_FIXTURE_PPE_UMOL_PER_J
DEFAULT_BARS = 8
DEFAULT_FIXTURE_LENGTH_IN = 46.85
DEFAULT_FIXTURE_WIDTH_IN = 42.8
DEFAULT_FIXTURE_HEIGHT_IN = 4.26
DEFAULT_IES_PATH = RADIANCE_IES_ROOT / "qube_660w_8bar.ies"
DEFAULT_IES_SPD_PATH = RADIANCE_CURVE_DATA_ROOT / "conventional" / "qube_660w_spd.csv"
QUBE_IES_LABEL = "EnVision Qube QB-FSG-8B-7T660W IES+SPD comparator"


def runtime_state_root() -> Path:
    return Path(os.getenv("RADIANCE_RUNTIME_STATE_ROOT", "runtime_state"))


def clamp_derate(raw: float) -> float:
    if raw < 0.0:
        print(f"NOTE: EFF_SCALE {raw} < 0; clamping to 0.0")
        return 0.0
    if raw > 1.0:
        print(f"NOTE: EFF_SCALE {raw} > 1; clamping to 1.0 (no overdrive)")
        return 1.0
    return raw
