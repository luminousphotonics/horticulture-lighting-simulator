from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO

from fastapi import Request

from rad_rebuild.radiance.domain import JobState
from rad_rebuild.radiance.settings import get_settings


TERMINAL_STATES = {
    JobState.SUCCEEDED.value,
    JobState.FAILED.value,
    JobState.CANCELLED.value,
    JobState.TIMED_OUT.value,
}
ACTIVE_STATES = {
    JobState.QUEUED.value,
    JobState.RUNNING.value,
    JobState.CANCELLING.value,
}
COMPLETED_JOB_RETENTION_S = get_settings().completed_job_retention_s

_ENV_EXACT_ALLOWLIST = {
    "ALIGN_LONG_AXIS_X",
    "AUTO_DIM",
    "AUTO_DIM_MODE",
    "AUTO_DIM_TARGET",
    "AXES_ONLY",
    "BASIS_PATH",
    "CANOPY_AREA_M2",
    "EFF_SCALE",
    "HOME",
    "LANG",
    "LC_ALL",
    "LOG_CAP_METRICS",
    "MARGIN_IN",
    "MEAN_TOL",
    "MPLCONFIGDIR",
    "MPLBACKEND",
    "MODE",
    "MOUNT_Z_M",
    "NX",
    "NY",
    "OPTICS",
    "OUT_JSON",
    "PATH",
    "PY",
    "PYTHONPATH",
    "RAYPATH",
    "PPE_IS_SYSTEM",
    "RING_POWERS_JSON",
    "RING_POWERS_REQUIRED",
    "RING_POWERS_STRICT",
    "RUN_BASIS",
    "SETPOINT_PPFD",
    "SUBPATCH_GRID",
    "SYM",
    "TARGET_PPFD",
    "TMPDIR",
    "USER",
    "USE_RING_POWERS_JSON",
    "W_MAX",
    "W_MIN",
}
_ENV_PREFIX_ALLOWLIST = (
    "AUTO_",
    "BOARD_",
    "GRID_",
    "COMPETITOR_",
    "DRIVER_",
    "DOCKER_",
    "DROOP_",
    "HPS_",
    "LAMBDA_",
    "LAYOUT_",
    "LENGTH_",
    "MODULE_",
    "OMP_",
    "PATCH_",
    "PMMA_",
    "PTFE_",
    "RADIANCE_",
    "RESOLUTION_",
    "SOLVE_",
    "SMD_",
    "SP_",
    "SPYDR_",
    "STACK_",
    "THERMAL_",
    "WALL_",
    "WIDTH_",
    "WIRING_",
)


class JobBackpressureError(RuntimeError):
    """Raised when the bounded local queue cannot accept more work."""


class JobOwnershipError(RuntimeError):
    """Raised when a session tries to read or mutate another session's job."""


class JobNotFoundError(RuntimeError):
    """Raised when a job ID is unknown or has been pruned."""


class JobSseLimitError(RuntimeError):
    """Raised when too many streaming log clients are attached."""


@dataclass(frozen=True)
class JobRecord:
    id: str
    status: str
    command: list[str]
    cwd: Path
    env: dict[str, str]
    owner_session: str
    request_fingerprint: str
    kind: str
    exit_code: int | None
    attempts: int
    created_at: float
    queued_at: float
    started_at: float | None
    finished_at: float | None
    updated_at: float
    timeout_s: float | None
    failure: dict[str, object] | None
    log_path: Path


@dataclass(frozen=True)
class JobTail:
    lines: list[str]
    next_cursor: int
    done: bool
    status: str


class JobRepository(Protocol):
    def create(self, record: JobRecord) -> JobRecord: ...

    def get(self, job_id: str) -> JobRecord | None: ...

    def count_by_status(self, statuses: Iterable[str]) -> int: ...

    def claim_next_queued(self) -> JobRecord | None: ...

    def append_log(self, job_id: str, stream: str, line: str) -> None: ...

    def tail_logs(self, job_id: str, cursor: int, limit: int) -> JobTail: ...

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        exit_code: int | None = None,
        failure: dict[str, object] | None = None,
    ) -> JobRecord: ...

    def request_cancel(self, job_id: str, owner_session: str | None = None) -> JobRecord: ...

    def recover_orphans(self, now: float) -> int: ...

    def prune_completed(self, now: float, retention_s: int) -> int: ...


class Executor(Protocol):
    def start(self) -> None: ...

    def wake(self) -> None: ...

    def cancel(self, job_id: str) -> None: ...

    def shutdown(self) -> None: ...


def _json_dict(raw: str | None) -> dict[str, object] | None:
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"kind": "invalid_failure_metadata", "raw": raw}
    return value if isinstance(value, dict) else {"kind": "invalid_failure_metadata", "raw": value}


def _allowlisted_env(env: dict[str, str] | None) -> dict[str, str]:
    source = os.environ if env is None else env
    clean: dict[str, str] = {}
    for key, value in source.items():
        if key in _ENV_EXACT_ALLOWLIST or any(key.startswith(prefix) for prefix in _ENV_PREFIX_ALLOWLIST):
            clean[key] = str(value)
    clean.setdefault("PATH", os.environ.get("PATH", ""))
    return clean


class SQLiteJobRepository:
    def __init__(
        self,
        db_path: Path,
        *,
        log_dir: Path,
        max_log_lines: int,
        max_log_bytes: int,
    ) -> None:
        self.db_path = db_path
        self.log_dir = log_dir
        self.max_log_lines = max(1, int(max_log_lines))
        self.max_log_bytes = max(1024, int(max_log_bytes))
        self._lock = threading.RLock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    command_json TEXT NOT NULL,
                    cwd TEXT NOT NULL,
                    env_json TEXT NOT NULL,
                    owner_session TEXT NOT NULL,
                    request_fingerprint TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    exit_code INTEGER,
                    attempts INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    queued_at REAL NOT NULL,
                    started_at REAL,
                    finished_at REAL,
                    updated_at REAL NOT NULL,
                    timeout_s REAL,
                    failure_json TEXT,
                    log_path TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS job_logs (
                    job_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    stream TEXT NOT NULL,
                    line TEXT NOT NULL,
                    PRIMARY KEY (job_id, seq),
                    FOREIGN KEY (job_id) REFERENCES jobs(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_job_logs_job_seq ON job_logs(job_id, seq);
                """
            )

    def _row_to_record(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=str(row["id"]),
            status=str(row["status"]),
            command=[str(part) for part in json.loads(str(row["command_json"]))],
            cwd=Path(str(row["cwd"])),
            env={str(k): str(v) for k, v in json.loads(str(row["env_json"])).items()},
            owner_session=str(row["owner_session"]),
            request_fingerprint=str(row["request_fingerprint"]),
            kind=str(row["kind"]),
            exit_code=None if row["exit_code"] is None else int(row["exit_code"]),
            attempts=int(row["attempts"]),
            created_at=float(row["created_at"]),
            queued_at=float(row["queued_at"]),
            started_at=None if row["started_at"] is None else float(row["started_at"]),
            finished_at=None if row["finished_at"] is None else float(row["finished_at"]),
            updated_at=float(row["updated_at"]),
            timeout_s=None if row["timeout_s"] is None else float(row["timeout_s"]),
            failure=_json_dict(row["failure_json"]),
            log_path=Path(str(row["log_path"])),
        )

    def create(self, record: JobRecord) -> JobRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (
                    id, status, command_json, cwd, env_json, owner_session, request_fingerprint,
                    kind, exit_code, attempts, created_at, queued_at, started_at, finished_at,
                    updated_at, timeout_s, failure_json, log_path
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.status,
                    json.dumps(record.command),
                    str(record.cwd),
                    json.dumps(record.env, sort_keys=True),
                    record.owner_session,
                    record.request_fingerprint,
                    record.kind,
                    record.exit_code,
                    record.attempts,
                    record.created_at,
                    record.queued_at,
                    record.started_at,
                    record.finished_at,
                    record.updated_at,
                    record.timeout_s,
                    json.dumps(record.failure, sort_keys=True) if record.failure is not None else None,
                    str(record.log_path),
                ),
            )
        record.log_path.parent.mkdir(parents=True, exist_ok=True)
        record.log_path.touch(exist_ok=True)
        return record

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._row_to_record(row) if row is not None else None

    def count_by_status(self, statuses: Iterable[str]) -> int:
        tokens = set(statuses)
        if not tokens:
            return 0
        with self._lock, self._connect() as conn:
            rows = conn.execute("SELECT status, COUNT(*) AS count FROM jobs GROUP BY status").fetchall()
        return sum(int(row["count"]) for row in rows if str(row["status"]) in tokens)

    def claim_next_queued(self) -> JobRecord | None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status = ? ORDER BY queued_at ASC, created_at ASC LIMIT 1",
                (JobState.QUEUED.value,),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, started_at = ?, updated_at = ?, attempts = attempts + 1
                WHERE id = ? AND status = ?
                """,
                (JobState.RUNNING.value, now, now, row["id"], JobState.QUEUED.value),
            )
            claimed = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
        return self._row_to_record(claimed) if claimed is not None else None

    def _append_file_log(self, record: JobRecord, line: str) -> None:
        encoded = f"{line}\n".encode("utf-8", errors="replace")
        current_size = record.log_path.stat().st_size if record.log_path.exists() else 0
        remaining = self.max_log_bytes - current_size
        if remaining <= 0:
            return
        with record.log_path.open("ab") as handle:
            handle.write(encoded[:remaining])

    def append_log(self, job_id: str, stream: str, line: str) -> None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            record = self._row_to_record(row)
            seq_row = conn.execute("SELECT COALESCE(MAX(seq), -1) + 1 AS seq FROM job_logs WHERE job_id = ?", (job_id,)).fetchone()
            seq = int(seq_row["seq"]) if seq_row is not None else 0
            conn.execute(
                "INSERT INTO job_logs (job_id, seq, created_at, stream, line) VALUES (?, ?, ?, ?, ?)",
                (job_id, seq, now, stream, line),
            )
            cutoff_row = conn.execute(
                "SELECT seq FROM job_logs WHERE job_id = ? ORDER BY seq DESC LIMIT 1 OFFSET ?",
                (job_id, self.max_log_lines - 1),
            ).fetchone()
            if cutoff_row is not None:
                conn.execute("DELETE FROM job_logs WHERE job_id = ? AND seq < ?", (job_id, int(cutoff_row["seq"])))
        self._append_file_log(record, f"[{stream}] {line}")

    def tail_logs(self, job_id: str, cursor: int, limit: int) -> JobTail:
        start = max(0, int(cursor))
        size = max(1, min(int(limit), 500))
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            record = self._row_to_record(row)
            rows = conn.execute(
                """
                SELECT seq, stream, line FROM job_logs
                WHERE job_id = ? AND seq >= ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (job_id, start, size),
            ).fetchall()
        lines = [str(log_row["line"]) for log_row in rows]
        next_cursor = (int(rows[-1]["seq"]) + 1) if rows else start
        done = record.status in TERMINAL_STATES and not rows
        return JobTail(lines=lines, next_cursor=next_cursor, done=done, status=record.status)

    def finish(
        self,
        job_id: str,
        status: str,
        *,
        exit_code: int | None = None,
        failure: dict[str, object] | None = None,
    ) -> JobRecord:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, exit_code = ?, finished_at = ?, updated_at = ?, failure_json = ?
                WHERE id = ?
                """,
                (
                    status,
                    exit_code,
                    now,
                    now,
                    json.dumps(failure, sort_keys=True) if failure is not None else None,
                    job_id,
                ),
            )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._row_to_record(row)

    def request_cancel(self, job_id: str, owner_session: str | None = None) -> JobRecord:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if row is None:
                raise JobNotFoundError(job_id)
            record = self._row_to_record(row)
            if owner_session is not None and record.owner_session != owner_session:
                raise JobOwnershipError(job_id)
            if record.status == JobState.QUEUED.value:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, finished_at = ?, updated_at = ?, failure_json = ?
                    WHERE id = ?
                    """,
                    (
                        JobState.CANCELLED.value,
                        now,
                        now,
                        json.dumps({"kind": "cancelled_before_start"}),
                        job_id,
                    ),
                )
            elif record.status == JobState.RUNNING.value:
                conn.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                    (JobState.CANCELLING.value, now, job_id),
                )
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFoundError(job_id)
        return self._row_to_record(row)

    def recover_orphans(self, now: float) -> int:
        failure = json.dumps({"kind": "orphaned_on_startup"})
        with self._lock, self._connect() as conn:
            result = conn.execute(
                """
                UPDATE jobs
                SET status = ?, finished_at = ?, updated_at = ?, failure_json = ?
                WHERE status = ? OR status = ? OR status = ?
                """,
                (
                    JobState.FAILED.value,
                    now,
                    now,
                    failure,
                    JobState.QUEUED.value,
                    JobState.RUNNING.value,
                    JobState.CANCELLING.value,
                ),
            )
        return int(result.rowcount if result.rowcount is not None else 0)

    def prune_completed(self, now: float, retention_s: int) -> int:
        cutoff = now - max(0, retention_s)
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, log_path FROM jobs
                WHERE (status = ? OR status = ? OR status = ? OR status = ?)
                AND finished_at IS NOT NULL
                AND finished_at < ?
                """,
                (
                    JobState.SUCCEEDED.value,
                    JobState.FAILED.value,
                    JobState.CANCELLED.value,
                    JobState.TIMED_OUT.value,
                    cutoff,
                ),
            ).fetchall()
            for row in rows:
                conn.execute("DELETE FROM jobs WHERE id = ?", (row["id"],))
        for row in rows:
            try:
                Path(str(row["log_path"])).unlink(missing_ok=True)
            except OSError:
                pass
        return len(rows)


class _ProcessHandle:
    def __init__(self) -> None:
        self.cancel_event = threading.Event()
        self.process: subprocess.Popen[str] | None = None

    def cancel(self) -> None:
        self.cancel_event.set()
        proc = self.process
        if proc is not None and proc.poll() is None:
            _terminate_process_tree(proc)


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    if os.name == "nt":
        proc.terminate()
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except ProcessLookupError:
            return
        except OSError:
            proc.terminate()
    try:
        proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        if os.name == "nt":
            proc.kill()
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                return
            except OSError:
                proc.kill()
        proc.wait(timeout=2.0)


class LocalExecutor:
    def __init__(self, service: JobServiceProtocol, *, worker_count: int) -> None:
        self.service = service
        self.worker_count = max(1, int(worker_count))
        self._condition = threading.Condition()
        self._shutdown = False
        self._started = False
        self._threads: list[threading.Thread] = []
        self._handles: dict[str, _ProcessHandle] = {}
        self._handles_lock = threading.Lock()

    def start(self) -> None:
        with self._condition:
            if self._started:
                return
            self._started = True
            for index in range(self.worker_count):
                thread = threading.Thread(target=self._worker, name=f"radiance-job-worker-{index}", daemon=True)
                self._threads.append(thread)
                thread.start()

    def wake(self) -> None:
        with self._condition:
            self._condition.notify_all()

    def cancel(self, job_id: str) -> None:
        with self._handles_lock:
            handle = self._handles.get(job_id)
        if handle is not None:
            handle.cancel()
        self.wake()

    def shutdown(self) -> None:
        with self._condition:
            self._shutdown = True
            self._condition.notify_all()
        with self._handles_lock:
            handles = list(self._handles.values())
        for handle in handles:
            handle.cancel()
        for thread in self._threads:
            thread.join(timeout=2.0)

    def _worker(self) -> None:
        while True:
            if self._shutdown:
                return
            record = self.service.claim_next_job()
            if record is None:
                with self._condition:
                    self._condition.wait(timeout=0.5)
                continue
            handle = _ProcessHandle()
            with self._handles_lock:
                self._handles[record.id] = handle
            try:
                self.service.execute_claimed_job(record, handle)
            finally:
                with self._handles_lock:
                    self._handles.pop(record.id, None)
                self.wake()


class JobServiceProtocol(Protocol):
    def claim_next_job(self) -> JobRecord | None: ...

    def execute_claimed_job(self, record: JobRecord, handle: _ProcessHandle) -> None: ...


class JobService:
    def __init__(
        self,
        repository: JobRepository,
        executor: Executor | None = None,
        *,
        queue_limit: int,
        worker_count: int,
        default_timeout_s: float | None,
        sse_client_limit: int,
    ) -> None:
        self.repository = repository
        self.queue_limit = max(1, int(queue_limit))
        self.default_timeout_s = default_timeout_s if default_timeout_s and default_timeout_s > 0 else None
        self.sse_client_limit = max(1, int(sse_client_limit))
        self._callbacks: dict[str, Callable[[JobRecord], None]] = {}
        self._callbacks_lock = threading.Lock()
        self._log_condition = threading.Condition()
        self._sse_clients = 0
        self.executor = executor if executor is not None else LocalExecutor(self, worker_count=worker_count)

    def start(self) -> None:
        self.executor.start()

    def shutdown(self) -> None:
        self.executor.shutdown()

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
        on_complete: Callable[[JobRecord], None] | None = None,
    ) -> JobRecord:
        self.start()
        queued = self.repository.count_by_status([JobState.QUEUED.value])
        if queued >= self.queue_limit:
            raise JobBackpressureError(f"Job queue is full ({queued}/{self.queue_limit} queued).")
        now = time.time()
        job_id = str(uuid.uuid4())
        repository_log_dir = getattr(self.repository, "log_dir", _default_log_dir())
        log_path = Path(repository_log_dir) / f"{job_id}.log"
        record = JobRecord(
            id=job_id,
            status=JobState.QUEUED.value,
            command=[str(part) for part in command],
            cwd=cwd,
            env=_allowlisted_env(env),
            owner_session=owner_session,
            request_fingerprint=request_fingerprint,
            kind=kind,
            exit_code=None,
            attempts=0,
            created_at=now,
            queued_at=now,
            started_at=None,
            finished_at=None,
            updated_at=now,
            timeout_s=timeout_s if timeout_s is not None else self.default_timeout_s,
            failure=None,
            log_path=log_path,
        )
        created = self.repository.create(record)
        if on_complete is not None:
            with self._callbacks_lock:
                self._callbacks[created.id] = on_complete
        self.executor.wake()
        return created

    def get(self, job_id: str, owner_session: str | None = None) -> JobRecord:
        record = self.repository.get(job_id)
        if record is None:
            raise JobNotFoundError(job_id)
        if owner_session is not None and record.owner_session != owner_session:
            raise JobOwnershipError(job_id)
        return record

    def tail(self, job_id: str, *, cursor: int = 0, limit: int = 200, owner_session: str | None = None) -> JobTail:
        self.get(job_id, owner_session)
        return self.repository.tail_logs(job_id, cursor, limit)

    def stream(
        self,
        job_id: str,
        *,
        owner_session: str | None = None,
    ) -> Generator[str, None, None]:
        self.get(job_id, owner_session)
        with self._log_condition:
            if self._sse_clients >= self.sse_client_limit:
                raise JobSseLimitError("Too many job log streams are open.")
            self._sse_clients += 1
        try:
            cursor = 0
            while True:
                tail = self.tail(job_id, cursor=cursor, limit=200, owner_session=owner_session)
                cursor = tail.next_cursor
                for line in tail.lines:
                    yield f"data: {line}\n\n"
                if tail.done:
                    yield f"data: [done:{tail.status}]\n\n"
                    break
                if not tail.lines:
                    yield ": keepalive\n\n"
                    with self._log_condition:
                        self._log_condition.wait(timeout=2.0)
        finally:
            with self._log_condition:
                self._sse_clients = max(0, self._sse_clients - 1)

    def cancel(self, job_id: str, owner_session: str | None = None) -> JobRecord:
        record = self.repository.request_cancel(job_id, owner_session)
        self.executor.cancel(job_id)
        self._notify_logs()
        return record

    def claim_next_job(self) -> JobRecord | None:
        return self.repository.claim_next_queued()

    def execute_claimed_job(self, record: JobRecord, handle: _ProcessHandle) -> None:
        status = JobState.FAILED.value
        exit_code: int | None = -1
        failure: dict[str, object] | None = None
        try:
            exit_code, status, failure = self._run_subprocess(record, handle)
        finally:
            finished = self._finalize_job(record.id, status, exit_code=exit_code, failure=failure)
            self._run_completion_callback(finished)

    def _run_subprocess(self, record: JobRecord, handle: _ProcessHandle) -> tuple[int | None, str, dict[str, object] | None]:
        self.append_log(record.id, "system", f"Job started: {' '.join(record.command)}")
        try:
            if os.name == "nt":
                # Commands are argv lists built by backend route adapters, never shell strings.
                proc = subprocess.Popen(  # nosec B603
                    record.command,
                    cwd=record.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=record.env,
                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                )
            else:
                # Commands are argv lists built by backend route adapters, never shell strings.
                proc = subprocess.Popen(  # nosec B603
                    record.command,
                    cwd=record.cwd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    env=record.env,
                    start_new_session=True,
                )
        except OSError as exc:
            self.append_log(record.id, "system", f"Failed to start: {exc}")
            return -1, JobState.FAILED.value, {"kind": "start_failed", "message": str(exc)}

        handle.process = proc
        readers = [
            threading.Thread(target=self._read_stream, args=(record.id, "stdout", proc.stdout), daemon=True),
            threading.Thread(target=self._read_stream, args=(record.id, "stderr", proc.stderr), daemon=True),
        ]
        for reader in readers:
            reader.start()

        deadline = None if record.timeout_s is None else time.time() + record.timeout_s
        status = JobState.FAILED.value
        failure: dict[str, object] | None = None
        while True:
            current_record = self.repository.get(record.id)
            if handle.cancel_event.is_set() or (
                current_record is not None and current_record.status == JobState.CANCELLING.value
            ):
                _terminate_process_tree(proc)
                status = JobState.CANCELLED.value
                failure = {"kind": "cancelled"}
                break
            return_code = proc.poll()
            if return_code is not None:
                status = JobState.SUCCEEDED.value if return_code == 0 else JobState.FAILED.value
                break
            if deadline is not None and time.time() >= deadline:
                _terminate_process_tree(proc)
                status = JobState.TIMED_OUT.value
                failure = {"kind": "timeout", "timeout_s": record.timeout_s}
                break
            time.sleep(0.05)

        for reader in readers:
            reader.join(timeout=1.0)
        return_code = proc.returncode
        if status == JobState.FAILED.value and failure is None:
            failure = {"kind": "exit_code", "exit_code": return_code}
        self.append_log(record.id, "system", f"Job finished with status {status}.")
        return return_code, status, failure

    def _read_stream(self, job_id: str, stream: str, pipe: TextIO | None) -> None:
        if pipe is None:
            return
        try:
            for line in pipe:
                self.append_log(job_id, stream, str(line).rstrip())
        finally:
            pipe.close()

    def append_log(self, job_id: str, stream: str, line: str) -> None:
        self.repository.append_log(job_id, stream, line)
        self._notify_logs()

    def _finalize_job(
        self,
        job_id: str,
        status: str,
        *,
        exit_code: int | None,
        failure: dict[str, object] | None,
    ) -> JobRecord:
        record = self.repository.finish(job_id, status, exit_code=exit_code, failure=failure)
        self._notify_logs()
        return record

    def _run_completion_callback(self, record: JobRecord) -> None:
        with self._callbacks_lock:
            callback = self._callbacks.pop(record.id, None)
        if callback is None:
            return
        try:
            callback(record)
        except Exception as exc:
            self.append_log(record.id, "system", f"Failed to finalize workspace: {exc}")
            self.repository.finish(
                record.id,
                JobState.FAILED.value,
                exit_code=-1,
                failure={"kind": "completion_callback_failed", "message": str(exc)},
            )
            self._notify_logs()

    def recover_orphaned_jobs(self) -> int:
        recovered = self.repository.recover_orphans(time.time())
        self._notify_logs()
        return recovered

    def prune_completed(self, now: float, retention_s: int = COMPLETED_JOB_RETENTION_S) -> int:
        pruned = self.repository.prune_completed(now, retention_s)
        self._notify_logs()
        return pruned

    def _notify_logs(self) -> None:
        with self._log_condition:
            self._log_condition.notify_all()


def _default_db_path() -> Path:
    return get_settings().paths.runtime_state_root / "jobs.sqlite3"


def _default_log_dir() -> Path:
    return get_settings().paths.runtime_state_root / "job_logs"


def create_job_service(
    *,
    db_path: Path | None = None,
    log_dir: Path | None = None,
    queue_limit: int | None = None,
    worker_count: int | None = None,
    max_log_lines: int | None = None,
    max_log_bytes: int | None = None,
    timeout_s: float | None = None,
    sse_client_limit: int | None = None,
) -> JobService:
    settings = get_settings()
    repository = SQLiteJobRepository(
        db_path or _default_db_path(),
        log_dir=log_dir or _default_log_dir(),
        max_log_lines=max_log_lines or settings.job_log_max_lines,
        max_log_bytes=max_log_bytes or settings.job_log_max_bytes,
    )
    return JobService(
        repository,
        queue_limit=queue_limit or settings.job_queue_limit,
        worker_count=worker_count or settings.job_worker_count,
        default_timeout_s=timeout_s if timeout_s is not None else settings.job_timeout_s,
        sse_client_limit=sse_client_limit or settings.job_sse_client_limit,
    )


_DEFAULT_SERVICE_LOCK = threading.Lock()
_DEFAULT_SERVICE: JobService | None = None


def job_service() -> JobService:
    global _DEFAULT_SERVICE
    with _DEFAULT_SERVICE_LOCK:
        if _DEFAULT_SERVICE is None:
            _DEFAULT_SERVICE = create_job_service()
        return _DEFAULT_SERVICE


def job_service_for_request(request: Request) -> JobService:
    try:
        service = getattr(request.app.state, "job_service", None)
    except (AttributeError, RuntimeError):
        service = None
    return service if isinstance(service, JobService) else job_service()


def set_job_service(service: JobService | None) -> None:
    global _DEFAULT_SERVICE
    with _DEFAULT_SERVICE_LOCK:
        if _DEFAULT_SERVICE is not None and _DEFAULT_SERVICE is not service:
            _DEFAULT_SERVICE.shutdown()
        _DEFAULT_SERVICE = service


def _prune_old_jobs(now: float) -> None:
    job_service().prune_completed(now)
