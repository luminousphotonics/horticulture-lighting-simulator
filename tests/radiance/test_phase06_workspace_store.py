from __future__ import annotations

import json
import multiprocessing
import multiprocessing.queues
import time
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi import HTTPException

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.workspace import (  # noqa: E402
    WorkspaceState,
    _workspace_root_for_request,
    allocate_workspace_for_run,
    artifact_key_from_request,
    authorize_workspace_from_request,
    commit_staged_workspace,
    fail_staged_workspace,
    prune_workspace_store,
    request_fingerprint,
)
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_DOCKER, MODE_SMD  # noqa: E402


def _commit_workspace_in_process(
    session_id: str,
    target_ppfd: float,
    value: str,
    queue: multiprocessing.queues.Queue[tuple[str, str, str]],
) -> None:
    req = RadianceRunRequest(
        action="all",
        mode=MODE_SMD,
        execution_mode="precomputed",
        length_ft=10,
        width_ft=10,
        target_ppfd=target_ppfd,
        mount_z_m=0.4572,
    )
    lease = allocate_workspace_for_run(session_id, req)
    (lease.staging_workspace / "runtime_state").mkdir(parents=True, exist_ok=True)
    (lease.staging_workspace / "ppfd_map.txt").write_text(
        f"0 0 0 {value}\n", encoding="utf-8"
    )
    (lease.staging_workspace / "room.rad").write_text("# room\n", encoding="utf-8")
    (lease.staging_workspace / "sensor_points.txt").write_text(
        "0 0 0\n", encoding="utf-8"
    )
    (lease.staging_workspace / "runtime_state" / "smd_layout.json").write_text(
        "{}", encoding="utf-8"
    )
    commit_staged_workspace(lease, {"runtime": "process"}, req)
    queue.put(
        (lease.workspace_key, str(lease.committed_workspace), lease.artifact_token)
    )


ProcessContext = (
    multiprocessing.context.SpawnContext | multiprocessing.context.ForkServerContext
)


def _safe_process_context() -> ProcessContext:
    methods = multiprocessing.get_all_start_methods()
    return cast(
        ProcessContext,
        multiprocessing.get_context("spawn" if "spawn" in methods else "forkserver"),
    )


class _FakeRequest:
    headers: dict[str, str]

    def __init__(self, session_id: str, token: str | None = None) -> None:
        self.query_params = {"session_id": session_id}
        if token is not None:
            self.query_params["artifact_token"] = token
        self.headers = {}


class Phase06WorkspaceStoreTests(unittest.TestCase):
    def _request(self, **overrides: object) -> RadianceRunRequest:
        data: dict[str, Any] = {
            "action": "all",
            "mode": MODE_SMD,
            "execution_mode": "precomputed",
            "length_ft": 10,
            "width_ft": 10,
            "target_ppfd": 1000,
            "mount_z_m": 0.4572,
        }
        data.update(overrides)
        return RadianceRunRequest(**data)

    def _write_minimal_workspace(self, root: Path, value: str = "100") -> None:
        (root / "runtime_state").mkdir(parents=True, exist_ok=True)
        (root / "ppfd_map.txt").write_text(f"0 0 0 {value}\n", encoding="utf-8")
        (root / "room.rad").write_text("# room\n", encoding="utf-8")
        (root / "sensor_points.txt").write_text("0 0 0\n", encoding="utf-8")
        (root / "runtime_state" / "smd_layout.json").write_text("{}", encoding="utf-8")

    def test_artifact_key_is_schema_and_fingerprint_only(self) -> None:
        req = self._request()
        key = artifact_key_from_request(req)

        self.assertRegex(key, r"^fp1_[0-9a-f]{64}$")
        self.assertEqual(key, f"fp1_{request_fingerprint(req)}")

    def test_equal_requests_share_committed_workspace_but_use_unique_staging(
        self,
    ) -> None:
        req = self._request()
        first = allocate_workspace_for_run("browser-a", req)
        second = allocate_workspace_for_run("browser-b", req)

        self.assertEqual(first.committed_workspace, second.committed_workspace)
        self.assertNotEqual(first.staging_workspace, second.staging_workspace)
        self.assertNotEqual(first.artifact_token, second.artifact_token)

        self._write_minimal_workspace(first.staging_workspace, "111")
        commit_staged_workspace(first, {"runtime": "same"})
        self._write_minimal_workspace(second.staging_workspace, "222")
        commit_staged_workspace(second, {"runtime": "same"})

        self.assertEqual(
            (first.committed_workspace / "ppfd_map.txt").read_text(encoding="utf-8"),
            "0 0 0 111\n",
        )
        self.assertFalse(second.staging_workspace.exists())
        self.assertEqual(
            _workspace_root_for_request("browser-a", req), first.committed_workspace
        )

    def test_live_runs_replace_committed_workspace_for_same_request(self) -> None:
        req = self._request(execution_mode=EXECUTION_MODE_LIVE_DOCKER)
        first = allocate_workspace_for_run("browser-a", req)
        self._write_minimal_workspace(first.staging_workspace, "1")
        commit_staged_workspace(first, {"runtime": "same-live"}, req)

        second = allocate_workspace_for_run("browser-a", req)
        self._write_minimal_workspace(second.staging_workspace, "1000")
        commit_staged_workspace(second, {"runtime": "same-live"}, req)

        self.assertEqual(first.committed_workspace, second.committed_workspace)
        self.assertEqual(
            (second.committed_workspace / "ppfd_map.txt").read_text(encoding="utf-8"),
            "0 0 0 1000\n",
        )
        self.assertFalse(second.staging_workspace.exists())
        self.assertTrue((second.record_root / "quarantine").exists())

    def test_session_bound_token_is_required_for_private_workspace_reads(self) -> None:
        req = self._request()
        lease = allocate_workspace_for_run("browser-a", req)
        self._write_minimal_workspace(lease.staging_workspace)
        commit_staged_workspace(lease, {"runtime": "token"})

        authorized = authorize_workspace_from_request(
            _FakeRequest("browser-a", lease.artifact_token), req
        )
        self.assertEqual(authorized, lease.committed_workspace)

        with self.assertRaises(HTTPException) as missing:
            authorize_workspace_from_request(_FakeRequest("browser-a"), req)
        self.assertEqual(missing.exception.status_code, 403)

        with self.assertRaises(HTTPException) as crossed:
            authorize_workspace_from_request(
                _FakeRequest("browser-b", lease.artifact_token), req
            )
        self.assertEqual(crossed.exception.status_code, 403)

    def test_integrity_mismatch_fails_closed(self) -> None:
        req = self._request()
        lease = allocate_workspace_for_run("browser-a", req)
        self._write_minimal_workspace(lease.staging_workspace)
        commit_staged_workspace(lease, {"runtime": "integrity"})

        (lease.committed_workspace / "ppfd_map.txt").write_text(
            "0 0 0 999\n", encoding="utf-8"
        )

        with self.assertRaises(HTTPException) as raised:
            authorize_workspace_from_request(
                _FakeRequest("browser-a", lease.artifact_token), req
            )
        self.assertEqual(raised.exception.status_code, 409)

    def test_failed_staging_workspaces_are_expired_with_lock_awareness(self) -> None:
        req = self._request(target_ppfd=900)
        lease = allocate_workspace_for_run("browser-a", req)
        self._write_minimal_workspace(lease.staging_workspace)
        fail_staged_workspace(lease, "simulated failure")

        state_path = lease.record_root / "state.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["updated_at"] = time.time() - 10
        state_path.write_text(json.dumps(state), encoding="utf-8")

        metrics = prune_workspace_store(now=time.time(), retention_s=0, max_records=100)

        self.assertGreaterEqual(metrics["expired"], 1)
        updated = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(updated["state"], WorkspaceState.EXPIRED.value)

    def test_two_processes_deduplicate_equal_requests_and_separate_unequal_requests(
        self,
    ) -> None:
        context = _safe_process_context()
        self.assertIn(context.get_start_method(), {"forkserver", "spawn"})

        equal_queue = context.Queue()
        first = context.Process(
            target=_commit_workspace_in_process,
            args=("proc-a", 875, "875", equal_queue),
        )
        second = context.Process(
            target=_commit_workspace_in_process,
            args=("proc-b", 875, "876", equal_queue),
        )
        first.start()
        second.start()
        first.join(10)
        second.join(10)
        self.assertEqual(first.exitcode, 0)
        self.assertEqual(second.exitcode, 0)
        equal_results = [equal_queue.get(timeout=2), equal_queue.get(timeout=2)]
        self.assertEqual(equal_results[0][0], equal_results[1][0])
        self.assertEqual(equal_results[0][1], equal_results[1][1])

        unequal_queue = context.Queue()
        third = context.Process(
            target=_commit_workspace_in_process,
            args=("proc-c", 876, "876", unequal_queue),
        )
        fourth = context.Process(
            target=_commit_workspace_in_process,
            args=("proc-d", 877, "877", unequal_queue),
        )
        third.start()
        fourth.start()
        third.join(10)
        fourth.join(10)
        self.assertEqual(third.exitcode, 0)
        self.assertEqual(fourth.exitcode, 0)
        unequal_results = [unequal_queue.get(timeout=2), unequal_queue.get(timeout=2)]
        self.assertNotEqual(unequal_results[0][0], unequal_results[1][0])
        self.assertNotEqual(unequal_results[0][1], unequal_results[1][1])


if __name__ == "__main__":
    unittest.main()
