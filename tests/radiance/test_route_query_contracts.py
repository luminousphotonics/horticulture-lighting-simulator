from __future__ import annotations

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
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_DOCKER, MODE_HPS  # noqa: E402
from rad_rebuild.web.app import load_web_settings  # noqa: E402


class _FakeRequest:
    headers: dict[str, str] = {}

    def __init__(self, session_id: str = "route-contract", method: str = "GET") -> None:
        self.query_params = {"session_id": session_id}
        self.method = method


def _request(session_id: str = "route-contract", method: str = "GET") -> Request:
    return cast(Request, _FakeRequest(session_id, method))


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

        self.assertEqual(Path(response.path), csv_file)
        self.assertEqual(captured.req.hps_ies_variant, "karma")
        self.assertEqual(captured.req.w_min, 10.0)

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

        self.assertEqual(captured.req.hps_ies_variant, "karma")
        self.assertEqual(captured.req.w_min, 10.0)
        overlay_query = parse_qs(urlparse(payload["overlay"]).query)
        annot_query = parse_qs(urlparse(payload["annot"]).query)
        self.assertEqual(overlay_query["hps_ies_variant"], ["karma"])
        self.assertEqual(annot_query["hps_ies_variant"], ["karma"])
        self.assertEqual(overlay_query["w_min"], ["10"])
        self.assertEqual(annot_query["w_min"], ["10"])

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
