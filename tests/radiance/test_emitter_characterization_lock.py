from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any, cast
from unittest.mock import patch

import pytest

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from tests.radiance.private_assets import require_private_ies_assets  # noqa: E402
from rad_rebuild.radiance.engine.emitters import generate_emitters_smd as smd  # noqa: E402
from rad_rebuild.radiance.engine.emitters import generate_emitters_spydr3 as spydr  # noqa: E402


SPYDR_MODULE = "rad_rebuild.radiance.engine.emitters.generate_emitters_spydr3"


def _scrub_smd_layout_payload(path: Path) -> dict[str, Any]:
    payload = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
    payload.pop("timestamp", None)
    return payload


def _reload_spydr_with_env(env: dict[str, str]) -> ModuleType:
    previous = sys.modules.pop(SPYDR_MODULE, None)
    with patch.dict(os.environ, env, clear=False):
        module = importlib.import_module(SPYDR_MODULE)
    if previous is not None:
        sys.modules[SPYDR_MODULE] = previous
    else:
        sys.modules.pop(SPYDR_MODULE, None)
    return module


@pytest.mark.radiance
@pytest.mark.parametrize(
    ("layout_mode", "count", "spacing", "rings", "ring_n", "first", "last", "meta"),
    (
        (
            "grid",
            165,
            0.24493571428571426,
            8,
            7,
            {"ring": 7, "i": -5, "j": -7, "x": -1.224679, "y": -1.71455},
            {"ring": 7, "i": 5, "j": 7, "x": 1.224679, "y": 1.71455},
            {"pitch_x_m": 0.24493571428571426, "pitch_y_m": 0.24493571428571426},
        ),
        (
            "square",
            85,
            0.2360083333333333,
            7,
            6,
            {"ring": 0, "i": 0, "j": 0, "x": 0.0, "y": 0.0},
            {"ring": 6, "i": 5, "j": 1, "x": 0.944033, "y": 1.41605},
            {"pitch_x_m": 0.2360083333333333, "pitch_y_m": 0.2360083333333333},
        ),
        (
            "rect_rect",
            127,
            0.1564409090909091,
            6,
            5,
            {"ring": 0, "x": -0.9386454545454546, "y": 0.0},
            {"ring": 5, "x": 1.7208500000000002, "y": 1.4097499999999998},
            {
                "pitch_x_m": 0.1564409090909091,
                "pitch_y_m": 0.28195,
                "swapped_axes": True,
                "offset": 6,
            },
        ),
        (
            "exact_tiled",
            61,
            0.28195,
            6,
            5,
            {"ring": 0, "i": 0.0, "j": 0.0, "x": 0.0, "y": 0.0},
            {"ring": 5, "i": -4.0, "j": 1.0, "x": -1.03251, "y": -1.40975},
            {
                "pitch_x_m": 0.34417,
                "pitch_y_m": 0.28195,
                "layout_family": "horticultural_tiled_v1",
                "swapped_axes": True,
            },
        ),
    ),
)
def test_smd_position_planning_characterizes_all_supported_layouts(
    layout_mode: str,
    count: int,
    spacing: float,
    rings: int,
    ring_n: int,
    first: dict[str, float],
    last: dict[str, float],
    meta: dict[str, object],
) -> None:
    with (
        patch.dict(
            os.environ,
            {
                "LENGTH_FT": "10",
                "WIDTH_FT": "12",
                "LAYOUT_MODE": layout_mode,
                "SMD_FIXED_PITCH_M": "0",
            },
            clear=False,
        ),
        patch.object(smd, "SMD_PERIM_GAP_FILL", False),
    ):
        positions, actual_spacing, actual_meta = smd._compute_positions_from_env()

    assert len(positions) == count
    assert actual_spacing == pytest.approx(spacing, rel=1e-12)
    assert actual_meta["rings"] == rings
    assert actual_meta["ring_n"] == ring_n
    assert actual_meta["layout_mode"] == layout_mode
    for key, expected_value in first.items():
        assert positions[0][key] == pytest.approx(expected_value)
    for key, expected_value in last.items():
        assert positions[-1][key] == pytest.approx(expected_value)
    for key, expected_meta_value in meta.items():
        if isinstance(expected_meta_value, float):
            assert actual_meta[key] == pytest.approx(expected_meta_value)
        else:
            assert actual_meta[key] == expected_meta_value


@pytest.mark.radiance
def test_smd_generation_artifacts_are_deterministic_except_layout_timestamp(
    tmp_path: Path,
) -> None:
    runtime_a = tmp_path / "runtime A with spaces"
    runtime_b = tmp_path / "runtime B with spaces"
    runtime_a.mkdir()
    runtime_b.mkdir()

    def generate(runtime: Path) -> tuple[str, str, dict[str, Any]]:
        config = smd.SmdEmitterConfig(
            output_dir=runtime,
            layout_json=runtime / "smd_layout.json",
        )
        result = smd.run_smd_emitter_generation(config)
        assert result.module_count == 121
        assert result.ring_count == 6
        assert result.total_source_umol_s > 0.0
        return (
            result.emitter_rad.read_text(encoding="utf-8"),
            result.summary_txt.read_text(encoding="utf-8"),
            _scrub_smd_layout_payload(result.layout_json),
        )

    with (
        patch.dict(
            os.environ,
            {
                "LENGTH_FT": "10",
                "WIDTH_FT": "10",
                "LAYOUT_MODE": "grid",
                "SMD_FIXED_PITCH_M": "0",
                "USE_RING_POWERS_JSON": "0",
                "RING_POWERS_JSON": str(tmp_path / "missing powers.json"),
            },
            clear=False,
        ),
        patch.object(smd, "SMD_PERIM_GAP_FILL", False),
    ):
        rad_a, summary_a, layout_a = generate(runtime_a)
        rad_b, summary_b, layout_b = generate(runtime_b)

    assert rad_a == rad_b
    assert summary_a == summary_b
    assert layout_a == layout_b
    assert "void light smd_L" in rad_a
    assert "SMD macro emitter summary:" in summary_a
    assert layout_a["modules"] == 121
    assert layout_a["meta"]["layout_mode"] == "grid"
    assert layout_a["meta"]["rings"] == 6


@pytest.mark.radiance
@pytest.mark.parametrize(
    ("env", "count", "nx", "ny", "expected"),
    (
        (
            {
                "LENGTH_FT": "12",
                "WIDTH_FT": "12",
                "SPYDR_LAYOUT_MODE": "full",
                "NX": "2",
                "NY": "2",
                "SPYDR_USE_IES_DIMS": "0",
            },
            4,
            2,
            2,
            {
                "layout_mode": "full",
                # Public CI does not include the private Qube IES source, so
                # this lock uses the committed fallback fixture dimensions.
                "pitch_x_in": 63.616666692913384,
                "pitch_y_in": 62.26666669291339,
                "array_anchor": "centered",
            },
        ),
        (
            {
                "LENGTH_FT": "20",
                "WIDTH_FT": "12",
                "SPYDR_LAYOUT_MODE": "practical",
                "NX": "4",
                "NY": "3",
                "SPYDR_EDGE_INSET_IN": "1",
                "SPYDR_TARGET_GAP_X_IN": "4",
                "SPYDR_TARGET_GAP_Y_IN": "8",
                "SPYDR_ACTUAL_GAP_X_IN": "3.5",
                "SPYDR_ACTUAL_GAP_Y_IN": "7.5",
                "SPYDR_USE_IES_DIMS": "0",
            },
            12,
            4,
            3,
            {
                "layout_mode": "practical",
                "target_gap_x_in": 4.0,
                "target_gap_y_in": 8.0,
                "actual_gap_x_in": 3.5,
                "actual_gap_y_in": 7.5,
                "array_anchor": "centered_even_gap",
            },
        ),
    ),
)
def test_spydr_layout_characterizes_full_and_practical_import_time_env(
    env: dict[str, str],
    count: int,
    nx: int,
    ny: int,
    expected: dict[str, object],
) -> None:
    module = _reload_spydr_with_env(env)
    geometry = module._spydr_fixture_geometry()
    centers, nx_eff, ny_eff = module._fixture_centers(
        geometry.fixture_length_m,
        geometry.fixture_width_m,
    )
    layout = module._base_spydr_layout(geometry, centers, nx_eff, ny_eff)

    assert len(centers) == count
    assert nx_eff == nx
    assert ny_eff == ny
    assert layout["nx"] == nx
    assert layout["ny"] == ny
    for key, value in expected.items():
        if isinstance(value, float):
            assert layout[key] == pytest.approx(value)
        else:
            assert layout[key] == value


@pytest.mark.radiance
def test_spydr_full_layout_private_ies_dimensions_when_explicitly_enabled() -> None:
    require_private_ies_assets("qube_660w_8bar.ies")
    module = _reload_spydr_with_env(
        {
            "LENGTH_FT": "12",
            "WIDTH_FT": "12",
            "SPYDR_LAYOUT_MODE": "full",
            "NX": "2",
            "NY": "2",
        }
    )
    geometry = module._spydr_fixture_geometry()
    centers, nx_eff, ny_eff = module._fixture_centers(
        geometry.fixture_length_m,
        geometry.fixture_width_m,
    )
    layout = module._base_spydr_layout(geometry, centers, nx_eff, ny_eff)

    assert len(centers) == 4
    assert nx_eff == 2
    assert ny_eff == 2
    assert layout["pitch_x_in"] == pytest.approx(63.61679787401575)
    assert layout["pitch_y_in"] == pytest.approx(62.26509188976378)
    assert layout["array_anchor"] == "centered"


@pytest.mark.radiance
def test_spydr_main_characterizes_compat_filename_proxy_and_summary(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    runtime = tmp_path / "spydr runtime with spaces"
    env = {
        "RADIANCE_RUNTIME_STATE_ROOT": str(runtime),
        "LENGTH_FT": "12",
        "WIDTH_FT": "12",
        "NX": "1",
        "NY": "1",
        "COMPAT": "1",
        "SPYDR_PROXY_MODE": "1",
    }
    module = _reload_spydr_with_env(env)
    metrics = module.RelativeSPDPhotonMetrics(
        spd_path="spd.csv",
        sample_count=3,
        total_relative_power_area=1.0,
        par_relative_power_area=0.9,
        par_fraction_of_total_power=0.9,
        par_centroid_nm=550.0,
        luminous_efficacy_lm_per_radiant_w=100.0,
        par_photon_umol_per_radiant_w=4.5,
        umol_per_lumen=0.045,
    )
    stage = module.SpydrIesStage(
        lines=[],
        lumens=1000.0,
        dims_m=None,
        spd_metrics=metrics,
        lm_to_umol=0.045,
        lm_to_umol_method="characterization",
        baseline_ppf=45.0,
        baseline_radiant_w=10.0,
        target_radiant_w=20.0,
        scale=2.0,
        rad_path=Path("fixture.rad"),
        source_meta={
            "geometry": "single_downward_flatcorr_aperture",
            "aperture_name": "fixture_aperture",
            "aperture_length_m": 1.0,
            "aperture_width_m": 0.5,
            "aperture_z_m": 0.0,
            "box_face_count": 6,
            "faces_removed": 5,
        },
    )

    with patch.object(module, "_build_spydr_ies_stage", return_value=stage):
        module.main()

    compat_rad = runtime / "emitters_smd_ALL_umol.rad"
    assert compat_rad.is_file()
    assert not (runtime / "emitters_spydr3_ALL_umol.rad").exists()
    rad_text = compat_rad.read_text(encoding="utf-8")
    assert "!xform -rx 0.000000 -rz 0.000000 -t 0.000000 0.000000 0.457200 fixture.rad" in rad_text
    assert "!genbox spydr3_proxy fixture_proxy_" in rad_text
    layout = json.loads((runtime / "spydr3_layout.json").read_text(encoding="utf-8"))
    assert layout["directional_model"] == "whole_fixture_ies_photometry"
    assert layout["fixtures"][0]["bars"]
    summary = (runtime / "spydr3_summary.txt").read_text(encoding="utf-8")
    assert "WHOLE_FIXTURE_IES=1" in summary
    assert "IES_SOURCE_GEOMETRY=single_downward_flatcorr_aperture" in summary
    assert "Wrote conventional layout JSON" in capsys.readouterr().out


@pytest.mark.radiance
def test_spydr_ies2rad_failure_preserves_resolved_argv_and_space_paths(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime with spaces"
    runtime.mkdir()
    ies_path = tmp_path / "fixture with spaces.ies"
    ies_path.write_text("IESNA\n", encoding="utf-8")
    ies2rad = tmp_path / "ies2rad with spaces"
    ies2rad.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fail_run(
        cmd: list[str], *, cwd: Path, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert cwd == runtime
        assert check is True
        calls.append(cmd)
        raise subprocess.CalledProcessError(9, cmd)

    with (
        patch.object(spydr, "OUT_DIR", runtime),
        patch.object(spydr, "resolve_executable", return_value=ies2rad),
        patch.object(
            cast(Any, getattr(spydr, "subprocess")), "run", side_effect=fail_run
        ),
        pytest.raises(subprocess.CalledProcessError),
    ):
        spydr._run_ies2rad(ies_path, "fixture output", 1.25)

    assert len(calls) == 2
    assert calls[0][0] == str(ies2rad)
    assert calls[0][-1] == str(ies_path.resolve())
    assert calls[1] == [str(ies2rad), "-o", "fixture output", str(ies_path.resolve())]
