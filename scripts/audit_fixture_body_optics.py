#!/usr/bin/env python3
"""Run the isolated fixture-body geometry/material causal audit."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
from typing import Any, Mapping, Sequence

import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from fspm_optics.diagnostics.fixture_body_optics_audit import (  # noqa: E402
    AUDIT_SCHEMA_ID,
    AUDIT_SCHEMA_VERSION,
    COARSE_SAMPLING,
    DIAGNOSTIC_MATERIALS,
    FINE_SAMPLING,
    ZERO_ABSORBER,
    FixtureBodyOpticsAuditError,
    audit_authenticated_asset_geometry,
    build_diagnostic_trace_commands,
    canonical_json_text,
    compute_field_statistics,
    convergence_statistics,
    difference_statistics,
    execute_diagnostic_trace,
    field_csv_text,
    geometry_variant_instance_text,
    materialize_and_compile_diagnostic_body_sources,
    plan_diagnostic_body_sources,
    preflight_new_artifact_root,
    sha256_file,
    verify_sha256_inventory,
    write_projected_silhouette_svgs,
    write_sha256_inventory,
)
from fspm_optics.fixtures.conventional_led import (  # noqa: E402
    DEFAULT_MOUNT_HEIGHT_M,
    PRACTICAL_LAYOUT_POLICY,
)
from fspm_optics.fixtures.occlusion.manifest import (  # noqa: E402
    classification_manifest_sha256,
)
from fspm_optics.fixtures.proposed_cob import COB_SOURCE_MODE  # noqa: E402
from fspm_optics.fixtures.smd.positions import (  # noqa: E402
    generate_proposed_led_layout,
)
from fspm_optics.fixtures.smd.radiance_writer import (  # noqa: E402
    SmdEmitterAssumptions,
)
from fspm_optics.geometry.room import FEET_TO_METERS  # noqa: E402
from fspm_optics.geometry.sensor_grid import generate_sensor_points  # noqa: E402
from fspm_optics.radiance.executables import resolve_executable  # noqa: E402
from fspm_optics.radiance.runner import LocalRunner  # noqa: E402
from fspm_optics.radiance.versioning import (  # noqa: E402
    discover_radiance_installation,
    probe_radiance_version,
)
from fspm_optics.transport.conventional_scalar import (  # noqa: E402
    ConventionalScalarTransportRequest,
    execute_conventional_scalar_transport,
)
from fspm_optics.transport.proposed_uniform import (  # noqa: E402
    materialize_uniform_proposed_stage_a,
    plan_uniform_proposed_stage_a,
)

DEFAULT_OUTPUT = Path(
    "/home/austin/Documents/fspm-fixture-body-optics-audit-v1-20260729"
)
AMBIENT_DEPTHS = (0, 1, 2, 5)
PROPOSED_GEOMETRY_VARIANTS = (
    "no_bodies",
    "all_bodies",
    "centerpiece_only",
    "linear2_only",
)


def execute_audit(
    output_root: str | Path,
    *,
    trace_timeout_s: float | None = None,
) -> dict[str, object]:
    root = preflight_new_artifact_root(output_root)
    root.mkdir()
    runner = LocalRunner()
    radiance = discover_radiance_installation()
    ies2rad = resolve_executable("ies2rad", label="ies2rad")
    git = _git_identity()

    inputs = root / "inputs"
    inputs.mkdir()
    proposed_input = inputs / "proposed"
    proposed_layout = generate_proposed_led_layout(10.0, 10.0)
    proposed_plan = plan_uniform_proposed_stage_a(
        layout=proposed_layout,
        output_directory=proposed_input,
        radiance_options=("-ab", "0"),
        nthreads=1,
        use_ambient_cache=False,
        emitter_assumptions=SmdEmitterAssumptions(source_mode=COB_SOURCE_MODE),
    )
    materialize_uniform_proposed_stage_a(proposed_plan)

    # This one public direct execution stages and authenticates the Conventional
    # ies2rad source boundary.  It is diagnostic input preparation, not a web or
    # production Quality run.  The exact causal traces below use their own flags.
    conventional_seed_request = ConventionalScalarTransportRequest(
        workspace=inputs / "conventional_seed",
        room_length_m=10.0 * FEET_TO_METERS,
        room_width_m=10.0 * FEET_TO_METERS,
        layout_policy=PRACTICAL_LAYOUT_POLICY,
        reference_plane_z_m=proposed_plan.sensor_grid_spec.canopy_height_m,
        mount_height_m=DEFAULT_MOUNT_HEIGHT_M,
        quality_profile="direct",
        threads=1,
    )
    conventional_seed = execute_conventional_scalar_transport(
        conventional_seed_request,
        runner=runner,
    )
    conventional_plan = conventional_seed.plan

    geometry_root = root / "geometry"
    geometry_root.mkdir()
    geometry_records = {
        asset_id: audit_authenticated_asset_geometry(asset_id)
        for asset_id in (
            "proposed-centerpiece-v1",
            "proposed-linear2-v1",
            "conventional-led-8-bar-v1",
        )
    }
    silhouette_records: list[dict[str, object]] = []
    for asset_id in geometry_records:
        silhouette_records.extend(
            write_projected_silhouette_svgs(
                asset_id, geometry_root / "silhouettes"
            )
        )
    geometry_inventory = {
        "schema_id": f"{AUDIT_SCHEMA_ID}.geometry-inventory",
        "schema_version": AUDIT_SCHEMA_VERSION,
        "classification_manifest_sha256": classification_manifest_sha256(),
        "assets": geometry_records,
        "transformed_shape_inventory": {
            "proposed": _shape_inventory(proposed_plan.fixture_occlusion),
            "conventional": _shape_inventory(conventional_plan.fixture_occlusion),
        },
        "instance_inventory": {
            "proposed": _instance_inventory(proposed_plan.fixture_occlusion),
            "conventional": _instance_inventory(conventional_plan.fixture_occlusion),
        },
        "silhouettes": silhouette_records,
    }
    _write_json(geometry_root / "geometry-and-classification-inventory.v1.json", geometry_inventory)

    material_root = root / "diagnostic-body-materials"
    material_root.mkdir()
    body_sources: dict[tuple[str, str], Any] = {}
    body_compile_records: list[dict[str, object]] = []
    for system_id, occlusion_plan in (
        ("proposed", proposed_plan.fixture_occlusion),
        ("conventional", conventional_plan.fixture_occlusion),
    ):
        for material in DIAGNOSTIC_MATERIALS:
            diagnostic = plan_diagnostic_body_sources(
                occlusion_plan,
                material=material,
                output_directory=material_root / system_id / material.material_id,
                oconv_bin=radiance.oconv.path,
            )
            records = materialize_and_compile_diagnostic_body_sources(
                occlusion_plan, diagnostic, runner
            )
            body_sources[(system_id, material.material_id)] = diagnostic
            body_compile_records.append(
                {
                    "system_id": system_id,
                    "material": material.to_payload(),
                    "shapes": list(records),
                }
            )
    _write_json(
        material_root / "compiled-body-source-inventory.v1.json",
        {
            "schema_id": f"{AUDIT_SCHEMA_ID}.compiled-body-sources",
            "schema_version": AUDIT_SCHEMA_VERSION,
            "records": body_compile_records,
        },
    )

    scene_inputs = root / "diagnostic-scene-inputs"
    scene_inputs.mkdir()
    instance_paths: dict[tuple[str, str, str], Path] = {}
    for system_id, occlusion_plan, variants in (
        ("proposed", proposed_plan.fixture_occlusion, PROPOSED_GEOMETRY_VARIANTS),
        (
            "conventional",
            conventional_plan.fixture_occlusion,
            ("no_bodies", "all_bodies"),
        ),
    ):
        for material in DIAGNOSTIC_MATERIALS:
            diagnostic = body_sources[(system_id, material.material_id)]
            for variant in variants:
                path = scene_inputs / system_id / material.material_id / f"{variant}.rad"
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    geometry_variant_instance_text(
                        occlusion_plan, diagnostic, variant
                    ),
                    encoding="utf-8",
                )
                instance_paths[(system_id, material.material_id, variant)] = path

    proposed_coordinates = np.asarray(
        [
            (point.x_m, point.y_m, point.z_m)
            for point in generate_sensor_points(proposed_plan.sensor_grid_spec)
        ],
        dtype=np.float64,
    )
    conventional_coordinates = np.asarray(
        [
            (point.x_m, point.y_m, point.z_m)
            for point in conventional_plan.sensor_points
        ],
        dtype=np.float64,
    )
    systems = {
        "proposed": {
            "occlusion": proposed_plan.fixture_occlusion,
            "scene_sources": (
                proposed_plan.paths.room,
                proposed_plan.paths.source,
            ),
            "receiver": proposed_plan.paths.sensors,
            "cwd": proposed_input,
            "coordinates": proposed_coordinates,
            "grid": proposed_plan.sensor_grid_spec,
        },
        "conventional": {
            "occlusion": conventional_plan.fixture_occlusion,
            "scene_sources": (
                conventional_plan.paths.room_rad,
                conventional_plan.paths.shared_angular_rad,
                conventional_plan.paths.aperture_rad,
            ),
            "receiver": conventional_plan.paths.sensor_rays,
            "cwd": conventional_plan.paths.artifact_root,
            "coordinates": conventional_coordinates,
            "grid": conventional_plan.sensor_grid_spec,
        },
    }

    case_root = root / "transport-cases"
    case_root.mkdir()
    case_records: dict[str, dict[str, object]] = {}
    fields: dict[str, np.ndarray] = {}

    def run_case(
        *,
        system_id: str,
        material_id: str,
        geometry_variant: str,
        ambient_bounces: int,
        sampling_id: str = "fine",
    ) -> str:
        sampling = FINE_SAMPLING if sampling_id == "fine" else COARSE_SAMPLING
        case_id = (
            f"{system_id}__{material_id}__{geometry_variant}"
            f"__ab{ambient_bounces}__{sampling_id}"
        )
        if case_id in case_records:
            return case_id
        config = systems[system_id]
        directory = case_root / case_id
        body_path = instance_paths[
            (system_id, material_id, geometry_variant)
        ]
        oconv, rtrace, _ambient = build_diagnostic_trace_commands(
            scene_sources=(*config["scene_sources"], body_path),
            receiver_input=config["receiver"],
            case_directory=directory,
            scene_cwd=config["cwd"],
            ambient_bounces=ambient_bounces,
            sampling=sampling,
            oconv_bin=radiance.oconv.path,
            rtrace_bin=radiance.rtrace.path,
        )
        coordinates = config["coordinates"]
        field, commands = execute_diagnostic_trace(
            oconv_command=oconv,
            rtrace_command=rtrace,
            expected_sensor_count=len(coordinates),
            runner=runner,
            timeout_s=trace_timeout_s,
        )
        grid = config["grid"]
        usable_length = grid.room.length_m - 2.0 * grid.inset_m
        usable_width = grid.room.width_m - 2.0 * grid.inset_m
        statistics = compute_field_statistics(
            field,
            coordinates,
            resolution_x=grid.resolution_x,
            resolution_y=grid.resolution_y,
            receiver_length_m=usable_length,
            receiver_width_m=usable_width,
        )
        np.save(directory / "field.ppfd.npy", field, allow_pickle=False)
        (directory / "field.ppfd.csv").write_text(
            field_csv_text(coordinates, field), encoding="utf-8"
        )
        record = {
            "case_id": case_id,
            "system_id": system_id,
            "material_id": material_id,
            "geometry_variant": geometry_variant,
            "ambient_bounces": ambient_bounces,
            "sampling": asdict(sampling),
            "source_input_fixed": True,
            "target_control_enabled": False,
            "commands": commands,
            "statistics": statistics.to_payload(),
            "field_npy": str(directory / "field.ppfd.npy"),
            "field_npy_sha256": sha256_file(directory / "field.ppfd.npy"),
            "field_csv": str(directory / "field.ppfd.csv"),
            "field_csv_sha256": sha256_file(directory / "field.ppfd.csv"),
        }
        _write_json(directory / "case.v1.json", record)
        case_records[case_id] = record
        fields[case_id] = field
        return case_id

    case_specs: set[tuple[str, str, str, int, str]] = set()

    # Proposed causal geometry-by-bounce-depth matrix.
    for geometry_variant in PROPOSED_GEOMETRY_VARIANTS:
        for ambient_bounces in AMBIENT_DEPTHS:
            case_specs.add(
                (
                    "proposed",
                    ZERO_ABSORBER.material_id,
                    geometry_variant,
                    ambient_bounces,
                    "fine",
                )
            )

    # Conventional no-body/current-body references required for controlled
    # system comparison and direct/indirect decomposition.
    for geometry_variant in ("no_bodies", "all_bodies"):
        for ambient_bounces in (0, 5):
            case_specs.add(
                (
                    "conventional",
                    ZERO_ABSORBER.material_id,
                    geometry_variant,
                    ambient_bounces,
                    "fine",
                )
            )

    # Controlled material matrix.  Direct and ab5 are both evaluated because
    # metal can return specular paths without consuming an ambient bounce.
    for system_id in ("proposed", "conventional"):
        for material in DIAGNOSTIC_MATERIALS:
            for ambient_bounces in (0, 5):
                case_specs.add(
                    (
                        system_id,
                        material.material_id,
                        "all_bodies",
                        ambient_bounces,
                        "fine",
                    )
                )

    # Declared sampling convergence checks for the two causal endpoints in
    # both systems.
    for system_id in ("proposed", "conventional"):
        for geometry_variant in ("no_bodies", "all_bodies"):
            case_specs.add(
                (
                    system_id,
                    ZERO_ABSORBER.material_id,
                    geometry_variant,
                    5,
                    "coarse",
                )
            )

    # Every Radiance command remains explicitly -n 1.  Four independent
    # isolated cases run concurrently to make the convergence-qualified matrix
    # practical without sharing octrees, ambient caches, or output streams.
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {
            executor.submit(
                run_case,
                system_id=system_id,
                material_id=material_id,
                geometry_variant=geometry_variant,
                ambient_bounces=ambient_bounces,
                sampling_id=sampling_id,
            ): (
                system_id,
                material_id,
                geometry_variant,
                ambient_bounces,
                sampling_id,
            )
            for (
                system_id,
                material_id,
                geometry_variant,
                ambient_bounces,
                sampling_id,
            ) in sorted(case_specs)
        }
        for future in as_completed(futures):
            future.result()

    convergence_records: list[dict[str, object]] = []
    for system_id in ("proposed", "conventional"):
        for geometry_variant in ("no_bodies", "all_bodies"):
            coarse_id = (
                f"{system_id}__zero_absorber__{geometry_variant}__ab5__coarse"
            )
            fine_id = (
                f"{system_id}__zero_absorber__{geometry_variant}__ab5__fine"
            )
            convergence = convergence_statistics(
                fields[coarse_id], fields[fine_id]
            )
            convergence_records.append(
                {
                    "system_id": system_id,
                    "geometry_variant": geometry_variant,
                    "coarse_case_id": coarse_id,
                    "fine_case_id": fine_id,
                    **convergence,
                }
            )
    if not all(bool(record["passed"]) for record in convergence_records):
        raise FixtureBodyOpticsAuditError(
            "coarse/fine sampling convergence failed; causal claims are withheld."
        )

    difference_root = root / "difference-fields"
    difference_root.mkdir()
    for case_id, record in sorted(case_records.items()):
        if record["sampling"]["sampling_id"] != "fine":
            continue
        system_id = str(record["system_id"])
        ambient_bounces = int(record["ambient_bounces"])
        baseline_id = (
            f"{system_id}__zero_absorber__no_bodies"
            f"__ab{ambient_bounces}__fine"
        )
        if baseline_id not in fields:
            # Material matrix contains only ab0/ab5 and their no-body references.
            continue
        delta = fields[case_id] - fields[baseline_id]
        path = difference_root / f"{case_id}.minus-no-body.npy"
        np.save(path, delta, allow_pickle=False)
        record["difference_from_no_body"] = {
            **difference_statistics(fields[case_id], fields[baseline_id]),
            "field_path": str(path),
            "field_sha256": sha256_file(path),
        }
        if ambient_bounces > 0:
            direct_id = (
                f"{system_id}__{record['material_id']}__"
                f"{record['geometry_variant']}__ab0__fine"
            )
            if direct_id not in fields:
                # Causal geometry ab1/ab2 uses the zero-absorber direct field.
                direct_id = (
                    f"{system_id}__zero_absorber__"
                    f"{record['geometry_variant']}__ab0__fine"
                )
            indirect = fields[case_id] - fields[direct_id]
            record["direct_component_case_id"] = direct_id
            record["indirect_component"] = {
                "mean": float(np.mean(indirect)),
                "minimum": float(np.min(indirect)),
                "maximum": float(np.max(indirect)),
                "integrated_receiver_plane_ppfd_umol_s": float(
                    np.sum(indirect)
                    * (
                        (
                            systems[system_id]["grid"].room.length_m
                            - 2.0 * systems[system_id]["grid"].inset_m
                        )
                        / systems[system_id]["grid"].resolution_x
                    )
                    * (
                        (
                            systems[system_id]["grid"].room.width_m
                            - 2.0 * systems[system_id]["grid"].inset_m
                        )
                        / systems[system_id]["grid"].resolution_y
                    )
                ),
            }

    source_matrix = _source_configuration_matrix(
        proposed_plan, conventional_plan
    )
    material_sensitivity = _material_sensitivity_table(
        case_records, fields
    )
    causal_table = _causal_table(case_records)
    causal_findings = _causal_findings(case_records, convergence_records)
    report = {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_date": "2026-07-29",
        "diagnostic_only": True,
        "production_material_changed": False,
        "production_classification_changed": False,
        "target_control_enabled": False,
        "git": git,
        "radiance": {
            **radiance.to_dict(),
            "ies2rad": {
                "path": str(ies2rad),
                "version_text": probe_radiance_version(ies2rad),
            },
        },
        "source_configuration_matrix": source_matrix,
        "geometry_inventory_path": str(
            geometry_root / "geometry-and-classification-inventory.v1.json"
        ),
        "material_definitions": [
            material.to_payload() for material in DIAGNOSTIC_MATERIALS
        ],
        "case_count": len(case_records),
        "cases": [case_records[key] for key in sorted(case_records)],
        "convergence": convergence_records,
        "causal_comparison_table": causal_table,
        "material_sensitivity": material_sensitivity,
        "findings": causal_findings,
        "production_authority_required": {
            "minimum_measurements": [
                (
                    "Representative external fixture components measured over "
                    "400-700 nm for total hemispherical reflectance."
                ),
                (
                    "Diffuse/specular partition (or BRDF) at representative "
                    "incidence angles and documented surface finish/coating."
                ),
                (
                    "Spectral reflectance or five-band values if Stage B "
                    "multispectral transport is to share the authority."
                ),
                (
                    "Component-to-material mapping and manufacturing tolerance "
                    "for frame, heat sinks, driver, fasteners, and covers."
                ),
            ],
            "glb_pbr_values_accepted_as_scientific_data": False,
            "production_change_authorized": False,
        },
        "prohibited_actions_confirmation": {
            "production_material_change": False,
            "production_quality_web_run": False,
            "full_python_suite": False,
            "commit": False,
            "push": False,
            "merge": False,
            "rebase": False,
            "branch_deletion": False,
            "pull_request": False,
        },
    }
    report_path = root / "fixture-body-optics-audit-report.v1.json"
    _write_json(report_path, report)
    summary_path = root / "SUMMARY.md"
    summary_path.write_text(
        _markdown_summary(report, geometry_inventory),
        encoding="utf-8",
    )
    source_matrix_path = root / "source-configuration-matrix.v1.json"
    _write_json(source_matrix_path, source_matrix)
    material_path = root / "material-sensitivity.v1.json"
    _write_json(material_path, material_sensitivity)
    convergence_path = root / "sampling-convergence.v1.json"
    _write_json(convergence_path, convergence_records)
    cases_path = root / "transport-case-index.v1.json"
    _write_json(cases_path, [case_records[key] for key in sorted(case_records)])
    inventory_path = write_sha256_inventory(root)
    verify_sha256_inventory(root)
    return {
        "artifact_root": str(root),
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path),
        "summary_path": str(summary_path),
        "summary_sha256": sha256_file(summary_path),
        "sha256_inventory_path": str(inventory_path),
        "sha256_inventory_sha256": sha256_file(inventory_path),
        "case_count": len(case_records),
        "findings": causal_findings,
    }


def _shape_inventory(plan: Any) -> list[dict[str, object]]:
    counts = Counter(instance.shape_id for instance in plan.instances)
    return [
        {
            **shape.scientific_payload(),
            "instance_count": counts[shape.shape_id],
            "total_projected_opaque_area_m2": (
                shape.projected_opaque_area_m2 * counts[shape.shape_id]
            ),
        }
        for shape in plan.shapes
    ]


def _instance_inventory(plan: Any) -> dict[str, object]:
    by_asset = Counter(instance.asset_id for instance in plan.instances)
    return {
        "instance_count": len(plan.instances),
        "by_asset": dict(sorted(by_asset.items())),
        "total_projected_opaque_area_m2": plan.total_projected_opaque_area_m2,
        "external_triangle_instances": plan.total_external_triangle_instances,
        "transform_set_sha256": plan.transform_set_sha256,
        "occlusion_identity_sha256": plan.identity_sha256,
        "instances": [instance.scientific_payload() for instance in plan.instances],
    }


def _source_configuration_matrix(
    proposed_plan: Any,
    conventional_plan: Any,
) -> dict[str, object]:
    proposed_source = proposed_plan.emitter_document.metadata
    conventional_source = conventional_plan.source
    return {
        "comparison_policy": (
            "Fixed source input within each system; no target solve or post-trace "
            "normalization. Geometry/material variants share exact sources."
        ),
        "proposed": {
            "system_id": "proposed",
            "room_ft": [10.0, 10.0],
            "layout_mode": proposed_plan.layout.proposed_layout_mode.value,
            "fixture_policy_id": proposed_plan.layout.fixture_policy_id,
            "fixture_count": len(proposed_plan.layout.fixtures),
            "module_count": len(proposed_plan.layout.modules),
            "module_watts_equal": (
                len(set(proposed_plan.schedule.watts_by_module)) == 1
            ),
            "watts_per_module": proposed_plan.reference_watts_per_module,
            "total_input_watts": proposed_source.total_watts,
            "source_mode": proposed_source.source_mode,
            "modeled_completed_aperture_ppf_umol_s": (
                proposed_source.modeled_completed_aperture_par_ppf_umol_s
            ),
            "completed_aperture_ppe_umol_per_j": (
                proposed_source.completed_aperture_fixture_ppe_umol_per_j
            ),
            "source_text_sha256": sha256_file(proposed_plan.paths.source),
            "room_text_sha256": sha256_file(proposed_plan.paths.room),
            "sensor_text_sha256": sha256_file(proposed_plan.paths.sensors),
            "sensor_count": proposed_plan.sensor_grid_spec.point_count,
        },
        "conventional": {
            "system_id": "conventional",
            "room_ft": [10.0, 10.0],
            "layout_policy": conventional_plan.layout.policy.name,
            "fixture_count": len(conventional_plan.layout.fixtures),
            "fixture_ppf_umol_s": (
                conventional_source.declared_operating_point.par_ppf_umol_s_per_fixture
            ),
            "whole_layout_transported_downward_ppf_umol_s": (
                conventional_source.whole_layout_transported_downward_ppf_umol_s
            ),
            "global_dimming_factor": (
                conventional_plan.request.global_dimming_factor
            ),
            "room_text_sha256": sha256_file(conventional_plan.paths.room_rad),
            "shared_angular_source_sha256": sha256_file(
                conventional_plan.paths.shared_angular_rad
            ),
            "aperture_source_sha256": sha256_file(
                conventional_plan.paths.aperture_rad
            ),
            "sensor_text_sha256": sha256_file(
                conventional_plan.paths.sensor_rays
            ),
            "sensor_count": len(conventional_plan.sensor_points),
        },
    }


def _causal_table(
    cases: Mapping[str, Mapping[str, object]],
) -> list[dict[str, object]]:
    output: list[dict[str, object]] = []
    for variant in PROPOSED_GEOMETRY_VARIANTS:
        for depth in AMBIENT_DEPTHS:
            case_id = f"proposed__zero_absorber__{variant}__ab{depth}__fine"
            record = cases[case_id]
            statistics = record["statistics"]
            output.append(
                {
                    "geometry_variant": variant,
                    "ambient_bounces": depth,
                    "mean": statistics["mean"],
                    "exact_center": statistics["exact_center"],
                    "center_region": statistics["mean_within_0_5m"],
                    "annulus": statistics["mean_annulus_0_75_to_1_25m"],
                    "annulus_minus_center": statistics["annulus_minus_center"],
                    "annulus_minus_center_percent": (
                        statistics["annulus_minus_center_percent"]
                    ),
                    "cv_percent": statistics["cv_percent"],
                    "difference_from_no_body": record.get(
                        "difference_from_no_body"
                    ),
                    "indirect_component": record.get("indirect_component"),
                }
            )
    return output


def _material_sensitivity_table(
    cases: Mapping[str, Mapping[str, object]],
    fields: Mapping[str, np.ndarray],
) -> dict[str, object]:
    records: list[dict[str, object]] = []
    for system_id in ("proposed", "conventional"):
        zero_id = f"{system_id}__zero_absorber__all_bodies__ab5__fine"
        no_body_id = f"{system_id}__zero_absorber__no_bodies__ab5__fine"
        for material in DIAGNOSTIC_MATERIALS:
            direct_id = (
                f"{system_id}__{material.material_id}__all_bodies__ab0__fine"
            )
            indirect_id = (
                f"{system_id}__{material.material_id}__all_bodies__ab5__fine"
            )
            statistics = cases[indirect_id]["statistics"]
            records.append(
                {
                    "system_id": system_id,
                    "material_id": material.material_id,
                    "material": material.to_payload(),
                    "direct_case_id": direct_id,
                    "ab5_case_id": indirect_id,
                    "ab5_statistics": statistics,
                    "ab5_minus_zero_absorber": difference_statistics(
                        fields[indirect_id], fields[zero_id]
                    ),
                    "ab5_minus_no_body": difference_statistics(
                        fields[indirect_id], fields[no_body_id]
                    ),
                    "direct_minus_zero_absorber": difference_statistics(
                        fields[direct_id],
                        fields[
                            f"{system_id}__zero_absorber__all_bodies__ab0__fine"
                        ],
                    ),
                }
            )
    return {
        "policy": (
            "Identical controlled material definitions applied to Proposed and "
            "Conventional external bodies; not a real-finish claim."
        ),
        "records": records,
    }


def _causal_findings(
    cases: Mapping[str, Mapping[str, object]],
    convergence: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    def stats(variant: str, depth: int) -> Mapping[str, float]:
        return cases[
            f"proposed__zero_absorber__{variant}__ab{depth}__fine"
        ]["statistics"]  # type: ignore[return-value]

    direct_no = stats("no_bodies", 0)
    direct_all = stats("all_bodies", 0)
    ab5_no = stats("no_bodies", 5)
    ab5_all = stats("all_bodies", 5)
    ab5_center = stats("centerpiece_only", 5)
    ab5_linear = stats("linear2_only", 5)
    direct_mean_change_percent = (
        100.0
        * (float(direct_all["mean"]) - float(direct_no["mean"]))
        / float(direct_no["mean"])
    )
    ab5_center_delta_change = (
        float(ab5_center["annulus_minus_center"])
        - float(ab5_no["annulus_minus_center"])
    )
    ab5_linear_delta_change = (
        float(ab5_linear["annulus_minus_center"])
        - float(ab5_no["annulus_minus_center"])
    )
    ab5_all_delta_change = (
        float(ab5_all["annulus_minus_center"])
        - float(ab5_no["annulus_minus_center"])
    )
    direct_material = abs(direct_mean_change_percent) > 0.1
    centerpiece_share = (
        None
        if abs(ab5_all_delta_change) <= 1.0e-12
        else ab5_center_delta_change / ab5_all_delta_change
    )
    return {
        "sampling_converged": all(bool(item["passed"]) for item in convergence),
        "direct_no_body_vs_all_body_mean_change_percent": direct_mean_change_percent,
        "direct_obstruction_material": direct_material,
        "no_body_ab5_annulus_minus_center": ab5_no["annulus_minus_center"],
        "all_body_ab5_annulus_minus_center": ab5_all["annulus_minus_center"],
        "centerpiece_only_ab5_annulus_minus_center": (
            ab5_center["annulus_minus_center"]
        ),
        "linear2_only_ab5_annulus_minus_center": (
            ab5_linear["annulus_minus_center"]
        ),
        "all_body_added_depression": ab5_all_delta_change,
        "centerpiece_only_added_depression": ab5_center_delta_change,
        "linear2_only_added_depression": ab5_linear_delta_change,
        "centerpiece_share_of_all_body_added_depression": centerpiece_share,
        "causal_classification": (
            "direct_obstruction_or_registration"
            if direct_material
            else "positive-bounce_fixture-body_obstruction_absorption"
        ),
        "production_material_authority_available": False,
        "production_change_recommended_now": False,
    }


def _markdown_summary(
    report: Mapping[str, object],
    geometry: Mapping[str, object],
) -> str:
    findings = report["findings"]
    causal = report["causal_comparison_table"]
    materials = report["material_sensitivity"]["records"]
    lines = [
        "# Fixture-body optics audit v1",
        "",
        "This is a diagnostic-only fixed-input audit. Production fixture "
        "materials and classifications were not changed.",
        "",
        "## Causal finding",
        "",
        f"- Classification: `{findings['causal_classification']}`.",
        f"- Direct no-body → all-body mean change: "
        f"{findings['direct_no_body_vs_all_body_mean_change_percent']:.6f}%.",
        f"- No-body ab5 annulus-minus-center: "
        f"{findings['no_body_ab5_annulus_minus_center']:.6f}.",
        f"- All-body ab5 annulus-minus-center: "
        f"{findings['all_body_ab5_annulus_minus_center']:.6f}.",
        f"- Centerpiece-only added depression: "
        f"{findings['centerpiece_only_added_depression']:.6f}.",
        f"- Linear2-only added depression: "
        f"{findings['linear2_only_added_depression']:.6f}.",
        f"- Sampling convergence passed: `{findings['sampling_converged']}`.",
        "",
        "## Direct and bounce-depth matrix",
        "",
        "| Bodies | -ab | Mean | Center | Center ≤0.5 m | Annulus | Annulus-center | % |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for record in causal:
        lines.append(
            f"| {record['geometry_variant']} | {record['ambient_bounces']} | "
            f"{record['mean']:.6f} | {record['exact_center']:.6f} | "
            f"{record['center_region']:.6f} | {record['annulus']:.6f} | "
            f"{record['annulus_minus_center']:.6f} | "
            f"{record['annulus_minus_center_percent']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Material sensitivity at -ab 5",
            "",
            "| System | Material | Total R | Specular | Roughness | Mean | Center | Annulus-center | Δ mean vs zero |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for record in materials:
        material = record["material"]
        stats = record["ab5_statistics"]
        lines.append(
            f"| {record['system_id']} | {record['material_id']} | "
            f"{material['declared_total_reflectance']:.2f} | "
            f"{material['specular_fraction']:.2f} | "
            f"{material['roughness']:.2f} | {stats['mean']:.6f} | "
            f"{stats['exact_center']:.6f} | "
            f"{stats['annulus_minus_center']:.6f} | "
            f"{record['ab5_minus_zero_absorber']['mean']:.6f} |"
        )
    assets = geometry["assets"]
    lines.extend(
        [
            "",
            "## Geometry authentication",
            "",
            "| Asset | External primitives | External triangles | Degenerate | Duplicate excess | Nonmanifold edges |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for asset_id, record in assets.items():
        quality = record["triangle_quality"]
        lines.append(
            f"| {asset_id} | "
            f"{record['asset']['classification_counts']['external_occluder']} | "
            f"{record['external_triangle_count']} | "
            f"{quality['degenerate_triangle_count_area_le_1e-14_mm2']} | "
            f"{quality['exact_duplicate_triangle_excess_count']} | "
            f"{quality['nonmanifold_edge_count']} |"
        )
    lines.extend(
        [
            "",
            "The detected zero-area triangles, exact duplicate pairs, and "
            "nonmanifold shared edges are recorded for classification review. "
            "They are not, by themselves, authority to edit the GLBs or exclude "
            "a named component.",
            "",
            "## Production decision",
            "",
            "No measured fixture-surface optical authority was found. A production "
            "material change is therefore not defensible from this sensitivity "
            "matrix alone. Required data are component-mapped spectral or PAR total "
            "reflectance, diffuse/specular partition or BRDF, roughness, coating/"
            "finish identity, and manufacturing tolerance.",
            "",
            "No production Quality run, full Python suite, commit, push, merge, "
            "rebase, branch deletion, or PR was performed.",
            "",
        ]
    )
    return "\n".join(lines)


def _git_identity() -> dict[str, object]:
    def git(*args: str) -> str:
        completed = subprocess.run(
            ("git", *args),
            cwd=REPOSITORY_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        return completed.stdout.rstrip()

    return {
        "repository_root": str(REPOSITORY_ROOT),
        "branch": git("branch", "--show-current"),
        "head": git("rev-parse", "HEAD"),
        "main": git("rev-parse", "main"),
        "worktree_status_before_audit": git("status", "--short", "--branch"),
        "fixture_occlusion_introducing_commit": (
            "59e182f5d985c3abe5f77008bab1ed9fc1ef6684"
        ),
        "history_command": (
            "git log -Scompile_fixture_body_shape --all -- "
            "src tests scripts; git log --all -- fixture occlusion paths"
        ),
    }


def _write_json(path: Path, payload: object) -> None:
    if path.exists() or path.is_symlink():
        raise FixtureBodyOpticsAuditError(
            f"refusing existing audit JSON artifact: {path}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json_text(payload), encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="new output directory; existing paths are rejected",
    )
    parser.add_argument(
        "--trace-timeout-s",
        type=float,
        default=None,
        help="optional per-command timeout",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = execute_audit(
            args.output_root,
            trace_timeout_s=args.trace_timeout_s,
        )
    except (FixtureBodyOpticsAuditError, OSError, ValueError) as exc:
        print(f"fixture-body optics audit failed: {exc}", file=sys.stderr)
        return 1
    print(canonical_json_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
