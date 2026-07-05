from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tests.radiance.runtime_env import configure_test_runtime


configure_test_runtime()

from rad_rebuild.radiance.config import (  # noqa: E402
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
)
from rad_rebuild.radiance.engine.emitters import generate_emitters_smd  # noqa: E402
from rad_rebuild.radiance.engine.simulation import basis_rcontrib  # noqa: E402
from rad_rebuild.radiance.engine.simulation import precompute_sweep  # noqa: E402


def _position_env(layout_mode: str) -> dict[str, str]:
    return {
        "LENGTH_FT": "10",
        "WIDTH_FT": "10",
        "LAYOUT_MODE": layout_mode,
        "SMD_FIXED_PITCH_M": "0",
    }


@pytest.mark.radiance
@pytest.mark.parametrize(
    ("layout_mode", "count", "spacing", "rings", "ring_n", "first", "last"),
    (
        (
            "grid",
            121,
            0.24493571428571426,
            6,
            5,
            {"ring": 5, "i": -5, "j": -5, "x": -1.224679, "y": -1.224679},
            {"ring": 5, "i": 5, "j": 5, "x": 1.224679, "y": 1.224679},
        ),
        (
            "square",
            85,
            0.2349583333333333,
            7,
            6,
            {"ring": 0, "i": 0, "j": 0, "x": 0.0, "y": 0.0},
            {"ring": 6, "i": 5, "j": 1, "x": 0.939833, "y": 1.40975},
        ),
        (
            "exact_tiled",
            61,
            0.28195,
            5,
            4,
            {"ring": 0, "i": 0.0, "j": 0.0, "x": 0.0, "y": 0.0},
            {"ring": 4, "i": -1.0, "j": 4.0, "x": 0.84963, "y": -1.40975},
        ),
    ),
)
def test_smd_position_planning_current_contract(
    layout_mode: str,
    count: int,
    spacing: float,
    rings: int,
    ring_n: int,
    first: dict[str, float],
    last: dict[str, float],
) -> None:
    with (
        patch.dict(os.environ, _position_env(layout_mode), clear=False),
        patch.object(generate_emitters_smd, "SMD_PERIM_GAP_FILL", False),
    ):
        positions, actual_spacing, meta = (
            generate_emitters_smd._compute_positions_from_env()
        )

    assert len(positions) == count
    assert actual_spacing == pytest.approx(spacing, rel=1e-12)
    assert meta["rings"] == rings
    assert meta["ring_n"] == ring_n
    assert meta["layout_mode"] == layout_mode
    for key, expected in first.items():
        assert positions[0][key] == pytest.approx(expected)
    for key, expected in last.items():
        assert positions[-1][key] == pytest.approx(expected)


def _point(ring: int, x: float, y: float) -> dict[str, float | int]:
    return {"ring": ring, "i": 0, "j": 0, "x": x, "y": y, "z": 0.5}


@pytest.mark.radiance
def test_smd_perimeter_gap_fill_axis_contract() -> None:
    positions = [
        _point(0, 0.0, 0.0),
        _point(1, -1.0, 0.0),
        _point(1, 1.0, 0.0),
        _point(1, 0.0, 0.5),
        _point(1, 0.0, -0.5),
        _point(2, -1.0, -1.0),
        _point(2, -1.0, 1.0),
        _point(2, 1.0, -1.0),
        _point(2, 1.0, 1.0),
    ]

    filled, info = generate_emitters_smd._apply_perimeter_gap_fill(positions)

    assert info == {
        "perim_gap_fill": True,
        "perim_gap_fill_added": 4,
        "perim_gap_fill_removed_ring": 1,
    }
    assert len(filled) == 11
    filled_keys = {(item["ring"], item["x"], item["y"]) for item in filled}
    assert (2, -1.0, 0.0) in filled_keys
    assert (2, 1.0, 0.0) in filled_keys
    assert (1, -1.0, 0.0) not in filled_keys
    assert (1, 1.0, 0.0) not in filled_keys


@pytest.mark.radiance
@pytest.mark.parametrize(
    ("layout_mode", "group_count", "counts"),
    (
        (
            "square",
            24,
            {
                "L": 6,
                "centerpiece": 1,
                "linear3": 12,
                "linear4": 1,
                "reverse_L": 4,
            },
        ),
        (
            "exact_tiled",
            17,
            {"L": 5, "centerpiece": 1, "linear3": 8, "reverse_L": 3},
        ),
        ("grid", 0, {}),
    ),
)
def test_smd_fixture_overlay_current_contract(
    layout_mode: str,
    group_count: int,
    counts: dict[str, int],
) -> None:
    with (
        patch.dict(os.environ, _position_env(layout_mode), clear=False),
        patch.object(generate_emitters_smd, "SMD_PERIM_GAP_FILL", False),
    ):
        positions, _spacing, meta = generate_emitters_smd._compute_positions_from_env()
        groups, actual_counts = generate_emitters_smd._build_fixture_overlay(
            positions, meta, 0.4572
        )

    assert len(groups) == group_count
    assert actual_counts == counts
    if groups:
        assert groups[0]["type"] == "centerpiece"
        assert len(groups[0]["points"]) == 5


@pytest.mark.benchmark
@pytest.mark.slow
@pytest.mark.radiance
def test_phase125d1_smd_30x30_position_planning_benchmark(benchmark: Any) -> None:
    def plan_positions() -> tuple[list[dict[str, Any]], float, dict[str, Any]]:
        with (
            patch.dict(
                os.environ,
                {
                    "LENGTH_FT": "30",
                    "WIDTH_FT": "30",
                    "LAYOUT_MODE": "exact_tiled",
                    "SMD_FIXED_PITCH_M": "0",
                },
                clear=False,
            ),
            patch.object(generate_emitters_smd, "SMD_PERIM_GAP_FILL", False),
        ):
            return generate_emitters_smd._compute_positions_from_env()

    positions, spacing, meta = benchmark(plan_positions)

    assert len(positions) == 481
    assert spacing > 0
    assert meta["rings"] == 15


@pytest.mark.benchmark
@pytest.mark.slow
@pytest.mark.radiance
def test_phase125d1_full_public_precompute_plan_benchmark(benchmark: Any) -> None:
    with tempfile.TemporaryDirectory(prefix="rad_rebuild_phase125d1_plan_") as tmp:
        config = precompute_sweep.PrecomputeSweepConfig(
            length_min=10,
            length_max=20,
            width_min=10,
            width_max=20,
            step=1,
            square_only=False,
            modes=(MODE_SMD, MODE_HPS, MODE_COMPETITOR),
            dataset_root=Path(tmp) / "dataset",
            dry_run=True,
            competitor_layouts=("practical",),
        )

        plan = benchmark(precompute_sweep.plan_precompute_sweep, config)

    assert plan.bundle_count == 198
    assert plan.items[0].room == (10, 10)
    assert plan.items[-1].room == (20, 20)


@pytest.mark.benchmark
@pytest.mark.slow
@pytest.mark.radiance
def test_phase125d1_large_rcontrib_parse_benchmark(benchmark: Any) -> None:
    sensor_count = 1369
    modifier_order = [f"ring_{idx}" for idx in range(15)]
    row = " ".join(str(float(idx)) for idx in range(45))
    raw = "\n".join(row for _ in range(sensor_count))

    matrix = benchmark(
        basis_rcontrib.parse_rcontrib_ascii_output,
        raw,
        sensor_count=sensor_count,
        oversample=1,
        modifier_order=modifier_order,
    )

    assert matrix.shape == (1369, 15)
    assert matrix[0, 0] == pytest.approx(1.0)
    assert matrix[0, -1] == pytest.approx(43.0)
