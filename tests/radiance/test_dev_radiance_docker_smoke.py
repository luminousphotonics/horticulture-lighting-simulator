from __future__ import annotations

from pathlib import Path

from scripts.dev import radiance_docker_smoke as smoke


def test_docker_smoke_build_command_uses_runtime_dockerfile() -> None:
    assert smoke._build_command("rad:test", None) == [
        "docker",
        "build",
        "-f",
        "docker/radiance/Dockerfile",
        "-t",
        "rad:test",
        ".",
    ]


def test_docker_smoke_run_command_checks_runtime_without_live_simulation() -> None:
    command = smoke._run_command("rad:test", "linux/amd64")

    assert command[:5] == ["docker", "run", "--rm", "--platform", "linux/amd64"]
    assert command[5:8] == ["rad:test", "/opt/venv/bin/python", "-c"]
    assert "rtrace" in command[-1]
    assert "ies2rad" not in command[-1]
    assert "rad_rebuild.radiance.backend.server" in command[-1]
    assert 'status["live_supported_modes"] != ["SMD"]' in command[-1]
    assert "/workspace/data/radiance/ies_sources/GLH-KARMA-8-HPS1000.IES" not in command[-1]


def test_runtime_dockerfile_does_not_copy_private_ies_sources() -> None:
    dockerfile = Path("docker/radiance/Dockerfile").read_text(encoding="utf-8")

    assert "COPY data/radiance/ies_sources" not in dockerfile
    assert "mkdir -p /workspace/data/radiance/ies_sources" in dockerfile
