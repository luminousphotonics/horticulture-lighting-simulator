from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
from types import SimpleNamespace
import venv
import zipfile

import httpx
import pytest

from fspm_optics.application.domain import (
    ConventionalRunRequest,
    HpsRunRequest,
    ProposedRunRequest,
)
from fspm_optics.web.app import create_app
from fspm_optics.web.mode import ApplicationMode
from fspm_optics.web.public import _public_index, create_public_app


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRECOMPUTED_ROOT = REPOSITORY_ROOT / "precomputed"
PUBLIC_TITLE = "Horticulture Lighting Simulator"
PUBLIC_DESCRIPTION = (
    "Radiance-based horticultural lighting simulation and 3D visualization engine."
)
PUBLIC_URL = "https://sim.luminousphotonics.com/"
PUBLIC_OG_IMAGE_URL = f"{PUBLIC_URL}static/og-image.png"


def _metric_card_opening_tag(html: str, metric: str) -> str:
    metric_position = html.index(f'data-metric="{metric}"')
    start = html.rfind("<div", 0, metric_position)
    return html[start : html.index(">", start) + 1]


async def _wait_for_playback(
    client: httpx.AsyncClient, response: httpx.Response
) -> dict[str, object]:
    assert response.status_code in {200, 202}
    body = response.json()
    for _attempt in range(1000):
        if body["state"] in {"completed", "failed", "expired"}:
            return body
        await asyncio.sleep(0.01)
        status = await client.get(body["status_url"])
        assert status.status_code == 200
        body = status.json()
    raise AssertionError("playback request did not reach a terminal state")


def _live_payload(system: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "system": system,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "quality": "direct",
        "analysis_scope": "baseline_ppfd",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 75.0,
        "mounting_height_in": 24.0 if system == "hps" else 18.0,
        "aisle_mode": False,
    }
    if system != "hps":
        payload["target_ppfd"] = 250.0
        payload["lighting_target_mode"] = "mean_target"
    if system == "conventional":
        payload["layout_mode"] = "practical"
    return payload


def test_public_and_live_metrics_share_the_public_release_presentation() -> None:
    public_html = _public_index().decode("utf-8")
    live_html = (
        REPOSITORY_ROOT / "src/fspm_optics/resources/web/index.html"
    ).read_text(encoding="utf-8")
    public_javascript = (
        REPOSITORY_ROOT / "src/fspm_optics/resources/web/public-app.js"
    ).read_text(encoding="utf-8")
    result_renderer = (
        REPOSITORY_ROOT / "src/fspm_optics/resources/web/result-view.js"
    ).read_text(encoding="utf-8")

    live_result = live_html.split("<!-- SHARED_RESULT_VIEW_START -->", 1)[1].split(
        "<!-- SHARED_RESULT_VIEW_END -->", 1
    )[0].strip()
    assert live_result in public_html

    for label in (
        "Modularized LED control mode",
        "Module arrangement",
        "Modularized LED source mode",
    ):
        assert label not in public_html
        assert label not in live_result
    for metric in (
        "proposed-control-mode",
        "proposed-ring-mode",
        "proposed-source-mode",
    ):
        assert f'data-metric="{metric}"' not in public_html
        assert f'data-metric="{metric}"' not in live_result

    for html in (public_html, live_html):
        assert _metric_card_opening_tag(html, "spectral-basis") == (
            '<div class="spectral-control-result" data-counterfactual="false">'
        )
        assert _metric_card_opening_tag(html, "modules") == "<div>"
        assert "Fixtures / modules / zones" not in html
        assert 'data-metric="counts"' not in html
    for metric in (
        "fspm",
        "baseline-leaf-cv",
        "fspm-fr-absorbed-density",
    ):
        assert 'class="wide"' in _metric_card_opening_tag(public_html, metric)
        assert 'class="wide"' in _metric_card_opening_tag(live_html, metric)

    for html in (public_html, live_html):
        assert "Area-weighted modeled plant surfaces</span>" in html
        assert "separate from spatial uniformity" not in html
        assert "Crop-total photon capture · plant spatial consistency</span>" in html
        assert "leaf organ-scale variability" not in html

    assert "createResultView()" in public_javascript
    assert "export function createResultView()" in result_renderer
    assert "Matched with Conventional LED System's SPD" in result_renderer
    assert "publicPresentation" not in result_renderer
    assert 'setMetric("modules", String(metrics.counts.modules))' in result_renderer


def test_public_app_has_only_playback_routes_and_no_runtime_or_worker(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime-that-must-not-exist"
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)
    paths = {route.path for route in app.routes}

    assert app.state.application_mode is ApplicationMode.PUBLIC_PRECOMPUTED
    assert not hasattr(app.state, "jobs")
    assert not hasattr(app.state, "workspaces")
    assert not hasattr(app.state, "shutdown_runtime")
    assert "/api/runs" not in paths
    assert not any(path.startswith("/api/jobs") for path in paths)
    assert not any(path.startswith("/api/runs/") for path in paths)
    assert paths == {
        "/",
        "/static/{asset_name}",
        "/health/live",
        "/health/ready",
        "/api/capabilities",
        "/api/precomputed/availability",
        "/api/precomputed/playbacks",
        "/api/precomputed/playback-requests/{request_id}",
        "/api/precomputed/playbacks/{playback_id}/metrics",
        "/api/precomputed/playbacks/{playback_id}/manifest",
        "/api/precomputed/playbacks/{playback_id}/artifacts/{artifact_name}",
        "/precomputed/{playback_id}/viewer/{viewer_path:path}",
        "/precomputed/{playback_id}/scatter/{scatter_path:path}",
    }
    assert not runtime.exists()

    isolated_import = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import fspm_optics.web.__main__; "
                "assert 'fspm_optics.web.jobs' not in sys.modules; "
                "assert 'fspm_optics.web.workspaces' not in sys.modules"
            ),
        ],
        check=True,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    assert isolated_import.stderr == ""


def test_public_frontend_and_crafted_live_requests_fail_closed() -> None:
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
                cookies={"execution_mode": "live"},
            ) as client,
        ):
            index = await client.get("/")
            assert index.status_code == 200
            assert "Execution Mode" not in index.text
            assert 'id="execution-mode"' not in index.text
            assert "/static/public-app.js" in index.text
            assert "/static/public-app.js?v=public-ui-v2" in index.text
            assert "/static/styles.css?v=public-ui-v2" in index.text
            assert "/static/app.js" not in index.text
            assert "HORTICULTURE LIGHTING SIMULATOR" in index.text
            assert 'href="https://luminousphotonics.com/"' in index.text
            assert 'src="/static/homeleaf.png"' in index.text
            assert 'alt="Luminous Photonics home"' in index.text
            assert f"<title>{PUBLIC_TITLE}</title>" in index.text
            assert (
                f'<meta name="description" content="{PUBLIC_DESCRIPTION}">'
                in index.text
            )
            assert f'<link rel="canonical" href="{PUBLIC_URL}">' in index.text
            for property_name, content in (
                ("og:type", "website"),
                ("og:url", PUBLIC_URL),
                ("og:title", PUBLIC_TITLE),
                ("og:description", PUBLIC_DESCRIPTION),
                ("og:image", PUBLIC_OG_IMAGE_URL),
                ("og:image:secure_url", PUBLIC_OG_IMAGE_URL),
                ("og:image:type", "image/png"),
                ("og:image:width", "1200"),
                ("og:image:height", "630"),
                (
                    "og:image:alt",
                    "Luminous Photonics Horticulture Lighting Simulator",
                ),
            ):
                assert (
                    f'<meta property="{property_name}" content="{content}">'
                    in index.text
                )
            for name, content in (
                ("twitter:card", "summary_large_image"),
                ("twitter:title", PUBLIC_TITLE),
                ("twitter:description", PUBLIC_DESCRIPTION),
                ("twitter:image", PUBLIC_OG_IMAGE_URL),
                (
                    "twitter:image:alt",
                    "Luminous Photonics Horticulture Lighting Simulator",
                ),
                ("theme-color", "#0b0f14"),
                ("robots", "index,follow"),
            ):
                assert f'<meta name="{name}" content="{content}">' in index.text
            assert "Lighting Target Policy" in index.text
            form = index.text.split('<form id="run-form">', 1)[1].split(
                "</form>", 1
            )[0]
            assert re.findall(
                r'<(?:input|select|button)\b[^>]*\bid="([^"]+)"', form
            ) == [
                "system",
                "layout-mode",
                "lighting-target-mode",
                "target-ppfd",
                "fspm-tolerance",
                "room-size",
                "aisle-mode",
                "run-button",
            ]
            assert "Target mean PPFD" in form
            assert "Maximum PPFD cap" not in form
            assert "FSPM Reference Tolerance" in form
            assert "± µmol/m²/s" in form
            assert re.search(
                r'<input id="fspm-tolerance" '
                r'name="fspm_target_tolerance" type="number" '
                r'min="0\.000001" step="any" value="75" required disabled>',
                form,
            )
            assert [
                label
                for label in ("10' × 10'", "15' × 30'", "30' × 50'")
                if label in form
            ] == ["10' × 10'", "15' × 30'", "30' × 50'"]
            for excluded in (
                "Execution Mode",
                "Module arrangement",
                "Basis-Matrix",
                "COB Mode",
                "Analysis Scope",
                "spectral basis",
                "far-red",
                "Mounting height",
                "Room length",
                "Room width",
                "Radiance quality",
                "FSPM Reference PPFD",
            ):
                assert excluded not in form
            assert "System default" not in form
            assert "Rolling Bench uses a centered rigid 48 in fixture pitch" not in form
            assert "Modularized LED System" in form
            assert "1000W HPS System" in form
            assert "Proposed LED System" not in form
            assert ">HPS System</option>" not in form
            assert ">Run Simulation<" in form
            assert "Load Authenticated Result" not in form
            assert form.index("Rolling Bench") < form.index(
                "Practical Coverage"
            )

            public_javascript = await client.get("/static/public-app.js")
            assert public_javascript.status_code == 200
            public_styles = await client.get("/static/styles.css")
            assert public_styles.status_code == 200
            assert (
                "input:focus, input:focus-visible { border-color: "
                "var(--color-focus); box-shadow: none; outline: none; }"
                in public_styles.text
            )
            assert 'from "./result-view.js"' in public_javascript.text
            assert 'from "./system-labels.js"' in public_javascript.text
            assert 'from "./app.js"' not in public_javascript.text
            assert "sessionStorage" in public_javascript.text
            assert "Idempotency-Key" in public_javascript.text
            assert "Preparing playback…" in public_javascript.text
            assert "response.status === 429 || response.status === 503" in (
                public_javascript.text
            )
            assert "response.status === 404" in public_javascript.text
            assert "1.45 ** Math.min(attempt, 8)" in public_javascript.text
            assert "Math.random()" in public_javascript.text
            assert "if (playbackPending) return" in public_javascript.text
            assert (
                "fspm_target_tolerance: Number(toleranceInput.value)"
                in public_javascript.text
            )
            assert (
                "toleranceInput.value = String("
                "saved.selector.fspm_target_tolerance)"
                in public_javascript.text
            )
            assert "toleranceInput.disabled = true" in public_javascript.text
            assert (
                "capabilities.fspm_target_tolerance" in public_javascript.text
            )
            assert (await client.get("/static/app.js")).status_code == 404
            labels = await client.get("/static/system-labels.js")
            assert labels.status_code == 200
            assert 'proposed: "Modularized LED System"' in labels.text
            assert 'hps: "1000W HPS System"' in labels.text
            logo = await client.get("/static/homeleaf.png")
            assert logo.status_code == 200
            assert logo.headers["content-type"] == "image/png"
            assert len(logo.content) == 131_902
            assert hashlib.sha256(logo.content).hexdigest() == (
                "f34e4905a9df5b122de1350a0310fab193e73ff213afb41252b8789230b46212"
            )
            social_image = await client.get("/static/og-image.png")
            assert social_image.status_code == 200
            assert social_image.headers["content-type"] == "image/png"
            assert social_image.headers["cache-control"] == (
                "public, max-age=0, must-revalidate"
            )
            assert social_image.headers["etag"]
            assert social_image.content == _web_asset("og-image.png")

            capabilities = (await client.get("/api/capabilities")).json()
            assert capabilities == {
                "application_mode": "public_precomputed",
                "precomputed_playback": {
                    "enabled": True,
                    "systems": ["proposed", "conventional", "hps"],
                    "fspm_target_tolerance": {
                        "enabled": True,
                        "supported_systems": [
                            "proposed",
                            "conventional",
                            "hps",
                        ],
                        "type": "number",
                        "default": 75.0,
                        "finite": True,
                        "strictly_positive": True,
                    },
                    "lighting_target_modes": {
                        "proposed": ["mean_target", "target_capped"],
                        "conventional": ["mean_target", "target_capped"],
                        "hps": [],
                    },
                    "fixture_layout_modes": {
                        "proposed": [],
                        "conventional": ["rolling_bench", "practical"],
                        "hps": [],
                    },
                },
                "live_simulation": {"enabled": False, "systems": []},
            }
            for request in (
                client.post("/api/runs", json=_live_payload("proposed")),
                client.post("/api/jobs/crafted", json={}),
                client.post("/api/runs?live=1", json=_live_payload("proposed")),
            ):
                response = await request
                assert response.status_code in {404, 405}

            queue_before_invalid = (await client.get("/health/ready")).json()[
                "playback_queue"
            ]
            crafted = await client.post(
                "/api/precomputed/playbacks",
                json={
                    "system": "proposed",
                    "room_length_ft": 10.0,
                    "room_width_ft": 10.0,
                    "aisle_mode": False,
                    "target_ppfd": 250.0,
                    "execution_mode": "live",
                },
            )
            assert crafted.status_code == 422
            assert crafted.json()["error"]["code"] == "invalid_precomputed_selector"
            queue_after_invalid = (await client.get("/health/ready")).json()[
                "playback_queue"
            ]
            assert queue_after_invalid == queue_before_invalid

            capped = await client.post(
                "/api/precomputed/playbacks",
                json={
                    "system": "proposed",
                    "room_length_ft": 10.0,
                    "room_width_ft": 10.0,
                    "aisle_mode": False,
                    "target_ppfd": 100.0,
                    "lighting_target_mode": "target_capped",
                    "fspm_target_tolerance": 22.5,
                },
                headers={"Idempotency-Key": "public-capped-0001"},
            )
            assert capped.status_code == 202
            assert capped.json()["state"] == "queued"
            capped_body = await _wait_for_playback(client, capped)
            assert capped_body["state"] == "completed"
            assert capped_body["result"]["lighting_target_mode"] == (
                "target_capped"
            )
            assert capped_body["result"]["fspm_target_tolerance"] == 22.5
            repeated = await client.post(
                "/api/precomputed/playbacks",
                json={
                    "system": "proposed",
                    "room_length_ft": 10.0,
                    "room_width_ft": 10.0,
                    "aisle_mode": False,
                    "target_ppfd": 100.0,
                    "lighting_target_mode": "target_capped",
                    "fspm_target_tolerance": 22.5,
                },
                headers={"Idempotency-Key": "public-capped-0001"},
            )
            assert repeated.status_code == 200
            assert repeated.json() == capped_body
            conflict = await client.post(
                "/api/precomputed/playbacks",
                json={
                    "system": "proposed",
                    "room_length_ft": 10.0,
                    "room_width_ft": 10.0,
                    "aisle_mode": False,
                    "target_ppfd": 101.0,
                    "lighting_target_mode": "target_capped",
                    "fspm_target_tolerance": 22.5,
                },
                headers={"Idempotency-Key": "public-capped-0001"},
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == (
                "idempotency_key_conflict"
            )
            cached = await client.post(
                "/api/precomputed/playbacks",
                json={
                    "system": "proposed",
                    "room_length_ft": 10.0,
                    "room_width_ft": 10.0,
                    "aisle_mode": False,
                    "target_ppfd": 100.0,
                    "lighting_target_mode": "target_capped",
                    "fspm_target_tolerance": 22.5,
                },
                headers={"Idempotency-Key": "public-capped-cache"},
            )
            assert cached.status_code == 200
            assert cached.json()["state"] == "completed"
            missing = await client.get(
                "/api/precomputed/playback-requests/" + "f" * 32
            )
            assert missing.status_code == 404
            assert missing.headers["cache-control"] == "no-store"

    asyncio.run(scenario())


def test_public_health_security_limits_conditionals_and_ranges() -> None:
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            assert (await client.get("/health/live")).json() == {
                "status": "alive"
            }
            assert (await client.get("/health/ready")).status_code == 503

        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                ready = await client.get(
                    "/health/ready",
                    headers={"x-request-id": "request-12345678"},
                )
                assert ready.status_code == 200
                assert ready.json()["catalog_cases"] == 24
                assert ready.headers["x-request-id"] == "request-12345678"
                assert ready.headers["x-content-type-options"] == "nosniff"
                assert ready.headers["x-frame-options"] == "DENY"
                assert "frame-ancestors 'none'" in ready.headers[
                    "content-security-policy"
                ]

                untrusted = await client.get(
                    "/", headers={"host": "untrusted.invalid"}
                )
                assert untrusted.status_code == 400
                assert untrusted.headers["x-content-type-options"] == "nosniff"

                wrong_media = await client.post(
                    "/api/precomputed/playbacks",
                    content=b"{}",
                    headers={"content-type": "text/plain"},
                )
                assert wrong_media.status_code == 415
                assert wrong_media.json()["error"]["code"] == (
                    "unsupported_media_type"
                )
                oversized = await client.post(
                    "/api/precomputed/playbacks",
                    content=b"x" * (64 * 1024 + 1),
                    headers={"content-type": "application/json"},
                )
                assert oversized.status_code == 413

                asset = await client.get("/static/homeleaf.png")
                assert asset.status_code == 200
                assert asset.headers["accept-ranges"] == "bytes"
                assert asset.headers["cache-control"] == (
                    "public, max-age=0, must-revalidate"
                )
                conditional = await client.get(
                    "/static/homeleaf.png",
                    headers={"if-none-match": asset.headers["etag"]},
                )
                assert conditional.status_code == 304
                assert conditional.content == b""
                partial = await client.get(
                    "/static/homeleaf.png", headers={"range": "bytes=0-99"}
                )
                assert partial.status_code == 206
                assert partial.headers["content-range"].endswith("/131902")
                assert len(partial.content) == 100

    asyncio.run(scenario())


def test_live_app_advertises_and_accepts_all_three_live_systems(
    tmp_path: Path,
) -> None:
    native_calls: list[dict[str, object]] = []

    def recording_native(**kwargs: object):
        native_calls.append(kwargs)
        raise RuntimeError("recording executor stops before native execution")

    app = create_app(
        runtime_root=tmp_path / "live-runtime",
        precomputed_root=PRECOMPUTED_ROOT,
        run_executor=recording_native,
    )

    bundles = sorted((PRECOMPUTED_ROOT / "conventional").rglob("*.fspm-compact"))
    assert bundles
    with zipfile.ZipFile(bundles[0]) as archive:
        historical_manifest = json.loads(archive.read("manifest.json"))
    historical_selector = historical_manifest["authenticated_identities"][
        "fixed_plan_inputs"
    ]["compatibility_inputs"]["source"]["profile_id"]
    assert historical_selector != "conventional"
    hps_bundles = sorted((PRECOMPUTED_ROOT / "hps").rglob("*.fspm-compact"))
    assert hps_bundles
    with zipfile.ZipFile(hps_bundles[0]) as archive:
        historical_hps_selector = json.loads(archive.read("manifest.json"))[
            "system_id"
        ]
    assert historical_hps_selector != "hps"

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                index = await client.get("/")
                assert "HORTICULTURE LIGHTING SIMULATOR" in index.text
                assert 'src="/static/homeleaf.png"' in index.text
                assert "Load Authenticated Result" not in index.text
                logo = await client.get("/static/homeleaf.png")
                assert logo.status_code == 200
                assert hashlib.sha256(logo.content).hexdigest() == (
                    "f34e4905a9df5b122de1350a0310fab193e73ff213afb41252b8789230b46212"
                )
                capabilities = (await client.get("/api/capabilities")).json()
                assert capabilities["application_mode"] == "trusted_local_live"
                assert capabilities["live_simulation"] == {
                    "enabled": True,
                    "systems": ["proposed", "conventional", "hps"],
                }
                assert capabilities["precomputed_playback"]["systems"] == [
                    "proposed",
                    "conventional",
                    "hps",
                ]
                assert (
                    '<option value="conventional">Conventional LED System</option>'
                    in index.text
                )
                assert (
                    '<option value="hps">1000W HPS System</option>' in index.text
                )
                live_controller = await client.get("/static/app.js")
                assert live_controller.status_code == 200
                assert "applicationCapabilities.live_simulation.systems" in (
                    live_controller.text
                )
                assert (
                    "option.disabled = "
                    "!advertisedSystems.includes(option.value);"
                ) in live_controller.text

                payloads = (
                    _live_payload("conventional")
                    | {
                        "target_ppfd": 321.5,
                        "mounting_height_in": 23.5,
                        "fspm_target_tolerance": 12.5,
                        "layout_mode": "practical",
                        "lighting_target_mode": "mean_target",
                    },
                    _live_payload("conventional")
                    | {
                        "target_ppfd": 654.25,
                        "room_length_ft": 30.0,
                        "room_width_ft": 15.0,
                        "mounting_height_in": 31.25,
                        "quality": "standard",
                        "analysis_scope": "baseline_plus_multispectral_fspm",
                        "include_far_red": True,
                        "fspm_target_mode": "override",
                        "fspm_target_ppfd": 411.0,
                        "fspm_target_tolerance": 33.25,
                        "aisle_mode": True,
                        "layout_mode": "rolling_bench",
                        "lighting_target_mode": "target_capped",
                    },
                )
                for expected_calls, payload in enumerate(payloads, start=1):
                    response = await client.post(
                        "/api/runs",
                        json=payload,
                    )
                    assert response.status_code == 202
                    body = response.json()
                    assert body["analysis_scope"] == payload["analysis_scope"]
                    assert body["lighting_target_mode"] == (
                        payload["lighting_target_mode"]
                    )
                    for _attempt in range(200):
                        status = (await client.get(body["job_url"])).json()
                        if status["terminal"]:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError("recording live job did not terminate")
                    assert status["state"] == "failed"
                    assert len(native_calls) == expected_calls

                recorded = [call["request"] for call in native_calls]
                assert all(
                    isinstance(item, ConventionalRunRequest) for item in recorded
                )
                assert [item.layout_mode for item in recorded] == [
                    "practical",
                    "rolling_bench",
                ]
                assert [item.target_ppfd_umol_m2_s for item in recorded] == [
                    321.5,
                    654.25,
                ]
                assert [item.mounting_height_in for item in recorded] == [
                    23.5,
                    31.25,
                ]
                assert [item.analysis_scope.value for item in recorded] == [
                    "baseline_ppfd",
                    "baseline_plus_multispectral_fspm",
                ]
                assert [item.fspm_target_mode for item in recorded] == [
                    "automatic",
                    "override",
                ]
                assert [
                    item.fspm_target_override_umol_m2_s for item in recorded
                ] == [None, 411.0]
                assert [
                    item.fspm_target_tolerance_umol_m2_s for item in recorded
                ] == [12.5, 33.25]
                assert [item.lighting_target_mode.value for item in recorded] == [
                    "mean_target",
                    "target_capped",
                ]
                assert [item.aisle_mode for item in recorded] == [False, True]
                assert recorded[1].include_far_red is True

                proposed = await client.post(
                    "/api/runs", json=_live_payload("proposed")
                )
                assert proposed.status_code == 202
                proposed_body = proposed.json()
                for _attempt in range(200):
                    proposed_status = (
                        await client.get(proposed_body["job_url"])
                    ).json()
                    if proposed_status["terminal"]:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("recording Proposed job did not terminate")
                assert proposed_status["state"] == "failed"
                assert len(native_calls) == 3
                assert isinstance(native_calls[2]["request"], ProposedRunRequest)
                assert native_calls[2]["request"].system == "proposed"

                hps_payload = _live_payload("hps") | {
                    "room_length_ft": 30.0,
                    "room_width_ft": 15.0,
                    "mounting_height_in": 36.5,
                    "quality": "standard",
                    "analysis_scope": "baseline_plus_multispectral_fspm",
                    "include_far_red": True,
                    "fspm_target_mode": "override",
                    "fspm_target_ppfd": 612.75,
                    "fspm_target_tolerance": 27.5,
                    "aisle_mode": True,
                }
                hps = await client.post("/api/runs", json=hps_payload)
                assert hps.status_code == 202
                hps_body = hps.json()
                assert hps_body["analysis_scope"] == hps_payload["analysis_scope"]
                assert hps_body["lighting_target_mode"] is None
                for _attempt in range(200):
                    hps_status = (await client.get(hps_body["job_url"])).json()
                    if hps_status["terminal"]:
                        break
                    await asyncio.sleep(0.01)
                else:
                    raise AssertionError("recording HPS job did not terminate")
                assert hps_status["state"] == "failed"
                assert any(
                    "Native 1000W HPS System run started." in item["line"]
                    for item in hps_status["logs"]
                )
                assert len(native_calls) == 4
                hps_request = native_calls[3]["request"]
                assert isinstance(hps_request, HpsRunRequest)
                assert hps_request.system == "hps"
                assert (hps_request.room_length_ft, hps_request.room_width_ft) == (
                    30.0,
                    15.0,
                )
                assert hps_request.quality == "standard"
                assert hps_request.mounting_height_in == 36.5
                assert hps_request.analysis_scope.value == (
                    "baseline_plus_multispectral_fspm"
                )
                assert hps_request.include_far_red is True
                assert hps_request.aisle_mode is True
                assert hps_request.fspm_target_mode == "override"
                assert hps_request.fspm_target_override_umol_m2_s == 612.75
                assert hps_request.fspm_target_tolerance_umol_m2_s == 27.5
                assert not hasattr(hps_request, "target_ppfd_umol_m2_s")
                assert not hasattr(hps_request, "lighting_target_mode")
                assert not hasattr(hps_request, "layout_mode")
                assert "target_ppfd_umol_m2_s" not in hps_request.to_dict()
                assert "lighting_target_mode" not in hps_request.to_dict()
                assert "layout_mode" not in hps_request.to_dict()
                assert sum(
                    isinstance(call["request"], HpsRunRequest)
                    for call in native_calls
                ) == 1

                invalid_payloads = (
                    payloads[0] | {"layout_mode": "invalid-layout"},
                    payloads[0] | {"fspm_target_tolerance": 0.0},
                )
                for payload in invalid_payloads:
                    response = await client.post("/api/runs", json=payload)
                    assert response.status_code == 422

                malformed_hps_payloads = (
                    hps_payload | {"target_ppfd": 250.0},
                    hps_payload | {"lighting_target_mode": "mean_target"},
                    hps_payload | {"layout_mode": "practical"},
                    hps_payload | {"fspm_target_tolerance": 0.0},
                )
                for payload in malformed_hps_payloads:
                    response = await client.post("/api/runs", json=payload)
                    assert response.status_code == 422

                for selector in (
                    historical_selector,
                    historical_hps_selector,
                    "HPS",
                ):
                    rejected = await client.post(
                        "/api/runs",
                        json=hps_payload | {"system": selector},
                    )
                    assert rejected.status_code == 422
                    assert rejected.json()["error"]["field"] == "system"
                assert len(native_calls) == 4

    asyncio.run(scenario())
    assert len(native_calls) == 4


def test_all_24_authenticated_bundles_are_publicly_cataloged_and_loadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSPM_PLAYBACK_CACHE_SIZE", "24")
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)
    catalog = app.state.precomputed
    availability = catalog.availability()

    assert availability["plan_identity_sha256"] == (
        "114d6171d46a12284c9c7c3de8fae776851dbd7cb806be55b64aa1053d693416"
    )
    assert availability["case_count"] == 24
    assert availability["valid_case_count"] == 24
    assert availability["missing_case_count"] == 0
    assert availability["invalid_case_count"] == 0
    assert {case["system"] for case in availability["cases"]} == {
        "proposed",
        "conventional",
        "hps",
    }

    identities = set()
    for case in availability["cases"]:
        room = case["supported_room_orders_ft"][0]
        selector = {
            "system": case["system"],
            "room_length_ft": room["length"],
            "room_width_ft": room["width"],
            "aisle_mode": case["aisle_mode"],
        }
        if case["system"] == "conventional":
            selector["layout_mode"] = case["layout"]
        if case["system"] != "hps":
            selector["target_ppfd"] = 250.0
        playback = catalog.load(selector)
        identities.add(playback.playback.canonical_bundle_identity_sha256)
        assert playback.result_payload()["source_runtime_required"] is False
    assert len(identities) == 24


def test_public_playback_queue_is_bounded_and_status_stays_responsive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSPM_MAX_CONCURRENT_WORK", "1")
    monkeypatch.setenv("FSPM_PLAYBACK_ACTIVE_LIMIT", "1")
    monkeypatch.setenv("FSPM_PLAYBACK_WAITING_LIMIT", "1")
    monkeypatch.setenv("FSPM_PLAYBACK_RECORD_LIMIT", "4")
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)
    start_order: list[float] = []

    def blocking_load(selector, *, initial_pin=True):
        start_order.append(float(selector["target_ppfd"]))
        time.sleep(0.2)
        return SimpleNamespace(
            result_payload=lambda: {
                "run_id": "a" * 32,
                "case_id": "test-case",
                "execution_mode": "precomputed",
                "source_runtime_required": False,
            }
        )

    monkeypatch.setattr(app.state.precomputed, "load", blocking_load)
    first_selector = {
        "system": "proposed",
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "aisle_mode": False,
        "target_ppfd": 250.0,
    }

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                first = await client.post(
                    "/api/precomputed/playbacks",
                    json=first_selector,
                    headers={"Idempotency-Key": "bounded-queue-0001"},
                )
                assert first.status_code == 202
                second = await client.post(
                    "/api/precomputed/playbacks",
                    json=first_selector | {"target_ppfd": 125.0},
                    headers={"Idempotency-Key": "bounded-queue-0002"},
                )
                assert second.status_code == 202
                refused = await client.post(
                    "/api/precomputed/playbacks",
                    json=first_selector | {"target_ppfd": 130.0},
                    headers={"Idempotency-Key": "bounded-queue-0003"},
                )
                assert refused.status_code == 503
                assert refused.headers["retry-after"] == "2"
                assert refused.json()["error"]["code"] == (
                    "playback_queue_full"
                )
                await asyncio.sleep(0)
                running = await client.get(first.json()["status_url"])
                queued = await client.get(second.json()["status_url"])
                assert running.json()["state"] == "running"
                assert queued.json()["state"] == "queued"
                health = await client.get("/health/ready")
                assert health.status_code == 200
                assert health.json()["playback_queue"]["running"] == 1
                assert health.json()["playback_queue"]["queued"] == 1
                assert (await _wait_for_playback(client, running))["state"] == (
                    "completed"
                )
                assert (await _wait_for_playback(client, queued))["state"] == (
                    "completed"
                )
                assert start_order == [250.0, 125.0]

    asyncio.run(scenario())


def test_cli_parser_help_invalid_arguments_and_mode_defaults(capsys) -> None:
    from fspm_optics.web.__main__ import build_parser, main

    default = build_parser().parse_args([])
    assert default.live is False
    assert default.host == "127.0.0.1"
    assert default.port == 8895
    assert build_parser().parse_args(["--live"]).live is True

    with pytest.raises(SystemExit) as help_exit:
        build_parser().parse_args(["--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--live" in help_text
    assert "authenticated precomputed playback by default" in help_text

    for arguments in (["--unknown"], ["--port", "0"], ["--port", "nope"]):
        with pytest.raises(SystemExit) as invalid:
            build_parser().parse_args(arguments)
        assert invalid.value.code == 2
    with pytest.raises(SystemExit) as unsafe_runtime:
        main(["--runtime-root", "/tmp/public-must-not-create-runtime"])
    assert unsafe_runtime.value.code == 2


def test_default_cli_constructs_only_the_public_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fspm_optics.web import __main__ as cli

    captured: dict[str, object] = {}
    public_app = SimpleNamespace()
    monkeypatch.setattr(
        cli,
        "create_public_app",
        lambda **kwargs: captured.update(kwargs) or public_app,
    )
    monkeypatch.setattr(
        cli.uvicorn,
        "run",
        lambda app, **kwargs: captured.update({"app": app, **kwargs}),
    )

    cli.main([])

    assert captured == {
        "precomputed_root": None,
        "app": public_app,
        "host": "127.0.0.1",
        "port": 8895,
        "workers": 1,
        "access_log": False,
        "proxy_headers": True,
        "forwarded_allow_ips": "127.0.0.1",
        "server_header": False,
    }


def test_live_ui_enables_exactly_the_capability_advertised_systems() -> None:
    javascript = (
        REPOSITORY_ROOT / "src/fspm_optics/resources/web/app.js"
    ).read_text(encoding="utf-8")
    assert "applicationCapabilities.live_simulation.systems" in javascript
    assert "advertisedSystems.includes(option.value)" in javascript
    assert "lighting_target_mode: payload.lighting_target_mode" in javascript
    assert "lightingTargetModeSelect.value = \"mean_target\"" not in javascript
    assert "advertisedSystems[0]" in javascript
    assert 'systemSelect.value === "conventional"' in javascript
    assert 'const fixedOutput = systemSelect.value === "hps"' in javascript
    assert 'if (systemSelect.value !== "hps")' in javascript


def test_public_asset_graph_is_small_and_excludes_live_controller() -> None:
    from fspm_optics.web.public import STATIC_ASSETS, _public_index, _web_resource

    assert "app.js" not in STATIC_ASSETS
    assert set(STATIC_ASSETS) == {
        "homeleaf.png",
        "og-image.png",
        "styles.css",
        "public-app.js",
        "result-view.js",
        "result-contracts.js",
        "system-labels.js",
    }
    executable_bytes = len(_public_index()) + sum(
        len(_web_resource(name)) for name in STATIC_ASSETS
        if not name.endswith(".png")
    )
    assert executable_bytes < 100_000
    logo = _web_resource("homeleaf.png")
    assert len(logo) == 131_902
    assert hashlib.sha256(logo).hexdigest() == (
        "f34e4905a9df5b122de1350a0310fab193e73ff213afb41252b8789230b46212"
    )


def _web_asset(name: str) -> bytes:
    return (
        REPOSITORY_ROOT / "src" / "fspm_optics" / "resources" / "web" / name
    ).read_bytes()


def test_social_preview_image_is_web_safe_opaque_png() -> None:
    image = _web_asset("og-image.png")
    assert image.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, bit_depth, color_type = struct.unpack(">IIBB", image[16:26])
    assert (width, height) == (1200, 630)
    assert (bit_depth, color_type) == (8, 2)
    assert len(image) < 1_000_000

    chunk_types: list[bytes] = []
    position = 8
    while position < len(image):
        chunk_length = struct.unpack(">I", image[position : position + 4])[0]
        chunk_type = image[position + 4 : position + 8]
        chunk_types.append(chunk_type)
        position += 12 + chunk_length
        if chunk_type == b"IEND":
            break
    assert chunk_types[-1] == b"IEND"
    assert b"tRNS" not in chunk_types
    assert b"iCCP" not in chunk_types


def test_console_script_installs_editably_in_temporary_environment(
    tmp_path: Path,
) -> None:
    environment = tmp_path / "editable-environment"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / "bin" / "python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--editable",
            str(REPOSITORY_ROOT),
        ],
        check=True,
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )
    console = environment / "bin" / "fspm-optics"
    dependency_path = next(
        path for path in sys.path if path.endswith("site-packages")
    )
    command_environment = os.environ | {"PYTHONPATH": dependency_path}
    completed = subprocess.run(
        [str(console), "--help"],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=command_environment,
    )
    module = subprocess.run(
        [str(python), "-m", "fspm_optics.web", "--help"],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=command_environment,
    )
    assert "--live" in completed.stdout
    assert "--live" in module.stdout
    assert console.is_file()


def test_noneditable_wheel_contains_and_serves_social_image(tmp_path: Path) -> None:
    environment = tmp_path / "wheel-environment"
    wheelhouse = tmp_path / "wheelhouse"
    venv.EnvBuilder(with_pip=True, system_site_packages=True).create(environment)
    python = environment / "bin" / "python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "wheel",
            "--no-deps",
            "--no-build-isolation",
            "--wheel-dir",
            str(wheelhouse),
            str(REPOSITORY_ROOT),
        ],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    wheel = next(wheelhouse.glob("fspm_optics-*.whl"))
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    dependency_path = next(
        path for path in sys.path if path.endswith("site-packages")
    )
    command_environment = os.environ | {"PYTHONPATH": dependency_path}
    program = """
import asyncio
import httpx
from importlib.resources import files
from fspm_optics.web.public import create_public_app

async def verify():
    packaged = files("fspm_optics").joinpath(
        "resources", "web", "og-image.png"
    ).read_bytes()
    assert len(packaged) < 1_000_000
    app = create_public_app(precomputed_root="missing-not-used-for-static-request")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://testserver"
    ) as client:
        response = await client.get("/static/og-image.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == packaged

asyncio.run(verify())
"""
    completed = subprocess.run(
        [str(python), "-c", program],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=command_environment,
    )
    console = subprocess.run(
        [str(environment / "bin" / "fspm-optics"), "--help"],
        check=True,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        env=command_environment,
    )
    assert completed.stderr == ""
    assert "--live" in console.stdout


def test_render_hosts_accept_custom_domain_and_onrender(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FSPM_ALLOWED_HOSTS", "sim.luminousphotonics.com")
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport,
                base_url="https://sim.luminousphotonics.com",
            ) as client,
        ):
            for host in (
                "sim.luminousphotonics.com",
                "rad-simul-engine.onrender.com",
            ):
                index = await client.get("/", headers={"host": host})
                readiness = await client.get(
                    "/health/ready", headers={"host": host}
                )
                assert index.status_code == 200
                assert readiness.status_code == 200
                assert readiness.json()["status"] == "ready"

    asyncio.run(scenario())


def test_render_blueprint_is_public_repo_native_and_bounded() -> None:
    blueprint = (REPOSITORY_ROOT / "render.yaml").read_text(encoding="utf-8")

    assert "runtime: python" in blueprint
    assert "plan: standard" in blueprint
    assert "buildCommand: pip install ." in blueprint
    assert (
        'startCommand: fspm-optics --host 0.0.0.0 --port "$PORT" '
        "--precomputed-root ./precomputed"
    ) in blueprint
    assert "healthCheckPath: /health/ready" in blueprint
    assert "FSPM_MAX_CONCURRENT_WORK" in blueprint
    assert "FSPM_ALLOWED_HOSTS" in blueprint
    assert "value: sim.luminousphotonics.com" in blueprint
    assert "--live" not in blueprint
    assert ".venv/bin" not in blueprint


def test_load_test_refuses_non_local_target_without_override() -> None:
    script = REPOSITORY_ROOT / "scripts" / "load_test_public.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(script),
            "--base-url",
            "https://example.invalid",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    assert "refusing a non-local target" in completed.stderr
