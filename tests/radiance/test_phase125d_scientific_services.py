from __future__ import annotations

import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.engine.simulation import basis_rcontrib  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precompute_sweep  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precomputed_playback  # noqa: E402
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    bundle_ref,
    request_params_for_mode,
)
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402


def _basis_sha256(basis: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(basis, dtype=np.float64).tobytes()).hexdigest()


class Phase125DBasisServiceTests(unittest.TestCase):
    def test_basis_service_parser_rejects_non_finite_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-finite"):
            basis_rcontrib.parse_rcontrib_ascii_output(
                "1 1 1 nan nan nan\n",
                sensor_count=1,
                oversample=1,
                modifier_order=["ring_0", "ring_1"],
            )

    def test_basis_service_config_normalizes_threads_and_paths(self) -> None:
        config = basis_rcontrib.RcontribBasisConfig(
            backend="mcpt",
            root=Path("."),
            basis_dir=Path("basis"),
            rad_tmp=Path("tmp"),
            static_room_oct=Path("room.oct"),
            sensor_points=Path("sensors.txt"),
            ring_count=2,
            basis_unit_w=1.0,
            oversample=0,
            nthreads=0,
            log_path=Path("basis-log.json"),
            env={"PATH": "/bin"},
        )

        normalized = config.normalized(cpu_count=8)

        self.assertEqual(normalized.oversample, 1)
        self.assertEqual(normalized.nthreads, 8)
        self.assertEqual(normalized.ring_count, 2)
        self.assertEqual(normalized.env["PATH"], "/bin")


class Phase125DPrecomputeServiceTests(unittest.TestCase):
    def test_precompute_plan_is_typed_and_side_effect_free(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase125d_plan_") as tmp:
            config = precompute_sweep.PrecomputeSweepConfig(
                length_min=10,
                length_max=10,
                width_min=10,
                width_max=10,
                step=1,
                square_only=True,
                modes=(MODE_SMD, MODE_COMPETITOR),
                dataset_root=Path(tmp) / "dataset",
                dry_run=True,
                competitor_layouts=("practical",),
            )

            plan = precompute_sweep.plan_precompute_sweep(config)
            assert config.dataset_root is not None

            self.assertEqual(plan.bundle_count, 2)
            self.assertEqual([item.room for item in plan.items], [(10, 10), (10, 10)])
            self.assertEqual(
                [item.mode for item in plan.items], [MODE_SMD, MODE_COMPETITOR]
            )
            self.assertFalse(config.dataset_root.exists())

    def test_precompute_smd_sanity_rejects_non_finite_basis(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase125d_sane_") as tmp:
            bundle_dir = Path(tmp)
            np.save(
                bundle_dir / "basis_A.npy", np.array([[1.0], [np.nan]], dtype=float)
            )
            (bundle_dir / "basis_manifest.json").write_text(
                json.dumps(
                    {
                        "layout_modules": 1,
                        "basis_unit_w_per_module": 1.0,
                        "matrix_sha256": _basis_sha256(
                            np.array([[1.0], [np.nan]], dtype=float)
                        ),
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "dims_ft": {"length_ft": 10, "width_ft": 10},
                "artifacts": {
                    "basis_A_npy": "basis_A.npy",
                    "basis_manifest_json": "basis_manifest.json",
                },
            }

            self.assertFalse(precompute_sweep.smd_bundle_is_sane(bundle_dir, manifest))


class Phase125DPlaybackServiceTests(unittest.TestCase):
    def test_playback_service_reports_missing_manifest_without_global_mutation(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_phase125d_missing_"
        ) as tmp:
            workspace_root = Path(tmp) / "workspace"
            dataset_root = Path(tmp) / "dataset"
            config = precomputed_playback.PrecomputedPlaybackConfig(
                mode=MODE_SMD,
                length_ft=10.0,
                width_ft=10.0,
                target_ppfd=1000.0,
                dataset_root=dataset_root,
                workspace_root=workspace_root,
            )

            with self.assertRaisesRegex(
                precomputed_playback.PlaybackError, "No precomputed manifest"
            ):
                precomputed_playback.run_precomputed_playback(config)

            self.assertFalse(workspace_root.exists())

    def test_competitor_playback_service_materializes_result(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_phase125d_playback_"
        ) as tmp:
            root = Path(tmp)
            dataset_root = root / "dataset"
            workspace_root = root / "workspace"
            req = RadianceRunRequest(
                action="competitor",
                mode=MODE_COMPETITOR,
                length_ft=10,
                width_ft=10,
                target_ppfd=75,
                competitor_layout="practical",
            )
            ref = bundle_ref(
                precomputed_playback.ROOT,
                req.mode,
                req.length_ft,
                req.width_ft,
                dataset_root,
                req=req,
            )
            self.assertIsNotNone(ref)
            assert ref is not None
            ref.path.mkdir(parents=True)
            with gzip.open(
                ref.path / "ppfd_map.txt.gz", "wt", encoding="utf-8"
            ) as handle:
                handle.write("0 0 0 100\n")
                handle.write("1 0 0 200\n")
            (ref.path / "spydr3_layout.json").write_text(
                '{"fixtures": []}', encoding="utf-8"
            )
            (ref.path / "power.json").write_text(
                json.dumps(
                    {
                        "model_label": "tiny conventional fixture",
                        "ppe_effective": 2.8,
                        "ppe_full": 2.8,
                        "ppe_low": 2.8,
                        "w_full": 800.0,
                        "w_low": 400.0,
                        "droop_k": 0.0,
                        "total_ppf": 2240.0,
                        "total_w": 800.0,
                        "fixture_input_w": 800.0,
                        "layout_mode": "practical",
                        "fixture_count": 1,
                        "droop_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "schema_version": 1,
                "mode": MODE_COMPETITOR,
                "dims_ft": {"length_ft": 10, "width_ft": 10},
                "request_params": request_params_for_mode(req),
                "artifacts": {
                    "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                    "layout_json": "spydr3_layout.json",
                    "power_json": "power.json",
                },
                "stats": {"base_peak_ppfd": 200.0, "base_mean_ppfd": 150.0},
            }
            ref.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            config = precomputed_playback.PrecomputedPlaybackConfig.from_request(
                req,
                dataset_root=dataset_root,
                workspace_root=workspace_root,
            )

            with patch.object(precomputed_playback, "_write_room_and_grid"):
                result = precomputed_playback.run_precomputed_playback(config)

            self.assertEqual(result.mode, MODE_COMPETITOR)
            self.assertEqual(result.bundle_dir, ref.path)
            self.assertEqual(result.workspace_root, workspace_root)
            self.assertTrue((workspace_root / "ppfd_map.txt").is_file())


if __name__ == "__main__":
    unittest.main()
