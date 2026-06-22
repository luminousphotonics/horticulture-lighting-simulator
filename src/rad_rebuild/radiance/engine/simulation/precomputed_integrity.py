from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PureWindowsPath
from typing import Any

import numpy as np

JsonObject = dict[str, Any]

_HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PrecomputedIntegrityError(ValueError):
    """Raised when a precomputed bundle manifest references unsafe content."""


def load_json_object(path: Path) -> JsonObject:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PrecomputedIntegrityError(f"Expected JSON object in {path}")
    return data


def resolve_bundle_file(bundle_root: Path, relpath: object) -> Path:
    if not isinstance(relpath, str) or not relpath.strip():
        raise PrecomputedIntegrityError(f"Invalid bundle artifact path: {relpath!r}")
    raw = relpath.strip()
    posix_path = Path(raw)
    windows_path = PureWindowsPath(raw)
    if posix_path.is_absolute() or windows_path.is_absolute() or windows_path.drive:
        raise PrecomputedIntegrityError(f"Bundle artifact path is absolute: {raw}")
    if ".." in posix_path.parts or ".." in windows_path.parts:
        raise PrecomputedIntegrityError(f"Bundle artifact path escapes root: {raw}")

    root = bundle_root.resolve(strict=True)
    try:
        resolved = (root / posix_path).resolve(strict=True)
        resolved.relative_to(root)
    except FileNotFoundError as exc:
        raise PrecomputedIntegrityError(f"Bundle artifact is missing: {raw}") from exc
    except ValueError as exc:
        raise PrecomputedIntegrityError(f"Bundle artifact escapes root: {raw}") from exc
    if not resolved.is_file():
        raise PrecomputedIntegrityError(f"Bundle artifact is not a file: {raw}")
    return resolved


def artifact_map(manifest: JsonObject) -> dict[str, str]:
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise PrecomputedIntegrityError("Precomputed manifest is missing artifacts")
    out: dict[str, str] = {}
    for key, value in artifacts.items():
        if not isinstance(key, str):
            raise PrecomputedIntegrityError("Precomputed artifact keys must be strings")
        if not isinstance(value, str) or not value:
            raise PrecomputedIntegrityError(
                f"Precomputed artifact {key!r} has an invalid path"
            )
        out[key] = value
    return out


def resolve_artifact_map(bundle_root: Path, manifest: JsonObject) -> dict[str, Path]:
    return {
        key: resolve_bundle_file(bundle_root, relpath)
        for key, relpath in artifact_map(manifest).items()
    }


def basis_matrix_sha256(path: Path) -> str:
    matrix = np.load(path, allow_pickle=False, mmap_mode="r")
    canonical = np.asarray(matrix, dtype=np.float64, order="C")
    return hashlib.sha256(canonical.tobytes()).hexdigest()


def expected_basis_sha256(
    manifest: JsonObject, resolved_artifacts: dict[str, Path]
) -> str | None:
    explicit = manifest.get("basis_matrix_sha256")
    if isinstance(explicit, str) and explicit:
        return explicit
    basis_manifest = resolved_artifacts.get("basis_manifest_json")
    if basis_manifest is None:
        return None
    data = load_json_object(basis_manifest)
    value = data.get("matrix_sha256") or data.get("basis_matrix_sha256")
    return value if isinstance(value, str) and value else None


def verify_basis_hash(
    manifest: JsonObject, resolved_artifacts: dict[str, Path]
) -> None:
    basis_path = resolved_artifacts.get("basis_A_npy")
    if basis_path is None:
        return
    expected = expected_basis_sha256(manifest, resolved_artifacts)
    if expected is None or not _HEX_SHA256.fullmatch(expected):
        raise PrecomputedIntegrityError("SMD basis matrix hash is missing or malformed")
    actual = basis_matrix_sha256(basis_path)
    if actual != expected:
        raise PrecomputedIntegrityError("SMD basis matrix hash mismatch")
