from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.executables import (  # noqa: E402
    ExecutableResolutionError,
    resolve_executable,
)
from rad_rebuild.radiance.engine.emitters import generate_emitters_spydr3  # noqa: E402
from rad_rebuild.radiance.engine.simulation import basis_rcontrib  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precompute_sweep  # noqa: E402


def _write_executable(path: Path, body: str) -> None:
    path.write_text(f"#!{sys.executable}\n{body}", encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class ExecutableResolutionTests(unittest.TestCase):
    def test_resolve_explicit_relative_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_exec_explicit_") as tmp:
            root = Path(tmp)
            tool = root / "tool with spaces.py"
            _write_executable(tool, "import sys\nsys.exit(0)\n")

            self.assertEqual(resolve_executable(tool), tool.resolve())
            self.assertEqual(
                resolve_executable(f"./{tool.name}", cwd=root),
                tool.resolve(),
            )

    def test_resolve_bare_name_uses_supplied_path(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_exec_path_") as tmp:
            bindir = Path(tmp) / "bin"
            bindir.mkdir()
            tool = bindir / "tool"
            _write_executable(tool, "import sys\nsys.exit(0)\n")

            self.assertEqual(
                resolve_executable("tool", env={"PATH": str(bindir)}),
                tool.resolve(),
            )

    def test_resolve_rejects_missing_and_non_executable_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_exec_bad_") as tmp:
            root = Path(tmp)
            plain = root / "plain"
            plain.write_text("not executable\n", encoding="utf-8")

            with self.assertRaisesRegex(ExecutableResolutionError, "not found"):
                resolve_executable(root / "missing")
            with self.assertRaisesRegex(ExecutableResolutionError, "not executable"):
                resolve_executable(plain)
            with self.assertRaisesRegex(ExecutableResolutionError, "not executable"):
                resolve_executable(plain.name, env={"PATH": str(root)})

    def test_local_precompute_script_argv_preserves_spaces_and_metacharacters(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_exec_argv_") as tmp:
            root = Path(tmp)
            bindir = root / "bin"
            bindir.mkdir()
            log_path = root / "argv.json"
            fake_bash = bindir / "bash"
            _write_executable(
                fake_bash,
                "import json, os, sys\n"
                "open(os.environ['ARGV_LOG'], 'w', encoding='utf-8').write(json.dumps(sys.argv))\n",
            )
            script = root / "script with spaces;$(touch nope).sh"
            script.write_text("unused by fake bash\n", encoding="utf-8")
            env = {"PATH": str(bindir), "ARGV_LOG": str(log_path)}

            precompute_sweep._run_script(script, env)

            argv = json.loads(log_path.read_text(encoding="utf-8"))
            self.assertEqual(argv[0], str(fake_bash.resolve()))
            self.assertEqual(argv[1:], [str(script.resolve())])
            self.assertFalse((root / "nope").exists())

    def test_docker_precompute_command_uses_bash_and_script_as_separate_argv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_exec_docker_") as tmp:
            root = Path(tmp)
            docker = root / "docker"
            _write_executable(docker, "import sys\nsys.exit(0)\n")
            script = Path("/home/austin/Desktop/rad_rebuild/scripts/radiance/run x.sh")
            with (
                patch(
                    "rad_rebuild.radiance.backend.runner._resolve_docker",
                    return_value=str(docker),
                ),
                patch(
                    "rad_rebuild.radiance.backend.runner._docker_cli_env",
                    return_value={"PATH": str(root)},
                ),
                patch(
                    "rad_rebuild.radiance.backend.runner._docker_platform_args",
                    return_value=[],
                ),
                patch(
                    "rad_rebuild.radiance.backend.runner._docker_user_args",
                    return_value=[],
                ),
            ):
                cmd = precompute_sweep._docker_script_command(script, {})

            self.assertEqual(cmd[0], str(docker.resolve()))
            self.assertNotIn("-lc", cmd)
            self.assertEqual(cmd[-2], "/bin/bash")
            self.assertTrue(cmd[-1].endswith("/scripts/radiance/run x.sh"))

    def test_run_checked_reports_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_exec_nonzero_") as tmp:
            with self.assertRaisesRegex(RuntimeError, "Command failed \\(7\\)"):
                basis_rcontrib._run_checked(
                    [sys.executable, "-c", "import sys; sys.exit(7)"],
                    cwd=Path(tmp),
                    env=os.environ.copy(),
                )

    def test_spydr_ies2rad_retry_uses_same_resolved_executable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_exec_spydr_") as tmp:
            root = Path(tmp)
            out_dir = root / "runtime with spaces"
            out_dir.mkdir()
            ies_path = root / "fixture.ies"
            ies_path.write_text("IESNA\n", encoding="utf-8")
            ies2rad = root / "ies2rad"
            _write_executable(ies2rad, "import sys\nsys.exit(0)\n")
            calls: list[list[str]] = []

            def fake_run(
                cmd: list[str], *, cwd: Path, check: bool
            ) -> subprocess.CompletedProcess[str]:
                self.assertEqual(cwd, out_dir)
                self.assertTrue(check)
                calls.append(cmd)
                if len(calls) == 1:
                    raise subprocess.CalledProcessError(1, cmd)
                (out_dir / "fixture.rad").write_text("# rad\n", encoding="utf-8")
                return subprocess.CompletedProcess(cmd, 0, "", "")

            with (
                patch.object(generate_emitters_spydr3, "OUT_DIR", out_dir),
                patch.object(
                    generate_emitters_spydr3,
                    "resolve_executable",
                    return_value=ies2rad.resolve(),
                ),
                patch.object(
                    generate_emitters_spydr3.subprocess,
                    "run",
                    side_effect=fake_run,
                ),
                patch.object(generate_emitters_spydr3, "_scale_ies_rad") as scale_mock,
            ):
                rad_path = generate_emitters_spydr3._run_ies2rad(
                    ies_path, "fixture", 1.25
                )

            self.assertEqual(rad_path, out_dir / "fixture.rad")
            self.assertEqual(calls[0][0], str(ies2rad.resolve()))
            self.assertEqual(calls[1][0], str(ies2rad.resolve()))
            self.assertIn("-m", calls[0])
            self.assertNotIn("-m", calls[1])
            scale_mock.assert_called_once_with(out_dir / "fixture.rad", 1.25)


if __name__ == "__main__":
    unittest.main()
