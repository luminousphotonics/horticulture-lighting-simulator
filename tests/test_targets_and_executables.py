from __future__ import annotations

import os

import pytest

from fspm_optics.fspm.targets import (
    fspm_target_metadata,
    resolve_fspm_target_ppfd,
    resolve_fspm_target_tolerance,
)
from fspm_optics.radiance.executables import ExecutableResolutionError, resolve_executable


def test_target_defaults_and_thresholds() -> None:
    target = resolve_fspm_target_ppfd(None)
    metadata = fspm_target_metadata(
        target_ppfd_umol_m2_s=target,
        tolerance_umol_m2_s=20.0,
    )
    assert metadata["target_lower_threshold_umol_m2_s"] == pytest.approx(target - 20.0)
    assert metadata["target_upper_threshold_umol_m2_s"] == pytest.approx(target + 20.0)
    assert resolve_fspm_target_tolerance(None) == 75.0


def test_executable_resolution_only_resolves_and_validates(tmp_path) -> None:
    executable = tmp_path / "solver-tool"
    executable.write_text("placeholder\n", encoding="utf-8")
    executable.chmod(0o700)
    assert resolve_executable("solver-tool", env={"PATH": str(tmp_path)}) == executable.resolve()
    assert resolve_executable("./solver-tool", cwd=tmp_path, env={"PATH": ""}) == executable.resolve()


def test_executable_resolution_rejects_missing_or_non_executable(tmp_path) -> None:
    with pytest.raises(ExecutableResolutionError, match="not found"):
        resolve_executable("missing", env={"PATH": str(tmp_path)})
    file = tmp_path / "plain"
    file.write_text("placeholder\n", encoding="utf-8")
    file.chmod(0o600)
    if os.name != "nt":
        with pytest.raises(ExecutableResolutionError, match="not executable"):
            resolve_executable(file)
