from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fspm_optics.fixtures.conventional_led.errors import ConventionalResourceError
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_IES_SHA256,
    CONVENTIONAL_RESOURCE_HASHES,
    CONVENTIONAL_SPD_RESOURCE_NAME,
    CONVENTIONAL_SPD_ROW_COUNT,
    CONVENTIONAL_SPD_SHA256,
    CONVENTIONAL_SPD_ZERO_TAIL_START_NM,
    conventional_resource_bytes,
    load_conventional_spd,
)

def test_packaged_resources_load_by_package_identity_and_match_approved_hashes(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    assert dict(CONVENTIONAL_RESOURCE_HASHES) == {
        CONVENTIONAL_IES_RESOURCE_NAME: CONVENTIONAL_IES_SHA256,
        CONVENTIONAL_SPD_RESOURCE_NAME: CONVENTIONAL_SPD_SHA256,
    }
    assert CONVENTIONAL_IES_RESOURCE_NAME == "conventional_led_8_bar.ies"
    assert CONVENTIONAL_SPD_RESOURCE_NAME == "conventional_led_spd.csv"
    for resource_name, expected_hash in CONVENTIONAL_RESOURCE_HASHES.items():
        raw = conventional_resource_bytes(resource_name)
        assert hashlib.sha256(raw).hexdigest() == expected_hash


def test_explicit_resource_override_is_still_hash_checked(tmp_path: Path) -> None:
    corrupted = conventional_resource_bytes(CONVENTIONAL_IES_RESOURCE_NAME) + b"changed"
    (tmp_path / CONVENTIONAL_IES_RESOURCE_NAME).write_bytes(corrupted)

    with pytest.raises(ConventionalResourceError, match="hash mismatch"):
        conventional_resource_bytes(
            CONVENTIONAL_IES_RESOURCE_NAME,
            data_root=tmp_path,
        )


def test_legacy_vendor_resource_names_are_not_accepted() -> None:
    legacy_names = (
        "qu" + "be_660w_8bar.ies",
        "qu" + "be_660w_spd.csv",
    )
    for legacy_name in legacy_names:
        with pytest.raises(KeyError, match="unknown explicit Conventional resource"):
            conventional_resource_bytes(legacy_name)


def test_user_replacement_spd_contract_hash_and_explicit_zero_tail() -> None:
    raw = conventional_resource_bytes(CONVENTIONAL_SPD_RESOURCE_NAME)
    spd = load_conventional_spd()

    assert raw.splitlines()[0] == b"wavelength_nm,relative_spd"
    assert spd.sha256 == CONVENTIONAL_SPD_SHA256
    assert len(spd.wavelength_nm) == CONVENTIONAL_SPD_ROW_COUNT == 401
    assert spd.wavelength_nm == tuple(float(value) for value in range(380, 781))
    assert all(value >= 0.0 for value in spd.relative_spd)
    tail = tuple(
        value
        for wavelength, value in zip(
            spd.wavelength_nm, spd.relative_spd, strict=True
        )
        if wavelength >= CONVENTIONAL_SPD_ZERO_TAIL_START_NM
    )
    assert len(tail) == 41
    assert tail == (0.0,) * 41
