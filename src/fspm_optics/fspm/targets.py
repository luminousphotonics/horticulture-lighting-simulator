"""Shared FSPM target PPFD contract helpers."""

from __future__ import annotations

import math
from typing import Any


DEFAULT_FSPM_TARGET_PPFD_UMOL_M2_S = 275.0
DEFAULT_FSPM_TARGET_TOLERANCE_UMOL_M2_S = 75.0
FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY = "canopy_plane_equivalent_incident_ppfd"
FSPM_TARGET_CLASSIFICATION_BASIS_CANOPY_LABEL = (
    "canopy-plane equivalent incident PPFD"
)
FSPM_TARGET_CLASSIFICATION_SOURCE_PPFD_MAP = "interpolated_runtime_ppfd_map"
FSPM_TARGET_CLASSIFICATION_NOTE_CANOPY_MAP = (
    "Target classification uses interpolated runtime PPFD map values at plant "
    "surface XY positions; raw plant-surface receiver incident and absorbed "
    "flux metrics remain separate."
)
FSPM_TARGET_CLASSIFICATION_BASIS_FALLBACK = (
    "plant_surface_receiver_incident_ppfd_fallback"
)
FSPM_TARGET_CLASSIFICATION_BASIS_FALLBACK_LABEL = (
    "plant-surface receiver incident PPFD fallback"
)
FSPM_TARGET_CLASSIFICATION_SOURCE_FALLBACK = "plant_surface_receiver_rows"
FSPM_TARGET_CLASSIFICATION_NOTE_FALLBACK = (
    "Fallback target classification uses plant-surface receiver incident PPFD "
    "and is not directly equivalent to a horizontal canopy-plane target PPFD."
)
FSPM_TARGET_LIMITATION_NOTE = (
    "Target-capped values are lighting-analysis inputs, not biological validation."
)


def positive_finite_float(name: str, value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a JSON number.")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite.")
    if number <= 0.0:
        raise ValueError(f"{name} must be greater than zero.")
    return number


def optional_positive_finite_float(name: str, value: object | None) -> float | None:
    if value is None:
        return None
    return positive_finite_float(name, value)


def resolve_fspm_target_ppfd(
    value: object | None,
    *,
    fallback_target_ppfd: object | None = None,
) -> float:
    explicit = optional_positive_finite_float("fspm_target_ppfd_umol_m2_s", value)
    if explicit is not None:
        return explicit
    try:
        fallback = optional_positive_finite_float("target_ppfd", fallback_target_ppfd)
    except ValueError:
        fallback = None
    return fallback if fallback is not None else DEFAULT_FSPM_TARGET_PPFD_UMOL_M2_S


def resolve_fspm_target_tolerance(value: object | None) -> float:
    explicit = optional_positive_finite_float("fspm_target_tolerance_umol_m2_s", value)
    return explicit if explicit is not None else DEFAULT_FSPM_TARGET_TOLERANCE_UMOL_M2_S


def fspm_target_metadata(
    *,
    target_ppfd_umol_m2_s: float,
    tolerance_umol_m2_s: float,
    target_capping_enabled: bool = True,
    target_classification_basis: str | None = None,
    target_classification_basis_label: str | None = None,
    target_classification_source: str | None = None,
    target_classification_note: str | None = None,
) -> dict[str, Any]:
    lower = target_ppfd_umol_m2_s - tolerance_umol_m2_s
    upper = target_ppfd_umol_m2_s + tolerance_umol_m2_s
    basis = target_classification_basis or FSPM_TARGET_CLASSIFICATION_BASIS_FALLBACK
    basis_label = (
        target_classification_basis_label
        or FSPM_TARGET_CLASSIFICATION_BASIS_FALLBACK_LABEL
    )
    source = target_classification_source or FSPM_TARGET_CLASSIFICATION_SOURCE_FALLBACK
    note = target_classification_note or FSPM_TARGET_CLASSIFICATION_NOTE_FALLBACK
    return {
        "target_ppfd_umol_m2_s": target_ppfd_umol_m2_s,
        "tolerance_umol_m2_s": tolerance_umol_m2_s,
        "target_classification_basis": basis,
        "target_classification_basis_label": basis_label,
        "target_classification_source": source,
        "target_classification_note": note,
        "target_basis": basis,
        "target_basis_label": basis_label,
        "target_lower_threshold_umol_m2_s": lower,
        "target_upper_threshold_umol_m2_s": upper,
        "target_capping_enabled": target_capping_enabled,
        "limitation_note": FSPM_TARGET_LIMITATION_NOTE,
    }
