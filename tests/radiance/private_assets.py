from __future__ import annotations

import os
from pathlib import Path

import pytest

from rad_rebuild.radiance.paths import RADIANCE_IES_ROOT


PRIVATE_IES_TESTS_ENV = "RAD_REBUILD_ENABLE_PRIVATE_IES_TESTS"
PRIVATE_IES_SKIP_REASON = (
    "private IES source assets are not included in the public repository"
)


def require_private_ies_assets(*relative_paths: str) -> Path:
    """Return the private IES root when opt-in private assets are available."""
    if os.getenv(PRIVATE_IES_TESTS_ENV) != "1":
        pytest.skip(PRIVATE_IES_SKIP_REASON)
    if not RADIANCE_IES_ROOT.is_dir():
        pytest.skip(PRIVATE_IES_SKIP_REASON)
    for relative_path in relative_paths:
        if not (RADIANCE_IES_ROOT / relative_path).is_file():
            pytest.skip(PRIVATE_IES_SKIP_REASON)
    return RADIANCE_IES_ROOT
