"""Source-neutral native Rex receiver decoding, metrics, and NPZ contracts."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import math
from typing import Callable, Sequence
import zipfile

import numpy as np
from numpy.typing import NDArray

from fspm_optics.receivers.samples import MeshPatchReceiverSample

FloatArray = NDArray[np.float64]
IntArray = NDArray[np.int64]
ErrorFactory = Callable[[str], Exception]


@dataclass(frozen=True, slots=True)
class NativeRexReceiverGroups:
    front_indices: IntArray
    back_indices: IntArray
    physical_patch_area_m2: FloatArray


@dataclass(frozen=True, slots=True)
class NativeIncidentMetricValues:
    receiver_count: int
    front_receiver_count: int
    back_receiver_count: int
    physical_patch_count: int
    minimum_incident_pfd: float
    maximum_incident_pfd: float
    front_area_weighted_mean_incident_pfd: float
    back_area_weighted_mean_incident_pfd: float
    combined_area_weighted_mean_incident_pfd: float
    whole_plant_incident_photon_flux_umol_s: float


@dataclass(frozen=True, slots=True)
class NativeIncidentComparisonValues:
    scalar_area_weighted_mean: float
    four_band_area_weighted_mean: float
    signed_difference: float
    absolute_difference: float
    relative_difference: float | None


@dataclass(frozen=True, slots=True)
class NativeScalarFourBandValues:
    front: NativeIncidentComparisonValues
    back: NativeIncidentComparisonValues
    combined: NativeIncidentComparisonValues
    receiver_level_rmse: float
    receiver_level_relative_rmse: float | None


def decode_native_rex_receiver_rgb(
    text: str,
    *,
    interval_id: str,
    expected_receiver_count: int = 1024,
    error_factory: ErrorFactory,
) -> FloatArray:
    if (
        isinstance(expected_receiver_count, bool)
        or not isinstance(expected_receiver_count, int)
        or expected_receiver_count <= 0
    ):
        raise ValueError("expected_receiver_count must be a positive integer.")
    values: list[float] = []
    for row_number, line in enumerate(text.splitlines(), start=1):
        parts = line.split()
        if len(parts) != 3:
            raise error_factory(
                f"{interval_id} row {row_number} must have exactly three channels."
            )
        try:
            channels = tuple(float(value) for value in parts)
        except ValueError as exc:
            raise error_factory(
                f"{interval_id} row {row_number} channels must be numeric."
            ) from exc
        if any(not math.isfinite(value) for value in channels):
            raise error_factory(
                f"{interval_id} row {row_number} channels must be finite."
            )
        if any(value < 0.0 for value in channels):
            raise error_factory(
                f"{interval_id} row {row_number} channels must be non-negative."
            )
        if not (
            math.isclose(channels[0], channels[1], rel_tol=1e-6, abs_tol=1e-6)
            and math.isclose(
                channels[0], channels[2], rel_tol=1e-6, abs_tol=1e-6
            )
        ):
            raise error_factory(
                f"{interval_id} row {row_number} must preserve equal R=G=B channels."
            )
        values.append(math.fsum(channels) / 3.0)
    if len(values) != expected_receiver_count:
        raise error_factory(
            f"{interval_id} receiver count mismatch: expected "
            f"{expected_receiver_count}, got {len(values)}."
        )
    array = np.asarray(values, dtype=np.float64)
    if array.shape != (expected_receiver_count,):
        raise error_factory(
            f"{interval_id} decoded shape is invalid: {array.shape}."
        )
    return array


def build_native_rex_receiver_groups(
    samples: Sequence[MeshPatchReceiverSample],
    *,
    error_factory: ErrorFactory,
) -> NativeRexReceiverGroups:
    if len(samples) != 1024:
        raise error_factory("Rex receiver contract requires exactly 1024 samples.")
    front: list[int] = []
    back: list[int] = []
    areas: list[float] = []
    receiver_ids: set[str] = set()
    for pair_index in range(512):
        front_index = pair_index * 2
        back_index = front_index + 1
        first = samples[front_index]
        second = samples[back_index]
        if (
            first.side != "front"
            or second.side != "back"
            or first.patch_id != second.patch_id
            or first.leaf_id != second.leaf_id
            or first.area_m2 != second.area_m2
        ):
            raise error_factory(
                f"receiver pair {pair_index} is not a stable front/back physical patch."
            )
        for sample in (first, second):
            if sample.receiver_id in receiver_ids:
                raise error_factory("receiver identities must be unique.")
            receiver_ids.add(sample.receiver_id)
            values = (*sample.point_m, *sample.normal)
            if any(not math.isfinite(value) for value in values):
                raise error_factory("receiver origins/directions must be finite.")
            if not math.isclose(
                math.sqrt(math.fsum(value * value for value in sample.normal)),
                1.0,
                rel_tol=0.0,
                abs_tol=1e-12,
            ):
                raise error_factory("receiver directions must be unit vectors.")
        if not math.isfinite(first.area_m2) or first.area_m2 <= 0.0:
            raise error_factory("physical patch area must be finite and positive.")
        front.append(front_index)
        back.append(back_index)
        areas.append(first.area_m2)
    return NativeRexReceiverGroups(
        np.asarray(front, dtype=np.int64),
        np.asarray(back, dtype=np.int64),
        np.asarray(areas, dtype=np.float64),
    )


def compute_native_incident_metrics(
    values: Sequence[float] | FloatArray,
    samples: Sequence[MeshPatchReceiverSample],
    *,
    groups: NativeRexReceiverGroups | None = None,
    label: str = "incident",
) -> NativeIncidentMetricValues:
    vector = validated_native_rex_vector(values, len(samples), label)
    selected = groups or build_native_rex_receiver_groups(
        samples, error_factory=ValueError
    )
    area = selected.physical_patch_area_m2
    physical_area = float(area.sum())
    front_flux = float(np.dot(vector[selected.front_indices], area))
    back_flux = float(np.dot(vector[selected.back_indices], area))
    return NativeIncidentMetricValues(
        receiver_count=len(vector),
        front_receiver_count=len(selected.front_indices),
        back_receiver_count=len(selected.back_indices),
        physical_patch_count=len(area),
        minimum_incident_pfd=float(vector.min()),
        maximum_incident_pfd=float(vector.max()),
        front_area_weighted_mean_incident_pfd=front_flux / physical_area,
        back_area_weighted_mean_incident_pfd=back_flux / physical_area,
        combined_area_weighted_mean_incident_pfd=(front_flux + back_flux)
        / physical_area,
        whole_plant_incident_photon_flux_umol_s=front_flux + back_flux,
    )


def compute_native_scalar_four_band_diagnostics(
    scalar: Sequence[float] | FloatArray,
    four_band: Sequence[float] | FloatArray,
    samples: Sequence[MeshPatchReceiverSample],
    *,
    groups: NativeRexReceiverGroups | None = None,
) -> NativeScalarFourBandValues:
    scalar_vector = validated_native_rex_vector(scalar, len(samples), "scalar_par")
    band_vector = validated_native_rex_vector(
        four_band, len(samples), "four_band_par"
    )
    selected = groups or build_native_rex_receiver_groups(
        samples, error_factory=ValueError
    )
    scalar_metrics = compute_native_incident_metrics(
        scalar_vector, samples, groups=selected
    )
    band_metrics = compute_native_incident_metrics(
        band_vector, samples, groups=selected
    )
    difference = scalar_vector - band_vector
    rmse = float(np.sqrt(np.mean(np.square(difference))))
    scalar_rms = float(np.sqrt(np.mean(np.square(scalar_vector))))
    return NativeScalarFourBandValues(
        front=_comparison(
            scalar_metrics.front_area_weighted_mean_incident_pfd,
            band_metrics.front_area_weighted_mean_incident_pfd,
        ),
        back=_comparison(
            scalar_metrics.back_area_weighted_mean_incident_pfd,
            band_metrics.back_area_weighted_mean_incident_pfd,
        ),
        combined=_comparison(
            scalar_metrics.combined_area_weighted_mean_incident_pfd,
            band_metrics.combined_area_weighted_mean_incident_pfd,
        ),
        receiver_level_rmse=rmse,
        receiver_level_relative_rmse=(None if scalar_rms == 0.0 else rmse / scalar_rms),
    )


def format_native_rex_incident_npz(
    arrays: Sequence[tuple[str, Sequence[float] | FloatArray]],
    *,
    expected_names: tuple[str, ...],
) -> bytes:
    ordered = tuple(
        (name, np.asarray(values, dtype=np.float64)) for name, values in arrays
    )
    if tuple(name for name, _ in ordered) != expected_names:
        raise ValueError("incident NPZ arrays are incomplete or out of order.")
    if any(array.shape != (1024,) for _, array in ordered):
        raise ValueError("incident NPZ arrays must each have shape (1024,).")
    output = BytesIO()
    with zipfile.ZipFile(output, mode="w", compression=zipfile.ZIP_STORED) as archive:
        for name, array in ordered:
            member = BytesIO()
            np.save(member, array, allow_pickle=False)
            info = zipfile.ZipInfo(f"{name}.npy", date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o600 << 16
            archive.writestr(info, member.getvalue())
    return output.getvalue()


def validated_native_rex_vector(
    values: Sequence[float] | FloatArray,
    expected_count: int,
    label: str,
) -> FloatArray:
    vector = np.asarray(values, dtype=np.float64)
    if vector.shape != (expected_count,):
        raise ValueError(f"{label} values must have shape ({expected_count},).")
    if not np.all(np.isfinite(vector)) or np.any(vector < 0.0):
        raise ValueError(f"{label} values must be finite and non-negative.")
    return vector


def _comparison(
    scalar: float, four_band: float
) -> NativeIncidentComparisonValues:
    signed = scalar - four_band
    absolute = abs(signed)
    return NativeIncidentComparisonValues(
        scalar_area_weighted_mean=scalar,
        four_band_area_weighted_mean=four_band,
        signed_difference=signed,
        absolute_difference=absolute,
        relative_difference=(None if scalar == 0.0 else absolute / scalar),
    )
