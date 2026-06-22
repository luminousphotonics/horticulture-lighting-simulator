from __future__ import annotations

import atexit
import os
import tempfile


_RUNTIME_DIR = tempfile.TemporaryDirectory(prefix="rad_rebuild_test_runtime_")
atexit.register(_RUNTIME_DIR.cleanup)


def configure_test_runtime() -> str:
    """Point import-time backend runtime paths at temporary storage."""
    os.environ.setdefault("RADIANCE_OUTPUT_ROOT", _RUNTIME_DIR.name)
    os.environ.setdefault(
        "RADIANCE_BASIS_OUTPUT_ROOT", os.path.join(_RUNTIME_DIR.name, "basis")
    )
    os.environ.setdefault(
        "RADIANCE_CACHE_ROOT", os.path.join(_RUNTIME_DIR.name, "cache")
    )
    os.environ.setdefault("MPLCONFIGDIR", os.path.join(_RUNTIME_DIR.name, "matplotlib"))
    os.environ.setdefault(
        "RADIANCE_VISUALIZATION_OUTPUT_ROOT",
        os.path.join(_RUNTIME_DIR.name, "visualizations"),
    )
    return _RUNTIME_DIR.name
