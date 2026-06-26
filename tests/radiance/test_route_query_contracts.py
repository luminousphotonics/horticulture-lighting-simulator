from __future__ import annotations

import csv
from io import StringIO
import json
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from fastapi import HTTPException, Request

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend.routes import artifacts as artifacts_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import metrics as metrics_route  # noqa: E402
from rad_rebuild.radiance.assembly.fspm_csv import FSPM_CSV_HEADERS  # noqa: E402
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_DOCKER, MODE_HPS, MODE_SMD  # noqa: E402
from rad_rebuild.radiance.engine.plants.photoreceptor import PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.photosynthesis import PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.spectral import PLANT_SPECTRAL_RESPONSE_SCHEMA  # noqa: E402
from rad_rebuild.radiance.engine.plants.surface_flux import PLANT_SURFACE_FLUX_SCHEMA  # noqa: E402
from rad_rebuild.web.app import load_web_settings  # noqa: E402


class _FakeRequest:
    headers: dict[str, str] = {}

    def __init__(
        self,
        session_id: str = "route-contract",
        method: str = "GET",
        query_params: dict[str, str] | None = None,
    ) -> None:
        self.query_params = {"session_id": session_id, **(query_params or {})}
        self.method = method


def _request(
    session_id: str = "route-contract",
    method: str = "GET",
    query_params: dict[str, str] | None = None,
) -> Request:
    return cast(Request, _FakeRequest(session_id, method, query_params))


class RadianceRouteQueryContractTests(unittest.TestCase):
    def test_manifest_accepts_visualize_request_action(self) -> None:
        workspace = Path(tempfile.mkdtemp(prefix="rad_rebuild_manifest_workspace_"))
        outdir = workspace / "ppfd_visualizations_proposed"
        manifest_path = workspace / "artifacts" / "radiance_manifest_smd.json"
        (workspace / "ppfd_map.txt").write_text("0 0 0 1000\n", encoding="utf-8")
        manifest = {"mode": "SMD", "grid": {"ppfd_map_txt": "ppfd_map.txt"}}

        with (
            patch.object(
                artifacts_route,
                "authorize_workspace_from_request",
                return_value=workspace,
            ),
            patch.object(artifacts_route, "_env_for_mode", return_value={}),
            patch.object(artifacts_route, "_ensure_visuals", return_value=outdir),
            patch.object(
                artifacts_route,
                "_get_or_build_manifest",
                return_value=(manifest, manifest_path),
            ),
        ):
            payload = artifacts_route.radiance_manifest(
                artifacts_route.RadianceRunRequest(
                    action="visualize",
                    mode="SMD",
                    execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                    length_ft=10,
                    width_ft=10,
                    target_ppfd=1000,
                ),
                _request(),
            )

        self.assertEqual(payload["manifest"], manifest)
        self.assertEqual(payload["path"], "artifacts/radiance_manifest_smd.json")

    def test_metrics_workspace_lookup_preserves_karma_hps_ies_variant(self) -> None:
        captured = SimpleNamespace(req=None)

        def fake_authorize(_request: object, req: object) -> Path:
            captured.req = req
            return Path(tempfile.mkdtemp(prefix="rad_rebuild_metrics_contract_"))

        with (
            patch.object(
                metrics_route,
                "authorize_workspace_from_request",
                side_effect=fake_authorize,
            ),
            patch.object(
                metrics_route,
                "get_metrics_payload",
                return_value={"ok": True},
            ),
        ):
            payload = metrics_route.radiance_metrics(
                _request(),
                mode=MODE_HPS,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=12,
                target_ppfd=1000,
                hps_coverage_ft=5,
                hps_z_m=1.0668,
                hps_ies_variant="karma",
            )

        self.assertEqual(payload, {"ok": True})
        self.assertEqual(captured.req.hps_ies_variant, "karma")
        self.assertEqual(captured.req.w_min, 10.0)

    def test_metrics_workspace_lookup_preserves_fspm_target_query_fields(self) -> None:
        captured = SimpleNamespace(req=None)

        def fake_authorize(_request: object, req: object) -> Path:
            captured.req = req
            return Path(tempfile.mkdtemp(prefix="rad_rebuild_metrics_fspm_contract_"))

        with (
            patch.object(
                metrics_route,
                "authorize_workspace_from_request",
                side_effect=fake_authorize,
            ),
            patch.object(
                metrics_route,
                "get_metrics_payload",
                return_value={"ok": True},
            ),
        ):
            payload = metrics_route.radiance_metrics(
                _request(
                    query_params={
                        "plants_enabled": "true",
                        "fspm_target_ppfd_umol_m2_s": "275",
                        "fspm_target_tolerance_umol_m2_s": "20",
                    }
                ),
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=12,
                target_ppfd=1000,
            )

        self.assertEqual(payload, {"ok": True})
        self.assertTrue(captured.req.plants_enabled)
        self.assertEqual(captured.req.fspm_target_ppfd_umol_m2_s, 275.0)
        self.assertEqual(captured.req.fspm_target_tolerance_umol_m2_s, 20.0)

    def test_metrics_rejects_removed_hps_ies_variant(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            metrics_route.radiance_metrics(
                _request(),
                mode=MODE_HPS,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=12,
                target_ppfd=1000,
                hps_coverage_ft=5,
                hps_z_m=1.0668,
                hps_ies_variant="og",
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertIn("Unsupported HPS IES variant", str(raised.exception.detail))

    def test_ppfd_csv_workspace_lookup_preserves_karma_hps_ies_variant(self) -> None:
        captured = SimpleNamespace(req=None)
        csv_file = (
            Path(tempfile.mkdtemp(prefix="rad_rebuild_csv_contract_")) / "ppfd_map.csv"
        )
        csv_file.write_text("x,y,ppfd\n", encoding="utf-8")

        def fake_authorize(_request: object, req: object) -> Path:
            captured.req = req
            root = Path(tempfile.mkdtemp(prefix="rad_rebuild_ppfd_contract_"))
            (root / "ppfd_map.txt").write_text("0 0 1000\n", encoding="utf-8")
            return root

        with (
            patch.object(
                artifacts_route,
                "authorize_workspace_from_request",
                side_effect=fake_authorize,
            ),
            patch.object(
                artifacts_route,
                "_write_ppfd_csv",
                return_value=csv_file,
            ),
        ):
            response = artifacts_route.radiance_ppfd_csv(
                _request(
                    query_params={
                        "plants_enabled": "true",
                        "fspm_target_ppfd_umol_m2_s": "275",
                        "fspm_target_tolerance_umol_m2_s": "20",
                    }
                ),
                mode=MODE_HPS,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=12,
                target_ppfd=1000,
                hps_coverage_ft=5,
                hps_z_m=1.0668,
                hps_ies_variant="karma",
            )

        self.assertEqual(Path(response.path), csv_file)
        self.assertEqual(captured.req.hps_ies_variant, "karma")
        self.assertEqual(captured.req.w_min, 10.0)
        self.assertTrue(captured.req.plants_enabled)
        self.assertEqual(captured.req.fspm_target_ppfd_umol_m2_s, 275.0)
        self.assertEqual(captured.req.fspm_target_tolerance_umol_m2_s, 20.0)

    def test_fspm_csv_exports_compact_authorized_summary(self) -> None:
        captured = SimpleNamespace(req=None)

        def fake_authorize(_request: object, req: object) -> Path:
            captured.req = req
            root = Path(tempfile.mkdtemp(prefix="rad_rebuild_fspm_csv_workspace_"))
            runtime = root / "runtime_state"
            runtime.mkdir()
            (runtime / "plant_surface_flux.json").write_text(
                json.dumps(
                    {
                        "schema": PLANT_SURFACE_FLUX_SCHEMA,
                        "schema_version": 1,
                        "status": "computed",
                        "method": "radiance_leaf_surface_receiver_sampling_v1",
                        "plant_count": 2,
                        "leaf_count": 8,
                        "surface_count": 16,
                        "one_sided_leaf_area_m2": 0.124,
                        "target_ppfd_umol_m2_s": 275.0,
                        "target_tolerance_umol_m2_s": 20.0,
                        "target_lower_threshold_umol_m2_s": 255.0,
                        "target_upper_threshold_umol_m2_s": 295.0,
                        "target_classification_basis": "canopy_plane_equivalent_incident_ppfd",
                        "target_classification_source": "interpolated_runtime_ppfd_map",
                        "under_lit_leaf_count": 2,
                        "target_range_leaf_count": 5,
                        "over_lit_leaf_count": 1,
                        "under_lit_leaf_fraction": 0.25,
                        "target_range_leaf_fraction": 0.625,
                        "over_lit_leaf_fraction": 0.125,
                        "under_lit_surface_count": 4,
                        "target_range_surface_count": 10,
                        "over_lit_surface_count": 2,
                        "under_lit_surface_fraction": 0.25,
                        "target_range_surface_fraction": 0.625,
                        "over_lit_surface_fraction": 0.125,
                        "target_capped_incident_flux_total_umol_s": 34.0,
                        "excess_incident_flux_above_target_umol_s": 4.0,
                        "deficit_to_target_incident_flux_umol_s": 7.0,
                        "raw_mean_flux_density_umol_m2_s": 320.0,
                        "target_capped_incident_mean_flux_density_umol_m2_s": 274.2,
                        "lower_tail_raw_flux_density_umol_m2_s": 150.0,
                        "lower_tail_target_classification_ppfd_umol_m2_s": 180.0,
                        "total_incident_photon_flux_umol_s": 60.0,
                        "total_absorbed_photon_flux_umol_s": 42.0,
                        "mean_absorbed_fraction_of_incident": 0.7,
                        "plant_to_plant_absorbed_photon_flux_cv": 0.08,
                        "plant_to_plant_target_capped_incident_flux_cv": 0.06,
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "plant_spectral_response.json").write_text(
                json.dumps(
                    {
                        "schema": PLANT_SPECTRAL_RESPONSE_SCHEMA,
                        "schema_version": 1,
                        "status": "computed",
                        "method": "surface_flux_band_weighted_leaf_absorptance_v1",
                        "plant_count": 2,
                        "leaf_count": 8,
                        "surface_count": 16,
                        "total_absorbed_par_photon_flux_umol_s": 30.0,
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "plant_photosynthesis_response.json").write_text(
                json.dumps(
                    {
                        "schema": PLANT_PHOTOSYNTHESIS_RESPONSE_SCHEMA,
                        "schema_version": 2,
                        "status": "computed",
                        "method": "absorbed_par_non_rectangular_hyperbola_v2",
                        "calibration_status": "uncalibrated_model_scaffold",
                        "area_weighted_mean_local_response_0_1": 0.64,
                        "local_response_p10_0_1": 0.58,
                        "plant_to_plant_photosynthetic_response_cv": 0.04,
                        "nonuniformity_response_retention_0_1": 0.97,
                    }
                ),
                encoding="utf-8",
            )
            (runtime / "plant_photoreceptor_exposure.json").write_text(
                json.dumps(
                    {
                        "schema": PLANT_PHOTORECEPTOR_EXPOSURE_SCHEMA,
                        "schema_version": 1,
                        "status": "computed",
                        "method": "spectral_band_exposure_inputs_v1",
                        "plant_count": 2,
                        "leaf_count": 8,
                        "surface_count": 16,
                        "phytochrome_pss_proxy": {"value": None, "status": "not_computed"},
                        "blue_photon_dose": {"value_umol_m2": None, "status": "not_computed"},
                        "plant_summaries": [
                            {
                                "absorbed_blue_pfd_umol_m2_s": 83.3,
                                "absorbed_green_pfd_umol_m2_s": 125.0,
                                "absorbed_red_pfd_umol_m2_s": 208.3,
                                "absorbed_far_red_pfd_umol_m2_s": 83.3,
                                "absorbed_blue_fraction_of_par": 0.2,
                                "absorbed_red_to_far_red_ratio_diagnostic": 2.5,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            return root

        with patch.object(
            artifacts_route,
            "authorize_workspace_from_request",
            side_effect=fake_authorize,
        ):
            response = artifacts_route.radiance_fspm_csv(
                _request(
                    session_id="fspm-csv-contract",
                    query_params={
                        "plants_enabled": "true",
                        "plant_seed": "13",
                        "plant_rows": "2",
                        "plant_columns": "1",
                        "plant_leaf_count": "4",
                        "fspm_target_ppfd_umol_m2_s": "275",
                        "fspm_target_tolerance_umol_m2_s": "20",
                    },
                ),
                mode=MODE_SMD,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
                match_system_ppe=True,
            )

        self.assertEqual(response.media_type, "text/csv")
        self.assertIn(
            'attachment; filename="fspm_summary_smd_fspm-csv-contract.csv"',
            response.headers["content-disposition"],
        )
        self.assertTrue(captured.req.match_system_ppe)
        self.assertTrue(captured.req.plants_enabled)
        self.assertEqual(captured.req.plant_seed, 13)
        self.assertEqual(captured.req.plant_rows, 2)
        self.assertEqual(captured.req.plant_leaf_count, 4)
        self.assertEqual(captured.req.fspm_target_ppfd_umol_m2_s, 275.0)
        self.assertEqual(captured.req.fspm_target_tolerance_umol_m2_s, 20.0)

        text = response.body.decode("utf-8")
        self.assertNotIn("metric_name", text)
        self.assertNotIn("bucket_label", text)
        self.assertNotIn("bucket_min", text)
        self.assertNotIn("bucket_max", text)
        self.assertNotIn("plant_id", text)
        self.assertNotIn("leaf_id", text)
        self.assertNotIn("surface_id", text)
        rows = list(csv.DictReader(StringIO(text)))
        self.assertEqual(list(rows[0].keys()), list(FSPM_CSV_HEADERS))
        self.assertEqual(len(rows), 1)
        self.assertNotIn("raw_incident_vs_target_capacity_percent", rows[0])
        row = rows[0]
        self.assertEqual(row["run_id"], "fspm-csv-contract")
        self.assertEqual(row["mode"], MODE_SMD)
        self.assertEqual(row["system_label"], "Proposed LED System")
        self.assertEqual(row["artifact_schema"], PLANT_SURFACE_FLUX_SCHEMA)
        self.assertEqual(row["method"], "radiance_leaf_surface_receiver_sampling_v1")
        self.assertEqual(row["plant_count"], "2")
        self.assertEqual(row["leaf_count"], "8")
        self.assertEqual(row["receiver_surface_count"], "16")
        self.assertEqual(row["under_lit_leaves"], "2")
        self.assertEqual(row["target_range_leaves"], "5")
        self.assertEqual(row["over_lit_leaves"], "1")
        self.assertEqual(row["under_lit_leaf_percent"], "25")
        self.assertEqual(row["target_range_leaf_percent"], "62.5")
        self.assertEqual(row["over_lit_leaf_percent"], "12.5")
        self.assertEqual(row["under_lit_receiver_surfaces"], "4")
        self.assertEqual(row["target_range_receiver_surfaces"], "10")
        self.assertEqual(row["over_lit_receiver_surfaces"], "2")
        self.assertEqual(row["under_lit_receiver_surface_percent"], "25")
        self.assertEqual(row["target_range_receiver_surface_percent"], "62.5")
        self.assertEqual(row["over_lit_receiver_surface_percent"], "12.5")
        self.assertEqual(row["target_capped_incident_flux_total_umol_s"], "34")
        self.assertAlmostEqual(
            float(row["target_capacity_incident_flux_umol_s"]),
            34.1,
        )
        self.assertEqual(row["raw_incident_flux_total_umol_s"], "60")
        self.assertEqual(row["raw_absorbed_flux_total_umol_s"], "42")
        self.assertEqual(row["absorbed_fraction_percent"], "70")
        self.assertAlmostEqual(
            float(row["target_capped_incident_fraction_of_raw_percent"]),
            34.0 / 60.0 * 100.0,
        )
        self.assertAlmostEqual(
            float(row["target_capped_incident_fraction_of_capacity_percent"]),
            34.0 / 34.1 * 100.0,
        )
        self.assertAlmostEqual(
            float(row["excess_incident_fraction_of_raw_percent"]),
            4.0 / 60.0 * 100.0,
        )
        self.assertAlmostEqual(
            float(row["deficit_to_target_capacity_percent"]),
            7.0 / 34.1 * 100.0,
        )
        self.assertEqual(row["raw_plant_to_plant_absorption_cv_percent"], "8")
        self.assertEqual(row["target_capped_incident_plant_to_plant_cv_percent"], "6")
        self.assertEqual(row["blue_pfd_umol_m2_s"], "83.3")
        self.assertEqual(row["blue_fraction_of_par_percent"], "20")
        self.assertEqual(row["red_to_far_red_diagnostic"], "2.5")
        self.assertEqual(row["phytochrome_pss_proxy"], "")
        self.assertEqual(row["blue_dose_mol_m2"], "")
        self.assertEqual(row["photosynthetic_light_response_mean"], "0.64")
        self.assertEqual(row["photosynthetic_light_response_lower_tail"], "0.58")
        self.assertEqual(row["photosynthetic_light_response_cv_percent"], "4")
        self.assertEqual(row["nonuniformity_response_retention"], "0.97")
        self.assertEqual(row["calibration_status"], "uncalibrated_model_scaffold")
        self.assertEqual(
            row["target_classification_basis"],
            "canopy_plane_equivalent_incident_ppfd",
        )
        self.assertEqual(
            row["target_classification_source"],
            "interpolated_runtime_ppfd_map",
        )
        self.assertIn(
            "Raw receiver incident flux is physical receiver accounting",
            row["note"],
        )
        self.assertIn("not target-equivalent PPFD classification", row["note"])
        for key, value in row.items():
            if key in {
                "run_id",
                "mode",
                "system_label",
                "method",
                "artifact_schema",
                "target_classification_basis",
                "target_classification_source",
                "calibration_status",
                "note",
            }:
                continue
            if value:
                self.assertRegex(value, r"^-?\d+(?:\.\d+)?(?:e[+-]?\d+)?$", key)
        self.assertNotRegex(text.lower(), r"yield|biomass|harvest|crop output|growth prediction")

    def test_fspm_csv_missing_optional_artifacts_uses_blank_cells(self) -> None:
        def fake_authorize(_request: object, _req: object) -> Path:
            root = Path(tempfile.mkdtemp(prefix="rad_rebuild_fspm_csv_minimal_"))
            runtime = root / "runtime_state"
            runtime.mkdir()
            (runtime / "plant_surface_flux.json").write_text(
                json.dumps(
                    {
                        "schema": PLANT_SURFACE_FLUX_SCHEMA,
                        "status": "computed",
                        "method": "radiance_leaf_surface_receiver_sampling_v1",
                        "plant_count": 1,
                        "leaf_count": 2,
                        "surface_count": 4,
                        "one_sided_leaf_area_m2": 0.0,
                        "target_ppfd_umol_m2_s": 300.0,
                        "target_tolerance_umol_m2_s": 30.0,
                        "target_lower_threshold_umol_m2_s": 270.0,
                        "target_upper_threshold_umol_m2_s": 330.0,
                        "under_lit_leaf_count": 0,
                        "target_range_leaf_count": 2,
                        "over_lit_leaf_count": 0,
                        "under_lit_leaf_fraction": 0.0,
                        "target_range_leaf_fraction": 1.0,
                        "over_lit_leaf_fraction": 0.0,
                        "total_incident_photon_flux_umol_s": 8.0,
                        "total_absorbed_photon_flux_umol_s": 5.6,
                        "mean_absorbed_fraction_of_incident": 0.7,
                    }
                ),
                encoding="utf-8",
            )
            return root

        with patch.object(
            artifacts_route,
            "authorize_workspace_from_request",
            side_effect=fake_authorize,
        ):
            response = artifacts_route.radiance_fspm_csv(
                _request(session_id="minimal-fspm-csv"),
                mode=MODE_SMD,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            )

        rows = list(csv.DictReader(StringIO(response.body.decode("utf-8"))))
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["plant_count"], "1")
        self.assertEqual(row["target_range_leaf_percent"], "100")
        self.assertEqual(row["target_capacity_incident_flux_umol_s"], "0")
        self.assertEqual(row["target_capped_incident_fraction_of_capacity_percent"], "")
        self.assertEqual(row["deficit_to_target_capacity_percent"], "")
        self.assertEqual(row["blue_pfd_umol_m2_s"], "")
        self.assertEqual(row["photosynthetic_light_response_mean"], "")
        self.assertEqual(row["calibration_status"], "")

    def test_fspm_csv_export_has_single_header_for_manual_combining(self) -> None:
        header = ",".join(FSPM_CSV_HEADERS)
        body = ",".join("" for _ in FSPM_CSV_HEADERS)
        exported_text = f"{header}\n{body}\n"

        self.assertEqual(exported_text.count(header), 1)
        rows = list(csv.DictReader(StringIO(exported_text)))
        self.assertEqual(len(rows), 1)
        self.assertNotEqual(rows[0]["run_id"], "run_id")

    def test_scatter_request_workspace_lookup_preserves_karma_hps_ies_variant(
        self,
    ) -> None:
        captured = SimpleNamespace(req=None)
        scatter_file = (
            Path(tempfile.mkdtemp(prefix="rad_rebuild_scatter_contract_"))
            / "scatter.html"
        )
        scatter_file.write_text("<html></html>", encoding="utf-8")

        def fake_authorize(_request: object, req: object) -> Path:
            captured.req = req
            root = Path(tempfile.mkdtemp(prefix="rad_rebuild_scatter_workspace_"))
            (root / "ppfd_map.txt").write_text("0 0 1000\n", encoding="utf-8")
            return root

        with (
            patch.object(
                artifacts_route,
                "authorize_workspace_from_request",
                side_effect=fake_authorize,
            ),
            patch.object(
                artifacts_route,
                "_env_for_mode",
                return_value={},
            ),
            patch.object(artifacts_route, "_ensure_scatter", return_value=scatter_file),
        ):
            response = artifacts_route.radiance_scatter(
                _request(),
                mode=MODE_HPS,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=12,
                target_ppfd=1000,
                hps_coverage_ft=5,
                hps_z_m=1.0668,
                hps_ies_variant="karma",
            )

        self.assertEqual(Path(response.path), scatter_file)
        self.assertEqual(captured.req.hps_ies_variant, "karma")
        self.assertEqual(captured.req.w_min, 10.0)

    def test_scatter_head_preflight_generates_artifact(self) -> None:
        workspace = Path(tempfile.mkdtemp(prefix="rad_rebuild_scatter_head_workspace_"))
        scatter_file = workspace / "ppfd_visualizations_proposed" / "ppfd_scatter_3d.html"
        scatter_file.parent.mkdir(parents=True)
        scatter_file.write_text("<html></html>", encoding="utf-8")
        (workspace / "ppfd_map.txt").write_text("0 0 0 1000\n", encoding="utf-8")

        with (
            patch.object(
                artifacts_route,
                "authorize_workspace_from_request",
                return_value=workspace,
            ),
            patch.object(artifacts_route, "_env_for_mode", return_value={}),
            patch.object(
                artifacts_route,
                "_ensure_scatter",
                return_value=scatter_file,
            ) as ensure_scatter,
        ):
            response = artifacts_route.radiance_scatter(
                _request(method="HEAD"),
                mode="SMD",
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            )

        ensure_scatter.assert_called_once()
        self.assertEqual(response.status_code, 200)

    def test_images_workspace_lookup_preserves_karma_hps_ies_variant(self) -> None:
        captured = SimpleNamespace(req=None)
        outdir = Path(tempfile.mkdtemp(prefix="rad_rebuild_images_contract_"))
        overlay = outdir / "ppfd_heatmap_overlay.png"
        annot = outdir / "ppfd_heatmap_annotated.png"
        overlay.write_bytes(b"overlay")
        annot.write_bytes(b"annot")

        def fake_authorize(_request: object, req: object) -> Path:
            captured.req = req
            return Path(tempfile.mkdtemp(prefix="rad_rebuild_images_workspace_"))

        with (
            patch.object(
                artifacts_route,
                "authorize_workspace_from_request",
                side_effect=fake_authorize,
            ),
            patch.object(
                artifacts_route,
                "_resolve_output_dir_path",
                return_value=outdir,
            ),
        ):
            payload = artifacts_route.radiance_images(
                _request(
                    query_params={
                        "plants_enabled": "true",
                        "fspm_target_ppfd_umol_m2_s": "275",
                        "fspm_target_tolerance_umol_m2_s": "20",
                    }
                ),
                mode=MODE_HPS,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=12,
                target_ppfd=1000,
                hps_coverage_ft=5,
                hps_z_m=1.0668,
                hps_ies_variant="karma",
            )

        self.assertEqual(captured.req.hps_ies_variant, "karma")
        self.assertEqual(captured.req.w_min, 10.0)
        self.assertTrue(captured.req.plants_enabled)
        self.assertEqual(captured.req.fspm_target_ppfd_umol_m2_s, 275.0)
        self.assertEqual(captured.req.fspm_target_tolerance_umol_m2_s, 20.0)
        overlay_query = parse_qs(urlparse(payload["overlay"]).query)
        annot_query = parse_qs(urlparse(payload["annot"]).query)
        self.assertEqual(overlay_query["hps_ies_variant"], ["karma"])
        self.assertEqual(annot_query["hps_ies_variant"], ["karma"])
        self.assertEqual(overlay_query["w_min"], ["10"])
        self.assertEqual(annot_query["w_min"], ["10"])
        self.assertEqual(overlay_query["fspm_target_ppfd_umol_m2_s"], ["275"])
        self.assertEqual(annot_query["fspm_target_tolerance_umol_m2_s"], ["20"])

    def test_image_workspace_lookup_preserves_karma_hps_ies_variant(self) -> None:
        captured = SimpleNamespace(req=None)
        outdir = Path(tempfile.mkdtemp(prefix="rad_rebuild_image_contract_"))
        image = outdir / "ppfd_heatmap_overlay.png"
        image.write_bytes(b"image")

        def fake_authorize(_request: object, req: object) -> Path:
            captured.req = req
            return Path(tempfile.mkdtemp(prefix="rad_rebuild_image_workspace_"))

        with (
            patch.object(
                artifacts_route,
                "authorize_workspace_from_request",
                side_effect=fake_authorize,
            ),
            patch.object(
                artifacts_route,
                "_resolve_output_dir_path",
                return_value=outdir,
            ),
        ):
            response = artifacts_route.radiance_image(
                _request(),
                mode=MODE_HPS,
                name="ppfd_heatmap_overlay.png",
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=12,
                target_ppfd=1000,
                hps_coverage_ft=5,
                hps_z_m=1.0668,
                hps_ies_variant="karma",
            )

        self.assertEqual(Path(response.path), image)
        self.assertEqual(captured.req.hps_ies_variant, "karma")
        self.assertEqual(captured.req.w_min, 10.0)

    def test_images_lazily_generate_visuals_from_precomputed_workspace(self) -> None:
        workspace = Path(tempfile.mkdtemp(prefix="rad_rebuild_lazy_images_workspace_"))
        outdir = workspace / "ppfd_visualizations_hps"
        overlay = outdir / "ppfd_heatmap_overlay.png"
        annot = outdir / "ppfd_heatmap_annotated.png"
        (workspace / "ppfd_map.txt").write_text("0 0 0 100\n", encoding="utf-8")

        def fake_ensure_visuals(
            _req: object, _env: dict[str, str], _workspace_root: Path
        ) -> Path:
            self.assertEqual(_workspace_root, workspace)
            outdir.mkdir(parents=True)
            overlay.write_bytes(b"overlay")
            annot.write_bytes(b"annot")
            return outdir

        with (
            patch.object(
                artifacts_route,
                "authorize_workspace_from_request",
                return_value=workspace,
            ),
            patch.object(
                artifacts_route,
                "_resolve_output_dir_path",
                return_value=outdir,
            ),
            patch.object(artifacts_route, "_env_for_mode", return_value={}),
            patch.object(
                artifacts_route,
                "_ensure_visuals",
                side_effect=fake_ensure_visuals,
            ) as ensure_visuals,
        ):
            payload = artifacts_route.radiance_images(
                _request(),
                mode=MODE_HPS,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
                hps_coverage_ft=4,
                hps_ies_variant="karma",
            )

        ensure_visuals.assert_called_once()
        self.assertIn("ppfd_heatmap_overlay.png", payload["overlay"])
        self.assertIn("ppfd_heatmap_annotated.png", payload["annot"])

    def test_web_proxy_read_timeout_default_allows_live_docker_preflight(self) -> None:
        settings = load_web_settings({})

        self.assertEqual(settings.proxy_read_timeout_s, 300.0)


if __name__ == "__main__":
    unittest.main()
