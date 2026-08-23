"""Phase 27G-D5-C2 authenticated endpoint-repeatability analysis.

The analysis is deliberately additive and descriptive.  It authenticates the
completed D5-A1, D5-A2, and D5-C1 authorities before decoding receiver values,
then reuses the authoritative Phase 27G-C four-band PAR kernel for both the
original and repeated observations.  It never invokes Radiance and never
creates a production calibration resource.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import shutil
import struct
import tempfile
from typing import Callable, Iterable, Mapping, Sequence

from fspm_optics.application import surface_flux_coefficient_analysis as a2
from fspm_optics.application import surface_flux_endpoint_replication as c1
from fspm_optics.application.fspm_science import (
    FspmScientificAggregationError,
    ParPatchSurfaceLight,
    stream_juvenile_par_surface_light,
)
from fspm_optics.application.surface_flux_recalibration import (
    D5_BAND_ORDER,
    D5_COMPLETION_NAME,
    D5_EXPECTED_PLANT_COUNT,
    D5_EXPECTED_RECEIVER_BYTES_PER_BAND,
    D5_EXPECTED_RECEIVER_COUNT_PER_BAND,
    D5_EXPERIMENT_ID,
    D5_PATCHES_PER_PLANT,
    D5_QUALITY_ORDER,
    D5_RECEIVERS_SHA256,
    D5_SAMPLING_PROFILE_ID,
    D5_TOPOLOGY_SHA256,
    _quality_option_identity,
)


D5_C2_ANALYSIS_ID = "phase27g-d5-c2-endpoint-repeatability-analysis-v1"
D5_C2_SCHEMA_ID = "fspm-optics.surface-flux-endpoint-repeatability-analysis"
D5_C2_COMPLETION_SCHEMA_ID = (
    "fspm-optics.surface-flux-endpoint-repeatability-analysis-completion"
)
D5_C2_SCHEMA_VERSION = 1
D5_C2_REPORT_NAME = "surface-flux-endpoint-repeatability-analysis.v1.json"
D5_C2_COMPLETION_NAME = (
    "surface-flux-endpoint-repeatability-analysis-completion.v1.json"
)

D5_C2_FAMILY_ORDER = ("quality", "standard")
D5_C2_LEVEL_ORDER = (250.0, 500.0)
D5_C2_SIDE_ORDER = ("front", "back")
D5_C2_METRIC_ORDER = ("incident", "absorbed")
D5_C2_REPLICATE_ORDER = ("original_a1", "repeated_c1")
D5_C2_CHANNEL_ORDER = tuple(
    (side, metric, f"{side}_{metric}")
    for side in D5_C2_SIDE_ORDER
    for metric in D5_C2_METRIC_ORDER
)

D5_C2_DEFAULT_A1_INPUT_DIRECTORY = Path(
    "/home/austin/fspm-calibration-runs/phase27g-d5-a1-run-v3b"
)
D5_C2_DEFAULT_A2_INPUT_DIRECTORY = Path(
    "/home/austin/fspm-calibration-runs/phase27g-d5-a2-analysis-v1"
)
D5_C2_DEFAULT_C1_INPUT_DIRECTORY = Path(
    "/home/austin/fspm-calibration-runs/phase27g-d5-c1-endpoint-replication-v1"
)
D5_C2_DEFAULT_OUTPUT_DIRECTORY = Path(
    "/home/austin/fspm-calibration-runs/phase27g-d5-c2-repeatability-analysis-v1"
)

_COEFFICIENT_MANIFEST_FIELDS = {
    "schema_id",
    "schema_version",
    "analysis_id",
    "source_experiment_id",
    "source_completion_sha256",
    "component_type",
    "byte_order",
    "stride_bytes",
    "units",
    "coefficient_symbol",
    "array_count",
    "values_per_array",
    "array_order",
    "coefficient_artifacts",
    "combined_canonical_payload",
    "production_calibration_resource",
}
_COEFFICIENT_RECORD_FIELDS = {
    "schema_id",
    "schema_version",
    "order_index",
    "family",
    "side",
    "metric",
    "artifact",
    "distribution_summary",
}
_BINARY_ARTIFACT_FIELDS = {
    "role",
    "path",
    "media_type",
    "byte_length",
    "sha256",
    "schema",
    "component_type",
    "byte_order",
    "stride_bytes",
    "value_count",
    "shape",
    "ordering",
    "units",
}
_FIT_FIELDS = {
    "local_patch_index",
    "coefficient_gamma",
    "coefficient_units",
    "through_origin_equation",
    "r_squared",
    "r_squared_definition",
    "relative_ratio_drift",
    "relative_ratio_drift_definition",
    "residual_sum_squares",
    "total_sum_squares_centered",
    "levels",
    "promotion_eligible",
    "promotion_reasons",
}
_LEVEL_FIELDS = {
    "requested_reference_level_umol_m2_s",
    "achieved_reference_R_umol_m2_s",
    "mean_q_over_64_plants_umol_m2_s",
    "response_ratio_mean_q_over_R",
    "predicted_mean_q_umol_m2_s",
    "signed_residual_umol_m2_s",
}
_A2_FAILURE_REASONS = (
    "exact_zero_coefficient",
    "through_origin_r_squared_below_minimum",
    "relative_ratio_drift_above_maximum",
)

EventSink = Callable[[str, Mapping[str, object] | None], None]


class SurfaceFluxRepeatabilityAnalysisError(RuntimeError):
    """A D5-C2 authentication, numerical, or publication contract failed."""


@dataclass(frozen=True, slots=True)
class SurfaceFluxRepeatabilityAnalysisConfig:
    a1_input_directory: Path = D5_C2_DEFAULT_A1_INPUT_DIRECTORY
    a2_input_directory: Path = D5_C2_DEFAULT_A2_INPUT_DIRECTORY
    c1_input_directory: Path = D5_C2_DEFAULT_C1_INPUT_DIRECTORY
    output_directory: Path = D5_C2_DEFAULT_OUTPUT_DIRECTORY

    def __post_init__(self) -> None:
        object.__setattr__(self, "a1_input_directory", Path(self.a1_input_directory))
        object.__setattr__(self, "a2_input_directory", Path(self.a2_input_directory))
        object.__setattr__(self, "c1_input_directory", Path(self.c1_input_directory))
        object.__setattr__(self, "output_directory", Path(self.output_directory))


@dataclass(frozen=True, slots=True)
class SurfaceFluxRepeatabilityAnalysisPublication:
    output_directory: Path
    report_path: Path
    completion_path: Path
    report: Mapping[str, object]
    completion: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _AuthenticatedC1:
    root: Path
    configuration: Mapping[str, object]
    configuration_sha256: str
    outcome: Mapping[str, object]
    outcome_sha256: str
    job_results: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class _CoefficientEvidence:
    manifest: Mapping[str, object]
    manifest_sha256: str
    values_by_identity: Mapping[tuple[str, str, str], tuple[float, ...]]
    fits_by_identity: Mapping[tuple[str, str, str], tuple[Mapping[str, object], ...]]
    all_failed_records: tuple[Mapping[str, object], ...]


@dataclass(frozen=True, slots=True)
class _AuthenticatedSources:
    upstream: c1._AuthenticatedInputs
    c1: _AuthenticatedC1
    coefficients: _CoefficientEvidence
    authorities: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class _Observation:
    family: str
    requested_level: float
    replicate: str
    achieved_reference: float
    values_by_channel: Mapping[str, tuple[float, ...]]
    stage_c_validation: Mapping[str, object]


def analyze_surface_flux_endpoint_repeatability(
    config: SurfaceFluxRepeatabilityAnalysisConfig,
    *,
    event_sink: EventSink | None = None,
) -> SurfaceFluxRepeatabilityAnalysisPublication:
    """Authenticate all sources, derive two observations, and publish C2."""

    if not isinstance(config, SurfaceFluxRepeatabilityAnalysisConfig):
        raise TypeError("config must be SurfaceFluxRepeatabilityAnalysisConfig.")
    sink = event_sink or (lambda _message, _data=None: None)
    a1_root, a2_root, c1_root, output = _validated_locations(config)
    sink("Authenticating completed D5-A1, D5-A2, and D5-C1 authorities.", None)
    authenticated = _authenticate_sources(a1_root, a2_root, c1_root)
    sink("Authentication complete; decoding receiver values with Stage C.", None)
    observations = _derive_observations(authenticated)
    report = _analysis_report(authenticated, observations)
    publication = _publish(output, report, authenticated.authorities)
    sink("Published deterministic D5-C2 evidence atomically.", None)
    return publication


def _validated_locations(
    config: SurfaceFluxRepeatabilityAnalysisConfig,
) -> tuple[Path, Path, Path, Path]:
    inputs = tuple(
        _real_input_root(path, label)
        for path, label in (
            (config.a1_input_directory, "D5-A1"),
            (config.a2_input_directory, "D5-A2"),
            (config.c1_input_directory, "D5-C1"),
        )
    )
    if len(set(inputs)) != len(inputs):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-A1, D5-A2, and D5-C1 inputs must be distinct directories."
        )
    requested_output = config.output_directory.expanduser()
    if requested_output.is_symlink() or requested_output.exists():
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-C2 output directory must initially be absent."
        )
    parent = requested_output.parent.resolve(strict=True)
    if not parent.is_dir() or parent.is_symlink():
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-C2 output parent must be a real directory."
        )
    output = parent / requested_output.name
    for root in inputs:
        if output == root or output.is_relative_to(root) or root.is_relative_to(output):
            raise SurfaceFluxRepeatabilityAnalysisError(
                "D5-C2 output must not overlap any authenticated input."
            )
    return inputs[0], inputs[1], inputs[2], output


def _real_input_root(path: Path, label: str) -> Path:
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"{label} input must be a real non-symbolic-link directory."
        )
    try:
        root = expanded.resolve(strict=True)
    except OSError as exc:
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"{label} input directory does not exist: {path}"
        ) from exc
    if not root.is_dir() or root.is_symlink():
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"{label} input must be a real non-symbolic-link directory."
        )
    return root


def _authenticate_sources(
    a1_root: Path, a2_root: Path, c1_root: Path
) -> _AuthenticatedSources:
    """Finish every source authentication before receiver-value decoding."""

    try:
        upstream = c1._authenticate_inputs(a1_root, a2_root)
        authenticated_c1 = _authenticate_completed_c1(c1_root, upstream)
        coefficients = _authenticate_coefficient_evidence(upstream)
        authorities = _source_authorities(upstream, authenticated_c1, coefficients)
    except SurfaceFluxRepeatabilityAnalysisError:
        raise
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"D5-C2 source authentication failed: {exc}"
        ) from exc
    return _AuthenticatedSources(
        upstream=upstream,
        c1=authenticated_c1,
        coefficients=coefficients,
        authorities=authorities,
    )


def _authenticate_completed_c1(
    root: Path, upstream: c1._AuthenticatedInputs
) -> _AuthenticatedC1:
    state_path = root / c1.D5_C1_CONFIGURATION_NAME
    outcome_path = root / c1.D5_C1_OUTCOME_NAME
    if not state_path.is_file() or state_path.is_symlink():
        raise SurfaceFluxRepeatabilityAnalysisError(
            "completed D5-C1 configuration is missing or unsafe."
        )
    if not outcome_path.is_file() or outcome_path.is_symlink():
        raise SurfaceFluxRepeatabilityAnalysisError(
            "completed D5-C1 outcome is missing or unsafe."
        )
    state = c1._read_json_object(state_path)
    c1_config = c1.SurfaceFluxEndpointReplicationConfig(
        a1_input_directory=upstream.a1.input_root,
        a2_input_directory=upstream.a2.root,
        output_directory=root,
    )
    plan = c1._build_plan(upstream, output_root=root)
    try:
        identity = c1._validate_stored_identity_without_discovery(
            state,
            config=c1_config,
            authenticated=upstream,
            plan=plan,
        )
        outcome = c1._validate_outcome(
            root,
            identity=identity,
            authenticated=upstream,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"completed D5-C1 authority was rejected: {exc}"
        ) from exc
    if (
        outcome.get("status") != "complete"
        or outcome.get("execution_complete") is not True
        or outcome.get("stage_a_trace_count") != c1.D5_C1_STAGE_A_TRACE_COUNT
        or outcome.get("stage_b_trace_count_planned")
        != c1.D5_C1_STAGE_B_TRACE_COUNT
        or outcome.get("stage_b_trace_count_executed")
        != c1.D5_C1_STAGE_B_TRACE_COUNT
        or outcome.get("stage_b_trace_count_remaining") != 0
        or outcome.get("job_order")
        != [job.job_id for job in c1.endpoint_replication_jobs()]
        or outcome.get("independently_seeded_sample_claimed") is not False
        or outcome.get("nonidentical_bytes_prove_independent_seed") is not False
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-C1 is not the required complete 0-Stage-A/16-Stage-B authority."
        )
    results: list[Mapping[str, object]] = []
    nonidentical_count = 0
    for source in upstream.source_jobs:
        path = root / c1.D5_C1_JOBS_DIRECTORY / source.job.job_id / c1.D5_C1_JOB_RESULT_NAME
        result = c1._read_json_object(path)
        traces = result.get("ordered_traces")
        if (
            result.get("status") != "complete"
            or result.get("stage_a_trace_count") != 0
            or result.get("stage_b_trace_count") != len(D5_BAND_ORDER)
            or not isinstance(traces, list)
            or len(traces) != len(D5_BAND_ORDER)
        ):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"D5-C1 job is incomplete after validation: {source.job.job_id}"
            )
        for band_id, trace in zip(D5_BAND_ORDER, traces, strict=True):
            if (
                not isinstance(trace, Mapping)
                or trace.get("band_id") != band_id
                or trace.get("byte_identical_to_original") is not False
                or trace.get("independence_interpretation")
                != "nonidentical bytes do not prove an independently seeded sample"
            ):
                raise SurfaceFluxRepeatabilityAnalysisError(
                    f"D5-C1 pairing/nonidentity contract changed: "
                    f"{source.job.job_id}/{band_id}"
                )
            nonidentical_count += 1
        results.append(result)
    if nonidentical_count != c1.D5_C1_STAGE_B_TRACE_COUNT:
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-C1 does not contain exactly sixteen nonidentical repetitions."
        )
    return _AuthenticatedC1(
        root=root,
        configuration=state,
        configuration_sha256=_sha256_file(state_path),
        outcome=outcome,
        outcome_sha256=_sha256_file(outcome_path),
        job_results=tuple(results),
    )


def _authenticate_coefficient_evidence(
    upstream: c1._AuthenticatedInputs,
) -> _CoefficientEvidence:
    root = upstream.a2.root
    manifest_record = c1._a2_authority(
        upstream.a2.completion.get("coefficient_manifest"),
        a2.D5_A2_COEFFICIENT_MANIFEST_NAME,
        "D5-A2 coefficient manifest",
    )
    manifest = c1._authorized_json(root, manifest_record, "D5-A2 coefficient manifest")
    if set(manifest) != _COEFFICIENT_MANIFEST_FIELDS:
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-A2 coefficient-manifest field inventory changed."
        )
    expected_identities = tuple(
        (family, side, metric)
        for family in D5_QUALITY_ORDER
        for side, metric, _channel in a2.COEFFICIENT_CHANNEL_ORDER
    )
    expected_order = [
        {
            "order_index": index,
            "family": family,
            "side": side,
            "metric": metric,
        }
        for index, (family, side, metric) in enumerate(expected_identities)
    ]
    artifacts = manifest.get("coefficient_artifacts")
    analyses = upstream.a2.report.get("family_side_metric_analyses")
    if (
        manifest.get("schema_id") != a2.D5_A2_COEFFICIENT_SCHEMA_ID
        or manifest.get("schema_version") != a2.D5_A2_COEFFICIENT_SCHEMA_VERSION
        or manifest.get("analysis_id") != a2.D5_A2_ANALYSIS_ID
        or manifest.get("source_experiment_id") != D5_EXPERIMENT_ID
        or manifest.get("source_completion_sha256")
        != upstream.a1.completion_sha256
        or manifest.get("component_type") != a2.COMPONENT_TYPE
        or manifest.get("byte_order") != a2.BYTE_ORDER
        or manifest.get("stride_bytes") != a2.FLOAT64_STRIDE_BYTES
        or manifest.get("units") != a2.COEFFICIENT_UNITS
        or manifest.get("coefficient_symbol")
        != "gamma[family,side,metric,local_patch]"
        or manifest.get("array_count") != a2.COEFFICIENT_ARRAY_COUNT
        or manifest.get("values_per_array") != D5_PATCHES_PER_PLANT
        or manifest.get("array_order") != expected_order
        or not isinstance(artifacts, list)
        or len(artifacts) != len(expected_identities)
        or not isinstance(analyses, list)
        or len(analyses) != len(expected_identities)
        or manifest.get("production_calibration_resource") is not False
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-A2 coefficient identity, shape, or order changed."
        )
    achieved_by_family = _a2_achieved_references(upstream.a2.report)
    values_by_identity: dict[tuple[str, str, str], tuple[float, ...]] = {}
    fits_by_identity: dict[
        tuple[str, str, str], tuple[Mapping[str, object], ...]
    ] = {}
    payloads: list[bytes] = []
    payload_hashes: list[str] = []
    failed: list[Mapping[str, object]] = []
    for index, (value, analysis_value, identity) in enumerate(
        zip(artifacts, analyses, expected_identities, strict=True)
    ):
        family, side, metric = identity
        record = _mapping(value, "D5-A2 coefficient record")
        analysis = _mapping(analysis_value, "D5-A2 fit analysis")
        expected_path = (
            f"{a2.D5_A2_COEFFICIENT_DIRECTORY}/{index:02d}-"
            f"{family}-{side}-{metric}.v1.f64le.bin"
        )
        artifact = _validate_coefficient_record(
            record,
            index=index,
            family=family,
            side=side,
            metric=metric,
            expected_path=expected_path,
        )
        data = _authorized_bytes(root, artifact, f"D5-A2 coefficient {index}")
        values = struct.unpack(f"<{D5_PATCHES_PER_PLANT}d", data)
        if any(not math.isfinite(number) or number < 0.0 for number in values):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"D5-A2 coefficient {index} contains invalid Float64 values."
            )
        if record.get("distribution_summary") != a2._distribution_summary(values):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"D5-A2 coefficient {index} distribution is invalid."
            )
        fits = _validate_fit_analysis(
            analysis,
            identity=identity,
            coefficients=values,
            achieved_levels=achieved_by_family[family],
        )
        for fit in fits:
            reasons = fit["promotion_reasons"]
            if reasons:
                failed.append(
                    {
                        "family": family,
                        "side": side,
                        "metric": metric,
                        "local_patch_index": fit["local_patch_index"],
                        "a2_source_failure_reasons": list(reasons),
                        "original_a2_gamma": fit["coefficient_gamma"],
                    }
                )
        values_by_identity[identity] = tuple(values)
        fits_by_identity[identity] = fits
        payloads.append(data)
        payload_hashes.append(str(artifact["sha256"]))
    _validate_combined_coefficients(
        root,
        manifest.get("combined_canonical_payload"),
        payloads=payloads,
        payload_hashes=payload_hashes,
    )
    _validate_a2_failed_set(upstream.a2.report, failed)
    return _CoefficientEvidence(
        manifest=manifest,
        manifest_sha256=str(manifest_record["sha256"]),
        values_by_identity=values_by_identity,
        fits_by_identity=fits_by_identity,
        all_failed_records=tuple(failed),
    )


def _a2_achieved_references(
    report: Mapping[str, object],
) -> Mapping[str, tuple[float, float]]:
    records = report.get("authenticated_jobs")
    if not isinstance(records, list):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-A2 authenticated job evidence is missing."
        )
    values: dict[str, tuple[float, float]] = {}
    for family in D5_QUALITY_ORDER:
        family_records = tuple(
            record
            for record in records
            if isinstance(record, Mapping) and record.get("family") == family
        )
        if (
            len(family_records) != 2
            or tuple(
                record.get("requested_reference_level_umol_m2_s")
                for record in family_records
            )
            != D5_C2_LEVEL_ORDER
        ):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"D5-A2 achieved-reference order changed for {family}."
            )
        achieved = tuple(
            _positive_finite(
                record.get("achieved_reference_R_umol_m2_s"),
                f"D5-A2 {family} achieved reference",
            )
            for record in family_records
        )
        values[family] = (achieved[0], achieved[1])
    return values


def _validate_coefficient_record(
    record: Mapping[str, object],
    *,
    index: int,
    family: str,
    side: str,
    metric: str,
    expected_path: str,
) -> Mapping[str, object]:
    artifact = _mapping(record.get("artifact"), "D5-A2 coefficient artifact")
    if (
        set(record) != _COEFFICIENT_RECORD_FIELDS
        or set(artifact) != _BINARY_ARTIFACT_FIELDS
        or record.get("schema_id") != a2.D5_A2_COEFFICIENT_SCHEMA_ID
        or record.get("schema_version") != a2.D5_A2_COEFFICIENT_SCHEMA_VERSION
        or record.get("order_index") != index
        or record.get("family") != family
        or record.get("side") != side
        or record.get("metric") != metric
        or artifact.get("role") != "local_patch_surface_flux_coefficients"
        or artifact.get("path") != expected_path
        or artifact.get("media_type") != "application/octet-stream"
        or artifact.get("byte_length") != a2.COEFFICIENT_ARRAY_BYTES
        or artifact.get("schema") != "headerless fixed-stride Float64 array"
        or artifact.get("component_type") != a2.COMPONENT_TYPE
        or artifact.get("byte_order") != a2.BYTE_ORDER
        or artifact.get("stride_bytes") != a2.FLOAT64_STRIDE_BYTES
        or artifact.get("value_count") != D5_PATCHES_PER_PLANT
        or artifact.get("shape") != [D5_PATCHES_PER_PLANT]
        or artifact.get("ordering") != "canonical local_patch_index 0..191"
        or artifact.get("units") != a2.COEFFICIENT_UNITS
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"D5-A2 coefficient record {index} was reordered or substituted."
        )
    return artifact


def _validate_fit_analysis(
    analysis: Mapping[str, object],
    *,
    identity: tuple[str, str, str],
    coefficients: Sequence[float],
    achieved_levels: Sequence[float],
) -> tuple[Mapping[str, object], ...]:
    family, side, metric = identity
    fits_value = analysis.get("local_patch_fits")
    if (
        set(analysis)
        != {"family", "side", "metric", "distribution_summary", "local_patch_fits"}
        or analysis.get("family") != family
        or analysis.get("side") != side
        or analysis.get("metric") != metric
        or analysis.get("distribution_summary")
        != a2._distribution_summary(coefficients)
        or not isinstance(fits_value, list)
        or len(fits_value) != D5_PATCHES_PER_PLANT
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"D5-A2 fit-analysis identity changed: {identity}."
        )
    fits: list[Mapping[str, object]] = []
    for local_patch_index, (fit_value, coefficient) in enumerate(
        zip(fits_value, coefficients, strict=True)
    ):
        fit = _mapping(fit_value, "D5-A2 local-patch fit")
        _validate_fit_numbers(
            fit,
            local_patch_index=local_patch_index,
            coefficient=coefficient,
            achieved_levels=achieved_levels,
        )
        fits.append(fit)
    return tuple(fits)


def _validate_fit_numbers(
    fit: Mapping[str, object],
    *,
    local_patch_index: int,
    coefficient: float,
    achieved_levels: Sequence[float],
) -> None:
    reasons = fit.get("promotion_reasons")
    levels_value = fit.get("levels")
    if (
        set(fit) != _FIT_FIELDS
        or fit.get("local_patch_index") != local_patch_index
        or fit.get("coefficient_gamma") != coefficient
        or fit.get("coefficient_units") != a2.COEFFICIENT_UNITS
        or fit.get("through_origin_equation")
        != "gamma = sum_l(R_l * mean_q_l) / sum_l(R_l^2)"
        or fit.get("r_squared_definition")
        != (
            "1 - sum((mean_q_l - gamma*R_l)^2) / "
            "sum((mean_q_l - mean(mean_q))^2)"
        )
        or fit.get("relative_ratio_drift_definition")
        != (
            "(max(mean_q_l/R_l) - min(mean_q_l/R_l)) / "
            "mean(mean_q_l/R_l); exact zero mean maps to exact zero drift"
        )
        or not isinstance(reasons, list)
        or any(reason not in _A2_FAILURE_REASONS for reason in reasons)
        or len(set(reasons)) != len(reasons)
        or fit.get("promotion_eligible") is not (not reasons)
        or not isinstance(levels_value, list)
        or len(levels_value) != 2
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"D5-A2 local-patch fit contract changed at {local_patch_index}."
        )
    means: list[float] = []
    ratios: list[float] = []
    predictions: list[float] = []
    residuals: list[float] = []
    for requested, achieved, level_value in zip(
        D5_C2_LEVEL_ORDER, achieved_levels, levels_value, strict=True
    ):
        level = _mapping(level_value, "D5-A2 fit level")
        mean = _finite_nonnegative(
            level.get("mean_q_over_64_plants_umol_m2_s"), "D5-A2 fit mean"
        )
        ratio = mean / achieved
        prediction = coefficient * achieved
        residual = mean - prediction
        if (
            set(level) != _LEVEL_FIELDS
            or level.get("requested_reference_level_umol_m2_s") != requested
            or level.get("achieved_reference_R_umol_m2_s") != achieved
            or level.get("response_ratio_mean_q_over_R") != ratio
            or level.get("predicted_mean_q_umol_m2_s") != prediction
            or level.get("signed_residual_umol_m2_s") != residual
        ):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"D5-A2 fit arithmetic changed at {local_patch_index}."
            )
        means.append(mean)
        ratios.append(ratio)
        predictions.append(prediction)
        residuals.append(residual)
    ratio_mean = math.fsum(ratios) / 2
    drift = 0.0 if ratio_mean == 0.0 else (max(ratios) - min(ratios)) / ratio_mean
    mean_of_means = math.fsum(means) / 2
    if (
        coefficient != _through_origin_slope(achieved_levels, means)
        or fit.get("r_squared") != _r_squared(means, predictions)
        or fit.get("relative_ratio_drift") != drift
        or fit.get("residual_sum_squares")
        != math.fsum(value * value for value in residuals)
        or fit.get("total_sum_squares_centered")
        != math.fsum((value - mean_of_means) ** 2 for value in means)
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"D5-A2 fit evidence is internally inconsistent at {local_patch_index}."
        )


def _validate_combined_coefficients(
    root: Path,
    value: object,
    *,
    payloads: Sequence[bytes],
    payload_hashes: Sequence[str],
) -> None:
    record = _mapping(value, "D5-A2 combined coefficient payload")
    artifact = _mapping(record.get("artifact"), "D5-A2 combined coefficient artifact")
    expected_path = (
        f"{a2.D5_A2_COEFFICIENT_DIRECTORY}/"
        f"{a2.D5_A2_COMBINED_COEFFICIENT_NAME}"
    )
    if (
        set(record)
        != {"schema_id", "schema_version", "artifact", "constituent_sha256_order"}
        or record.get("schema_id") != a2.D5_A2_COEFFICIENT_SCHEMA_ID
        or record.get("schema_version") != a2.D5_A2_COEFFICIENT_SCHEMA_VERSION
        or record.get("constituent_sha256_order") != list(payload_hashes)
        or set(artifact) != _BINARY_ARTIFACT_FIELDS
        or artifact.get("path") != expected_path
        or artifact.get("role")
        != "combined_canonical_local_patch_surface_flux_coefficients"
        or artifact.get("media_type") != "application/octet-stream"
        or artifact.get("byte_length") != a2.COMBINED_COEFFICIENT_BYTES
        or artifact.get("schema") != "headerless fixed-stride Float64 array"
        or artifact.get("component_type") != a2.COMPONENT_TYPE
        or artifact.get("byte_order") != a2.BYTE_ORDER
        or artifact.get("stride_bytes") != a2.FLOAT64_STRIDE_BYTES
        or artifact.get("value_count") != a2.COEFFICIENT_VALUE_COUNT
        or artifact.get("shape")
        != [len(D5_QUALITY_ORDER), len(a2.COEFFICIENT_CHANNEL_ORDER), D5_PATCHES_PER_PLANT]
        or artifact.get("ordering")
        != (
            "family-major Standard, Quality, Rigorous; within family front "
            "incident, front absorbed, back incident, back absorbed; within "
            "array canonical local_patch_index 0..191"
        )
        or artifact.get("units") != a2.COEFFICIENT_UNITS
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-A2 combined coefficient authority changed."
        )
    combined = _authorized_bytes(root, artifact, "D5-A2 combined coefficients")
    if combined != b"".join(payloads):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-A2 combined coefficients do not match the individual arrays."
        )


def _validate_a2_failed_set(
    report: Mapping[str, object], failed: Sequence[Mapping[str, object]]
) -> None:
    summary = _mapping(report.get("promotion_reasons"), "D5-A2 failure summary")
    affected = _mapping(summary.get("affected_local_patches"), "D5-A2 affected set")
    reason_counts = _mapping(summary.get("reason_counts"), "D5-A2 reason counts")
    reconstructed_affected = {reason: [] for reason in _A2_FAILURE_REASONS}
    reconstructed_counts = {reason: 0 for reason in _A2_FAILURE_REASONS}
    for record in failed:
        identity = {
            "family": record["family"],
            "side": record["side"],
            "metric": record["metric"],
            "local_patch_index": record["local_patch_index"],
        }
        for reason in record["a2_source_failure_reasons"]:
            reconstructed_counts[str(reason)] += 1
            reconstructed_affected[str(reason)].append(identity)
    if (
        set(summary) != {"failed_patch_fit_count", "reason_counts", "affected_local_patches"}
        or summary.get("failed_patch_fit_count") != len(failed)
        or dict(reason_counts) != reconstructed_counts
        or dict(affected) != reconstructed_affected
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-A2 failed-cell sets do not reproduce from authenticated fits."
        )


def _authorized_bytes(
    root: Path, record: Mapping[str, object], label: str
) -> bytes:
    relative = record.get("path")
    if not isinstance(relative, str):
        raise SurfaceFluxRepeatabilityAnalysisError(f"{label} path is invalid.")
    posix = PurePosixPath(relative)
    if posix.is_absolute() or any(part in {"", ".", ".."} for part in posix.parts):
        raise SurfaceFluxRepeatabilityAnalysisError(f"{label} path is unsafe.")
    path = root.joinpath(*posix.parts)
    expected_size = record.get("byte_length")
    expected_hash = record.get("sha256")
    if (
        isinstance(expected_size, bool)
        or not isinstance(expected_size, int)
        or expected_size < 0
        or not isinstance(expected_hash, str)
        or len(expected_hash) != 64
        or any(character not in "0123456789abcdef" for character in expected_hash)
        or not path.is_file()
        or path.is_symlink()
        or not path.resolve(strict=True).is_relative_to(root.resolve(strict=True))
        or path.stat().st_size != expected_size
        or _sha256_file(path) != expected_hash
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(f"{label} failed authentication.")
    return path.read_bytes()


def _source_authorities(
    upstream: c1._AuthenticatedInputs,
    authenticated_c1: _AuthenticatedC1,
    coefficients: _CoefficientEvidence,
) -> dict[str, object]:
    c1_jobs = []
    for source in upstream.source_jobs:
        root = authenticated_c1.root / c1.D5_C1_JOBS_DIRECTORY / source.job.job_id
        c1_jobs.append(
            {
                "job_id": source.job.job_id,
                "job_result": _file_authority(
                    authenticated_c1.root, root / c1.D5_C1_JOB_RESULT_NAME
                ),
                "job_completion": _file_authority(
                    authenticated_c1.root, root / c1.D5_C1_JOB_COMPLETION_NAME
                ),
            }
        )
    return {
        "d5_a1": {
            "experiment_id": D5_EXPERIMENT_ID,
            "input_directory": str(upstream.a1.input_root),
            "completion": _file_authority(
                upstream.a1.input_root,
                upstream.a1.input_root / D5_COMPLETION_NAME,
            ),
            "configuration_sha256": upstream.a1.completion["configuration_sha256"],
            "status": "complete",
        },
        "d5_a2": {
            "analysis_id": a2.D5_A2_ANALYSIS_ID,
            "input_directory": str(upstream.a2.root),
            "report": _file_authority(
                upstream.a2.root, upstream.a2.root / a2.D5_A2_ANALYSIS_NAME
            ),
            "completion": _file_authority(
                upstream.a2.root, upstream.a2.root / a2.D5_A2_COMPLETION_NAME
            ),
            "coefficient_manifest": _file_authority(
                upstream.a2.root,
                upstream.a2.root / a2.D5_A2_COEFFICIENT_MANIFEST_NAME,
            ),
            "coefficient_manifest_sha256": coefficients.manifest_sha256,
            "status": "complete",
        },
        "d5_c1": {
            "experiment_id": c1.D5_C1_EXPERIMENT_ID,
            "repetition_id": c1.D5_C1_REPETITION_ID,
            "input_directory": str(authenticated_c1.root),
            "configuration": _file_authority(
                authenticated_c1.root,
                authenticated_c1.root / c1.D5_C1_CONFIGURATION_NAME,
            ),
            "outcome": _file_authority(
                authenticated_c1.root,
                authenticated_c1.root / c1.D5_C1_OUTCOME_NAME,
            ),
            "ordered_jobs": c1_jobs,
            "status": "complete",
            "stage_a_trace_count": 0,
            "stage_b_trace_count": 16,
            "nonidentical_repeated_artifact_count": 16,
            "independently_seeded_sample_claimed": False,
        },
    }


def _derive_observations(
    authenticated: _AuthenticatedSources,
) -> tuple[_Observation, ...]:
    observations: list[_Observation] = []
    results_by_job = {
        str(result["job"]["job_id"]): result
        for result in authenticated.c1.job_results
    }
    for source in authenticated.upstream.source_jobs:
        original = _derive_observation(
            root=source.a1_job_root,
            scene=authenticated.upstream.a1.scene,
            band_records=source.bands,
            family=source.job.quality,
            requested_level=source.job.requested_level,
            replicate="original_a1",
            achieved_reference=source.achieved_reference,
        )
        repeated_root = (
            authenticated.c1.root / c1.D5_C1_JOBS_DIRECTORY / source.job.job_id
        )
        repeated_records = _repeated_band_records(
            source,
            results_by_job[source.job.job_id],
            repeated_root,
        )
        repeated = _derive_observation(
            root=repeated_root,
            scene=authenticated.upstream.a1.scene,
            band_records=repeated_records,
            family=source.job.quality,
            requested_level=source.job.requested_level,
            replicate="repeated_c1",
            achieved_reference=source.achieved_reference,
        )
        observations.extend((original, repeated))
    expected = tuple(
        (family, level, replicate)
        for family in D5_C2_FAMILY_ORDER
        for level in D5_C2_LEVEL_ORDER
        for replicate in D5_C2_REPLICATE_ORDER
    )
    actual = tuple(
        (value.family, value.requested_level, value.replicate)
        for value in observations
    )
    if actual != expected:
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-C2 observation order is incomplete or changed."
        )
    return tuple(observations)


def _repeated_band_records(
    source: c1._SourceJob,
    result: Mapping[str, object],
    repeated_root: Path,
) -> tuple[Mapping[str, object], ...]:
    traces = result.get("ordered_traces")
    if not isinstance(traces, list) or len(traces) != len(D5_BAND_ORDER):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"D5-C1 trace matrix is incomplete for {source.job.job_id}."
        )
    records: list[Mapping[str, object]] = []
    for index, (band_id, source_band, trace_value) in enumerate(
        zip(D5_BAND_ORDER, source.bands, traces, strict=True)
    ):
        trace = _mapping(trace_value, "D5-C1 trace")
        repeated = _mapping(trace.get("repeated"), "D5-C1 repeated record")
        receiver = _mapping(repeated.get("receiver_values"), "D5-C1 receiver")
        workspace = f"{c1.D5_C1_TRACES_DIRECTORY}/{index:02d}-{band_id}"
        material_path = repeated_root / workspace / "leaf-material.rad"
        if (
            trace.get("job_id") != source.job.job_id
            or trace.get("band_order_index") != index
            or trace.get("band_id") != band_id
            or repeated.get("workspace") != workspace
            or receiver.get("path")
            != f"{workspace}/receiver-values.v1.f64le.bin"
            or receiver.get("row_count") != D5_EXPECTED_RECEIVER_COUNT_PER_BAND
            or receiver.get("byte_length") != D5_EXPECTED_RECEIVER_BYTES_PER_BAND
            or not material_path.is_file()
            or material_path.is_symlink()
            or _sha256_file(material_path) != source_band.get("material_sha256")
        ):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"D5-C1 original/repeat pairing changed: {source.job.job_id}/{band_id}"
            )
        records.append(
            {
                **dict(source_band),
                "receiver_values": dict(receiver),
                "material_artifact": {
                    "path": f"{workspace}/leaf-material.rad",
                    "byte_length": material_path.stat().st_size,
                    "sha256": source_band["material_sha256"],
                },
            }
        )
    return tuple(records)


def _derive_observation(
    *,
    root: Path,
    scene: object,
    band_records: Sequence[Mapping[str, object]],
    family: str,
    requested_level: float,
    replicate: str,
    achieved_reference: float,
) -> _Observation:
    values: dict[str, list[float]] = {
        channel: [] for _side, _metric, channel in D5_C2_CHANNEL_ORDER
    }
    expected_global_patch = 0

    def collect(patch: ParPatchSurfaceLight) -> None:
        nonlocal expected_global_patch
        expected_plant, expected_local_patch = divmod(
            expected_global_patch, D5_PATCHES_PER_PLANT
        )
        if (
            patch.global_patch_index != expected_global_patch
            or patch.plant_index != expected_plant
            or patch.local_patch_index != expected_local_patch
        ):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"Stage C changed canonical patch order for {family}-{requested_level}."
            )
        observed = {
            "front_incident": patch.front_incident_photon_flux_density_umol_m2_s,
            "front_absorbed": patch.front_absorbed_photon_flux_density_umol_m2_s,
            "back_incident": patch.back_incident_photon_flux_density_umol_m2_s,
            "back_absorbed": patch.back_absorbed_photon_flux_density_umol_m2_s,
        }
        for channel, number in observed.items():
            value = float(number)
            if not math.isfinite(value) or value < 0.0:
                raise SurfaceFluxRepeatabilityAnalysisError(
                    f"Stage C returned invalid {channel} data."
                )
            values[channel].append(value)
        expected_global_patch += 1

    try:
        validation = stream_juvenile_par_surface_light(
            root=root,
            scene=scene,
            band_records=band_records,
            patch_sink=collect,
        )
    except SurfaceFluxRepeatabilityAnalysisError:
        raise
    except (OSError, TypeError, ValueError, FspmScientificAggregationError) as exc:
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"authoritative Stage C reconstruction failed for "
            f"{family}-{requested_level}/{replicate}: {exc}"
        ) from exc
    expected_count = D5_EXPECTED_PLANT_COUNT * D5_PATCHES_PER_PLANT
    if expected_global_patch != expected_count or any(
        len(channel_values) != expected_count for channel_values in values.values()
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"Stage C patch count is incomplete for {family}-{requested_level}."
        )
    return _Observation(
        family=family,
        requested_level=requested_level,
        replicate=replicate,
        achieved_reference=achieved_reference,
        values_by_channel={
            name: tuple(channel_values) for name, channel_values in values.items()
        },
        stage_c_validation=validation.to_dict(),
    )


def _authenticated_endpoint_jobs(
    authenticated: _AuthenticatedSources,
) -> list[dict[str, object]]:
    results = {
        str(result["job"]["job_id"]): result
        for result in authenticated.c1.job_results
    }
    records = []
    for source in authenticated.upstream.source_jobs:
        result = results[source.job.job_id]
        traces = result["ordered_traces"]
        records.append(
            {
                "job_id": source.job.job_id,
                "family": source.job.quality,
                "requested_reference_level_umol_m2_s": source.job.requested_level,
                "achieved_reference_R_umol_m2_s": source.achieved_reference,
                "resolved_total_source_radiance_amplitude": (
                    source.total_source_amplitude
                ),
                "band_source_amplitude": source.band_source_amplitude,
                "amplitude_provenance": (
                    "authenticated D5-A1 Stage A final authority reused by D5-C1"
                ),
                "quality_option_identity": _quality_option_identity(
                    source.job.quality
                ),
                "stage_a_trace_count": 0,
                "stage_b_repetition_count": len(D5_BAND_ORDER),
                "ordered_original_repeat_pairings": [
                    {
                        "band_order_index": index,
                        "band_id": band_id,
                        "original_receiver_values": dict(
                            trace["original"]["receiver_values"]
                        ),
                        "repeated_receiver_values": dict(
                            trace["repeated"]["receiver_values"]
                        ),
                        "byte_identical": False,
                        "independence_interpretation": trace[
                            "independence_interpretation"
                        ],
                    }
                    for index, (band_id, trace) in enumerate(
                        zip(D5_BAND_ORDER, traces, strict=True)
                    )
                ],
            }
        )
    return records


def _analysis_report(
    authenticated: _AuthenticatedSources,
    observations: Sequence[_Observation],
) -> dict[str, object]:
    by_observation = {
        (value.family, value.requested_level, value.replicate): value
        for value in observations
    }
    cells: list[dict[str, object]] = []
    for family in D5_C2_FAMILY_ORDER:
        for side, metric, channel in D5_C2_CHANNEL_ORDER:
            identity = (family, side, metric)
            fits = authenticated.coefficients.fits_by_identity[identity]
            coefficient_values = authenticated.coefficients.values_by_identity[identity]
            for local_patch_index in range(D5_PATCHES_PER_PLANT):
                cells.append(
                    _analyze_cell(
                        family=family,
                        side=side,
                        metric=metric,
                        channel=channel,
                        local_patch_index=local_patch_index,
                        fit=fits[local_patch_index],
                        coefficient=coefficient_values[local_patch_index],
                        by_observation=by_observation,
                    )
                )
    expected_cell_count = (
        len(D5_C2_FAMILY_ORDER)
        * len(D5_C2_SIDE_ORDER)
        * len(D5_C2_METRIC_ORDER)
        * D5_PATCHES_PER_PLANT
    )
    if len(cells) != expected_cell_count:
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-C2 detailed cell matrix is incomplete."
        )
    return {
        "schema_id": D5_C2_SCHEMA_ID,
        "schema_version": D5_C2_SCHEMA_VERSION,
        "analysis_id": D5_C2_ANALYSIS_ID,
        "status": "complete",
        "source_authorities": dict(authenticated.authorities),
        "input_authentication": {
            "status": "all_sources_authenticated_before_receiver_value_decode",
            "d5_a1_complete_authority_and_original_artifacts_validated": True,
            "d5_a2_report_completion_inventory_and_coefficients_validated": True,
            "d5_c1_configuration_outcome_jobs_and_traces_validated": True,
            "declared_byte_lengths_and_sha256_validated": True,
            "quality_option_identities_validated": True,
            "achieved_references_and_source_amplitudes_validated": True,
            "sampling_topology_receiver_count_and_order_validated": True,
            "original_to_repeat_pairing_validated": True,
            "stage_a_trace_count": 0,
            "stage_b_repetition_count": 16,
            "nonidentical_repeated_binary_count": 16,
            "nonidentical_binary_interpretation": (
                "nonidentical bytes do not prove an independently seeded sample"
            ),
            "input_directories_modified": False,
        },
        "fixed_identity": {
            "families": list(D5_C2_FAMILY_ORDER),
            "requested_levels_umol_m2_s": list(D5_C2_LEVEL_ORDER),
            "sides": list(D5_C2_SIDE_ORDER),
            "metrics": list(D5_C2_METRIC_ORDER),
            "local_patch_indices": {"first": 0, "last": 191, "count": 192},
            "plant_indices": {"first": 0, "last": 63, "count": 64},
            "replicates": list(D5_C2_REPLICATE_ORDER),
            "band_order": list(D5_BAND_ORDER),
            "sampling_profile_id": D5_SAMPLING_PROFILE_ID,
            "topology_sha256": D5_TOPOLOGY_SHA256,
            "receivers_sha256": D5_RECEIVERS_SHA256,
            "receiver_count_per_band": D5_EXPECTED_RECEIVER_COUNT_PER_BAND,
            "receiver_byte_length_per_band": D5_EXPECTED_RECEIVER_BYTES_PER_BAND,
            "canonical_receiver_order": c1.D5_C1_CANONICAL_RECEIVER_ORDER,
            "quality_option_identities": [
                _quality_option_identity(family) for family in D5_C2_FAMILY_ORDER
            ],
        },
        "authenticated_endpoint_jobs": _authenticated_endpoint_jobs(authenticated),
        "scope": {
            "analysis_type": "descriptive endpoint repeatability evidence",
            "analyzed_families": list(D5_C2_FAMILY_ORDER),
            "authoritative_kernel": (
                "fspm_optics.application.fspm_science."
                "stream_juvenile_par_surface_light"
            ),
            "absorption_reimplemented": False,
            "raw_stage_c_science_changed": False,
            "rigorous_raw_transport_changed": False,
            "direct_raw_transport_changed": False,
            "proposed_and_conventional_remain_holdouts": True,
            "new_leaf_region_classifications_created": False,
        },
        "numerical_methods": {
            "component_type": "IEEE-754 binary64",
            "reduction": "math.fsum followed by explicit division",
            "plant_mean_count": D5_EXPECTED_PLANT_COUNT,
            "replicate_count_per_level": 2,
            "pooled_within_level_degrees_of_freedom": 2,
            "undefined_values_serialized_as": None,
        },
        "equations": {
            "normalized_ratio": "z[level,replicate] = mean_q_over_64_plants / achieved_R",
            "symmetric_relative_repeat_difference": (
                "abs(z1-z0) / mean(abs(z0),abs(z1)); exact zero denominator is undefined"
            ),
            "signed_endpoint_direction": "z[500,replicate] - z[250,replicate]",
            "pooled_within_level_variance": (
                "sum_level,sum_replicate((z-level_mean_z)^2) / 2"
            ),
            "descriptive_standardized_level_effect": (
                "(mean_z_500-mean_z_250) / sqrt(pooled_within_level_variance); "
                "exact zero variance is undefined"
            ),
            "repeated_through_origin_gamma": "sum_level(R*y_repeat) / sum_level(R^2)",
            "pooled_through_origin_gamma": (
                "sum_level,replicate(R*y) / sum_level,replicate(R^2)"
            ),
            "equal_observation_normalized_ratio_mean": (
                "mean of the four z[level,replicate] observations"
            ),
        },
        "stage_c_reconstructions": [
            {
                "family": value.family,
                "requested_reference_level_umol_m2_s": value.requested_level,
                "replicate": value.replicate,
                "achieved_reference_R_umol_m2_s": value.achieved_reference,
                "validation": dict(value.stage_c_validation),
            }
            for value in observations
        ],
        "aggregate_summaries": _aggregate_summaries(cells),
        "a2_failed_cell_reproduction": _failed_cell_evidence(
            cells, authenticated.coefficients.all_failed_records
        ),
        "detailed_cells": cells,
        "interpretation_limits": {
            "two_replicates_are_not_an_inferential_test": True,
            "standardized_effect_is_descriptive_only": True,
            "incident_and_absorbed_failures_are_not_independent_replication": True,
            "nonidentical_c1_bytes_do_not_establish_independent_random_seeding": True,
            "evidence_question": (
                "whether endpoint drift is dominated by within-level repeat "
                "variation or persists as level-dependent behavior"
            ),
        },
        "explicit_non_claims": {
            "calibration_promotion_decision": False,
            "production_calibration_suitability_decision": False,
            "independent_random_seeding": False,
            "new_acceptance_thresholds": False,
            "availability_masks": False,
            "palette_anchors": False,
            "clipping_policy": False,
            "production_resource_generated": False,
            "radiance_invoked": False,
        },
    }


def _analyze_cell(
    *,
    family: str,
    side: str,
    metric: str,
    channel: str,
    local_patch_index: int,
    fit: Mapping[str, object],
    coefficient: float,
    by_observation: Mapping[tuple[str, float, str], _Observation],
) -> dict[str, object]:
    levels: list[dict[str, object]] = []
    for level_index, requested_level in enumerate(D5_C2_LEVEL_ORDER):
        original = by_observation[(family, requested_level, "original_a1")]
        repeated = by_observation[(family, requested_level, "repeated_c1")]
        level = _level_repeatability(
            original=original,
            repeated=repeated,
            channel=channel,
            local_patch_index=local_patch_index,
        )
        source_level = _mapping(fit["levels"][level_index], "D5-A2 source fit level")
        if (
            level["achieved_reference_R_umol_m2_s"]
            != source_level.get("achieved_reference_R_umol_m2_s")
            or level["mean_q_over_64_plants_umol_m2_s"]["original_a1"]
            != source_level.get("mean_q_over_64_plants_umol_m2_s")
            or level["normalized_ratio_z"]["original_a1"]
            != source_level.get("response_ratio_mean_q_over_R")
        ):
            raise SurfaceFluxRepeatabilityAnalysisError(
                f"D5-A1 reconstruction disagrees with A2 at "
                f"{family}/{side}/{metric}/{local_patch_index}/{requested_level}."
            )
        levels.append(level)

    original_z = tuple(
        float(level["normalized_ratio_z"]["original_a1"]) for level in levels
    )
    repeated_z = tuple(
        float(level["normalized_ratio_z"]["repeated_c1"]) for level in levels
    )
    original_y = tuple(
        float(level["mean_q_over_64_plants_umol_m2_s"]["original_a1"])
        for level in levels
    )
    repeated_y = tuple(
        float(level["mean_q_over_64_plants_umol_m2_s"]["repeated_c1"])
        for level in levels
    )
    achieved = tuple(
        float(level["achieved_reference_R_umol_m2_s"]) for level in levels
    )
    original_signed = original_z[1] - original_z[0]
    repeated_signed = repeated_z[1] - repeated_z[0]
    original_drift = _endpoint_relative_drift(original_z)
    repeated_drift = _endpoint_relative_drift(repeated_z)
    if (
        fit.get("coefficient_gamma") != coefficient
        or fit.get("relative_ratio_drift") != original_drift
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"D5-A2 coefficient/drift link changed at "
            f"{family}/{side}/{metric}/{local_patch_index}."
        )
    sign_relation = _sign_relation(original_signed, repeated_signed)
    level_means = tuple(
        math.fsum((original_z[index], repeated_z[index])) / 2
        for index in range(2)
    )
    signed_level_mean_difference = level_means[1] - level_means[0]
    pooled_variance = _pooled_within_level_variance(original_z, repeated_z)
    standardized = _descriptive_standardized_effect(
        signed_level_mean_difference, pooled_variance
    )
    repeated_gamma = _through_origin_slope(achieved, repeated_y)
    pooled_gamma = _through_origin_slope(
        (achieved[0], achieved[0], achieved[1], achieved[1]),
        (original_y[0], repeated_y[0], original_y[1], repeated_y[1]),
    )
    equal_ratio_mean = math.fsum(
        (original_z[0], repeated_z[0], original_z[1], repeated_z[1])
    ) / 4
    estimates = {
        "original_a2_gamma": coefficient,
        "repeated_only_through_origin_gamma": repeated_gamma,
        "pooled_four_observation_through_origin_gamma": pooled_gamma,
        "equal_observation_normalized_ratio_mean": equal_ratio_mean,
    }
    repeat_differences = tuple(
        abs(repeated_z[index] - original_z[index]) for index in range(2)
    )
    maximum_repeat_difference = max(repeat_differences)
    relative_repeat_differences = tuple(
        level["repeat_difference"]["symmetric_relative"]["value"]
        for level in levels
    )
    defined_relative_repeat_differences = tuple(
        float(value) for value in relative_repeat_differences if value is not None
    )
    maximum_relative_repeat_difference = (
        max(defined_relative_repeat_differences)
        if defined_relative_repeat_differences
        else None
    )
    original_endpoint_magnitude = abs(original_signed)
    return {
        "family": family,
        "side": side,
        "metric": metric,
        "local_patch_index": local_patch_index,
        "levels": levels,
        "endpoint_drift": {
            "original_a2_relative_ratio_drift": float(fit["relative_ratio_drift"]),
            "original_recomputed_relative_ratio_drift": original_drift,
            "repeated_relative_ratio_drift": repeated_drift,
            "original_signed_z500_minus_z250": original_signed,
            "repeated_signed_z500_minus_z250": repeated_signed,
            "original_direction": _direction(original_signed),
            "repeated_direction": _direction(repeated_signed),
            "sign_relation": sign_relation,
            "same_nonzero_sign": sign_relation == "same_nonzero_sign",
            "reversed_nonzero_sign": sign_relation == "reversed_nonzero_sign",
        },
        "two_replicate_level_summary": {
            "mean_normalized_ratio_z_250": level_means[0],
            "mean_normalized_ratio_z_500": level_means[1],
            "signed_level_mean_difference_z500_minus_z250": (
                signed_level_mean_difference
            ),
            "pooled_within_level_variance": pooled_variance,
            "pooled_within_level_degrees_of_freedom": 2,
            "descriptive_standardized_level_effect": standardized,
            "inferential_interpretation": False,
        },
        "coefficient_estimates": {
            **estimates,
            "pairwise_symmetric_relative_differences": (
                _pairwise_relative_differences(estimates)
            ),
        },
        "endpoint_drift_vs_within_level_repeat_difference": {
            "absolute_original_endpoint_difference": original_endpoint_magnitude,
            "absolute_repeat_difference_by_level": {
                "250": repeat_differences[0],
                "500": repeat_differences[1],
            },
            "maximum_absolute_within_level_repeat_difference": (
                maximum_repeat_difference
            ),
            "original_endpoint_not_larger_than_maximum_repeat_difference": (
                original_endpoint_magnitude <= maximum_repeat_difference
            ),
            "endpoint_to_maximum_repeat_scale_ratio": _nonnegative_ratio(
                original_endpoint_magnitude, maximum_repeat_difference
            ),
            "symmetric_relative_repeat_difference_by_level": {
                "250": relative_repeat_differences[0],
                "500": relative_repeat_differences[1],
            },
            "maximum_defined_symmetric_relative_repeat_difference": (
                maximum_relative_repeat_difference
            ),
            "original_relative_endpoint_drift_not_larger_than_maximum_symmetric_relative_repeat_difference": (
                None
                if maximum_relative_repeat_difference is None
                else original_drift <= maximum_relative_repeat_difference
            ),
            "comparison_is_descriptive_not_an_acceptance_rule": True,
        },
        "a2_source_failed_cell": bool(fit["promotion_reasons"]),
        "a2_source_failure_reasons": list(fit["promotion_reasons"]),
    }


def _level_repeatability(
    *,
    original: _Observation,
    repeated: _Observation,
    channel: str,
    local_patch_index: int,
) -> dict[str, object]:
    if (
        original.family != repeated.family
        or original.requested_level != repeated.requested_level
        or original.achieved_reference != repeated.achieved_reference
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "original/repeat observation identities do not pair exactly."
        )
    original_values = tuple(
        original.values_by_channel[channel][
            plant_index * D5_PATCHES_PER_PLANT + local_patch_index
        ]
        for plant_index in range(D5_EXPECTED_PLANT_COUNT)
    )
    repeated_values = tuple(
        repeated.values_by_channel[channel][
            plant_index * D5_PATCHES_PER_PLANT + local_patch_index
        ]
        for plant_index in range(D5_EXPECTED_PLANT_COUNT)
    )
    original_mean = math.fsum(original_values) / D5_EXPECTED_PLANT_COUNT
    repeated_mean = math.fsum(repeated_values) / D5_EXPECTED_PLANT_COUNT
    reference = original.achieved_reference
    original_z = original_mean / reference
    repeated_z = repeated_mean / reference
    signed = repeated_z - original_z
    raw_differences = tuple(
        right - left
        for left, right in zip(original_values, repeated_values, strict=True)
    )
    normalized_differences = tuple(value / reference for value in raw_differences)
    return {
        "requested_reference_level_umol_m2_s": original.requested_level,
        "achieved_reference_R_umol_m2_s": reference,
        "mean_q_over_64_plants_umol_m2_s": {
            "original_a1": original_mean,
            "repeated_c1": repeated_mean,
            "value_counts": _value_counts((original_mean, repeated_mean)),
        },
        "normalized_ratio_z": {
            "original_a1": original_z,
            "repeated_c1": repeated_z,
            "value_counts": _value_counts((original_z, repeated_z)),
        },
        "repeat_difference": {
            "signed_repeated_minus_original_z": signed,
            "absolute_z": abs(signed),
            "symmetric_relative": _symmetric_relative_difference(
                original_z, repeated_z
            ),
            "value_counts": _value_counts((signed, abs(signed))),
        },
        "per_plant_original_repeat_difference_distribution": {
            "signed_q_repeated_minus_original_umol_m2_s": (
                _distribution_summary(raw_differences)
            ),
            "signed_normalized_ratio_repeated_minus_original": (
                _distribution_summary(normalized_differences)
            ),
        },
    }


def _pooled_within_level_variance(
    original_z: Sequence[float], repeated_z: Sequence[float]
) -> float:
    if len(original_z) != 2 or len(repeated_z) != 2:
        raise SurfaceFluxRepeatabilityAnalysisError(
            "pooled variance requires two levels and two replicates."
        )
    means = tuple(
        math.fsum((original_z[index], repeated_z[index])) / 2
        for index in range(2)
    )
    squared = tuple(
        (value - means[level_index]) ** 2
        for level_index in range(2)
        for value in (original_z[level_index], repeated_z[level_index])
    )
    return math.fsum(squared) / 2


def _descriptive_standardized_effect(
    signed_level_mean_difference: float, pooled_variance: float
) -> dict[str, object]:
    if pooled_variance < 0.0 or not math.isfinite(pooled_variance):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "pooled variance must be finite and nonnegative."
        )
    if pooled_variance == 0.0:
        return {
            "value": None,
            "status": "undefined_exact_zero_pooled_within_level_variance",
        }
    value = signed_level_mean_difference / math.sqrt(pooled_variance)
    return {"value": value, "status": "defined"}


def _symmetric_relative_difference(left: float, right: float) -> dict[str, object]:
    if not math.isfinite(left) or not math.isfinite(right):
        return {
            "value": None,
            "status": "undefined_nonfinite_input",
            "absolute_difference": None,
            "denominator_mean_absolute": None,
        }
    numerator = abs(right - left)
    denominator = math.fsum((abs(left), abs(right))) / 2
    if denominator == 0.0:
        return {
            "value": None,
            "status": "undefined_exact_zero_denominator",
            "absolute_difference": numerator,
            "denominator_mean_absolute": denominator,
        }
    return {
        "value": numerator / denominator,
        "status": "defined",
        "absolute_difference": numerator,
        "denominator_mean_absolute": denominator,
    }


def _nonnegative_ratio(numerator: float, denominator: float) -> dict[str, object]:
    if denominator == 0.0:
        return {
            "value": None,
            "status": "undefined_exact_zero_denominator",
        }
    return {"value": numerator / denominator, "status": "defined"}


def _endpoint_relative_drift(values: Sequence[float]) -> float:
    if len(values) != 2 or any(value < 0.0 or not math.isfinite(value) for value in values):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "endpoint drift requires two finite nonnegative ratios."
        )
    mean = math.fsum(values) / 2
    return 0.0 if mean == 0.0 else (max(values) - min(values)) / mean


def _direction(value: float) -> str:
    if value > 0.0:
        return "increasing"
    if value < 0.0:
        return "decreasing"
    return "exact_zero"


def _sign_relation(original: float, repeated: float) -> str:
    original_sign = (original > 0.0) - (original < 0.0)
    repeated_sign = (repeated > 0.0) - (repeated < 0.0)
    if original_sign == 0 and repeated_sign == 0:
        return "both_exact_zero"
    if original_sign == 0:
        return "original_zero_repeated_nonzero"
    if repeated_sign == 0:
        return "original_nonzero_repeated_zero"
    if original_sign == repeated_sign:
        return "same_nonzero_sign"
    return "reversed_nonzero_sign"


def _pairwise_relative_differences(
    estimates: Mapping[str, float],
) -> list[dict[str, object]]:
    items = tuple(estimates.items())
    return [
        {
            "left_estimate": left_name,
            "right_estimate": right_name,
            "symmetric_relative_difference": _symmetric_relative_difference(
                left, right
            ),
        }
        for index, (left_name, left) in enumerate(items)
        for right_name, right in items[index + 1 :]
    ]


def _aggregate_summaries(cells: Sequence[Mapping[str, object]]) -> dict[str, object]:
    strata = []
    for family in D5_C2_FAMILY_ORDER:
        for side in D5_C2_SIDE_ORDER:
            for metric in D5_C2_METRIC_ORDER:
                selected = tuple(
                    cell
                    for cell in cells
                    if cell["family"] == family
                    and cell["side"] == side
                    and cell["metric"] == metric
                )
                strata.append(_stratum_summary(family, side, metric, selected))
    return {
        "cell_count": len(cells),
        "level_record_count": len(cells) * 2,
        "per_plant_difference_count": len(cells) * 2 * D5_EXPECTED_PLANT_COUNT,
        "endpoint_sign_relation_counts": _category_counts(
            str(cell["endpoint_drift"]["sign_relation"]) for cell in cells
        ),
        "symmetric_relative_repeat_difference_distribution": (
            _distribution_summary(
                tuple(
                    level["repeat_difference"]["symmetric_relative"]["value"]
                    for cell in cells
                    for level in cell["levels"]
                )
            )
        ),
        "absolute_repeat_difference_z_distribution": _distribution_summary(
            tuple(
                level["repeat_difference"]["absolute_z"]
                for cell in cells
                for level in cell["levels"]
            )
        ),
        "original_endpoint_relative_drift_distribution": _distribution_summary(
            tuple(
                cell["endpoint_drift"]["original_recomputed_relative_ratio_drift"]
                for cell in cells
            )
        ),
        "repeated_endpoint_relative_drift_distribution": _distribution_summary(
            tuple(
                cell["endpoint_drift"]["repeated_relative_ratio_drift"]
                for cell in cells
            )
        ),
        "descriptive_standardized_level_effect_distribution": (
            _distribution_summary(
                tuple(
                    cell["two_replicate_level_summary"][
                        "descriptive_standardized_level_effect"
                    ]["value"]
                    for cell in cells
                )
            )
        ),
        "coefficient_estimate_relative_difference_summaries": (
            _coefficient_relative_difference_summaries(cells)
        ),
        "family_side_metric_strata": strata,
    }


def _coefficient_relative_difference_summaries(
    cells: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    if not cells:
        return []
    first = cells[0]["coefficient_estimates"][
        "pairwise_symmetric_relative_differences"
    ]
    summaries = []
    for pair_index, pair in enumerate(first):
        summaries.append(
            {
                "left_estimate": pair["left_estimate"],
                "right_estimate": pair["right_estimate"],
                "symmetric_relative_difference_distribution": (
                    _distribution_summary(
                        tuple(
                            cell["coefficient_estimates"][
                                "pairwise_symmetric_relative_differences"
                            ][pair_index]["symmetric_relative_difference"]["value"]
                            for cell in cells
                        )
                    )
                ),
            }
        )
    return summaries


def _stratum_summary(
    family: str,
    side: str,
    metric: str,
    cells: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    failed = tuple(cell for cell in cells if cell["a2_source_failed_cell"])
    return {
        "family": family,
        "side": side,
        "metric": metric,
        "cell_count": len(cells),
        "a2_source_failed_cell_count": len(failed),
        "endpoint_sign_relation_counts": _category_counts(
            str(cell["endpoint_drift"]["sign_relation"]) for cell in cells
        ),
        "failed_cell_sign_relation_counts": _category_counts(
            str(cell["endpoint_drift"]["sign_relation"]) for cell in failed
        ),
        "absolute_repeat_difference_z_distribution": _distribution_summary(
            tuple(
                level["repeat_difference"]["absolute_z"]
                for cell in cells
                for level in cell["levels"]
            )
        ),
        "original_endpoint_drift_distribution": _distribution_summary(
            tuple(
                cell["endpoint_drift"]["original_recomputed_relative_ratio_drift"]
                for cell in cells
            )
        ),
        "repeated_endpoint_drift_distribution": _distribution_summary(
            tuple(
                cell["endpoint_drift"]["repeated_relative_ratio_drift"]
                for cell in cells
            )
        ),
    }


def _failed_cell_evidence(
    cells: Sequence[Mapping[str, object]],
    all_a2_failed: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    scope_failed = tuple(cell for cell in cells if cell["a2_source_failed_cell"])
    sign_counts = _category_counts(
        str(cell["endpoint_drift"]["sign_relation"]) for cell in scope_failed
    )
    smaller = tuple(
        cell
        for cell in scope_failed
        if cell["endpoint_drift_vs_within_level_repeat_difference"][
            "original_relative_endpoint_drift_not_larger_than_maximum_symmetric_relative_repeat_difference"
        ]
        is True
    )
    return {
        "authenticated_a2_full_failed_set": {
            "exact_reproduction": True,
            "failed_cell_count": len(all_a2_failed),
            "includes_authenticated_out_of_c2_scope_rigorous_records": True,
            "records": [dict(record) for record in all_a2_failed],
        },
        "quality_standard_endpoint_scope_failed_set": {
            "failed_cell_count": len(scope_failed),
            "same_endpoint_drift_sign_count": sign_counts.get(
                "same_nonzero_sign", 0
            ),
            "reversed_endpoint_drift_sign_count": sign_counts.get(
                "reversed_nonzero_sign", 0
            ),
            "all_sign_relation_counts": sign_counts,
            "original_endpoint_not_larger_than_maximum_within_level_repeat_count": (
                len(smaller)
            ),
            "comparison_definition": (
                "A2 relative endpoint drift <= max_level(symmetric relative "
                "original/repeat difference)"
            ),
            "comparison_is_descriptive_and_has_no_tolerance_threshold": True,
            "distributions": {
                "by_family": _field_counts(scope_failed, "family"),
                "by_side": _field_counts(scope_failed, "side"),
                "by_metric": _field_counts(scope_failed, "metric"),
                "by_family_side_metric": [
                    {
                        "family": family,
                        "side": side,
                        "metric": metric,
                        "failed_cell_count": sum(
                            cell["family"] == family
                            and cell["side"] == side
                            and cell["metric"] == metric
                            for cell in scope_failed
                        ),
                    }
                    for family in D5_C2_FAMILY_ORDER
                    for side in D5_C2_SIDE_ORDER
                    for metric in D5_C2_METRIC_ORDER
                ],
                "original_a2_coefficient_magnitude": _distribution_summary(
                    tuple(
                        abs(
                            float(
                                cell["coefficient_estimates"]["original_a2_gamma"]
                            )
                        )
                        for cell in scope_failed
                    )
                ),
                "by_local_patch_index": [
                    {
                        "local_patch_index": index,
                        "failed_cell_count": sum(
                            cell["local_patch_index"] == index
                            for cell in scope_failed
                        ),
                    }
                    for index in range(D5_PATCHES_PER_PLANT)
                ],
            },
            "incident_absorbed_overlap": _incident_absorbed_overlap(scope_failed),
            "presentation_critical_quality_strata": (
                _quality_presentation_strata(scope_failed)
            ),
            "records": [
                {
                    "family": cell["family"],
                    "side": cell["side"],
                    "metric": cell["metric"],
                    "local_patch_index": cell["local_patch_index"],
                    "a2_source_failure_reasons": cell["a2_source_failure_reasons"],
                    "endpoint_sign_relation": cell["endpoint_drift"][
                        "sign_relation"
                    ],
                    "endpoint_not_larger_than_maximum_repeat_difference": cell[
                        "endpoint_drift_vs_within_level_repeat_difference"
                    ][
                        "original_relative_endpoint_drift_not_larger_than_maximum_symmetric_relative_repeat_difference"
                    ],
                }
                for cell in scope_failed
            ],
        },
    }


def _incident_absorbed_overlap(
    failed: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    records = []
    for family in D5_C2_FAMILY_ORDER:
        for side in D5_C2_SIDE_ORDER:
            incident = {
                int(cell["local_patch_index"])
                for cell in failed
                if cell["family"] == family
                and cell["side"] == side
                and cell["metric"] == "incident"
            }
            absorbed = {
                int(cell["local_patch_index"])
                for cell in failed
                if cell["family"] == family
                and cell["side"] == side
                and cell["metric"] == "absorbed"
            }
            records.append(
                {
                    "family": family,
                    "side": side,
                    "incident_failed_count": len(incident),
                    "absorbed_failed_count": len(absorbed),
                    "overlap_count": len(incident & absorbed),
                    "incident_only_count": len(incident - absorbed),
                    "absorbed_only_count": len(absorbed - incident),
                    "union_count": len(incident | absorbed),
                    "overlap_local_patch_indices": sorted(incident & absorbed),
                }
            )
    return {
        "strata": records,
        "independent_replication_interpretation": False,
        "reason": (
            "incident and absorbed values share receiver transport and material "
            "derivation and are reported as correlated channels"
        ),
    }


def _quality_presentation_strata(
    failed: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    records = []
    for side in D5_C2_SIDE_ORDER:
        selected = tuple(
            cell
            for cell in failed
            if cell["family"] == "quality" and cell["side"] == side
        )
        records.append(
            {
                "stratum": f"quality_{side}",
                "family": "quality",
                "side": side,
                "failed_cell_count": len(selected),
                "by_metric": _field_counts(selected, "metric"),
                "endpoint_sign_relation_counts": _category_counts(
                    str(cell["endpoint_drift"]["sign_relation"])
                    for cell in selected
                ),
                "original_endpoint_not_larger_than_maximum_repeat_count": sum(
                    bool(
                        cell["endpoint_drift_vs_within_level_repeat_difference"][
                            "original_relative_endpoint_drift_not_larger_than_maximum_symmetric_relative_repeat_difference"
                        ]
                    )
                    for cell in selected
                ),
                "local_patch_indices": sorted(
                    {int(cell["local_patch_index"]) for cell in selected}
                ),
            }
        )
    return records


def _distribution_summary(
    values: Sequence[float | None],
) -> dict[str, object]:
    values_tuple = tuple(values)
    defined = tuple(value for value in values_tuple if value is not None)
    finite = tuple(float(value) for value in defined if math.isfinite(float(value)))
    mean = math.fsum(finite) / len(finite) if finite else None
    sorted_values = tuple(sorted(finite))
    return {
        "count": len(values_tuple),
        "defined_count": len(defined),
        "undefined_count": len(values_tuple) - len(defined),
        "finite_count": len(finite),
        "nonfinite_count": len(defined) - len(finite),
        "nonnegative_count": sum(value >= 0.0 for value in finite),
        "negative_count": sum(value < 0.0 for value in finite),
        "exact_zero_count": sum(value == 0.0 for value in finite),
        "minimum": min(finite) if finite else None,
        "maximum": max(finite) if finite else None,
        "mean": mean,
        "mean_absolute": (
            math.fsum(abs(value) for value in finite) / len(finite)
            if finite
            else None
        ),
        "root_mean_square": (
            math.sqrt(math.fsum(value * value for value in finite) / len(finite))
            if finite
            else None
        ),
        "population_standard_deviation": (
            math.sqrt(
                math.fsum((value - mean) ** 2 for value in finite) / len(finite)
            )
            if finite and mean is not None
            else None
        ),
        "quantiles": {
            "p05": _linear_quantile(sorted_values, 0.05),
            "p25": _linear_quantile(sorted_values, 0.25),
            "p50": _linear_quantile(sorted_values, 0.50),
            "p75": _linear_quantile(sorted_values, 0.75),
            "p95": _linear_quantile(sorted_values, 0.95),
        },
        "quantile_definition": "sorted linear interpolation at index (count-1)*p",
    }


def _linear_quantile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    position = (len(values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return values[lower]
    fraction = position - lower
    return values[lower] + fraction * (values[upper] - values[lower])


def _value_counts(values: Sequence[float | None]) -> dict[str, int]:
    defined = tuple(value for value in values if value is not None)
    finite = tuple(float(value) for value in defined if math.isfinite(float(value)))
    return {
        "count": len(values),
        "finite_count": len(finite),
        "nonnegative_count": sum(value >= 0.0 for value in finite),
        "exact_zero_count": sum(value == 0.0 for value in finite),
        "undefined_count": len(values) - len(defined),
        "nonfinite_count": len(defined) - len(finite),
    }


def _category_counts(values: Iterable[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return counts


def _field_counts(
    records: Sequence[Mapping[str, object]], field: str
) -> dict[str, int]:
    return _category_counts(str(record[field]) for record in records)


def _through_origin_slope(x: Sequence[float], y: Sequence[float]) -> float:
    if len(x) != len(y):
        raise SurfaceFluxRepeatabilityAnalysisError(
            "through-origin vectors have unequal lengths."
        )
    denominator = math.fsum(value * value for value in x)
    if denominator <= 0.0:
        raise SurfaceFluxRepeatabilityAnalysisError(
            "through-origin regression is singular."
        )
    return math.fsum(
        left * right for left, right in zip(x, y, strict=True)
    ) / denominator


def _r_squared(observed: Sequence[float], predicted: Sequence[float]) -> float:
    mean = math.fsum(observed) / len(observed)
    residual = math.fsum(
        (actual - estimate) ** 2
        for actual, estimate in zip(observed, predicted, strict=True)
    )
    total = math.fsum((value - mean) ** 2 for value in observed)
    if total == 0.0:
        return 1.0 if residual == 0.0 else 0.0
    return 1.0 - residual / total


def _publish(
    output: Path,
    report: Mapping[str, object],
    source_authorities: Mapping[str, object],
) -> SurfaceFluxRepeatabilityAnalysisPublication:
    if output.exists() or output.is_symlink():
        raise SurfaceFluxRepeatabilityAnalysisError(
            "D5-C2 output directory must remain absent until atomic publication."
        )
    stage = Path(tempfile.mkdtemp(prefix=".d5-c2-repeatability-", dir=output.parent))
    try:
        report_path = stage / D5_C2_REPORT_NAME
        _write_fsynced(report_path, format_surface_flux_repeatability_json(report).encode())
        report_authority = _file_authority(stage, report_path)
        completion = {
            "schema_id": D5_C2_COMPLETION_SCHEMA_ID,
            "schema_version": D5_C2_SCHEMA_VERSION,
            "analysis_id": D5_C2_ANALYSIS_ID,
            "status": "complete",
            "report": report_authority,
            "source_authorities": dict(source_authorities),
            "ordered_artifact_inventory": [
                {**report_authority, "media_type": "application/json"}
            ],
            "atomic_publication": (
                "both JSON artifacts fsynced before same-filesystem directory rename"
            ),
            "input_directories_modified": False,
            "radiance_invoked": False,
            "production_calibration_resource_generated": False,
            "calibration_decision_made": False,
            "independent_random_seeding_claimed": False,
        }
        completion_path = stage / D5_C2_COMPLETION_NAME
        _write_fsynced(
            completion_path,
            format_surface_flux_repeatability_json(completion).encode(),
        )
        _fsync_directory(stage)
        if output.exists() or output.is_symlink():
            raise SurfaceFluxRepeatabilityAnalysisError(
                "D5-C2 output appeared during staged publication."
            )
        os.replace(stage, output)
        _fsync_directory(output.parent)
    except BaseException:
        if stage.exists():
            shutil.rmtree(stage)
        raise
    return SurfaceFluxRepeatabilityAnalysisPublication(
        output_directory=output,
        report_path=output / D5_C2_REPORT_NAME,
        completion_path=output / D5_C2_COMPLETION_NAME,
        report=report,
        completion=completion,
    )


def _write_fsynced(path: Path, data: bytes) -> None:
    with path.open("xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _file_authority(root: Path, path: Path) -> dict[str, object]:
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=True)
    if not resolved_path.is_relative_to(resolved_root) or path.is_symlink():
        raise SurfaceFluxRepeatabilityAnalysisError(
            "artifact authority path is unsafe."
        )
    return {
        "path": resolved_path.relative_to(resolved_root).as_posix(),
        "byte_length": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SurfaceFluxRepeatabilityAnalysisError(f"{label} must be an object.")
    return value


def _positive_finite(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) <= 0.0
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"{label} must be finite and positive."
        )
    return float(value)


def _finite_nonnegative(value: object, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or float(value) < 0.0
    ):
        raise SurfaceFluxRepeatabilityAnalysisError(
            f"{label} must be finite and nonnegative."
        )
    return float(value)


def format_surface_flux_repeatability_json(value: Mapping[str, object]) -> str:
    """Return deterministic strict JSON for the report and completion."""

    return json.dumps(
        value,
        sort_keys=True,
        indent=2,
        ensure_ascii=False,
        allow_nan=False,
    ) + "\n"


__all__ = [
    "D5_C2_ANALYSIS_ID",
    "D5_C2_COMPLETION_NAME",
    "D5_C2_DEFAULT_A1_INPUT_DIRECTORY",
    "D5_C2_DEFAULT_A2_INPUT_DIRECTORY",
    "D5_C2_DEFAULT_C1_INPUT_DIRECTORY",
    "D5_C2_DEFAULT_OUTPUT_DIRECTORY",
    "D5_C2_REPORT_NAME",
    "SurfaceFluxRepeatabilityAnalysisConfig",
    "SurfaceFluxRepeatabilityAnalysisError",
    "SurfaceFluxRepeatabilityAnalysisPublication",
    "analyze_surface_flux_endpoint_repeatability",
    "format_surface_flux_repeatability_json",
]
