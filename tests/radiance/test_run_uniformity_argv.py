from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.paths import REPO_ROOT  # noqa: E402


class RunUniformityArgvTests(unittest.TestCase):
    def test_bash_groups_special_variable_reproduces_prior_assignment_failure(
        self,
    ) -> None:
        result = subprocess.run(
            ["bash", "-c", 'GROUPS="13 11"; printf "%s" "$GROUPS"'],
            cwd=REPO_ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        self.assertNotEqual(result.stdout, "13 11")

    def test_ring_plus_outer_smooth_groups_reach_solver_as_exact_argv(self) -> None:
        script = REPO_ROOT / "scripts" / "radiance" / "run_uniformity.sh"
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_uniformity_argv_") as tmp:
            root = Path(tmp)
            basis_dir = root / "basis"
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
                    "BASIS_PATH": str(basis_dir / "basis_A.npy"),
                    "SMD_VAR_MODE": "outer",
                    "RUN_UNIFORMITY_DUMP_SOLVER_ARGV": "1",
                    "USE_CHEBYSHEV": "1",
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
        smooth_index = argv.index("--smooth-groups")
        self.assertEqual(
            argv[smooth_index : smooth_index + 3], ["--smooth-groups", "13", "11"]
        )
        self.assertIn("--use-chebyshev", argv)
        lambda_s_index = argv.index("--lambda-s")
        self.assertEqual(
            argv[lambda_s_index : lambda_s_index + 3], ["--lambda-s", "0.0", "0.1"]
        )
        lambda_r_index = argv.index("--lambda-r")
        self.assertEqual(
            argv[lambda_r_index : lambda_r_index + 3], ["--lambda-r", "0.0", "0.01"]
        )


if __name__ == "__main__":
    unittest.main()
