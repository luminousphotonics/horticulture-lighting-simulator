from __future__ import annotations

import asyncio
import json
from pathlib import Path
import time

import httpx
import pytest
from starlette.testclient import TestClient

from fspm_optics.application.domain import (
    EMITTED_PPF_BOUNDARIES,
    PROPOSED_SYSTEM_ID,
    ProposedRunRequest,
    parse_run_request,
)
from fspm_optics.application.proposed import ProposedRunError, ProposedRunOutcome, _layout_identity
from fspm_optics.application.publication import (
    RunSciencePublication,
    publish_native_baseline_run,
)
from fspm_optics.application.source_state import PhysicalSourceState, hash_json
from fspm_optics.application.visualization import VisualizationReference
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.fixtures.proposed_cob.source import (
    resolve_proposed_source_authority,
)
from fspm_optics.radiance.options import radiance_options
from fspm_optics.transport.scalar_ppfd import PpfdMapSample
from fspm_optics.web.app import create_app
from fspm_optics.web.jobs import JobManager
from fspm_optics.web.workspaces import RuntimeWorkspaces, WorkspaceSafetyError


def _fake_success(**kwargs: object) -> ProposedRunOutcome:
    run_id = str(kwargs["run_id"])
    workspace = Path(kwargs["workspace"])
    request = kwargs["request"]
    assert isinstance(request, ProposedRunRequest)
    samples = tuple(
        PpfdMapSample(x, y, 0.005, 900.0)
        for y in (-0.75, 0.75)
        for x in (-0.75, 0.75)
    )
    layout = generate_proposed_led_layout(
        request.room_length_ft,
        request.room_width_ft,
        mount_z_m=request.mounting_geometry.emitting_aperture_plane_z_m,
        proposed_layout_mode=request.proposed_layout_mode,
        proposed_ring_mode=request.proposed_ring_mode,
    )
    layout_identity = _layout_identity(layout)
    fixture_count = len(layout.fixtures)
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
    target_capped = request.lighting_target_mode.value == "target_capped"
    target_control = {
        "schema_id": "fspm-optics.proposed-global-target-control",
        "schema_version": 3,
        "lighting_target_mode": request.lighting_target_mode.value,
        "requested_target_ppfd_umol_m2_s": request.target_ppfd_umol_m2_s,
        "full_output_mean_ppfd_umol_m2_s": 1000.0,
        "full_output_maximum_ppfd_umol_m2_s": 1000.0,
        "achieved_mean_ppfd_umol_m2_s": 900.0,
        "achieved_maximum_ppfd_umol_m2_s": 900.0,
        "dimming_factor": 0.9,
        "feasible": True,
        "infeasibility": None,
    }
    if target_capped:
        target_control.update(
            {
                "cap_binding": True,
                "cap_compliant": True,
                "compliance_tolerance_umol_m2_s": 1e-6,
                "limiting_sample": {
                    "index": 0,
                    "x_m": -0.75,
                    "y_m": -0.75,
                    "z_m": 0.005,
                    "achieved_ppfd_umol_m2_s": 900.0,
                },
            }
        )
    operating_point = {
        "lighting_target_mode": request.lighting_target_mode.value,
        "power": power,
        "ppf": ppf,
        "proposed_source": proposed_source,
    }
    science = RunSciencePublication(
        system_id=request.system,
        samples=samples,
        layout_identity=layout_identity,
        overlay_plan=layout.authoritative_overlay_plan(),
        visualization_reference=(
            VisualizationReference.requested_sampled_cap(
                request.target_ppfd_umol_m2_s
            )
            if target_capped
            else VisualizationReference.requested_target(
                request.target_ppfd_umol_m2_s
            )
        ),
        quality_options=tuple(radiance_options(request.quality)),
        target_control=target_control,
        full_output_schedule={"policy": "test_full_output"},
        operating_point=operating_point,
        counts={
            "fixture_groups": fixture_count,
            "fixtures": fixture_count,
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
            system_id=request.system,
            layout_identity=layout_identity,
            full_output_schedule={"policy": "test_full_output"},
            operating_point=operating_point,
            source_operation={
                "policy_id": "test_stage_a_source",
                "lighting_target_mode": request.lighting_target_mode.value,
                "proposed_source": proposed_source,
                "proposed_layout_mode": layout.proposed_layout_mode.value,
                "proposed_ring_mode": layout.proposed_ring_mode.value,
                "module_pattern_id": layout.module_pattern_id,
                "fixture_policy_id": layout.fixture_policy_id,
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
    published = publish_native_baseline_run(
        workspace,
        run_id=run_id,
        request=request,
        science=science,
        event_sink=kwargs["event_sink"],  # type: ignore[arg-type]
    )
    return ProposedRunOutcome(
        run_id,
        published.metrics,
        published.manifest,
        True,
        None,
    )


def _fake_failure(**_kwargs: object) -> ProposedRunOutcome:
    raise ProposedRunError(
        "native_basis_execution_failed", "basis_execution", "native failure"
    )


def _fake_stage_b_failure(**_kwargs: object) -> ProposedRunOutcome:
    raise ProposedRunError(
        "juvenile_multispectral_transport_failed",
        "multispectral_fspm",
        "band transport failed",
    )


def _fake_mismatched_result(**kwargs: object) -> ProposedRunOutcome:
    _fake_success(**kwargs)
    return ProposedRunOutcome("f" * 32, {}, {}, True, None)


def _fake_mismatched_system(**kwargs: object) -> ProposedRunOutcome:
    _fake_success(**kwargs)
    return ProposedRunOutcome(
        str(kwargs["run_id"]),
        {},
        {},
        None,
        None,
        system_id="hps",
    )


def _request() -> dict[str, object]:
    return {
        "system": "proposed",
        "target_ppfd": 900.0,
        "room_length_ft": 10.5,
        "room_width_ft": 9.75,
        "mounting_height_in": 18.0,
        "quality": "standard",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
    }


def _terminal(client: TestClient, job_url: str) -> dict[str, object]:
    payload: dict[str, object] = {}
    for _attempt in range(100):
        response = client.get(job_url, params={"cursor": 0})
        payload = response.json()
        if payload["terminal"]:
            return payload
        time.sleep(0.01)
    raise AssertionError("fake background job did not terminate")


def test_app_construction_does_not_create_runtime_directories(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_success)
    assert not runtime.exists()
    app.state.jobs.shutdown()


def test_app_binds_one_resolved_proposed_layout_mode_and_rejects_invalid_startup(
    tmp_path: Path,
) -> None:
    app = create_app(
        runtime_root=tmp_path / "legacy-runtime",
        run_executor=_fake_success,
        proposed_layout_mode="legacy",
    )
    assert app.state.jobs.proposed_layout_mode.value == "legacy"
    app.state.jobs.shutdown()
    with pytest.raises(ValueError, match="FSPM_PROPOSED_LAYOUT_MODE"):
        create_app(
            runtime_root=tmp_path / "invalid-runtime",
            proposed_layout_mode="unsupported",
        )


def test_analysis_scope_ui_helper_is_served_as_a_module(tmp_path: Path) -> None:
    app = create_app(runtime_root=tmp_path / "runtime", run_executor=_fake_success)
    with TestClient(app) as client:
        response = client.get("/static/analysis-scope-ui.js")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/javascript")
        assert "MULTISPECTRAL_FSPM_SCOPE" in response.text


def test_async_asgi_reduced_ring_request_and_artifacts(tmp_path: Path) -> None:
    async def scenario() -> None:
        app = create_app(
            runtime_root=tmp_path / "async-runtime",
            run_executor=_fake_success,
        )
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                malformed = await client.post(
                    "/api/runs",
                    json=_request()
                    | {
                        "quality": "direct",
                        "proposed_ring_mode": " reduced_one_ring",
                    },
                )
                assert malformed.status_code == 422
                assert malformed.json()["error"]["field"] == "proposed_ring_mode"

                hidden = await client.post(
                    "/api/runs",
                    json=_request()
                    | {
                        "quality": "direct",
                        "proposed_layout_mode": "standalone_modules",
                    },
                )
                assert hidden.status_code == 422

                accepted = await client.post(
                    "/api/runs",
                    json=_request()
                    | {
                        "quality": "direct",
                        "room_length_ft": 10.0,
                        "room_width_ft": 10.0,
                        "proposed_ring_mode": "reduced_one_ring",
                    },
                )
                assert accepted.status_code == 202
                identifiers = accepted.json()
                terminal: dict[str, object] | None = None
                for _attempt in range(200):
                    response = await client.get(
                        identifiers["job_url"],
                        params={"cursor": 0},
                    )
                    candidate = response.json()
                    if candidate["terminal"]:
                        terminal = candidate
                        break
                    await asyncio.sleep(0.01)
                assert terminal is not None
                assert terminal["state"] == "succeeded", terminal
                result = terminal["result"]
                assert result["proposed_layout"]["ring_mode"] == (
                    "reduced_one_ring"
                )

                run_id = identifiers["run_id"]
                manifest = (
                    await client.get(f"/api/runs/{run_id}/manifest")
                ).json()
                assert manifest["request"]["proposed_ring_mode"] == (
                    "reduced_one_ring"
                )
                assert manifest["layout"]["module_pattern_id"] == (
                    "centered_square_reduced_one_ring_v1"
                )
                assert len(manifest["layout"]["modules"]) == 41
                assert len(manifest["layout"]["fixtures"]) == 41
                assert manifest["layout"]["topology"]["control_zone_count"] == 4
                assert manifest["proposed_layout"]["ring_mode"] == (
                    "reduced_one_ring"
                )

                # Starlette FileResponse delegates os.stat to a worker thread,
                # which is the known hanging path in this environment even
                # under ASGITransport. The POST/job/manifest route coverage
                # above stays fully async; inspect the atomically promoted
                # viewer artifact directly for equivalent scene coverage.
                scene = json.loads(
                    (
                        tmp_path
                        / "async-runtime"
                        / "completed"
                        / run_id
                        / "plant-layout-viewer"
                        / "scene.v1.json"
                    ).read_text(encoding="utf-8")
                )
                assert scene["run"]["information"]["proposed_layout"] == {
                    "ring_mode": "reduced_one_ring",
                    "module_pattern_id": (
                        "centered_square_reduced_one_ring_v1"
                    ),
                }

    asyncio.run(scenario())


def test_post_cursor_promotion_and_run_scoped_artifacts(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_success)
    with TestClient(app) as client:
        accepted = client.post("/api/runs", json=_request())
        assert accepted.status_code == 202
        identifiers = accepted.json()
        assert len(identifiers["job_id"]) == 32
        assert len(identifiers["run_id"]) == 32
        assert identifiers["analysis_scope"] == "baseline_ppfd"

        terminal = _terminal(client, identifiers["job_url"])
        assert terminal["state"] == "succeeded"
        assert terminal["analysis_scope"] == "baseline_ppfd"
        assert terminal["lighting_target_mode"] == "mean_target"
        assert terminal["result"]["analysis_scope"] == "baseline_ppfd"
        assert terminal["result"]["lighting_target_mode"] == "mean_target"
        assert terminal["result"]["proposed_control"]["mode"] == (
            "basis_matrix_optimized"
        )
        assert "ppfd_scatter_data_url" not in terminal["result"]
        assert terminal["result"]["ppfd_scatter_viewer_url"].endswith(
            "/scatter/index.html"
        )
        assert terminal["result"][
            "baseline_leaf_position_uniformity_url"
        ].endswith("/artifacts/baseline-leaf-position-uniformity.v1.json")
        first_cursor = terminal["next_cursor"]
        fresh = client.get(
            identifiers["job_url"], params={"cursor": first_cursor}
        ).json()
        assert fresh["events"] == []
        assert fresh["logs"] == []

        run_id = identifiers["run_id"]
        metrics_response = client.get(f"/api/runs/{run_id}/metrics")
        assert metrics_response.status_code == 200
        assert metrics_response.json()["lighting_target_mode"] == (
            terminal["result"]["lighting_target_mode"]
        )
        assert metrics_response.json()["proposed_control"]["mode"] == (
            "basis_matrix_optimized"
        )
        assert metrics_response.json()["proposed_layout"]["mode"] == (
            "standalone_modules"
        )
        manifest_response = client.get(f"/api/runs/{run_id}/manifest")
        assert manifest_response.status_code == 200
        manifest = manifest_response.json()
        assert manifest["request"]["proposed_layout_mode"] == "standalone_modules"
        assert manifest["request"]["proposed_ring_mode"] == "full"
        assert manifest["layout"]["proposed_layout_mode"] == "standalone_modules"
        assert manifest["layout"]["proposed_ring_mode"] == "full"
        assert manifest["proposed_layout"] == {
            "mode": "standalone_modules",
            "fixture_policy_id": manifest["layout"]["fixture_policy_id"],
            "ring_mode": "full",
            "module_pattern_id": "centered_square_full_v1",
        }
        assert manifest["authoritative_overlay_plan"]["metadata"][
            "proposed_layout_mode"
        ] == "standalone_modules"
        source_state_response = client.get(
            f"/api/runs/{run_id}/artifacts/physical-source-state.json"
        )
        assert source_state_response.status_code == 200
        source_state = source_state_response.json()
        assert source_state["layout_identity_sha256"] == hash_json(
            manifest["layout"]
        )
        assert source_state["source_operation"]["proposed_layout_mode"] == (
            "standalone_modules"
        )
        assert source_state["source_operation"]["proposed_ring_mode"] == "full"
        assert (
            client.get(f"/api/runs/{run_id}/artifacts/ppfd.csv").status_code
            == 200
        )
        baseline_leaf_response = client.get(
            f"/api/runs/{run_id}/artifacts/"
            "baseline-leaf-position-uniformity.v1.json"
        )
        assert baseline_leaf_response.status_code == 200
        assert baseline_leaf_response.json()["schema_id"] == (
            "fspm-optics.baseline-leaf-position-uniformity"
        )
        assert client.get(f"/runs/{run_id}/viewer/index.html").status_code == 200
        scene_response = client.get(f"/runs/{run_id}/viewer/scene.v1.json")
        assert scene_response.status_code == 200
        assert scene_response.json()["run"]["information"][
            "proposed_control"
        ]["mode"] == "basis_matrix_optimized"
        assert scene_response.json()["run"]["information"][
            "proposed_layout"
        ] == {
            "ring_mode": "full",
            "module_pattern_id": "centered_square_full_v1",
        }
        assert client.get(
            f"/runs/{run_id}/viewer/vendor/addons/environments/RoomEnvironment.js"
        ).status_code == 200
        assert client.get(
            f"/runs/{run_id}/viewer/vendor/addons/loaders/GLTFLoader.js"
        ).status_code == 200
        assert client.get(
            f"/runs/{run_id}/viewer/vendor/addons/utils/BufferGeometryUtils.js"
        ).status_code == 200
        assert client.get(
            f"/runs/{run_id}/viewer/vendor/addons/utils/SkeletonUtils.js"
        ).status_code == 200
        assert (
            client.get(f"/runs/{run_id}/viewer/instances.f32le.bin").status_code
            == 200
        )
        fixture_catalog_response = client.get(
            f"/runs/{run_id}/viewer/fixtures/catalog.v1.json"
        )
        assert fixture_catalog_response.status_code == 200
        fixture_catalog = fixture_catalog_response.json()
        assert fixture_catalog["authoritative_layout_sha256"] == hash_json(
            manifest["layout"]
        )
        assert {
            group["display_fixture_type"]
            for group in fixture_catalog["asset_groups"]
        } <= {"centerpiece", "linear2", "linear3", "standalone_module"}
        fixture_group = fixture_catalog["asset_groups"][0]
        assert client.get(
            f"/runs/{run_id}/viewer/fixtures/{fixture_group['asset']['filename']}"
        ).status_code == 200
        assert client.get(
            "/runs/"
            f"{run_id}/viewer/fixtures/{fixture_group['instance_matrices']['filename']}"
        ).status_code == 200
        assert client.get(
            f"/api/runs/{run_id}/artifacts/ppfd-heatmap.png"
        ).status_code == 200
        assert client.get(
            f"/api/runs/{run_id}/artifacts/ppfd-heatmap-overlay.png"
        ).status_code == 200
        assert client.get(
            f"/api/runs/{run_id}/artifacts/visualization.json"
        ).status_code == 200
        assert client.get(
            f"/api/runs/{run_id}/artifacts/ppfd-scatter.f32le.bin"
        ).status_code == 200
        assert client.get(f"/runs/{run_id}/scatter/index.html").status_code == 200
        presentation_export = client.get(
            f"/runs/{run_id}/scatter/presentation-export.js"
        )
        assert presentation_export.status_code == 200
        assert presentation_export.headers["content-type"].startswith(
            "text/javascript"
        )
        assert client.get(
            f"/runs/{run_id}/scatter/vendor/three.module.js"
        ).status_code == 200
        assert client.get(
            f"/runs/{run_id}/scatter/vendor/three.core.js"
        ).status_code == 200
        assert client.get(
            f"/runs/{run_id}/scatter/vendor/addons/controls/OrbitControls.js"
        ).status_code == 200
        assert client.get(
            f"/runs/{run_id}/scatter/not-allowed.html"
        ).status_code == 400
        assert (runtime / "completed" / run_id).is_dir()
        assert not (runtime / "staging" / run_id).exists()
    assert not (runtime / "completed" / run_id).exists()
    assert tuple((runtime / "staging").iterdir()) == ()
    assert tuple((runtime / "failed").iterdir()) == ()


@pytest.mark.parametrize(
    "lighting_target_mode",
    ["mean_target", "target_capped"],
)
def test_promoted_result_carries_authorized_lighting_target_mode(
    tmp_path: Path,
    lighting_target_mode: str,
) -> None:
    workspaces = RuntimeWorkspaces(
        tmp_path / "runtime",
        Path(__file__).parents[1],
    )
    workspaces.initialize()
    jobs = JobManager(workspaces, run_executor=_fake_success)
    jobs.start()
    try:
        job_id, _run_id = jobs.submit(
            parse_run_request(
                _request() | {"lighting_target_mode": lighting_target_mode}
            )
        )
        deadline = time.monotonic() + 10.0
        while True:
            terminal = jobs.snapshot(job_id, 0)
            if terminal["terminal"]:
                break
            if time.monotonic() >= deadline:
                raise AssertionError("mocked promotion did not finish")
            time.sleep(0.01)
    finally:
        jobs.shutdown()

    assert terminal["state"] == "succeeded"
    assert terminal["lighting_target_mode"] == lighting_target_mode
    result = terminal["result"]
    assert isinstance(result, dict)
    assert result["lighting_target_mode"] == lighting_target_mode
    published = workspaces.completed_run(str(terminal["run_id"]))
    assert (
        json.loads(
            (published / "metrics.json").read_text(encoding="utf-8")
        )["lighting_target_mode"]
        == result["lighting_target_mode"]
    )


def test_exception_shutdown_removes_temporary_artifacts(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_success)

    with pytest.raises(RuntimeError, match="escape lifespan"):
        with TestClient(app) as client:
            accepted = client.post("/api/runs", json=_request()).json()
            terminal = _terminal(client, accepted["job_url"])
            assert terminal["state"] == "succeeded"
            run_id = accepted["run_id"]
            assert (runtime / "completed" / run_id).is_dir()
            raise RuntimeError("escape lifespan")

    assert not (runtime / "completed" / run_id).exists()


def test_cleanup_failure_warns_without_masking_the_server_exception(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_success)

    def fail_cleanup() -> None:
        raise OSError("injected cleanup failure")

    with caplog.at_level("WARNING"):
        with pytest.raises(RuntimeError, match="original server failure"):
            with TestClient(app):
                monkeypatch.setattr(
                    app.state.workspaces,
                    "_purge_artifact_directories",
                    fail_cleanup,
                )
                raise RuntimeError("original server failure")

    assert "failed to clean managed runtime artifacts" in caplog.text
    assert "injected cleanup failure" in caplog.text


def test_keep_runtime_preserves_artifacts_after_app_shutdown(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(
        runtime_root=runtime,
        run_executor=_fake_success,
        keep_runtime=True,
    )
    with TestClient(app) as client:
        accepted = client.post("/api/runs", json=_request()).json()
        terminal = _terminal(client, accepted["job_url"])
        assert terminal["state"] == "succeeded"
        run_id = accepted["run_id"]

    assert (runtime / "completed" / run_id / "manifest.json").is_file()


def test_failed_job_retains_diagnostics_without_public_success(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_failure)
    with TestClient(app) as client:
        accepted = client.post("/api/runs", json=_request()).json()
        terminal = _terminal(client, accepted["job_url"])
        assert terminal["state"] == "failed"
        assert terminal["error"]["code"] == "native_basis_execution_failed"
        run_id = accepted["run_id"]
        assert (runtime / "failed" / run_id / "failure.json").is_file()
        assert not (runtime / "completed" / run_id).exists()
        assert client.get(f"/api/runs/{run_id}/metrics").status_code == 404


def test_stage_b_failure_retains_diagnostics_and_prevents_promotion(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_stage_b_failure)
    request = _request() | {
        "analysis_scope": "baseline_plus_multispectral_fspm"
    }
    with TestClient(app) as client:
        accepted = client.post("/api/runs", json=request).json()
        terminal = _terminal(client, accepted["job_url"])

        assert terminal["state"] == "failed"
        assert terminal["analysis_scope"] == (
            "baseline_plus_multispectral_fspm"
        )
        assert terminal["error"]["stage"] == "multispectral_fspm"
        run_id = accepted["run_id"]
        assert not (runtime / "completed" / run_id).exists()
        assert (runtime / "failed" / run_id / "failure.json").is_file()
        assert client.get(f"/api/runs/{run_id}/manifest").status_code == 404


def test_mismatched_executor_result_cannot_authorize_promotion(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_mismatched_result)
    with TestClient(app) as client:
        accepted = client.post("/api/runs", json=_request()).json()
        terminal = _terminal(client, accepted["job_url"])

        assert terminal["state"] == "failed"
        assert terminal["error"]["code"] == "result_identity_mismatch"
        run_id = accepted["run_id"]
        assert not (runtime / "completed" / run_id).exists()
        assert (runtime / "failed" / run_id / "failure.json").is_file()
        assert client.get(f"/api/runs/{run_id}/metrics").status_code == 404


def test_mismatched_executor_system_cannot_authorize_promotion(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_mismatched_system)
    with TestClient(app) as client:
        accepted = client.post("/api/runs", json=_request()).json()
        terminal = _terminal(client, accepted["job_url"])

        assert terminal["state"] == "failed"
        assert terminal["error"]["code"] == "result_identity_mismatch"
        run_id = accepted["run_id"]
        assert not (runtime / "completed" / run_id).exists()
        assert (runtime / "failed" / run_id / "failure.json").is_file()
        assert client.get(f"/api/runs/{run_id}/metrics").status_code == 404


def test_ids_artifact_names_and_paths_fail_closed(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    app = create_app(runtime_root=runtime, run_executor=_fake_success)
    with TestClient(app) as client:
        invalid_request = _request() | {"system": "unsupported"}
        assert client.post("/api/runs", json=invalid_request).status_code == 422
        expansive_request = _request() | {"artifact_path": "/etc/passwd"}
        assert client.post("/api/runs", json=expansive_request).status_code == 422
        assert client.get("/api/jobs/not-an-id").status_code == 400
        assert client.get("/api/runs/not-an-id/metrics").status_code == 400
        unknown = "a" * 32
        assert (
            client.get(f"/api/runs/{unknown}/artifacts/not-allowed").status_code
            == 404
        )
        assert (
            client.get(f"/runs/{unknown}/viewer/../manifest.json").status_code
            != 200
        )
        assert client.get(
            f"/runs/{unknown}/scatter/not-allowed.html"
        ).status_code != 200


def test_runtime_root_must_be_absolute_and_reject_arbitrary_repository_paths(
    tmp_path: Path,
) -> None:
    repository = Path(__file__).parents[1]
    try:
        RuntimeWorkspaces("relative", repository)
    except WorkspaceSafetyError:
        pass
    else:
        raise AssertionError("relative runtime root was accepted")
    try:
        RuntimeWorkspaces(repository / "runtime", repository)
    except WorkspaceSafetyError:
        pass
    else:
        raise AssertionError("repository-local runtime root was accepted")


def test_viewer_symlink_escape_is_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    workspaces = RuntimeWorkspaces(runtime, Path(__file__).parents[1])
    workspaces.initialize()
    run_id = "a" * 32
    staging = workspaces.allocate_staging(run_id)
    viewer = staging / "plant-layout-viewer"
    viewer.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("outside", encoding="utf-8")
    (viewer / "index.html").symlink_to(outside)
    fixture_root = viewer / "fixtures"
    fixture_root.mkdir()
    (fixture_root / "catalog.v1.json").symlink_to(outside)
    workspaces.promote(run_id)

    try:
        workspaces.safe_viewer_file(run_id, "index.html")
    except WorkspaceSafetyError:
        pass
    else:
        raise AssertionError("viewer symlink escape was accepted")

    with pytest.raises(WorkspaceSafetyError, match="symlinks"):
        workspaces.safe_viewer_file(run_id, "fixtures/catalog.v1.json")


def test_scatter_symlink_escape_and_unlisted_paths_are_rejected(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    workspaces = RuntimeWorkspaces(runtime, Path(__file__).parents[1])
    workspaces.initialize()
    run_id = "b" * 32
    staging = workspaces.allocate_staging(run_id)
    scatter = staging / "ppfd-scatter-viewer"
    scatter.mkdir()
    outside = tmp_path / "outside.html"
    outside.write_text("outside", encoding="utf-8")
    (scatter / "index.html").symlink_to(outside)
    (scatter / "presentation-export.js").write_text(
        "export const ready = true;\n",
        encoding="utf-8",
    )
    workspaces.promote(run_id)

    assert workspaces.safe_scatter_file(
        run_id, "presentation-export.js"
    ).read_text(encoding="utf-8") == "export const ready = true;\n"
    with pytest.raises(WorkspaceSafetyError, match="symlinks"):
        workspaces.safe_scatter_file(run_id, "index.html")
    with pytest.raises(WorkspaceSafetyError, match="not allowed"):
        workspaces.safe_scatter_file(run_id, "../manifest.json")
    with pytest.raises(WorkspaceSafetyError, match="not allowed"):
        workspaces.safe_scatter_file(run_id, "unlisted-export.js")
