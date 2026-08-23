from __future__ import annotations

import asyncio

import pytest

from fspm_optics.web.playback_queue import (
    PlaybackIdempotencyConflict,
    PlaybackIdempotencyInvalid,
    PlaybackLaunchQueue,
    PlaybackQueueFull,
    PlaybackRequestNotFound,
)
from fspm_optics.web.public import _PublicOverloaded, _PublicWorkLimiter


def test_fifo_burst_is_bounded_and_eventually_drains() -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        started: list[int] = []

        async def loader(selector: dict[str, object]) -> dict[str, object]:
            started.append(int(selector["sequence"]))
            await release.wait()
            return {"sequence": selector["sequence"]}

        queue = PlaybackLaunchQueue(
            loader,
            active_limit=2,
            waiting_limit=3,
            record_limit=8,
        )
        await queue.start()
        admissions = [
            await queue.submit(
                {"sequence": index}, f"burst-key-{index:03d}"
            )
            for index in range(5)
        ]
        await asyncio.sleep(0)
        assert started == [0, 1]
        stats = await queue.stats()
        assert stats["running"] == 2
        assert stats["queued"] == 3
        with pytest.raises(PlaybackQueueFull):
            await queue.submit({"sequence": 5}, "burst-key-005")

        release.set()
        for _attempt in range(100):
            if (await queue.stats())["completed"] == 5:
                break
            await asyncio.sleep(0)
        assert started == [0, 1, 2, 3, 4]
        assert (await queue.stats())["completed"] == 5
        for item in admissions:
            status = await queue.get(item.payload["request_id"])
            assert status["state"] == "completed"
        await queue.shutdown()

    asyncio.run(scenario())


def test_idempotency_cached_completion_and_status_are_work_free() -> None:
    async def scenario() -> None:
        calls = 0

        async def loader(_selector: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"unexpected": True}

        queue = PlaybackLaunchQueue(
            loader,
            active_limit=1,
            waiting_limit=1,
            record_limit=4,
        )
        await queue.start()
        first = await queue.submit(
            {"system": "proposed"},
            "stable-key-0001",
            immediate_result={"run_id": "a" * 32},
        )
        repeated = await queue.submit(
            {"system": "proposed"}, "stable-key-0001"
        )
        status = await queue.get(first.payload["request_id"])

        assert first.immediate is True
        assert repeated.repeated is True
        assert repeated.payload == first.payload
        assert status["state"] == "completed"
        assert calls == 0
        with pytest.raises(PlaybackIdempotencyConflict):
            await queue.submit(
                {"system": "conventional"}, "stable-key-0001"
            )
        with pytest.raises(PlaybackIdempotencyInvalid):
            await queue.submit({"system": "proposed"}, "short")
        await queue.shutdown()

    asyncio.run(scenario())


def test_terminal_records_expire_to_tombstones_then_are_removed() -> None:
    async def scenario() -> None:
        now = [0.0]

        async def loader(selector: dict[str, object]) -> dict[str, object]:
            return dict(selector)

        queue = PlaybackLaunchQueue(
            loader,
            active_limit=1,
            waiting_limit=1,
            record_limit=2,
            terminal_ttl_seconds=10.0,
            expired_ttl_seconds=5.0,
            clock=lambda: now[0],
        )
        await queue.start()
        admission = await queue.submit(
            {"system": "proposed"},
            "expiry-key-0001",
            immediate_result={"run_id": "b" * 32},
        )
        request_id = admission.payload["request_id"]

        now[0] = 10.0
        assert (await queue.get(request_id))["state"] == "expired"
        now[0] = 15.0
        with pytest.raises(PlaybackRequestNotFound):
            await queue.get(request_id)
        recreated = await queue.submit(
            {"system": "proposed"},
            "expiry-key-0001",
            immediate_result={"run_id": "c" * 32},
        )
        assert recreated.payload["request_id"] != request_id
        await queue.shutdown()

    asyncio.run(scenario())


def test_graceful_shutdown_expires_live_requests() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def loader(_selector: dict[str, object]) -> dict[str, object]:
            started.set()
            await asyncio.Event().wait()
            return {}

        queue = PlaybackLaunchQueue(
            loader,
            active_limit=1,
            waiting_limit=1,
            record_limit=3,
        )
        await queue.start()
        running = await queue.submit({"sequence": 0}, "shutdown-key-000")
        queued = await queue.submit({"sequence": 1}, "shutdown-key-001")
        await started.wait()
        await queue.shutdown()

        assert (await queue.get(running.payload["request_id"]))["state"] == (
            "expired"
        )
        assert (await queue.get(queued.payload["request_id"]))["state"] == (
            "expired"
        )
        assert (await queue.stats())["accepting"] is False

    asyncio.run(scenario())


def test_admitted_work_waits_for_executor_instead_of_failing_overload() -> None:
    async def scenario() -> None:
        limiter = _PublicWorkLimiter(maximum=1, wait_seconds=0.01)
        await limiter._semaphore.acquire()
        with pytest.raises(_PublicOverloaded):
            await limiter.run(lambda: "ordinary")
        second = asyncio.create_task(limiter.run_admitted(lambda: "second"))
        await asyncio.sleep(0)
        assert not second.done()
        limiter._semaphore.release()
        assert await second == "second"
        limiter.shutdown()

    asyncio.run(scenario())


def test_standard_queue_accepts_one_hundred_without_duplicate_generation() -> None:
    async def scenario() -> None:
        release = asyncio.Event()
        calls: list[int] = []

        async def loader(selector: dict[str, object]) -> dict[str, object]:
            calls.append(int(selector["sequence"]))
            await release.wait()
            return {"sequence": selector["sequence"]}

        queue = PlaybackLaunchQueue(
            loader,
            active_limit=4,
            waiting_limit=128,
            record_limit=256,
        )
        await queue.start()
        admissions = [
            await queue.submit(
                {"sequence": index}, f"hundred-users-{index:03d}"
            )
            for index in range(100)
        ]
        repeated = await queue.submit(
            {"sequence": 0}, "hundred-users-000"
        )
        assert repeated.repeated is True
        assert repeated.payload["request_id"] == admissions[0].payload["request_id"]

        await asyncio.sleep(0)
        stats = await queue.stats()
        assert stats["running"] == 4
        assert stats["queued"] == 96
        assert (await queue.get(admissions[0].payload["request_id"]))[
            "state"
        ] == "running"

        release.set()
        for _attempt in range(1000):
            if (await queue.stats())["completed"] == 100:
                break
            await asyncio.sleep(0)
        assert (await queue.stats())["completed"] == 100
        assert calls == list(range(100))
        await queue.shutdown()

    asyncio.run(scenario())


def test_genuine_processing_failure_is_a_safe_terminal_record() -> None:
    async def scenario() -> None:
        async def loader(_selector: dict[str, object]) -> dict[str, object]:
            raise RuntimeError("private path: /tmp/secret")

        queue = PlaybackLaunchQueue(
            loader,
            active_limit=1,
            waiting_limit=1,
            record_limit=3,
        )
        await queue.start()
        admission = await queue.submit(
            {"sequence": 0}, "terminal-failure-000"
        )
        for _attempt in range(100):
            status = await queue.get(admission.payload["request_id"])
            if status["state"] == "failed":
                break
            await asyncio.sleep(0)
        assert status["state"] == "failed"
        assert status["error"] == {
            "code": "playback_failed",
            "message": "Playback preparation could not be completed.",
        }
        assert "/tmp/secret" not in str(status)
        await queue.shutdown()

    asyncio.run(scenario())
