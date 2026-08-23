from __future__ import annotations

from pathlib import Path

import pytest

from fspm_optics.radiance.versioning import (
    RadianceDiscoveryError,
    discover_radiance_installation,
)


def make_fake_executable(path: Path) -> Path:
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return path


def test_missing_radiance_executable_error_is_clear(tmp_path: Path) -> None:
    with pytest.raises(
        RadianceDiscoveryError,
        match=r"Required Radiance executable 'oconv' is unavailable",
    ):
        discover_radiance_installation(env={"PATH": str(tmp_path)})


def test_version_metadata_is_captured_from_injected_fake_probe(tmp_path: Path) -> None:
    oconv = make_fake_executable(tmp_path / "oconv")
    rtrace = make_fake_executable(tmp_path / "rtrace")
    versions = {
        oconv.resolve(): "oconv fake 1.0",
        rtrace.resolve(): "rtrace fake 1.0",
    }
    installation = discover_radiance_installation(
        env={"PATH": str(tmp_path)},
        version_probe=lambda path: versions[path],
    )
    assert installation.oconv.path == oconv.resolve()
    assert installation.rtrace.path == rtrace.resolve()
    assert installation.oconv.version_text == "oconv fake 1.0"
    assert installation.rtrace.version_text == "rtrace fake 1.0"
    assert installation.to_dict()["oconv"]["path"] == str(oconv.resolve())


def test_unavailable_version_text_does_not_hide_resolved_executables(
    tmp_path: Path,
) -> None:
    make_fake_executable(tmp_path / "oconv")
    make_fake_executable(tmp_path / "rtrace")
    installation = discover_radiance_installation(
        env={"PATH": str(tmp_path)},
        version_probe=lambda _path: None,
    )
    assert installation.oconv.version_text is None
    assert installation.rtrace.version_text is None
