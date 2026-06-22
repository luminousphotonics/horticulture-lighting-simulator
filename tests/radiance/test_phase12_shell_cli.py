from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.cli.scripts import (  # noqa: E402
    ScriptEvent,
    _build_uniformity_config,
    build_uniformity_solver_argv,
)
from rad_rebuild.radiance.paths import REPO_ROOT  # noqa: E402


class Phase12ShellCliTests(unittest.TestCase):
    def test_solver_argv_is_built_without_shell_synthesized_flags(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad rebuild phase12 argv ") as tmp:
            root = Path(tmp)
            basis_dir = root / "basis with spaces"
            basis_dir.mkdir()
            env = os.environ.copy()
            env.update(
                {
                    "PY": sys.executable,
                    "RADIANCE_OUTPUT_ROOT": str(root),
                    "RADIANCE_BASIS_OUTPUT_ROOT": str(basis_dir),
                    "BASIS_PATH": str(basis_dir / "basis_A.npy"),
                    "OUT_JSON": str(root / "ring powers.json"),
                    "SMD_VAR_MODE": "outer",
                    "USE_CHEBYSHEV": "1",
                    "LAMBDA_S": "0.0 0.1",
                    "LAMBDA_R": "0.0 0.01",
                }
            )
            config = _build_uniformity_config(env)
            argv = build_uniformity_solver_argv(
                config,
                {"variable_groups": {"rings": 13, "outer_modules": 11}},
            )

        smooth_index = argv.index("--smooth-groups")
        self.assertEqual(
            argv[smooth_index : smooth_index + 3], ["--smooth-groups", "13", "11"]
        )
        self.assertIn("--use-chebyshev", argv)
        self.assertIn(str(basis_dir / "basis_A.npy"), argv)
        self.assertIn(str(root / "ring powers.json"), argv)

    def test_run_uniformity_wrapper_dump_argv_handles_paths_with_spaces(self) -> None:
        script = REPO_ROOT / "scripts" / "radiance" / "run_uniformity.sh"
        with tempfile.TemporaryDirectory(prefix="rad rebuild phase12 wrapper ") as tmp:
            root = Path(tmp)
            basis_dir = root / "basis dir"
            basis_dir.mkdir()
            (basis_dir / "basis_manifest.json").write_text(
                json.dumps(
                    {
                        "variables": "ring_plus_outer_modules",
                        "n_vars": 24,
                        "n_rings": 13,
                        "layout_modules": 137,
                        "basis_backend": "rtrace",
                        "variable_groups": {"rings": 13, "outer_modules": 11},
                    }
                ),
                encoding="utf-8",
            )
            env = os.environ.copy()
            env.update(
                {
                    "PY": sys.executable,
                    "PYTHONPATH": f"{REPO_ROOT / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}",
                    "RADIANCE_OUTPUT_ROOT": str(root),
                    "RADIANCE_BASIS_OUTPUT_ROOT": str(basis_dir),
                    "BASIS_PATH": str(basis_dir / "basis A.npy"),
                    "OUT_JSON": str(root / "ring powers optimized.json"),
                    "SMD_VAR_MODE": "outer",
                    "RUN_UNIFORMITY_DUMP_SOLVER_ARGV": "1",
                    "LAMBDA_S": "0.0 0.1",
                    "LAMBDA_R": "0.0 0.01",
                }
            )
            result = subprocess.run(
                ["bash", str(script)],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            )

        argv = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertIn(str(basis_dir / "basis A.npy"), argv)
        self.assertIn(str(root / "ring powers optimized.json"), argv)
        smooth_index = argv.index("--smooth-groups")
        self.assertEqual(
            argv[smooth_index : smooth_index + 3], ["--smooth-groups", "13", "11"]
        )

    def test_radiance_shell_scripts_are_thin_exec_wrappers(self) -> None:
        expected_commands = {
            "generate_precomputed_bundles.sh": "generate-precomputed-bundles",
            "generate_sensor_grid.sh": "generate-sensor-grid",
            "reproduce.sh": "reproduce",
            "run_basis_extraction.sh": "run-basis-extraction",
            "run_simulation_hps.sh": "run-simulation-hps",
            "run_simulation_smd.sh": "run-simulation-smd",
            "run_simulation_spydr3.sh": "run-simulation-spydr3",
            "run_uniformity.sh": "run-uniformity",
        }
        forbidden_tokens = (
            "build_solver_args",
            "BASIS_MANIFEST",
            "LAMBDA_S=",
            "rtrace ",
            "oconv ",
            "awk ",
            "tee ",
        )
        for script_name, command in expected_commands.items():
            with self.subTest(script=script_name):
                script = REPO_ROOT / "scripts" / "radiance" / script_name
                text = script.read_text(encoding="utf-8")
                self.assertIn("set -euo pipefail", text)
                self.assertIn("exec", text)
                self.assertIn(f"rad_rebuild.radiance.cli.scripts {command}", text)
                for token in forbidden_tokens:
                    self.assertNotIn(token, text)
                self.assertIsNone(
                    re.search(r"^\s*(case|for|while)\b", text, flags=re.MULTILINE)
                )

    def test_structured_event_json_is_stable(self) -> None:
        event = ScriptEvent(
            "error",
            "command.missing",
            "required command not found",
            {"command": "rtrace"},
        )
        self.assertEqual(
            json.loads(event.to_json()),
            {
                "code": "command.missing",
                "details": {"command": "rtrace"},
                "level": "error",
                "message": "required command not found",
            },
        )


if __name__ == "__main__":
    unittest.main()
