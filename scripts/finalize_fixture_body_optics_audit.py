#!/usr/bin/env python3
"""Refine convergence and finalize a completed fixture-body trace matrix."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
for candidate in (str(SOURCE_ROOT), str(Path(__file__).resolve().parent)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

import audit_fixture_body_optics as original  # noqa: E402
from fspm_optics.diagnostics.fixture_body_optics_audit import (  # noqa: E402
    AUDIT_SCHEMA_ID,
    AUDIT_SCHEMA_VERSION,
    DIAGNOSTIC_MATERIALS,
    ZERO_ABSORBER,
    FixtureBodyOpticsAuditError,
    TraceSampling,
    build_diagnostic_trace_commands,
    canonical_json_text,
    compute_field_statistics,
    convergence_statistics,
    difference_statistics,
    execute_diagnostic_trace,
    field_csv_text,
    sha256_file,
    verify_sha256_inventory,
    write_sha256_inventory,
)
from fspm_optics.fixtures.conventional_led import (  # noqa: E402
    DEFAULT_MOUNT_HEIGHT_M,
    PRACTICAL_LAYOUT_POLICY,
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
from fspm_optics.radiance.runner import LocalRunner  # noqa: E402
from fspm_optics.radiance.versioning import (  # noqa: E402
    discover_radiance_installation,
)
from fspm_optics.transport.conventional_scalar import (  # noqa: E402
    ConventionalScalarTransportRequest,
    plan_conventional_scalar_transport,
    resolve_conventional_scalar_executables,
)
from fspm_optics.transport.proposed_uniform import (  # noqa: E402
    plan_uniform_proposed_stage_a,
)

REFINED_SAMPLING = TraceSampling("refined", 1024, 256, 0.08, 96)
EXPECTED_PRELIMINARY_CASES = 44


def finalize(
    root_path: str | Path,
    *,
    run_refinement: bool = False,
) -> dict[str, object]:
    root = Path(root_path).expanduser().resolve()
    if (
        not root.is_dir()
        or root.is_symlink()
        or (root / "fixture-body-optics-audit-report.v1.json").exists()
        or (root / "SHA256SUMS").exists()
    ):
        raise FixtureBodyOpticsAuditError(
            "finalizer requires one unfinalized, non-symlink audit root."
        )
    for path in root.rglob("*"):
        if path.is_symlink():
            raise FixtureBodyOpticsAuditError(
                f"finalizer rejects symlink in audit root: {path}"
            )
    preliminary_records = tuple(
        sorted((root / "transport-cases").glob("*/case.v1.json"))
    )
    if len(preliminary_records) != EXPECTED_PRELIMINARY_CASES:
        raise FixtureBodyOpticsAuditError(
            "unfinalized audit does not contain the exact 44-case matrix."
        )

    radiance = discover_radiance_installation()
    runner = LocalRunner()
    proposed_input = root / "inputs" / "proposed"
    proposed_layout = generate_proposed_led_layout(10.0, 10.0)
    proposed_plan = plan_uniform_proposed_stage_a(
        layout=proposed_layout,
        output_directory=proposed_input,
        radiance_options=("-ab", "0"),
        nthreads=1,
        use_ambient_cache=False,
        emitter_assumptions=SmdEmitterAssumptions(source_mode=COB_SOURCE_MODE),
    )
    conventional_request = ConventionalScalarTransportRequest(
        workspace=root / "inputs" / "conventional_seed",
        room_length_m=10.0 * FEET_TO_METERS,
        room_width_m=10.0 * FEET_TO_METERS,
        layout_policy=PRACTICAL_LAYOUT_POLICY,
        reference_plane_z_m=proposed_plan.sensor_grid_spec.canopy_height_m,
        mount_height_m=DEFAULT_MOUNT_HEIGHT_M,
        quality_profile="direct",
        threads=1,
    )
    conventional_bins = resolve_conventional_scalar_executables(
        conventional_request
    )
    conventional_plan = plan_conventional_scalar_transport(
        conventional_request,
        executables=conventional_bins,
    )
    coordinates = np.asarray(
        [
            (point.x_m, point.y_m, point.z_m)
            for point in generate_sensor_points(proposed_plan.sensor_grid_spec)
        ],
        dtype=np.float64,
    )

    def run_refined(variant: str) -> tuple[str, np.ndarray, dict[str, object]]:
        case_id = (
            f"proposed__zero_absorber__{variant}__ab5__refined"
        )
        directory = root / "transport-cases" / case_id
        body = (
            root
            / "diagnostic-scene-inputs"
            / "proposed"
            / ZERO_ABSORBER.material_id
            / f"{variant}.rad"
        )
        oconv, rtrace, _ambient = build_diagnostic_trace_commands(
            scene_sources=(
                proposed_plan.paths.room,
                proposed_plan.paths.source,
                body,
            ),
            receiver_input=proposed_plan.paths.sensors,
            case_directory=directory,
            scene_cwd=proposed_input,
            ambient_bounces=5,
            sampling=REFINED_SAMPLING,
            oconv_bin=radiance.oconv.path,
            rtrace_bin=radiance.rtrace.path,
        )
        field, commands = execute_diagnostic_trace(
            oconv_command=oconv,
            rtrace_command=rtrace,
            expected_sensor_count=len(coordinates),
            runner=runner,
        )
        grid = proposed_plan.sensor_grid_spec
        statistics = compute_field_statistics(
            field,
            coordinates,
            resolution_x=grid.resolution_x,
            resolution_y=grid.resolution_y,
            receiver_length_m=grid.room.length_m - 2.0 * grid.inset_m,
            receiver_width_m=grid.room.width_m - 2.0 * grid.inset_m,
        )
        np.save(directory / "field.ppfd.npy", field, allow_pickle=False)
        (directory / "field.ppfd.csv").write_text(
            field_csv_text(coordinates, field), encoding="utf-8"
        )
        record = {
            "case_id": case_id,
            "system_id": "proposed",
            "material_id": ZERO_ABSORBER.material_id,
            "geometry_variant": variant,
            "ambient_bounces": 5,
            "sampling": asdict(REFINED_SAMPLING),
            "source_input_fixed": True,
            "target_control_enabled": False,
            "commands": commands,
            "statistics": statistics.to_payload(),
            "field_npy": str(directory / "field.ppfd.npy"),
            "field_npy_sha256": sha256_file(directory / "field.ppfd.npy"),
            "field_csv": str(directory / "field.ppfd.csv"),
            "field_csv_sha256": sha256_file(directory / "field.ppfd.csv"),
        }
        original._write_json(directory / "case.v1.json", record)
        return case_id, field, record

    if run_refinement:
        with ThreadPoolExecutor(max_workers=2) as executor:
            tuple(executor.map(run_refined, ("no_bodies", "all_bodies")))

    case_records: dict[str, dict[str, object]] = {}
    fields: dict[str, np.ndarray] = {}
    for path in sorted((root / "transport-cases").glob("*/case.v1.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        case_id = str(record["case_id"])
        if case_id in case_records:
            raise FixtureBodyOpticsAuditError(
                f"duplicate case identity during finalization: {case_id}"
            )
        field_path = path.parent / "field.ppfd.npy"
        if sha256_file(field_path) != record["field_npy_sha256"]:
            raise FixtureBodyOpticsAuditError(
                f"case field identity changed before finalization: {case_id}"
            )
        field = np.load(field_path, allow_pickle=False)
        if field.shape != (441,) or not np.all(np.isfinite(field)):
            raise FixtureBodyOpticsAuditError(
                f"case field is incompatible: {case_id}"
            )
        case_records[case_id] = record
        fields[case_id] = np.asarray(field, dtype=np.float64)

    convergence_records: list[dict[str, object]] = []
    # Preserve the original coarse/fine checks as preliminary evidence.
    for system_id in ("proposed", "conventional"):
        for variant in ("no_bodies", "all_bodies"):
            coarse_id = (
                f"{system_id}__zero_absorber__{variant}__ab5__coarse"
            )
            fine_id = (
                f"{system_id}__zero_absorber__{variant}__ab5__fine"
            )
            convergence_records.append(
                {
                    "stage": "preliminary_64_vs_256",
                    "system_id": system_id,
                    "geometry_variant": variant,
                    "coarse_case_id": coarse_id,
                    "fine_case_id": fine_id,
                    **convergence_statistics(
                        fields[coarse_id], fields[fine_id]
                    ),
                }
            )
    refined_convergence: list[dict[str, object]] = []
    if run_refinement:
        # The optional refined checks retain the original predeclared 1% mean /
        # 3% pointwise-RMS gate; no threshold is changed after preliminary data.
        for variant in ("no_bodies", "all_bodies"):
            fine_id = f"proposed__zero_absorber__{variant}__ab5__fine"
            refined_id = (
                f"proposed__zero_absorber__{variant}__ab5__refined"
            )
            record = {
                "stage": "refined_256_vs_1024",
                "system_id": "proposed",
                "geometry_variant": variant,
                "coarse_case_id": fine_id,
                "fine_case_id": refined_id,
                **convergence_statistics(fields[fine_id], fields[refined_id]),
            }
            refined_convergence.append(record)
            convergence_records.append(record)
        if not all(bool(item["passed"]) for item in refined_convergence):
            raise FixtureBodyOpticsAuditError(
                "refined 256/1024 convergence failed; causal claims remain withheld."
            )

    difference_root = root / "difference-fields"
    difference_root.mkdir(exist_ok=False)
    for case_id, record in sorted(case_records.items()):
        if record["sampling"]["sampling_id"] not in {"fine", "refined"}:
            continue
        system_id = str(record["system_id"])
        depth = int(record["ambient_bounces"])
        sampling_id = str(record["sampling"]["sampling_id"])
        baseline_sampling = (
            "refined"
            if sampling_id == "refined"
            else "fine"
        )
        baseline_id = (
            f"{system_id}__zero_absorber__no_bodies"
            f"__ab{depth}__{baseline_sampling}"
        )
        if baseline_id not in fields:
            continue
        delta = fields[case_id] - fields[baseline_id]
        path = difference_root / f"{case_id}.minus-no-body.npy"
        np.save(path, delta, allow_pickle=False)
        record["difference_from_no_body"] = {
            **difference_statistics(fields[case_id], fields[baseline_id]),
            "field_path": str(path),
            "field_sha256": sha256_file(path),
        }
        if depth > 0 and sampling_id == "fine":
            direct_id = (
                f"{system_id}__{record['material_id']}__"
                f"{record['geometry_variant']}__ab0__fine"
            )
            if direct_id not in fields:
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
            }

    geometry_path = (
        root / "geometry" / "geometry-and-classification-inventory.v1.json"
    )
    geometry = json.loads(geometry_path.read_text(encoding="utf-8"))
    source_matrix = original._source_configuration_matrix(
        proposed_plan, conventional_plan
    )
    material_sensitivity = original._material_sensitivity_table(
        case_records, fields
    )
    causal_table = original._causal_table(case_records)
    convergence_for_findings = (
        refined_convergence if run_refinement else convergence_records
    )
    findings = original._causal_findings(
        case_records, convergence_for_findings
    )
    findings["preliminary_convergence_failure_preserved"] = True
    if run_refinement:
        findings["refinement_policy"] = (
            "256 vs 1024 retained the original 1% mean and 3% pointwise RMS gates"
        )
    else:
        findings["refinement_policy"] = (
            "1024 refinement was aborted at user direction; no incomplete "
            "refinement field entered analysis."
        )
        findings["causal_classification"] = (
            "inconclusive_under_declared_convergence_gate; preliminary evidence "
            "supports positive-bounce centerpiece absorption/obstruction"
        )
        findings["production_change_recommended_now"] = False
    report = {
        "schema_id": AUDIT_SCHEMA_ID,
        "schema_version": AUDIT_SCHEMA_VERSION,
        "audit_date": "2026-07-29",
        "diagnostic_only": True,
        "production_material_changed": False,
        "production_classification_changed": False,
        "target_control_enabled": False,
        "git": original._git_identity(),
        "initial_repository_inspection": {
            "branch": "feat/fixture-body-reflectance-audit",
            "head_and_main": "fb22506f932324d3e7d1e1cf331f5e40923a1905",
            "worktree_clean_before_modification": True,
        },
        "radiance": {
            **radiance.to_dict(),
            "ies2rad": {
                "path": str(conventional_bins.ies2rad),
                "version_text": None,
            },
        },
        "source_configuration_matrix": source_matrix,
        "geometry_inventory_path": str(geometry_path),
        "material_definitions": [
            material.to_payload() for material in DIAGNOSTIC_MATERIALS
        ],
        "case_count": len(case_records),
        "cases": [case_records[key] for key in sorted(case_records)],
        "convergence": convergence_records,
        "causal_comparison_table": causal_table,
        "material_sensitivity": material_sensitivity,
        "findings": findings,
        "production_authority_required": {
            "minimum_measurements": [
                "400-700 nm total hemispherical reflectance by component.",
                "Diffuse/specular partition or BRDF by incidence angle.",
                "Spectral or five-band reflectance for Stage B authority.",
                "Component-to-finish mapping and manufacturing tolerance.",
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
    original._write_json(report_path, report)
    summary_path = root / "SUMMARY.md"
    summary_path.write_text(
        original._markdown_summary(report, geometry), encoding="utf-8"
    )
    original._write_json(
        root / "source-configuration-matrix.v1.json", source_matrix
    )
    original._write_json(
        root / "material-sensitivity.v1.json", material_sensitivity
    )
    original._write_json(
        root / "sampling-convergence.v1.json", convergence_records
    )
    original._write_json(
        root / "transport-case-index.v1.json",
        [case_records[key] for key in sorted(case_records)],
    )
    inventory = write_sha256_inventory(root)
    verify_sha256_inventory(root)
    return {
        "artifact_root": str(root),
        "case_count": len(case_records),
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path),
        "summary_path": str(summary_path),
        "summary_sha256": sha256_file(summary_path),
        "sha256_inventory_path": str(inventory),
        "sha256_inventory_sha256": sha256_file(inventory),
        "findings": findings,
    }


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(argv or sys.argv[1:])
    if len(arguments) != 1:
        print(
            "usage: finalize_fixture_body_optics_audit.py AUDIT_ROOT",
            file=sys.stderr,
        )
        return 2
    try:
        result = finalize(arguments[0])
    except (FixtureBodyOpticsAuditError, OSError, ValueError) as exc:
        print(f"fixture-body audit finalization failed: {exc}", file=sys.stderr)
        return 1
    print(canonical_json_text(result), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
