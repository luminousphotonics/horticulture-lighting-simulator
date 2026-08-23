#!/usr/bin/env python3
"""Analyze existing fixture-body audit fields without running transport."""

from __future__ import annotations

import argparse
import csv
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
    field_symmetry_diagnostics,
    signed_field_symmetry_maps,
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coordinates(root: Path, system_id: str) -> np.ndarray:
    candidates = (
        root / "inputs" / system_id / "sensors.pts",
        root / "inputs" / "proposed" / "sensors.pts",
    )
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        raise ValueError(f"sensor coordinates are missing for {system_id}.")
    coordinates = np.loadtxt(path, dtype=np.float64)[:, :3]
    return np.asarray(coordinates, dtype=np.float64)


def _resolution(coordinates: np.ndarray) -> tuple[int, int]:
    x_count = len(np.unique(coordinates[:, 0]))
    y_count = len(np.unique(coordinates[:, 1]))
    if x_count * y_count != len(coordinates):
        raise ValueError("sensor coordinates do not form a complete grid.")
    expected = np.asarray(
        [
            (x, y)
            for y in np.unique(coordinates[:, 1])
            for x in np.unique(coordinates[:, 0])
        ],
        dtype=np.float64,
    )
    if not np.array_equal(coordinates[:, :2], expected):
        raise ValueError("sensor grid is not ascending y-major/x-minor order.")
    return x_count, y_count


def _flatten(record: dict[str, object]) -> dict[str, object]:
    statistics = record["symmetry"]
    assert isinstance(statistics, dict)

    def value(transform: str, metric: str) -> float | None:
        payload = statistics.get(transform)
        return None if payload is None else float(payload[metric])

    return {
        "case_id": record["case_id"],
        "system_id": record["system_id"],
        "material_id": record["material_id"],
        "geometry_variant": record["geometry_variant"],
        "ambient_bounces": record["ambient_bounces"],
        "sampling_id": record["sampling_id"],
        "upper_minus_lower_mean": statistics["upper_minus_lower_mean"],
        "right_minus_left_mean": statistics["right_minus_left_mean"],
        "x_mirror_mae": value("x_mirror", "mean_absolute"),
        "x_mirror_rms": value("x_mirror", "rms"),
        "y_mirror_mae": value("y_mirror", "mean_absolute"),
        "y_mirror_rms": value("y_mirror", "rms"),
        "rotation_180_mae": value("rotation_180", "mean_absolute"),
        "rotation_180_rms": value("rotation_180", "rms"),
        "rotation_90_mae": value("rotation_90", "mean_absolute"),
        "rotation_90_rms": value("rotation_90", "rms"),
        "maximum": statistics["maximum"],
        "maximum_location_m": json.dumps(statistics["maximum_location_m"]),
        "minimum": statistics["minimum"],
        "minimum_location_m": json.dumps(statistics["minimum_location_m"]),
        "field_npy_sha256": record["field_npy_sha256"],
    }


def analyze_existing_fields(
    audit_root: str | Path,
    output_directory: str | Path,
) -> tuple[Path, Path, Path]:
    root = Path(audit_root).expanduser().resolve()
    output = Path(output_directory).expanduser().resolve()
    if output.exists() or output.is_symlink():
        raise ValueError(f"output directory already exists: {output}")
    case_paths = sorted(root.glob("transport-cases/*/case.v1.json"))
    if not case_paths:
        raise ValueError("no fixture-body audit cases were found.")
    output.mkdir(parents=True)
    coordinate_cache: dict[str, np.ndarray] = {}
    resolution_cache: dict[str, tuple[int, int]] = {}
    records: list[dict[str, object]] = []
    maps: dict[str, np.ndarray] = {}
    for case_path in case_paths:
        case = json.loads(case_path.read_text(encoding="utf-8"))
        ambient_bounces = int(case["ambient_bounces"])
        if ambient_bounces not in {0, 1, 2, 5}:
            continue
        field_path = case_path.parent / "field.ppfd.npy"
        if not field_path.is_file():
            continue
        system_id = str(case["system_id"])
        coordinates = coordinate_cache.setdefault(
            system_id, _coordinates(root, system_id)
        )
        resolution_x, resolution_y = resolution_cache.setdefault(
            system_id, _resolution(coordinates)
        )
        field = np.load(field_path, allow_pickle=False)
        if field.shape != (len(coordinates),):
            raise ValueError(f"field shape is incompatible: {field_path}")
        statistics = field_symmetry_diagnostics(
            field,
            coordinates,
            resolution_x=resolution_x,
            resolution_y=resolution_y,
        )
        case_id = str(case["case_id"])
        for name, residual in signed_field_symmetry_maps(
            field,
            resolution_x=resolution_x,
            resolution_y=resolution_y,
        ).items():
            maps[f"{case_id}__{name}"] = residual
        records.append(
            {
                "case_id": case_id,
                "system_id": system_id,
                "material_id": str(case["material_id"]),
                "geometry_variant": str(case["geometry_variant"]),
                "ambient_bounces": ambient_bounces,
                "sampling_id": str(case["sampling"]["sampling_id"]),
                "field_npy": str(field_path),
                "field_npy_sha256": _sha256(field_path),
                "symmetry": statistics,
            }
        )
    report = {
        "schema_id": "fspm-optics.fixture-body-field-symmetry",
        "schema_version": 1,
        "source_audit_root": str(root),
        "transport_executed": False,
        "field_count": len(records),
        "signed_residual_definition": (
            "field at each original grid cell minus its transformed counterpart"
        ),
        "half_plane_definition": (
            "strict positive-coordinate mean minus strict negative-coordinate "
            "mean; the center row or column is excluded"
        ),
        "cases": records,
    }
    json_path = output / "existing-field-symmetry.v1.json"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    csv_path = output / "existing-field-symmetry.v1.csv"
    rows = [_flatten(record) for record in records]
    with csv_path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    maps_path = output / "signed-residual-maps.v1.npz"
    np.savez_compressed(maps_path, **maps)
    return json_path, csv_path, maps_path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--audit-root", required=True, type=Path)
    parser.add_argument("--output-directory", required=True, type=Path)
    arguments = parser.parse_args()
    for path in analyze_existing_fields(
        arguments.audit_root, arguments.output_directory
    ):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
