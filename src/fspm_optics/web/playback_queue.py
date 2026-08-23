"""Bounded in-process FIFO admission for public playback preparation."""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import logging
import re
import time
from typing import Awaitable, Callable, Mapping
import uuid

from .precomputed import PrecomputedPlaybackError


logger = logging.getLogger("uvicorn.error.fspm_optics.public.queue")
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{7,127}\Z")
_TERMINAL_STATES = frozenset({"completed", "failed"})


class PlaybackQueueFull(RuntimeError):
    """The bounded active-plus-waiting admission capacity is exhausted."""


class PlaybackRequestNotFound(LookupError):
    """A request ID is unknown or its tombstone has expired."""


class PlaybackIdempotencyConflict(ValueError):
    """An idempotency key was already used for a different selector."""


class PlaybackIdempotencyInvalid(ValueError):
    """An idempotency key is outside the bounded public contract."""


@dataclass(slots=True)
class _PlaybackRequestRecord:
    request_id: str
    idempotency_key: str
    selector_fingerprint: str
    selector: dict[str, object] | None
    state: str
    created_at: float
    admission_sequence: int
    start_sequence: int | None = None
    started_at: float | None = None
    finished_at: float | None = None
    expired_at: float | None = None
    result: dict[str, object] | None = None
    error: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class PlaybackAdmission:
    payload: dict[str, object]
    immediate: bool
    repeated: bool


class PlaybackLaunchQueue:
    """FIFO queue whose records contain no bundle or playback objects."""

    def __init__(
        self,
        loader: Callable[[dict[str, object]], Awaitable[dict[str, object]]],
        *,
        active_limit: int = 4,
        waiting_limit: int = 128,
        record_limit: int = 512,
        terminal_ttl_seconds: float = 15 * 60.0,
        expired_ttl_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if active_limit < 1 or waiting_limit < 1:
            raise ValueError("playback queue capacities must be positive")
        if record_limit < active_limit + waiting_limit:
            raise ValueError("record limit must cover active and waiting capacity")
        if terminal_ttl_seconds <= 0.0 or expired_ttl_seconds <= 0.0:
            raise ValueError("playback queue TTLs must be positive")
        self.active_limit = active_limit
        self.waiting_limit = waiting_limit
        self.record_limit = record_limit
        self.terminal_ttl_seconds = terminal_ttl_seconds
        self.expired_ttl_seconds = expired_ttl_seconds
        self._loader = loader
        self._clock = clock
        self._pending: asyncio.Queue[str] = asyncio.Queue(
            maxsize=active_limit + waiting_limit
        )
        self._records: OrderedDict[str, _PlaybackRequestRecord] = OrderedDict()
        self._idempotency: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._workers: list[asyncio.Task[None]] = []
        self._cleaner: asyncio.Task[None] | None = None
        self._accepting = False
        self._admission_sequence = 0
        self._start_sequence = 0

    async def start(self) -> None:
        async with self._lock:
            if self._accepting:
                return
            self._accepting = True
            self._workers = [
                asyncio.create_task(
                    self._worker(index), name=f"playback-queue-{index}"
                )
                for index in range(self.active_limit)
            ]
            self._cleaner = asyncio.create_task(
                self._cleanup_loop(), name="playback-queue-cleanup"
            )

    async def submit(
        self,
        selector: Mapping[str, object],
        idempotency_key: str | None,
        *,
        immediate_result: Mapping[str, object] | None = None,
    ) -> PlaybackAdmission:
        token = _validated_idempotency_key(idempotency_key)
        normalized = dict(selector)
        fingerprint = _selector_fingerprint(normalized)
        now = self._clock()
        async with self._lock:
            self._cleanup_locked(now)
            if not self._accepting:
                raise PlaybackQueueFull("playback admission is unavailable")
            existing_id = self._idempotency.get(token)
            if existing_id is not None:
                existing = self._records[existing_id]
                if existing.selector_fingerprint != fingerprint:
                    raise PlaybackIdempotencyConflict(
                        "Idempotency key is already bound to a different request."
                    )
                return PlaybackAdmission(
                    self._payload(existing, now),
                    immediate=existing.state == "completed",
                    repeated=True,
                )
            live_count = sum(
                record.state in {"queued", "running"}
                for record in self._records.values()
            )
            if immediate_result is None and live_count >= (
                self.active_limit + self.waiting_limit
            ):
                raise PlaybackQueueFull("playback queue capacity is exhausted")
            self._make_record_space_locked()
            request_id = uuid.uuid4().hex
            record = _PlaybackRequestRecord(
                request_id=request_id,
                idempotency_key=token,
                selector_fingerprint=fingerprint,
                selector=None if immediate_result is not None else normalized,
                state="completed" if immediate_result is not None else "queued",
                created_at=now,
                admission_sequence=self._admission_sequence,
                started_at=now if immediate_result is not None else None,
                finished_at=now if immediate_result is not None else None,
                result=(
                    dict(immediate_result)
                    if immediate_result is not None
                    else None
                ),
            )
            self._admission_sequence += 1
            self._records[request_id] = record
            self._idempotency[token] = request_id
            if immediate_result is None:
                self._pending.put_nowait(request_id)
            return PlaybackAdmission(
                self._payload(record, now),
                immediate=immediate_result is not None,
                repeated=False,
            )

    async def get(self, request_id: str) -> dict[str, object]:
        if not _valid_request_id(request_id):
            raise PlaybackRequestNotFound(request_id)
        now = self._clock()
        async with self._lock:
            self._cleanup_locked(now)
            record = self._records.get(request_id)
            if record is None:
                raise PlaybackRequestNotFound(request_id)
            return self._payload(record, now)

    async def stats(self) -> dict[str, int | bool]:
        now = self._clock()
        async with self._lock:
            self._cleanup_locked(now)
            counts = {
                state: sum(record.state == state for record in self._records.values())
                for state in ("queued", "running", "completed", "failed", "expired")
            }
            return {
                **counts,
                "accepting": self._accepting,
                "active_limit": self.active_limit,
                "waiting_limit": self.waiting_limit,
                "record_limit": self.record_limit,
            }

    async def shutdown(self) -> None:
        async with self._lock:
            self._accepting = False
            tasks = [*self._workers]
            if self._cleaner is not None:
                tasks.append(self._cleaner)
            self._workers = []
            self._cleaner = None
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        now = self._clock()
        async with self._lock:
            for record in self._records.values():
                if record.state in {"queued", "running"}:
                    record.state = "expired"
                    record.selector = None
                    record.finished_at = now
                    record.expired_at = now
            logger.info("playback queue shutdown records=%d", len(self._records))

    async def _worker(self, index: int) -> None:
        while True:
            request_id = await self._pending.get()
            try:
                await self._run_request(request_id, index)
            finally:
                self._pending.task_done()

    async def _run_request(self, request_id: str, worker_index: int) -> None:
        now = self._clock()
        async with self._lock:
            record = self._records.get(request_id)
            if record is None or record.state != "queued":
                return
            record.state = "running"
            record.started_at = now
            record.start_sequence = self._start_sequence
            self._start_sequence += 1
            selector = dict(record.selector or {})
        logger.info(
            "playback request started id=%s worker=%d queue_wait_ms=%.3f",
            request_id,
            worker_index,
            (now - record.created_at) * 1000.0,
        )
        try:
            result = await self._loader(selector)
        except asyncio.CancelledError:
            raise
        except PrecomputedPlaybackError as exc:
            await self._finish(
                request_id,
                result=None,
                error={"code": exc.code, "message": str(exc)},
            )
        except Exception:
            logger.exception("playback request failed id=%s", request_id)
            await self._finish(
                request_id,
                result=None,
                error={
                    "code": "playback_failed",
                    "message": "Playback preparation could not be completed.",
                },
            )
        else:
            await self._finish(request_id, result=result, error=None)

    async def _finish(
        self,
        request_id: str,
        *,
        result: Mapping[str, object] | None,
        error: dict[str, str] | None,
    ) -> None:
        now = self._clock()
        async with self._lock:
            record = self._records.get(request_id)
            if record is None or record.state != "running":
                return
            record.state = "completed" if error is None else "failed"
            record.selector = None
            record.result = None if result is None else dict(result)
            record.error = error
            record.finished_at = now
            elapsed_ms = (
                0.0
                if record.started_at is None
                else (now - record.started_at) * 1000.0
            )
        logger.info(
            "playback request finished id=%s state=%s processing_ms=%.3f",
            request_id,
            record.state,
            elapsed_ms,
        )

    async def _cleanup_loop(self) -> None:
        interval = max(
            1.0,
            min(30.0, self.terminal_ttl_seconds, self.expired_ttl_seconds),
        )
        while True:
            await asyncio.sleep(interval)
            async with self._lock:
                self._cleanup_locked(self._clock())

    def _cleanup_locked(self, now: float) -> None:
        removed = 0
        for request_id, record in list(self._records.items()):
            if (
                record.state in _TERMINAL_STATES
                and record.finished_at is not None
                and now - record.finished_at >= self.terminal_ttl_seconds
            ):
                record.state = "expired"
                record.selector = None
                record.result = None
                record.error = None
                record.expired_at = now
            elif (
                record.state == "expired"
                and record.expired_at is not None
                and now - record.expired_at >= self.expired_ttl_seconds
            ):
                self._records.pop(request_id, None)
                self._idempotency.pop(record.idempotency_key, None)
                removed += 1
        if removed:
            logger.info("expired playback request records removed count=%d", removed)

    def _make_record_space_locked(self) -> None:
        while len(self._records) >= self.record_limit:
            candidate = next(
                (
                    request_id
                    for request_id, record in self._records.items()
                    if record.state in _TERMINAL_STATES | {"expired"}
                ),
                None,
            )
            if candidate is None:
                raise PlaybackQueueFull("playback record capacity is exhausted")
            removed = self._records.pop(candidate)
            self._idempotency.pop(removed.idempotency_key, None)

    def _payload(
        self, record: _PlaybackRequestRecord, now: float
    ) -> dict[str, object]:
        queue_wait_ms: float | None = None
        processing_ms: float | None = None
        total_ms: float | None = None
        if record.started_at is not None:
            queue_wait_ms = max(
                0.0, (record.started_at - record.created_at) * 1000.0
            )
            processing_ms = max(
                0.0,
                ((record.finished_at or now) - record.started_at) * 1000.0,
            )
        if record.finished_at is not None:
            total_ms = max(
                0.0, (record.finished_at - record.created_at) * 1000.0
            )
        payload: dict[str, object] = {
            "schema_id": "fspm-optics.precomputed-playback-request",
            "schema_version": 1,
            "request_id": record.request_id,
            "state": record.state,
            "status_url": (
                f"/api/precomputed/playback-requests/{record.request_id}"
            ),
            "poll_after_ms": _poll_after_ms(record.state),
            "execution_mode": "precomputed",
            "timing": {
                "admission_sequence": record.admission_sequence,
                "start_sequence": record.start_sequence,
                "queue_wait_ms": queue_wait_ms,
                "processing_ms": processing_ms,
                "total_ms": total_ms,
            },
        }
        if record.result is not None:
            payload["result"] = dict(record.result)
        if record.error is not None:
            payload["error"] = dict(record.error)
        return payload


def _validated_idempotency_key(value: str | None) -> str:
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise PlaybackIdempotencyInvalid(
            "Idempotency-Key must be 8 to 128 URL-safe ASCII characters."
        )
    return value


def _selector_fingerprint(selector: Mapping[str, object]) -> str:
    encoded = json.dumps(
        selector,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()


def _valid_request_id(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 32
        and all(character in "0123456789abcdef" for character in value)
    )


def _poll_after_ms(state: str) -> int:
    if state == "queued":
        return 1500
    if state == "running":
        return 1000
    return 0


__all__ = [
    "PlaybackAdmission",
    "PlaybackIdempotencyConflict",
    "PlaybackIdempotencyInvalid",
    "PlaybackLaunchQueue",
    "PlaybackQueueFull",
    "PlaybackRequestNotFound",
]
