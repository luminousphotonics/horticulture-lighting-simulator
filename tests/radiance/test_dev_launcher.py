from __future__ import annotations

import signal
import unittest
from collections.abc import Mapping
from io import StringIO
from typing import Any

from rad_rebuild import dev


class _FakeProcess:
    def __init__(self, poll_values: list[int | None] | None = None) -> None:
        self.poll_values = list(poll_values or [None])
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False
        self.waited = False
        self.signals: list[int] = []

    def poll(self) -> int | None:
        if self.returncode is not None:
            return self.returncode
        if len(self.poll_values) > 1:
            value = self.poll_values.pop(0)
        else:
            value = self.poll_values[0]
        if value is not None:
            self.returncode = value
        return value

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def send_signal(self, sig: int) -> None:
        self.signals.append(sig)


class _ProcessFactory:
    def __init__(self, processes: list[_FakeProcess]) -> None:
        self.processes = processes
        self.calls: list[tuple[list[str], dict[str, str]]] = []

    def __call__(self, args: list[str], env: Mapping[str, str]) -> _FakeProcess:
        self.calls.append((args, dict(env)))
        return self.processes[len(self.calls) - 1]


class DevLauncherTests(unittest.TestCase):
    def test_import_has_no_process_side_effects(self) -> None:
        self.assertTrue(callable(dev.main))

    def test_supervisor_propagates_backend_and_web_environment(self) -> None:
        backend = _FakeProcess([None, None])
        web = _FakeProcess([0])
        factory = _ProcessFactory([backend, web])

        result = dev.run_supervisor(
            dev.DevServerConfig(backend_port=8899, web_port=4321),
            base_env={"FLASK_DEBUG": "1", "WERKZEUG_RUN_MAIN": "true"},
            popen_factory=factory,
            ready_checker=lambda *_args: None,
            signal_setter=lambda _sig, _handler: None,
        )

        self.assertEqual(result, 0)
        self.assertEqual(len(factory.calls), 2)
        self.assertEqual(
            factory.calls[0][0],
            [
                dev.sys.executable,
                "-m",
                "rad_rebuild.radiance.backend.server",
            ],
        )
        self.assertEqual(
            factory.calls[1][0],
            [dev.sys.executable, "-m", "rad_rebuild.web.app"],
        )
        for _args, env in factory.calls:
            self.assertEqual(env["RADIANCE_HOST"], "127.0.0.1")
            self.assertEqual(env["RADIANCE_PORT"], "8899")
            self.assertEqual(env["RADIANCE_RESEARCH_BACKEND_HOST"], "127.0.0.1")
            self.assertEqual(env["RADIANCE_RESEARCH_BACKEND_PORT"], "8899")
            self.assertEqual(env["RADIANCE_RESEARCH_API_BASE"], "http://127.0.0.1:8899")
            self.assertEqual(env["RADIANCE_RESEARCH_WEB_PORT"], "4321")
            self.assertEqual(env["RAD_REBUILD_SHOW_LIVE_MODES"], "0")
            self.assertEqual(env["RADIANCE_ENABLE_LIVE_EXECUTION"], "0")
            self.assertEqual(env["FLASK_DEBUG"], "0")
            self.assertNotIn("WERKZEUG_RUN_MAIN", env)
        self.assertTrue(backend.terminated)

    def test_live_flag_enables_live_mode_environment(self) -> None:
        env = dev.build_child_env(
            {},
            dev.DevServerConfig(live=True),
        )

        self.assertEqual(env["RAD_REBUILD_SHOW_LIVE_MODES"], "1")
        self.assertEqual(env["RADIANCE_ENABLE_LIVE_EXECUTION"], "1")

    def test_readiness_failure_stops_backend_without_starting_web(self) -> None:
        backend = _FakeProcess([None])
        factory = _ProcessFactory([backend])
        stderr = StringIO()

        result = dev.run_supervisor(
            dev.DevServerConfig(),
            base_env={},
            popen_factory=factory,
            ready_checker=lambda *_args: (_ for _ in ()).throw(
                dev.DevSupervisorError("not ready")
            ),
            signal_setter=lambda _sig, _handler: None,
            stderr=stderr,
        )

        self.assertEqual(result, 1)
        self.assertEqual(len(factory.calls), 1)
        self.assertTrue(backend.terminated)
        self.assertIn("not ready", stderr.getvalue())

    def test_one_child_failure_stops_the_other_child(self) -> None:
        backend = _FakeProcess([None, None])
        web = _FakeProcess([7])
        factory = _ProcessFactory([backend, web])

        result = dev.run_supervisor(
            dev.DevServerConfig(),
            base_env={},
            popen_factory=factory,
            ready_checker=lambda *_args: None,
            signal_setter=lambda _sig, _handler: None,
        )

        self.assertEqual(result, 7)
        self.assertTrue(backend.terminated)

    def test_signal_cleanup_forwards_signal_and_stops_children(self) -> None:
        backend = _FakeProcess([None])
        web = _FakeProcess([None])
        factory = _ProcessFactory([backend, web])
        handlers: dict[signal.Signals, Any] = {}

        def fake_signal(signum: signal.Signals, handler: Any) -> Any:
            handlers[signum] = handler
            return None

        def interrupting_sleep(_seconds: float) -> None:
            handler = handlers[signal.SIGTERM]
            handler(signal.SIGTERM, None)

        result = dev.run_supervisor(
            dev.DevServerConfig(),
            base_env={},
            popen_factory=factory,
            ready_checker=lambda *_args: None,
            sleep=interrupting_sleep,
            signal_setter=fake_signal,
        )

        self.assertEqual(result, 128 + signal.SIGTERM)
        self.assertEqual(backend.signals, [signal.SIGTERM])
        self.assertEqual(web.signals, [signal.SIGTERM])
        self.assertTrue(backend.terminated)
        self.assertTrue(web.terminated)


if __name__ == "__main__":
    unittest.main()
