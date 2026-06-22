from __future__ import annotations

import math
import sys
from collections import Counter
from dataclasses import FrozenInstanceError
from importlib.util import find_spec
from io import BytesIO
from typing import Any, cast

import pytest
from hypothesis import given, strategies as st
from PIL import Image

from rad_rebuild.radiance.engine.layout import layout_engine, layout_generator
from rad_rebuild.radiance.engine.layout.domain import LayoutResult
from rad_rebuild.web import app as web_app


PARITY_DIMS = (
    (4, 4),
    (10, 10),
    (12, 10),
    (10, 12),
    (15, 12),
    (20, 10),
    (22, 16),
    (30, 30),
)


def _point_key(point: tuple[float, float]) -> tuple[float, float]:
    return (round(float(point[0]), 6), round(float(point[1]), 6))


def _sorted_positions(layout: dict[str, object]) -> list[tuple[float, float]]:
    positions = layout["all_positions"]
    assert isinstance(positions, list)
    return sorted(
        (_point_key(point) for point in positions),
        key=lambda point: (point[1], point[0]),
    )


def _module_signature(
    layout: dict[str, object],
) -> list[tuple[str, str, tuple[tuple[float, float], ...]]]:
    module_groups = layout["module_groups"]
    assert isinstance(module_groups, list)
    return [
        (module_type, orient, tuple(_point_key(point) for point in cobs))
        for module_type, orient, cobs in module_groups
    ]


def _module_counts(layout: dict[str, object]) -> dict[str, int]:
    module_groups = layout["module_groups"]
    assert isinstance(module_groups, list)
    return dict(Counter(module_type for module_type, _orient, _cobs in module_groups))


@pytest.mark.radiance
def test_expired_layout_compatibility_package_is_removed() -> None:
    sys.modules.pop("rad_rebuild.layout", None)
    sys.modules.pop("rad_rebuild.layout.layout_engine", None)
    sys.modules.pop("rad_rebuild.layout.layout_generator", None)

    assert find_spec("rad_rebuild.layout") is None

    assert web_app.generate_layout is layout_generator.generate_layout


@pytest.mark.radiance
@pytest.mark.parametrize(("length", "width"), PARITY_DIMS)
def test_public_and_canonical_layout_paths_share_approved_geometry(
    length: int, width: int
) -> None:
    public_layout = web_app.generate_layout(length, width)
    canonical_layout = layout_generator.generate_layout(length, width)
    canonical_zones = layout_generator.generate_layout_with_zones(length, width)

    assert _sorted_positions(public_layout) == _sorted_positions(canonical_layout)
    assert _module_signature(public_layout) == _module_signature(canonical_layout)
    assert _module_counts(public_layout) == _module_counts(canonical_layout)
    assert (
        canonical_zones["topology"]
        == layout_generator.generate_layout_result(length, width).topology.as_dict()
    )


@pytest.mark.radiance
def test_public_and_canonical_payload_paths_share_rendering_adapter() -> None:
    canonical_payload = layout_engine.build_layout_payload(12, 10, include_svg=True)

    public_payload = web_app.build_layout_payload(12, 10, include_svg=True)
    assert public_payload == canonical_payload
    svg = cast(str, canonical_payload["svg"])
    assert 'class="room-boundary"' in svg
    assert "Module legend" in svg
    assert public_payload["png_download_name"] == "horticultural-layout-12ft-x-10ft.png"


@pytest.mark.radiance
def test_canonical_layout_result_is_immutable_and_legacy_compatible() -> None:
    result = layout_generator.generate_layout_result(12, 10)

    assert isinstance(result, LayoutResult)
    assert result.topology.base_n == 5
    assert len(result.all_positions) == 61
    assert len(result.module_groups) == 17
    assert result.as_legacy_dict(
        include_zones=False
    ) == layout_generator.generate_layout(12, 10)
    assert result.as_legacy_dict(
        include_zones=True
    ) == layout_generator.generate_layout_with_zones(12, 10)

    with pytest.raises(FrozenInstanceError):
        result.topology.base_n = 99  # type: ignore[misc]


@pytest.mark.radiance
@pytest.mark.parametrize(
    ("length", "width"),
    ((0, 10), (-1, 10), (math.inf, 10), (math.nan, 10), (10, math.inf), (10, math.nan)),
)
def test_canonical_layout_rejects_non_finite_or_unbounded_dimensions(
    length: float, width: float
) -> None:
    with pytest.raises(ValueError):
        layout_generator.generate_layout_result(length, width)


@pytest.mark.radiance
@given(
    length=st.integers(min_value=4, max_value=30),
    width=st.integers(min_value=4, max_value=30),
)
def test_canonical_layout_properties(length: int, width: int) -> None:
    layout = layout_generator.generate_layout_result(length, width)
    swapped = layout_generator.generate_layout_result(width, length)
    positions = layout.all_positions
    module_points = tuple(
        point for group in layout.module_groups for point in group.points
    )

    assert layout == layout_generator.generate_layout_result(length, width)
    assert len(positions) == len(set(positions))
    assert set(positions) == set(module_points)
    assert len(positions) == len(swapped.all_positions)
    assert len(layout.module_groups) == len(swapped.module_groups)
    assert layout.topology == swapped.topology
    assert all(
        math.isfinite(coord) for point in positions for coord in (point.x, point.y)
    )
    assert all(
        abs(coord) <= 2 * max(length, width)
        for point in positions
        for coord in (point.x, point.y)
    )


@pytest.mark.radiance
def test_canonical_png_adapter_preserves_export_metadata() -> None:
    layout = layout_generator.generate_layout(12, 10)
    png_bytes = layout_engine.build_layout_png(
        layout["all_positions"],
        layout["module_groups"],
        length_ft=12,
        width_ft=10,
    )

    with Image.open(BytesIO(png_bytes)) as image:
        assert image.format == "PNG"
        assert image.mode == "RGB"
        assert image.size == (6540, 7200)
        assert tuple(round(float(value)) for value in image.info["dpi"]) == (600, 600)


@pytest.mark.radiance
def test_flask_png_route_reuses_one_layout_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original_generate_layout = web_app.generate_layout

    def wrapped_generate_layout(length_ft: float, width_ft: float) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return original_generate_layout(length_ft, width_ft)

    monkeypatch.setattr(web_app, "generate_layout", wrapped_generate_layout)
    app = web_app.create_app(config={"TESTING": True})

    response = app.test_client().get(
        "/api/layout-generator/png?length_ft=12&width_ft=10"
    )

    assert response.status_code == 200
    assert response.mimetype == "image/png"
    assert calls == 1


@pytest.mark.radiance
@pytest.mark.benchmark
def test_canonical_layout_performance_30x30(benchmark: Any) -> None:
    result = benchmark(layout_generator.generate_layout_result, 30, 30)

    assert len(result.all_positions) == 481
    assert len(result.module_groups) == 127
