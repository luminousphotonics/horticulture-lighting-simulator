from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

settings.register_profile(
    "phase03",
    deadline=None,
    derandomize=True,
    max_examples=25,
    suppress_health_check=(HealthCheck.function_scoped_fixture,),
)
settings.load_profile(os.getenv("HYPOTHESIS_PROFILE", "phase03"))


@pytest.fixture
def clean_radiance_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    workspace = tmp_path / "workspace"
    runtime = workspace / "runtime_state"
    basis = workspace / "basis"
    cache = workspace / "cache"
    visualizations = workspace / "visualizations"
    for path in (runtime, basis, cache, visualizations):
        path.mkdir(parents=True)
    monkeypatch.setenv("RADIANCE_OUTPUT_ROOT", str(workspace))
    monkeypatch.setenv("RADIANCE_RUNTIME_STATE_ROOT", str(runtime))
    monkeypatch.setenv("RADIANCE_BASIS_OUTPUT_ROOT", str(basis))
    monkeypatch.setenv("RADIANCE_CACHE_ROOT", str(cache))
    monkeypatch.setenv("RADIANCE_VISUALIZATION_OUTPUT_ROOT", str(visualizations))
    yield workspace
