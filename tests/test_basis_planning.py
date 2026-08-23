from __future__ import annotations

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from fspm_optics.fixtures.smd.radiance_writer import (
    current_proposed_source_identity_payload,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.radiance.commands import LOCAL_DEFAULT_NTHREADS
from fspm_optics.transport.basis.planning import plan_isolated_rtrace_basis
from fspm_optics.transport.basis.source_compatibility import (
    CurrentSourceCompatibilityError,
    require_current_proposed_source_manifest,
)


def build_plan(tmp_path: Path):
    return plan_isolated_rtrace_basis(
        layout=generate_proposed_led_layout(10, 10),
        sensor_count=9,
        room_height_m=3.048,
        room_source_path=tmp_path / "room.rad",
        sensor_input_path=tmp_path / "sensors.txt",
        output_directory=tmp_path / "basis",
        reference_watts=1.0,
    )


def test_ten_by_ten_basis_plan_has_five_control_zone_columns(tmp_path: Path) -> None:
    plan = build_plan(tmp_path)
    assert plan.control_zone_count == 5
    assert len(plan.columns) == 5
    assert plan.manifest.control_zone_count == 5
    assert plan.manifest.matrix_shape == (9, 5)
    assert plan.manifest.layout_module_count == 61


def test_each_column_activates_exactly_one_control_zone(tmp_path: Path) -> None:
    plan = build_plan(tmp_path)
    for column in plan.columns:
        coefficients = column.control_zone_coefficients
        assert coefficients[column.control_zone_index] == 1.0
        assert sum(value != 0.0 for value in coefficients) == 1
        assert all(
            value == 0.0
            for index, value in enumerate(coefficients)
            if index != column.control_zone_index
        )
        assert column.power_schedule.watts_by_control_zone == coefficients
        radiances = column.emitter_document.metadata.source_radiance_by_control_zone
        assert radiances[column.control_zone_index] > 0.0
        assert all(
            value == 0.0
            for index, value in enumerate(radiances)
            if index != column.control_zone_index
        )


def test_column_command_specs_are_deterministic_and_local_first(tmp_path: Path) -> None:
    first = build_plan(tmp_path)
    second = build_plan(tmp_path)
    assert first == second
    assert not first.output_directory.exists()

    for column in first.columns:
        assert column.oconv_command.argv == (
            "oconv",
            "-f",
            str(first.room_source_path),
            str(column.emitter_source_path),
            str(first.fixture_occlusion.instance_source_path),
        )
        assert column.oconv_command.stdout_path == column.octree_path
        assert column.rtrace_command.argv[:5] == (
            "rtrace",
            "-h",
            "-I+",
            "-n",
            str(LOCAL_DEFAULT_NTHREADS),
        )
        assert column.rtrace_command.stdin_path == first.sensor_input_path
        assert column.rtrace_command.stdout_path == column.rgb_output_path
        assert "-af" in column.rtrace_command.argv
        assert str(column.ambient_cache_path) in column.rtrace_command.argv
        assert (
            first.manifest.proposed_source_identity_sha256[:16]
            in column.ambient_cache_path.name
        )


def test_current_production_rejects_historical_normalization_identity(
    tmp_path: Path,
) -> None:
    manifest = build_plan(tmp_path).manifest
    require_current_proposed_source_manifest(manifest)
    historical_payload = current_proposed_source_identity_payload()
    historical_payload["completed_aperture_transmission"] = (
        0.8100428775596422
    )
    historical_identity = hashlib.sha256(
        json.dumps(
            historical_payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    historical = replace(
        manifest,
        proposed_source_identity_sha256=historical_identity,
    )
    with pytest.raises(
        CurrentSourceCompatibilityError,
        match="historically authentic but incompatible",
    ):
        require_current_proposed_source_manifest(historical)


def test_planning_only_uses_the_isolated_trace_command_family() -> None:
    package_root = Path(__file__).parents[1] / "src" / "fspm_optics"
    blocked_command_names = ("r" + "contrib", "r" + "fluxmtx")
    isolated_calibration_boundary = {
        package_root / "fixtures" / "smd" / "aperture_ppe_calibration.py",
        package_root / "fixtures" / "smd" / "angular_characterization.py",
        package_root / "diagnostics" / "proposed_source_response.py",
    }
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in package_root.rglob("*.py")
        if path not in isolated_calibration_boundary
    )
    assert all(name not in source for name in blocked_command_names)
