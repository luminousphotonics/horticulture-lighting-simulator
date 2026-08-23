"""Non-executing plans for repeated isolated scalar rtrace basis columns."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Sequence

from fspm_optics.fixtures.occlusion import (
    FixtureOcclusionPlan,
    plan_fixture_occlusion,
    proposed_layout_transport_payload,
)
from fspm_optics.fixtures.smd.module_profile import DEFAULT_SMD_MODULE_PROFILE
from fspm_optics.fixtures.smd.positions import SmdLayout
from fspm_optics.fixtures.smd.power_schedule import (
    ModulePowerSchedule,
    build_optimized_module_schedule,
)
from fspm_optics.fixtures.smd.radiance_writer import (
    SmdEmitterAssumptions,
    SmdRadianceDocument,
    build_smd_radiance_document,
    smd_source_identity_sha256,
)
from fspm_optics.radiance.commands import (
    LOCAL_DEFAULT_NTHREADS,
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_command,
)
from fspm_optics.radiance.options import (
    radiance_options as build_radiance_options,
    replace_radiance_option_value,
)
from fspm_optics.geometry.room import PRODUCTION_ROOM_MODEL_IDENTITY_SHA256

from .manifest import BasisManifest


@dataclass(frozen=True, slots=True)
class BasisColumnPlan:
    """One independently compiled and traced control-zone basis column."""

    control_zone_index: int
    control_zone_coefficients: tuple[float, ...]
    power_schedule: ModulePowerSchedule
    emitter_document: SmdRadianceDocument
    emitter_source_path: Path
    octree_path: Path
    rgb_output_path: Path
    ambient_cache_path: Path | None
    oconv_command: CommandSpec
    rtrace_command: CommandSpec


@dataclass(frozen=True, slots=True)
class BasisGenerationPlan:
    """Complete local basis plan with no execution behavior."""

    manifest: BasisManifest
    columns: tuple[BasisColumnPlan, ...]
    room_source_path: Path
    sensor_input_path: Path
    output_directory: Path
    fixture_occlusion: FixtureOcclusionPlan
    source_variant_cal_path: Path | None = None
    source_variant_cal_text: str | None = None
    source_angular_data_path: Path | None = None
    source_angular_data_text: str | None = None

    @property
    def control_zone_count(self) -> int:
        return len(self.columns)


def plan_isolated_rtrace_basis(
    *,
    layout: SmdLayout,
    sensor_count: int,
    room_height_m: float,
    room_source_path: str | Path,
    sensor_input_path: str | Path,
    output_directory: str | Path,
    reference_watts: float = 1.0,
    radiance_options: Sequence[str] | None = None,
    nthreads: int = LOCAL_DEFAULT_NTHREADS,
    use_ambient_cache: bool = True,
    emitter_assumptions: SmdEmitterAssumptions = SmdEmitterAssumptions(),
    fixture_occlusion: FixtureOcclusionPlan | None = None,
) -> BasisGenerationPlan:
    """Plan one isolated fixture-only trace per SMD control zone."""

    if isinstance(sensor_count, bool) or not isinstance(sensor_count, int) or sensor_count <= 0:
        raise ValueError("sensor_count must be a positive integer.")
    if (
        isinstance(room_height_m, bool)
        or not isinstance(room_height_m, int | float)
        or not math.isfinite(float(room_height_m))
        or float(room_height_m) <= 0.0
    ):
        raise ValueError("room_height_m must be finite and positive.")
    if (
        isinstance(reference_watts, bool)
        or not isinstance(reference_watts, int | float)
        or not math.isfinite(float(reference_watts))
        or float(reference_watts) <= 0.0
    ):
        raise ValueError("reference_watts must be finite and positive.")
    if isinstance(nthreads, bool) or not isinstance(nthreads, int) or nthreads <= 0:
        raise ValueError("nthreads must be a positive integer.")
    room_path = _required_path_text(room_source_path, "room_source_path")
    sensor_path = _required_path_text(sensor_input_path, "sensor_input_path")
    output_dir = _required_path_text(output_directory, "output_directory")
    body_plan = fixture_occlusion or plan_fixture_occlusion(
        system_id="proposed",
        layout_identity=proposed_layout_transport_payload(layout),
        output_directory=output_dir / "fixture_occlusion",
    )
    base_options = tuple(
        build_radiance_options("standard")
        if radiance_options is None
        else (str(value) for value in radiance_options)
    )
    if any(not option for option in base_options):
        raise ValueError("radiance_options must contain non-empty tokens.")

    columns: list[BasisColumnPlan] = []
    for control_zone_index in layout.control_zone_indices:
        coefficients = tuple(
            float(reference_watts) if index == control_zone_index else 0.0
            for index in layout.control_zone_indices
        )
        power_schedule = build_optimized_module_schedule(layout, coefficients)
        emitter_document = build_smd_radiance_document(
            layout,
            power_schedule,
            assumptions=emitter_assumptions,
        )
        source_identity = smd_source_identity_sha256(
            emitter_document.metadata
        )
        stem = f"basis_control_zone_{control_zone_index:03d}"
        emitter_path = output_dir / f"{stem}.rad"
        octree_path = output_dir / f"{stem}.oct"
        rgb_path = output_dir / f"{stem}.rgb"
        ambient_path = (
            output_dir
            / (
                f"{stem}.{layout.proposed_layout_mode.value}"
                f".{layout.proposed_ring_mode.value}"
                + (
                    ".cob_source_shape_surrogate"
                    if emitter_assumptions.source_mode
                    == "cob_source_shape_surrogate"
                    else ""
                )
                + f".{source_identity[:16]}"
                + f".{body_plan.identity_sha256[:16]}"
                + f".{PRODUCTION_ROOM_MODEL_IDENTITY_SHA256[:16]}.amb"
            )
            if use_ambient_cache
            else None
        )
        column_options = list(base_options)
        if ambient_path is not None:
            column_options = replace_radiance_option_value(
                column_options, "-af", str(ambient_path)
            )
        raw_oconv = build_oconv_command(
            (
                room_path,
                emitter_path,
                body_plan.instance_source_path,
            ),
            output_octree=octree_path,
            cwd=output_dir,
            label=f"compile_{stem}",
        )
        raw_rtrace = build_baseline_rtrace_command(
            octree=octree_path,
            receiver_input=sensor_path,
            rgb_output=rgb_path,
            options=column_options,
            nthreads=nthreads,
            cwd=output_dir,
        )
        rtrace_command = CommandSpec(
            argv=raw_rtrace.argv,
            stdin_path=raw_rtrace.stdin_path,
            stdout_path=raw_rtrace.stdout_path,
            stdout_mode=raw_rtrace.stdout_mode,
            cwd=raw_rtrace.cwd,
            env=raw_rtrace.env,
            label=f"trace_{stem}",
        )
        columns.append(
            BasisColumnPlan(
                control_zone_index=control_zone_index,
                control_zone_coefficients=coefficients,
                power_schedule=power_schedule,
                emitter_document=emitter_document,
                emitter_source_path=emitter_path,
                octree_path=octree_path,
                rgb_output_path=rgb_path,
                ambient_cache_path=ambient_path,
                oconv_command=raw_oconv,
                rtrace_command=rtrace_command,
            )
        )

    cal_texts = {
        column.emitter_document.source_variant_cal_text for column in columns
    }
    if len(cal_texts) != 1:
        raise ValueError(
            "every control-zone column must use the same angular CAL content."
        )
    source_variant_cal_text = cal_texts.pop()
    source_variant_cal_path = (
        output_dir / "smd_source_variant.cal"
        if source_variant_cal_text is not None
        else None
    )
    angular_filenames = {
        column.emitter_document.source_angular_data_filename for column in columns
    }
    angular_texts = {
        column.emitter_document.source_angular_data_text for column in columns
    }
    if len(angular_filenames) != 1 or len(angular_texts) != 1:
        raise ValueError(
            "every control-zone column must use the same angular data artifact."
        )
    angular_filename = angular_filenames.pop()
    source_angular_data_text = angular_texts.pop()
    source_angular_data_path = (
        output_dir / angular_filename
        if angular_filename is not None and source_angular_data_text is not None
        else None
    )
    metadata = columns[0].emitter_document.metadata
    source_identity = smd_source_identity_sha256(metadata)
    manifest = BasisManifest(
        room_length_m=layout.room_length_m,
        room_width_m=layout.room_width_m,
        room_height_m=float(room_height_m),
        sensor_count=sensor_count,
        control_zone_count=layout.control_zone_count,
        matrix_shape=(sensor_count, layout.control_zone_count),
        radiance_options=base_options,
        nthreads=nthreads,
        reference_watts=float(reference_watts),
        layout_module_count=len(layout.modules),
        module_profile_id=DEFAULT_SMD_MODULE_PROFILE.profile_id,
        proposed_layout_mode=layout.proposed_layout_mode,
        proposed_ring_mode=layout.proposed_ring_mode,
        module_pattern_id=layout.module_pattern_id,
        fixture_policy_id=layout.fixture_policy_id,
        mechanical_envelope_id=layout.mechanical_envelope_id,
        module_footprint_x_m=layout.module_footprint_x_m,
        module_footprint_y_m=layout.module_footprint_y_m,
        fixture_asset_set_id=layout.fixture_asset_set_id,
        ambient_cache_policy=(
            "per_control_zone" if use_ambient_cache else "disabled"
        ),
        fixture_occlusion_identity=body_plan.identity_sha256,
        fixture_occlusion_manifest_sha256=(
            body_plan.classification_manifest_sha256
        ),
        proposed_source_mode=metadata.source_mode,
        proposed_source_identity_sha256=source_identity,
        angular_data_sha256=metadata.normalized_angular_dat_sha256,
        source_classification=metadata.source_classification,
        emitter_shape=metadata.emitter_shape,
        emitter_area_per_module_m2=metadata.emitter_area_per_module_m2,
        emitter_diameter_m=metadata.emitter_diameter_m,
        authenticated_ies_sha256=metadata.authenticated_ies_sha256,
        normalized_angular_identity_sha256=(
            metadata.normalized_angular_identity_sha256
        ),
        angular_normalization_policy=metadata.angular_normalization_policy,
        completed_aperture_characterization_identity_sha256=(
            metadata.completed_aperture_characterization_identity_sha256
        ),
        controlled_spd_identity_sha256=(
            metadata.controlled_spd_identity_sha256
        ),
    )
    return BasisGenerationPlan(
        manifest=manifest,
        columns=tuple(columns),
        room_source_path=room_path,
        sensor_input_path=sensor_path,
        output_directory=output_dir,
        fixture_occlusion=body_plan,
        source_variant_cal_path=source_variant_cal_path,
        source_variant_cal_text=source_variant_cal_text,
        source_angular_data_path=source_angular_data_path,
        source_angular_data_text=source_angular_data_text,
    )


def _required_path_text(value: str | Path, name: str) -> Path:
    if not str(value):
        raise ValueError(f"{name} must be non-empty.")
    return Path(value)
