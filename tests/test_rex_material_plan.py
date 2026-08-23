from __future__ import annotations

from dataclasses import replace
import json
import math
from pathlib import Path

import pytest

from fspm_optics.optics.rex_material_plan import (
    DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID,
    REX_RADIANCE_MATERIAL_PLAN_CLAIM,
    REX_TRANS_MODEL_ASSUMPTIONS,
    RadianceTransParameters,
    RexRadianceTransMaterialPlan,
    build_rex_radiance_trans_material_plan,
    format_rex_radiance_trans_material_plan_json,
    map_atr_to_diffuse_trans,
    read_rex_radiance_trans_material_plan_json,
    reconstruct_radiance_trans,
    render_radiance_trans_material,
    write_rex_radiance_trans_material_plan_json,
)
from fspm_optics.optics.rex_weighting import (
    AtrCoefficients,
    build_rex_source_weighted_atr_payload,
)


EXPECTED_TRANS_ARGUMENTS = {
    "scalar_par": (0.193376309341186, 0.520084207785277),
    "blue": (0.095603239180271, 0.295501820630401),
    "green": (0.309809212577060, 0.542414416301420),
    "orange": (0.213707020294201, 0.558632066631424),
    "red": (0.124473242790819, 0.509344289982879),
    "far_red": (0.719954775658055, 0.569132607968614),
}

EXPECTED_INTERVAL_IDS = (
    "scalar_par",
    "blue",
    "green",
    "orange",
    "red",
    "far_red",
)


def _parameters_from_rendered_text(text: str) -> RadianceTransParameters:
    lines = text.splitlines()
    assert len(lines) == 4
    count, *arguments = lines[3].split()
    assert count == "7"
    assert len(arguments) == 7
    return RadianceTransParameters(*(float(value) for value in arguments))


def _atr_errors(material) -> tuple[float, float, float]:
    source = material.source_interval.coefficients
    reconstructed = material.reconstruction
    return (
        abs(reconstructed.absorptance - source.absorptance),
        abs(reconstructed.total_transmittance - source.transmittance),
        abs(reconstructed.total_reflectance - source.reflectance),
    )


def test_plan_contains_all_six_computed_grayscale_materials() -> None:
    plan = build_rex_radiance_trans_material_plan()

    assert tuple(item.interval_id for item in plan.materials) == EXPECTED_INTERVAL_IDS
    assert tuple(item.material_identifier for item in plan.materials) == tuple(
        f"rex_leaf_trans_{interval_id}" for interval_id in EXPECTED_INTERVAL_IDS
    )
    for material in plan.materials:
        parameters = material.parameters
        assert parameters.a1 == parameters.a2 == parameters.a3
        assert (parameters.a4, parameters.a5, parameters.a7) == (0.0, 0.0, 0.0)


def test_material_arguments_match_approved_regression_values() -> None:
    plan = build_rex_radiance_trans_material_plan()

    for interval_id, (scattered, transmitted_fraction) in (
        EXPECTED_TRANS_ARGUMENTS.items()
    ):
        parameters = plan.material(interval_id).parameters
        assert parameters.a1 == pytest.approx(scattered, abs=1e-15)
        assert parameters.a2 == pytest.approx(scattered, abs=1e-15)
        assert parameters.a3 == pytest.approx(scattered, abs=1e-15)
        assert parameters.a6 == pytest.approx(transmitted_fraction, abs=1e-15)


def test_forward_reconstruction_recovers_phase16_atr_and_closes() -> None:
    plan = build_rex_radiance_trans_material_plan()

    for material in plan.materials:
        source = material.source_interval.coefficients
        reconstructed = reconstruct_radiance_trans(material.parameters)
        assert reconstructed == material.reconstruction
        assert reconstructed.diffuse_reflectance == pytest.approx(
            source.reflectance,
            abs=1e-12,
        )
        assert reconstructed.diffuse_transmittance == pytest.approx(
            source.transmittance,
            abs=1e-12,
        )
        assert reconstructed.specular_reflectance == 0.0
        assert reconstructed.specular_transmittance == 0.0
        assert reconstructed.total_reflectance == pytest.approx(
            source.reflectance,
            abs=1e-12,
        )
        assert reconstructed.total_transmittance == pytest.approx(
            source.transmittance,
            abs=1e-12,
        )
        assert reconstructed.absorptance == pytest.approx(
            source.absorptance,
            abs=1e-12,
        )
        assert abs(reconstructed.closure_error) <= 1e-12
        assert max(_atr_errors(material)) <= 1e-12


def test_rendered_material_arguments_preserve_atr_after_parsing() -> None:
    plan = build_rex_radiance_trans_material_plan()

    for material in plan.materials:
        parsed = _parameters_from_rendered_text(material.radiance_text)
        reconstructed = reconstruct_radiance_trans(parsed)
        source = material.source_interval.coefficients
        assert reconstructed.absorptance == pytest.approx(
            source.absorptance,
            abs=1e-12,
        )
        assert reconstructed.total_transmittance == pytest.approx(
            source.transmittance,
            abs=1e-12,
        )
        assert reconstructed.total_reflectance == pytest.approx(
            source.reflectance,
            abs=1e-12,
        )


def test_radiance_material_text_is_deterministic_and_exactly_shaped() -> None:
    material = build_rex_radiance_trans_material_plan().material("scalar_par")

    first = material.radiance_text
    second = render_radiance_trans_material(
        material.material_identifier,
        material.parameters,
    )

    assert first == second
    assert first == (
        "void trans rex_leaf_trans_scalar_par\n"
        "0\n"
        "0\n"
        "7 0.19337630934118616 0.19337630934118616 "
        "0.19337630934118616 0 0 0.52008420778527731 0\n"
    )
    assert first.endswith("\n")


def test_plan_preserves_phase16_provenance_and_explicit_assumptions() -> None:
    phase16 = build_rex_source_weighted_atr_payload()
    plan = build_rex_radiance_trans_material_plan(phase16)
    serialized = plan.to_payload()

    assert plan.phase16_atr is phase16
    assert serialized["phase16_atr"] == phase16.to_payload()
    assert plan.policy_id == DIFFUSE_ONLY_SYMMETRIC_THIN_LEAF_POLICY_ID
    assert plan.claim_language == REX_RADIANCE_MATERIAL_PLAN_CLAIM
    assert plan.assumptions == REX_TRANS_MODEL_ASSUMPTIONS
    assert "front_to_back_optical_behavior_is_symmetric" in plan.assumptions
    assert "spectral_bands_are_not_packed_into_radiance_rgb_channels" in (
        plan.assumptions
    )


def test_plan_json_round_trip_is_typed_and_deterministic(tmp_path: Path) -> None:
    plan = build_rex_radiance_trans_material_plan()
    first = format_rex_radiance_trans_material_plan_json(plan)
    second = format_rex_radiance_trans_material_plan_json(plan)
    output = write_rex_radiance_trans_material_plan_json(
        tmp_path / "rex_material_plan.json",
        plan,
    )

    assert first == second
    assert output.read_text(encoding="utf-8") == first
    assert RexRadianceTransMaterialPlan.from_payload(json.loads(first)) == plan
    assert read_rex_radiance_trans_material_plan_json(output) == plan


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("absorptance", float("nan")),
        ("transmittance", float("inf")),
        ("reflectance", -0.01),
        ("absorptance", 1.01),
    ],
)
def test_atr_nonfinite_and_bounds_fail_before_mapping(field: str, value: float) -> None:
    values = {
        "absorptance": 0.7,
        "transmittance": 0.2,
        "reflectance": 0.1,
    }
    values[field] = value

    with pytest.raises(ValueError, match=field):
        map_atr_to_diffuse_trans(AtrCoefficients(**values))


def test_mapper_rejects_closure_failure_and_perfect_absorber() -> None:
    with pytest.raises(ValueError, match="must close to 1"):
        map_atr_to_diffuse_trans(AtrCoefficients(0.7, 0.1, 0.1))

    with pytest.raises(ValueError, match=r"R \+ T must be greater than zero"):
        map_atr_to_diffuse_trans(AtrCoefficients(1.0, 0.0, 0.0))


def test_transmittance_zero_is_canonical_and_has_no_negative_zero() -> None:
    parameters = map_atr_to_diffuse_trans(AtrCoefficients(0.8, -0.0, 0.2))
    rendered = render_radiance_trans_material("zero_transmission", parameters)

    assert parameters == RadianceTransParameters(0.2, 0.2, 0.2, 0, 0, 0, 0)
    assert parameters.a6 == 0.0 and math.copysign(1.0, parameters.a6) == 1.0
    assert "-0" not in rendered


def test_reflectance_zero_is_valid_and_reconstructs_exactly() -> None:
    coefficients = AtrCoefficients(0.75, 0.25, 0.0)
    parameters = map_atr_to_diffuse_trans(coefficients)
    reconstructed = reconstruct_radiance_trans(parameters)

    assert parameters == RadianceTransParameters(0.25, 0.25, 0.25, 0, 0, 1, 0)
    assert reconstructed.absorptance == coefficients.absorptance
    assert reconstructed.total_transmittance == coefficients.transmittance
    assert reconstructed.total_reflectance == coefficients.reflectance


@pytest.mark.parametrize(
    "identifier",
    ["", "9rex", "rex leaf", "rex/leaf", "rex;leaf", "rex\nleaf"],
)
def test_invalid_material_identifiers_are_rejected(identifier: str) -> None:
    parameters = RadianceTransParameters(0.2, 0.2, 0.2, 0, 0, 0.5, 0)

    with pytest.raises(ValueError, match="material name"):
        render_radiance_trans_material(identifier, parameters)

    with pytest.raises(ValueError, match="material name"):
        build_rex_radiance_trans_material_plan(material_prefix=identifier)


def test_parameters_reject_nonfinite_bounds_and_non_grayscale_reconstruction() -> None:
    with pytest.raises(ValueError, match="a1"):
        RadianceTransParameters(float("nan"), 0.2, 0.2, 0, 0, 0.5, 0)
    with pytest.raises(ValueError, match="a6"):
        RadianceTransParameters(0.2, 0.2, 0.2, 0, 0, 1.01, 0)
    with pytest.raises(ValueError, match="requires A1=A2=A3"):
        reconstruct_radiance_trans(
            RadianceTransParameters(0.2, 0.3, 0.2, 0, 0, 0.5, 0)
        )


def test_json_rejects_tampered_reconstruction_and_material_text() -> None:
    raw = build_rex_radiance_trans_material_plan().to_payload()
    raw["materials"]["blue"]["reconstruction"]["closure_error"] = 1e-6

    with pytest.raises(ValueError, match="does not close"):
        RexRadianceTransMaterialPlan.from_payload(raw)

    raw = build_rex_radiance_trans_material_plan().to_payload()
    raw["materials"]["blue"]["radiance_text"] += "\n"
    with pytest.raises(ValueError, match="text is not deterministic"):
        RexRadianceTransMaterialPlan.from_payload(raw)


def test_interval_plan_rejects_parameters_that_diverge_from_phase16() -> None:
    plan = build_rex_radiance_trans_material_plan()
    blue = plan.material("blue")

    with pytest.raises(ValueError, match="parameters do not match"):
        replace(
            blue,
            parameters=replace(blue.parameters, a6=blue.parameters.a6 + 0.01),
        )
