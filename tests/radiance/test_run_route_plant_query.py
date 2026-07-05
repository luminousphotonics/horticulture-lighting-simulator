from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from tests.radiance.runtime_env import configure_test_runtime

configure_test_runtime()

from rad_rebuild.radiance.backend import env as backend_env  # noqa: E402
from rad_rebuild.radiance.backend.models import RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.routes import runs  # noqa: E402
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_LOCAL  # noqa: E402


def _request(query: dict[str, str]) -> Any:
    return SimpleNamespace(query_params=query)


def test_run_route_plant_query_overrides_feed_runtime_env() -> None:
    req = RadianceRunRequest(
        action="all",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
        plants_enabled=False,
    )

    updated = runs._apply_run_plant_query_overrides(
        req,
        _request(
            {
                "plants_enabled": "true",
                "plant_seed": "99",
                "plant_rows": "1",
                "plant_columns": "2",
                "plant_spacing_m": "0.42",
                "plant_height_m": "0.2",
                "plant_canopy_radius_m": "0.22",
                "plant_leaf_count": "8",
                "plant_growth_stage": "0.75",
            }
        ),
    )

    assert updated.plants_enabled is True
    assert updated.plant_seed == 99
    assert updated.plant_rows == 1
    assert updated.plant_columns == 2

    env = backend_env._env_smd(updated)
    assert env["FSPM_PLANTS_ENABLED"] == "1"
    assert env["FSPM_PLANT_SEED"] == "99"
    assert env["FSPM_PLANT_ROWS"] == "1"
    assert env["FSPM_PLANT_COLUMNS"] == "2"


def test_run_route_absent_plant_query_preserves_no_plant_default() -> None:
    req = RadianceRunRequest(
        action="all",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
    )

    updated = runs._apply_run_plant_query_overrides(req, _request({}))

    assert updated == req
    assert "FSPM_PLANTS_ENABLED" not in backend_env._env_smd(updated)


def test_run_route_invalid_plant_query_value_is_public_400() -> None:
    req = RadianceRunRequest(
        action="all",
        execution_mode=EXECUTION_MODE_LIVE_LOCAL,
    )

    with pytest.raises(HTTPException) as exc_info:
        runs._apply_run_plant_query_overrides(
            req,
            _request({"plants_enabled": "true", "plant_rows": "not-an-int"}),
        )

    assert exc_info.value.status_code == 400
