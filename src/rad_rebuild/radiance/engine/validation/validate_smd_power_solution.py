#!/usr/bin/env python3
"""Smoke-test SMD optimized-power compatibility and solve metadata."""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import numpy as np

from rad_rebuild.radiance.engine.photometry.ppfd_metrics import (
    compute_ppfd_metrics,
    metric_float,
)
from rad_rebuild.radiance.engine.optimization.solve_uniformity import (
    build_basis_solve_transform,
    load_basis,
    solution_coefficients_from_json,
)


from rad_rebuild.radiance.paths import RADIANCE_BASIS_OUTPUT_ROOT, RADIANCE_OUTPUT_ROOT

ROOT = RADIANCE_OUTPUT_ROOT
BASIS_PATH = RADIANCE_BASIS_OUTPUT_ROOT / "basis_A.npy"
BASIS_MANIFEST_PATH = ROOT / "basis_manifest.json"
STALE_JSON_PATH = ROOT / "ring_powers_optimized.json"


# Bandit B404/B603 waiver: this validator invokes fixed repository modules with
# argv lists and no shell. Owner: Phase 12.5C.2. Review before 2026-09-30.


@dataclass(frozen=True)
class SmdPowerSolutionResult:
    basis_mean_ppfd: float
    fresh_run_excerpt: tuple[str, ...]


def _load_manifest() -> dict[str, object]:
    manifest = json.loads(BASIS_MANIFEST_PATH.read_text())
    if not isinstance(manifest, dict):
        raise SystemExit("basis_manifest.json did not contain an object.")
    return cast(dict[str, object], manifest)


def _emitter_env_value(manifest: Mapping[str, object], key: str) -> object:
    emitter_env = manifest.get("emitter_env")
    if not isinstance(emitter_env, Mapping):
        return 0
    return emitter_env.get(key, 0)


def _solve_env(manifest: Mapping[str, object]) -> dict[str, str]:
    return {
        "SMD_MODEL": "curve",
        "PPE_IS_SYSTEM": "0",
        "SMD_TARGET_PPE_UMOL_PER_J": "0.0",
        "SMD_MODULE_PROFILE": "145led_clear_lid_ptfe_stack_v2",
        "LAYOUT_MODE": "exact_tiled",
        "SMD_BASE_RING_N": str(_emitter_env_value(manifest, "SMD_BASE_RING_N")),
        "LENGTH_FT": "12",
        "WIDTH_FT": "12",
        "SMD_WW_COUNT": "52",
        "SMD_CW_COUNT": "52",
        "SMD_RED_COUNT": "41",
        "SMD_WW_NOMINAL_W": "0.68",
        "SMD_CW_NOMINAL_W": "0.68",
        "SMD_RED_NOMINAL_W": "0.44",
        "SMD_WW_NOMINAL_PPE": "2.73",
        "SMD_CW_NOMINAL_PPE": "2.81",
        "SMD_RED_NOMINAL_PPE": "4.13",
    }


def _run_generator(*, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "rad_rebuild.radiance.engine.emitters.generate_emitters_smd",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


def _run_solver(
    *, out_json: Path, solve_env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [
            sys.executable,
            "-m",
            "rad_rebuild.radiance.engine.optimization.solve_uniformity",
            "--solve-method",
            "minvar_qp",
            "--basis",
            str(BASIS_PATH),
            "--target-ppfd",
            "1000",
            "--w-min",
            "0",
            "--w-max",
            "100",
            "--legacy-metrics",
            "--out-json",
            str(out_json),
        ],
        cwd=ROOT,
        env={**os.environ, **solve_env},
        capture_output=True,
        text=True,
    )


def _load_solution(path: Path) -> dict[str, object]:
    solved = json.loads(path.read_text())
    if not isinstance(solved, dict):
        raise SystemExit("Solve output did not contain an object.")
    return cast(dict[str, object], solved)


def _validate_solved_field(
    *,
    manifest: dict[str, object],
    solve_env: dict[str, str],
    solved: Mapping[str, object],
) -> float:
    basis = load_basis(BASIS_PATH)
    weights = np.asarray(solved.get("ring_powers_W_per_module") or [], dtype=float)
    if weights.size == 0:
        raise SystemExit("Solve output did not contain ring powers.")
    transform = build_basis_solve_transform(
        basis_manifest=manifest, runtime_env=solve_env
    )
    coeffs = solution_coefficients_from_json(solved, transform=transform)
    field = basis @ coeffs
    field_metrics = compute_ppfd_metrics(
        field, setpoint_ppfd=1000.0, legacy_metrics=True
    )
    basis_mean_ppfd = metric_float(field_metrics, "mean")
    if abs(basis_mean_ppfd - 1000.0) > 1e-3:
        raise SystemExit(f"Basis solve mean drifted: {field_metrics['mean']}")
    if not isinstance(solved.get("smd_solution_metadata"), dict):
        raise SystemExit("Solve output missing smd_solution_metadata.")
    return basis_mean_ppfd


def _stale_env() -> dict[str, str]:
    stale_env = dict(os.environ)
    stale_env.update(
        {
            "SMD_MODEL": "curve",
            "PPE_IS_SYSTEM": "0",
            "SMD_TARGET_PPE_UMOL_PER_J": "0.0",
            "SMD_MODULE_PROFILE": "145led_clear_lid_ptfe_stack_v2",
            "USE_RING_POWERS_JSON": "1",
            "RING_POWERS_JSON": str(STALE_JSON_PATH),
            "LAYOUT_MODE": "square",
            "LENGTH_FT": "12",
            "WIDTH_FT": "12",
        }
    )
    return stale_env


def _fresh_env(*, tmp_path: Path, manifest: Mapping[str, object]) -> dict[str, str]:
    ok_env = dict(os.environ)
    ok_env.update(
        {
            "SMD_MODEL": "curve",
            "PPE_IS_SYSTEM": "0",
            "SMD_TARGET_PPE_UMOL_PER_J": "0.0",
            "SMD_MODULE_PROFILE": "145led_clear_lid_ptfe_stack_v2",
            "USE_RING_POWERS_JSON": "1",
            "RING_POWERS_JSON": str(tmp_path),
            "LAYOUT_MODE": "exact_tiled",
            "SMD_BASE_RING_N": str(_emitter_env_value(manifest, "SMD_BASE_RING_N")),
            "LENGTH_FT": "12",
            "WIDTH_FT": "12",
            "SUBPATCH_GRID": "1",
            "SMD_WW_COUNT": "52",
            "SMD_CW_COUNT": "52",
            "SMD_RED_COUNT": "41",
            "SMD_WW_NOMINAL_W": "0.68",
            "SMD_CW_NOMINAL_W": "0.68",
            "SMD_RED_NOMINAL_W": "0.44",
            "SMD_WW_NOMINAL_PPE": "2.73",
            "SMD_CW_NOMINAL_PPE": "2.81",
            "SMD_RED_NOMINAL_PPE": "4.13",
        }
    )
    return ok_env


def _required_output_markers() -> tuple[str, ...]:
    return (
        "Applied validated power overrides",
        "ring powers status       : compatible",
        "smd_model   : curve",
        "nominal reference:",
        "run-average wall-plug PPE",
    )


def _excerpt_lines(output: str) -> tuple[str, ...]:
    markers = (
        "Applied validated power overrides",
        "ring powers status",
        "smd_model   :",
        "nominal reference:",
        "schedule note:",
        "run-average source PPE",
        "run-average wall-plug PPE",
    )
    return tuple(
        line
        for line in output.splitlines()
        if any(marker in line for marker in markers)
    )


def _validate_stale_json_rejected() -> None:
    stale_proc = _run_generator(env=_stale_env())
    if stale_proc.returncode == 0 or "Incompatible ring powers JSON" not in (
        stale_proc.stderr + stale_proc.stdout
    ):
        raise SystemExit("Stale ring-power JSON was not rejected.")


def _fresh_run_output(*, tmp_path: Path, manifest: Mapping[str, object]) -> str:
    ok_proc = _run_generator(env=_fresh_env(tmp_path=tmp_path, manifest=manifest))
    if ok_proc.returncode != 0:
        raise SystemExit(ok_proc.stderr or ok_proc.stdout)
    output = ok_proc.stdout
    for marker in _required_output_markers():
        if marker not in output:
            raise SystemExit(f"Missing expected output marker: {marker}")
    if "legacy:" in output:
        raise SystemExit("Found stale legacy metrics label in output.")
    return output


def run_smd_power_solution_validation() -> SmdPowerSolutionResult:
    if not BASIS_PATH.exists() or not BASIS_MANIFEST_PATH.exists():
        raise SystemExit(
            "Missing basis_A.npy or basis_manifest.json for SMD smoke test."
        )

    manifest = _load_manifest()
    solve_env = _solve_env(manifest)

    with tempfile.TemporaryDirectory(prefix="smd_solution_smoke_") as tmpdir:
        tmp_path = Path(tmpdir) / "ring_powers_optimized.json"
        solve_proc = _run_solver(out_json=tmp_path, solve_env=solve_env)
        if solve_proc.returncode != 0:
            raise SystemExit(solve_proc.stderr or solve_proc.stdout)

        basis_mean_ppfd = _validate_solved_field(
            manifest=manifest,
            solve_env=solve_env,
            solved=_load_solution(tmp_path),
        )
        _validate_stale_json_rejected()
        fresh_output = _fresh_run_output(tmp_path=tmp_path, manifest=manifest)
        return SmdPowerSolutionResult(
            basis_mean_ppfd=basis_mean_ppfd,
            fresh_run_excerpt=_excerpt_lines(fresh_output),
        )


def main() -> None:
    result = run_smd_power_solution_validation()
    print("basis_mean_ppfd=%.6f" % result.basis_mean_ppfd)
    print("stale_json_rejected=yes")
    print("fresh_json_validated=yes")
    print("fresh_run_excerpt:")
    for line in result.fresh_run_excerpt:
        print(line)


if __name__ == "__main__":
    main()
