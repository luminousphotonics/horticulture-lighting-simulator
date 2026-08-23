"""Isolated native-Radiance conservation probes for the Stage A audit.

This module is diagnostic-only.  It uses the production source writers, but
builds separate source-flux and black-room scenes under an explicit output
directory and never participates in a production run.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping, Sequence

from fspm_optics.fixtures.conventional_led import (
    CONVENTIONAL_FIXTURE_POWER_W,
    CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J,
    CONVENTIONAL_SHARED_LIGHT_MODIFIER,
    build_conventional_radiance_source_plan,
    plan_conventional_layout_from_feet,
    validate_converted_ies_output,
)
from fspm_optics.fixtures.smd.optical_stack import (
    APERTURE_AREA_M2,
    APERTURE_SIDE_M,
    COMPLETED_APERTURE_PPE_UMOL_PER_J,
)
from fspm_optics.fixtures.smd.positions import (
    SmdFixtureAssembly,
    SmdFixtureConnector,
    SmdLayout,
    SmdModulePosition,
    generate_proposed_led_layout,
)
from fspm_optics.fixtures.smd.power_schedule import (
    build_optimized_module_schedule,
)
from fspm_optics.fixtures.smd.radiance_writer import (
    build_smd_radiance_document,
)
from fspm_optics.geometry.room import RoomDimensions
from fspm_optics.geometry.sensor_grid import (
    SensorGridSpec,
    format_rtrace_receivers,
    generate_sensor_points,
)
from fspm_optics.layout.domain import LayoutTopology
from fspm_optics.radiance.commands import (
    CommandSpec,
    build_baseline_rtrace_command,
    build_oconv_command,
)
from fspm_optics.radiance.options import (
    radiance_options,
    replace_radiance_option_value,
)
from fspm_optics.radiance.runner import LocalRunner
from fspm_optics.transport.basis.atomic import atomic_write_bytes, atomic_write_text
from fspm_optics.transport.native_scalar_data import (
    decode_exact_native_scalar_rgb_rows,
)

SCHEMA_ID = "fspm-optics.stage-a-isolated-conservation-audit"
SCHEMA_VERSION = 1
ANGULAR_RESOLUTIONS = (8, 16)
RECEIVER_RESOLUTIONS = (81, 161)
CONVERGENCE_LIMIT = 0.005
TERMINAL_BOUND_FACTOR = 1.005
EPSILON_M = 1.0e-5
BLACK_ROOM_SIDE_M = 6.0
PROPOSED_TEST_WATTS = 23.0


class StageAConservationError(RuntimeError):
    """One isolated diagnostic failed to execute or close."""


@dataclass(frozen=True, slots=True)
class ProposedCase:
    case_id: str
    layout: SmdLayout
    watts_by_control_zone: tuple[float, ...]
    fixture_count: int


@dataclass(frozen=True, slots=True)
class MaterializedConventionalSource:
    directory: Path
    shared_rad: Path
    apertures_rad: Path
    plan: Any


def execute_stage_a_conservation_audit(
    output_directory: str | Path,
    *,
    nproc: int = 1,
) -> dict[str, object]:
    """Execute all isolated source and receiver probes and publish JSON."""

    if isinstance(nproc, bool) or not isinstance(nproc, int) or nproc <= 0:
        raise ValueError("nproc must be a positive integer.")
    root = Path(output_directory).expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise StageAConservationError(
            "output directory must be absent or empty for a reproducible audit."
        )
    root.mkdir(parents=True, exist_ok=True)
    runner = LocalRunner()
    proposed_cases = _proposed_cases()
    source_results = [
        _measure_proposed_case(root, case, runner=runner, nproc=nproc)
        for case in proposed_cases
    ]
    conventional = _materialize_conventional_source(root, runner=runner)
    source_results.append(
        _measure_conventional_source(conventional, runner=runner, nproc=nproc)
    )
    source_by_id = {str(item["case_id"]): item for item in source_results}
    receiver_results = (
        _measure_proposed_receiver(
            root,
            proposed_cases[0],
            emitted_flux=float(
                source_by_id["proposed_one_module"]["integrated_flux_umol_s"]["16"]
            ),
            runner=runner,
            nproc=nproc,
        ),
        _measure_conventional_receiver(
            conventional,
            emitted_flux=float(
                source_by_id["conventional_one_fixture"][
                    "integrated_flux_umol_s"
                ]["16"]
            ),
            runner=runner,
            nproc=nproc,
        ),
    )
    payload: dict[str, object] = {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "status": "pass",
        "limits": {
            "successive_resolution_relative_difference_maximum": (
                CONVERGENCE_LIMIT
            ),
            "terminal_receiver_to_integrated_emission_maximum": (
                TERMINAL_BOUND_FACTOR
            ),
        },
        "source_flux": source_results,
        "terminal_receiver": list(receiver_results),
    }
    atomic_write_text(
        root / "stage_a_conservation_audit.json",
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
    )
    return payload


def _proposed_cases() -> tuple[ProposedCase, ...]:
    base = generate_proposed_led_layout(
        10.0,
        10.0,
        mount_z_m=0.5,
        proposed_layout_mode="legacy",
    )
    first = base.fixtures[0]
    second = base.fixtures[1]
    return (
        ProposedCase(
            "proposed_one_module",
            _subset_layout(base, (first.member_module_indices[0],)),
            (PROPOSED_TEST_WATTS,),
            1,
        ),
        ProposedCase(
            "proposed_complete_fixture",
            _subset_layout(base, first.member_module_indices),
            (19.0,),
            1,
        ),
        ProposedCase(
            "proposed_multiple_fixtures_multiple_zones",
            _subset_layout(
                base,
                first.member_module_indices + second.member_module_indices,
            ),
            (11.0, 29.0),
            2,
        ),
    )


def _subset_layout(base: SmdLayout, selected: Sequence[int]) -> SmdLayout:
    selected_old = tuple(int(value) for value in selected)
    index_map = {old: new for new, old in enumerate(selected_old)}
    zones = tuple(
        sorted({base.modules[index].control_zone_index for index in selected_old})
    )
    zone_map = {old: new for new, old in enumerate(zones)}
    modules = tuple(
        SmdModulePosition(
            module_index=new,
            x_m=base.modules[old].x_m,
            y_m=base.modules[old].y_m,
            z_m=base.modules[old].z_m,
            control_zone_index=zone_map[base.modules[old].control_zone_index],
        )
        for new, old in enumerate(selected_old)
    )
    fixtures: list[SmdFixtureAssembly] = []
    for original in base.fixtures:
        members = tuple(
            index_map[index]
            for index in original.member_module_indices
            if index in index_map
        )
        if not members:
            continue
        connectors = tuple(
            SmdFixtureConnector(
                index_map[connector.start_module_index],
                index_map[connector.end_module_index],
            )
            for connector in original.connectors
            if connector.start_module_index in index_map
            and connector.end_module_index in index_map
        )
        fixtures.append(
            SmdFixtureAssembly(
                fixture_id=f"audit-fixture-{len(fixtures):04d}",
                fixture_type=original.fixture_type,
                display_fixture_type=original.display_fixture_type,
                source_orientation=original.source_orientation,
                orientation_degrees=original.orientation_degrees,
                member_module_indices=members,
                connectors=connectors,
            )
        )
    return SmdLayout(
        modules=modules,
        fixtures=tuple(fixtures),
        proposed_layout_mode=base.proposed_layout_mode,
        proposed_ring_mode=base.proposed_ring_mode,
        module_pattern_id=base.module_pattern_id,
        fixture_policy_id=base.fixture_policy_id,
        mechanical_envelope_id=base.mechanical_envelope_id,
        module_footprint_x_m=base.module_footprint_x_m,
        module_footprint_y_m=base.module_footprint_y_m,
        fixture_asset_set_id=base.fixture_asset_set_id,
        topology=LayoutTopology(
            base_n=base.topology.base_n,
            nominal_base_n=base.topology.nominal_base_n,
            proposed_ring_mode=base.topology.proposed_ring_mode,
            module_pattern_id=base.topology.module_pattern_id,
            square_tile_count=base.topology.square_tile_count,
            connector_count=sum(len(item.connectors) for item in fixtures),
            has_rectangular_extension=base.topology.has_rectangular_extension,
            rectangular_long_ft=base.topology.rectangular_long_ft,
            rectangular_offset=base.topology.rectangular_offset,
            control_zone_count=len(zones),
        ),
        room_length_m=base.room_length_m,
        room_width_m=base.room_width_m,
        pitch_x_m=base.pitch_x_m,
        pitch_y_m=base.pitch_y_m,
        axes_swapped=base.axes_swapped,
    )


def _measure_proposed_case(
    root: Path,
    case: ProposedCase,
    *,
    runner: LocalRunner,
    nproc: int,
) -> dict[str, object]:
    case_root = root / case.case_id
    case_root.mkdir()
    schedule = build_optimized_module_schedule(
        case.layout,
        case.watts_by_control_zone,
    )
    document = build_smd_radiance_document(case.layout, schedule)
    declared = (
        schedule.total_watts * COMPLETED_APERTURE_PPE_UMOL_PER_J
    )
    observations: dict[str, float] = {}
    commands: dict[str, list[str]] = {}
    # Multiple tagged receiver modifiers make an rfluxmtx matrix harder to
    # audit by eye.  Integrate each zone's production writer separately and
    # sum them; linearity is then explicit and no zone multiplier is shared.
    for resolution in ANGULAR_RESOLUTIONS:
        flux = 0.0
        for zone_index, zone_watts in enumerate(case.watts_by_control_zone):
            module_indices = tuple(
                module.module_index
                for module in case.layout.modules
                if module.control_zone_index == zone_index
            )
            zone_layout = _subset_layout(case.layout, module_indices)
            zone_schedule = build_optimized_module_schedule(
                zone_layout,
                (zone_watts,),
            )
            zone_document = build_smd_radiance_document(
                zone_layout,
                zone_schedule,
            )
            receiver_text, support_text = _split_proposed_receiver(
                zone_document.radiance_text
            )
            key = f"sc{resolution}_zone{zone_index}"
            measured, argv = _rflux_integral(
                directory=case_root,
                key=key,
                resolution=resolution,
                sender_text=_sender_for_proposed(zone_layout, resolution),
                receiver_text=receiver_text,
                receiver_support_files=(support_text,),
                sender_area_m2=len(zone_layout.modules) * APERTURE_AREA_M2,
                ambient_bounces=8,
                runner=runner,
                nproc=nproc,
            )
            flux += measured
            commands[key] = argv
        observations[str(resolution)] = flux
    return _source_result(
        case_id=case.case_id,
        source_boundary="completed Proposed module aperture(s)",
        electrical_power_w=schedule.total_watts,
        declared_flux=declared,
        observations=observations,
        counts={
            "fixtures": case.fixture_count,
            "modules": len(case.layout.modules),
            "control_zones": len(case.watts_by_control_zone),
            "emitter_primitives": document.metadata.emitter_primitive_count,
        },
        commands=commands,
    )


def _materialize_conventional_source(
    root: Path,
    *,
    runner: LocalRunner,
) -> MaterializedConventionalSource:
    source_root = root / "conventional_source"
    source_root.mkdir()
    layout = plan_conventional_layout_from_feet(
        5.0,
        5.0,
        policy="rolling_bench",
    )
    plan = build_conventional_radiance_source_plan(
        layout,
        workspace=source_root,
        ies2rad_bin="/opt/radiance/bin/ies2rad",
    )
    derived_path = plan.ies2rad.expected_paths.derived_ies
    atomic_write_text(derived_path, plan.derived_ies.text)
    result = runner.run(
        plan.ies2rad.command,
        stderr_path=source_root / "ies2rad.stderr",
    )
    if not result.success:
        raise StageAConservationError(
            result.failure_message or "diagnostic ies2rad failed."
        )
    converted_path = plan.ies2rad.expected_paths.converted_rad
    contract = validate_converted_ies_output(
        converted_path.read_text(encoding="utf-8"),
        expected_carrier_multiplier=plan.carrier_scale.ies2rad_multiplier,
    )
    shared = source_root / "conventional_shared.rad"
    apertures = source_root / "conventional_apertures.rad"
    atomic_write_text(
        shared,
        _annotate_light(
            contract.shared_definition_text,
            (
                f"{contract.clean_angular_modifier_id} light "
                f"{CONVENTIONAL_SHARED_LIGHT_MODIFIER}"
            ),
        ),
    )
    atomic_write_text(apertures, plan.aperture_radiance_text())
    return MaterializedConventionalSource(
        directory=source_root,
        shared_rad=shared,
        apertures_rad=apertures,
        plan=plan,
    )


def _measure_conventional_source(
    source: MaterializedConventionalSource,
    *,
    runner: LocalRunner,
    nproc: int,
) -> dict[str, object]:
    observations: dict[str, float] = {}
    commands: dict[str, list[str]] = {}
    aperture = source.plan.apertures[0]
    area = _polygon_area_xy(aperture.vertices_m)
    for resolution in ANGULAR_RESOLUTIONS:
        key = f"sc{resolution}"
        measured, argv = _rflux_integral(
            directory=source.directory,
            key=key,
            resolution=resolution,
            sender_text=_sender_from_polygons(
                (aperture.vertices_m,),
                resolution,
            ),
            receiver_text=(
                source.shared_rad.read_text(encoding="utf-8")
                + "\n"
                + source.apertures_rad.read_text(encoding="utf-8")
            ),
            receiver_support_files=(),
            sender_area_m2=area,
            ambient_bounces=0,
            runner=runner,
            nproc=nproc,
        )
        observations[str(resolution)] = measured
        commands[key] = argv
    declared = (
        CONVENTIONAL_FIXTURE_POWER_W
        * CONVENTIONAL_FIXTURE_PPE_UMOL_PER_J
    )
    return _source_result(
        case_id="conventional_one_fixture",
        source_boundary="one-sided complete Conventional fixture aperture",
        electrical_power_w=CONVENTIONAL_FIXTURE_POWER_W,
        declared_flux=declared,
        observations=observations,
        counts={"fixtures": 1, "aperture_primitives": 1},
        commands=commands,
    )


def _rflux_integral(
    *,
    directory: Path,
    key: str,
    resolution: int,
    sender_text: str,
    receiver_text: str,
    receiver_support_files: Sequence[str],
    sender_area_m2: float,
    ambient_bounces: int,
    runner: LocalRunner,
    nproc: int,
) -> tuple[float, list[str]]:
    sender = directory / f"{key}.sender.rad"
    receiver = directory / f"{key}.receiver.rad"
    atomic_write_text(sender, sender_text)
    atomic_write_text(receiver, receiver_text)
    support_paths = []
    for index, text in enumerate(receiver_support_files):
        path = directory / f"{key}.support{index}.rad"
        atomic_write_text(path, text)
        support_paths.append(path)
    output = directory / f"{key}.rgb"
    stderr = directory / f"{key}.stderr"
    options = (
        "-V+",
        "-h",
        "-u-",
        "-n",
        str(nproc),
        "-c",
        "1000",
        "-ab",
        str(ambient_bounces),
        "-ad",
        "4096",
        "-ds",
        "0.025",
        "-dj",
        "1",
        "-lr",
        "-32",
        "-lw",
        "1e-7",
        "-st",
        "0",
        "-av",
        "0",
        "0",
        "0",
        "-aw",
        "0",
    )
    command = CommandSpec(
        argv=(
            "/opt/radiance/bin/rfluxmtx",
            *options,
            str(sender),
            str(receiver),
            *(str(path) for path in support_paths),
        ),
        stdout_path=output,
        cwd=directory,
        env={"RAYPATH": ".:/opt/radiance/lib"},
        label=f"stage-a-source-flux-{key}",
    )
    result = runner.run(command, stderr_path=stderr)
    if not result.success:
        raise StageAConservationError(
            result.failure_message or f"rfluxmtx failed for {key}."
        )
    rows = _parse_equal_rgb_rows(
        output.read_text(encoding="utf-8"),
        expected_count=resolution * resolution,
    )
    flux = (
        sender_area_m2
        * math.pi
        * math.fsum(rows)
        / float(resolution * resolution)
    )
    return flux, list(command.argv)


def _source_result(
    *,
    case_id: str,
    source_boundary: str,
    electrical_power_w: float,
    declared_flux: float,
    observations: Mapping[str, float],
    counts: Mapping[str, int],
    commands: Mapping[str, list[str]],
) -> dict[str, object]:
    low = observations[str(ANGULAR_RESOLUTIONS[0])]
    high = observations[str(ANGULAR_RESOLUTIONS[1])]
    convergence = abs(high - low) / abs(low)
    if convergence > CONVERGENCE_LIMIT:
        raise StageAConservationError(
            f"{case_id} angular convergence {convergence:.6%} exceeds 0.5%."
        )
    ratio = high / declared_flux
    if abs(ratio - 1.0) > CONVERGENCE_LIMIT:
        raise StageAConservationError(
            f"{case_id} source closure ratio {ratio:.9g} differs from one by >0.5%."
        )
    return {
        "case_id": case_id,
        "source_boundary": source_boundary,
        "electrical_power_w": electrical_power_w,
        "completed_aperture_ppe_umol_per_j": declared_flux / electrical_power_w,
        "declared_flux_umol_s": declared_flux,
        "angular_basis": "Shirley-Chiu scN; equal projected-solid-angle bins",
        "angular_resolutions": list(ANGULAR_RESOLUTIONS),
        "integrated_flux_umol_s": dict(observations),
        "successive_relative_difference": convergence,
        "high_resolution_integrated_to_declared_ratio": ratio,
        "counts": dict(counts),
        "commands": dict(commands),
        "status": "pass",
    }


def _measure_proposed_receiver(
    root: Path,
    case: ProposedCase,
    *,
    emitted_flux: float,
    runner: LocalRunner,
    nproc: int,
) -> dict[str, object]:
    schedule = build_optimized_module_schedule(
        case.layout,
        case.watts_by_control_zone,
    )
    document = build_smd_radiance_document(case.layout, schedule)
    return _measure_terminal_receiver(
        directory=root / "proposed_terminal_receiver",
        case_id="proposed_one_module",
        source_texts=(document.radiance_text,),
        emitted_flux=emitted_flux,
        electrical_power_w=schedule.total_watts,
        runner=runner,
        nproc=nproc,
    )


def _measure_conventional_receiver(
    source: MaterializedConventionalSource,
    *,
    emitted_flux: float,
    runner: LocalRunner,
    nproc: int,
) -> dict[str, object]:
    return _measure_terminal_receiver(
        directory=source.directory / "terminal_receiver",
        case_id="conventional_one_fixture",
        source_texts=(
            source.shared_rad.read_text(encoding="utf-8"),
            source.apertures_rad.read_text(encoding="utf-8"),
            (
                source.plan.ies2rad.expected_paths.converted_dat.read_bytes()
            ),
        ),
        emitted_flux=emitted_flux,
        electrical_power_w=CONVENTIONAL_FIXTURE_POWER_W,
        runner=runner,
        nproc=nproc,
        conventional_dat_name=(
            source.plan.ies2rad.expected_paths.converted_dat.name
        ),
    )


def _diagnostic_black_room_text(room: RoomDimensions) -> str:
    """Build the audit-only absorbing enclosure without a production profile."""

    hx, hy, height = room.half_length_m, room.half_width_m, room.height_m

    def polygon(
        name: str, vertices: tuple[tuple[float, float, float], ...]
    ) -> str:
        lines = [f"audit_black polygon {name}", "0", "0", str(3 * len(vertices))]
        lines.extend(f"{x:.6f} {y:.6f} {z:.6f}" for x, y, z in vertices)
        return "\n".join(lines)

    polygons = (
        polygon("floor", ((-hx, -hy, 0.0), (hx, -hy, 0.0), (hx, hy, 0.0), (-hx, hy, 0.0))),
        polygon("ceiling", ((-hx, -hy, height), (-hx, hy, height), (hx, hy, height), (hx, -hy, height))),
        polygon("wall_neg_x", ((-hx, -hy, 0.0), (-hx, hy, 0.0), (-hx, hy, height), (-hx, -hy, height))),
        polygon("wall_pos_x", ((hx, -hy, 0.0), (hx, -hy, height), (hx, hy, height), (hx, hy, 0.0))),
        polygon("wall_neg_y", ((-hx, -hy, 0.0), (-hx, -hy, height), (hx, -hy, height), (hx, -hy, 0.0))),
        polygon("wall_pos_y", ((-hx, hy, 0.0), (hx, hy, 0.0), (hx, hy, height), (-hx, hy, height))),
    )
    return (
        "void plastic audit_black\n0\n0\n5 0 0 0 0 0\n\n"
        + "\n\n".join(polygons)
        + "\n"
    )


def _measure_terminal_receiver(
    *,
    directory: Path,
    case_id: str,
    source_texts: Sequence[str | bytes],
    emitted_flux: float,
    electrical_power_w: float,
    runner: LocalRunner,
    nproc: int,
    conventional_dat_name: str | None = None,
) -> dict[str, object]:
    directory.mkdir(parents=True)
    room = RoomDimensions(BLACK_ROOM_SIDE_M, BLACK_ROOM_SIDE_M, 3.0)
    room_path = directory / "black_room.rad"
    atomic_write_text(room_path, _diagnostic_black_room_text(room))
    source_paths: list[Path] = []
    for index, content in enumerate(source_texts):
        if isinstance(content, bytes):
            if conventional_dat_name is None:
                raise StageAConservationError("binary source support filename is missing.")
            path = directory / conventional_dat_name
            atomic_write_bytes(path, content)
        else:
            path = directory / f"source_{index}.rad"
            atomic_write_text(path, content)
            source_paths.append(path)
    octree = directory / "scene.oct"
    oconv = build_oconv_command(
        (room_path, *source_paths),
        output_octree=octree,
        cwd=directory,
        env={"RAYPATH": ".:/opt/radiance/lib"},
        oconv_bin="/opt/radiance/bin/oconv",
        label=f"{case_id}-black-terminal-oconv",
    )
    result = runner.run(oconv, stderr_path=directory / "oconv.stderr")
    if not result.success:
        raise StageAConservationError(
            result.failure_message or f"oconv failed for {case_id}."
        )
    observations: dict[str, float] = {}
    commands: dict[str, list[str]] = {"oconv": list(oconv.argv)}
    direct_options = radiance_options("direct")
    direct_options = replace_radiance_option_value(direct_options, "-dj", "0")
    direct_options = replace_radiance_option_value(direct_options, "-ds", "0.04")
    direct_options = [
        "-u-" if option == "-u+" else option for option in direct_options
    ]
    for resolution in RECEIVER_RESOLUTIONS:
        spec = SensorGridSpec(
            room=room,
            resolution_x=resolution,
            resolution_y=resolution,
            canopy_height_m=0.001,
            inset_m=0.0,
            layout="centered",
        )
        points = generate_sensor_points(spec)
        sensors = directory / f"receiver_{resolution}.pts"
        output = directory / f"receiver_{resolution}.rgb"
        atomic_write_text(sensors, format_rtrace_receivers(points))
        command = build_baseline_rtrace_command(
            octree=octree,
            receiver_input=sensors,
            rgb_output=output,
            options=direct_options,
            nthreads=nproc,
            cwd=directory,
            env={"RAYPATH": ".:/opt/radiance/lib"},
            rtrace_bin="/opt/radiance/bin/rtrace",
        )
        result = runner.run(
            command,
            stderr_path=directory / f"receiver_{resolution}.stderr",
        )
        if not result.success:
            raise StageAConservationError(
                result.failure_message
                or f"terminal rtrace failed for {case_id}."
            )
        values = decode_exact_native_scalar_rgb_rows(
            output.read_text(encoding="utf-8"),
            expected_count=len(points),
            error_factory=StageAConservationError,
        )
        observations[str(resolution)] = (
            math.fsum(values)
            * room.length_m
            * room.width_m
            / float(len(points))
        )
        commands[f"rtrace_{resolution}"] = list(command.argv)
    low = observations[str(RECEIVER_RESOLUTIONS[0])]
    high = observations[str(RECEIVER_RESOLUTIONS[1])]
    convergence = abs(high - low) / abs(low)
    bound_ratio = high / emitted_flux
    if convergence > CONVERGENCE_LIMIT:
        raise StageAConservationError(
            f"{case_id} receiver convergence {convergence:.6%} exceeds 0.5%."
        )
    if bound_ratio > TERMINAL_BOUND_FACTOR:
        raise StageAConservationError(
            f"{case_id} terminal receiver/emission ratio {bound_ratio:.9g} exceeds 1.005."
        )
    return {
        "case_id": case_id,
        "transport": "direct-only -ab 0",
        "room_material": "fully absorptive nonemitting RGB=0 plastic",
        "terminal_boundary": (
            "complete cell-centered floor quadrature immediately above a black "
            "floor; photons are absorbed after first downward crossing"
        ),
        "electrical_power_w": electrical_power_w,
        "independently_integrated_emitted_flux_umol_s": emitted_flux,
        "receiver_grid_resolutions": list(RECEIVER_RESOLUTIONS),
        "integrated_receiver_flux_umol_s": observations,
        "successive_relative_difference": convergence,
        "high_resolution_receiver_to_integrated_emission_ratio": bound_ratio,
        "commands": commands,
        "status": "pass",
    }


def _sender_for_proposed(layout: SmdLayout, resolution: int) -> str:
    polygons = tuple(
        _square_vertices(module.x_m, module.y_m, module.z_m)
        for module in layout.modules
    )
    return _sender_from_polygons(polygons, resolution)


def _sender_from_polygons(
    polygons: Sequence[Sequence[tuple[float, float, float]]],
    resolution: int,
) -> str:
    sections = [
        f"#@rfluxmtx h=sc{resolution} u=Y\n"
        "void glow audit_sender_basis\n0\n0\n4 1 1 1 0\n"
    ]
    for index, vertices in enumerate(polygons):
        shifted = tuple((x, y, z - EPSILON_M) for x, y, z in vertices)
        sections.append(
            _polygon_text(
                "audit_sender_basis",
                f"audit_sender_{index:04d}",
                shifted,
            )
        )
    return "\n".join(sections)


def _square_vertices(
    x_m: float,
    y_m: float,
    z_m: float,
) -> tuple[tuple[float, float, float], ...]:
    half = APERTURE_SIDE_M / 2.0
    return (
        (x_m - half, y_m - half, z_m),
        (x_m - half, y_m + half, z_m),
        (x_m + half, y_m + half, z_m),
        (x_m + half, y_m - half, z_m),
    )


def _polygon_text(
    modifier: str,
    identifier: str,
    vertices: Iterable[tuple[float, float, float]],
) -> str:
    points = tuple(vertices)
    return "\n".join(
        (
            f"{modifier} polygon {identifier}",
            "0",
            "0",
            str(3 * len(points)),
            *(f"{x:.9f} {y:.9f} {z:.9f}" for x, y, z in points),
            "",
        )
    )


def _annotate_light(text: str, declaration: str) -> str:
    marker = f"#@rfluxmtx h=u u=Y\n{declaration}"
    if text.count(declaration) != 1:
        raise StageAConservationError(
            f"expected one receiver light declaration: {declaration}"
        )
    return text.replace(declaration, marker, 1)


def _split_proposed_receiver(text: str) -> tuple[str, str]:
    """Keep receiver light and emitter polygons contiguous for rfluxmtx."""

    lines = text.splitlines()
    declaration = "void light smd_control_zone_000"
    try:
        light_start = lines.index(declaration)
    except ValueError as exc:
        raise StageAConservationError(
            "Proposed production document is missing its zone light."
        ) from exc
    light_end = light_start + 4
    if light_end > len(lines) or not lines[light_start + 3].startswith("3 "):
        raise StageAConservationError("Proposed zone-light primitive is malformed.")
    light_block = lines[light_start:light_end]
    emitter_blocks: list[list[str]] = []
    remove_indices = set(range(light_start, light_end))
    for index, line in enumerate(lines):
        if (
            line.startswith("smd_control_zone_000 polygon ")
            and line.endswith("_internal_emitter")
        ):
            end = index + 8
            if end > len(lines) or lines[index + 3].strip() != "12":
                raise StageAConservationError(
                    "Proposed internal-emitter primitive is malformed."
                )
            emitter_blocks.append(lines[index:end])
            remove_indices.update(range(index, end))
    if not emitter_blocks:
        raise StageAConservationError(
            "Proposed production document has no internal emitter."
        )
    receiver = "\n".join(
        (
            "#@rfluxmtx h=u u=Y",
            *light_block,
            *(line for block in emitter_blocks for line in block),
            "",
        )
    )
    support = "\n".join(
        line for index, line in enumerate(lines) if index not in remove_indices
    )
    return receiver, support + "\n"


def _parse_equal_rgb_rows(text: str, *, expected_count: int) -> tuple[float, ...]:
    rows: list[float] = []
    for row_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        tokens = line.split()
        if len(tokens) != 3:
            raise StageAConservationError(
                f"rfluxmtx row {row_number} is not one RGB triple."
            )
        values = tuple(float(value) for value in tokens)
        if not all(
            math.isclose(values[0], value, rel_tol=1e-10, abs_tol=1e-10)
            for value in values[1:]
        ):
            raise StageAConservationError(
                f"rfluxmtx row {row_number} is not equal-channel scalar output."
            )
        rows.append(math.fsum(values) / 3.0)
    if len(rows) != expected_count:
        raise StageAConservationError(
            f"expected {expected_count} angular rows, got {len(rows)}."
        )
    return tuple(rows)


def _polygon_area_xy(vertices: Sequence[tuple[float, float, float]]) -> float:
    xs = [item[0] for item in vertices]
    ys = [item[1] for item in vertices]
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=".venv/bin/python scripts/stage_a_conservation_audit.py",
        description="Execute isolated Stage A source-flux and receiver conservation probes.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--nproc", type=int, default=1)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        payload = execute_stage_a_conservation_audit(
            arguments.output_dir,
            nproc=arguments.nproc,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Stage A conservation audit failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": payload["status"],
                "report": str(
                    arguments.output_dir.resolve()
                    / "stage_a_conservation_audit.json"
                ),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SCHEMA_ID",
    "SCHEMA_VERSION",
    "StageAConservationError",
    "build_parser",
    "execute_stage_a_conservation_audit",
    "main",
]
