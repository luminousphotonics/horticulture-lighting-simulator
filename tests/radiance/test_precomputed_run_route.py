from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from fastapi import HTTPException, Request

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend import workspace as workspace_mod  # noqa: E402
from rad_rebuild.radiance.backend.jobs import JobRecord, TERMINAL_STATES, create_job_service  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest, request_with_updates  # noqa: E402
from rad_rebuild.radiance.backend.routes import runs as runs_route  # noqa: E402
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.domain import JobState  # noqa: E402
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (  # noqa: E402
    DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
    DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    NOMINAL_FIXTURE_PPF_UMOL_S as DEFAULT_HPS_FIXTURE_PPF,
    NOMINAL_INPUT_WATTS as DEFAULT_HPS_INPUT_WATTS,
)
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    SCHEMA_VERSION,
    bundle_ref,
    canonical_plant_enabled_precomputed_request,
    request_params_for_mode,
)


class _FakeRequest:
    def __init__(self, session_id: str = "precomputed-route", job_service=None) -> None:
        self.query_params = {"session_id": session_id}
        self.headers: dict[str, str] = {}
        if job_service is not None:
            self.app = SimpleNamespace(state=SimpleNamespace(job_service=job_service))


def _request(
    session_id: str = "precomputed-route", job_service: object | None = None
) -> Request:
    return cast(Request, _FakeRequest(session_id, job_service))


class _FakeJobService:
    def __init__(self) -> None:
        self.submitted: list[JobRecord] = []

    def submit(
        self,
        command: list[str],
        cwd: Path,
        *,
        env: dict[str, str] | None = None,
        owner_session: str = "anon",
        request_fingerprint: str = "",
        kind: str = "radiance",
        timeout_s: float | None = None,
        on_complete=None,
    ) -> JobRecord:
        record = JobRecord(
            id="fake-hps-job",
            status="succeeded",
            command=command,
            cwd=cwd,
            env=env or {},
            owner_session=owner_session,
            request_fingerprint=request_fingerprint,
            kind=kind,
            exit_code=0,
            attempts=1,
            created_at=1.0,
            queued_at=1.0,
            started_at=1.0,
            finished_at=1.0,
            updated_at=1.0,
            timeout_s=timeout_s,
            failure=None,
            log_path=Path("/tmp/fake-hps-job.log"),
        )
        self.submitted.append(record)
        return record


class PrecomputedRunRouteTests(unittest.TestCase):
    def _write_smoke_bundle(self, dataset_root: Path, req: RadianceRunRequest) -> Path:
        ref = bundle_ref(
            Path("/unused-engine-root"),
            req.mode,
            req.length_ft,
            req.width_ft,
            dataset_root,
            req=req,
        )
        self.assertIsNotNone(ref)
        assert ref is not None
        ref.path.mkdir(parents=True)
        (ref.path / "ppfd_map.txt.gz").write_bytes(b"tiny")
        (ref.path / "layout.json").write_text("{}", encoding="utf-8")
        (ref.path / "power.json").write_text("{}", encoding="utf-8")
        ref.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "mode": req.mode,
                    "dims_ft": {"length_ft": req.length_ft, "width_ft": req.width_ft},
                    "request_params": request_params_for_mode(req),
                    "artifacts": {
                        "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                        "layout_json": "layout.json",
                        "power_json": "power.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        return ref.path

    def _write_hps_smoke_bundle(
        self, dataset_root: Path, coverage: float = 4.0
    ) -> Path:
        req = RadianceRunRequest(
            action="all",
            mode=MODE_HPS,
            length_ft=10,
            width_ft=10,
            hps_coverage_ft=coverage,
            hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
            hps_z_m=DEFAULT_HPS_MOUNT_Z_M,
            hps_fixture_ppf=DEFAULT_HPS_FIXTURE_PPF,
            hps_input_watts=DEFAULT_HPS_INPUT_WATTS,
        )
        ref = bundle_ref(
            Path("/unused-engine-root"),
            req.mode,
            req.length_ft,
            req.width_ft,
            dataset_root,
            req=req,
        )
        self.assertIsNotNone(ref)
        assert ref is not None
        ref.path.mkdir(parents=True)
        (ref.path / "ppfd_map.txt.gz").write_bytes(b"tiny")
        (ref.path / "hps_layout.json").write_text('{"fixtures": []}', encoding="utf-8")
        (ref.path / "power.json").write_text("{}", encoding="utf-8")
        ref.manifest_path.write_text(
            json.dumps(
                {
                    "schema_version": SCHEMA_VERSION,
                    "mode": req.mode,
                    "dims_ft": {"length_ft": 10, "width_ft": 10},
                    "request_params": request_params_for_mode(req),
                    "artifacts": {
                        "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                        "layout_json": "hps_layout.json",
                        "power_json": "power.json",
                    },
                }
            ),
            encoding="utf-8",
        )
        return ref.path

    def test_precomputed_job_writes_to_staging_then_promotes_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_route_job_staging_") as tmp:
            root = Path(tmp)
            dataset_root = root / "precomputed"
            workspace_root = root / "workspace-store"
            evidence_path = root / "stub-evidence.json"
            req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                execution_mode="precomputed",
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            )
            self._write_smoke_bundle(
                dataset_root,
                request_with_updates(req, match_system_ppe=True),
            )
            service = create_job_service(
                db_path=root / "jobs.sqlite3",
                log_dir=root / "job-logs",
                queue_limit=4,
                worker_count=1,
                max_log_lines=100,
                max_log_bytes=64_000,
                timeout_s=5.0,
                sse_client_limit=2,
            )
            leases: list[workspace_mod.WorkspaceLease] = []
            original_allocate = workspace_mod.allocate_workspace_for_run

            def record_allocate(session_id: str, request_obj: object) -> workspace_mod.WorkspaceLease:
                lease = original_allocate(session_id, request_obj)
                leases.append(lease)
                return lease

            def stub_pipeline_command(
                request_obj: RadianceRunRequest,
                *,
                workspace_root: Path,
                session_id: str = "anon",
            ) -> tuple[list[str], dict[str, str]]:
                del request_obj, session_id
                script = (
                    "import json, pathlib, sys;"
                    "workspace = pathlib.Path(sys.argv[1]);"
                    "evidence = pathlib.Path(sys.argv[2]);"
                    "(workspace / 'runtime_state').mkdir(parents=True, exist_ok=True);"
                    "(workspace / 'ppfd_map.txt').write_text('1\\n', encoding='utf-8');"
                    "(workspace / 'runtime_state' / 'stub.json').write_text('{}', encoding='utf-8');"
                    "evidence.write_text(json.dumps({"
                    "'cwd': str(pathlib.Path.cwd()),"
                    "'workspace': str(workspace),"
                    "'ppfd_exists': (workspace / 'ppfd_map.txt').is_file(),"
                    "}), encoding='utf-8')"
                )
                return (
                    [sys.executable, "-c", script, str(workspace_root), str(evidence_path)],
                    {"PATH": os.environ.get("PATH", ""), "RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)},
                )

            try:
                with (
                    patch.dict(os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}),
                    patch.object(workspace_mod, "ROOT", workspace_root),
                    patch.object(workspace_mod, "ARTIFACTS", workspace_root / "artifacts"),
                    patch.object(workspace_mod, "CONTENT_ARTIFACTS", workspace_root / "artifacts" / "content"),
                    patch.object(workspace_mod, "STAGING_ARTIFACTS", workspace_root / "artifacts" / "staging"),
                    patch.object(workspace_mod, "SESSION_ARTIFACTS", workspace_root / "artifacts" / "session_grants"),
                    patch.object(
                        workspace_mod,
                        "ARTIFACT_SECRET_PATH",
                        workspace_root / "runtime_state" / "artifact_token_secret.key",
                    ),
                    patch.object(runs_route, "ROOT", workspace_root),
                    patch.object(runs_route, "maybe_cleanup_runtime_state", lambda: None),
                    patch.object(runs_route, "allocate_workspace_for_run", record_allocate),
                    patch.object(runs_route, "pipeline_command", stub_pipeline_command),
                ):
                    for directory in (
                        workspace_root / "runtime_state",
                        workspace_root / "artifacts",
                        workspace_root / "artifacts" / "content",
                        workspace_root / "artifacts" / "staging",
                        workspace_root / "artifacts" / "session_grants",
                    ):
                        directory.mkdir(parents=True, exist_ok=True)
                    response = runs_route.run_radiance(
                        req,
                        _request("staged-precomputed-route", service),
                    )
                    job = self._wait_for_promoted_job(
                        service,
                        str(response["job_id"]),
                        leases,
                        evidence_path,
                    )
                    self.assertEqual(job.status, JobState.SUCCEEDED.value)
                    self.assertEqual(len(leases), 1)
                    lease = leases[0]
                    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
                    self.assertEqual(evidence["cwd"], str(lease.staging_workspace))
                    self.assertEqual(evidence["workspace"], str(lease.staging_workspace))
                    self.assertTrue(evidence["ppfd_exists"])
                    self.assertTrue((lease.committed_workspace / "ppfd_map.txt").is_file())
                    self.assertFalse(lease.staging_root.exists())
                    tail = service.tail(job.id, cursor=0, limit=100, owner_session="staged-precomputed-route")
                    self.assertNotIn("Failed to finalize workspace", "\n".join(tail.lines))
            finally:
                service.shutdown()

    def _wait_for_promoted_job(
        self,
        service,
        job_id: str,
        leases: list[workspace_mod.WorkspaceLease],
        evidence_path: Path,
    ) -> JobRecord:
        deadline = time.time() + 5.0
        last = service.get(job_id)
        while time.time() < deadline:
            last = service.get(job_id)
            if last.status in TERMINAL_STATES and leases:
                lease = leases[0]
                promoted = (
                    evidence_path.is_file()
                    and (lease.committed_workspace / "ppfd_map.txt").is_file()
                    and not lease.staging_root.exists()
                )
                if last.status != JobState.SUCCEEDED.value or promoted:
                    return last
            time.sleep(0.05)
        raise AssertionError(f"Timed out waiting for promoted job; last status {last.status}")

    def test_start_all_hps_precomputed_route_uses_default_bundle_identity(self) -> None:
        service = _FakeJobService()
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_hps_precomputed_"
        ) as tmp:
            dataset_root = Path(tmp)
            bundle_path = self._write_hps_smoke_bundle(dataset_root, coverage=4.0)
            req = RadianceRunRequest(
                action="all",
                mode=MODE_HPS,
                length_ft=10,
                width_ft=10,
                hps_coverage_ft=4,
                hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
                hps_z_m=0.4572,
                hps_fixture_ppf=DEFAULT_HPS_FIXTURE_PPF,
                hps_input_watts=DEFAULT_HPS_INPUT_WATTS,
            )

            with (
                patch.dict(
                    os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
                ),
                patch.object(
                    runs_route,
                    "job_service",
                    return_value=service,
                ),
            ):
                response = runs_route.run_radiance(req, _request())

        self.assertEqual(response["job_id"], "fake-hps-job")
        self.assertEqual(response["status"], "succeeded")
        self.assertRegex(response["artifact_token"], r"^v1\.")
        self.assertEqual(len(service.submitted), 1)
        cmd = service.submitted[0].command
        command_text = " ".join(cmd)
        self.assertIn("precomputed_playback", command_text)
        self.assertIn(str(bundle_path.parent.parent.parent), command_text)
        self.assertIn("--hps-z-m 0.4572", command_text)
        self.assertIn("--hps-coverage-ft 4", command_text)
        self.assertIn("--hps-ies-variant karma", command_text)

    def test_start_all_proposed_precomputed_route_matches_10x10_plant_bundle(
        self,
    ) -> None:
        service = _FakeJobService()
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_smd_10x10_precomputed_"
        ) as tmp:
            dataset_root = Path(tmp)
            ui_req = RadianceRunRequest(
                action="all",
                mode=MODE_SMD,
                execution_mode="precomputed",
                sim_mode="standard",
                length_ft=10,
                width_ft=10,
                target_ppfd=750,
                match_system_ppe=True,
                plants_enabled=True,
                fspm_target_ppfd_umol_m2_s=275,
                fspm_target_tolerance_umol_m2_s=20,
            )
            bundle_req = canonical_plant_enabled_precomputed_request(ui_req)
            bundle_path = self._write_smoke_bundle(dataset_root, bundle_req)

            with (
                patch.dict(
                    os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
                ),
                patch.object(
                    runs_route,
                    "job_service",
                    return_value=service,
                ),
            ):
                response = runs_route.run_radiance(ui_req, _request("smd-10x10"))

        self.assertEqual(response["job_id"], "fake-hps-job")
        self.assertEqual(len(service.submitted), 1)
        command_text = " ".join(service.submitted[0].command)
        self.assertIn("precomputed_playback", command_text)
        self.assertIn(str(bundle_path.parent.parent), command_text)
        self.assertIn("--length-ft 10", command_text)
        self.assertIn("--width-ft 10", command_text)
        self.assertIn("--plants-enabled", command_text)
        self.assertIn("--fspm-target-ppfd-umol-m2-s 275", command_text)
        self.assertIn("--fspm-target-tolerance-umol-m2-s 20", command_text)

    def test_start_all_proposed_precomputed_route_matches_with_plants_omitted_or_false(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_smd_10x10_variants_"
        ) as tmp:
            dataset_root = Path(tmp)
            base_req = RadianceRunRequest(
                action="all",
                mode=MODE_SMD,
                execution_mode="precomputed",
                length_ft=10,
                width_ft=10,
                match_system_ppe=True,
            )
            self._write_smoke_bundle(
                dataset_root,
                canonical_plant_enabled_precomputed_request(base_req),
            )
            cases: tuple[tuple[str, dict[str, object]], ...] = (
                ("smd-10x10-omitted", {}),
                ("smd-10x10-false", {"plants_enabled": False}),
            )
            for session_id, extra_fields in cases:
                service = _FakeJobService()
                req = RadianceRunRequest.model_validate(
                    {
                        "action": "all",
                        "mode": MODE_SMD,
                        "execution_mode": "precomputed",
                        "length_ft": 10,
                        "width_ft": 10,
                        "match_system_ppe": True,
                        **extra_fields,
                    }
                )
                with (
                    patch.dict(
                        os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
                    ),
                    patch.object(
                        runs_route,
                        "job_service",
                        return_value=service,
                    ),
                ):
                    response = runs_route.run_radiance(req, _request(session_id))
                self.assertEqual(response["job_id"], "fake-hps-job")
                self.assertEqual(len(service.submitted), 1)
                self.assertIn("--plants-enabled", " ".join(service.submitted[0].command))

    def test_start_all_competitor_practical_precomputed_route_resolves_10x10(
        self,
    ) -> None:
        service = _FakeJobService()
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_competitor_10x10_precomputed_"
        ) as tmp:
            dataset_root = Path(tmp)
            req = canonical_plant_enabled_precomputed_request(
                RadianceRunRequest(
                    action="all",
                    mode=MODE_COMPETITOR,
                    execution_mode="precomputed",
                    length_ft=10,
                    width_ft=10,
                    competitor_layout="practical",
                )
            )
            bundle_path = self._write_smoke_bundle(dataset_root, req)

            with (
                patch.dict(
                    os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
                ),
                patch.object(
                    runs_route,
                    "job_service",
                    return_value=service,
                ),
            ):
                response = runs_route.run_radiance(req, _request("competitor-10x10"))

        self.assertEqual(response["job_id"], "fake-hps-job")
        command_text = " ".join(service.submitted[0].command)
        self.assertIn("precomputed_playback", command_text)
        self.assertIn(str(bundle_path.parent.parent), command_text)
        self.assertIn("--competitor-layout practical", command_text)

    def test_start_all_precomputed_route_matches_unique_rectangle_plant_identity(
        self,
    ) -> None:
        service = _FakeJobService()
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_smd_rect_precomputed_"
        ) as tmp:
            dataset_root = Path(tmp)
            bundle_req = canonical_plant_enabled_precomputed_request(
                RadianceRunRequest(
                    action="all",
                    mode=MODE_SMD,
                    execution_mode="precomputed",
                    length_ft=11,
                    width_ft=10,
                )
            )
            bundle_path = self._write_smoke_bundle(dataset_root, bundle_req)
            req = RadianceRunRequest(
                action="all",
                mode=MODE_SMD,
                execution_mode="precomputed",
                length_ft=10,
                width_ft=11,
                plants_enabled=False,
                target_ppfd=1200,
                fspm_target_ppfd_umol_m2_s=275,
                fspm_target_tolerance_umol_m2_s=20,
            )

            with (
                patch.dict(
                    os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
                ),
                patch.object(
                    runs_route,
                    "job_service",
                    return_value=service,
                ),
            ):
                response = runs_route.run_radiance(req, _request("smd-rect"))

        self.assertEqual(response["job_id"], "fake-hps-job")
        self.assertEqual(len(service.submitted), 1)
        command_text = " ".join(service.submitted[0].command)
        self.assertIn("precomputed_playback", command_text)
        self.assertIn(str(bundle_path.parent.parent), command_text)
        self.assertIn("--length-ft 11", command_text)
        self.assertIn("--width-ft 10", command_text)
        self.assertIn("--plants-enabled", command_text)

    def test_missing_precomputed_bundle_response_is_structured_for_frontend(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_missing_precomputed_"
        ) as tmp:
            req = RadianceRunRequest(
                action="all",
                mode="SMD",
                execution_mode="precomputed",
                length_ft=11,
                width_ft=10,
            )
            with patch.dict(os.environ, {"RADIANCE_PRECOMPUTED_ROOT": tmp}):
                with self.assertRaises(HTTPException) as raised:
                    runs_route.run_radiance(req, _request("missing-precomputed"))

        self.assertEqual(raised.exception.status_code, 404)
        detail = cast(dict[str, Any], raised.exception.detail)
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["error"], "precomputed_bundle_missing")
        self.assertEqual(detail["mode"], "SMD")
        self.assertEqual(detail["dimensions"]["slug"], "11x10")
        self.assertIn(
            "download_precomputed.py --dataset full", detail["download_command"]
        )
        self.assertEqual(detail["estimated_size"], "15.6 MiB")
        self.assertEqual(detail["demo"], {"length_ft": 10, "width_ft": 10})
        diagnostics = cast(dict[str, Any], detail["diagnostics"])
        self.assertIn("attempts", diagnostics)
        attempts = cast(list[dict[str, Any]], diagnostics["attempts"])
        self.assertTrue(attempts)
        self.assertIn("requested_params", attempts[0])
        self.assertIn("available_sizes_for_mode", attempts[0])

    def test_missing_precomputed_bundle_diagnostics_include_identity_diff(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_mismatch_precomputed_"
        ) as tmp:
            dataset_root = Path(tmp)
            installed_req = canonical_plant_enabled_precomputed_request(
                RadianceRunRequest(
                    action="all",
                    mode=MODE_SMD,
                    execution_mode="precomputed",
                    length_ft=11,
                    width_ft=10,
                    plant_seed=2,
                )
            )
            self._write_smoke_bundle(dataset_root, installed_req)
            req = RadianceRunRequest(
                action="all",
                mode=MODE_SMD,
                execution_mode="precomputed",
                length_ft=10,
                width_ft=11,
                plants_enabled=False,
            )
            with patch.dict(
                os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
            ):
                with self.assertRaises(HTTPException) as raised:
                    runs_route.run_radiance(req, _request("mismatch-precomputed"))

        detail = cast(dict[str, Any], raised.exception.detail)
        diagnostics = cast(dict[str, Any], detail["diagnostics"])
        attempts = cast(list[dict[str, Any]], diagnostics["attempts"])
        plant_attempt = next(
            attempt
            for attempt in attempts
            if attempt.get("candidate") == "plant-contract"
        )
        diff = cast(dict[str, Any], plant_attempt["param_diff"])
        differing = cast(dict[str, Any], diff["differing"])
        self.assertEqual(
            differing["plant_seed"],
            {"requested": 1, "installed": 2},
        )

    def test_obsolete_false_ppe_smd_manifest_mismatches_with_identity_diff(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_route_old_smd_contract_"
        ) as tmp:
            dataset_root = Path(tmp)
            base_req = RadianceRunRequest(
                action="all",
                mode=MODE_SMD,
                execution_mode="precomputed",
                length_ft=10,
                width_ft=10,
                plants_enabled=True,
                match_system_ppe=False,
            )
            old_bundle_req = request_with_updates(
                canonical_plant_enabled_precomputed_request(base_req),
                match_system_ppe=False,
            )
            self._write_smoke_bundle(dataset_root, old_bundle_req)
            req = RadianceRunRequest(
                action="all",
                mode=MODE_SMD,
                execution_mode="precomputed",
                length_ft=10,
                width_ft=10,
                plants_enabled=True,
                match_system_ppe=False,
            )
            with patch.dict(
                os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
            ):
                with self.assertRaises(HTTPException) as raised:
                    runs_route.run_radiance(req, _request("old-smd-contract"))

        detail = cast(dict[str, Any], raised.exception.detail)
        diagnostics = cast(dict[str, Any], detail["diagnostics"])
        attempts = cast(list[dict[str, Any]], diagnostics["attempts"])
        plant_attempt = next(
            attempt
            for attempt in attempts
            if attempt.get("candidate") == "plant-contract"
        )
        diff = cast(dict[str, Any], plant_attempt["param_diff"])
        differing = cast(dict[str, Any], diff["differing"])
        self.assertEqual(
            differing["match_system_ppe"],
            {"requested": True, "installed": False},
        )
        self.assertEqual(
            differing["smd_model"],
            {"requested": "legacy", "installed": "curve"},
        )

    def test_precomputed_dimensions_above_public_range_are_structured(self) -> None:
        req = RadianceRunRequest.model_construct(
            action="all",
            mode="SMD",
            execution_mode="precomputed",
            length_ft=31,
            width_ft=10,
        )
        with self.assertRaises(HTTPException) as raised:
            runs_route.run_radiance(req, _request("range-precomputed"))

        self.assertEqual(raised.exception.status_code, 422)
        detail = cast(dict[str, Any], raised.exception.detail)
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail["error"], "precomputed_dimension_unsupported")
        self.assertEqual(detail["dimensions"]["slug"], "31x10")


if __name__ == "__main__":
    unittest.main()
