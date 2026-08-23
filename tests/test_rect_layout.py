from __future__ import annotations

import pytest

from fspm_optics.geometry.rect_layout import (
    RectLayoutSpec,
    axis_positions,
    generate_rect_layout,
)
from fspm_optics.geometry.room import RoomDimensions


def test_square_room_layout_is_centered_and_deterministic() -> None:
    spec = RectLayoutSpec(
        room=RoomDimensions(4.0, 4.0, 3.0),
        rows=3,
        columns=3,
        mount_height_m=2.5,
        edge_margin_x_m=0.5,
        edge_margin_y_m=0.5,
    )
    first = generate_rect_layout(spec)
    second = generate_rect_layout(spec)
    assert first == second
    assert first.count == 9
    assert first.pitch_x_m == pytest.approx(1.5)
    assert first.pitch_y_m == pytest.approx(1.5)
    assert first.footprint_length_m == pytest.approx(3.0)
    assert first.footprint_width_m == pytest.approx(3.0)
    assert first.positions[4].x_m == pytest.approx(0.0)
    assert first.positions[4].y_m == pytest.approx(0.0)


def test_rectangular_room_preserves_explicit_axes() -> None:
    spec = RectLayoutSpec(
        room=RoomDimensions(6.0, 3.0, 3.0),
        rows=2,
        columns=4,
        mount_height_m=2.4,
        edge_margin_x_m=0.5,
        edge_margin_y_m=0.5,
    )
    layout = generate_rect_layout(spec)
    assert layout.count == 8
    assert layout.pitch_x_m == pytest.approx(5.0 / 3.0)
    assert layout.pitch_y_m == pytest.approx(2.0)
    assert layout.footprint_length_m == pytest.approx(5.0)
    assert layout.footprint_width_m == pytest.approx(2.0)
    assert [position.x_m for position in layout.positions[:4]] == pytest.approx(
        [-2.5, -5.0 / 6.0, 5.0 / 6.0, 2.5]
    )
    assert sorted({position.y_m for position in layout.positions}) == [-1.0, 1.0]


def test_single_axis_position_is_centered() -> None:
    assert axis_positions(5.0, 1, edge_margin_m=0.5) == (0.0,)


def test_layout_rejects_impossible_margins() -> None:
    with pytest.raises(ValueError, match="no usable room length"):
        RectLayoutSpec(
            room=RoomDimensions(2.0, 2.0, 3.0),
            rows=2,
            columns=2,
            mount_height_m=2.0,
            edge_margin_x_m=1.0,
        )
