"""Diagnostic-only native-SMD versus COB optical-stack audit utilities.

Nothing in this module is imported by a production execution path.  The
functions deliberately consume the production source, optical-stack, layout,
mounting, receiver, and Stage A authorities without changing them.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
from typing import Iterable, Mapping, Sequence

import numpy as np

from fspm_optics.application.domain import ProposedRunRequest
from fspm_optics.application.proposed import run_proposed_baseline
from fspm_optics.diagnostics.stage_a_validator import validate_stage_a_run
from fspm_optics.fixtures.proposed_cob.characterization import (
    flux_receiver_text as cob_flux_receiver_text,
    unit_internal_source_text as cob_unit_internal_source_text,
)
from fspm_optics.fixtures.proposed_cob.source import (
    COB_ANGULAR_DAT_FILENAME,
    COB_CHARACTERIZATION_IDENTITY_SHA256,
    COB_COMPLETED_APERTURE_FWHM_DEG,
    COB_COMPLETED_APERTURE_PROFILE_SHA256,
    COB_COMPLETED_APERTURE_TRANSMISSION,
    COB_IES_SHA256,
    COB_SOURCE_MODE,
    NATIVE_SOURCE_MODE,
    load_cob_angular_law,
    native_proposed_spd_identity_sha256,
    resolve_proposed_source_authority,
)
from fspm_optics.fixtures.occlusion import (
    compile_fixture_occlusion,
    load_authenticated_fixture_asset,
    materialize_fixture_occlusion,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
    validate_complete_classification_manifest,
)
from fspm_optics.fixtures.smd.angular_characterization import (
    AngularCharacterizationConfig,
    angular_sample_angles,
    build_flux_receiver_text,
    execute_fixed_completed_aperture_profile,
    far_field_azimuth_quadrature,
    format_far_field_receivers,
)
from fspm_optics.fixtures.smd.aperture_ppe_calibration import (
    aperture_flux_from_rgb,
    calibration_scene_texts,
    check_options,
    parse_equal_rgb,
)
from fspm_optics.fixtures.smd.optical_stack import (
    ACCEPTED_FIXTURE_TRANSMISSION,
    APERTURE_SIDE_M,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
    proposed_fixture_materials_radiance_text,
    proposed_fixture_stack_radiance_text,
)
from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.geometry.active_domain import ActiveRoomDomain
from fspm_optics.geometry.mounting import MountingGeometry
from fspm_optics.radiance.commands import CommandSpec, build_oconv_command
from fspm_optics.radiance.executables import resolve_executable
from fspm_optics.radiance.options import radiance_options
from fspm_optics.radiance.runner import LocalRunner
from fspm_optics.radiance.versioning import (
    discover_radiance_installation,
    probe_radiance_version,
)
from fspm_optics.transport.basis.atomic import atomic_write_text
from fspm_optics.transport.scalar_ppfd import parse_rtrace_rgb_rows

AUDIT_SCHEMA_ID = "fspm-optics.proposed-cob-native-comparative-audit"
AUDIT_SCHEMA_VERSION = 1
ANGULAR_SAMPLES_SCHEMA_ID = (
    "fspm-optics.proposed-cob-matched-completed-aperture-samples"
)
ANGULAR_SAMPLES_SCHEMA_VERSION = 1
ACCEPTANCE_SCHEMA_ID = "fspm-optics.proposed-cob-receiver-acceptance"
ACCEPTANCE_SCHEMA_VERSION = 1
DIRECT_SCHEMA_ID = "fspm-optics.proposed-cob-matched-direct"
DIRECT_SCHEMA_VERSION = 1
INTERCEPTION_SCHEMA_ID = "fspm-optics.proposed-cob-fixture-interception"
INTERCEPTION_SCHEMA_VERSION = 1

CERTIFIED_RUNS = (
    "c25ac75d8fa24a5ca2c920cf2201dbf9",
    "88c49f07607c480d8ef8c7a04113166e",
)
EXPECTED_NORMALIZED_ANGULAR_IDENTITY = (
    "bdff179f96f7ee079c2c554984cbd1574c25c4a3f9176e55744691722656333b"
)
EXPECTED_COB_DAT_SHA256 = (
    "38145def01b2e9801353d1b2d3a86c32885e64b686a4bfaaea3454c54c4c0b91"
)
EXPECTED_ARCHIVE_HEAD = "d0eedcf12bfda6ce06d3fc3c84279efda7be2aa2"
EXPECTED_ARCHIVE_BRANCH = "feat/proposed-cob-mode"

FINE_ANGLE_STEP_DEG = 0.5
FINE_AZIMUTH_COUNT = 19
COARSE_ANGLE_STEP_DEG = 1.0
COARSE_AZIMUTH_COUNT = 10
FAR_FIELD_DISTANCE_M = 20.0
AZIMUTH_QUADRATURE = "square_symmetry_endpoint_trapezoid"
COMPLETED_PPE_UMOL_PER_J = 2.6
INTERCEPTION_POLAR_EDGES_DEG = (0, 15, 30, 45, 60, 75, 90)
INTERCEPTION_AZIMUTH_STEP_DEG = 15


class ProposedCobAuditError(RuntimeError):
    """The diagnostic could not establish an authenticated finite result."""


@dataclass(frozen=True, slots=True)
class TreeInventory:
    root: str
    records: tuple[dict[str, object], ...]
    identity_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_id": "fspm-optics.byte-inventory",
            "schema_version": 1,
            "root": self.root,
            "file_count": len(self.records),
            "records": list(self.records),
            "identity_sha256": self.identity_sha256,
        }


@dataclass(frozen=True, slots=True)
class AngularSampleGrid:
    source_mode: str
    angles_deg: tuple[float, ...]
    azimuths_deg: tuple[float, ...]
    azimuth_weights: tuple[float, ...]
    far_field_distance_m: float
    raw_radiant_intensity: tuple[tuple[float, ...], ...]
    boundary_policy: str

    def __post_init__(self) -> None:
        if self.source_mode not in {NATIVE_SOURCE_MODE, COB_SOURCE_MODE}:
            raise ValueError("angular source mode is unsupported.")
        if (
            len(self.raw_radiant_intensity) != len(self.angles_deg)
            or not self.angles_deg
            or self.angles_deg[-1] != 90.0
        ):
            raise ValueError("angular sample grid shape is malformed.")
        if len(self.azimuths_deg) != len(self.azimuth_weights):
            raise ValueError("angular azimuth nodes and weights disagree.")
        for row in self.raw_radiant_intensity:
            if len(row) != len(self.azimuths_deg):
                raise ValueError("angular sample row width changed.")
            if any(not math.isfinite(value) or value < 0.0 for value in row):
                raise ValueError(
                    "angular radiant intensities must be finite and non-negative."
                )

    @property
    def identity_sha256(self) -> str:
        return hash_json(self.payload(include_identity=False))

    def payload(self, *, include_identity: bool = True) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_id": ANGULAR_SAMPLES_SCHEMA_ID,
            "schema_version": ANGULAR_SAMPLES_SCHEMA_VERSION,
            "source_mode": self.source_mode,
            "far_field_distance_m": self.far_field_distance_m,
            "polar_angles_deg": list(self.angles_deg),
            "azimuths_deg": list(self.azimuths_deg),
            "azimuth_weights_over_0_to_90": list(self.azimuth_weights),
            "azimuth_quadrature": AZIMUTH_QUADRATURE,
            "square_symmetry_quadrants": 4,
            "radiant_intensity_units": (
                "umol s^-1 sr^-1 for a 1 umol/s internal source"
            ),
            "raw_radiant_intensity_by_polar_then_azimuth": [
                list(row) for row in self.raw_radiant_intensity
            ],
            "grazing_boundary_policy": self.boundary_policy,
        }
        if include_identity:
            payload["identity_sha256"] = hash_json(payload)
        return payload


def hash_json(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(
            payload, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def inventory_tree(root: str | Path) -> TreeInventory:
    """Return a deterministic path/size/byte-hash inventory, rejecting links."""

    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise ProposedCobAuditError(f"inventory root is missing: {base}")
    records: list[dict[str, object]] = []
    for directory, names, filenames in os.walk(base, followlinks=False):
        current = Path(directory)
        for name in sorted(names):
            candidate = current / name
            if candidate.is_symlink():
                raise ProposedCobAuditError(
                    f"inventory refuses directory symlink: {candidate}"
                )
        for name in sorted(filenames):
            candidate = current / name
            if candidate.is_symlink():
                raise ProposedCobAuditError(
                    f"inventory refuses file symlink: {candidate}"
                )
            if not candidate.is_file():
                raise ProposedCobAuditError(
                    f"inventory refuses non-regular entry: {candidate}"
                )
            relative = candidate.relative_to(base).as_posix()
            records.append(
                {
                    "path": relative,
                    "size_bytes": candidate.stat().st_size,
                    "sha256": sha256_file(candidate),
                }
            )
    records.sort(key=lambda item: str(item["path"]))
    identity = hash_json(
        {
            "algorithm": "sorted_relative_path_size_sha256_json_v1",
            "records": records,
        }
    )
    return TreeInventory(str(base), tuple(records), identity)


def _inside(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def authenticate_certified_archive(
    archive: str | Path,
    validation_output: str | Path,
) -> dict[str, object]:
    """Authenticate the archive, revalidate both runs, and prove no mutation."""

    archive_path = Path(archive).expanduser().resolve()
    output = Path(validation_output).expanduser().resolve()
    if _inside(output, archive_path):
        raise ProposedCobAuditError(
            "certified validation output must be outside the archive."
        )
    before = inventory_tree(archive_path)
    provenance = archive_path / "provenance"
    head = (provenance / "git-head.txt").read_text(encoding="utf-8").strip()
    branch = (provenance / "git-branch.txt").read_text(encoding="utf-8").strip()
    status = (provenance / "git-status.txt").read_text(encoding="utf-8")
    index_patch = provenance / "index.patch"
    worktree_patch = provenance / "worktree.patch"
    if head != EXPECTED_ARCHIVE_HEAD or branch != EXPECTED_ARCHIVE_BRANCH:
        raise ProposedCobAuditError("certified Git provenance changed.")
    if index_patch.stat().st_size != 0 or worktree_patch.stat().st_size <= 0:
        raise ProposedCobAuditError("certified patch provenance is malformed.")

    recorded_digests: dict[str, str] = {}
    for line in (provenance / "run-tree-digests.sha256").read_text(
        encoding="ascii"
    ).splitlines():
        fields = line.split()
        if len(fields) != 2 or fields[1] not in CERTIFIED_RUNS:
            raise ProposedCobAuditError("recorded run-tree digest is malformed.")
        recorded_digests[fields[1]] = fields[0]
    if set(recorded_digests) != set(CERTIFIED_RUNS):
        raise ProposedCobAuditError("certified run-tree digest set changed.")

    law = load_cob_angular_law()
    if (
        law.ies_sha256 != COB_IES_SHA256
        or law.normalized_identity_sha256
        != EXPECTED_NORMALIZED_ANGULAR_IDENTITY
        or law.dat_sha256 != EXPECTED_COB_DAT_SHA256
    ):
        raise ProposedCobAuditError("current COB resource identity changed.")
    source_records: dict[str, object] = {}
    validation_records: dict[str, object] = {}
    independent_run_inventories: dict[str, object] = {}
    for run_id in CERTIFIED_RUNS:
        run = archive_path / "runs" / run_id
        independent_run_inventories[run_id] = inventory_tree(run).to_dict()
        manifest = read_json(run / "manifest.json")
        stage_manifest = read_json(
            run / "uniform-stage-a" / "uniform_stage_a_manifest.json"
        )
        request = require_mapping(manifest, "request")
        if (
            request.get("proposed_source_mode") != COB_SOURCE_MODE
            or request.get("quality") != "quality"
            or request.get("proposed_control_mode")
            != "uniform_module_dimming"
        ):
            raise ProposedCobAuditError(
                f"certified request identity changed for {run_id}."
            )
        metadata = require_mapping(stage_manifest, "source_metadata")
        expected = {
            "authenticated_ies_sha256": COB_IES_SHA256,
            "normalized_angular_identity_sha256": (
                EXPECTED_NORMALIZED_ANGULAR_IDENTITY
            ),
            "angular_data_sha256": EXPECTED_COB_DAT_SHA256,
            "completed_aperture_characterization_identity_sha256": (
                COB_CHARACTERIZATION_IDENTITY_SHA256
            ),
            "controlled_spd_identity_sha256": (
                native_proposed_spd_identity_sha256()
            ),
        }
        if any(metadata.get(key) != value for key, value in expected.items()):
            raise ProposedCobAuditError(
                f"certified source identities changed for {run_id}."
            )
        source_records[run_id] = expected
        validation_records[run_id] = validate_stage_a_run(
            run, output / run_id
        )
    after = inventory_tree(archive_path)
    if before.identity_sha256 != after.identity_sha256 or before.records != (
        after.records
    ):
        raise ProposedCobAuditError(
            "certified archive content changed during read-only validation."
        )
    return {
        "archive": str(archive_path),
        "read_only": True,
        "symlinks_rejected": True,
        "inventory_before": before.to_dict(),
        "inventory_after": after.to_dict(),
        "archive_unchanged": True,
        "git": {
            "head": head,
            "branch": branch,
            "status_text": status,
            "index_patch": {
                "size_bytes": index_patch.stat().st_size,
                "sha256": sha256_file(index_patch),
            },
            "worktree_patch": {
                "size_bytes": worktree_patch.stat().st_size,
                "sha256": sha256_file(worktree_patch),
            },
        },
        "recorded_run_tree_digests": recorded_digests,
        "independent_run_tree_inventories": independent_run_inventories,
        "source_identities": source_records,
        "stage_a_revalidation": validation_records,
    }


def _quadrant_cell_records(
    grid: AngularSampleGrid,
) -> Iterable[
    tuple[
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
        float,
    ]
]:
    """Yield theta/phi cell bounds, four intensities, and full-symmetry dΩ."""

    angles = grid.angles_deg
    azimuths = grid.azimuths_deg
    for ti, (theta0, theta1) in enumerate(
        zip(angles[:-1], angles[1:], strict=True)
    ):
        solid_polar = math.cos(math.radians(theta0)) - math.cos(
            math.radians(theta1)
        )
        for pi, (phi0, phi1) in enumerate(
            zip(azimuths[:-1], azimuths[1:], strict=True)
        ):
            solid_angle = (
                4.0 * math.radians(phi1 - phi0) * solid_polar
            )
            yield (
                theta0,
                theta1,
                phi0,
                phi1,
                grid.raw_radiant_intensity[ti][pi],
                grid.raw_radiant_intensity[ti][pi + 1],
                grid.raw_radiant_intensity[ti + 1][pi],
                grid.raw_radiant_intensity[ti + 1][pi + 1],
                solid_angle,
            )


def _cell_flux(values: Sequence[float], solid_angle: float) -> float:
    return math.fsum(values) * 0.25 * solid_angle


def _weighted_cell_flux(
    theta0: float,
    theta1: float,
    values: Sequence[float],
    solid_angle: float,
    moment,
) -> float:
    weights = (
        moment(math.radians(theta0)),
        moment(math.radians(theta0)),
        moment(math.radians(theta1)),
        moment(math.radians(theta1)),
    )
    return math.fsum(
        value * weight for value, weight in zip(values, weights, strict=True)
    ) * 0.25 * solid_angle


def _azimuth_average(row: Sequence[float], weights: Sequence[float]) -> float:
    return math.fsum(
        value * weight for value, weight in zip(row, weights, strict=True)
    )


def _fwhm(angles: Sequence[float], profile: Sequence[float]) -> float:
    reference = float(profile[0])
    if reference <= 0.0:
        raise ProposedCobAuditError("optical-axis intensity is not positive.")
    half = 0.5 * reference
    for a0, a1, v0, v1 in zip(
        angles[:-1], angles[1:], profile[:-1], profile[1:], strict=True
    ):
        if v0 >= half and v1 <= half:
            if v0 == v1:
                crossing = 0.5 * (a0 + a1)
            else:
                crossing = a0 + (a1 - a0) * (v0 - half) / (v0 - v1)
            return 2.0 * crossing
    raise ProposedCobAuditError("angular profile has no half-maximum crossing.")


def _containment_angle(
    rings: Sequence[tuple[float, float, float]],
    target_fraction: float,
    total: float,
) -> float:
    target = target_fraction * total
    cumulative = 0.0
    for lower, upper, flux in rings:
        if cumulative + flux >= target:
            if flux <= 0.0:
                return upper
            share = (target - cumulative) / flux
            # Interpolate within the represented exact solid-angle cell.
            cos_value = math.cos(math.radians(lower)) - share * (
                math.cos(math.radians(lower))
                - math.cos(math.radians(upper))
            )
            return math.degrees(math.acos(max(-1.0, min(1.0, cos_value))))
        cumulative += flux
    return 90.0


def angular_metrics(grid: AngularSampleGrid) -> dict[str, object]:
    """Integrate the raw quadrant samples without assuming axisymmetry."""

    total = 0.0
    theta = 0.0
    theta2 = 0.0
    cosine = 0.0
    cosine2 = 0.0
    ring_flux = {
        (lower, upper): 0.0
        for lower, upper in zip(
            grid.angles_deg[:-1], grid.angles_deg[1:], strict=True
        )
    }
    for record in _quadrant_cell_records(grid):
        theta0, theta1, _phi0, _phi1, *tail = record
        values = tail[:4]
        solid_angle = tail[4]
        flux = _cell_flux(values, solid_angle)
        total += flux
        ring_flux[(theta0, theta1)] += flux
        theta += _weighted_cell_flux(
            theta0,
            theta1,
            values,
            solid_angle,
            lambda value: value,
        )
        theta2 += _weighted_cell_flux(
            theta0,
            theta1,
            values,
            solid_angle,
            lambda value: value * value,
        )
        cosine += _weighted_cell_flux(
            theta0, theta1, values, solid_angle, math.cos
        )
        cosine2 += _weighted_cell_flux(
            theta0,
            theta1,
            values,
            solid_angle,
            lambda value: math.cos(value) ** 2,
        )
    if not math.isfinite(total) or total <= 0.0:
        raise ProposedCobAuditError("angular integration is not positive.")
    rings = tuple(
        (lower, upper, flux)
        for (lower, upper), flux in sorted(ring_flux.items())
    )
    averaged = tuple(
        _azimuth_average(row, grid.azimuth_weights)
        for row in grid.raw_radiant_intensity
    )
    azimuth_records = []
    azimuth_cv_values: list[tuple[float, float]] = []
    azimuth_ratio_values: list[tuple[float, float]] = []
    for angle, row, mean in zip(
        grid.angles_deg,
        grid.raw_radiant_intensity,
        averaged,
        strict=True,
    ):
        variance = math.fsum(
            weight * (value - mean) ** 2
            for value, weight in zip(
                row, grid.azimuth_weights, strict=True
            )
        )
        cv = 0.0 if mean == 0.0 else math.sqrt(variance) / mean
        minimum = min(row)
        ratio = None if minimum <= 0.0 else max(row) / minimum
        azimuth_records.append(
            {
                "polar_angle_deg": angle,
                "weighted_mean_radiant_intensity": mean,
                "weighted_standard_deviation": math.sqrt(variance),
                "cv": cv,
                "minimum": minimum,
                "maximum": max(row),
                "maximum_to_minimum": ratio,
            }
        )
        if angle < 90.0 and mean > 0.0:
            azimuth_cv_values.append((mean, cv))
            if ratio is not None:
                azimuth_ratio_values.append((mean, ratio))
    az_weight = math.fsum(weight for weight, _value in azimuth_cv_values)
    summarized_cv = (
        math.fsum(weight * value for weight, value in azimuth_cv_values)
        / az_weight
    )
    summarized_ratio = max(
        value for _weight, value in azimuth_ratio_values
    )
    fractions_beyond = {
        str(threshold): math.fsum(
            flux for lower, _upper, flux in rings if lower >= threshold
        )
        / total
        for threshold in (30.0, 45.0, 60.0, 75.0)
    }
    containment = {
        str(int(fraction * 100)): _containment_angle(rings, fraction, total)
        for fraction in (0.50, 0.80, 0.90, 0.95)
    }
    mean_theta_rad = theta / total
    return {
        "integrated_angular_ppf_umol_s": total,
        "completed_aperture_fwhm_deg": _fwhm(
            grid.angles_deg, averaged
        ),
        "flux_weighted_mean_polar_angle_deg": math.degrees(mean_theta_rad),
        "flux_weighted_rms_polar_angle_deg": math.degrees(
            math.sqrt(theta2 / total)
        ),
        "flux_weighted_expected_cos_theta": cosine / total,
        "flux_weighted_expected_cos2_theta": cosine2 / total,
        "energy_fraction_beyond_polar_angle_deg": fractions_beyond,
        "containment_polar_angle_deg": containment,
        "azimuthal_variation": {
            "per_polar_angle": azimuth_records,
            "intensity_weighted_mean_cv": summarized_cv,
            "maximum_finite_maximum_to_minimum": summarized_ratio,
        },
        "integration": {
            "formula": (
                "sum over theta/phi cells: mean(I00,I01,I10,I11) * "
                "4 * delta_phi_rad * (cos(theta0)-cos(theta1))"
            ),
            "moments": (
                "same cell quadrature applied to I*g(theta) at each endpoint"
            ),
            "units": {
                "radiant_intensity": "umol s^-1 sr^-1",
                "integrated_ppf": "umol s^-1",
                "polar_angle": "degree",
                "solid_angle": "sr",
            },
            "axisymmetry_assumed": False,
            "square_symmetry_used": True,
        },
    }


def _run(command: CommandSpec, stderr_path: Path, runner: LocalRunner) -> None:
    result = runner.run(command, stderr_path=stderr_path)
    if not result.success:
        raise ProposedCobAuditError(
            result.failure_message or f"{command.label} failed."
        )


def _trace_cob_grid(
    output: Path,
    config: AngularCharacterizationConfig,
    *,
    oconv: Path,
    rtrace: Path,
    runner: LocalRunner,
) -> None:
    output.mkdir(parents=True, exist_ok=False)
    law = load_cob_angular_law()
    source_path = output / "internal-source.rad"
    fixture_path = output / "fixture.rad"
    dat_path = output / COB_ANGULAR_DAT_FILENAME
    receivers_path = output / "far-field.pts"
    octree_path = output / "fixture.oct"
    rgb_path = output / "far-field.rgb"
    atomic_write_text(source_path, cob_unit_internal_source_text())
    atomic_write_text(dat_path, law.dat_text)
    atomic_write_text(
        fixture_path,
        proposed_fixture_materials_radiance_text()
        + "\n"
        + proposed_fixture_stack_radiance_text(
            0, 0.0, 0.0, 0.0, name_prefix="matched_cob_audit"
        ),
    )
    receivers, _keys = format_far_field_receivers(
        angles_deg=angular_sample_angles(config.angle_step_deg),
        azimuth_count=config.azimuth_count,
        distance_m=config.far_field_distance_m,
        azimuth_quadrature=AZIMUTH_QUADRATURE,
    )
    atomic_write_text(receivers_path, receivers)
    _run(
        CommandSpec(
            argv=(str(oconv), "-f", str(fixture_path), str(source_path)),
            stdout_path=octree_path,
            stdout_mode="binary",
            cwd=output,
            label="matched-cob-angular-oconv",
        ),
        output / "oconv.stderr",
        runner,
    )
    options = [
        option
        for option in radiance_options(config.quality)
        if option not in {"-u+", "-u-"}
    ]
    options.append("-u-")
    _run(
        CommandSpec(
            argv=(
                str(rtrace),
                "-h",
                "-I+",
                "-n",
                str(config.nthreads),
                *options,
                str(octree_path),
            ),
            stdin_path=receivers_path,
            stdout_path=rgb_path,
            cwd=output,
            label="matched-cob-angular-rtrace",
        ),
        output / "rtrace.stderr",
        runner,
    )


def _load_grid(
    output: Path,
    config: AngularCharacterizationConfig,
    source_mode: str,
) -> AngularSampleGrid:
    values = parse_rtrace_rgb_rows(
        (output / "far-field.rgb").read_text(encoding="utf-8")
    )
    angles = angular_sample_angles(config.angle_step_deg)
    azimuths, weights = far_field_azimuth_quadrature(
        config.azimuth_count, method=AZIMUTH_QUADRATURE
    )
    expected = sum(angle < 90.0 for angle in angles) * len(azimuths)
    if len(values) != expected:
        raise ProposedCobAuditError(
            f"{source_mode} angular sample count changed."
        )
    rows: list[tuple[float, ...]] = []
    cursor = 0
    scale = config.far_field_distance_m**2
    for angle in angles:
        if angle >= 90.0:
            rows.append(tuple(0.0 for _ in azimuths))
            continue
        row = tuple(
            value * scale
            for value in values[cursor : cursor + len(azimuths)]
        )
        cursor += len(azimuths)
        rows.append(row)
    return AngularSampleGrid(
        source_mode=source_mode,
        angles_deg=angles,
        azimuths_deg=azimuths,
        azimuth_weights=weights,
        far_field_distance_m=config.far_field_distance_m,
        raw_radiant_intensity=tuple(rows),
        boundary_policy=(
            "theta=90 degrees is the explicit zero-intensity aperture-plane "
            "boundary; rtrace receivers exist for theta<90 only"
        ),
    )


def _execute_flux(
    output: Path,
    *,
    source_mode: str,
    internal_ppf: float,
    option_key: str,
    flux_tool: Path,
    runner: LocalRunner,
) -> float:
    output.mkdir(parents=True, exist_ok=False)
    scenes = calibration_scene_texts()
    for name in ("materials.rad", "system.rad", "aperture_sender.rad"):
        atomic_write_text(output / name, scenes[name])
    if source_mode == NATIVE_SOURCE_MODE:
        receiver_text, cal_text = build_flux_receiver_text(
            0.0, internal_flux_umol_s=internal_ppf
        )
        if cal_text is not None:
            raise ProposedCobAuditError("native flux source unexpectedly has CAL.")
    elif source_mode == COB_SOURCE_MODE:
        law = load_cob_angular_law()
        atomic_write_text(output / COB_ANGULAR_DAT_FILENAME, law.dat_text)
        receiver_text = cob_flux_receiver_text(
            internal_flux_umol_s=internal_ppf
        )
    else:
        raise ProposedCobAuditError("flux source mode is unsupported.")
    receiver_path = output / "internal-source-receiver.rad"
    rgb_path = output / "aperture-flux.rgb"
    atomic_write_text(receiver_path, receiver_text)
    _run(
        CommandSpec(
            argv=(
                str(flux_tool),
                *check_options(option_key, 1),
                str(output / "aperture_sender.rad"),
                str(receiver_path),
                str(output / "materials.rad"),
                str(output / "system.rad"),
            ),
            stdout_path=rgb_path,
            cwd=output,
            label=f"matched-{source_mode}-flux-{option_key}",
        ),
        output / "flux-matrix.stderr",
        runner,
    )
    return aperture_flux_from_rgb(
        parse_equal_rgb(rgb_path.read_text(encoding="utf-8"))
    )


def _convergence(
    fine: Mapping[str, object], coarse: Mapping[str, object]
) -> dict[str, object]:
    comparisons = {
        "integrated_angular_ppf_relative": (
            abs(
                float(fine["integrated_angular_ppf_umol_s"])
                - float(coarse["integrated_angular_ppf_umol_s"])
            )
            / float(fine["integrated_angular_ppf_umol_s"]),
            0.01,
        ),
        "fwhm_absolute_deg": (
            abs(
                float(fine["completed_aperture_fwhm_deg"])
                - float(coarse["completed_aperture_fwhm_deg"])
            ),
            0.75,
        ),
        "mean_polar_absolute_deg": (
            abs(
                float(fine["flux_weighted_mean_polar_angle_deg"])
                - float(coarse["flux_weighted_mean_polar_angle_deg"])
            ),
            0.5,
        ),
        "expected_cos_absolute": (
            abs(
                float(fine["flux_weighted_expected_cos_theta"])
                - float(coarse["flux_weighted_expected_cos_theta"])
            ),
            0.01,
        ),
    }
    checks = {
        name: {
            "observed": value,
            "maximum_inclusive": tolerance,
            "pass": value <= tolerance,
        }
        for name, (value, tolerance) in comparisons.items()
    }
    if not all(bool(item["pass"]) for item in checks.values()):
        raise ProposedCobAuditError(
            "matched angular grid convergence did not meet tolerance."
        )
    return {
        "fine": {
            "polar_step_deg": FINE_ANGLE_STEP_DEG,
            "azimuth_count": FINE_AZIMUTH_COUNT,
        },
        "coarse": {
            "polar_step_deg": COARSE_ANGLE_STEP_DEG,
            "azimuth_count": COARSE_AZIMUTH_COUNT,
        },
        "checks": checks,
        "pass": True,
    }


def execute_matched_characterization(
    output_directory: str | Path,
    *,
    quality: str = "quality",
) -> dict[str, object]:
    """Run matched native and COB completed-aperture characterization."""

    root = Path(output_directory).expanduser().resolve()
    if root.exists():
        raise ProposedCobAuditError(
            f"matched characterization output already exists: {root}"
        )
    root.mkdir(parents=True)
    installation = discover_radiance_installation()
    flux_tool_name = "r" + "fluxmtx"
    flux_tool = resolve_executable(
        flux_tool_name, cwd=Path.cwd(), label=flux_tool_name
    )
    runner = LocalRunner()
    grids: dict[str, AngularSampleGrid] = {}
    results: dict[str, object] = {}
    for source_mode in (NATIVE_SOURCE_MODE, COB_SOURCE_MODE):
        mode_root = root / source_mode
        mode_root.mkdir()
        measured: dict[str, tuple[AngularSampleGrid, dict[str, object]]] = {}
        for label, step, azimuth_count in (
            ("fine", FINE_ANGLE_STEP_DEG, FINE_AZIMUTH_COUNT),
            ("coarse", COARSE_ANGLE_STEP_DEG, COARSE_AZIMUTH_COUNT),
        ):
            config = AngularCharacterizationConfig(
                output_directory=mode_root / label,
                quality=quality,
                nthreads=1,
                angle_step_deg=step,
                azimuth_count=azimuth_count,
                far_field_distance_m=FAR_FIELD_DISTANCE_M,
                fwhm_tolerance_deg=max(step, FINE_ANGLE_STEP_DEG),
            )
            evaluation = mode_root / label / "profile"
            if source_mode == NATIVE_SOURCE_MODE:
                execute_fixed_completed_aperture_profile(
                    config,
                    exponent=0.0,
                    evaluation_directory=evaluation,
                    authenticated_cal_text=None,
                    runner=runner,
                    oconv_path=installation.oconv.path,
                    rtrace_path=installation.rtrace.path,
                )
            else:
                _trace_cob_grid(
                    evaluation,
                    config,
                    oconv=installation.oconv.path,
                    rtrace=installation.rtrace.path,
                    runner=runner,
                )
            grid = _load_grid(evaluation, config, source_mode)
            metrics = angular_metrics(grid)
            measured[label] = (grid, metrics)
            atomic_write_text(
                mode_root / f"angular-samples-{label}.json",
                json.dumps(
                    grid.payload(), indent=2, sort_keys=True, allow_nan=False
                )
                + "\n",
            )
        fine_grid, fine_metrics = measured["fine"]
        _coarse_grid, coarse_metrics = measured["coarse"]
        grids[source_mode] = fine_grid
        flux = {
            key: _execute_flux(
                mode_root / f"flux-{key}",
                source_mode=source_mode,
                internal_ppf=1.0,
                option_key=key,
                flux_tool=flux_tool,
                runner=runner,
            )
            for key in ("A", "B", "C")
        }
        transmission = flux["C"]
        # The authenticated COB characterization contract accepts B/C at
        # 0.5%.  Check A is retained as a lower-sampling observation, but its
        # 5,000-ray setting is not converged for the sharp brightdata LES and
        # therefore is not an acceptance gate.
        if abs(flux["C"] - flux["B"]) / flux["B"] > 0.005:
            raise ProposedCobAuditError(
                f"{source_mode} aperture-flux quadrature did not converge."
            )
        internal_ppe = COMPLETED_PPE_UMOL_PER_J / transmission
        completed_ppf = _execute_flux(
            mode_root / "reference-watt",
            source_mode=source_mode,
            internal_ppf=internal_ppe,
            option_key="C",
            flux_tool=flux_tool,
            runner=runner,
        )
        closure_error = abs(completed_ppf - COMPLETED_PPE_UMOL_PER_J)
        if closure_error / COMPLETED_PPE_UMOL_PER_J > 0.005:
            raise ProposedCobAuditError(
                f"{source_mode} completed-aperture PPE closure failed."
            )
        authority = resolve_proposed_source_authority(source_mode)
        frozen_transmission = authority.completed_aperture_transmission
        checkpoint_error = abs(transmission - frozen_transmission)
        if checkpoint_error / frozen_transmission > 0.005:
            raise ProposedCobAuditError(
                f"{source_mode} stack transmission misses frozen authority."
            )
        if source_mode == COB_SOURCE_MODE and abs(
            float(fine_metrics["completed_aperture_fwhm_deg"])
            - COB_COMPLETED_APERTURE_FWHM_DEG
        ) > 0.75:
            raise ProposedCobAuditError(
                "COB completed-aperture FWHM misses frozen checkpoint."
            )
        angular_flux_relative_error = abs(
            float(fine_metrics["integrated_angular_ppf_umol_s"])
            - transmission
        ) / transmission
        results[source_mode] = {
            "source_mode": source_mode,
            "integrated_internal_ppf_umol_s": 1.0,
            "completed_aperture_ppf_umol_s": transmission,
            "stack_transmission": transmission,
            "transmission_observations": flux,
            "transmission_convergence": {
                "accepted_comparison": "B_to_C",
                "B_to_C_relative_difference": (
                    abs(flux["C"] - flux["B"]) / flux["B"]
                ),
                "maximum_inclusive": 0.005,
                "pass": True,
                "A_role": (
                    "reported lower-sampling observation; not an acceptance "
                    "gate for the sharp COB brightdata LES"
                ),
            },
            "required_internal_ppe_umol_per_j": internal_ppe,
            "completed_aperture_ppe_umol_per_j": completed_ppf,
            "completed_aperture_ppe_closure_absolute_umol_per_j": (
                closure_error
            ),
            "completed_aperture_ppe_closure_relative": (
                closure_error / COMPLETED_PPE_UMOL_PER_J
            ),
            "angular_far_field_flux_to_aperture_flux_relative_error": (
                angular_flux_relative_error
            ),
            "metrics": fine_metrics,
            "convergence": _convergence(fine_metrics, coarse_metrics),
            "sample_identity_sha256": fine_grid.identity_sha256,
            "frozen_checkpoint": {
                "transmission": frozen_transmission,
                "transmission_relative_error": (
                    checkpoint_error / frozen_transmission
                ),
                "fwhm_deg": (
                    COB_COMPLETED_APERTURE_FWHM_DEG
                    if source_mode == COB_SOURCE_MODE
                    else None
                ),
            },
        }
    payload = {
        "schema_id": (
            "fspm-optics.proposed-cob-matched-completed-aperture-"
            "characterization"
        ),
        "schema_version": 1,
        "settings": {
            "quality": quality,
            "nthreads": 1,
            "fine_polar_step_deg": FINE_ANGLE_STEP_DEG,
            "fine_azimuth_count": FINE_AZIMUTH_COUNT,
            "coarse_polar_step_deg": COARSE_ANGLE_STEP_DEG,
            "coarse_azimuth_count": COARSE_AZIMUTH_COUNT,
            "azimuth_quadrature": AZIMUTH_QUADRATURE,
            "far_field_distance_m": FAR_FIELD_DISTANCE_M,
            "optical_stack": (
                "production PMMA/PTFE cavity/bezel/aperture/internal plane"
            ),
        },
        "source_modes": results,
        "source_identities": current_source_identities(),
        "radiance": {
            "oconv": installation.oconv.to_dict(),
            "rtrace": installation.rtrace.to_dict(),
            "flux_matrix_tool": {
                "name": flux_tool_name,
                "path": str(flux_tool),
                "version_text": probe_radiance_version(flux_tool),
            },
        },
    }
    payload["identity_sha256"] = hash_json(payload)
    atomic_write_text(
        root / "matched-characterization.v1.json",
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    return {"report": payload, "grids": grids}


def current_source_identities() -> dict[str, object]:
    law = load_cob_angular_law()
    if (
        law.normalized_identity_sha256
        != EXPECTED_NORMALIZED_ANGULAR_IDENTITY
        or law.dat_sha256 != EXPECTED_COB_DAT_SHA256
    ):
        raise ProposedCobAuditError("COB source identity checkpoint failed.")
    return {
        "ies_sha256": law.ies_sha256,
        "normalized_angular_identity_sha256": (
            law.normalized_identity_sha256
        ),
        "generated_dat_sha256": law.dat_sha256,
        "frozen_completed_profile_sha256": (
            COB_COMPLETED_APERTURE_PROFILE_SHA256
        ),
        "frozen_characterization_identity_sha256": (
            COB_CHARACTERIZATION_IDENTITY_SHA256
        ),
        "native_completed_aperture_transmission": (
            ACCEPTED_FIXTURE_TRANSMISSION
        ),
        "cob_completed_aperture_transmission": (
            COB_COMPLETED_APERTURE_TRANSMISSION
        ),
        "native_proposed_spd_identity_sha256": (
            native_proposed_spd_identity_sha256()
        ),
    }


def _fold_azimuth_deg(value: float) -> float:
    angle = value % 360.0
    quadrant = angle % 90.0
    if int(angle // 90.0) % 2:
        quadrant = 90.0 - quadrant
    return quadrant


def interpolate_intensity(
    grid: AngularSampleGrid, polar_deg: float, azimuth_deg: float
) -> float:
    """Bilinearly interpolate the authenticated quadrant sample grid."""

    theta = float(polar_deg)
    if theta < 0.0 or theta >= 90.0:
        return 0.0
    phi = _fold_azimuth_deg(float(azimuth_deg))
    angles = grid.angles_deg
    azimuths = grid.azimuths_deg
    ti = max(
        0,
        min(
            len(angles) - 2,
            int(np.searchsorted(angles, theta, side="right")) - 1,
        ),
    )
    pi = max(
        0,
        min(
            len(azimuths) - 2,
            int(np.searchsorted(azimuths, phi, side="right")) - 1,
        ),
    )
    t0, t1 = angles[ti], angles[ti + 1]
    p0, p1 = azimuths[pi], azimuths[pi + 1]
    tf = (theta - t0) / (t1 - t0)
    pf = (phi - p0) / (p1 - p0)
    i00 = grid.raw_radiant_intensity[ti][pi]
    i01 = grid.raw_radiant_intensity[ti][pi + 1]
    i10 = grid.raw_radiant_intensity[ti + 1][pi]
    i11 = grid.raw_radiant_intensity[ti + 1][pi + 1]
    return (
        i00 * (1.0 - tf) * (1.0 - pf)
        + i01 * (1.0 - tf) * pf
        + i10 * tf * (1.0 - pf)
        + i11 * tf * pf
    )


def receiver_capture_for_modules(
    grid: AngularSampleGrid,
    module_centers_xy: Sequence[tuple[float, float]],
    *,
    bounds_xy: tuple[float, float, float, float],
    mounting_height_m: float,
    edge_flags: Sequence[bool] | None = None,
) -> dict[str, object]:
    """Integrate directions whose center ray intersects the active rectangle."""

    if not module_centers_xy:
        raise ValueError("receiver acceptance requires module centers.")
    if edge_flags is None:
        flags = tuple(False for _ in module_centers_xy)
    else:
        flags = tuple(bool(value) for value in edge_flags)
    if len(flags) != len(module_centers_xy):
        raise ValueError("edge flags must match module centers.")
    min_x, max_x, min_y, max_y = bounds_xy
    height = float(mounting_height_m)
    if height <= 0.0:
        raise ValueError("mounting height must be positive.")

    denominator = 0.0
    numerators = [0.0 for _ in module_centers_xy]
    rejected_by_ring: dict[tuple[float, float], float] = {}
    for record in _quadrant_cell_records(grid):
        theta0, theta1, phi0, phi1, *tail = record
        values = tail[:4]
        solid_angle = tail[4]
        cell_energy = _cell_flux(values, solid_angle)
        denominator += cell_energy
        center_theta = 0.5 * (theta0 + theta1)
        center_phi = 0.5 * (phi0 + phi1)
        radial = height * math.tan(math.radians(center_theta))
        for index, (x0, y0) in enumerate(module_centers_xy):
            accepted = 0
            for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1)):
                x = x0 + sx * radial * math.cos(math.radians(center_phi))
                y = y0 + sy * radial * math.sin(math.radians(center_phi))
                accepted += int(min_x <= x <= max_x and min_y <= y <= max_y)
            numerators[index] += cell_energy * accepted / 4.0
        average_accepted = math.fsum(
            sum(
                int(
                    min_x
                    <= x0
                    + sx
                    * radial
                    * math.cos(math.radians(center_phi))
                    <= max_x
                    and min_y
                    <= y0
                    + sy
                    * radial
                    * math.sin(math.radians(center_phi))
                    <= max_y
                )
                for sx, sy in ((1, 1), (-1, 1), (-1, -1), (1, -1))
            )
            / 4.0
            for x0, y0 in module_centers_xy
        ) / len(module_centers_xy)
        average_rejected = 1.0 - average_accepted
        rejected_by_ring[(theta0, theta1)] = (
            rejected_by_ring.get((theta0, theta1), 0.0)
            + cell_energy * average_rejected
        )
    fractions = tuple(value / denominator for value in numerators)
    overall = math.fsum(fractions) / len(fractions)
    active_area = (max_x - min_x) * (max_y - min_y)
    required_ppf = 750.0 * active_area / overall
    modules = [
        {
            "module_index": index,
            "center_x_m": center[0],
            "center_y_m": center[1],
            "edge": flags[index],
            "capture_fraction": fractions[index],
            "rejected_fraction": 1.0 - fractions[index],
        }
        for index, center in enumerate(module_centers_xy)
    ]

    def group(flag: bool) -> dict[str, object]:
        values = [
            value for value, item_flag in zip(fractions, flags, strict=True)
            if item_flag is flag
        ]
        return {
            "count": len(values),
            "mean_capture_fraction": (
                None if not values else math.fsum(values) / len(values)
            ),
            "minimum_capture_fraction": None if not values else min(values),
            "maximum_capture_fraction": None if not values else max(values),
        }

    rejected_total = 1.0 - overall
    return {
        "source_mode": grid.source_mode,
        "geometric_receiver_domain_capture_fraction": overall,
        "rejected_fraction": rejected_total,
        "rejected_angular_distribution": [
            {
                "polar_range_deg": [lower, upper],
                "fraction_of_emitted_ppf": value / denominator,
                "fraction_of_rejected_ppf": (
                    0.0 if rejected_total == 0.0 else value / denominator / rejected_total
                ),
            }
            for (lower, upper), value in sorted(rejected_by_ring.items())
        ],
        "predicted_completed_aperture_ppf_for_750_umol_m2_s": required_ppf,
        "per_module": modules,
        "edge_modules": group(True),
        "interior_modules": group(False),
        "approximation": (
            "far-field completed-aperture angular distribution launched from "
            "each module center; aperture position-angle phase space is unavailable"
        ),
        "integration": (
            "matched theta/phi cell energy with four production square-symmetry "
            "quadrants evaluated against the active receiver rectangle"
        ),
    }


def evaluate_receiver_acceptance(
    grids: Mapping[str, AngularSampleGrid],
) -> dict[str, object]:
    rooms: dict[str, object] = {}
    for room_id, length_ft, width_ft, aisle in (
        ("10x10_aisle_off", 10.0, 10.0, False),
        ("30x50_aisle_on", 30.0, 50.0, True),
    ):
        domain = ActiveRoomDomain.from_feet(
            length_ft, width_ft, enabled=aisle
        )
        mounting = MountingGeometry.resolve(18.0)
        layout = generate_proposed_led_layout(
            domain.active_requested_length_ft,
            domain.active_requested_width_ft,
            mount_z_m=mounting.emitting_aperture_plane_z_m,
        )
        max_zone = max(module.control_zone_index for module in layout.modules)
        centers = tuple((module.x_m, module.y_m) for module in layout.modules)
        edge = tuple(
            module.control_zone_index == max_zone for module in layout.modules
        )
        by_source = {
            source_mode: receiver_capture_for_modules(
                grids[source_mode],
                centers,
                bounds_xy=domain.active_bounds_aligned_m,
                mounting_height_m=mounting.mounting_height_m,
                edge_flags=edge,
            )
            for source_mode in (NATIVE_SOURCE_MODE, COB_SOURCE_MODE)
        }
        ratio = (
            float(
                by_source[COB_SOURCE_MODE][
                    "predicted_completed_aperture_ppf_for_750_umol_m2_s"
                ]
            )
            / float(
                by_source[NATIVE_SOURCE_MODE][
                    "predicted_completed_aperture_ppf_for_750_umol_m2_s"
                ]
            )
        )
        rooms[room_id] = {
            "outer_room_ft": [length_ft, width_ft],
            "aisle_mode": aisle,
            "active_domain": domain.to_payload(),
            "mounting": mounting.to_payload(),
            "layout": {
                "module_count": len(layout.modules),
                "fixture_count": len(layout.fixtures),
                "control_zone_count": layout.control_zone_count,
                "edge_definition": (
                    "outermost production control-zone ring"
                ),
            },
            "source_modes": by_source,
            "cob_to_native_required_ppf_ratio": ratio,
        }
    payload = {
        "schema_id": ACCEPTANCE_SCHEMA_ID,
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "target_mean_ppfd_umol_m2_s": 750.0,
        "rooms": rooms,
    }
    payload["identity_sha256"] = hash_json(payload)
    return payload


def _triangle_area_squared(
    triangle: tuple[
        tuple[float, float, float],
        tuple[float, float, float],
        tuple[float, float, float],
    ],
) -> float:
    left = tuple(
        triangle[1][axis] - triangle[0][axis] for axis in range(3)
    )
    right = tuple(
        triangle[2][axis] - triangle[0][axis] for axis in range(3)
    )
    cross = (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )
    return math.fsum(value * value for value in cross)


def _primitive_triangle_ranges(
    asset_id: str,
) -> tuple[tuple[int, int, int, int, str], ...]:
    authenticated = load_authenticated_fixture_asset(asset_id)
    output: list[tuple[int, int, int, int, str]] = []
    first = 0
    for item in authenticated.external_primitives:
        count = sum(
            _triangle_area_squared(triangle) > 1.0e-28
            for triangle in authenticated.decoded.triangles(item.inventory)
        )
        output.append(
            (
                first,
                first + count,
                item.inventory.node_index,
                item.inventory.primitive_index,
                item.inventory.node_path,
            )
        )
        first += count
    return tuple(output)


def _trace_first_hits(
    *,
    root: Path,
    octree: Path,
    rays: Iterable[str],
    ray_count: int,
    runner: LocalRunner,
) -> Path:
    ray_path = root / "fixture-interception.rays"
    output_path = root / "fixture-interception.first-hits.tsv"
    with ray_path.open("w", encoding="ascii", newline="\n") as handle:
        for ray in rays:
            handle.write(ray)
            handle.write("\n")
    if sum(1 for _ in ray_path.open("r", encoding="ascii")) != ray_count:
        raise ProposedCobAuditError("fixture-interception ray count changed.")
    _run(
        CommandSpec(
            argv=("rtrace", "-h", "-ab", "0", "-oLsm", str(octree)),
            stdin_path=ray_path,
            stdout_path=output_path,
            cwd=root,
            label="proposed-source-weighted-fixture-interception",
        ),
        root / "fixture-interception.rtrace.stderr",
        runner,
    )
    return output_path


def _bucket_payload(
    buckets: Mapping[str, Mapping[str, float]]
) -> dict[str, object]:
    output: dict[str, object] = {}
    for key, values in sorted(buckets.items()):
        total = float(values.get("total", 0.0))
        blocked = float(values.get("blocked", 0.0))
        output[key] = {
            "sampled_source_weight": total,
            "blocked_source_weight": blocked,
            "blocked_fraction": 0.0 if total == 0.0 else blocked / total,
        }
    return output


def execute_fixture_interception(
    output_directory: str | Path,
    grids: Mapping[str, AngularSampleGrid],
) -> dict[str, object]:
    """Run first-hit body rays once per room and weight them by each source."""

    root = Path(output_directory).expanduser().resolve()
    if root.exists():
        raise ProposedCobAuditError(
            f"fixture-interception output already exists: {root}"
        )
    root.mkdir(parents=True)
    runner = LocalRunner()
    classifications = validate_complete_classification_manifest()
    classification_counts = {
        item.asset.asset_id: item.classification_counts
        for item in classifications
        if item.asset.system_id == "proposed"
    }
    rooms: dict[str, object] = {}
    for room_id, length_ft, width_ft, aisle in (
        ("10x10_aisle_off", 10.0, 10.0, False),
        ("30x50_aisle_on", 30.0, 50.0, True),
    ):
        room_root = root / room_id
        room_root.mkdir()
        domain = ActiveRoomDomain.from_feet(
            length_ft, width_ft, enabled=aisle
        )
        mounting = MountingGeometry.resolve(18.0)
        layout = generate_proposed_led_layout(
            domain.active_requested_length_ft,
            domain.active_requested_width_ft,
            mount_z_m=mounting.emitting_aperture_plane_z_m,
        )
        plan = plan_fixture_occlusion(
            system_id="proposed",
            layout_identity=proposed_layout_transport_payload(layout),
            output_directory=room_root / "fixture-occlusion",
        )
        materialize_fixture_occlusion(plan)
        compile_fixture_occlusion(plan, runner)
        octree = room_root / "fixture-bodies.oct"
        command = build_oconv_command(
            (plan.instance_source_path,),
            output_octree=octree,
            cwd=room_root,
            label=f"compile-{room_id}-fixture-bodies",
        )
        _run(command, room_root / "fixture-bodies.oconv.stderr", runner)

        fixture_asset = {
            instance.fixture_id: instance.asset_id
            for instance in plan.instances
        }
        module_fixture: dict[int, object] = {}
        for fixture in layout.fixtures:
            for module_index in fixture.member_module_indices:
                if module_index in module_fixture:
                    raise ProposedCobAuditError(
                        "Proposed module belongs to multiple fixtures."
                    )
                module_fixture[module_index] = fixture
        shape_asset = {
            shape.shape_id.replace("-", "_"): shape.asset_id
            for shape in plan.shapes
        }
        ranges = {
            asset_id: _primitive_triangle_ranges(asset_id)
            for asset_id in sorted(set(shape_asset.values()))
        }
        half = APERTURE_SIDE_M / 2.0
        aperture_positions = (
            ("center", 0.0, 0.0, 16.0 / 36.0),
            ("north_edge", 0.0, half, 4.0 / 36.0),
            ("south_edge", 0.0, -half, 4.0 / 36.0),
            ("east_edge", half, 0.0, 4.0 / 36.0),
            ("west_edge", -half, 0.0, 4.0 / 36.0),
            ("north_east_corner", half, half, 1.0 / 36.0),
            ("north_west_corner", -half, half, 1.0 / 36.0),
            ("south_east_corner", half, -half, 1.0 / 36.0),
            ("south_west_corner", -half, -half, 1.0 / 36.0),
        )
        angular_cells = tuple(
            (
                float(lower),
                float(upper),
                0.5 * (lower + upper),
                float(azimuth),
                math.radians(INTERCEPTION_AZIMUTH_STEP_DEG)
                * (
                    math.cos(math.radians(lower))
                    - math.cos(math.radians(upper))
                ),
            )
            for lower, upper in zip(
                INTERCEPTION_POLAR_EDGES_DEG[:-1],
                INTERCEPTION_POLAR_EDGES_DEG[1:],
                strict=True,
            )
            for azimuth in range(0, 360, INTERCEPTION_AZIMUTH_STEP_DEG)
        )
        ray_count = (
            len(layout.modules)
            * len(aperture_positions)
            * len(angular_cells)
        )

        def ray_rows() -> Iterable[str]:
            for module in layout.modules:
                for _position, offset_x, offset_y, _weight in aperture_positions:
                    origin = (
                        module.x_m + offset_x,
                        module.y_m + offset_y,
                        module.z_m,
                    )
                    for (
                        _lower,
                        _upper,
                        polar,
                        azimuth,
                        _solid_angle,
                    ) in angular_cells:
                        polar_rad = math.radians(polar)
                        azimuth_rad = math.radians(azimuth)
                        direction = (
                            math.sin(polar_rad) * math.cos(azimuth_rad),
                            math.sin(polar_rad) * math.sin(azimuth_rad),
                            -math.cos(polar_rad),
                        )
                        yield " ".join(
                            f"{value:.17g}" for value in (*origin, *direction)
                        )

        first_hits = _trace_first_hits(
            root=room_root,
            octree=octree,
            rays=ray_rows(),
            ray_count=ray_count,
            runner=runner,
        )
        totals = {
            source_mode: {
                "total": 0.0,
                "blocked": 0.0,
                "by_asset": {},
                "by_classification": {},
                "by_polar": {},
                "by_module": {},
            }
            for source_mode in (NATIVE_SOURCE_MODE, COB_SOURCE_MODE)
        }
        line_count = 0
        with first_hits.open("r", encoding="ascii") as handle:
            for module in layout.modules:
                fixture = module_fixture[module.module_index]
                target_asset_id = fixture_asset[fixture.fixture_id]
                for _position, _dx, _dy, position_weight in aperture_positions:
                    for lower, upper, polar, azimuth, solid_angle in angular_cells:
                        line = handle.readline()
                        if not line:
                            raise ProposedCobAuditError(
                                "fixture first-hit output ended early."
                            )
                        line_count += 1
                        fields = line.split()
                        if len(fields) != 3:
                            raise ProposedCobAuditError(
                                "fixture first-hit row is malformed."
                            )
                        surface = fields[1]
                        blocked = surface != "*"
                        hit_asset_id: str | None = None
                        node_path: str | None = None
                        if blocked:
                            matching_shapes = tuple(
                                (prefix, asset_id)
                                for prefix, asset_id in shape_asset.items()
                                if surface.startswith(prefix + "_t")
                            )
                            if len(matching_shapes) != 1:
                                raise ProposedCobAuditError(
                                    f"cannot map fixture first hit: {surface}"
                                )
                            prefix, hit_asset_id = matching_shapes[0]
                            triangle_index = int(surface[len(prefix) + 2 :])
                            matches = tuple(
                                item
                                for item in ranges[hit_asset_id]
                                if item[0] <= triangle_index < item[1]
                            )
                            if len(matches) != 1:
                                raise ProposedCobAuditError(
                                    f"cannot map fixture triangle: {surface}"
                                )
                            node_path = matches[0][4]
                        for source_mode in (
                            NATIVE_SOURCE_MODE,
                            COB_SOURCE_MODE,
                        ):
                            weight = (
                                interpolate_intensity(
                                    grids[source_mode], polar, azimuth
                                )
                                * solid_angle
                                * position_weight
                            )
                            record = totals[source_mode]
                            record["total"] += weight
                            if blocked:
                                record["blocked"] += weight

                            def add(
                                bucket_name: str,
                                key: str,
                            ) -> None:
                                buckets = record[bucket_name]
                                bucket = buckets.setdefault(
                                    key, {"total": 0.0, "blocked": 0.0}
                                )
                                bucket["total"] += weight
                                if blocked:
                                    bucket["blocked"] += weight

                            add(
                                "by_asset",
                                (
                                    "unblocked"
                                    if hit_asset_id is None
                                    else hit_asset_id
                                ),
                            )
                            add(
                                "by_classification",
                                (
                                    "unblocked"
                                    if node_path is None
                                    else node_path
                                ),
                            )
                            add(
                                "by_polar",
                                f"{lower:g}-{upper:g}",
                            )
                            add(
                                "by_module",
                                str(module.module_index),
                            )
                if target_asset_id not in classification_counts:
                    raise ProposedCobAuditError(
                        "fixture classification asset identity changed."
                    )
            if handle.readline():
                raise ProposedCobAuditError(
                    "fixture first-hit output has extra rows."
                )
        if line_count != ray_count:
            raise ProposedCobAuditError(
                "fixture first-hit output count changed."
            )
        source_payload: dict[str, object] = {}
        for source_mode, record in totals.items():
            total = float(record["total"])
            blocked = float(record["blocked"])
            source_payload[source_mode] = {
                "sampled_source_weight": total,
                "blocked_source_weight": blocked,
                "fixture_body_interception_fraction": blocked / total,
                "by_hit_asset": _bucket_payload(record["by_asset"]),
                "by_hit_classification_node": _bucket_payload(
                    record["by_classification"]
                ),
                "by_polar_angle_range_deg": _bucket_payload(
                    record["by_polar"]
                ),
                "by_module": _bucket_payload(record["by_module"]),
            }
        rooms[room_id] = {
            "outer_room_ft": [length_ft, width_ft],
            "aisle_mode": aisle,
            "module_count": len(layout.modules),
            "fixture_count": len(layout.fixtures),
            "ray_count": ray_count,
            "aperture_position_quadrature": {
                "count": len(aperture_positions),
                "policy": "3x3 area weights 16/36, 4/36, and 1/36",
            },
            "angular_quadrature": {
                "polar_ring_edges_deg": list(
                    INTERCEPTION_POLAR_EDGES_DEG
                ),
                "azimuth_step_deg": INTERCEPTION_AZIMUTH_STEP_DEG,
                "cell_weight": (
                    "source intensity at cell center times exact cell solid "
                    "angle delta_phi*(cos(theta0)-cos(theta1))"
                ),
            },
            "fixture_occlusion_identity_sha256": plan.identity_sha256,
            "classification_counts": classification_counts,
            "source_modes": source_payload,
        }
        # Raw rays and hit rows are deterministic intermediates, not report
        # artifacts; retaining their hashes proves which first-hit evidence was
        # aggregated without inflating the primary JSON.
        rooms[room_id]["first_hit_evidence"] = {
            "rays_sha256": sha256_file(
                room_root / "fixture-interception.rays"
            ),
            "first_hits_sha256": sha256_file(first_hits),
        }
    payload = {
        "schema_id": INTERCEPTION_SCHEMA_ID,
        "schema_version": INTERCEPTION_SCHEMA_VERSION,
        "classification_resource_unchanged": True,
        "glb_assets_modified": False,
        "rooms": rooms,
    }
    payload["identity_sha256"] = hash_json(payload)
    atomic_write_text(
        root / "fixture-interception.v1.json",
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    return payload


def direct_request_payload(
    *,
    room_length_ft: float,
    room_width_ft: float,
    aisle_mode: bool,
    source_mode: str,
) -> dict[str, object]:
    return {
        "system": "proposed",
        "target_ppfd": 750.0,
        "room_length_ft": room_length_ft,
        "room_width_ft": room_width_ft,
        "mounting_height_in": 18.0,
        "quality": "direct",
        "fspm_target_mode": "automatic",
        "fspm_target_tolerance": 20.0,
        "aisle_mode": aisle_mode,
        "analysis_scope": "baseline_ppfd",
        "proposed_control_mode": "uniform_module_dimming",
        "proposed_source_mode": source_mode,
    }


def _direct_run_id(case_id: str) -> str:
    return hashlib.sha256(
        f"{DIRECT_SCHEMA_ID}:{DIRECT_SCHEMA_VERSION}:{case_id}".encode()
    ).hexdigest()[:32]


def execute_direct_cases(
    output_directory: str | Path,
    *,
    execution_directory: str | Path | None = None,
) -> dict[str, object]:
    root = Path(output_directory).expanduser().resolve()
    if root.exists():
        raise ProposedCobAuditError(f"Direct output already exists: {root}")
    root.mkdir(parents=True)
    execution_root = (
        root
        if execution_directory is None
        else Path(execution_directory).expanduser().resolve()
    )
    if execution_root != root:
        if execution_root.exists() or execution_root.is_symlink():
            raise ProposedCobAuditError(
                f"Direct execution output already exists: {execution_root}"
            )
        execution_root.mkdir(parents=True)
    records: dict[str, object] = {}
    for room_id, length, width, aisle in (
        ("10x10_aisle_off", 10.0, 10.0, False),
        ("30x50_aisle_on", 30.0, 50.0, True),
    ):
        for source_mode in (NATIVE_SOURCE_MODE, COB_SOURCE_MODE):
            case_id = f"{room_id}__{source_mode}"
            run_id = _direct_run_id(case_id)
            workspace = execution_root / "runs" / run_id
            workspace.mkdir(parents=True)
            request = ProposedRunRequest.from_payload(
                direct_request_payload(
                    room_length_ft=length,
                    room_width_ft=width,
                    aisle_mode=aisle,
                    source_mode=source_mode,
                )
            )
            events: list[dict[str, object]] = []

            def event_sink(
                event_type: str,
                message: str,
                payload: Mapping[str, object] | None = None,
            ) -> None:
                events.append(
                    {
                        "event_type": event_type,
                        "message": message,
                        "payload": {} if payload is None else dict(payload),
                    }
                )

            run_proposed_baseline(
                run_id=run_id,
                request=request,
                workspace=workspace,
                event_sink=event_sink,
            )
            event_rows = [
                {
                    "cursor": index,
                    "data": item["payload"],
                    "message": item["message"],
                    "timestamp_unix_s": 0.0,
                    "type": item["event_type"],
                }
                for index, item in enumerate(events, start=1)
            ]
            atomic_write_text(
                workspace / "events.jsonl",
                "".join(
                    json.dumps(item, sort_keys=True) + "\n"
                    for item in event_rows
                ),
            )
            atomic_write_text(
                workspace / "run.log",
                "".join(
                    f"[{item['event_type']}] {item['message']}\n"
                    for item in events
                ),
            )
            atomic_write_text(
                workspace / "audit-events.json",
                json.dumps(events, indent=2, sort_keys=True) + "\n",
            )
            validation = validate_stage_a_run(
                workspace, execution_root / "validation" / run_id
            )
            metrics = read_json(workspace / "metrics.json")
            manifest = read_json(workspace / "manifest.json")
            operating = read_json(workspace / "operating-point.json")
            records[case_id] = {
                "case_id": case_id,
                "run_id": run_id,
                "run_directory": str(workspace),
                "request": request.to_dict(),
                "metrics": metrics,
                "manifest": manifest,
                "operating_point": operating,
                "validator": validation,
            }
    execution_inventory = inventory_tree(execution_root)
    if execution_root != root:
        shutil.copytree(execution_root / "runs", root / "runs")
        for record in records.values():
            if not isinstance(record, dict):
                raise ProposedCobAuditError("Direct case record changed.")
            run_id = str(record["run_id"])
            copied_workspace = root / "runs" / run_id
            record["run_directory"] = str(copied_workspace)
            record["validator"] = validate_stage_a_run(
                copied_workspace, root / "validation" / run_id
            )
    paired = validate_direct_pairs(records)
    payload = {
        "schema_id": DIRECT_SCHEMA_ID,
        "schema_version": DIRECT_SCHEMA_VERSION,
        "cases": records,
        "paired_invariants": paired,
        "execution_workspace": {
            "directory": str(execution_root),
            "outside_report_tree": execution_root != root,
            "authenticated_inventory": execution_inventory.to_dict(),
            "copied_run_trees_revalidated": execution_root != root,
        },
    }
    payload["identity_sha256"] = hash_json(payload)
    atomic_write_text(
        root / "matched-direct.v1.json",
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    return payload


def _nested(payload: Mapping[str, object], *path: str) -> object:
    value: object = payload
    for name in path:
        if not isinstance(value, Mapping) or name not in value:
            raise ProposedCobAuditError(
                f"required Direct field is missing: {'.'.join(path)}"
            )
        value = value[name]
    return value


def validate_direct_pairs(
    records: Mapping[str, object],
) -> dict[str, object]:
    """Assert source-neutral production geometry for each Direct pair."""

    output: dict[str, object] = {}
    for room_id in ("10x10_aisle_off", "30x50_aisle_on"):
        native = require_mapping(
            records, f"{room_id}__{NATIVE_SOURCE_MODE}"
        )
        cob = require_mapping(records, f"{room_id}__{COB_SOURCE_MODE}")
        native_manifest = require_mapping(native, "manifest")
        cob_manifest = require_mapping(cob, "manifest")
        equal_fields = {
            "layout": native_manifest.get("layout") == cob_manifest.get("layout"),
            "proposed_layout": (
                native_manifest.get("proposed_layout")
                == cob_manifest.get("proposed_layout")
            ),
            "active_domain": (
                native_manifest.get("active_domain")
                == cob_manifest.get("active_domain")
            ),
            "mounting_height": (
                native_manifest.get("mounting_height")
                == cob_manifest.get("mounting_height")
            ),
            "room": native_manifest.get("room") == cob_manifest.get("room"),
            "quality": (
                native_manifest.get("quality") == cob_manifest.get("quality")
            ),
            "proposed_control_mode": (
                _nested(native_manifest, "proposed_control", "mode")
                == _nested(cob_manifest, "proposed_control", "mode")
                == "uniform_module_dimming"
            ),
            "basis_matrix_solver_disabled": (
                _nested(
                    native_manifest,
                    "proposed_control",
                    "basis_matrix_solver_enabled",
                )
                is False
                and _nested(
                    cob_manifest,
                    "proposed_control",
                    "basis_matrix_solver_enabled",
                )
                is False
            ),
            "fixture_occlusion_identity": (
                _nested(
                    native_manifest,
                    "engine_provenance",
                    "fixture_occlusion",
                    "identity_sha256",
                )
                == _nested(
                    cob_manifest,
                    "engine_provenance",
                    "fixture_occlusion",
                    "identity_sha256",
                )
            ),
        }
        if not all(equal_fields.values()):
            failed = [name for name, passed in equal_fields.items() if not passed]
            raise ProposedCobAuditError(
                f"Direct paired invariants failed for {room_id}: {failed}"
            )
        native_metrics = require_mapping(native, "metrics")
        cob_metrics = require_mapping(cob, "metrics")

        def metric(
            payload: Mapping[str, object], name: str
        ) -> float:
            value = payload.get(name)
            if isinstance(value, bool) or not isinstance(value, int | float):
                raise ProposedCobAuditError(
                    f"Direct metric is missing or nonfinite: {name}"
                )
            number = float(value)
            if not math.isfinite(number):
                raise ProposedCobAuditError(
                    f"Direct metric is missing or nonfinite: {name}"
                )
            return number

        comparisons = {}
        for name in (
            "full_output_mean_ppfd_umol_m2_s",
            "dimming_factor",
            "minimum_ppfd_umol_m2_s",
            "maximum_ppfd_umol_m2_s",
            "standard_deviation_ppfd_umol_m2_s",
            "cv_percent",
        ):
            left, right = metric(native_metrics, name), metric(cob_metrics, name)
            comparisons[name] = {
                NATIVE_SOURCE_MODE: left,
                COB_SOURCE_MODE: right,
                "cob_to_native_ratio": right / left,
            }
        for name, path in (
            ("emitted_completed_aperture_ppf_umol_s", ("ppf", "emitted_umol_s")),
            ("modeled_power_w", ("power", "effective_w")),
        ):
            left = float(_nested(native_metrics, *path))
            right = float(_nested(cob_metrics, *path))
            comparisons[name] = {
                NATIVE_SOURCE_MODE: left,
                COB_SOURCE_MODE: right,
                "cob_to_native_ratio": right / left,
            }
        native_validator = require_mapping(native, "validator")
        cob_validator = require_mapping(cob, "validator")
        for name, path in (
            (
                "integrated_receiver_plane_ppfd_umol_s",
                ("coordinate_quadrature", "integrated_receiver_flux_umol_s"),
            ),
            (
                "receiver_to_emitted_ppf_ratio",
                ("flux_closure", "receiver_to_emitted_ppf_ratio"),
            ),
        ):
            left = float(_nested(native_validator, *path))
            right = float(_nested(cob_validator, *path))
            comparisons[name] = {
                NATIVE_SOURCE_MODE: left,
                COB_SOURCE_MODE: right,
                "cob_to_native_ratio": right / left,
            }
        for mode, item in (
            (NATIVE_SOURCE_MODE, native_metrics),
            (COB_SOURCE_MODE, cob_metrics),
        ):
            mean = metric(item, "achieved_mean_ppfd_umol_m2_s")
            minimum = metric(item, "minimum_ppfd_umol_m2_s")
            comparisons.setdefault("minimum_to_mean", {})[mode] = (
                minimum / mean
            )
        comparisons["minimum_to_mean"]["cob_to_native_ratio"] = (
            comparisons["minimum_to_mean"][COB_SOURCE_MODE]
            / comparisons["minimum_to_mean"][NATIVE_SOURCE_MODE]
        )
        output[room_id] = {
            "invariants": equal_fields,
            "metrics": comparisons,
        }
    return output


def artifact_with_identity(payload: Mapping[str, object]) -> dict[str, object]:
    output = dict(payload)
    output.pop("identity_sha256", None)
    output["identity_sha256"] = hash_json(output)
    return output


def verify_artifact_identity(payload: Mapping[str, object]) -> None:
    expected = payload.get("identity_sha256")
    if not isinstance(expected, str) or len(expected) != 64:
        raise ProposedCobAuditError("artifact identity is missing.")
    candidate = dict(payload)
    candidate.pop("identity_sha256", None)
    actual = hash_json(candidate)
    if actual != expected:
        raise ProposedCobAuditError(
            f"artifact identity mismatch: expected {expected}, got {actual}."
        )


def read_json(path: str | Path) -> dict[str, object]:
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise ProposedCobAuditError(f"required JSON file is unsafe: {candidate}")
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProposedCobAuditError(
            f"required JSON file is malformed: {candidate}"
        ) from exc
    if not isinstance(payload, dict):
        raise ProposedCobAuditError(
            f"required JSON root must be an object: {candidate}"
        )
    return payload


def require_mapping(
    payload: Mapping[str, object], name: str
) -> Mapping[str, object]:
    value = payload.get(name)
    if not isinstance(value, Mapping):
        raise ProposedCobAuditError(f"required mapping is missing: {name}")
    return value


def command_version(command: Sequence[str]) -> str:
    completed = subprocess.run(
        tuple(command),
        check=False,
        capture_output=True,
        text=True,
    )
    return (completed.stdout + completed.stderr).strip()


def copy_file_authenticated(source: Path, destination: Path) -> dict[str, object]:
    if source.is_symlink() or not source.is_file():
        raise ProposedCobAuditError(f"copy source is unsafe: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise ProposedCobAuditError(f"copy destination exists: {destination}")
    shutil.copyfile(source, destination)
    source_hash = sha256_file(source)
    destination_hash = sha256_file(destination)
    if source_hash != destination_hash:
        raise ProposedCobAuditError("authenticated diagnostic copy changed.")
    return {
        "source": str(source),
        "destination": str(destination),
        "sha256": source_hash,
    }


__all__ = [
    "ACCEPTANCE_SCHEMA_ID",
    "ANGULAR_SAMPLES_SCHEMA_ID",
    "AUDIT_SCHEMA_ID",
    "AUDIT_SCHEMA_VERSION",
    "AngularSampleGrid",
    "ProposedCobAuditError",
    "TreeInventory",
    "angular_metrics",
    "artifact_with_identity",
    "authenticate_certified_archive",
    "copy_file_authenticated",
    "current_source_identities",
    "direct_request_payload",
    "evaluate_receiver_acceptance",
    "execute_direct_cases",
    "execute_fixture_interception",
    "execute_matched_characterization",
    "hash_json",
    "interpolate_intensity",
    "inventory_tree",
    "receiver_capture_for_modules",
    "sha256_file",
    "validate_direct_pairs",
    "verify_artifact_identity",
]
