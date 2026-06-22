from __future__ import annotations

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.engine.optimization.solve_uniformity import (  # noqa: E402
    UniformitySolverConfig,
    run_uniformity_solver,
    solve_minvar_qp,
    validate_basis_matrix,
)


class Phase11SolverDecompositionTests(unittest.TestCase):
    def test_uniformity_solver_service_returns_typed_result_and_json_payload(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase11_solver_") as tmp:
            root = Path(tmp)
            basis_path = root / "basis_A.npy"
            np.save(basis_path, np.eye(2, dtype=float))
            (root / "basis_manifest.json").write_text(
                json.dumps(
                    {
                        "emitter_env": {"SMD_MODEL": "legacy"},
                        "n_rings": 2,
                        "layout_modules": 2,
                        "room_L_m": 1.0,
                        "room_W_m": 1.0,
                    }
                ),
                encoding="utf-8",
            )
            output_path = root / "ring_powers_optimized.json"

            result = run_uniformity_solver(
                UniformitySolverConfig(
                    solve_method="minvar_qp",
                    basis_path=basis_path,
                    target_ppfd=100.0,
                    w_min=0.0,
                    w_max=200.0,
                    lambda_s=(0.0,),
                    lambda_smooth=0.0,
                    lambda_r=(0.0,),
                    ridge_weights=None,
                    lambda_mean=10.0,
                    smooth_groups=None,
                    use_chebyshev=False,
                    tol_mean=0.005,
                    out_json=output_path,
                    legacy_metrics=True,
                ),
                runtime_env={"SMD_MODEL": "legacy"},
            )

            self.assertEqual(result.n_points, 2)
            self.assertEqual(result.n_variables, 2)
            self.assertEqual(result.output_path, output_path)
            self.assertEqual(result.output_payload["strategy"], "minvar_qp")
            self.assertEqual(
                result.output_payload["ring_powers_W_per_module"], [100.0, 100.0]
            )
            self.assertTrue(output_path.is_file())
            self.assertIn(
                "strategy=minvar_qp",
                "\n".join(event.message for event in result.events),
            )

    def test_basis_matrix_validation_rejects_non_finite_values(self) -> None:
        with self.assertRaisesRegex(SystemExit, "non-finite"):
            validate_basis_matrix(np.array([[1.0, np.nan]], dtype=float))

    def test_minvar_solver_reports_infeasible_target_bounds(self) -> None:
        with self.assertRaisesRegex(SystemExit, "infeasible"):
            solve_minvar_qp(
                np.eye(2, dtype=float),
                target_mu=100.0,
                w_min=0.0,
                w_max=10.0,
                basis_manifest={"emitter_env": {"SMD_MODEL": "legacy"}},
                runtime_env={"SMD_MODEL": "legacy"},
            )

    def test_solver_and_smd_entrypoints_are_thin_wrappers(self) -> None:
        from rad_rebuild.radiance.engine.emitters import generate_emitters_smd
        from rad_rebuild.radiance.engine.optimization import solve_uniformity

        self.assertLessEqual(
            len(inspect.getsource(solve_uniformity.main).splitlines()), 8
        )
        self.assertLessEqual(
            len(inspect.getsource(generate_emitters_smd.main).splitlines()), 3
        )


class Phase11SmdEmitterDecompositionTests(unittest.TestCase):
    def test_smd_emitter_service_returns_result_and_events(self) -> None:
        from rad_rebuild.radiance.engine.emitters import generate_emitters_smd

        with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase11_smd_") as tmp:
            root = Path(tmp)
            root.mkdir(exist_ok=True)
            layout_json = root / "smd_layout.json"
            events: list[str] = []
            config = generate_emitters_smd.SmdEmitterConfig(
                output_dir=root,
                layout_json=layout_json,
            )
            with (
                patch.object(generate_emitters_smd, "OUT_DIR", root),
                patch.object(
                    generate_emitters_smd,
                    "SMD_JSON",
                    layout_json,
                ),
            ):
                result = generate_emitters_smd.run_smd_emitter_generation(
                    config,
                    emit=lambda event: events.append(event.message),
                )

            self.assertTrue(result.emitter_rad.is_file())
            self.assertTrue(result.summary_txt.is_file())
            self.assertTrue(result.layout_json.is_file())
            self.assertGreater(result.module_count, 0)
            self.assertGreater(result.ring_count, 0)
            self.assertGreater(result.total_source_umol_s, 0.0)
            self.assertTrue(any("Wrote" in message for message in events))


if __name__ == "__main__":
    unittest.main()
