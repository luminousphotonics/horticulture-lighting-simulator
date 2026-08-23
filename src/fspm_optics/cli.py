"""Thin local command line entry points for the headless optics engine."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Sequence

from fspm_optics.fixtures.smd.positions import generate_proposed_led_layout
from fspm_optics.plants import (
    RexPlantConfig,
    generate_rex_butterhead_plant,
    rex_geometry_summary,
    write_plant_mesh_obj,
    write_rex_geometry_summary,
)
from fspm_optics.radiance.commands import LOCAL_DEFAULT_NTHREADS, CommandSpec
from fspm_optics.radiance.runner import LocalRunner
from fspm_optics.radiance.versioning import (
    RadianceInstallation,
    discover_radiance_installation,
)
from fspm_optics.transport.basis.execution import (
    execute_basis_column,
    execute_basis_matrix,
)
from fspm_optics.transport.basis.composite import (
    execute_composite_validation,
    plan_composite_validation,
)
from fspm_optics.transport.basis.solve import solve_basis_workspace
from fspm_optics.transport.basis.workspace import (
    materialize_basis_workspace,
    plan_basis_workspace,
)
from fspm_optics.transport.plant_receiver import (
    execute_rex_receiver_smoke,
    plan_rex_receiver_smoke,
)
from fspm_optics.transport.five_band import (
    FIVE_BAND_ORDER,
    plan_rex_five_band_transport,
)
from fspm_optics.transport.five_band_execution import (
    execute_rex_five_band_receiver_smoke,
)
from fspm_optics.transport.five_band_absorption import (
    calculate_rex_five_band_absorbed_metrics,
)
from fspm_optics.transport.conventional_scalar import (
    ConventionalScalarExecutables,
    ConventionalScalarTransportRequest,
    execute_conventional_scalar_transport,
)
from fspm_optics.transport.hps_scalar import (
    HPS_FIXTURE_POWER_W,
    HPS_FIXTURE_PPF_UMOL_S,
    HpsScalarExecutables,
    HpsScalarTransportRequest,
    execute_hps_scalar_transport,
)
from fspm_optics.transport.hps_rex_execution import (
    HPS_REX_RUN_ORDER,
    HpsRexExecutables,
    HpsRexTransportRequest,
    execute_hps_rex_transport,
)
from fspm_optics.transport.conventional_rex_execution import (
    ConventionalRexExecutables,
    ConventionalRexTransportRequest,
    execute_conventional_rex_transport,
)
from fspm_optics.transport.conventional_rex import CONVENTIONAL_REX_RUN_ORDER
from fspm_optics.transport.conventional_rex_absorption import (
    calculate_conventional_rex_absorbed_metrics,
)
from fspm_optics.transport.hps_rex_absorption import (
    calculate_hps_rex_absorbed_metrics,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fspm-optics")
    commands = parser.add_subparsers(dest="command", required=True)
    smoke = commands.add_parser(
        "smd-basis-smoke",
        help="plan or execute isolated local SMD basis columns",
    )
    smoke.add_argument("--workspace", type=Path, required=True)
    smoke.add_argument(
        "--room-ft",
        type=float,
        nargs=2,
        metavar=("LENGTH", "WIDTH"),
        required=True,
    )
    selection = smoke.add_mutually_exclusive_group()
    selection.add_argument("--zone", type=int)
    selection.add_argument("--all-zones", action="store_true")
    smoke.add_argument("--threads", type=int, default=6)
    smoke.add_argument("--reference-watts", type=float, default=1.0)
    smoke.add_argument("--execute", action="store_true")
    solve = commands.add_parser(
        "smd-basis-solve",
        help="solve a persisted SMD basis into explicit control-zone wattages",
    )
    solve.add_argument("--workspace", type=Path, required=True)
    solve.add_argument("--target-ppfd", type=float, required=True)
    solve.add_argument("--min-watts", type=float, default=0.0)
    solve.add_argument("--max-watts", type=float, default=100.0)
    solve.add_argument(
        "--reference-watts-per-module",
        type=float,
        help="fallback only when an older basis manifest omits reference_watts",
    )
    composite = commands.add_parser(
        "smd-composite-validate",
        help="plan or execute final composite SMD PPFD validation",
    )
    composite.add_argument("--workspace", type=Path, required=True)
    composite.add_argument("--execute", action="store_true")
    rex_export = commands.add_parser(
        "rex-plant-export",
        help="export deterministic Rex scientific geometry to debug OBJ",
    )
    rex_export.add_argument("--output", type=Path, required=True)
    rex_export.add_argument("--seed", type=int, default=1)
    rex_export.add_argument("--leaf-patch-u", type=int, default=4)
    rex_export.add_argument("--leaf-patch-v", type=int, default=4)
    rex_receiver = commands.add_parser(
        "rex-receiver-smoke",
        help="plan or execute a scalar Rex plant receiver transport smoke",
    )
    rex_receiver.add_argument("--workspace", type=Path, required=True)
    rex_receiver.add_argument("--seed", type=int, default=1)
    rex_receiver.add_argument("--execute", action="store_true")
    five_band_receiver = commands.add_parser(
        "rex-five-band-receiver-smoke",
        help="plan or sequentially execute five isolated Rex receiver bands",
    )
    five_band_receiver.add_argument("--workspace", type=Path, required=True)
    five_band_receiver.add_argument("--seed", type=int, default=1)
    five_band_receiver.add_argument("--normal-offset-m", type=float, default=5e-5)
    five_band_receiver.add_argument(
        "--threads", type=int, default=LOCAL_DEFAULT_NTHREADS
    )
    five_band_receiver.add_argument("--execute", action="store_true")
    absorbed_metrics = commands.add_parser(
        "rex-five-band-absorbed-metrics",
        help="derive absorbed photon metrics from successful five-band artifacts",
    )
    absorbed_metrics.add_argument("--workspace", type=Path, required=True)
    conventional = commands.add_parser(
        "conventional-scalar-transport",
        help="execute fixed-output Conventional horizontal-PPFD transport",
    )
    conventional.add_argument("--workspace", type=Path, required=True)
    conventional.add_argument(
        "--room-ft",
        type=float,
        nargs=2,
        metavar=("LENGTH", "WIDTH"),
        default=(10.0, 10.0),
        help="room length and width in feet (default: 10 10)",
    )
    conventional.add_argument(
        "--threads", type=int, default=LOCAL_DEFAULT_NTHREADS
    )
    hps_scalar = commands.add_parser(
        "hps-scalar-transport",
        help="execute fixed-output HPS horizontal-PPFD transport",
    )
    hps_scalar.add_argument("--workspace", type=Path, required=True)
    hps_scalar.add_argument(
        "--room-ft",
        type=float,
        nargs=2,
        metavar=("LENGTH", "WIDTH"),
        default=(10.0, 10.0),
        help="room length and width in feet (default: 10 10)",
    )
    hps_scalar.add_argument(
        "--threads", type=int, default=LOCAL_DEFAULT_NTHREADS
    )
    hps_rex = commands.add_parser(
        "hps-rex-transport",
        help="execute six-run HPS Rex incident transport",
    )
    hps_rex.add_argument("--workspace", type=Path, required=True)
    hps_rex.add_argument(
        "--room-ft",
        type=float,
        nargs=2,
        metavar=("LENGTH", "WIDTH"),
        default=(10.0, 10.0),
        help="room length and width in feet (default: 10 10)",
    )
    hps_rex.add_argument("--threads", type=int, default=LOCAL_DEFAULT_NTHREADS)
    conventional_rex = commands.add_parser(
        "conventional-rex-transport",
        help="execute Conventional scalar and five-band Rex incident transport",
    )
    conventional_rex.add_argument("--workspace", type=Path, required=True)
    conventional_rex.add_argument(
        "--room-ft",
        type=float,
        nargs=2,
        metavar=("LENGTH", "WIDTH"),
        default=(10.0, 10.0),
        help="room length and width in feet (default: 10 10)",
    )
    conventional_rex.add_argument(
        "--threads", type=int, default=LOCAL_DEFAULT_NTHREADS
    )
    conventional_rex_absorbed = commands.add_parser(
        "conventional-rex-absorbed-metrics",
        help="derive Conventional Rex optical partitions from native incident artifacts",
    )
    conventional_rex_absorbed.add_argument(
        "--workspace", type=Path, required=True
    )
    hps_rex_absorbed = commands.add_parser(
        "hps-rex-absorbed-metrics",
        help="derive HPS Rex optical partitions from Phase 25D incident artifacts",
    )
    hps_rex_absorbed.add_argument("--workspace", type=Path, required=True)
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: LocalRunner | None = None,
    radiance_installation: RadianceInstallation | None = None,
    conventional_executables: ConventionalScalarExecutables | None = None,
    hps_scalar_executables: HpsScalarExecutables | None = None,
    hps_rex_executables: HpsRexExecutables | None = None,
    conventional_rex_executables: ConventionalRexExecutables | None = None,
) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "hps-rex-absorbed-metrics":
        try:
            result = calculate_hps_rex_absorbed_metrics(args.workspace)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "command": "hps-rex-absorbed-metrics",
                        "success": False,
                        "error": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        plant = result.computed.spectral.plant
        print(
            json.dumps(
                {
                    "command": "hps-rex-absorbed-metrics",
                    "success": True,
                    "workspace": str(result.loaded.workspace_root),
                    "summary": str(result.summary_path),
                    "patch_metrics": str(result.patch_npz_path),
                    "absorbed_par_photon_flux_umol_s": plant.par.absorbed_flux,
                    "area_weighted_absorbed_par_pfd_umol_m2_s": (
                        plant.par.area_weighted_absorbed_pfd
                    ),
                    "absorbed_far_red_photon_flux_umol_s": plant.far_red.absorbed_flux,
                    "scalar_four_band_validation": (
                        result.computed.scalar_diagnostic.to_payload()
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "hps-rex-transport":
        length_ft, width_ft = args.room_ft
        try:
            result = execute_hps_rex_transport(
                HpsRexTransportRequest.from_feet(
                    workspace=args.workspace,
                    room_length_ft=length_ft,
                    room_width_ft=width_ft,
                    threads=args.threads,
                ),
                runner or LocalRunner(),
                executables=hps_rex_executables,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "command": "hps-rex-transport",
                        "success": False,
                        "error": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        print(
            json.dumps(
                {
                    "command": "hps-rex-transport",
                    "success": True,
                    "workspace": str(result.plan.request.workspace),
                    "artifact_root": str(result.plan.paths.artifact_root),
                    "run_order": list(HPS_REX_RUN_ORDER),
                    "receiver_count": len(result.plan.receiver_samples),
                    "native_command_count": result.command_count,
                    "scalar_four_band_diagnostics": (
                        result.scalar_four_band_diagnostics.to_payload()
                    ),
                    "artifacts": {
                        "incident_transport_summary": str(
                            result.plan.paths.incident_transport_summary
                        ),
                        "incident_receiver_values": str(
                            result.plan.paths.incident_receiver_values_npz
                        ),
                        "command_provenance": str(
                            result.plan.paths.command_provenance_summary
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "hps-scalar-transport":
        length_ft, width_ft = args.room_ft
        try:
            result = execute_hps_scalar_transport(
                HpsScalarTransportRequest.from_feet(
                    workspace=args.workspace,
                    room_length_ft=length_ft,
                    room_width_ft=width_ft,
                    threads=args.threads,
                ),
                runner or LocalRunner(),
                executables=hps_scalar_executables,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "command": "hps-scalar-transport",
                        "success": False,
                        "error": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        print(
            json.dumps(
                {
                    "command": "hps-scalar-transport",
                    "success": True,
                    "workspace": str(result.plan.request.workspace),
                    "artifact_root": str(result.plan.paths.artifact_root),
                    "fixture_count": len(result.plan.layout.fixtures),
                    "sensor_count": result.metrics.sensor_count,
                    "mean_ppfd_umol_m2_s": result.metrics.mean_ppfd_umol_m2_s,
                    "minimum_ppfd_umol_m2_s": result.metrics.minimum_ppfd_umol_m2_s,
                    "maximum_ppfd_umol_m2_s": result.metrics.maximum_ppfd_umol_m2_s,
                    "coefficient_of_variation_percent": (
                        result.metrics.coefficient_of_variation_percent
                    ),
                    "total_tested_system_input_power_w": (
                        len(result.plan.layout.fixtures) * HPS_FIXTURE_POWER_W
                    ),
                    "total_planned_ppf_umol_s": (
                        len(result.plan.layout.fixtures) * HPS_FIXTURE_PPF_UMOL_S
                    ),
                    "artifacts": {
                        "source_conversion_summary": str(
                            result.plan.paths.source_conversion_summary
                        ),
                        "scalar_transport_summary": str(
                            result.plan.paths.scalar_transport_summary
                        ),
                        "command_provenance": str(
                            result.plan.paths.command_provenance_summary
                        ),
                        "ppfd_values": str(result.plan.paths.ppfd_npz),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "conventional-rex-absorbed-metrics":
        try:
            result = calculate_conventional_rex_absorbed_metrics(args.workspace)
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "command": "conventional-rex-absorbed-metrics",
                        "success": False,
                        "error": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        plant = result.computed.spectral.plant
        print(
            json.dumps(
                {
                    "command": "conventional-rex-absorbed-metrics",
                    "success": True,
                    "workspace": str(result.loaded.workspace_root),
                    "summary": str(result.summary_path),
                    "patch_metrics": str(result.patch_npz_path),
                    "absorbed_par_photon_flux_umol_s": plant.par.absorbed_flux,
                    "area_weighted_absorbed_par_pfd_umol_m2_s": (
                        plant.par.area_weighted_absorbed_pfd
                    ),
                    "absorbed_far_red_photon_flux_umol_s": (
                        plant.far_red.absorbed_flux
                    ),
                    "scalar_four_band_validation": (
                        result.computed.scalar_diagnostic.to_payload()
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "conventional-rex-transport":
        length_ft, width_ft = args.room_ft
        try:
            result = execute_conventional_rex_transport(
                ConventionalRexTransportRequest.from_feet(
                    workspace=args.workspace,
                    room_length_ft=length_ft,
                    room_width_ft=width_ft,
                    threads=args.threads,
                ),
                runner or LocalRunner(),
                executables=conventional_rex_executables,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "command": "conventional-rex-transport",
                        "success": False,
                        "error": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        print(
            json.dumps(
                {
                    "command": "conventional-rex-transport",
                    "success": True,
                    "workspace": str(result.plan.request.workspace),
                    "artifact_root": str(result.plan.paths.artifact_root),
                    "run_order": list(CONVENTIONAL_REX_RUN_ORDER),
                    "receiver_count": len(result.plan.receiver_samples),
                    "native_command_count": result.command_count,
                    "scalar_four_band_diagnostics": (
                        result.scalar_four_band_diagnostics.to_payload()
                    ),
                    "artifacts": {
                        "incident_transport_summary": str(
                            result.plan.paths.incident_transport_summary
                        ),
                        "incident_receiver_values": str(
                            result.plan.paths.incident_receiver_values_npz
                        ),
                        "command_provenance": str(
                            result.plan.paths.command_provenance_summary
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "conventional-scalar-transport":
        length_ft, width_ft = args.room_ft
        try:
            result = execute_conventional_scalar_transport(
                ConventionalScalarTransportRequest.from_feet(
                    workspace=args.workspace,
                    room_length_ft=length_ft,
                    room_width_ft=width_ft,
                    threads=args.threads,
                ),
                runner or LocalRunner(),
                executables=conventional_executables,
            )
        except Exception as exc:
            print(
                json.dumps(
                    {
                        "command": "conventional-scalar-transport",
                        "success": False,
                        "error": str(exc),
                    },
                    indent=2,
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
            return 1
        print(
            json.dumps(
                {
                    "command": "conventional-scalar-transport",
                    "success": True,
                    "workspace": str(result.plan.request.workspace),
                    "fixture_count": len(result.plan.layout.fixtures),
                    "sensor_count": result.metrics.sensor_count,
                    "mean_ppfd_umol_m2_s": result.metrics.mean_ppfd_umol_m2_s,
                    "minimum_ppfd_umol_m2_s": result.metrics.minimum_ppfd_umol_m2_s,
                    "maximum_ppfd_umol_m2_s": result.metrics.maximum_ppfd_umol_m2_s,
                    "coefficient_of_variation_percent": (
                        result.metrics.coefficient_of_variation_percent
                    ),
                    "rated_fixture_power_w": (
                        result.plan.source.declared_operating_point.electrical_power_w_per_fixture
                    ),
                    "full_output_fixture_ppe_umol_per_j": (
                        result.plan.source.declared_operating_point.par_ppe_umol_per_j
                    ),
                    "full_output_fixture_ppf_umol_s": (
                        result.plan.source.declared_operating_point.par_ppf_umol_s_per_fixture
                    ),
                    "total_rated_power_w": (
                        len(result.plan.layout.fixtures)
                        * result.plan.source.declared_operating_point.electrical_power_w_per_fixture
                    ),
                    "total_full_output_ppf_umol_s": (
                        result.plan.source.whole_layout_transported_downward_ppf_umol_s
                    ),
                    "artifacts": {
                        "source_conversion_summary": str(
                            result.plan.paths.source_conversion_summary
                        ),
                        "scalar_transport_summary": str(
                            result.plan.paths.scalar_transport_summary
                        ),
                        "ppfd_values": str(result.plan.paths.ppfd_npz),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "rex-five-band-absorbed-metrics":
        result = calculate_rex_five_band_absorbed_metrics(args.workspace)
        print(
            json.dumps(
                {
                    "command": "rex-five-band-absorbed-metrics",
                    "success": True,
                    "workspace": str(result.summary.workspace_root),
                    "artifact_root": str(result.summary.artifact_root),
                    "band_order": list(FIVE_BAND_ORDER),
                    "par_band_order": list(FIVE_BAND_ORDER[:4]),
                    "summary": str(result.summary_path),
                    "patch_metrics": str(result.patch_npz_path),
                    "whole_plant": result.computed.plant.to_dict(),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "rex-five-band-receiver-smoke":
        plan = plan_rex_five_band_transport(
            args.workspace,
            seed=args.seed,
            normal_offset_m=args.normal_offset_m,
            nthreads=args.threads,
        )
        if not args.execute:
            print(
                json.dumps(
                    {
                        "command": "rex-five-band-receiver-smoke",
                        "dry_run": True,
                        "workspace": str(plan.workspace),
                        "artifact_root": str(plan.output_root),
                        "transport_plan": str(plan.manifest_path),
                        "band_order": list(FIVE_BAND_ORDER),
                        "threads": args.threads,
                        "leaf_count": plan.leaf_count,
                        "patch_count": plan.patch_count,
                        "receiver_count": plan.receiver_count,
                        "result_scope": "incident_light_diagnostics_only",
                        "bands": [
                            {
                                "band_id": band.band_id,
                                "units": band.expected_result_units,
                                "paths": band.paths.to_dict(),
                                "oconv_command": _command_payload(
                                    band.oconv_command
                                ),
                                "rtrace_command": _command_payload(
                                    band.rtrace_command
                                ),
                            }
                            for band in plan.band_plans
                        ],
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        installation = radiance_installation or discover_radiance_installation()
        result = execute_rex_five_band_receiver_smoke(
            plan,
            runner or LocalRunner(),
            radiance_installation=installation,
        )
        print(
            json.dumps(
                {
                    "command": "rex-five-band-receiver-smoke",
                    "dry_run": False,
                    "success": True,
                    "workspace": str(result.workspace_root),
                    "artifact_root": str(result.artifact_root),
                    "band_order": list(result.band_order),
                    "receiver_count": result.receiver_count,
                    "summary": str(result.summary_path),
                    "bands": [
                        {
                            "band_id": item.band_id,
                            "metrics": item.metrics.to_dict(),
                            "decoded_npy": str(item.decoded_npy_path),
                        }
                        for item in result.band_runs
                    ],
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "rex-receiver-smoke":
        plan = plan_rex_receiver_smoke(args.workspace, seed=args.seed)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "command": "rex-receiver-smoke",
                        "dry_run": True,
                        "workspace": str(plan.workspace),
                        "seed": plan.plant.seed,
                        "leaf_count": len(plan.plant.leaves),
                        "patch_count": plan.plant.patch_count,
                        "receiver_count": plan.receiver_count,
                        "leaf_optics_policy": (
                            "opaque_neutral_scalar_placeholder_not_rex_rta"
                        ),
                        "paths": {
                            "plant": str(plan.paths.plant_path),
                            "receivers": str(plan.paths.receiver_path),
                            "octree": str(plan.paths.octree_path),
                            "rgb_output": str(plan.paths.rgb_output_path),
                            "flux": str(plan.paths.flux_path),
                            "metrics": str(plan.paths.metrics_path),
                            "execution": str(plan.paths.execution_path),
                        },
                        "oconv_command": _command_payload(plan.oconv_command),
                        "rtrace_command": _command_payload(plan.rtrace_command),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        installation = radiance_installation or discover_radiance_installation()
        result = execute_rex_receiver_smoke(
            plan,
            runner or LocalRunner(),
            radiance_installation=installation,
        )
        print(
            json.dumps(
                {
                    "command": "rex-receiver-smoke",
                    "dry_run": False,
                    "success": True,
                    "workspace": str(plan.workspace),
                    "metrics": result.metrics.to_dict(),
                    "artifacts": {
                        "plant": str(plan.paths.plant_path),
                        "receivers": str(plan.paths.receiver_path),
                        "flux": str(plan.paths.flux_path),
                        "metrics": str(plan.paths.metrics_path),
                        "execution": str(plan.paths.execution_path),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "rex-plant-export":
        output_path = args.output.expanduser().resolve()
        if output_path.suffix.lower() != ".obj":
            raise ValueError("--output must use the .obj suffix.")
        plant = generate_rex_butterhead_plant(
            RexPlantConfig(
                seed=args.seed,
                leaf_patch_u=args.leaf_patch_u,
                leaf_patch_v=args.leaf_patch_v,
            )
        )
        summary_path = output_path.with_suffix(".json")
        material_path = output_path.with_suffix(".mtl")
        write_plant_mesh_obj(output_path, plant)
        write_rex_geometry_summary(summary_path, plant)
        print(
            json.dumps(
                {
                    "command": "rex-plant-export",
                    "obj_path": str(output_path),
                    "mtl_path": str(material_path),
                    "summary_path": str(summary_path),
                    "summary": rex_geometry_summary(plant),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "smd-composite-validate":
        plan = plan_composite_validation(args.workspace)
        if not args.execute:
            print(
                json.dumps(
                    {
                        "command": "smd-composite-validate",
                        "dry_run": True,
                        "workspace": str(plan.workspace),
                        "sensor_count": plan.sensor_count,
                        "target_ppfd": plan.target_ppfd,
                        "module_count": plan.schedule.module_count,
                        "emitter_path": str(plan.paths.emitter_path),
                        "oconv_command": _command_payload(plan.oconv_command),
                        "rtrace_command": _command_payload(plan.rtrace_command),
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
            return 0
        installation = radiance_installation or discover_radiance_installation()
        result = execute_composite_validation(
            plan,
            runner or LocalRunner(),
            radiance_installation=installation,
        )
        print(
            json.dumps(
                {
                    "command": "smd-composite-validate",
                    "dry_run": False,
                    "success": True,
                    "workspace": str(plan.workspace),
                    "comparison": result.comparison.to_dict(),
                    "artifacts": {
                        "emitters": str(plan.paths.emitter_path),
                        "actual_ppfd": str(plan.paths.actual_ppfd_path),
                        "metrics": str(plan.paths.metrics_path),
                        "comparison": str(plan.paths.comparison_path),
                        "execution": str(plan.paths.execution_path),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command == "smd-basis-solve":
        result = solve_basis_workspace(
            args.workspace,
            target_ppfd=args.target_ppfd,
            min_watts=args.min_watts,
            max_watts=args.max_watts,
            reference_watts_per_module=args.reference_watts_per_module,
        )
        print(
            json.dumps(
                {
                    "command": "smd-basis-solve",
                    "success": True,
                    "workspace": str(args.workspace.expanduser().resolve()),
                    "target_ppfd": args.target_ppfd,
                    "watts_by_control_zone": list(
                        result.schedule.watts_by_control_zone
                    ),
                    "mean_ppfd": result.uniformity.mean,
                    "cv_percent": result.uniformity.cv_percent,
                    "artifacts": {
                        "basis_solution": str(
                            result.artifact_paths.solution_path
                        ),
                        "optimized_module_schedule": str(
                            result.artifact_paths.schedule_path
                        ),
                        "predicted_ppfd": str(
                            result.artifact_paths.predicted_ppfd_path
                        ),
                        "predicted_ppfd_metrics": str(
                            result.artifact_paths.metrics_path
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0
    if args.command != "smd-basis-smoke":
        raise ValueError(f"unsupported command: {args.command}")
    length_ft, width_ft = args.room_ft
    layout = generate_proposed_led_layout(length_ft, width_ft)
    workspace_plan = plan_basis_workspace(
        layout=layout,
        output_directory=args.workspace,
        reference_watts=args.reference_watts,
        nthreads=args.threads,
    )
    selected_zone = 0 if args.zone is None else args.zone
    if not args.all_zones and not (
        0 <= selected_zone < workspace_plan.manifest.control_zone_count
    ):
        raise ValueError(
            f"--zone must be between 0 and "
            f"{workspace_plan.manifest.control_zone_count - 1}."
        )
    workspace = materialize_basis_workspace(workspace_plan)
    if not args.execute:
        if args.all_zones:
            column_plans = workspace.generation_plan.columns
            payload = {
                "command": "smd-basis-smoke",
                "dry_run": True,
                "all_zones": True,
                "workspace": str(workspace_plan.generation_plan.output_directory),
                "control_zone_count": workspace.manifest.control_zone_count,
                "sensor_count": workspace.manifest.sensor_count,
                "columns": [
                    {
                        "control_zone_index": column.control_zone_index,
                        "oconv_argv": list(column.oconv_command.argv),
                        "rtrace_argv": list(column.rtrace_command.argv),
                        "octree_path": str(column.octree_path),
                        "rgb_output_path": str(column.rgb_output_path),
                    }
                    for column in column_plans
                ],
            }
        else:
            column_plan = workspace.generation_plan.columns[selected_zone]
            payload = {
                "command": "smd-basis-smoke",
                "dry_run": True,
                "all_zones": False,
                "workspace": str(workspace_plan.generation_plan.output_directory),
                "control_zone_index": selected_zone,
                "sensor_count": workspace.manifest.sensor_count,
                "oconv_argv": list(column_plan.oconv_command.argv),
                "rtrace_argv": list(column_plan.rtrace_command.argv),
                "octree_path": str(column_plan.octree_path),
                "rgb_output_path": str(column_plan.rgb_output_path),
            }
        print(
            json.dumps(
                payload,
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    installation = radiance_installation or discover_radiance_installation()
    local_runner = runner or LocalRunner()
    if args.all_zones:
        result = execute_basis_matrix(
            workspace,
            local_runner,
            radiance_installation=installation,
        )
    else:
        result = execute_basis_column(
            workspace,
            selected_zone,
            local_runner,
            radiance_installation=installation,
        )
    print(
        json.dumps(
            {
                "command": "smd-basis-smoke",
                "dry_run": False,
                "all_zones": args.all_zones,
                "execution": result.metadata.to_dict(),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _command_payload(command: CommandSpec) -> dict[str, object]:
    return {
        "argv": list(command.argv),
        "stdin_path": (
            None if command.stdin_path is None else str(command.stdin_path)
        ),
        "stdout_path": (
            None if command.stdout_path is None else str(command.stdout_path)
        ),
        "stdout_mode": command.stdout_mode,
        "cwd": None if command.cwd is None else str(command.cwd),
        "env": dict(sorted(command.env.items())),
        "label": command.label,
    }


if __name__ == "__main__":
    sys.exit(main())
