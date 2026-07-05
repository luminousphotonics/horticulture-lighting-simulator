from __future__ import annotations

import gzip
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precomputed_playback  # noqa: E402
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    SCHEMA_VERSION,
    bundle_complete,
    bundle_ref,
)
from rad_rebuild.radiance.engine.simulation.precomputed_integrity import (  # noqa: E402
    PrecomputedIntegrityError,
    resolve_bundle_file,
)


def _basis_sha256(basis: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(basis, dtype=np.float64).tobytes()).hexdigest()


def _minimal_direct_layout() -> dict[str, object]:
    return {
        "units": "meters",
        "z": 0.4572,
        "fixtures": [
            {
                "cx": 0.0,
                "cy": 0.0,
                "body_corners": [
                    [-0.5, -0.25],
                    [0.5, -0.25],
                    [0.5, 0.25],
                    [-0.5, 0.25],
                ],
            }
        ],
    }


class PrecomputedIntegrityTests(unittest.TestCase):
    def test_resolver_rejects_traversal_and_absolute_paths(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_paths_") as tmp:
            root = Path(tmp)
            (root / "inside.txt").write_text("ok", encoding="utf-8")
            for relpath in ("../outside.txt", "/tmp/outside.txt", "C:\\tmp\\x.txt"):
                with self.subTest(relpath=relpath):
                    with self.assertRaises(PrecomputedIntegrityError):
                        resolve_bundle_file(root, relpath)

    def test_resolver_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_symlink_") as tmp:
            root = Path(tmp) / "bundle"
            outside = Path(tmp) / "outside"
            root.mkdir()
            outside.write_text("escape", encoding="utf-8")
            try:
                os.symlink(outside, root / "escape.txt")
            except (AttributeError, NotImplementedError, OSError) as exc:
                self.skipTest(f"symlinks are unavailable: {exc}")

            with self.assertRaises(PrecomputedIntegrityError):
                resolve_bundle_file(root, "escape.txt")

    def test_resolver_accepts_nested_paths_with_spaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_nested_") as tmp:
            root = Path(tmp)
            nested = root / "nested path" / "basis files"
            nested.mkdir(parents=True)
            expected = nested / "basis A.npy"
            expected.write_bytes(b"ok")

            self.assertEqual(
                resolve_bundle_file(root, "nested path/basis files/basis A.npy"),
                expected.resolve(),
            )

    def test_resolver_rejects_directory_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_dir_") as tmp:
            root = Path(tmp)
            (root / "artifact_dir").mkdir()

            with self.assertRaises(PrecomputedIntegrityError):
                resolve_bundle_file(root, "artifact_dir")

    def test_bundle_complete_rejects_basis_mutation_and_reordering(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_hash_") as tmp:
            root = Path(tmp)
            req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10,
                width_ft=10,
            )
            ref = bundle_ref(
                precomputed_playback.ROOT,
                req.mode,
                req.length_ft,
                req.width_ft,
                root,
                req=req,
            )
            assert ref is not None
            ref.path.mkdir(parents=True)
            basis = np.array([[1.0, 2.0], [3.0, 4.0]], dtype=float)
            np.save(ref.path / "basis_A.npy", basis)
            (ref.path / "basis_manifest.json").write_text(
                json.dumps({"matrix_sha256": _basis_sha256(basis)}),
                encoding="utf-8",
            )
            (ref.path / "smd_layout.json").write_text(
                json.dumps({"positions": [{"ring": 0}, {"ring": 1}]}),
                encoding="utf-8",
            )
            ref.manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "mode": MODE_SMD,
                        "artifacts": {
                            "basis_A_npy": "basis_A.npy",
                            "basis_manifest_json": "basis_manifest.json",
                            "layout_json": "smd_layout.json",
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertTrue(bundle_complete(ref))
            np.save(ref.path / "basis_A.npy", basis.copy() * 2.0)
            self.assertFalse(bundle_complete(ref))
            np.save(ref.path / "basis_A.npy", basis[:, ::-1])
            self.assertFalse(bundle_complete(ref))

    def test_bundle_complete_rejects_present_invalid_optional_artifact(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_optional_") as tmp:
            root = Path(tmp)
            req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10,
                width_ft=10,
            )
            ref = bundle_ref(
                precomputed_playback.ROOT,
                req.mode,
                req.length_ft,
                req.width_ft,
                root,
                req=req,
            )
            assert ref is not None
            ref.path.mkdir(parents=True)
            basis = np.ones((1, 1), dtype=float)
            np.save(ref.path / "basis_A.npy", basis)
            (ref.path / "basis_manifest.json").write_text(
                json.dumps({"matrix_sha256": _basis_sha256(basis)}),
                encoding="utf-8",
            )
            (ref.path / "smd_layout.json").write_text("{}", encoding="utf-8")
            ref.manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "mode": MODE_SMD,
                        "artifacts": {
                            "basis_A_npy": "basis_A.npy",
                            "basis_manifest_json": "basis_manifest.json",
                            "layout_json": "smd_layout.json",
                            "basis_build_log_json": "../basis_build_log.json",
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(bundle_complete(ref))

    def test_bundle_complete_rejects_missing_and_malformed_basis_hash(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_bad_hash_") as tmp:
            root = Path(tmp)
            req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10,
                width_ft=10,
            )
            ref = bundle_ref(
                precomputed_playback.ROOT,
                req.mode,
                req.length_ft,
                req.width_ft,
                root,
                req=req,
            )
            assert ref is not None
            ref.path.mkdir(parents=True)
            np.save(ref.path / "basis_A.npy", np.ones((1, 1), dtype=float))
            (ref.path / "smd_layout.json").write_text("{}", encoding="utf-8")
            ref.manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "mode": MODE_SMD,
                        "artifacts": {
                            "basis_A_npy": "basis_A.npy",
                            "layout_json": "smd_layout.json",
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertFalse(bundle_complete(ref))
            (ref.path / "basis_manifest.json").write_text(
                json.dumps({"matrix_sha256": "not-a-sha"}),
                encoding="utf-8",
            )
            manifest = json.loads(ref.manifest_path.read_text(encoding="utf-8"))
            manifest["artifacts"]["basis_manifest_json"] = "basis_manifest.json"
            ref.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            self.assertFalse(bundle_complete(ref))

    def test_invalid_playback_preflight_leaves_no_partial_workspace(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_partial_") as tmp:
            root = Path(tmp)
            dataset_root = root / "dataset"
            workspace_root = root / "workspace"
            req = RadianceRunRequest(
                action="competitor",
                mode=MODE_COMPETITOR,
                length_ft=10,
                width_ft=10,
                competitor_layout="full",
            )
            ref = bundle_ref(
                precomputed_playback.ROOT,
                req.mode,
                req.length_ft,
                req.width_ft,
                dataset_root,
                req=req,
            )
            assert ref is not None
            ref.path.mkdir(parents=True)
            with gzip.open(ref.path / "ppfd_map.txt.gz", "wt", encoding="utf-8") as handle:
                handle.write("0 0 0 100\n")
            ref.manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "mode": MODE_COMPETITOR,
                        "artifacts": {
                            "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                            "layout_json": "../layout.json",
                            "power_json": "power.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            config = precomputed_playback.PrecomputedPlaybackConfig.from_request(
                req, dataset_root=dataset_root, workspace_root=workspace_root
            )

            with self.assertRaises(precomputed_playback.PlaybackError):
                precomputed_playback.run_precomputed_playback(config)

            self.assertFalse(workspace_root.exists())

    def test_playback_can_run_same_bundle_into_sequential_workspaces(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_workspace_") as tmp:
            root = Path(tmp)
            dataset_root = root / "dataset"
            req = RadianceRunRequest(
                action="competitor",
                mode=MODE_COMPETITOR,
                length_ft=10,
                width_ft=10,
                target_ppfd=50,
                competitor_layout="full",
            )
            ref = bundle_ref(
                precomputed_playback.ROOT,
                req.mode,
                req.length_ft,
                req.width_ft,
                dataset_root,
                req=req,
            )
            assert ref is not None
            ref.path.mkdir(parents=True)
            with gzip.open(ref.path / "ppfd_map.txt.gz", "wt", encoding="utf-8") as handle:
                handle.write("0 0 0 100\n")
            (ref.path / "layout.json").write_text(
                json.dumps(_minimal_direct_layout()),
                encoding="utf-8",
            )
            (ref.path / "power.json").write_text(
                json.dumps({"total_ppf": 100.0, "total_w": 50.0}),
                encoding="utf-8",
            )
            ref.manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "mode": MODE_COMPETITOR,
                        "artifacts": {
                            "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                            "layout_json": "layout.json",
                            "power_json": "power.json",
                        },
                        "stats": {"base_peak_ppfd": 100.0, "base_mean_ppfd": 100.0},
                    }
                ),
                encoding="utf-8",
            )

            def fake_room_and_grid(
                _env: dict[str, str], workspace_root: Path
            ) -> None:
                (workspace_root / "runtime_state").mkdir(parents=True, exist_ok=True)

            workspaces = [root / "workspace-a", root / "workspace-b"]
            with patch.object(
                precomputed_playback,
                "_write_room_and_grid",
                side_effect=fake_room_and_grid,
            ):
                for workspace in workspaces:
                    config = precomputed_playback.PrecomputedPlaybackConfig.from_request(
                        req, dataset_root=dataset_root, workspace_root=workspace
                    )
                    result = precomputed_playback.run_precomputed_playback(config)
                    self.assertEqual(result.workspace_root, workspace.resolve())

            for workspace in workspaces:
                self.assertTrue((workspace / "ppfd_map.txt").is_file())
                self.assertTrue(
                    (workspace / "runtime_state" / "spydr3_power.txt").is_file()
                )

    def test_malformed_smd_ring_payload_raises_playback_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_integrity_smd_payload_") as tmp:
            root = Path(tmp)
            workspace_root = root / "workspace"
            layout_path = workspace_root / "runtime_state" / "smd_layout.json"
            layout_path.parent.mkdir(parents=True)
            layout_path.write_text(
                json.dumps({"positions": [{"ring": "0"}]}), encoding="utf-8"
            )

            with self.assertRaises(precomputed_playback.PlaybackError):
                precomputed_playback._write_smd_summary(
                    layout_path=layout_path,
                    workspace_root=workspace_root,
                    basis_meta={"variables": "rings", "ring_indices": [0]},
                    weights=np.array([1.0], dtype=float),
                    solve_details=None,
                    metrics={},
                    target_ppfd=100.0,
                )


if __name__ == "__main__":
    unittest.main()
