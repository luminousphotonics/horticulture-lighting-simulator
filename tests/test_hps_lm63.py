from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

import pytest

from fspm_optics.fixtures.conventional_led.lm63 import (
    Lm63ParseError as ConventionalLm63ParseError,
    load_approved_lm63,
    parse_lm63 as parse_conventional_lm63,
)
from fspm_optics.fixtures.conventional_led.profile import (
    CONVENTIONAL_COMPARISON_PROFILE_ID,
    CONVENTIONAL_SOURCE_ID,
    build_conventional_comparison_profile,
)
from fspm_optics.fixtures.conventional_led.resources import (
    CONVENTIONAL_IES_RESOURCE_NAME,
    conventional_resource_bytes,
)
from fspm_optics.fixtures.hps import (
    HPS_IES_RESOURCE_NAME,
    hps_resource_bytes,
    load_hps_lm63,
)
from fspm_optics.photometry.lm63 import Lm63ParseError, parse_lm63


def _shared_synthetic(
    *,
    version: str = "IESNA:LM-63-2002",
    units_type: int = 1,
    height: float = 0.0,
    vertical: str = "0 45 90",
    horizontal: str = "0 45 90",
    candela: str = "4 2 0  3 1 0  2 1 0",
) -> str:
    return (
        f"{version}\n"
        "[TEST]SYNTHETIC\n"
        "TILT=NONE\n"
        f"1 1000 1 3 3 1 {units_type} 1.98 2.62 {height}\n"
        "1 1 1045\n"
        f"{vertical}\n"
        f"{horizontal}\n"
        f"{candela}\n"
    )


def test_approved_hps_lm63_uses_sanitized_metadata_and_converts_feet_to_metres() -> None:
    photometry = load_hps_lm63()

    assert photometry.version == "IESNA:LM-63-2002"
    assert photometry.keyword("TEST") == "ANONYMIZED HPS PHOTOMETRY"
    assert photometry.keyword("TESTLAB") == "ANONYMIZED"
    assert photometry.keyword("ISSUEDATE") == "ANONYMIZED"
    assert photometry.keyword("MANUFAC") == "GENERIC HPS REFERENCE"
    assert photometry.keyword("LUMCAT") == "HPS-1000W"
    assert photometry.keyword("LUMINAIRE") == "Generic reflector housing"
    assert photometry.keyword("LAMP") == "Generic 1000 W high-pressure sodium lamp"
    assert photometry.keyword("OTHER") == "Generic 1000 W electronic ballast"
    assert photometry.units_type == 1
    assert (
        photometry.source_fixture_width,
        photometry.source_fixture_length,
        photometry.source_fixture_height,
    ) == (1.98, 2.62, 0.0)
    assert (
        photometry.fixture_width_m,
        photometry.fixture_length_m,
        photometry.fixture_height_m,
    ) == (0.603504, 0.798576, 0.0)
    assert photometry.vertical_angle_count == 37
    assert photometry.horizontal_angle_count == 5
    assert photometry.candela_value_count == 185
    assert photometry.is_quadrant_symmetric_type_c
    assert photometry.is_downward_only
    assert not photometry.has_duplicate_horizontal_closure


def test_hps_lm63_suffix_and_numerical_payload_are_immutable() -> None:
    raw = hps_resource_bytes(HPS_IES_RESOURCE_NAME)
    marker = b"TILT=NONE"
    assert raw.count(marker) == 1
    prefix, suffix = raw.split(marker, 1)
    suffix = marker + suffix

    assert hashlib.sha256(suffix).hexdigest() == (
        "fe8b413c6d2da88c1b00b7d296c55f18c5dddbe3c8f9951169b2598cc42eef14"
    )
    assert b"\n" not in raw.replace(b"\r\n", b"")
    assert prefix == (
        b"IESNA:LM-63-2002\r\n"
        b"[TEST]ANONYMIZED HPS PHOTOMETRY\r\n"
        b"[TESTLAB]ANONYMIZED\r\n"
        b"[ISSUEDATE]ANONYMIZED\r\n"
        b"[MANUFAC]GENERIC HPS REFERENCE\r\n"
        b"[LUMCAT]HPS-1000W\r\n"
        b"[LUMINAIRE]Generic reflector housing\r\n"
        b"[LAMP]Generic 1000 W high-pressure sodium lamp\r\n"
        b"[OTHER]Generic 1000 W electronic ballast\r\n"
    )

    numerical_payload = asdict(load_hps_lm63())
    numerical_payload.pop("keywords")
    canonical = json.dumps(
        numerical_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")
    assert hashlib.sha256(canonical).hexdigest() == (
        "198279a371673c8e16f01d4dc7831949e4b9a2c1db1d2c7615ce8012da7b7bd1"
    )


def test_decimal_dimension_tokens_canonicalize_before_public_float_conversion() -> None:
    parsed = parse_lm63(_shared_synthetic())
    equivalent_spelling = parse_lm63(
        _shared_synthetic().replace("1.98 2.62 0.0", "1.9800 2.6200 0.000")
    )

    assert parsed.fixture_width_m == 0.603504
    assert parsed.fixture_length_m == 0.798576
    assert parsed.fixture_height_m == 0.0
    assert (
        parsed.fixture_width_m,
        parsed.fixture_length_m,
        parsed.fixture_height_m,
    ) == (
        equivalent_spelling.fixture_width_m,
        equivalent_spelling.fixture_length_m,
        equivalent_spelling.fixture_height_m,
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (_shared_synthetic(version="IESNA:LM-63-1995"), "unsupported LM-63 version"),
        (_shared_synthetic(units_type=3), "unsupported LM-63 units"),
        (_shared_synthetic(height=-0.1), "height must be non-negative"),
        (_shared_synthetic(vertical="0 90 45"), "strictly increasing"),
        (_shared_synthetic(horizontal="0 90 45"), "strictly increasing"),
        (_shared_synthetic(candela="4 2 0  3 -1 0  2 1 0"), "non-negative"),
        (_shared_synthetic(candela="4 2 0  3 nan 0  2 1 0"), "non-finite"),
        (_shared_synthetic(candela="4 2 0  3 1 0"), "missing numeric values"),
    ],
)
def test_shared_lm63_2002_subset_rejects_malformed_inputs(text: str, message: str) -> None:
    with pytest.raises(Lm63ParseError, match=message):
        parse_lm63(text)


def test_conventional_compatibility_adapter_retains_its_stricter_public_subset() -> None:
    with pytest.raises(ConventionalLm63ParseError, match="unsupported LM-63 version"):
        parse_conventional_lm63(_shared_synthetic())

    conventional = _shared_synthetic(
        version="IESNA:LM-63-2019",
        units_type=2,
        height=0.1,
        vertical="0 90 180",
        horizontal="0 180 360",
        candela="4 2 0  2 1 0  4 2 0",
    )
    parsed = parse_conventional_lm63(conventional)
    assert parsed.vertical_angles_deg == (0.0, 90.0, 180.0)
    assert parsed.horizontal_angles_deg == (0.0, 180.0, 360.0)
    assert parsed.has_duplicate_horizontal_closure


def test_conventional_adapter_results_and_scientific_identities_are_unchanged() -> None:
    raw = conventional_resource_bytes(CONVENTIONAL_IES_RESOURCE_NAME).decode("ascii")
    shared = parse_lm63(raw, source="shared-conventional-compatibility-test")
    adapted = load_approved_lm63()
    profile = build_conventional_comparison_profile()

    assert adapted == shared
    assert (
        adapted.fixture_width_m,
        adapted.fixture_length_m,
        adapted.fixture_height_m,
    ) == (1.087, 1.190, 0.108)
    assert adapted.applicable_uniform_factor == 1.1
    assert profile.profile_id == CONVENTIONAL_COMPARISON_PROFILE_ID == (
        "rated_conventional_led_8_bar_shape_660w_2p6_v2"
    )
    assert profile.source_id == CONVENTIONAL_SOURCE_ID == (
        "conventional_led_rated_660w_source_v2"
    )
    assert (
        profile.modeled_operating_point.electrical_power_w_per_fixture,
        profile.modeled_operating_point.par_ppe_umol_per_j,
        profile.modeled_operating_point.par_ppf_umol_s_per_fixture,
    ) == (660.0, 2.6, 1716.0)
