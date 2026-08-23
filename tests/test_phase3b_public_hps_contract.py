from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Mapping

import httpx
import pytest

from fspm_optics.application.domain import (
    HPS_SYSTEM_ID,
    HpsRunRequest,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.precomputed.committed_playback import (
    HISTORICAL_PLAN_IDENTITY_SHA256,
    CommittedPlaybackRole,
    build_committed_playback_plan,
    load_committed_case_bundle,
)
from fspm_optics.web.precomputed import (
    PrecomputedCatalog,
    PrecomputedPlaybackError,
    PrecomputedPlaybackNotFound,
)
from fspm_optics.web.public import create_public_app
from fspm_optics.web.precomputed_projection import (
    PUBLIC_HPS_PLACEMENT_CONTRACT_SHA256,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRECOMPUTED_ROOT = REPOSITORY_ROOT / "precomputed"
JSON_ARTIFACTS = (
    "visualization.json",
    "target-control.json",
    "full-output-schedule.json",
    "operating-point.json",
    "physical-source-state.json",
    "baseline-leaf-position-uniformity.v1.json",
    "natural-fit-layout.json",
)
VIEWER_JSON = (
    "scene.v1.json",
    "fixtures/catalog.v1.json",
    "ppfd-heatmap/visualization.json",
)


def _selector(case) -> dict[str, object]:
    room = case.canonical_domain["supported_requested_room_orders_ft"][0]
    return {
        "system": HPS_SYSTEM_ID,
        "room_length_ft": room["length"],
        "room_width_ft": room["width"],
        "aisle_mode": case.aisle_enabled,
    }


def _historical_system_id() -> str:
    plan = build_committed_playback_plan()
    case = next(
        item
        for item in plan.cases
        if item.role is CommittedPlaybackRole.FIXED_OUTPUT_REFERENCE
    )
    playback = load_committed_case_bundle(
        case.output_path(PRECOMPUTED_ROOT),
        case,
        plan.plan_identity_sha256,
    )
    value = playback.manifest["system_id"]
    assert isinstance(value, str)
    assert value != HPS_SYSTEM_ID
    return value


def _assert_vendor_neutral(value: object, forbidden_token: str) -> None:
    if isinstance(value, bytes):
        text = value.decode("utf-8")
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, allow_nan=False)
    assert forbidden_token.lower() not in text.lower()


def test_public_hps_selector_is_exact_and_historical_aliases_fail_closed() -> None:
    request = parse_run_request(
        {
            "system": HPS_SYSTEM_ID,
            "room_length_ft": 10.0,
            "room_width_ft": 10.0,
            "quality": "standard",
            "analysis_scope": "baseline_plus_multispectral_fspm",
            "mounting_height_in": 24.0,
            "aisle_mode": False,
            "fspm_target_mode": "automatic",
            "fspm_target_tolerance": 75.0,
            "include_far_red": True,
        }
    )
    assert isinstance(request, HpsRunRequest)
    assert request.system == "hps"

    historical = _historical_system_id()
    for alias in (historical, historical.upper(), "HPS"):
        with pytest.raises(RequestValidationError):
            parse_run_request(request.to_dict() | {"system": alias})

    catalog = PrecomputedCatalog(
        PRECOMPUTED_ROOT, repository_root=REPOSITORY_ROOT
    )
    for alias in (historical, historical.upper()):
        with pytest.raises(PrecomputedPlaybackError):
            catalog.load(
                {
                    "system": alias,
                    "room_length_ft": 10.0,
                    "room_width_ft": 10.0,
                    "aisle_mode": False,
                }
            )


def test_all_six_hps_artifacts_load_through_vendor_neutral_projection() -> None:
    plan = build_committed_playback_plan()
    catalog = PrecomputedCatalog(
        PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
        playback_cache_size=8,
    )
    availability = catalog.availability()
    assert availability["plan_identity_sha256"] == HISTORICAL_PLAN_IDENTITY_SHA256
    assert availability["valid_case_count"] == 24
    assert availability["invalid_case_count"] == 0
    assert availability["missing_case_count"] == 0
    hps_availability = [
        item for item in availability["cases"] if item["system"] == HPS_SYSTEM_ID
    ]
    assert len(hps_availability) == 6
    assert all(item["controlled_settings"]["lighting_target_modes"] == [] for item in hps_availability)

    hps_cases = [
        case
        for case in plan.cases
        if case.role is CommittedPlaybackRole.FIXED_OUTPUT_REFERENCE
    ]
    forbidden_token = _historical_system_id().split("_", 1)[0]
    loaded_bundle_ids: set[str] = set()
    for case in hps_cases:
        record = catalog.load(_selector(case))
        loaded_bundle_ids.add(record.playback.canonical_bundle_identity_sha256)
        result = record.result_payload()
        assert result["system_id"] == HPS_SYSTEM_ID
        assert result["case_id"].startswith("hps-")
        assert result["bundle_identity_sha256"] == case.bundle_identity_sha256
        assert record.metrics()["system_id"] == HPS_SYSTEM_ID
        _assert_vendor_neutral(result, forbidden_token)
        _assert_vendor_neutral(record.metrics(), forbidden_token)
        _assert_vendor_neutral(record.manifest(), forbidden_token)
        for name in JSON_ARTIFACTS:
            _assert_vendor_neutral(record.artifact(name).data, forbidden_token)
        for name in VIEWER_JSON:
            _assert_vendor_neutral(record.viewer_artifact(name).data, forbidden_token)
    assert loaded_bundle_ids == {case.bundle_identity_sha256 for case in hps_cases}


def test_hps_viewer_uses_generic_paths_and_original_authenticated_bytes() -> None:
    plan = build_committed_playback_plan()
    case = next(
        item
        for item in plan.cases
        if item.role is CommittedPlaybackRole.FIXED_OUTPUT_REFERENCE
    )
    catalog = PrecomputedCatalog(
        PRECOMPUTED_ROOT, repository_root=REPOSITORY_ROOT
    )
    record = catalog.load(_selector(case))
    public_catalog = json.loads(
        record.viewer_artifact("fixtures/catalog.v1.json").data
    )
    group = public_catalog["asset_groups"][0]
    asset_path = f"fixtures/{group['asset']['filename']}"
    transform_path = f"fixtures/{group['instance_matrices']['filename']}"
    assert asset_path.startswith("fixtures/assets/hps-")
    assert transform_path.startswith("fixtures/transforms/hps-")
    assert group["registry"]["packaged_resource_path"] == "fixtures/hps/hps.glb"
    assert {
        item["placement_contract_sha256"]
        for item in public_catalog["fixture_plan"]["fixtures"]
    } == {PUBLIC_HPS_PLACEMENT_CONTRACT_SHA256}

    asset = record.viewer_artifact(asset_path)
    packaged = (
        REPOSITORY_ROOT
        / "src/fspm_optics/resources/viewer/fixtures/hps/hps.glb"
    ).read_bytes()
    assert asset.data == packaged
    assert hashlib.sha256(asset.data).hexdigest() == group["asset"]["sha256"]

    transforms = record.viewer_artifact(transform_path)
    canonical_transforms = [
        data
        for name, data in record.playback.canonical.payloads.items()
        if name.startswith("fixture_transforms_")
    ]
    assert canonical_transforms == [transforms.data]
    assert hashlib.sha256(transforms.data).hexdigest() == group[
        "instance_matrices"
    ]["sha256"]

    canonical_catalog = json.loads(
        record.playback.canonical.payloads["fixture_catalog"]
    )
    historical_group = canonical_catalog["asset_groups"][0]
    for path in (
        f"fixtures/{historical_group['asset']['filename']}",
        f"fixtures/{historical_group['instance_matrices']['filename']}",
    ):
        with pytest.raises(PrecomputedPlaybackNotFound):
            record.viewer_artifact(path)


def test_public_http_surfaces_never_expose_historical_hps_vocabulary() -> None:
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)
    plan = build_committed_playback_plan()
    case = next(
        item
        for item in plan.cases
        if item.role is CommittedPlaybackRole.FIXED_OUTPUT_REFERENCE
    )
    forbidden_system = _historical_system_id()
    forbidden_token = forbidden_system.split("_", 1)[0]

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=transport, base_url="http://testserver") as client,
        ):
            record = app.state.precomputed.load(_selector(case))
            playback_id = record.playback_id
            text_paths = (
                "/",
                "/static/public-app.js",
                "/static/system-labels.js",
                "/api/capabilities",
                "/api/precomputed/availability",
                f"/api/precomputed/playbacks/{playback_id}/metrics",
                f"/api/precomputed/playbacks/{playback_id}/manifest",
                f"/api/precomputed/playbacks/{playback_id}/artifacts/visualization.json",
                f"/precomputed/{playback_id}/viewer/index.html",
                f"/precomputed/{playback_id}/viewer/main.js",
                f"/precomputed/{playback_id}/viewer/fixture-artifacts.js",
                f"/precomputed/{playback_id}/viewer/scene.v1.json",
                f"/precomputed/{playback_id}/viewer/fixtures/catalog.v1.json",
            )
            for path in text_paths:
                response = await client.get(path)
                assert response.status_code == 200
                _assert_vendor_neutral(response.text, forbidden_token)

            capabilities = (await client.get("/api/capabilities")).json()
            systems = capabilities["precomputed_playback"]["systems"]
            assert systems == ["proposed", "conventional", "hps"]
            assert forbidden_system not in systems

            for alias in (forbidden_system, forbidden_system.upper()):
                response = await client.post(
                    "/api/precomputed/playbacks",
                    json=_selector(case) | {"system": alias},
                )
                assert response.status_code == 422
                _assert_vendor_neutral(response.text, forbidden_token)

            submitted = await client.post(
                "/api/precomputed/playbacks",
                json=_selector(case),
                headers={"Idempotency-Key": "phase3b-hps-playback"},
            )
            assert submitted.status_code in {200, 202}
            status = submitted
            for _attempt in range(200):
                body = status.json()
                _assert_vendor_neutral(body, forbidden_token)
                if body["state"] in {"completed", "failed", "expired"}:
                    break
                await asyncio.sleep(0.01)
                status = await client.get(body["status_url"])
                assert status.status_code == 200
            else:
                raise AssertionError("HPS playback status did not become terminal")
            body = status.json()
            assert body["state"] == "completed"
            assert body["result"]["system_id"] == HPS_SYSTEM_ID

            public_catalog = (
                await client.get(
                    f"/precomputed/{playback_id}/viewer/fixtures/catalog.v1.json"
                )
            ).json()
            asset_path = public_catalog["asset_groups"][0]["asset"]["filename"]
            asset = await client.get(
                f"/precomputed/{playback_id}/viewer/fixtures/{asset_path}"
            )
            assert asset.status_code == 200
            assert asset.content == (
                REPOSITORY_ROOT
                / "src/fspm_optics/resources/viewer/fixtures/hps/hps.glb"
            ).read_bytes()

    asyncio.run(scenario())
