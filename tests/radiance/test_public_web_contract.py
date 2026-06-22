from __future__ import annotations

import os
import unittest
from dataclasses import replace
from unittest.mock import patch

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.web.app import app, create_app, load_web_settings  # noqa: E402


class PublicRadianceWebContractTests(unittest.TestCase):
    def test_root_opens_public_simulator_directly(self) -> None:
        with patch.dict(os.environ, {"RAD_REBUILD_SHOW_LIVE_MODES": ""}):
            client = app.test_client()
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("<title>Horticulture Lighting Simulator</title>", html)
        self.assertIn('class="radiance-shell"', html)
        self.assertIn('id="radiance-form"', html)
        self.assertIn('id="btn-rad-all"', html)
        self.assertIn('data-about-open', html)
        self.assertIn('href="https://github.com/luminousphotonics/horticulture-lighting-simulator"', html)
        self.assertIn('id="about-modal"', html)
        self.assertIn("Engineering highlights", html)
        self.assertIn("https://patents.google.com/patent/US10687478B2/en", html)
        self.assertNotIn("Available tools", html)
        self.assertNotIn("Open Layout Generator", html)

    def test_public_simulator_defaults_to_10x10_and_hides_live_modes(self) -> None:
        with patch.dict(os.environ, {"RAD_REBUILD_SHOW_LIVE_MODES": ""}):
            client = app.test_client()
            response = client.get("/radiance-simulator")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn(
            'id="rad-length" type="number" min="10" max="30" step="1" value="10"', html
        )
        self.assertIn(
            'id="rad-width" type="number" min="10" max="30" step="1" value="10"', html
        )
        self.assertIn('id="rad-sim-mode-field" hidden', html)
        self.assertNotIn("Live - Docker", html)
        self.assertNotIn("Live - Local Radiance", html)
        self.assertIn("download_precomputed.py --dataset full", html)
        self.assertIn("15.6 MiB", html)
        self.assertIn('id="btn-rad-assembly"', html)
        self.assertIn(">View 3D Assembly</button>", html)

    def test_retired_layout_generator_page_redirects_to_simulator(self) -> None:
        client = app.test_client()

        for path in ("/layout-generator", "/layout-generator/", "/technology/layout-generator"):
            with self.subTest(path=path):
                response = client.get(path)

                self.assertEqual(response.status_code, 301)
                self.assertEqual(response.headers["Location"], "/radiance-simulator")

    def test_assembly_viewer_shell_route_is_available(self) -> None:
        client = app.test_client()
        response = client.get("/viewer/assembly")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Horticulture Lighting Simulator 3D Assembly Viewer", html)
        self.assertIn("js/assembly-viewer/main.js", html)

    def test_dev_env_flag_shows_live_execution_modes(self) -> None:
        with patch.dict(os.environ, {"RAD_REBUILD_SHOW_LIVE_MODES": "1"}):
            client = app.test_client()
            response = client.get("/radiance-simulator")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="rad-sim-mode-field"', html)
        self.assertNotIn('id="rad-sim-mode-field" hidden', html)
        self.assertIn('value="precomputed" selected>Precomputed', html)
        self.assertIn('value="live_docker"', html)
        self.assertIn("Live - Docker", html)
        self.assertIn('value="live_local"', html)
        self.assertIn("Live - Local Radiance", html)

    def test_production_mode_hides_live_modes_even_when_flag_is_set(self) -> None:
        with patch.dict(
            os.environ,
            {
                "RAD_REBUILD_DEPLOYMENT_MODE": "production",
                "RAD_REBUILD_SHOW_LIVE_MODES": "1",
            },
        ):
            client = app.test_client()
            response = client.get("/radiance-simulator")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn('id="rad-sim-mode-field" hidden', html)
        self.assertNotIn("Live - Docker", html)
        self.assertNotIn("Live - Local Radiance", html)

    def test_healthz_endpoint_is_lightweight_json(self) -> None:
        client = app.test_client()
        response = client.get("/healthz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"service": "rad-rebuild-web", "status": "ok"})

    def test_production_static_cache_headers_preserve_dev_reload(self) -> None:
        client = app.test_client()
        dev_response = client.get("/static/css/site.css")
        self.assertEqual(dev_response.headers["Cache-Control"], "no-cache")

        with patch.dict(os.environ, {"RAD_REBUILD_DEPLOYMENT_MODE": "production"}):
            css_response = client.get("/static/css/site.css")
            vendor_response = client.get("/static/vendor/three/three.module.js")
            glb_response = client.get(
                "/static/viewer/proposed_led_system/fixture_centerpiece.proxy.glb"
            )

        self.assertEqual(css_response.headers["Cache-Control"], "public, max-age=3600")
        self.assertEqual(
            vendor_response.headers["Cache-Control"],
            "public, max-age=31536000, immutable",
        )
        self.assertEqual(
            glb_response.headers["Cache-Control"],
            "public, max-age=31536000, immutable",
        )

    def test_frame_ancestors_are_restricted_to_configured_embed_origin(self) -> None:
        settings = replace(
            load_web_settings({"RAD_REBUILD_FRAME_ANCESTORS": "https://luminousphotonics.com"}),
            trusted_hosts=(),
        )
        client = create_app(config={"WEB_SETTINGS": settings}).test_client()

        viewer_response = client.get("/viewer/assembly")
        page_response = client.get("/radiance-simulator")

        self.assertIn(
            "frame-ancestors 'self' https://luminousphotonics.com",
            viewer_response.headers["Content-Security-Policy"],
        )
        self.assertIn(
            "frame-ancestors 'none'",
            page_response.headers["Content-Security-Policy"],
        )


if __name__ == "__main__":
    unittest.main()
