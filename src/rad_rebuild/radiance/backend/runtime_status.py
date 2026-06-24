from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from rad_rebuild.radiance.config import (
    DEFAULT_DOCKER_IMAGE,
    PUBLIC_LIVE_SUPPORTED_MODES,
    PUBLIC_LIVE_UNSUPPORTED_MODE_MESSAGE,
    MODE_COMPETITOR,
    MODE_HPS,
    RADIANCE_MODE_LABELS,
    canonicalize_radiance_mode,
    is_production_deployment,
)
from rad_rebuild.radiance.settings import load_settings

from . import runner


REQUIRED_PUBLIC_LOCAL_RADIANCE_EXECUTABLES = ("oconv", "rtrace", "rcontrib")
FORCE_LOCAL_UNAVAILABLE_ENV = "RAD_REBUILD_FORCE_LOCAL_RADIANCE_UNAVAILABLE"
FORCE_DOCKER_UNAVAILABLE_ENV = "RAD_REBUILD_FORCE_DOCKER_UNAVAILABLE"
DISABLE_RADIANCE_AUTODETECT_ENV = "RAD_REBUILD_DISABLE_RADIANCE_AUTODETECT"
ENABLE_PRIVATE_LIVE_MODES_ENV = "RAD_REBUILD_ENABLE_PRIVATE_LIVE_MODES"
PRIVATE_CONVENTIONAL_IES_ENV = "RAD_REBUILD_PRIVATE_CONVENTIONAL_IES"
PRIVATE_HPS_IES_ENV = "RAD_REBUILD_PRIVATE_HPS_IES"
PRIVATE_LIVE_MODES = (MODE_COMPETITOR, MODE_HPS)


def _source_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


def _private_ies_requirements(env: Mapping[str, str]) -> dict[str, Path | None]:
    conventional_raw = env.get(PRIVATE_CONVENTIONAL_IES_ENV, "").strip()
    hps_raw = env.get(PRIVATE_HPS_IES_ENV, "").strip()
    return {
        MODE_COMPETITOR: Path(conventional_raw).expanduser() if conventional_raw else None,
        MODE_HPS: Path(hps_raw).expanduser() if hps_raw else None,
    }


def private_photometry_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = _source_env(env)
    requested = _truthy(source, ENABLE_PRIVATE_LIVE_MODES_ENV)

    if is_production_deployment(source):
        return {
            "enabled": False,
            "available": False,
            "reason": "production_precomputed_only",
            "supported_private_modes": [],
            "missing_env_vars": [],
            "missing_files": [],
        }

    if not requested:
        return {
            "enabled": False,
            "available": False,
            "reason": "not_enabled",
            "supported_private_modes": [],
            "missing_env_vars": [],
            "missing_files": [],
        }

    requirements = _private_ies_requirements(source)
    missing_env_vars = [
        PRIVATE_CONVENTIONAL_IES_ENV
        if requirements[MODE_COMPETITOR] is None
        else "",
        PRIVATE_HPS_IES_ENV if requirements[MODE_HPS] is None else "",
    ]
    missing_env_vars = [name for name in missing_env_vars if name]

    missing_files = [
        mode
        for mode, candidate in requirements.items()
        if candidate is not None and not candidate.is_file()
    ]

    if missing_env_vars or missing_files:
        return {
            "enabled": True,
            "available": False,
            "reason": "private_photometry_assets_missing",
            "supported_private_modes": [],
            "missing_env_vars": missing_env_vars,
            "missing_files": missing_files,
        }

    return {
        "enabled": True,
        "available": True,
        "reason": None,
        "supported_private_modes": list(PRIVATE_LIVE_MODES),
        "missing_env_vars": [],
        "missing_files": [],
    }


def live_supported_modes(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    source = _source_env(env)
    if is_production_deployment(source):
        return ()
    modes = list(PUBLIC_LIVE_SUPPORTED_MODES)
    if private_photometry_status(source)["available"]:
        modes.extend(PRIVATE_LIVE_MODES)
    return tuple(modes)


def live_unsupported_mode_message() -> str:
    return PUBLIC_LIVE_UNSUPPORTED_MODE_MESSAGE


def live_mode_supported(mode: str | None, env: Mapping[str, str] | None = None) -> bool:
    return canonicalize_radiance_mode(mode) in live_supported_modes(env)


def unsupported_live_mode_detail(mode: str | None, env: Mapping[str, str] | None = None) -> dict[str, object]:
    canonical = canonicalize_radiance_mode(mode)
    private_status = private_photometry_status(env)
    detail: dict[str, object] = {
        "error": "live_mode_unsupported",
        "message": PUBLIC_LIVE_UNSUPPORTED_MODE_MESSAGE,
        "mode": canonical,
        "mode_label": RADIANCE_MODE_LABELS.get(canonical, canonical),
        "supported_lighting_modes": list(live_supported_modes(env)),
        "precomputed_available": True,
    }
    if canonical in PRIVATE_LIVE_MODES and private_status["enabled"] and not private_status["available"]:
        detail["private_live_mode_reason"] = private_status["reason"]
        detail["missing_env_vars"] = private_status["missing_env_vars"]
        detail["missing_private_modes"] = private_status["missing_files"]
    return detail


def _truthy(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _env_path_values(value: str | None) -> list[Path]:
    if not value:
        return []
    return [Path(part).expanduser() for part in value.split(os.pathsep) if part.strip()]


def _executable_at(directory: Path, name: str) -> Path | None:
    candidate = directory / name
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except FileNotFoundError:
        return None
    if resolved.is_file() and os.access(resolved, os.X_OK):
        return resolved
    return None


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for path in paths:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        result.append(path)
    return result


def _local_bin_candidates(env: Mapping[str, str]) -> list[Path]:
    disabled = _truthy(env, DISABLE_RADIANCE_AUTODETECT_ENV)
    candidates: list[Path] = []
    if env.get("RADIANCE_BIN_DIR", "").strip():
        candidates.append(Path(env["RADIANCE_BIN_DIR"]).expanduser())
    if env.get("RADIANCE_HOME", "").strip():
        candidates.append(Path(env["RADIANCE_HOME"]).expanduser() / "bin")
    if not disabled:
        opt_bin = Path("/opt/radiance/bin")
        if opt_bin.is_dir():
            candidates.append(opt_bin)
        candidates.extend(_env_path_values(env.get("PATH")))
    return _dedupe_paths(candidates)


def _local_lib_candidates(env: Mapping[str, str]) -> list[Path]:
    disabled = _truthy(env, DISABLE_RADIANCE_AUTODETECT_ENV)
    candidates: list[Path] = []
    if env.get("RADIANCE_LIB_DIR", "").strip():
        candidates.append(Path(env["RADIANCE_LIB_DIR"]).expanduser())
    if env.get("RADIANCE_HOME", "").strip():
        candidates.append(Path(env["RADIANCE_HOME"]).expanduser() / "lib")
    if not disabled:
        opt_lib = Path("/opt/radiance/lib")
        if opt_lib.is_dir():
            candidates.append(opt_lib)
        candidates.extend(_env_path_values(env.get("RAYPATH")))
    return _dedupe_paths(candidates)


def _detect_public_local_executables(env: Mapping[str, str]) -> dict[str, str]:
    detected: dict[str, str] = {}
    for executable in REQUIRED_PUBLIC_LOCAL_RADIANCE_EXECUTABLES:
        for directory in _local_bin_candidates(env):
            path = _executable_at(directory, executable)
            if path is not None:
                detected[executable] = str(path)
                break
    return detected


def _radiance_home_from_detection(env: Mapping[str, str], detected: Mapping[str, str]) -> str | None:
    explicit = env.get("RADIANCE_HOME", "").strip()
    if explicit:
        return str(Path(explicit).expanduser())
    for path_text in detected.values():
        path = Path(path_text)
        if path.parent.name == "bin":
            return str(path.parent.parent)
    return None


def _first_existing_lib(env: Mapping[str, str]) -> str | None:
    for candidate in _local_lib_candidates(env):
        if candidate.is_dir():
            return str(candidate)
    return str(_local_lib_candidates(env)[0]) if _local_lib_candidates(env) else None


def local_radiance_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    supported = list(live_supported_modes(source))
    detected = _detect_public_local_executables(source)
    missing = [
        name
        for name in REQUIRED_PUBLIC_LOCAL_RADIANCE_EXECUTABLES
        if name not in detected
    ]
    forced = _truthy(source, FORCE_LOCAL_UNAVAILABLE_ENV)
    reason = None
    if forced:
        reason = "forced_unavailable_for_testing"
    elif missing:
        reason = "missing_executables"
    radiance_home = _radiance_home_from_detection(source, detected)
    bin_dir = source.get("RADIANCE_BIN_DIR", "").strip() or (
        str(Path(next(iter(detected.values()))).parent) if detected else None
    )
    lib_dir = source.get("RADIANCE_LIB_DIR", "").strip() or _first_existing_lib(source)
    return {
        "available": not forced and not missing,
        "reason": reason,
        "supported_lighting_modes": supported,
        "radiance_home": radiance_home,
        "radiance_bin_dir": bin_dir,
        "radiance_lib_dir": lib_dir,
        "detected_executables": detected,
        "missing_executables": missing,
        "env_values": {
            "RADIANCE_HOME": source.get("RADIANCE_HOME") or None,
            "RADIANCE_BIN_DIR": source.get("RADIANCE_BIN_DIR") or None,
            "RADIANCE_LIB_DIR": source.get("RADIANCE_LIB_DIR") or None,
            "RAD_REBUILD_DISABLE_RADIANCE_AUTODETECT": source.get(DISABLE_RADIANCE_AUTODETECT_ENV) or None,
            "RAD_REBUILD_FORCE_LOCAL_RADIANCE_UNAVAILABLE": source.get(FORCE_LOCAL_UNAVAILABLE_ENV) or None,
        },
        "setup_commands": _local_setup_commands(),
    }


def _local_setup_commands() -> list[dict[str, str]]:
    return [
        {
            "label": "Use Radiance from PATH",
            "command": "\n".join(
                [
                    'export RADIANCE_BIN_DIR="$(dirname "$(command -v oconv)")"',
                    'export RADIANCE_HOME="$(dirname "$RADIANCE_BIN_DIR")"',
                    'export RADIANCE_LIB_DIR="$RADIANCE_HOME/lib"',
                    "python -m rad_rebuild.dev --live",
                ]
            ),
        },
        {
            "label": "Use an explicit install path",
            "command": "\n".join(
                [
                    "export RADIANCE_HOME=/opt/radiance",
                    'export RADIANCE_BIN_DIR="$RADIANCE_HOME/bin"',
                    'export RADIANCE_LIB_DIR="$RADIANCE_HOME/lib"',
                    "python -m rad_rebuild.dev --live",
                ]
            ),
        },
    ]


def _docker_setup_commands(reason: str | None) -> list[dict[str, str]]:
    commands = [
        {
            "label": "Clear Docker host override",
            "command": "unset DOCKER_HOST\npython -m rad_rebuild.dev --live",
        }
    ]
    if reason == "docker_cli_missing":
        commands.extend(
            [
                {
                    "label": "Linux Docker Engine",
                    "command": "Install Docker Engine, then restart this dev server with:\npython -m rad_rebuild.dev --live",
                },
                {
                    "label": "macOS Docker Desktop",
                    "command": "Install and start Docker Desktop, then restart this dev server with:\npython -m rad_rebuild.dev --live",
                },
            ]
        )
    return commands


def _classify_docker_error(message: str) -> str:
    text = message.lower()
    if "permission denied" in text or "got permission denied" in text:
        return "docker_permission_denied"
    return "docker_daemon_unreachable"


def docker_runtime_status(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    settings = load_settings(source)
    supported = list(live_supported_modes(source))
    reason: str | None = None
    docker_cli: str | None = None
    daemon_available = False
    image_available = False
    can_build_image = (settings.paths.docker_root / "Dockerfile").is_file()

    if _truthy(source, FORCE_DOCKER_UNAVAILABLE_ENV):
        reason = "forced_unavailable_for_testing"
    elif not settings.use_docker:
        reason = "docker_disabled"
    else:
        docker_cli = runner._resolve_docker()
        if not docker_cli:
            reason = "docker_cli_missing"
        else:
            try:
                runner._run_docker([docker_cli, "version", "--format", "{{.Server.Version}}"], timeout=4)
                daemon_available = True
            except Exception as exc:
                reason = _classify_docker_error(str(exc))
            if daemon_available:
                try:
                    runner._run_docker([docker_cli, "image", "inspect", settings.docker_image or DEFAULT_DOCKER_IMAGE], timeout=4)
                    image_available = True
                except Exception:
                    image_available = False

    available = reason is None and daemon_available
    return {
        "available": available,
        "reason": reason,
        "supported_lighting_modes": supported,
        "docker_cli": docker_cli,
        "daemon_available": daemon_available,
        "image_available": image_available,
        "can_build_image": can_build_image,
        "setup_commands": _docker_setup_commands(reason),
    }


def runtime_status_payload(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    source = dict(os.environ if env is None else env)
    settings = load_settings(source)
    if is_production_deployment(source):
        return {
            "live_execution_enabled": False,
            "live_supported_modes": [],
            "live_unsupported_mode_message": "Hosted production is precomputed-only.",
            "private_photometry": private_photometry_status(source),
            "modes": {
                "precomputed": {
                    "available": True,
                },
                "live_docker": {
                    "available": False,
                    "reason": "production_precomputed_only",
                    "supported_lighting_modes": [],
                    "docker_cli": None,
                    "daemon_available": False,
                    "image_available": False,
                    "can_build_image": False,
                    "setup_commands": [],
                },
                "live_local": {
                    "available": False,
                    "reason": "production_precomputed_only",
                    "supported_lighting_modes": [],
                    "radiance_home": None,
                    "radiance_bin_dir": None,
                    "radiance_lib_dir": None,
                    "detected_executables": {},
                    "missing_executables": [],
                    "env_values": {},
                    "setup_commands": [],
                },
            },
        }
    return {
        "live_execution_enabled": settings.live_execution_enabled,
        "live_supported_modes": list(live_supported_modes(source)),
        "live_unsupported_mode_message": PUBLIC_LIVE_UNSUPPORTED_MODE_MESSAGE,
        "private_photometry": private_photometry_status(source),
        "modes": {
            "precomputed": {
                "available": True,
            },
            "live_docker": docker_runtime_status(source),
            "live_local": local_radiance_status(source),
        },
    }
