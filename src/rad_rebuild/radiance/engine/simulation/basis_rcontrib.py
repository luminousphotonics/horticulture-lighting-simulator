from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
# Subprocess calls in this module use resolved argv lists without invoking a shell.
import subprocess  # nosec B404
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import numpy.typing as npt

from rad_rebuild.radiance.executables import (
    ExecutableResolutionError,
    resolve_executable,
)
from rad_rebuild.radiance.engine.simulation.basis_backends import (
    BASIS_BACKEND_RTRACE,
    basis_ring_modifier_name,
    canonicalize_basis_backend,
    describe_basis_backend_config,
    rcontrib_command_args,
)

FloatArray = npt.NDArray[np.float64]
JsonObject = dict[str, Any]


@dataclass(frozen=True)
class RcontribBasisConfig:
    """Typed service config for ring-wise rcontrib basis construction."""

    backend: str
    root: Path
    basis_dir: Path
    rad_tmp: Path
    static_room_oct: Path
    sensor_points: Path
    ring_count: int
    basis_unit_w: float
    oversample: int
    nthreads: int
    log_path: Path
    env: Mapping[str, Any]

    @classmethod
    def from_args(
        cls, args: argparse.Namespace, env: Mapping[str, Any] | None = None
    ) -> "RcontribBasisConfig":
        return cls(
            backend=str(args.backend),
            root=Path(args.root).resolve(),
            basis_dir=Path(args.basis_dir).resolve(),
            rad_tmp=Path(args.rad_tmp).resolve(),
            static_room_oct=Path(args.static_room_oct).resolve(),
            sensor_points=Path(args.sensor_points).resolve(),
            ring_count=int(args.ring_count),
            basis_unit_w=float(args.basis_unit_w),
            oversample=int(args.oversample),
            nthreads=int(args.nthreads or 0),
            log_path=Path(args.log_path).resolve(),
            env=os.environ if env is None else env,
        ).normalized()

    def normalized(self, *, cpu_count: int | None = None) -> "RcontribBasisConfig":
        ring_count = int(self.ring_count)
        if ring_count <= 0:
            raise ValueError("ring_count must be positive")
        basis_unit_w = float(self.basis_unit_w)
        if not np.isfinite(basis_unit_w) or basis_unit_w <= 0.0:
            raise ValueError("basis_unit_w must be finite and positive")
        nthreads = int(self.nthreads)
        if nthreads <= 0:
            nthreads = max(
                1, int(cpu_count if cpu_count is not None else (os.cpu_count() or 1))
            )
        return RcontribBasisConfig(
            backend=canonicalize_basis_backend(self.backend),
            root=self.root,
            basis_dir=self.basis_dir,
            rad_tmp=self.rad_tmp,
            static_room_oct=self.static_room_oct,
            sensor_points=self.sensor_points,
            ring_count=ring_count,
            basis_unit_w=basis_unit_w,
            oversample=max(1, int(self.oversample)),
            nthreads=nthreads,
            log_path=self.log_path,
            env={str(k): "" if v is None else str(v) for k, v in self.env.items()},
        )


@dataclass(frozen=True)
class RcontribBasisResult:
    """Structured result for basis build orchestration and serialization."""

    backend: str
    basis_dir: Path
    log_path: Path
    matrix_sha256: str
    matrix_shape: tuple[int, int]
    modifier_order: tuple[str, ...]
    wall_time_s: float
    payload: JsonObject

    @classmethod
    def from_payload(
        cls, config: RcontribBasisConfig, payload: JsonObject
    ) -> "RcontribBasisResult":
        summary = payload.get("matrix_summary")
        if not isinstance(summary, dict):
            raise ValueError("basis build payload is missing matrix_summary")
        shape = summary.get("shape")
        if not isinstance(shape, list) or len(shape) != 2:
            raise ValueError("basis build payload has invalid matrix shape")
        return cls(
            backend=str(payload.get("basis_backend", config.backend)),
            basis_dir=config.basis_dir,
            log_path=config.log_path,
            matrix_sha256=str(payload["matrix_sha256"]),
            matrix_shape=(int(shape[0]), int(shape[1])),
            modifier_order=tuple(
                str(item) for item in payload.get("modifier_order", ())
            ),
            wall_time_s=float(payload.get("wall_time_s", 0.0) or 0.0),
            payload=payload,
        )


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Build a ring-wise SMD basis with rcontrib."
    )
    ap.add_argument("--backend", required=True)
    ap.add_argument("--root", default=".")
    ap.add_argument("--basis-dir", required=True)
    ap.add_argument("--rad-tmp", required=True)
    ap.add_argument("--static-room-oct", required=True)
    ap.add_argument("--sensor-points", required=True)
    ap.add_argument("--ring-count", type=int, required=True)
    ap.add_argument("--basis-unit-w", type=float, required=True)
    ap.add_argument("--oversample", type=int, default=4)
    ap.add_argument("--nthreads", type=int, default=0)
    ap.add_argument("--log-path", required=True)
    return ap.parse_args()


def _quoted(cmd: list[str]) -> str:
    return shlex.join([str(part) for part in cmd])


def _build_raypath(root: Path, env: Mapping[str, Any] | None = None) -> str:
    source = {
        str(k): "" if v is None else str(v) for k, v in (env or os.environ).items()
    }
    ies_root = source.get("RADIANCE_IES_ROOT", "")
    parts = [str(root), str(root / "runtime_state")]
    if ies_root:
        parts.append(str(ies_root))
    parts.append(str(root / "ies_sources"))
    existing = str(source.get("RAYPATH", "")).strip()
    if existing:
        parts.extend([p for p in existing.split(os.pathsep) if p])
    try:
        rcontrib_path = resolve_executable("rcontrib", env=source)
    except ExecutableResolutionError:
        rcontrib_path = None
    if rcontrib_path is not None:
        lib_guess = rcontrib_path.parent.parent / "lib"
        if lib_guess.joinpath("rayinit.cal").exists():
            parts.append(str(lib_guess))
    for candidate in (
        Path("/home/ladybugbot/lib"),
        Path("/usr/local/lib/ray"),
        Path("/usr/share/radiance/cal"),
        Path("/usr/share/radiance"),
        Path("/usr/lib/radiance"),
        Path("/opt/radiance/lib"),
    ):
        if candidate.joinpath("rayinit.cal").exists():
            parts.append(str(candidate))
    seen: set[str] = set()
    ordered: list[str] = []
    for item in parts:
        token = str(item).strip()
        if not token or token in seen:
            continue
        seen.add(token)
        ordered.append(token)
    return os.pathsep.join(ordered)


def _snake_sensor_coords(sensor_path: Path) -> FloatArray:
    coords = np.loadtxt(sensor_path, dtype=float)
    if coords.ndim == 1:
        coords = coords.reshape(1, -1)
    if coords.shape[1] < 3:
        raise ValueError(
            f"{sensor_path} has unexpected shape {coords.shape}; expected xyz coordinates"
        )
    tol = 1e-6
    rows: dict[float, list[tuple[float, float, float]]] = {}
    for row in coords[:, :3]:
        x, y, z = (float(row[0]), float(row[1]), float(row[2]))
        key = round(y / tol) * tol
        rows.setdefault(key, []).append((x, y, z))
    ordered: list[tuple[float, float, float]] = []
    for row_index, y_key in enumerate(sorted(rows)):
        segment = sorted(rows[y_key], key=lambda item: item[0])
        ordered.extend(segment if (row_index % 2) == 0 else list(reversed(segment)))
    return np.asarray(ordered, dtype=np.float64)


def _write_sensor_cache(
    rad_tmp: Path,
    snake_coords: FloatArray,
    *,
    oversample: int,
) -> tuple[Path, Path]:
    snake_path = rad_tmp / "sensors_snake.txt"
    snake_os_path = rad_tmp / f"sensors_snake_os_{oversample}.txt"
    dirs_path = rad_tmp / f"dirs_tmp_os_{oversample}.txt"
    np.savetxt(snake_path, snake_coords, fmt="%.6f")
    repeated = np.repeat(snake_coords, oversample, axis=0)
    np.savetxt(snake_os_path, repeated, fmt="%.6f")
    with dirs_path.open("w", encoding="utf-8") as handle:
        for row in repeated:
            handle.write(f"{row[0]:.6f} {row[1]:.6f} {row[2]:.6f} 0 0 1\n")
    return snake_os_path, dirs_path


def parse_rcontrib_ascii_output(
    text: str,
    *,
    sensor_count: int,
    oversample: int,
    modifier_order: list[str],
) -> FloatArray:
    # Observed layout for `rcontrib -h- -I+ -V+ -M file ...` is one text row per
    # input sensor record, with RGB triplets concatenated in the exact -M order.
    # The dedicated unit test covers this parser contract so column order cannot
    # silently drift if the command shape changes later.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    expected_records = int(sensor_count) * int(oversample)
    if len(lines) != expected_records:
        raise ValueError(
            f"Malformed rcontrib output: expected {expected_records} records for "
            f"{sensor_count} sensors x oversample {oversample}, got {len(lines)}."
        )
    ring_count = len(modifier_order)
    expected_values = ring_count * 3
    records = np.zeros((expected_records, ring_count), dtype=float)
    for row_index, line in enumerate(lines, start=1):
        parts = line.split()
        if len(parts) != expected_values:
            raise ValueError(
                f"Malformed rcontrib output row {row_index}: expected {expected_values} values "
                f"(RGB triplets for {ring_count} modifiers), got {len(parts)}."
            )
        try:
            values = np.asarray([float(token) for token in parts], dtype=float).reshape(
                ring_count, 3
            )
        except Exception as exc:
            raise ValueError(
                f"Malformed rcontrib output row {row_index}: {exc}"
            ) from exc
        if not np.all(np.isfinite(values)):
            raise ValueError(
                f"Malformed rcontrib output row {row_index}: contains non-finite values."
            )
        records[row_index - 1, :] = values.mean(axis=1)
    if oversample > 1:
        records = records.reshape(sensor_count, oversample, ring_count).mean(axis=1)
    return records


def _matrix_summary(matrix: FloatArray) -> JsonObject:
    return {
        "shape": [int(matrix.shape[0]), int(matrix.shape[1])],
        "min": float(np.min(matrix)),
        "max": float(np.max(matrix)),
        "mean": float(np.mean(matrix)),
        "col_mean": [float(value) for value in np.mean(matrix, axis=0)],
        "col_max": [float(value) for value in np.max(matrix, axis=0)],
    }


def _write_basis_ring_files(
    basis_dir: Path,
    snake_coords: FloatArray,
    basis_matrix: FloatArray,
) -> None:
    for ring_index in range(basis_matrix.shape[1]):
        out = basis_dir / f"basis_ring_{ring_index}.txt"
        data = np.column_stack([snake_coords, basis_matrix[:, ring_index]])
        np.savetxt(out, data, fmt="%.6f")


def _run_checked(
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    stdin_path: Path | None = None,
    stdout_path: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    stdin_handle = stdin_path.open("r", encoding="utf-8") if stdin_path else None
    stdout_handle = stdout_path.open("w", encoding="utf-8") if stdout_path else None
    try:
        completed = subprocess.run(  # nosec B603
            cmd,
            cwd=cwd,
            env=env,
            stdin=stdin_handle,
            stdout=stdout_handle if stdout_handle is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    finally:
        if stdin_handle is not None:
            stdin_handle.close()
        if stdout_handle is not None:
            stdout_handle.close()
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(
            f"Command failed ({completed.returncode}): {_quoted(cmd)}"
            + (f"\n{stderr}" if stderr else "")
        )
    return completed


def _generate_basis_emitters(
    *,
    root: Path,
    env: dict[str, str],
    basis_unit_w: float,
) -> None:
    basis_env = dict(env)
    basis_env["SMD_BASIS_MODE"] = "0"
    basis_env["SMD_RCONTRIB_BASIS_MODE"] = "1"
    basis_env["SMD_MODIFIER_GROUPING"] = "rings"
    basis_env["SMD_BASIS_UNIT_W"] = f"{basis_unit_w:g}"
    try:
        py = resolve_executable(basis_env.get("PY") or sys.executable, env=basis_env)
    except ExecutableResolutionError as exc:
        raise RuntimeError(str(exc)) from exc
    _run_checked(
        [str(py), "-m", "rad_rebuild.radiance.engine.emitters.generate_emitters_smd"],
        cwd=root,
        env=basis_env,
    )


def run_rcontrib_basis_build(config: RcontribBasisConfig) -> RcontribBasisResult:
    """Build a ring-wise basis and return structured artifact metadata.

    This service owns filesystem/subprocess orchestration. The CLI adapter is
    responsible only for converting argv and environment into `config`.
    """
    config = config.normalized()
    backend = config.backend
    if backend == BASIS_BACKEND_RTRACE:
        raise ValueError("rtrace backend should not call the rcontrib basis builder")
    run_env = dict(config.env)
    run_env["RAYPATH"] = _build_raypath(config.root, run_env)
    config.basis_dir.mkdir(parents=True, exist_ok=True)
    config.rad_tmp.mkdir(parents=True, exist_ok=True)

    try:
        rcontrib = resolve_executable("rcontrib", env=run_env)
        oconv_exe = resolve_executable("oconv", env=run_env)
    except ExecutableResolutionError as exc:
        raise RuntimeError(
            f"Basis backend {backend!r} was selected, but {exc}"
        ) from exc

    snake_coords = _snake_sensor_coords(config.sensor_points)
    _write_sensor_cache(config.rad_tmp, snake_coords, oversample=config.oversample)
    dirs_path = config.rad_tmp / f"dirs_tmp_os_{config.oversample}.txt"

    modifier_order = [
        basis_ring_modifier_name(index) for index in range(config.ring_count)
    ]
    modifier_file = config.basis_dir / "basis_rcontrib_modifiers.txt"
    modifier_file.write_text(
        "".join(f"{name}\n" for name in modifier_order), encoding="utf-8"
    )

    emitters_path = config.root / "runtime_state" / "emitters_smd_ALL_umol.rad"
    scene_oct = config.rad_tmp / "rcontrib_scene.oct"
    raw_output_path = config.basis_dir / "basis_rcontrib_raw.txt"
    sim_mode = run_env.get("MODE", "standard")
    command = [
        str(rcontrib),
        "-h-",
        "-faa",
        "-I+",
        "-V+",
        "-n",
        str(config.nthreads),
        *rcontrib_command_args(backend, sim_mode=sim_mode, env=run_env),
        "-M",
        str(modifier_file),
        str(scene_oct),
    ]

    start = time.perf_counter()
    _generate_basis_emitters(
        root=config.root, env=run_env, basis_unit_w=config.basis_unit_w
    )
    with scene_oct.open("wb") as oct_handle:
        oconv = subprocess.run(  # nosec B603
            [str(oconv_exe), "-f", "-i", str(config.static_room_oct), str(emitters_path)],
            cwd=config.root,
            env=run_env,
            stdout=oct_handle,
            stderr=subprocess.PIPE,
            text=False,
            check=False,
        )
    if oconv.returncode != 0:
        stderr = (oconv.stderr or b"").decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"Command failed ({oconv.returncode}): "
            f"{_quoted([str(oconv_exe), '-f', '-i', str(config.static_room_oct), str(emitters_path)])}"
            + (f"\n{stderr}" if stderr else "")
        )
    completed = _run_checked(
        command,
        cwd=config.root,
        env=run_env,
        stdin_path=dirs_path,
        stdout_path=raw_output_path,
    )
    wall_time_s = time.perf_counter() - start
    raw_text = raw_output_path.read_text(encoding="utf-8")
    basis_matrix = parse_rcontrib_ascii_output(
        raw_text,
        sensor_count=int(snake_coords.shape[0]),
        oversample=config.oversample,
        modifier_order=modifier_order,
    )
    _write_basis_ring_files(config.basis_dir, snake_coords, basis_matrix)
    matrix_sha256 = hashlib.sha256(
        np.asarray(basis_matrix, dtype=np.float64).tobytes()
    ).hexdigest()
    payload = {
        "basis_backend": backend,
        "basis_backend_config": describe_basis_backend_config(
            backend, sim_mode=sim_mode, env=run_env
        ),
        "command": _quoted(command),
        "modifier_file": str(modifier_file),
        "modifier_order": modifier_order,
        "oversample": int(config.oversample),
        "threads": int(config.nthreads),
        "ring_count": int(config.ring_count),
        "sensor_count": int(snake_coords.shape[0]),
        "scene_octree": str(scene_oct),
        "raw_output_path": str(raw_output_path),
        "wall_time_s": float(wall_time_s),
        "stderr": (completed.stderr or "").strip(),
        "matrix_sha256": matrix_sha256,
        "matrix_summary": _matrix_summary(basis_matrix),
    }
    config.log_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return RcontribBasisResult.from_payload(config, payload)


def build_rcontrib_ring_basis(
    *,
    backend: str,
    root: Path,
    basis_dir: Path,
    rad_tmp: Path,
    static_room_oct: Path,
    sensor_points: Path,
    ring_count: int,
    basis_unit_w: float,
    oversample: int,
    nthreads: int,
    log_path: Path,
    env: Mapping[str, Any] | None = None,
) -> JsonObject:
    result = run_rcontrib_basis_build(
        RcontribBasisConfig(
            backend=backend,
            root=root,
            basis_dir=basis_dir,
            rad_tmp=rad_tmp,
            static_room_oct=static_room_oct,
            sensor_points=sensor_points,
            ring_count=ring_count,
            basis_unit_w=basis_unit_w,
            oversample=oversample,
            nthreads=nthreads,
            log_path=log_path,
            env=os.environ if env is None else env,
        )
    )
    return result.payload


def main() -> None:
    run_rcontrib_basis_build(
        RcontribBasisConfig.from_args(parse_args(), env=os.environ)
    )


if __name__ == "__main__":
    main()
