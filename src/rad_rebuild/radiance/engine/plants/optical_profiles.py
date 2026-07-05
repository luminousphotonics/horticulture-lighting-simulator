"""Source-bound leaf optical profiles for modeled plant photon absorption.

The first registered profile comes from digitized published Rex lettuce leaf
optical curves. The v0.2 bundle intentionally preserves reflectance and
transmittance beyond the direct absorptance range; implied absorptance is used
only where direct raw absorptance is unavailable. These profiles support modeled
absorbed photon flux, not photosynthesis, morphology, biomass, or yield
prediction.
"""

from __future__ import annotations

from dataclasses import dataclass
import csv
import json
import math
from pathlib import Path
from typing import Any, Iterable, Mapping

REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1 = (
    "rex_green_butterhead_mature_leaf_optics_v1"
)
REX_LEAF_OPTICS_TREATMENT_ID = "mean_of_treatments"

_REX_LETTUCE_PROFILE_DIR = Path("data/radiance/plant_optics/lettuce_rex")
_REX_LETTUCE_PROFILE_CSV = (
    _REX_LETTUCE_PROFILE_DIR / "rex_leaf_optical_properties_digitized_v0_2.csv"
)
_REX_LETTUCE_MANIFEST = (
    _REX_LETTUCE_PROFILE_DIR / "rex_leaf_optical_profile_manifest_v0_2.json"
)
_REGISTERED_PROFILE_IDS = (REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,)


@dataclass(frozen=True)
class LeafOpticalTreatmentProfile:
    """Per-treatment wavelength curve retained for source auditability."""

    treatment_id: str
    treatment_label: str
    wavelength_nm: tuple[int, ...]
    reflectance: tuple[float, ...]
    transmittance: tuple[float, ...]
    absorptance: tuple[float, ...]
    raw_reflectance: tuple[float | None, ...]
    raw_transmittance: tuple[float | None, ...]
    raw_absorptance: tuple[float | None, ...]
    implied_absorptance: tuple[float | None, ...]
    absorptance_basis: tuple[str, ...]

    def __post_init__(self) -> None:
        _validate_curve_lengths(
            self.wavelength_nm,
            self.reflectance,
            self.transmittance,
            self.absorptance,
            self.raw_reflectance,
            self.raw_transmittance,
            self.raw_absorptance,
            self.implied_absorptance,
            self.absorptance_basis,
        )
        if not self.treatment_id:
            raise ValueError("treatment_id must be non-empty.")
        if not self.treatment_label:
            raise ValueError("treatment_label must be non-empty.")
        _validate_sorted_wavelengths(self.wavelength_nm)
        _validate_fraction_sequence("reflectance", self.reflectance)
        _validate_fraction_sequence("transmittance", self.transmittance)
        _validate_fraction_sequence("absorptance", self.absorptance)
        _validate_optional_fraction_sequence("raw_reflectance", self.raw_reflectance)
        _validate_optional_fraction_sequence("raw_transmittance", self.raw_transmittance)
        _validate_optional_fraction_sequence("raw_absorptance", self.raw_absorptance)
        _validate_optional_fraction_sequence(
            "implied_absorptance",
            self.implied_absorptance,
        )

    def index_for_wavelength(self, wavelength_nm: int) -> int:
        return self.wavelength_nm.index(wavelength_nm)


@dataclass(frozen=True)
class LeafOpticalProfile:
    """Loaded leaf optical profile on a wavelength grid."""

    profile_id: str
    species: str
    cultivar: str
    growth_stage: str
    leaf_side_basis: str
    wavelength_nm: tuple[int, ...]
    reflectance: tuple[float, ...]
    transmittance: tuple[float, ...]
    absorptance: tuple[float, ...]
    source: str
    data_provenance: str
    validation_status: str
    raw_reflectance: tuple[float | None, ...]
    raw_transmittance: tuple[float | None, ...]
    raw_absorptance: tuple[float | None, ...]
    implied_absorptance: tuple[float | None, ...]
    absorptance_basis: tuple[str, ...]
    treatment_id: str
    treatment_label: str
    profile_version: str
    treatments: tuple[LeafOpticalTreatmentProfile, ...]

    def __post_init__(self) -> None:
        _validate_curve_lengths(
            self.wavelength_nm,
            self.reflectance,
            self.transmittance,
            self.absorptance,
            self.raw_reflectance,
            self.raw_transmittance,
            self.raw_absorptance,
            self.implied_absorptance,
            self.absorptance_basis,
        )
        for field_name in (
            "profile_id",
            "species",
            "cultivar",
            "growth_stage",
            "leaf_side_basis",
            "source",
            "data_provenance",
            "validation_status",
            "treatment_id",
            "treatment_label",
            "profile_version",
        ):
            if not getattr(self, field_name):
                raise ValueError(f"{field_name} must be non-empty.")
        _validate_sorted_wavelengths(self.wavelength_nm)
        _validate_fraction_sequence("reflectance", self.reflectance)
        _validate_fraction_sequence("transmittance", self.transmittance)
        _validate_fraction_sequence("absorptance", self.absorptance)
        if not self.treatments:
            raise ValueError("At least one treatment curve is required.")

    def treatment(self, treatment_id: str) -> LeafOpticalTreatmentProfile:
        for treatment in self.treatments:
            if treatment.treatment_id == treatment_id:
                return treatment
        raise KeyError(f"Unknown treatment_id: {treatment_id!r}")


def list_leaf_optical_profiles(data_root: str | Path | None = None) -> tuple[str, ...]:
    """Return source-bound leaf optical profiles available to opt in to."""

    del data_root
    return _REGISTERED_PROFILE_IDS


def load_leaf_optical_profile(
    profile_id: str,
    data_root: str | Path | None = None,
) -> LeafOpticalProfile:
    """Load a registered leaf optical profile by explicit profile ID."""

    if profile_id == REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1:
        return load_rex_green_butterhead_mature_leaf_optics_v1(data_root=data_root)
    raise KeyError(f"Unknown leaf optical profile: {profile_id!r}")


def load_rex_green_butterhead_mature_leaf_optics_v1(
    data_root: str | Path | None = None,
) -> LeafOpticalProfile:
    """Load the v0.2 Rex lettuce leaf optical profile from repo data."""

    root = _resolve_data_root(data_root)
    csv_path = root / _REX_LETTUCE_PROFILE_CSV
    manifest_path = root / _REX_LETTUCE_MANIFEST
    manifest = _read_manifest(manifest_path)
    rows = _read_profile_rows(csv_path)
    profile_rows = [
        row
        for row in rows
        if row["profile_id"] == REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1
    ]
    if not profile_rows:
        raise ValueError(
            "No rows found for "
            f"{REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1!r} in {csv_path}."
        )

    treatments = tuple(
        _curve_from_rows(treatment_rows)
        for treatment_rows in _rows_by_treatment(profile_rows).values()
    )
    main = _find_treatment(treatments, REX_LEAF_OPTICS_TREATMENT_ID)
    source = _source_text(manifest, profile_rows[0])

    return LeafOpticalProfile(
        profile_id=REX_GREEN_BUTTERHEAD_MATURE_LEAF_OPTICS_V1,
        species=str(manifest.get("species") or profile_rows[0]["species"]),
        cultivar=str(manifest.get("cultivar") or profile_rows[0]["cultivar"]),
        growth_stage=str(
            manifest.get("growth_stage")
            or profile_rows[0]["growth_stage"]
            or "uppermost fully expanded mature leaves"
        ),
        leaf_side_basis=str(
            profile_rows[0].get("leaf_side_basis")
            or "leaf-level optical properties from published Figure 7 digitization"
        ),
        wavelength_nm=main.wavelength_nm,
        reflectance=main.reflectance,
        transmittance=main.transmittance,
        absorptance=main.absorptance,
        source=source,
        data_provenance=str(
            manifest.get("data_provenance")
            or profile_rows[0]["data_provenance"]
            or "digitized_from_published_figure"
        ),
        validation_status=str(
            manifest.get("validation_status")
            or profile_rows[0]["validation_status"]
            or "not_physically_validated_in_this_simulator"
        ),
        raw_reflectance=main.raw_reflectance,
        raw_transmittance=main.raw_transmittance,
        raw_absorptance=main.raw_absorptance,
        implied_absorptance=main.implied_absorptance,
        absorptance_basis=main.absorptance_basis,
        treatment_id=main.treatment_id,
        treatment_label=main.treatment_label,
        profile_version=str(manifest.get("profile_version") or profile_rows[0]["profile_version"]),
        treatments=treatments,
    )


def _resolve_data_root(data_root: str | Path | None) -> Path:
    if data_root is not None:
        return Path(data_root)
    current = Path(__file__).resolve()
    for parent in current.parents:
        if (parent / _REX_LETTUCE_PROFILE_CSV).exists():
            return parent
    return current.parents[5]


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Leaf optical profile manifest not found: {path}") from exc


def _read_profile_rows(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            return list(csv.DictReader(handle))
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"Leaf optical profile CSV not found: {path}") from exc


def _rows_by_treatment(
    rows: Iterable[Mapping[str, str]],
) -> dict[str, list[Mapping[str, str]]]:
    grouped: dict[str, list[Mapping[str, str]]] = {}
    for row in rows:
        grouped.setdefault(row["treatment_id"], []).append(row)
    return dict(sorted(grouped.items()))


def _curve_from_rows(rows: list[Mapping[str, str]]) -> LeafOpticalTreatmentProfile:
    ordered = sorted(rows, key=lambda row: int(row["wavelength_nm"]))
    treatment_ids = {row["treatment_id"] for row in ordered}
    if len(treatment_ids) != 1:
        raise ValueError(f"Expected one treatment per curve, got {sorted(treatment_ids)}.")

    _validate_absorptance_source_choice(ordered)
    return LeafOpticalTreatmentProfile(
        treatment_id=ordered[0]["treatment_id"],
        treatment_label=ordered[0]["treatment_label"],
        wavelength_nm=tuple(int(row["wavelength_nm"]) for row in ordered),
        reflectance=tuple(_float(row, "reflectance_fraction_model") for row in ordered),
        transmittance=tuple(_float(row, "transmittance_fraction_model") for row in ordered),
        absorptance=tuple(_float(row, "absorptance_fraction_model") for row in ordered),
        raw_reflectance=tuple(
            _optional_float(row, "reflectance_fraction_raw_digitized") for row in ordered
        ),
        raw_transmittance=tuple(
            _optional_float(row, "transmittance_fraction_raw_digitized")
            for row in ordered
        ),
        raw_absorptance=tuple(
            _optional_float(row, "absorptance_fraction_raw_digitized") for row in ordered
        ),
        implied_absorptance=tuple(
            _optional_float(row, "absorptance_fraction_implied_from_rt") for row in ordered
        ),
        absorptance_basis=tuple(row["absorptance_basis"] for row in ordered),
    )


def _find_treatment(
    treatments: tuple[LeafOpticalTreatmentProfile, ...],
    treatment_id: str,
) -> LeafOpticalTreatmentProfile:
    for treatment in treatments:
        if treatment.treatment_id == treatment_id:
            return treatment
    raise ValueError(f"Missing required treatment curve: {treatment_id!r}.")


def _source_text(manifest: Mapping[str, Any], fallback_row: Mapping[str, str]) -> str:
    source = manifest.get("source")
    if isinstance(source, Mapping):
        citation = source.get("citation_short")
        figure = source.get("source_figure")
        if citation and figure:
            return f"{citation}; {figure}"
        if citation:
            return str(citation)
    return fallback_row["source"]


def _float(row: Mapping[str, str], column: str) -> float:
    raw_value = row[column]
    if raw_value == "":
        raise ValueError(f"Required numeric column {column!r} is blank.")
    value = float(raw_value)
    if not math.isfinite(value):
        raise ValueError(f"Column {column!r} must be finite.")
    return value


def _optional_float(row: Mapping[str, str], column: str) -> float | None:
    raw_value = row[column]
    if raw_value == "":
        return None
    value = float(raw_value)
    if not math.isfinite(value):
        raise ValueError(f"Column {column!r} must be finite when present.")
    return value


def _validate_absorptance_source_choice(rows: list[Mapping[str, str]]) -> None:
    for row in rows:
        basis = row["absorptance_basis"]
        model = _float(row, "absorptance_fraction_model")
        raw_absorptance = _optional_float(row, "absorptance_fraction_raw_digitized")
        implied_absorptance = _optional_float(
            row,
            "absorptance_fraction_implied_from_rt",
        )
        if basis == "raw_digitized" and raw_absorptance is not None:
            _assert_close_fraction(
                "absorptance_fraction_model",
                model,
                raw_absorptance,
                row["wavelength_nm"],
            )
        if basis.startswith("implied_from_reflectance_transmittance"):
            if raw_absorptance is not None:
                raise ValueError(
                    "Implied absorptance basis cannot be used when direct raw "
                    f"absorptance is present at {row['wavelength_nm']} nm."
                )
            if implied_absorptance is not None:
                _assert_close_fraction(
                    "absorptance_fraction_model",
                    model,
                    implied_absorptance,
                    row["wavelength_nm"],
                )


def _assert_close_fraction(name: str, actual: float, expected: float, wavelength: str) -> None:
    if abs(actual - expected) > 1e-9:
        raise ValueError(
            f"{name} at {wavelength} nm does not match selected source column."
        )


def _validate_curve_lengths(*arrays: tuple[Any, ...]) -> None:
    lengths = {len(array) for array in arrays}
    if len(lengths) != 1:
        raise ValueError(f"Curve arrays must have matching lengths, got {sorted(lengths)}.")
    if not lengths or next(iter(lengths)) == 0:
        raise ValueError("Curve arrays must not be empty.")


def _validate_sorted_wavelengths(wavelength_nm: tuple[int, ...]) -> None:
    if tuple(sorted(wavelength_nm)) != wavelength_nm:
        raise ValueError("wavelength_nm must be sorted ascending.")
    if len(set(wavelength_nm)) != len(wavelength_nm):
        raise ValueError("wavelength_nm values must be unique.")


def _validate_fraction_sequence(name: str, values: tuple[float, ...]) -> None:
    for value in values:
        _validate_fraction(name, value)


def _validate_optional_fraction_sequence(
    name: str,
    values: tuple[float | None, ...],
) -> None:
    for value in values:
        if value is not None:
            _validate_fraction(name, value)


def _validate_fraction(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(f"{name} values must be finite.")
    if value < 0.0 or value > 1.0:
        raise ValueError(f"{name} values must be fractions in [0, 1].")
