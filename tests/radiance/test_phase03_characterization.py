from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest

from rad_rebuild.radiance.backend.models import RadianceRunRequest
from rad_rebuild.radiance.backend.runtime import pipeline_command, pipeline_shell
from rad_rebuild.radiance.backend.workspace import (
    artifact_key_from_request,
    request_fingerprint,
)
from rad_rebuild.radiance.config import (
    COMPETITOR_LAYOUT_PRACTICAL,
    EXECUTION_MODE_LIVE_DOCKER,
    EXECUTION_MODE_PRECOMPUTED,
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
    QUALITY_PRESET_QUALITY,
    QUALITY_PRESET_STANDARD,
)
from rad_rebuild.radiance.engine.emitters.hps_generation.profile import (
    DEFAULT_IES_VARIANT as DEFAULT_HPS_IES_VARIANT,
    DEFAULT_MOUNT_Z_M as DEFAULT_HPS_MOUNT_Z_M,
    NOMINAL_FIXTURE_PPF_UMOL_S as DEFAULT_HPS_FIXTURE_PPF,
    NOMINAL_INPUT_WATTS as DEFAULT_HPS_INPUT_WATTS,
)
from rad_rebuild.radiance.engine.layout.layout_generator import (
    generate_layout_with_zones,
)
from rad_rebuild.radiance.engine.photometry.ppfd_metrics import compute_ppfd_metrics
from rad_rebuild.radiance.engine.simulation.precomputed_dataset import (
    bundle_mode_dirname,
    bundle_ref,
    load_manifest,
    params_match,
    request_params_for_mode,
    resolve_precomputed_root,
)


def _smd_request(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_SMD,
        "execution_mode": EXECUTION_MODE_PRECOMPUTED,
        "length_ft": 10,
        "width_ft": 10,
        "target_ppfd": 1000,
        "sim_mode": QUALITY_PRESET_STANDARD,
    }
    data.update(overrides)
    return RadianceRunRequest(**cast(Any, data))


def _competitor_request(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_COMPETITOR,
        "execution_mode": EXECUTION_MODE_LIVE_DOCKER,
        "length_ft": 12,
        "width_ft": 10,
        "target_ppfd": 900,
        "sim_mode": QUALITY_PRESET_QUALITY,
        "competitor_layout": COMPETITOR_LAYOUT_PRACTICAL,
        "sp_z_m": 0.6096,
    }
    data.update(overrides)
    return RadianceRunRequest(**cast(Any, data))


def _hps_request(**overrides: object) -> RadianceRunRequest:
    data: dict[str, object] = {
        "action": "all",
        "mode": MODE_HPS,
        "execution_mode": EXECUTION_MODE_PRECOMPUTED,
        "length_ft": 30,
        "width_ft": 10,
        "target_ppfd": 1000,
        "hps_coverage_ft": 4,
        "hps_z_m": DEFAULT_HPS_MOUNT_Z_M,
        "hps_fixture_ppf": DEFAULT_HPS_FIXTURE_PPF,
        "hps_input_watts": DEFAULT_HPS_INPUT_WATTS,
        "hps_ies_variant": DEFAULT_HPS_IES_VARIANT,
    }
    data.update(overrides)
    return RadianceRunRequest(**cast(Any, data))


REQUEST_GOLDENS: tuple[
    tuple[str, Callable[[], RadianceRunRequest], dict[str, Any]], ...
] = (
    (
        "smd_10x10",
        _smd_request,
        {
            "artifact_key": "fp1_194c440ab7b6ed3193e65daef804473d5dc4958c66920cdfee67217d08188c6c",
            "fingerprint": "194c440ab7b6ed3193e65daef804473d5dc4958c66920cdfee67217d08188c6c",
            "mode_dir": "smd",
            "request_params": {
                "basis_backend": "rtrace",
                "dialux_sensor_grid": False,
                "layout_family": "horticultural_tiled_v1",
                "match_system_ppe": False,
                "module_profile": "145led_clear_lid_ptfe_stack_v2",
                "mount_z_m": 0.4572,
                "sensor_grid_profile": "adaptive_centered_v1",
                "sensor_grid_spacing_m": 0.25,
                "smd_base_ring": 0,
                "smd_curve_model": "curve_vf_rel_ppe_v1",
                "smd_model": "curve",
                "subpatch_grid": 1,
            },
        },
    ),
    (
        "competitor_practical_12x10",
        _competitor_request,
        {
            "artifact_key": "fp1_b3035fc65e9cca67363badcc7a488d9ee60cec13ee43ad8db1ee62871001f29a",
            "fingerprint": "b3035fc65e9cca67363badcc7a488d9ee60cec13ee43ad8db1ee62871001f29a",
            "mode_dir": "competitor_practical",
            "request_params": {
                "dialux_sensor_grid": False,
                "gap_source": "smd_exact_tiled_clear_gap_v1",
                "layout_policy": "practical",
                "mount_z_m": 0.4572,
                "outer_margin_in": 1.0,
                "packing_profile": "smd_gap_threshold_v1",
                "sensor_grid_profile": "adaptive_centered_v1",
                "sensor_grid_spacing_m": 0.25,
                "sp_ppe": 2.8,
                "sp_ppf": 2240.0,
                "sp_z_m": 0.6096,
                "subpatch_grid": 1,
            },
        },
    ),
    (
        "hps_30x10",
        _hps_request,
        {
            "artifact_key": "fp1_11d22fc23aa501bb87e503177b0ee0bf19fbf7f2481d177003d0f9fbcb4d9b7e",
            "fingerprint": "11d22fc23aa501bb87e503177b0ee0bf19fbf7f2481d177003d0f9fbcb4d9b7e",
            "mode_dir": "hps_karma_4x4",
            "request_params": {
                "dialux_sensor_grid": False,
                "fixture_profile": "glh_karma_8_hps1000_research_primary_v1",
                "hps_coverage_ft": 4.0,
                "hps_fixture_ppf": 1797.4,
                "hps_ies_variant": "karma",
                "hps_input_watts": 1045.0,
                "hps_z_m": 0.4572,
                "layout_profile": "coverage_grid_margin1_v1",
                "mount_z_m": 0.4572,
                "outer_margin_in": 1.0,
                "output_policy": "fixed_output_v1",
                "sensor_grid_profile": "adaptive_centered_v1",
                "sensor_grid_spacing_m": 0.25,
                "subpatch_grid": 1,
            },
        },
    ),
)


@pytest.mark.radiance
@pytest.mark.parametrize(("name", "factory", "expected"), REQUEST_GOLDENS)
def test_representative_request_golden_contracts(
    name: str,
    factory: Callable[[], RadianceRunRequest],
    expected: dict[str, Any],
) -> None:
    req = factory()

    assert request_fingerprint(req) == expected["fingerprint"], name
    assert artifact_key_from_request(req) == expected["artifact_key"], name
    assert bundle_mode_dirname(req.mode, req) == expected["mode_dir"], name
    assert request_params_for_mode(req) == expected["request_params"], name


LAYOUT_GOLDENS = (
    (
        (10, 10),
        61,
        17,
        {
            "base_n": 5,
            "connector_count": 0,
            "has_rect_extension": False,
            "rect_long_ft": 0.0,
            "rect_offset": 0,
            "square_tile_count": 1,
            "zone_count": 5,
        },
    ),
    (
        (12, 10),
        61,
        17,
        {
            "base_n": 5,
            "connector_count": 0,
            "has_rect_extension": True,
            "rect_long_ft": 12.0,
            "rect_offset": 0,
            "square_tile_count": 0,
            "zone_count": 6,
        },
    ),
    (
        (30, 10),
        171,
        48,
        {
            "base_n": 5,
            "connector_count": 1,
            "has_rect_extension": True,
            "rect_long_ft": 18.0,
            "rect_offset": 4,
            "square_tile_count": 1,
            "zone_count": 12,
        },
    ),
    (
        (10, 30),
        171,
        48,
        {
            "base_n": 5,
            "connector_count": 1,
            "has_rect_extension": True,
            "rect_long_ft": 18.0,
            "rect_offset": 4,
            "square_tile_count": 1,
            "zone_count": 12,
        },
    ),
    (
        (30, 30),
        481,
        127,
        {
            "base_n": 15,
            "connector_count": 0,
            "has_rect_extension": False,
            "rect_long_ft": 0.0,
            "rect_offset": 0,
            "square_tile_count": 1,
            "zone_count": 15,
        },
    ),
)


@pytest.mark.radiance
@pytest.mark.parametrize(
    ("dims", "position_count", "group_count", "topology"), LAYOUT_GOLDENS
)
def test_layout_topology_and_counts_are_golden(
    dims: tuple[int, int],
    position_count: int,
    group_count: int,
    topology: dict[str, object],
) -> None:
    layout = generate_layout_with_zones(*dims)

    assert len(layout["all_positions"]) == position_count
    assert len(layout["module_groups"]) == group_count
    assert layout["topology"] == topology


@pytest.mark.radiance
def test_ppfd_metrics_contract_uses_documented_tolerances() -> None:
    field = np.array([800.0, 900.0, 1000.0, 1100.0, 1200.0])

    metrics = compute_ppfd_metrics(
        field,
        setpoint_ppfd=1000.0,
        canopy_area_m2=9.290304,
        total_input_watts=500.0,
        emitted_ppf_umol_s=12000.0,
        legacy_metrics=True,
    )

    assert metrics["mean"] == pytest.approx(1000.0, abs=1e-12)
    assert metrics["cap_scale"] == pytest.approx(5.0 / 6.0, rel=1e-12)
    assert metrics["mean_at_cap"] == pytest.approx(833.3333333333334, rel=1e-12)
    assert metrics["full_run_deuc_elec"] == pytest.approx(18.580608, abs=1e-9)
    assert metrics["capped_deuc_elec"] == pytest.approx(18.580608, abs=1e-9)
    assert metrics["ppf_ge_90_at_cap"] == pytest.approx(3561.2832000000008, abs=1e-9)
    assert metrics["legacy"]["cv_percent"] == pytest.approx(
        14.142135623730953, rel=1e-12
    )


@pytest.mark.integration
@pytest.mark.radiance
@pytest.mark.parametrize(
    ("mode", "request_factory", "expected_dir"),
    (
        (MODE_SMD, lambda: _smd_request(length_ft=10, width_ft=10), "smd"),
        (
            MODE_COMPETITOR,
            lambda: _competitor_request(
                execution_mode=EXECUTION_MODE_PRECOMPUTED,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
                sp_z_m=0.4572,
            ),
            "competitor_practical",
        ),
        (MODE_HPS, lambda: _hps_request(length_ft=10, width_ft=10), "hps_karma_4x4"),
    ),
)
def test_committed_precomputed_manifests_match_request_params(
    mode: str,
    request_factory: Callable[[], RadianceRunRequest],
    expected_dir: str,
) -> None:
    req = request_factory()
    ref = bundle_ref(Path("/unused"), mode, req.length_ft, req.width_ft, req=req)
    assert ref is not None
    assert ref.mode_dirname == expected_dir

    manifest = load_manifest(ref)
    assert manifest is not None
    assert manifest["schema_version"] == 1
    assert params_match(manifest, request_params_for_mode(req))


@pytest.mark.integration
@pytest.mark.radiance
@pytest.mark.parametrize(
    ("request_factory", "expected_tokens"),
    (
        (_smd_request, ("precomputed_playback", "--mode", MODE_SMD, "--dataset-root")),
        (
            lambda: _competitor_request(
                execution_mode=EXECUTION_MODE_PRECOMPUTED,
                length_ft=10,
                width_ft=10,
                target_ppfd=1000,
                sp_z_m=0.4572,
            ),
            (
                "precomputed_playback",
                "--competitor-layout",
                COMPETITOR_LAYOUT_PRACTICAL,
            ),
        ),
        (
            lambda: _hps_request(length_ft=10, width_ft=10),
            ("precomputed_playback", "--hps-ies-variant", DEFAULT_HPS_IES_VARIANT),
        ),
    ),
)
def test_precomputed_command_construction_uses_demo_bundles(
    request_factory: Callable[[], RadianceRunRequest],
    expected_tokens: tuple[str, ...],
) -> None:
    req = request_factory()
    workspace_root = Path("/tmp/rad-rebuild-phase03-staging-workspace")
    cmd, env = pipeline_command(
        req,
        workspace_root=workspace_root,
        session_id="phase03",
    )
    command_text = " ".join(cmd)

    for token in expected_tokens:
        assert token in command_text
    assert f"--workspace-root {workspace_root}" in command_text
    assert env["RADIANCE_PRECOMPUTED_ROOT"] == str(resolve_precomputed_root())


@pytest.mark.radiance
@pytest.mark.parametrize(
    ("request_factory", "script_name"),
    (
        (
            lambda: _smd_request(execution_mode=EXECUTION_MODE_LIVE_DOCKER),
            "run_uniformity.sh",
        ),
        (_competitor_request, "run_simulation_spydr3.sh"),
        (
            lambda: _hps_request(execution_mode=EXECUTION_MODE_LIVE_DOCKER),
            "run_simulation_hps.sh",
        ),
    ),
)
def test_live_command_construction_stays_mode_specific(
    request_factory: Callable[[], RadianceRunRequest],
    script_name: str,
) -> None:
    shell, env = pipeline_shell(request_factory())

    assert script_name in shell
    assert env["PYTHONUNBUFFERED"] == "1"


@pytest.mark.unit
@pytest.mark.parametrize(("name", "factory", "expected"), REQUEST_GOLDENS)
def test_serialized_request_payload_round_trips(
    name: str,
    factory: Callable[[], RadianceRunRequest],
    expected: dict[str, Any],
) -> None:
    req = factory()
    serialized = req.model_dump_json()
    round_trip = RadianceRunRequest.model_validate_json(serialized)

    assert json.loads(serialized)["mode"] == req.mode, name
    assert request_fingerprint(round_trip) == expected["fingerprint"], name
