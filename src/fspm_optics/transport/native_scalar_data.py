"""Source-neutral native scalar decoding, metrics, DAT, and NPZ contracts."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
import statistics
from typing import Callable
import zipfile

import numpy as np

from fspm_optics.transport.scalar_ppfd import PpfdMapSample, decode_grey_channel_ppfd

ErrorFactory = Callable[[str], Exception]


@dataclass(frozen=True, slots=True)
class NativeScalarMetricValues:
    sensor_count: int
    mean_ppfd_umol_m2_s: float
    minimum_ppfd_umol_m2_s: float
    maximum_ppfd_umol_m2_s: float
    standard_deviation_ppfd_umol_m2_s: float
    coefficient_of_variation: float
    coefficient_of_variation_percent: float
    minimum_to_mean_uniformity: float


def compute_native_scalar_metric_values(
    samples: tuple[PpfdMapSample, ...],
) -> NativeScalarMetricValues:
    if not samples:
        raise ValueError("at least one PPFD sample is required.")
    values = tuple(float(item.ppfd_umol_m2_s) for item in samples)
    mean = statistics.fmean(values)
    standard_deviation = statistics.pstdev(values)
    cv = standard_deviation / mean if mean > 0.0 else 0.0
    return NativeScalarMetricValues(
        sensor_count=len(values),
        mean_ppfd_umol_m2_s=mean,
        minimum_ppfd_umol_m2_s=min(values),
        maximum_ppfd_umol_m2_s=max(values),
        standard_deviation_ppfd_umol_m2_s=standard_deviation,
        coefficient_of_variation=cv,
        coefficient_of_variation_percent=100.0 * cv,
        minimum_to_mean_uniformity=min(values) / mean if mean > 0.0 else 0.0,
    )


def format_native_scalar_ppfd_npz(samples: tuple[PpfdMapSample, ...]) -> bytes:
    arrays = (
        ("x_m", np.asarray([item.x_m for item in samples], dtype=np.float64)),
        ("y_m", np.asarray([item.y_m for item in samples], dtype=np.float64)),
        ("z_m", np.asarray([item.z_m for item in samples], dtype=np.float64)),
        (
            "ppfd_umol_m2_s",
            np.asarray([item.ppfd_umol_m2_s for item in samples], dtype=np.float64),
        ),
    )
    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in arrays:
            member = BytesIO()
            np.save(member, array, allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, member.getvalue())
    return output.getvalue()


def validate_native_scalar_dat(
    data: bytes, *, error_factory: ErrorFactory
) -> dict[str, object]:
    if not data or b"\0" in data:
        raise error_factory("converted DAT is missing or binary.")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise error_factory("converted DAT must be ASCII.") from exc
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if len(lines) < 4 or lines[0] != "2":
        raise error_factory("converted DAT must declare two dimensions.")
    dimensions: list[dict[str, float | int]] = []
    expected_values = 1
    for line in lines[1:3]:
        parts = line.split()
        if len(parts) != 3:
            raise error_factory("converted DAT dimension rows are invalid.")
        try:
            start, stop, raw_count = (float(value) for value in parts)
        except ValueError as exc:
            raise error_factory("converted DAT dimensions must be numeric.") from exc
        count = int(raw_count)
        if (
            not all(math.isfinite(value) for value in (start, stop, raw_count))
            or count != raw_count
            or count < 2
            or stop <= start
        ):
            raise error_factory("converted DAT dimension bounds are invalid.")
        expected_values *= count
        dimensions.append({"start": start, "stop": stop, "count": count})
    try:
        values = tuple(float(token) for line in lines[3:] for token in line.split())
    except ValueError as exc:
        raise error_factory("converted DAT values must be numeric.") from exc
    if len(values) != expected_values:
        raise error_factory(
            f"converted DAT value count mismatch: expected {expected_values}, got {len(values)}."
        )
    if any(not math.isfinite(value) or value < 0.0 for value in values):
        raise error_factory("converted DAT values must be finite and non-negative.")
    if not any(value > 0.0 for value in values):
        raise error_factory("converted DAT angular table must be nonzero.")
    return {
        "dimension_count": 2,
        "dimensions": dimensions,
        "value_count": len(values),
        "finite_nonnegative": True,
    }


def decode_exact_native_scalar_rgb_rows(
    text: str,
    *,
    expected_count: int,
    error_factory: ErrorFactory,
) -> tuple[float, ...]:
    if (
        isinstance(expected_count, bool)
        or not isinstance(expected_count, int)
        or expected_count <= 0
    ):
        raise ValueError("expected_count must be a positive integer.")
    values: list[float] = []
    for row_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 3:
            raise error_factory(
                f"rtrace row {row_number} must contain exactly three channels."
            )
        try:
            red, green, blue = (float(value) for value in parts)
            values.append(
                decode_grey_channel_ppfd(red, green, blue, row_number=row_number)
            )
        except ValueError as exc:
            raise error_factory(f"invalid scalar rtrace output: {exc}") from exc
    if len(values) != expected_count:
        raise error_factory(
            f"rtrace receiver count mismatch: expected {expected_count}, got {len(values)}."
        )
    return tuple(values)
