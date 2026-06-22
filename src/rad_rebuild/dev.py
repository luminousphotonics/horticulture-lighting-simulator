from __future__ import annotations

import argparse
import http.client
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from types import FrameType
from typing import Protocol, TextIO

from rad_rebuild.radiance.config import (
    DEFAULT_BACKEND_HOST,
    DEFAULT_BACKEND_PORT,
    DEFAULT_WEB_PORT,
    ENV_RADIANCE_ENABLE_LIVE_EXECUTION,
    ENV_RADIANCE_HOST,
    ENV_RADIANCE_PORT,
    ENV_RESEARCH_API_BASE,
    ENV_RESEARCH_BACKEND_HOST,
    ENV_RESEARCH_BACKEND_PORT,
    ENV_RESEARCH_WEB_PORT,
)


ENV_SHOW_LIVE_MODES = "RAD_REBUILD_SHOW_LIVE_MODES"
BACKEND_READY_TIMEOUT_S = 30.0
READY_POLL_INTERVAL_S = 0.25
SHUTDOWN_TIMEOUT_S = 10.0


class DevSupervisorError(RuntimeError):
    """Raised when the development supervisor cannot start cleanly."""


class ProcessLike(Protocol):
    returncode: int | None

    def poll(self) -> int | None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def send_signal(self, sig: int) -> None: ...


PopenFactory = Callable[..., ProcessLike]
ReadyChecker = Callable[[str, float, float, ProcessLike], None]
SleepFunc = Callable[[float], None]
SignalHandler = Callable[[int, FrameType | None], object] | int | None
SignalSetter = Callable[[signal.Signals, SignalHandler], SignalHandler]


@dataclass(frozen=True)
class DevServerConfig:
    backend_host: str = DEFAULT_BACKEND_HOST
    backend_port: int = DEFAULT_BACKEND_PORT
    web_port: int = DEFAULT_WEB_PORT
    live: bool = False
    ready_timeout_s: float = BACKEND_READY_TIMEOUT_S
    ready_poll_interval_s: float = READY_POLL_INTERVAL_S
    shutdown_timeout_s: float = SHUTDOWN_TIMEOUT_S

    @property
    def backend_base_url(self) -> str:
        return f"http://{self.backend_host}:{self.backend_port}"


def _positive_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rad-rebuild-dev",
        description="Start the local Radiance backend and Flask web app for development.",
    )
    parser.add_argument(
        "--backend-port",
        type=_positive_port,
        default=DEFAULT_BACKEND_PORT,
        help=f"FastAPI backend port. Defaults to {DEFAULT_BACKEND_PORT}.",
    )
    parser.add_argument(
        "--web-port",
        type=_positive_port,
        default=DEFAULT_WEB_PORT,
        help=f"Flask web port. Defaults to {DEFAULT_WEB_PORT}.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Expose live execution modes and enable backend live execution.",
    )
    parser.add_argument(
        "--ready-timeout",
        type=float,
        default=BACKEND_READY_TIMEOUT_S,
        help=f"Seconds to wait for backend /ready. Defaults to {BACKEND_READY_TIMEOUT_S:g}.",
    )
    return parser


def parse_args(argv: Sequence[str] | None = None) -> DevServerConfig:
    args = _build_parser().parse_args(argv)
    if args.ready_timeout <= 0:
        raise SystemExit("--ready-timeout must be greater than zero")
    return DevServerConfig(
        backend_port=args.backend_port,
        web_port=args.web_port,
        live=bool(args.live),
        ready_timeout_s=float(args.ready_timeout),
    )


def build_child_env(
    base_env: Mapping[str, str],
    config: DevServerConfig,
) -> dict[str, str]:
    env = dict(base_env)
    env[ENV_RADIANCE_HOST] = config.backend_host
    env[ENV_RADIANCE_PORT] = str(config.backend_port)
    env[ENV_RESEARCH_BACKEND_HOST] = config.backend_host
    env[ENV_RESEARCH_BACKEND_PORT] = str(config.backend_port)
    env[ENV_RESEARCH_API_BASE] = config.backend_base_url
    env[ENV_RESEARCH_WEB_PORT] = str(config.web_port)
    env[ENV_SHOW_LIVE_MODES] = "1" if config.live else "0"
    env[ENV_RADIANCE_ENABLE_LIVE_EXECUTION] = "1" if config.live else "0"
    env["FLASK_DEBUG"] = "0"
    env["PYTHONUNBUFFERED"] = env.get("PYTHONUNBUFFERED", "1")
    env.pop("WERKZEUG_RUN_MAIN", None)
    return env


def _start_child(
    args: list[str],
    env: Mapping[str, str],
    popen_factory: PopenFactory,
) -> ProcessLike:
    return popen_factory(args, env=dict(env))


def wait_for_backend_ready(
    backend_base_url: str,
    timeout_s: float,
    poll_interval_s: float,
    backend_process: ProcessLike,
) -> None:
    ready_url = f"{backend_base_url.rstrip('/')}/ready"
    deadline = time.monotonic() + timeout_s
    last_error = "backend did not report ready"
    while time.monotonic() < deadline:
        returncode = backend_process.poll()
        if returncode is not None:
            raise DevSupervisorError(
                f"Radiance backend exited before readiness with code {returncode}."
            )
        try:
            with urllib.request.urlopen(ready_url, timeout=min(1.0, poll_interval_s)):
                return
        except (
            http.client.HTTPException,
            OSError,
            TimeoutError,
            urllib.error.URLError,
        ) as exc:
            last_error = str(exc) or last_error
        time.sleep(poll_interval_s)
    raise DevSupervisorError(
        f"Timed out waiting for Radiance backend readiness at {ready_url}: {last_error}"
    )


def _running(process: ProcessLike) -> bool:
    return process.poll() is None


def _forward_signal(processes: Sequence[ProcessLike], signum: int) -> None:
    for process in processes:
        if _running(process):
            process.send_signal(signum)


def _terminate_processes(processes: Sequence[ProcessLike], timeout_s: float) -> None:
    for process in processes:
        if _running(process):
            process.terminate()
    for process in processes:
        if not _running(process):
            continue
        try:
            process.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=timeout_s)


def _monitor_processes(
    processes: Sequence[ProcessLike],
    *,
    sleep: SleepFunc,
    poll_interval_s: float,
    received_signal: Callable[[], int | None],
) -> int:
    while True:
        signum = received_signal()
        if signum is not None:
            return 128 + signum
        for process in processes:
            returncode = process.poll()
            if returncode is not None:
                return int(returncode)
        sleep(poll_interval_s)


def run_supervisor(
    config: DevServerConfig,
    *,
    base_env: Mapping[str, str] | None = None,
    popen_factory: PopenFactory = subprocess.Popen,
    ready_checker: ReadyChecker = wait_for_backend_ready,
    sleep: SleepFunc = time.sleep,
    signal_setter: SignalSetter = signal.signal,
    stderr: TextIO = sys.stderr,
) -> int:
    env = build_child_env(os.environ if base_env is None else base_env, config)
    processes: list[ProcessLike] = []
    received: MutableMapping[str, int | None] = {"signum": None}
    previous_handlers: dict[signal.Signals, SignalHandler] = {}

    def handle_signal(signum: int, _frame: FrameType | None) -> None:
        received["signum"] = signum
        _forward_signal(processes, signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal_setter(signum, handle_signal)

    try:
        backend = _start_child(
            [sys.executable, "-m", "rad_rebuild.radiance.backend.server"],
            env,
            popen_factory,
        )
        processes.append(backend)
        try:
            ready_checker(
                config.backend_base_url,
                config.ready_timeout_s,
                config.ready_poll_interval_s,
                backend,
            )
        except DevSupervisorError as exc:
            print(str(exc), file=stderr)
            return 1

        web = _start_child(
            [sys.executable, "-m", "rad_rebuild.web.app"],
            env,
            popen_factory,
        )
        processes.append(web)
        return _monitor_processes(
            processes,
            sleep=sleep,
            poll_interval_s=config.ready_poll_interval_s,
            received_signal=lambda: received["signum"],
        )
    finally:
        _terminate_processes(processes, config.shutdown_timeout_s)
        for signum, previous_handler in previous_handlers.items():
            signal_setter(signum, previous_handler)


def main(argv: Sequence[str] | None = None) -> int:
    config = parse_args(argv)
    return run_supervisor(config)


if __name__ == "__main__":
    raise SystemExit(main())
