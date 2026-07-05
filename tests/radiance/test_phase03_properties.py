from __future__ import annotations

import math
from typing import Any

import numpy as np
import pytest
from hypothesis import given, strategies as st

from rad_rebuild.radiance.backend.models import RadianceRunRequest
from rad_rebuild.radiance.backend.workspace import (
    _sanitize_session_id,
    canonical_request_fingerprint_json,
    request_fingerprint,
)
from rad_rebuild.radiance.config import (
    EXECUTION_MODE_PRECOMPUTED,
    MODE_COMPETITOR,
    MODE_HPS,
    MODE_SMD,
)
from rad_rebuild.radiance.engine.layout.layout_generator import (
    generate_layout_with_zones,
)
from rad_rebuild.radiance.engine.photometry.ppfd_metrics import (
    compute_ppfd_metrics,
    metric_float,
)


def _numeric_items(payload: Any, prefix: str = "") -> list[tuple[str, float]]:
    if isinstance(payload, dict):
        values: list[tuple[str, float]] = []
        for key, value in payload.items():
            item_prefix = f"{prefix}.{key}" if prefix else str(key)
            values.extend(_numeric_items(value, item_prefix))
        return values
    if isinstance(payload, bool):
        return []
    if isinstance(payload, int | float):
        return [(prefix, float(payload))]
    return []


@pytest.mark.radiance
@given(
    length=st.integers(min_value=10, max_value=30),
    width=st.integers(min_value=10, max_value=30),
)
def test_layout_is_deterministic_axis_swap_symmetric_and_bounded(
    length: int, width: int
) -> None:
    layout = generate_layout_with_zones(length, width)
    repeated = generate_layout_with_zones(length, width)
    swapped = generate_layout_with_zones(width, length)
    points = layout["all_positions"]

    assert layout == repeated
    assert len(points) == len(set(points))
    assert len(points) == len(swapped["all_positions"])
    assert len(layout["module_groups"]) == len(swapped["module_groups"])
    assert layout["topology"] == swapped["topology"]
    assert all(math.isfinite(coord) for point in points for coord in point)
    assert all(
        abs(coord) <= 2 * max(length, width) for point in points for coord in point
    )


@pytest.mark.radiance
@given(
    samples=st.lists(
        st.floats(
            min_value=0.0, max_value=3000.0, allow_nan=False, allow_infinity=False
        ),
        min_size=3,
        max_size=25,
    )
)
def test_ppfd_metrics_for_non_negative_fields_are_finite(samples: list[float]) -> None:
    metrics = compute_ppfd_metrics(
        np.array(samples, dtype=float),
        setpoint_ppfd=1000.0,
        canopy_area_m2=9.290304,
        total_input_watts=500.0,
        emitted_ppf_umol_s=12000.0,
        legacy_metrics=True,
    )

    for key, value in _numeric_items(metrics):
        assert math.isfinite(value)
        if key != "legacy.dou_percent":
            assert value >= 0.0


@pytest.mark.unit
def test_plane_utilization_matches_capture_fraction() -> None:
    metrics = compute_ppfd_metrics(
        np.array([100.0, 120.0, 140.0], dtype=float),
        canopy_area_m2=2.0,
        emitted_ppf_umol_s=1000.0,
    )

    assert metrics["ppf_out"] == pytest.approx(240.0)
    assert metrics["capture_frac"] == pytest.approx(0.24)
    assert metrics["plane_utilization"] == pytest.approx(metrics["capture_frac"])


@pytest.mark.radiance
@given(
    samples=st.lists(
        st.floats(
            min_value=1.0, max_value=2000.0, allow_nan=False, allow_infinity=False
        ),
        min_size=3,
        max_size=25,
    ),
    scale=st.floats(
        min_value=0.1, max_value=3.0, allow_nan=False, allow_infinity=False
    ),
)
def test_ppfd_metrics_scale_linearly_for_field_statistics(
    samples: list[float], scale: float
) -> None:
    base = compute_ppfd_metrics(np.array(samples, dtype=float))
    scaled = compute_ppfd_metrics(np.array(samples, dtype=float) * scale)

    for key in ("mean", "min", "max", "p05", "p50", "p95"):
        assert metric_float(scaled, key) == pytest.approx(
            metric_float(base, key) * scale, rel=1e-12, abs=1e-9
        )
    for key in ("peak_over_mean", "min_over_mean", "min_over_max", "mean_over_peak"):
        assert metric_float(scaled, key) == pytest.approx(
            metric_float(base, key), rel=1e-12, abs=1e-12
        )


@pytest.mark.unit
@given(
    mode=st.sampled_from((MODE_SMD, MODE_COMPETITOR, MODE_HPS)),
    length=st.integers(min_value=10, max_value=30),
    width=st.integers(min_value=10, max_value=30),
    target=st.integers(min_value=500, max_value=1500),
)
def test_request_fingerprint_survives_json_roundtrip_and_key_order(
    mode: str,
    length: int,
    width: int,
    target: int,
) -> None:
    req = RadianceRunRequest(
        action="all",
        mode=mode,
        execution_mode=EXECUTION_MODE_PRECOMPUTED,
        length_ft=length,
        width_ft=width,
        target_ppfd=target,
    )
    payload = req.model_dump()
    reversed_payload = dict(reversed(list(payload.items())))
    round_trip = RadianceRunRequest.model_validate_json(req.model_dump_json())

    assert canonical_request_fingerprint_json(
        payload
    ) == canonical_request_fingerprint_json(reversed_payload)
    assert request_fingerprint(req) == request_fingerprint(round_trip)


@pytest.mark.unit
@given(raw=st.text(min_size=0, max_size=120))
def test_session_id_sanitization_preserves_archive_key_invariants(raw: str) -> None:
    sanitized = _sanitize_session_id(raw)

    assert sanitized
    assert "/" not in sanitized
    assert "\\" not in sanitized
    assert sanitized not in {".", ".."}
    assert len(sanitized) <= 80
