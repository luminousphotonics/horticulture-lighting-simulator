from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import fspm_optics.application.multispectral as multispectral_module
import fspm_optics.application.proposed as proposed_module
from fspm_optics.application.domain import (
    ProposedControlMode,
    ProposedRunRequest,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.application.multispectral import (
    build_proposed_juvenile_source_adapter,
)
from fspm_optics.application.source_state import PhysicalSourceState
from fspm_optics.application.target_control import (
    apply_target_control,
    derive_uniform_full_output_schedule,
)
from fspm_optics.fixtures.smd.optical_stack import (
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
)
from fspm_optics.fixtures.occlusion import (
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.smd.power_schedule import build_uniform_module_schedule
from fspm_optics.fixtures.smd.radiance_writer import (
    smd_source_identity_sha256,
)
from fspm_optics.geometry.room import RoomDimensions, room_radiance_text
from fspm_optics.radiance.commands import CommandSpec
from fspm_optics.radiance.runner import RunnerResult
from fspm_optics.radiance.versioning import (
    RadianceExecutableVersion,
    RadianceInstallation,
)
from fspm_optics.transport.proposed_uniform import (
    execute_uniform_proposed_stage_a,
    materialize_uniform_proposed_stage_a,
    plan_uniform_proposed_stage_a,
)


def _payload() -> dict[str, object]:
    return {
        "system": "proposed",
        "target_ppfd": 500.0,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    }


def _installation() -> RadianceInstallation:
    return RadianceInstallation(
        oconv=RadianceExecutableVersion(
            "oconv", Path("/fake/oconv"), "fake oconv"
        ),
        rtrace=RadianceExecutableVersion(
            "rtrace", Path("/fake/rtrace"), "fake rtrace"
        ),
    )


class _UniformRunner:
    def __init__(self, rows: str) -> None:
        self.rows = rows
        self.calls: list[CommandSpec] = []

    def run(
        self,
        command: CommandSpec,
        *,
        timeout_s: float | None = None,
        stderr_path: str | Path | None = None,
    ) -> RunnerResult:
        del timeout_s, stderr_path
        self.calls.append(command)
        assert command.stdout_path is not None
        if command.stdout_path.suffix == ".oct":
            command.stdout_path.write_bytes(b"uniform octree")
        else:
            command.stdout_path.write_text(self.rows, encoding="utf-8")
        return RunnerResult(
            command_label=command.label,
            argv=command.argv,
            returncode=0,
            stdout_path=command.stdout_path,
            stderr_text="",
            stderr_path=None,
            wall_time_s=0.1,
            success=True,
        )


def test_request_defaults_optimized_and_round_trips_uniform_mode() -> None:
    default = parse_run_request(_payload())
    uniform = parse_run_request(
        _payload() | {"proposed_control_mode": "uniform_module_dimming"}
    )

    assert isinstance(default, ProposedRunRequest)
    assert default.control_mode is ProposedControlMode.BASIS_MATRIX_OPTIMIZED
    assert default.to_dict()["proposed_control_mode"] == "basis_matrix_optimized"
    assert isinstance(uniform, ProposedRunRequest)
    assert uniform.control_mode is ProposedControlMode.UNIFORM_MODULE_DIMMING
    serialized = uniform.to_dict()
    reparsed = parse_run_request(
        _payload()
        | {"proposed_control_mode": serialized["proposed_control_mode"]}
    )
    assert reparsed.control_mode is ProposedControlMode.UNIFORM_MODULE_DIMMING
    assert hashlib.sha256(
        json.dumps(default.to_dict(), sort_keys=True).encode()
    ).hexdigest() != hashlib.sha256(
        json.dumps(uniform.to_dict(), sort_keys=True).encode()
    ).hexdigest()


def test_promotion_recomputes_the_published_control_mode_identity() -> None:
    source = inspect.getsource(proposed_module.validate_success_artifacts)
    assert (
        'expected_scientific_identity["proposed_control_identity_sha256"]'
        in source
    )
    assert "_hash_json(proposed_control)" in source


@pytest.mark.parametrize("system", ["conventional", "hps"])
def test_non_proposed_system_rejects_uniform_control_mode(system: str) -> None:
    payload = _payload() | {
        "system": system,
        "proposed_control_mode": "uniform_module_dimming",
    }
    if system == "hps":
        payload.pop("target_ppfd")
    with pytest.raises(RequestValidationError, match="unsupported request fields"):
        parse_run_request(payload)


def test_uniform_target_control_is_equal_per_module_and_obeys_power_equations() -> None:
    layout = generate_proposed_led_layout(10.0, 10.0)
    reference_field = np.asarray([800.0, 1000.0, 1200.0], dtype=float)
    full = derive_uniform_full_output_schedule(reference_field, layout)
    controlled = apply_target_control(full, 500.0)

    assert set(full.schedule.watts_by_module) == {100.0}
    assert set(full.schedule.watts_by_control_zone) == {100.0}
    assert controlled.dimming_factor == pytest.approx(0.5)
    assert controlled.achieved_mean_ppfd == pytest.approx(500.0)
    assert controlled.achieved_field == pytest.approx(reference_field * 0.5)
    effective_per_module = 100.0 * controlled.dimming_factor
    assert controlled.effective_power_w == pytest.approx(
        len(layout.modules) * effective_per_module
    )
    assert (
        controlled.effective_modeled_completed_aperture_par_ppf_umol_s
        == pytest.approx(
            controlled.effective_power_w
            * COMPLETED_APERTURE_PPE_UMOL_PER_J
        )
    )

    capped = apply_target_control(full, 1500.0)
    assert capped.feasible is False
    assert capped.dimming_factor == 1.0
    assert capped.achieved_mean_ppfd == pytest.approx(1000.0)
    assert set(full.schedule.watts_by_module) == {100.0}


def test_uniform_complete_scene_executes_one_trace_and_no_basis_artifacts(
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(10.0, 10.0)
    plan = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=tmp_path / "uniform-stage-a",
        radiance_options=("-ab", "0"),
    )
    materialize_uniform_proposed_stage_a(plan)
    rows = "1 1 1\n" * plan.sensor_grid_spec.point_count
    runner = _UniformRunner(rows)
    result = execute_uniform_proposed_stage_a(
        plan,
        runner,  # type: ignore[arg-type]
        radiance_installation=_installation(),
    )

    assert [call.label for call in runner.calls] == [
        *(
            shape.compile_command.label
            for shape in plan.fixture_occlusion.shapes
        ),
        "compile_proposed_uniform_complete_scene",
        "trace_proposed_uniform_complete_scene",
    ]
    assert result.execution_metadata["complete_scene_trace_count"] == 1
    assert result.execution_metadata["basis_column_count"] == 0
    assert set(plan.schedule.watts_by_module) == {100.0}
    assert len(
        set(plan.emitter_document.metadata.source_radiance_by_control_zone)
    ) == 1
    assert not (tmp_path / "uniform-stage-a" / "basis_matrix.npy").exists()
    assert not (tmp_path / "basis").exists()


def test_uniform_and_source_state_identities_are_mode_distinct_but_sources_match(
    tmp_path: Path,
) -> None:
    linear = generate_proposed_led_layout(
        10.0,
        10.0,
        proposed_layout_mode="linear",
    )
    legacy = generate_proposed_led_layout(
        10.0,
        10.0,
        proposed_layout_mode="legacy",
    )
    linear_plan = plan_uniform_proposed_stage_a(
        layout=linear,
        output_directory=tmp_path / "linear",
        radiance_options=("-ab", "0"),
    )
    legacy_plan = plan_uniform_proposed_stage_a(
        layout=legacy,
        output_directory=tmp_path / "legacy",
        radiance_options=("-ab", "0"),
    )
    assert linear_plan.emitter_document.radiance_text == (
        legacy_plan.emitter_document.radiance_text
    )
    assert linear_plan.room_text == legacy_plan.room_text
    assert linear_plan.sensor_text == legacy_plan.sensor_text
    assert linear_plan.identity_sha256 != legacy_plan.identity_sha256
    assert linear_plan.paths.ambient_cache is not None
    assert legacy_plan.paths.ambient_cache is not None
    assert linear_plan.paths.ambient_cache.name != legacy_plan.paths.ambient_cache.name
    assert (
        smd_source_identity_sha256(linear_plan.emitter_document.metadata)[:16]
        in linear_plan.paths.ambient_cache.name
    )

    schedule = {"watts_by_module": [1.0] * len(linear.modules)}
    operating = {"dimming_factor": 0.5}
    linear_state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity=proposed_module._layout_identity(linear),
        full_output_schedule=schedule,
        operating_point=operating,
        source_operation={
            "policy_id": "test",
            "proposed_layout_mode": "linear",
            "fixture_policy_id": linear.fixture_policy_id,
            "mechanical_envelope": {
                "id": linear.mechanical_envelope_id,
                "width_x_m": linear.module_footprint_x_m,
                "height_y_m": linear.module_footprint_y_m,
            },
            "fixture_asset_set_id": linear.fixture_asset_set_id,
        },
    )
    legacy_state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity=proposed_module._layout_identity(legacy),
        full_output_schedule=schedule,
        operating_point=operating,
        source_operation={
            "policy_id": "test",
            "proposed_layout_mode": "legacy",
            "fixture_policy_id": legacy.fixture_policy_id,
            "mechanical_envelope": {
                "id": legacy.mechanical_envelope_id,
                "width_x_m": legacy.module_footprint_x_m,
                "height_y_m": legacy.module_footprint_y_m,
            },
            "fixture_asset_set_id": legacy.fixture_asset_set_id,
        },
    )
    assert linear_state.source_state_id != legacy_state.source_state_id
    assert linear_state.payload()["layout_identity_sha256"] != (
        legacy_state.payload()["layout_identity_sha256"]
    )


def test_uniform_stage_b_uses_equal_authenticated_schedule_without_optimizer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    layout = generate_proposed_led_layout(10.0, 10.0)
    full_schedule = build_uniform_module_schedule(layout, 100.0)
    state = PhysicalSourceState.create(
        system_id="proposed",
        layout_identity={"layout": "uniform"},
        full_output_schedule={"watts_by_module": list(full_schedule.watts_by_module)},
        operating_point={"dimming_factor": 0.4},
        source_operation={
            "proposed_control_mode": "uniform_module_dimming",
            "proposed_layout_mode": layout.proposed_layout_mode.value,
            "fixture_policy_id": layout.fixture_policy_id,
            "mechanical_envelope": {
                "id": layout.mechanical_envelope_id,
                "width_x_m": layout.module_footprint_x_m,
                "height_y_m": layout.module_footprint_y_m,
            },
            "fixture_asset_set_id": layout.fixture_asset_set_id,
            "effective_watts_by_module": [
                40.0 for _ in full_schedule.watts_by_module
            ],
        },
    )

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("optimized schedule builder must not run")

    monkeypatch.setattr(
        multispectral_module, "build_optimized_module_schedule", forbidden
    )
    fixture_occlusion = plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=tmp_path / "fixture_occlusion",
    )
    materialize_fixture_occlusion(fixture_occlusion)
    adapter = build_proposed_juvenile_source_adapter(
        layout=layout,
        full_output_schedule=full_schedule,
        dimming_factor=0.4,
        room_text=room_radiance_text(RoomDimensions(3.048, 3.048, 3.048)),
        source_state=state,
        quality_profile="direct",
        threads=1,
        oconv_bin="/fake/oconv",
        rtrace_bin="/fake/rtrace",
        control_mode=ProposedControlMode.UNIFORM_MODULE_DIMMING,
        fixture_occlusion=fixture_occlusion,
    )

    assert adapter.source_policy["proposed_control_mode"] == (
        "uniform_module_dimming"
    )
    assert adapter.source_policy["basis_matrix_solver_enabled"] is False
    assert adapter.source_policy["all_effective_module_watts_equal"] is True
    for band in adapter.bands:
        assert band.source_provenance["proposed_control_mode"] == (
            "uniform_module_dimming"
        )
    rendered = adapter.bands[0].render_source_text()
    assert "# schedule_source=uniform_module_dimming" in rendered
    assert "solver_watts=40.000000000" in rendered


def test_uniform_orchestration_never_calls_basis_planner_executor_or_optimizer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ProposedRunRequest.from_payload(
        _payload() | {"proposed_control_mode": "uniform_module_dimming"}
    )

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("basis or optimized solver path must not run")

    monkeypatch.setattr(proposed_module, "plan_basis_workspace", forbidden)
    monkeypatch.setattr(proposed_module, "execute_basis_matrix", forbidden)
    monkeypatch.setattr(proposed_module, "derive_full_output_schedule", forbidden)

    def fake_uniform_execution(plan: object, _runner: object) -> object:
        sensor_count = plan.sensor_grid_spec.point_count  # type: ignore[attr-defined]
        return SimpleNamespace(
            reference_field=np.full(sensor_count, 1000.0),
            radiance_installation=_installation(),
            execution_metadata={"field_sha256": "a" * 64},
        )

    captured: dict[str, object] = {}

    def fake_publish(
        _root: Path,
        *,
        science: object,
        **_kwargs: object,
    ) -> object:
        captured["science"] = science
        return SimpleNamespace(metrics={"ok": True}, manifest={"ok": True})

    monkeypatch.setattr(
        proposed_module,
        "execute_uniform_proposed_stage_a",
        fake_uniform_execution,
    )
    monkeypatch.setattr(
        proposed_module, "publish_native_baseline_run", fake_publish
    )
    outcome = proposed_module.run_proposed_baseline(
        run_id="a" * 32,
        request=request,
        workspace=tmp_path,
        event_sink=lambda *_args: None,
    )

    science = captured["science"]
    assert outcome.metrics == {"ok": True}
    assert science.transport_policy["backend"] == (
        "single_complete_uniform_rtrace"
    )
    assert science.transport_policy["basis_matrix_solver_enabled"] is False
    assert science.engine_provenance["basis_column_count"] == 0
    assert science.operating_point["proposed_control"]["mode"] == (
        "uniform_module_dimming"
    )
    assert science.operating_point["proposed_control"][
        "all_module_effective_watts_equal"
    ] is True
