from __future__ import annotations

from types import SimpleNamespace
from typing import cast

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend.routes import assembly as assembly_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import artifacts as artifacts_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import metrics as metrics_route  # noqa: E402


class _FakeRequest:
    method = "GET"

    def __init__(self, query_params: dict[str, str] | None = None) -> None:
        self.query_params = query_params or {"session_id": "matched-ppe-test"}
        self.headers: dict[str, str] = {}
        self.state = SimpleNamespace()
        self.app = SimpleNamespace(state=SimpleNamespace())


def test_radiance_images_preserves_match_system_ppe_in_generated_image_urls(monkeypatch, tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "ppfd_map.txt").write_text("0 0 0 275\n", encoding="utf-8")
    visuals = workspace / "visuals"
    visuals.mkdir()
    (visuals / "ppfd_heatmap_overlay.png").write_bytes(b"png")
    (visuals / "ppfd_heatmap_annotated.png").write_bytes(b"png")

    monkeypatch.setattr(artifacts_route, "maybe_cleanup_runtime_state", lambda: None)
    monkeypatch.setattr(artifacts_route, "authorize_workspace_from_request", lambda _request, _req: workspace)
    monkeypatch.setattr(artifacts_route, "_ensure_visuals", lambda _req, _env, _workspace: visuals)

    result = artifacts_route.radiance_images(
        cast(object, _FakeRequest({"session_id": "matched-ppe-test"})),
        mode="SMD",
        execution_mode="live_local",
        target_ppfd=275,
        match_system_ppe=True,
        length_ft=10,
        width_ft=10,
    )

    assert result["overlay"] is not None
    assert "match_system_ppe=true" in result["overlay"]


def test_radiance_metrics_authorizes_against_match_system_ppe_request(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def fake_get_metrics_payload(req, workspace_root):
        captured["req"] = req
        captured["workspace_root"] = workspace_root
        return {"metrics": {"ok": True}, "cost_estimate": None}

    monkeypatch.setattr(metrics_route, "maybe_cleanup_runtime_state", lambda: None)
    monkeypatch.setattr(metrics_route, "authorize_workspace_from_request", lambda _request, _req: workspace)
    monkeypatch.setattr(metrics_route, "get_metrics_payload", fake_get_metrics_payload)

    result = metrics_route.radiance_metrics(
        cast(object, _FakeRequest({"session_id": "matched-ppe-test", "match_system_ppe": "true"})),
        mode="SMD",
        execution_mode="live_local",
        target_ppfd=275,
        length_ft=10,
        width_ft=10,
    )

    assert result["metrics"] == {"ok": True}
    assert getattr(captured["req"], "match_system_ppe") is True


def test_radiance_assembly_scene_authorizes_against_match_system_ppe_request(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "ppfd_map.txt").write_text("0 0 0 275\n", encoding="utf-8")

    def fake_authorize(_request, req):
        captured["req"] = req
        return workspace

    monkeypatch.setattr(assembly_route, "maybe_cleanup_runtime_state", lambda: None)
    monkeypatch.setattr(assembly_route, "authorize_workspace_from_request", fake_authorize)
    monkeypatch.setattr(assembly_route, "build_assembly_scene", lambda _workspace, _req: {"ok": True})

    request = _FakeRequest({"session_id": "matched-ppe-test", "match_system_ppe": "true"})
    request.method = "HEAD"

    result = assembly_route.radiance_assembly_scene(
        cast(object, request),
        mode="SMD",
        execution_mode="live_local",
        target_ppfd=275,
        match_system_ppe=True,
        length_ft=10,
        width_ft=10,
    )

    assert result.status_code == 200
    assert getattr(captured["req"], "match_system_ppe") is True


def test_radiance_scatter_authorizes_against_match_system_ppe_request(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "ppfd_map.txt").write_text("0 0 0 275\n", encoding="utf-8")
    scatter_path = workspace / "scatter.html"
    scatter_path.write_text("<html></html>", encoding="utf-8")

    def fake_authorize(_request, req):
        captured["req"] = req
        return workspace

    monkeypatch.setattr(artifacts_route, "maybe_cleanup_runtime_state", lambda: None)
    monkeypatch.setattr(artifacts_route, "authorize_workspace_from_request", fake_authorize)
    monkeypatch.setattr(artifacts_route, "_ensure_scatter", lambda _req, _env, _workspace: scatter_path)

    request = _FakeRequest({"session_id": "matched-ppe-test", "match_system_ppe": "true"})
    request.method = "HEAD"

    result = artifacts_route.radiance_scatter(
        cast(object, request),
        mode="SMD",
        execution_mode="live_local",
        target_ppfd=275,
        match_system_ppe=True,
        length_ft=10,
        width_ft=10,
    )

    assert result.status_code == 200
    assert getattr(captured["req"], "match_system_ppe") is True


def test_radiance_scatter_authorizes_against_match_system_ppe_and_plant_request(monkeypatch, tmp_path) -> None:
    captured: dict[str, object] = {}
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "ppfd_map.txt").write_text("0 0 0 275\n", encoding="utf-8")
    scatter_path = workspace / "scatter.html"
    scatter_path.write_text("<html></html>", encoding="utf-8")

    def fake_authorize(_request, req):
        captured["req"] = req
        return workspace

    monkeypatch.setattr(artifacts_route, "maybe_cleanup_runtime_state", lambda: None)
    monkeypatch.setattr(artifacts_route, "authorize_workspace_from_request", fake_authorize)
    monkeypatch.setattr(artifacts_route, "_ensure_scatter", lambda _req, _env, _workspace: scatter_path)

    request = _FakeRequest(
        {
            "session_id": "matched-ppe-test",
            "match_system_ppe": "true",
            "plants_enabled": "true",
            "plant_seed": "42",
            "plant_rows": "2",
            "plant_columns": "2",
            "plant_spacing_m": "0.3",
            "plant_height_m": "0.16",
            "plant_canopy_radius_m": "0.18",
            "plant_leaf_count": "12",
            "plant_growth_stage": "1",
        }
    )
    request.method = "HEAD"

    result = artifacts_route.radiance_scatter(
        cast(object, request),
        mode="SMD",
        execution_mode="live_local",
        target_ppfd=275,
        match_system_ppe=True,
        length_ft=10,
        width_ft=10,
    )

    assert result.status_code == 200
    assert getattr(captured["req"], "match_system_ppe") is True
    assert getattr(captured["req"], "plants_enabled") is True
    assert getattr(captured["req"], "plant_rows") == 2
