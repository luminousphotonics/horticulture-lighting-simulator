"""Byte-verified installed-package resources for the Conventional comparator."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping

from fspm_optics.spectral.spd import (
    RelativeRadiantSpd,
    parse_relative_radiant_spd_csv,
)

from .errors import ConventionalResourceError, ConventionalSpdError

CONVENTIONAL_IES_RESOURCE_NAME: Final = "conventional_led_8_bar.ies"
CONVENTIONAL_SPD_RESOURCE_NAME: Final = "conventional_led_spd.csv"
CONVENTIONAL_RESOURCE_NAMES: Final = (
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_SPD_RESOURCE_NAME,
)
CONVENTIONAL_IES_SHA256: Final = (
    "67d6f93b40638b5a0534fe34fa43d78811c0c0476e1e367490a38157e4ba1cf4"
)
CONVENTIONAL_SPD_SHA256: Final = (
    "1820512df69f93d7325b08e27887ab2e8155efa9d536bcd06b9f1c739bf57e44"
)
CONVENTIONAL_SPD_START_NM: Final = 380
CONVENTIONAL_SPD_END_NM: Final = 780
CONVENTIONAL_SPD_STEP_NM: Final = 1
CONVENTIONAL_SPD_ROW_COUNT: Final = 401
CONVENTIONAL_SPD_ZERO_TAIL_START_NM: Final = 740
CONVENTIONAL_RESOURCE_HASHES: Final[Mapping[str, str]] = MappingProxyType(
    {
        CONVENTIONAL_IES_RESOURCE_NAME: CONVENTIONAL_IES_SHA256,
        CONVENTIONAL_SPD_RESOURCE_NAME: CONVENTIONAL_SPD_SHA256,
    }
)
_RESOURCE_PARTS: Final = ("resources", "data", "conventional_led")


def conventional_resource_bytes(
    resource_name: str,
    *,
    data_root: str | Path | None = None,
) -> bytes:
    """Read and hash-check one explicitly named production resource."""

    if resource_name not in CONVENTIONAL_RESOURCE_NAMES:
        raise KeyError(f"unknown explicit Conventional resource: {resource_name!r}")
    try:
        if data_root is None:
            node = resources.files("fspm_optics").joinpath(*_RESOURCE_PARTS, resource_name)
            raw = node.read_bytes()
        else:
            path = Path(data_root).expanduser() / resource_name
            raw = path.read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise ConventionalResourceError(
            f"required packaged Conventional resource not found: {resource_name}"
        ) from exc
    actual = hashlib.sha256(raw).hexdigest()
    expected = CONVENTIONAL_RESOURCE_HASHES[resource_name]
    if actual != expected:
        raise ConventionalResourceError(
            f"Conventional resource hash mismatch for {resource_name}: "
            f"expected {expected}, got {actual}."
        )
    return raw


def load_conventional_spd(
    *,
    data_root: str | Path | None = None,
) -> RelativeRadiantSpd:
    """Load the approved relative radiant shape without absolute calibration."""

    raw = conventional_resource_bytes(
        CONVENTIONAL_SPD_RESOURCE_NAME,
        data_root=data_root,
    )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ConventionalSpdError(
            "approved Conventional SPD must be UTF-8 text."
        ) from exc
    try:
        spd = parse_relative_radiant_spd_csv(
            text,
            resource_name=CONVENTIONAL_SPD_RESOURCE_NAME,
            sha256=CONVENTIONAL_SPD_SHA256,
            source=f"packaged:{CONVENTIONAL_SPD_RESOURCE_NAME}",
        )
    except ValueError as exc:
        raise ConventionalSpdError(str(exc)) from exc
    expected_wavelengths = tuple(
        float(value)
        for value in range(
            CONVENTIONAL_SPD_START_NM,
            CONVENTIONAL_SPD_END_NM + 1,
            CONVENTIONAL_SPD_STEP_NM,
        )
    )
    if (
        len(spd.wavelength_nm) != CONVENTIONAL_SPD_ROW_COUNT
        or spd.wavelength_nm != expected_wavelengths
    ):
        raise ConventionalSpdError(
            "approved Conventional SPD must contain exactly 401 ordered 1 nm "
            "samples from 380 through 780 nm."
        )
    tail = tuple(
        value
        for wavelength, value in zip(
            spd.wavelength_nm, spd.relative_spd, strict=True
        )
        if wavelength >= CONVENTIONAL_SPD_ZERO_TAIL_START_NM
    )
    if len(tail) != 41 or any(value != 0.0 for value in tail):
        raise ConventionalSpdError(
            "approved Conventional SPD missing tail must remain explicit zero "
            "from 740 through 780 nm; extrapolated values are prohibited."
        )
    return spd
