from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
from pathlib import Path

from rad_rebuild.radiance.paths import RADIANCE_DOCKER_ROOT, REPO_ROOT
from rad_rebuild.radiance.settings import get_settings, load_settings

from .env import _docker_path_prefix


_SETTINGS = get_settings()
IMAGE_NAME = _SETTINGS.docker_image
CONTAINER_REPO_ROOT = "/workspace"
CONTAINER_ROOT = f"{CONTAINER_REPO_ROOT}/outputs/radiance"
CONTAINER_DATA_ROOT = f"{CONTAINER_REPO_ROOT}/data/radiance"
CONTAINER_PY = "/opt/venv/bin/python"


def _run_cmd(
    cmd: list[str],
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    try:
        out = subprocess.check_output(
            cmd,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=cwd,
            env=env,
            timeout=timeout,
        )
        return out.strip()
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Command timed out after {timeout}s: {' '.join(cmd)}") from e
    except subprocess.CalledProcessError as e:
        detail = e.output.strip() if e.output else str(e)
        raise RuntimeError(detail) from e


def _resolve_docker() -> str | None:
    env_bin = load_settings().docker_bin
    if env_bin and Path(env_bin).exists():
        return env_bin
    docker_bin = shutil.which("docker")
    if docker_bin:
        return docker_bin
    if os.name == "nt":
        program_files = os.getenv("ProgramFiles", r"C:\Program Files")
        program_files_x86 = os.getenv("ProgramFiles(x86)", r"C:\Program Files (x86)")
        candidates = [
            Path(program_files) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
            Path(program_files_x86) / "Docker" / "Docker" / "resources" / "bin" / "docker.exe",
            Path(r"C:\ProgramData\DockerDesktop\version-bin") / "docker.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
    for candidate_str in (
        "/opt/homebrew/bin/docker",
        "/usr/local/bin/docker",
        "/Applications/Docker.app/Contents/Resources/bin/docker",
    ):
        if Path(candidate_str).exists():
            return candidate_str
    return None


def _docker_platform() -> str | None:
    settings = load_settings()
    if settings.docker_platform:
        return settings.docker_platform
    machine = platform.machine().lower()
    if machine in {"arm64", "aarch64"}:
        return "linux/amd64"
    return None


def _docker_platform_args() -> list[str]:
    platform_value = _docker_platform()
    if platform_value:
        return ["--platform", platform_value]
    return []


def _docker_user_args() -> list[str]:
    settings = load_settings()
    if settings.docker_user:
        return ["--user", settings.docker_user]
    if os.name != "nt" and hasattr(os, "getuid") and hasattr(os, "getgid"):
        return ["--user", f"{os.getuid()}:{os.getgid()}"]
    return []


def _use_docker() -> bool:
    return load_settings().use_docker


def _docker_cli_env() -> dict[str, str]:
    env = os.environ.copy()
    env.setdefault("PATH", "")
    prefix = _docker_path_prefix()
    if prefix:
        env["PATH"] = f"{prefix}:{env['PATH']}" if env["PATH"] else prefix
    return env


def _run_docker(cmd: list[str], cwd: Path | None = None, timeout: float | None = None) -> str:
    return _run_cmd(cmd, cwd=cwd, env=_docker_cli_env(), timeout=timeout)


def _docker_volume_path(path: Path) -> str:
    if os.name == "nt":
        return str(path.resolve()).replace("\\", "/")
    return str(path)


def _containerize_text(value: str) -> str:
    if not value:
        return value
    repo_root_str = str(REPO_ROOT)
    if not repo_root_str:
        return value
    if repo_root_str:
        value = value.replace(repo_root_str, CONTAINER_REPO_ROOT)
    return value


def _docker_env_args(env: dict[str, str]) -> list[str]:
    args: list[str] = []
    env = {
        **env,
        "PYTHONPATH": f"{CONTAINER_REPO_ROOT}/src",
        "RADIANCE_DATA_ROOT": CONTAINER_DATA_ROOT,
        "RADIANCE_CURVE_DATA_ROOT": f"{CONTAINER_DATA_ROOT}/curve_data",
        "RADIANCE_IES_ROOT": f"{CONTAINER_DATA_ROOT}/ies_sources",
        "RADIANCE_OUTPUT_ROOT": CONTAINER_ROOT,
        "RADIANCE_RUNTIME_STATE_ROOT": f"{CONTAINER_ROOT}/runtime_state",
        "RADIANCE_BASIS_OUTPUT_ROOT": f"{CONTAINER_ROOT}/basis",
        "RADIANCE_VISUALIZATION_OUTPUT_ROOT": f"{CONTAINER_ROOT}/visualizations",
        "RADIANCE_CACHE_ROOT": f"{CONTAINER_ROOT}/cache",
    }
    for key, value in env.items():
        if key in {"PATH", "PY"}:
            continue
        args.extend(["-e", f"{key}={_containerize_text(str(value))}"])
    args.extend(["-e", f"PY={CONTAINER_PY}"])
    return args


def _docker_shell_cmd(shell_cmd: str) -> str:
    shell_cmd = _containerize_text(shell_cmd)
    if os.name != "nt":
        return shell_cmd
    workdir = load_settings().docker_workdir
    excludes = [
        ".git",
        ".venv",
        "venv",
        "__pycache__",
        ".radcache",
        "artifacts",
        "basis_runs",
        "ppfd_visualizations*",
        "*.oct",
    ]
    include_patterns = [
        "ppfd_map.txt",
        "ppfd_map.csv",
        "ppfd_visualizations*/",
        "ppfd_visualizations*/**",
        "ies_sources/",
        "ies_sources/**",
        "runtime_state/",
        "runtime_state/**",
        "room.rad",
        "sensor_points.txt",
        "ring_powers_optimized.json",
        "basis_A.npy",
        "basis_manifest.json",
        "basis_runs/",
        "basis_runs/**",
        "artifacts/",
        "artifacts/**",
    ]
    exclude_args = " ".join(f"--exclude={shlex.quote(pat)}" for pat in excludes)
    include_args = " ".join(f"--include={shlex.quote(pat)}" for pat in include_patterns)
    workdir_q = shlex.quote(workdir)
    return (
        "set -euo pipefail;"
        f"WORKDIR={workdir_q};"
        'rm -rf "$WORKDIR";'
        'mkdir -p "$WORKDIR";'
        f"rsync -a --delete {exclude_args} {shlex.quote(CONTAINER_ROOT)}/ \"$WORKDIR\"/;"
        'cd "$WORKDIR";'
        "set +e;"
        f"{shell_cmd};"
        "STATUS=$?;"
        "set -e;"
        f"rsync -a --delete {include_args} --exclude='*' \"$WORKDIR\"/ {shlex.quote(CONTAINER_ROOT)}/ || true;"
        "exit $STATUS"
    )


def _docker_command(shell_cmd: str, env: dict[str, str]) -> list[str]:
    docker_bin = _resolve_docker()
    if not docker_bin:
        raise RuntimeError("Docker CLI not found. Install Docker Desktop.")
    shell_cmd = _docker_shell_cmd(shell_cmd)
    volume = f"{_docker_volume_path(REPO_ROOT)}:{CONTAINER_REPO_ROOT}"
    return [
        docker_bin,
        "run",
        "--rm",
        *_docker_platform_args(),
        *_docker_user_args(),
        "-v",
        volume,
        "-w",
        CONTAINER_ROOT,
        *_docker_env_args(env),
        IMAGE_NAME,
        "bash",
        "-lc",
        shell_cmd,
    ]


def _local_command(shell_cmd: str) -> list[str]:
    if os.name == "nt":
        raise RuntimeError("Local radiance execution is not supported on Windows.")
    return ["bash", "-c", shell_cmd]


def _shell_quote_command(cmd: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in cmd)


def _load_image_from_tar() -> bool:
    candidates = []
    image_tar = load_settings().docker_image_tar
    if image_tar:
        candidates.append(Path(image_tar))
    candidates.append(REPO_ROOT / "launcher" / "resources" / "radiance-image.tar")
    docker_bin = _resolve_docker()
    if not docker_bin:
        return False
    for path in candidates:
        if path.exists():
            _run_docker([docker_bin, "load", "-i", str(path)])
            return True
    return False


def _image_runtime_ready(docker_bin: str) -> bool:
    try:
        _run_docker(
            [
                docker_bin,
                "run",
                "--rm",
                *_docker_platform_args(),
                IMAGE_NAME,
                CONTAINER_PY,
                "-c",
                "import pydantic, numpy, scipy, matplotlib, plotly",
            ],
            timeout=20,
        )
        return True
    except Exception:
        return False


def ensure_image() -> None:
    docker_bin = _resolve_docker()
    if not docker_bin:
        raise RuntimeError("Docker CLI not found. Install Docker Desktop.")
    try:
        _run_docker([docker_bin, "image", "inspect", IMAGE_NAME])
        if _image_runtime_ready(docker_bin):
            return
    except Exception:
        pass
    try:
        if _load_image_from_tar() and _image_runtime_ready(docker_bin):
            _run_docker([docker_bin, "image", "inspect", IMAGE_NAME])
            return
    except Exception:
        pass
    _run_docker(
        [
            docker_bin,
            "build",
            *_docker_platform_args(),
            "-f",
            str(RADIANCE_DOCKER_ROOT / "Dockerfile"),
            "-t",
            IMAGE_NAME,
            str(REPO_ROOT),
        ],
        cwd=REPO_ROOT,
    )


def reproduce_command(artifacts: Path) -> list[str]:
    docker_bin = _resolve_docker()
    if not docker_bin:
        raise RuntimeError("Docker CLI not found.")
    return [
        docker_bin,
        "run",
        "--rm",
        *_docker_platform_args(),
        *_docker_user_args(),
        "-v",
        f"{artifacts}:/out",
        IMAGE_NAME,
        "/workspace/reproduce.sh",
    ]


def docker_status_payload(precomputed_mode: str, precomputed_root: Path) -> dict[str, object]:
    if precomputed_mode == "only":
        return {
            "available": True,
            "detail": f"Precomputed playback enabled from {precomputed_root}. Docker is not required.",
        }
    if precomputed_mode == "prefer":
        return {
            "available": True,
            "detail": (
                f"Precomputed playback available from {precomputed_root}. "
                "Cached layouts work without Docker; cache misses require Docker."
            ),
        }
    if not _use_docker():
        return {"available": True, "detail": "Docker disabled (RADIANCE_USE_DOCKER=0). Using local Radiance."}
    docker_bin = _resolve_docker()
    if not docker_bin:
        return {"available": False, "detail": "Docker CLI not found in PATH."}
    context = ""
    client_version = ""
    try:
        context = _run_docker([docker_bin, "context", "show"], timeout=2)
    except Exception:
        context = ""
    try:
        client_version = _run_docker([docker_bin, "version", "--format", "{{.Client.Version}}"], timeout=2)
    except Exception as e:
        detail = str(e)
        if context:
            detail = f"{detail} (context {context})"
        return {"available": False, "detail": detail}

    try:
        out = _run_docker([docker_bin, "version", "--format", "{{.Server.Version}}"], timeout=4)
        detail = f"Docker daemon {out}"
        if client_version:
            detail = f"{detail} (client {client_version})"
        if context:
            detail = f"{detail} (context {context})"
        return {"available": True, "detail": detail}
    except Exception as e:
        detail = f"Docker daemon not responding: {e}"
        if client_version:
            detail = f"{detail} (client {client_version})"
        if context:
            detail = f"{detail} (context {context})"
        return {"available": False, "detail": detail}
