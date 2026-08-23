"""Local NumPy and JSON persistence for scalar SMD basis artifacts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
from numpy.typing import NDArray

from .atomic import atomic_save_npy, atomic_write_text
from .manifest import BasisManifest

FloatArray = NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class BasisArtifacts:
    matrix: FloatArray
    manifest: BasisManifest
    matrix_path: Path
    manifest_path: Path


@dataclass(frozen=True, slots=True)
class BasisWorkspaceArtifacts:
    """Validated matrix, manifest, and execution metadata from one workspace."""

    matrix: FloatArray
    manifest: BasisManifest
    execution_metadata: Mapping[str, Any]
    matrix_path: Path
    manifest_path: Path
    execution_summary_path: Path
    reference_watts_per_module: float
    reference_watts_source: str


def validate_basis_matrix_shape(
    matrix: NDArray[np.float64] | object,
    manifest: BasisManifest,
) -> FloatArray:
    """Return a finite numeric matrix matching the manifest contract."""

    try:
        array = np.asarray(matrix, dtype=float)
    except (TypeError, ValueError) as exc:
        raise ValueError("basis matrix must be numeric.") from exc
    if array.ndim != 2:
        raise ValueError(f"basis matrix must be two-dimensional; got {array.shape}.")
    if tuple(array.shape) != manifest.matrix_shape:
        raise ValueError(
            "basis matrix shape does not match manifest: "
            f"expected {manifest.matrix_shape}, got {tuple(array.shape)}."
        )
    if not np.all(np.isfinite(array)):
        raise ValueError("basis matrix contains non-finite values.")
    if np.any(array < 0.0):
        raise ValueError("basis matrix contains negative PPFD values.")
    return np.asarray(array, dtype=float)


def save_basis_matrix(
    path: str | Path,
    matrix: NDArray[np.float64] | object,
    manifest: BasisManifest,
) -> Path:
    output = _npy_path(path)
    array = validate_basis_matrix_shape(matrix, manifest)
    return atomic_save_npy(output, array)


def load_basis_matrix(
    path: str | Path,
    manifest: BasisManifest,
) -> FloatArray:
    source = _npy_path(path)
    try:
        matrix = np.load(source, allow_pickle=False)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"basis matrix not found: {source}") from exc
    return validate_basis_matrix_shape(matrix, manifest)


def save_basis_manifest(path: str | Path, manifest: BasisManifest) -> Path:
    output = Path(path)
    return atomic_write_text(output, format_basis_manifest_json(manifest))


def format_basis_manifest_json(manifest: BasisManifest) -> str:
    return json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n"


def save_basis_execution_summary(
    path: str | Path,
    payload: Mapping[str, Any],
) -> Path:
    """Atomically save a JSON-compatible basis execution summary."""

    return save_json_artifact(path, payload)


def save_json_artifact(path: str | Path, payload: Mapping[str, Any]) -> Path:
    """Atomically save a deterministic JSON object artifact."""

    output = Path(path)
    return atomic_write_text(
        output,
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
    )


def load_basis_manifest(path: str | Path) -> BasisManifest:
    source = Path(path)
    payload = _load_json_object(source, artifact_name="basis manifest")
    return BasisManifest.from_dict(payload)


def load_basis_workspace_artifacts(
    workspace: str | Path,
    *,
    reference_watts_per_module: float | None = None,
) -> BasisWorkspaceArtifacts:
    """Load and cross-check the persisted inputs required for a basis solve.

    ``reference_watts_per_module`` is a compatibility fallback for manifests
    created before reference power was recorded. When a manifest does record
    reference power, a supplied value must agree with it.
    """

    root = Path(workspace).expanduser().resolve()
    manifest_path = root / "basis_manifest.json"
    matrix_path = root / "basis_matrix.npy"
    execution_path = root / "basis_matrix.execution.json"
    manifest_payload = _load_json_object(
        manifest_path,
        artifact_name="basis manifest",
    )
    recorded_reference = manifest_payload.get("reference_watts")
    explicit_reference = _optional_positive_reference(reference_watts_per_module)
    if recorded_reference is None:
        reference = 1.0 if explicit_reference is None else explicit_reference
        reference_source = (
            "default_missing_manifest_reference"
            if explicit_reference is None
            else "explicit_missing_manifest_reference"
        )
        manifest_payload = dict(manifest_payload)
        manifest_payload["reference_watts"] = reference
    else:
        reference = _positive_reference(recorded_reference)
        reference_source = "basis_manifest"
        if explicit_reference is not None and not math.isclose(
            explicit_reference,
            reference,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "reference_watts_per_module conflicts with the basis manifest: "
                f"manifest={reference:.12g}, supplied={explicit_reference:.12g}."
            )
    manifest = BasisManifest.from_dict(manifest_payload)
    matrix = load_basis_matrix(matrix_path, manifest)
    execution = _load_json_object(
        execution_path,
        artifact_name="basis execution summary",
    )
    _validate_execution_metadata(execution, manifest, matrix_path)
    return BasisWorkspaceArtifacts(
        matrix=matrix,
        manifest=manifest,
        execution_metadata=execution,
        matrix_path=matrix_path,
        manifest_path=manifest_path,
        execution_summary_path=execution_path,
        reference_watts_per_module=reference,
        reference_watts_source=reference_source,
    )


def save_basis_artifacts(
    *,
    matrix_path: str | Path,
    manifest_path: str | Path,
    matrix: NDArray[np.float64] | object,
    manifest: BasisManifest,
) -> BasisArtifacts:
    saved_matrix = save_basis_matrix(matrix_path, matrix, manifest)
    saved_manifest = save_basis_manifest(manifest_path, manifest)
    return BasisArtifacts(
        matrix=validate_basis_matrix_shape(matrix, manifest),
        manifest=manifest,
        matrix_path=saved_matrix,
        manifest_path=saved_manifest,
    )


def load_basis_artifacts(
    *,
    matrix_path: str | Path,
    manifest_path: str | Path,
) -> BasisArtifacts:
    manifest = load_basis_manifest(manifest_path)
    matrix = load_basis_matrix(matrix_path, manifest)
    return BasisArtifacts(
        matrix=matrix,
        manifest=manifest,
        matrix_path=Path(matrix_path),
        manifest_path=Path(manifest_path),
    )


def _npy_path(value: str | Path) -> Path:
    path = Path(value)
    if path.suffix != ".npy":
        raise ValueError("basis matrix path must use the .npy suffix.")
    return path


def _load_json_object(path: Path, *, artifact_name: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"{artifact_name} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"{artifact_name} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"{artifact_name} root must be a JSON object.")
    return payload


def _validate_execution_metadata(
    payload: Mapping[str, Any],
    manifest: BasisManifest,
    matrix_path: Path,
) -> None:
    expected = {
        "sensor_count": manifest.sensor_count,
        "control_zone_count": manifest.control_zone_count,
    }
    for name, expected_value in expected.items():
        value = payload.get(name)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"basis execution summary is missing integer {name}."
            )
        if value != expected_value:
            raise ValueError(
                f"basis execution summary {name} mismatch: "
                f"expected {expected_value}, got {value}."
            )
    shape = payload.get("matrix_shape")
    if (
        not isinstance(shape, list)
        or len(shape) != 2
        or any(isinstance(value, bool) or not isinstance(value, int) for value in shape)
        or tuple(shape) != manifest.matrix_shape
    ):
        raise ValueError(
            "basis execution summary matrix_shape mismatch: "
            f"expected {manifest.matrix_shape}, got {shape}."
        )
    columns = payload.get("per_column")
    if not isinstance(columns, list) or len(columns) != manifest.control_zone_count:
        raise ValueError(
            "basis execution summary must contain one per_column entry per "
            "control zone."
        )
    for index, column in enumerate(columns):
        if not isinstance(column, Mapping) or column.get("control_zone_index") != index:
            raise ValueError(
                "basis execution summary per_column entries must be ordered by "
                "control_zone_index."
            )
    hashes = payload.get("hashes")
    if not isinstance(hashes, Mapping) or not isinstance(
        hashes.get("matrix_sha256"), str
    ):
        raise ValueError("basis execution summary is missing matrix_sha256.")
    actual_hash = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
    if hashes["matrix_sha256"] != actual_hash:
        raise ValueError("basis matrix hash does not match execution metadata.")


def _optional_positive_reference(value: float | None) -> float | None:
    return None if value is None else _positive_reference(value)


def _positive_reference(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError("reference_watts_per_module must be finite and positive.")
    reference = float(value)
    if not math.isfinite(reference) or reference <= 0.0:
        raise ValueError("reference_watts_per_module must be finite and positive.")
    return reference
