#!/usr/bin/env python3
"""Start the hosted simulator services for Render."""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404 - deployment supervisor launches fixed local service commands.
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BACKEND_HOST = "127.0.0.1"
DEFAULT_BACKEND_PORT = "8786"
BACKEND_READY_TIMEOUT_S = 45.0


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _precomputed_root(env: dict[str, str]) -> Path:
    raw = env.get("RADIANCE_PRECOMPUTED_ROOT", "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    render_disk = env.get("RENDER_DISK_PATH", "").strip()
    if render_disk:
        return Path(render_disk).expanduser().resolve() / "precomputed"
    return REPO_ROOT / "data" / "radiance" / "precomputed"


def _precomputed_present(root: Path) -> bool:
    expected = ("smd", "competitor_practical", "hps_karma_4x4")
    return all(any((root / mode).glob("*/manifest.json")) for mode in expected)


def _maybe_download_precomputed(env: dict[str, str]) -> None:
    if not _truthy(env.get("RAD_REBUILD_PRECOMPUTED_AUTO_DOWNLOAD")):
        return
    root = _precomputed_root(env)
    env["RADIANCE_PRECOMPUTED_ROOT"] = str(root)
    if _precomputed_present(root):
        return
    command = [
        sys.executable,
        str(REPO_ROOT / "scripts" / "radiance" / "download_precomputed.py"),
        "--dataset",
        "full",
        "--dest",
        str(root),
        "--force",
    ]
    subprocess.run(command, cwd=REPO_ROOT, env=env, check=True)  # nosec B603


def _build_env() -> dict[str, str]:
    env = dict(os.environ)
    port = env.get("PORT", "").strip() or "5001"
    backend_host = env.get("RADIANCE_HOST", "").strip() or DEFAULT_BACKEND_HOST
    backend_port = env.get("RADIANCE_PORT", "").strip() or DEFAULT_BACKEND_PORT
    backend_base_url = f"http://{backend_host}:{backend_port}"

    env.setdefault("RAD_REBUILD_DEPLOYMENT_MODE", "production")
    env["RAD_REBUILD_SHOW_LIVE_MODES"] = "0"
    env["RADIANCE_ENABLE_LIVE_EXECUTION"] = "0"
    env["RADIANCE_USE_DOCKER"] = "0"
    env["RADIANCE_PRECOMPUTED_MODE"] = "only"
    env["RADIANCE_HOST"] = backend_host
    env["RADIANCE_PORT"] = backend_port
    env["RADIANCE_RESEARCH_BACKEND_HOST"] = backend_host
    env["RADIANCE_RESEARCH_BACKEND_PORT"] = backend_port
    env["RADIANCE_RESEARCH_API_BASE"] = backend_base_url
    env["RADIANCE_RESEARCH_WEB_PORT"] = port
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["PYTHONPATH"] = str(REPO_ROOT / "src")

    render_disk = env.get("RENDER_DISK_PATH", "").strip()
    if render_disk and not env.get("RADIANCE_OUTPUT_ROOT", "").strip():
        output_root = Path(render_disk).expanduser().resolve() / "runtime"
        env["RADIANCE_OUTPUT_ROOT"] = str(output_root)
        env.setdefault("RADIANCE_BASIS_OUTPUT_ROOT", str(output_root / "basis"))
        env.setdefault("RADIANCE_CACHE_ROOT", str(output_root / "cache"))
        env.setdefault("RADIANCE_RUNTIME_STATE_ROOT", str(output_root / "runtime_state"))
        env.setdefault("RADIANCE_VISUALIZATION_OUTPUT_ROOT", str(output_root / "visualizations"))
    if render_disk and not env.get("RADIANCE_PRECOMPUTED_ROOT", "").strip():
        env["RADIANCE_PRECOMPUTED_ROOT"] = str(Path(render_disk).expanduser().resolve() / "precomputed")
    return env


def _start(command: Sequence[str], env: dict[str, str]) -> subprocess.Popen[bytes]:
    return subprocess.Popen(list(command), cwd=REPO_ROOT, env=env)  # nosec B603


def _wait_for_backend(env: dict[str, str], process: subprocess.Popen[bytes]) -> None:
    url = f"{env['RADIANCE_RESEARCH_API_BASE']}/ready"
    deadline = time.monotonic() + BACKEND_READY_TIMEOUT_S
    last_error = "backend did not report ready"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Radiance backend exited early with code {process.returncode}.")
        try:
            with urllib.request.urlopen(url, timeout=1.0):  # nosec B310
                return
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            last_error = str(exc) or last_error
        time.sleep(0.5)
    raise RuntimeError(f"Timed out waiting for backend readiness at {url}: {last_error}")


def _stop(processes: Sequence[subprocess.Popen[bytes]]) -> None:
    for process in processes:
        if process.poll() is None:
            process.terminate()
    deadline = time.monotonic() + 10.0
    for process in processes:
        while process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        if process.poll() is None:
            process.kill()


def main() -> int:
    env = _build_env()
    _maybe_download_precomputed(env)

    backend = _start([sys.executable, "-m", "rad_rebuild.radiance.backend.server"], env)
    processes: list[subprocess.Popen[bytes]] = [backend]

    def forward_signal(signum: int, _frame: object) -> None:
        for process in processes:
            if process.poll() is None:
                process.send_signal(signum)

    signal.signal(signal.SIGTERM, forward_signal)
    signal.signal(signal.SIGINT, forward_signal)

    try:
        _wait_for_backend(env, backend)
        web = _start(
            [
                sys.executable,
                "-m",
                "gunicorn",
                "rad_rebuild.web.app:app",
                "--bind",
                f"0.0.0.0:{env['RADIANCE_RESEARCH_WEB_PORT']}",
                "--workers",
                env.get("WEB_CONCURRENCY", "2"),
            ],
            env,
        )
        processes.append(web)
        while True:
            for process in processes:
                returncode = process.poll()
                if returncode is not None:
                    return int(returncode)
            time.sleep(0.5)
    finally:
        _stop(processes)


if __name__ == "__main__":
    raise SystemExit(main())
