from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from fspm_optics.sources.smd.spd import (
    SMD_RESOURCE_NAMES,
    load_smd_spd,
    load_spd_csv,
    smd_resource_path,
)

AUDIT_SHA256 = {
    "smd_3000k_spd.csv": (
        "68797d94ab11251ea777dfdf98bf478baa25fcd2874e349a1173e2b57bab03c1"
    ),
    "smd_5000k_spd.csv": (
        "630acb83a1f83fd1723621671a83b502fc933dabcb6af109dd0b9578e5fe698b"
    ),
    "smd_660nm_spd.csv": (
        "5a1f911fd6596e2ffd91e691561e08a245277f621617fbca51fd8df5160cd6de"
    ),
}


def test_all_explicit_smd_package_resources_exist_and_match_audit_hashes() -> None:
    assert set(SMD_RESOURCE_NAMES) == set(AUDIT_SHA256)
    for resource_name in SMD_RESOURCE_NAMES:
        path = smd_resource_path(resource_name)
        assert path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == AUDIT_SHA256[
            resource_name
        ]


def test_actual_spd_resources_load_with_strict_schema() -> None:
    warm = load_smd_spd("smd_3000k_spd.csv")
    cool = load_smd_spd("smd_5000k_spd.csv")
    red = load_smd_spd("smd_660nm_spd.csv")

    assert len(warm.wavelength_nm) == 401
    assert len(cool.wavelength_nm) == 401
    assert len(red.wavelength_nm) == 101
    assert warm.sha256 == AUDIT_SHA256[warm.resource_name]
    assert cool.sha256 == AUDIT_SHA256[cool.resource_name]
    assert red.sha256 == AUDIT_SHA256[red.resource_name]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("wavelength,relative_spd\n400,1\n", "exactly the headers"),
        ("wavelength_nm,relative_spd\n", "contains no data rows"),
        ("wavelength_nm,relative_spd\n400\n", "expected two values"),
        ("wavelength_nm,relative_spd\nabc,1\n", "values must be numeric"),
        ("wavelength_nm,relative_spd\n0,1\n", "finite and positive"),
        ("wavelength_nm,relative_spd\n400,-0.1\n", "finite and non-negative"),
        ("wavelength_nm,relative_spd\n400,nan\n", "finite and non-negative"),
        (
            "wavelength_nm,relative_spd\n401,1\n400,0.5\n",
            "strictly increasing",
        ),
    ],
)
def test_strict_spd_loader_rejects_malformed_csv(
    tmp_path: Path,
    text: str,
    message: str,
) -> None:
    source = tmp_path / "malformed.csv"
    source.write_text(text, encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_spd_csv(source)
