from __future__ import annotations

import pytest

from rad_rebuild.radiance.engine.geometry.generate_grid import (
    build_grid_spec,
    print_coords_mode,
    print_rtrace_mode,
)
from rad_rebuild.radiance.engine.geometry.rect_layout import build_rect_grid


def _small_centered_grid_env() -> dict[str, str]:
    return {
        "LENGTH_FT": "4",
        "WIDTH_FT": "2",
        "GRID_TARGET_SPACING_M": "0.5",
        "GRID_MIN_POINTS_X": "2",
        "GRID_MIN_POINTS_Y": "2",
        "GRID_MIN_FLOOR_POINTS_X": "3",
        "GRID_MIN_FLOOR_POINTS_Y": "3",
        "GRID_Z": "0.25",
        "GRID_SAMPLE_LAYOUT": "centered",
        "WALL_MARGIN_M": "0.0",
        "GRID_MODULE_SIDE_M": "0.0",
    }


def test_generate_grid_centered_adaptive_ordering_and_text_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = build_grid_spec(_small_centered_grid_env())

    assert spec["resolution_x"] == 3
    assert spec["resolution_y"] == 3
    assert spec["minimum_floor_enforced_x"] is True
    assert spec["minimum_floor_enforced_y"] is True
    assert spec["x_coords"] == pytest.approx([-0.4064, 0.0, 0.4064])
    assert spec["y_coords"] == pytest.approx([-0.2032, 0.0, 0.2032])
    assert spec["n_points"] == 9

    print_coords_mode(spec)
    coord_lines = capsys.readouterr().out.splitlines()
    assert coord_lines == [
        "x y z",
        "-0.406400 -0.203200 0.250000",
        "0.000000 -0.203200 0.250000",
        "0.406400 -0.203200 0.250000",
        "-0.406400 0.000000 0.250000",
        "0.000000 0.000000 0.250000",
        "0.406400 0.000000 0.250000",
        "-0.406400 0.203200 0.250000",
        "0.000000 0.203200 0.250000",
        "0.406400 0.203200 0.250000",
    ]

    print_rtrace_mode(spec)
    rtrace_lines = capsys.readouterr().out.splitlines()
    assert rtrace_lines[0] == (
        "-0.406400 -0.203200 0.250000 0.000000 0.000000 1.000000"
    )
    assert rtrace_lines[-1] == ("0.406400 0.203200 0.250000 0.000000 0.000000 1.000000")
    assert len(rtrace_lines) == 9


def test_generate_grid_edge_aligned_swap_preserves_long_axis_x() -> None:
    spec = build_grid_spec(
        {
            "LENGTH_M": "2",
            "WIDTH_M": "4",
            "ALIGN_LONG_AXIS_X": "1",
            "RESOLUTION_X": "3",
            "RESOLUTION_Y": "2",
            "GRID_SAMPLE_LAYOUT": "edge_aligned",
            "GRID_Z": "0.1",
            "WALL_MARGIN_M": "0",
            "GRID_MODULE_SIDE_M": "0",
        }
    )

    assert spec["length_m"] == 4.0
    assert spec["width_m"] == 2.0
    assert spec["explicit_resolution"] is True
    assert spec["x_coords"] == [-2.0, 0.0, 2.0]
    assert spec["y_coords"] == [-1.0, 1.0]
    assert spec["n_points"] == 6


def test_rect_layout_representative_24_by_12_grid_is_stable() -> None:
    positions, meta = build_rect_grid(
        length_m=7.3152,
        width_m=3.6576,
        height_m=3.048,
        wall_margin_m=0.127,
        module_side_x_m=0.1524,
        module_side_y_m=0.1650,
        mount_z_m=2.88925,
    )

    assert len(positions) == 233
    assert positions[:3] == [
        {"x": -1.83896, "y": 0.0, "z": 2.88925, "ring": 0},
        {"x": -1.37922, "y": 0.0, "z": 2.88925, "ring": 0},
        {"x": -0.91948, "y": 0.0, "z": 2.88925, "ring": 0},
    ]
    assert positions[-1] == {
        "x": 3.44805,
        "y": 1.6129499999999999,
        "z": 2.88925,
        "ring": 7,
    }
    assert meta["pitch_x_m"] == pytest.approx(0.22987)
    assert meta["pitch_y_m"] == pytest.approx(0.23042142857142855)
    assert meta["span_u"] == 30
    assert meta["span_v"] == 14
    assert meta["modules"] == 233
    assert meta["ring_n"] == 7
    assert meta["offset"] == 8
