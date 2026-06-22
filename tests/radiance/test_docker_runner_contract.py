from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from rad_rebuild.radiance.backend import runner


class DockerRunnerContractTests(unittest.TestCase):
    @unittest.skipIf(os.name == "nt", "POSIX UID/GID mapping is not used on Windows")
    def test_docker_command_runs_as_host_user_by_default(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(runner, "_resolve_docker", return_value="/usr/bin/docker"),
        ):
            cmd = runner._docker_command("true", {})
        self.assertIn("--user", cmd)
        self.assertEqual(cmd[cmd.index("--user") + 1], f"{os.getuid()}:{os.getgid()}")

    def test_docker_user_env_override_is_honored(self) -> None:
        with patch.dict(os.environ, {"RADIANCE_DOCKER_USER": "123:456"}, clear=True):
            self.assertEqual(runner._docker_user_args(), ["--user", "123:456"])

    def test_ensure_image_rebuilds_existing_image_when_runtime_deps_are_missing(self) -> None:
        calls: list[list[str]] = []

        def fake_run_docker(cmd, *args, **kwargs):
            del args, kwargs
            calls.append(list(cmd))
            return ""

        with (
            patch.object(runner, "_resolve_docker", return_value="/usr/bin/docker"),
            patch.object(runner, "_run_docker", side_effect=fake_run_docker),
            patch.object(runner, "_image_runtime_ready", return_value=False),
            patch.object(runner, "_load_image_from_tar", return_value=False),
        ):
            runner.ensure_image()

        self.assertIn(["/usr/bin/docker", "image", "inspect", runner.IMAGE_NAME], calls)
        self.assertTrue(any(call[:2] == ["/usr/bin/docker", "build"] for call in calls))

    def test_ensure_image_keeps_existing_image_when_runtime_deps_are_ready(self) -> None:
        calls: list[list[str]] = []

        def fake_run_docker(cmd, *args, **kwargs):
            del args, kwargs
            calls.append(list(cmd))
            return ""

        with (
            patch.object(runner, "_resolve_docker", return_value="/usr/bin/docker"),
            patch.object(runner, "_run_docker", side_effect=fake_run_docker),
            patch.object(runner, "_image_runtime_ready", return_value=True),
        ):
            runner.ensure_image()

        self.assertEqual(calls, [["/usr/bin/docker", "image", "inspect", runner.IMAGE_NAME]])


if __name__ == "__main__":
    unittest.main()
