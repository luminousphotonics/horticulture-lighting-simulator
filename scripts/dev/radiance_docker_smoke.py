#!/usr/bin/env python3
"""Build and smoke-test the Radiance Docker runtime image."""

from __future__ import annotations

import argparse
import subprocess
import sys
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_IMAGE = "rad-rebuild-radiance:local"
SMOKE_CODE = r"""
import importlib
import os
import shutil
from pathlib import Path

from matplotlib import font_manager

commands = ("oconv", "rtrace", "rcontrib")
missing = [command for command in commands if shutil.which(command) is None]
if missing:
    raise SystemExit(f"missing Radiance executables: {', '.join(missing)}")

for module in (
    "rad_rebuild",
    "rad_rebuild.radiance.cli.scripts",
    "rad_rebuild.radiance.backend.server",
    "rad_rebuild.radiance.engine.simulation.basis_rcontrib",
):
    importlib.import_module(module)

from rad_rebuild.radiance.backend.runtime_status import runtime_status_payload

status = runtime_status_payload({"RADIANCE_ENABLE_LIVE_EXECUTION": "1", **os.environ})
if status["live_supported_modes"] != ["SMD"]:
    raise SystemExit(f"unexpected live-supported modes: {status['live_supported_modes']}")
if status["modes"]["live_local"]["supported_lighting_modes"] != ["SMD"]:
    raise SystemExit(
        "unexpected live-local supported modes: "
        f"{status['modes']['live_local']['supported_lighting_modes']}"
    )
if status["modes"]["precomputed"]["available"] is not True:
    raise SystemExit("precomputed runtime mode must remain available")

required_paths = (
    "/workspace/pyproject.toml",
    "/workspace/src/rad_rebuild/__init__.py",
    "/workspace/src/rad_rebuild/radiance/cli/scripts.py",
    "/workspace/scripts/radiance/reproduce.sh",
    "/workspace/reproduce.sh",
    "/workspace/data/radiance/curve_data/smd/smd_3000k_spd.csv",
    "/workspace/data/radiance/ies_sources",
)
missing_paths = [path for path in required_paths if not Path(path).exists()]
if missing_paths:
    raise SystemExit(f"missing runtime paths: {', '.join(missing_paths)}")

for raw_path in (
    "/workspace/outputs/radiance",
    "/workspace/outputs/radiance/runtime_state",
    "/workspace/outputs/radiance/basis",
    "/workspace/outputs/radiance/cache",
    "/workspace/outputs/radiance/visualizations",
    "/out",
    os.environ["MPLCONFIGDIR"],
    os.environ["XDG_CACHE_HOME"],
    Path(os.environ["XDG_CACHE_HOME"]) / "fontconfig",
    os.environ["TMPDIR"],
    "/tmp/rad-rebuild",
):
    path = Path(raw_path)
    path.mkdir(parents=True, exist_ok=True)
    probe = path / ".write-smoke"
    probe.write_text("ok\n", encoding="utf-8")
    probe.unlink()

font_manager.findfont("DejaVu Sans", fallback_to_default=False)

print("Radiance Docker runtime smoke check passed.")
"""


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, cwd=ROOT, check=True)


def _build_command(image: str, platform: str | None) -> list[str]:
    cmd = ["docker", "build"]
    if platform:
        cmd.extend(["--platform", platform])
    cmd.extend(["-f", "docker/radiance/Dockerfile", "-t", image, "."])
    return cmd


def _run_command(image: str, platform: str | None) -> list[str]:
    cmd = ["docker", "run", "--rm"]
    if platform:
        cmd.extend(["--platform", platform])
    cmd.extend([image, "/opt/venv/bin/python", "-c", SMOKE_CODE])
    return cmd


def _image_size(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Size}}"],
        cwd=ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    parser.add_argument("--platform", default=None)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args(argv)

    if not args.skip_build:
        _run(_build_command(args.image, args.platform))
    _run(_run_command(args.image, args.platform))
    print(f"{args.image} size: {_image_size(args.image)} bytes")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except subprocess.CalledProcessError as exc:
        command = textwrap.shorten(" ".join(exc.cmd), width=180, placeholder=" ...")
        print(f"Command failed with exit code {exc.returncode}: {command}", file=sys.stderr)
        raise SystemExit(exc.returncode) from exc
