from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import re
import subprocess
from typing import Mapping
import zipfile

import httpx
import pytest

from fspm_optics.application.domain import (
    CONVENTIONAL_SYSTEM_ID,
    HPS_SYSTEM_ID,
    PROPOSED_SYSTEM_ID,
    RequestValidationError,
    parse_run_request,
)
from fspm_optics.precomputed.committed_playback import (
    CommittedPlaybackRole,
    build_committed_playback_plan,
)
from fspm_optics.web.precomputed import (
    PrecomputedCatalog,
    PrecomputedPlaybackNotFound,
)
from fspm_optics.web.public import create_public_app


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRECOMPUTED_ROOT = REPOSITORY_ROOT / "precomputed"
IMMUTABLE_SCAN_ALLOWLIST = ("precomputed/", ".git/")
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
PUBLIC_STATIC_TEXT = (
    "public-app.js",
    "system-labels.js",
    "result-contracts.js",
    "result-view.js",
    "styles.css",
)


def _selector(case) -> dict[str, object]:
    room = case.canonical_domain["supported_requested_room_orders_ft"][0]
    selector: dict[str, object] = {
        "system": case.system_id,
        "room_length_ft": room["length"],
        "room_width_ft": room["width"],
        "aisle_mode": case.aisle_enabled,
    }
    if case.system_id == CONVENTIONAL_SYSTEM_ID:
        selector["layout_mode"] = case.layout_variant
    return selector


def _canonical_json(system: str, member: str) -> dict[str, object]:
    bundle = sorted((PRECOMPUTED_ROOT / system).rglob("*.fspm-compact"))[0]
    with zipfile.ZipFile(bundle) as archive:
        value = json.loads(archive.read(member))
    assert isinstance(value, dict)
    return value


def _prohibited_values() -> tuple[str, ...]:
    conventional_manifest = _canonical_json(CONVENTIONAL_SYSTEM_ID, "manifest.json")
    identities = conventional_manifest["authenticated_identities"]
    assert isinstance(identities, Mapping)
    fixed_inputs = identities["fixed_plan_inputs"]
    assert isinstance(fixed_inputs, Mapping)
    compatibility = fixed_inputs["compatibility_inputs"]
    assert isinstance(compatibility, Mapping)
    fixture = compatibility["fixture"]
    source = compatibility["source"]
    assert isinstance(fixture, Mapping) and isinstance(source, Mapping)
    assets = fixture["assets"]
    assert isinstance(assets, list) and len(assets) == 1
    asset = assets[0]
    assert isinstance(asset, Mapping)

    conventional_catalog = _canonical_json(
        CONVENTIONAL_SYSTEM_ID,
        "payload/viewer/fixtures/catalog.v1.json",
    )
    groups = conventional_catalog["asset_groups"]
    assert isinstance(groups, list) and len(groups) == 1
    group = groups[0]
    assert isinstance(group, Mapping)
    matrices = group["instance_matrices"]
    assert isinstance(matrices, Mapping)

    proposed_result = _canonical_json(
        PROPOSED_SYSTEM_ID, "payload/public-result.v1.json"
    )
    metrics = proposed_result["metrics"]
    assert isinstance(metrics, Mapping)
    spectral = metrics["spectral_basis"]
    assert isinstance(spectral, Mapping)
    provenance = spectral["control_provenance"]
    assert isinstance(provenance, Mapping)
    spd = provenance["relative_spd_authority"]
    assert isinstance(spd, Mapping)

    brand_token = str(asset["asset_id"]).split("-")[1]
    return tuple(
        value.lower()
        for value in {
            brand_token,
            str(asset["asset_id"]),
            str(asset["fixture_type"]),
            str(asset["resource_path"]),
            str(source["profile_id"]),
            str(source["ies_sha256"]),
            str(group["display_asset_id"]),
            str(group["display_fixture_type"]),
            str(matrices["filename"]),
            str(spectral["id"]),
            str(spectral["label"]),
            str(spectral["source_model_id"]),
            str(spectral["spectral_distribution_id"]),
            str(spd["resource"]),
            "qb" + "-fsg",
            "blc" + "2107022e",
            "bel" + "ling",
            "gpm" + "-3000",
            "sp" + "ydr",
            "conventional_" + brand_token + "_unit_downward_flux",
            "sr" + "-3h47",
        }
        if value
    )


def _assert_anonymous(value: object) -> None:
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="ignore")
    elif isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, sort_keys=True, allow_nan=False)
    lowered = text.lower()
    assert not [token for token in _prohibited_values() if token in lowered]


def _tracked_paths() -> tuple[Path, ...]:
    completed = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    paths = completed.stdout.decode("utf-8").split("\0")
    return tuple(
        REPOSITORY_ROOT / relative
        for relative in paths
        if relative
        and not any(relative.startswith(prefix) for prefix in IMMUTABLE_SCAN_ALLOWLIST)
    )


def test_current_tracked_tree_has_no_prohibited_identifiers() -> None:
    printable = re.compile(rb"[\x20-\x7e]{4,}")
    prohibited = _prohibited_values()
    for path in _tracked_paths():
        relative = path.relative_to(REPOSITORY_ROOT).as_posix().lower()
        assert not [token for token in prohibited if token in relative]
        raw = path.read_bytes()
        try:
            searchable = raw.decode("utf-8").lower()
        except UnicodeDecodeError:
            searchable = "\n".join(
                item.decode("ascii").lower() for item in printable.findall(raw)
            )
        assert not [token for token in prohibited if token in searchable], relative


def test_all_public_playback_json_is_anonymous_and_exactly_24_cases() -> None:
    plan = build_committed_playback_plan()
    catalog = PrecomputedCatalog(
        PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
        base_bundle_cache_size=4,
        playback_cache_size=24,
    )
    availability = catalog.availability()
    _assert_anonymous(availability)
    assert availability["valid_case_count"] == 24
    assert availability["missing_case_count"] == 0
    assert availability["invalid_case_count"] == 0

    counts = {PROPOSED_SYSTEM_ID: 0, CONVENTIONAL_SYSTEM_ID: 0, HPS_SYSTEM_ID: 0}
    loaded_bundle_ids: list[str] = []
    for case in plan.cases:
        record = catalog.load(_selector(case))
        counts[case.system_id] += 1
        loaded_bundle_ids.append(record.playback.canonical_bundle_identity_sha256)
        for surface in (record.result_payload(), record.metrics(), record.manifest()):
            _assert_anonymous(surface)
        for name in JSON_ARTIFACTS:
            _assert_anonymous(record.artifact(name).data)
        for name in VIEWER_JSON:
            _assert_anonymous(record.viewer_artifact(name).data)

        if case.system_id == PROPOSED_SYSTEM_ID:
            spectral = record.metrics()["spectral_basis"]
            assert spectral["id"] == "conventional_led_control"
            assert spectral["label"] == "Conventional LED spectrum (controlled A/B)"
        elif case.system_id == CONVENTIONAL_SYSTEM_ID:
            fixture_catalog = json.loads(
                record.viewer_artifact("fixtures/catalog.v1.json").data
            )
            fixture_group = fixture_catalog["asset_groups"][0]
            assert fixture_group["display_asset_id"] == "conventional-led-8-bar-v1"
            assert fixture_group["display_fixture_type"] == "conventional_led_8_bar"

    assert counts == {
        PROPOSED_SYSTEM_ID: 6,
        CONVENTIONAL_SYSTEM_ID: 12,
        HPS_SYSTEM_ID: 6,
    }
    assert loaded_bundle_ids == [case.bundle_identity_sha256 for case in plan.cases]


def test_conventional_viewer_maps_generic_paths_to_unchanged_authenticated_bytes() -> None:
    plan = build_committed_playback_plan()
    case = next(
        item
        for item in plan.cases
        if item.role is CommittedPlaybackRole.CONTROLLED_REFERENCE
    )
    catalog = PrecomputedCatalog(PRECOMPUTED_ROOT, repository_root=REPOSITORY_ROOT)
    record = catalog.load(_selector(case))
    public_catalog = json.loads(record.viewer_artifact("fixtures/catalog.v1.json").data)
    group = public_catalog["asset_groups"][0]
    asset_path = f"fixtures/{group['asset']['filename']}"
    transforms_path = f"fixtures/{group['instance_matrices']['filename']}"
    assert asset_path.startswith("fixtures/assets/conventional-led-8-bar-v1-")
    assert transforms_path.startswith("fixtures/transforms/conventional-led-8-bar-v1-")

    public_asset = record.viewer_artifact(asset_path)
    packaged_asset = (
        REPOSITORY_ROOT
        / "src/fspm_optics/resources/viewer/fixtures/conventional/conventional_led_8_bar.glb"
    ).read_bytes()
    assert public_asset.data == packaged_asset
    assert hashlib.sha256(public_asset.data).hexdigest() == group["asset"]["sha256"]

    public_transforms = record.viewer_artifact(transforms_path)
    canonical_transforms = [
        data
        for name, data in record.playback.canonical.payloads.items()
        if name.startswith("fixture_transforms_")
    ]
    assert canonical_transforms == [public_transforms.data]

    canonical_catalog = json.loads(
        record.playback.canonical.payloads["fixture_catalog"]
    )
    historical_group = canonical_catalog["asset_groups"][0]
    for historical_path in (
        f"fixtures/{historical_group['asset']['filename']}",
        f"fixtures/{historical_group['instance_matrices']['filename']}",
    ):
        with pytest.raises(PrecomputedPlaybackNotFound):
            record.viewer_artifact(historical_path)


def test_legacy_control_selector_and_public_http_surfaces_fail_closed() -> None:
    plan = build_committed_playback_plan()
    proposed = next(case for case in plan.cases if case.system_id == PROPOSED_SYSTEM_ID)
    proposed_payload = proposed.request.to_dict()
    historical_result = _canonical_json(
        PROPOSED_SYSTEM_ID, "payload/public-result.v1.json"
    )
    historical_metrics = historical_result["metrics"]
    assert isinstance(historical_metrics, Mapping)
    historical_spectral = historical_metrics["spectral_basis"]
    assert isinstance(historical_spectral, Mapping)
    with pytest.raises(RequestValidationError):
        parse_run_request(
            proposed_payload | {"spectral_basis": historical_spectral["id"]}
        )

    conventional = next(
        case for case in plan.cases if case.system_id == CONVENTIONAL_SYSTEM_ID
    )
    hps = next(case for case in plan.cases if case.system_id == HPS_SYSTEM_ID)
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client,
        ):
            record = app.state.precomputed.load(_selector(conventional))
            playback_id = record.playback_id
            paths = (
                "/",
                *(f"/static/{name}" for name in PUBLIC_STATIC_TEXT),
                "/health/live",
                "/health/ready",
                "/api/capabilities",
                "/api/precomputed/availability",
                f"/api/precomputed/playbacks/{playback_id}/metrics",
                f"/api/precomputed/playbacks/{playback_id}/manifest",
                *(
                    f"/api/precomputed/playbacks/{playback_id}/artifacts/{name}"
                    for name in JSON_ARTIFACTS
                ),
                f"/precomputed/{playback_id}/viewer/index.html",
                f"/precomputed/{playback_id}/viewer/main.js",
                f"/precomputed/{playback_id}/viewer/fixture-artifacts.js",
                *(
                    f"/precomputed/{playback_id}/viewer/{name}"
                    for name in VIEWER_JSON
                ),
            )
            for path in paths:
                response = await client.get(path)
                assert response.status_code == 200, path
                _assert_anonymous(response.content)
                _assert_anonymous(dict(response.headers))

            public_catalog = (
                await client.get(
                    f"/precomputed/{playback_id}/viewer/fixtures/catalog.v1.json"
                )
            ).json()
            asset_name = public_catalog["asset_groups"][0]["asset"]["filename"]
            asset_response = await client.get(
                f"/precomputed/{playback_id}/viewer/fixtures/{asset_name}"
            )
            assert asset_response.status_code == 200
            assert asset_response.content == record.viewer_artifact(
                f"fixtures/{asset_name}"
            ).data
            _assert_anonymous(dict(asset_response.headers))

            hps_record = app.state.precomputed.load(_selector(hps))
            hps_catalog = json.loads(
                hps_record.viewer_artifact("fixtures/catalog.v1.json").data
            )
            hps_asset_name = hps_catalog["asset_groups"][0]["asset"]["filename"]
            hps_asset_response = await client.get(
                f"/precomputed/{hps_record.playback_id}/viewer/fixtures/"
                f"{hps_asset_name}"
            )
            assert hps_asset_response.status_code == 200
            assert hps_asset_response.content == hps_record.viewer_artifact(
                f"fixtures/{hps_asset_name}"
            ).data
            _assert_anonymous(dict(hps_asset_response.headers))

            canonical_catalog = json.loads(
                record.playback.canonical.payloads["fixture_catalog"]
            )
            historical_name = canonical_catalog["asset_groups"][0]["asset"]["filename"]
            rejected = await client.get(
                f"/precomputed/{playback_id}/viewer/fixtures/{historical_name}"
            )
            assert rejected.status_code == 404
            _assert_anonymous(rejected.content)
            _assert_anonymous(dict(rejected.headers))

            submitted = await client.post(
                "/api/precomputed/playbacks",
                json=_selector(conventional),
                headers={"Idempotency-Key": "phase4b-conventional-playback"},
            )
            assert submitted.status_code in {200, 202}
            for _attempt in range(200):
                _assert_anonymous(submitted.content)
                body = submitted.json()
                if body["state"] in {"completed", "failed", "expired"}:
                    break
                await asyncio.sleep(0.01)
                submitted = await client.get(body["status_url"])
                assert submitted.status_code == 200
            else:
                raise AssertionError("Conventional playback status did not become terminal")
            assert submitted.json()["state"] == "completed"

            rejected_selector = await client.post(
                "/api/precomputed/playbacks",
                json=_selector(proposed)
                | {"spectral_basis": historical_spectral["id"]},
            )
            assert rejected_selector.status_code == 422
            _assert_anonymous(rejected_selector.content)

    asyncio.run(scenario())
