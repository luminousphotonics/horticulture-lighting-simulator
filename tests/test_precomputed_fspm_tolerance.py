from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
import subprocess

import httpx
import pytest

from fspm_optics.precomputed.committed_playback import (
    HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256,
    HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256,
    HISTORICAL_PLAN_IDENTITY_SHA256,
    build_committed_playback_plan,
    inspect_committed_playback_catalog,
    load_committed_case_bundle,
)
from fspm_optics.web.precomputed import (
    PrecomputedCatalog,
    PrecomputedPlaybackError,
)
from fspm_optics.web.public import create_public_app


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRECOMPUTED_ROOT = REPOSITORY_ROOT / "precomputed"
HISTORICAL_PLAN_IDENTITY = (
    "114d6171d46a12284c9c7c3de8fae776851dbd7cb806be55b64aa1053d693416"
)


def _catalog() -> PrecomputedCatalog:
    return PrecomputedCatalog(
        PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
        playback_cache_size=16,
    )


def _selector(
    system: str,
    *,
    layout: str | None = None,
    target: float | None = 250.0,
    tolerance: object | None = None,
    lighting_target_mode: str | None = None,
) -> dict[str, object]:
    selector: dict[str, object] = {
        "system": system,
        "room_length_ft": 10.0,
        "room_width_ft": 10.0,
        "aisle_mode": False,
    }
    if layout is not None:
        selector["layout_mode"] = layout
    if target is not None:
        selector["target_ppfd"] = target
    if tolerance is not None:
        selector["fspm_target_tolerance"] = tolerance
    if lighting_target_mode is not None:
        selector["lighting_target_mode"] = lighting_target_mode
    return selector


def _json_artifact(record, name: str) -> dict[str, object]:
    value = json.loads(record.artifact(name).data)
    assert isinstance(value, dict)
    return value


def _scene(record) -> dict[str, object]:
    value = json.loads(record.viewer_artifact("scene.v1.json").data)
    assert isinstance(value, dict)
    return value


def _sequence_identity(values: list[str]) -> str:
    data = json.dumps(values, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _classification_counts(payload: dict[str, object]) -> dict[str, int]:
    records = payload["records"]
    assert isinstance(records, list)
    return {
        classification: sum(
            isinstance(record, dict)
            and record.get("classification") == classification
            for record in records
        )
        for classification in ("under_lit", "target_range", "over_lit")
    }


def test_omitted_tolerance_defaults_to_explicit_75_with_one_cache_identity() -> None:
    catalog = _catalog()
    selector = _selector("proposed")
    omitted = catalog.load(selector)
    explicit = catalog.load(selector | {"fspm_target_tolerance": 75.0})

    assert catalog.prepare_selector(selector) == catalog.prepare_selector(
        selector | {"fspm_target_tolerance": 75.0}
    )
    assert catalog.prepare_selector(selector)["fspm_target_tolerance"] == 75.0
    assert omitted is explicit
    assert omitted.playback_id == explicit.playback_id
    assert omitted.result_payload() == explicit.result_payload()
    assert omitted.metrics() == explicit.metrics()
    assert omitted.manifest() == explicit.manifest()
    assert omitted.result_payload()["fspm_target_tolerance"] == 75.0


def test_custom_tolerance_is_consistent_at_authenticated_proposed_250() -> None:
    record = _catalog().load(
        _selector("proposed", tolerance=20.0)
    )
    result = record.result_payload()
    metrics = record.metrics()
    manifest = record.manifest()
    baseline = _json_artifact(
        record, "baseline-leaf-position-uniformity.v1.json"
    )
    scene = _scene(record)

    assert result["fspm_target_tolerance"] == 20.0
    assert result["target_adjustment"]["exact_base_target_reuse"] is True
    assert metrics["fspm_target_policy"]["tolerance_umol_m2_s"] == 20.0
    assert manifest["fspm_target_tolerance"] == 20.0
    assert manifest["requested_orientation"][
        "fspm_target_tolerance_umol_m2_s"
    ] == 20.0
    assert baseline["target_policy"]["inputs"][
        "tolerance_umol_m2_s"
    ] == 20.0
    assert scene["ppfd_heatmap"]["target_coverage"][
        "tolerance_ppfd_umol_m2_s"
    ] == 20.0
    assert result["bundle_identity_sha256"] == (
        result["fspm_tolerance_presentation"][
            "authenticated_bundle_identity_sha256"
        ]
    )


def test_target_adjusted_conventional_supports_custom_tolerance() -> None:
    record = _catalog().load(
        _selector(
            "conventional",
            layout="practical",
            target=125.0,
            tolerance=12.5,
        )
    )
    metrics = record.metrics()
    policy = metrics["fspm_target_policy"]
    reference = policy["resolved_target_umol_m2_s"]

    assert metrics["achieved_mean_ppfd_umol_m2_s"] == pytest.approx(125.0)
    assert policy["classification_range"] == {
        "lower_umol_m2_s": max(0.0, reference - 12.5),
        "upper_umol_m2_s": reference + 12.5,
        "bounds": "inclusive",
    }
    assert record.result_payload()["target_adjustment"][
        "requested_target_ppfd_umol_m2_s"
    ] == 125.0
    assert _scene(record)["ppfd_heatmap"]["target_coverage"][
        "tolerance_ppfd_umol_m2_s"
    ] == 12.5


def test_fixed_output_hps_supports_tolerance_without_target_semantics() -> None:
    catalog = _catalog()
    selector = _selector("hps", target=None, tolerance=40.0)
    record = catalog.load(selector)
    result = record.result_payload()
    metrics = record.metrics()

    assert result["system_id"] == "hps"
    assert result["lighting_target_mode"] is None
    assert "target_adjustment" not in result
    assert metrics["fspm_target_policy"]["tolerance_umol_m2_s"] == 40.0
    assert metrics["fspm_target_policy"]["resolved_target_umol_m2_s"] == (
        metrics["achieved_mean_ppfd_umol_m2_s"]
    )
    assert _scene(record)["ppfd_heatmap"]["target_coverage"][
        "reference"
    ]["source"] == "achieved_stage_a_baseline_mean"
    with pytest.raises(PrecomputedPlaybackError):
        catalog.load(selector | {"target_ppfd": 250.0})
    with pytest.raises(PrecomputedPlaybackError):
        catalog.load(selector | {"layout_mode": "practical"})


def test_tolerance_identities_are_distinct_and_deterministic() -> None:
    catalog = _catalog()
    selector = _selector("proposed", target=125.0)
    narrow = catalog.load(selector | {"fspm_target_tolerance": 10.0})
    repeated = catalog.load(selector | {"fspm_target_tolerance": 10})
    wide = catalog.load(selector | {"fspm_target_tolerance": 100.0})

    assert narrow is repeated
    assert narrow.playback_id == repeated.playback_id
    assert narrow.playback.fspm_tolerance_derivation_identity_sha256 == (
        repeated.playback.fspm_tolerance_derivation_identity_sha256
    )
    assert narrow.playback.derived_playback_identity_sha256 == (
        wide.playback.derived_playback_identity_sha256
    )
    assert narrow.playback.fspm_tolerance_derivation_identity_sha256 != (
        wide.playback.fspm_tolerance_derivation_identity_sha256
    )
    assert narrow.playback.presentation_identity_sha256 != (
        wide.playback.presentation_identity_sha256
    )
    assert narrow.playback_id != wide.playback_id


def test_target_capped_physical_derivation_is_separate_from_tolerance() -> None:
    catalog = _catalog()
    selector = _selector(
        "conventional",
        layout="practical",
        target=200.0,
        lighting_target_mode="target_capped",
    )
    default = catalog.load(selector)
    custom = catalog.load(selector | {"fspm_target_tolerance": 25.0})

    assert default.playback.samples == custom.playback.samples
    assert default.playback.derived_playback_identity_sha256 == (
        custom.playback.derived_playback_identity_sha256
    )
    assert custom.result_payload()["lighting_target_mode"] == "target_capped"
    assert custom.result_payload()["fspm_tolerance_presentation"][
        "source_derived_playback_identity_sha256"
    ] == custom.playback.derived_playback_identity_sha256
    assert custom.playback.presentation_identity_sha256 != (
        custom.result_payload()["target_adjustment"][
            "presentation_identity_sha256"
        ]
    )
    assert _scene(custom)["ppfd_heatmap"]["target_coverage"][
        "tolerance_ppfd_umol_m2_s"
    ] == 25.0


def test_tolerance_reclassifies_boundaries_and_categories_only() -> None:
    catalog = _catalog()
    selector = _selector("proposed")
    narrow = catalog.load(selector | {"fspm_target_tolerance": 1.0})
    wide = catalog.load(selector | {"fspm_target_tolerance": 100.0})
    narrow_leaf = _json_artifact(
        narrow, "baseline-leaf-position-uniformity.v1.json"
    )
    wide_leaf = _json_artifact(
        wide, "baseline-leaf-position-uniformity.v1.json"
    )
    narrow_counts = _classification_counts(narrow_leaf)
    wide_counts = _classification_counts(wide_leaf)

    assert narrow_leaf["target_policy"]["classification_range"] == {
        "lower_umol_m2_s": 249.0,
        "upper_umol_m2_s": 251.0,
        "bounds": "inclusive",
    }
    assert wide_leaf["target_policy"]["classification_range"] == {
        "lower_umol_m2_s": 150.0,
        "upper_umol_m2_s": 350.0,
        "bounds": "inclusive",
    }
    assert narrow_counts != wide_counts
    for key, classification in (
        ("under_lit_leaves", "under_lit"),
        ("target_range_leaves", "target_range"),
        ("over_lit_leaves", "over_lit"),
    ):
        assert narrow_leaf["summary"][key]["count"] == narrow_counts[
            classification
        ]
        assert wide_leaf["summary"][key]["count"] == wide_counts[
            classification
        ]


def test_tolerance_preserves_all_unrelated_physical_outputs() -> None:
    catalog = _catalog()
    selector = _selector(
        "conventional", layout="rolling_bench", target=125.0
    )
    default = catalog.load(selector)
    custom = catalog.load(selector | {"fspm_target_tolerance": 5.0})

    assert default.playback.canonical_bundle_identity_sha256 == (
        custom.playback.canonical_bundle_identity_sha256
    )
    assert default.playback.derived_playback_identity_sha256 == (
        custom.playback.derived_playback_identity_sha256
    )
    assert default.playback.samples == custom.playback.samples
    for name in (
        "ppfd.csv",
        "ppfd-scatter.f32le.bin",
        "ppfd-heatmap.png",
        "ppfd-heatmap-overlay.png",
        "target-control.json",
        "full-output-schedule.json",
        "operating-point.json",
        "physical-source-state.json",
    ):
        assert default.artifact(name).data == custom.artifact(name).data
    default_layout = _json_artifact(default, "natural-fit-layout.json")
    custom_layout = _json_artifact(custom, "natural-fit-layout.json")
    default_layout.pop("requested_orientation")
    custom_layout.pop("requested_orientation")
    assert default_layout == custom_layout
    for key in (
        "achieved_mean_ppfd_umol_m2_s",
        "achieved_maximum_ppfd_umol_m2_s",
        "dimming_factor",
        "power",
        "ppf",
        "spatial_uniformity",
        "fspm_surface_light_metrics",
    ):
        assert default.metrics().get(key) == custom.metrics().get(key)


@pytest.mark.parametrize(
    "value",
    (0, -1, True, "75", float("nan"), float("inf"), float("-inf")),
)
def test_invalid_tolerance_values_fail_closed(value: object) -> None:
    with pytest.raises(
        PrecomputedPlaybackError,
        match="finite strictly positive number",
    ):
        _catalog().prepare_selector(
            _selector("proposed") | {"fspm_target_tolerance": value}
        )


def test_public_custom_tolerance_submits_no_native_work() -> None:
    app = create_public_app(precomputed_root=PRECOMPUTED_ROOT)

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post(
                    "/api/precomputed/playbacks",
                    json=_selector("hps", target=None, tolerance=33.0),
                )
                assert response.status_code in {200, 202}
                body = response.json()
                for _attempt in range(1000):
                    if body["state"] in {
                        "succeeded",
                        "completed",
                        "failed",
                        "expired",
                    }:
                        break
                    await asyncio.sleep(0.01)
                    status = await client.get(body["status_url"])
                    assert status.status_code == 200
                    body = status.json()
                assert body["state"] in {"succeeded", "completed"}
                result = body["result"]
                assert result["fspm_target_tolerance"] == 33.0
                playback_id = result["run_id"]
                metrics = await client.get(result["metrics_url"])
                manifest = await client.get(result["manifest_url"])
                target_coverage = await client.get(
                    f"/api/precomputed/playbacks/{playback_id}/artifacts/"
                    "baseline-leaf-position-uniformity.v1.json"
                )
                scene = await client.get(
                    f"/precomputed/{playback_id}/viewer/scene.v1.json"
                )
                assert metrics.status_code == 200
                assert manifest.status_code == 200
                assert target_coverage.status_code == 200
                assert scene.status_code == 200
                assert metrics.json()["fspm_target_policy"][
                    "tolerance_umol_m2_s"
                ] == 33.0
                assert manifest.json()["fspm_target_tolerance"] == 33.0
                assert target_coverage.json()["target_policy"]["inputs"][
                    "tolerance_umol_m2_s"
                ] == 33.0
                assert scene.json()["ppfd_heatmap"]["target_coverage"][
                    "tolerance_ppfd_umol_m2_s"
                ] == 33.0

                invalid = await client.post(
                    "/api/precomputed/playbacks",
                    json=_selector("hps", target=None, tolerance=0.0),
                )
                assert invalid.status_code == 422
                assert invalid.json()["error"]["code"] == (
                    "invalid_precomputed_fspm_target_tolerance"
                )

    asyncio.run(scenario())
    assert not hasattr(app.state, "jobs")
    assert "/api/runs" not in {route.path for route in app.routes}


def test_committed_catalog_and_protected_paths_remain_unchanged() -> None:
    plan = build_committed_playback_plan()
    status = inspect_committed_playback_catalog(PRECOMPUTED_ROOT, plan=plan)

    assert HISTORICAL_PLAN_IDENTITY_SHA256 == HISTORICAL_PLAN_IDENTITY
    assert plan.plan_identity_sha256 == HISTORICAL_PLAN_IDENTITY
    assert plan.case_count == status.valid_count == 24
    assert status.invalid_count == status.remaining_count == 0
    loaded_case_ids = []
    for case in plan.cases:
        playback = load_committed_case_bundle(
            case.output_path(PRECOMPUTED_ROOT),
            case,
            plan.plan_identity_sha256,
        )
        loaded_case_ids.append(
            playback.manifest["run_configuration"]["fixed_plan"]["case_id"]
        )
    assert _sequence_identity(loaded_case_ids) == (
        HISTORICAL_CASE_SEQUENCE_IDENTITY_SHA256
    )
    assert _sequence_identity(
        [case.bundle_identity_sha256 for case in plan.cases]
    ) == HISTORICAL_BUNDLE_SEQUENCE_IDENTITY_SHA256
    protected = subprocess.run(
        [
            "git",
            "diff",
            "--exit-code",
            "--",
            "precomputed/",
            "src/fspm_optics/resources/data/",
            "src/fspm_optics/resources/cad/",
            "src/fspm_optics/resources/viewer/",
            ":(exclude)src/fspm_optics/resources/viewer/artifacts.js",
            "src/fspm_optics/fixtures/",
        ],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
    )
    assert protected.returncode == 0, protected.stdout.decode("utf-8")
