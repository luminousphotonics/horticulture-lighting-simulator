"""Byte-verified installed-package resources for the HPS comparator."""

from __future__ import annotations

import hashlib
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Final, Mapping

from fspm_optics.spectral.spd import RelativeRadiantSpd, parse_relative_radiant_spd_csv

from .errors import HpsResourceError

HPS_IES_RESOURCE_NAME: Final = "hps_1000w.ies"
HPS_SPD_RESOURCE_NAME: Final = "hps_1000w_spd.csv"
HPS_IES_SHA256: Final = (
    "05f449b6d5762fd1ae459a41df2cad510a680d5d9bbb9639ca0a26d3609002a3"
)
HPS_SPD_SHA256: Final = (
    "d27fffb2410d30ed38c7fde6851563e0ddba6a8de824ecefe8c7624038e5e133"
)
HPS_SPD_START_NM: Final = 380
HPS_SPD_END_NM: Final = 780
HPS_SPD_STEP_NM: Final = 1
HPS_SPD_ROW_COUNT: Final = 401
HPS_RESOURCE_HASHES: Final[Mapping[str, str]] = MappingProxyType(
    {
        HPS_IES_RESOURCE_NAME: HPS_IES_SHA256,
        HPS_SPD_RESOURCE_NAME: HPS_SPD_SHA256,
    }
)
HPS_RESOURCE_NAMES: Final = tuple(HPS_RESOURCE_HASHES)
_RESOURCE_PARTS: Final = ("resources", "data", "hps")


def hps_resource_bytes(
    resource_name: str,
    *,
    data_root: str | Path | None = None,
) -> bytes:
    """Read one explicitly named HPS resource and verify its approved hash."""

    if resource_name not in HPS_RESOURCE_HASHES:
        raise KeyError(f"unknown explicit HPS resource: {resource_name!r}")
    try:
        if data_root is None:
            node = resources.files("fspm_optics").joinpath(*_RESOURCE_PARTS, resource_name)
            raw = node.read_bytes()
        else:
            raw = (Path(data_root).expanduser() / resource_name).read_bytes()
    except (FileNotFoundError, ModuleNotFoundError) as exc:
        raise HpsResourceError(
            f"required packaged HPS resource not found: {resource_name}"
        ) from exc
    actual = hashlib.sha256(raw).hexdigest()
    expected = HPS_RESOURCE_HASHES[resource_name]
    if actual != expected:
        raise HpsResourceError(
            f"HPS resource hash mismatch for {resource_name}: "
            f"expected {expected}, got {actual}."
        )
    return raw


def load_hps_spd(
    *,
    data_root: str | Path | None = None,
) -> RelativeRadiantSpd:
    """Load the approved relative spectral shape without absolute calibration."""

    raw = hps_resource_bytes(HPS_SPD_RESOURCE_NAME, data_root=data_root)
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HpsResourceError("approved HPS SPD must be UTF-8 text.") from exc
    try:
        spd = parse_relative_radiant_spd_csv(
            text,
            resource_name=HPS_SPD_RESOURCE_NAME,
            sha256=HPS_SPD_SHA256,
            source=f"packaged:{HPS_SPD_RESOURCE_NAME}",
        )
    except ValueError as exc:
        raise HpsResourceError(str(exc)) from exc
    expected_wavelengths = tuple(
        float(value)
        for value in range(
            HPS_SPD_START_NM,
            HPS_SPD_END_NM + 1,
            HPS_SPD_STEP_NM,
        )
    )
    if (
        len(spd.wavelength_nm) != HPS_SPD_ROW_COUNT
        or spd.wavelength_nm != expected_wavelengths
    ):
        raise HpsResourceError(
            "approved HPS SPD must contain exactly 401 ordered 1 nm samples "
            "from 380 through 780 nm."
        )
    return spd
