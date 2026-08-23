from __future__ import annotations

from pathlib import Path

import pytest

from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.mounting import (
    MOUNTING_HEIGHT_DEFINITION,
    MountingGeometry,
)
from fspm_optics.geometry.room import (
    DEFAULT_ROOM_HEIGHT_M,
    PRODUCTION_ROOM_MODEL_IDENTITY_SHA256,
    RoomDimensions,
    room_radiance_text,
)
from fspm_optics.geometry.sensor_grid import BASELINE_REFERENCE_PLANE_Z_M
from fspm_optics.transport.basis.workspace import plan_basis_workspace
from fspm_optics.transport.conventional_rex_execution import (
    ConventionalRexExecutables,
    ConventionalRexTransportRequest,
    plan_conventional_rex_execution,
)
from fspm_optics.transport.conventional_scalar import (
    ConventionalScalarExecutables,
    ConventionalScalarTransportRequest,
    plan_conventional_scalar_transport,
)
from fspm_optics.transport.hps_rex_execution import (
    HpsRexExecutables,
    HpsRexTransportRequest,
    plan_hps_rex_execution,
)
from fspm_optics.transport.hps_scalar import (
    HpsScalarExecutables,
    HpsScalarTransportRequest,
    plan_hps_scalar_transport,
)
from fspm_optics.transport.proposed_uniform import plan_uniform_proposed_stage_a


def _conventional_bins(root: Path) -> ConventionalScalarExecutables:
    return ConventionalScalarExecutables(
        root / "ies2rad", root / "oconv", root / "rtrace", "test"
    )


def _hps_bins(root: Path) -> HpsScalarExecutables:
    return HpsScalarExecutables(
        root / "ies2rad", root / "oconv", root / "rtrace", "test"
    )


def test_every_active_lighting_scene_path_uses_one_room_authority(
    tmp_path: Path,
) -> None:
    room = RoomDimensions(3.048, 3.048, DEFAULT_ROOM_HEIGHT_M)
    expected = room_radiance_text(room)
    layout = generate_proposed_led_layout(10.0, 10.0)
    basis = plan_basis_workspace(
        layout=layout,
        output_directory=tmp_path / "basis",
        radiance_options=("-ab", "0"),
    )
    uniform = plan_uniform_proposed_stage_a(
        layout=layout,
        output_directory=tmp_path / "uniform",
        radiance_options=("-ab", "0"),
    )
    conventional = plan_conventional_scalar_transport(
        ConventionalScalarTransportRequest.from_feet(
            workspace=tmp_path / "conventional"
        ),
        executables=_conventional_bins(tmp_path / "bins-conventional"),
    )
    hps = plan_hps_scalar_transport(
        HpsScalarTransportRequest.from_feet(workspace=tmp_path / "hps"),
        executables=_hps_bins(tmp_path / "bins-hps"),
    )
    conventional_rex = plan_conventional_rex_execution(
        ConventionalRexTransportRequest.from_feet(
            workspace=tmp_path / "conventional-rex"
        ),
        executables=ConventionalRexExecutables(
            tmp_path / "ies2rad", tmp_path / "oconv", tmp_path / "rtrace", "test"
        ),
    )
    hps_rex = plan_hps_rex_execution(
        HpsRexTransportRequest.from_feet(workspace=tmp_path / "hps-rex"),
        executables=HpsRexExecutables(
            tmp_path / "ies2rad", tmp_path / "oconv", tmp_path / "rtrace", "test"
        ),
    )

    assert {
        basis.room_text,
        uniform.room_text,
        conventional.room_text,
        hps.room_text,
        conventional_rex.room_text,
        hps_rex.room_text,
    } == {expected}

    room_prefix = PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]
    caches = [
        *(column.ambient_cache_path for column in basis.generation_plan.columns),
        uniform.paths.ambient_cache,
        conventional.paths.ambient_cache,
        hps.paths.ambient_cache,
        *(run.paths.ambient_cache for run in conventional_rex.runs),
        *(run.paths.ambient_cache for run in hps_rex.runs),
    ]
    assert caches
    assert all(path is not None and room_prefix in path.name for path in caches)


def test_sensor_offset_and_aperture_mounting_semantics_are_unchanged() -> None:
    mounting = MountingGeometry.resolve(18.0)
    assert BASELINE_REFERENCE_PLANE_Z_M == 0.005
    assert mounting.reference_plane_z_m == BASELINE_REFERENCE_PLANE_Z_M
    assert mounting.definition == MOUNTING_HEIGHT_DEFINITION
    assert mounting.emitting_aperture_plane_z_m == pytest.approx(
        mounting.reference_plane_z_m + mounting.mounting_height_m
    )
    assert mounting.mounting_height_m == pytest.approx(0.4572)
