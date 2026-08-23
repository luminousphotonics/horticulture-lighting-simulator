#!/usr/bin/env python3
"""Local-only synchronized-user load test for public playback admission."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import ipaddress
import json
import math
import os
from pathlib import Path
import random
import sys
import time
from typing import Sequence
from urllib.parse import urlparse

import httpx


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    selector: dict[str, object]


@dataclass(frozen=True, slots=True)
class UserResult:
    index: int
    scenario: str
    state: str
    request_id: str | None
    admission_statuses: tuple[int, ...]
    admission_retries: int
    poll_count: int
    admission_sequence: int | None
    start_sequence: int | None
    queue_wait_ms: float | None
    processing_ms: float | None
    terminal_elapsed_ms: float
    flow_elapsed_ms: float
    resource_statuses: tuple[int, ...]
    resource_retry_count: int
    error: str | None


def _scenarios() -> tuple[Scenario, ...]:
    rooms = ((10.0, 10.0, "10x10"), (30.0, 50.0, "30x50"))
    scenarios: list[Scenario] = []
    for system, layout, label in (
        ("proposed", None, "proposed"),
        ("conventional", "rolling_bench", "conventional-rolling"),
        ("conventional", "practical", "conventional-practical"),
    ):
        for length, width, room_label in rooms:
            for mode in ("mean_target", "target_capped"):
                selector: dict[str, object] = {
                    "system": system,
                    "room_length_ft": length,
                    "room_width_ft": width,
                    "aisle_mode": False,
                    "target_ppfd": 250.0,
                    "lighting_target_mode": mode,
                }
                if layout is not None:
                    selector["layout_mode"] = layout
                scenarios.append(
                    Scenario(f"{label}-{room_label}-{mode}", selector)
                )
    for length, width, room_label in rooms:
        scenarios.append(
            Scenario(
                f"hps-{room_label}-fixed",
                {
                    "system": "hps",
                    "room_length_ft": length,
                    "room_width_ft": width,
                    "aisle_mode": False,
                },
            )
        )
    return tuple(scenarios)


def _varied_scenario(
    scenarios: Sequence[Scenario], ordinal: int
) -> Scenario:
    source = scenarios[ordinal % len(scenarios)]
    selector = dict(source.selector)
    selector["aisle_mode"] = (ordinal // len(scenarios)) % 2 == 1
    if selector["system"] != "hps":
        selector["target_ppfd"] = 100.0 + (ordinal * 17 % 1800) + ordinal / 1000
    if ordinal % 3 == 1 and selector["room_length_ft"] != selector["room_width_ft"]:
        selector["room_length_ft"], selector["room_width_ft"] = (
            selector["room_width_ft"],
            selector["room_length_ft"],
        )
    return Scenario(source.name, selector)


async def _json_response(
    response: httpx.Response,
) -> dict[str, object]:
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"non-JSON response from {response.request.url.path}"
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError("server returned a non-object JSON response")
    return payload


async def _one_user(
    client: httpx.AsyncClient,
    gate: asyncio.Event,
    scenario: Scenario,
    *,
    index: int,
    token_prefix: str,
    poll_initial_seconds: float,
) -> UserResult:
    await gate.wait()
    started = time.perf_counter()
    token = f"load-{token_prefix}-{index:04d}"
    admission_statuses: list[int] = []
    admission_retries = 0
    request_id: str | None = None
    poll_count = 0
    resource_statuses: list[int] = []
    resource_retry_count = 0
    record: dict[str, object] | None = None
    rng = random.Random(index)
    try:
        while True:
            try:
                response = await client.post(
                    "/api/precomputed/playbacks",
                    json=scenario.selector,
                    headers={"Idempotency-Key": token},
                )
            except httpx.HTTPError:
                admission_retries += 1
                await asyncio.sleep(_retry_delay(1.0, admission_retries, rng))
                continue
            admission_statuses.append(response.status_code)
            if response.status_code in {429, 503}:
                admission_retries += 1
                retry_after = _retry_after_seconds(response)
                await asyncio.sleep(
                    _retry_delay(retry_after, admission_retries, rng)
                )
                continue
            if response.status_code not in {200, 202}:
                body = await _json_response(response)
                raise RuntimeError(
                    str(body.get("error", {}))
                    or f"admission returned {response.status_code}"
                )
            record = await _json_response(response)
            request_id = _request_id(record)
            break

        poll_attempt = 0
        while record["state"] in {"queued", "running"}:
            server_delay = float(record.get("poll_after_ms", 0)) / 1000.0
            base_delay = max(poll_initial_seconds, server_delay)
            await asyncio.sleep(_retry_delay(base_delay, poll_attempt, rng))
            poll_attempt += 1
            try:
                response = await client.get(str(record["status_url"]))
            except httpx.HTTPError:
                continue
            poll_count += 1
            if response.status_code in {429, 503}:
                continue
            if response.status_code != 200:
                raise RuntimeError(
                    f"status endpoint returned {response.status_code}"
                )
            next_record = await _json_response(response)
            if next_record.get("state") != record.get("state"):
                poll_attempt = 0
            record = next_record

        terminal_elapsed_ms = (time.perf_counter() - started) * 1000.0
        state = str(record["state"])
        if state != "completed":
            raise RuntimeError(
                f"terminal state {state}: {record.get('error', {})}"
            )
        result = record.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("completed request omitted its result")
        timing = record.get("timing")
        timing = timing if isinstance(timing, dict) else {}
        for url in _representative_urls(result):
            statuses, transport_retries = await _get_with_retry(
                client, url, rng
            )
            resource_statuses.extend(statuses)
            resource_retry_count += transport_retries + len(statuses) - 1
        return UserResult(
            index=index,
            scenario=scenario.name,
            state=state,
            request_id=request_id,
            admission_statuses=tuple(admission_statuses),
            admission_retries=admission_retries,
            poll_count=poll_count,
            admission_sequence=_optional_int(
                timing.get("admission_sequence")
            ),
            start_sequence=_optional_int(timing.get("start_sequence")),
            queue_wait_ms=_optional_float(timing.get("queue_wait_ms")),
            processing_ms=_optional_float(timing.get("processing_ms")),
            terminal_elapsed_ms=terminal_elapsed_ms,
            flow_elapsed_ms=(time.perf_counter() - started) * 1000.0,
            resource_statuses=tuple(resource_statuses),
            resource_retry_count=resource_retry_count,
            error=None,
        )
    except (httpx.HTTPError, KeyError, RuntimeError, TypeError, ValueError) as exc:
        return UserResult(
            index=index,
            scenario=scenario.name,
            state=str(record.get("state", "error")) if record else "error",
            request_id=request_id,
            admission_statuses=tuple(admission_statuses),
            admission_retries=admission_retries,
            poll_count=poll_count,
            admission_sequence=None,
            start_sequence=None,
            queue_wait_ms=None,
            processing_ms=None,
            terminal_elapsed_ms=(time.perf_counter() - started) * 1000.0,
            flow_elapsed_ms=(time.perf_counter() - started) * 1000.0,
            resource_statuses=tuple(resource_statuses),
            resource_retry_count=resource_retry_count,
            error=f"{type(exc).__name__}: {exc}",
        )


async def _health_monitor(
    client: httpx.AsyncClient,
    stop: asyncio.Event,
    *,
    server_pid: int | None,
) -> dict[str, object]:
    samples: list[float] = []
    errors: list[str] = []
    transport_error_count = 0
    attempt_count = 0
    maximum_queued = 0
    maximum_running = 0
    peak_rss = _rss_bytes(server_pid)
    while not stop.is_set():
        attempt_count += 1
        started = time.perf_counter()
        try:
            response = await client.get("/health/ready")
            samples.append((time.perf_counter() - started) * 1000.0)
            if response.status_code != 200:
                errors.append(f"ready:{response.status_code}")
            else:
                payload = await _json_response(response)
                queue = payload.get("playback_queue", {})
                if isinstance(queue, dict):
                    maximum_queued = max(
                        maximum_queued, int(queue.get("queued", 0))
                    )
                    maximum_running = max(
                        maximum_running, int(queue.get("running", 0))
                    )
        except httpx.HTTPError:
            transport_error_count += 1
        except (RuntimeError, TypeError, ValueError) as exc:
            errors.append(type(exc).__name__)
        rss = _rss_bytes(server_pid)
        if rss is not None:
            peak_rss = max(peak_rss or 0, rss)
        try:
            await asyncio.wait_for(stop.wait(), timeout=0.25)
        except TimeoutError:
            pass
    return {
        "poll_count": attempt_count,
        "transient_transport_error_count": transport_error_count,
        "unexpected_count": len(errors),
        "unexpected": errors[:20],
        "latency_ms": _latency_summary(samples),
        "maximum_queued_observed": maximum_queued,
        "maximum_running_observed": maximum_running,
        "peak_rss_bytes": peak_rss,
    }


async def _queue_level(
    client: httpx.AsyncClient,
    scenarios: Sequence[Scenario],
    *,
    level: int,
    ordinal_offset: int,
    token_prefix: str,
    poll_initial_seconds: float,
    level_timeout_seconds: float,
    server_pid: int | None,
) -> dict[str, object]:
    gate = asyncio.Event()
    stop_monitor = asyncio.Event()
    chosen = [
        _varied_scenario(scenarios, ordinal_offset + index)
        for index in range(level)
    ]
    tasks = [
        asyncio.create_task(
            _one_user(
                client,
                gate,
                scenario,
                index=index,
                token_prefix=f"{token_prefix}-{level}",
                poll_initial_seconds=poll_initial_seconds,
            )
        )
        for index, scenario in enumerate(chosen)
    ]
    monitor = asyncio.create_task(
        _health_monitor(client, stop_monitor, server_pid=server_pid)
    )
    cpu_before = _cpu_seconds(server_pid)
    rss_before = _rss_bytes(server_pid)
    started = time.perf_counter()
    await asyncio.sleep(0)
    gate.set()
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*tasks), timeout=level_timeout_seconds
        )
    finally:
        stop_monitor.set()
    health = await monitor
    elapsed = time.perf_counter() - started
    cpu_after = _cpu_seconds(server_pid)
    rss_after = _rss_bytes(server_pid)
    final_health = await client.get("/health/ready")
    final_payload = await _json_response(final_health)
    final_queue = final_payload.get("playback_queue", {})
    queue_waits = [
        item.queue_wait_ms
        for item in results
        if item.queue_wait_ms is not None
    ]
    processing = [
        item.processing_ms
        for item in results
        if item.processing_ms is not None
    ]
    flow_latencies = [item.flow_elapsed_ms for item in results]
    terminal_latencies = [item.terminal_elapsed_ms for item in results]
    started_records = sorted(
        (
            item
            for item in results
            if item.start_sequence is not None
            and item.admission_sequence is not None
        ),
        key=lambda item: int(item.start_sequence),
    )
    started_order = [item.index for item in started_records]
    admission_order_at_start = [
        int(item.admission_sequence)
        for item in started_records
    ]
    fifo_inversions = _inversion_count(admission_order_at_start)
    errors = [
        {
            "index": item.index,
            "scenario": item.scenario,
            "state": item.state,
            "error": item.error,
            "resource_statuses": list(item.resource_statuses),
        }
        for item in results
        if item.error is not None or item.state != "completed"
    ]
    admission_statuses = _integer_counts(
        status for item in results for status in item.admission_statuses
    )
    resource_statuses = _integer_counts(
        status for item in results for status in item.resource_statuses
    )
    return {
        "synchronized_users": level,
        "distinct_selector_count": len(
            {json.dumps(item.selector, sort_keys=True) for item in chosen}
        ),
        "eventually_completed_count": sum(
            item.state == "completed" for item in results
        ),
        "all_eventually_completed": all(
            item.state == "completed" and item.error is None
            for item in results
        ),
        "queue_drain_seconds": max(terminal_latencies, default=0.0) / 1000.0,
        "full_flow_elapsed_seconds": elapsed,
        "playback_completion_throughput_per_second": (
            level / (max(terminal_latencies, default=0.0) / 1000.0)
            if terminal_latencies and max(terminal_latencies) > 0.0
            else 0.0
        ),
        "full_user_flow_throughput_per_second": (
            level / elapsed if elapsed > 0.0 else 0.0
        ),
        "queue_wait_ms": _latency_summary(queue_waits),
        "processing_ms": _latency_summary(processing),
        "terminal_latency_ms": _latency_summary(terminal_latencies),
        "full_flow_latency_ms": _latency_summary(flow_latencies),
        "status_poll_count": sum(item.poll_count for item in results),
        "admission_retry_count": sum(
            item.admission_retries for item in results
        ),
        "admission_statuses": admission_statuses,
        "representative_resource_statuses": resource_statuses,
        "representative_resource_request_count": sum(
            len(item.resource_statuses) for item in results
        ),
        "representative_resource_retry_count": sum(
            item.resource_retry_count for item in results
        ),
        "fifo": {
            "start_order_inversion_count": fifo_inversions,
            "client_launch_order_preserved": fifo_inversions == 0,
            "first_started_indexes": started_order[:20],
            "last_started_indexes": started_order[-20:],
            "first_started_admission_sequences": admission_order_at_start[:20],
            "last_started_admission_sequences": admission_order_at_start[-20:],
            "note": (
                "FIFO compares server admission and worker-start sequence numbers."
            ),
        },
        "health": health,
        "final_health_status": final_health.status_code,
        "final_queue": final_queue,
        "server_cpu_seconds": (
            None
            if cpu_before is None or cpu_after is None
            else max(0.0, cpu_after - cpu_before)
        ),
        "server_cpu_one_core_percent": (
            None
            if cpu_before is None or cpu_after is None or elapsed == 0.0
            else max(0.0, cpu_after - cpu_before) / elapsed * 100.0
        ),
        "rss_before_bytes": rss_before,
        "rss_after_bytes": rss_after,
        "rss_peak_bytes": health["peak_rss_bytes"],
        "unexpected_count": len(errors),
        "unexpected": errors[:50],
    }


def _representative_urls(result: dict[str, object]) -> tuple[str, ...]:
    playback_id = str(result["run_id"])
    return (
        str(result["metrics_url"]),
        str(result["visualization_metadata_url"]),
        str(result["ppfd_heatmap_url"]),
        str(result["plant_layout_viewer_url"]),
        str(result["ppfd_scatter_viewer_url"]),
        f"/precomputed/{playback_id}/viewer/scene.v1.json",
    )


async def _get_with_retry(
    client: httpx.AsyncClient,
    url: str,
    rng: random.Random,
) -> tuple[list[int], int]:
    statuses: list[int] = []
    transport_retries = 0
    attempt = 0
    while True:
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            transport_retries += 1
            await asyncio.sleep(_retry_delay(1.0, attempt, rng))
            attempt += 1
            continue
        statuses.append(response.status_code)
        if response.status_code in {429, 503}:
            await asyncio.sleep(
                _retry_delay(_retry_after_seconds(response), attempt, rng)
            )
            attempt += 1
            continue
        if response.status_code != 200:
            raise RuntimeError(
                f"representative endpoint returned {response.status_code}"
            )
        return statuses, transport_retries


def _request_id(record: dict[str, object]) -> str:
    request_id = record.get("request_id")
    if not isinstance(request_id, str) or len(request_id) != 32:
        raise RuntimeError("queue response omitted a valid request ID")
    if record.get("state") not in {
        "queued", "running", "completed", "failed", "expired"
    }:
        raise RuntimeError("queue response returned an invalid state")
    if not isinstance(record.get("status_url"), str):
        raise RuntimeError("queue response omitted its status URL")
    return request_id


def _retry_after_seconds(response: httpx.Response) -> float:
    try:
        return max(0.25, float(response.headers.get("Retry-After", "1")))
    except ValueError:
        return 1.0


def _retry_delay(base: float, attempt: int, rng: random.Random) -> float:
    maximum = min(8.0, base * (1.45 ** min(attempt, 8)))
    return maximum * (0.8 + rng.random() * 0.4)


def _optional_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    return None


def _latency_summary(values: Sequence[float]) -> dict[str, float]:
    ordered = sorted(values)
    if not ordered:
        return {key: 0.0 for key in ("p50", "p95", "p99", "maximum")}

    def percentile(fraction: float) -> float:
        rank = max(0, math.ceil(fraction * len(ordered)) - 1)
        return ordered[rank]

    return {
        "p50": percentile(0.50),
        "p95": percentile(0.95),
        "p99": percentile(0.99),
        "maximum": ordered[-1],
    }


def _integer_counts(values) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        key = str(value)
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def _inversion_count(values: Sequence[int]) -> int:
    return sum(
        left > right
        for index, left in enumerate(values)
        for right in values[index + 1 :]
    )


def _rss_bytes(pid: int | None) -> int | None:
    if pid is None:
        return None
    try:
        lines = Path(f"/proc/{pid}/status").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    return None


def _cpu_seconds(pid: int | None) -> float | None:
    if pid is None:
        return None
    try:
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(
            ")", 1
        )[1].split()
        ticks = int(fields[11]) + int(fields[12])
        return ticks / float(os.sysconf("SC_CLK_TCK"))
    except (IndexError, OSError, ValueError):
        return None


def _local_target(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return False
    if parsed.hostname.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        return False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8895")
    parser.add_argument(
        "--allow-non-local",
        action="store_true",
        help="Explicitly permit a non-loopback target. Never implied.",
    )
    parser.add_argument(
        "--queue-levels",
        default="25,50,75,100",
        help="Comma-separated synchronized user levels (maximum 128).",
    )
    parser.add_argument("--server-pid", type=int)
    parser.add_argument("--settle-seconds", type=float, default=5.0)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--level-timeout-seconds", type=float, default=900.0)
    parser.add_argument("--poll-initial-seconds", type=float, default=1.5)
    return parser


async def _run(arguments: argparse.Namespace) -> dict[str, object]:
    scenarios = _scenarios()
    levels = tuple(int(value) for value in arguments.queue_levels.split(","))
    if not levels or any(value <= 0 or value > 128 for value in levels):
        raise ValueError("queue levels must be integers between 1 and 128")
    if not 1.0 <= arguments.poll_initial_seconds <= 2.0:
        raise ValueError("initial poll interval must be between 1 and 2 seconds")
    if not 0.0 <= arguments.settle_seconds <= 60.0:
        raise ValueError("settle seconds must be between 0 and 60")
    limits = httpx.Limits(
        max_connections=max(32, max(levels) * 2),
        max_keepalive_connections=max(32, max(levels) * 2),
    )
    timeout = httpx.Timeout(arguments.timeout_seconds)
    report: dict[str, object] = {
        "target": arguments.base_url,
        "local_target": _local_target(arguments.base_url),
        "queue_levels": list(levels),
        "scenario_count": len(scenarios),
        "representative_endpoints_per_user": 6,
        "poll_initial_seconds": arguments.poll_initial_seconds,
        "cpu_limit_note": (
            "Local measurements are not an exact Render CPU or memory guarantee."
        ),
        "levels": [],
    }
    token_prefix = f"{time.time_ns():x}"
    benchmark_started = time.perf_counter()
    async with httpx.AsyncClient(
        base_url=arguments.base_url.rstrip("/"),
        timeout=timeout,
        limits=limits,
    ) as client:
        health = await client.get("/health/ready")
        if health.status_code != 200:
            raise RuntimeError("local server readiness check failed")
        readiness = await _json_response(health)
        report["initial_readiness"] = readiness
        invalid = await client.post(
            "/api/precomputed/playbacks",
            json={
                "system": "hps",
                "room_length_ft": 10.0,
                "room_width_ft": 10.0,
                "aisle_mode": False,
                "target_ppfd": 250.0,
            },
        )
        report["expected_validation"] = {
            "status": invalid.status_code,
            "matched": invalid.status_code == 422,
        }
        ordinal_offset = 0
        for level in levels:
            print(
                f"running synchronized queue level {level}",
                file=sys.stderr,
                flush=True,
            )
            level_report = await _queue_level(
                client,
                scenarios,
                level=level,
                ordinal_offset=ordinal_offset,
                token_prefix=token_prefix,
                poll_initial_seconds=arguments.poll_initial_seconds,
                level_timeout_seconds=arguments.level_timeout_seconds,
                server_pid=arguments.server_pid,
            )
            report["levels"].append(level_report)  # type: ignore[union-attr]
            ordinal_offset += level
    report["benchmark_elapsed_seconds"] = (
        time.perf_counter() - benchmark_started
    )
    if arguments.settle_seconds:
        await asyncio.sleep(arguments.settle_seconds)
    report["rss_settled_bytes"] = _rss_bytes(arguments.server_pid)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    arguments = parser.parse_args(argv)
    if not _local_target(arguments.base_url) and not arguments.allow_non_local:
        parser.error(
            "refusing a non-local target; pass --allow-non-local explicitly"
        )
    try:
        report = asyncio.run(_run(arguments))
    except (RuntimeError, ValueError, httpx.HTTPError, TimeoutError) as exc:
        print(f"load test failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, indent=2, allow_nan=False))
    failures = int(not report["expected_validation"]["matched"])  # type: ignore[index]
    failures += sum(
        int(level["unexpected_count"])
        + int(not level["all_eventually_completed"])
        + int(level["health"]["unexpected_count"])  # type: ignore[index]
        for level in report["levels"]  # type: ignore[union-attr]
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
