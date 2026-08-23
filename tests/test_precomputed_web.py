from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import struct
import threading

import httpx
import pytest

import fspm_optics.web.precomputed as web_precomputed

from fspm_optics.web.app import create_app
from fspm_optics.web.precomputed import (
    PrecomputedCatalog,
    PrecomputedPlaybackError,
    PrecomputedPlaybackNotFound,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
REAL_PRECOMPUTED_ROOT = REPOSITORY_ROOT / "precomputed"
REAL_BUNDLES_AVAILABLE = REAL_PRECOMPUTED_ROOT.is_dir() and any(
    REAL_PRECOMPUTED_ROOT.rglob("*.fspm-compact")
)
requires_real_bundles = pytest.mark.skipif(
    not REAL_BUNDLES_AVAILABLE,
    reason="real fixed-sweep bundles are not present in this checkout",
)


def _catalog() -> PrecomputedCatalog:
    return PrecomputedCatalog(
        REAL_PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
    )


def _selector(
    system: str,
    *,
    layout: str | None = None,
    length: float = 10.0,
    width: float = 10.0,
    aisle: bool = False,
    target: float | None = 250.0,
    lighting_target_mode: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "system": system,
        "room_length_ft": length,
        "room_width_ft": width,
        "aisle_mode": aisle,
    }
    if layout is not None:
        payload["layout_mode"] = layout
    if target is not None:
        payload["target_ppfd"] = target
    if lighting_target_mode is not None:
        payload["lighting_target_mode"] = lighting_target_mode
    return payload


async def _submit_and_wait(
    client: httpx.AsyncClient,
    selector: dict[str, object],
    *,
    idempotency_key: str,
) -> dict[str, object]:
    response = await client.post(
        "/api/precomputed/playbacks",
        json=selector,
        headers={"Idempotency-Key": idempotency_key},
    )
    assert response.status_code in {200, 202}
    body = response.json()
    for _attempt in range(1000):
        if body["state"] in {"succeeded", "completed", "failed", "expired"}:
            return body
        await asyncio.sleep(0.01)
        status = await client.get(body["status_url"])
        assert status.status_code == 200
        body = status.json()
    raise AssertionError("playback request did not reach a terminal state")


@requires_real_bundles
def test_real_catalog_discovers_valid_missing_and_invalid_cases_dynamically() -> None:
    catalog = _catalog()
    availability = catalog.availability()
    bundle_count = len(tuple(REAL_PRECOMPUTED_ROOT.rglob("*.fspm-compact")))

    assert availability["case_count"] == 24
    assert availability["valid_case_count"] == bundle_count
    assert availability["invalid_case_count"] == 0
    assert availability["missing_case_count"] == 24 - bundle_count
    cases = availability["cases"]
    assert all(
        case["controlled_settings"]["quality"] == "standard" for case in cases
    )
    assert all(
        case["controlled_settings"]["include_far_red"] is True for case in cases
    )
    assert {
        (room["length"], room["width"])
        for room in (case["canonical_display_room_ft"] for case in cases)
    } == {(10.0, 10.0), (30.0, 15.0), (50.0, 30.0)}
    assert bundle_count == 24
    assert all(case["available"] for case in cases)
    assert all(
        case["controlled_settings"]["lighting_target_modes"]
        == (
            []
            if case["system"] == "hps"
            else ["mean_target", "target_capped"]
        )
        for case in cases
    )


@requires_real_bundles
@pytest.mark.parametrize(
    "selector",
    (
        _selector("proposed", target=250.0),
        _selector("conventional", layout="practical", target=250.0),
        _selector("conventional", layout="rolling_bench", target=250.0),
    ),
)
def test_real_exact_250_playback_covers_complete_public_result(
    selector: dict[str, object],
) -> None:
    record = _catalog().load(selector)
    result = record.result_payload()
    metrics = record.metrics()
    visualization = json.loads(record.artifact("visualization.json").data)
    scene = json.loads(record.viewer_artifact("scene.v1.json").data)

    assert result["execution_mode"] == "precomputed"
    assert result["source_runtime_required"] is False
    assert result["target_adjustment"]["exact_base_target_reuse"] is True
    assert metrics["quality"] == "standard"
    assert metrics["analysis_scope"]["value"] == (
        "baseline_plus_multispectral_fspm"
    )
    assert metrics["fspm_surface_light_metrics"]["far_red_executed"] is True
    assert metrics["fspm_surface_light_metrics"]["surface_light"]["far_red"]
    assert metrics["baseline_leaf_position_uniformity"]["available"] is True
    assert visualization["field"]["sample_count"] > 0
    assert scene["ppfd_heatmap"]["target_coverage"]["availability"] == "available"
    assert record.artifact("ppfd.csv").data.startswith(
        b"x_m,y_m,z_m,ppfd_umol_m2_s"
    )
    assert record.artifact("ppfd-heatmap.png").data.startswith(b"\x89PNG")
    assert record.artifact("ppfd-heatmap-overlay.png").data.startswith(b"\x89PNG")
    assert record.viewer_artifact("index.html").media_type.startswith("text/html")
    assert record.viewer_artifact("instances.f32le.bin").data
    assert record.viewer_artifact("fixtures/catalog.v1.json").data
    assert record.scatter_artifact("index.html").media_type.startswith("text/html")


@requires_real_bundles
def test_real_target_scaling_zero_and_capacity_saturation() -> None:
    catalog = _catalog()
    different = catalog.load(
        _selector("conventional", layout="practical", target=125.0)
    )
    zero = catalog.load(
        _selector("conventional", layout="rolling_bench", target=0.0)
    )
    saturated = catalog.load(
        _selector("proposed", target=1_000_000.0)
    )

    assert different.metrics()["achieved_mean_ppfd_umol_m2_s"] == pytest.approx(
        125.0
    )
    assert different.result_payload()["target_adjustment"]["output_limited"] is False
    assert zero.metrics()["achieved_mean_ppfd_umol_m2_s"] == 0.0
    zero_scene = json.loads(zero.viewer_artifact("scene.v1.json").data)
    assert zero_scene["ppfd_heatmap"]["target_coverage"]["availability"] == (
        "available"
    )
    assert zero.artifact("ppfd-heatmap.png").data.startswith(b"\x89PNG")
    assert zero.metrics()["fspm_surface_light_metrics"]["far_red_executed"] is True
    assert all(
        value == 0.0
        for _x, _y, value in struct.iter_unpack(
            "<fff", zero.artifact("ppfd-scatter.f32le.bin").data
        )
    )
    assert saturated.result_payload()["target_adjustment"]["output_limited"] is True
    assert saturated.result_payload()["target_adjustment"]["limit_reason"] == (
        "maximum_output"
    )
    assert saturated.metrics()["target_feasible"] is False
    assert saturated.artifact("ppfd.csv").data.startswith(
        b"x_m,y_m,z_m,ppfd_umol_m2_s"
    )


@requires_real_bundles
def test_explicit_mean_target_is_byte_equivalent_to_existing_default() -> None:
    catalog = _catalog()
    selector = _selector(
        "conventional",
        layout="rolling_bench",
        length=30.0,
        width=15.0,
        target=375.0,
    )
    existing = catalog.load(selector)
    explicit = catalog.load(selector | {"lighting_target_mode": "mean_target"})

    assert existing.playback.derived_playback_identity_sha256 == (
        explicit.playback.derived_playback_identity_sha256
    )
    assert existing.playback.presentation_identity_sha256 == (
        explicit.playback.presentation_identity_sha256
    )
    assert existing.result_payload() == explicit.result_payload()
    assert existing.metrics() == explicit.metrics()
    for name in (
        "ppfd.csv",
        "ppfd-scatter.f32le.bin",
        "ppfd-heatmap.png",
        "ppfd-heatmap-overlay.png",
        "visualization.json",
    ):
        assert existing.artifact(name).data == explicit.artifact(name).data


@requires_real_bundles
@pytest.mark.parametrize(
    ("system", "layout"),
    (
        ("proposed", None),
        ("conventional", "practical"),
        ("conventional", "rolling_bench"),
    ),
)
def test_authenticated_target_capped_contract_matrix(
    system: str,
    layout: str | None,
) -> None:
    catalog = _catalog()
    mean = catalog.load(_selector(system, layout=layout, target=250.0))
    base_identity = mean.playback.canonical_bundle_identity_sha256
    base_maximum = mean.metrics()["achieved_maximum_ppfd_umol_m2_s"]
    assert isinstance(base_maximum, float)
    full_control = json.loads(mean.artifact("target-control.json").data)
    full_maximum = full_control["full_output_maximum_ppfd_umol_m2_s"]

    selections = {
        "zero": 0.0,
        "below_base": base_maximum * 0.5,
        "at_base": base_maximum,
        "above_base": base_maximum * 1.5,
        "above_capacity": full_maximum * 1.25,
    }
    views = {
        name: catalog.load(
            _selector(
                system,
                layout=layout,
                target=target,
                lighting_target_mode="target_capped",
            )
        )
        for name, target in selections.items()
    }

    assert all(
        view.playback.canonical_bundle_identity_sha256 == base_identity
        for view in views.values()
    )
    assert all(
        view.result_payload()["lighting_target_mode"] == "target_capped"
        for view in views.values()
    )
    assert views["zero"].metrics()["achieved_maximum_ppfd_umol_m2_s"] == 0.0
    assert views["at_base"].artifact("ppfd.csv").data == (
        mean.artifact("ppfd.csv").data
    )
    for name, cap in selections.items():
        result = views[name].result_payload()
        metrics = views[name].metrics()
        adjustment = result["target_adjustment"]
        assert adjustment["schema_id"] == (
            "fspm-optics.precomputed-authenticated-target-capped-playback"
        )
        assert adjustment["schema_version"] == 1
        assert adjustment["requested_cap_ppfd_umol_m2_s"] == cap
        assert adjustment["cap_compliant"] is True
        assert metrics["achieved_maximum_ppfd_umol_m2_s"] <= cap + 1e-6
        assert metrics["lighting_target_mode"] == "target_capped"
        assert metrics["requested_sampled_ppfd_cap_umol_m2_s"] == cap
        assert result["source_runtime_required"] is False
        visualization = json.loads(
            views[name].artifact("visualization.json").data
        )
        assert visualization["display"][
            "requested_sampled_ppfd_cap_umol_m2_s"
        ] == cap
        scene = json.loads(views[name].viewer_artifact("scene.v1.json").data)
        coverage = scene["ppfd_heatmap"]["target_coverage"]
        assert coverage["reference"] == {
            "ppfd_umol_m2_s": cap,
            "source": "requested_lighting_target",
            "policy_mode": "automatic",
        }
    saturated = views["above_capacity"].result_payload()["target_adjustment"]
    assert saturated["output_fraction"] == 1.0
    assert saturated["maximum_output_saturated"] is True
    assert saturated["feasible"] is True
    assert saturated["infeasibility"] is None
    assert saturated["cap_binding"] is False
    if system == "conventional":
        for view in views.values():
            adjustment = view.result_payload()["target_adjustment"]
            assert adjustment["output_fraction"] <= (
                adjustment["capped_factor_before_native_quantization"]
            )


@requires_real_bundles
def test_target_capped_identity_is_deterministic_policy_and_presentation_bound() -> None:
    catalog = _catalog()
    selector = _selector(
        "proposed",
        length=30.0,
        width=15.0,
        target=300.0,
        lighting_target_mode="target_capped",
    )
    repeated = (catalog.load(selector), catalog.load(selector))
    mean = catalog.load(selector | {"lighting_target_mode": "mean_target"})
    portrait = catalog.load(
        selector | {"room_length_ft": 15.0, "room_width_ft": 30.0}
    )

    assert repeated[0].playback.derived_playback_identity_sha256 == (
        repeated[1].playback.derived_playback_identity_sha256
    )
    assert repeated[0].playback.canonical_bundle_identity_sha256 == (
        mean.playback.canonical_bundle_identity_sha256
    )
    assert repeated[0].playback.derived_playback_identity_sha256 != (
        mean.playback.derived_playback_identity_sha256
    )
    assert portrait.playback.canonical_bundle_identity_sha256 == (
        repeated[0].playback.canonical_bundle_identity_sha256
    )
    assert portrait.playback.derived_playback_identity_sha256 != (
        repeated[0].playback.derived_playback_identity_sha256
    )
    adjustment = repeated[0].result_payload()["target_adjustment"]
    assert adjustment["presentation_identity_sha256"] == (
        repeated[0].playback.presentation_identity_sha256
    )
    for key in (
        "fixed_case_binding_identity_sha256",
        "target_control_contract_identity_sha256",
        "canonical_domain_identity_sha256",
    ):
        assert len(adjustment[key]) == 64


@requires_real_bundles
@pytest.mark.parametrize(
    ("system", "layout", "canonical_room", "reversed_room"),
    (
        ("conventional", "practical", (30.0, 15.0), (15.0, 30.0)),
        ("proposed", None, (50.0, 30.0), (30.0, 50.0)),
    ),
)
def test_real_equivalent_room_orders_share_canonical_landscape_presentation(
    system: str,
    layout: str | None,
    canonical_room: tuple[float, float],
    reversed_room: tuple[float, float],
) -> None:
    catalog = _catalog()
    landscape = catalog.load(
        _selector(
            system,
            layout=layout,
            length=canonical_room[0],
            width=canonical_room[1],
            target=125.0,
        )
    )
    reversed_selection = catalog.load(
        _selector(
            system,
            layout=layout,
            length=reversed_room[0],
            width=reversed_room[1],
            target=125.0,
        )
    )

    assert landscape.playback.canonical_bundle_identity_sha256 == (
        reversed_selection.playback.canonical_bundle_identity_sha256
    )
    assert landscape.playback.presentation_identity_sha256 != (
        reversed_selection.playback.presentation_identity_sha256
    )
    assert landscape.playback.derived_playback_identity_sha256 == (
        reversed_selection.playback.derived_playback_identity_sha256
    )
    assert landscape.metrics() == reversed_selection.metrics()
    for name in (
        "ppfd.csv",
        "ppfd-scatter.f32le.bin",
        "ppfd-heatmap.png",
        "ppfd-heatmap-overlay.png",
    ):
        assert landscape.artifact(name).data == reversed_selection.artifact(name).data
    assert landscape.playback.plant_instance_translations() == (
        reversed_selection.playback.plant_instance_translations()
    )
    assert landscape.playback.fixture_transform_payloads() == (
        reversed_selection.playback.fixture_transform_payloads()
    )
    for name in (
        "instances.f32le.bin",
        "profiles/rex_juvenile_preheading_12leaf_v1/geometry.glb",
        "profiles/rex_juvenile_preheading_12leaf_v1/receivers.f32le.bin",
        "fixtures/catalog.v1.json",
        "ppfd-heatmap/visualization.json",
        "ppfd-heatmap/ppfd-scatter.f32le.bin",
    ):
        assert landscape.viewer_artifact(name).data == (
            reversed_selection.viewer_artifact(name).data
        )

    landscape_visualization = json.loads(
        landscape.artifact("visualization.json").data
    )
    reversed_visualization = json.loads(
        reversed_selection.artifact("visualization.json").data
    )
    landscape_provenance = landscape_visualization.pop("requested_orientation")
    reversed_provenance = reversed_visualization.pop("requested_orientation")
    landscape_visualization.pop("run_id")
    reversed_visualization.pop("run_id")
    assert landscape_visualization == reversed_visualization

    landscape_scene = json.loads(landscape.viewer_artifact("scene.v1.json").data)
    reversed_scene = json.loads(
        reversed_selection.viewer_artifact("scene.v1.json").data
    )
    landscape_scene.pop("requested_orientation")
    reversed_scene.pop("requested_orientation")
    assert landscape_scene == reversed_scene
    assert reversed_scene["requested_room"] == {
        "length_ft": canonical_room[0],
        "length_m": canonical_room[0] * 0.3048,
        "width_ft": canonical_room[1],
        "width_m": canonical_room[1] * 0.3048,
    }
    assert reversed_scene["ppfd_heatmap"]["target_coverage"]["availability"] == (
        "available"
    )
    assert reversed_provenance["coordinate_frame"] == reversed_scene[
        "aligned_simulation_room"
    ]["coordinate_frame"]
    assert reversed_provenance["requested_room_ft"] == {
        "length": reversed_room[0],
        "width": reversed_room[1],
    }
    assert landscape_provenance["canonical_display_room_ft"] == (
        reversed_provenance["canonical_display_room_ft"]
    ) == {"length": canonical_room[0], "width": canonical_room[1]}
    for name in (
        "simulation_to_requested_rotation_degrees_about_z",
        "viewer_global_rotation_degrees_about_y",
        "heatmap_counterclockwise_quarter_turns",
    ):
        assert landscape_provenance[name] == reversed_provenance[name] == 0

    landscape_fit = json.loads(
        landscape.artifact("natural-fit-layout.json").data
    )
    reversed_fit = json.loads(
        reversed_selection.artifact("natural-fit-layout.json").data
    )
    landscape_fit.pop("requested_orientation")
    reversed_fit.pop("requested_orientation")
    assert landscape_fit == reversed_fit


@requires_real_bundles
def test_real_aisle_selection_resolves_a_distinct_authenticated_bundle() -> None:
    catalog = _catalog()
    without_aisle = catalog.load(_selector("proposed", aisle=False))
    with_aisle = catalog.load(_selector("proposed", aisle=True))
    scene = json.loads(with_aisle.viewer_artifact("scene.v1.json").data)

    assert "aisle-off" in without_aisle.resolution.case.case_id
    assert "aisle-on" in with_aisle.resolution.case.case_id
    assert without_aisle.playback.canonical_bundle_identity_sha256 != (
        with_aisle.playback.canonical_bundle_identity_sha256
    )
    assert scene["active_domain"]["enabled"] is True
    assert scene["active_domain"]["aisle"]["width_ft_per_wall"] == 2.0


@requires_real_bundles
def test_precomputed_api_never_submits_native_work_and_rejects_client_paths(
    tmp_path: Path,
) -> None:
    native_calls: list[object] = []

    def forbidden_native(**kwargs: object):
        native_calls.append(kwargs)
        raise AssertionError("precomputed playback invoked native work")

    app = create_app(
        runtime_root=tmp_path / "managed-runtime",
        precomputed_root=REAL_PRECOMPUTED_ROOT,
        run_executor=forbidden_native,
    )
    async def scenario() -> None:
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://testserver",
            ) as client:
                await exercise_api(client)

    async def exercise_api(client: httpx.AsyncClient) -> None:
        availability = await client.get("/api/precomputed/availability")
        assert availability.status_code == 200
        completed = await _submit_and_wait(
            client,
            _selector("conventional", layout="practical", target=125.0),
            idempotency_key="precomputed-api-0001",
        )
        assert completed["state"] == "succeeded"
        result = completed["result"]
        playback_id = result["run_id"]
        assert (await client.get(result["metrics_url"])).status_code == 200
        assert (await client.get(result["manifest_url"])).json()[
            "source_runtime_required"
        ] is False
        for url in (
            result["ppfd_csv_url"],
            result["ppfd_heatmap_url"],
            result["ppfd_heatmap_overlay_url"],
            result["visualization_metadata_url"],
            (
                f"/api/precomputed/playbacks/{playback_id}/artifacts/"
                "ppfd-scatter.f32le.bin"
            ),
            result["plant_layout_viewer_url"],
            result["ppfd_scatter_viewer_url"],
            f"/precomputed/{playback_id}/viewer/scene.v1.json",
            f"/precomputed/{playback_id}/viewer/ppfd-heatmap/visualization.json",
            f"/precomputed/{playback_id}/viewer/fixtures/catalog.v1.json",
        ):
            assert (await client.get(url)).status_code == 200

        unsafe = await client.post(
            "/api/precomputed/playbacks",
            json=_selector("proposed") | {"precomputed_root": "/tmp/other"},
        )
        assert unsafe.status_code == 422
        assert unsafe.json()["error"]["code"] == "invalid_precomputed_selector"

        hps = await _submit_and_wait(
            client,
            _selector("hps", target=None),
            idempotency_key="precomputed-api-0002",
        )
        assert hps["result"]["system_id"] == "hps"

        rolling = await _submit_and_wait(
            client,
            _selector(
                "conventional",
                layout="rolling_bench",
                length=30.0,
                width=50.0,
            ),
            idempotency_key="precomputed-api-0003",
        )
        assert rolling["result"]["system_id"] == "conventional"

    asyncio.run(scenario())
    assert native_calls == []
    assert app.state.jobs._jobs == {}
    assert not (tmp_path / ".fspm-optics-runtime").exists()


def test_precomputed_root_is_server_configured_and_symlinks_fail_closed(
    tmp_path: Path,
) -> None:
    real = tmp_path / "real"
    real.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError, match="symlink"):
        PrecomputedCatalog(linked, repository_root=REPOSITORY_ROOT)

    catalog = PrecomputedCatalog(real, repository_root=REPOSITORY_ROOT)
    with pytest.raises(PrecomputedPlaybackError, match="Unsupported"):
        catalog.load(_selector("proposed") | {"bundle_path": "/tmp/file"})

    with pytest.raises(PrecomputedPlaybackError, match="mean_target or target_capped"):
        catalog.load(
            _selector("proposed") | {"lighting_target_mode": "crafted"}
        )
    with pytest.raises(PrecomputedPlaybackError, match="target or target policy"):
        catalog.load(
            _selector("hps", target=None)
            | {"lighting_target_mode": "target_capped"}
        )


@requires_real_bundles
def test_catalog_initialization_snapshot_and_bounded_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inspections = 0
    base_loads = 0
    actual_inspect = web_precomputed.inspect_fixed_sweep
    actual_load = web_precomputed.load_compact_bundle

    def counted_inspect(*args: object, **kwargs: object):
        nonlocal inspections
        inspections += 1
        return actual_inspect(*args, **kwargs)

    def counted_load(*args: object, **kwargs: object):
        nonlocal base_loads
        base_loads += 1
        return actual_load(*args, **kwargs)

    monkeypatch.setattr(web_precomputed, "inspect_fixed_sweep", counted_inspect)
    monkeypatch.setattr(web_precomputed, "load_compact_bundle", counted_load)
    catalog = PrecomputedCatalog(
        REAL_PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
        base_bundle_cache_size=1,
        playback_cache_size=2,
        max_sessions=4,
    )

    first_availability = catalog.availability()
    second_availability = catalog.availability()
    assert inspections == 1
    assert first_availability == second_availability
    assert first_availability is not second_availability
    assert catalog.ready is True

    selector = _selector("proposed", target=125.0)
    first = catalog.load(selector)
    repeated = catalog.load(selector)
    alternate = catalog.load(_selector("proposed", target=130.0))
    assert first is repeated
    assert first.playback.canonical.base is alternate.playback.canonical.base
    assert base_loads == 1
    assert catalog.cache_stats() == {
        "base_bundles": 1,
        "playbacks": 2,
        "sessions": 2,
        "base_loads_in_flight": 0,
        "playback_loads_in_flight": 0,
    }


@requires_real_bundles
def test_catalog_singleflight_and_session_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = threading.Event()
    release = threading.Event()
    base_loads = 0
    actual_load = web_precomputed.load_compact_bundle

    def coordinated_load(*args: object, **kwargs: object):
        nonlocal base_loads
        base_loads += 1
        started.set()
        assert release.wait(timeout=10)
        return actual_load(*args, **kwargs)

    monkeypatch.setattr(web_precomputed, "load_compact_bundle", coordinated_load)
    now = [0.0]
    catalog = PrecomputedCatalog(
        REAL_PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
        playback_cache_size=1,
        max_sessions=2,
        session_ttl_seconds=10.0,
        clock=lambda: now[0],
    )
    selector = _selector("conventional", layout="practical", target=250.0)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(catalog.load, selector)
        assert started.wait(timeout=10)
        second_future = executor.submit(catalog.load, selector)
        release.set()
        first = first_future.result(timeout=10)
        second = second_future.result(timeout=10)
    assert first is second
    assert base_loads == 1

    now[0] = 11.0
    with pytest.raises(PrecomputedPlaybackNotFound):
        catalog.get(first.playback_id)
    assert catalog.cache_stats()["sessions"] == 0
    assert catalog.cache_stats()["playbacks"] == 0


@requires_real_bundles
def test_catalog_pins_recent_playback_and_rejects_capacity_cleanly() -> None:
    now = [0.0]
    catalog = PrecomputedCatalog(
        REAL_PRECOMPUTED_ROOT,
        repository_root=REPOSITORY_ROOT,
        playback_cache_size=1,
        max_sessions=2,
        clock=lambda: now[0],
    )
    first = catalog.load(_selector("proposed", target=250.0))

    with pytest.raises(PrecomputedPlaybackError) as error:
        catalog.load(_selector("proposed", target=125.0))
    assert error.value.status_code == 503
    assert error.value.code == "playback_capacity_exhausted"

    assert catalog.get(first.playback_id) is first
    second = catalog.load(_selector("proposed", target=125.0))
    assert second.playback_id != first.playback_id

    with pytest.raises(PrecomputedPlaybackError) as second_error:
        catalog.load(_selector("proposed", target=130.0))
    assert second_error.value.code == "playback_capacity_exhausted"

    now[0] = web_precomputed.INITIAL_PLAYBACK_PIN_SECONDS + 0.1
    third = catalog.load(_selector("proposed", target=130.0))
    assert third.playback_id != second.playback_id
    assert catalog.cache_stats()["playbacks"] == 1
