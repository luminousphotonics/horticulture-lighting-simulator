from __future__ import annotations

import math
import struct
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import Response

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.backend.models import AssemblyPhotometricLayerResponse, RadianceRunRequest  # noqa: E402
from rad_rebuild.radiance.backend.photometric_layer import (  # noqa: E402
    PhotometricLayerError,
    build_assembly_photometric_layer,
)
from rad_rebuild.radiance.backend.routes import assembly as assembly_route  # noqa: E402
from rad_rebuild.radiance.backend.routes import photometrics as photometrics_route  # noqa: E402
from rad_rebuild.radiance.backend.workspace import (  # noqa: E402
    allocate_workspace_for_run,
    commit_staged_workspace,
)
from rad_rebuild.radiance.config import EXECUTION_MODE_LIVE_DOCKER, MODE_COMPETITOR, MODE_HPS, MODE_SMD  # noqa: E402


class _FakeRequest:
    headers: dict[str, str]

    def __init__(
        self,
        *,
        session_id: str = "photometric-layer",
        token: str | None = None,
        method: str = "GET",
        query: dict[str, str] | None = None,
    ) -> None:
        self.query_params = {"session_id": session_id}
        if token is not None:
            self.query_params["artifact_token"] = token
        if query:
            self.query_params.update(query)
        self.headers = {}
        self.method = method


def _request(
    *,
    session_id: str = "photometric-layer",
    token: str | None = None,
    method: str = "GET",
    query: dict[str, str] | None = None,
) -> Request:
    return cast(
        Request,
        _FakeRequest(session_id=session_id, token=token, method=method, query=query),
    )


def _smd_req(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_SMD,
        "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
        "length_ft": 10,
        "width_ft": 10,
        "target_ppfd": 1000,
        "mount_z_m": 0.4572,
    }
    data.update(overrides)
    return RadianceRunRequest.model_validate(data)


def _competitor_req(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_COMPETITOR,
        "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
        "length_ft": 10,
        "width_ft": 10,
        "target_ppfd": 1000,
        "w_min": 10,
        "competitor_layout": "full",
    }
    data.update(overrides)
    return RadianceRunRequest.model_validate(data)


def _hps_req(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_HPS,
        "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
        "length_ft": 10,
        "width_ft": 10,
        "target_ppfd": 1000,
        "w_min": 10,
        "hps_coverage_ft": 4,
        "hps_ies_variant": "karma",
    }
    data.update(overrides)
    return RadianceRunRequest.model_validate(data)


def _write_map(workspace_root: Path, text: str) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)
    (workspace_root / "ppfd_map.txt").write_text(text, encoding="utf-8")


def _valid_grid_text() -> str:
    return "\n".join(
        [
            "2 1 0.005 60",
            "0 0 0.005 10",
            "1 0 0.005 20",
            "2 0 0.005 30",
            "0 1 0.005 40",
            "1 1 0.005 50",
        ]
    )


def _metadata_from_workspace(workspace_root: Path) -> dict[str, Any]:
    with patch.object(
        photometrics_route,
        "authorize_workspace_from_request",
        return_value=workspace_root,
    ):
        return cast(
            dict[str, Any],
            photometrics_route.radiance_assembly_photometric_layer(
                _request(),
                Response(),
                mode=MODE_SMD,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            ),
        )


def _metadata_from_workspace_for_mode(workspace_root: Path, mode: str) -> dict[str, Any]:
    with patch.object(
        photometrics_route,
        "authorize_workspace_from_request",
        return_value=workspace_root,
    ):
        return cast(
            dict[str, Any],
            photometrics_route.radiance_assembly_photometric_layer(
                _request(),
                Response(),
                mode=mode,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
                w_min=10,
                competitor_layout="full",
                hps_coverage_ft=4,
                hps_ies_variant="karma",
            ),
        )


def _error_detail(exc: HTTPException) -> dict[str, Any]:
    return cast(dict[str, Any], exc.detail)


def test_valid_grid_returns_strict_metadata(tmp_path: Path) -> None:
    _write_map(tmp_path, _valid_grid_text())

    payload = _metadata_from_workspace(tmp_path)

    AssemblyPhotometricLayerResponse.model_validate(payload)
    assert payload["schema_version"] == 1
    assert payload["mode"] == MODE_SMD
    assert payload["mode_label"] == "Proposed LED System"
    assert payload["units"] == "µmol/m²/s"
    assert payload["grid_width"] == 3
    assert payload["grid_height"] == 2
    assert payload["value_count"] == 6
    assert payload["bounds_m"] == {
        "x_min": 0.0,
        "x_max": 2.0,
        "y_min": 0.0,
        "y_max": 1.0,
        "z_m": 0.005,
    }
    assert payload["min_ppfd"] == 10.0
    assert payload["max_ppfd"] == 60.0
    assert payload["mean_ppfd"] == 35.0
    assert payload["target_ppfd"] == 1000.0
    assert payload["encoding"] == "float32-le"
    assert payload["orientation"]["value_index"] == "row * grid_width + column"
    assert payload["warnings"] == []


@pytest.mark.parametrize(
    ("mode", "expected_label"),
    [
        (MODE_COMPETITOR, "Conventional LED System"),
        (MODE_HPS, "1000W HPS"),
    ],
)
def test_supported_non_smd_modes_return_strict_metadata(
    tmp_path: Path,
    mode: str,
    expected_label: str,
) -> None:
    _write_map(tmp_path, _valid_grid_text())

    payload = _metadata_from_workspace_for_mode(tmp_path, mode)

    AssemblyPhotometricLayerResponse.model_validate(payload)
    assert payload["schema_version"] == 1
    assert payload["mode"] == mode
    assert payload["mode_label"] == expected_label
    assert payload["grid_width"] == 3
    assert payload["grid_height"] == 2
    assert payload["value_count"] == 6
    assert payload["encoding"] == "float32-le"
    assert payload["colormap"]["name"] == "viridis"
    assert payload["color_scale"] == {
        "vmin": 0.0,
        "vmax": 235.0,
        "source": "simulation-visualization-mean-ppfd",
        "clamp": True,
    }
    assert payload["orientation"]["value_index"] == "row * grid_width + column"


@pytest.mark.parametrize("mode", [MODE_COMPETITOR, MODE_HPS])
def test_supported_non_smd_binary_endpoint_returns_float32_values(
    tmp_path: Path,
    mode: str,
) -> None:
    _write_map(tmp_path, _valid_grid_text())

    with patch.object(
        photometrics_route,
        "authorize_workspace_from_request",
        return_value=tmp_path,
    ):
        response = photometrics_route.radiance_assembly_photometric_layer_binary(
            _request(),
            mode=mode,
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=10,
            width_ft=10,
            target_ppfd=1000,
            w_min=10,
            competitor_layout="full",
            hps_coverage_ft=4,
            hps_ies_variant="karma",
        )

    assert response.media_type == "application/octet-stream"
    assert response.body == struct.pack("<6f", 10.0, 20.0, 30.0, 40.0, 50.0, 60.0)


@pytest.mark.parametrize(
    ("mode", "req"),
    [
        (MODE_COMPETITOR, _competitor_req()),
        (MODE_HPS, _hps_req()),
    ],
)
def test_supported_non_smd_committed_workspace_requires_artifact_token(
    mode: str,
    req: RadianceRunRequest,
) -> None:
    session_id = f"photometric-token-{mode.lower().replace(' ', '-')}"
    lease = allocate_workspace_for_run(session_id, req)
    _write_map(lease.staging_workspace, _valid_grid_text())
    commit_staged_workspace(lease, {"runtime": "photometric-test"}, req)

    payload = photometrics_route.radiance_assembly_photometric_layer(
        _request(session_id=session_id, token=lease.artifact_token),
        Response(),
        mode=mode,
        execution_mode=EXECUTION_MODE_LIVE_DOCKER,
        length_ft=10,
        width_ft=10,
        target_ppfd=1000,
        w_min=10,
        competitor_layout="full",
        hps_coverage_ft=4,
        hps_ies_variant="karma",
    )

    assert isinstance(payload, dict)
    assert payload["mode"] == mode
    assert payload["value_count"] == 6

    with pytest.raises(HTTPException) as missing:
        photometrics_route.radiance_assembly_photometric_layer(
            _request(session_id=session_id),
            Response(),
            mode=mode,
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=10,
            width_ft=10,
            target_ppfd=1000,
            w_min=10,
            competitor_layout="full",
            hps_coverage_ft=4,
            hps_ies_variant="karma",
        )

    assert missing.value.status_code == 403


def test_six_decimal_centered_grid_spacing_jitter_is_accepted(tmp_path: Path) -> None:
    _write_map(
        tmp_path,
        "\n".join(
            f"{x} {y} 0.005 {100 + row_index * 10 + column_index}"
            for row_index, y in enumerate(["0", "0.144666", "0.289333"])
            for column_index, x in enumerate(["0", "0.247985", "0.495971"])
        ),
    )

    payload = _metadata_from_workspace(tmp_path)

    assert payload["grid_width"] == 3
    assert payload["grid_height"] == 3
    assert payload["value_count"] == 9
    assert payload["bounds_m"] == {
        "x_min": 0.0,
        "x_max": 0.495971,
        "y_min": 0.0,
        "y_max": 0.289333,
        "z_m": 0.005,
    }
    assert payload["warnings"] == []


def test_binary_endpoint_returns_float32_le_row_major_values(tmp_path: Path) -> None:
    _write_map(tmp_path, _valid_grid_text())

    with patch.object(
        photometrics_route,
        "authorize_workspace_from_request",
        return_value=tmp_path,
    ):
        response = photometrics_route.radiance_assembly_photometric_layer_binary(
            _request(),
            mode=MODE_SMD,
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=10,
            width_ft=10,
            target_ppfd=1000,
        )

    assert response.media_type == "application/octet-stream"
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert response.body == struct.pack("<6f", 10.0, 20.0, 30.0, 40.0, 50.0, 60.0)


def test_missing_artifact_token_returns_403() -> None:
    session_id = "photometric-token"
    req = _smd_req()
    lease = allocate_workspace_for_run(session_id, req)
    _write_map(lease.staging_workspace, "0 0 0.005 100\n")
    commit_staged_workspace(lease, {"runtime": "photometric-test"}, req)

    with pytest.raises(HTTPException) as missing:
        photometrics_route.radiance_assembly_photometric_layer(
            _request(session_id=session_id),
            Response(),
            mode=MODE_SMD,
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=10,
            width_ft=10,
            target_ppfd=1000,
        )

    assert missing.value.status_code == 403


def test_missing_ppfd_map_returns_404(tmp_path: Path) -> None:
    with patch.object(
        photometrics_route,
        "authorize_workspace_from_request",
        return_value=tmp_path,
    ):
        with pytest.raises(HTTPException) as raised:
            photometrics_route.radiance_assembly_photometric_layer(
                _request(),
                Response(),
                mode=MODE_SMD,
                execution_mode=EXECUTION_MODE_LIVE_DOCKER,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
            )

    assert raised.value.status_code == 404
    assert _error_detail(raised.value)["error"] == "photometric_layer_not_found"


def test_incomplete_grid_returns_409(tmp_path: Path) -> None:
    _write_map(tmp_path, "0 0 0.005 10\n1 0 0.005 20\n0 1 0.005 30\n")

    with pytest.raises(HTTPException) as raised:
        _metadata_from_workspace(tmp_path)

    assert raised.value.status_code == 409
    assert _error_detail(raised.value)["error"] == "photometric_layer_incomplete_grid"


def test_duplicate_grid_cell_returns_409(tmp_path: Path) -> None:
    _write_map(tmp_path, "0 0 0.005 10\n0 0 0.005 20\n")

    with pytest.raises(HTTPException) as raised:
        _metadata_from_workspace(tmp_path)

    assert raised.value.status_code == 409
    assert _error_detail(raised.value)["error"] == "photometric_layer_duplicate_cell"


def test_malformed_or_nonfinite_map_returns_422(tmp_path: Path) -> None:
    _write_map(tmp_path, "0 0 0.005 NaN\n")

    with pytest.raises(HTTPException) as raised:
        _metadata_from_workspace(tmp_path)

    assert raised.value.status_code == 422
    assert _error_detail(raised.value)["error"] == "photometric_layer_malformed"


def test_head_returns_no_body_and_no_store_headers(tmp_path: Path) -> None:
    _write_map(tmp_path, _valid_grid_text())

    with patch.object(
        photometrics_route,
        "authorize_workspace_from_request",
        return_value=tmp_path,
    ):
        response = photometrics_route.radiance_assembly_photometric_layer(
            _request(method="HEAD"),
            Response(),
            mode=MODE_SMD,
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=10,
            width_ft=10,
            target_ppfd=1000,
        )

    assert response.status_code == 200
    assert response.body == b""
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"


def test_metadata_includes_viridis_colormap_info(tmp_path: Path) -> None:
    _write_map(tmp_path, _valid_grid_text())

    payload = _metadata_from_workspace(tmp_path)

    assert payload["colormap"] == {
        "name": "viridis",
        "source": "matplotlib",
        "normalization": "linear-clamped",
    }


def test_color_scale_matches_simulator_mean_ppfd_logic(tmp_path: Path) -> None:
    _write_map(tmp_path, "0 0 0.005 300\n1 0 0.005 500\n0 1 0.005 700\n1 1 0.005 900\n")

    payload = _metadata_from_workspace(tmp_path)

    assert payload["mean_ppfd"] == 600.0
    assert payload["color_scale"] == {
        "vmin": 400.0,
        "vmax": 800.0,
        "source": "simulation-visualization-mean-ppfd",
        "clamp": True,
    }


def test_route_query_canonicalization_matches_assembly_scene_for_smd(tmp_path: Path) -> None:
    _write_map(tmp_path, "0 0 0.005 100\n")
    runtime = tmp_path / "runtime_state"
    runtime.mkdir(parents=True)
    (runtime / "smd_layout.json").write_text(
        (
            '{"version":2,"units":"meters","room":{"L":3.048,"W":3.048},'
            '"z":0.4572,"positions":[{"x":0,"y":0,"z":0.4572}]}'
        ),
        encoding="utf-8",
    )
    captured: dict[str, RadianceRunRequest] = {}

    def capture(name: str):
        def fake_authorize(_request: object, req: RadianceRunRequest) -> Path:
            captured[name] = req
            return tmp_path

        return fake_authorize

    query = {"peak_capping_enabled": "true"}
    with patch.object(
        assembly_route,
        "authorize_workspace_from_request",
        side_effect=capture("assembly"),
    ):
        assembly_route.radiance_assembly_scene(
            _request(query=query),
            mode="proposed",
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=12,
            width_ft=10,
            target_ppfd=900,
            peak_capping_enabled=False,
            competitor_layout="Practical Coverage",
        )

    with patch.object(
        photometrics_route,
        "authorize_workspace_from_request",
        side_effect=capture("photometric"),
    ):
        photometrics_route.radiance_assembly_photometric_layer(
            _request(query=query),
            Response(),
            mode="proposed",
            execution_mode=EXECUTION_MODE_LIVE_DOCKER,
            length_ft=12,
            width_ft=10,
            target_ppfd=900,
            peak_capping_enabled=False,
            competitor_layout="Practical Coverage",
        )

    assert captured["photometric"].model_dump() == captured["assembly"].model_dump()
    assert captured["photometric"].mode == MODE_SMD
    assert captured["photometric"].peak_capping_enabled is True
    assert captured["photometric"].competitor_layout == "full"


def test_unknown_mode_still_fails_cleanly(tmp_path: Path) -> None:
    _write_map(tmp_path, "0 0 0.005 100\n")
    req = RadianceRunRequest.model_construct(mode="Experimental", target_ppfd=1000)

    with pytest.raises(PhotometricLayerError) as raised:
        build_assembly_photometric_layer(tmp_path, req)

    assert raised.value.status_code == 422
    assert raised.value.error == "assembly_photometric_layer_unsupported"
    assert "Experimental" in raised.value.message


def test_slight_z_variation_uses_median_and_warning(tmp_path: Path) -> None:
    _write_map(tmp_path, "0 0 0.005 100\n1 0 0.00505 200\n")

    payload = _metadata_from_workspace(tmp_path)

    assert math.isclose(payload["bounds_m"]["z_m"], 0.005025)
    assert payload["warnings"] == [
        "ppfd_map.txt z values vary slightly; bounds_m.z_m uses the median measurement height."
    ]
