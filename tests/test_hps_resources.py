from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

import fspm_optics.fixtures.hps.resources as hps_resources
from fspm_optics.fixtures.hps import (
    HPS_IES_RESOURCE_NAME,
    HPS_IES_SHA256,
    HPS_RESOURCE_HASHES,
    HPS_SPD_RESOURCE_NAME,
    HPS_SPD_SHA256,
    HpsResourceError,
    hps_resource_bytes,
    load_hps_spd,
)

EXPECTED_HPS_SPD_NAME = "hps_1000w_spd.csv"
EXPECTED_HPS_IES_NAME = "hps_1000w.ies"
EXPECTED_HPS_IES_SHA256 = (
    "05f449b6d5762fd1ae459a41df2cad510a680d5d9bbb9639ca0a26d3609002a3"
)
EXPECTED_HPS_SPD_SHA256 = (
    "d27fffb2410d30ed38c7fde6851563e0ddba6a8de824ecefe8c7624038e5e133"
)


def test_hps_resources_load_by_package_identity_and_match_approved_hashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert dict(HPS_RESOURCE_HASHES) == {
        HPS_IES_RESOURCE_NAME: HPS_IES_SHA256,
        HPS_SPD_RESOURCE_NAME: HPS_SPD_SHA256,
    }
    assert HPS_SPD_RESOURCE_NAME == EXPECTED_HPS_SPD_NAME
    assert HPS_IES_RESOURCE_NAME == EXPECTED_HPS_IES_NAME
    assert HPS_IES_SHA256 == EXPECTED_HPS_IES_SHA256
    assert HPS_SPD_SHA256 == EXPECTED_HPS_SPD_SHA256
    assert "unknown_hps_resource.csv" not in HPS_RESOURCE_HASHES
    with pytest.raises(KeyError, match="unknown explicit HPS resource"):
        hps_resource_bytes("unknown_hps_resource.csv")
    for resource_name, expected in HPS_RESOURCE_HASHES.items():
        assert hashlib.sha256(hps_resource_bytes(resource_name)).hexdigest() == expected


def test_hps_resource_override_remains_byte_verified(tmp_path: Path) -> None:
    (tmp_path / HPS_IES_RESOURCE_NAME).write_bytes(
        hps_resource_bytes(HPS_IES_RESOURCE_NAME) + b"changed"
    )
    with pytest.raises(HpsResourceError, match="hash mismatch"):
        hps_resource_bytes(HPS_IES_RESOURCE_NAME, data_root=tmp_path)


def test_hps_spd_uses_shared_strict_relative_radiant_schema() -> None:
    spd = load_hps_spd()
    raw = hps_resource_bytes(HPS_SPD_RESOURCE_NAME)

    assert spd.resource_name == HPS_SPD_RESOURCE_NAME
    assert spd.sha256 == HPS_SPD_SHA256
    assert spd.wavelength_nm == tuple(float(value) for value in range(380, 781))
    assert raw.splitlines()[0] == b"wavelength_nm,relative_spd"
    assert len(raw.splitlines()[1:]) == 401
    assert all(math.isfinite(value) and value >= 0.0 for value in spd.relative_spd)
    assert min(spd.relative_spd) == 0.0
    assert max(spd.relative_spd) == 1.0
    assert spd.relative_spd[570 - 380] == 1.0


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("nm,relative_spd\n380,1\n", "headers wavelength_nm, relative_spd"),
        (
            "wavelength_nm,relative_spd\n380,0\n381,1\n",
            "exactly 401 ordered 1 nm samples",
        ),
        (
            "wavelength_nm,relative_spd\n380,-1\n",
            "relative_spd must be finite and non-negative",
        ),
        (
            "wavelength_nm,relative_spd\n380,nan\n",
            "relative_spd must be finite and non-negative",
        ),
    ],
)
def test_hps_spd_contract_fails_closed_after_byte_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    text: str,
    message: str,
) -> None:
    raw = text.encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    (tmp_path / HPS_SPD_RESOURCE_NAME).write_bytes(raw)
    monkeypatch.setattr(hps_resources, "HPS_SPD_SHA256", digest)
    monkeypatch.setattr(
        hps_resources,
        "HPS_RESOURCE_HASHES",
        {HPS_SPD_RESOURCE_NAME: digest},
    )

    with pytest.raises(HpsResourceError, match=message):
        hps_resources.load_hps_spd(data_root=tmp_path)
