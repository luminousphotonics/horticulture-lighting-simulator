from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

import pytest

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_BAND_ORDER,
    build_conventional_spectral_distribution,
    build_conventional_spectral_source_payload,
    load_conventional_spd,
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_optimized_module_schedule
from fspm_optics.optics.conventional_rex import (
    build_conventional_rex_radiance_material_plan,
    build_conventional_rex_weighted_atr_payload,
)
from fspm_optics.optics.rex_weighting import build_rex_source_weighted_atr_payload
from fspm_optics.sources.smd.profile import build_nominal_smd_source_model
from fspm_optics.transport.conventional_rex import (
    CONVENTIONAL_REX_RUN_ORDER,
    format_conventional_rex_transport_bundle_json,
    plan_conventional_rex_transport_bundle,
)
from fspm_optics.transport.five_band import (
    adapt_smd_band_source_plan,
    build_five_band_source_plans,
)


EXPECTED_SOURCE = {
    "blue": (
        0.17540617270400982,
        0.45605604903042557,
        300.99699236008087,
        1203.9879694403235,
    ),
    "green": (
        0.4358935618794981,
        1.1333232608866952,
        747.9933521852188,
        2991.973408740875,
    ),
    "orange": (
        0.13668100935568855,
        0.3553706243247903,
        234.54461205436155,
        938.1784482174462,
    ),
    "red": (
        0.25201925606080355,
        0.6552500657580893,
        432.4650434003389,
        1729.8601736013557,
    ),
    "far_red": (
        0.009237180707785467,
        0.024016669840242214,
        15.851002094559862,
        63.404008378239446,
    ),
}

EXPECTED_ATR = {
    "scalar_par": (0.782841022327099, 0.11371774161402765, 0.10344123605887345),
    "blue": (0.9020345157559143, 0.02978005455349847, 0.06818542969058743),
    "green": (0.6905747299691899, 0.16790558973211694, 0.14151968029869308),
    "orange": (0.7856538083835263, 0.11975954647181047, 0.09458664514466324),
    "red": (0.8579406563085379, 0.07513836544996583, 0.0669209782414962),
    "far_red": (0.3312604079463673, 0.3814175738579723, 0.2873220181956604),
}

EXPECTED_TRANS = {
    "scalar_par": (0.2171589776729011, 0.5236612496183173),
    "blue": (0.0979654842440859, 0.30398517174987855),
    "green": (0.30942527003081, 0.5426369659963399),
    "orange": (0.21434619161647372, 0.5587201973063013),
    "red": (0.14205934369146203, 0.528922374955909),
    "far_red": (0.6687395920536328, 0.57035291223999),
}


def _source_payload():
    return build_conventional_spectral_source_payload(
        plan_conventional_layout_from_feet(10, 10)
    )


def test_recomputed_conventional_band_fractions_yields_and_ppf_closure() -> None:
    payload = _source_payload()

    assert CONVENTIONAL_BAND_ORDER == (
        "blue", "green", "orange", "red", "far_red"
    )
    assert tuple(item.band_id for item in payload.bands) == CONVENTIONAL_BAND_ORDER
    for band in payload.bands:
        expected = EXPECTED_SOURCE[band.band_id]
        assert (
            band.photon_fraction_relative_to_par,
            band.effective_yield_umol_per_j,
            band.per_fixture_ppf_umol_s,
            band.whole_layout_ppf_umol_s,
        ) == pytest.approx(expected, abs=1e-12)
    par = payload.bands[:4]
    assert math.fsum(item.photon_fraction_relative_to_par for item in par) == pytest.approx(1.0, abs=1e-15)
    assert math.fsum(item.effective_yield_umol_per_j for item in par) == pytest.approx(2.6, abs=1e-15)
    assert math.fsum(item.per_fixture_ppf_umol_s for item in par) == pytest.approx(1716.0, abs=1e-12)
    assert math.fsum(item.whole_layout_ppf_umol_s for item in par) == pytest.approx(6864.0, abs=1e-12)
    assert payload.far_red_relative_to_par == pytest.approx(0.009237180707785467, abs=1e-15)
    assert all(item.is_par for item in par)
    assert payload.band("far_red").is_par is False
    assert "smd" not in payload.source_payload_id.lower()


def test_conventional_photon_fractions_are_invariant_to_spd_amplitude() -> None:
    spd = load_conventional_spd()
    scaled = replace(
        spd,
        relative_spd=tuple(value * 17.0 for value in spd.relative_spd),
    )
    original = build_conventional_spectral_distribution(spd)
    changed = build_conventional_spectral_distribution(scaled)

    for (original_name, original_value), (changed_name, changed_value) in zip(
        original.par_band_photon_fractions,
        changed.par_band_photon_fractions,
        strict=True,
    ):
        assert original_name == changed_name
        assert changed_value == pytest.approx(original_value, rel=0.0, abs=1e-15)
    assert original.far_red_relative_to_par == changed.far_red_relative_to_par


def test_conventional_weighted_scalar_and_band_atr_are_closed_and_distinct_from_smd() -> None:
    conventional = build_conventional_rex_weighted_atr_payload(_source_payload())
    smd = build_rex_source_weighted_atr_payload()

    assert conventional.optical_payload_id != smd.to_payload()["model_id"]
    assert conventional.intervals.positive_source_start_nm == 416
    assert conventional.intervals.positive_source_end_nm == 739
    assert dict(conventional.intervals.unsupported_source_mass_by_interval) == {
        interval_id: 0.0 for interval_id in CONVENTIONAL_REX_RUN_ORDER
    }
    for interval in (conventional.scalar_par, *conventional.bands):
        coefficients = interval.coefficients
        actual = (
            coefficients.absorptance,
            coefficients.transmittance,
            coefficients.reflectance,
        )
        assert actual == pytest.approx(EXPECTED_ATR[interval.interval_id], abs=1e-15)
        assert math.fsum(actual) == pytest.approx(1.0, abs=1e-12)
    assert conventional.band("red").coefficients != smd.band("red").coefficients


def test_conventional_materials_reconstruct_weighted_atr() -> None:
    optics = build_conventional_rex_weighted_atr_payload(_source_payload())
    materials = build_conventional_rex_radiance_material_plan(optics)

    assert tuple(item.interval_id for item in materials.materials) == CONVENTIONAL_REX_RUN_ORDER
    for material in materials.materials:
        scattered, transmitted_fraction = EXPECTED_TRANS[material.interval_id]
        parameters = material.parameters
        assert parameters.a1 == parameters.a2 == parameters.a3 == pytest.approx(scattered, abs=1e-15)
        assert (parameters.a4, parameters.a5, parameters.a7) == (0.0, 0.0, 0.0)
        assert parameters.a6 == pytest.approx(transmitted_fraction, abs=1e-15)
        source = material.source_interval.coefficients
        reconstructed = material.reconstruction
        assert reconstructed.absorptance == pytest.approx(source.absorptance, abs=1e-12)
        assert reconstructed.total_transmittance == pytest.approx(source.transmittance, abs=1e-12)
        assert reconstructed.total_reflectance == pytest.approx(source.reflectance, abs=1e-12)


def test_source_neutral_smd_adapter_preserves_existing_smd_payload() -> None:
    source = build_nominal_smd_source_model()
    layout = generate_proposed_led_layout(10, 10)
    schedule = build_optimized_module_schedule(
        layout,
        tuple(30.0 for _ in range(layout.control_zone_count)),
    )
    plans = build_five_band_source_plans(source, layout, schedule)
    before = json.dumps([item.to_dict() for item in plans], sort_keys=True)
    adapted = tuple(adapt_smd_band_source_plan(item) for item in plans)

    assert hashlib.sha256(before.encode("utf-8")).hexdigest() == (
        "6791cbaa14532b9c2fc467aa79b7c2e724db78c3cff609a215965e93a0ebd464"
    )
    assert {item.source_model_id for item in plans} == {
        "proposed_led_smd_relative_spectral_mix_v2"
    }
    assert json.dumps([item.to_dict() for item in plans], sort_keys=True) == before
    assert tuple(item.interval_id for item in adapted) == CONVENTIONAL_BAND_ORDER
    assert all(item.source_family == "smd" for item in adapted)
    assert all(item.absolute_photon_flux_applied_before_trace for item in adapted)
    assert all(not item.spectral_fraction_applied_after_trace for item in adapted)


def test_bundle_plans_scalar_and_exactly_five_isolated_bands_without_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "future workspace with spaces"
    bundle = plan_conventional_rex_transport_bundle(workspace)

    assert bundle.bundle_id == (
        "conventional-rex-transport-bundle-v4-"
        "de2230be211942c48eb483f3d3ea13a41c9e41fdb840c95245d3aabebece4387"
    )
    assert tuple(item.interval_id for item in bundle.runs) == CONVENTIONAL_REX_RUN_ORDER
    assert len(bundle.band_plans) == 5
    assert bundle.scalar_par.interval_id == "scalar_par"
    assert not workspace.exists()
    assert all(not item.paths.directory.exists() for item in bundle.runs)
    assert all(not item.source_input.spectral_fraction_applied_after_trace for item in bundle.runs)
    assert all(item.source_input.rgb_channel_policy == "equal_grayscale_scalar_carrier" for item in bundle.runs)
    assert bundle.scientific_payload()["scalar_plus_band_summation"] is False


def test_bundle_carriers_share_dat_but_isolate_source_scene_octree_and_cache(tmp_path: Path) -> None:
    bundle = plan_conventional_rex_transport_bundle(tmp_path / "future")

    assert len({item.source_input.shared_angular_data_identity for item in bundle.runs}) == 1
    assert len({item.source_definition_id for item in bundle.runs}) == 6
    assert len({item.scene_id for item in bundle.runs}) == 6
    assert len({item.octree_identity for item in bundle.runs}) == 6
    assert len({item.ambient_cache_identity for item in bundle.runs}) == 6
    for run in bundle.runs:
        expected_carrier = run.per_fixture_ppf_umol_s * 179.0
        assert run.carrier_multiplier == pytest.approx(expected_carrier, abs=1e-9)
        assert run.aperture_area_m2 == pytest.approx(
            0.17822200689268405,
            abs=1e-15,
        )
        assert run.aperture_area_m2 < 1.190 * 1.087
        assert run.flat_source_correction == pytest.approx(
            expected_carrier / run.aperture_area_m2, abs=1e-9
        )
    assert bundle.scalar_par.carrier_multiplier == 307164.0


def test_rex_geometry_receiver_order_and_absorption_boundary_are_shared(tmp_path: Path) -> None:
    bundle = plan_conventional_rex_transport_bundle(tmp_path / "future")

    assert bundle.plant_polygon_count == 5248
    assert bundle.receiver_count == 1024
    assert len(bundle.ordered_receiver_ids) == 1024
    assert len(set(bundle.ordered_receiver_ids)) == 1024
    compatibility = bundle.absorption_compatibility
    assert compatibility.receiver_identity == bundle.receiver_identity
    assert compatibility.band_order == CONVENTIONAL_BAND_ORDER
    assert compatibility.absorbed_execution_in_scope is False
    assert compatibility.scalar_par_result_policy == (
        "separate_cross_check_not_added_to_band_results"
    )


def test_bundle_serialization_is_deterministic_and_path_free_identity(tmp_path: Path) -> None:
    first = plan_conventional_rex_transport_bundle(tmp_path / "one")
    repeat = plan_conventional_rex_transport_bundle(tmp_path / "one")
    elsewhere = plan_conventional_rex_transport_bundle(tmp_path / "two")

    assert format_conventional_rex_transport_bundle_json(first) == (
        format_conventional_rex_transport_bundle_json(repeat)
    )
    assert first.scientific_payload() == elsewhere.scientific_payload()
    assert first.bundle_id == elsewhere.bundle_id
    assert str(tmp_path) not in json.dumps(first.scientific_payload(), sort_keys=True)


def test_planning_modules_have_no_execution_or_legacy_coefficient_boundary() -> None:
    root = Path(__file__).parents[1]
    sources = tuple(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "src/fspm_optics/fixtures/conventional_led/band_source.py",
            "src/fspm_optics/optics/conventional_rex.py",
            "src/fspm_optics/transport/conventional_rex.py",
        )
    )
    combined = "\n".join(sources)
    assert "subprocess" not in combined
    assert "LocalRunner" not in combined
    assert "discover_radiance" not in combined
    assert ".salvage_source" not in combined
    assert "0.903040," not in combined
    assert "execution_performed\": True" not in combined
