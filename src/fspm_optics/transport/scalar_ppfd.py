"""Scalar PAR/PPFD decoding and baseline trace argument construction.

This module never locates or executes Radiance. It only validates scalar RGB
transport values, parses map data, performs small statistics/transforms, and
constructs the argument vector a caller may pass to an injected runner.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Iterable, Sequence

BASELINE_PPFD_TRANSPORT_BASIS = "canopy_plane_scalar_par_ppfd"
BASELINE_PPFD_RGB_DECODE_METHOD = "grey_channel_average_after_equality_assertion"
BASELINE_SOURCE_CHANNEL_POLICY = "r_equals_g_equals_b_scalar_par_ppfd_carrier"
PPFD_CONVERSION_BASIS = (
    "radiance_rgb_values_are_scalar_par_ppfd_no_179_luminous_conversion"
)
BASELINE_PPFD_PHOTOPIC_LUMINANCE_WEIGHTING_AVOIDED = True
BASELINE_PPFD_RGB_EQUALITY_ABS_TOL = 1e-6
BASELINE_PPFD_RGB_EQUALITY_REL_TOL = 1e-6


@dataclass(frozen=True)
class PpfdMapSample:
    """One Cartesian scalar PPFD sample."""

    x_m: float
    y_m: float
    z_m: float
    ppfd_umol_m2_s: float

    def __post_init__(self) -> None:
        for name, value in (
            ("x_m", self.x_m),
            ("y_m", self.y_m),
            ("z_m", self.z_m),
            ("ppfd_umol_m2_s", self.ppfd_umol_m2_s),
        ):
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ValueError(f"{name} must be a number.")
            if not math.isfinite(float(value)):
                raise ValueError(f"{name} must be finite.")
        if float(self.ppfd_umol_m2_s) < 0.0:
            raise ValueError("ppfd_umol_m2_s must be non-negative.")


def decode_grey_channel_ppfd(
    red: float,
    green: float,
    blue: float,
    *,
    row_number: int | None = None,
) -> float:
    """Validate R=G=B scalar transport and return the channel mean."""

    channels = (float(red), float(green), float(blue))
    if any(not math.isfinite(channel) for channel in channels):
        location = f" at row {row_number}" if row_number is not None else ""
        raise ValueError(f"Scalar PPFD RGB channels must be finite{location}.")
    if any(channel < 0.0 for channel in channels):
        location = f" at row {row_number}" if row_number is not None else ""
        raise ValueError(f"Scalar PPFD RGB channels must be non-negative{location}.")
    if not (
        math.isclose(
            channels[0],
            channels[1],
            rel_tol=BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
            abs_tol=BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
        )
        and math.isclose(
            channels[0],
            channels[2],
            rel_tol=BASELINE_PPFD_RGB_EQUALITY_REL_TOL,
            abs_tol=BASELINE_PPFD_RGB_EQUALITY_ABS_TOL,
        )
    ):
        location = f" Row {row_number}" if row_number is not None else ""
        raise ValueError(
            "Baseline PPFD rtrace output must use grey scalar channels "
            f"(R=G=B).{location} had R={channels[0]:.12g}, "
            f"G={channels[1]:.12g}, B={channels[2]:.12g}."
        )
    return sum(channels) / 3.0


def parse_rtrace_rgb_rows(text: str) -> tuple[float, ...]:
    """Parse rtrace-style rows, decoding the final three columns as scalar PPFD."""

    values: list[float] = []
    for row_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        parts = stripped.split()
        if len(parts) < 3:
            raise ValueError(
                f"Malformed rtrace RGB row {row_number}: expected at least three columns."
            )
        try:
            red, green, blue = (float(value) for value in parts[-3:])
        except ValueError as exc:
            raise ValueError(
                f"Malformed rtrace RGB row {row_number}: channels must be numeric."
            ) from exc
        values.append(
            decode_grey_channel_ppfd(
                red,
                green,
                blue,
                row_number=row_number,
            )
        )
    if not values:
        raise ValueError("No rtrace RGB rows were provided.")
    return tuple(values)


def decode_rtrace_ppfd_map(
    coordinates_m: Iterable[Sequence[float]],
    rgb_text: str,
    *,
    oversample: int = 1,
) -> tuple[PpfdMapSample, ...]:
    """Average oversampled scalar RGB rows into coordinate-aligned PPFD samples."""

    if isinstance(oversample, bool) or not isinstance(oversample, int) or oversample <= 0:
        raise ValueError("oversample must be a positive integer.")
    coordinates: list[tuple[float, float, float]] = []
    for index, coordinate in enumerate(coordinates_m):
        if len(coordinate) < 3:
            raise ValueError(f"Coordinate row {index + 1} must contain x, y, and z.")
        point = tuple(float(value) for value in coordinate[:3])
        if any(not math.isfinite(value) for value in point):
            raise ValueError(f"Coordinate row {index + 1} must be finite.")
        coordinates.append(point)
    values = parse_rtrace_rgb_rows(rgb_text)
    if len(coordinates) != len(values):
        raise ValueError(
            "Coordinate and rtrace row counts must match: "
            f"{len(coordinates)} != {len(values)}."
        )
    if len(values) % oversample:
        raise ValueError(
            f"Receiver row count {len(values)} is not divisible by oversample={oversample}."
        )

    samples: list[PpfdMapSample] = []
    for start in range(0, len(values), oversample):
        point_group = coordinates[start : start + oversample]
        reference = point_group[0]
        if any(point != reference for point in point_group[1:]):
            raise ValueError(
                "Oversampled coordinate rows must repeat the same x, y, z position."
            )
        value_group = values[start : start + oversample]
        samples.append(
            PpfdMapSample(
                x_m=reference[0],
                y_m=reference[1],
                z_m=reference[2],
                ppfd_umol_m2_s=sum(value_group) / len(value_group),
            )
        )
    return tuple(samples)


def parse_ppfd_map(text: str, *, source: str = "<text>") -> tuple[PpfdMapSample, ...]:
    """Parse four-column x/y/z/PPFD map text."""

    samples: list[PpfdMapSample] = []
    for row_number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        parts = stripped.replace(",", " ").split()
        if len(parts) < 4:
            raise ValueError(
                f"Malformed PPFD map row {row_number} in {source}: expected four columns."
            )
        try:
            x_m, y_m, z_m, ppfd = (float(value) for value in parts[:4])
        except ValueError as exc:
            raise ValueError(
                f"Malformed PPFD map row {row_number} in {source}: values must be numeric."
            ) from exc
        samples.append(PpfdMapSample(x_m, y_m, z_m, ppfd))
    if not samples:
        raise ValueError(f"No PPFD values found in {source}.")
    return tuple(samples)


def load_ppfd_map(path: str | Path) -> tuple[PpfdMapSample, ...]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"PPFD map not found: {source}") from exc
    return parse_ppfd_map(text, source=str(source))


def ppfd_mean(samples: Iterable[PpfdMapSample]) -> float:
    values = [float(sample.ppfd_umol_m2_s) for sample in samples]
    if not values:
        raise ValueError("At least one PPFD sample is required.")
    return sum(values) / len(values)


def ppfd_max(samples: Iterable[PpfdMapSample]) -> float:
    values = [float(sample.ppfd_umol_m2_s) for sample in samples]
    if not values:
        raise ValueError("At least one PPFD sample is required.")
    return max(values)


def scale_ppfd_map(
    samples: Iterable[PpfdMapSample], multiplier: float
) -> tuple[PpfdMapSample, ...]:
    scale = float(multiplier)
    if not math.isfinite(scale) or scale < 0.0:
        raise ValueError("multiplier must be finite and non-negative.")
    return tuple(
        PpfdMapSample(
            sample.x_m,
            sample.y_m,
            sample.z_m,
            sample.ppfd_umol_m2_s * scale,
        )
        for sample in samples
    )


def format_ppfd_map(samples: Iterable[PpfdMapSample]) -> str:
    return "".join(
        f"{sample.x_m:.6f} {sample.y_m:.6f} {sample.z_m:.6f} "
        f"{sample.ppfd_umol_m2_s:.6f}\n"
        for sample in samples
    )


def build_baseline_rtrace_argv(
    *,
    octree: str | Path,
    options: Sequence[str],
    nthreads: int,
    rtrace_bin: str | Path = "rtrace",
) -> list[str]:
    """Construct baseline scalar irradiance argv without executing it."""

    executable = str(rtrace_bin)
    if not executable:
        raise ValueError("rtrace_bin must be non-empty.")
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or nthreads <= 0:
        raise ValueError("nthreads must be a positive integer.")
    octree_text = str(octree)
    if not octree_text:
        raise ValueError("octree must be non-empty.")
    option_list = [str(option) for option in options]
    if any(not option for option in option_list):
        raise ValueError("Radiance options must be non-empty strings.")
    return [
        executable,
        "-h",
        "-I+",
        "-n",
        str(nthreads),
        *option_list,
        octree_text,
    ]
