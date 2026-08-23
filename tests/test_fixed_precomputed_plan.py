from __future__ import annotations

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import struct

import pytest

from fspm_optics.application.domain import (
    AnalysisScope,
    CONVENTIONAL_SYSTEM_ID,
    DEFAULT_FSPM_TOLERANCE_UMOL_M2_S,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    ProposedControlMode,
    ProposedSourceMode,
    ProposedSpectralBasis,
    parse_run_request,
)
from fspm_optics.fixtures.conventional_led import (
    PRACTICAL_LAYOUT_POLICY,
    ROLLING_BENCH_LAYOUT_POLICY,
)
from fspm_optics.geometry.coordinate_frame import ROOM_FRAME_POLICY_ID
from fspm_optics.layout.ring import ProposedRingMode
from fspm_optics.precomputed.compact_bundle import CompactPlayback
from fspm_optics.precomputed import compact_bundle
from fspm_optics.precomputed import fixed_plan as fixed_plan_module
from fspm_optics.precomputed.fixed_plan import (
    FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S,
    FIXED_QUALITY,
    build_fixed_sweep_plan,
    canonical_room_domain,
)
from fspm_optics.precomputed.playback import (
    RequestedCompactPlayback,
    resolve_fixed_case,
)
from fspm_optics.precomputed_cli import main as precomputed_main
from fspm_optics.radiance.options import radiance_options
from fspm_optics.transport.scalar_ppfd import PpfdMapSample


EXPECTED_STANDARD_PLAN_IDENTITY_SHA256 = (
    "494a8e24e0e5e737b5144e4ed84695ecaf041fda41de7b331aac70fe691f8f24"
)
EXPECTED_STANDARD_CASE_IDS = (
    "proposed-10x10-ppfd-250-aisle-off-be7f4370ab896211",
    "proposed-10x10-ppfd-250-aisle-on-6b9005431fdf11b1",
    "proposed-30x15-ppfd-250-aisle-off-2e2b80cd7639f394",
    "proposed-30x15-ppfd-250-aisle-on-76903eae55ba346f",
    "proposed-50x30-ppfd-250-aisle-off-597de6d0d31ec5c1",
    "proposed-50x30-ppfd-250-aisle-on-c704256afaf67668",
    "conventional-practical-10x10-ppfd-250-aisle-off-0371d45bb1395416",
    "conventional-practical-10x10-ppfd-250-aisle-on-507e5b8eb5ed0605",
    "conventional-practical-30x15-ppfd-250-aisle-off-5f21f48431556889",
    "conventional-practical-30x15-ppfd-250-aisle-on-9f4ff3d82ea03d30",
    "conventional-practical-50x30-ppfd-250-aisle-off-71d4484b8c460cb8",
    "conventional-practical-50x30-ppfd-250-aisle-on-6f762e51a0e26a7e",
    "conventional-rolling-bench-10x10-ppfd-250-aisle-off-f20616908d59465e",
    "conventional-rolling-bench-10x10-ppfd-250-aisle-on-81ff78e94a6d162c",
    "conventional-rolling-bench-30x15-ppfd-250-aisle-off-52837b0260b9d276",
    "conventional-rolling-bench-30x15-ppfd-250-aisle-on-abba96cccfa1cdee",
    "conventional-rolling-bench-50x30-ppfd-250-aisle-off-8afe0964fa32a913",
    "conventional-rolling-bench-50x30-ppfd-250-aisle-on-d634bc862272d235",
    "hps-10x10-ppfd-250-aisle-off-b5460e360dcf5a76",
    "hps-10x10-ppfd-250-aisle-on-70fbe36ae64a3aa7",
    "hps-30x15-ppfd-250-aisle-off-0ebf02807c6ed861",
    "hps-30x15-ppfd-250-aisle-on-604732d0e81973bc",
    "hps-50x30-ppfd-250-aisle-off-85500aaad6910efe",
    "hps-50x30-ppfd-250-aisle-on-8433f4caf18e0a33",
)


def _target_values(value: object, *, key: str = "") -> list[object]:
    if isinstance(value, dict):
        result: list[object] = []
        for item_key, item in value.items():
            result.extend(_target_values(item, key=str(item_key)))
        return result
    if isinstance(value, list):
        result = []
        for item in value:
            result.extend(_target_values(item, key=key))
        return result
    return [value] if "target" in key.lower() else []


def _request(
    *,
    system: str,
    length_ft: float,
    width_ft: float,
    aisle: bool = False,
    layout: str = PRACTICAL_LAYOUT_POLICY,
    target: float = 250.0,
):
    payload: dict[str, object] = {
        "system": system,
        "room_length_ft": length_ft,
        "room_width_ft": width_ft,
        "quality": "standard",
        "analysis_scope": "baseline_plus_multispectral_fspm",
        "include_far_red": True,
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 75.0,
        "aisle_mode": aisle,
        "mounting_height_in": 24.0 if system == HPS_SYSTEM_ID else 18.0,
    }
    if system != HPS_SYSTEM_ID:
        payload["target_ppfd"] = target
    if system == CONVENTIONAL_SYSTEM_ID:
        payload["layout_mode"] = layout
    if system == PROPOSED_SYSTEM_ID:
        payload.update(
            {
                "spectral_basis": "conventional_led_control",
                "proposed_control_mode": "uniform_module_dimming",
                "proposed_ring_mode": "reduced_one_ring",
                "proposed_source_mode": "native_smd",
            }
        )
    return parse_run_request(payload)


def test_fixed_plan_is_exact_deterministic_and_target_250_fail_closed() -> None:
    first = build_fixed_sweep_plan()
    second = build_fixed_sweep_plan()

    assert first == second
    assert first.plan_identity_sha256 == EXPECTED_STANDARD_PLAN_IDENTITY_SHA256
    assert tuple(case.case_id for case in first.cases) == EXPECTED_STANDARD_CASE_IDS
    assert first.case_count == 24
    assert first.to_payload()["target_ppfd_umol_m2_s"] == 250.0
    assert FIXED_PRODUCTION_TARGET_PPFD_UMOL_M2_S == 250.0
    assert [case.ordinal for case in first.cases] == list(range(1, 25))
    assert len({case.case_id for case in first.cases}) == 24
    assert len({case.output_relative_path for case in first.cases}) == 24
    assert all("ppfd-250" in case.case_id for case in first.cases)
    assert all("/ppfd-250/" in case.output_relative_path for case in first.cases)
    assert all(
        case.resolved_configuration["target_ppfd_umol_m2_s"] == 250.0
        for case in first.cases
    )
    assert all(
        1000 not in _target_values(case.to_payload()) for case in first.cases
    )

    counts = Counter((case.system_id, case.layout_variant) for case in first.cases)
    assert counts == {
        (PROPOSED_SYSTEM_ID, "proposed_reduced_one_ring"): 6,
        (CONVENTIONAL_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY): 6,
        (CONVENTIONAL_SYSTEM_ID, ROLLING_BENCH_LAYOUT_POLICY): 6,
        (HPS_SYSTEM_ID, "fixed_full_output"): 6,
    }
    assert {
        (
            case.canonical_domain["canonical_aligned_room"]["length_x_ft"],
            case.canonical_domain["canonical_aligned_room"]["width_y_ft"],
        )
        for case in first.cases
    } == {(10.0, 10.0), (30.0, 15.0), (50.0, 30.0)}
    canonical_metric_rooms = {
        (
            case.canonical_domain["canonical_aligned_room"]["length_x_ft"],
            case.canonical_domain["canonical_aligned_room"]["width_y_ft"],
        ): (
            case.canonical_domain["canonical_aligned_room"]["length_x_m"],
            case.canonical_domain["canonical_aligned_room"]["width_y_m"],
        )
        for case in first.cases
    }
    assert canonical_metric_rooms == {
        (10.0, 10.0): (3.048, 3.048),
        (30.0, 15.0): (9.144, 4.572),
        (50.0, 30.0): (15.24, 9.144),
    }

def test_hps_physical_storage_path_is_separate_from_authenticated_plan(
    tmp_path: Path,
) -> None:
    plan = build_fixed_sweep_plan()

    assert plan.plan_identity_sha256 == EXPECTED_STANDARD_PLAN_IDENTITY_SHA256
    assert tuple(case.case_id for case in plan.cases) == EXPECTED_STANDARD_CASE_IDS
    for case in plan.cases:
        authenticated = Path(case.output_relative_path)
        physical = case.output_path(tmp_path).relative_to(tmp_path)
        if case.system_id == HPS_SYSTEM_ID:
            assert authenticated.parts[0] == "hps"
            assert physical == Path("hps", *authenticated.parts[1:])
        else:
            assert physical == authenticated


def test_fixed_plan_preserves_all_typed_application_authorities() -> None:
    plan = build_fixed_sweep_plan()
    assert FIXED_QUALITY == "standard"
    assert Counter(case.request.quality for case in plan.cases) == {
        "standard": 24
    }
    for case in plan.cases:
        request = case.request
        assert request.quality == "standard"
        assert case.resolved_configuration["request"]["quality"] == "standard"
        assert case.compatibility_inputs["solver"]["radiance_quality"] == (
            "standard"
        )
        assert case.compatibility_inputs["solver"]["radiance_options"] == (
            radiance_options("standard")
        )
        assert request.analysis_scope is AnalysisScope.BASELINE_PLUS_MULTISPECTRAL_FSPM
        assert request.include_far_red is True
        assert request.fspm_target_mode == "automatic"
        assert request.fspm_target_override_umol_m2_s is None
        assert DEFAULT_FSPM_TOLERANCE_UMOL_M2_S == 75.0
        assert request.fspm_target_tolerance_umol_m2_s == 75.0
        assert request.mounting_height_in == (
            24.0 if case.system_id == HPS_SYSTEM_ID else 18.0
        )
        assert request.mounting_geometry.mounting_height_m == pytest.approx(
            0.6096 if case.system_id == HPS_SYSTEM_ID else 0.4572
        )
        assert case.canonical_domain["coordinate_frame_policy_id"] == (
            ROOM_FRAME_POLICY_ID
        )
        if case.system_id == HPS_SYSTEM_ID:
            assert not hasattr(request, "target_ppfd_umol_m2_s")
            assert case.resolved_configuration["target_semantics"] == (
                "fixed_output_comparison_reference_no_dimming"
            )
            assert "lighting_target_mode" not in request.to_dict()
        else:
            assert request.target_ppfd_umol_m2_s == 250.0
            assert request.lighting_target_mode.value == "mean_target"
            assert case.resolved_configuration["target_semantics"] == (
                "requested_mean_target"
            )
        if case.system_id == PROPOSED_SYSTEM_ID:
            assert request.control_mode is ProposedControlMode.UNIFORM_MODULE_DIMMING
            assert request.spectral_basis is ProposedSpectralBasis.CONVENTIONAL_LED_CONTROL
            assert request.proposed_ring_mode is ProposedRingMode.REDUCED_ONE_RING
            assert request.source_mode is ProposedSourceMode.NATIVE_SMD

    by_system_room = {
        (
            case.system_id,
            case.layout_variant,
            case.canonical_domain["canonical_aligned_room"]["length_x_ft"],
            case.canonical_domain["canonical_aligned_room"]["width_y_ft"],
        ): set()
        for case in plan.cases
    }
    for case in plan.cases:
        aligned = case.canonical_domain["canonical_aligned_room"]
        key = (
            case.system_id,
            case.layout_variant,
            aligned["length_x_ft"],
            aligned["width_y_ft"],
        )
        by_system_room[key].add(case.aisle_enabled)
    assert all(states == {False, True} for states in by_system_room.values())


def _without_authenticated_quality(value: object) -> object:
    normalized = deepcopy(value)
    assert isinstance(normalized, dict)
    request = normalized["request"]
    assert isinstance(request, dict)
    request["quality"] = "<normalized-quality>"
    compatibility = normalized["compatibility_inputs"]
    assert isinstance(compatibility, dict)
    solver = compatibility["solver"]
    assert isinstance(solver, dict)
    solver["radiance_quality"] = "<normalized-quality>"
    solver["radiance_options"] = ["<normalized-options>"]
    return normalized


def test_standard_is_the_only_plan_change_and_quality_bindings_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    standard_plan = build_fixed_sweep_plan()
    monkeypatch.setattr(fixed_plan_module, "_validate_case_matrix", lambda _cases: None)
    monkeypatch.setattr(fixed_plan_module, "FIXED_QUALITY", "quality")
    quality_plan = build_fixed_sweep_plan()

    assert standard_plan.case_count == quality_plan.case_count == 24
    assert standard_plan.plan_identity_sha256 != quality_plan.plan_identity_sha256
    assert all(case.request.quality == "standard" for case in standard_plan.cases)
    assert all(case.request.quality == "quality" for case in quality_plan.cases)

    for standard_case, quality_case in zip(
        standard_plan.cases, quality_plan.cases, strict=True
    ):
        assert (
            standard_case.ordinal,
            standard_case.system_id,
            standard_case.layout_variant,
            standard_case.aisle_enabled,
            standard_case.canonical_domain,
            standard_case.output_relative_path,
        ) == (
            quality_case.ordinal,
            quality_case.system_id,
            quality_case.layout_variant,
            quality_case.aisle_enabled,
            quality_case.canonical_domain,
            quality_case.output_relative_path,
        )
        assert _without_authenticated_quality(
            standard_case.resolved_configuration
        ) == _without_authenticated_quality(quality_case.resolved_configuration)
        assert standard_case.case_id != quality_case.case_id
        assert (
            standard_case.configuration_identity_sha256
            != quality_case.configuration_identity_sha256
        )
        assert (
            standard_case.compatibility_inputs_sha256
            != quality_case.compatibility_inputs_sha256
        )

        standard_configuration = standard_case.expected_run_configuration(
            standard_plan.plan_identity_sha256
        )
        quality_configuration = quality_case.expected_run_configuration(
            quality_plan.plan_identity_sha256
        )
        assert not compact_bundle._mapping_contains(
            quality_configuration, standard_configuration
        )
        assert not compact_bundle._mapping_contains(
            quality_case.expected_authenticated_identities(),
            standard_case.expected_authenticated_identities(),
        )


@pytest.mark.parametrize(
    ("system", "layout"),
    [
        (PROPOSED_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY),
        (CONVENTIONAL_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY),
        (CONVENTIONAL_SYSTEM_ID, ROLLING_BENCH_LAYOUT_POLICY),
        (HPS_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY),
    ],
)
@pytest.mark.parametrize(
    ("first_order", "second_order"),
    [((15.0, 30.0), (30.0, 15.0)), ((30.0, 50.0), (50.0, 30.0))],
)
def test_reversed_dimensions_resolve_to_one_case_and_path(
    system: str,
    layout: str,
    first_order: tuple[float, float],
    second_order: tuple[float, float],
) -> None:
    plan = build_fixed_sweep_plan()
    first = resolve_fixed_case(
        _request(system=system, length_ft=first_order[0], width_ft=first_order[1], layout=layout),
        plan=plan,
    )
    second = resolve_fixed_case(
        _request(system=system, length_ft=second_order[0], width_ft=second_order[1], layout=layout),
        plan=plan,
    )
    assert first.case_id == second.case_id
    assert first.output_relative_path == second.output_relative_path
    assert first.configuration_identity_sha256 == second.configuration_identity_sha256


def test_resolver_varies_only_led_playback_target_and_square_is_unrotated() -> None:
    base = resolve_fixed_case(
        _request(
            system=CONVENTIONAL_SYSTEM_ID,
            length_ft=15,
            width_ft=30,
            target=250,
        )
    )
    adjusted = resolve_fixed_case(
        _request(
            system=CONVENTIONAL_SYSTEM_ID,
            length_ft=15,
            width_ft=30,
            target=251,
        )
    )
    assert adjusted.case_id == base.case_id
    assert adjusted.output_relative_path == base.output_relative_path
    assert resolve_fixed_case(
        _request(
            system=CONVENTIONAL_SYSTEM_ID,
            length_ft=15,
            width_ft=30,
            target=250,
        ),
        target_ppfd_umol_m2_s=0.0,
    ).case_id == base.case_id
    with pytest.raises(ValueError, match="finite non-negative"):
        resolve_fixed_case(
            _request(
                system=CONVENTIONAL_SYSTEM_ID,
                length_ft=15,
                width_ft=30,
            ),
            target_ppfd_umol_m2_s=True,
        )
    with pytest.raises(ValueError, match="fixed-output.*rejects"):
        resolve_fixed_case(
            _request(
                system=HPS_SYSTEM_ID,
                length_ft=15,
                width_ft=30,
            ),
            target_ppfd_umol_m2_s=250.0,
        )
    square = canonical_room_domain(10, 10)
    assert square["supported_requested_room_orders_ft"] == [
        {"length": 10.0, "width": 10.0}
    ]
    assert square["mapping"]["square_rotation_degrees_about_z"] == 0


def _fake_playback(case) -> CompactPlayback:
    identity = "a" * 64
    samples = (
        PpfdMapSample(-2.0, -1.0, 0.5, 100.0),
        PpfdMapSample(2.0, -1.0, 0.5, 200.0),
        PpfdMapSample(-2.0, 1.0, 0.5, 300.0),
        PpfdMapSample(2.0, 1.0, 0.5, 400.0),
    )
    matrix = (
        1.0, 0.0, 0.0, 0.0,
        0.0, 1.0, 0.0, 0.0,
        0.0, 0.0, 1.0, 0.0,
        2.0, 3.0, -1.0, 1.0,
    )
    visualization = {
        "field": {"identity_sha256": "b" * 64},
        "grid": {
            "resolution": {"x": 2, "y": 2},
            "x_centers_m": [-2.0, 2.0],
            "y_centers_m": [-1.0, 1.0],
            "extent_m": {"x_min": -3.0, "x_max": 3.0, "y_min": -2.0, "y_max": 2.0},
        },
        "orientation": {},
        "display": {"normalization": {"method": "unchanged-test-policy"}},
        "scatter": {
            "source_field_identity_sha256": "b" * 64,
            "vertical_display_transform": {"policy_id": "unchanged-test-policy"},
        },
    }
    scene = {
        "requested_room": {},
        "aligned_simulation_room": {},
        "camera_bounds": {"minimum_xyz": [-4.572, 0.0, -2.286], "maximum_xyz": [4.572, 2.0, 2.286]},
        "plant_bounds": {"minimum_xyz": [1.0, 0.0, -2.0], "maximum_xyz": [3.0, 1.0, 0.0]},
        "projected_footprint_bounds": {"minimum_xz": [1.0, -2.0], "maximum_xz": [3.0, 0.0]},
        "room_bounds": {"minimum_xz": [-4.572, -2.286], "maximum_xz": [4.572, 2.286]},
        "reference_plane": {"vertices_xyz": [[-4.572, 0.0, -2.286], [4.572, 0.0, 2.286]]},
        "ppfd_heatmap": {"target_coverage": {"leaf_ids": ["leaf-1"]}},
    }
    natural_fit = {
        "requested_room_m": {"length_m": 9.144, "width_m": 4.572},
        "axis_mapping": {},
        "x_axis": {"requested_axis": "length"},
        "y_axis": {"requested_axis": "width"},
        "plants": [
            {
                "plant_id": "plant-1",
                "origin_m": {
                    "aligned_x_m": 2.0,
                    "aligned_y_m": 1.0,
                    "requested_x_m": 2.0,
                    "requested_y_m": 1.0,
                },
            }
        ],
    }
    return CompactPlayback(
        bundle_path=Path("/canonical/case.fspm-compact"),
        manifest={"bundle_identity_sha256": identity},
        public_payload={
            "run_id": "c" * 32,
            "system_id": case.system_id,
            "metrics": {
                "mean_ppfd_umol_m2_s": 287.25,
                "analysis_scope": {
                    "value": "baseline_plus_multispectral_fspm"
                },
            },
        },
        payloads={
            "visualization_metadata": json.dumps(visualization).encode(),
            "viewer_scene": json.dumps(scene).encode(),
            "viewer_instances": struct.pack("<fff", 2.0, 3.0, -1.0),
            "fixture_transforms_00": struct.pack("<16f", *matrix),
            "natural_fit_layout": json.dumps(natural_fit).encode(),
            "heatmap": b"canonical-plain-pixels",
            "heatmap_overlay": b"canonical-overlay-pixels",
        },
        samples=samples,
    )


@pytest.mark.parametrize(
    ("system", "layout"),
    [
        (PROPOSED_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY),
        (CONVENTIONAL_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY),
        (CONVENTIONAL_SYSTEM_ID, ROLLING_BENCH_LAYOUT_POLICY),
        (HPS_SYSTEM_ID, PRACTICAL_LAYOUT_POLICY),
    ],
)
@pytest.mark.parametrize(
    ("requested_room", "canonical_room"),
    [
        ((15.0, 30.0), (30.0, 15.0)),
        ((30.0, 50.0), (50.0, 30.0)),
    ],
)
def test_requested_playback_keeps_every_public_coordinate_contract_canonical(
    system: str,
    layout: str,
    requested_room: tuple[float, float],
    canonical_room: tuple[float, float],
) -> None:
    request = _request(
        system=system,
        length_ft=requested_room[0],
        width_ft=requested_room[1],
        layout=layout,
    )
    case = resolve_fixed_case(request)
    canonical = _fake_playback(case)
    playback = RequestedCompactPlayback.create(
        canonical,
        case,
        requested_length_ft=requested_room[0],
        requested_width_ft=requested_room[1],
    )
    landscape = RequestedCompactPlayback.create(
        canonical,
        case,
        requested_length_ft=canonical_room[0],
        requested_width_ft=canonical_room[1],
    )

    assert playback.canonical is canonical
    assert playback.canonical_bundle_identity_sha256 == "a" * 64
    assert landscape.canonical_bundle_identity_sha256 == (
        playback.canonical_bundle_identity_sha256
    )
    assert landscape.presentation_identity_sha256 != (
        playback.presentation_identity_sha256
    )
    assert playback.metrics is canonical.metrics
    assert playback.coordinate_frame == landscape.coordinate_frame
    assert playback.coordinate_frame.axes_swapped is False
    assert playback.simulation_to_requested_position((2.0, 1.0, 3.0)) == (
        2.0,
        1.0,
        3.0,
    )
    assert playback.simulation_to_requested_direction((1.0, 0.0, 0.0)) == (
        1.0,
        0.0,
        0.0,
    )
    assert playback.simulation_bounds_to_requested(
        (-4.572, 4.572, -2.286, 2.286)
    ) == pytest.approx((-4.572, 4.572, -2.286, 2.286))

    assert playback.samples == landscape.samples
    assert [(sample.x_m, sample.y_m, sample.ppfd_umol_m2_s) for sample in playback.samples] == [
        (-2.0, -1.0, 100.0),
        (2.0, -1.0, 200.0),
        (-2.0, 1.0, 300.0),
        (2.0, 1.0, 400.0),
    ]
    filename, media_type, csv_bytes = playback.final_baseline_ppfd_csv()
    assert (filename, media_type) == ("ppfd.csv", "text/csv; charset=utf-8")
    assert csv_bytes == landscape.final_baseline_ppfd_csv()[2] == (
        b"x_m,y_m,z_m,ppfd_umol_m2_s\r\n"
        b"-2,-1,0.5,100\r\n2,-1,0.5,200\r\n"
        b"-2,1,0.5,300\r\n2,1,0.5,400\r\n"
    )
    scatter_metadata, scatter = playback.validated_ppfd_scatter()
    landscape_metadata, landscape_scatter = landscape.validated_ppfd_scatter()
    assert scatter == landscape_scatter
    assert struct.unpack("<12f", scatter) == pytest.approx(
        (-2, -1, 100, 2, -1, 200, -2, 1, 300, 2, 1, 400)
    )
    assert scatter_metadata["grid"]["resolution"] == {"x": 2, "y": 2}
    assert scatter_metadata["grid"]["x_centers_m"] == [-2.0, 2.0]
    assert scatter_metadata["grid"]["y_centers_m"] == [-1.0, 1.0]
    assert scatter_metadata["grid"]["extent_m"] == {
        "x_min": -3.0,
        "x_max": 3.0,
        "y_min": -2.0,
        "y_max": 2.0,
    }
    playback_provenance = scatter_metadata.pop("requested_orientation")
    landscape_provenance = landscape_metadata.pop("requested_orientation")
    assert scatter_metadata == landscape_metadata
    assert playback_provenance["requested_room_ft"] == {
        "length": requested_room[0],
        "width": requested_room[1],
    }
    assert playback_provenance["canonical_display_room_ft"] == {
        "length": canonical_room[0],
        "width": canonical_room[1],
    }
    assert landscape_provenance["canonical_display_room_ft"] == (
        playback_provenance["canonical_display_room_ft"]
    )
    for name in (
        "simulation_to_requested_rotation_degrees_about_z",
        "viewer_global_rotation_degrees_about_y",
        "heatmap_counterclockwise_quarter_turns",
    ):
        assert playback_provenance[name] == landscape_provenance[name] == 0
    assert scatter_metadata["display"]["normalization"] == {
        "method": "unchanged-test-policy"
    }
    assert scatter_metadata["scatter"]["vertical_display_transform"] == {
        "policy_id": "unchanged-test-policy"
    }

    assert playback.plant_instance_translations() == (
        landscape.plant_instance_translations()
    )
    assert struct.unpack(
        "<fff", playback.plant_instance_translations()
    ) == pytest.approx((2.0, 3.0, -1.0))
    fixture = struct.unpack("<16f", playback.fixture_transform_payloads()["fixture_transforms_00"])
    assert playback.fixture_transform_payloads() == (
        landscape.fixture_transform_payloads()
    )
    assert fixture[12:15] == pytest.approx((2.0, 3.0, -1.0))
    assert fixture[0:3] == pytest.approx((1.0, 0.0, 0.0))
    layout = playback.natural_fit_layout()
    landscape_layout = landscape.natural_fit_layout()
    layout.pop("requested_orientation")
    landscape_layout.pop("requested_orientation")
    assert layout == landscape_layout
    assert layout["plants"][0]["origin_m"]["requested_x_m"] == 2.0
    assert layout["plants"][0]["origin_m"]["requested_y_m"] == 1.0
    scene = playback.viewer_scene()
    landscape_scene = landscape.viewer_scene()
    scene_provenance = scene.pop("requested_orientation")
    landscape_scene.pop("requested_orientation")
    assert scene == landscape_scene
    assert scene["room_bounds"] == {"minimum_xz": [-4.572, -2.286], "maximum_xz": [4.572, 2.286]}
    assert scene["ppfd_heatmap"]["target_coverage"] == {"leaf_ids": ["leaf-1"]}
    assert scene_provenance["viewer_global_rotation_degrees_about_y"] == 0
    heatmaps = playback.heatmap_payloads()
    landscape_heatmaps = landscape.heatmap_payloads()
    assert heatmaps["plain_png"] is canonical.payloads["heatmap"]
    assert heatmaps["plain_png"] == landscape_heatmaps["plain_png"]
    assert heatmaps["overlay_png"] == landscape_heatmaps["overlay_png"]
    assert heatmaps["canonical_pixels_reused"] is True
    assert heatmaps["presentation"]["heatmap_counterclockwise_quarter_turns"] == 0


def test_plan_cli_is_read_only_and_execution_requires_explicit_authorization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "never-created-by-plan"

    class ForbiddenWorker:
        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("plan initialized the native worker")

    monkeypatch.setattr(
        "fspm_optics.precomputed_cli.BoundedNativeSweepExecutor", ForbiddenWorker
    )
    assert precomputed_main(["plan", "--output-root", str(output)]) == 0
    readable = capsys.readouterr().out
    assert readable.count("target=250") == 24
    assert "Canonical cases: 24" in readable
    assert not output.exists()

    monkeypatch.setattr(
        "fspm_optics.precomputed_cli.preflight_fixed_sweep_execution",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("unauthorized execute reached preflight")
        ),
    )
    assert precomputed_main(
        [
            "execute",
            "--output-root",
            str(output),
            "--runtime-root",
            str(tmp_path / "runtime"),
        ]
    ) == 2
    assert not output.exists()
