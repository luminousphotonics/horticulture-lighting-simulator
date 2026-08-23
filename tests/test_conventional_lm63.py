from __future__ import annotations

import hashlib
import json

import pytest

from fspm_optics.fixtures.conventional_led.lm63 import (
    Lm63ParseError,
    load_approved_lm63,
    parse_lm63,
)
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_IES_RESOURCE_NAME,
    conventional_resource_bytes,
)


EXPECTED_TILT_SUFFIX_SHA256 = (
    "2e777de00a1dff732153a71861ffe46480c0981e3287c7ffe05c06067f095299"
)
EXPECTED_NUMERICAL_PHOTOMETRY_SHA256 = (
    "6c27b16b92be7ff9ee944cd74dd84da6818051845f988ca7074ccd4103378948"
)


def _numerical_photometry_sha256(photometry) -> str:
    payload = {
        name: getattr(photometry, name)
        for name in (
            "lamp_count",
            "lumens_per_lamp",
            "candela_multiplier",
            "vertical_angle_count",
            "horizontal_angle_count",
            "photometric_type",
            "units_type",
            "fixture_width_m",
            "fixture_length_m",
            "fixture_height_m",
            "source_fixture_width",
            "source_fixture_length",
            "source_fixture_height",
            "ballast_factor",
            "future_use_factor",
            "input_watts",
            "vertical_angles_deg",
            "horizontal_angles_deg",
            "candela_by_horizontal_plane",
        )
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _synthetic_lm63(
    *,
    tilt: str = "NONE",
    photometric_type: int = 1,
    units_type: int = 2,
    ballast_factor: float = 1.0,
    future_use_factor: float = 1.1,
    vertical_angles: str = "0 90 180",
    horizontal_angles: str = "0 180 360",
    candela: str = "4 2 0  2 1 0  4 2 0",
    trailing: str = "",
) -> str:
    return (
        "IESNA:LM-63-2019\n"
        "[TEST] SYNTHETIC\n"
        "[MANUFAC] TEST\n"
        f"TILT={tilt}\n"
        f"1 -1 1 3 3 {photometric_type} {units_type} 1 1 0.1\n"
        f"{ballast_factor} {future_use_factor} 100\n"
        f"{vertical_angles}\n"
        f"{horizontal_angles}\n"
        f"{candela}{trailing}\n"
    )


def test_approved_lm63_preserves_complete_structure_and_generic_metadata() -> None:
    photometry = load_approved_lm63()

    assert photometry.version == "IESNA:LM-63-2019"
    assert photometry.tilt == "NONE"
    assert photometry.keyword("TEST") == "GENERIC-CONVENTIONAL-LED-PHOTOMETRY"
    assert photometry.keyword("MANUFAC") == "GENERIC CONVENTIONAL LED"
    assert photometry.keyword("LUMCAT") == "CONVENTIONAL-LED-8-BAR"
    for keyword in (
        "ISSUEDATE",
        "_VOLTAGE",
        "_CURRENT",
        "_POWERFACTOR",
        "_THD",
        "TESTLAB",
        "_TESTINST",
        "_TESTDIST",
        "_TESTTEMPERATURE",
        "BALLAST",
        "BALLASTCAT",
    ):
        assert photometry.keyword(keyword) == "NOT DISCLOSED"
    assert photometry.lamp_count == 1
    assert photometry.lumens_per_lamp == -1.0
    assert photometry.candela_multiplier == 1.0
    assert photometry.ballast_factor == 1.0
    assert photometry.future_use_factor == 1.1
    assert photometry.input_watts == 663.20
    assert photometry.photometric_type == 1
    assert photometry.units_type == 2
    assert (
        photometry.fixture_width_m,
        photometry.fixture_length_m,
        photometry.fixture_height_m,
    ) == (1.087, 1.190, 0.108)
    assert photometry.vertical_angle_count == 181
    assert photometry.horizontal_angle_count == 17
    assert photometry.candela_value_count == 3077
    assert len(photometry.candela_by_horizontal_plane) == 17
    assert all(len(plane) == 181 for plane in photometry.candela_by_horizontal_plane)
    assert photometry.vertical_angles_deg == tuple(float(value) for value in range(181))
    assert photometry.horizontal_angles_deg == tuple(
        22.5 * value for value in range(17)
    )
    assert photometry.has_duplicate_horizontal_closure
    assert _numerical_photometry_sha256(photometry) == (
        EXPECTED_NUMERICAL_PHOTOMETRY_SHA256
    )


def test_approved_lm63_preserves_exact_binary_numerical_suffix() -> None:
    raw = conventional_resource_bytes(CONVENTIONAL_IES_RESOURCE_NAME)
    suffix = raw[raw.index(b"TILT=NONE") :]

    assert hashlib.sha256(suffix).hexdigest() == EXPECTED_TILT_SUFFIX_SHA256
    assert len(suffix) == 23814
    assert raw.count(b"\r\n") == raw.count(b"\n") == 41
    assert b"\n" not in raw.replace(b"\r\n", b"")


def test_approved_lm63_header_contains_no_identifying_metadata() -> None:
    raw = conventional_resource_bytes(CONVENTIONAL_IES_RESOURCE_NAME)
    header = raw[: raw.index(b"TILT=NONE")]
    forbidden = (
        b"BLC" + b"2107022E-G-R",
        b"Q" + b"UBE ENVISION",
        b"QB" + b"-FSG-8B-7T660W",
        b"Bell" + b"ing Test Laboratory",
        b"GPM" + b"-3000",
        b"2021-8-12",
        b"14.140 m",
        b"25.2 'C",
    )
    assert all(value not in header for value in forbidden)


def test_approved_lm63_preserves_measured_c_plane_asymmetry() -> None:
    photometry = load_approved_lm63()

    assert photometry.candela_by_horizontal_plane[0] != (
        photometry.candela_by_horizontal_plane[8]
    )
    assert photometry.candela_by_horizontal_plane[4] != (
        photometry.candela_by_horizontal_plane[12]
    )
    assert photometry.candela_by_horizontal_plane[0] == (
        photometry.candela_by_horizontal_plane[-1]
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_synthetic_lm63(tilt="INCLUDE"), "only TILT=NONE"),
        (_synthetic_lm63(photometric_type=2), "Type C"),
        (_synthetic_lm63(units_type=1), "metric units"),
        (_synthetic_lm63(vertical_angles="0 100 90"), "strictly increasing"),
        (_synthetic_lm63(horizontal_angles="0 360 180"), "strictly increasing"),
        (_synthetic_lm63(candela="4 2 0  2 -1 0  4 2 0"), "non-negative"),
        (_synthetic_lm63(candela="4 2 0  2 nan 0  4 2 0"), "non-finite"),
        (_synthetic_lm63(candela="4 2 0  2 1 0  4 3 0"), "closure plane"),
        (_synthetic_lm63(candela="4 2 0  2 1 0"), "missing numeric values"),
        (_synthetic_lm63(trailing=" 7"), "excess numeric values"),
        (
            _synthetic_lm63().replace("1 -1 1 3 3 1 2", "1 -1 1 3.5 3 1 2"),
            "angle count must be an integer",
        ),
        (
            _synthetic_lm63().replace("1 -1 1 3 3 1 2", "1 -2 1 3 3 1 2"),
            "lumens per lamp",
        ),
        (
            _synthetic_lm63().replace("1 -1 1 3 3 1 2", "1 -1 -1 3 3 1 2"),
            "candela multiplier",
        ),
        (_synthetic_lm63(ballast_factor=0.0), "factors must be positive"),
    ],
)
def test_strict_lm63_subset_rejects_unsupported_or_malformed_inputs(
    text: str,
    message: str,
) -> None:
    with pytest.raises(Lm63ParseError, match=message):
        parse_lm63(text)


def test_strict_lm63_rejects_missing_tilt_and_unsupported_version() -> None:
    without_tilt = _synthetic_lm63().replace("TILT=NONE\n", "")
    with pytest.raises(Lm63ParseError, match="TILT declaration is missing"):
        parse_lm63(without_tilt)

    unsupported = _synthetic_lm63().replace("LM-63-2019", "LM-63-2002")
    with pytest.raises(Lm63ParseError, match="unsupported LM-63 version"):
        parse_lm63(unsupported)
