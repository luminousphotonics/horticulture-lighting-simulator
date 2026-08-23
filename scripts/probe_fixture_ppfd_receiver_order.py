#!/usr/bin/env python3
"""Probe positive-bounce receiver-order effects in the existing no-body scene."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fspm_optics.diagnostics.fixture_body_optics_audit import (  # noqa: E402
    FINE_SAMPLING,
    deterministic_trace_options,
    field_symmetry_diagnostics,
)
from fspm_optics.radiance.commands import (  # noqa: E402
    build_baseline_rtrace_command,
    build_oconv_command,
)
from fspm_optics.radiance.runner import LocalRunner  # noqa: E402
from fspm_optics.transport.basis.parsing import parse_basis_column  # noqa: E402


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _difference(left: np.ndarray, right: np.ndarray) -> dict[str, float]:
    delta = np.asarray(left - right, dtype=np.float64)
    return {
        "mean": float(np.mean(delta)),
        "mean_absolute": float(np.mean(np.abs(delta))),
        "rms": float(np.sqrt(np.mean(np.square(delta)))),
        "maximum_absolute": float(np.max(np.abs(delta))),
    }


def run_probe(
    audit_root: str | Path,
    output_directory: str | Path,
) -> Path:
    audit = Path(audit_root).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise ValueError(f"output directory already exists: {output}")
    source_root = audit / "inputs" / "proposed"
    sensors_path = source_root / "sensors.pts"
    lines = sensors_path.read_text(encoding="utf-8").splitlines()
    if len(lines) != 441:
        raise ValueError("receiver-order probe requires the existing 21x21 grid.")
    rows = [line.split() for line in lines]
    coordinates = np.asarray(
        [[float(token) for token in row[:3]] for row in rows],
        dtype=np.float64,
    )
    output.mkdir(parents=True)
    receiver_inputs = {
        "forward": lines,
        "reverse": list(reversed(lines)),
        "y_mirrored_same_sequence": [
            " ".join(
                [
                    row[0],
                    f"{-float(row[1]):.6f}",
                    *row[2:],
                ]
            )
            for row in rows
        ],
    }
    receiver_paths: dict[str, Path] = {}
    for name, receiver_lines in receiver_inputs.items():
        path = output / f"{name}.pts"
        path.write_text("\n".join(receiver_lines) + "\n", encoding="utf-8")
        receiver_paths[name] = path
    octree = output / "no-bodies.oct"
    oconv = build_oconv_command(
        (
            source_root / "room.rad",
            source_root / "uniform_complete_source.rad",
            audit
            / "diagnostic-scene-inputs"
            / "proposed"
            / "zero_absorber"
            / "no_bodies.rad",
        ),
        output_octree=octree,
        cwd=source_root,
        oconv_bin="/opt/radiance/bin/oconv",
        label="compile_no_body_receiver_order_probe",
    )
    runner = LocalRunner()
    compiled = runner.run(oconv)
    if not compiled.success:
        raise RuntimeError(compiled.failure_message)
    fields: dict[str, np.ndarray] = {}
    trace_records: dict[str, object] = {}
    for name, receiver_path in receiver_paths.items():
        rgb = output / f"{name}.rgb"
        ambient = output / f"{name}.amb"
        options = deterministic_trace_options(
            ambient_bounces=1,
            sampling=FINE_SAMPLING,
            ambient_cache=ambient,
        )
        command = build_baseline_rtrace_command(
            octree=octree,
            receiver_input=receiver_path,
            rgb_output=rgb,
            options=options,
            nthreads=1,
            cwd=source_root,
            rtrace_bin="/opt/radiance/bin/rtrace",
        )
        traced = runner.run(command)
        if not traced.success:
            raise RuntimeError(traced.failure_message)
        field = parse_basis_column(
            rgb.read_text(encoding="utf-8"),
            expected_sensor_count=len(lines),
        )
        fields[name] = np.asarray(field, dtype=np.float64)
        trace_records[name] = {
            "argv": list(command.argv),
            "receiver_input": str(receiver_path),
            "receiver_sha256": _sha256(receiver_path),
            "rgb": str(rgb),
            "rgb_sha256": _sha256(rgb),
            "ambient_cache": str(ambient),
            "ambient_cache_sha256": _sha256(ambient),
            "wall_time_s": traced.wall_time_s,
        }
    reverse_aligned = fields["reverse"][::-1]
    y_mirrored_aligned = fields["y_mirrored_same_sequence"]
    np.save(output / "forward.npy", fields["forward"], allow_pickle=False)
    np.save(output / "reverse-aligned.npy", reverse_aligned, allow_pickle=False)
    np.save(
        output / "y-mirrored-aligned.npy",
        y_mirrored_aligned,
        allow_pickle=False,
    )
    report = {
        "schema_id": "fspm-optics.fixture-body-receiver-order-probe",
        "schema_version": 1,
        "transport_scope": "COB no bodies, first positive bounce only",
        "source_input_fixed": True,
        "target_control_enabled": False,
        "emitted_ppf_changed": False,
        "nthreads": 1,
        "sampling_randomization": "-u-",
        "ambient_state": "one initially absent isolated cache per trace",
        "oconv": {
            "argv": list(oconv.argv),
            "octree": str(octree),
            "octree_sha256": _sha256(octree),
            "wall_time_s": compiled.wall_time_s,
        },
        "traces": trace_records,
        "forward_symmetry": field_symmetry_diagnostics(
            fields["forward"],
            coordinates,
            resolution_x=21,
            resolution_y=21,
        ),
        "coordinate_aligned_differences": {
            "forward_minus_reverse_receiver_order": _difference(
                fields["forward"], reverse_aligned
            ),
            "forward_minus_y_mirrored_same_sequence": _difference(
                fields["forward"], y_mirrored_aligned
            ),
        },
    }
    path = output / "receiver-order-probe.v1.json"
    path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args()
    print(run_probe(arguments.audit_root, arguments.output_directory))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
