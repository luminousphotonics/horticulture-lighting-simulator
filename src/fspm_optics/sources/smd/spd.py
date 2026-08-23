"""Strict loaders for explicit SMD relative radiant SPD resources."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

from fspm_optics.spectral.spd import (
    RelativeRadiantSpd,
    load_relative_radiant_spd_csv,
)

SMD_SPD_RESOURCE_NAMES: Final[tuple[str, ...]] = (
    "smd_3000k_spd.csv",
    "smd_5000k_spd.csv",
    "smd_660nm_spd.csv",
)
SMD_RESOURCE_NAMES: Final[tuple[str, ...]] = SMD_SPD_RESOURCE_NAMES
_PACKAGE_SMD_RESOURCE_ROOT = (
    Path(__file__).resolve().parents[2] / "resources" / "data" / "smd"
)


def smd_resource_directory(data_root: str | Path | None = None) -> Path:
    return (
        _PACKAGE_SMD_RESOURCE_ROOT
        if data_root is None
        else Path(data_root).expanduser()
    )


def smd_resource_path(
    resource_name: str,
    *,
    data_root: str | Path | None = None,
) -> Path:
    if resource_name not in SMD_RESOURCE_NAMES:
        raise KeyError(f"unknown explicit SMD resource: {resource_name!r}")
    path = smd_resource_directory(data_root) / resource_name
    if not path.is_file():
        raise FileNotFoundError(f"required SMD resource not found: {path}")
    return path


def sha256_resource(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_smd_spd(
    resource_name: str,
    *,
    data_root: str | Path | None = None,
) -> RelativeRadiantSpd:
    """Load one explicitly named SMD SPD without filename-based identity inference."""

    if resource_name not in SMD_SPD_RESOURCE_NAMES:
        raise KeyError(f"unknown explicit SMD SPD resource: {resource_name!r}")
    path = smd_resource_path(resource_name, data_root=data_root)
    try:
        return load_relative_radiant_spd_csv(path)
    except OSError as exc:
        raise OSError(f"could not read SMD SPD resource {path}: {exc}") from exc


def load_spd_csv(path: str | Path) -> RelativeRadiantSpd:
    """Load a strict standalone SPD CSV, primarily for explicit external data."""

    return load_relative_radiant_spd_csv(path)
