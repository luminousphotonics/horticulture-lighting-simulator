"""Local Radiance executable discovery and best-effort version metadata."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
from typing import Callable, Mapping

from .executables import ExecutableResolutionError, resolve_executable

VersionProbe = Callable[[Path], str | None]


class RadianceDiscoveryError(RuntimeError):
    """A required local Radiance executable could not be resolved."""


@dataclass(frozen=True, slots=True)
class RadianceExecutableVersion:
    name: str
    path: Path
    version_text: str | None

    def to_dict(self) -> dict[str, str | None]:
        return {
            "name": self.name,
            "path": str(self.path),
            "version_text": self.version_text,
        }


@dataclass(frozen=True, slots=True)
class RadianceInstallation:
    oconv: RadianceExecutableVersion
    rtrace: RadianceExecutableVersion

    def to_dict(self) -> dict[str, dict[str, str | None]]:
        return {
            "oconv": self.oconv.to_dict(),
            "rtrace": self.rtrace.to_dict(),
        }


def discover_radiance_installation(
    *,
    oconv_command: str | os.PathLike[str] = "oconv",
    rtrace_command: str | os.PathLike[str] = "rtrace",
    env: Mapping[str, str] | None = None,
    cwd: Path | None = None,
    version_probe: VersionProbe | None = None,
) -> RadianceInstallation:
    """Resolve required tools and capture version text when available."""

    probe = version_probe or probe_radiance_version
    oconv = _discover_one(
        "oconv", oconv_command, env=env, cwd=cwd, version_probe=probe
    )
    rtrace = _discover_one(
        "rtrace", rtrace_command, env=env, cwd=cwd, version_probe=probe
    )
    return RadianceInstallation(oconv=oconv, rtrace=rtrace)


def probe_radiance_version(executable: Path) -> str | None:
    """Run a bounded version probe; absence of version output is non-fatal."""

    try:
        completed = subprocess.run(
            (str(executable), "-version"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    output = "\n".join(
        part.strip() for part in (completed.stdout, completed.stderr) if part.strip()
    )
    return output or None


def _discover_one(
    name: str,
    command: str | os.PathLike[str],
    *,
    env: Mapping[str, str] | None,
    cwd: Path | None,
    version_probe: VersionProbe,
) -> RadianceExecutableVersion:
    try:
        path = resolve_executable(command, env=env, cwd=cwd, label=name)
    except ExecutableResolutionError as exc:
        raise RadianceDiscoveryError(
            f"Required Radiance executable {name!r} is unavailable: {exc}"
        ) from exc
    try:
        version_text = version_probe(path)
    except Exception:
        version_text = None
    return RadianceExecutableVersion(
        name=name,
        path=path,
        version_text=version_text,
    )
