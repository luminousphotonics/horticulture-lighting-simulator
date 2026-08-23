from __future__ import annotations

import ast
from dataclasses import replace
import hashlib
from importlib.resources import files
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    EMITTED_PPF_BOUNDARIES,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    ConventionalRunRequest,
    HpsRunRequest,
    ProposedRunRequest,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.application.proposed import (
    ProposedRunError,
    ProposedRunOutcome,
    run_proposed_baseline,
    validate_success_artifacts,
)
from fspm_optics.application.proposed import _layout_identity as _complete_proposed_layout_identity
from fspm_optics.application.publication import (
    PublicationError,
    RunSciencePublication,
    publish_native_baseline_run,
    validate_emitted_ppf_contract,
)
from fspm_optics.application.source_state import PhysicalSourceState
from fspm_optics.application.target_control import (
    resolve_fspm_target_policy,
    resolve_global_source_dimming,
)
from fspm_optics.application.visualization import (
    VisualizationReference,
    build_ppfd_visualization_artifacts,
    reference_plane_field_identity,
)
from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_IES_RESOURCE_NAME,
    CONVENTIONAL_IES_SHA256,
    PRACTICAL_LAYOUT_POLICY,
)
from fspm_optics.fixtures.hps import (
    HPS_IES_RESOURCE_NAME,
    HPS_IES_SHA256,
)
from fspm_optics.fixtures.conventional_led.layout import (
    CONVENTIONAL_OVERLAY_BAR_COUNT,
    MountReferencePlaneSemantics,
    plan_conventional_layout_from_feet,
)
from fspm_optics.fixtures.hps.layout import (
    HPS_DEFAULT_MOUNT_HEIGHT_M,
    HPS_FIXTURE_LENGTH_X_M,
    HPS_FIXTURE_WIDTH_Y_M,
    HPS_LAYOUT_POLICY_ID,
    plan_hps_layout_from_feet,
)
from fspm_optics.fixtures.proposed_cob.source import (
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.radiance.options import radiance_options
from fspm_optics.transport.conventional_scalar import (
    ConventionalScalarExecutables,
    ConventionalScalarTransportRequest,
    format_ppfd_values_npz,
    plan_conventional_scalar_transport,
)
from fspm_optics.transport.hps_scalar import (
    HPS_FIXTURE_POWER_W,
    HPS_FIXTURE_PPE_UMOL_PER_J,
    HPS_FIXTURE_PPF_UMOL_S,
    HpsScalarExecutables,
    HpsScalarTransportRequest,
    plan_hps_scalar_transport,
)
from fspm_optics.transport.scalar_ppfd import PpfdMapSample


REPOSITORY = Path(__file__).parents[1]
RUN_ID = "e" * 32


def _common(system: str) -> dict[str, object]:
    return {
        "system": system,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "mounting_height_in": 24.0 if system == HPS_SYSTEM_ID else 18.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    }


@pytest.mark.parametrize(
    ("system", "request_type", "has_lighting_target"),
    [
        (PROPOSED_SYSTEM_ID, ProposedRunRequest, True),
        (CONVENTIONAL_SYSTEM_ID, ConventionalRunRequest, True),
        (HPS_SYSTEM_ID, HpsRunRequest, False),
    ],
)
def test_exact_discriminator_builds_one_closed_request_variant(
    system: str,
    request_type: type[object],
    has_lighting_target: bool,
) -> None:
    payload = _common(system)
    if has_lighting_target:
        payload["target_ppfd"] = 900.0
    request = parse_run_request(payload)

    assert isinstance(request, request_type)
    assert request.system == system
    assert ("target_ppfd_umol_m2_s" in request.to_dict()) is has_lighting_target


@pytest.mark.parametrize(
    "system",
    ["Proposed", "proposed_led_system", "competitor", "HPS", "1000W HPS"],
)
def test_system_aliases_and_case_variants_are_rejected(system: str) -> None:
    with pytest.raises(RequestValidationError, match="exactly"):
        parse_run_request(_common(system))


@pytest.mark.parametrize("value", [None, 0.0, 1000.0])
def test_hps_rejects_any_lighting_target_key(value: object) -> None:
    with pytest.raises(RequestValidationError) as caught:
        parse_run_request(_common(HPS_SYSTEM_ID) | {"target_ppfd": value})
    assert caught.value.field == "target_ppfd"


@pytest.mark.parametrize("value", [None, [], {}])
def test_automatic_fspm_mode_rejects_hidden_or_malformed_override_state(
    value: object,
) -> None:
    payload = _common(PROPOSED_SYSTEM_ID) | {"target_ppfd": 900.0}
    if value is None:
        payload["fspm_target_ppfd"] = None
    else:
        payload["fspm_target_mode"] = value
    with pytest.raises(RequestValidationError):
        parse_run_request(payload)


@pytest.mark.parametrize(
    ("system", "replacement"),
    [
        (PROPOSED_SYSTEM_ID, CONVENTIONAL_SYSTEM_ID),
        (CONVENTIONAL_SYSTEM_ID, HPS_SYSTEM_ID),
        (HPS_SYSTEM_ID, PROPOSED_SYSTEM_ID),
    ],
)
def test_typed_requests_reject_direct_discriminator_mutation(
    system: str,
    replacement: str,
) -> None:
    payload = _common(system)
    if system != HPS_SYSTEM_ID:
        payload["target_ppfd"] = 900.0
    request = parse_run_request(payload)
    with pytest.raises(RequestValidationError):
        replace(request, system=replacement)


@pytest.mark.parametrize(
    "payload",
    [
        _common(PROPOSED_SYSTEM_ID),
        _common(CONVENTIONAL_SYSTEM_ID),
        _common(HPS_SYSTEM_ID) | {"layout_policy": "full_fit"},
        _common(HPS_SYSTEM_ID) | {"ies_variant": "legacy"},
        _common(PROPOSED_SYSTEM_ID)
        | {"target_ppfd": 900.0, "workspace": "/tmp/escape"},
    ],
)
def test_missing_or_hidden_expansive_fields_fail_closed(
    payload: dict[str, object],
) -> None:
    with pytest.raises(RequestValidationError):
        parse_run_request(payload)


def test_dispatch_uses_validated_variant_without_string_rediscrimination(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fspm_optics.application import systems

    calls: list[str] = []

    def fake(**kwargs: object) -> ProposedRunOutcome:
        request = kwargs["request"]
        calls.append(request.system)  # type: ignore[union-attr]
        fixed_output = request.system == HPS_SYSTEM_ID  # type: ignore[union-attr]
        return ProposedRunOutcome(
            RUN_ID,
            {},
            {},
            None if fixed_output else True,
            None,
            system_id=request.system,  # type: ignore[union-attr]
        )

    monkeypatch.setattr(systems, "run_proposed_baseline", fake)
    monkeypatch.setattr(systems, "run_conventional_baseline", fake)
    monkeypatch.setattr(systems, "run_hps_baseline", fake)
    for system in (
        PROPOSED_SYSTEM_ID,
        CONVENTIONAL_SYSTEM_ID,
        HPS_SYSTEM_ID,
    ):
        payload = _common(system)
        if system != HPS_SYSTEM_ID:
            payload["target_ppfd"] = 900.0
        outcome = systems.run_system_baseline(
            run_id=RUN_ID,
            request=parse_run_request(payload),
            workspace=tmp_path,
            event_sink=lambda *_args: None,
        )
        assert outcome.system_id == system
    assert calls == [PROPOSED_SYSTEM_ID, CONVENTIONAL_SYSTEM_ID, HPS_SYSTEM_ID]


def test_result_discriminator_enforces_hps_null_feasibility_and_led_boolean() -> None:
    hps_metrics = {
        "system_id": HPS_SYSTEM_ID,
        "target_feasible": None,
        "target_infeasibility": None,
    }
    hps_manifest = {
        "system_id": HPS_SYSTEM_ID,
        "request": {"system": HPS_SYSTEM_ID},
        "target_control": {"lighting_target_supported": False},
    }
    outcome = ProposedRunOutcome(
        RUN_ID,
        hps_metrics,
        hps_manifest,
        None,
        None,
        system_id=HPS_SYSTEM_ID,
    )
    assert outcome.target_feasible is None

    with pytest.raises(ValueError, match="must be null"):
        ProposedRunOutcome(
            RUN_ID,
            hps_metrics,
            hps_manifest,
            True,
            None,
            system_id=HPS_SYSTEM_ID,
        )
    with pytest.raises(ValueError, match="boolean feasibility"):
        ProposedRunOutcome(
            RUN_ID,
            {},
            {},
            None,
            None,
            system_id=PROPOSED_SYSTEM_ID,
        )
    with pytest.raises(ValueError, match="system discriminator"):
        ProposedRunOutcome(
            RUN_ID,
            {"system_id": CONVENTIONAL_SYSTEM_ID},
            {},
            True,
            None,
            system_id=PROPOSED_SYSTEM_ID,
        )


def test_conventional_practical_layout_and_eight_bar_overlay_are_authoritative() -> None:
    first = plan_conventional_layout_from_feet(
        10.0, 10.0, policy=PRACTICAL_LAYOUT_POLICY
    )
    repeated = plan_conventional_layout_from_feet(
        10.0, 10.0, policy=PRACTICAL_LAYOUT_POLICY
    )
    overlay = first.authoritative_overlay_plan()
    bar_width = 1.087 / 15.0

    assert first.layout_id == repeated.layout_id
    assert first.policy.name == PRACTICAL_LAYOUT_POLICY
    assert len(overlay.rectangles) == (
        len(first.fixtures) * CONVENTIONAL_OVERLAY_BAR_COUNT
    )
    assert {item.width_x_m for item in overlay.rectangles} == {1.190}
    assert {item.height_y_m for item in overlay.rectangles} == {bar_width}
    assert {item.orientation_degrees for item in overlay.rectangles} == {0.0}
    assert overlay.metadata["transport_geometry_affected"] is False


def test_conventional_pretrace_factor_changes_carrier_not_layout(
    tmp_path: Path,
) -> None:
    executables = ConventionalScalarExecutables(
        tmp_path / "ies2rad", tmp_path / "oconv", tmp_path / "rtrace"
    )
    full = plan_conventional_scalar_transport(
        ConventionalScalarTransportRequest.from_feet(
            workspace=tmp_path / "full",
            global_dimming_factor=1.0,
            quality_profile="direct",
        ),
        executables=executables,
    )
    dimmed = plan_conventional_scalar_transport(
        ConventionalScalarTransportRequest.from_feet(
            workspace=tmp_path / "dimmed",
            global_dimming_factor=0.5,
            quality_profile="direct",
        ),
        executables=executables,
    )

    assert full.layout.layout_id == dimmed.layout.layout_id
    assert full.source.carrier_scale.ies2rad_multiplier == 1716.0 * 179.0
    assert dimmed.source.carrier_scale.ies2rad_multiplier == 858.0 * 179.0
    assert dimmed.source.transported_downward_ppf_umol_s_per_fixture == 858.0
    assert "-af" not in full.radiance_options
    assert dimmed.source.scientific_payload()["post_trace_scale"] is None


def test_conventional_global_factor_is_linear_capped_and_structurally_infeasible() -> None:
    feasible = resolve_global_source_dimming(
        requested_target_ppfd=500.0,
        full_output_mean_ppfd=1000.0,
        system_id=CONVENTIONAL_SYSTEM_ID,
    )
    unreachable = resolve_global_source_dimming(
        requested_target_ppfd=1250.0,
        full_output_mean_ppfd=1000.0,
        system_id=CONVENTIONAL_SYSTEM_ID,
    )

    assert feasible.raw_factor == feasible.dimming_factor == 0.5
    assert feasible.feasible is True
    assert feasible.infeasibility is None
    assert unreachable.raw_factor == 1.25
    assert unreachable.dimming_factor == 1.0
    assert unreachable.feasible is False
    assert unreachable.infeasibility == {
        "code": "requested_target_exceeds_full_output_mean",
        "system_id": CONVENTIONAL_SYSTEM_ID,
        "message": "Requested target exceeds the system full-output mean.",
        "requested_target_ppfd": 1250.0,
        "maximum_achievable_mean_ppfd": 1000.0,
    }


@pytest.mark.parametrize(
    (
        "target_ppfd",
        "lighting_target_mode",
        "expected_output_fraction",
        "expected_feasible",
    ),
    [
        (500.0, "mean_target", 0.5, True),
        (1250.0, "mean_target", 1.0, False),
        (500.0, "target_capped", 0.5, True),
        (1250.0, "target_capped", 1.0, True),
    ],
)
@pytest.mark.parametrize("layout_mode", ["practical", "rolling_bench"])
def test_conventional_orchestrator_traces_once_and_scales_float64_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    target_ppfd: float,
    lighting_target_mode: str,
    expected_output_fraction: float,
    expected_feasible: bool,
    layout_mode: str,
) -> None:
    from fspm_optics.application import systems

    layout = plan_conventional_layout_from_feet(
        10.0,
        10.0,
        policy=layout_mode,
        mount=MountReferencePlaneSemantics(
            reference_plane_z_m=0.005,
            mount_height_m=0.5334,
        ),
    )
    factors: list[float] = []
    transport_requests: list[ConventionalScalarTransportRequest] = []
    publications: list[RunSciencePublication] = []

    def fake_execute(
        transport_request: ConventionalScalarTransportRequest,
        _runner: object,
    ) -> object:
        transport_requests.append(transport_request)
        factor = transport_request.global_dimming_factor
        factors.append(factor)
        mean = 1000.0 * factor
        samples = (
            PpfdMapSample(-0.5, -0.5, 0.005, mean),
            PpfdMapSample(0.5, -0.5, 0.005, mean),
            PpfdMapSample(-0.5, 0.5, 0.005, mean),
            PpfdMapSample(0.5, 0.5, 0.005, mean),
        )
        metrics = SimpleNamespace(
            mean_ppfd_umol_m2_s=mean,
            to_payload=lambda: {"mean_ppfd_umol_m2_s": mean},
        )
        artifact_root = transport_request.workspace / "conventional_scalar"
        artifact_root.mkdir(parents=True)
        scalar_summary = artifact_root / "scalar_transport_summary.json"
        scalar_summary.write_text('{"success":true}\n', encoding="utf-8")
        command_summary = artifact_root / "command_provenance_summary.json"
        command_summary.write_text(
            json.dumps(
                {
                    "commands": [
                        {"label": "convert_conventional_unit_downward_flux_ies"},
                        {"label": "compile_conventional_scalar_scene"},
                        {"label": "baseline_scalar_ppfd_rtrace"},
                    ]
                }
            ),
            encoding="utf-8",
        )
        ppfd_npz = artifact_root / "ppfd_values.npz"
        ppfd_bytes = format_ppfd_values_npz(samples)
        ppfd_npz.write_bytes(ppfd_bytes)
        plan = SimpleNamespace(
            layout=layout,
            request=transport_request,
            fixture_occlusion=SimpleNamespace(
                identity_sha256="fixture-occlusion",
                shapes=(object(),),
                scientific_payload=lambda: {
                    "identity_sha256": "fixture-occlusion"
                },
            ),
            source=SimpleNamespace(
                source_plan_id=f"source-{factor}",
                transported_downward_ppf_umol_s_per_fixture=1716.0 * factor,
                carrier_scale=SimpleNamespace(
                    ies2rad_multiplier=1716.0 * 179.0 * factor
                ),
                original_ies=SimpleNamespace(tested_input_watts=663.2),
                derived_ies=SimpleNamespace(
                    original_flux=SimpleNamespace(
                        upward_fraction_of_full_sphere=0.01
                    )
                ),
            ),
            paths=SimpleNamespace(
                scalar_transport_summary=scalar_summary,
                command_provenance_summary=command_summary,
                ppfd_npz=ppfd_npz,
            ),
            transport_identity=f"transport-{factor}",
            result_identity=f"result-{factor}",
        )
        return SimpleNamespace(
            plan=plan,
            samples=samples,
            metrics=metrics,
            scalar_transport_summary={"radiance": {}},
            ppfd_npz_sha256=hashlib.sha256(ppfd_bytes).hexdigest(),
        )

    def fake_publish(
        _root: Path,
        *,
        science: RunSciencePublication,
        **_kwargs: object,
    ) -> object:
        publications.append(science)
        return SimpleNamespace(metrics={}, manifest={})

    monkeypatch.setattr(systems, "execute_conventional_scalar_transport", fake_execute)
    monkeypatch.setattr(systems, "publish_native_baseline_run", fake_publish)
    request = parse_run_request(
        _common(CONVENTIONAL_SYSTEM_ID)
        | {
            "target_ppfd": target_ppfd,
            "lighting_target_mode": lighting_target_mode,
            "layout_mode": layout_mode,
            "mounting_height_in": 21.0,
        }
    )
    outcome = systems.run_conventional_baseline(
        run_id=RUN_ID,
        request=request,
        workspace=tmp_path,
        event_sink=lambda *_args: None,
        runner=SimpleNamespace(),
    )

    assert factors == [1.0]
    assert [item.reference_plane_z_m for item in transport_requests] == [
        0.005
    ]
    assert [item.mount_height_m for item in transport_requests] == [
        0.5334
    ]
    assert outcome.target_feasible is expected_feasible
    assert len(publications) == 1
    science = publications[0]
    assert science.target_control["post_trace_scaling"] is True
    assert science.target_control["dimming_factor"] == expected_output_fraction
    assert science.target_control["lighting_target_mode"] == lighting_target_mode
    assert science.target_control["full_output_mean_ppfd_umol_m2_s"] == 1000.0
    assert science.target_control["full_output_maximum_ppfd_umol_m2_s"] == 1000.0
    assert science.target_control["achieved_mean_ppfd_umol_m2_s"] == (
        1000.0 * expected_output_fraction
    )
    assert science.target_control["achieved_maximum_ppfd_umol_m2_s"] == (
        1000.0 * expected_output_fraction
    )
    assert science.transport_policy["target_dependent_fixture_count"] is False
    assert science.transport_policy["fixture_level_optimization"] is False
    assert science.operating_point["fixture"] == {
        "rated_electrical_power_w": 660.0,
        "tested_ies_input_w_provenance_only": 663.2,
        "full_output_ppe_umol_per_j": 2.6,
        "full_output_ppf_umol_s": 1716.0,
    }
    assert science.target_control["effective_power_w"] == pytest.approx(
        4 * 660.0 * expected_output_fraction
    )
    assert science.target_control["effective_ppf_umol_s"] == pytest.approx(
        4 * 1716.0 * expected_output_fraction
    )
    ppf = science.operating_point["ppf"]
    assert ppf["emitted_umol_s"] == ppf["effective_umol_s"]
    assert ppf["emission_boundary_id"] == (
        EMITTED_PPF_BOUNDARIES[CONVENTIONAL_SYSTEM_ID]["id"]
    )
    assert science.counts["modules"] == 0
    assert science.engine_provenance["original_ies_resource"] == (
        CONVENTIONAL_IES_RESOURCE_NAME
    )
    assert science.engine_provenance["original_ies_sha256"] == (
        CONVENTIONAL_IES_SHA256
    )
    assert science.mounting_height == request.mounting_geometry.to_payload()
    assert science.full_output_schedule["layout_policy"] == layout_mode
    assert science.transport_policy["post_trace_scaling"] is True
    assert science.operating_point["post_trace_scale"] == expected_output_fraction
    source_operation = science.physical_source_state.payload()["source_operation"]
    assert source_operation["policy_id"] == (
        "conventional_global_stage_a_source_operation_v3"
    )
    assert source_operation["global_dimming_applied_before_trace"] is False
    assert source_operation["global_dimming_applied_after_trace"] is True
    assert source_operation["global_dimming_factor"] == expected_output_fraction
    assert science.engine_provenance["source_rerun_performed"] is False
    assert science.engine_provenance["native_stage_a_command_counts"] == {
        "ies2rad_conversion": 1,
        "fixture_shape_compilation": 1,
        "scalar_scene_compilation": 1,
        "baseline_rtrace": 1,
    }
    assert science.engine_artifacts["conventional_full_output_summary"] != (
        science.engine_artifacts["conventional_final_summary"]
    )
    scaled_summary = json.loads(
        (tmp_path / science.engine_artifacts["conventional_final_summary"])
        .read_text(encoding="utf-8")
    )
    assert scaled_summary["native_commands_after_full_output_trace"] == []
    assert scaled_summary["authority"]["global_output_fraction"] == (
        expected_output_fraction
    )
    assert scaled_summary["scaled_result"]["metrics"][
        "mean_ppfd_umol_m2_s"
    ] == (1000.0 * expected_output_fraction)
    assert scaled_summary["native_stage_a_command_counts"] == {
        "ies2rad_conversion": 1,
        "fixture_shape_compilation": 1,
        "scalar_scene_compilation": 1,
        "baseline_rtrace": 1,
    }
    if expected_feasible:
        assert science.target_infeasibility is None
        assert {sample.ppfd_umol_m2_s for sample in science.samples} == {
            1000.0 * expected_output_fraction
        }
        if lighting_target_mode == "target_capped":
            assert science.target_control["cap_compliant"] is True
            assert science.target_control["achieved_maximum_ppfd_umol_m2_s"] <= (
                target_ppfd + 1e-6
            )
    else:
        assert science.target_infeasibility is not None
        assert science.target_infeasibility["code"] == (
            "requested_target_exceeds_full_output_mean"
        )
        assert {sample.ppfd_umol_m2_s for sample in science.samples} == {1000.0}


def test_hps_overlay_and_full_output_plan_are_fixed(tmp_path: Path) -> None:
    layout = plan_hps_layout_from_feet(10.0, 10.0)
    overlay = layout.authoritative_overlay_plan()
    plan = plan_hps_scalar_transport(
        HpsScalarTransportRequest.from_feet(
            workspace=tmp_path / "hps", quality_profile="direct"
        ),
        executables=HpsScalarExecutables(
            tmp_path / "ies2rad", tmp_path / "oconv", tmp_path / "rtrace"
        ),
    )

    assert layout.policy_id == HPS_LAYOUT_POLICY_ID
    assert layout.mount.mount_height_m == HPS_DEFAULT_MOUNT_HEIGHT_M == 0.6096
    assert {item.width_x_m for item in overlay.rectangles} == {
        HPS_FIXTURE_LENGTH_X_M
    }
    assert {item.height_y_m for item in overlay.rectangles} == {
        HPS_FIXTURE_WIDTH_Y_M
    }
    assert {item.orientation_degrees for item in overlay.rectangles} == {0.0}
    assert HPS_FIXTURE_POWER_W == 1045.0
    assert HPS_FIXTURE_PPE_UMOL_PER_J == 1750.0 / 1045.0
    assert HPS_FIXTURE_PPF_UMOL_S == 1750.0
    assert plan.source.carrier_scale.fixture_ppf_umol_s == 1750.0
    assert "-af" not in plan.radiance_options
    assert plan.source.scientific_payload()["post_trace_scale"] is None


def test_hps_orchestrator_traces_once_for_automatic_and_explicit_fspm(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from fspm_optics.application import systems

    layout = plan_hps_layout_from_feet(
        10.0,
        10.0,
        reference_plane_z_m=0.005,
        mount_height_m=0.762,
    )
    transport_requests: list[HpsScalarTransportRequest] = []
    publications: list[tuple[HpsRunRequest, RunSciencePublication]] = []
    samples = (
        PpfdMapSample(-0.5, -0.5, 0.005, 850.0),
        PpfdMapSample(0.5, -0.5, 0.005, 850.0),
        PpfdMapSample(-0.5, 0.5, 0.005, 850.0),
        PpfdMapSample(0.5, 0.5, 0.005, 850.0),
    )

    def fake_execute(
        transport_request: HpsScalarTransportRequest,
        _runner: object,
    ) -> object:
        transport_requests.append(transport_request)
        metrics = SimpleNamespace(
            mean_ppfd_umol_m2_s=850.0,
            to_payload=lambda: {"mean_ppfd_umol_m2_s": 850.0},
        )
        return SimpleNamespace(
            plan=SimpleNamespace(
                layout=layout,
                transport_identity="hps-transport",
                result_identity="hps-result-with-occlusion",
                fixture_occlusion=SimpleNamespace(
                    identity_sha256="a" * 64,
                    scientific_payload=lambda: {
                        "identity_sha256": "a" * 64,
                        "system_id": "hps",
                    },
                ),
                source=SimpleNamespace(
                    source_plan_id="hps-source",
                    derived_ies=SimpleNamespace(
                        to_payload=lambda: {"excluded_upward_fraction": 0.0}
                    ),
                ),
                paths=SimpleNamespace(
                    scalar_transport_summary=(
                        transport_request.workspace / "summary.json"
                    )
                ),
            ),
            samples=samples,
            metrics=metrics,
            scalar_transport_summary={"radiance": {}},
        )

    def fake_publish(
        _root: Path,
        *,
        request: HpsRunRequest,
        science: RunSciencePublication,
        **_kwargs: object,
    ) -> object:
        publications.append((request, science))
        return SimpleNamespace(metrics={}, manifest={})

    monkeypatch.setattr(systems, "execute_hps_scalar_transport", fake_execute)
    monkeypatch.setattr(systems, "publish_native_baseline_run", fake_publish)
    automatic = parse_run_request(
        _common(HPS_SYSTEM_ID) | {"mounting_height_in": 30.0}
    )
    explicit = parse_run_request(
        _common(HPS_SYSTEM_ID)
        | {
            "fspm_target_mode": "override",
            "fspm_target_ppfd": 700.0,
            "mounting_height_in": 30.0,
        }
    )
    for request in (automatic, explicit):
        outcome = systems.run_hps_baseline(
            run_id=RUN_ID,
            request=request,
            workspace=tmp_path,
            event_sink=lambda *_args: None,
            runner=SimpleNamespace(),
        )
        assert outcome.target_feasible is None

    assert len(transport_requests) == 2
    assert all(not hasattr(item, "target_ppfd_umol_m2_s") for item in transport_requests)
    assert [item.quality_profile for item in transport_requests] == ["direct", "direct"]
    assert [item.reference_plane_z_m for item in transport_requests] == [0.005, 0.005]
    assert [item.mount_height_m for item in transport_requests] == [0.762, 0.762]
    assert [request.fspm_target_mode for request, _science in publications] == [
        "automatic",
        "override",
    ]
    for _request, science in publications:
        assert science.samples == samples
        assert science.target_control["lighting_target_supported"] is False
        assert science.target_control["operation"] == "full_output_only"
        assert science.visualization_reference.kind == (
            "achieved_final_baseline_mean"
        )
        assert science.visualization_reference.center_ppfd_umol_m2_s == 850.0
        assert science.operating_point["fixture"] == {
            "electrical_power_w": 1045.0,
            "ppe_umol_per_j": 1750.0 / 1045.0,
            "ppf_umol_s": 1750.0,
            "electrical_power_role": "tested_system_input",
            "ppf_role": "documented_initial_lamp_PAR_PPF",
            "ppe_role": "computed_system_PPF_over_input_power",
            "nominal_lamp_class_power_w": 1000.0,
            "nominal_lamp_class_is_provenance_only": True,
        }
        ppf = science.operating_point["ppf"]
        assert ppf["emitted_umol_s"] == ppf["effective_umol_s"] == 4 * 1750.0
        assert ppf["emission_boundary_id"] == (
            EMITTED_PPF_BOUNDARIES[HPS_SYSTEM_ID]["id"]
        )
        assert science.counts["modules"] == 0
        assert science.transport_policy["dimming"] is False
        assert science.transport_policy["post_trace_scaling"] is False
        assert science.transport_policy["ppe_matching"] is False
        assert science.transport_policy["peak_cap_control"] is False
        assert science.transport_policy["output_equalization"] is False
        authority = science.full_output_schedule["source_authority"]
        assert authority["initial_lamp_par_ppf_umol_s"] == 1750.0
        assert authority["tested_system_input_power_w"] == 1045.0
        assert authority["computed_system_ppe_umol_per_j"] == 1750.0 / 1045.0
        assert authority["radiance_carrier_per_fixture"] == 313250.0
        assert authority["excluded_upward_ies_fraction"] == 0.0
        assert authority["fixed_output"] is True
        assert science.engine_provenance["original_ies_resource"] == (
            HPS_IES_RESOURCE_NAME
        )
        assert science.engine_provenance["original_ies_sha256"] == (
            HPS_IES_SHA256
        )
        assert science.engine_provenance["result_identity"] == (
            "hps-result-with-occlusion"
        )
        assert science.engine_provenance["fixture_occlusion"] == {
            "identity_sha256": "a" * 64,
            "system_id": "hps",
        }
        assert science.physical_source_state.payload()["source_operation"][
            "fixture_occlusion_identity"
        ] == "a" * 64
        assert science.mounting_height == _request.mounting_geometry.to_payload()


def test_hps_visual_reference_is_achieved_mean_and_preserves_raw_identity() -> None:
    samples = tuple(
        PpfdMapSample(x, y, 0.005, value)
        for (x, y), value in zip(
            ((-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)),
            (700.0, 800.0, 900.0, 1000.0),
            strict=True,
        )
    )
    layout = plan_hps_layout_from_feet(10.0, 10.0)
    artifacts = build_ppfd_visualization_artifacts(
        run_id=RUN_ID,
        samples=samples,
        layout=None,
        layout_identity=layout.to_payload(),
        overlay_plan=layout.authoritative_overlay_plan(),
        reference=VisualizationReference.achieved_baseline_mean(850.0),
    )
    display = artifacts.metadata["display"]

    assert artifacts.field_sha256 == reference_plane_field_identity(samples)
    assert artifacts.sample_count == len(samples)
    assert display["color_limits_ppfd_umol_m2_s"] == [650.0, 1050.0]
    assert display["color_limit_policy"] == (
        "achieved_final_baseline_mean_plus_or_minus_200"
    )
    assert "requested_lighting_target_ppfd_umol_m2_s" not in display
    assert artifacts.metadata["scatter"]["source_field_identity_sha256"] == (
        artifacts.field_sha256
    )


@pytest.mark.parametrize(
    ("system_id", "power", "ppf"),
    [
        (
            PROPOSED_SYSTEM_ID,
            {"effective_w": 90.0},
            {
                "emitted_umol_s": 234.0,
                "emission_boundary_id": EMITTED_PPF_BOUNDARIES[
                    PROPOSED_SYSTEM_ID
                ]["id"],
                "emission_boundary_description": EMITTED_PPF_BOUNDARIES[
                    PROPOSED_SYSTEM_ID
                ]["description"],
                "completed_aperture_fixture_ppe_umol_per_j": 2.6,
                "effective_modeled_completed_aperture_par_ppf_umol_s": 234.0,
                "internal_source_ppe_umol_per_j": 3.120711832761085,
                "effective_internal_par_ppf_umol_s": 288.8735972902508,
            },
        ),
        (
            CONVENTIONAL_SYSTEM_ID,
            {"effective_w": 660.0},
            {
                "emitted_umol_s": 1716.0,
                "emission_boundary_id": EMITTED_PPF_BOUNDARIES[
                    CONVENTIONAL_SYSTEM_ID
                ]["id"],
                "emission_boundary_description": EMITTED_PPF_BOUNDARIES[
                    CONVENTIONAL_SYSTEM_ID
                ]["description"],
                "ppe_umol_per_j": 2.6,
                "effective_umol_s": 1716.0,
            },
        ),
        (
            HPS_SYSTEM_ID,
            {"effective_w": 1045.0},
            {
                "emitted_umol_s": 1750.0,
                "emission_boundary_id": EMITTED_PPF_BOUNDARIES[
                    HPS_SYSTEM_ID
                ]["id"],
                "emission_boundary_description": EMITTED_PPF_BOUNDARIES[
                    HPS_SYSTEM_ID
                ]["description"],
                "ppe_umol_per_j": 1750.0 / 1045.0,
                "effective_umol_s": 1750.0,
            },
        ),
    ],
)
def test_all_systems_satisfy_common_emitted_ppf_publication_contract(
    system_id: str,
    power: dict[str, float],
    ppf: dict[str, object],
) -> None:
    validate_emitted_ppf_contract(system_id, power=power, ppf=ppf)


def test_proposed_common_emitted_ppf_maps_only_to_completed_aperture() -> None:
    source = Path(run_proposed_baseline.__code__.co_filename).read_text(
        encoding="utf-8"
    )
    function_source = source[source.index("def run_proposed_baseline(") :]
    operating_point_source = function_source[
        function_source.index("    operating_point = {") :
        function_source.index("    source_state = PhysicalSourceState.create(")
    ]

    assert '"emitted_umol_s": (' in operating_point_source
    assert (
        "controlled.effective_modeled_completed_aperture_par_ppf_umol_s"
        in operating_point_source
    )
    assert '"effective_internal_par_ppf_umol_s": (' in operating_point_source
    assert '"effective_umol_s"' not in operating_point_source


@pytest.mark.parametrize("invalid", [None, "234", float("nan"), float("inf")])
def test_emitted_ppf_publication_contract_fails_closed(invalid: object) -> None:
    ppf = {
        "emitted_umol_s": invalid,
        "emission_boundary_id": EMITTED_PPF_BOUNDARIES[PROPOSED_SYSTEM_ID][
            "id"
        ],
        "emission_boundary_description": EMITTED_PPF_BOUNDARIES[
            PROPOSED_SYSTEM_ID
        ]["description"],
        "completed_aperture_fixture_ppe_umol_per_j": 2.6,
        "effective_modeled_completed_aperture_par_ppf_umol_s": 234.0,
    }
    with pytest.raises(PublicationError, match="ppf.emitted_umol_s"):
        validate_emitted_ppf_contract(
            PROPOSED_SYSTEM_ID,
            power={"effective_w": 90.0},
            ppf=ppf,
        )


def test_proposed_internal_source_ppf_cannot_fill_emitted_boundary() -> None:
    ppf = {
        "emitted_umol_s": 90.0 * 3.120711832761085,
        "emission_boundary_id": EMITTED_PPF_BOUNDARIES[PROPOSED_SYSTEM_ID][
            "id"
        ],
        "emission_boundary_description": EMITTED_PPF_BOUNDARIES[
            PROPOSED_SYSTEM_ID
        ]["description"],
        "completed_aperture_fixture_ppe_umol_per_j": 2.6,
        "effective_modeled_completed_aperture_par_ppf_umol_s": 234.0,
        "effective_internal_par_ppf_umol_s": 90.0 * 3.120711832761085,
    }
    with pytest.raises(PublicationError, match="emission boundary"):
        validate_emitted_ppf_contract(
            PROPOSED_SYSTEM_ID,
            power={"effective_w": 90.0},
            ppf=ppf,
        )


def test_shared_manifest_hashes_are_recomputed_before_promotion(
    tmp_path: Path,
) -> None:
    request = parse_run_request(
        _common(PROPOSED_SYSTEM_ID) | {"target_ppfd": 900.0}
    )
    layout = generate_proposed_led_layout(
        10.0,
        10.0,
        mount_z_m=request.mounting_geometry.emitting_aperture_plane_z_m,
    )
    samples = tuple(
        PpfdMapSample(x, y, 0.005, value)
        for (x, y), value in zip(
            ((-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)),
            (700.0, 800.0, 900.0, 1000.0),
            strict=True,
        )
    )
    power = {"full_output_w": 100.0, "effective_w": 90.0}
    ppf = {
        "emitted_umol_s": 234.0,
        "emission_boundary_id": (
            EMITTED_PPF_BOUNDARIES[PROPOSED_SYSTEM_ID]["id"]
        ),
        "emission_boundary_description": (
            EMITTED_PPF_BOUNDARIES[PROPOSED_SYSTEM_ID]["description"]
        ),
        "internal_source_ppe_umol_per_j": 3.120711832761085,
        "completed_aperture_fixture_ppe_umol_per_j": 2.6,
        "full_output_internal_par_ppf_umol_s": 320.9706636558342,
        "effective_internal_par_ppf_umol_s": 288.8735972902508,
        "full_output_modeled_completed_aperture_par_ppf_umol_s": 260.0,
        "effective_modeled_completed_aperture_par_ppf_umol_s": 234.0,
    }
    proposed_source = resolve_proposed_source_authority().to_dict()
    operating_point = {
        "power": power,
        "ppf": ppf,
        "proposed_source": proposed_source,
    }
    science = RunSciencePublication(
        system_id=PROPOSED_SYSTEM_ID,
        samples=samples,
        layout_identity=_complete_proposed_layout_identity(layout),
        overlay_plan=layout.authoritative_overlay_plan(),
        visualization_reference=VisualizationReference.requested_target(900.0),
        quality_options=tuple(radiance_options(request.quality)),
        target_control={
            "requested_target_ppfd_umol_m2_s": 900.0,
            "full_output_mean_ppfd_umol_m2_s": 1000.0,
            "achieved_mean_ppfd_umol_m2_s": 850.0,
            "dimming_factor": 0.9,
            "feasible": True,
            "infeasibility": None,
        },
        full_output_schedule={
            "policy": "test_full_output",
            "fixture_count": 1,
        },
        operating_point=operating_point,
        counts={
            "fixture_groups": 1,
            "fixtures": 1,
            "modules": len(layout.modules),
            "control_zones": layout.control_zone_count,
        },
        transport_policy={"backend": "test_basis"},
        runtime_provenance={"runtime": "test"},
        engine_provenance={
            "engine": "test",
            "mounting_height": request.mounting_geometry.to_payload(),
        },
        engine_artifacts={},
        target_feasible=True,
        target_infeasibility=None,
        physical_source_state=PhysicalSourceState.create(
            system_id=PROPOSED_SYSTEM_ID,
            layout_identity=_complete_proposed_layout_identity(layout),
            full_output_schedule={
                "policy": "test_full_output",
                "fixture_count": 1,
            },
            operating_point=operating_point,
            source_operation={
                "policy_id": "test_stage_a_source",
                "proposed_source": proposed_source,
                "proposed_layout_mode": layout.proposed_layout_mode.value,
                "fixture_policy_id": layout.fixture_policy_id,
                "proposed_ring_mode": layout.proposed_ring_mode.value,
                "module_pattern_id": layout.module_pattern_id,
                "mechanical_envelope": {
                    "id": layout.mechanical_envelope_id,
                    "width_x_m": layout.module_footprint_x_m,
                    "height_y_m": layout.module_footprint_y_m,
                },
                "fixture_asset_set_id": layout.fixture_asset_set_id,
                "mounting_height": request.mounting_geometry.to_payload(),
            },
        ),
        mounting_height=request.mounting_geometry.to_payload(),
    )
    publish_native_baseline_run(
        tmp_path,
        run_id=RUN_ID,
        request=request,
        science=science,
        event_sink=lambda *_args: None,
    )
    published_manifest = json.loads(
        (tmp_path / "manifest.json").read_text(encoding="utf-8")
    )
    published_metrics = json.loads(
        (tmp_path / "metrics.json").read_text(encoding="utf-8")
    )
    mounting = request.mounting_geometry.to_payload()
    mounting_sha256 = hashlib.sha256(
        json.dumps(
            mounting,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    assert published_manifest["schema_version"] == 4
    assert published_metrics["schema_version"] == 4
    assert published_metrics["analysis_scope"]["value"] == "baseline_ppfd"
    assert published_metrics["baseline_leaf_position_uniformity"][
        "artifact"
    ] == "baseline-leaf-position-uniformity.v1.json"
    assert published_manifest["artifacts"][
        "baseline_leaf_position_uniformity"
    ] == "baseline-leaf-position-uniformity.v1.json"
    assert published_manifest["scientific_identity"][
        "baseline_leaf_position_uniformity_derivation_sha256"
    ] == published_metrics["baseline_leaf_position_uniformity"][
        "derivation_identity_sha256"
    ]
    assert published_manifest["request"]["mounting_height_in"] == 18.0
    assert published_manifest["mounting_height"] == mounting
    assert published_metrics["mounting_height"] == mounting
    assert published_manifest["scientific_identity"]["mounting_height_sha256"] == (
        mounting_sha256
    )
    assert published_manifest["viewer_publication"][
        "mounting_height_sha256"
    ] == mounting_sha256
    ppfd_publication = published_manifest["viewer_publication"]["ppfd_heatmap"]
    root_metadata = (tmp_path / "visualization.json").read_bytes()
    root_scalar = (tmp_path / "ppfd-scatter.f32le.bin").read_bytes()
    viewer_metadata = (
        tmp_path / "plant-layout-viewer" / ppfd_publication["metadata"]
    ).read_bytes()
    viewer_scalar = (
        tmp_path / "plant-layout-viewer" / ppfd_publication["scalar_field"]
    ).read_bytes()
    assert viewer_metadata == root_metadata
    assert viewer_scalar == root_scalar
    assert ppfd_publication["metadata_sha256"] == hashlib.sha256(
        root_metadata
    ).hexdigest()
    assert ppfd_publication["scalar_field_sha256"] == hashlib.sha256(
        root_scalar
    ).hexdigest()
    validator_source = (
        REPOSITORY / "src/fspm_optics/application/proposed.py"
    ).read_text(encoding="utf-8")
    assert "shared_schema_version not in {2, 3, 4}" in validator_source
    assert "if shared_schema_version == 4:" in validator_source
    (tmp_path / "events.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "run.log").write_text("published\n", encoding="utf-8")

    validate_success_artifacts(
        tmp_path,
        expected_run_id=RUN_ID,
        expected_system_id=PROPOSED_SYSTEM_ID,
        expected_request=request.to_dict(),
    )

    export_module_path = (
        tmp_path / "ppfd-scatter-viewer" / "presentation-export.js"
    )
    export_module_bytes = export_module_path.read_bytes()
    export_module_path.unlink()
    with pytest.raises(ProposedRunError, match="required run artifact"):
        validate_success_artifacts(
            tmp_path,
            expected_run_id=RUN_ID,
            expected_system_id=PROPOSED_SYSTEM_ID,
            expected_request=request.to_dict(),
        )
    export_module_path.write_bytes(export_module_bytes)

    baseline_leaf_path = tmp_path / "baseline-leaf-position-uniformity.v1.json"
    baseline_leaf_text = baseline_leaf_path.read_text(encoding="utf-8")
    baseline_leaf_payload = json.loads(baseline_leaf_text)
    assert baseline_leaf_payload["schema_id"] == (
        "fspm-optics.baseline-leaf-position-uniformity"
    )
    assert baseline_leaf_payload["schema_version"] == 1
    baseline_leaf_payload["records"][0]["classification"] = "tampered"
    baseline_leaf_path.write_text(
        json.dumps(baseline_leaf_payload),
        encoding="utf-8",
    )
    with pytest.raises(ProposedRunError, match="baseline leaf-position"):
        validate_success_artifacts(
            tmp_path,
            expected_run_id=RUN_ID,
            expected_system_id=PROPOSED_SYSTEM_ID,
            expected_request=request.to_dict(),
        )
    baseline_leaf_path.write_text(baseline_leaf_text, encoding="utf-8")

    source_state_path = tmp_path / "physical-source-state.json"
    source_state_text = source_state_path.read_text(encoding="utf-8")
    source_state_payload = json.loads(source_state_text)
    source_state_payload["source_operation"]["policy_id"] = "tampered"
    source_state_path.write_text(json.dumps(source_state_payload), encoding="utf-8")
    with pytest.raises(ProposedRunError, match="physical source-state"):
        validate_success_artifacts(
            tmp_path,
            expected_run_id=RUN_ID,
            expected_system_id=PROPOSED_SYSTEM_ID,
            expected_request=request.to_dict(),
        )
    source_state_path.write_text(source_state_text, encoding="utf-8")

    manifest_path = tmp_path / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["schema_version"] = 1
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProposedRunError, match="schema is incompatible"):
        validate_success_artifacts(
            tmp_path,
            expected_run_id=RUN_ID,
            expected_system_id=PROPOSED_SYSTEM_ID,
            expected_request=request.to_dict(),
        )

    manifest["schema_version"] = 4
    manifest["artifacts"]["escape_probe"] = "/etc/passwd"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProposedRunError, match="missing or unsafe"):
        validate_success_artifacts(
            tmp_path,
            expected_run_id=RUN_ID,
            expected_system_id=PROPOSED_SYSTEM_ID,
            expected_request=request.to_dict(),
        )

    del manifest["artifacts"]["escape_probe"]
    manifest["scientific_identity"]["room_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ProposedRunError, match="manifest hashes"):
        validate_success_artifacts(
            tmp_path,
            expected_run_id=RUN_ID,
            expected_system_id=PROPOSED_SYSTEM_ID,
            expected_request=request.to_dict(),
        )


@pytest.mark.parametrize("system", [PROPOSED_SYSTEM_ID, CONVENTIONAL_SYSTEM_ID, HPS_SYSTEM_ID])
def test_fspm_automatic_and_override_remain_classification_only(system: str) -> None:
    automatic_payload = _common(system)
    if system != HPS_SYSTEM_ID:
        automatic_payload["target_ppfd"] = 900.0
    automatic_request = parse_run_request(automatic_payload)
    override_request = parse_run_request(
        automatic_payload
        | {
            "fspm_target_mode": "override",
            "fspm_target_ppfd": 700.0,
            "fspm_target_tolerance": 15.0,
        }
    )
    automatic = resolve_fspm_target_policy(
        mode=automatic_request.fspm_target_mode,
        achieved_baseline_mean_ppfd=823.5,
        tolerance_umol_m2_s=(
            automatic_request.fspm_target_tolerance_umol_m2_s
        ),
        override_umol_m2_s=(
            automatic_request.fspm_target_override_umol_m2_s
        ),
    )
    explicit = resolve_fspm_target_policy(
        mode=override_request.fspm_target_mode,
        achieved_baseline_mean_ppfd=823.5,
        tolerance_umol_m2_s=override_request.fspm_target_tolerance_umol_m2_s,
        override_umol_m2_s=override_request.fspm_target_override_umol_m2_s,
    )

    assert automatic_request.system == override_request.system == system
    assert automatic.resolved_target_umol_m2_s == 823.5
    assert explicit.resolved_target_umol_m2_s == 700.0
    assert automatic.affects_baseline_transport is False
    assert explicit.affects_baseline_transport is False


def test_ui_switch_clears_hps_target_and_payload_omits_it() -> None:
    html = (REPOSITORY / "src/fspm_optics/resources/web/index.html").read_text()
    javascript = (REPOSITORY / "src/fspm_optics/resources/web/app.js").read_text()

    for system in ("proposed", "conventional", "hps"):
        assert html.count(f'<option value="{system}">') == 1
    assert 'id="target-ppfd"' in html
    assert 'value="500"' in html
    assert "Mounting height <small>(in)</small>" in html
    assert 'id="mounting-height"' in html
    assert 'name="mounting_height_in"' in html
    assert 'value="18"' in html
    assert 'max="119.8"' in html
    assert 'proposed: "18"' in javascript
    assert 'conventional: "18"' in javascript
    assert 'hps: "24"' in javascript
    assert (
        "mountingHeightBySystem[activeSystemId] = "
        "mountingHeightInput.value.trim()"
    ) in javascript
    assert "mountingHeightBySystem[systemSelect.value]" in javascript
    assert "mounting_height_in: mountingHeightIn" in javascript
    assert "mountingHeightIn * 0.0254" not in javascript
    assert "Number.isFinite(mountingHeightIn)" in javascript
    assert 'systemSelect.value !== "hps"' in javascript
    assert 'targetInput.value = ""' in javascript
    assert "targetInput.disabled = true" in javascript
    assert "targetInput.required = false" in javascript
    assert 'payload.target_ppfd = Number(targetInput.value)' in javascript
    assert "Lighting Target PPFD is not accepted" in javascript
    assert javascript.index("synchronizeFspmTargetState();") < javascript.index(
        'modeSelect.addEventListener("change", synchronizeFspmTargetState)'
    )
    assert 'window.addEventListener("pageshow"' in javascript
    assert 'jobIdentity.textContent = "No active job"' in javascript


def test_shared_architecture_has_no_parallel_web_or_visualization_stack() -> None:
    systems_source = (
        REPOSITORY / "src/fspm_optics/application/systems.py"
    ).read_text()
    publication_source = (
        REPOSITORY / "src/fspm_optics/application/publication.py"
    ).read_text()
    visualization_source = (
        REPOSITORY / "src/fspm_optics/application/visualization.py"
    ).read_text()
    jobs_source = (REPOSITORY / "src/fspm_optics/web/jobs.py").read_text()

    assert "run_system_baseline" in jobs_source
    assert "publish_native_baseline_run" in systems_source
    assert "publish_native_baseline_run" in (
        REPOSITORY / "src/fspm_optics/application/proposed.py"
    ).read_text()
    assert publication_source.count("publish_ppfd_visualizations(") == 1
    assert visualization_source.count("def _render_heatmap(") == 1
    assert "scale_ppfd_map" not in systems_source
    for forbidden in (
        ".salvage_source",
        "horticulture-lighting-simulator",
        "docker",
        "precomputed",
    ):
        assert forbidden not in (systems_source + publication_source).lower()

    tree = ast.parse(systems_source)
    imports = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "fspm_optics.transport.scalar_ppfd" not in imports


def test_package_data_includes_both_approved_ies_assets_and_shared_ui() -> None:
    pyproject = (REPOSITORY / "pyproject.toml").read_text()
    assert '"resources/data/conventional_led/*.ies"' in pyproject
    assert '"resources/data/hps/*.ies"' in pyproject
    assert '"resources/web/*.js"' in pyproject
    assert '"resources/web/*.png"' in pyproject
    assert '"resources/scatter/*.js"' in pyproject

    package = files("fspm_optics")
    conventional = package.joinpath(
        "resources", "data", "conventional_led", CONVENTIONAL_IES_RESOURCE_NAME
    ).read_bytes()
    hps = package.joinpath(
        "resources", "data", "hps", HPS_IES_RESOURCE_NAME
    ).read_bytes()
    assert hashlib.sha256(conventional).hexdigest() == CONVENTIONAL_IES_SHA256
    assert hashlib.sha256(hps).hexdigest() == HPS_IES_SHA256
    logo = package.joinpath("resources", "web", "homeleaf.png").read_bytes()
    assert len(logo) == 131_902
    assert hashlib.sha256(logo).hexdigest() == (
        "f34e4905a9df5b122de1350a0310fab193e73ff213afb41252b8789230b46212"
    )
    assert package.joinpath("resources", "web", "app.js").read_bytes()
    assert package.joinpath("resources", "scatter", "main.js").read_bytes()
