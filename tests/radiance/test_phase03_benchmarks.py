from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from rad_rebuild.radiance.backend.models import RadianceRunRequest
from rad_rebuild.radiance.backend.workspace import request_fingerprint
from rad_rebuild.radiance.config import EXECUTION_MODE_PRECOMPUTED, MODE_SMD
from rad_rebuild.radiance.engine.layout.layout_generator import (
    generate_layout_with_zones,
)
from rad_rebuild.radiance.engine.photometry.ppfd_metrics import compute_ppfd_metrics


@pytest.mark.benchmark
@pytest.mark.slow
@pytest.mark.radiance
def test_largest_pure_layout_benchmark(benchmark: Any) -> None:
    result = benchmark(generate_layout_with_zones, 30, 30)

    assert len(result["all_positions"]) == 481


@pytest.mark.benchmark
@pytest.mark.slow
@pytest.mark.radiance
def test_ppfd_metrics_kernel_benchmark(benchmark: Any) -> None:
    samples = np.linspace(500.0, 1500.0, num=225)

    result = benchmark(
        compute_ppfd_metrics,
        samples,
        setpoint_ppfd=1000.0,
        canopy_area_m2=9.290304,
        total_input_watts=500.0,
        emitted_ppf_umol_s=12000.0,
        legacy_metrics=True,
    )

    assert result["mean"] == pytest.approx(1000.0)


@pytest.mark.benchmark
@pytest.mark.slow
@pytest.mark.unit
def test_request_serialization_benchmark(benchmark: Any) -> None:
    req = RadianceRunRequest(
        action="all",
        mode=MODE_SMD,
        execution_mode=EXECUTION_MODE_PRECOMPUTED,
        length_ft=20,
        width_ft=20,
        target_ppfd=1000,
    )

    digest = benchmark(request_fingerprint, req)

    assert len(digest) == 64
