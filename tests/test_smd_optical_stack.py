from __future__ import annotations

import math

import pytest

from fspm_optics.fixtures.smd.module_profile import DEFAULT_SMD_MODULE_PROFILE
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    APERTURE_SIDE_M,
    BEZEL_OUTER_SIDE_M,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    EMITTER_SIDE_M,
    EMITTER_Z_M,
    INTERNAL_SOURCE_PPE_UMOL_PER_J,
    PMMA_SIDE_M,
    PMMA_THICKNESS_M,
    PROPOSED_FIXTURE_OPTICAL_STACK_ID,
    PTFE_PHYSICAL_THICKNESS_M,
    box_radiance_text,
    proposed_fixture_materials_radiance_text,
    proposed_fixture_stack_radiance_text,
)
from fspm_optics.fixtures.smd.source_variants import (
    get_smd_source_variant,
    source_variant_cal_text,
)


def _vertices(text: str, primitive: str) -> tuple[tuple[float, float, float], ...]:
    lines = text.splitlines()
    index = next(i for i, line in enumerate(lines) if line.endswith(f" polygon {primitive}"))
    count = int(lines[index + 3]) // 3
    return tuple(
        tuple(float(value) for value in lines[index + 4 + i].split())  # type: ignore[misc]
        for i in range(count)
    )


def _normal(vertices: tuple[tuple[float, float, float], ...]) -> tuple[float, float, float]:
    a, b, c = vertices[:3]
    ab = tuple(b[i] - a[i] for i in range(3))
    ac = tuple(c[i] - a[i] for i in range(3))
    cross = (
        ab[1] * ac[2] - ab[2] * ac[1],
        ab[2] * ac[0] - ab[0] * ac[2],
        ab[0] * ac[1] - ab[1] * ac[0],
    )
    length = math.sqrt(sum(value**2 for value in cross))
    return tuple(value / length for value in cross)


def test_frozen_profile_and_ppe_constants_share_one_fixture_authority() -> None:
    profile = DEFAULT_SMD_MODULE_PROFILE
    assert profile.window_side_m == EMITTER_SIDE_M == 0.120
    assert profile.lid_side_m == PMMA_SIDE_M == 0.128
    assert profile.lid_thickness_m == PMMA_THICKNESS_M == 0.003
    assert profile.stack_height_m == 0.008
    assert profile.gasket_aperture_m == APERTURE_SIDE_M == 0.126
    assert profile.bezel_pocket_m == BEZEL_OUTER_SIDE_M == 0.129
    assert profile.ptfe_liner_thickness_m == PTFE_PHYSICAL_THICKNESS_M == 0.000508
    assert EMITTER_Z_M == 0.011
    assert ACCEPTED_FIXTURE_TRANSMISSION == 0.8331432504293806
    assert COMPLETED_APERTURE_PPE_UMOL_PER_J == 2.6
    assert INTERNAL_SOURCE_PPE_UMOL_PER_J == 3.120711832761085
    assert (
        INTERNAL_SOURCE_PPE_UMOL_PER_J * ACCEPTED_FIXTURE_TRANSMISSION
        == COMPLETED_APERTURE_PPE_UMOL_PER_J
    )
    assert PROPOSED_FIXTURE_OPTICAL_STACK_ID


def test_exact_nonabsorbing_pmma_ptfe_and_black_bezel_declarations() -> None:
    assert proposed_fixture_materials_radiance_text() == """void dielectric proposed_pmma
0
0
5 1 1 1 1.49 0

void plastic proposed_ptfe
0
0
5 0.90 0.90 0.90 0 0

void plastic proposed_black_bezel
0
0
5 0 0 0 0 0
"""


def test_stack_geometry_is_the_accepted_translated_local_model() -> None:
    text = proposed_fixture_stack_radiance_text(7, 0.25, -0.5, 1.0)
    assert text.count("proposed_pmma polygon") == 6
    assert text.count("proposed_ptfe polygon") == 4
    assert text.count("proposed_black_bezel polygon") == 4
    assert "0.97" not in text

    pmma = tuple(
        vertex
        for face in ("top", "bottom", "north", "south", "west", "east")
        for vertex in _vertices(text, f"proposed_m0007_pmma_{face}")
    )
    assert {vertex[2] for vertex in pmma} == {1.0, 1.003}
    assert max(abs(vertex[0] - 0.25) for vertex in pmma) == pytest.approx(0.064)
    assert max(abs(vertex[1] + 0.5) for vertex in pmma) == pytest.approx(0.064)

    inward = {
        "north": (0.0, -1.0, 0.0),
        "south": (0.0, 1.0, 0.0),
        "west": (1.0, 0.0, 0.0),
        "east": (-1.0, 0.0, 0.0),
    }
    for side, expected in inward.items():
        ptfe = _vertices(text, f"proposed_m0007_ptfe_{side}")
        bezel = _vertices(text, f"proposed_m0007_black_bezel_{side}")
        assert _normal(ptfe) == pytest.approx(expected)
        assert {vertex[2] for vertex in ptfe} == {1.003, 1.011}
        assert _normal(bezel) == pytest.approx((0.0, 0.0, 1.0))


def test_box_helper_emits_six_faces() -> None:
    text = box_radiance_text("test_material", "test_box", 0, 1, 0, 1, 0, 1)
    assert text.count("test_material polygon test_box_") == 6


def test_non_native_source_variant_is_explicitly_research_only() -> None:
    variant = get_smd_source_variant("cosine_140deg")
    assert variant.key == "altered_cosine_140deg"
    assert "Research-only" in variant.description
    assert "uncalibrated" in variant.description
    with pytest.raises(ValueError, match="uncalibrated CAL artifacts"):
        source_variant_cal_text(variant)
