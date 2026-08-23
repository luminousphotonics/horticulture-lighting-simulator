"""Strict source-neutral relative radiant SPD loading."""

from __future__ import annotations

from dataclasses import dataclass
import csv
import hashlib
from io import StringIO
import math
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True, slots=True)
class RelativeRadiantSpd:
    """One relative radiant-power shape on a strictly increasing grid."""

    resource_name: str
    wavelength_nm: tuple[float, ...]
    relative_spd: tuple[float, ...]
    sha256: str

    def __post_init__(self) -> None:
        if not self.resource_name:
            raise ValueError("SPD resource_name must be non-empty.")
        if len(self.wavelength_nm) != len(self.relative_spd) or not self.wavelength_nm:
            raise ValueError("SPD wavelengths and values must have equal non-zero lengths.")
        wavelengths = tuple(float(value) for value in self.wavelength_nm)
        values = tuple(float(value) for value in self.relative_spd)
        if any(not math.isfinite(value) or value <= 0.0 for value in wavelengths):
            raise ValueError("SPD wavelengths must be finite and positive.")
        if any(
            current <= previous
            for previous, current in zip(wavelengths, wavelengths[1:])
        ):
            raise ValueError("SPD wavelengths must be strictly increasing.")
        if any(not math.isfinite(value) or value < 0.0 for value in values):
            raise ValueError("SPD values must be finite and non-negative.")
        if len(self.sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.sha256
        ):
            raise ValueError("SPD sha256 must be a lowercase hexadecimal SHA-256.")
        object.__setattr__(self, "wavelength_nm", wavelengths)
        object.__setattr__(self, "relative_spd", values)


def parse_relative_radiant_spd_csv(
    text: str,
    *,
    resource_name: str,
    sha256: str | None = None,
    source: str | None = None,
) -> RelativeRadiantSpd:
    """Parse the shared two-column SPD schema from in-memory text."""

    if not isinstance(text, str):
        raise TypeError("relative radiant SPD CSV text must be a string.")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest() if sha256 is None else sha256
    with StringIO(text, newline="") as handle:
        wavelengths, values = _read_strict_spd_rows(
            handle,
            source=source or resource_name,
        )
    return RelativeRadiantSpd(
        resource_name=resource_name,
        wavelength_nm=wavelengths,
        relative_spd=values,
        sha256=digest,
    )


def load_relative_radiant_spd_csv(path: str | Path) -> RelativeRadiantSpd:
    """Load the shared strict SPD schema from a standalone file."""

    source = Path(path)
    try:
        raw = source.read_bytes()
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"relative radiant SPD CSV not found: {source}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"relative radiant SPD CSV is not UTF-8: {source}") from exc
    return parse_relative_radiant_spd_csv(
        text,
        resource_name=source.name,
        sha256=hashlib.sha256(raw).hexdigest(),
        source=str(source),
    )


def _read_strict_spd_rows(
    handle: TextIO,
    *,
    source: str,
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    reader = csv.reader(handle)
    try:
        raw_header = next(reader)
    except StopIteration as exc:
        raise ValueError(f"relative radiant SPD CSV is empty: {source}") from exc
    header = tuple(value.strip() for value in raw_header)
    if header != ("wavelength_nm", "relative_spd"):
        raise ValueError(
            f"relative radiant SPD CSV {source} must have exactly the headers "
            "wavelength_nm, relative_spd."
        )

    wavelengths: list[float] = []
    values: list[float] = []
    for row_number, row in enumerate(reader, start=2):
        if len(row) != 2 or any(not value.strip() for value in row):
            raise ValueError(
                f"Malformed SPD row {row_number} in {source}: expected two values."
            )
        try:
            wavelength = float(row[0])
            value = float(row[1])
        except ValueError as exc:
            raise ValueError(
                f"Malformed SPD row {row_number} in {source}: values must be numeric."
            ) from exc
        if not math.isfinite(wavelength) or wavelength <= 0.0:
            raise ValueError(
                f"Malformed SPD row {row_number} in {source}: "
                "wavelength_nm must be finite and positive."
            )
        if not math.isfinite(value) or value < 0.0:
            raise ValueError(
                f"Malformed SPD row {row_number} in {source}: "
                "relative_spd must be finite and non-negative."
            )
        if wavelengths and wavelength <= wavelengths[-1]:
            raise ValueError(
                f"Malformed SPD row {row_number} in {source}: "
                "wavelength_nm must be strictly increasing."
            )
        wavelengths.append(wavelength)
        values.append(value)
    if not wavelengths:
        raise ValueError(f"relative radiant SPD CSV contains no data rows: {source}")
    return tuple(wavelengths), tuple(values)
