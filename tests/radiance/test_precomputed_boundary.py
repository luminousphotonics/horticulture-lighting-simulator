from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

import numpy as np

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.engine.simulation import precomputed_playback  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precompute_sweep  # noqa: E402
from rad_rebuild.radiance.backend import env as backend_env  # noqa: E402
from rad_rebuild.radiance.backend import runtime as backend_runtime  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest, request_with_updates  # noqa: E402
from rad_rebuild.radiance.config import MODE_COMPETITOR, MODE_HPS, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.domain import plant_geometry_config_from_request  # noqa: E402
from rad_rebuild.radiance.paths import RADIANCE_DATA_ROOT  # noqa: E402
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (  # noqa: E402
    DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
    DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    NOMINAL_FIXTURE_PPF_UMOL_S as DEFAULT_HPS_FIXTURE_PPF,
    NOMINAL_INPUT_WATTS as DEFAULT_HPS_INPUT_WATTS,
)
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (  # noqa: E402
    PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
    PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE,
    PRECOMPUTED_PLANT_SPACING_M,
    SCHEMA_VERSION,
    build_precomputed_plant_receiver_payload,
    bundle_complete,
    bundle_ref,
    canonical_plant_enabled_precomputed_request,
    canonical_competitor_layout,
    default_precomputed_root,
    inspect_precomputed_plant_receiver_bundle,
    load_precomputed_plant_receiver_npz_compact,
    load_precomputed_plant_receiver_npz,
    load_manifest,
    PRECOMPUTED_ROOT_ENV,
    params_match,
    precomputed_plant_density,
    request_params_for_mode,
    resolve_precomputed_root,
    build_smd_precomputed_plant_receiver_basis_payload,
    validate_mesh_patch_plant_receiver_payload,
    write_precomputed_plant_receiver_npz,
)
from rad_rebuild.radiance.engine.plants import generate_plant_scene  # noqa: E402
from rad_rebuild.radiance.engine.plants.absorption import leaf_absorption_surfaces  # noqa: E402
from rad_rebuild.radiance.engine.plants.surface_flux import (  # noqa: E402
    RADIANCE_RECEIVER_METHOD,
    build_plant_surface_flux_payload,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
GENERATION_SCRIPT = (
    REPO_ROOT / "scripts" / "radiance" / "generate_precomputed_bundles.sh"
)


def _basis_sha256(basis: np.ndarray) -> str:
    return hashlib.sha256(np.asarray(basis, dtype=np.float64).tobytes()).hexdigest()


def _mesh_patch_surface_flux_fixture(*, front: float = 12.0, back: float = 18.0) -> dict[str, object]:
    return {
        "receiver_granularity": "mesh_patch",
        "receiver_generation_basis": "one_mesh_patch_centroid_nearest_leaf_area_centroid",
        "receiver_area_basis": "mesh_patch_area",
        "receiver_side_policy": "front_and_back_per_mesh_surface_row",
        "normal_generation_basis": "mesh_patch_face_normal",
        "receiver_granularity_role": "high_resolution_reference",
        "fspm_spectral_transport_mode": "scalar_source_weighted",
        "plant_count": 1,
        "leaf_count": 1,
        "surface_count": 1,
        "receiver_sample_count": 2,
        "surface_summaries": [
            {
                "surface_id": "plant_r000_c000_leaf_000_face_0000",
                "plant_id": "plant_r000_c000",
                "leaf_id": "plant_r000_c000_leaf_000",
                "leaf_index": 0,
                "face_index": 0,
                "area_m2": 0.01,
                "incident_photon_flux_density_umol_m2_s": front + back,
                "receiver_sample_count": 2,
                "receiver_rows_per_mesh_surface_row": 2,
                "receiver_sides": ["front", "back"],
                "side_summaries": [
                    {
                        "sample_id": "plant_r000_c000_leaf_000_face_0000_front",
                        "side": "front",
                        "incident_photon_flux_density_umol_m2_s": front,
                        "normal": [0.0, 0.0, 1.0],
                    },
                    {
                        "sample_id": "plant_r000_c000_leaf_000_face_0000_back",
                        "side": "back",
                        "incident_photon_flux_density_umol_m2_s": back,
                        "normal": [0.0, 0.0, -1.0],
                    },
                ],
            }
        ],
    }


def _mesh_patch_surface_flux_fixture_for_request(
    req: RadianceRunRequest,
    *,
    front: float = 12.0,
    back: float = 18.0,
) -> dict[str, object]:
    scene = generate_plant_scene(plant_geometry_config_from_request(req))
    surface_summaries: list[dict[str, object]] = []
    for surface in leaf_absorption_surfaces(scene):
        surface_summaries.append(
            {
                "surface_id": surface.surface_id,
                "plant_id": surface.plant_id,
                "leaf_id": surface.leaf_id,
                "leaf_index": surface.leaf_index,
                "face_index": surface.face_index,
                "area_m2": surface.area_m2,
                "incident_photon_flux_density_umol_m2_s": front + back,
                "receiver_sample_count": 2,
                "receiver_rows_per_mesh_surface_row": 2,
                "receiver_sides": ["front", "back"],
                "side_summaries": [
                    {
                        "sample_id": f"{surface.surface_id}_front",
                        "side": "front",
                        "incident_photon_flux_density_umol_m2_s": front,
                        "normal": [0.0, 0.0, 1.0],
                    },
                    {
                        "sample_id": f"{surface.surface_id}_back",
                        "side": "back",
                        "incident_photon_flux_density_umol_m2_s": back,
                        "normal": [0.0, 0.0, -1.0],
                    },
                ],
            }
        )
    return {
        **_mesh_patch_surface_flux_fixture(front=front, back=back),
        "surface_count": len(surface_summaries),
        "receiver_sample_count": len(surface_summaries) * 2,
        "surface_summaries": surface_summaries,
    }


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


def _write_synthetic_mesh_patch_receiver_bundle(
    bundle: Path,
    *,
    include_samples: bool = True,
) -> dict[str, object]:
    receiver_metadata = {
        "receiver_granularity": "mesh_patch",
        "surface_receiver_encoding": "plant_grid_indices_v1",
        "receiver_sample_encoding": "mesh_patch_side_samples_v1" if include_samples else None,
        "surface_count": 2,
        "receiver_sample_count": 4 if include_samples else 0,
    }
    receiver_metadata = {key: value for key, value in receiver_metadata.items() if value is not None}
    receiver_arrays: dict[str, Any] = {
        "metadata_json": np.frombuffer(
            json.dumps(receiver_metadata, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            ),
            dtype=np.uint8,
        ),
        "plant_row": np.array([0, 0], dtype=np.uint16),
        "plant_column": np.array([0, 0], dtype=np.uint16),
        "leaf_index": np.array([0, 1], dtype=np.uint16),
        "face_index": np.array([0, 0], dtype=np.uint16),
        "stored_ppfd_umol_m2_s": np.array([15.0, 25.0], dtype=np.float64),
    }
    if include_samples:
        receiver_arrays.update(
            {
                "sample_id": np.array(
                    [
                        "plant_r000_c000_leaf_000_face_0000_front",
                        "plant_r000_c000_leaf_000_face_0000_back",
                        "plant_r000_c000_leaf_001_face_0000_front",
                        "plant_r000_c000_leaf_001_face_0000_back",
                    ]
                ),
                "sample_surface_id": np.array(
                    [
                        "plant_r000_c000_leaf_000_face_0000",
                        "plant_r000_c000_leaf_000_face_0000",
                        "plant_r000_c000_leaf_001_face_0000",
                        "plant_r000_c000_leaf_001_face_0000",
                    ]
                ),
                "sample_side": np.array(["front", "back", "front", "back"]),
                "sample_leaf_index": np.array([0, 0, 1, 1], dtype=np.uint16),
                "sample_face_index": np.array([0, 0, 0, 0], dtype=np.uint16),
                "sample_stored_ppfd_umol_m2_s": np.array(
                    [10.0, 5.0, 12.0, 13.0],
                    dtype=np.float64,
                ),
            }
        )
    np.savez_compressed(bundle / "plant_receiver.npz", **receiver_arrays)
    basis_rows = 4 if include_samples else 2
    np.savez_compressed(
        bundle / "plant_receiver_basis_A.npz",
        plant_receiver_basis_A=np.ones((basis_rows, 1), dtype=np.float64),
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE_SMD,
        "request_params": {
            "plants_enabled": True,
            "fspm_receiver_granularity": "mesh_patch",
        },
        "artifacts": {
            "plant_receiver_npz": "plant_receiver.npz",
            "plant_receiver_basis_A_npz": "plant_receiver_basis_A.npz",
        },
    }


class PrecomputedBoundaryTests(unittest.TestCase):
    def test_default_precomputed_root_is_repo_data_root_when_env_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(
                default_precomputed_root(), RADIANCE_DATA_ROOT / "precomputed"
            )
            self.assertEqual(
                resolve_precomputed_root(), RADIANCE_DATA_ROOT / "precomputed"
            )

    def test_precomputed_root_env_override_still_wins(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_precomputed_override_"
        ) as tmp:
            with patch.dict(os.environ, {PRECOMPUTED_ROOT_ENV: tmp}):
                self.assertEqual(resolve_precomputed_root(), Path(tmp))

    def test_backend_base_env_includes_resolved_precomputed_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_precomputed_env_") as tmp:
            with patch.dict(os.environ, {PRECOMPUTED_ROOT_ENV: tmp}):
                env = backend_env._base_env()

            self.assertEqual(env[PRECOMPUTED_ROOT_ENV], tmp)

    def test_precomputed_plant_density_uses_40cm_grid(self) -> None:
        self.assertEqual(precomputed_plant_density(10, 10), (8, 8))
        self.assertEqual(precomputed_plant_density(10, 11), (8, 8))
        self.assertEqual(precomputed_plant_density(10, 12), (9, 8))
        self.assertEqual(precomputed_plant_density(12, 10), (9, 8))
        self.assertEqual(precomputed_plant_density(12, 12), (9, 9))
        self.assertEqual(precomputed_plant_density(20, 20), (15, 15))
        self.assertEqual(precomputed_plant_density(30, 30), (23, 23))

    def test_canonical_plant_enabled_precomputed_request_contract(self) -> None:
        req = RadianceRunRequest(
            action="uniformity",
            mode=MODE_SMD,
            length_ft=10.0,
            width_ft=10.0,
            target_ppfd=750.0,
            fspm_target_ppfd_umol_m2_s=275.0,
            fspm_target_tolerance_umol_m2_s=25.0,
        )

        plant_req = canonical_plant_enabled_precomputed_request(req)

        self.assertTrue(plant_req.plants_enabled)
        self.assertEqual(plant_req.plant_rows, 8)
        self.assertEqual(plant_req.plant_columns, 8)
        self.assertEqual(plant_req.plant_spacing_m, PRECOMPUTED_PLANT_SPACING_M)
        self.assertEqual(plant_req.sim_mode, "standard")
        self.assertTrue(plant_req.match_system_ppe)
        self.assertEqual(
            plant_req.fspm_receiver_granularity,
            PRECOMPUTED_FSPM_RECEIVER_GRANULARITY,
        )
        self.assertEqual(
            plant_req.fspm_spectral_transport_mode,
            PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE,
        )
        self.assertEqual(plant_req.plant_seed, req.plant_seed)
        self.assertEqual(plant_req.plant_height_m, req.plant_height_m)
        self.assertEqual(plant_req.plant_canopy_radius_m, req.plant_canopy_radius_m)
        self.assertEqual(plant_req.plant_leaf_count, req.plant_leaf_count)
        self.assertEqual(plant_req.plant_growth_stage, req.plant_growth_stage)

    def test_canonical_plant_request_uses_unique_rectangle_orientation(self) -> None:
        req = RadianceRunRequest(
            action="uniformity",
            mode=MODE_SMD,
            length_ft=10.0,
            width_ft=11.0,
        )

        plant_req = canonical_plant_enabled_precomputed_request(req)

        self.assertEqual((plant_req.length_ft, plant_req.width_ft), (11.0, 10.0))
        self.assertEqual(plant_req.plant_rows, 8)
        self.assertEqual(plant_req.plant_columns, 8)
        self.assertEqual(
            request_params_for_mode(plant_req)["plant_rows"],
            8,
        )
        self.assertEqual(
            request_params_for_mode(plant_req)["plant_columns"],
            8,
        )

    def test_request_params_include_plant_identity_fields_when_enabled(self) -> None:
        req = canonical_plant_enabled_precomputed_request(
            RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10.0,
                width_ft=10.0,
                target_ppfd=800.0,
                fspm_target_ppfd_umol_m2_s=250.0,
                fspm_target_tolerance_umol_m2_s=15.0,
            )
        )

        params = request_params_for_mode(req)

        self.assertEqual(params["plants_enabled"], True)
        self.assertEqual(params["plant_seed"], 1)
        self.assertEqual(params["plant_rows"], 8)
        self.assertEqual(params["plant_columns"], 8)
        self.assertEqual(params["plant_spacing_m"], 0.4)
        self.assertEqual(params["plant_height_m"], 0.16)
        self.assertEqual(params["plant_canopy_radius_m"], 0.18)
        self.assertEqual(params["plant_leaf_count"], 12)
        self.assertEqual(params["plant_growth_stage"], 1.0)
        self.assertEqual(params["match_system_ppe"], True)
        self.assertEqual(params["smd_model"], "legacy")
        self.assertEqual(params["fspm_receiver_granularity"], "mesh_patch")
        self.assertEqual(
            params["fspm_spectral_transport_mode"],
            "scalar_source_weighted",
        )
        self.assertEqual(
            params["fspm_leaf_radiance_material_mode"],
            "rex_source_weighted_trans",
        )
        self.assertNotIn("target_ppfd", params)
        self.assertNotIn("fspm_target_ppfd_umol_m2_s", params)
        self.assertNotIn("fspm_target_tolerance_umol_m2_s", params)

    def test_bundle_identity_excludes_runtime_targets_for_plant_contract(self) -> None:
        base = canonical_plant_enabled_precomputed_request(
            RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10.0,
                width_ft=10.0,
                target_ppfd=700.0,
                fspm_target_ppfd_umol_m2_s=240.0,
                fspm_target_tolerance_umol_m2_s=10.0,
            )
        )
        changed_targets = canonical_plant_enabled_precomputed_request(
            RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10.0,
                width_ft=10.0,
                target_ppfd=1200.0,
                fspm_target_ppfd_umol_m2_s=320.0,
                fspm_target_tolerance_umol_m2_s=35.0,
            )
        )
        changed_geometry = canonical_plant_enabled_precomputed_request(
            RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10.0,
                width_ft=10.0,
                plant_seed=2,
            )
        )
        manifest = {"request_params": request_params_for_mode(base)}

        self.assertTrue(params_match(manifest, request_params_for_mode(changed_targets)))
        self.assertFalse(params_match(manifest, request_params_for_mode(changed_geometry)))

    def test_mesh_patch_precomputed_receiver_npz_preserves_side_samples(self) -> None:
        payload = build_precomputed_plant_receiver_payload(
            _mesh_patch_surface_flux_fixture(),
            value_semantics="smd_receiver_basis",
        )
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_receiver_npz_") as tmp:
            path = write_precomputed_plant_receiver_npz(
                Path(tmp) / "plant_receiver.npz",
                payload,
            )
            loaded = load_precomputed_plant_receiver_npz(path)

        self.assertEqual(len(loaded["surface_receivers"]), 1)
        self.assertEqual(len(loaded["receiver_samples"]), 2)
        self.assertEqual(
            [row["side"] for row in loaded["receiver_samples"]],
            ["front", "back"],
        )
        self.assertEqual(
            [row["stored_ppfd_umol_m2_s"] for row in loaded["receiver_samples"]],
            [12.0, 18.0],
        )

    def test_mesh_patch_validation_rejects_surface_only_receiver_payload(self) -> None:
        payload = build_precomputed_plant_receiver_payload(
            {
                **_mesh_patch_surface_flux_fixture(),
                "surface_summaries": [
                    {
                        "surface_id": "plant_r000_c000_leaf_000_face_0000",
                        "incident_photon_flux_density_umol_m2_s": 30.0,
                    }
                ],
            },
            value_semantics="smd_receiver_basis",
        )

        reasons = validate_mesh_patch_plant_receiver_payload(
            payload,
            basis_row_count=1,
        )

        self.assertIn(
            "mesh_patch plant receiver payload is missing receiver_samples.",
            reasons,
        )

    def test_mesh_patch_precomputed_receiver_basis_uses_side_sample_rows(self) -> None:
        col_0 = build_precomputed_plant_receiver_payload(
            _mesh_patch_surface_flux_fixture(front=1.0, back=2.0),
            value_semantics="smd_receiver_basis",
        )
        col_1 = build_precomputed_plant_receiver_payload(
            _mesh_patch_surface_flux_fixture(front=3.0, back=4.0),
            value_semantics="smd_receiver_basis",
        )

        receiver_payload, basis = build_smd_precomputed_plant_receiver_basis_payload(
            [col_0, col_1],
            basis_metadata={"n_vars": 2},
        )

        self.assertEqual(basis.shape, (2, 2))
        self.assertEqual(basis.tolist(), [[1.0, 3.0], [2.0, 4.0]])
        self.assertEqual(len(receiver_payload["surface_receivers"]), 1)
        self.assertEqual(len(receiver_payload["receiver_samples"]), 2)
        self.assertEqual(
            receiver_payload["basis_metadata"]["plant_receiver_basis_shape"],
            [2, 2],
        )

    def test_mesh_patch_bundle_inspector_reports_side_sample_arrays_and_basis_rows(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_receiver_bundle_") as tmp:
            bundle = Path(tmp)
            manifest = _write_synthetic_mesh_patch_receiver_bundle(bundle)

            with patch(
                "rad_rebuild.radiance.engine.simulation.precomputed_dataset."
                "load_precomputed_plant_receiver_npz",
                side_effect=AssertionError("inspector must not hydrate receiver rows"),
            ):
                result = inspect_precomputed_plant_receiver_bundle(bundle, manifest)

        self.assertTrue(result["ok"], result["reasons"])
        self.assertEqual(result["surface_row_count"], 2)
        self.assertEqual(result["receiver_sample_count"], 4)
        self.assertEqual(result["basis_row_count"], 4)
        self.assertTrue(result["side_sample_arrays_exist"])
        self.assertEqual(result["receiver_sides"], ["back", "front"])

    def test_mesh_patch_bundle_inspector_fails_surface_only_npz(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_receiver_bundle_bad_") as tmp:
            bundle = Path(tmp)
            manifest = _write_synthetic_mesh_patch_receiver_bundle(
                bundle,
                include_samples=False,
            )

            with patch(
                "rad_rebuild.radiance.engine.simulation.precomputed_dataset."
                "load_precomputed_plant_receiver_npz",
                side_effect=AssertionError("inspector must not hydrate receiver rows"),
            ):
                result = inspect_precomputed_plant_receiver_bundle(bundle, manifest)

        self.assertFalse(result["ok"])
        self.assertEqual(result["surface_row_count"], 2)
        self.assertEqual(result["receiver_sample_count"], 0)
        self.assertEqual(result["basis_row_count"], 2)
        self.assertFalse(result["side_sample_arrays_exist"])
        self.assertIn(
            "plant_receiver.npz is missing side-specific sample arrays.",
            result["reasons"],
        )
        self.assertIn(
            "receiver sample count must be 2 * mesh surface row count (4); got 0.",
            result["reasons"],
        )

    def test_precomputed_playback_mesh_patch_samples_materialize_dense_detail(self) -> None:
        req = RadianceRunRequest(
            action="uniformity",
            mode=MODE_SMD,
            length_ft=10.0,
            width_ft=10.0,
            plants_enabled=True,
            plant_rows=1,
            plant_columns=1,
            fspm_receiver_granularity="mesh_patch",
            fspm_spectral_transport_mode="scalar_source_weighted",
        )
        receiver_payload = build_precomputed_plant_receiver_payload(
            _mesh_patch_surface_flux_fixture_for_request(req, front=12.0, back=18.0),
            value_semantics="smd_receiver_basis",
        )
        runtime_values = [12.0, 18.0] * int(receiver_payload["surface_count"])

        scene, rows = precomputed_playback._plant_receiver_surface_flux_rows(
            req,
            receiver_payload,
            runtime_values,
        )
        payload = build_plant_surface_flux_payload(
            scene,
            rows,
            method=RADIANCE_RECEIVER_METHOD,
            source_ppfd_map="ppfd_map.txt",
            receiver_sample_count=len(runtime_values),
            receiver_granularity="mesh_patch",
            receiver_rows_per_mesh_surface_row=2.0,
        )

        self.assertEqual(rows[0]["receiver_sample_count"], 2)
        self.assertEqual(rows[0]["receiver_sides"], ["front", "back"])
        self.assertEqual(len(rows[0]["side_summaries"]), 2)
        detail = payload["visualization"]["raw_leaf_surface_flux_detail"]
        self.assertEqual(detail["receiver_granularity"], "mesh_patch")
        self.assertEqual(detail["visual_granularity"], "leaf_average")

    def test_compact_mesh_patch_npz_arrays_build_leaf_major_dense_detail(self) -> None:
        receiver_payload = build_precomputed_plant_receiver_payload(
            _mesh_patch_surface_flux_fixture(front=12.0, back=18.0),
            value_semantics="smd_receiver_basis",
        )
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_compact_detail_") as tmp:
            receiver_path = write_precomputed_plant_receiver_npz(
                Path(tmp) / "plant_receiver.npz",
                receiver_payload,
            )
            compact = load_precomputed_plant_receiver_npz_compact(receiver_path)

        detail = precomputed_playback._dense_mesh_patch_detail_from_arrays(
            compact,
            [12.0, 18.0],
            leaf_count=1,
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["mode"], "raw_leaf_surface_flux")
        self.assertEqual(detail["visual_granularity"], "mesh_patch")
        self.assertEqual(detail["encoding"], "leaf_major_dense")
        self.assertEqual(detail["leaf_count"], 1)
        self.assertEqual(detail["leaf_ids"], ["plant_r000_c000_leaf_000"])
        self.assertEqual(detail["patches_per_leaf"], 1)
        self.assertEqual(detail["patch_face_indices"], [[0]])
        self.assertEqual(detail["sides"], ["front", "back"])
        self.assertTrue(detail["top_bottom_support"])
        self.assertEqual(detail["values_ppfd"]["front"], [[12.0]])
        self.assertEqual(detail["values_ppfd"]["back"], [[18.0]])

    def test_compact_mesh_patch_dense_detail_uses_leaf_ids_not_local_leaf_index(self) -> None:
        compact = {
            "schema": "rad_rebuild.precomputed.plant_receiver.v1",
            "schema_version": 1,
            "receiver_granularity": "mesh_patch",
            "_sample_arrays": {
                "leaf_index": np.array([0, 0, 0, 0], dtype=np.int32),
                "leaf_id": np.array(["leaf_a", "leaf_a", "leaf_b", "leaf_b"]),
                "face_index": np.array([0, 0, 0, 0], dtype=np.int32),
                "side": np.array(["front", "back", "front", "back"]),
                "stored_ppfd_umol_m2_s": np.array(
                    [10.0, 1.0, 20.0, 2.0],
                    dtype=np.float64,
                ),
            },
        }

        detail = precomputed_playback._dense_mesh_patch_detail_from_arrays(
            compact,
            [10.0, 1.0, 20.0, 2.0],
            leaf_count=2,
            leaf_ids=["leaf_a", "leaf_b"],
        )

        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["leaf_ids"], ["leaf_a", "leaf_b"])
        self.assertEqual(detail["values_ppfd"]["front"], [[10.0], [20.0]])
        self.assertEqual(detail["values_ppfd"]["back"], [[1.0], [2.0]])

    def test_runtime_receiver_artifact_stays_summary_only_for_compact_mesh_patch(
        self,
    ) -> None:
        receiver_payload = build_precomputed_plant_receiver_payload(
            _mesh_patch_surface_flux_fixture(front=12.0, back=18.0),
            value_semantics="smd_receiver_basis",
        )
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_runtime_receiver_") as tmp:
            root = Path(tmp)
            receiver_path = write_precomputed_plant_receiver_npz(
                root / "plant_receiver.npz",
                receiver_payload,
            )
            compact = load_precomputed_plant_receiver_npz_compact(receiver_path)
            precomputed_playback._write_runtime_plant_receiver_payload(
                root,
                compact,
                [12.0, 18.0],
            )
            runtime_payload = json.loads(
                (root / "runtime_state" / "plant_receiver.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertEqual(runtime_payload["runtime_receiver_sample_count"], 2)
        self.assertNotIn("receiver_samples", runtime_payload)
        self.assertNotIn("surface_receivers", runtime_payload)

    def test_precomputed_playback_target_classification_prefers_baseline_map(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_target_map_") as tmp:
            workspace = Path(tmp)

            self.assertIsNone(
                precomputed_playback._target_classification_map_for_playback(workspace)
            )
            (workspace / "ppfd_map.txt").write_text("0 0 0 275\n", encoding="utf-8")

            self.assertEqual(
                precomputed_playback._target_classification_map_for_playback(workspace),
                workspace / "ppfd_map.txt",
            )

    def test_proposed_precomputed_contract_forces_matched_ppe_smd_identity(
        self,
    ) -> None:
        req = RadianceRunRequest(
            action="all",
            mode=MODE_SMD,
            execution_mode="precomputed",
            length_ft=10.0,
            width_ft=10.0,
            target_ppfd=750.0,
            match_system_ppe=True,
            plants_enabled=True,
            fspm_target_ppfd_umol_m2_s=275.0,
            fspm_target_tolerance_umol_m2_s=20.0,
        )

        params = request_params_for_mode(
            canonical_plant_enabled_precomputed_request(req)
        )

        self.assertEqual(params["match_system_ppe"], True)
        self.assertEqual(params["smd_model"], "legacy")
        self.assertEqual(params["plants_enabled"], True)
        self.assertEqual(params["plant_rows"], 8)
        self.assertEqual(params["plant_columns"], 8)
        self.assertEqual(
            params["fspm_spectral_transport_mode"],
            PRECOMPUTED_FSPM_SPECTRAL_TRANSPORT_MODE,
        )
        self.assertNotIn("target_ppfd", params)
        self.assertNotIn("fspm_target_ppfd_umol_m2_s", params)
        self.assertNotIn("fspm_target_tolerance_umol_m2_s", params)

    def test_manifest_schema_loading_for_tiny_temp_bundle(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_manifest_fixture_") as tmp:
            dataset_root = Path(tmp)
            req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_HPS,
                length_ft=10.0,
                width_ft=10.0,
                hps_coverage_ft=5.0,
                hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
            )
            ref = bundle_ref(
                Path("/unused-engine-root"),
                req.mode,
                req.length_ft,
                req.width_ft,
                dataset_root,
                req=req,
            )
            self.assertIsNotNone(ref)
            assert ref is not None
            self.assertEqual(ref.mode_dirname, f"hps_{DEFAULT_HPS_IES_VARIANT}_5x5")
            ref.path.mkdir(parents=True)
            (ref.path / "ppfd_map.txt.gz").write_bytes(b"tiny artifact placeholder")
            manifest = {
                "schema_version": SCHEMA_VERSION,
                "mode": req.mode,
                "dims_ft": {"length_ft": 10, "width_ft": 10},
                "request_params": request_params_for_mode(req),
                "artifacts": {"ppfd_map_txt_gz": "ppfd_map.txt.gz"},
            }
            ref.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            loaded = load_manifest(ref)
            self.assertEqual(loaded, manifest)
            self.assertTrue(bundle_complete(ref))
            self.assertTrue(params_match(manifest, request_params_for_mode(req)))

    def test_hps_smoke_bundle_lookup_uses_variant_and_coverage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_hps_lookup_") as tmp:
            dataset_root = Path(tmp)

            for coverage in (4.0, 5.0):
                req = RadianceRunRequest(
                    action="uniformity",
                    mode=MODE_HPS,
                    length_ft=10.0,
                    width_ft=10.0,
                    hps_coverage_ft=coverage,
                    hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
                )
                ref = bundle_ref(
                    Path("/unused-engine-root"),
                    req.mode,
                    req.length_ft,
                    req.width_ft,
                    dataset_root,
                    req=req,
                )
                self.assertIsNotNone(ref)
                assert ref is not None
                self.assertEqual(
                    ref.mode_dirname,
                    f"hps_{DEFAULT_HPS_IES_VARIANT}_{int(coverage)}x{int(coverage)}",
                )
                ref.path.mkdir(parents=True)
                (ref.path / "ppfd_map.txt.gz").write_bytes(b"tiny")
                (ref.path / "hps_layout.json").write_text(
                    json.dumps(_minimal_direct_layout()), encoding="utf-8"
                )
                (ref.path / "power.json").write_text("{}", encoding="utf-8")
                ref.manifest_path.write_text(
                    json.dumps(
                        {
                            "schema_version": SCHEMA_VERSION,
                            "mode": req.mode,
                            "dims_ft": {"length_ft": 10, "width_ft": 10},
                            "request_params": request_params_for_mode(req),
                            "artifacts": {
                                "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                                "layout_json": "hps_layout.json",
                                "power_json": "power.json",
                            },
                        }
                    ),
                    encoding="utf-8",
                )

                with patch.dict(
                    os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
                ):
                    bundle = backend_runtime.precomputed_bundle(req)

                self.assertIsNotNone(bundle)
                assert bundle is not None
                self.assertEqual(bundle[0], ref.path)

    def test_hps_smoke_bundle_lookup_falls_back_to_default_bundle_identity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_hps_default_lookup_"
        ) as tmp:
            dataset_root = Path(tmp)
            bundle_req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_HPS,
                length_ft=10.0,
                width_ft=10.0,
                hps_coverage_ft=4.0,
                hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
                hps_z_m=DEFAULT_HPS_MOUNT_Z_M,
                hps_fixture_ppf=DEFAULT_HPS_FIXTURE_PPF,
                hps_input_watts=DEFAULT_HPS_INPUT_WATTS,
            )
            ref = bundle_ref(
                Path("/unused-engine-root"),
                bundle_req.mode,
                bundle_req.length_ft,
                bundle_req.width_ft,
                dataset_root,
                req=bundle_req,
            )
            self.assertIsNotNone(ref)
            assert ref is not None
            ref.path.mkdir(parents=True)
            (ref.path / "ppfd_map.txt.gz").write_bytes(b"tiny")
            (ref.path / "hps_layout.json").write_text(
                json.dumps(_minimal_direct_layout()), encoding="utf-8"
            )
            (ref.path / "power.json").write_text("{}", encoding="utf-8")
            ref.manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "mode": bundle_req.mode,
                        "dims_ft": {"length_ft": 10, "width_ft": 10},
                        "request_params": request_params_for_mode(bundle_req),
                        "artifacts": {
                            "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                            "layout_json": "hps_layout.json",
                            "power_json": "power.json",
                        },
                    }
                ),
                encoding="utf-8",
            )
            stale_req = RadianceRunRequest(
                action="all",
                mode=MODE_HPS,
                execution_mode="precomputed",
                length_ft=10.0,
                width_ft=10.0,
                hps_coverage_ft=4.0,
                hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
                hps_z_m=0.9144,
                hps_fixture_ppf=DEFAULT_HPS_FIXTURE_PPF,
                hps_input_watts=DEFAULT_HPS_INPUT_WATTS,
            )

            with patch.dict(
                os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
            ):
                matched = backend_runtime.precomputed_request_for_available_bundle(
                    stale_req
                )
                bundle = backend_runtime.precomputed_bundle(stale_req)

            self.assertIsNotNone(matched)
            assert matched is not None
            self.assertEqual(matched.hps_z_m, DEFAULT_HPS_MOUNT_Z_M)
            self.assertEqual(matched.hps_fixture_ppf, DEFAULT_HPS_FIXTURE_PPF)
            self.assertEqual(matched.hps_input_watts, DEFAULT_HPS_INPUT_WATTS)
            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle[0], ref.path)

    def test_precomputed_lookup_normalizes_disabled_plants_to_bundle_contract(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_plant_contract_lookup_"
        ) as tmp:
            dataset_root = Path(tmp)
            user_req = RadianceRunRequest(
                action="all",
                mode=MODE_SMD,
                execution_mode="precomputed",
                length_ft=10.0,
                width_ft=10.0,
                plants_enabled=False,
                fspm_target_ppfd_umol_m2_s=250.0,
                fspm_target_tolerance_umol_m2_s=15.0,
            )
            bundle_req = canonical_plant_enabled_precomputed_request(user_req)
            ref = bundle_ref(
                Path("/unused-engine-root"),
                bundle_req.mode,
                bundle_req.length_ft,
                bundle_req.width_ft,
                dataset_root,
                req=bundle_req,
            )
            self.assertIsNotNone(ref)
            assert ref is not None
            ref.path.mkdir(parents=True)
            basis = np.array([[100.0]], dtype=float)
            np.save(ref.path / "basis_A.npy", basis)
            (ref.path / "basis_manifest.json").write_text(
                json.dumps(
                    {
                        "n_points": 1,
                        "n_vars": 1,
                        "n_rings": 1,
                        "layout_modules": 1,
                        "basis_unit_w_per_module": 1.0,
                        "variables": "rings",
                        "ring_indices": [0],
                        "matrix_sha256": _basis_sha256(basis),
                    }
                ),
                encoding="utf-8",
            )
            (ref.path / "smd_layout.json").write_text(
                '{"version": 2, "units": "meters", "positions": []}',
                encoding="utf-8",
            )
            np.savez_compressed(
                ref.path / "plant_receiver.npz",
                plant_row=np.array([0], dtype=np.uint16),
                plant_column=np.array([0], dtype=np.uint16),
                leaf_index=np.array([0], dtype=np.uint16),
                face_index=np.array([0], dtype=np.uint16),
                stored_ppfd_umol_m2_s=np.array([0.0], dtype=np.float64),
                metadata_json=np.frombuffer(
                    json.dumps(
                        {
                            "schema": "rad_rebuild.precomputed.plant_receiver.v1",
                            "schema_version": 1,
                            "value_semantics": "smd_receiver_basis",
                            "surface_receiver_encoding": "plant_grid_indices_v1",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8"),
                    dtype=np.uint8,
                ),
            )
            np.savez_compressed(
                ref.path / "plant_receiver_basis_A.npz",
                plant_receiver_basis_A=np.array([[0.0]], dtype=np.float64),
            )
            ref.manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "mode": bundle_req.mode,
                        "dims_ft": {"length_ft": 10, "width_ft": 10},
                        "request_params": request_params_for_mode(bundle_req),
                        "artifacts": {
                            "basis_A_npy": "basis_A.npy",
                            "basis_manifest_json": "basis_manifest.json",
                            "layout_json": "smd_layout.json",
                            "plant_receiver_npz": "plant_receiver.npz",
                            "plant_receiver_basis_A_npz": "plant_receiver_basis_A.npz",
                        },
                    }
                ),
                encoding="utf-8",
            )

            with patch.dict(
                os.environ, {"RADIANCE_PRECOMPUTED_ROOT": str(dataset_root)}
            ):
                matched = backend_runtime.precomputed_request_for_available_bundle(
                    user_req
                )
                bundle = backend_runtime.precomputed_bundle(user_req)

            self.assertIsNotNone(matched)
            assert matched is not None
            self.assertTrue(matched.plants_enabled)
            self.assertEqual(matched.plant_rows, 8)
            self.assertEqual(matched.plant_columns, 8)
            self.assertEqual(matched.plant_spacing_m, PRECOMPUTED_PLANT_SPACING_M)
            self.assertEqual(matched.fspm_receiver_granularity, "mesh_patch")
            self.assertEqual(
                request_params_for_mode(matched),
                request_params_for_mode(
                    canonical_plant_enabled_precomputed_request(
                        request_with_updates(
                            user_req,
                            fspm_target_ppfd_umol_m2_s=100.0,
                            fspm_target_tolerance_umol_m2_s=40.0,
                        )
                    )
                ),
            )
            self.assertIsNotNone(bundle)
            assert bundle is not None
            self.assertEqual(bundle[0], ref.path)

    def test_playback_missing_bundle_remains_explicit(self) -> None:
        with (
            tempfile.TemporaryDirectory(
                prefix="rad_rebuild_missing_bundle_"
            ) as dataset_root,
            tempfile.TemporaryDirectory(
                prefix="rad_rebuild_playback_workspace_"
            ) as workspace_root,
        ):
            argv = [
                "precomputed_playback.py",
                "--mode",
                "SMD",
                "--length-ft",
                "10",
                "--width-ft",
                "10",
                "--dataset-root",
                dataset_root,
                "--workspace-root",
                workspace_root,
            ]
            with (
                patch.object(sys, "argv", argv),
                self.assertRaises(SystemExit) as raised,
            ):
                precomputed_playback.main()

        self.assertIn(
            "No precomputed manifest found for SMD 10.0x10.0", str(raised.exception)
        )

    def test_playback_workspace_env_points_derived_artifacts_at_workspace(self) -> None:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_playback_env_"
        ) as workspace_tmp:
            workspace_root = Path(workspace_tmp)
            with patch.object(precomputed_playback, "WORK_ROOT", workspace_root):
                env = precomputed_playback._workspace_env(
                    {"RADIANCE_OUTPUT_ROOT": "/unused"}, workspace_root
                )

            self.assertEqual(env["RADIANCE_OUTPUT_ROOT"], str(workspace_root))
            self.assertEqual(
                env["RADIANCE_RUNTIME_STATE_ROOT"],
                str(workspace_root / "runtime_state"),
            )
            self.assertEqual(
                env["RADIANCE_VISUALIZATION_OUTPUT_ROOT"], str(workspace_root)
            )

    def test_smd_playback_materializes_tiny_bundle_without_generator_subprocess(
        self,
    ) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="rad_rebuild_smd_bundle_") as bundle_tmp,
            tempfile.TemporaryDirectory(
                prefix="rad_rebuild_smd_workspace_"
            ) as workspace_tmp,
        ):
            bundle_dir = Path(bundle_tmp)
            workspace_root = Path(workspace_tmp)
            basis = np.array([[100.0], [100.0]], dtype=float)
            np.save(bundle_dir / "basis_A.npy", basis)
            basis_manifest = {
                "n_points": 2,
                "n_vars": 1,
                "n_rings": 1,
                "layout_modules": 1,
                "basis_unit_w_per_module": 1.0,
                "variables": "rings",
                "ring_indices": [0],
                "emitter_env": {"LAYOUT_MODE": "exact_tiled"},
                "matrix_sha256": _basis_sha256(basis),
            }
            (bundle_dir / "basis_manifest.json").write_text(
                json.dumps(basis_manifest), encoding="utf-8"
            )
            (bundle_dir / "basis_build_log.json").write_text("{}", encoding="utf-8")
            (bundle_dir / "smd_layout.json").write_text(
                json.dumps(
                    {
                        "version": 2,
                        "units": "meters",
                        "rings": 1,
                        "modules": 1,
                        "positions": [{"x": 0.0, "y": 0.0, "z": 0.4572, "ring": 0}],
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "artifacts": {
                    "basis_A_npy": "basis_A.npy",
                    "basis_manifest_json": "basis_manifest.json",
                    "basis_build_log_json": "basis_build_log.json",
                    "layout_json": "smd_layout.json",
                }
            }
            req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_SMD,
                length_ft=10,
                width_ft=10,
                target_ppfd=100,
                w_min=0,
                w_max=10,
            )

            def fake_room_and_grid(
                _env: dict[str, str], _workspace_root: Path
            ) -> None:
                workspace_root.mkdir(parents=True, exist_ok=True)
                (workspace_root / "runtime_state").mkdir(parents=True, exist_ok=True)
                (workspace_root / "sensor_points.txt").write_text(
                    "0 0 0\n1 0 0\n", encoding="utf-8"
                )

            resolved_artifacts = precomputed_playback._preflight_playback_artifacts(
                bundle_dir, manifest, MODE_SMD
            )
            with (
                patch.object(
                    precomputed_playback,
                    "_write_room_and_grid",
                    side_effect=fake_room_and_grid,
                ),
                patch.object(
                    precomputed_playback,
                    "_run_local",
                    side_effect=AssertionError(
                        "SMD playback should not invoke generator subprocesses after grid setup."
                    ),
                ),
                patch.dict(os.environ, {}, clear=True),
                redirect_stdout(io.StringIO()),
            ):
                precomputed_playback._materialize_smd(
                    bundle_dir, manifest, req, workspace_root, resolved_artifacts
                )

            self.assertEqual(
                (workspace_root / "ppfd_map.txt")
                .read_text(encoding="utf-8")
                .splitlines(),
                [
                    "0.000000 0.000000 0.000000 100.000000",
                    "1.000000 0.000000 0.000000 100.000000",
                ],
            )
            self.assertTrue((workspace_root / "ring_powers_optimized.json").is_file())
            self.assertTrue(
                (workspace_root / "runtime_state" / "smd_layout.json").is_file()
            )
            summary = (workspace_root / "runtime_state" / "smd_summary.txt").read_text(
                encoding="utf-8"
            )
            self.assertIn("precomputed playback", summary)
            self.assertIn("total electrical input", summary)
            self.assertTrue(
                (workspace_root / "basis" / "basis_manifest.json").is_file()
            )

    def test_precompute_sweep_dry_run_plans_without_creating_dataset_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_sweep_plan_") as tmp:
            dataset_root = Path(tmp) / "planned-precomputed"
            argv = [
                "precompute_sweep.py",
                "--dry-run",
                "--dataset-root",
                str(dataset_root),
                "--length-min",
                "10",
                "--length-max",
                "10",
                "--width-min",
                "10",
                "--width-max",
                "10",
                "--square-only",
                "--modes",
                "1000W HPS",
                "--hps-coverages",
                "4",
                "--hps-ies-variants",
                "karma",
            ]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                precompute_sweep.main()

            text = output.getvalue()
            self.assertIn("Planned bundles: 1", text)
            self.assertIn("hps_karma_4x4/10x10", text)
            self.assertFalse(dataset_root.exists())

    def test_precompute_sweep_defaults_are_public_rectangular_range(self) -> None:
        with patch.object(sys, "argv", ["precompute_sweep.py"]):
            args = precompute_sweep.parse_args()

        dims = precompute_sweep._sweep_dims(args)

        self.assertFalse(args.plants_enabled)
        self.assertEqual(args.fspm_receiver_granularity, "mesh_patch")
        self.assertEqual(args.length_min, 10)
        self.assertEqual(args.width_min, 10)
        self.assertEqual(args.length_max, 20)
        self.assertEqual(args.width_max, 20)
        self.assertEqual(dims[0], (10, 10))
        self.assertEqual(dims[-1], (20, 20))
        self.assertEqual(len(dims), 66)
        self.assertTrue(all(10 <= width <= length <= 20 for length, width in dims))
        self.assertIn((20, 10), dims)
        self.assertNotIn((10, 20), dims)
        self.assertEqual(len({frozenset(dim) for dim in dims}), len(dims))

    def test_precompute_sweep_dry_run_excludes_mirrored_rectangles(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_unique_rect_plan_") as tmp:
            dataset_root = Path(tmp) / "planned-precomputed"
            argv = [
                "precompute_sweep.py",
                "--dry-run",
                "--dataset-root",
                str(dataset_root),
                "--length-min",
                "10",
                "--length-max",
                "20",
                "--width-min",
                "10",
                "--width-max",
                "20",
                "--modes",
                "SMD",
            ]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                precompute_sweep.main()

            text = output.getvalue()
            self.assertIn("Planned room sizes: 66", text)
            self.assertIn("DRY-RUN SMD 10x10", text)
            self.assertIn("DRY-RUN SMD 20x10", text)
            self.assertIn("DRY-RUN SMD 20x20", text)
            self.assertNotIn("DRY-RUN SMD 10x20", text)
            self.assertFalse(dataset_root.exists())

    def test_precompute_sweep_parser_accepts_mesh_patch_receiver_granularity(self) -> None:
        argv = [
            "precompute_sweep.py",
            "--plants-enabled",
            "--fspm-receiver-granularity",
            "mesh_patch",
        ]

        with patch.object(sys, "argv", argv):
            args = precompute_sweep.parse_args()

        self.assertEqual(args.fspm_receiver_granularity, "mesh_patch")

    def test_precompute_sweep_parser_rejects_invalid_receiver_granularity(self) -> None:
        argv = [
            "precompute_sweep.py",
            "--plants-enabled",
            "--fspm-receiver-granularity",
            "leaf_centroid",
        ]

        with patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit):
                precompute_sweep.parse_args()

    def test_precompute_sweep_plant_dry_run_shows_density(self) -> None:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_plant_sweep_plan_") as tmp:
            dataset_root = Path(tmp) / "planned-precomputed"
            argv = [
                "precompute_sweep.py",
                "--dry-run",
                "--plants-enabled",
                "--dataset-root",
                str(dataset_root),
                "--length-min",
                "10",
                "--length-max",
                "20",
                "--width-min",
                "10",
                "--width-max",
                "20",
                "--step",
                "10",
                "--square-only",
                "--modes",
                "SMD",
            ]
            output = io.StringIO()
            with patch.object(sys, "argv", argv), redirect_stdout(output):
                precompute_sweep.main()

            text = output.getvalue()
            self.assertIn("Planned bundles: 2", text)
            self.assertIn("DRY-RUN SMD 10x10 plants=8x8 spacing=0.4m", text)
            self.assertIn("DRY-RUN SMD 20x20 plants=15x15 spacing=0.4m", text)
            self.assertFalse(dataset_root.exists())

    def test_precompute_sweep_plant_smd_request_uses_matched_ppe_contract(self) -> None:
        args = precompute_sweep.PrecomputeSweepConfig(
            modes=(MODE_SMD,),
            plants_enabled=True,
        ).to_namespace()

        req = precompute_sweep._req_for(MODE_SMD, 10, 10, args)
        params = request_params_for_mode(req)

        self.assertTrue(req.match_system_ppe)
        self.assertEqual(req.sim_mode, "standard")
        self.assertTrue(req.plants_enabled)
        self.assertEqual(req.fspm_receiver_granularity, "mesh_patch")
        self.assertEqual(req.fspm_spectral_transport_mode, "scalar_source_weighted")
        self.assertEqual(params["match_system_ppe"], True)
        self.assertEqual(params["smd_model"], "legacy")
        self.assertEqual(params["fspm_receiver_granularity"], "mesh_patch")

    def test_precompute_sweep_plant_request_allows_explicit_leaf_quadrature(self) -> None:
        args = precompute_sweep.PrecomputeSweepConfig(
            modes=(MODE_SMD,),
            plants_enabled=True,
            fspm_receiver_granularity="leaf_quadrature_4",
        ).to_namespace()

        req = precompute_sweep._req_for(MODE_SMD, 10, 10, args)
        params = request_params_for_mode(req)

        self.assertEqual(req.fspm_receiver_granularity, "leaf_quadrature_4")
        self.assertEqual(params["fspm_receiver_granularity"], "leaf_quadrature_4")

    def _run_generation_profile(self, profile: str, *extra_args: str) -> str:
        with tempfile.TemporaryDirectory(prefix="rad_rebuild_profile_plan_") as tmp:
            dataset_root = Path(tmp) / "planned-precomputed"
            env = os.environ.copy()
            env["PY"] = sys.executable
            env["RADIANCE_PRECOMPUTED_ROOT"] = str(dataset_root)
            result = subprocess.run(
                [str(GENERATION_SCRIPT), profile, "--dry-run", *extra_args],
                cwd=REPO_ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=True,
            )
            self.assertFalse(dataset_root.exists())
            return result.stdout

    def _run_generation_profile_failure(
        self, profile: str, *extra_args: str
    ) -> subprocess.CalledProcessError:
        with tempfile.TemporaryDirectory(
            prefix="rad_rebuild_profile_plan_fail_"
        ) as tmp:
            dataset_root = Path(tmp) / "planned-precomputed"
            env = os.environ.copy()
            env["PY"] = sys.executable
            env["RADIANCE_PRECOMPUTED_ROOT"] = str(dataset_root)
            with self.assertRaises(subprocess.CalledProcessError) as raised:
                subprocess.run(
                    [str(GENERATION_SCRIPT), profile, "--dry-run", *extra_args],
                    cwd=REPO_ROOT,
                    env=env,
                    text=True,
                    capture_output=True,
                    check=True,
                )
            self.assertFalse(dataset_root.exists())
            return raised.exception

    def test_smoke_profile_plans_ui_bundle_set(self) -> None:
        smoke = self._run_generation_profile("smoke")

        self.assertIn("Planned bundles: 3", smoke)
        self.assertIn("DRY-RUN SMD 10x10", smoke)
        self.assertIn("DRY-RUN Competitor Practical Coverage 10x10", smoke)
        self.assertIn(f"DRY-RUN 1000W HPS {DEFAULT_HPS_IES_VARIANT} 4x4 10x10", smoke)
        self.assertIn("smd/10x10", smoke)
        self.assertIn("competitor_practical/10x10", smoke)
        self.assertIn(f"hps_{DEFAULT_HPS_IES_VARIANT}_4x4/10x10", smoke)
        self.assertNotIn("DRY-RUN Competitor 10x10", smoke)
        self.assertNotIn(f"1000W HPS {DEFAULT_HPS_IES_VARIANT} 5x5", smoke)
        self.assertNotIn(f"hps_{DEFAULT_HPS_IES_VARIANT}_5x5", smoke)

    def test_full_profile_plans_only_default_hps_ies_variant(self) -> None:
        full = self._run_generation_profile("full", "--square-only")

        self.assertIn("Planned room sizes: 11", full)
        self.assertIn("Planned mode/layout variants: 3", full)
        self.assertIn("Planned bundles: 33", full)
        self.assertIn(f"1000W HPS {DEFAULT_HPS_IES_VARIANT} 4x4 10x10", full)
        self.assertIn(f"hps_{DEFAULT_HPS_IES_VARIANT}_4x4/10x10", full)
        self.assertIn("smd/20x20", full)
        self.assertIn("competitor_practical/20x20", full)
        self.assertIn(f"hps_{DEFAULT_HPS_IES_VARIANT}_4x4/20x20", full)
        self.assertNotIn("21x21", full)
        self.assertNotIn("30x30", full)
        self.assertNotIn("40x40", full)
        self.assertNotIn("DRY-RUN Competitor 10x10", full)
        self.assertNotIn(f"1000W HPS {DEFAULT_HPS_IES_VARIANT} 5x5", full)
        self.assertNotIn(f"hps_{DEFAULT_HPS_IES_VARIANT}_5x5", full)
        for variant in {"karma", "og", "hde", "legacy"} - {DEFAULT_HPS_IES_VARIANT}:
            self.assertNotIn(f"1000W HPS {variant} ", full)
            self.assertNotIn(f"hps_{variant}_", full)

    def test_custom_profile_rejects_removed_hps_ies_variant(self) -> None:
        failure = self._run_generation_profile_failure(
            "custom",
            "--length-min",
            "10",
            "--length-max",
            "10",
            "--width-min",
            "10",
            "--width-max",
            "10",
            "--square-only",
            "--modes",
            "1000W HPS",
            "--hps-coverages",
            "4",
            "--hps-ies-variants",
            "og",
        )

        self.assertNotEqual(failure.returncode, 0)
        self.assertIn("Unsupported HPS IES variant", failure.stderr)

    def test_competitor_layout_contract_keeps_full_and_practical(self) -> None:
        self.assertEqual(canonical_competitor_layout(None), "full")
        self.assertEqual(canonical_competitor_layout("full"), "full")
        self.assertEqual(canonical_competitor_layout("practical"), "practical")
        self.assertEqual(canonical_competitor_layout("Practical Coverage"), "practical")

    def test_competitor_playback_materializes_tiny_temp_bundle(self) -> None:
        with (
            tempfile.TemporaryDirectory(
                prefix="rad_rebuild_competitor_bundle_"
            ) as bundle_tmp,
            tempfile.TemporaryDirectory(
                prefix="rad_rebuild_competitor_workspace_"
            ) as workspace_tmp,
        ):
            bundle_dir = Path(bundle_tmp)
            workspace_root = Path(workspace_tmp)
            runtime_state = workspace_root / "runtime_state"
            runtime_state.mkdir(parents=True)
            with gzip.open(
                bundle_dir / "ppfd_map.txt.gz", "wt", encoding="utf-8"
            ) as handle:
                handle.write("0 0 0 100\n")
                handle.write("1 0 0 200\n")
            (bundle_dir / "spydr3_layout.json").write_text(
                json.dumps(_minimal_direct_layout()), encoding="utf-8"
            )
            (bundle_dir / "power.json").write_text(
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
                        "layout_mode": "full",
                        "fixture_count": 1,
                        "droop_enabled": False,
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "artifacts": {
                    "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                    "layout_json": "spydr3_layout.json",
                    "power_json": "power.json",
                },
                "stats": {"base_peak_ppfd": 200.0, "base_mean_ppfd": 150.0},
            }
            req = RadianceRunRequest(
                action="competitor",
                mode=MODE_COMPETITOR,
                length_ft=10,
                width_ft=10,
                target_ppfd=75,
                competitor_layout="full",
            )

            with (
                patch.object(
                    precomputed_playback,
                    "_write_room_and_grid",
                ),
                redirect_stdout(io.StringIO()),
            ):
                resolved_artifacts = precomputed_playback._preflight_playback_artifacts(
                    bundle_dir, manifest, MODE_COMPETITOR
                )
                precomputed_playback._materialize_competitor(
                    bundle_dir, manifest, req, workspace_root, resolved_artifacts
                )

            ppfd_lines = (
                (workspace_root / "ppfd_map.txt")
                .read_text(encoding="utf-8")
                .splitlines()
            )
            self.assertEqual(ppfd_lines, ["0 0 0 50.000000", "1 0 0 100.000000"])
            self.assertTrue((runtime_state / "spydr3_layout.json").is_file())
            self.assertIn(
                "total_ppf=1120.00000000",
                (runtime_state / "spydr3_power.txt").read_text(encoding="utf-8"),
            )

    def test_hps_playback_materializes_tiny_temp_bundle(self) -> None:
        with (
            tempfile.TemporaryDirectory(prefix="rad_rebuild_hps_bundle_") as bundle_tmp,
            tempfile.TemporaryDirectory(
                prefix="rad_rebuild_hps_workspace_"
            ) as workspace_tmp,
        ):
            bundle_dir = Path(bundle_tmp)
            workspace_root = Path(workspace_tmp)
            runtime_state = workspace_root / "runtime_state"
            runtime_state.mkdir(parents=True)
            with gzip.open(
                bundle_dir / "ppfd_map.txt.gz", "wt", encoding="utf-8"
            ) as handle:
                handle.write("0 0 0 100\n")
                handle.write("1 0 0 200\n")
            (bundle_dir / "hps_layout.json").write_text(
                json.dumps({**_minimal_direct_layout(), "nx": 1, "ny": 1}),
                encoding="utf-8",
            )
            (bundle_dir / "power.json").write_text(
                json.dumps(
                    {
                        "profile": "tiny hps",
                        "coverage_ft": 4,
                        "fixture_ppf": 1797.4,
                        "active_fixture_ppf_umol_s": 1797.4,
                        "fixture_input_w": 1045.0,
                        "fixture_ppe": 1.72,
                        "total_ppf": 1797.4,
                        "total_w": 1045.0,
                        "fixture_count": 1,
                        "model_label": "tiny hps fixture",
                        "model_mode": "strict_ies",
                        "archetype": "test",
                    }
                ),
                encoding="utf-8",
            )
            manifest = {
                "artifacts": {
                    "ppfd_map_txt_gz": "ppfd_map.txt.gz",
                    "layout_json": "hps_layout.json",
                    "power_json": "power.json",
                },
                "stats": {"base_mean_ppfd": 150.0},
            }
            req = RadianceRunRequest(
                action="uniformity",
                mode=MODE_HPS,
                length_ft=10,
                width_ft=10,
                hps_coverage_ft=4,
                hps_ies_variant=DEFAULT_HPS_IES_VARIANT,
            )

            with (
                patch.object(
                    precomputed_playback,
                    "_write_room_and_grid",
                ),
                redirect_stdout(io.StringIO()),
            ):
                resolved_artifacts = precomputed_playback._preflight_playback_artifacts(
                    bundle_dir, manifest, MODE_HPS
                )
                precomputed_playback._materialize_hps(
                    bundle_dir, manifest, req, workspace_root, resolved_artifacts
                )

            self.assertEqual(
                (workspace_root / "ppfd_map.txt")
                .read_text(encoding="utf-8")
                .splitlines(),
                ["0 0 0 100.000000", "1 0 0 200.000000"],
            )
            self.assertTrue((runtime_state / "hps_layout.json").is_file())
            self.assertIn(
                "total_ppf=1797.40000000",
                (runtime_state / "hps_power.txt").read_text(encoding="utf-8"),
            )
            self.assertIn(
                "tiny hps fixture",
                (runtime_state / "hps_summary.txt").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
