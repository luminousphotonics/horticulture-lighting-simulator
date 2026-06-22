from __future__ import annotations

import stat
import sys
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend import runtime as backend_runtime  # noqa: E402
from rad_rebuild.radiance.backend import runtime_status  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.settings import load_settings  # noqa: E402
from rad_rebuild.radiance.config import (  # noqa: E402
    EXECUTION_MODE_LIVE_DOCKER,
    EXECUTION_MODE_LIVE_LOCAL,
    EXECUTION_MODE_PRECOMPUTED,
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
)
from rad_rebuild.radiance.domain import PrecomputedMode  # noqa: E402


def _write_executable(path: Path) -> None:
    path.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(0)\n", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def test_runtime_status_reports_precomputed_and_proposed_only_live_modes() -> None:
    env = {
        "RADIANCE_ENABLE_LIVE_EXECUTION": "1",
        "PATH": "",
        "RAD_REBUILD_DISABLE_RADIANCE_AUTODETECT": "1",
    }
    with patch.object(runtime_status.runner, "_resolve_docker", return_value=None):
        payload = runtime_status.runtime_status_payload(env)

    assert payload["live_execution_enabled"] is True
    assert payload["live_supported_modes"] == ["SMD"]
    assert payload["modes"]["precomputed"]["available"] is True
    assert "Proposed LED System" in payload["live_unsupported_mode_message"]
    assert runtime_status.live_mode_supported("SMD") is True
    assert runtime_status.live_mode_supported("Competitor") is False
    assert runtime_status.live_mode_supported("1000W HPS") is False


def test_production_runtime_status_is_precomputed_only_without_autodetect() -> None:
    env = {
        "RAD_REBUILD_DEPLOYMENT_MODE": "production",
        "RADIANCE_ENABLE_LIVE_EXECUTION": "1",
        "RADIANCE_USE_DOCKER": "1",
        "RADIANCE_BIN_DIR": "/tmp/not-radiance/bin",
        "RADIANCE_IES_ROOT": "/tmp/private-ies-should-not-be-needed",
        "PATH": "",
    }
    with patch.object(runtime_status.runner, "_resolve_docker") as resolve_docker:
        payload = runtime_status.runtime_status_payload(env)

    resolve_docker.assert_not_called()
    assert payload["live_execution_enabled"] is False
    assert payload["live_supported_modes"] == []
    assert payload["modes"]["precomputed"]["available"] is True
    assert payload["modes"]["live_docker"]["reason"] == "production_precomputed_only"
    assert payload["modes"]["live_local"]["reason"] == "production_precomputed_only"
    assert payload["modes"]["live_local"]["detected_executables"] == {}


def test_production_settings_ignore_accidental_live_environment() -> None:
    settings = load_settings(
        {
            "RAD_REBUILD_DEPLOYMENT_MODE": "production",
            "RADIANCE_ENABLE_LIVE_EXECUTION": "1",
            "RADIANCE_USE_DOCKER": "1",
            "RADIANCE_PRECOMPUTED_MODE": "prefer",
        }
    )

    assert settings.live_execution_enabled is False
    assert settings.use_docker is False
    assert settings.precomputed_mode == PrecomputedMode.ONLY


def test_production_live_run_request_is_rejected_even_when_env_enables_live() -> None:
    prod_settings = load_settings(
        {
            "RAD_REBUILD_DEPLOYMENT_MODE": "production",
            "RADIANCE_ENABLE_LIVE_EXECUTION": "1",
        }
    )
    req = RadianceRunRequest(
        action="all",
        mode=MODE_SMD,
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
    )

    with (
        patch.object(backend_runtime, "get_settings", return_value=prod_settings),
        pytest.raises(HTTPException) as raised,
    ):
        backend_runtime.assert_live_execution_allowed(req, "session")

    assert raised.value.status_code == 403
    detail = cast(dict[str, object], raised.value.detail)
    assert detail["error"] == "live_execution_disabled"


def test_local_radiance_status_accepts_proposed_tools_without_ies2rad(tmp_path: Path) -> None:
    bin_dir = tmp_path / "radiance" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("oconv", "rtrace", "rcontrib"):
        _write_executable(bin_dir / name)

    payload = runtime_status.local_radiance_status(
        {
            "RADIANCE_BIN_DIR": str(bin_dir),
            "RAD_REBUILD_DISABLE_RADIANCE_AUTODETECT": "1",
            "PATH": "",
        }
    )

    assert payload["available"] is True
    assert payload["reason"] is None
    assert payload["missing_executables"] == []
    assert set(payload["detected_executables"]) == {"oconv", "rtrace", "rcontrib"}
    assert "ies2rad" not in payload["detected_executables"]


def test_force_local_radiance_unavailable_reports_testing_reason(tmp_path: Path) -> None:
    bin_dir = tmp_path / "radiance" / "bin"
    bin_dir.mkdir(parents=True)
    for name in ("oconv", "rtrace", "rcontrib"):
        _write_executable(bin_dir / name)

    payload = runtime_status.local_radiance_status(
        {
            "RADIANCE_BIN_DIR": str(bin_dir),
            "RAD_REBUILD_FORCE_LOCAL_RADIANCE_UNAVAILABLE": "1",
            "RAD_REBUILD_DISABLE_RADIANCE_AUTODETECT": "1",
        }
    )

    assert payload["available"] is False
    assert payload["reason"] == "forced_unavailable_for_testing"


def test_bad_explicit_radiance_paths_with_autodetect_disabled_report_missing() -> None:
    payload = runtime_status.local_radiance_status(
        {
            "RADIANCE_HOME": "/tmp/not-radiance",
            "RADIANCE_BIN_DIR": "/tmp/not-radiance/bin",
            "RADIANCE_LIB_DIR": "/tmp/not-radiance/lib",
            "RAD_REBUILD_DISABLE_RADIANCE_AUTODETECT": "1",
            "PATH": "",
        }
    )

    assert payload["available"] is False
    assert payload["reason"] == "missing_executables"
    assert payload["missing_executables"] == ["oconv", "rtrace", "rcontrib"]


def test_docker_runtime_status_available_with_mocked_docker() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        del args, kwargs
        calls.append(list(cmd))
        return "ok"

    with (
        patch.object(runtime_status.runner, "_resolve_docker", return_value="/usr/bin/docker"),
        patch.object(runtime_status.runner, "_run_docker", side_effect=fake_run),
    ):
        payload = runtime_status.docker_runtime_status({"RADIANCE_USE_DOCKER": "1"})

    assert payload["available"] is True
    assert payload["reason"] is None
    assert payload["daemon_available"] is True
    assert payload["image_available"] is True
    assert any(call[:3] == ["/usr/bin/docker", "image", "inspect"] for call in calls)


def test_force_docker_unavailable_reports_testing_reason() -> None:
    payload = runtime_status.docker_runtime_status(
        {"RAD_REBUILD_FORCE_DOCKER_UNAVAILABLE": "1"}
    )

    assert payload["available"] is False
    assert payload["reason"] == "forced_unavailable_for_testing"


def test_docker_daemon_failure_reports_daemon_unreachable() -> None:
    with (
        patch.object(runtime_status.runner, "_resolve_docker", return_value="/usr/bin/docker"),
        patch.object(runtime_status.runner, "_run_docker", side_effect=RuntimeError("Cannot connect to Docker daemon")),
    ):
        payload = runtime_status.docker_runtime_status(
            {"DOCKER_HOST": "unix:///tmp/rad-rebuild-missing-docker.sock"}
        )

    assert payload["available"] is False
    assert payload["reason"] == "docker_daemon_unreachable"
    assert payload["setup_commands"]


@pytest.mark.parametrize("mode", [MODE_COMPETITOR, MODE_HPS])
@pytest.mark.parametrize("execution_mode", [EXECUTION_MODE_LIVE_DOCKER, EXECUTION_MODE_LIVE_LOCAL])
def test_unsupported_live_modes_are_blocked_before_subprocess(
    mode: str,
    execution_mode: str,
) -> None:
    req = RadianceRunRequest(
        action="all",
        mode=mode,
        execution_mode=execution_mode,
    )

    with pytest.raises(HTTPException) as raised:
        backend_runtime.assert_live_execution_allowed(req, "session")

    assert raised.value.status_code == 422
    detail = cast(dict[str, object], raised.value.detail)
    assert detail["error"] == "live_mode_unsupported"
    assert detail["precomputed_available"] is True


@pytest.mark.parametrize("mode", [MODE_SMD, MODE_COMPETITOR, MODE_HPS])
def test_precomputed_modes_bypass_live_runtime_gate(mode: str) -> None:
    req = RadianceRunRequest(
        action="all",
        mode=mode,
        execution_mode=EXECUTION_MODE_PRECOMPUTED,
    )

    backend_runtime.assert_live_execution_allowed(req, "session")
