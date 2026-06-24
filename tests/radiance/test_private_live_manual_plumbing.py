from __future__ import annotations

from types import SimpleNamespace

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend.env import _base_env  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.routes.artifacts import (  # noqa: E402
    _apply_artifact_plant_query_overrides,
    _plant_query_fragment,
)
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_LOCAL  # noqa: E402


def _request(query: dict[str, str]) -> SimpleNamespace:
    return SimpleNamespace(query_params=query)


def test_private_photometry_env_sets_live_generator_ies_paths(monkeypatch) -> None:
    monkeypatch.setenv("RAD_REBUILD_PRIVATE_CONVENTIONAL_IES", "/tmp/private/qube.ies")
    monkeypatch.setenv("RAD_REBUILD_PRIVATE_HPS_IES", "/tmp/private/hps.ies")

    env = _base_env()

    assert env["SPYDR_IES_PATH"] == "/tmp/private/qube.ies"
    assert env["HPS_IES_PATH"] == "/tmp/private/hps.ies"


def test_artifact_routes_apply_plant_query_overrides() -> None:
    req = RadianceRunRequest(
        action="visualize",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=False,
    )

    updated = _apply_artifact_plant_query_overrides(
        req,
        _request(
            {
                "plants_enabled": "true",
                "plant_seed": "77",
                "plant_rows": "1",
                "plant_columns": "2",
                "plant_leaf_count": "5",
                "plant_spacing_m": "0.34",
            }
        ),
    )

    assert updated.plants_enabled is True
    assert updated.plant_seed == 77
    assert updated.plant_rows == 1
    assert updated.plant_columns == 2
    assert updated.plant_leaf_count == 5
    assert updated.plant_spacing_m == 0.34


def test_artifact_image_url_fragment_preserves_plant_workspace_key() -> None:
    req = RadianceRunRequest(
        action="visualize",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=True,
        plant_seed=77,
        plant_rows=1,
        plant_columns=2,
        plant_leaf_count=5,
        plant_spacing_m=0.34,
    )

    fragment = _plant_query_fragment(req)

    assert "plants_enabled=true" in fragment
    assert "plant_seed=77" in fragment
    assert "plant_rows=1" in fragment
    assert "plant_columns=2" in fragment
    assert "plant_leaf_count=5" in fragment
    assert "plant_spacing_m=0.34" in fragment
