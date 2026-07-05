from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend.jobs import (  # noqa: E402
    JobBackpressureError,
    JobOwnershipError,
    JobRecord,
    SQLiteJobRepository,
    _allowlisted_env,
    create_job_service,
)
from rad_rebuild.radiance.domain import JobState  # noqa: E402


def _python_cmd(source: str) -> list[str]:
    return [sys.executable, "-c", source]


def _wait_for_status(
    service, job_id: str, statuses: set[str], timeout_s: float = 5.0
) -> JobRecord:
    deadline = time.time() + timeout_s
    last = service.get(job_id)
    while time.time() < deadline:
        last = service.get(job_id)
        if last.status in statuses:
            return last
        time.sleep(0.05)
    raise AssertionError(
        f"Timed out waiting for {job_id} in {statuses}; last status {last.status}"
    )


class Phase07JobServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(prefix="rad_rebuild_phase07_jobs_")
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _service(
        self,
        name: str = "jobs",
        *,
        queue_limit: int = 4,
        worker_count: int = 1,
        max_log_lines: int = 50,
        timeout_s: float | None = 5.0,
    ):
        return create_job_service(
            db_path=self.root / f"{name}.sqlite3",
            log_dir=self.root / f"{name}-logs",
            queue_limit=queue_limit,
            worker_count=worker_count,
            max_log_lines=max_log_lines,
            max_log_bytes=32_768,
            timeout_s=timeout_s,
            sse_client_limit=2,
        )

    def test_queue_backpressure_and_fifo_ordering_are_enforced(self) -> None:
        service = self._service(queue_limit=1, worker_count=1)
        try:
            first = service.submit(
                _python_cmd("import time; print('first', flush=True); time.sleep(0.6)"),
                self.root,
                owner_session="owner-a",
            )
            _wait_for_status(service, first.id, {JobState.RUNNING.value})

            second = service.submit(
                _python_cmd("print('second', flush=True)"),
                self.root,
                owner_session="owner-a",
            )

            with self.assertRaises(JobBackpressureError):
                service.submit(
                    _python_cmd("print('third')"), self.root, owner_session="owner-a"
                )

            first_done = _wait_for_status(service, first.id, {JobState.SUCCEEDED.value})
            second_done = _wait_for_status(
                service, second.id, {JobState.SUCCEEDED.value}
            )
            self.assertIsNotNone(first_done.started_at)
            self.assertIsNotNone(second_done.started_at)
            assert first_done.started_at is not None
            assert second_done.started_at is not None
            self.assertLessEqual(first_done.started_at, second_done.started_at)
        finally:
            service.shutdown()

    def test_successful_job_status_survives_service_restart(self) -> None:
        service = self._service(name="durable")
        try:
            job = service.submit(
                _python_cmd("print('durable', flush=True)"),
                self.root,
                owner_session="owner-a",
            )
            _wait_for_status(service, job.id, {JobState.SUCCEEDED.value})
        finally:
            service.shutdown()

        restarted = self._service(name="durable")
        try:
            restored = restarted.get(job.id, "owner-a")
            tail = restarted.tail(job.id, owner_session="owner-a")
            self.assertEqual(restored.status, JobState.SUCCEEDED.value)
            self.assertIn("durable", "\n".join(tail.lines))
        finally:
            restarted.shutdown()

    def test_timeout_marks_job_and_stops_subprocess(self) -> None:
        service = self._service(timeout_s=0.2)
        try:
            job = service.submit(
                _python_cmd("import time; time.sleep(5)"),
                self.root,
                owner_session="owner-a",
            )
            timed_out = _wait_for_status(service, job.id, {JobState.TIMED_OUT.value})
            self.assertIsNotNone(timed_out.failure)
            assert timed_out.failure is not None
            self.assertEqual(timed_out.failure["kind"], "timeout")
            self.assertEqual(timed_out.failure["timeout_s"], 0.2)
            self.assertEqual(timed_out.failure["stage"], "radiance")
            self.assertEqual(timed_out.failure["active_command"], job.command)
            elapsed_s = timed_out.failure["elapsed_s"]
            assert isinstance(elapsed_s, int | float)
            self.assertGreaterEqual(float(elapsed_s), 0.0)
            tail = service.tail(job.id, cursor=0, limit=20, owner_session="owner-a")
            logs = "\n".join(tail.lines)
            self.assertIn("Job timeout: 0.2s.", logs)
            self.assertIn("Job timed out after", logs)
            self.assertIn("while running radiance:", logs)
        finally:
            service.shutdown()

    def test_explicit_none_timeout_disables_default_job_timeout(self) -> None:
        service = self._service(timeout_s=5.0)
        try:
            job = service.submit(
                _python_cmd("print('no-timeout', flush=True)"),
                self.root,
                owner_session="owner-a",
                timeout_s=None,
            )
            self.assertIsNone(job.timeout_s)
            _wait_for_status(service, job.id, {JobState.SUCCEEDED.value})
        finally:
            service.shutdown()

    @unittest.skipIf(
        os.name == "nt", "POSIX process group cleanup contract is covered on Unix"
    )
    def test_cancellation_terminates_child_process_group(self) -> None:
        service = self._service(timeout_s=10.0)
        pid_file = self.root / "child.pid"
        child_script = (
            "import pathlib, subprocess, sys, time;"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']);"
            f"pathlib.Path({str(pid_file)!r}).write_text(str(child.pid), encoding='utf-8');"
            "print('child-ready', flush=True);"
            "time.sleep(30)"
        )
        try:
            job = service.submit(
                _python_cmd(child_script), self.root, owner_session="owner-a"
            )
            _wait_for_status(service, job.id, {JobState.RUNNING.value})
            deadline = time.time() + 3.0
            while time.time() < deadline and not pid_file.exists():
                time.sleep(0.05)
            self.assertTrue(pid_file.exists())
            child_pid = int(pid_file.read_text(encoding="utf-8"))

            service.cancel(job.id, "owner-a")
            cancelled = _wait_for_status(service, job.id, {JobState.CANCELLED.value})
            self.assertEqual(cancelled.failure, {"kind": "cancelled"})

            child_gone_deadline = time.time() + 3.0
            while time.time() < child_gone_deadline:
                try:
                    os.kill(child_pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(0.05)
            else:
                self.fail(f"Child process {child_pid} survived job cancellation")
        finally:
            service.shutdown()

    def test_log_retention_is_bounded_and_tails_are_repeatable(self) -> None:
        service = self._service(max_log_lines=4)
        try:
            job = service.submit(
                _python_cmd(
                    "import time\nfor i in range(8):\n print(f'line-{i}', flush=True)\n time.sleep(0.01)"
                ),
                self.root,
                owner_session="owner-a",
            )
            _wait_for_status(service, job.id, {JobState.SUCCEEDED.value})
            first_tail = service.tail(
                job.id, cursor=0, limit=10, owner_session="owner-a"
            )
            second_tail = service.tail(
                job.id, cursor=0, limit=10, owner_session="owner-a"
            )
            self.assertLessEqual(len(first_tail.lines), 4)
            self.assertEqual(first_tail.lines, second_tail.lines)
            self.assertTrue((self.root / "jobs-logs" / f"{job.id}.log").exists())
        finally:
            service.shutdown()

    def test_ownership_is_required_for_status_tail_and_cancel(self) -> None:
        service = self._service()
        try:
            job = service.submit(
                _python_cmd("import time; time.sleep(0.2)"),
                self.root,
                owner_session="owner-a",
            )
            with self.assertRaises(JobOwnershipError):
                service.get(job.id, "owner-b")
            with self.assertRaises(JobOwnershipError):
                service.tail(job.id, owner_session="owner-b")
            with self.assertRaises(JobOwnershipError):
                service.cancel(job.id, "owner-b")
            service.cancel(job.id, "owner-a")
            _wait_for_status(
                service, job.id, {JobState.CANCELLED.value, JobState.SUCCEEDED.value}
            )
        finally:
            service.shutdown()

    def test_restart_recovery_marks_active_jobs_failed_with_metadata(self) -> None:
        repository = SQLiteJobRepository(
            self.root / "orphan.sqlite3",
            log_dir=self.root / "orphan-logs",
            max_log_lines=10,
            max_log_bytes=8192,
        )
        now = time.time()
        repository.create(
            JobRecord(
                id="orphaned-job",
                status=JobState.RUNNING.value,
                command=_python_cmd("print('orphan')"),
                cwd=self.root,
                env={},
                owner_session="owner-a",
                request_fingerprint="fingerprint",
                kind="test",
                exit_code=None,
                attempts=1,
                created_at=now,
                queued_at=now,
                started_at=now,
                finished_at=None,
                updated_at=now,
                timeout_s=5.0,
                failure=None,
                log_path=self.root / "orphan-logs" / "orphaned-job.log",
            )
        )
        service = create_job_service(
            db_path=self.root / "orphan.sqlite3",
            log_dir=self.root / "orphan-logs",
            queue_limit=1,
            worker_count=1,
            max_log_lines=10,
            max_log_bytes=8192,
            timeout_s=5.0,
        )
        try:
            self.assertEqual(service.recover_orphaned_jobs(), 1)
            recovered = service.get("orphaned-job", "owner-a")
            self.assertEqual(recovered.status, JobState.FAILED.value)
            self.assertEqual(recovered.failure, {"kind": "orphaned_on_startup"})
        finally:
            service.shutdown()

    def test_radiance_runtime_env_survives_queue_sanitization(self) -> None:
        raw_env = {
            "TARGET_PPFD": "1000",
            "LENGTH_FT": "10",
            "WIDTH_FT": "10",
            "NX": "2",
            "NY": "2",
            "AUTO_DIM": "1",
            "AUTO_DIM_MODE": "scale",
            "W_MIN": "10",
            "W_MAX": "100",
            "PYTHONPATH": "src",
            "RAYPATH": "/opt/radiance/lib:.",
            "SPYDR_LAYOUT_MODE": "practical",
            "SECRET_TOKEN": "do-not-preserve",
        }

        clean = _allowlisted_env(raw_env)

        for key in (
            "TARGET_PPFD",
            "LENGTH_FT",
            "WIDTH_FT",
            "NX",
            "NY",
            "AUTO_DIM",
            "AUTO_DIM_MODE",
            "W_MIN",
            "W_MAX",
            "PYTHONPATH",
            "RAYPATH",
            "SPYDR_LAYOUT_MODE",
        ):
            self.assertEqual(clean[key], raw_env[key])
        self.assertNotIn("SECRET_TOKEN", clean)


if __name__ == "__main__":
    unittest.main()
